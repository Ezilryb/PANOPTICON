# panopticon/spectra/__init__.py
# Fait de spectra/ un package Python et expose les classes principales du
# module SPECTRA (amélioration d'image : faible luminosité, contraste,
# couleur, débruitage — et détection grossière d'état d'écran).

from .client import SpectraClient
from .config import (
    ArgusConnectionConfig,
    EnhancerConfig,
    PublisherConfig,
    ScreenRegionConfig,
    SpectraConfig,
    default_config,
    load_config,
)
from .data_types import EnhancementResult, ScreenRegionState, SpectraEvent, spectra_camera_id
from .enhancer import BaseEnhancer, ClassicEnhancer, build_enhancer
from .pipeline import SpectraEngine
from .screen_state import ScreenStateMonitor

__all__ = [
    "SpectraConfig",
    "EnhancerConfig",
    "PublisherConfig",
    "ArgusConnectionConfig",
    "ScreenRegionConfig",
    "load_config",
    "default_config",
    "SpectraEngine",
    "EnhancementResult",
    "ScreenRegionState",
    "SpectraEvent",
    "spectra_camera_id",
    "BaseEnhancer",
    "ClassicEnhancer",
    "build_enhancer",
    "ScreenStateMonitor",
    "SpectraClient",
]