"""
panopticon/pulse_track/pipeline.py

PulseTrackEngine : cœur du module PULSE_TRACK. Se connecte à ARGUS ET à
ROSTER en tant que CLIENT (deux threads dédiés, un par flux — les deux
générateurs .events() sont bloquants sur recv(), donc chacun a besoin du
sien), évalue chaque évènement reçu contre les règles configurées via
RuleEngine (cf. rules.py — verrouillé en interne, sûr face aux deux threads
concurrents), et publie un PulseTrackEvent sur son propre bus à chaque
déclenchement de règle.

PULSE_TRACK ne réécrit AUCUNE frame sur disque (comme ORACLE) : cf.
client.py pour comment un futur consommateur récupère l'image via
argus.frame_store.FrameReader et le camera_id porté par l'évènement.
"""

import logging
import threading
import time
from typing import Optional

from argus.client import ArgusClient
from argus.data_types import DetectionEvent
from roster.client import RosterClient
from roster.data_types import RosterEvent

from .config import PulseTrackConfig
from .data_types import PulseTrackEvent
from .metrics import PulseTrackMetrics
from .publisher import PulseTrackPublisher
from .rules import RuleEngine

logger = logging.getLogger("pulse_track.pipeline")

# Nombre de tentatives et délai entre chaque tentative de connexion à ARGUS/ROSTER : DAEMON
# marque un module "running" dès que le subprocess est lancé, avant même que son publisher ait
# fini de binder son socket TCP — PULSE_TRACK doit donc pouvoir patienter quelques instants au
# démarrage (même principe que roster/pipeline.py::_connect_to_argus_with_retry et
# spectra/oracle). _connect_with_retry() est appelé une fois par flux, indépendamment.
_CONNECT_MAX_RETRIES = 20
_CONNECT_RETRY_DELAY_S = 0.5


class PulseTrackEngine:
    """Assemble connexions ARGUS + ROSTER, moteur de règles et publication en un pipeline unique."""

    def __init__(self, config: PulseTrackConfig) -> None:
        self.config = config

        self.rule_engine = RuleEngine(config)
        self.publisher = PulseTrackPublisher(config.publisher.host, config.publisher.port)
        self.metrics = PulseTrackMetrics(log_every_s=config.log_stats_every_s)

        self.argus_client = ArgusClient(config.argus.host, config.argus.port)
        self.roster_client = RosterClient(config.roster.host, config.roster.port)

        self._stop_event = threading.Event()
        self._argus_thread: Optional[threading.Thread] = None
        self._roster_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        enabled_rules = sum(1 for r in self.config.rules if r.enabled)
        logger.info("PulseTrackEngine : démarrage (%d règle(s) active(s) / %d déclarée(s))",
                    enabled_rules, len(self.config.rules))
        self.publisher.start()
        self._connect_with_retry(self.argus_client, self.config.argus.host, self.config.argus.port, "ARGUS")
        self._connect_with_retry(self.roster_client, self.config.roster.host, self.config.roster.port, "ROSTER")

        self._stop_event.clear()
        self._argus_thread = threading.Thread(target=self._argus_loop, name="pulse_track-argus-loop", daemon=True)
        self._roster_thread = threading.Thread(target=self._roster_loop, name="pulse_track-roster-loop", daemon=True)
        self._argus_thread.start()
        self._roster_thread.start()
        logger.info("PulseTrackEngine : démarré")

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        logger.info("PulseTrackEngine : arrêt demandé")
        self._stop_event.set()
        self.argus_client.close()   # tente de débloquer chaque events() si son thread y est bloqué en lecture
        self.roster_client.close()
        if self._argus_thread is not None:
            self._argus_thread.join(timeout=10)
            self._argus_thread = None
        if self._roster_thread is not None:
            self._roster_thread.join(timeout=10)
            self._roster_thread = None
        self.publisher.stop()
        logger.info("PulseTrackEngine : arrêté proprement")

    def run_forever(self) -> None:
        """Bloque l'appelant jusqu'à `stop()` (utilisé par run_pulse_track.py après réception d'un signal d'arrêt)."""
        self.start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(0.5)
        finally:
            self.stop()

    def _connect_with_retry(self, client, host: str, port: int, label: str) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, _CONNECT_MAX_RETRIES + 1):
            try:
                client.connect()
                logger.info("PulseTrackEngine : connecté à %s sur %s:%d (tentative %d)", label, host, port, attempt)
                return
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "PulseTrackEngine : connexion à %s échouée (tentative %d/%d) : %s",
                    label, attempt, _CONNECT_MAX_RETRIES, exc,
                )
                self._stop_event.wait(_CONNECT_RETRY_DELAY_S)

        raise RuntimeError(
            f"PulseTrackEngine : impossible de se connecter à {label} sur {host}:{port} "
            f"après {_CONNECT_MAX_RETRIES} tentatives ({last_error}). {label} est-il bien démarré ?"
        )

    # ------------------------------------------------------------------ #
    # Boucles principales (un thread par flux) — bloquantes sur la lecture socket
    # ------------------------------------------------------------------ #

    def _argus_loop(self) -> None:
        try:
            for event in self.argus_client.events():
                if self._stop_event.is_set():
                    break
                self._handle_detection_event(event)
        except OSError:
            if not self._stop_event.is_set():
                logger.error("PulseTrackEngine : connexion ARGUS perdue de façon inattendue")

    def _roster_loop(self) -> None:
        try:
            for event in self.roster_client.events():
                if self._stop_event.is_set():
                    break
                self._handle_roster_event(event)
        except OSError:
            if not self._stop_event.is_set():
                logger.error("PulseTrackEngine : connexion ROSTER perdue de façon inattendue")

    def _handle_detection_event(self, event: DetectionEvent) -> None:
        self.metrics.record_detection_event_seen()
        triggered = self.rule_engine.evaluate_detection_event(event)
        self._publish_all(triggered)
        self.metrics.maybe_log()

    def _handle_roster_event(self, event: RosterEvent) -> None:
        self.metrics.record_roster_event_seen()
        triggered = self.rule_engine.evaluate_roster_event(event)
        self._publish_all(triggered)
        self.metrics.maybe_log()

    def _publish_all(self, events: list[PulseTrackEvent]) -> None:
        for pt_event in events:
            self.publisher.publish(pt_event)
            self.metrics.record_trigger(pt_event.rule_id, pt_event.severity, pt_event.latency_ms)
            logger.debug("PulseTrackEvent publié : %s (%s) sur %s", pt_event.rule_name, pt_event.rule_id, pt_event.camera_id)

    # ------------------------------------------------------------------ #
    # Introspection (utile pour NEXUS-V / diagnostics CLI)
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        return {
            "metrics": self.metrics.snapshot(),
            "publisher_clients": self.publisher.client_count,
        }