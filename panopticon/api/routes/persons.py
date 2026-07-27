"""Routes stub Phase 4+."""

from uuid import UUID

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("")
async def list_persons() -> list:
    return []


@router.post("", status_code=501)
async def enroll_person() -> None:
    raise HTTPException(status_code=501, detail="ROSTER — Phase 4")


@router.delete("/{person_id}", status_code=501)
async def delete_person(person_id: UUID) -> None:
    raise HTTPException(status_code=501, detail="ROSTER — Phase 4")
