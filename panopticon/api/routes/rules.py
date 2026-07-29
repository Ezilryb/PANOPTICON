"""Routes PULSE_TRACK — règles et alertes."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.repositories import operator_actions as action_repo
from api.repositories import rules as rule_repo
from shared.models import Alert, Rule

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["rules"])


class RuleCreate(BaseModel):
    name: str
    conditions: dict = Field(default_factory=dict)
    action: Literal["push", "email", "webhook"]
    action_target: str
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = None
    conditions: dict | None = None
    action: Literal["push", "email", "webhook"] | None = None
    action_target: str | None = None
    enabled: bool | None = None


@router.get("/rules", response_model=list[Rule])
async def list_rules(session: AsyncSession = Depends(get_session)) -> list[Rule]:
    return await rule_repo.list_rules(session)


@router.post("/rules", response_model=Rule, status_code=201)
async def create_rule(data: RuleCreate, session: AsyncSession = Depends(get_session)) -> Rule:
    rule = Rule(id=uuid4(), **data.model_dump())
    created = await rule_repo.create_rule(session, rule)
    try:
        await action_repo.log_action(session, "rule_created", str(created.id), {"name": created.name})
    except Exception:
        logger.exception("Échec de journalisation SYS-LOG pour la règle %s", created.id)
    return created


@router.put("/rules/{rule_id}", response_model=Rule)
async def update_rule(rule_id: UUID, data: RuleUpdate, session: AsyncSession = Depends(get_session)) -> Rule:
    updated = await rule_repo.update_rule(session, rule_id, **data.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Règle introuvable")
    try:
        await action_repo.log_action(session, "rule_updated", str(rule_id), data.model_dump(exclude_unset=True))
    except Exception:
        logger.exception("Échec de journalisation SYS-LOG pour la mise à jour de la règle %s", rule_id)
    return updated


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: UUID, session: AsyncSession = Depends(get_session)) -> Response:
    deleted = await rule_repo.delete_rule(session, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Règle introuvable")
    try:
        await action_repo.log_action(session, "rule_deleted", str(rule_id))
    except Exception:
        logger.exception("Échec de journalisation SYS-LOG pour la suppression de la règle %s", rule_id)
    return Response(status_code=204)


@router.get("/alerts", response_model=list[Alert])
async def list_alerts(
    acknowledged: bool | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[Alert]:
    return await rule_repo.list_alerts(session, acknowledged=acknowledged, limit=limit)


@router.post("/alerts/{alert_id}/acknowledge", response_model=Alert)
async def acknowledge_alert(alert_id: UUID, session: AsyncSession = Depends(get_session)) -> Alert:
    alert = await rule_repo.acknowledge_alert(session, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alerte introuvable")
    return alert
