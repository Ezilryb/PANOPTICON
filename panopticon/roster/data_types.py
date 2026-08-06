"""
panopticon/roster/data_types.py

Types de données partagés par ROSTER : une personne enrôlée (avec son
consentement horodaté et ses embeddings de référence), une observation de
visage détectée dans une frame, le résultat d'un matching, et l'évènement
complet publié vers les futurs modules consommateurs (PULSE_TRACK, SYS-LOG,
NEXUS-V). Aucun type ici ne persiste de donnée sur une personne "inconnue"
au-delà du strict nécessaire au traitement temps réel (cf. critère
d'acceptation section 10 du brief projet).
"""

import time
from dataclasses import dataclass, field
from typing import Optional

# Bounding box en pixels : (x1, y1, x2, y2) — coin haut-gauche puis bas-droit.
BBox = tuple[float, float, float, float]

# Embedding facial : vecteur de flottants (dimension dépend du backend — 128 pour
# face_recognition/dlib). Type générique ici pour rester agnostique du backend.
Embedding = list[float]


@dataclass
class EnrolledPerson:
    """
    Une personne explicitement enrôlée dans ROSTER. `consent_confirmed_at` est
    horodaté au moment de l'enrôlement et ne doit JAMAIS être falsifié après
    coup : c'est la preuve que la personne (ou son représentant légal) a
    donné son accord explicite pour être reconnue par le système.
    """

    person_id: str
    name: str
    consent_confirmed_at: float             # epoch seconds, horodatage du consentement
    embeddings: list[Embedding] = field(default_factory=list)   # un embedding par photo de référence
    reference_photo_paths: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    notes: str = ""                          # champ libre opérateur (ex: "famille", "voisin autorisé")

    def to_dict(self) -> dict:
        return {
            "person_id": self.person_id,
            "name": self.name,
            "consent_confirmed_at": self.consent_confirmed_at,
            "embeddings": self.embeddings,
            "reference_photo_paths": self.reference_photo_paths,
            "created_at": self.created_at,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(payload: dict) -> "EnrolledPerson":
        return EnrolledPerson(
            person_id=payload["person_id"],
            name=payload["name"],
            consent_confirmed_at=payload["consent_confirmed_at"],
            embeddings=payload.get("embeddings", []),
            reference_photo_paths=payload.get("reference_photo_paths", []),
            created_at=payload.get("created_at", payload["consent_confirmed_at"]),
            notes=payload.get("notes", ""),
        )


@dataclass
class FaceObservation:
    """Un visage détecté dans une frame, avant matching. Objet transitoire : jamais persisté tel quel."""

    camera_id: str
    frame_id: int
    ts_capture: float
    bbox: BBox
    embedding: Embedding
    source_track_id: Optional[int] = None    # track_id ARGUS de la détection "person" d'origine, si disponible


@dataclass
class FaceMatch:
    """Résultat du matching d'un FaceObservation contre la base des personnes enrôlées."""

    matched: bool
    person_id: Optional[str] = None
    name: Optional[str] = None
    distance: Optional[float] = None
    bbox: Optional[BBox] = None    # position du visage dans la frame pleine résolution (overlay NEXUS-V)

    @property
    def label(self) -> str:
        """Étiquette au format `known:{nom}` / `unknown`, cf. section 5 du brief projet."""
        return f"known:{self.name}" if self.matched and self.name else "unknown"


@dataclass
class RosterEvent:
    """
    Évènement complet publié par ROSTER pour une frame donnée : identifie la
    caméra/frame, porte la liste des visages observés avec leur résultat de
    matching. Ne porte JAMAIS l'embedding brut d'une personne non enrôlée au
    delà de la durée de vie de cet évènement (pas de champ de stockage long
    terme ici — c'est le rôle de VAULT, pas de ROSTER).
    """

    camera_id: str
    frame_id: int
    ts_capture: float
    ts_matched: float
    matches: list[FaceMatch] = field(default_factory=list)
    ts_published: float = field(default_factory=time.time)

    @property
    def latency_ms(self) -> float:
        return (self.ts_published - self.ts_capture) * 1000.0

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "frame_id": self.frame_id,
            "ts_capture": self.ts_capture,
            "ts_matched": self.ts_matched,
            "ts_published": self.ts_published,
            "latency_ms": round(self.latency_ms, 2),
            "matches": [
                {
                    "matched": m.matched,
                    "person_id": m.person_id,
                    "name": m.name,
                    "distance": round(m.distance, 4) if m.distance is not None else None,
                    "label": m.label,
                    "bbox": [round(v, 1) for v in m.bbox] if m.bbox is not None else None,
                }
                for m in self.matches
            ],
        }

    @staticmethod
    def from_dict(payload: dict) -> "RosterEvent":
        matches = [
            FaceMatch(
                matched=m["matched"],
                person_id=m.get("person_id"),
                name=m.get("name"),
                distance=m.get("distance"),
                bbox=tuple(m["bbox"]) if m.get("bbox") is not None else None,
            )
            for m in payload.get("matches", [])
        ]
        return RosterEvent(
            camera_id=payload["camera_id"],
            frame_id=payload["frame_id"],
            ts_capture=payload["ts_capture"],
            ts_matched=payload["ts_matched"],
            ts_published=payload["ts_published"],
            matches=matches,
        )
