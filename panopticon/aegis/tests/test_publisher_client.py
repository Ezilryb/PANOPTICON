"""
panopticon/aegis/tests/test_publisher_client.py

Tests unitaires du bus de publication AEGIS : un AegisClient connecté
reçoit bien les AegisEvent publiés, dans l'ordre, avec leurs champs intacts ;
un client qui ne lit jamais ses messages ne fait pas planter le publisher.
Même structure de test que argus/tests/test_publisher_client.py,
roster/tests/test_publisher_client.py, spectra/tests/test_publisher_client.py,
oracle/tests/test_publisher_client.py et pulse_track/tests/test_publisher_client.py.
"""

import threading
import time
import unittest

from aegis.data_types import AegisEvent, PostureResult
from aegis.publisher import AegisPublisher
from aegis.client import AegisClient


def _make_event(camera_id: str, frame_id: int) -> AegisEvent:
    posture = PostureResult(posture="lying", confidence=0.9, aspect_ratio=1.6, orientation_deg=82.0, source="mock")
    return AegisEvent(
        event_type="fall_confirmed", camera_id=camera_id, track_id=7, frame_id=frame_id,
        ts_triggered=time.time(), fall_started_at=time.time() - 5.0, posture=posture, fast_fall_observed=True,
    )


class TestAegisPublisherClient(unittest.TestCase):
    def setUp(self) -> None:
        self.port = 20400 + (hash(self.id()) % 500)
        self.publisher = AegisPublisher("127.0.0.1", self.port)
        self.publisher.start()
        time.sleep(0.1)

    def tearDown(self) -> None:
        self.publisher.stop()

    def test_client_receives_events_in_order(self) -> None:
        client = AegisClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)

        received: list[AegisEvent] = []

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
        self.assertEqual(received[0].event_type, "fall_confirmed")
        self.assertEqual(received[0].track_id, 7)
        self.assertTrue(received[0].fast_fall_observed)
        self.assertEqual(received[0].posture.posture, "lying")
        self.assertAlmostEqual(received[0].posture.orientation_deg, 82.0, places=4)

    def test_slow_client_does_not_block_publisher(self) -> None:
        client = AegisClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)
        # Le client se connecte mais ne lit jamais : publish() ne doit ni bloquer ni lever
        # d'exception, même en dépassant largement la file interne (100 messages).
        for i in range(300):
            self.publisher.publish(_make_event("CAMX", i))
        client.close()
        # Le test réussit s'il n'a pas bloqué / n'a levé aucune exception jusqu'ici.


if __name__ == "__main__":
    unittest.main()
