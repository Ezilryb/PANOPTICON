"""
panopticon/roster/tests/test_pipeline_synthetic.py

Test d'intégration bout-en-bout de RosterEngine, avec un faux bus ARGUS
(ArgusPublisher + SharedFrameStore réels, mais pilotés à la main plutôt que
par une vraie caméra) et de vraies photos de visage. Vérifie que :
1) une personne enrôlée est bien reconnue (`known:{nom}`) sur une frame où
   ARGUS a détecté une "person" ;
2) une personne non enrôlée est rapportée `unknown` ;
3) une frame sans détection "person" ne produit aucun RosterEvent (ROSTER
   ne traite que ce qu'ARGUS lui signale, jamais la frame brute en direct).

Même structure que `argus/tests/test_pipeline_synthetic.py`.
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import cv2

from argus.data_types import Detection, DetectionEvent
from argus.frame_store import SharedFrameStore
from argus.publisher import ArgusPublisher

from roster.client import RosterClient
from roster.config import ArgusConnectionConfig, EmbedderConfig, MatcherConfig, PublisherConfig, RosterConfig
from roster.embedder import MockEmbedder
from roster.enrollment import EnrollmentService
from roster.pipeline import RosterEngine
from roster.store import PersonStore

_PHOTOS_DIR = os.environ.get("ROSTER_TEST_PHOTOS_DIR", "/home/claude/test_photos")


def _make_person_event(camera_id: str, frame_id: int, image_shape) -> DetectionEvent:
    """DetectionEvent avec une seule détection 'person' couvrant (quasi) toute l'image."""
    height, width = image_shape[0], image_shape[1]
    now = time.time()
    return DetectionEvent(
        camera_id=camera_id,
        frame_id=frame_id,
        ts_capture=now,
        ts_detected=now,
        width=width,
        height=height,
        detections=[Detection(0, "person", 0.95, (0.0, 0.0, float(width), float(height)), track_id=1)],
    )


@unittest.skipUnless(os.path.isdir(_PHOTOS_DIR), "Photos de test réelles indisponibles dans cet environnement")
class TestRosterPipelineIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="roster_pipeline_test_"))

        self.argus_port = 19700 + (hash(self.id()) % 300)
        self.roster_port = 19750 + (hash(self.id()) % 300)

        # --- Faux ARGUS : publisher + frame store, pilotés à la main ---
        self.argus_publisher = ArgusPublisher("127.0.0.1", self.argus_port)
        self.argus_publisher.start()
        self.frame_store = SharedFrameStore("CAM-0")

        # --- Config ROSTER pointant vers ce faux ARGUS ---
        self.config = RosterConfig(
            data_dir=str(self.tmp_dir / "roster_data"),
            embedder=EmbedderConfig(backend="mock"),
            matcher=MatcherConfig(distance_threshold=11.0),  # cf. calibration dans test_matcher.py
            argus=ArgusConnectionConfig(host="127.0.0.1", port=self.argus_port),
            publisher=PublisherConfig(host="127.0.0.1", port=self.roster_port),
            log_stats_every_s=999.0,
        )

        # --- Pré-enrôlement de Barack, via le même embedder que celui du pipeline ---
        embedder = MockEmbedder(EmbedderConfig(backend="mock"))
        embedder.warmup()
        store = PersonStore(self.config.persons_db_path, self.config.reference_photos_dir)
        enrollment = EnrollmentService(store, embedder, self.config.reference_photos_dir)
        enrollment.enroll_person(
            "Barack", [os.path.join(_PHOTOS_DIR, "obama1.jpg")], consent_given=True,
        )

        self.engine = RosterEngine(self.config)

    def tearDown(self) -> None:
        self.engine.stop()
        self.frame_store.close()
        self.argus_publisher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _collect_events(self, n_expected: int, timeout_s: float = 8.0) -> list:
        client = RosterClient("127.0.0.1", self.roster_port)
        client.connect()
        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)
                if len(received) >= n_expected:
                    break

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        consumer.join(timeout=timeout_s)
        client.close()
        return received

    def test_enrolled_person_is_recognized(self) -> None:
        self.engine.start()
        time.sleep(0.3)  # laisser RosterEngine se connecter à ARGUS

        image = cv2.imread(os.path.join(_PHOTOS_DIR, "obama1.jpg"))
        self.frame_store.write(image, frame_id=1, ts_capture=time.time())

        received = self._collect_events(n_expected=1)
        self.argus_publisher.publish(_make_person_event("CAM-0", 1, image.shape))

        received = self._collect_events(n_expected=1)
        self.assertEqual(len(received), 1)
        event = received[0]
        self.assertEqual(len(event.matches), 1)
        self.assertTrue(event.matches[0].matched)
        self.assertEqual(event.matches[0].name, "Barack")
        self.assertEqual(event.matches[0].label, "known:Barack")
        self.assertIsNotNone(event.matches[0].bbox)

    def test_unenrolled_person_is_unknown(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        image = cv2.imread(os.path.join(_PHOTOS_DIR, "biden1.jpg"))
        self.frame_store.write(image, frame_id=1, ts_capture=time.time())

        received = self._collect_events(n_expected=1)
        self.argus_publisher.publish(_make_person_event("CAM-0", 1, image.shape))

        received = self._collect_events(n_expected=1)
        self.assertEqual(len(received), 1)
        self.assertFalse(received[0].matches[0].matched)
        self.assertEqual(received[0].matches[0].label, "unknown")

    def test_no_person_detection_produces_no_event(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        image = cv2.imread(os.path.join(_PHOTOS_DIR, "obama1.jpg"))
        self.frame_store.write(image, frame_id=2, ts_capture=time.time())

        # DetectionEvent SANS détection "person" (ex: seulement un "car" ailleurs dans le brief) :
        # ROSTER ne doit rien publier, car il ne traite que ce qu'ARGUS lui signale explicitement.
        now = time.time()
        empty_event = DetectionEvent(
            camera_id="CAM-0", frame_id=2, ts_capture=now, ts_detected=now,
            width=image.shape[1], height=image.shape[0], detections=[],
        )
        self.argus_publisher.publish(empty_event)

        client = RosterClient("127.0.0.1", self.roster_port)
        client.connect()
        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        consumer.join(timeout=1.5)  # laisse le temps à un éventuel (mauvais) évènement d'arriver
        client.close()

        self.assertEqual(len(received), 0)


if __name__ == "__main__":
    unittest.main()
