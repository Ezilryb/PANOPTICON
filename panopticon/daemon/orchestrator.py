"""Orchestrateur DAEMON — supervise le cycle de vie des modules."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from daemon.event_bus import InMemoryEventBus, RedisEventBus, create_event_bus
from daemon.module_registry import MODULE_REGISTRY, ModuleSpec, modules_for_profile
from daemon.resource_monitor import can_allocate, get_resource_snapshot
from shared.config import settings
from shared.models import ModuleStatus

logger = logging.getLogger(__name__)


def _run_module_worker(module_name: str) -> None:
    """Point d'entrée des sous-processus de module."""
    from shared.logging_utils import setup_logging

    setup_logging(settings.log_level)
    log = logging.getLogger(f"module.{module_name}")

    if module_name == "argus":
        from modules.argus.service import run_argus

        run_argus()
    elif module_name == "sys_log":
        from modules.sys_log.service import run_sys_log

        run_sys_log()
    elif module_name == "vault":
        from modules.vault.storage_manager import run_vault

        run_vault()
    else:
        log.warning("Module %s non implémenté — worker en attente", module_name)
        import time

        while True:
            time.sleep(60)


@dataclass
class ManagedModule:
    spec: ModuleSpec
    status: str = "stopped"
    process: mp.Process | None = None
    started_at: datetime | None = None
    message: str | None = None


class DaemonOrchestrator:
    """Supervise les modules PANOPTICON."""

    def __init__(self) -> None:
        self._modules: dict[str, ManagedModule] = {
            name: ManagedModule(spec=spec) for name, spec in MODULE_REGISTRY.items()
        }
        self._lock = asyncio.Lock()
        self.event_bus: InMemoryEventBus | RedisEventBus = create_event_bus(settings.redis_url)
        self._monitor_task: asyncio.Task | None = None

    async def start(self) -> None:
        if isinstance(self.event_bus, RedisEventBus):
            await self.event_bus.connect()
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        profile = settings.panopticon_profile
        for name in modules_for_profile(profile):
            if name == "nexus_v":
                continue  # frontend séparé
            await self.start_module(name, force=False)

    async def stop(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        for name in list(self._modules):
            await self.stop_module(name)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            async with self._lock:
                for name, managed in self._modules.items():
                    proc = managed.process
                    if proc and not proc.is_alive() and managed.status == "running":
                        managed.status = "crashed"
                        managed.message = f"Processus {name} terminé (code {proc.exitcode})"
                        managed.process = None
                        logger.error("Module %s crashé (exit %s)", name, proc.exitcode)
                        await self.event_bus.publish(
                            "daemon.module_crashed",
                            {"module": name, "exitcode": proc.exitcode},
                        )

    async def _log_action(self, action: str, target: str, detail: dict | None = None) -> None:
        """SYS-LOG — journalise une action opérateur. Best-effort : ne doit jamais lever."""
        try:
            from api.database import SessionLocal
            from api.repositories.operator_actions import log_action

            async with SessionLocal() as session:
                await log_action(session, action, target, detail)
        except Exception:
            logger.exception("Échec de journalisation SYS-LOG pour l'action '%s' sur '%s'", action, target)

    def _dependencies_running(self, spec: ModuleSpec) -> tuple[bool, str]:
        for dep in spec.dependencies:
            if dep == "daemon":
                continue
            managed = self._modules.get(dep)
            if not managed or managed.status != "running":
                return False, f"Dépendance '{dep}' non active"
        return True, "ok"

    async def start_module(self, name: str, force: bool = False) -> ModuleStatus:
        async with self._lock:
            managed = self._modules.get(name)
            if not managed:
                raise KeyError(f"Module inconnu: {name}")
            if managed.status in ("running", "starting"):
                return self._to_status(managed)

            spec = managed.spec
            deps_ok, deps_msg = self._dependencies_running(spec)
            if not deps_ok and not force:
                managed.message = deps_msg
                logger.warning("Refus démarrage %s: %s", name, deps_msg)
                await self._log_action("module_start_refused", name, {"reason": deps_msg})
                return self._to_status(managed)

            ok, reason = can_allocate(spec.ram_mb, spec.cpu_cores, spec.gpu_mb)
            if not ok and not force:
                managed.message = reason
                logger.warning("Refus démarrage %s: %s", name, reason)
                await self.event_bus.publish(
                    "daemon.module_refused",
                    {"module": name, "reason": reason},
                )
                await self._log_action("module_start_refused", name, {"reason": reason})
                return self._to_status(managed)

            managed.status = "starting"
            proc = mp.Process(
                target=_run_module_worker,
                args=(name,),
                name=f"panopticon-{name}",
                daemon=True,
            )
            proc.start()
            managed.process = proc
            managed.started_at = datetime.utcnow()
            managed.status = "running"
            managed.message = None
            logger.info("Module %s démarré (pid %s)", name, proc.pid)
            await self.event_bus.publish("daemon.module_started", {"module": name})
            await self._log_action("module_started", name, {"pid": proc.pid})
            return self._to_status(managed)

    async def stop_module(self, name: str) -> ModuleStatus:
        async with self._lock:
            managed = self._modules.get(name)
            if not managed:
                raise KeyError(f"Module inconnu: {name}")
            proc = managed.process
            if proc and proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
                if proc.is_alive():
                    proc.kill()
            managed.process = None
            managed.status = "stopped"
            managed.started_at = None
            logger.info("Module %s arrêté", name)
            await self.event_bus.publish("daemon.module_stopped", {"module": name})
            await self._log_action("module_stopped", name)
            return self._to_status(managed)

    def list_modules(self) -> list[ModuleStatus]:
        return [self._to_status(m) for m in self._modules.values()]

    def get_resources(self):
        return get_resource_snapshot()

    def _to_status(self, managed: ManagedModule) -> ModuleStatus:
        cpu = ram = None
        proc = managed.process
        if proc and proc.is_alive():
            try:
                import psutil

                p = psutil.Process(proc.pid)
                cpu = p.cpu_percent(interval=0.0)
                ram = p.memory_info().rss / (1024 * 1024)
            except Exception:
                pass
        return ModuleStatus(
            name=managed.spec.name,
            status=managed.status,  # type: ignore[arg-type]
            cpu_percent=cpu,
            ram_mb=ram,
            started_at=managed.started_at,
            message=managed.message,
        )


# Instance globale partagée avec l'API
orchestrator = DaemonOrchestrator()
