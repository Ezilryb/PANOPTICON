"""
panopticon/aegis/pipeline.py

AegisEngine : cœur du module AEGIS. Se connecte à ARGUS en tant que CLIENT
(même contrat d'intégration que ROSTER/SPECTRA/ORACLE), filtre les
détections de classe "person", récupère la frame brute correspondante,
recadre chaque personne détectée et lance dessus l'analyse de posture, puis
transmet le résultat (avec le centroïde de la bbox) au FallStateTracker.

Contrairement à ROSTER/SPECTRA/ORACLE (qui publient un évènement par frame
traitée), AEGIS ne publie QUE lorsqu'une alerte change d'état
(fall_confirmed / fall_ended) — même philosophie que PULSE_TRACK : "rien
n'est publié tant qu'aucune règle ne se déclenche". Le volume du bus AEGIS
reste donc nul en fonctionnement normal, et ne croît qu'avec les évènements
réellement pertinents pour un opérateur.
"""

import logging
import threading
import time
from typing import Optional

import numpy as np

from argus.client import ArgusClient
from argus.data_types import DetectionEvent

from .config import AegisConfig
from .data_types import AegisEvent
from .fall_tracker import FallStateTracker
from .metrics import AegisMetrics
from .posture_analyzer import build_analyzer
from .publisher import AegisPublisher

logger = logging.getLogger("aegis.pipeline")

# Nombre de tentatives et délai entre chaque tentative de connexion à ARGUS : DAEMON marque
# ARGUS "running" dès que le subprocess est lancé, avant même que son publisher ait fini de
# binder son socket TCP — AEGIS doit donc pouvoir patienter quelques instants au démarrage
# (même principe que roster/pipeline.py::_connect_to_argus_with_retry).
_ARGUS_CONNECT_MAX_RETRIES = 20
_ARGUS_CONNECT_RETRY_DELAY_S = 0.5

# Marge ajoutée autour de la bbox "person" avant recadrage : entre celle de ROSTER (0.15,
# pense aux cheveux/menton en bord de boîte visage) et celle d'ORACLE (0.05, un objet remplit
# déjà bien sa bbox) — un corps entier déborde parfois légèrement sa bbox ARGUS aux extrémités
# (poignets, chevilles), utile au backend yolo_pose pour ces points-clés en bord de cadre.
_CROP_MARGIN_RATIO = 0.08


class AegisEngine:
    """Assemble connexion ARGUS, analyseur de posture, machine à états de chute et publication en un pipeline unique."""

    def __init__(self, config: AegisConfig) -> None:
        self.config = config

        self.analyzer = build_analyzer(config.analyzer)
        self.fall_tracker = FallStateTracker(config.fall_detection)
        self.publisher = AegisPublisher(config.publisher.host, config.publisher.port)
        self.metrics = AegisMetrics(log_every_s=config.log_stats_every_s)

        self.argus_client = ArgusClient(config.argus.host, config.argus.port)

        self._stop_event = threading.Event()
        self._main_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        logger.info("AegisEngine : démarrage (backend analyzer=%s)", self.config.analyzer.backend)
        self.analyzer.warmup()
        self.publisher.start()
        self._connect_to_argus_with_retry()

        self._stop_event.clear()
        self._main_thread = threading.Thread(target=self._run_loop, name="aegis-main-loop", daemon=True)
        self._main_thread.start()
        logger.info("AegisEngine : démarré")

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        logger.info("AegisEngine : arrêt demandé")
        self._stop_event.set()
        self.argus_client.close()  # débloque events() si le thread principal y est bloqué en lecture
        if self._main_thread is not None:
            self._main_thread.join(timeout=10)
            self._main_thread = None
        self.publisher.stop()
        logger.info("AegisEngine : arrêté proprement")

    def run_forever(self) -> None:
        """Bloque l'appelant jusqu'à `stop()` (utilisé par run_aegis.py après réception d'un signal d'arrêt)."""
        self.start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(0.5)
        finally:
            self.stop()

    def _connect_to_argus_with_retry(self) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, _ARGUS_CONNECT_MAX_RETRIES + 1):
            try:
                self.argus_client.connect()
                logger.info("AegisEngine : connecté à ARGUS sur %s:%d (tentative %d)",
                            self.config.argus.host, self.config.argus.port, attempt)
                return
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "AegisEngine : connexion à ARGUS échouée (tentative %d/%d) : %s",
                    attempt, _ARGUS_CONNECT_MAX_RETRIES, exc,
                )
                self._stop_event.wait(_ARGUS_CONNECT_RETRY_DELAY_S)

        raise RuntimeError(
            f"AegisEngine : impossible de se connecter à ARGUS sur "
            f"{self.config.argus.host}:{self.config.argus.port} après {_ARGUS_CONNECT_MAX_RETRIES} tentatives "
            f"({last_error}). ARGUS est-il bien démarré ?"
        )

    # ------------------------------------------------------------------ #
    # Boucle principale (thread dédié) — bloquante sur la lecture socket
    # ------------------------------------------------------------------ #

    def _run_loop(self) -> None:
        try:
            for event in self.argus_client.events():
                if self._stop_event.is_set():
                    break
                self._process_event(event)
        except OSError:
            if not self._stop_event.is_set():
                logger.error("AegisEngine : connexion ARGUS perdue de façon inattendue")

    def _process_event(self, event: DetectionEvent) -> None:
        fall_cfg = self.config.fall_detection
        if fall_cfg.monitored_camera_ids and event.camera_id not in fall_cfg.monitored_camera_ids:
            return  # caméra hors périmètre de surveillance AEGIS (ex: chambre) — cf. config.py

        now = time.time()
        person_detections = [d for d in event.detections if d.class_name in fall_cfg.person_classes]
        # "vu" indépendamment de la confiance : cf. LIMITE HONNÊTE dans fall_tracker.py sur la
        # confiance de détection d'ARGUS qui peut chuter précisément pendant une chute.
        seen_track_ids = {d.track_id for d in person_detections if d.track_id is not None}

        to_publish: list[AegisEvent] = []
        analyzable = [d for d in person_detections if d.confidence >= fall_cfg.min_detection_confidence]

        if analyzable:
            frame = self.argus_client.read_frame(event)
            if frame is None:
                logger.debug("Frame indisponible pour %s (frame_id=%d), analyse de posture ignorée pour cette frame",
                             event.camera_id, event.frame_id)
            else:
                for detection in analyzable:
                    if detection.track_id is None:
                        continue  # confirmation de chute impossible sans piste stable, cf. fall_tracker.py
                    crop, _ox, _oy = self._crop_with_margin(frame, detection.bbox)
                    if crop.size == 0:
                        continue

                    result = self.analyzer.analyze(crop, detection.bbox)
                    self.metrics.record_observation(event.camera_id, result.posture)

                    x1, y1, x2, y2 = detection.bbox
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    aegis_event = self.fall_tracker.update(
                        event.camera_id, detection.track_id, event.frame_id, result, cx, cy, now,
                    )
                    if aegis_event is not None:
                        to_publish.append(aegis_event)

        to_publish.extend(self.fall_tracker.prune_stale(event.camera_id, seen_track_ids, now))

        for aegis_event in to_publish:
            self.publisher.publish(aegis_event)
            self.metrics.record_trigger(aegis_event.event_type, aegis_event.latency_ms)
            suffix = f" ({aegis_event.end_reason})" if aegis_event.end_reason else ""
            logger.info("AegisEvent publié : %s piste=%d caméra=%s%s",
                        aegis_event.event_type, aegis_event.track_id, aegis_event.camera_id, suffix)

        self.metrics.maybe_log()

    @staticmethod
    def _crop_with_margin(frame: np.ndarray, bbox) -> tuple[np.ndarray, int, int]:
        """Recadre `frame` sur `bbox` avec une marge, clippé aux dimensions réelles de l'image."""
        height, width = frame.shape[0], frame.shape[1]
        x1, y1, x2, y2 = bbox
        box_w, box_h = x2 - x1, y2 - y1
        margin_x, margin_y = box_w * _CROP_MARGIN_RATIO, box_h * _CROP_MARGIN_RATIO

        cx1 = max(0, int(x1 - margin_x))
        cy1 = max(0, int(y1 - margin_y))
        cx2 = min(width, int(x2 + margin_x))
        cy2 = min(height, int(y2 + margin_y))

        return frame[cy1:cy2, cx1:cx2], cx1, cy1

    # ------------------------------------------------------------------ #
    # Introspection (utile pour NEXUS-V / diagnostics CLI)
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        return {
            "active_alerts": self.fall_tracker.active_alert_count(),
            "metrics": self.metrics.snapshot(),
            "publisher_clients": self.publisher.client_count,
        }
