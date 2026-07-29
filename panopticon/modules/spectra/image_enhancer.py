"""SPECTRA — Phase 3 : amélioration d'image et détection d'état d'écran.

Détection d'état d'écran : signaux photométriques globaux uniquement
(luminosité moyenne, taux de changement pixel à pixel entre frames
successives) — jamais de lecture, d'OCR ni d'interprétation du contenu
affiché. Conforme à la contrainte éthique du projet (voir README).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# --- Amélioration d'image ---------------------------------------------------


def apply_clahe(frame: np.ndarray, clip_limit: float = 2.0, tile_grid: int = 8) -> np.ndarray:
    """Égalisation d'histogramme adaptative (contraste local) sur le canal de luminance."""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_grid, tile_grid))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def apply_gamma(frame: np.ndarray, gamma: float = 1.0) -> np.ndarray:
    """Correction gamma : gamma > 1 éclaircit l'image, gamma < 1 l'assombrit (gamma=1 : inchangé)."""
    if gamma is None or gamma == 1.0:
        return frame
    inv = 1.0 / max(gamma, 1e-6)
    table = (np.linspace(0, 1, 256) ** inv * 255).astype("uint8")
    return cv2.LUT(frame, table)


def denoise(frame: np.ndarray, strength: int = 5) -> np.ndarray:
    """Débruitage léger (filtre bilatéral : lisse le bruit tout en préservant les contours)."""
    s = max(1, strength)
    return cv2.bilateralFilter(frame, d=5, sigmaColor=s * 10, sigmaSpace=s * 10)


def enhance_frame(
    frame: np.ndarray,
    use_clahe: bool = True,
    gamma: float | None = None,
    use_denoise: bool = False,
) -> np.ndarray:
    """Pipeline d'amélioration optionnelle pensé pour la vidéosurveillance en faible lumière.

    Désactivé par défaut côté ARGUS (voir ``PANOPTICON_SPECTRA_ENHANCE_FRAMES``
    dans .env) : n'affecte jamais le comportement existant sans opt-in.
    """
    out = frame
    if use_clahe:
        out = apply_clahe(out)
    if gamma is not None:
        out = apply_gamma(out, gamma)
    if use_denoise:
        out = denoise(out)
    return out


def mean_luminance(frame: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> float:
    """Luminance moyenne (0-255) de l'image entière ou d'une région (x1, y1, x2, y2)."""
    region = frame
    if roi:
        x1, y1, x2, y2 = roi
        region = frame[max(0, y1):y2, max(0, x1):x2]
    if region.size == 0:
        return 0.0
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    return float(np.mean(gray))


# --- Détection d'état d'écran (sans lecture de contenu) ---------------------


@dataclass
class ScreenState:
    is_on: bool
    is_dynamic: bool
    luminance: float
    change_score: float


class ScreenStateAnalyzer:
    """Détecte si un écran est allumé/éteint et statique/dynamique entre frames successives.

    N'observe que des signaux globaux (luminosité moyenne, différence
    pixel à pixel sous-échantillonnée) : aucune reconnaissance de texte,
    d'objet, ni interprétation du contenu affiché.
    """

    def __init__(
        self,
        roi: tuple[int, int, int, int] | None = None,
        on_threshold: float = 15.0,
        change_threshold: float = 2.0,
    ) -> None:
        self.roi = roi
        self.on_threshold = on_threshold
        self.change_threshold = change_threshold
        self._prev_gray: np.ndarray | None = None
        self._last_state: ScreenState | None = None

    def _region(self, frame: np.ndarray) -> np.ndarray:
        if not self.roi:
            return frame
        x1, y1, x2, y2 = self.roi
        return frame[max(0, y1):y2, max(0, x1):x2]

    def analyze(self, frame: np.ndarray) -> ScreenState:
        region = self._region(frame)
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
        gray = cv2.resize(gray, (160, 90))  # sous-échantillonnage volontaire : signal global seulement

        luminance = float(np.mean(gray))
        is_on = luminance >= self.on_threshold

        change_score = 0.0
        if self._prev_gray is not None:
            diff = cv2.absdiff(gray, self._prev_gray)
            change_score = float(np.mean(diff))
        is_dynamic = is_on and change_score >= self.change_threshold

        self._prev_gray = gray
        return ScreenState(is_on=is_on, is_dynamic=is_dynamic, luminance=luminance, change_score=change_score)

    def update(self, frame: np.ndarray) -> tuple[bool, ScreenState]:
        """Retourne (changement_detecte, nouvel_etat) par rapport au dernier appel à update()."""
        state = self.analyze(frame)
        changed = self._last_state is None or (
            state.is_on != self._last_state.is_on or state.is_dynamic != self._last_state.is_dynamic
        )
        self._last_state = state
        return changed, state
