"""Repository caméras."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CameraRow
from shared.models import Camera, CameraCreate, CameraUpdate


def _row_to_model(row: CameraRow) -> Camera:
    return Camera(
        id=UUID(row.id),
        name=row.name,
        connection_url=row.connection_url,
        zone=row.zone,
        target_fps=row.target_fps,
        status=row.status,  # type: ignore[arg-type]
        created_at=row.created_at,
    )


async def list_cameras(session: AsyncSession) -> list[Camera]:
    result = await session.execute(select(CameraRow).order_by(CameraRow.created_at))
    return [_row_to_model(r) for r in result.scalars()]


async def get_camera(session: AsyncSession, camera_id: UUID) -> Camera | None:
    row = await session.get(CameraRow, str(camera_id))
    return _row_to_model(row) if row else None


async def create_camera(session: AsyncSession, data: CameraCreate) -> Camera:
    row = CameraRow(
        name=data.name,
        connection_url=data.connection_url,
        zone=data.zone,
        target_fps=data.target_fps,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_to_model(row)


async def update_camera(session: AsyncSession, camera_id: UUID, data: CameraUpdate) -> Camera | None:
    row = await session.get(CameraRow, str(camera_id))
    if not row:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return _row_to_model(row)


async def delete_camera(session: AsyncSession, camera_id: UUID) -> bool:
    row = await session.get(CameraRow, str(camera_id))
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def set_camera_status(session: AsyncSession, camera_id: UUID, status: str) -> None:
    row = await session.get(CameraRow, str(camera_id))
    if row:
        row.status = status
        await session.commit()
