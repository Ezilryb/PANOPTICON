"""
panopticon/roster/tests/test_publisher_client.py

Tests unitaires du bus de publication ROSTER : un RosterClient connecté reçoit
bien les RosterEvent publiés, dans l'ordre, avec leurs matches intacts ; un
client qui ne lit jamais ses messages ne fait pas planter le publisher.
Même structure de test que `argus/tests/test_publisher_client.py`.
"""

import threading
import time
import unittest

from roster.data_types import FaceMatch, RosterEvent
from roster.publisher import RosterPublisher
from roster.client import RosterClient


def _make_event(camera_id: str, frame_id: int) -> RosterEvent:
    return RosterEvent(
        camera_id=camera_id,
        frame_id=frame_id,
        ts_capture=time.time(),
        ts_matched=time.time(),
        matches=[FaceMatch(matched=True, person_id="p1", name="Alice", distance=0.2)],
    )


class TestRosterPublisherClient(unittest.TestCase):
    def setUp(self) -> None:
        # Port dédié par test (dérivé de l'id du test) pour éviter toute collision entre tests.
        self.port = 19600 + (hash(self.id()) % 500)
        self.publisher = RosterPublisher("127.0.0.1", self.port)
        self.publisher.start()
        time.sleep(0.1)

    def tearDown(self) -> None:
        self.publisher.stop()

    def test_client_receives_events_in_order(self) -> None:
        client = RosterClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)

        received: list[RosterEvent] = []

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
        self.assertEqual(received[0].matches[0].name, "Alice")
        self.assertEqual(received[0].matches[0].label, "known:Alice")

    def test_slow_client_does_not_block_publisher(self) -> None:
        client = RosterClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)
        # Le client se connecte mais ne lit jamais : publish() ne doit ni bloquer ni lever
        # d'exception, même en dépassant largement la file interne (100 messages).
        for i in range(300):
            self.publisher.publish(_make_event("CAMX", i))
        client.close()
        # Le test réussit s'il n'a pas bloqué / n'a levé aucune exception jusqu'ici.

    def test_read_frame_without_argus_returns_none(self) -> None:
        # Dans cet environnement de test, le module `argus` n'expose pas nécessairement
        # frame_store avec un fichier écrit : read_frame() doit renvoyer None proprement,
        # jamais lever d'exception.
        client = RosterClient("127.0.0.1", self.port)
        client.connect()
        event = _make_event("CAM-NEVER-WRITTEN", 0)
        frame = client.read_frame(event)
        self.assertIsNone(frame)
        client.close()


if __name__ == "__main__":
    unittest.main()
