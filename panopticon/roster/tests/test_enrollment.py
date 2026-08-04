"""
panopticon/roster/tests/test_enrollment.py

Tests unitaires d'EnrollmentService : refus catégorique sans consentement
explicite, enrôlement réussi avec photos réelles (embeddings calculés +
photos copiées dans le stockage local), refus si aucune photo n'a de visage
exploitable, et suppression complète (droit à l'effacement) via le service.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import cv2

from roster.config import EmbedderConfig
from roster.embedder import MockEmbedder
from roster.enrollment import ConsentNotGivenError, EnrollmentService, NoFaceDetectedError
from roster.store import PersonStore

_PHOTOS_DIR = os.environ.get("ROSTER_TEST_PHOTOS_DIR", "/home/claude/test_photos")


class TestEnrollmentService(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="roster_enroll_test_"))
        self.store = PersonStore(self.tmp_dir / "persons.json", self.tmp_dir / "reference_photos")
        self.embedder = MockEmbedder(EmbedderConfig(backend="mock"))
        self.embedder.warmup()
        self.service = EnrollmentService(self.store, self.embedder, self.tmp_dir / "reference_photos")

        # Image sans visage, générée localement (pas de dépendance à un fichier externe).
        self.blank_image_path = str(self.tmp_dir / "blank.jpg")
        cv2.imwrite(self.blank_image_path, np.zeros((200, 200, 3), dtype="uint8"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_enrollment_refused_without_consent(self) -> None:
        with self.assertRaises(ConsentNotGivenError):
            self.service.enroll_person("Alice", [self.blank_image_path], consent_given=False)
        self.assertEqual(len(self.store), 0)

    def test_enrollment_refused_with_no_face_photos(self) -> None:
        with self.assertRaises(NoFaceDetectedError):
            self.service.enroll_person("Alice", [self.blank_image_path], consent_given=True)
        self.assertEqual(len(self.store), 0)

    @unittest.skipUnless(os.path.isdir(_PHOTOS_DIR), "Photos de test réelles indisponibles dans cet environnement")
    def test_successful_enrollment_with_real_photo(self) -> None:
        photo_path = os.path.join(_PHOTOS_DIR, "obama1.jpg")
        person = self.service.enroll_person("Barack", [photo_path], consent_given=True, notes="test")

        self.assertEqual(len(person.embeddings), 1)
        self.assertEqual(len(person.reference_photo_paths), 1)
        self.assertTrue(Path(person.reference_photo_paths[0]).is_file())
        self.assertIsNotNone(person.consent_confirmed_at)
        self.assertEqual(len(self.store), 1)

    @unittest.skipUnless(os.path.isdir(_PHOTOS_DIR), "Photos de test réelles indisponibles dans cet environnement")
    def test_partial_failure_still_enrolls_with_valid_photos(self) -> None:
        photo_path = os.path.join(_PHOTOS_DIR, "obama1.jpg")
        person = self.service.enroll_person(
            "Barack", [photo_path, self.blank_image_path], consent_given=True,
        )
        # 1 photo valide sur 2 fournies : enrôlement accepté (>= 1 embedding exploitable).
        self.assertEqual(len(person.embeddings), 1)

    @unittest.skipUnless(os.path.isdir(_PHOTOS_DIR), "Photos de test réelles indisponibles dans cet environnement")
    def test_deletion_removes_person_and_photos(self) -> None:
        photo_path = os.path.join(_PHOTOS_DIR, "obama1.jpg")
        person = self.service.enroll_person("Barack", [photo_path], consent_given=True)
        stored_photo = Path(person.reference_photo_paths[0])
        self.assertTrue(stored_photo.is_file())

        deleted = self.service.delete_person(person.person_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.store.get(person.person_id))
        self.assertFalse(stored_photo.is_file())


if __name__ == "__main__":
    unittest.main()
