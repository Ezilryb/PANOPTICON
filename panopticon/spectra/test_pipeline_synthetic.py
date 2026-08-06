"""
panopticon/spectra/tests/test_pipeline_synthetic.py

Test d'intégration bout-en-bout de SpectraEngine, avec un faux bus ARGUS
(ArgusPublisher + SharedFrameStore réels, pilotés à la main plutôt que par un
vrai ArgusEngine) et une image synthétique volontairement sombre. Vérifie que
SPECTRA (1) consomme les évènements ARGUS quel que soit leur contenu
(contrairement à ROSTER qui filtre sur "person"), (2) applique la correction
attendue et publie les métriques correspondantes, (3) écrit une frame
améliorée relisible via SpectraClient, distincte de la frame brute d'ARGUS,
et (4) fait remonter l'état d'une zone-écran configurée. Même structure que
`roster/tests/test_pipeline_synthetic.py`.
"""

import threading
import time
import unittest

import numpy as np

from argus.data_types import Detection, DetectionEvent
from argus.frame_store import SharedFrameStore
from argus.publisher import ArgusPublisher

from spectra.client import SpectraClient
from spectra.config import ArgusConnectionConfig, EnhancerConfig, PublisherConfig, ScreenRegionConfig, SpectraConfig
from spectra.pipeline import SpectraEngine


def _dark_frame(shape=(240, 320, 3), seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(loc=0.0, scale=8.0, size=shape)
    return np.clip(25 + noise, 0, 255).astype(np.uint8)


def _make_detection_event(camera_id: str, frame_id: int, image_shape) -> DetectionEvent:
    height, width = image_shape[0], image_shape[1]
    now = time.time()
    return DetectionEvent(
        camera_id=camera_id, frame_id=frame_id, ts_capture=now, ts_detected=now,
        width=width, height=height,
        detections=[Detection(0, "object", 0.8, (10.0, 10.0, 50.0, 50.0))],
    )


class TestSpectraPipelineIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.argus_port = 19900 + (hash(self.id()) % 300)
        self.spectra_port = 19950 + (hash(self.id()) % 300)

        # --- Faux ARGUS : publisher + frame store, pilotés à la main ---
        self.argus_publisher = ArgusPublisher("127.0.0.1", self.argus_port)
        self.argus_publisher.start()
        self.frame_store = SharedFrameStore("CAM-0")

        self.config = SpectraConfig(
            enhancer=EnhancerConfig(),
            argus=ArgusConnectionConfig(host="127.0.0.1", port=self.argus_port),
            publisher=PublisherConfig(host="127.0.0.1", port=self.spectra_port),
            screen_regions=[ScreenRegionConfig(camera_id="CAM-0", region_name="coin-haut-gauche",
                                                bbox=(0, 0, 60, 60), on_brightness_threshold=15.0)],
            log_stats_every_s=999.0,
        )
        self.engine = SpectraEngine(self.config)

    def tearDown(self) -> None:
        self.engine.stop()
        self.frame_store.close()
        self.argus_publisher.stop()

    def _collect_events(self, n_expected: int, timeout_s: float = 8.0) -> list:
        client = SpectraClient("127.0.0.1", self.spectra_port)
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

    def test_dark_frame_triggers_low_light_correction(self) -> None:
        self.engine.start()
        time.sleep(0.3)  # laisser SpectraEngine se connecter à ARGUS

        image = _dark_frame()
        self.frame_store.write(image, frame_id=1, ts_capture=time.time())
        self.argus_publisher.publish(_make_detection_event("CAM-0", 1, image.shape))

        received = self._collect_events(n_expected=1)
        self.assertEqual(len(received), 1)
        event = received[0]
        self.assertEqual(event.camera_id, "CAM-0")
        self.assertTrue(event.result.low_light_correction_applied)
        self.assertGreater(event.result.brightness_after, event.result.brightness_before)

    def test_enhanced_frame_is_readable_and_brighter_than_raw(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        image = _dark_frame()
        self.frame_store.write(image, frame_id=2, ts_capture=time.time())
        self.argus_publisher.publish(_make_detection_event("CAM-0", 2, image.shape))

        received = self._collect_events(n_expected=1)
        self.assertEqual(len(received), 1)
        event = received[0]

        client = SpectraClient("127.0.0.1", self.spectra_port)
        enhanced = client.read_frame(event)
        self.assertIsNotNone(enhanced)
        self.assertEqual(enhanced.shape, image.shape)
        self.assertGreater(float(enhanced.mean()), float(image.mean()) + 20.0)
        client.close()

    def test_processes_events_without_any_detection(self) -> None:
        # Contrairement à ROSTER, SPECTRA doit traiter une frame même si ARGUS n'y a rien
        # détecté : son rôle est la qualité d'image générale, pas conditionné à une détection.
        self.engine.start()
        time.sleep(0.3)

        image = _dark_frame()
        self.frame_store.write(image, frame_id=3, ts_capture=time.time())
        now = time.time()
        empty_event = DetectionEvent(
            camera_id="CAM-0", frame_id=3, ts_capture=now, ts_detected=now,
            width=image.shape[1], height=image.shape[0], detections=[],
        )
        self.argus_publisher.publish(empty_event)

        received = self._collect_events(n_expected=1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].frame_id, 3)

    def test_screen_region_state_is_reported(self) -> None:
        self.engine.start()
        time.sleep(0.3)

        image = _dark_frame()
        image[0:60, 0:60] = 5  # coin-haut-gauche délibérément très sombre : "éteint"
        self.frame_store.write(image, frame_id=4, ts_capture=time.time())
        self.argus_publisher.publish(_make_detection_event("CAM-0", 4, image.shape))

        received = self._collect_events(n_expected=1)
        self.assertEqual(len(received), 1)
        regions = received[0].screen_regions
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].region_name, "coin-haut-gauche")
        self.assertFalse(regions[0].is_on)


if __name__ == "__main__":
    unittest.main()