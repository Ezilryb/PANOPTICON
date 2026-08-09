"""
panopticon/argus/tests/test_light_tracker.py

Tests unitaires d'OpticalFlowTracker : suivi d'un damier texturé translaté,
piste perdue sur zone sans texture, stabilité sur plusieurs frames légères
successives. MosseTracker testé séparément, seulement si opencv-contrib
est disponible dans l'environnement.
"""

import unittest

import cv2
import numpy as np

from argus.config import TrackingModeConfig
from argus.light_tracker import OpticalFlowTracker, build_light_tracker


def _checkerboard(shape=(200, 200, 3), square: int = 10, offset=(0, 0)) -> np.ndarray:
    """Damier haut-contraste : plein de coins exploitables par goodFeaturesToTrack, contrairement à un aplat uni."""
    height, width = shape[0], shape[1]
    image = np.full(shape, 200, dtype=np.uint8)
    ox, oy = offset
    for y in range(-square, height + square, square):
        for x in range(-square, width + square, square):
            if ((x - ox) // square + (y - oy) // square) % 2 == 0:
                x1, y1 = max(0, x), max(0, y)
                x2, y2 = min(width, x + square), min(height, y + square)
                if x2 > x1 and y2 > y1:
                    image[y1:y2, x1:x2] = 30
    return image


class TestOpticalFlowTracker(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = OpticalFlowTracker(TrackingModeConfig())

    def test_static_object_stays_in_place(self) -> None:
        frame = _checkerboard()
        bbox = (50.0, 50.0, 150.0, 150.0)
        self.tracker.init(frame, bbox)

        ok, new_bbox = self.tracker.update(frame)  # même frame, aucun mouvement réel
        self.assertTrue(ok)
        for original, updated in zip(bbox, new_bbox):
            self.assertAlmostEqual(original, updated, delta=1.0)

    def test_shifted_object_is_tracked(self) -> None:
        shift_x, shift_y = 15, 8
        frame_a = _checkerboard(offset=(0, 0))
        frame_b = _checkerboard(offset=(shift_x, shift_y))
        bbox = (50.0, 50.0, 150.0, 150.0)
        self.tracker.init(frame_a, bbox)

        ok, new_bbox = self.tracker.update(frame_b)
        self.assertTrue(ok)
        self.assertAlmostEqual(new_bbox[0] - bbox[0], shift_x, delta=4.0)
        self.assertAlmostEqual(new_bbox[1] - bbox[1], shift_y, delta=4.0)

    def test_textureless_region_reports_failure(self) -> None:
        blank = np.full((200, 200, 3), 128, dtype=np.uint8)  # aucun coin exploitable
        self.tracker.init(blank, (50.0, 50.0, 150.0, 150.0))
        ok, result = self.tracker.update(blank)
        self.assertFalse(ok)
        self.assertIsNone(result)

    def test_update_before_init_raises(self) -> None:
        fresh = OpticalFlowTracker(TrackingModeConfig())
        with self.assertRaises(RuntimeError):
            fresh.update(_checkerboard())

    def test_multiple_light_frames_stay_reasonably_centered(self) -> None:
        """Simule un cycle detect_and_track (4 frames légères sans mouvement réel) : la bbox ne doit pas s'envoler."""
        frame = _checkerboard()
        bbox = (50.0, 50.0, 150.0, 150.0)
        self.tracker.init(frame, bbox)

        for _ in range(4):
            ok, bbox = self.tracker.update(frame)
            self.assertTrue(ok)

        self.assertAlmostEqual(bbox[0], 50.0, delta=3.0)
        self.assertAlmostEqual(bbox[1], 50.0, delta=3.0)


class TestBuildLightTracker(unittest.TestCase):
    def test_optical_flow_backend(self) -> None:
        tracker = build_light_tracker(TrackingModeConfig(light_tracker_backend="optical_flow"))
        self.assertIsInstance(tracker, OpticalFlowTracker)

    def test_unknown_backend_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_light_tracker(TrackingModeConfig(light_tracker_backend="does-not-exist"))

    @unittest.skipUnless(hasattr(cv2, "legacy") or hasattr(cv2, "TrackerMOSSE_create"),
                          "opencv-contrib indisponible dans cet environnement")
    def test_mosse_backend_tracks_a_static_frame(self) -> None:
        from argus.light_tracker import MosseTracker
        tracker = build_light_tracker(TrackingModeConfig(light_tracker_backend="mosse"))
        self.assertIsInstance(tracker, MosseTracker)

        frame = _checkerboard()
        bbox = (50.0, 50.0, 150.0, 150.0)
        tracker.init(frame, bbox)
        ok, new_bbox = tracker.update(frame)
        self.assertTrue(ok)
        self.assertAlmostEqual(new_bbox[0], bbox[0], delta=3.0)


if __name__ == "__main__":
    unittest.main()