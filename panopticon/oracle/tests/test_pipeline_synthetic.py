"""
panopticon/oracle/tests/test_pipeline_synthetic.py

Test d'intégration bout-en-bout d'OracleEngine, avec un faux bus ARGUS
(ArgusPublisher + SharedFrameStore réels, pilotés à la main plutôt que par
un vrai ArgusEngine — même approche que
`roster/tests/test_pipeline_synthetic.py` et `spectra/tests/test_pipeline_synthetic.py`)
et le backend "mock" (aucun réseau). Contrairement aux tests ROSTER, aucune
photo de test réelle n'est nécessaire : le backend mock d'ORACLE fonctionne
sur n'importe quel contenu d'image, donc ce test tourne toujours, sans
condition d'environnement.

Vérifie que :
1) un objet d'une classe identifiable configurée est bien identifié ;
2) une seconde occurrence du même objet (même contenu de crop) est servie
   depuis le cache plutôt que ré-identifiée ;
3) une détection de classe "person" n'est JAMAIS traitée, même si elle
   apparaît par erreur dans `identifiable_classes` — c'est le critère
   d'acceptation central du brief projet (section 10) pour ORACLE ;
4) une classe non configurée ne produit aucun évènement ;
5) une détection sous le seuil de confiance configuré ne produit aucun évènement.
"""

import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from argus.data_types import Detection, DetectionEvent
from argus.frame_store import SharedFrameStore
from argus.publisher import ArgusPublisher

from oracle.client import OracleClient
from oracle.config import ArgusConnectionConfig, CacheConfig, IdentifierConfig, OracleConfig, PublisherConfig
from oracle.pipeline import OracleEngine


def _test_image(shape=(120, 160, 3), color: int = 90) -> np.ndarray:
    image = np.full(shape, color, dtype=np.uint8)
    return image


class TestOraclePipelineIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="oracle_pipeline_test_"))

        self.argus_port = 20100 + (hash(self.id()) % 300)
        self.oracle_port = 20150 + (hash(self.id()) % 300)

        # --- Faux ARGUS : publisher + frame store, pilotés à la main ---
        self.argus_publisher = ArgusPublisher("127.0.0.1", self.argus_port)
        self.argus_publisher.start()
        self.frame_store = SharedFrameStore("CAM-0")

        # identifiable_classes inclut délibérément "person" : le test
        # test_person_detection_is_never_identified vérifie que le garde-fou
        # en dur de la pipeline (pas la config) empêche quand même son traitement.
        self.config = OracleConfig(
            data_dir=str(self.tmp_dir / "oracle_data"),
            identifier=IdentifierConfig(backend="mock"),
            cache=CacheConfig(max_hamming_distance=6),
            argus=ArgusConnectionConfig(host="127.0.0.1", port=self.argus_port),
            publisher=PublisherConfig(host="127.0.0.1", port=self.oracle_port),
            identifiable_classes=["car", "laptop", "person"],
            min_confidence_to_identify=0.5,
            log_stats_every_s=999.0,
        )
        self.engine = OracleEngine(self.config)

    def tearDown(self) -> None:
        self.engine.stop()
        self.frame_store.close()
        self.argus_publisher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_event(self, frame_id: int, class_name: str, confidence: float, image_shape) -> DetectionEvent:
        height, width = image_shape[0], image_shape[1]
        now = time.time()
        return DetectionEvent(
            camera_id="CAM-0", frame_id=frame_id, ts_capture=now, ts_detected=now,
            width=width, height=height,
            detections=[Detection(0, class_name, confidence, (10.0, 10.0, 90.0, 90.0), track_id=1)],
        )

    def _expect_events(self, n_expected: int, publish_fn, timeout_s: float = 5.0) -> list:
        """
        Connecte un OracleClient et démarre l'écoute AVANT d'appeler `publish_fn()` (qui
        déclenche l'évènement ARGUS d'origine), plutôt que l'inverse : avec le backend
        "mock" (aucun réseau, quasi instantané), OracleEngine peut traiter et republier
        plus vite qu'un client de test ne se connecterait s'il ne se connectait qu'après
        coup — cette structure élimine la course par construction, pas par un délai arbitraire.
        """
        client = OracleClient("127.0.0.1", self.oracle_port)
        client.connect()
        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)
                if len(received) >= n_expected:
                    break

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        time.sleep(0.1)  # laisse la boucle events() atteindre son premier recv() bloquant

        publish_fn()

        consumer.join(timeout=timeout_s)
        client.close()
        return received

    def _expect_no_events(self, publish_fn, wait_s: float = 1.5) -> None:
        """Symétrique de `_expect_events` pour le cas « aucun évènement attendu » : écoute
        démarrée avant `publish_fn()`, pour ne jamais transformer un vrai bug (un évènement
        publié à tort) en faux succès simplement parce que le client s'est connecté trop tard."""
        client = OracleClient("127.0.0.1", self.oracle_port)
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

    def test_identifiable_object_is_identified(self) -> None:
        self.engine.start()
        time.sleep(0.3)  # laisser OracleEngine se connecter à ARGUS

        image = _test_image(color=90)

        def publish() -> None:
            self.frame_store.write(image, frame_id=1, ts_capture=time.time())
            self.argus_publisher.publish(self._make_event(1, "car", 0.9, image.shape))

        received = self._expect_events(n_expected=1, publish_fn=publish)
        self.assertEqual(len(received), 1)
        event = received[0]
        self.assertEqual(len(event.objects), 1)
        obj = event.objects[0]
        self.assertEqual(obj.class_name, "car")
        self.assertEqual(obj.source_track_id, 1)
        self.assertIsNotNone(obj.identification)
        self.assertEqual(obj.identification.source, "mock")
        self.assertFalse(obj.from_cache)

    def test_second_identical_occurrence_is_served_from_cache(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        image = _test_image(color=150)

        def publish_first() -> None:
            self.frame_store.write(image, frame_id=1, ts_capture=time.time())
            self.argus_publisher.publish(self._make_event(1, "car", 0.9, image.shape))

        first = self._expect_events(n_expected=1, publish_fn=publish_first)
        self.assertEqual(len(first), 1)
        self.assertFalse(first[0].objects[0].from_cache)
        first_label = first[0].objects[0].identification.label

        # Même contenu d'image, même bbox -> crop strictement identique -> hash identique.
        def publish_second() -> None:
            self.frame_store.write(image, frame_id=2, ts_capture=time.time())
            self.argus_publisher.publish(self._make_event(2, "car", 0.9, image.shape))

        second = self._expect_events(n_expected=1, publish_fn=publish_second)
        self.assertEqual(len(second), 1)
        self.assertTrue(second[0].objects[0].from_cache)
        self.assertEqual(second[0].objects[0].identification.label, first_label)

    def test_person_detection_is_never_identified(self) -> None:
        # "person" est présent dans self.config.identifiable_classes (cf. setUp) : ce test
        # vérifie que la pipeline le bloque quand même, en dur, indépendamment de la config.
        self.engine.start()
        time.sleep(0.3)
        image = _test_image()

        def publish() -> None:
            self.frame_store.write(image, frame_id=1, ts_capture=time.time())
            self.argus_publisher.publish(self._make_event(1, "person", 0.95, image.shape))

        self._expect_no_events(publish)

    def test_unconfigured_class_produces_no_event(self) -> None:
        self.engine.start()
        time.sleep(0.3)
        image = _test_image()

        def publish() -> None:
            self.frame_store.write(image, frame_id=1, ts_capture=time.time())
            # "dog" n'est pas dans identifiable_classes (cf. setUp) : aucun évènement attendu.
            self.argus_publisher.publish(self._make_event(1, "dog", 0.95, image.shape))

        self._expect_no_events(publish)

    def test_low_confidence_detection_is_skipped(self) -> None:
        self.engine.start()
        time.sleep(0.3)
        image = _test_image()

        def publish() -> None:
            self.frame_store.write(image, frame_id=1, ts_capture=time.time())
            # confidence=0.2 < min_confidence_to_identify=0.5 (cf. setUp) : aucun évènement attendu.
            self.argus_publisher.publish(self._make_event(1, "car", 0.2, image.shape))

        self._expect_no_events(publish)


if __name__ == "__main__":
    unittest.main()
