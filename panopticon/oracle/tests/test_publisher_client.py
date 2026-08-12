"""
panopticon/oracle/tests/test_publisher_client.py

Tests unitaires du bus de publication ORACLE : un OracleClient connecté
reçoit bien les OracleEvent publiés, dans l'ordre, avec leurs objets
identifiés intacts ; un client qui ne lit jamais ses messages ne fait pas
planter le publisher. Même structure de test que
`argus/tests/test_publisher_client.py`, `roster/tests/test_publisher_client.py`
et `spectra/tests/test_publisher_client.py`.
"""

import threading
import time
import unittest

from oracle.data_types import IdentifiedObject, ObjectIdentification, OracleEvent
from oracle.publisher import OraclePublisher
from oracle.client import OracleClient


def _make_event(camera_id: str, frame_id: int) -> OracleEvent:
    identification = ObjectIdentification(label="Toyota Camry", confidence=0.82, source="mock", candidates=["Sedan"])
    obj = IdentifiedObject(bbox=(10.0, 10.0, 90.0, 60.0), class_name="car", source_track_id=7,
                            identification=identification, from_cache=False)
    return OracleEvent(camera_id=camera_id, frame_id=frame_id, ts_capture=time.time(),
                        ts_identified=time.time(), objects=[obj])


class TestOraclePublisherClient(unittest.TestCase):
    def setUp(self) -> None:
        self.port = 20000 + (hash(self.id()) % 500)
        self.publisher = OraclePublisher("127.0.0.1", self.port)
        self.publisher.start()
        time.sleep(0.1)

    def tearDown(self) -> None:
        self.publisher.stop()

    def test_client_receives_events_in_order(self) -> None:
        client = OracleClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)

        received: list[OracleEvent] = []

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
        self.assertEqual(received[0].objects[0].class_name, "car")
        self.assertEqual(received[0].objects[0].source_track_id, 7)
        self.assertEqual(received[0].objects[0].identification.label, "Toyota Camry")
        self.assertEqual(received[0].objects[0].identification.candidates, ["Sedan"])

    def test_slow_client_does_not_block_publisher(self) -> None:
        client = OracleClient("127.0.0.1", self.port)
        client.connect()
        time.sleep(0.1)
        for i in range(300):
            self.publisher.publish(_make_event("CAMX", i))
        client.close()
        # Le test réussit s'il n'a pas bloqué / n'a levé aucune exception jusqu'ici.


if __name__ == "__main__":
    unittest.main()
