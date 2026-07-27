"""Routes stub PULSE_TRACK — Phase 4."""

from uuid import UUID

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api", tags=["rules"])


@router.get("/rules")
async def list_rules() -> list:
    return []


@router.post("/rules", status_code=501)
async def create_rule() -> None:
    raise HTTPException(status_code=501, detail="PULSE_TRACK — Phase 4")


@router.put("/rules/{rule_id}", status_code=501)
async def update_rule(rule_id: UUID) -> None:
    raise HTTPException(status_code=501, detail="PULSE_TRACK — Phase 4")


@router.delete("/rules/{rule_id}", status_code=501)
async def delete_rule(rule_id: UUID) -> None:
    raise HTTPException(status_code=501, detail="PULSE_TRACK — Phase 4")


@router.get("/alerts")
async def list_alerts() -> list:
    return []


@router.post("/alerts/{alert_id}/acknowledge", status_code=501)
async def acknowledge_alert(alert_id: UUID) -> None:
    raise HTTPException(status_code=501, detail="PULSE_TRACK — Phase 4")
