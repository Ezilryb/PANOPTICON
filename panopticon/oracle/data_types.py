"""
panopticon/oracle/data_types.py

Types de données partagés par ORACLE : le résultat d'une identification
fine (marque/modèle + confiance), un objet identifié dans une frame
(détection ARGUS d'origine + résultat), et l'évènement complet publié vers
les futurs modules consommateurs (PULSE_TRACK, SYS-LOG, NEXUS-V).
"""

import time
from dataclasses import dataclass, field
from typing import Optional

# Bounding box en pixels : (x1, y1, x2, y2) — coin haut-gauche puis bas-droit.
BBox = tuple[float, float, float, float]


@dataclass
class ObjectIdentification:
    """
    Résultat d'identification fine pour UN crop d'objet. `label` est le
    meilleur candidat retenu (ex: "Toyota Camry"), `candidates` porte les
    autres pistes renvoyées par le backend, à titre indicatif seulement —
    NEXUS-V n'affichera que `label` par défaut, `candidates` sert surtout à
    l'interface de correction manuelle prévue par le brief (section 5).
    """

    label: str
    confidence: float
    source: str                                     # "mock", "google_vision"...
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "candidates": list(self.candidates),
        }

    @staticmethod
    def from_dict(payload: dict) -> "ObjectIdentification":
        return ObjectIdentification(
            label=payload["label"],
            confidence=payload["confidence"],
            source=payload["source"],
            candidates=payload.get("candidates", []),
        )


@dataclass
class IdentifiedObject:
    """
    Un objet identifiable détecté par ARGUS dans une frame donnée, avec le
    résultat d'ORACLE s'il a pu en produire un. `identification` est None
    quand ARGUS a détecté un objet éligible mais qu'aucune identification
    exploitable n'a pu être obtenue (confiance sous le seuil, échec API...) —
    l'objet reste dans l'évènement (utile pour NEXUS-V : "objet vu, pas
    identifié"), plutôt que d'être silencieusement supprimé.
    """

    bbox: BBox
    class_name: str                                  # classe ARGUS d'origine (ex: "car", "laptop")
    source_track_id: Optional[int]                   # track_id ARGUS de la détection d'origine, si disponible
    identification: Optional[ObjectIdentification]
    from_cache: bool = False

    def to_dict(self) -> dict:
        return {
            "bbox": [round(v, 1) for v in self.bbox],
            "class_name": self.class_name,
            "source_track_id": self.source_track_id,
            "identification": self.identification.to_dict() if self.identification is not None else None,
            "from_cache": self.from_cache,
        }

    @staticmethod
    def from_dict(payload: dict) -> "IdentifiedObject":
        identification = payload.get("identification")
        return IdentifiedObject(
            bbox=tuple(payload["bbox"]),
            class_name=payload["class_name"],
            source_track_id=payload.get("source_track_id"),
            identification=ObjectIdentification.from_dict(identification) if identification is not None else None,
            from_cache=payload.get("from_cache", False),
        )


@dataclass
class OracleEvent:
    """
    Évènement complet publié par ORACLE pour une frame donnée : identifie la
    caméra/frame d'origine (mêmes identifiants qu'ARGUS, pour recoupement
    facile côté consommateur) et porte la liste des objets identifiables
    traités sur cette frame, avec leur résultat d'identification éventuel.
    """

    camera_id: str
    frame_id: int
    ts_capture: float
    ts_identified: float
    objects: list[IdentifiedObject] = field(default_factory=list)
    ts_published: float = field(default_factory=time.time)

    @property
    def latency_ms(self) -> float:
        """Latence bout-en-bout : capture caméra -> évènement ORACLE prêt à publier."""
        return (self.ts_published - self.ts_capture) * 1000.0

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "frame_id": self.frame_id,
            "ts_capture": self.ts_capture,
            "ts_identified": self.ts_identified,
            "ts_published": self.ts_published,
            "latency_ms": round(self.latency_ms, 2),
            "objects": [o.to_dict() for o in self.objects],
        }

    @staticmethod
    def from_dict(payload: dict) -> "OracleEvent":
        return OracleEvent(
            camera_id=payload["camera_id"],
            frame_id=payload["frame_id"],
            ts_capture=payload["ts_capture"],
            ts_identified=payload["ts_identified"],
            ts_published=payload["ts_published"],
            objects=[IdentifiedObject.from_dict(o) for o in payload.get("objects", [])],
        )
