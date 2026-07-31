"""
panopticon/argus/config.py

Configuration d'ARGUS : liste des caméras à ingérer, réglages du Detector,
paramètres du bus de publication (port, qualité JPEG, ring buffer mémoire).
Chargée depuis un fichier JSON ; une configuration par défaut (caméra
synthétique) permet de démarrer ARGUS et de le tester sans caméra réelle.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("argus.config")


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
    tracker_max_age_frames: int = 15     # nb de frames sans match avant suppression d'une piste
    tracker_iou_threshold: float = 0.3
    log_stats_every_s: float = 10.0


def default_config() -> ArgusConfig:
    """Configuration prête à l'emploi : une caméra synthétique, backend mock. Zéro dépendance externe, zéro matériel requis."""
    return ArgusConfig(
        cameras=[CameraConfig(camera_id="DEMO-0", source="synthetic", target_fps=10.0, width=640, height=480)],
        detector=DetectorConfig(backend="mock"),
    )


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

    config = ArgusConfig(
        cameras=cameras,
        detector=detector,
        publisher=publisher,
        tracker_max_age_frames=raw.get("tracker_max_age_frames", 15),
        tracker_iou_threshold=raw.get("tracker_iou_threshold", 0.3),
        log_stats_every_s=raw.get("log_stats_every_s", 10.0),
    )
    logger.info("Configuration chargée depuis %s (%d caméra(s))", path, len(config.cameras))
    return config
