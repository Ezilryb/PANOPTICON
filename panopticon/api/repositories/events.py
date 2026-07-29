"""Repository événements."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import EventRow
from shared.models import DetectionEvent, EventSummary


def _row_to_model(row: EventRow) -> DetectionEvent:
    return DetectionEvent(
        id=UUID(row.id),
        camera_id=UUID(row.camera_id),
        source_module=row.source_module,
        event_type=row.event_type,  # type: ignore[arg-type]
        zone=row.zone,
        timestamp=row.timestamp,
        thumbnail_path=row.thumbnail_path,
        metadata=row.metadata_json or {},
    )


async def create_event(session: AsyncSession, event: DetectionEvent) -> DetectionEvent:
    row = EventRow(
        id=str(event.id),
        camera_id=str(event.camera_id),
        source_module=event.source_module,
        event_type=event.event_type,
        zone=event.zone,
        timestamp=event.timestamp,
        thumbnail_path=event.thumbnail_path,
        metadata_json=event.metadata,
    )
    session.add(row)
    await session.commit()
    return event


async def list_events(
    session: AsyncSession,
    camera_id: UUID | None = None,
    event_type: str | None = None,
    zone: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 100,
) -> list[DetectionEvent]:
    q = select(EventRow).order_by(EventRow.timestamp.desc()).limit(limit)
    if camera_id:
        q = q.where(EventRow.camera_id == str(camera_id))
    if event_type:
        q = q.where(EventRow.event_type == event_type)
    if zone:
        q = q.where(EventRow.zone == zone)
    if date_from:
        q = q.where(EventRow.timestamp >= date_from)
    if date_to:
        q = q.where(EventRow.timestamp <= date_to)
    result = await session.execute(q)
    return [_row_to_model(r) for r in result.scalars()]


async def get_event(session: AsyncSession, event_id: UUID) -> DetectionEvent | None:
    row = await session.get(EventRow, str(event_id))
    return _row_to_model(row) if row else None


async def summarize_events(session: AsyncSession, hours: int = 24) -> EventSummary:
    """SYS-LOG — agrège les événements des dernières `hours` heures par type/zone/module."""
    since = datetime.utcnow() - timedelta(hours=hours)
    q = select(EventRow).where(EventRow.timestamp >= since)
    result = await session.execute(q)
    rows = list(result.scalars())

    by_type: dict[str, int] = {}
    by_zone: dict[str, int] = {}
    by_module: dict[str, int] = {}
    for row in rows:
        by_type[row.event_type] = by_type.get(row.event_type, 0) + 1
        by_zone[row.zone] = by_zone.get(row.zone, 0) + 1
        by_module[row.source_module] = by_module.get(row.source_module, 0) + 1

    return EventSummary(
        period_hours=hours,
        total_events=len(rows),
        by_type=by_type,
        by_zone=by_zone,
        by_module=by_module,
    )
