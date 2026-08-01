"""
panopticon/argus/pipeline.py

ArgusEngine : cœur du module ARGUS. Boucle principale qui, dès qu'une ou
plusieurs caméras livrent une frame inédite, lance la détection (par lot,
toutes caméras confondues, pour rester efficace), applique le tracking par
caméra, publie l'évènement résultant et écrit la frame en mémoire partagée.
La boucle se réveille dès qu'une frame arrive (attente courte de 5ms au pire)
plutôt que d'interroger les caméras à intervalle fixe plus long, ce qui
minimise la latence entre la capture caméra et la disponibilité de l'analyse.
"""

import logging
import threading
import time
from typing import Optional

from .camera_manager import CameraManager
from .config import ArgusConfig
from .data_types import DetectionEvent, Frame
from .detector import build_detector
from .frame_store import SharedFrameStore
from .metrics import ArgusMetrics
from .publisher import ArgusPublisher
from .tracker import IouTracker

logger = logging.getLogger("argus.pipeline")

# Borne haute de la latence de réveil quand aucune caméra ne signale de frame inédite.
_IDLE_POLL_INTERVAL_S = 0.005
# Seuil au-delà duquel un lot de détection est jugé anormalement lent (log d'alerte uniquement).
_SLOW_BATCH_MS = 200.0


class ArgusEngine:
    """Assemble caméras, détecteur, tracking, mémoire partagée et publication en un pipeline unique."""

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

        self._stop_event = threading.Event()
        self._main_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        logger.info(
            "ArgusEngine : démarrage (%d caméra(s), backend detector=%s)",
            len(self.config.cameras), self.config.detector.backend,
        )
        self.detector.warmup()

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
        logger.info("ArgusEngine : arrêté proprement")

    def run_forever(self) -> None:
        """Bloque l'appelant jusqu'à `stop()` (utilisé par run_argus.py après réception d'un signal d'arrêt)."""
        self.start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(0.5)
        finally:
            self.stop()

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
        camera_ids = list(frames.keys())
        images = [frames[cid].image for cid in camera_ids]

        ts_batch_start = time.time()
        batch_detections = self.detector.detect_batch(images)
        ts_detected = time.time()

        for camera_id, detections in zip(camera_ids, batch_detections):
            frame = frames[camera_id]
            tracked = self._trackers[camera_id].update(detections)

            event = DetectionEvent(
                camera_id=camera_id,
                frame_id=frame.frame_id,
                ts_capture=frame.ts_capture,
                ts_detected=ts_detected,
                width=frame.width,
                height=frame.height,
                detections=tracked,
            )

            store = self._frame_stores.get(camera_id)
            if store is not None:
                store.write(frame.image, frame.frame_id, frame.ts_capture, self.config.publisher.jpeg_quality)

            self.publisher.publish(event)
            self.metrics.record_event(camera_id, event.latency_ms)

        elapsed_ms = (ts_detected - ts_batch_start) * 1000.0
        if elapsed_ms > _SLOW_BATCH_MS:
            logger.warning("Détection lente sur ce lot (%d caméra(s)) : %.1fms", len(camera_ids), elapsed_ms)

    # ------------------------------------------------------------------ #
    # Introspection (utile pour NEXUS-V / diagnostics CLI)
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        return {
            "cameras": self.camera_manager.stats(),
            "metrics": self.metrics.snapshot(),
            "publisher_clients": self.publisher.client_count,
        }
