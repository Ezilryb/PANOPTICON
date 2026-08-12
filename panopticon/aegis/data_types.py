"""
panopticon/aegis/data_types.py

Types de données partagés par AEGIS : le résultat d'une analyse de posture
pour UNE personne détectée par ARGUS, et l'évènement publié à chaque
CHANGEMENT d'état d'alerte (jamais un évènement par frame, cf. pipeline.py).

GARDE-FOU DE PÉRIMÈTRE (cf. section 2 du brief projet, note sur AEGIS) :
`PostureResult.posture` est volontairement restreint à un vocabulaire fermé
et purement géométrique (VALID_POSTURES ci-dessous). AEGIS ne doit JAMAIS
être étendu pour qualifier une émotion, une intention ou un niveau de
"dangerosité" à partir de l'apparence ou du comportement d'une personne —
c'est un point hors-scope explicite et durable du projet (fiabilité
scientifique + limites de l'AI Act art. 5), pas une simplification
provisoire. Toute évolution d'AEGIS doit rester dans ce périmètre : posture
au sens strictement mécanique du terme, à la seule fin de la détection de
chute/immobilité.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

BBox = tuple[float, float, float, float]

# Vocabulaire fermé et volontairement pauvre : AUCUNE valeur ici ne doit jamais décrire une
# émotion, une intention ou un niveau de dangerosité — cf. GARDE-FOU DE PÉRIMÈTRE ci-dessus.
# Vérifié par les deux backends (posture_analyzer.py) avant de construire un PostureResult.
VALID_POSTURES = frozenset({"upright", "lying", "uncertain"})

# "fall_confirmed" : une chute est confirmée pour cette piste (cf. fall_tracker.py).
# "fall_ended"     : l'alerte précédemment confirmée pour cette piste est close.
VALID_EVENT_TYPES = frozenset({"fall_confirmed", "fall_ended"})

# Raisons de clôture d'une alerte ("fall_ended" uniquement) :
# "posture_recovered" : la personne est retournée et reste "upright" assez longtemps.
# "track_lost"        : la piste ARGUS a disparu du flux pendant l'alerte (cf. LIMITE
#                        HONNÊTE dans fall_tracker.py — ambigu, à vérifier manuellement).
VALID_END_REASONS = frozenset({"posture_recovered", "track_lost"})


@dataclass
class PostureResult:
    """
    Résultat d'analyse de posture pour un recadrage de personne. `posture`
    vaut "upright" (debout/assis), "lying" (allongé/au sol) ou "uncertain"
    (zone de transition, ex: en train de s'accroupir) — cf. VALID_POSTURES.
    `orientation_deg` est un angle best-effort (0° = verticale, 90° =
    horizontale) : None si le backend n'a pas pu l'estimer (recadrage trop
    petit/peu exploitable pour le backend mock, points-clés insuffisants
    pour le backend yolo_pose). La classification `posture` elle-même ne
    dépend JAMAIS de cette seule estimation best-effort — cf. posture_analyzer.py.
    """
    posture: str                       # "upright" | "lying" | "uncertain" (cf. VALID_POSTURES)
    confidence: float
    aspect_ratio: float                 # largeur/hauteur de la bbox ARGUS d'origine (toujours calculable)
    orientation_deg: Optional[float] = None
    source: str = "mock"                # "mock" | "yolo_pose"

    def __post_init__(self) -> None:
        if self.posture not in VALID_POSTURES:
            raise ValueError(
                f"posture invalide : {self.posture!r} (attendu : {sorted(VALID_POSTURES)}) — "
                f"cf. garde-fou de périmètre dans data_types.py"
            )

    def to_dict(self) -> dict:
        return {
            "posture": self.posture,
            "confidence": round(self.confidence, 4),
            "aspect_ratio": round(self.aspect_ratio, 3),
            "orientation_deg": round(self.orientation_deg, 1) if self.orientation_deg is not None else None,
            "source": self.source,
        }

    @staticmethod
    def from_dict(payload: dict) -> "PostureResult":
        return PostureResult(
            posture=payload["posture"],
            confidence=payload["confidence"],
            aspect_ratio=payload["aspect_ratio"],
            orientation_deg=payload.get("orientation_deg"),
            source=payload.get("source", "mock"),
        )


@dataclass
class AegisEvent:
    """
    Évènement publié par AEGIS à chaque CHANGEMENT d'état d'alerte pour une
    piste — jamais un évènement par frame (même philosophie que PULSE_TRACK :
    rien n'est publié tant qu'aucune alerte ne change d'état, cf. pipeline.py).

    `event_type="fall_confirmed"` : chute confirmée (posture "lying" +
    immobilité soutenues, éventuellement précédées d'une chute verticale
    rapide détectée — cf. fall_tracker.py). `end_reason`/`duration_s` ne
    sont renseignés que pour `event_type="fall_ended"`.
    """
    event_type: str                     # "fall_confirmed" | "fall_ended" (cf. VALID_EVENT_TYPES)
    camera_id: str
    track_id: int
    frame_id: int
    ts_triggered: float
    fall_started_at: float
    posture: PostureResult
    fast_fall_observed: bool = False    # True si une chute verticale rapide a précédé la confirmation
    end_reason: Optional[str] = None    # "posture_recovered" | "track_lost" (cf. VALID_END_REASONS)
    duration_s: Optional[float] = None  # "fall_ended" uniquement : durée totale de l'alerte
    ts_published: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"event_type invalide : {self.event_type!r} (attendu : {sorted(VALID_EVENT_TYPES)})")
        if self.end_reason is not None and self.end_reason not in VALID_END_REASONS:
            raise ValueError(f"end_reason invalide : {self.end_reason!r} (attendu : {sorted(VALID_END_REASONS)})")

    @property
    def latency_ms(self) -> float:
        """Latence entre l'instant du déclenchement/de la clôture et la publication de l'évènement."""
        return (self.ts_published - self.ts_triggered) * 1000.0

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "frame_id": self.frame_id,
            "ts_triggered": self.ts_triggered,
            "fall_started_at": self.fall_started_at,
            "ts_published": self.ts_published,
            "latency_ms": round(self.latency_ms, 2),
            "posture": self.posture.to_dict(),
            "fast_fall_observed": self.fast_fall_observed,
            "end_reason": self.end_reason,
            "duration_s": round(self.duration_s, 2) if self.duration_s is not None else None,
        }

    @staticmethod
    def from_dict(payload: dict) -> "AegisEvent":
        return AegisEvent(
            event_type=payload["event_type"],
            camera_id=payload["camera_id"],
            track_id=payload["track_id"],
            frame_id=payload["frame_id"],
            ts_triggered=payload["ts_triggered"],
            fall_started_at=payload["fall_started_at"],
            ts_published=payload["ts_published"],
            posture=PostureResult.from_dict(payload["posture"]),
            fast_fall_observed=payload.get("fast_fall_observed", False),
            end_reason=payload.get("end_reason"),
            duration_s=payload.get("duration_s"),
        )
