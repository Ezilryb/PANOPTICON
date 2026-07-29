"""Service SPECTRA — surveillance d'état d'écran à partir des frames ARGUS.

Ne se connecte à aucune caméra directement : relit les frames déjà écrites
par ARGUS (``data/argus/frames/{camera_id}.jpg``), ce qui évite une seconde
connexion vidéo par caméra. D'où la dépendance ``argus`` déclarée dans
``daemon/module_registry.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from uuid import UUID

import cv2

from modules.event_sink import emit
from modules.spectra.image_enhancer import ScreenStateAnalyzer
from shared.models import DetectionEvent

logger = logging.getLogger(__name__)

FRAMES_DIR = Path("./data/argus/frames")
POLL_INTERVAL = 2.0
ZONE_CACHE_REFRESH_EVERY = 15  # ~30s à 2s/itération


def _db_path() -> str:
    from shared.config import settings

    url = settings.database_url
    if url.startswith("sqlite"):
        return url.split("///")[-1]
    return "./data/panopticon.db"


def _load_camera_zones() -> dict[str, str]:
    """Associe chaque camera_id à sa zone, pour que les événements SPECTRA soient filtrables comme ceux d'ARGUS."""
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, zone FROM cameras").fetchall()
        conn.close()
        return {r["id"]: r["zone"] for r in rows}
    except Exception:
        logger.exception("Impossible de charger les zones caméras pour SPECTRA")
        return {}


def run_spectra() -> None:
    from shared.config import settings
    from shared.logging_utils import setup_logging

    setup_logging(settings.log_level)
    logger.info("SPECTRA actif — surveillance état d'écran sur %s", FRAMES_DIR)

    analyzers: dict[str, ScreenStateAnalyzer] = {}
    zones = _load_camera_zones()
    iterations = 0

    while True:
        iterations += 1
        if iterations % ZONE_CACHE_REFRESH_EVERY == 0:
            zones = _load_camera_zones()

        if FRAMES_DIR.exists():
            for frame_path in sorted(FRAMES_DIR.glob("*.jpg")):
                camera_id = frame_path.stem
                frame = cv2.imread(str(frame_path))
                if frame is None:
                    continue

                analyzer = analyzers.setdefault(camera_id, ScreenStateAnalyzer())
                try:
                    changed, state = analyzer.update(frame)
                except Exception:
                    logger.exception("Erreur d'analyse SPECTRA pour la caméra %s", camera_id)
                    continue

                if not changed:
                    continue
                try:
                    cam_uuid = UUID(camera_id)
                except ValueError:
                    continue

                event = DetectionEvent(
                    camera_id=cam_uuid,
                    source_module="spectra",
                    event_type="screen_state_changed",
                    zone=zones.get(camera_id, "default"),
                    metadata={
                        "is_on": state.is_on,
                        "is_dynamic": state.is_dynamic,
                        "luminance": round(state.luminance, 1),
                    },
                )
                try:
                    emit(event)
                except Exception:
                    logger.exception("Erreur de publication d'événement SPECTRA pour %s", camera_id)
                    continue
                logger.info(
                    "Caméra %s: écran %s, %s",
                    camera_id,
                    "allumé" if state.is_on else "éteint",
                    "dynamique" if state.is_dynamic else "statique",
                )

        time.sleep(POLL_INTERVAL)
