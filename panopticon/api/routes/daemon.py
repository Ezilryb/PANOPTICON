"""Routes DAEMON."""

from fastapi import APIRouter, HTTPException

from daemon.orchestrator import orchestrator
from shared.models import ModuleStatus, ResourceSnapshot

router = APIRouter(prefix="/api/daemon", tags=["daemon"])


@router.get("/modules", response_model=list[ModuleStatus])
async def list_modules() -> list[ModuleStatus]:
    return orchestrator.list_modules()


@router.post("/modules/{name}/start", response_model=ModuleStatus)
async def start_module(name: str) -> ModuleStatus:
    try:
        return await orchestrator.start_module(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Module inconnu: {name}")


@router.post("/modules/{name}/stop", response_model=ModuleStatus)
async def stop_module(name: str) -> ModuleStatus:
    try:
        return await orchestrator.stop_module(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Module inconnu: {name}")


@router.get("/resources", response_model=ResourceSnapshot)
async def get_resources() -> ResourceSnapshot:
    return orchestrator.get_resources()
