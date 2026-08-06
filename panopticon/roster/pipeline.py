"""
panopticon/roster/pipeline.py

RosterEngine : cœur du module ROSTER. Se connecte à ARGUS en tant que
CLIENT (via `argus.client.ArgusClient`, cf. le contrat d'intégration défini
pour tous les futurs modules consommateurs), filtre les détections de classe
"person", récupère la frame brute correspondante, recadre chaque personne
détectée et lance dessus la détection de visage + calcul d'embedding, puis
matche contre la base des personnes enrôlées et publie le résultat sur son
propre bus (`RosterPublisher`).

Recadrer sur la bbox "person" d'ARGUS plutôt que scanner la frame entière
a deux avantages : (1) c'est nettement moins coûteux (surface analysée
réduite), et (2) chaque visage retrouvé reste directement associé au
`track_id` ARGUS de la personne dont il provient, ce qui permettra à
PULSE_TRACK de raisonner sur "cette personne suivie depuis 3 frames est
Alice" plutôt que sur des visages isolés sans continuité.

Aucune trace de l'embedding calculé n'est conservée après le matching :
seul le résultat (FaceMatch, cf. data_types.py) survit dans le RosterEvent
publié — conformément au critère "zéro donnée persistée sur les inconnus".
"""

import logging
import threading
import time
from typing import Optional

import numpy as np

from argus.client import ArgusClient
from argus.data_types import DetectionEvent

from .config import RosterConfig
from .data_types import FaceMatch, RosterEvent
from .embedder import build_embedder
from .matcher import FaceMatcher
from .metrics import RosterMetrics
from .publisher import RosterPublisher
from .store import PersonStore

logger = logging.getLogger("roster.pipeline")

# Nombre de tentatives et délai entre chaque tentative de connexion à ARGUS : DAEMON marque
# ARGUS "running" dès que le subprocess est lancé, avant même que son publisher ait fini de
# binder son socket TCP — ROSTER doit donc pouvoir patienter quelques instants au démarrage.
_ARGUS_CONNECT_MAX_RETRIES = 20
_ARGUS_CONNECT_RETRY_DELAY_S = 0.5

# Marge ajoutée autour de la bbox "person" avant recadrage : un visage déborde souvent
# légèrement de la boîte englobante détectée par ARGUS (cheveux, menton en bord de boîte).
_CROP_MARGIN_RATIO = 0.15


class RosterEngine:
    """Assemble connexion ARGUS, embedder, matcher, base des personnes et publication en un pipeline unique."""

    def __init__(self, config: RosterConfig) -> None:
        self.config = config

        self.store = PersonStore(config.persons_db_path, config.reference_photos_dir)
        self.embedder = build_embedder(config.embedder)
        self.matcher = FaceMatcher(self.store, config.matcher)
        self.publisher = RosterPublisher(config.publisher.host, config.publisher.port)
        self.metrics = RosterMetrics(log_every_s=config.log_stats_every_s)

        self.argus_client = ArgusClient(config.argus.host, config.argus.port)

        self._stop_event = threading.Event()
        self._main_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        logger.info("RosterEngine : démarrage (backend embedder=%s, %d personne(s) enrôlée(s))",
                    self.config.embedder.backend, len(self.store))
        self.embedder.warmup()
        self.publisher.start()
        self._connect_to_argus_with_retry()

        self._stop_event.clear()
        self._main_thread = threading.Thread(target=self._run_loop, name="roster-main-loop", daemon=True)
        self._main_thread.start()
        logger.info("RosterEngine : démarré")

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        logger.info("RosterEngine : arrêt demandé")
        self._stop_event.set()
        self.argus_client.close()  # débloque events() si le thread principal y est bloqué en lecture
        if self._main_thread is not None:
            self._main_thread.join(timeout=10)
            self._main_thread = None
        self.publisher.stop()
        logger.info("RosterEngine : arrêté proprement")

    def run_forever(self) -> None:
        """Bloque l'appelant jusqu'à `stop()` (utilisé par run_roster.py après réception d'un signal d'arrêt)."""
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
                logger.info("RosterEngine : connecté à ARGUS sur %s:%d (tentative %d)",
                            self.config.argus.host, self.config.argus.port, attempt)
                return
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "RosterEngine : connexion à ARGUS échouée (tentative %d/%d) : %s",
                    attempt, _ARGUS_CONNECT_MAX_RETRIES, exc,
                )
                self._stop_event.wait(_ARGUS_CONNECT_RETRY_DELAY_S)

        raise RuntimeError(
            f"RosterEngine : impossible de se connecter à ARGUS sur "
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
                logger.error("RosterEngine : connexion ARGUS perdue de façon inattendue")

    def _process_event(self, event: DetectionEvent) -> None:
        person_detections = [d for d in event.detections if d.class_name in self.config.person_classes]
        if not person_detections:
            return  # aucune personne dans cette frame : on évite même de décoder l'image

        frame = self.argus_client.read_frame(event)
        if frame is None:
            logger.debug("Frame indisponible pour %s (frame_id=%d), évènement ignoré", event.camera_id, event.frame_id)
            return

        ts_match_start = time.time()
        matches: list[FaceMatch] = []

        for detection in person_detections:
            crop, offset_x, offset_y = self._crop_with_margin(frame, detection.bbox)
            if crop.size == 0:
                continue

            face_results = self.embedder.detect_and_embed(crop)
            if not face_results:
                continue

            # Le plus grand visage détecté dans le recadrage est retenu comme sujet principal.
            face_results.sort(key=lambda r: (r[0][2] - r[0][0]) * (r[0][3] - r[0][1]), reverse=True)
            (fx1, fy1, fx2, fy2), embedding = face_results[0]

            match = self.matcher.match_embedding(embedding)
            match.bbox = (fx1 + offset_x, fy1 + offset_y, fx2 + offset_x, fy2 + offset_y)
            matches.append(match)

        if not matches:
            return  # des personnes ont été détectées par ARGUS, mais aucun visage exploitable

        ts_matched = time.time()
        roster_event = RosterEvent(
            camera_id=event.camera_id,
            frame_id=event.frame_id,
            ts_capture=event.ts_capture,
            ts_matched=ts_matched,
            matches=matches,
        )
        self.publisher.publish(roster_event)

        known_count = sum(1 for m in matches if m.matched)
        self.metrics.record_event(event.camera_id, roster_event.latency_ms, known_count, len(matches) - known_count)
        self.metrics.maybe_log()

        elapsed_ms = (ts_matched - ts_match_start) * 1000.0
        logger.debug(
            "Caméra %s frame=%d : %d visage(s) traité(s) en %.1fms",
            event.camera_id, event.frame_id, len(matches), elapsed_ms,
        )

    @staticmethod
    def _crop_with_margin(frame: np.ndarray, bbox) -> tuple[np.ndarray, int, int]:
        """Recadre `frame` sur `bbox` avec une marge, clippé aux dimensions réelles de l'image."""
        height, width = frame.shape[0], frame.shape[1]
        x1, y1, x2, y2 = bbox
        box_w, box_h = x2 - x1, y2 - y1
        margin_x, margin_y = box_w * _CROP_MARGIN_RATIO, box_h * _CROP_MARGIN_RATIO

        cx1 = max(0, int(x1 - margin_x))
        cy1 = max(0, int(y1 - margin_y))
        cx2 = min(width, int(x2 + margin_x))
        cy2 = min(height, int(y2 + margin_y))

        return frame[cy1:cy2, cx1:cx2], cx1, cy1

    # ------------------------------------------------------------------ #
    # Introspection (utile pour NEXUS-V / diagnostics CLI)
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        return {
            "enrolled_persons": len(self.store),
            "metrics": self.metrics.snapshot(),
            "publisher_clients": self.publisher.client_count,
        }
