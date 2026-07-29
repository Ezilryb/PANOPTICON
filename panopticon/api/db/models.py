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


class EnrolledPersonRow(Base):
    """ROSTER — personnes enrôlées (opt-in, consentement horodaté requis)."""

    __tablename__ = "enrolled_persons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    consent_confirmed_at: Mapped[datetime] = mapped_column(DateTime)
    reference_photo_paths_json: Mapped[list] = mapped_column(JSON, default=list)
    face_embedding_json: Mapped[list] = mapped_column(JSON, default=list)


class RuleRow(Base):
    """PULSE_TRACK — règles déclenchant des notifications."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    conditions_json: Mapped[dict] = mapped_column(JSON, default=dict)
    action: Mapped[str] = mapped_column(String(32))
    action_target: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AlertRow(Base):
    """PULSE_TRACK — alertes déclenchées par une règle."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    rule_id: Mapped[str] = mapped_column(String(36), index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
