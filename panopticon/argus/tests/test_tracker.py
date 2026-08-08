"""
panopticon/argus/tests/test_tracker.py

Tests unitaires d'IouTracker : persistance des track_id d'une frame à
l'autre pour un objet qui se déplace peu, création d'un nouveau track_id
pour un objet distinct, et suppression des pistes non retrouvées après
`max_age_frames` échecs consécutifs.
"""

import unittest

from argus.data_types import Detection
from argus.tracker import IouTracker, iou


class TestIou(unittest.TestCase):
    def test_iou_identical_boxes(self) -> None:
        box = (10.0, 10.0, 50.0, 50.0)
        self.assertAlmostEqual(iou(box, box), 1.0)

    def test_iou_disjoint_boxes(self) -> None:
        self.assertEqual(iou((0.0, 0.0, 10.0, 10.0), (100.0, 100.0, 110.0, 110.0)), 0.0)

    def test_iou_partial_overlap(self) -> None:
        # Deux carrés 10x10 se chevauchant sur un carré 5x5 : IoU = 25 / (100+100-25) = 25/175
        score = iou((0.0, 0.0, 10.0, 10.0), (5.0, 5.0, 15.0, 15.0))
        self.assertAlmostEqual(score, 25 / 175, places=4)


class TestIouTracker(unittest.TestCase):
    def test_track_id_persists_across_small_movement(self) -> None:
        tracker = IouTracker(iou_threshold=0.3, max_age_frames=2)

        frame1 = [Detection(0, "person", 0.9, (10, 10, 50, 100))]
        tracker.update(frame1)
        first_id = frame1[0].track_id
        self.assertIsNotNone(first_id)

        frame2 = [Detection(0, "person", 0.9, (12, 11, 52, 101))]
        tracker.update(frame2)
        self.assertEqual(frame2[0].track_id, first_id)

    def test_distinct_objects_get_distinct_ids(self) -> None:
        tracker = IouTracker()
        frame = [
            Detection(0, "person", 0.9, (10, 10, 50, 100)),
            Detection(1, "car", 0.8, (200, 200, 300, 260)),
        ]
        tracker.update(frame)
        self.assertNotEqual(frame[0].track_id, frame[1].track_id)

    def test_different_class_never_matches(self) -> None:
        tracker = IouTracker(iou_threshold=0.1)
        frame1 = [Detection(0, "person", 0.9, (10, 10, 50, 100))]
        tracker.update(frame1)
        person_id = frame1[0].track_id

        # Même bbox exactement, mais classe différente : ne doit jamais réutiliser le track_id.
        frame2 = [Detection(1, "car", 0.9, (10, 10, 50, 100))]
        tracker.update(frame2)
        self.assertNotEqual(frame2[0].track_id, person_id)

    def test_track_pruned_after_max_age(self) -> None:
        tracker = IouTracker(iou_threshold=0.3, max_age_frames=2)
        tracker.update([Detection(0, "person", 0.9, (10, 10, 50, 100))])
        tracker.update([Detection(1, "car", 0.8, (200, 200, 300, 260))])

        self.assertEqual(tracker.active_track_count, 2)

        # 3 frames sans la voiture : elle doit disparaître (max_age_frames=2 échecs tolérés).
        for _ in range(3):
            tracker.update([Detection(0, "person", 0.9, (10, 10, 50, 100))])

        self.assertEqual(tracker.active_track_count, 1)

    def test_new_object_never_reuses_a_pruned_id(self) -> None:
        tracker = IouTracker(iou_threshold=0.3, max_age_frames=1)
        frame1 = [Detection(1, "car", 0.8, (200, 200, 300, 260))]
        tracker.update(frame1)
        old_car_id = frame1[0].track_id

        tracker.update([])  # la voiture disparaît une frame
        tracker.update([])  # deuxième frame sans elle -> suppression (max_age_frames=1)

        frame_new = [Detection(1, "car", 0.7, (400, 400, 480, 460))]
        tracker.update(frame_new)
        self.assertNotEqual(frame_new[0].track_id, old_car_id)


if __name__ == "__main__":
    unittest.main()
