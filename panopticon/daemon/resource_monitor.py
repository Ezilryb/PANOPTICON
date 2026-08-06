"""
panopticon/daemon/resource_monitor.py

Lit les ressources systeme disponibles (CPU, RAM, GPU) via psutil et
optionnellement pynvml, et détermine si les besoins déclarés par un module
(ModuleSpec) peuvent être satisfaits avant que DAEMON n'autorise son démarrage.
"""

from dataclasses import dataclass
from typing import Optional

import psutil

from .module_registry import ModuleSpec

try:
    import pynvml
    pynvml.nvmlInit()
    _NVML_AVAILABLE = True
except Exception:
    _NVML_AVAILABLE = False


@dataclass
class SystemResources:
    cpu_percent_used: float
    cpu_cores_total: int
    ram_available_mb: float
    ram_total_mb: float
    gpu_available: bool
    gpu_name: Optional[str] = None
    gpu_free_mb: Optional[float] = None


class ResourceMonitor:
    """Expose l'état des ressources machine et arbitre les demandes de démarrage."""

    def snapshot(self) -> SystemResources:
        vm = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=0.3)
        gpu_available, gpu_name, gpu_free_mb = self._read_gpu()

        return SystemResources(
            cpu_percent_used=cpu_percent,
            cpu_cores_total=psutil.cpu_count(logical=True) or 1,
            ram_available_mb=vm.available / (1024 * 1024),
            ram_total_mb=vm.total / (1024 * 1024),
            gpu_available=gpu_available,
            gpu_name=gpu_name,
            gpu_free_mb=gpu_free_mb,
        )

    def _read_gpu(self) -> tuple[bool, Optional[str], Optional[float]]:
        if not _NVML_AVAILABLE:
            return False, None, None
        try:
            count = pynvml.nvmlDeviceGetCount()
            if count == 0:
                return False, None, None
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return True, name, mem.free / (1024 * 1024)
        except Exception:
            return False, None, None

    def check_availability(self, spec: ModuleSpec) -> tuple[bool, str]:
        """
        Compare les besoins déclarés par `spec` aux ressources disponibles.
        Retourne (autorisé, raison) — la raison est journalisée par l'appelant
        (DAEMON), que la demande soit acceptée ou refusée.
        """
        resources = self.snapshot()

        if spec.ram_mb is not None and resources.ram_available_mb < spec.ram_mb:
            return False, (
                f"RAM insuffisante pour {spec.codename} : "
                f"{spec.ram_mb:.0f} Mo requis, {resources.ram_available_mb:.0f} Mo disponibles"
            )

        if spec.cpu_cores is not None:
            cpu_available_cores = resources.cpu_cores_total * (1 - resources.cpu_percent_used / 100)
            if cpu_available_cores < spec.cpu_cores:
                return False, (
                    f"CPU insuffisant pour {spec.codename} : "
                    f"{spec.cpu_cores:.1f} coeurs requis, ~{cpu_available_cores:.1f} coeurs disponibles"
                )

        if spec.gpu_required and not resources.gpu_available:
            return False, f"GPU requis pour {spec.codename} mais aucun GPU détecté"

        return True, "Ressources suffisantes"
