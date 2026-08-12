# panopticon/aegis/__init__.py
# Fait d'aegis/ un package Python et expose les classes principales du
# module AEGIS (détection de chute / urgence par analyse de posture).

from .client import AegisClient
from .config import AegisConfig, AnalyzerConfig, ArgusConnectionConfig, FallDetectionConfig, PublisherConfig, default_config, load_config
from .data_types import AegisEvent, PostureResult, VALID_EVENT_TYPES, VALID_END_REASONS, VALID_POSTURES
from .fall_tracker import FallStateTracker
from .pipeline import AegisEngine
from .posture_analyzer import BasePostureAnalyzer, MockPostureAnalyzer, YoloPoseAnalyzer, build_analyzer

__all__ = [
    "AegisConfig",
    "AnalyzerConfig",
    "FallDetectionConfig",
    "ArgusConnectionConfig",
    "PublisherConfig",
    "load_config",
    "default_config",
    "AegisEngine",
    "FallStateTracker",
    "PostureResult",
    "AegisEvent",
    "VALID_POSTURES",
    "VALID_EVENT_TYPES",
    "VALID_END_REASONS",
    "BasePostureAnalyzer",
    "MockPostureAnalyzer",
    "YoloPoseAnalyzer",
    "build_analyzer",
    "AegisClient",
]
