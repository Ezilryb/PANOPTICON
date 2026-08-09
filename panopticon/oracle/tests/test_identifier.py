"""
panopticon/oracle/tests/test_identifier.py

Tests unitaires des backends d'identification :
- MockIdentifier : déterminisme, crop vide.
- GoogleVisionIdentifier : parsing de la réponse Web Detection (webEntities
  et repli sur bestGuessLabels), filtrage par seuil de confiance, échec
  réseau non-fatal, et refus explicite au warmup() sans clé API — le tout
  SANS aucun appel réseau réel (payloads de test simulant la forme exacte
  de la réponse Google Cloud Vision, et `requests` remplacé par un mock
  pour le test d'intégration `identify()`).
"""

import os
import unittest
from unittest.mock import MagicMock

import numpy as np

from oracle.config import IdentifierConfig
from oracle.identifier import GoogleVisionIdentifier, MockIdentifier, build_identifier


def _crop(shape=(60, 60, 3)) -> np.ndarray:
    return np.full(shape, 90, dtype=np.uint8)


class TestMockIdentifier(unittest.TestCase):
    def setUp(self) -> None:
        self.identifier = MockIdentifier()
        self.identifier.warmup()

    def test_deterministic_for_same_crop(self) -> None:
        crop = _crop()
        r1 = self.identifier.identify(crop)
        r2 = self.identifier.identify(crop)
        self.assertEqual(r1.label, r2.label)

    def test_different_crops_give_different_labels(self) -> None:
        r1 = self.identifier.identify(_crop((60, 60, 3)))
        r2 = self.identifier.identify(np.full((60, 60, 3), 200, dtype=np.uint8))
        self.assertNotEqual(r1.label, r2.label)

    def test_empty_crop_returns_none(self) -> None:
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        self.assertIsNone(self.identifier.identify(empty))

    def test_source_is_labelled_mock(self) -> None:
        result = self.identifier.identify(_crop())
        self.assertEqual(result.source, "mock")


class TestBuildIdentifier(unittest.TestCase):
    def test_mock_backend(self) -> None:
        identifier = build_identifier(IdentifierConfig(backend="mock"))
        self.assertIsInstance(identifier, MockIdentifier)

    def test_google_vision_backend(self) -> None:
        identifier = build_identifier(IdentifierConfig(backend="google_vision"))
        self.assertIsInstance(identifier, GoogleVisionIdentifier)

    def test_unknown_backend_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_identifier(IdentifierConfig(backend="does-not-exist"))


class TestGoogleVisionParsing(unittest.TestCase):
    """Teste uniquement la logique de parsing (méthode privée mais pure), aucun appel réseau."""

    def setUp(self) -> None:
        self.identifier = GoogleVisionIdentifier(IdentifierConfig(confidence_threshold=0.4, max_results=5))

    def test_parses_top_web_entity(self) -> None:
        payload = {
            "responses": [{
                "webDetection": {
                    "webEntities": [
                        {"entityId": "/m/x1", "score": 0.82, "description": "Toyota Camry"},
                        {"entityId": "/m/x2", "score": 0.51, "description": "Sedan"},
                    ],
                    "bestGuessLabels": [{"label": "toyota camry", "languageCode": "en"}],
                }
            }]
        }
        result = self.identifier._parse_web_detection(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "Toyota Camry")
        self.assertAlmostEqual(result.confidence, 0.82, places=4)
        self.assertEqual(result.candidates, ["Sedan"])
        self.assertEqual(result.source, "google_vision")

    def test_falls_back_to_best_guess_label_without_entities(self) -> None:
        payload = {"responses": [{"webDetection": {"webEntities": [], "bestGuessLabels": [{"label": "office chair"}]}}]}
        result = self.identifier._parse_web_detection(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "office chair")

    def test_below_confidence_threshold_returns_none(self) -> None:
        payload = {"responses": [{"webDetection": {
            "webEntities": [{"entityId": "/m/x1", "score": 0.1, "description": "Objet incertain"}],
        }}]}
        result = self.identifier._parse_web_detection(payload)
        self.assertIsNone(result)

    def test_entities_without_description_are_ignored(self) -> None:
        payload = {"responses": [{"webDetection": {
            "webEntities": [
                {"entityId": "/m/x1", "score": 0.9},  # pas de "description" -> ignoré
                {"entityId": "/m/x2", "score": 0.6, "description": "Vélo de route"},
            ],
        }}]}
        result = self.identifier._parse_web_detection(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "Vélo de route")

    def test_completely_empty_response_returns_none(self) -> None:
        payload = {"responses": [{"webDetection": {}}]}
        self.assertIsNone(self.identifier._parse_web_detection(payload))

    def test_no_responses_returns_none(self) -> None:
        self.assertIsNone(self.identifier._parse_web_detection({"responses": []}))


class TestGoogleVisionWarmupAndIdentify(unittest.TestCase):
    _ENV_VAR = "ORACLE_TEST_VISION_API_KEY_UNSET"

    def setUp(self) -> None:
        os.environ.pop(self._ENV_VAR, None)  # s'assure qu'elle est absente, quel que soit l'environnement hôte

    def test_warmup_without_api_key_raises_clear_error(self) -> None:
        identifier = GoogleVisionIdentifier(IdentifierConfig(api_key_env_var=self._ENV_VAR))
        with self.assertRaises(RuntimeError) as ctx:
            identifier.warmup()
        self.assertIn(self._ENV_VAR, str(ctx.exception))

    def test_identify_before_warmup_raises(self) -> None:
        identifier = GoogleVisionIdentifier(IdentifierConfig())
        with self.assertRaises(RuntimeError):
            identifier.identify(_crop())

    def test_identify_calls_api_and_parses_result(self) -> None:
        identifier = GoogleVisionIdentifier(IdentifierConfig(confidence_threshold=0.3))
        # Contourne warmup() (qui exigerait une vraie clé + le paquet `requests`) en injectant
        # directement un faux module `requests` et une fausse clé, pour tester identify() de
        # bout en bout sans réseau ni dépendance à l'environnement d'exécution.
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "responses": [{"webDetection": {"webEntities": [
                {"entityId": "/m/x1", "score": 0.77, "description": "Canon EOS R6"},
            ]}}]
        }
        fake_requests = MagicMock()
        fake_requests.post.return_value = fake_response

        identifier._requests = fake_requests
        identifier._api_key = "fake-key-for-test"

        result = identifier.identify(_crop())

        self.assertIsNotNone(result)
        self.assertEqual(result.label, "Canon EOS R6")
        fake_requests.post.assert_called_once()
        _args, kwargs = fake_requests.post.call_args
        self.assertEqual(kwargs["params"], {"key": "fake-key-for-test"})

    def test_identify_network_failure_returns_none_not_raise(self) -> None:
        identifier = GoogleVisionIdentifier(IdentifierConfig())
        fake_requests = MagicMock()
        fake_requests.post.side_effect = ConnectionError("réseau indisponible")
        identifier._requests = fake_requests
        identifier._api_key = "fake-key-for-test"

        result = identifier.identify(_crop())  # ne doit lever aucune exception
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
