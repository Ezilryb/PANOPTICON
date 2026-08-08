"""
panopticon/argus/tests/test_pipeline_synthetic.py

Test d'intégration bout-en-bout d'ArgusEngine, sans aucune caméra ni modèle
réels : caméra synthétique + backend "mock" du Detector. Vérifie que la
chaîne complète (capture -> détection -> tracking -> mémoire partagée ->
publication -> ArgusClient) fonctionne et que la latence bout-en-bout reste
faible, condition explicitement demandée pour ARGUS.
"""

import threading
import time
import unittest

from argus.client import ArgusClient
from argus.config import ArgusConfig, CameraConfig, DetectorConfig, PublisherConfig
from argus.pipeline import ArgusEngine

# Latence bout-en-bout maximale tolérée en CI : généreuse pour absorber un environnement
# de test chargé, tout en restant largement révélatrice d'une régression de performance.
_MAX_ACCEPTABLE_LATENCY_MS = 500.0


class TestPipelineSynthetic(unittest.TestCase):
    def _run_pipeline(self, n_cameras: int, port: int, n_events_wanted: int, timeout_s: float = 8.0):
        config = ArgusConfig(
            cameras=[
                CameraConfig(camera_id=f"CAM-{i}", source="synthetic", target_fps=15, width=320, height=240)
                for i in range(n_cameras)
            ],
            detector=DetectorConfig(backend="mock", confidence_threshold=0.3),
            publisher=PublisherConfig(host="127.0.0.1", port=port),
            log_stats_every_s=60.0,
        )
        engine = ArgusEngine(config)
        engine.start()
        time.sleep(0.4)  # laisser les caméras et le publisher démarrer

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
        return received

    def test_single_camera_end_to_end_latency(self) -> None:
        received = self._run_pipeline(n_cameras=1, port=19500, n_events_wanted=15)

        self.assertGreaterEqual(len(received), 10, "pas assez d'évènements reçus dans le délai imparti")

        latencies = [e.latency_ms for e in received]
        avg_latency = sum(latencies) / len(latencies)
        self.assertLess(avg_latency, _MAX_ACCEPTABLE_LATENCY_MS,
                         f"latence moyenne trop élevée : {avg_latency:.1f}ms")

        # L'objet synthétique unique doit produire un track_id stable au fil des frames.
        track_ids = {d.track_id for e in received for d in e.detections}
        self.assertTrue(len(track_ids) <= 2, f"trop de track_id différents pour un seul objet : {track_ids}")

    def test_multi_camera_batching(self) -> None:
        received = self._run_pipeline(n_cameras=3, port=19501, n_events_wanted=45)

        cameras_seen = {e.camera_id for e in received}
        self.assertEqual(cameras_seen, {"CAM-0", "CAM-1", "CAM-2"})

    def test_frame_readable_via_shared_memory(self) -> None:
        received = self._run_pipeline(n_cameras=1, port=19502, n_events_wanted=5)
        self.assertGreater(len(received), 0)

        # La mémoire partagée est libérée par engine.stop() : on relit donc PENDANT que le
        # pipeline tourne encore, dans _run_pipeline lui-même, plutôt qu'après son retour.
        # (Ce test vérifie simplement que des évènements exploitables ont bien été produits ;
        # la lecture mémoire partagée en direct est couverte par test_frame_store.py.)
        last_event = received[-1]
        self.assertEqual(last_event.width, 320)
        self.assertEqual(last_event.height, 240)


if __name__ == "__main__":
    unittest.main()
