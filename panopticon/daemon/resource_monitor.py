"""Surveillance des ressources système."""

import logging

import psutil

from shared.models import ResourceSnapshot

logger = logging.getLogger(__name__)


def _gpu_info() -> tuple[bool, str | None, float | None, float | None]:
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            name = torch.cuda.get_device_name(0)
            return True, name, total / (1024 * 1024), free / (1024 * 1024)
    except Exception as exc:
        logger.debug("GPU indisponible via torch: %s", exc)

    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode()
        pynvml.nvmlShutdown()
        return True, name, info.total / (1024 * 1024), info.free / (1024 * 1024)
    except Exception:
        pass

    return False, None, None, None


def get_resource_snapshot() -> ResourceSnapshot:
    mem = psutil.virtual_memory()
    gpu_available, gpu_name, gpu_total, gpu_free = _gpu_info()
    return ResourceSnapshot(
        cpu_percent=psutil.cpu_percent(interval=0.1),
        ram_total_mb=mem.total / (1024 * 1024),
        ram_available_mb=mem.available / (1024 * 1024),
        ram_used_mb=mem.used / (1024 * 1024),
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        gpu_memory_total_mb=gpu_total,
        gpu_memory_free_mb=gpu_free,
    )


def can_allocate(ram_mb: int, cpu_cores: float, gpu_mb: int = 0) -> tuple[bool, str]:
    snap = get_resource_snapshot()
    if snap.ram_available_mb < ram_mb:
        return False, f"RAM insuffisante: {snap.ram_available_mb:.0f} MB disponibles, {ram_mb} MB requis"
    if snap.cpu_percent > 90 and cpu_cores >= 0.5:
        return False, f"CPU saturé ({snap.cpu_percent:.0f}%)"
    if gpu_mb > 0:
        if not snap.gpu_available:
            return False, "GPU requis mais indisponible"
        if snap.gpu_memory_free_mb is not None and snap.gpu_memory_free_mb < gpu_mb:
            return False, (
                f"VRAM insuffisante: {snap.gpu_memory_free_mb:.0f} MB disponibles, "
                f"{gpu_mb} MB requis"
            )
    return True, "ok"
