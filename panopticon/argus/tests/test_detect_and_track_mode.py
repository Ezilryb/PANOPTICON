"""
panopticon/argus/tests/test_detect_and_track_mode.py

Test d'intégration bout-en-bout du mode "detect_and_track" : caméra
synthétique + backend "mock", comme test_pipeline_synthetic.py, mais avec le
Detector instrumenté pour compter ses appels. Vérifie que (1) le Detector
est appelé nettement moins souvent qu'il n'y a d'évènements publiés — la
motivation même du mode —, (2) CHAQUE frame (lourde ou légère) produit
malgré tout un DetectionEvent publié (contrat inchangé pour SPECTRA/ROSTER/
ORACLE/PULSE_TRACK), et (3) les détections portent via_light_tracker=True
sur une frame légère et False sur une frame lourde.
"""

import threading
import time
import unittest

from argus.client import ArgusClient
from argus.config import ArgusConfig, CameraConfig, DetectorConfig, PublisherConfig, TrackingModeConfig
from argus.pipeline import ArgusEngine

_DETECT_EVERY_N_FRAMES = 5


class TestDetectAndTrackMode(unittest.TestCase):
    def _run_pipeline(self, port: int, n_events_wanted: int, timeout_s: float = 10.0):
        config = ArgusConfig(
            cameras=[CameraConfig(camera_id="CAM-0", source="synthetic", target_fps=20, width=320, height=240)],
            detector=DetectorConfig(backend="mock", confidence_threshold=0.3),
            publisher=PublisherConfig(host="127.0.0.1", port=port),
            tracking_mode=TrackingModeConfig(
                mode="detect_and_track",
                detect_every_n_frames=_DETECT_EVERY_N_FRAMES,
                # Seuil abaissé par rapport au défaut de production (3) : le rectangle synthétique
                # n'offre que 4 coins exploitables (goodFeaturesToTrack), 2 suffisent à rester
                # robuste au bruit du test sans pour autant accepter n'importe quoi.
                of_min_surviving_points=2,
            ),
            log_stats_every_s=60.0,
        )
        engine = ArgusEngine(config)

        call_count = {"n": 0}
        original_detect_batch = engine.detector.detect_batch

        def counting_detect_batch(images):
            call_count["n"] += 1
            return original_detect_batch(images)

        engine.detector.detect_batch = counting_detect_batch

        engine.start()
        time.sleep(0.4)  # laisser la caméra et le publisher démarrer

        client = ArgusClient("127.0.0.1", port)
        client.connect()

        received = []

        def consume() -> None:
            for event in client.events():
                received.append(event)
                if len(received) >= n_events_wanted:
                    break

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        consumer.join(timeout=timeout_s)

        client.close()
        engine.stop()
        return received, call_count["n"]

    def test_detector_is_called_less_often_than_events_published(self) -> None:
        received, detector_calls = self._run_pipeline(port=19510, n_events_wanted=30)

        self.assertGreaterEqual(len(received), 20, "pas assez d'évènements reçus dans le délai imparti")
        # ~1 appel Detector toutes les detect_every_n_frames frames : largement moins d'appels
        # que d'évènements, avec une marge confortable pour absorber le jitter du test.
        self.assertLess(detector_calls, len(received) / 2)

    def test_every_frame_still_publishes_an_event(self) -> None:
        received, _detector_calls = self._run_pipeline(port=19511, n_events_wanted=25)
        self.assertGreaterEqual(len(received), 20)

        frame_ids = sorted(e.frame_id for e in received)
        # Aucun trou : chaque frame_id consécutif a bien produit un évènement, lourd ou léger.
        for a, b in zip(frame_ids, frame_ids[1:]):
            self.assertEqual(b - a, 1, f"trou détecté entre frame_id={a} et frame_id={b}")

    def test_detections_are_flagged_light_or_heavy_correctly(self) -> None:
        received, _detector_calls = self._run_pipeline(port=19512, n_events_wanted=40)
        self.assertGreaterEqual(len(received), 30)

        saw_heavy_detection = False
        saw_light_detection = False
        for event in received:
            is_heavy = (event.frame_id - 1) % _DETECT_EVERY_N_FRAMES == 0
            for det in event.detections:
                if is_heavy:
                    self.assertFalse(det.via_light_tracker, f"frame_id={event.frame_id} devrait être lourde")
                    saw_heavy_detection = True
                else:
                    self.assertTrue(det.via_light_tracker, f"frame_id={event.frame_id} devrait être légère")
                    saw_light_detection = True

        self.assertTrue(saw_heavy_detection, "aucune détection lourde observée sur la durée du test")
        self.assertTrue(saw_light_detection, "aucune détection légère observée sur la durée du test")


if __name__ == "__main__":
    unittest.main()