"""
panopticon/roster/enrollment.py

Flow d'enrôlement de ROSTER : strictement opt-in (critère section 5/10 du
brief projet). Aucune personne n'est ajoutée sans un consentement explicite
passé en paramètre par l'appelant (API/CLI) — ROSTER ne déduit ni ne suppose
jamais ce consentement. Calcule les embeddings de référence à partir de 3-5
photos, copie ces photos dans le stockage local de ROSTER, et horodate le
consentement au moment de l'enrôlement.
"""

import logging
import shutil
import time
import uuid
from pathlib import Path

import cv2

from .data_types import EnrolledPerson
from .embedder import BaseEmbedder
from .store import PersonStore

logger = logging.getLogger("roster.enrollment")

_MIN_REFERENCE_PHOTOS = 1
_RECOMMENDED_REFERENCE_PHOTOS = 3


class ConsentNotGivenError(Exception):
    """Levée si un enrôlement est tenté sans consentement explicite. Ne jamais contourner."""


class NoFaceDetectedError(Exception):
    """Levée si aucune photo fournie ne contient de visage exploitable."""


class EnrollmentService:
    """Orchestration de l'enrôlement/suppression d'une personne dans ROSTER."""

    def __init__(self, store: PersonStore, embedder: BaseEmbedder, reference_photos_dir: Path) -> None:
        self.store = store
        self.embedder = embedder
        self.reference_photos_dir = reference_photos_dir

    def enroll_person(
        self,
        name: str,
        photo_paths: list[str],
        consent_given: bool,
        notes: str = "",
    ) -> EnrolledPerson:
        """
        Enrôle une nouvelle personne. `consent_given` DOIT provenir d'une
        action explicite de l'utilisateur (case cochée, écran de consentement
        signé...) — c'est à l'appelant (API/CLI) de s'en assurer, ROSTER se
        contente de refuser catégoriquement si le paramètre est False.
        """
        if not consent_given:
            raise ConsentNotGivenError(
                f"Enrôlement refusé pour '{name}' : consentement explicite requis avant tout traitement."
            )
        if not photo_paths:
            raise ValueError("Au moins une photo de référence est requise pour l'enrôlement.")
        if len(photo_paths) < _RECOMMENDED_REFERENCE_PHOTOS:
            logger.warning(
                "Enrôlement de '%s' avec seulement %d photo(s) — %d à %d sont recommandées pour un matching fiable",
                name, len(photo_paths), _RECOMMENDED_REFERENCE_PHOTOS, 5,
            )

        person_id = uuid.uuid4().hex
        person_dir = self.reference_photos_dir / person_id
        person_dir.mkdir(parents=True, exist_ok=True)

        embeddings: list[list[float]] = []
        stored_photo_paths: list[str] = []

        for i, source_path in enumerate(photo_paths):
            image = cv2.imread(source_path)
            if image is None:
                logger.warning("Photo illisible, ignorée : %s", source_path)
                continue

            embedding = self.embedder.embed_single_face(image)
            if embedding is None:
                logger.warning("Aucun visage détecté dans la photo, ignorée : %s", source_path)
                continue

            dest_path = person_dir / f"ref_{i:02d}{Path(source_path).suffix or '.jpg'}"
            shutil.copy2(source_path, dest_path)

            embeddings.append(embedding)
            stored_photo_paths.append(str(dest_path))

        if len(embeddings) < _MIN_REFERENCE_PHOTOS:
            # Nettoyage : ne pas laisser un dossier de personne à moitié enrôlée sur disque.
            shutil.rmtree(person_dir, ignore_errors=True)
            raise NoFaceDetectedError(
                f"Enrôlement refusé pour '{name}' : aucun visage exploitable trouvé dans les {len(photo_paths)} "
                f"photo(s) fournie(s)."
            )

        person = EnrolledPerson(
            person_id=person_id,
            name=name,
            consent_confirmed_at=time.time(),
            embeddings=embeddings,
            reference_photo_paths=stored_photo_paths,
            notes=notes,
        )
        self.store.add_person(person)
        logger.info(
            "Enrôlement terminé : %s (id=%s, %d/%d photo(s) exploitée(s))",
            name, person_id, len(embeddings), len(photo_paths),
        )
        return person

    def delete_person(self, person_id: str) -> bool:
        """Droit à l'effacement : délègue au store (supprime entrée + photos)."""
        return self.store.delete_person(person_id)
