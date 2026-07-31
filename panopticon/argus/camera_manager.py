"""
panopticon/argus/camera_manager.py

Gère le cycle de vie de toutes les CameraSource déclarées dans la
configuration et fournit à la pipeline un point d'accès unique pour
récupérer, à chaque itération, les frames nouvellement disponibles sur
l'ensemble des caméras actives.
"""

import logging

from .camera_source import CameraSource
from .config import ArgusConfig
from .data_types import Frame

logger = logging.getLogger("argus.camera_manager")


class CameraManager:
    def __init__(self, config: ArgusConfig) -> None:
        self._sources: dict[str, CameraSource] = {
            cam.camera_id: CameraSource(cam) for cam in config.cameras if cam.enabled
        }
        if not self._sources:
            logger.warning("Aucune caméra activée dans la configuration")

    def start(self) -> None:
        for source in self._sources.values():
            source.start()
        logger.info("CameraManager : %d caméra(s) démarrée(s)", len(self._sources))

    def stop(self) -> None:
        for source in self._sources.values():
            source.stop()

    def poll_new_frames(self) -> dict[str, Frame]:
        """Renvoie {camera_id: Frame} uniquement pour les caméras ayant produit une frame inédite."""
        new_frames: dict[str, Frame] = {}
        for camera_id, source in self._sources.items():
            frame, is_new = source.get_latest()
            if frame is not None and is_new:
                new_frames[camera_id] = frame
        return new_frames

    @property
    def camera_ids(self) -> list[str]:
        return list(self._sources.keys())

    def stats(self) -> dict[str, dict]:
        return {
            camera_id: {
                "connected": src.connected,
                "frames_captured": src.frames_captured,
                "frames_dropped": src.frames_dropped,
            }
            for camera_id, src in self._sources.items()
        }
