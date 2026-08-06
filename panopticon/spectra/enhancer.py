"""
panopticon/spectra/enhancer.py

Backends d'amélioration d'image interchangeables derrière une interface
commune (BaseEnhancer), même principe que `argus/detector.py` et
`roster/embedder.py`. `ClassicEnhancer` est le seul backend pour l'instant :
vision classique OpenCV (gamma adaptatif, CLAHE, filtre bilatéral, gray-world
white balance), sans dépendance lourde ni modèle à charger.

LIMITE HONNÊTE (à documenter, cf. section 5 du brief projet) : sans capteur
infrarouge dédié, l'amélioration purement logicielle d'une image capturée
dans le noir a des limites physiques réelles — on ne peut pas récupérer un
signal qui n'a jamais été capté. SPECTRA optimise ce que la caméra a
effectivement capturé (bruit, sous-exposition, dominante colorée), il ne
remplace pas un capteur adapté (IR, faible lux) pour un site réellement sombre.

Chaque technique n'est appliquée QUE si elle est utile sur la frame courante
(luminosité/contraste/dominante mesurés avant d'agir), pour ne jamais
dépenser de temps CPU sur une image déjà correcte — cohérent avec l'exigence
de faible latence du reste de PANOPTICON.
"""

import logging
import math
from abc import ABC, abstractmethod

import cv2
import numpy as np

from .config import EnhancerConfig
from .data_types import EnhancementResult

logger = logging.getLogger("spectra.enhancer")


class BaseEnhancer(ABC):
    """Interface commune : tout backend d'amélioration d'image doit l'implémenter."""

    @abstractmethod
    def warmup(self) -> None:
        """Prépare les ressources (ex: objet CLAHE) avant la première frame réelle."""

    @abstractmethod
    def enhance(self, image: np.ndarray) -> tuple[np.ndarray, EnhancementResult]:
        """Améliore `image` (BGR, uint8) et renvoie (image_améliorée, métriques avant/après)."""


class ClassicEnhancer(BaseEnhancer):
    """
    Pipeline en 3 étapes, chacune conditionnelle :
      1. Faible luminosité -> débruitage (filtre bilatéral) PUIS correction gamma adaptative.
         Le débruitage passe AVANT le gamma : le bruit capteur (gain analogique/ISO élevé en
         faible lumière) serait sinon amplifié par la correction gamma qui suit.
      2. Image "plate" (contraste mesuré après l'étape 1) -> CLAHE sur le canal L (LAB), qui
         préserve la couleur mieux qu'une égalisation d'histogramme globale sur BGR/gris.
      3. Dominante colorée marquée -> équilibrage des blancs gray-world (moyenne des 3 canaux
         ramenée à une valeur commune), cf. "séparation/fusion de canaux couleur" du brief.
    """

    def __init__(self, config: EnhancerConfig) -> None:
        self.config = config
        self._clahe: cv2.CLAHE | None = None

    def warmup(self) -> None:
        self._clahe = cv2.createCLAHE(
            clipLimit=self.config.clahe_clip_limit,
            tileGridSize=(self.config.clahe_tile_grid_size, self.config.clahe_tile_grid_size),
        )
        logger.info(
            "ClassicEnhancer prêt (gamma adaptatif + CLAHE + filtre bilatéral + gray-world, "
            "aucun modèle à charger)"
        )

    def enhance(self, image: np.ndarray) -> tuple[np.ndarray, EnhancementResult]:
        if self._clahe is None:
            raise RuntimeError("ClassicEnhancer.warmup() doit être appelé avant enhance()")

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        brightness_before = float(gray.mean())
        contrast_before = float(gray.std())

        output = image
        gamma_used: float | None = None
        low_light_applied = False
        denoise_applied = False
        contrast_applied = False
        white_balance_applied = False

        # 1. Faible luminosité : débruitage puis gamma, uniquement si nécessaire.
        if brightness_before < self.config.low_light_threshold:
            if self.config.denoise_enabled:
                output = self._apply_denoise(output)
                denoise_applied = True
            gamma_used = self._compute_gamma(brightness_before)
            output = self._apply_gamma(output, gamma_used)
            low_light_applied = True

        # 2. Contraste : mesuré à nouveau après l'étape 1 (le gamma a pu déjà changer la
        # distribution des niveaux) — on ne veut pas décider sur une mesure périmée.
        gray_current = cv2.cvtColor(output, cv2.COLOR_BGR2GRAY)
        if float(gray_current.std()) < self.config.low_contrast_threshold:
            output = self._apply_clahe(output)
            contrast_applied = True

        # 3. Dominante colorée : uniquement si notable et si l'opérateur n'a pas désactivé
        # l'équilibrage des blancs.
        if self.config.white_balance_enabled and self._has_color_cast(output):
            output = self._apply_white_balance(output)
            white_balance_applied = True

        gray_after = cv2.cvtColor(output, cv2.COLOR_BGR2GRAY)
        brightness_after = float(gray_after.mean())
        contrast_after = float(gray_after.std())

        result = EnhancementResult(
            brightness_before=brightness_before,
            brightness_after=brightness_after,
            contrast_before=contrast_before,
            contrast_after=contrast_after,
            low_light_correction_applied=low_light_applied,
            denoise_applied=denoise_applied,
            contrast_enhancement_applied=contrast_applied,
            white_balance_applied=white_balance_applied,
            gamma_used=gamma_used,
        )
        return output, result

    # ------------------------------------------------------------------ #
    # Étape 1 : faible luminosité
    # ------------------------------------------------------------------ #

    def _apply_denoise(self, image: np.ndarray) -> np.ndarray:
        # Filtre bilatéral : lisse le bruit tout en préservant les contours, et reste largement
        # plus rapide que cv2.fastNlMeansDenoisingColored — important, SPECTRA traite un flux
        # caméra en continu, pas une image isolée.
        return cv2.bilateralFilter(
            image,
            d=self.config.denoise_bilateral_d,
            sigmaColor=self.config.denoise_bilateral_sigma_color,
            sigmaSpace=self.config.denoise_bilateral_sigma_space,
        )

    def _compute_gamma(self, mean_brightness: float) -> float:
        """
        Choisit gamma tel que (mean_brightness/255)**gamma ≈ target_brightness/255, borné à
        [gamma_min, gamma_max] par sécurité (image quasi noire, division par un log proche de 0...).
        """
        target = self.config.target_brightness
        if mean_brightness < 1.0:
            return self.config.gamma_max
        ratio_in = mean_brightness / 255.0
        ratio_target = target / 255.0
        if ratio_in >= ratio_target:
            return 1.0  # défensif : ne devrait pas arriver vu le seuil d'appel (low_light_threshold < target)
        gamma = math.log(ratio_target) / math.log(ratio_in)
        return float(np.clip(gamma, self.config.gamma_min, self.config.gamma_max))

    @staticmethod
    def _apply_gamma(image: np.ndarray, gamma: float) -> np.ndarray:
        # Table de correspondance précalculée (0-255) : bien plus rapide qu'une puissance par
        # pixel. Appliquée identiquement sur B/G/R (pas seulement sur la luminance) : plus
        # simple et plus rapide qu'un passage par un espace luminance/chrominance dédié, au
        # prix d'un léger déplacement de saturation sur les cas extrêmes — compromis assumé
        # pour rester dans le budget de latence de SPECTRA.
        lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
        return cv2.LUT(image, lut)

    # ------------------------------------------------------------------ #
    # Étape 2 : contraste
    # ------------------------------------------------------------------ #

    def _apply_clahe(self, image: np.ndarray) -> np.ndarray:
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        l_enhanced = self._clahe.apply(l_channel)
        merged = cv2.merge([l_enhanced, a_channel, b_channel])
        return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)

    # ------------------------------------------------------------------ #
    # Étape 3 : dominante colorée
    # ------------------------------------------------------------------ #

    def _has_color_cast(self, image: np.ndarray) -> bool:
        b, g, r = (image[:, :, i].astype(np.float64).mean() for i in range(3))
        return (max(b, g, r) - min(b, g, r)) > self.config.color_cast_threshold

    @staticmethod
    def _apply_white_balance(image: np.ndarray) -> np.ndarray:
        b, g, r = cv2.split(image.astype(np.float64))
        mean_b, mean_g, mean_r = b.mean(), g.mean(), r.mean()
        mean_gray = (mean_b + mean_g + mean_r) / 3.0

        # Évite une division par zéro sur un canal totalement noir (image de test synthétique, etc.).
        scale_b = mean_gray / mean_b if mean_b > 1e-6 else 1.0
        scale_g = mean_gray / mean_g if mean_g > 1e-6 else 1.0
        scale_r = mean_gray / mean_r if mean_r > 1e-6 else 1.0

        b = np.clip(b * scale_b, 0, 255)
        g = np.clip(g * scale_g, 0, 255)
        r = np.clip(r * scale_r, 0, 255)
        return cv2.merge([b, g, r]).astype(np.uint8)


def build_enhancer(config: EnhancerConfig) -> BaseEnhancer:
    """Fabrique le backend demandé par la configuration."""
    if config.backend == "classic":
        return ClassicEnhancer(config)
    raise ValueError(f"Backend d'amélioration inconnu : {config.backend!r} (attendu : 'classic')")