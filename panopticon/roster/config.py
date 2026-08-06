"""
panopticon/roster/config.py

Configuration de ROSTER : backend d'embeddings faciaux, seuil de distance de
matching, chemins de stockage (base des personnes enrôlées + photos de
référence), et connexion au bus ARGUS dont ROSTER consomme les évènements.
Chargée depuis un fichier JSON ; une configuration par défaut (backend mock,
stockage local sous panopticon/roster_data/) permet de démarrer et tester
ROSTER sans dlib/face_recognition installé.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("roster.config")

_DEFAULT_DATA_DIR = "roster_data"


@dataclass
class EmbedderConfig:
    backend: str = "mock"            # "mock" (histogramme couleur, zéro dépendance) ou "face_recognition" (dlib)
    model: str = "hog"               # utilisé seulement par le backend "face_recognition" ("hog" ou "cnn")
    upsample_times: int = 1          # nb de sur-échantillonnages pour la détection de visage (face_recognition)
    num_jitters: int = 1             # nb de ré-échantillonnages pour le calcul d'embedding (précision vs vitesse)


@dataclass
class MatcherConfig:
    distance_threshold: float = 0.6   # distance euclidienne max pour considérer un visage comme "connu"
    min_face_size_px: int = 20        # ignore les visages trop petits/trop loin (peu fiables)


@dataclass
class ArgusConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 8765                  # port du bus ArgusPublisher auquel ROSTER se connecte en client


@dataclass
class PublisherConfig:
    host: str = "127.0.0.1"
    port: int = 8766                  # port du bus de publication propre à ROSTER (RosterEvent)


@dataclass
class RosterConfig:
    data_dir: str = _DEFAULT_DATA_DIR                 # racine : personnes.json + photos de référence
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    matcher: MatcherConfig = field(default_factory=MatcherConfig)
    argus: ArgusConnectionConfig = field(default_factory=ArgusConnectionConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    person_classes: list[str] = field(default_factory=lambda: ["person"])  # classes ARGUS déclenchant un crop visage
    log_stats_every_s: float = 10.0

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def persons_db_path(self) -> Path:
        return self.data_path / "persons.json"

    @property
    def reference_photos_dir(self) -> Path:
        return self.data_path / "reference_photos"


def default_config() -> RosterConfig:
    """Configuration prête à l'emploi : backend mock, stockage local par défaut. Zéro dépendance externe."""
    return RosterConfig()


def load_config(path: Optional[str]) -> RosterConfig:
    """
    Charge la configuration depuis `path` (JSON). Si `path` est None ou que
    le fichier est introuvable, retombe sur `default_config()` et journalise
    un avertissement plutôt que d'échouer : ROSTER doit pouvoir démarrer pour
    être testé même sans configuration prête (même principe que ARGUS).
    """
    if not path:
        logger.warning("Aucun fichier de configuration fourni, utilisation de la configuration par défaut (backend mock)")
        return default_config()

    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Fichier de configuration introuvable (%s), utilisation de la configuration par défaut", path)
        return default_config()

    raw = json.loads(file_path.read_text(encoding="utf-8"))

    embedder = EmbedderConfig(**raw.get("embedder", {}))
    matcher = MatcherConfig(**raw.get("matcher", {}))
    argus = ArgusConnectionConfig(**raw.get("argus", {}))
    publisher = PublisherConfig(**raw.get("publisher", {}))

    config = RosterConfig(
        data_dir=raw.get("data_dir", _DEFAULT_DATA_DIR),
        embedder=embedder,
        matcher=matcher,
        argus=argus,
        publisher=publisher,
        person_classes=raw.get("person_classes", ["person"]),
        log_stats_every_s=raw.get("log_stats_every_s", 10.0),
    )
    logger.info("Configuration ROSTER chargée depuis %s (backend embedder=%s)", path, config.embedder.backend)
    return config
