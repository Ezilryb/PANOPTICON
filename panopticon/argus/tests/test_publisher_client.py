"""
panopticon/argus/tests/test_publisher_client.py

Tests unitaires du bus de publication : un ArgusClient connecté reçoit bien
les DetectionEvent publiés, dans l'ordre, avec leurs détections intactes ;
un client qui ne lit jamais ses messages ne fait pas planter le publisher.
"""

import threading
import time
import unittest

from argus.data_types import Detection, DetectionEvent
from argus.publisher import ArgusPublisher
from argus.client import ArgusClient


def _make_event(camera_id: str, frame_id: int) -> DetectionEvent:
    return DetectionEvent(
        camera_id=camera_id,
        frame_id=frame_id,
        ts_capture=time.time(),
        ts_detected=time.time(),
        width=640,
        height=480,
        detections=[Detection(0, "person", 0.9, (1.0, 2.0, 3.0, 4.0), track_id=42)],
    )


class TestPublisherClient(unittest.TestCase):
    def setUp(self) -> None:
        # Port dédié par test (dérivé de l'id du test) pour éviter toute collision entre tests.
        self.port = 19000 + (hash(self.id()) % 500)
        self.publisher = ArgusPublisher("127.0.0.1", self.port)
        self.publisher.start()
        time.sleep(0.1)

    def tearDown(self) -> None:
        self.publisher.stop()

    def test_client_receives_events_in_order(self) -> None:
        client = ArgusClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)  # laisser l'accept_loop enregistrer le client côté serveur

        received: list[DetectionEvent] = []

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
        self.assertEqual(received[0].detections[0].track_id, 42)
        self.assertEqual(received[0].detections[0].class_name, "person")

    def test_slow_client_does_not_block_publisher(self) -> None:
        client = ArgusClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)
        # Le client se connecte mais ne lit jamais (`events()` n'est pas consommé) :
        # publish() ne doit ni bloquer ni lever d'exception, même en dépassant la file interne.
        for i in range(300):
            self.publisher.publish(_make_event("CAMX", i))
        client.close()
        # Le test réussit s'il n'a pas bloqué / n'a levé aucune exception jusqu'ici.


if __name__ == "__main__":
    unittest.main()
