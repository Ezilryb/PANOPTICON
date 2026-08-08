# panopticon/roster/__init__.py
# Fait de roster/ un package Python et expose les classes principales du
# module ROSTER (reconnaissance de personnes connues, opt-in, 100% local).

from .client import RosterClient
from .config import ArgusConnectionConfig, EmbedderConfig, MatcherConfig, PublisherConfig, RosterConfig, default_config, load_config
from .data_types import EnrolledPerson, FaceMatch, FaceObservation, RosterEvent
from .enrollment import ConsentNotGivenError, EnrollmentService, NoFaceDetectedError
from .pipeline import RosterEngine
from .store import PersonStore

__all__ = [
    "RosterConfig",
    "EmbedderConfig",
    "MatcherConfig",
    "PublisherConfig",
    "ArgusConnectionConfig",
    "load_config",
    "default_config",
    "RosterEngine",
    "EnrolledPerson",
    "FaceMatch",
    "FaceObservation",
    "RosterEvent",
    "EnrollmentService",
    "ConsentNotGivenError",
    "NoFaceDetectedError",
    "PersonStore",
    "RosterClient",
]
