"""
panopticon/aegis/config.py

Configuration d'AEGIS : backend d'analyse de posture, paramètres de
confirmation de chute (durées, seuils de mouvement), connexion au bus ARGUS
dont AEGIS consomme les évènements, et paramètres de son propre bus de
publication. Chargée depuis un fichier JSON ; une configuration par défaut
(backend "mock", zéro dépendance lourde) permet de démarrer et tester AEGIS
sans installer ultralytics.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aegis.config")


@dataclass
class AnalyzerConfig:
    backend: str = "mock"                       # "mock" (aspect ratio + OpenCV/NumPy, zéro dépendance) ou "yolo_pose" (ultralytics)
    pose_weights: str = "yolo11n-pose.pt"        # backend "yolo_pose" uniquement — cf. écart assumé vs. brief (MediaPipe), documenté dans posture_analyzer.py
    device: str = "auto"                         # "auto" | "cpu" | "cuda"
    keypoint_confidence_threshold: float = 0.5   # confiance mini par point-clé pour l'utiliser dans le calcul d'angle (yolo_pose)

    # Seuils partagés par les deux backends (bbox aspect ratio — calcul principal du backend
    # "mock", repli du backend "yolo_pose" si trop peu de points-clés exploitables) :
    lying_aspect_ratio_threshold: float = 1.3     # largeur/hauteur bbox >= ce seuil -> "lying"
    upright_aspect_ratio_threshold: float = 0.8   # largeur/hauteur bbox <= ce seuil -> "upright"
    # (entre les deux seuils -> "uncertain", cf. posture_analyzer.py)

    # Seuils spécifiques au backend "yolo_pose" (angle tronc/verticale, 0°=vertical, 90°=horizontal) :
    lying_angle_threshold_deg: float = 55.0
    upright_angle_threshold_deg: float = 30.0


@dataclass
class FallDetectionConfig:
    """
    Cf. section 5 du brief projet : "chute verticale rapide + posture
    horizontale prolongée + absence de mouvement pendant N secondes", avec
    un délai de confirmation pour limiter les faux positifs. Les seuils de
    déplacement (`fall_min_vertical_px`/`max_movement_px`) sont exprimés en
    pixels IMAGE bruts, PAS normalisés par la distance caméra/personne —
    limite documentée dans fall_tracker.py.
    """
    confirm_seconds: float = 5.0             # durée mini de posture "lying" + immobilité SANS chute rapide observée récemment
    fast_confirm_seconds: float = 2.0        # durée mini SI une chute verticale rapide a été observée (cf. fall_trigger_grace_s)
    recovery_confirm_seconds: float = 2.0    # durée mini de retour à "upright" avant de clore une alerte confirmée
    track_lost_after_s: float = 12.0         # au-delà, une piste absente du flux ARGUS est jugée perdue (cf. LIMITE HONNÊTE)
    min_detection_confidence: float = 0.4    # confiance ARGUS minimale sur la détection "person" pour être analysée

    # Deux fenêtres DISTINCTES sur le même historique de centroïde (cf. fall_tracker.py) : la
    # chute elle-même implique un grand déplacement sur un temps COURT (fall_detection_window_s),
    # tandis que l'immobilité qui la confirme se mesure sur une fenêtre GLISSANTE des
    # confirm_seconds/fast_confirm_seconds dernières secondes (jamais depuis le tout début de
    # "lying", qui peut lui-même contenir la fin du mouvement de la chute) — motion_window_s doit
    # donc rester supérieure ou égale à la plus longue des deux durées de confirmation pour que
    # l'historique retienne assez d'échantillons (vérifié avec un avertissement au chargement,
    # cf. load_config()).
    motion_window_s: float = 6.0             # durée de rétention de l'historique de centroïde
    fall_detection_window_s: float = 1.5     # fenêtre (courte) d'évaluation du déplacement vertical rapide
    fall_min_vertical_px: float = 60.0       # déplacement vertical mini (px) dans fall_detection_window_s pour qualifier une "chute rapide"
    fall_trigger_grace_s: float = 3.0        # durée pendant laquelle une "chute rapide" détectée raccourcit la confirmation à venir
    max_movement_px: float = 40.0            # déplacement max toléré (depuis le début de "lying") pour juger la personne immobile

    monitored_camera_ids: list[str] = field(default_factory=list)   # vide = toutes les caméras ; sinon liste blanche
    person_classes: list[str] = field(default_factory=lambda: ["person"])


@dataclass
class ArgusConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 8765                    # port du bus ArgusPublisher auquel AEGIS se connecte en client


@dataclass
class PublisherConfig:
    host: str = "127.0.0.1"
    port: int = 8770                    # port du bus de publication propre à AEGIS (AegisEvent) —
                                         # distinct d'ARGUS (8765), ROSTER (8766), SPECTRA (8767),
                                         # ORACLE (8768) et PULSE_TRACK (8769)


@dataclass
class AegisConfig:
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    fall_detection: FallDetectionConfig = field(default_factory=FallDetectionConfig)
    argus: ArgusConnectionConfig = field(default_factory=ArgusConnectionConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    log_stats_every_s: float = 10.0


def default_config() -> AegisConfig:
    """Configuration prête à l'emploi : backend mock, connexion ARGUS locale par défaut, toutes caméras surveillées."""
    return AegisConfig()


def _warn_if_motion_window_too_short(fall_detection: FallDetectionConfig) -> None:
    """
    Avertit (sans corriger ni échouer — même philosophie que
    argus/config.py::_parse_tracking_mode) si `motion_window_s` est trop
    court pour couvrir la plus longue des deux durées de confirmation :
    l'historique de centroïde n'atteindrait alors plus l'échantillon pris
    au tout début de la posture "lying", et la vérification d'immobilité
    porterait sur une fenêtre plus courte que prévu (dégradation
    silencieuse mais non dangereuse — cf. fall_tracker.py::_is_still).
    """
    longest_confirm = max(fall_detection.confirm_seconds, fall_detection.fast_confirm_seconds)
    if fall_detection.motion_window_s < longest_confirm:
        logger.warning(
            "fall_detection.motion_window_s (%.1fs) est inférieur à la plus longue durée de "
            "confirmation (%.1fs) : la vérification d'immobilité ne couvrira pas toute la durée "
            "de confirmation. Augmentez motion_window_s à au moins %.1fs.",
            fall_detection.motion_window_s, longest_confirm, longest_confirm,
        )


def load_config(path: Optional[str]) -> AegisConfig:
    """
    Charge la configuration depuis `path` (JSON). Si `path` est None ou que
    le fichier est introuvable, retombe sur `default_config()` et journalise
    un avertissement plutôt que d'échouer (même principe qu'ARGUS/ROSTER/
    SPECTRA/ORACLE/PULSE_TRACK).
    """
    if not path:
        logger.warning("Aucun fichier de configuration fourni, utilisation de la configuration par défaut (backend mock)")
        return default_config()

    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Fichier de configuration introuvable (%s), utilisation de la configuration par défaut", path)
        return default_config()

    raw = json.loads(file_path.read_text(encoding="utf-8"))

    analyzer = AnalyzerConfig(**raw.get("analyzer", {}))
    fall_detection = FallDetectionConfig(**raw.get("fall_detection", {}))
    _warn_if_motion_window_too_short(fall_detection)
    argus = ArgusConnectionConfig(**raw.get("argus", {}))
    publisher = PublisherConfig(**raw.get("publisher", {}))

    config = AegisConfig(
        analyzer=analyzer,
        fall_detection=fall_detection,
        argus=argus,
        publisher=publisher,
        log_stats_every_s=raw.get("log_stats_every_s", 10.0),
    )
    logger.info("Configuration AEGIS chargée depuis %s (backend analyzer=%s)", path, config.analyzer.backend)
    return config
