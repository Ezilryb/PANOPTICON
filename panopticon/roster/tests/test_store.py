"""
panopticon/roster/tests/test_store.py

Tests unitaires de PersonStore : persistance JSON (écriture puis relecture
depuis un nouveau store, comme après un redémarrage de ROSTER), et droit à
l'effacement (suppression de l'entrée ET des photos de référence sur disque).
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from roster.data_types import EnrolledPerson
from roster.store import PersonStore


class TestPersonStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="roster_test_"))
        self.db_path = self.tmp_dir / "persons.json"
        self.photos_dir = self.tmp_dir / "reference_photos"
        self.store = PersonStore(self.db_path, self.photos_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_person(self, person_id: str = "abc123", with_photo: bool = True) -> EnrolledPerson:
        photo_paths = []
        if with_photo:
            person_dir = self.photos_dir / person_id
            person_dir.mkdir(parents=True, exist_ok=True)
            photo_path = person_dir / "ref_00.jpg"
            photo_path.write_bytes(b"fake-jpeg-bytes")
            photo_paths.append(str(photo_path))

        return EnrolledPerson(
            person_id=person_id,
            name="Alice",
            consent_confirmed_at=time.time(),
            embeddings=[[0.1, 0.2, 0.3]],
            reference_photo_paths=photo_paths,
        )

    def test_add_and_get(self) -> None:
        person = self._make_person()
        self.store.add_person(person)
        fetched = self.store.get("abc123")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Alice")
        self.assertEqual(len(self.store), 1)

    def test_duplicate_id_rejected(self) -> None:
        self.store.add_person(self._make_person())
        with self.assertRaises(ValueError):
            self.store.add_person(self._make_person())

    def test_persistence_across_instances(self) -> None:
        self.store.add_person(self._make_person())

        # Simule un redémarrage de ROSTER : nouvelle instance pointant sur le même fichier.
        reloaded_store = PersonStore(self.db_path, self.photos_dir)
        self.assertEqual(len(reloaded_store), 1)
        self.assertEqual(reloaded_store.get("abc123").name, "Alice")

    def test_delete_removes_entry_and_photos(self) -> None:
        person = self._make_person()
        self.store.add_person(person)
        photo_path = Path(person.reference_photo_paths[0])
        self.assertTrue(photo_path.is_file())

        deleted = self.store.delete_person("abc123")
        self.assertTrue(deleted)
        self.assertIsNone(self.store.get("abc123"))
        self.assertFalse(photo_path.is_file(), "la photo de référence doit être supprimée du disque")

    def test_delete_unknown_person_is_idempotent(self) -> None:
        self.assertFalse(self.store.delete_person("does-not-exist"))

    def test_find_by_name_case_insensitive(self) -> None:
        self.store.add_person(self._make_person())
        matches = self.store.find_by_name("alice")
        self.assertEqual(len(matches), 1)


if __name__ == "__main__":
    unittest.main()
