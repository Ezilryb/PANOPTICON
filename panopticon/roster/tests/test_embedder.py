"""
panopticon/roster/tests/test_embedder.py

Tests unitaires de MockEmbedder : détection de visage sur des photos réelles,
stabilité de l'embedding (déterministe pour une même image), et séparation
suffisante entre deux personnes différentes pour que le matching en aval ait
un sens (même visage : distance faible ; visages différents : distance plus
grande que pour deux photos de la même personne).
"""

import os
import unittest

import cv2

from roster.config import EmbedderConfig
from roster.embedder import MockEmbedder

_PHOTOS_DIR = os.environ.get("ROSTER_TEST_PHOTOS_DIR", "/home/claude/test_photos")


def _photo(name: str):
    path = os.path.join(_PHOTOS_DIR, name)
    if not os.path.isfile(path):
        return None
    return cv2.imread(path)


@unittest.skipUnless(os.path.isdir(_PHOTOS_DIR), "Photos de test réelles indisponibles dans cet environnement")
class TestMockEmbedder(unittest.TestCase):
    def setUp(self) -> None:
        self.embedder = MockEmbedder(EmbedderConfig(backend="mock"))
        self.embedder.warmup()

    def test_detects_face_in_real_photo(self) -> None:
        image = _photo("obama1.jpg")
        self.assertIsNotNone(image, "photo de test manquante")
        results = self.embedder.detect_and_embed(image)
        self.assertGreaterEqual(len(results), 1)
        bbox, embedding = results[0]
        self.assertEqual(len(bbox), 4)
        self.assertEqual(len(embedding), MockEmbedder._EMBED_SIZE ** 2)

    def test_embedding_is_deterministic(self) -> None:
        image = _photo("obama1.jpg")
        emb1 = self.embedder.embed_single_face(image)
        emb2 = self.embedder.embed_single_face(image)
        self.assertEqual(emb1, emb2)

    def test_same_person_closer_than_different_person(self) -> None:
        import numpy as np

        obama1 = self.embedder.embed_single_face(_photo("obama1.jpg"))
        obama2 = self.embedder.embed_single_face(_photo("obama2.jpg"))
        biden1 = self.embedder.embed_single_face(_photo("biden1.jpg"))

        self.assertIsNotNone(obama1)
        self.assertIsNotNone(obama2)
        self.assertIsNotNone(biden1)

        dist_same_person = np.linalg.norm(np.array(obama1) - np.array(obama2))
        dist_different_person = np.linalg.norm(np.array(obama1) - np.array(biden1))

        self.assertLess(
            dist_same_person, dist_different_person,
            "le descripteur mock devrait rapprocher deux photos de la même personne "
            "plus que deux photos de personnes différentes",
        )

    def test_no_face_returns_none(self) -> None:
        import numpy as np
        blank = np.zeros((200, 200, 3), dtype="uint8")
        self.assertIsNone(self.embedder.embed_single_face(blank))


if __name__ == "__main__":
    unittest.main()
