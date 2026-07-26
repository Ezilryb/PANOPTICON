"""Lecture de flux vidéo avec reconnexion."""

import logging
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class StreamReader:
    def __init__(self, url: str, reconnect_base: float = 1.0, reconnect_max: float = 30.0):
        self.url = url
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self._cap: cv2.VideoCapture | None = None
        self._attempt = 0

    def connect(self) -> bool:
        self.release()
        cap = cv2.VideoCapture(self.url)
        if not cap.isOpened():
            logger.warning("Impossible d'ouvrir le flux: %s", self.url)
            return False
        self._cap = cap
        self._attempt = 0
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._cap is None or not self._cap.isOpened():
            if not self._reconnect():
                return False, None
        assert self._cap is not None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            logger.warning("Frame invalide, reconnexion…")
            if not self._reconnect():
                return False, None
            ok, frame = self._cap.read()
        return ok, frame

    def _reconnect(self) -> bool:
        delay = min(self.reconnect_base * (2**self._attempt), self.reconnect_max)
        self._attempt += 1
        logger.info("Reconnexion dans %.1fs (tentative %d)", delay, self._attempt)
        time.sleep(delay)
        return self.connect()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
