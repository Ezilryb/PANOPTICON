"""
panopticon/roster/store.py

Persistance des personnes enrôlées dans ROSTER : un fichier JSON unique
(`persons.json`) contenant, pour chaque personne, son nom, l'horodatage de
son consentement, ses embeddings de référence et les chemins de ses photos.
Écriture atomique (fichier temporaire + os.replace()) pour ne jamais laisser
la base dans un état corrompu si le process est interrompu en plein
enregistrement — même technique que `argus/frame_store.py`.

`delete_person()` implémente le droit à l'effacement (critère section 5 du
brief projet) : supprime l'entrée ET les photos de référence sur disque,
sans laisser de résidu.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from .data_types import EnrolledPerson

logger = logging.getLogger("roster.store")


class PersonStore:
    """
    Gère la base des personnes enrôlées. Thread-safe (un verrou protège
    lecture/écriture) car ROSTER peut être interrogé par le thread de
    matching pendant qu'un enrôlement/suppression est en cours via l'API/CLI.
    """

    def __init__(self, db_path: Path, reference_photos_dir: Path) -> None:
        self._db_path = db_path
        self._reference_photos_dir = reference_photos_dir
        self._lock = threading.RLock()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._reference_photos_dir.mkdir(parents=True, exist_ok=True)

        self._persons: dict[str, EnrolledPerson] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Chargement / écriture
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if not self._db_path.is_file():
            logger.info("Aucune base ROSTER existante (%s), démarrage avec une base vide", self._db_path)
            return
        try:
            raw = json.loads(self._db_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Base ROSTER illisible (%s) : %s — démarrage avec une base vide", self._db_path, exc)
            return

        for payload in raw.get("persons", []):
            person = EnrolledPerson.from_dict(payload)
            self._persons[person.person_id] = person
        logger.info("Base ROSTER chargée : %d personne(s) enrôlée(s)", len(self._persons))

    def _flush(self) -> None:
        """Écriture atomique de l'état courant : jamais de fichier à moitié écrit en cas de crash."""
        payload = {"persons": [p.to_dict() for p in self._persons.values()]}
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._db_path.parent), prefix=".persons_", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._db_path)
        except OSError:
            logger.error("Échec d'écriture de la base ROSTER (%s)", self._db_path)
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def add_person(self, person: EnrolledPerson) -> None:
        with self._lock:
            if person.person_id in self._persons:
                raise ValueError(f"Personne déjà enrôlée avec cet identifiant : {person.person_id}")
            self._persons[person.person_id] = person
            self._flush()
        logger.info("Personne enrôlée : %s (id=%s)", person.name, person.person_id)

    def get(self, person_id: str) -> Optional[EnrolledPerson]:
        with self._lock:
            return self._persons.get(person_id)

    def find_by_name(self, name: str) -> list[EnrolledPerson]:
        with self._lock:
            return [p for p in self._persons.values() if p.name.lower() == name.lower()]

    def all(self) -> list[EnrolledPerson]:
        with self._lock:
            return list(self._persons.values())

    def delete_person(self, person_id: str) -> bool:
        """
        Droit à l'effacement : supprime l'entrée de la base ET ses photos de
        référence sur disque. Renvoie False si la personne n'existait pas
        (idempotent, ne lève pas d'exception pour ce cas).
        """
        with self._lock:
            person = self._persons.pop(person_id, None)
            if person is None:
                return False

            for photo_path in person.reference_photo_paths:
                try:
                    Path(photo_path).unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning("Échec de suppression de la photo de référence %s : %s", photo_path, exc)

            self._flush()
        logger.info("Personne supprimée (droit à l'effacement) : %s (id=%s)", person.name, person_id)
        return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._persons)
