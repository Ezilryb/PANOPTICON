# panopticon/oracle/__init__.py
# Fait d'oracle/ un package Python et expose les classes principales du
# module ORACLE (identification fine d'objets — marque/modèle — via API externe).

from .cache import IdentificationCache
from .client import OracleClient
from .config import (
    ArgusConnectionConfig,
    CacheConfig,
    IdentifierConfig,
    PERSON_CLASSES,
    PublisherConfig,
    OracleConfig,
    default_config,
    load_config,
)
from .data_types import IdentifiedObject, ObjectIdentification, OracleEvent
from .identifier import BaseIdentifier, GoogleVisionIdentifier, MockIdentifier, build_identifier
from .pipeline import OracleEngine

__all__ = [
    "OracleConfig",
    "IdentifierConfig",
    "CacheConfig",
    "ArgusConnectionConfig",
    "PublisherConfig",
    "PERSON_CLASSES",
    "load_config",
    "default_config",
    "OracleEngine",
    "IdentifiedObject",
    "ObjectIdentification",
    "OracleEvent",
    "BaseIdentifier",
    "MockIdentifier",
    "GoogleVisionIdentifier",
    "build_identifier",
    "IdentificationCache",
    "OracleClient",
]
