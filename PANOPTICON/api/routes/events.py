"""Routes événements SYS-LOG."""

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.repositories import events as event_repo
from shared.models import DetectionEvent

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[DetectionEvent])
async def list_events(
    camera_id: UUID | None = None,
    event_type: str | None = None,
    zone: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=100, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[DetectionEvent]:
    return await event_repo.list_events(
        session,
        camera_id=camera_id,
        event_type=event_type,
        zone=zone,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.get("/{event_id}", response_model=DetectionEvent)
async def get_event(
    event_id: UUID, session: AsyncSession = Depends(get_session)
) -> DetectionEvent:
    event = await event_repo.get_event(session, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")
    return event


@router.get("/{event_id}/thumbnail")
async def event_thumbnail(event_id: UUID, session: AsyncSession = Depends(get_session)):
    event = await event_repo.get_event(session, event_id)
    if not event or not event.thumbnail_path:
        raise HTTPException(status_code=404, detail="Miniature introuvable")
    path = Path(event.thumbnail_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier miniature absent")
    return FileResponse(path, media_type="image/jpeg")
