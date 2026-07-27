"""Registre des modules PANOPTICON."""

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    description: str
    ram_mb: int
    cpu_cores: float
    gpu_mb: int = 0
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    entrypoint: Callable[[], None] | None = None
    auto_start_profiles: tuple[str, ...] = ("standard", "full")


MODULE_REGISTRY: dict[str, ModuleSpec] = {
    "argus": ModuleSpec(
        name="argus",
        description="Ingestion multi-caméras et détection d'objets/personnes",
        ram_mb=1024,
        cpu_cores=1.0,
        gpu_mb=512,
        dependencies=("daemon",),
        auto_start_profiles=("light", "standard", "full"),
    ),
    "spectra": ModuleSpec(
        name="spectra",
        description="Amélioration d'image et détection d'état d'écran",
        ram_mb=256,
        cpu_cores=0.5,
        dependencies=("argus",),
        auto_start_profiles=("full",),
    ),
    "oracle": ModuleSpec(
        name="oracle",
        description="Identification fine d'objets via API externe",
        ram_mb=256,
        cpu_cores=0.5,
        dependencies=("argus",),
        auto_start_profiles=("full",),
    ),
    "roster": ModuleSpec(
        name="roster",
        description="Reconnaissance de personnes enrôlées (local, opt-in)",
        ram_mb=512,
        cpu_cores=0.5,
        dependencies=("argus",),
        auto_start_profiles=("standard", "full"),
    ),
    "pulse_track": ModuleSpec(
        name="pulse_track",
        description="Moteur de règles et notifications",
        ram_mb=128,
        cpu_cores=0.25,
        dependencies=("argus", "roster"),
        auto_start_profiles=("standard", "full"),
    ),
    "aegis": ModuleSpec(
        name="aegis",
        description="Détection de chute / urgence par posture",
        ram_mb=512,
        cpu_cores=0.5,
        gpu_mb=256,
        dependencies=("argus",),
        auto_start_profiles=("full",),
    ),
    "vault": ModuleSpec(
        name="vault",
        description="Stockage, rétention et chiffrement",
        ram_mb=128,
        cpu_cores=0.25,
        dependencies=(),
        auto_start_profiles=("light", "standard", "full"),
    ),
    "sys_log": ModuleSpec(
        name="sys_log",
        description="Journal unifié des événements et actions opérateur",
        ram_mb=128,
        cpu_cores=0.25,
        dependencies=(),
        auto_start_profiles=("standard", "full"),
    ),
    "nexus_v": ModuleSpec(
        name="nexus_v",
        description="Dashboard de visualisation",
        ram_mb=256,
        cpu_cores=0.25,
        dependencies=(),
        auto_start_profiles=("light", "standard", "full"),
    ),
}


def get_module(name: str) -> ModuleSpec | None:
    return MODULE_REGISTRY.get(name)


def modules_for_profile(profile: str) -> list[str]:
    return [
        spec.name
        for spec in MODULE_REGISTRY.values()
        if profile in spec.auto_start_profiles
    ]
