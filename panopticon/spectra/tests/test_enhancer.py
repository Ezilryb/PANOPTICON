"""
panopticon/spectra/tests/test_enhancer.py

Tests unitaires de ClassicEnhancer : la correction gamma (+ débruitage) ne se
déclenche que sur une image sombre et rapproche effectivement la luminosité
de la cible, CLAHE ne se déclenche que sur une image plate et augmente le
contraste, l'équilibrage des blancs ne se déclenche que sur une dominante
colorée marquée et rapproche les canaux, et une image qui n'a besoin
d'aucune correction ressort avec tous les drapeaux à False.
"""

import unittest

import numpy as np

from spectra.config import EnhancerConfig
from spectra.enhancer import ClassicEnhancer


def _noisy_image(mean: float, spread: float, shape=(120, 160, 3), seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(loc=0.0, scale=spread, size=shape)
    return np.clip(mean + noise, 0, 255).astype(np.uint8)


def _color_cast_image(shape=(120, 160, 3)) -> np.ndarray:
    # BGR : canal bleu nettement dominant (ex: éclairage LED bleuté), avec un peu de texture
    # (pas une image parfaitement plate) pour rester représentatif d'une vraie frame caméra.
    rng = np.random.default_rng(1)
    image = np.zeros(shape, dtype=np.float64)
    image[:, :, 0] = 200 + rng.normal(0, 5, shape[:2])  # B
    image[:, :, 1] = 110 + rng.normal(0, 5, shape[:2])  # G
    image[:, :, 2] = 100 + rng.normal(0, 5, shape[:2])  # R
    return np.clip(image, 0, 255).astype(np.uint8)


class TestClassicEnhancer(unittest.TestCase):
    def setUp(self) -> None:
        self.enhancer = ClassicEnhancer(EnhancerConfig())
        self.enhancer.warmup()

    def test_warmup_required_before_enhance(self) -> None:
        fresh = ClassicEnhancer(EnhancerConfig())
        with self.assertRaises(RuntimeError):
            fresh.enhance(_noisy_image(100, 20))

    def test_dark_image_gets_brightened_and_denoised(self) -> None:
        image = _noisy_image(mean=25, spread=10)
        _output, result = self.enhancer.enhance(image)
        self.assertTrue(result.low_light_correction_applied)
        self.assertTrue(result.denoise_applied)
        self.assertIsNotNone(result.gamma_used)
        self.assertLess(result.gamma_used, 1.0)  # gamma < 1 pour éclaircir
        self.assertGreater(result.brightness_after, result.brightness_before)
        # Se rapproche de la cible (peut légèrement la dépasser à cause des étapes suivantes).
        self.assertLess(
            abs(result.brightness_after - self.enhancer.config.target_brightness),
            abs(result.brightness_before - self.enhancer.config.target_brightness),
        )

    def test_well_lit_image_is_not_gamma_corrected(self) -> None:
        image = _noisy_image(mean=140, spread=45)
        _output, result = self.enhancer.enhance(image)
        self.assertFalse(result.low_light_correction_applied)
        self.assertFalse(result.denoise_applied)
        self.assertIsNone(result.gamma_used)

    def test_flat_image_gets_contrast_enhanced(self) -> None:
        image = _noisy_image(mean=140, spread=3)  # quasi uniforme : contraste initial très faible
        _output, result = self.enhancer.enhance(image)
        self.assertTrue(result.contrast_enhancement_applied)
        self.assertGreaterEqual(result.contrast_after, result.contrast_before)

    def test_high_contrast_image_is_not_clahe_corrected(self) -> None:
        # NB : la conversion BGR->gris moyenne 3 canaux de bruit indépendants (0.299R+0.587G+0.114B),
        # ce qui réduit le std résultant nettement sous le spread par canal (loi des grands nombres
        # sur une combinaison de variables indépendantes) — spread=100 laisse une marge confortable
        # au-dessus de low_contrast_threshold=35 une fois converti (vérifié empiriquement : std ~55).
        image = _noisy_image(mean=140, spread=100)
        _output, result = self.enhancer.enhance(image)
        self.assertFalse(result.contrast_enhancement_applied)

    def test_color_cast_is_corrected(self) -> None:
        image = _color_cast_image()
        b0, g0, r0 = (image[:, :, i].astype(np.float64).mean() for i in range(3))
        self.assertGreater(max(b0, g0, r0) - min(b0, g0, r0), 50.0)  # bien une dominante marquée au départ

        output, result = self.enhancer.enhance(image)
        self.assertTrue(result.white_balance_applied)
        b1, g1, r1 = (output[:, :, i].astype(np.float64).mean() for i in range(3))
        self.assertLess(max(b1, g1, r1) - min(b1, g1, r1), max(b0, g0, r0) - min(b0, g0, r0))

    def test_neutral_image_is_not_white_balanced(self) -> None:
        image = _noisy_image(mean=140, spread=25)  # même distribution sur les 3 canaux -> pas de dominante
        _output, result = self.enhancer.enhance(image)
        self.assertFalse(result.white_balance_applied)

    def test_output_shape_and_dtype_match_input(self) -> None:
        image = _noisy_image(mean=40, spread=10, shape=(90, 130, 3))
        output, _result = self.enhancer.enhance(image)
        self.assertEqual(output.shape, image.shape)
        self.assertEqual(output.dtype, image.dtype)

    def test_white_balance_disabled_via_config(self) -> None:
        config = EnhancerConfig(white_balance_enabled=False)
        enhancer = ClassicEnhancer(config)
        enhancer.warmup()
        image = _color_cast_image()
        _output, result = enhancer.enhance(image)
        self.assertFalse(result.white_balance_applied)

    def test_denoise_disabled_via_config_still_brightens(self) -> None:
        config = EnhancerConfig(denoise_enabled=False)
        enhancer = ClassicEnhancer(config)
        enhancer.warmup()
        image = _noisy_image(mean=25, spread=10)
        _output, result = enhancer.enhance(image)
        self.assertFalse(result.denoise_applied)
        self.assertTrue(result.low_light_correction_applied)

    def test_gamma_bounds_are_respected_on_extreme_darkness(self) -> None:
        image = np.zeros((80, 80, 3), dtype=np.uint8)  # image totalement noire
        _output, result = self.enhancer.enhance(image)
        self.assertTrue(result.low_light_correction_applied)
        self.assertGreaterEqual(result.gamma_used, self.enhancer.config.gamma_min)
        self.assertLessEqual(result.gamma_used, self.enhancer.config.gamma_max)


if __name__ == "__main__":
    unittest.main()