"""
panopticon/argus/types.py

Types de données partagés par tout ARGUS : une frame capturée, une détection
individuelle, et l'évènement complet (frame + détections) publié vers les
futurs modules consommateurs (SPECTRA, ORACLE, ROSTER, PULSE_TRACK, AEGIS).
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Bounding box en pixels : (x1, y1, x2, y2) — coin haut-gauche puis bas-droit.
BBox = tuple[float, float, float, float]


@dataclass
class Detection:
    """Une détection unique retournée par le Detector, avant ou après tracking."""

    class_id: int
    class_name: str
    confidence: float
    bbox: BBox
    track_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": [round(v, 1) for v in self.bbox],
            "track_id": self.track_id,
        }


@dataclass
class Frame:
    """
    Frame brute issue d'une caméra, avec ses métadonnées. `image` ne quitte
    jamais le process ARGUS tel quel : elle est encodée (JPEG) par le
    SharedFrameStore avant toute publication inter-process.
    """

    camera_id: str
    frame_id: int
    ts_capture: float
    image: np.ndarray

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


@dataclass
class DetectionEvent:
    """
    Évènement complet publié par ARGUS pour une frame donnée : identifie la
    caméra/frame, horodate chaque étape du pipeline (capture -> détection ->
    publication) et porte la liste des détections trackées.
    """

    camera_id: str
    frame_id: int
    ts_capture: float
    ts_detected: float
    width: int
    height: int
    detections: list[Detection] = field(default_factory=list)
    ts_published: float = field(default_factory=time.time)

    @property
    def latency_ms(self) -> float:
        """Latence bout-en-bout : capture caméra -> évènement prêt à publier."""
        return (self.ts_published - self.ts_capture) * 1000.0

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "frame_id": self.frame_id,
            "ts_capture": self.ts_capture,
            "ts_detected": self.ts_detected,
            "ts_published": self.ts_published,
            "width": self.width,
            "height": self.height,
            "latency_ms": round(self.latency_ms, 2),
            "detections": [d.to_dict() for d in self.detections],
        }

    @staticmethod
    def from_dict(payload: dict) -> "DetectionEvent":
        detections = [
            Detection(
                class_id=d["class_id"],
                class_name=d["class_name"],
                confidence=d["confidence"],
                bbox=tuple(d["bbox"]),
                track_id=d.get("track_id"),
            )
            for d in payload.get("detections", [])
        ]
        return DetectionEvent(
            camera_id=payload["camera_id"],
            frame_id=payload["frame_id"],
            ts_capture=payload["ts_capture"],
            ts_detected=payload["ts_detected"],
            ts_published=payload["ts_published"],
            width=payload["width"],
            height=payload["height"],
            detections=detections,
        )
