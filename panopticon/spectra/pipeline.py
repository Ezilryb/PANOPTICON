"""
panopticon/spectra/pipeline.py

SpectraEngine : cœur du module SPECTRA. Se connecte à ARGUS en tant que
CLIENT (comme ROSTER, cf. le contrat d'intégration défini pour tout futur
module consommateur d'ARGUS), lit CHAQUE frame publiée — contrairement à
ROSTER, qui ne traite que les frames où une "person" a été détectée, SPECTRA
traite systématiquement toutes les frames : son rôle est la qualité visuelle
générale du flux, pas conditionné à une détection particulière. Applique les
corrections nécessaires (faible luminosité, débruitage, contraste, dominante
colorée), met à jour l'état grossier des éventuelles zones-écran surveillées,
écrit le résultat dans son propre fichier de frame (préfixe SPECTRA-, cf.
`data_types.spectra_camera_id`) et publie un SpectraEvent léger avec les
métriques avant/après sur son propre bus.

CHOIX D'ARCHITECTURE (écart assumé par rapport au brief projet) : le brief
(section 5) décrit SPECTRA comme recevant "les frames brutes d'ARGUS avant
l'inférence", ce qui suggérerait un appel synchrone AVANT la détection
d'ARGUS. Ce n'est pas ce qui est implémenté ici : SPECTRA consomme le flux
ARGUS déjà publié (capture -> détection -> tracking -> publication), en
aval, exactement comme ROSTER. Deux raisons à ce choix, cohérentes avec le
reste de l'architecture déjà en place :
  1. Latence : ARGUS est explicitement optimisé pour une synchronisation
     caméra -> analyse rapide (boucle événementielle ~5ms). Un appel
     synchrone vers un autre sous-processus avant l'inférence réintroduirait
     exactement la latence qu'ARGUS a été conçu pour éviter.
  2. Isolation : `depends_on=["ARGUS"]` dans module_registry.py signifie que
     DAEMON démarre SPECTRA seulement après ARGUS — cohérent avec un
     consommateur en aval, pas avec un pré-traitement synchrone dont la
     panne bloquerait ARGUS lui-même (contraire au principe d'isolation par
     processus séparé, section 1 du brief : "le crash d'un module n'affecte
     pas les autres").
Si un jour la détection d'ARGUS doit tourner sur des frames pré-améliorées
(plutôt que la frame brute), la bonne implémentation est un enhancer appelé
EN PROCESS par ArgusEngine lui-même (pas par un sous-processus séparé) —
un changement d'ARGUS, pas de SPECTRA.
"""

import logging
import threading
import time
from typing import Optional

from argus.client import ArgusClient
from argus.data_types import DetectionEvent
from argus.frame_store import SharedFrameStore

from .config import SpectraConfig
from .data_types import SpectraEvent, spectra_camera_id
from .enhancer import build_enhancer
from .metrics import SpectraMetrics
from .publisher import SpectraPublisher
from .screen_state import ScreenStateMonitor

logger = logging.getLogger("spectra.pipeline")

# Nombre de tentatives et délai entre chaque tentative de connexion à ARGUS : DAEMON marque
# ARGUS "running" dès que le subprocess est lancé, avant même que son publisher ait fini de
# binder son socket TCP — SPECTRA doit donc pouvoir patienter quelques instants au démarrage
# (même principe que roster/pipeline.py::_connect_to_argus_with_retry).
_ARGUS_CONNECT_MAX_RETRIES = 20
_ARGUS_CONNECT_RETRY_DELAY_S = 0.5


class SpectraEngine:
    """Assemble connexion ARGUS, enhancer, moniteur d'état-écran et publication en un pipeline unique."""

    def __init__(self, config: SpectraConfig) -> None:
        self.config = config

        self.enhancer = build_enhancer(config.enhancer)
        self.screen_monitor = ScreenStateMonitor(config.screen_regions)
        self.publisher = SpectraPublisher(config.publisher.host, config.publisher.port)
        self.metrics = SpectraMetrics(log_every_s=config.log_stats_every_s)

        self.argus_client = ArgusClient(config.argus.host, config.argus.port)
        self._frame_stores: dict[str, SharedFrameStore] = {}

        self._stop_event = threading.Event()
        self._main_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        logger.info("SpectraEngine : démarrage (backend enhancer=%s, %d zone(s)-écran configurée(s))",
                    self.config.enhancer.backend, len(self.config.screen_regions))
        self.enhancer.warmup()
        self.publisher.start()
        self._connect_to_argus_with_retry()

        self._stop_event.clear()
        self._main_thread = threading.Thread(target=self._run_loop, name="spectra-main-loop", daemon=True)
        self._main_thread.start()
        logger.info("SpectraEngine : démarré")

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        logger.info("SpectraEngine : arrêt demandé")
        self._stop_event.set()
        self.argus_client.close()  # tente de débloquer events() si le thread principal y est bloqué en lecture
        if self._main_thread is not None:
            # NOTE : fermer le socket depuis ce thread ne débloque pas toujours de façon fiable
            # un recv() déjà bloqué dans l'autre thread (comportement dépendant de l'OS/de
            # l'implémentation socket — constaté empiriquement dans cet environnement). Comme
            # DAEMON force de toute façon l'arrêt (SIGKILL) 5s après le SIGTERM si le process
            # ne s'est pas terminé (cf. orchestrator.py::_terminate), attendre au-delà n'apporte
            # rien : on borne ce join() à une valeur courte pour laisser SPECTRA terminer le
            # reste de son propre arrêt (publisher, frame stores) sans retarder inutilement la
            # sortie du process. Le thread, s'il reste bloqué, est un thread daemon : il ne
            # retient pas le process à la sortie.
            self._main_thread.join(timeout=3)
            self._main_thread = None
        self.publisher.stop()
        for store in self._frame_stores.values():
            store.close()
        self._frame_stores.clear()
        logger.info("SpectraEngine : arrêté proprement")

    def run_forever(self) -> None:
        """Bloque l'appelant jusqu'à `stop()` (utilisé par run_spectra.py après réception d'un signal d'arrêt)."""
        self.start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(0.5)
        finally:
            self.stop()

    def _connect_to_argus_with_retry(self) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, _ARGUS_CONNECT_MAX_RETRIES + 1):
            try:
                self.argus_client.connect()
                logger.info("SpectraEngine : connecté à ARGUS sur %s:%d (tentative %d)",
                            self.config.argus.host, self.config.argus.port, attempt)
                return
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "SpectraEngine : connexion à ARGUS échouée (tentative %d/%d) : %s",
                    attempt, _ARGUS_CONNECT_MAX_RETRIES, exc,
                )
                self._stop_event.wait(_ARGUS_CONNECT_RETRY_DELAY_S)

        raise RuntimeError(
            f"SpectraEngine : impossible de se connecter à ARGUS sur "
            f"{self.config.argus.host}:{self.config.argus.port} après {_ARGUS_CONNECT_MAX_RETRIES} tentatives "
            f"({last_error}). ARGUS est-il bien démarré ?"
        )

    # ------------------------------------------------------------------ #
    # Boucle principale (thread dédié) — bloquante sur la lecture socket
    # ------------------------------------------------------------------ #

    def _run_loop(self) -> None:
        try:
            for event in self.argus_client.events():
                if self._stop_event.is_set():
                    break
                self._process_event(event)
        except OSError:
            if not self._stop_event.is_set():
                logger.error("SpectraEngine : connexion ARGUS perdue de façon inattendue")

    def _process_event(self, event: DetectionEvent) -> None:
        frame = self.argus_client.read_frame(event)
        if frame is None:
            logger.debug("Frame indisponible pour %s (frame_id=%d), évènement ignoré", event.camera_id, event.frame_id)
            return

        ts_enhance_start = time.time()
        enhanced_image, result = self.enhancer.enhance(frame)
        ts_enhanced = time.time()

        # État grossier des zones-écran : toujours sur la frame BRUTE, jamais sur la version
        # améliorée (le gamma/CLAHE/débruitage fausserait la luminosité et le score de
        # mouvement mesurés).
        screen_regions = self.screen_monitor.update(event.camera_id, frame)

        store = self._get_or_create_store(event.camera_id)
        store.write(enhanced_image, event.frame_id, event.ts_capture, self.config.publisher.jpeg_quality)

        spectra_event = SpectraEvent(
            camera_id=event.camera_id,
            frame_id=event.frame_id,
            ts_capture=event.ts_capture,
            ts_enhanced=ts_enhanced,
            width=event.width,
            height=event.height,
            result=result,
            screen_regions=screen_regions,
        )
        self.publisher.publish(spectra_event)
        self.metrics.record_event(
            event.camera_id, spectra_event.latency_ms,
            result.low_light_correction_applied, result.denoise_applied,
            result.contrast_enhancement_applied, result.white_balance_applied,
        )
        self.metrics.maybe_log()

        elapsed_ms = (ts_enhanced - ts_enhance_start) * 1000.0
        logger.debug(
            "Caméra %s frame=%d : amélioration en %.1fms (gamma=%s débruit=%s clahe=%s wb=%s)",
            event.camera_id, event.frame_id, elapsed_ms,
            result.low_light_correction_applied, result.denoise_applied,
            result.contrast_enhancement_applied, result.white_balance_applied,
        )

    def _get_or_create_store(self, camera_id: str) -> SharedFrameStore:
        # Créé à la volée (pas à l'avance comme ArgusEngine) : SPECTRA n'a pas de liste de
        # caméras déclarée dans sa propre config, il découvre les camera_id au fil des
        # évènements ARGUS reçus.
        store = self._frame_stores.get(camera_id)
        if store is None:
            store = SharedFrameStore(spectra_camera_id(camera_id), slots=self.config.publisher.frame_shm_slots)
            self._frame_stores[camera_id] = store
        return store

    # ------------------------------------------------------------------ #
    # Introspection (utile pour NEXUS-V / diagnostics CLI)
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        return {
            "metrics": self.metrics.snapshot(),
            "publisher_clients": self.publisher.client_count,
        }