"""Modèles Pydantic partagés."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ModuleStatus(BaseModel):
    name: str
    status: Literal["running", "stopped", "crashed", "starting"]
    cpu_percent: float | None = None
    ram_mb: float | None = None
    started_at: datetime | None = None
    message: str | None = None


class ResourceSnapshot(BaseModel):
    cpu_percent: float
    ram_total_mb: float
    ram_available_mb: float
    ram_used_mb: float
    gpu_available: bool
    gpu_name: str | None = None
    gpu_memory_total_mb: float | None = None
    gpu_memory_free_mb: float | None = None


class Camera(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    connection_url: str
    zone: str = "default"
    target_fps: int = 3
    status: Literal["online", "offline", "reconnecting"] = "offline"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CameraCreate(BaseModel):
    name: str
    connection_url: str
    zone: str = "default"
    target_fps: int = 3


class CameraUpdate(BaseModel):
    name: str | None = None
    connection_url: str | None = None
    zone: str | None = None
    target_fps: int | None = None


class DetectionEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    camera_id: UUID
    source_module: str
    event_type: Literal[
        "object_appeared",
        "object_disappeared",
        "object_moved",
        "person_entered_zone",
        "person_left_zone",
        "fall_detected",
        "screen_state_changed",
    ]
    zone: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    thumbnail_path: str | None = None
    metadata: dict = Field(default_factory=dict)


class EnrolledPerson(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    consent_confirmed_at: datetime
    reference_photo_paths: list[str]
    face_embedding: list[float] = Field(default_factory=list)


class OperatorAction(BaseModel):
    """SYS-LOG — une action déclenchée par l'opérateur (via CLI ou API)."""

    id: UUID = Field(default_factory=uuid4)
    action: str
    target: str
    detail: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EventSummary(BaseModel):
    """SYS-LOG — résumé des événements sur une fenêtre glissante."""

    period_hours: int
    total_events: int
    by_type: dict[str, int] = Field(default_factory=dict)
    by_zone: dict[str, int] = Field(default_factory=dict)
    by_module: dict[str, int] = Field(default_factory=dict)


class Rule(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    conditions: dict
    action: Literal["push", "email", "webhook"]
    action_target: str
    enabled: bool = True


class Alert(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    rule_id: UUID
    triggered_at: datetime = Field(default_factory=datetime.utcnow)
    payload: dict = Field(default_factory=dict)
    acknowledged: bool = False
