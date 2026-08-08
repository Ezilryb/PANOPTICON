"""
panopticon/spectra/screen_state.py

Détection GROSSIÈRE d'état d'écran pour des zones d'intérêt définies
manuellement par l'opérateur (ex: "moniteur-accueil") : allumé/éteint (via
la luminosité moyenne de la zone) et statique/dynamique (via la différence
avec la frame précédente). NE LIT JAMAIS le contenu affiché — aucun OCR,
aucune reconnaissance d'objet sur ce qui est affiché à l'écran, uniquement
des signaux bas niveau (luminosité, différence de pixels).

Cf. section 3 du brief projet : c'est le remplaçant volontairement limité de
SNIFFER-CORE (lire à distance ce qui s'affiche sur l'écran d'un tiers), qui a
été explicitement exclu du périmètre car fonctionnellement équivalent à un
stalkerware. Cas d'usage visé : "un écran est resté allumé toute la nuit" —
rien de plus.
"""

import logging

import cv2
import numpy as np

from .config import ScreenRegionConfig
from .data_types import ScreenRegionState

logger = logging.getLogger("spectra.screen_state")


class ScreenStateMonitor:
    """
    Garde en mémoire le dernier recadrage (niveaux de gris) de chaque zone
    configurée pour calculer un score de mouvement par différence de frames.
    Une seule instance couvre TOUTES les caméras (les zones sont indexées par
    couple camera_id/nom, pas une instance par caméra comme IouTracker) :
    SPECTRA n'a qu'un seul ScreenStateMonitor pour tout le pipeline.
    """

    def __init__(self, regions: list[ScreenRegionConfig]) -> None:
        self._regions_by_camera: dict[str, list[ScreenRegionConfig]] = {}
        for region in regions:
            self._regions_by_camera.setdefault(region.camera_id, []).append(region)
        self._previous_crops: dict[tuple[str, str], np.ndarray] = {}  # (camera_id, region_name) -> recadrage gris

    def has_regions_for(self, camera_id: str) -> bool:
        """Permet à la pipeline d'éviter tout travail (même la conversion en gris) si non configuré."""
        return bool(self._regions_by_camera.get(camera_id))

    def update(self, camera_id: str, image: np.ndarray) -> list[ScreenRegionState]:
        regions = self._regions_by_camera.get(camera_id)
        if not regions:
            return []

        height, width = image.shape[0], image.shape[1]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        results: list[ScreenRegionState] = []

        for region in regions:
            x1, y1, x2, y2 = region.bbox
            cx1, cy1 = max(0, int(x1)), max(0, int(y1))
            cx2, cy2 = min(width, int(x2)), min(height, int(y2))
            if cx2 <= cx1 or cy2 <= cy1:
                logger.warning(
                    "Zone '%s' (caméra %s) hors des limites de l'image (%dx%d), ignorée",
                    region.region_name, camera_id, width, height,
                )
                continue

            crop = gray[cy1:cy2, cx1:cx2]
            brightness = float(crop.mean())
            is_on = brightness >= region.on_brightness_threshold

            key = (camera_id, region.region_name)
            previous = self._previous_crops.get(key)
            if previous is not None and previous.shape == crop.shape:
                motion_score = float(cv2.absdiff(crop, previous).mean())
            else:
                motion_score = 0.0  # première observation de cette zone : rien à comparer encore
            self._previous_crops[key] = crop

            results.append(ScreenRegionState(
                region_name=region.region_name,
                brightness=round(brightness, 2),
                is_on=is_on,
                is_static=motion_score < region.motion_threshold,
                motion_score=round(motion_score, 2),
            ))

        return results