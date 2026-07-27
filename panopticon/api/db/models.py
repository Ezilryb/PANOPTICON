"""Modèles SQLAlchemy."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.database import Base


class CameraRow(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    connection_url: Mapped[str] = mapped_column(Text)
    zone: Mapped[str] = mapped_column(String(128), default="default")
    target_fps: Mapped[int] = mapped_column(default=3)
    status: Mapped[str] = mapped_column(String(32), default="offline")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    camera_id: Mapped[str] = mapped_column(String(36), index=True)
    source_module: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    zone: Mapped[str] = mapped_column(String(128))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class OperatorActionRow(Base):
    """SYS-LOG — journal des actions opérateur (démarrage/arrêt de module, caméras…)."""

    __tablename__ = "operator_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255))
    detail_json: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
