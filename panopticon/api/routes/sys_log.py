"""Routes SYS-LOG — résumé des événements et journal des actions opérateur."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.repositories import events as event_repo
from api.repositories import operator_actions as action_repo
from shared.models import EventSummary, OperatorAction

router = APIRouter(prefix="/api/sys-log", tags=["sys-log"])


@router.get("/summary", response_model=EventSummary)
async def events_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    session: AsyncSession = Depends(get_session),
) -> EventSummary:
    return await event_repo.summarize_events(session, hours=hours)


@router.get("/actions", response_model=list[OperatorAction])
async def list_actions(
    action: str | None = None,
    limit: int = Query(default=100, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[OperatorAction]:
    return await action_repo.list_actions(session, action=action, limit=limit)
