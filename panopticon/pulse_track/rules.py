"""
panopticon/pulse_track/rules.py

RuleEngine : évalue les évènements ARGUS/ROSTER contre les règles
configurées, gère l'anti-spam (cooldown) et le suivi de présence pour
"track_dwell" — logique pure, sans réseau ni disque, donc testable isolément.

THREAD-SAFETY : pipeline.py fait tourner un thread dédié par flux source
(ARGUS et ROSTER, cf. PulseTrackEngine), tous deux appelant CETTE MÊME
instance de RuleEngine concurremment. `_last_triggered` (cooldown) est
partagé entre `evaluate_detection_event()` et `evaluate_roster_event()` :
sans verrou, une correspondance simultanée sur les deux flux pourrait
enfreindre le cooldown d'une règle (lecture-puis-écriture non atomique). Un
simple RLock protège donc tout l'état interne, même principe que
`roster/store.py::PersonStore`.
"""

import logging
import threading
import time
from typing import Optional

from argus.data_types import DetectionEvent
from roster.data_types import RosterEvent

from .config import PulseTrackConfig, RuleCondition, RuleConfig
from .data_types import PulseTrackEvent

logger = logging.getLogger("pulse_track.rules")

# Au-delà de ce délai sans réapparaître dans un DetectionEvent, une piste est
# considérée perdue et son ancienneté oubliée — sinon un nouvel objet qui
# hériterait un jour du même track_id (après suppression côté IouTracker, cf.
# argus/tracker.py::test_new_object_never_reuses_a_pruned_id) se verrait à
# tort attribuer l'ancienneté de la piste précédente. PULSE_TRACK doit s'en
# prémunir lui-même : il ne voit que le flux d'évènements, pas l'état interne
# du tracker d'ARGUS.
_TRACK_STALE_AFTER_S = 5.0


class RuleEngine:
    """Évalue les règles configurées et produit des PulseTrackEvent, avec anti-spam et suivi de présence intégrés."""

    def __init__(self, config: PulseTrackConfig) -> None:
        self.config = config
        self._rules = [r for r in config.rules if r.enabled]
        if not self._rules:
            logger.warning("Aucune règle active : PULSE_TRACK ne déclenchera jamais rien tant que la configuration n'est pas complétée")

        self._lock = threading.RLock()

        # (rule_id, clé_contexte) -> dernier ts de déclenchement, pour le cooldown. La clé de
        # contexte inclut toujours camera_id (cf. _eval_*) pour ne jamais mélanger deux caméras.
        self._last_triggered: dict[tuple[str, str], float] = {}
        # (camera_id, track_id) -> ts de première/dernière apparition connue, pour "track_dwell".
        self._track_first_seen: dict[tuple[str, int], float] = {}
        self._track_last_seen: dict[tuple[str, int], float] = {}

    # ------------------------------------------------------------------ #
    # Points d'entrée (un par flux source) — chacun verrouille l'état
    # interne pour toute sa durée, cf. note THREAD-SAFETY en tête de fichier.
    # ------------------------------------------------------------------ #

    def evaluate_detection_event(self, event: DetectionEvent) -> list[PulseTrackEvent]:
        """Évalue les règles "object_class" et "track_dwell" contre un DetectionEvent ARGUS."""
        with self._lock:
            now = time.time()
            self._update_track_presence(event, now)

            triggered: list[PulseTrackEvent] = []
            for rule in self._rules:
                trigger = rule.condition.trigger
                if trigger == "object_class":
                    triggered.extend(self._eval_object_class(rule, event, now))
                elif trigger == "track_dwell":
                    triggered.extend(self._eval_track_dwell(rule, event, now))
            return triggered

    def evaluate_roster_event(self, event: RosterEvent) -> list[PulseTrackEvent]:
        """Évalue les règles "known_person" et "unknown_person" contre un RosterEvent."""
        with self._lock:
            now = time.time()
            triggered: list[PulseTrackEvent] = []
            for rule in self._rules:
                if rule.condition.trigger in ("known_person", "unknown_person"):
                    triggered.extend(self._eval_person(rule, event, now))
            return triggered

    # ------------------------------------------------------------------ #
    # Évaluation par type de déclencheur (appelées sous verrou uniquement)
    # ------------------------------------------------------------------ #

    def _eval_object_class(self, rule: RuleConfig, event: DetectionEvent, now: float) -> list[PulseTrackEvent]:
        cond = rule.condition
        if not self._camera_allowed(cond, event.camera_id) or not self._within_hours(cond, now):
            return []

        results = []
        for det in event.detections:
            if det.class_name not in cond.object_classes or det.confidence < cond.min_confidence:
                continue
            suffix = f"track:{det.track_id}" if det.track_id is not None else f"frame:{event.frame_id}"
            key = f"{event.camera_id}:{suffix}"
            if not self._cooldown_ok(rule, key, now):
                continue
            results.append(self._make_event(rule, event.camera_id, event.frame_id, now,
                                              track_id=det.track_id, object_class=det.class_name))
        return results

    def _eval_track_dwell(self, rule: RuleConfig, event: DetectionEvent, now: float) -> list[PulseTrackEvent]:
        cond = rule.condition
        if not self._camera_allowed(cond, event.camera_id) or not self._within_hours(cond, now):
            return []

        results = []
        for det in event.detections:
            if det.track_id is None or det.confidence < cond.min_confidence:
                continue
            if cond.object_classes and det.class_name not in cond.object_classes:  # vide = n'importe quelle classe
                continue
            first_seen = self._track_first_seen.get((event.camera_id, det.track_id))
            if first_seen is None or (now - first_seen) < cond.dwell_seconds:
                continue
            key = f"{event.camera_id}:track:{det.track_id}"
            if not self._cooldown_ok(rule, key, now):
                continue
            results.append(self._make_event(rule, event.camera_id, event.frame_id, now,
                                              track_id=det.track_id, object_class=det.class_name))
        return results

    def _eval_person(self, rule: RuleConfig, event: RosterEvent, now: float) -> list[PulseTrackEvent]:
        cond = rule.condition
        if not self._camera_allowed(cond, event.camera_id) or not self._within_hours(cond, now):
            return []

        results = []
        for match in event.matches:
            if cond.trigger == "known_person":
                if not match.matched:
                    continue
                if cond.person_names and match.name not in cond.person_names:
                    continue
                key_suffix = f"person:{match.name}"
            else:  # "unknown_person"
                if match.matched:
                    continue
                # Aucun identifiant persistant pour un inconnu (ROSTER ne renvoie pas le
                # track_id ARGUS d'origine sur un FaceMatch) : le cooldown s'applique donc
                # par caméra, pas par individu — un second inconnu sur la même caméra dans
                # la fenêtre de cooldown ne redéclenche pas la règle. Documenté plutôt que masqué.
                key_suffix = "person:unknown"

            key = f"{event.camera_id}:{key_suffix}"
            if not self._cooldown_ok(rule, key, now):
                continue
            results.append(self._make_event(rule, event.camera_id, event.frame_id, now,
                                              person_name=match.name if match.matched else None))
        return results

    # ------------------------------------------------------------------ #
    # Aides internes (appelées sous verrou uniquement)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _camera_allowed(cond: RuleCondition, camera_id: str) -> bool:
        return not cond.camera_ids or camera_id in cond.camera_ids

    @staticmethod
    def _within_hours(cond: RuleCondition, now: float) -> bool:
        """Vérifie la plage horaire optionnelle de la règle (heure locale). Gère le passage de minuit (ex: "22:00" -> "06:00")."""
        if cond.hours_start is None or cond.hours_end is None:
            return True
        current = time.strftime("%H:%M", time.localtime(now))
        start, end = cond.hours_start, cond.hours_end
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end  # plage à cheval sur minuit

    def _cooldown_ok(self, rule: RuleConfig, context_key: str, now: float) -> bool:
        key = (rule.rule_id, context_key)
        last = self._last_triggered.get(key)
        if last is not None and (now - last) < rule.cooldown_s:
            return False
        self._last_triggered[key] = now
        return True

    def _make_event(
        self, rule: RuleConfig, camera_id: str, frame_id: int, now: float,
        track_id: Optional[int] = None, person_name: Optional[str] = None, object_class: Optional[str] = None,
    ) -> PulseTrackEvent:
        message = rule.message_template.format(rule_name=rule.name, camera_id=camera_id)
        logger.info("Règle déclenchée : %s (%s) sur %s", rule.name, rule.rule_id, camera_id)
        return PulseTrackEvent(
            rule_id=rule.rule_id, rule_name=rule.name, severity=rule.severity,
            message=message, camera_id=camera_id, frame_id=frame_id, ts_triggered=now,
            track_id=track_id, person_name=person_name, object_class=object_class,
        )

    def _update_track_presence(self, event: DetectionEvent, now: float) -> None:
        seen_this_frame = set()
        for det in event.detections:
            if det.track_id is None:
                continue
            key = (event.camera_id, det.track_id)
            seen_this_frame.add(key)
            self._track_first_seen.setdefault(key, now)
            self._track_last_seen[key] = now

        # N'oublie que les pistes DE CETTE CAMÉRA pour rester bon marché (une caméra à fort
        # trafic ne doit pas payer le coût des pistes des autres caméras à chaque frame).
        stale = [
            key for key in self._track_last_seen
            if key[0] == event.camera_id and key not in seen_this_frame
            and (now - self._track_last_seen[key]) > _TRACK_STALE_AFTER_S
        ]
        for key in stale:
            del self._track_first_seen[key]
            del self._track_last_seen[key]