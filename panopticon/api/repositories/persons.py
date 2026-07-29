"""Repository ROSTER — personnes enrôlées (opt-in, consentement horodaté)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import EnrolledPersonRow
from shared.models import EnrolledPerson


def _row_to_model(row: EnrolledPersonRow) -> EnrolledPerson:
    return EnrolledPerson(
        id=UUID(row.id),
        name=row.name,
        consent_confirmed_at=row.consent_confirmed_at,
        reference_photo_paths=row.reference_photo_paths_json or [],
        face_embedding=row.face_embedding_json or [],
    )


async def list_persons(session: AsyncSession) -> list[EnrolledPerson]:
    result = await session.execute(select(EnrolledPersonRow).order_by(EnrolledPersonRow.name))
    return [_row_to_model(r) for r in result.scalars()]


async def get_person(session: AsyncSession, person_id: UUID) -> EnrolledPerson | None:
    row = await session.get(EnrolledPersonRow, str(person_id))
    return _row_to_model(row) if row else None


async def get_all_embeddings(session: AsyncSession) -> list[tuple[str, list[float]]]:
    """Pour le matching ROSTER : (person_id, embedding) de toutes les personnes enrôlées."""
    result = await session.execute(select(EnrolledPersonRow))
    return [(r.id, r.face_embedding_json) for r in result.scalars() if r.face_embedding_json]


async def create_person(
    session: AsyncSession,
    person_id: UUID,
    name: str,
    consent_confirmed_at: datetime,
    reference_photo_paths: list[str],
    face_embedding: list[float],
) -> EnrolledPerson:
    model = EnrolledPerson(
        id=person_id,
        name=name,
        consent_confirmed_at=consent_confirmed_at,
        reference_photo_paths=reference_photo_paths,
        face_embedding=face_embedding,
    )
    row = EnrolledPersonRow(
        id=str(model.id),
        name=model.name,
        consent_confirmed_at=model.consent_confirmed_at,
        reference_photo_paths_json=model.reference_photo_paths,
        face_embedding_json=model.face_embedding,
    )
    session.add(row)
    await session.commit()
    return model


async def delete_person(session: AsyncSession, person_id: UUID) -> bool:
    row = await session.get(EnrolledPersonRow, str(person_id))
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True
