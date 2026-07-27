"""Routes stub ORACLE — Phase 3."""

from uuid import UUID

from fastapi import APIRouter

router = APIRouter(prefix="/api/objects", tags=["objects"])


@router.get("")
async def list_objects() -> list:
    return []


@router.get("/{object_id}")
async def get_object(object_id: UUID) -> dict:
    return {"id": str(object_id), "status": "not_implemented", "phase": 3}
