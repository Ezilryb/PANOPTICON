"""Repository PULSE_TRACK — règles et alertes."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AlertRow, RuleRow
from shared.models import Alert, Rule


def _rule_row_to_model(row: RuleRow) -> Rule:
    return Rule(
        id=UUID(row.id),
        name=row.name,
        conditions=row.conditions_json or {},
        action=row.action,  # type: ignore[arg-type]
        action_target=row.action_target,
        enabled=row.enabled,
    )


def _alert_row_to_model(row: AlertRow) -> Alert:
    return Alert(
        id=UUID(row.id),
        rule_id=UUID(row.rule_id),
        triggered_at=row.triggered_at,
        payload=row.payload_json or {},
        acknowledged=row.acknowledged,
    )


async def list_rules(session: AsyncSession) -> list[Rule]:
    result = await session.execute(select(RuleRow).order_by(RuleRow.name))
    return [_rule_row_to_model(r) for r in result.scalars()]


async def get_rule(session: AsyncSession, rule_id: UUID) -> Rule | None:
    row = await session.get(RuleRow, str(rule_id))
    return _rule_row_to_model(row) if row else None


async def create_rule(session: AsyncSession, rule: Rule) -> Rule:
    row = RuleRow(
        id=str(rule.id),
        name=rule.name,
        conditions_json=rule.conditions,
        action=rule.action,
        action_target=rule.action_target,
        enabled=rule.enabled,
    )
    session.add(row)
    await session.commit()
    return rule


async def update_rule(
    session: AsyncSession,
    rule_id: UUID,
    name: str | None = None,
    conditions: dict | None = None,
    action: str | None = None,
    action_target: str | None = None,
    enabled: bool | None = None,
) -> Rule | None:
    row = await session.get(RuleRow, str(rule_id))
    if not row:
        return None
    if name is not None:
        row.name = name
    if conditions is not None:
        row.conditions_json = conditions
    if action is not None:
        row.action = action
    if action_target is not None:
        row.action_target = action_target
    if enabled is not None:
        row.enabled = enabled
    await session.commit()
    return _rule_row_to_model(row)


async def delete_rule(session: AsyncSession, rule_id: UUID) -> bool:
    row = await session.get(RuleRow, str(rule_id))
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def create_alert(session: AsyncSession, rule_id: UUID, payload: dict) -> Alert:
    model = Alert(rule_id=rule_id, payload=payload)
    row = AlertRow(
        id=str(model.id),
        rule_id=str(model.rule_id),
        triggered_at=model.triggered_at,
        payload_json=model.payload,
        acknowledged=False,
    )
    session.add(row)
    await session.commit()
    return model


async def list_alerts(
    session: AsyncSession, acknowledged: bool | None = None, limit: int = 100
) -> list[Alert]:
    q = select(AlertRow).order_by(AlertRow.triggered_at.desc()).limit(limit)
    if acknowledged is not None:
        q = q.where(AlertRow.acknowledged.is_(acknowledged))
    result = await session.execute(q)
    return [_alert_row_to_model(r) for r in result.scalars()]


async def acknowledge_alert(session: AsyncSession, alert_id: UUID) -> Alert | None:
    row = await session.get(AlertRow, str(alert_id))
    if not row:
        return None
    row.acknowledged = True
    await session.commit()
    return _alert_row_to_model(row)
