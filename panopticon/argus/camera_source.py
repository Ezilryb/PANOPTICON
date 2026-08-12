"""
panopticon/argus/camera_source.py

Capture d'une caméra unique dans un thread dédié : lit les frames en continu
et ne conserve que la plus récente (les frames intermédiaires non consommées
sont volontairement perdues) afin que l'analyse ne prenne jamais de retard
sur le flux caméra. Gère la reconnexion automatique et inclut une source
synthétique (aucun matériel requis) pour développer/tester sans caméra réelle.
"""

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

from .config import CameraConfig
from .data_types import Frame

logger = logging.getLogger("argus.camera_source")


class CameraSource:
    """
    Thread de capture pour une caméra. `get_latest()` est non bloquant et
    renvoie la dernière frame disponible ainsi qu'un booléen indiquant si elle
    est nouvelle depuis le dernier appel (pour éviter de retraiter deux fois
    la même image côté pipeline).
    """

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self._capture: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._latest_frame: Optional[Frame] = None
        self._last_delivered_frame_id = -1
        self._frame_counter = 0

        self.frames_captured = 0
        self.frames_dropped = 0
        self.consecutive_errors = 0
        self.connected = False

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"camera-{self.config.camera_id}", daemon=True
        )
        self._thread.start()
        logger.info("Caméra %s : thread de capture démarré (source=%s)", self.config.camera_id, self.config.source)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        logger.info(
            "Caméra %s : arrêtée (%d frames capturées, %d abandonnées pour respecter target_fps)",
            self.config.camera_id, self.frames_captured, self.frames_dropped,
        )

    # ------------------------------------------------------------------ #
    # Lecture (appelée depuis le thread de la pipeline, pas celui de capture)
    # ------------------------------------------------------------------ #

    def get_latest(self) -> tuple[Optional[Frame], bool]:
        """Renvoie (frame, is_new). `frame` peut être None si rien n'a encore été capturé."""
        with self._lock:
            frame = self._latest_frame
            if frame is None:
                return None, False
            is_new = frame.frame_id != self._last_delivered_frame_id
            self._last_delivered_frame_id = frame.frame_id
            return frame, is_new

    # ------------------------------------------------------------------ #
    # Boucle interne (thread de capture)
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        if self.config.source == "synthetic":
            self._run_synthetic()
        else:
            self._run_real()

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        source = self.config.source
        # Un index webcam est passé en tant que chaîne ("0", "1"...) dans la config JSON.
        cap_source: object = int(source) if source.isdigit() else source
        cap = cv2.VideoCapture(cap_source)
        if self.config.width and self.config.height:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _run_real(self) -> None:
        min_interval = 1.0 / self.config.target_fps if self.config.target_fps > 0 else 0.0
        last_push = 0.0

        while not self._stop_event.is_set():
            if self._capture is None:
                self._capture = self._open_capture()
                if self._capture is None:
                    self.connected = False
                    logger.warning(
                        "Caméra %s : connexion échouée (%s), nouvelle tentative dans %.1fs",
                        self.config.camera_id, self.config.source, self.config.reconnect_delay_s,
                    )
                    self._stop_event.wait(self.config.reconnect_delay_s)
                    continue
                self.connected = True
                logger.info("Caméra %s : connectée", self.config.camera_id)

            ok, image = self._capture.read()
            if not ok or image is None:
                self.consecutive_errors += 1
                logger.warning(
                    "Caméra %s : lecture échouée (%d erreur(s) consécutive(s)), reconnexion",
                    self.config.camera_id, self.consecutive_errors,
                )
                self._capture.release()
                self._capture = None
                self.connected = False
                self._stop_event.wait(self.config.reconnect_delay_s)
                continue

            self.consecutive_errors = 0
            now = time.time()

            # On capture au rythme natif de la caméra mais on ne "publie" une frame
            # vers la pipeline qu'au débit voulu (target_fps), pour ne jamais donner
            # au Detector plus de frames qu'il n'en a besoin.
            if min_interval > 0 and (now - last_push) < min_interval:
                self.frames_dropped += 1
                continue
            last_push = now

            self._publish_frame(image, now)

    def _run_synthetic(self) -> None:
        """
        Génère des frames de test : un rectangle coloré traversant l'image en
        mouvement sinusoïdal, simulant un objet mobile. Permet de valider tout
        le pipeline (capture -> détection -> tracking -> publication) sans
        aucune caméra réelle.
        """
        width, height = self.config.width or 640, self.config.height or 480
        interval = 1.0 / self.config.target_fps if self.config.target_fps > 0 else 0.1
        t0 = time.time()
        self.connected = True

        while not self._stop_event.is_set():
            now = time.time()
            elapsed = now - t0
            image = np.full((height, width, 3), 30, dtype=np.uint8)

            box_w, box_h = max(20, width // 8), max(20, height // 4)
            cx = int((width - box_w) * (0.5 + 0.45 * np.sin(elapsed * 0.8)))
            cy = height // 2 - box_h // 2
            cv2.rectangle(image, (cx, cy), (cx + box_w, cy + box_h), (60, 200, 60), thickness=-1)
            cv2.putText(image, self.config.camera_id, (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (200, 200, 200), 2, cv2.LINE_AA)

            self._publish_frame(image, now)
            self._stop_event.wait(interval)

    def _publish_frame(self, image: np.ndarray, ts_capture: float) -> None:
        self._frame_counter += 1
        frame = Frame(
            camera_id=self.config.camera_id,
            frame_id=self._frame_counter,
            ts_capture=ts_capture,
            image=image,
        )
        with self._lock:
            self._latest_frame = frame
        self.frames_captured += 1
