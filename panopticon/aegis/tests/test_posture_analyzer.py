"""
panopticon/aegis/tests/test_posture_analyzer.py

Tests unitaires de MockPostureAnalyzer : classification par aspect ratio
(seuils "lying"/"upright"/"uncertain"), déterminisme, confiance croissante
avec la distance au seuil, et estimation d'orientation best-effort (formes
contrôlées + repli sur None quand le recadrage est trop petit/peu
texturé). Teste aussi build_analyzer() et le comportement de garde
(warmup()/analyze() avant warmup(), backend inconnu) de YoloPoseAnalyzer
SANS jamais nécessiter `ultralytics` installé, ni aucun appel réseau/GPU —
même esprit que oracle/tests/test_identifier.py pour GoogleVisionIdentifier.
"""

import unittest

import numpy as np
import cv2

from aegis.config import AnalyzerConfig
from aegis.posture_analyzer import (
    MockPostureAnalyzer,
    YoloPoseAnalyzer,
    build_analyzer,
    classify_by_aspect_ratio,
    confidence_from_aspect_ratio,
    estimate_orientation_deg,
)


class TestClassifyByAspectRatio(unittest.TestCase):
    def test_wide_bbox_is_lying(self) -> None:
        self.assertEqual(classify_by_aspect_ratio(2.0, lying_threshold=1.3, upright_threshold=0.8), "lying")

    def test_tall_bbox_is_upright(self) -> None:
        self.assertEqual(classify_by_aspect_ratio(0.4, lying_threshold=1.3, upright_threshold=0.8), "upright")

    def test_middle_ratio_is_uncertain(self) -> None:
        self.assertEqual(classify_by_aspect_ratio(1.0, lying_threshold=1.3, upright_threshold=0.8), "uncertain")

    def test_boundary_values_are_inclusive(self) -> None:
        self.assertEqual(classify_by_aspect_ratio(1.3, lying_threshold=1.3, upright_threshold=0.8), "lying")
        self.assertEqual(classify_by_aspect_ratio(0.8, lying_threshold=1.3, upright_threshold=0.8), "upright")


class TestConfidenceFromAspectRatio(unittest.TestCase):
    def test_confidence_grows_with_distance_past_lying_threshold(self) -> None:
        near = confidence_from_aspect_ratio(1.35, lying_threshold=1.3, upright_threshold=0.8)
        far = confidence_from_aspect_ratio(3.0, lying_threshold=1.3, upright_threshold=0.8)
        self.assertLess(near, far)

    def test_confidence_never_exceeds_cap(self) -> None:
        extreme = confidence_from_aspect_ratio(50.0, lying_threshold=1.3, upright_threshold=0.8)
        self.assertLessEqual(extreme, 0.95)

    def test_uncertain_zone_has_lower_confidence_than_either_side(self) -> None:
        uncertain = confidence_from_aspect_ratio(1.05, lying_threshold=1.3, upright_threshold=0.8)
        lying_side = confidence_from_aspect_ratio(1.3, lying_threshold=1.3, upright_threshold=0.8)
        upright_side = confidence_from_aspect_ratio(0.8, lying_threshold=1.3, upright_threshold=0.8)
        self.assertLess(uncertain, lying_side)
        self.assertLess(uncertain, upright_side)


class TestEstimateOrientationDeg(unittest.TestCase):
    def test_horizontal_shape_is_near_90_degrees(self) -> None:
        image = np.full((100, 200, 3), 30, dtype=np.uint8)
        cv2.rectangle(image, (20, 40), (180, 60), (200, 200, 200), thickness=-1)
        cv2.rectangle(image, (40, 45), (60, 55), (10, 10, 10), thickness=-1)
        angle = estimate_orientation_deg(image)
        self.assertIsNotNone(angle)
        self.assertGreater(angle, 80.0)

    def test_vertical_shape_is_near_0_degrees(self) -> None:
        image = np.full((200, 100, 3), 30, dtype=np.uint8)
        cv2.rectangle(image, (40, 20), (60, 180), (200, 200, 200), thickness=-1)
        cv2.rectangle(image, (45, 40), (55, 60), (10, 10, 10), thickness=-1)
        angle = estimate_orientation_deg(image)
        self.assertIsNotNone(angle)
        self.assertLess(angle, 10.0)

    def test_blank_crop_returns_none(self) -> None:
        image = np.full((100, 100, 3), 50, dtype=np.uint8)  # aucun contour exploitable
        self.assertIsNone(estimate_orientation_deg(image))

    def test_tiny_crop_returns_none(self) -> None:
        image = np.full((3, 3, 3), 50, dtype=np.uint8)
        self.assertIsNone(estimate_orientation_deg(image))

    def test_empty_crop_returns_none(self) -> None:
        image = np.zeros((0, 0, 3), dtype=np.uint8)
        self.assertIsNone(estimate_orientation_deg(image))


class TestMockPostureAnalyzer(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = MockPostureAnalyzer(AnalyzerConfig())
        self.analyzer.warmup()

    def test_tall_bbox_is_upright(self) -> None:
        crop = np.zeros((100, 50, 3), dtype=np.uint8)
        result = self.analyzer.analyze(crop, bbox=(0.0, 0.0, 50.0, 100.0))
        self.assertEqual(result.posture, "upright")
        self.assertEqual(result.source, "mock")

    def test_wide_bbox_is_lying(self) -> None:
        crop = np.zeros((50, 100, 3), dtype=np.uint8)
        result = self.analyzer.analyze(crop, bbox=(0.0, 0.0, 100.0, 50.0))
        self.assertEqual(result.posture, "lying")

    def test_square_ish_bbox_is_uncertain(self) -> None:
        crop = np.zeros((100, 100, 3), dtype=np.uint8)
        result = self.analyzer.analyze(crop, bbox=(0.0, 0.0, 100.0, 100.0))
        self.assertEqual(result.posture, "uncertain")

    def test_deterministic_for_same_input(self) -> None:
        crop = np.zeros((50, 100, 3), dtype=np.uint8)
        bbox = (0.0, 0.0, 100.0, 50.0)
        r1 = self.analyzer.analyze(crop, bbox)
        r2 = self.analyzer.analyze(crop, bbox)
        self.assertEqual(r1.posture, r2.posture)
        self.assertEqual(r1.confidence, r2.confidence)

    def test_aspect_ratio_uses_original_bbox_not_padded_crop(self) -> None:
        # Le recadrage (avec marge) peut avoir un ratio différent de la bbox ARGUS d'origine :
        # la classification doit suivre la bbox, jamais les dimensions du recadrage lui-même.
        padded_crop = np.zeros((80, 120, 3), dtype=np.uint8)  # ratio du recadrage : 1.5 (lying)
        result = self.analyzer.analyze(padded_crop, bbox=(0.0, 0.0, 40.0, 100.0))  # ratio bbox : 0.4 (upright)
        self.assertEqual(result.posture, "upright")


class TestBuildAnalyzer(unittest.TestCase):
    def test_mock_backend(self) -> None:
        analyzer = build_analyzer(AnalyzerConfig(backend="mock"))
        self.assertIsInstance(analyzer, MockPostureAnalyzer)

    def test_yolo_pose_backend(self) -> None:
        analyzer = build_analyzer(AnalyzerConfig(backend="yolo_pose"))
        self.assertIsInstance(analyzer, YoloPoseAnalyzer)

    def test_unknown_backend_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_analyzer(AnalyzerConfig(backend="does-not-exist"))


class TestYoloPoseAnalyzerGuards(unittest.TestCase):
    """Vérifie les gardes de YoloPoseAnalyzer sans jamais nécessiter `ultralytics` installé."""

    def test_analyze_before_warmup_raises(self) -> None:
        analyzer = YoloPoseAnalyzer(AnalyzerConfig())
        crop = np.zeros((100, 50, 3), dtype=np.uint8)
        with self.assertRaises(RuntimeError):
            analyzer.analyze(crop, bbox=(0.0, 0.0, 50.0, 100.0))


if __name__ == "__main__":
    unittest.main()
