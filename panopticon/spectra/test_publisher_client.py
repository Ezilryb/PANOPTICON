"""
panopticon/spectra/tests/test_publisher_client.py

Tests unitaires du bus de publication SPECTRA : un SpectraClient connecté
reçoit bien les SpectraEvent publiés, dans l'ordre, avec leurs métriques et
zones-écran intactes ; un client qui ne lit jamais ses messages ne fait pas
planter le publisher. Même structure de test que
`argus/tests/test_publisher_client.py` et `roster/tests/test_publisher_client.py`.
"""

import threading
import time
import unittest

from spectra.data_types import EnhancementResult, ScreenRegionState, SpectraEvent
from spectra.publisher import SpectraPublisher
from spectra.client import SpectraClient


def _make_event(camera_id: str, frame_id: int) -> SpectraEvent:
    result = EnhancementResult(
        brightness_before=40.0, brightness_after=118.0,
        contrast_before=20.0, contrast_after=48.0,
        low_light_correction_applied=True, denoise_applied=True,
        contrast_enhancement_applied=True, white_balance_applied=False,
        gamma_used=0.55,
    )
    screen_regions = [ScreenRegionState(region_name="ecran", brightness=180.0, is_on=True,
                                         is_static=True, motion_score=1.2)]
    return SpectraEvent(
        camera_id=camera_id, frame_id=frame_id,
        ts_capture=time.time(), ts_enhanced=time.time(),
        width=640, height=480, result=result, screen_regions=screen_regions,
    )


class TestSpectraPublisherClient(unittest.TestCase):
    def setUp(self) -> None:
        self.port = 19800 + (hash(self.id()) % 500)
        self.publisher = SpectraPublisher("127.0.0.1", self.port)
        self.publisher.start()
        time.sleep(0.1)

    def tearDown(self) -> None:
        self.publisher.stop()

    def test_client_receives_events_in_order(self) -> None:
        client = SpectraClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)

        received: list[SpectraEvent] = []

        def consume() -> None:
            for event in client.events():
                received.append(event)
                if len(received) >= 5:
                    break

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        time.sleep(0.1)

        for i in range(5):
            self.publisher.publish(_make_event("CAMX", i))
            time.sleep(0.02)

        consumer.join(timeout=3)
        client.close()

        self.assertEqual(len(received), 5)
        self.assertEqual([e.frame_id for e in received], [0, 1, 2, 3, 4])
        self.assertEqual(received[0].camera_id, "CAMX")
        self.assertTrue(received[0].result.low_light_correction_applied)
        self.assertAlmostEqual(received[0].result.gamma_used, 0.55, places=4)
        self.assertEqual(len(received[0].screen_regions), 1)
        self.assertEqual(received[0].screen_regions[0].region_name, "ecran")
        self.assertTrue(received[0].screen_regions[0].is_on)

    def test_slow_client_does_not_block_publisher(self) -> None:
        client = SpectraClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)
        for i in range(300):
            self.publisher.publish(_make_event("CAMX", i))
        client.close()

    def test_read_frame_without_written_frame_returns_none(self) -> None:
        client = SpectraClient("127.0.0.1", self.port)
        client.connect()
        event = _make_event("CAM-NEVER-WRITTEN", 0)
        frame = client.read_frame(event)
        self.assertIsNone(frame)
        client.close()


if __name__ == "__main__":
    unittest.main()