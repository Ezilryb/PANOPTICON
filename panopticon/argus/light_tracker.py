"""
panopticon/argus/light_tracker.py

Tracker visuel léger pour le mode "detect_and_track" (cf. TrackingModeConfig) :
une instance = un objet suivi entre deux frames de détection complète. Deux
backends interchangeables : "optical_flow" (zéro nouvelle dépendance) et
"mosse" (opencv-contrib requis, le plus proche du ~1ms/objet visé).
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

import cv2
import numpy as np

from .config import TrackingModeConfig
from .data_types import BBox

logger = logging.getLogger("argus.light_tracker")


class BaseLightTracker(ABC):
    """
    Contrairement à BaseDetector (une instance partagée, appelée en lot sur
    toutes les caméras), UNE INSTANCE SUIT UN SEUL OBJET : build_light_tracker()
    doit être appelé une fois PAR PISTE à initialiser, pas une fois pour tout
    le moteur.
    """

    @abstractmethod
    def init(self, frame: np.ndarray, bbox: BBox) -> None:
        """Initialise (ou réinitialise) le tracker sur `bbox` dans `frame` (frame "lourde")."""

    @abstractmethod
    def update(self, frame: np.ndarray) -> tuple[bool, Optional[BBox]]:
        """
        Avance d'une frame "légère". Renvoie (succès, nouvelle_bbox).
        succès=False -> piste jugée perdue (occlusion, sortie de champ...) :
        l'appelant doit abandonner cette piste jusqu'à la prochaine frame lourde.
        """


class OpticalFlowTracker(BaseLightTracker):
    """
    Suit un petit nuage de points caractéristiques (goodFeaturesToTrack) à
    l'intérieur de la bbox initiale via flot optique pyramidal Lucas-Kanade,
    et déduit le déplacement de la bbox de la translation MÉDIANE du nuage
    (robuste à quelques points aberrants) — pas d'estimation d'échelle,
    volontairement simple vu la fenêtre courte entre deux recalibrations.

    Détection de piste perdue par erreur forward-backward (Kalal et al.) :
    chaque point est aussi suivi t+1 -> t, rejeté si l'aller-retour ne revient
    pas à moins de `of_fb_error_threshold` px du point de départ. Piste perdue
    si trop peu de points survivent (`of_min_surviving_points`).
    """

    def __init__(self, config: TrackingModeConfig) -> None:
        self.config = config
        self._prev_gray: Optional[np.ndarray] = None
        self._points: Optional[np.ndarray] = None      # shape (N, 1, 2), float32
        self._bbox: Optional[BBox] = None
        self._lk_params = dict(
            winSize=(config.of_win_size, config.of_win_size),
            maxLevel=config.of_max_pyramid_level,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03),
        )

    def init(self, frame: np.ndarray, bbox: BBox) -> None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        height, width = gray.shape[0], gray.shape[1]

        x1, y1, x2, y2 = bbox
        cx1, cy1 = max(0, int(x1)), max(0, int(y1))
        cx2, cy2 = min(width, int(x2)), min(height, int(y2))

        mask = np.zeros(gray.shape, dtype=np.uint8)
        if cx2 > cx1 and cy2 > cy1:
            mask[cy1:cy2, cx1:cx2] = 255

        points = cv2.goodFeaturesToTrack(
            gray, mask=mask, maxCorners=self.config.of_max_corners,
            qualityLevel=self.config.of_quality_level, minDistance=self.config.of_min_distance,
        )

        self._prev_gray = gray
        self._points = points  # peut être None si la bbox est trop petite/uniforme : update() le gère
        self._bbox = bbox

    def update(self, frame: np.ndarray) -> tuple[bool, Optional[BBox]]:
        if self._prev_gray is None or self._bbox is None:
            raise RuntimeError("OpticalFlowTracker.init() doit être appelé avant update()")
        if self._points is None or len(self._points) == 0:
            return False, None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

        next_points, status, _err = cv2.calcOpticalFlowPyrLK(
            self._prev_gray, gray, self._points, None, **self._lk_params
        )
        back_points, back_status, _err = cv2.calcOpticalFlowPyrLK(
            gray, self._prev_gray, next_points, None, **self._lk_params
        )
        fb_error = np.linalg.norm(self._points - back_points, axis=2).reshape(-1)

        valid = (
            (status.reshape(-1) == 1)
            & (back_status.reshape(-1) == 1)
            & (fb_error < self.config.of_fb_error_threshold)
        )

        if valid.sum() < self.config.of_min_surviving_points:
            return False, None

        old_valid = self._points[valid].reshape(-1, 2)
        new_valid = next_points[valid].reshape(-1, 2)

        shift = np.median(new_valid - old_valid, axis=0)
        x1, y1, x2, y2 = self._bbox
        new_bbox: BBox = (x1 + shift[0], y1 + shift[1], x2 + shift[0], y2 + shift[1])

        self._prev_gray = gray
        self._points = new_valid.reshape(-1, 1, 2).astype(np.float32)
        self._bbox = new_bbox
        return True, new_bbox


class MosseTracker(BaseLightTracker):
    """
    Tracker par corrélation MOSSE (Bolme et al., 2010) — le plus proche du
    ~1ms/objet visé. Nécessite `opencv-contrib-python-headless` (le module
    cv2.legacy/les trackers MOSSE ne sont PAS dans opencv-python-headless,
    la dépendance actuelle) : import différé, même principe que YoloDetector.

    NOTE : l'API Python n'expose que le booléen de succès d'update() (pas de
    score PSR configurable côté appelant) — la détection de piste perdue est
    entièrement déléguée à OpenCV, sans réglage fin possible ici.
    """

    def __init__(self, config: TrackingModeConfig) -> None:
        self.config = config
        self._tracker = None

    def _create_tracker(self):
        try:
            return cv2.legacy.TrackerMOSSE_create()   # opencv-contrib >= 4.5.1
        except AttributeError:
            pass
        try:
            return cv2.TrackerMOSSE_create()          # opencv-contrib plus ancien
        except AttributeError as exc:
            raise RuntimeError(
                "Le backend 'mosse' nécessite 'opencv-contrib-python-headless' "
                "(remplace 'opencv-python-headless' — même import cv2, aucun autre "
                "changement de code requis). Utilisez 'optical_flow' en attendant, "
                "ou installez la dépendance."
            ) from exc

    def init(self, frame: np.ndarray, bbox: BBox) -> None:
        x1, y1, x2, y2 = bbox
        cv_bbox = (int(x1), int(y1), int(x2 - x1), int(y2 - y1))  # OpenCV attend (x, y, w, h)
        self._tracker = self._create_tracker()
        self._tracker.init(frame, cv_bbox)

    def update(self, frame: np.ndarray) -> tuple[bool, Optional[BBox]]:
        if self._tracker is None:
            raise RuntimeError("MosseTracker.init() doit être appelé avant update()")
        ok, cv_bbox = self._tracker.update(frame)
        if not ok:
            return False, None
        x, y, w, h = cv_bbox
        return True, (float(x), float(y), float(x + w), float(y + h))


def build_light_tracker(config: TrackingModeConfig) -> BaseLightTracker:
    """Fabrique une NOUVELLE instance par piste (pas une instance partagée, cf. BaseLightTracker)."""
    if config.light_tracker_backend == "optical_flow":
        return OpticalFlowTracker(config)
    if config.light_tracker_backend == "mosse":
        return MosseTracker(config)
    raise ValueError(f"Backend de tracking léger inconnu : {config.light_tracker_backend!r} (attendu : 'optical_flow' ou 'mosse')")