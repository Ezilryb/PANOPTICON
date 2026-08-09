"""
panopticon/spectra/data_types.py

Types de données partagés par SPECTRA : le résultat d'une amélioration
d'image (métriques avant/après, techniques appliquées), l'état grossier
d'une zone-écran surveillée, et l'évènement complet publié vers les futurs
modules consommateurs (ORACLE, PULSE_TRACK, NEXUS-V...).
"""

import time
from dataclasses import dataclass, field
from typing import Optional


def spectra_camera_id(camera_id: str) -> str:
    """
    Construit l'identifiant utilisé pour écrire/lire la frame AMÉLIORÉE via
    SharedFrameStore/FrameReader (mécanisme fichier d'ARGUS, réutilisé tel
    quel plutôt que dupliqué — cf. pipeline.py). Préfixe distinct de celui
    d'ARGUS pour ne jamais écraser le fichier de la frame brute
    correspondante : les deux coexistent sur disque, lisibles indépendamment
    l'un de l'autre. Utilisé à l'écriture (pipeline.py) ET à la lecture
    (client.py) : NE JAMAIS dupliquer cette logique ailleurs, pour être sûr
    que les deux cotés s'accordent toujours sur le même nom de fichier.
    """
    return f"SPECTRA-{camera_id}"


@dataclass
class EnhancementResult:
    """Sortie d'un backend d'amélioration : métriques avant/après + techniques effectivement appliquées."""

    brightness_before: float
    brightness_after: float
    contrast_before: float
    contrast_after: float
    low_light_correction_applied: bool
    denoise_applied: bool
    contrast_enhancement_applied: bool
    white_balance_applied: bool
    gamma_used: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "brightness_before": round(self.brightness_before, 2),
            "brightness_after": round(self.brightness_after, 2),
            "contrast_before": round(self.contrast_before, 2),
            "contrast_after": round(self.contrast_after, 2),
            "low_light_correction_applied": self.low_light_correction_applied,
            "denoise_applied": self.denoise_applied,
            "contrast_enhancement_applied": self.contrast_enhancement_applied,
            "white_balance_applied": self.white_balance_applied,
            "gamma_used": round(self.gamma_used, 3) if self.gamma_used is not None else None,
        }

    @staticmethod
    def from_dict(payload: dict) -> "EnhancementResult":
        return EnhancementResult(
            brightness_before=payload["brightness_before"],
            brightness_after=payload["brightness_after"],
            contrast_before=payload["contrast_before"],
            contrast_after=payload["contrast_after"],
            low_light_correction_applied=payload["low_light_correction_applied"],
            denoise_applied=payload["denoise_applied"],
            contrast_enhancement_applied=payload["contrast_enhancement_applied"],
            white_balance_applied=payload["white_balance_applied"],
            gamma_used=payload.get("gamma_used"),
        )


@dataclass
class ScreenRegionState:
    """
    État GROSSIER d'une zone-écran surveillée (cf. screen_state.py) : jamais
    de contenu affiché, uniquement luminosité moyenne et un score de
    mouvement par différence de frames.
    """

    region_name: str
    brightness: float
    is_on: bool
    is_static: bool
    motion_score: float

    def to_dict(self) -> dict:
        return {
            "region_name": self.region_name,
            "brightness": self.brightness,
            "is_on": self.is_on,
            "is_static": self.is_static,
            "motion_score": self.motion_score,
        }

    @staticmethod
    def from_dict(payload: dict) -> "ScreenRegionState":
        return ScreenRegionState(
            region_name=payload["region_name"],
            brightness=payload["brightness"],
            is_on=payload["is_on"],
            is_static=payload["is_static"],
            motion_score=payload["motion_score"],
        )


@dataclass
class SpectraEvent:
    """
    Évènement complet publié par SPECTRA pour une frame donnée : identifie la
    caméra/frame d'origine (mêmes identifiants qu'ARGUS, pour recoupement
    facile côté consommateur), porte le résultat de l'amélioration et l'état
    des éventuelles zones-écran surveillées sur cette caméra.
    """

    camera_id: str
    frame_id: int
    ts_capture: float
    ts_enhanced: float
    width: int
    height: int
    result: EnhancementResult
    screen_regions: list[ScreenRegionState] = field(default_factory=list)
    ts_published: float = field(default_factory=time.time)

    @property
    def latency_ms(self) -> float:
        """Latence bout-en-bout : capture caméra -> évènement SPECTRA prêt à publier."""
        return (self.ts_published - self.ts_capture) * 1000.0

    @property
    def enhanced_camera_id(self) -> str:
        """Identifiant à utiliser pour relire la frame améliorée (cf. spectra_camera_id())."""
        return spectra_camera_id(self.camera_id)

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "frame_id": self.frame_id,
            "ts_capture": self.ts_capture,
            "ts_enhanced": self.ts_enhanced,
            "ts_published": self.ts_published,
            "width": self.width,
            "height": self.height,
            "latency_ms": round(self.latency_ms, 2),
            "result": self.result.to_dict(),
            "screen_regions": [r.to_dict() for r in self.screen_regions],
        }

    @staticmethod
    def from_dict(payload: dict) -> "SpectraEvent":
        return SpectraEvent(
            camera_id=payload["camera_id"],
            frame_id=payload["frame_id"],
            ts_capture=payload["ts_capture"],
            ts_enhanced=payload["ts_enhanced"],
            ts_published=payload["ts_published"],
            width=payload["width"],
            height=payload["height"],
            result=EnhancementResult.from_dict(payload["result"]),
            screen_regions=[ScreenRegionState.from_dict(r) for r in payload.get("screen_regions", [])],
        )