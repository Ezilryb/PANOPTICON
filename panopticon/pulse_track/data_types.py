"""
panopticon/pulse_track/data_types.py

PulseTrackEvent : l'évènement publié à chaque déclenchement d'une règle
(règle, sévérité, message, caméra/frame, piste/personne à l'origine).
Contrairement aux autres bus, rien n'est publié tant qu'aucune règle ne se
déclenche.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PulseTrackEvent:
    """
    Un déclenchement de règle : identifie la règle responsable, la caméra et
    la frame ARGUS/ROSTER à l'origine (permet à un futur consommateur de
    relire l'image via argus.frame_store.FrameReader(camera_id), cf.
    client.py — PULSE_TRACK ne réécrit lui-même aucune frame sur disque),
    et porte un message prêt à afficher (déjà formaté depuis
    RuleConfig.message_template).
    """

    rule_id: str
    rule_name: str
    severity: str
    message: str
    camera_id: str
    frame_id: int
    ts_triggered: float
    track_id: Optional[int] = None          # piste ARGUS à l'origine, si le déclencheur en portait une
    person_name: Optional[str] = None       # renseigné pour un déclencheur "known_person"
    object_class: Optional[str] = None      # renseigné pour "object_class"/"track_dwell"
    ts_published: float = field(default_factory=time.time)

    @property
    def latency_ms(self) -> float:
        """Latence entre l'instant du déclenchement et la publication de l'évènement."""
        return (self.ts_published - self.ts_triggered) * 1000.0

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "camera_id": self.camera_id,
            "frame_id": self.frame_id,
            "ts_triggered": self.ts_triggered,
            "ts_published": self.ts_published,
            "latency_ms": round(self.latency_ms, 2),
            "track_id": self.track_id,
            "person_name": self.person_name,
            "object_class": self.object_class,
        }

    @staticmethod
    def from_dict(payload: dict) -> "PulseTrackEvent":
        return PulseTrackEvent(
            rule_id=payload["rule_id"],
            rule_name=payload["rule_name"],
            severity=payload["severity"],
            message=payload["message"],
            camera_id=payload["camera_id"],
            frame_id=payload["frame_id"],
            ts_triggered=payload["ts_triggered"],
            ts_published=payload["ts_published"],
            track_id=payload.get("track_id"),
            person_name=payload.get("person_name"),
            object_class=payload.get("object_class"),
        )