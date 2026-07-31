"""
panopticon/daemon/orchestrator.py

DAEMON : processus d'orchestration long-vivant. Gère le cycle de vie des
modules (démarrage, arrêt, isolation par processus séparé) et refuse +
journalise tout démarrage si les ressources ou dépendances ne sont pas prêtes.
"""

import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .module_registry import ModuleRegistry, build_default_registry
from .resource_monitor import ResourceMonitor

logger = logging.getLogger("daemon")
_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ModuleRuntimeState:
    status: str = "stopped"  # stopped | starting | running | crashed | not_implemented
    process: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    started_at: Optional[datetime] = None
    last_message: str = ""


class Daemon:
    """
    Orchestrateur central de PANOPTICON. Ne contient aucune logique métier de
    module (pas de code ARGUS/SPECTRA/etc.) : uniquement la mécanique de
    supervision — registre, ressources, démarrage/arrêt, isolation processus.
    """

    def __init__(self, registry: Optional[ModuleRegistry] = None) -> None:
        self.registry: ModuleRegistry = registry or build_default_registry()
        self.resource_monitor = ResourceMonitor()
        self._state: dict[str, ModuleRuntimeState] = {
            spec.codename: ModuleRuntimeState() for spec in self.registry.all()
        }
        self.started_at = datetime.now()
        logger.info("DAEMON initialisé avec %d module(s) enregistré(s)", len(self._state))

    # ------------------------------------------------------------------ #
    # Consultation
    # ------------------------------------------------------------------ #

    def list_modules(self) -> list[dict]:
        """Vue combinée registre + état runtime, pour affichage CLI."""
        rows = []
        for spec in self.registry.all():
            state = self._state[spec.codename]
            rows.append({
                "codename": spec.codename,
                "description": spec.description,
                "depends_on": spec.depends_on,
                "implemented": spec.implemented,
                "status": state.status,
                "pid": state.pid,
                "started_at": state.started_at,
            })
        return rows

    def get_status(self, codename: str) -> Optional[ModuleRuntimeState]:
        return self._state.get(codename.upper())

    def has_running_modules(self) -> bool:
        return any(s.status == "running" for s in self._state.values())

    # ------------------------------------------------------------------ #
    # Démarrage / arrêt
    # ------------------------------------------------------------------ #

    def start_module(self, codename: str) -> str:
        codename = codename.upper()
        spec = self.registry.get(codename)

        if spec is None:
            msg = f"Module inconnu : {codename}"
            logger.warning(msg)
            return msg

        state = self._state[codename]
        if state.status == "running":
            return f"{codename} est déjà en cours d'exécution (PID {state.pid})"

        if not spec.implemented:
            msg = (
                f"{codename} n'est pas encore implémenté — seule sa déclaration "
                f"existe dans le registre à ce stade. Rien n'est démarré."
            )
            state.status = "not_implemented"
            state.last_message = msg
            logger.info("Refus de démarrage (module non implémenté) : %s", codename)
            return msg

        missing = [dep for dep in spec.depends_on if self._state[dep].status != "running"]
        if missing:
            msg = f"Dépendances non actives pour {codename} : {', '.join(missing)}"
            logger.warning("Refus de démarrage (dépendances manquantes) : %s -> %s", codename, missing)
            return msg

        allowed, reason = self.resource_monitor.check_availability(spec)
        if not allowed:
            logger.warning("Refus de démarrage (ressources insuffisantes) : %s -> %s", codename, reason)
            state.last_message = reason
            return f"Démarrage refusé pour {codename} : {reason}"

        if not spec.entry_point:
            msg = f"{codename} : ressources suffisantes, mais aucun entry_point n'est déclaré."
            logger.warning(msg)
            return msg

        return self._launch(codename, spec.entry_point, state, reason)

    def _launch(self, codename: str, entry_point: str, state: ModuleRuntimeState, reason: str) -> str:
        state.status = "starting"
        script_path = _REPO_ROOT / entry_point
        if not script_path.is_file():
            state.status = "crashed"
            msg = f"entry_point introuvable pour {codename} : {script_path}"
            logger.error(msg)
            return msg

        try:
            process = subprocess.Popen([sys.executable, str(script_path)], cwd=str(_REPO_ROOT))
        except Exception as exc:
            state.status = "crashed"
            logger.error("Échec du lancement de %s : %s", codename, exc)
            return f"Échec du lancement de {codename} : {exc}"

        state.process = process
        state.pid = process.pid
        state.started_at = datetime.now()
        state.status = "running"
        logger.info("%s démarré (PID %s) — %s", codename, process.pid, reason)
        return f"{codename} démarré (PID {process.pid})."

    def stop_module(self, codename: str) -> str:
        codename = codename.upper()
        state = self._state.get(codename)
        if state is None:
            return f"Module inconnu : {codename}"

        if state.status != "running" or state.process is None:
            return f"{codename} n'est pas en cours d'exécution"

        return self._terminate(codename, state)

    def stop_all(self) -> list[str]:
        """Arrête proprement tous les modules actuellement en cours d'exécution."""
        messages = [
            self._terminate(codename, state)
            for codename, state in self._state.items()
            if state.status == "running" and state.process is not None
        ]
        if not messages:
            messages.append("Aucun module actif à arrêter.")
        logger.info("stop_all() exécuté")
        return messages

    def refresh_crashed(self) -> list[str]:
        """Détecte les process morts sans intervention (crash) et journalise. Isole la panne : n'affecte pas les autres modules."""
        crashed = []
        for codename, state in self._state.items():
            if state.status == "running" and state.process is not None:
                if state.process.poll() is not None:
                    state.status = "crashed"
                    state.last_message = f"Process terminé de façon inattendue (code {state.process.returncode})"
                    logger.error("%s a planté : %s", codename, state.last_message)
                    state.process = None
                    state.pid = None
                    crashed.append(codename)
        return crashed

    def _terminate(self, codename: str, state: ModuleRuntimeState) -> str:
        assert state.process is not None
        logger.info("Arrêt de %s (PID %s)", codename, state.pid)
        state.process.terminate()
        try:
            state.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("%s ne répond pas à l'arrêt normal, arrêt forcé", codename)
            state.process.kill()
            state.process.wait(timeout=5)
        state.status = "stopped"
        state.process = None
        state.pid = None
        return f"{codename} arrêté."
