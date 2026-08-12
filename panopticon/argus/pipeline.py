"""
panopticon/argus/pipeline.py

ArgusEngine : cœur du module ARGUS. Deux modes de fonctionnement temporel
(cf. config.py::TrackingModeConfig) :
  - "total" (défaut, historique) : le Detector tourne sur CHAQUE frame de
    CHAQUE caméra — le plus précis, le plus gourmand en CPU/GPU.
  - "detect_and_track" : le Detector ne tourne que toutes les N frames
    ("frame lourde") ; entre deux frames lourdes, un tracker visuel léger
    (light_tracker.py) extrapole la position de chaque piste déjà connue
    ("frame légère", ~1ms/objet). Moins précis (dérive corrigée à la
    prochaine frame lourde), nettement moins gourmand.
Dans les deux cas, la boucle se réveille dès qu'une frame arrive (attente
courte de 5ms au pire) et PUBLIE UN évènement PAR FRAME PAR CAMÉRA, lourde
ou légère : le contrat "une DetectionEvent par frame ARGUS" ne change jamais
pour SPECTRA/ROSTER/ORACLE/PULSE_TRACK, seule la fraîcheur des détections
varie (cf. Detection.via_light_tracker).
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .camera_manager import CameraManager
from .config import ArgusConfig
from .data_types import Detection, DetectionEvent, Frame
from .detector import build_detector
from .frame_store import SharedFrameStore
from .light_tracker import BaseLightTracker, build_light_tracker
from .metrics import ArgusMetrics
from .publisher import ArgusPublisher
from .tracker import IouTracker

logger = logging.getLogger("argus.pipeline")

# Borne haute de la latence de réveil quand aucune caméra ne signale de frame inédite.
_IDLE_POLL_INTERVAL_S = 0.005
# Seuil au-delà duquel un lot de détection (frames lourdes) est jugé anormalement lent (log d'alerte uniquement).
_SLOW_BATCH_MS = 200.0
# Même principe pour le lot de tracking léger (frames légères, mode "detect_and_track") — seuil
# nettement plus bas puisque l'intérêt même du mode est un coût de l'ordre de ~1ms/objet.
_SLOW_LIGHT_BATCH_MS = 50.0


@dataclass
class _LightTrackState:
    """
    État interne (mode "detect_and_track" uniquement) : un tracker léger
    actif pour UNE piste d'UNE caméra. Le tracker lui-même ne connaît que la
    position (cf. light_tracker.py) — class_id/class_name/confidence sont
    conservés ici pour être réappliqués tels quels sur chaque Detection
    extrapolée, jusqu'à la prochaine frame lourde qui les rafraîchira.
    """
    tracker: BaseLightTracker
    class_id: int
    class_name: str
    confidence: float


class ArgusEngine:
    """Assemble caméras, détecteur, tracking (lourd et/ou léger), mémoire partagée et publication en un pipeline unique."""

    def __init__(self, config: ArgusConfig) -> None:
        self.config = config
        self.camera_manager = CameraManager(config)
        self.detector = build_detector(config.detector)
        self.publisher = ArgusPublisher(config.publisher.host, config.publisher.port)
        self.metrics = ArgusMetrics(log_every_s=config.log_stats_every_s)

        self._trackers: dict[str, IouTracker] = {
            cam.camera_id: IouTracker(config.tracker_iou_threshold, config.tracker_max_age_frames)
            for cam in config.cameras
        }
        self._frame_stores: dict[str, SharedFrameStore] = {}
        # Mode "detect_and_track" uniquement : {camera_id: {track_id: _LightTrackState}}.
        # Reste vide (et jamais consulté) en mode "total".
        self._light_tracks: dict[str, dict[int, _LightTrackState]] = {
            cam.camera_id: {} for cam in config.cameras
        }

        self._stop_event = threading.Event()
        self._main_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        logger.info(
            "ArgusEngine : démarrage (%d caméra(s), backend detector=%s, mode=%s)",
            len(self.config.cameras), self.config.detector.backend, self.config.tracking_mode.mode,
        )
        self.detector.warmup()
        self._warmup_light_tracker_if_needed()

        for cam in self.config.cameras:
            if not cam.enabled:
                continue
            self._frame_stores[cam.camera_id] = SharedFrameStore(
                cam.camera_id, slots=self.config.publisher.frame_shm_slots,
            )

        self.camera_manager.start()
        self.publisher.start()

        self._stop_event.clear()
        self._main_thread = threading.Thread(target=self._run_loop, name="argus-main-loop", daemon=True)
        self._main_thread.start()
        logger.info("ArgusEngine : démarré")

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        logger.info("ArgusEngine : arrêt demandé")
        self._stop_event.set()
        if self._main_thread is not None:
            self._main_thread.join(timeout=10)
            self._main_thread = None

        self.camera_manager.stop()
        self.publisher.stop()
        for store in self._frame_stores.values():
            store.close()
        self._frame_stores.clear()
        self._light_tracks.clear()
        logger.info("ArgusEngine : arrêté proprement")

    def run_forever(self) -> None:
        """Bloque l'appelant jusqu'à `stop()` (utilisé par run_argus.py après réception d'un signal d'arrêt)."""
        self.start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(0.5)
        finally:
            self.stop()

    def _warmup_light_tracker_if_needed(self) -> None:
        """
        Mode "detect_and_track" uniquement : construit et initialise un
        tracker léger de test AVANT de démarrer la boucle principale — même
        principe que detector.warmup() (échouer tout de suite, ex: backend
        'mosse' sans opencv-contrib installé, plutôt que silencieusement au
        milieu de _run_loop, qui n'a aucun try/except et laisserait mourir
        le thread sans que personne ne s'en aperçoive).
        """
        if self.config.tracking_mode.mode != "detect_and_track":
            return
        dummy = np.zeros((64, 64, 3), dtype=np.uint8)
        probe = build_light_tracker(self.config.tracking_mode)
        probe.init(dummy, (10.0, 10.0, 50.0, 50.0))
        logger.info(
            "Tracker léger prêt (backend=%s, 1 frame lourde / %d)",
            self.config.tracking_mode.light_tracker_backend, self.config.tracking_mode.detect_every_n_frames,
        )

    # ------------------------------------------------------------------ #
    # Boucle principale (thread dédié)
    # ------------------------------------------------------------------ #

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            new_frames = self.camera_manager.poll_new_frames()

            if not new_frames:
                self._stop_event.wait(_IDLE_POLL_INTERVAL_S)
                self.metrics.maybe_log()
                continue

            self._process_frames(new_frames)
            self.metrics.maybe_log()

    def _process_frames(self, frames: dict[str, Frame]) -> None:
        if self.config.tracking_mode.mode == "total":
            self._process_frames_total(frames)
        else:
            self._process_frames_detect_and_track(frames)

    # ------------------------------------------------------------------ #
    # Mode "total"
    # ------------------------------------------------------------------ #

    def _process_frames_total(self, frames: dict[str, Frame]) -> None:
        """Le Detector tourne sur CHAQUE frame de CHAQUE caméra, sans exception (comportement historique, inchangé)."""
        camera_ids = list(frames.keys())
        images = [frames[cid].image for cid in camera_ids]

        ts_batch_start = time.time()
        batch_detections = self.detector.detect_batch(images)
        ts_detected = time.time()

        for camera_id, detections in zip(camera_ids, batch_detections):
            frame = frames[camera_id]
            tracked = self._trackers[camera_id].update(detections)
            self._publish_camera_event(camera_id, frame, tracked, ts_detected)

        elapsed_ms = (ts_detected - ts_batch_start) * 1000.0
        if elapsed_ms > _SLOW_BATCH_MS:
            logger.warning("Détection lente sur ce lot (%d caméra(s)) : %.1fms", len(camera_ids), elapsed_ms)

    # ------------------------------------------------------------------ #
    # Mode "detect_and_track"
    # ------------------------------------------------------------------ #

    def _process_frames_detect_and_track(self, frames: dict[str, Frame]) -> None:
        """
        Sépare, PAR CAMÉRA, les frames "lourdes" (Detector complet, cf.
        TrackingModeConfig.is_heavy_frame) des frames "légères" (tracker
        visuel léger) — deux caméras peuvent très bien être l'une en frame
        lourde et l'autre en frame légère au même tick, chacune suit son
        propre cycle indépendant basé sur son propre frame_id.
        """
        heavy_camera_ids = [
            cid for cid, f in frames.items() if self.config.tracking_mode.is_heavy_frame(f.frame_id)
        ]
        light_camera_ids = [cid for cid in frames if cid not in heavy_camera_ids]

        if heavy_camera_ids:
            ts_batch_start = time.time()
            images = [frames[cid].image for cid in heavy_camera_ids]
            batch_detections = self.detector.detect_batch(images)
            ts_detected_heavy = time.time()

            elapsed_ms = (ts_detected_heavy - ts_batch_start) * 1000.0
            if elapsed_ms > _SLOW_BATCH_MS:
                logger.warning("Détection lente sur ce lot lourd (%d caméra(s)) : %.1fms",
                                len(heavy_camera_ids), elapsed_ms)

            for camera_id, detections in zip(heavy_camera_ids, batch_detections):
                frame = frames[camera_id]
                tracked = self._trackers[camera_id].update(detections)
                self._reinit_light_tracks(camera_id, frame, tracked)
                self._publish_camera_event(camera_id, frame, tracked, ts_detected_heavy)

        if light_camera_ids:
            ts_light_start = time.time()
            for camera_id in light_camera_ids:
                frame = frames[camera_id]
                tracked = self._update_light_tracks(camera_id, frame)
                self._publish_camera_event(camera_id, frame, tracked, time.time())

            elapsed_ms = (time.time() - ts_light_start) * 1000.0
            if elapsed_ms > _SLOW_LIGHT_BATCH_MS:
                logger.warning("Tracking léger lent sur ce lot (%d caméra(s)) : %.1fms",
                                len(light_camera_ids), elapsed_ms)

    def _reinit_light_tracks(self, camera_id: str, frame: Frame, tracked: list[Detection]) -> None:
        """
        Appelé après CHAQUE frame lourde : remplace entièrement l'état de
        tracking léger de cette caméra par un tracker fraîchement (ré)
        initialisé pour chaque piste confirmée par le Detector cette frame.
        Une piste qui n'apparaît pas dans `tracked` (non retrouvée, ou
        supprimée par IouTracker faute de correspondance récente) perd
        simplement son tracker léger : elle ne réapparaîtra, avec un
        track_id nouveau ou retrouvé, qu'à la prochaine frame lourde qui la
        détecte à nouveau — ARGUS ne peut pas raisonnablement extrapoler la
        position d'un objet qu'il ne voit plus du tout à la frame lourde.
        """
        fresh: dict[int, _LightTrackState] = {}
        for detection in tracked:
            if detection.track_id is None:
                continue  # ne devrait pas arriver après IouTracker.update(), défensif
            tracker = build_light_tracker(self.config.tracking_mode)
            try:
                tracker.init(frame.image, detection.bbox)
            except Exception as exc:  # noqa: BLE001 — l'échec d'UNE piste ne doit jamais arrêter tout ARGUS
                logger.warning(
                    "Échec d'initialisation du tracker léger pour la piste %d (caméra %s) : %s",
                    detection.track_id, camera_id, exc,
                )
                continue
            fresh[detection.track_id] = _LightTrackState(
                tracker=tracker, class_id=detection.class_id,
                class_name=detection.class_name, confidence=detection.confidence,
            )
        self._light_tracks[camera_id] = fresh

    def _update_light_tracks(self, camera_id: str, frame: Frame) -> list[Detection]:
        """
        Avance chaque tracker léger actif de cette caméra d'une frame. Une
        piste dont le tracker léger échoue est abandonnée (retirée de
        l'état pour cette caméra) : aucune Detection n'est produite pour
        elle cette frame, et elle ne reviendra qu'à la prochaine frame
        lourde. `IouTracker.sync_track_position()` tient le tracker lourd
        informé de la position extrapolée, pour que la PROCHAINE frame
        lourde compare sa nouvelle détection à une position à jour plutôt
        qu'à une position vieille de plusieurs frames (cf. tracker.py).
        """
        tracks = self._light_tracks.get(camera_id, {})
        detections: list[Detection] = []
        lost_track_ids: list[int] = []

        for track_id, state in tracks.items():
            try:
                ok, new_bbox = state.tracker.update(frame.image)
            except Exception as exc:  # noqa: BLE001 — même principe que _reinit_light_tracks
                logger.warning(
                    "Tracker léger en erreur pour la piste %d (caméra %s), piste abandonnée : %s",
                    track_id, camera_id, exc,
                )
                ok, new_bbox = False, None

            if not ok or new_bbox is None:
                lost_track_ids.append(track_id)
                continue

            self._trackers[camera_id].sync_track_position(track_id, new_bbox)
            detections.append(Detection(
                class_id=state.class_id, class_name=state.class_name, confidence=state.confidence,
                bbox=new_bbox, track_id=track_id, via_light_tracker=True,
            ))

        for track_id in lost_track_ids:
            del tracks[track_id]

        return detections

    # ------------------------------------------------------------------ #
    # Commun aux deux modes
    # ------------------------------------------------------------------ #

    def _publish_camera_event(self, camera_id: str, frame: Frame, detections: list[Detection], ts_detected: float) -> None:
        """Construit, écrit (frame_store) et publie l'évènement d'UNE caméra — factorisé entre les deux modes de tracking."""
        event = DetectionEvent(
            camera_id=camera_id,
            frame_id=frame.frame_id,
            ts_capture=frame.ts_capture,
            ts_detected=ts_detected,
            width=frame.width,
            height=frame.height,
            detections=detections,
        )

        store = self._frame_stores.get(camera_id)
        if store is not None:
            store.write(frame.image, frame.frame_id, frame.ts_capture, self.config.publisher.jpeg_quality)

        self.publisher.publish(event)
        self.metrics.record_event(camera_id, event.latency_ms)

    # ------------------------------------------------------------------ #
    # Introspection (utile pour NEXUS-V / diagnostics CLI)
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        return {
            "cameras": self.camera_manager.stats(),
            "metrics": self.metrics.snapshot(),
            "publisher_clients": self.publisher.client_count,
            "tracking_mode": self.config.tracking_mode.mode,
            "light_tracks_active": {cid: len(tracks) for cid, tracks in self._light_tracks.items()},
        }