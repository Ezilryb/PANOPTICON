"""Routes caméras ARGUS."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.repositories import cameras as camera_repo
from shared.models import Camera, CameraCreate, CameraUpdate

router = APIRouter(prefix="/api/cameras", tags=["cameras"])

FRAMES_DIR = Path("./data/argus/frames")


@router.get("", response_model=list[Camera])
async def list_cameras(session: AsyncSession = Depends(get_session)) -> list[Camera]:
    return await camera_repo.list_cameras(session)


@router.post("", response_model=Camera, status_code=201)
async def create_camera(
    data: CameraCreate, session: AsyncSession = Depends(get_session)
) -> Camera:
    return await camera_repo.create_camera(session, data)


@router.get("/{camera_id}", response_model=Camera)
async def get_camera(
    camera_id: UUID, session: AsyncSession = Depends(get_session)
) -> Camera:
    camera = await camera_repo.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Caméra introuvable")
    return camera


@router.put("/{camera_id}", response_model=Camera)
async def update_camera(
    camera_id: UUID,
    data: CameraUpdate,
    session: AsyncSession = Depends(get_session),
) -> Camera:
    camera = await camera_repo.update_camera(session, camera_id, data)
    if not camera:
        raise HTTPException(status_code=404, detail="Caméra introuvable")
    return camera


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(
    camera_id: UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    deleted = await camera_repo.delete_camera(session, camera_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Caméra introuvable")
    return Response(status_code=204)


@router.get("/{camera_id}/stream")
async def get_camera_stream(camera_id: UUID) -> FileResponse:
    frame_path = FRAMES_DIR / f"{camera_id}.jpg"
    if not frame_path.exists():
        raise HTTPException(status_code=404, detail="Flux indisponible")
    return FileResponse(frame_path, media_type="image/jpeg")


@router.get("/{camera_id}/health")
async def camera_health(
    camera_id: UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    camera = await camera_repo.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Caméra introuvable")
    frame_path = FRAMES_DIR / f"{camera_id}.jpg"
    return {
        "camera_id": str(camera_id),
        "status": camera.status,
        "frame_available": frame_path.exists(),
        "zone": camera.zone,
        "target_fps": camera.target_fps,
    }
