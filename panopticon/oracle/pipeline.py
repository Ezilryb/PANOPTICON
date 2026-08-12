"""
panopticon/oracle/pipeline.py

OracleEngine : cœur du module ORACLE. Se connecte à ARGUS en tant que
CLIENT (même contrat d'intégration que ROSTER/SPECTRA), filtre les
détections dont la classe fait partie de `identifiable_classes` (jamais
"person" — double garde-fou, cf. plus bas), récupère la frame brute
correspondante, recadre chaque objet éligible, calcule son hash perceptuel,
regarde d'abord dans le cache avant tout appel API, puis publie le résultat
sur son propre bus (`OraclePublisher`).

GARDE-FOU CRITIQUE (critère d'acceptation section 10 du brief projet) :
« ORACLE ne s'exécute jamais sur un crop contenant un visage ». Ce module
n'a aucune notion de "visage" — sa seule garantie possible est de ne JAMAIS
traiter un crop dont la détection ARGUS d'origine est de classe "person".
Ce filtre est appliqué à DEUX niveaux indépendants :
  1. `identifiable_classes` (liste blanche configurée, "person" en est
     retiré au chargement — cf. config.py::load_config) ;
  2. `_NEVER_IDENTIFY_CLASSES` ci-dessous, vérifié ICI en plus, JAMAIS à la
     place du (1) — même si la configuration est un jour corrompue ou mal
     éditée à la main pour inclure "person", ce filtre en dur l'empêche
     quand même d'atteindre `self.identifier.identify()`.
Un objet "person" détecté par ARGUS relève exclusivement de ROSTER.
"""

import logging
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from argus.client import ArgusClient
from argus.data_types import DetectionEvent

from .cache import IdentificationCache
from .config import PERSON_CLASSES, OracleConfig
from .data_types import IdentifiedObject, OracleEvent
from .identifier import build_identifier
from .metrics import OracleMetrics
from .phash import compute_dhash
from .publisher import OraclePublisher

logger = logging.getLogger("oracle.pipeline")

_ARGUS_CONNECT_MAX_RETRIES = 20
_ARGUS_CONNECT_RETRY_DELAY_S = 0.5

# Marge ajoutée autour de la bbox avant recadrage : petite, contrairement à ROSTER (visages),
# un objet (véhicule, électronique...) remplit en général déjà bien sa bbox ARGUS.
_CROP_MARGIN_RATIO = 0.05

# Garde-fou en dur, indépendant de toute configuration — cf. docstring du module.
_NEVER_IDENTIFY_CLASSES = frozenset(PERSON_CLASSES)


class _RateLimiter:
    """
    Limite le nombre d'appels API sortants par minute glissante, pour ne
    jamais dépasser un budget de coût même en cas d'afflux soudain d'objets
    identifiables (ex: plusieurs véhicules identifiables simultanément sur
    plusieurs caméras). N'affecte jamais les lectures de cache (`lookup()`
    est toujours autorisé) — seul un cache-miss consomme le débit autorisé.
    """

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: deque = deque()

    def allow(self) -> bool:
        if self.max_per_minute <= 0:
            return True  # 0 ou négatif = pas de limite
        now = time.time()
        while self._timestamps and now - self._timestamps[0] > 60.0:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_per_minute:
            return False
        self._timestamps.append(now)
        return True


class OracleEngine:
    """Assemble connexion ARGUS, cache perceptuel, identifiant et publication en un pipeline unique."""

    def __init__(self, config: OracleConfig) -> None:
        self.config = config

        self.identifier = build_identifier(config.identifier)
        self.cache = IdentificationCache(
            config.data_path, hash_size=config.cache.hash_size,
            max_hamming_distance=config.cache.max_hamming_distance, max_entries=config.cache.max_entries,
        )
        self.publisher = OraclePublisher(config.publisher.host, config.publisher.port)
        self.metrics = OracleMetrics(log_every_s=config.log_stats_every_s)
        self._rate_limiter = _RateLimiter(config.max_api_calls_per_minute)

        self.argus_client = ArgusClient(config.argus.host, config.argus.port)

        # Liste blanche effective = ce que la config déclare, MOINS toute classe interdite —
        # recalculée ici (pas seulement en confiance dans load_config) pour rester correcte
        # même si OracleConfig est construit directement en Python (tests, embedding...) sans
        # passer par load_config().
        self._identifiable_classes = frozenset(config.identifiable_classes) - _NEVER_IDENTIFY_CLASSES

        self._stop_event = threading.Event()
        self._main_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        logger.info("OracleEngine : démarrage (backend identifier=%s, %d classe(s) identifiable(s), cache=%d entrée(s))",
                    self.config.identifier.backend, len(self._identifiable_classes), len(self.cache))
        self.identifier.warmup()
        self.publisher.start()
        self._connect_to_argus_with_retry()

        self._stop_event.clear()
        self._main_thread = threading.Thread(target=self._run_loop, name="oracle-main-loop", daemon=True)
        self._main_thread.start()
        logger.info("OracleEngine : démarré")

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        logger.info("OracleEngine : arrêt demandé")
        self._stop_event.set()
        self.argus_client.close()  # débloque events() si le thread principal y est bloqué en lecture
        if self._main_thread is not None:
            self._main_thread.join(timeout=10)
            self._main_thread = None
        self.publisher.stop()
        logger.info("OracleEngine : arrêté proprement")

    def run_forever(self) -> None:
        """Bloque l'appelant jusqu'à `stop()` (utilisé par run_oracle.py après réception d'un signal d'arrêt)."""
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
                logger.info("OracleEngine : connecté à ARGUS sur %s:%d (tentative %d)",
                            self.config.argus.host, self.config.argus.port, attempt)
                return
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "OracleEngine : connexion à ARGUS échouée (tentative %d/%d) : %s",
                    attempt, _ARGUS_CONNECT_MAX_RETRIES, exc,
                )
                self._stop_event.wait(_ARGUS_CONNECT_RETRY_DELAY_S)

        raise RuntimeError(
            f"OracleEngine : impossible de se connecter à ARGUS sur "
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
                logger.error("OracleEngine : connexion ARGUS perdue de façon inattendue")

    def _process_event(self, event: DetectionEvent) -> None:
        candidates = [
            d for d in event.detections
            if d.class_name in self._identifiable_classes
            and d.class_name not in _NEVER_IDENTIFY_CLASSES  # redondant avec la ligne ci-dessus, assumé (défense en profondeur)
            and d.confidence >= self.config.min_confidence_to_identify
        ]
        if not candidates:
            return

        frame = self.argus_client.read_frame(event)
        if frame is None:
            logger.debug("Frame indisponible pour %s (frame_id=%d), évènement ignoré", event.camera_id, event.frame_id)
            return

        ts_identify_start = time.time()
        identified_objects: list[IdentifiedObject] = []

        for detection in candidates:
            assert detection.class_name not in _NEVER_IDENTIFY_CLASSES  # garde-fou : ne doit jamais pouvoir être faux ici

            crop = self._crop_with_margin(frame, detection.bbox)
            if crop.size == 0:
                continue

            phash = compute_dhash(crop, self.config.cache.hash_size)
            cached = self.cache.lookup(phash)
            if cached is not None:
                identified_objects.append(IdentifiedObject(
                    bbox=detection.bbox, class_name=detection.class_name,
                    source_track_id=detection.track_id, identification=cached, from_cache=True,
                ))
                continue

            if not self._rate_limiter.allow():
                logger.debug("Limite de débit API atteinte (%d/min), objet ignoré pour cette frame",
                             self.config.max_api_calls_per_minute)
                self.metrics.record_rate_limit_skip(event.camera_id)
                identified_objects.append(IdentifiedObject(
                    bbox=detection.bbox, class_name=detection.class_name,
                    source_track_id=detection.track_id, identification=None, from_cache=False,
                ))
                continue

            identification = self.identifier.identify(crop)
            if identification is not None:
                self.cache.store(phash, identification)
            identified_objects.append(IdentifiedObject(
                bbox=detection.bbox, class_name=detection.class_name,
                source_track_id=detection.track_id, identification=identification, from_cache=False,
            ))

        if not identified_objects:
            return

        ts_identified = time.time()
        oracle_event = OracleEvent(
            camera_id=event.camera_id,
            frame_id=event.frame_id,
            ts_capture=event.ts_capture,
            ts_identified=ts_identified,
            objects=identified_objects,
        )
        self.publisher.publish(oracle_event)
        self.metrics.record_event(event.camera_id, oracle_event.latency_ms, identified_objects)
        self.metrics.maybe_log()

        elapsed_ms = (ts_identified - ts_identify_start) * 1000.0
        logger.debug(
            "Caméra %s frame=%d : %d objet(s) traité(s) en %.1fms",
            event.camera_id, event.frame_id, len(identified_objects), elapsed_ms,
        )

    @staticmethod
    def _crop_with_margin(frame: np.ndarray, bbox) -> np.ndarray:
        """Recadre `frame` sur `bbox` avec une petite marge, clippé aux dimensions réelles de l'image."""
        height, width = frame.shape[0], frame.shape[1]
        x1, y1, x2, y2 = bbox
        box_w, box_h = x2 - x1, y2 - y1
        margin_x, margin_y = box_w * _CROP_MARGIN_RATIO, box_h * _CROP_MARGIN_RATIO

        cx1 = max(0, int(x1 - margin_x))
        cy1 = max(0, int(y1 - margin_y))
        cx2 = min(width, int(x2 + margin_x))
        cy2 = min(height, int(y2 + margin_y))

        return frame[cy1:cy2, cx1:cx2]

    # ------------------------------------------------------------------ #
    # Introspection (utile pour NEXUS-V / diagnostics CLI)
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        return {
            "cache_entries": len(self.cache),
            "metrics": self.metrics.snapshot(),
            "publisher_clients": self.publisher.client_count,
        }
