"""
panopticon/pulse_track/tests/test_publisher_client.py

Tests unitaires du bus de publication PULSE_TRACK : un PulseTrackClient
connecté reçoit bien les PulseTrackEvent publiés, dans l'ordre, avec leurs
champs intacts ; un client qui ne lit jamais ses messages ne fait pas
planter le publisher. Même structure de test que
argus/tests/test_publisher_client.py, roster/tests/test_publisher_client.py,
spectra/tests/test_publisher_client.py et oracle/tests/test_publisher_client.py.
"""

import threading
import time
import unittest

from pulse_track.data_types import PulseTrackEvent
from pulse_track.publisher import PulseTrackPublisher
from pulse_track.client import PulseTrackClient


def _make_event(camera_id: str, frame_id: int) -> PulseTrackEvent:
    return PulseTrackEvent(
        rule_id="r1", rule_name="Test", severity="warning", message="Test déclenché",
        camera_id=camera_id, frame_id=frame_id, ts_triggered=time.time(),
        track_id=7, person_name=None, object_class="car",
    )


class TestPulseTrackPublisherClient(unittest.TestCase):
    def setUp(self) -> None:
        self.port = 20200 + (hash(self.id()) % 500)
        self.publisher = PulseTrackPublisher("127.0.0.1", self.port)
        self.publisher.start()
        time.sleep(0.1)

    def tearDown(self) -> None:
        self.publisher.stop()

    def test_client_receives_events_in_order(self) -> None:
        client = PulseTrackClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)

        received: list[PulseTrackEvent] = []

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
        self.assertEqual(received[0].rule_id, "r1")
        self.assertEqual(received[0].object_class, "car")
        self.assertEqual(received[0].track_id, 7)

    def test_slow_client_does_not_block_publisher(self) -> None:
        client = PulseTrackClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)
        for i in range(300):
            self.publisher.publish(_make_event("CAMX", i))
        client.close()
        # Le test réussit s'il n'a pas bloqué / n'a levé aucune exception jusqu'ici.


if __name__ == "__main__":
    unittest.main()