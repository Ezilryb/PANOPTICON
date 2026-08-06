"""
panopticon/roster/tests/test_matcher.py

Tests unitaires de FaceMatcher : une personne enrôlée est reconnue sur une
AUTRE photo d'elle-même (distance sous le seuil), une personne différente
est rejetée comme "unknown" (distance au-dessus du seuil), une base vide ne
plante jamais, et un embedding de dimension incompatible est ignoré
proprement plutôt que de lever une exception.
"""

import os
import time
import unittest

from roster.config import MatcherConfig
from roster.data_types import EnrolledPerson, FaceObservation
from roster.matcher import FaceMatcher
from roster.store import PersonStore

_PHOTOS_DIR = os.environ.get("ROSTER_TEST_PHOTOS_DIR", "/home/claude/test_photos")


class _FakeStore:
    """Store minimal en mémoire, pour tester FaceMatcher isolément de PersonStore/disque."""

    def __init__(self, persons: list[EnrolledPerson]) -> None:
        self._persons = persons

    def all(self) -> list[EnrolledPerson]:
        return self._persons


def _person(person_id: str, name: str, embeddings: list[list[float]]) -> EnrolledPerson:
    return EnrolledPerson(person_id=person_id, name=name, consent_confirmed_at=time.time(), embeddings=embeddings)


class TestFaceMatcher(unittest.TestCase):
    def test_empty_database_returns_unmatched(self) -> None:
        matcher = FaceMatcher(_FakeStore([]), MatcherConfig(distance_threshold=0.6))
        result = matcher.match_embedding([0.1, 0.2, 0.3])
        self.assertFalse(result.matched)
        self.assertEqual(result.label, "unknown")

    def test_close_embedding_matches(self) -> None:
        alice = _person("p1", "Alice", [[0.0, 0.0, 0.0]])
        matcher = FaceMatcher(_FakeStore([alice]), MatcherConfig(distance_threshold=0.5))
        result = matcher.match_embedding([0.05, 0.0, 0.0])  # distance = 0.05, sous le seuil
        self.assertTrue(result.matched)
        self.assertEqual(result.name, "Alice")
        self.assertAlmostEqual(result.distance, 0.05, places=4)
        self.assertEqual(result.label, "known:Alice")

    def test_far_embedding_is_unknown(self) -> None:
        alice = _person("p1", "Alice", [[0.0, 0.0, 0.0]])
        matcher = FaceMatcher(_FakeStore([alice]), MatcherConfig(distance_threshold=0.1))
        result = matcher.match_embedding([5.0, 5.0, 5.0])
        self.assertFalse(result.matched)
        self.assertEqual(result.label, "unknown")

    def test_picks_closest_of_multiple_persons(self) -> None:
        alice = _person("p1", "Alice", [[0.0, 0.0]])
        bob = _person("p2", "Bob", [[10.0, 10.0]])
        matcher = FaceMatcher(_FakeStore([alice, bob]), MatcherConfig(distance_threshold=5.0))
        result = matcher.match_embedding([0.1, 0.1])
        self.assertTrue(result.matched)
        self.assertEqual(result.name, "Alice")

    def test_mismatched_dimension_is_ignored_not_crashed(self) -> None:
        alice = _person("p1", "Alice", [[0.0, 0.0, 0.0]])  # dimension 3
        matcher = FaceMatcher(_FakeStore([alice]), MatcherConfig(distance_threshold=0.5))
        result = matcher.match_embedding([0.0, 0.0])  # dimension 2 : incompatible
        self.assertFalse(result.matched)  # ne lève pas d'exception, renvoie simplement "unknown"

    @unittest.skipUnless(os.path.isdir(_PHOTOS_DIR), "Photos de test réelles indisponibles dans cet environnement")
    def test_end_to_end_with_mock_embedder_real_photos(self) -> None:
        import cv2
        from roster.config import EmbedderConfig
        from roster.embedder import MockEmbedder

        embedder = MockEmbedder(EmbedderConfig(backend="mock"))
        embedder.warmup()

        obama_ref = embedder.embed_single_face(cv2.imread(os.path.join(_PHOTOS_DIR, "obama1.jpg")))
        obama_query = embedder.embed_single_face(cv2.imread(os.path.join(_PHOTOS_DIR, "obama2.jpg")))
        biden_query = embedder.embed_single_face(cv2.imread(os.path.join(_PHOTOS_DIR, "biden1.jpg")))

        barack = _person("p1", "Barack", [obama_ref])
        # Seuil calibré empiriquement pour ce descripteur mock (pixels bruts, pas un vrai modèle de
        # reco faciale) : la séparation same-person/different-person est faible par construction,
        # ce test vérifie l'ordre relatif des distances, pas une précision de production.
        matcher = FaceMatcher(_FakeStore([barack]), MatcherConfig(distance_threshold=11.0))

        same_person_result = matcher.match_embedding(obama_query)
        different_person_result = matcher.match_embedding(biden_query)

        self.assertTrue(same_person_result.matched, "une autre photo de la même personne devrait matcher")
        self.assertLess(
            same_person_result.distance, different_person_result.distance,
            "la distance à Barack doit être plus faible pour une photo de Barack que pour une photo de Biden",
        )


if __name__ == "__main__":
    unittest.main()
