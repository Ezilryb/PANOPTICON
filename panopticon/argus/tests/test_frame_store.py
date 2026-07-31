"""
panopticon/argus/tests/test_frame_store.py

Tests unitaires du ring buffer en mémoire partagée : écriture/lecture d'une
frame, absence de nouveauté détectée entre deux lectures identiques, et
rotation correcte des slots sur plusieurs écritures successives.
"""

import unittest
import uuid

import numpy as np

from argus.frame_store import FrameReader, SharedFrameStore


class TestSharedFrameStore(unittest.TestCase):
    def setUp(self) -> None:
        # Nom de caméra unique par test pour ne jamais collisionner avec un segment d'un test précédent.
        self.camera_id = f"TEST-{uuid.uuid4().hex[:8]}"
        self.store = SharedFrameStore(self.camera_id, slot_size_bytes=200_000, slots=3)
        self.reader = FrameReader(self.camera_id, slot_size_bytes=200_000, slots=3)

    def tearDown(self) -> None:
        self.reader.close()
        self.store.close()

    def test_nothing_available_before_first_write(self) -> None:
        self.assertIsNone(self.reader.read_latest())

    def test_write_then_read_round_trip(self) -> None:
        image = np.full((100, 120, 3), 50, dtype=np.uint8)
        self.store.write(image, frame_id=1, ts_capture=111.0)

        result = self.reader.read_latest()
        self.assertIsNotNone(result)
        frame_id, ts_capture, decoded = result
        self.assertEqual(frame_id, 1)
        self.assertEqual(ts_capture, 111.0)
        self.assertEqual(decoded.shape, (100, 120, 3))
        # JPEG est destructif : on vérifie une valeur moyenne proche, pas une égalité stricte.
        self.assertAlmostEqual(float(decoded.mean()), 50.0, delta=2.0)

    def test_no_new_frame_returns_none(self) -> None:
        image = np.full((100, 120, 3), 50, dtype=np.uint8)
        self.store.write(image, frame_id=1, ts_capture=111.0)
        self.reader.read_latest()  # première lecture : consomme la nouveauté
        self.assertIsNone(self.reader.read_latest())  # rien de nouveau depuis

    def test_ring_buffer_rotation_keeps_latest_frame_readable(self) -> None:
        for i in range(1, 9):  # plus que le nombre de slots (3) pour forcer la rotation
            image = np.full((100, 120, 3), i * 10 % 255, dtype=np.uint8)
            self.store.write(image, frame_id=i, ts_capture=100.0 + i)
            frame_id, _ts, decoded = self.reader.read_latest()
            self.assertEqual(frame_id, i)
            self.assertAlmostEqual(float(decoded.mean()), i * 10 % 255, delta=2.0)

    def test_oversized_frame_is_ignored_not_crashed(self) -> None:
        # Slot volontairement minuscule : même une image JPEG bien compressée ne peut pas y tenir,
        # sans dépendre d'une hypothèse sur le taux de compression réel.
        tiny_camera_id = f"{self.camera_id}-tiny"
        tiny_store = SharedFrameStore(tiny_camera_id, slot_size_bytes=200, slots=2)
        tiny_reader = FrameReader(tiny_camera_id, slot_size_bytes=200, slots=2)
        try:
            image = np.full((480, 640, 3), 128, dtype=np.uint8)
            tiny_store.write(image, frame_id=99, ts_capture=1.0)  # ne doit pas lever d'exception
            self.assertIsNone(tiny_reader.read_latest())  # rien de valide n'a pu être publié
        finally:
            tiny_reader.close()
            tiny_store.close()


if __name__ == "__main__":
    unittest.main()
