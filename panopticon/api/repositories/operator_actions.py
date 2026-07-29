"""Repository SYS-LOG — actions opérateur (démarrage/arrêt de module, caméras…)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import OperatorActionRow
from shared.models import OperatorAction


def _row_to_model(row: OperatorActionRow) -> OperatorAction:
    return OperatorAction(
        id=UUID(row.id),
        action=row.action,
        target=row.target,
        detail=row.detail_json or {},
        timestamp=row.timestamp,
    )


async def log_action(
    session: AsyncSession, action: str, target: str, detail: dict | None = None
) -> OperatorAction:
    """Enregistre une action opérateur. Ne doit jamais faire échouer l'opération appelante."""
    model = OperatorAction(action=action, target=target, detail=detail or {})
    row = OperatorActionRow(
        id=str(model.id),
        action=model.action,
        target=model.target,
        detail_json=model.detail,
        timestamp=model.timestamp,
    )
    session.add(row)
    await session.commit()
    return model


async def list_actions(
    session: AsyncSession, action: str | None = None, limit: int = 100
) -> list[OperatorAction]:
    q = select(OperatorActionRow).order_by(OperatorActionRow.timestamp.desc()).limit(limit)
    if action:
        q = q.where(OperatorActionRow.action == action)
    result = await session.execute(q)
    return [_row_to_model(r) for r in result.scalars()]
