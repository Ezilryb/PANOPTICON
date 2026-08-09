"""
panopticon/argus/config.py

Configuration d'ARGUS : caméras, Detector, bus de publication, et mode
temporel de détection (TOTAL ou DETECT_AND_TRACK, cf. TrackingModeConfig).
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("argus.config")

# Vérifiées ici ET par build_light_tracker() (défense en profondeur, même principe
# que PERSON_CLASSES côté ORACLE) : une config corrompue ne doit jamais planter
# ARGUS silencieusement plus tard dans la pipeline.
VALID_TRACKING_MODES = frozenset({"total", "detect_and_track"})
VALID_LIGHT_TRACKER_BACKENDS = frozenset({"optical_flow", "mosse"})


@dataclass
class CameraConfig:
    camera_id: str
    source: str                     # index webcam ("0"), URL RTSP/HTTP, chemin fichier vidéo, ou "synthetic"
    target_fps: float = 10.0        # FPS visé pour l'analyse (indépendant du FPS natif de la caméra)
    width: Optional[int] = None     # redimensionnement demandé à la capture (None = résolution native)
    height: Optional[int] = None
    reconnect_delay_s: float = 3.0
    enabled: bool = True


@dataclass
class DetectorConfig:
    backend: str = "mock"           # "mock" (vision classique, sans dépendance lourde) ou "yolo" (ultralytics)
    weights: str = "yolo11n.pt"     # utilisé seulement par le backend "yolo"
    device: str = "auto"            # "auto" | "cpu" | "cuda"
    confidence_threshold: float = 0.4
    iou_threshold: float = 0.45
    classes_filter: Optional[list[str]] = None   # None = toutes les classes ; sinon whitelist (ex: ["person","car"])
    max_batch_size: int = 8


@dataclass
class TrackingModeConfig:
    """
    "total" (défaut, inchangé) : le Detector tourne sur CHAQUE frame — le plus
    précis, le plus gourmand.

    "detect_and_track" : le Detector ne tourne que toutes les
    `detect_every_n_frames` frames ("frame lourde" : détection complète +
    (ré)init des trackers légers). Entre deux frames lourdes, un tracker
    visuel léger (light_tracker.py) met juste à jour la position de chaque
    piste déjà connue (~1ms/objet), au prix d'une dérive corrigée à la
    prochaine frame lourde. Moins précis, nettement moins gourmand — mais
    en CPU/GPU seulement : la mémoire du Detector (ex: modèle YOLO chargé)
    reste identique, ce mode ne change pas l'empreinte RAM déclarée dans
    module_registry.py.

    Réglage GLOBAL à l'échelle d'ARGUS (comme DetectorConfig), pas par
    caméra : cohérent avec le Detector déjà appelé en lot sur toutes les
    caméras à chaque tick (cf. pipeline.py).
    """
    mode: str = "total"
    detect_every_n_frames: int = 5          # frame 0 lourde, 1..N-1 légères, N lourde, etc.
    light_tracker_backend: str = "optical_flow"   # "optical_flow" (zéro dépendance) ou "mosse" (opencv-contrib)

    # --- Réglages du backend "optical_flow" (ignorés si backend="mosse") ---
    of_max_corners: int = 30                # nb max de points suivis par objet (goodFeaturesToTrack)
    of_quality_level: float = 0.01
    of_min_distance: float = 7.0            # distance mini (px) entre deux points suivis
    of_win_size: int = 15                   # taille de fenêtre de recherche Lucas-Kanade
    of_max_pyramid_level: int = 2
    of_fb_error_threshold: float = 3.0      # erreur forward-backward max (px) tolérée par point
    of_min_surviving_points: int = 3        # sous ce nombre de points valides, la piste est jugée perdue

    def is_heavy_frame(self, frame_id: int) -> bool:
        """
        True si `frame_id` (1-indexé, cf. data_types.Frame.frame_id) doit
        passer par le Detector complet plutôt que par le tracker léger.
        Toujours True en mode "total". En mode "detect_and_track", vraie une
        fois tous les `detect_every_n_frames` (frame_id=1 est toujours
        lourde, quelle que soit la valeur de N).
        """
        if self.mode == "total":
            return True
        return (frame_id - 1) % self.detect_every_n_frames == 0


@dataclass
class PublisherConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    jpeg_quality: int = 80
    frame_shm_slots: int = 3        # nb de slots du ring buffer mémoire partagée, par caméra


@dataclass
class ArgusConfig:
    cameras: list[CameraConfig] = field(default_factory=list)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    tracking_mode: TrackingModeConfig = field(default_factory=TrackingModeConfig)
    tracker_max_age_frames: int = 15     # nb de frames sans match avant suppression d'une piste
    tracker_iou_threshold: float = 0.3
    log_stats_every_s: float = 10.0


def default_config() -> ArgusConfig:
    """Configuration prête à l'emploi : une caméra synthétique, backend mock, mode "total". Zéro dépendance externe, zéro matériel requis."""
    return ArgusConfig(
        cameras=[CameraConfig(camera_id="DEMO-0", source="synthetic", target_fps=10.0, width=640, height=480)],
        detector=DetectorConfig(backend="mock"),
    )


def _parse_tracking_mode(raw: dict) -> TrackingModeConfig:
    config = TrackingModeConfig(**raw)
    if config.mode not in VALID_TRACKING_MODES:
        logger.warning("tracking_mode.mode invalide (%r), retour à 'total' (attendu : %s)",
                        config.mode, sorted(VALID_TRACKING_MODES))
        config.mode = "total"
    if config.light_tracker_backend not in VALID_LIGHT_TRACKER_BACKENDS:
        logger.warning("tracking_mode.light_tracker_backend invalide (%r), retour à 'optical_flow' (attendu : %s)",
                        config.light_tracker_backend, sorted(VALID_LIGHT_TRACKER_BACKENDS))
        config.light_tracker_backend = "optical_flow"
    if config.detect_every_n_frames < 1:
        logger.warning("tracking_mode.detect_every_n_frames doit être >= 1 (reçu %d), retour à 5",
                        config.detect_every_n_frames)
        config.detect_every_n_frames = 5
    return config


def load_config(path: Optional[str]) -> ArgusConfig:
    """
    Charge la configuration depuis `path` (JSON). Si `path` est None ou que
    le fichier est introuvable, retombe sur `default_config()` et journalise
    un avertissement plutôt que d'échouer : ARGUS doit pouvoir démarrer pour
    être testé même sans configuration prête.
    """
    if not path:
        logger.warning("Aucun fichier de configuration fourni, utilisation de la configuration par défaut (caméra synthétique)")
        return default_config()

    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Fichier de configuration introuvable (%s), utilisation de la configuration par défaut", path)
        return default_config()

    raw = json.loads(file_path.read_text(encoding="utf-8"))

    cameras = [CameraConfig(**c) for c in raw.get("cameras", [])]
    if not cameras:
        logger.warning("Aucune caméra déclarée dans %s, ajout de la caméra synthétique de secours", path)
        cameras = [CameraConfig(camera_id="DEMO-0", source="synthetic", width=640, height=480)]

    detector = DetectorConfig(**raw.get("detector", {}))
    publisher = PublisherConfig(**raw.get("publisher", {}))
    tracking_mode = _parse_tracking_mode(raw.get("tracking_mode", {}))

    config = ArgusConfig(
        cameras=cameras,
        detector=detector,
        publisher=publisher,
        tracking_mode=tracking_mode,
        tracker_max_age_frames=raw.get("tracker_max_age_frames", 15),
        tracker_iou_threshold=raw.get("tracker_iou_threshold", 0.3),
        log_stats_every_s=raw.get("log_stats_every_s", 10.0),
    )
    logger.info("Configuration chargée depuis %s (%d caméra(s), mode=%s)",
                path, len(config.cameras), config.tracking_mode.mode)
    return config