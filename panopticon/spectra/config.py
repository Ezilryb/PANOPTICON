"""
panopticon/spectra/config.py

Configuration de SPECTRA : backend d'amélioration d'image, seuils déclenchant
chaque correction (faible luminosité, contraste, dominante colorée, bruit),
connexion au bus ARGUS dont SPECTRA consomme les frames, paramètres de son
propre bus de publication, et zones-écran optionnelles à surveiller
(cf. screen_state.py). Chargée depuis un fichier JSON ; une configuration
par défaut permet de démarrer et tester SPECTRA sans fichier de configuration.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("spectra.config")


@dataclass
class EnhancerConfig:
    backend: str = "classic"            # "classic" (CLAHE + gamma + gray-world + bilatéral, OpenCV seul) — seul backend pour l'instant
    low_light_threshold: float = 90.0    # luminosité moyenne (0-255) en-dessous de laquelle gamma + débruitage s'activent
    target_brightness: float = 120.0     # luminosité moyenne visée par la correction gamma quand elle s'active
    gamma_min: float = 0.3               # bornes de sécurité pour éviter une sur-correction sur des cas extrêmes
    gamma_max: float = 2.5
    clahe_clip_limit: float = 2.5
    clahe_tile_grid_size: int = 8
    low_contrast_threshold: float = 35.0   # écart-type de luminosité en-dessous duquel une image est jugée "plate"
    white_balance_enabled: bool = True
    color_cast_threshold: float = 8.0      # écart max entre moyennes de canaux B/G/R au-dessus duquel une dominante est corrigée
    denoise_enabled: bool = True
    denoise_bilateral_d: int = 5           # diamètre du voisinage pour le filtre bilatéral (débruitage faible lumière)
    denoise_bilateral_sigma_color: float = 50.0
    denoise_bilateral_sigma_space: float = 50.0


@dataclass
class ScreenRegionConfig:
    """
    Zone d'intérêt fixe (en pixels, référentiel de la frame de `camera_id`)
    dont SPECTRA surveille l'état GROSSIER (allumé/éteint, statique/
    dynamique) — jamais le contenu affiché. Cf. section 3 du brief projet :
    remplaçant volontairement limité de SNIFFER-CORE (hors périmètre).
    """
    camera_id: str
    region_name: str
    bbox: tuple[float, float, float, float]     # (x1, y1, x2, y2)
    on_brightness_threshold: float = 60.0
    motion_threshold: float = 6.0


@dataclass
class ArgusConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 8765                    # port du bus ArgusPublisher auquel SPECTRA se connecte en client


@dataclass
class PublisherConfig:
    host: str = "127.0.0.1"
    port: int = 8767                    # port du bus de publication propre à SPECTRA (SpectraEvent) — distinct
                                         # d'ARGUS (8765) et de ROSTER (8766)
    jpeg_quality: int = 85              # qualité JPEG de la frame améliorée écrite sur disque (frame_store)
    frame_shm_slots: int = 3            # conservé pour cohérence d'API avec ARGUS (frame_store.py) ; sans effet


@dataclass
class SpectraConfig:
    enhancer: EnhancerConfig = field(default_factory=EnhancerConfig)
    argus: ArgusConnectionConfig = field(default_factory=ArgusConnectionConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    screen_regions: list[ScreenRegionConfig] = field(default_factory=list)
    log_stats_every_s: float = 10.0


def default_config() -> SpectraConfig:
    """Configuration prête à l'emploi : backend classique, connexion ARGUS locale par défaut, aucune zone-écran."""
    return SpectraConfig()


def load_config(path: Optional[str]) -> SpectraConfig:
    """
    Charge la configuration depuis `path` (JSON). Si `path` est None ou que
    le fichier est introuvable, retombe sur `default_config()` et journalise
    un avertissement plutôt que d'échouer (même principe qu'ARGUS/ROSTER).
    """
    if not path:
        logger.warning("Aucun fichier de configuration fourni, utilisation de la configuration par défaut")
        return default_config()

    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Fichier de configuration introuvable (%s), utilisation de la configuration par défaut", path)
        return default_config()

    raw = json.loads(file_path.read_text(encoding="utf-8"))

    enhancer = EnhancerConfig(**raw.get("enhancer", {}))
    argus = ArgusConnectionConfig(**raw.get("argus", {}))
    publisher = PublisherConfig(**raw.get("publisher", {}))
    screen_regions = [
        ScreenRegionConfig(
            camera_id=r["camera_id"],
            region_name=r["region_name"],
            bbox=tuple(r["bbox"]),
            on_brightness_threshold=r.get("on_brightness_threshold", 60.0),
            motion_threshold=r.get("motion_threshold", 6.0),
        )
        for r in raw.get("screen_regions", [])
    ]

    config = SpectraConfig(
        enhancer=enhancer,
        argus=argus,
        publisher=publisher,
        screen_regions=screen_regions,
        log_stats_every_s=raw.get("log_stats_every_s", 10.0),
    )
    logger.info(
        "Configuration SPECTRA chargée depuis %s (backend enhancer=%s, %d zone(s)-écran)",
        path, config.enhancer.backend, len(config.screen_regions),
    )
    return config