"""Routes ROSTER — personnes enrôlées (opt-in, consentement horodaté requis).

Le consentement n'est pas un simple champ décoratif : la requête est
rejetée (400) si `consent` n'est pas explicitement à true. Aucune photo
n'est traitée sans ce paramètre.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.repositories import operator_actions as action_repo
from api.repositories import persons as person_repo
from shared.config import settings
from shared.models import EnrolledPerson

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("", response_model=list[EnrolledPerson])
async def list_persons(session: AsyncSession = Depends(get_session)) -> list[EnrolledPerson]:
    return await person_repo.list_persons(session)


@router.post("", response_model=EnrolledPerson, status_code=201)
async def enroll_person(
    name: str = Form(...),
    consent: bool = Form(...),
    photos: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
) -> EnrolledPerson:
    if not consent:
        raise HTTPException(
            status_code=400,
            detail=(
                "Consentement explicite requis pour l'enrôlement ROSTER "
                "(paramètre 'consent' à true). Obtenez l'accord de la personne avant d'enrôler."
            ),
        )
    if not photos:
        raise HTTPException(status_code=400, detail="Au moins une photo de référence est requise.")

    import cv2
    import numpy as np

    from modules.roster.face_engine import FaceEngine

    person_id = uuid4()
    photo_dir = settings.storage_path / "roster" / str(person_id)
    photo_dir.mkdir(parents=True, exist_ok=True)

    engine = FaceEngine()
    embeddings: list[list[float]] = []
    saved_paths: list[str] = []

    for idx, photo in enumerate(photos):
        content = await photo.read()
        path = photo_dir / f"{idx}.jpg"
        path.write_bytes(content)
        saved_paths.append(str(path))

        arr = np.frombuffer(content, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            continue
        try:
            emb = engine.extract_embedding(image)
        except Exception:
            logger.exception("Échec d'extraction d'empreinte faciale pour %s", path)
            continue
        if emb is not None:
            embeddings.append(emb.vector)

    if not embeddings:
        raise HTTPException(
            status_code=422,
            detail=(
                "Aucun visage détecté dans les photos fournies. "
                "Utilisez des photos nettes, cadrées sur le visage, bien éclairées."
            ),
        )

    avg_embedding = (np.mean(embeddings, axis=0)).tolist()
    person = await person_repo.create_person(
        session,
        person_id=person_id,
        name=name,
        consent_confirmed_at=datetime.utcnow(),
        reference_photo_paths=saved_paths,
        face_embedding=avg_embedding,
    )

    try:
        await action_repo.log_action(
            session, "person_enrolled", str(person.id), {"name": name, "photos": len(saved_paths)}
        )
    except Exception:
        logger.exception("Échec de journalisation SYS-LOG pour l'enrôlement de %s", person.id)

    return person


@router.delete("/{person_id}", status_code=204)
async def delete_person(person_id: UUID, session: AsyncSession = Depends(get_session)) -> Response:
    deleted = await person_repo.delete_person(session, person_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Personne introuvable")
    try:
        await action_repo.log_action(session, "person_deleted", str(person_id))
    except Exception:
        logger.exception("Échec de journalisation SYS-LOG pour la suppression de %s", person_id)
    return Response(status_code=204)
