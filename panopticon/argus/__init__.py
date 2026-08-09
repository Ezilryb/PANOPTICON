# panopticon/argus/__init__.py
# Fait d'argus/ un package Python et expose les classes principales du
# module ARGUS (ingestion multi-caméras + détection d'objets et de personnes).

from .client import ArgusClient
from .config import ArgusConfig, CameraConfig, DetectorConfig, PublisherConfig, TrackingModeConfig, default_config, load_config
from .data_types import Detection, DetectionEvent, Frame
from .pipeline import ArgusEngine

__all__ = [
    "ArgusConfig",
    "CameraConfig",
    "DetectorConfig",
    "PublisherConfig",
    "TrackingModeConfig",
    "load_config",
    "default_config",
    "ArgusEngine",
    "Detection",
    "DetectionEvent",
    "Frame",
    "ArgusClient",
]