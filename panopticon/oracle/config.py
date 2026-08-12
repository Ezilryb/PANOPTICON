"""
panopticon/oracle/config.py

Configuration d'ORACLE : backend d'identification fine d'objets (marque/
modèle), paramètres du cache par hash perceptuel, connexion au bus ARGUS
dont ORACLE consomme les évènements, et paramètres de son propre bus de
publication. Chargée depuis un fichier JSON ; une configuration par défaut
(backend "mock", zéro dépendance réseau) permet de démarrer et tester
ORACLE sans clé API.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("oracle.config")

_DEFAULT_DATA_DIR = "oracle_data"

# Classes ARGUS/COCO considérées comme des objets "identifiables" (marque/modèle a du
# sens) par défaut. Volontairement une liste blanche : toute classe absente d'ici est
# ignorée par ORACLE, y compris si elle est ajoutée un jour à ARGUS sans y être ajoutée
# explicitement ici. "person" ne doit JAMAIS y figurer — cf. PERSON_CLASSES ci-dessous,
# qui est un garde-fou en dur appliqué par la pipeline en plus de cette liste, pas à sa place.
_DEFAULT_IDENTIFIABLE_CLASSES = [
    "car", "motorcycle", "bus", "truck", "bicycle",
    "laptop", "tv", "cell phone", "keyboard", "mouse",
    "backpack", "handbag", "suitcase", "book", "clock",
]

# Classes qu'ORACLE ne doit JAMAIS envoyer à un identifiant, quelle que soit la
# configuration : garde-fou en dur (cf. critère d'acceptation section 10 du brief
# projet — "ORACLE ne s'exécute jamais sur un crop contenant un visage"). Vérifié en
# plus de identifiable_classes dans pipeline.py, jamais à sa place.
PERSON_CLASSES = frozenset({"person"})


@dataclass
class IdentifierConfig:
    backend: str = "mock"                  # "mock" (zéro réseau) ou "google_vision" (API externe)
    api_key_env_var: str = "ORACLE_VISION_API_KEY"   # nom de la variable d'environnement — jamais la clé elle-même ici
    endpoint: str = "https://vision.googleapis.com/v1/images:annotate"
    timeout_s: float = 8.0
    max_results: int = 8                   # nb de candidats web/entités demandés à l'API
    confidence_threshold: float = 0.4      # score minimal pour accepter un candidat comme identification


@dataclass
class CacheConfig:
    hash_size: int = 8                     # dHash sur une grille hash_size x hash_size (8 -> hash 64 bits)
    max_hamming_distance: int = 6          # tolérance de similarité pour considérer deux crops "identiques"
    max_entries: int = 5000                # borne haute, purge des plus anciennes entrées au-delà (LRU)


@dataclass
class ArgusConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 8765                       # port du bus ArgusPublisher auquel ORACLE se connecte en client


@dataclass
class PublisherConfig:
    host: str = "127.0.0.1"
    port: int = 8768                       # port du bus de publication propre à ORACLE (OracleEvent) —
                                            # distinct d'ARGUS (8765), ROSTER (8766) et SPECTRA (8767)


@dataclass
class OracleConfig:
    data_dir: str = _DEFAULT_DATA_DIR                       # racine : cache d'identification persistant
    identifier: IdentifierConfig = field(default_factory=IdentifierConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    argus: ArgusConnectionConfig = field(default_factory=ArgusConnectionConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    identifiable_classes: list[str] = field(default_factory=lambda: list(_DEFAULT_IDENTIFIABLE_CLASSES))
    min_confidence_to_identify: float = 0.55    # confiance ARGUS minimale sur la détection pour tenter une identification
    max_api_calls_per_minute: int = 20          # limite de débit sortant vers l'API — protège le budget en cas d'afflux
    log_stats_every_s: float = 10.0

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def cache_db_path(self) -> Path:
        return self.data_path / "identification_cache.json"


def default_config() -> OracleConfig:
    """Configuration prête à l'emploi : backend mock, aucune clé API requise, aucun appel réseau."""
    return OracleConfig()


def load_config(path: Optional[str]) -> OracleConfig:
    """
    Charge la configuration depuis `path` (JSON). Si `path` est None ou que
    le fichier est introuvable, retombe sur `default_config()` et journalise
    un avertissement plutôt que d'échouer (même principe qu'ARGUS/ROSTER/SPECTRA).
    """
    if not path:
        logger.warning("Aucun fichier de configuration fourni, utilisation de la configuration par défaut (backend mock)")
        return default_config()

    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Fichier de configuration introuvable (%s), utilisation de la configuration par défaut", path)
        return default_config()

    raw = json.loads(file_path.read_text(encoding="utf-8"))

    identifier = IdentifierConfig(**raw.get("identifier", {}))
    cache = CacheConfig(**raw.get("cache", {}))
    argus = ArgusConnectionConfig(**raw.get("argus", {}))
    publisher = PublisherConfig(**raw.get("publisher", {}))

    identifiable_classes = raw.get("identifiable_classes", list(_DEFAULT_IDENTIFIABLE_CLASSES))
    blocked = PERSON_CLASSES.intersection(identifiable_classes)
    if blocked:
        # On n'échoue pas le chargement pour ça : on retire silencieusement puis on avertit,
        # la pipeline aurait de toute façon bloqué ces classes au moment du traitement.
        logger.warning(
            "identifiable_classes contient des classes interdites (%s) — ignorées. "
            "ORACLE ne traite jamais de crop de personne.", sorted(blocked),
        )
        identifiable_classes = [c for c in identifiable_classes if c not in PERSON_CLASSES]

    config = OracleConfig(
        data_dir=raw.get("data_dir", _DEFAULT_DATA_DIR),
        identifier=identifier,
        cache=cache,
        argus=argus,
        publisher=publisher,
        identifiable_classes=identifiable_classes,
        min_confidence_to_identify=raw.get("min_confidence_to_identify", 0.55),
        max_api_calls_per_minute=raw.get("max_api_calls_per_minute", 20),
        log_stats_every_s=raw.get("log_stats_every_s", 10.0),
    )
    logger.info("Configuration ORACLE chargée depuis %s (backend identifier=%s)", path, config.identifier.backend)
    return config
