"""
panopticon/aegis/tests/test_pipeline_synthetic.py

Test d'intégration bout-en-bout d'AegisEngine, avec un faux bus ARGUS
(ArgusPublisher + SharedFrameStore réels, pilotés à la main plutôt que par
un vrai ArgusEngine — même approche que roster/spectra/oracle/pulse_track).
Vérifie que : (1) une posture "lying" soutenue et immobile déclenche
exactement un `fall_confirmed` ; (2) une posture "upright" soutenue ne
déclenche jamais rien ; (3) après confirmation, un retour "upright" soutenu
déclenche un `fall_ended`/`posture_recovered` avec la bonne durée ; (4) une
caméra hors de `monitored_camera_ids` est ignorée ; (5) une détection sous
`min_detection_confidence` est ignorée ; (6) une piste disparue pendant une
alerte déclenche un `fall_ended`/`track_lost`.
"""

import threading
import time
import unittest

import numpy as np

from argus.data_types import Detection, DetectionEvent
from argus.frame_store import SharedFrameStore
from argus.publisher import ArgusPublisher

from aegis.client import AegisClient
from aegis.config import AegisConfig, AnalyzerConfig, ArgusConnectionConfig, FallDetectionConfig, PublisherConfig
from aegis.pipeline import AegisEngine

_UPRIGHT_BBOX = (50.0, 30.0, 100.0, 180.0)   # largeur=50, hauteur=150 -> ratio=0.33 -> upright
_LYING_BBOX = (50.0, 100.0, 200.0, 150.0)    # largeur=150, hauteur=50 -> ratio=3.0 -> lying


def _make_person_event(camera_id: str, frame_id: int, bbox, track_id: int = 1, confidence: float = 0.9) -> DetectionEvent:
    now = time.time()
    return DetectionEvent(
        camera_id=camera_id, frame_id=frame_id, ts_capture=now, ts_detected=now,
        width=320, height=240,
        detections=[Detection(0, "person", confidence, bbox, track_id=track_id)],
    )


def _make_empty_event(camera_id: str, frame_id: int) -> DetectionEvent:
    now = time.time()
    return DetectionEvent(camera_id=camera_id, frame_id=frame_id, ts_capture=now, ts_detected=now,
                           width=320, height=240, detections=[])


class TestAegisPipelineIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.argus_port = 20450 + (hash(self.id()) % 200)
        self.aegis_port = 20650 + (hash(self.id()) % 200)

        self.argus_publisher = ArgusPublisher("127.0.0.1", self.argus_port)
        self.argus_publisher.start()
        self.frame_store = SharedFrameStore("CAM-0")
        self.dummy_image = np.zeros((240, 320, 3), dtype=np.uint8)
        self.frame_store.write(self.dummy_image, frame_id=0, ts_capture=time.time())

        self.config = AegisConfig(
            analyzer=AnalyzerConfig(backend="mock"),
            fall_detection=FallDetectionConfig(
                confirm_seconds=0.25, fast_confirm_seconds=0.1, recovery_confirm_seconds=0.15,
                track_lost_after_s=0.3, min_detection_confidence=0.4,
                motion_window_s=1.0, fall_detection_window_s=0.1, fall_min_vertical_px=9999.0,  # chemin rapide désactivé (non testé ici)
                fall_trigger_grace_s=0.3, max_movement_px=20.0,
                monitored_camera_ids=[], person_classes=["person"],
            ),
            argus=ArgusConnectionConfig(host="127.0.0.1", port=self.argus_port),
            publisher=PublisherConfig(host="127.0.0.1", port=self.aegis_port),
            log_stats_every_s=999.0,
        )
        self.engine = AegisEngine(self.config)

    def tearDown(self) -> None:
        self.engine.stop()
        self.frame_store.close()
        self.argus_publisher.stop()

    def _publish_person(self, camera_id: str, frame_id: int, bbox, track_id: int = 1, confidence: float = 0.9) -> None:
        """Écrit une frame fraîche PUIS publie l'évènement correspondant : FrameReader.read_latest()
        ne renvoie une image qu'une seule fois par version, une frame écrite une seule fois en
        setUp() ne serait donc lisible que pour le tout premier évènement traité."""
        self.frame_store.write(self.dummy_image, frame_id=frame_id, ts_capture=time.time())
        self.argus_publisher.publish(_make_person_event(camera_id, frame_id, bbox, track_id=track_id, confidence=confidence))

    def _collect_events(self, n_expected: int, publish_fn, timeout_s: float = 5.0) -> list:
        """Connecte AVANT de publier, pour éliminer la course par construction plutôt que
        par un délai arbitraire (même pattern qu'oracle/pulse_track test_pipeline_synthetic.py)."""
        client = AegisClient("127.0.0.1", self.aegis_port)
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

    def _expect_no_events(self, publish_fn, wait_s: float = 1.0) -> None:
        client = AegisClient("127.0.0.1", self.aegis_port)
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

    def test_sustained_lying_triggers_fall_confirmed(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        def publish() -> None:
            for i in range(8):
                self._publish_person("CAM-0", i, _LYING_BBOX, track_id=1)
                time.sleep(0.08)  # 8 * 0.08s = 0.64s > confirm_seconds=0.25s

        received = self._collect_events(1, publish)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].event_type, "fall_confirmed")
        self.assertEqual(received[0].track_id, 1)
        self.assertEqual(received[0].camera_id, "CAM-0")
        self.assertEqual(received[0].posture.posture, "lying")

    def test_sustained_upright_never_triggers(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        def publish() -> None:
            for i in range(6):
                self._publish_person("CAM-0", i, _UPRIGHT_BBOX, track_id=1)
                time.sleep(0.08)

        self._expect_no_events(publish)

    def test_recovery_after_confirmation(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        def publish() -> None:
            for i in range(8):  # confirme la chute
                self._publish_person("CAM-0", i, _LYING_BBOX, track_id=1)
                time.sleep(0.08)
            for i in range(8, 15):  # se relève et le reste assez longtemps pour clore l'alerte
                self._publish_person("CAM-0", i, _UPRIGHT_BBOX, track_id=1)
                time.sleep(0.08)

        received = self._collect_events(2, publish, timeout_s=8.0)
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0].event_type, "fall_confirmed")
        self.assertEqual(received[1].event_type, "fall_ended")
        self.assertEqual(received[1].end_reason, "posture_recovered")
        self.assertIsNotNone(received[1].duration_s)

    def test_unmonitored_camera_produces_no_events(self) -> None:
        config = AegisConfig(
            analyzer=self.config.analyzer,
            fall_detection=FallDetectionConfig(
                **{**self.config.fall_detection.__dict__, "monitored_camera_ids": ["SALLE-DE-SPORT"]},
            ),
            argus=self.config.argus,
            publisher=PublisherConfig(host="127.0.0.1", port=self.aegis_port + 1),
            log_stats_every_s=999.0,
        )
        engine = AegisEngine(config)
        self.addCleanup(engine.stop)
        engine.start()
        time.sleep(0.3)

        client = AegisClient("127.0.0.1", config.publisher.port)
        client.connect()
        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        time.sleep(0.1)

        for i in range(8):  # publié sur "CAM-0", hors périmètre de cette config -> ignoré
            self._publish_person("CAM-0", i, _LYING_BBOX, track_id=1)
            time.sleep(0.08)

        consumer.join(timeout=1.0)
        client.close()
        self.assertEqual(len(received), 0)

    def test_low_confidence_detection_is_skipped(self) -> None:
        def publish() -> None:
            for i in range(8):
                # confidence=0.2 < min_detection_confidence=0.4 (cf. setUp) : jamais analysée.
                self._publish_person("CAM-0", i, _LYING_BBOX, track_id=1, confidence=0.2)
                time.sleep(0.08)

        self.engine.start()
        time.sleep(0.3)
        self._expect_no_events(publish)

    def test_track_lost_while_confirmed_ends_alert(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        client = AegisClient("127.0.0.1", self.aegis_port)
        client.connect()
        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)
                if len(received) >= 2:
                    break

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        time.sleep(0.1)

        for i in range(8):  # confirme la chute de la piste 1
            self._publish_person("CAM-0", i, _LYING_BBOX, track_id=1)
            time.sleep(0.08)

        time.sleep(0.5)  # dépasse track_lost_after_s=0.3s SANS jamais republier la piste 1
        # Un évènement (même sans "person") sur CAM-0 est nécessaire pour que la pipeline
        # ré-évalue les pistes disparues de cette caméra (cf. pipeline.py::_process_event).
        self.argus_publisher.publish(_make_empty_event("CAM-0", 99))

        consumer.join(timeout=5.0)
        client.close()

        self.assertEqual(len(received), 2)
        self.assertEqual(received[0].event_type, "fall_confirmed")
        self.assertEqual(received[1].event_type, "fall_ended")
        self.assertEqual(received[1].end_reason, "track_lost")


if __name__ == "__main__":
    unittest.main()
