"""
panopticon/daemon/killswitch.py

Sous-module indépendant dont le seul rôle est l'arrêt propre de tout
PANOPTICON : intercepte Ctrl+C (SIGINT) et SIGTERM, arrête tous les modules
actifs via DAEMON, journalise la séquence, puis termine le processus.
"""

import logging
import signal
import sys
from types import FrameType
from typing import Optional

from .orchestrator import Daemon

logger = logging.getLogger("killswitch")


class Killswitch:
    """
    Ne contient aucune logique métier de module : peut arrêter PANOPTICON
    même si un ou plusieurs modules sont plantés ou ne répondent plus.
    """

    def __init__(self, daemon: Daemon) -> None:
        self.daemon = daemon
        self._triggered = False

    def arm(self) -> None:
        """Active l'écoute des signaux d'arrêt (Ctrl+C, kill, arrêt système)."""
        signal.signal(signal.SIGINT, self._on_signal)
        try:
            signal.signal(signal.SIGTERM, self._on_signal)
        except (AttributeError, ValueError):
            # SIGTERM indisponible/restreint sur certaines plateformes (ex. Windows) : SIGINT reste actif.
            logger.warning("SIGTERM non disponible sur cette plateforme, SIGINT reste actif")
        logger.info("Killswitch armé (SIGINT/SIGTERM)")

    def _on_signal(self, signum: int, frame: Optional[FrameType]) -> None:
        name = signal.Signals(signum).name
        logger.info("Signal reçu : %s — déclenchement de l'arrêt d'urgence", name)
        self.trigger(exit_code=0)

    def trigger(self, exit_code: int = 0) -> None:
        """Arrête tous les modules actifs puis quitte PANOPTICON. Idempotent."""
        if self._triggered:
            return
        self._triggered = True

        print("\n[KILLSWITCH] Arrêt de PANOPTICON en cours...")
        for line in self.daemon.stop_all():
            print(f"  - {line}")
        logger.info("Arrêt complet de PANOPTICON effectué")
        print("[KILLSWITCH] DAEMON arrêté. Fin de PANOPTICON.")
        sys.exit(exit_code)
