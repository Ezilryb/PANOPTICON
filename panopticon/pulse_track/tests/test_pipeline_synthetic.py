"""
panopticon/pulse_track/tests/test_pipeline_synthetic.py

Test d'intégration bout-en-bout de PulseTrackEngine, avec de faux bus ARGUS
et ROSTER (ArgusPublisher et RosterPublisher réels, pilotés à la main plutôt
que par de vrais ArgusEngine/RosterEngine — même approche que
roster/tests/test_pipeline_synthetic.py, spectra/tests/test_pipeline_synthetic.py
et oracle/tests/test_pipeline_synthetic.py). Vérifie que PulseTrackEngine
consomme BIEN les deux flux indépendamment (une règle "object_class"
déclenchée par ARGUS, une règle "known_person" déclenchée par ROSTER), que
le PulseTrackEvent publié porte le bon frame_id, et qu'un flux sans règle
correspondante ne produit aucun évènement.
"""

import threading
import time
import unittest

from argus.data_types import Detection, DetectionEvent
from argus.publisher import ArgusPublisher

from roster.data_types import FaceMatch, RosterEvent
from roster.publisher import RosterPublisher

from pulse_track.client import PulseTrackClient
from pulse_track.config import (
    ArgusConnectionConfig, PublisherConfig, PulseTrackConfig, RosterConnectionConfig,
    RuleCondition, RuleConfig,
)
from pulse_track.pipeline import PulseTrackEngine


class TestPulseTrackPipelineIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.argus_port = 20300 + (hash(self.id()) % 200)
        self.roster_port = 20500 + (hash(self.id()) % 200)
        self.pulse_track_port = 20700 + (hash(self.id()) % 200)

        # --- Faux ARGUS et ROSTER : publishers réels, pilotés à la main ---
        self.argus_publisher = ArgusPublisher("127.0.0.1", self.argus_port)
        self.argus_publisher.start()
        self.roster_publisher = RosterPublisher("127.0.0.1", self.roster_port)
        self.roster_publisher.start()

        self.config = PulseTrackConfig(
            rules=[
                RuleConfig(rule_id="vehicle", name="Véhicule",
                           condition=RuleCondition(trigger="object_class", object_classes=["car"], min_confidence=0.5),
                           cooldown_s=0.0),
                RuleConfig(rule_id="known", name="Connu",
                           condition=RuleCondition(trigger="known_person"), cooldown_s=0.0),
            ],
            argus=ArgusConnectionConfig(host="127.0.0.1", port=self.argus_port),
            roster=RosterConnectionConfig(host="127.0.0.1", port=self.roster_port),
            publisher=PublisherConfig(host="127.0.0.1", port=self.pulse_track_port),
            log_stats_every_s=999.0,
        )
        self.engine = PulseTrackEngine(self.config)

    def tearDown(self) -> None:
        self.engine.stop()
        self.argus_publisher.stop()
        self.roster_publisher.stop()

    def _expect_events(self, n_expected: int, publish_fn, timeout_s: float = 5.0) -> list:
        """Connecte AVANT de publier (même raison qu'oracle/tests/test_pipeline_synthetic.py::
        _expect_events : élimine la course par construction plutôt que par un délai arbitraire)."""
        client = PulseTrackClient("127.0.0.1", self.pulse_track_port)
        client.connect()
        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)
                if len(received) >= n_expected:
                    break

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        time.sleep(0.1)

        publish_fn()

        consumer.join(timeout=timeout_s)
        client.close()
        return received

    def _expect_no_events(self, publish_fn, wait_s: float = 1.5) -> None:
        client = PulseTrackClient("127.0.0.1", self.pulse_track_port)
        client.connect()
        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        time.sleep(0.1)

        publish_fn()

        consumer.join(timeout=wait_s)
        client.close()
        self.assertEqual(len(received), 0)

    def test_argus_stream_triggers_object_class_rule(self) -> None:
        self.engine.start()
        time.sleep(0.3)  # laisser PulseTrackEngine se connecter aux deux flux

        now = time.time()
        event = DetectionEvent(
            camera_id="CAM-0", frame_id=42, ts_capture=now, ts_detected=now,
            width=640, height=480,
            detections=[Detection(0, "car", 0.9, (0.0, 0.0, 10.0, 10.0), track_id=3)],
        )
        received = self._expect_events(1, lambda: self.argus_publisher.publish(event))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].rule_id, "vehicle")
        self.assertEqual(received[0].camera_id, "CAM-0")
        self.assertEqual(received[0].frame_id, 42)
        self.assertEqual(received[0].object_class, "car")
        self.assertEqual(received[0].track_id, 3)

    def test_roster_stream_triggers_known_person_rule(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        now = time.time()
        event = RosterEvent(
            camera_id="ENTREE", frame_id=9, ts_capture=now, ts_matched=now,
            matches=[FaceMatch(matched=True, person_id="p1", name="Alice")],
        )
        received = self._expect_events(1, lambda: self.roster_publisher.publish(event))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].rule_id, "known")
        self.assertEqual(received[0].person_name, "Alice")
        self.assertEqual(received[0].frame_id, 9)

    def test_unmatched_class_produces_no_event(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        now = time.time()
        event = DetectionEvent(
            camera_id="CAM-0", frame_id=1, ts_capture=now, ts_detected=now,
            width=640, height=480,
            detections=[Detection(0, "dog", 0.9, (0.0, 0.0, 10.0, 10.0))],  # "dog" ne matche aucune règle
        )
        self._expect_no_events(lambda: self.argus_publisher.publish(event))

    def test_both_streams_are_consumed_independently(self) -> None:
        """Une règle ARGUS puis une règle ROSTER, dans cet ordre : vérifie que les deux threads
        (cf. pipeline.py::_argus_loop / _roster_loop) tournent bien en parallèle, pas l'un
        bloquant l'autre."""
        self.engine.start()
        time.sleep(0.3)

        now = time.time()
        argus_event = DetectionEvent(
            camera_id="CAM-0", frame_id=1, ts_capture=now, ts_detected=now,
            width=640, height=480,
            detections=[Detection(0, "car", 0.9, (0.0, 0.0, 10.0, 10.0), track_id=1)],
        )
        roster_event = RosterEvent(
            camera_id="CAM-0", frame_id=1, ts_capture=now, ts_matched=now,
            matches=[FaceMatch(matched=True, person_id="p1", name="Bob")],
        )

        def publish_both() -> None:
            self.argus_publisher.publish(argus_event)
            self.roster_publisher.publish(roster_event)

        received = self._expect_events(2, publish_both)
        rule_ids = {e.rule_id for e in received}
        self.assertEqual(rule_ids, {"vehicle", "known"})


if __name__ == "__main__":
    unittest.main()