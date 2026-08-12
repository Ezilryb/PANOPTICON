"""
panopticon/aegis/fall_tracker.py

FallStateTracker : machine à états de confirmation de chute, une instance
par piste (camera_id, track_id) — cf. section 5 du brief projet : "chute
verticale rapide + posture horizontale prolongée + absence de mouvement
pendant N secondes", avec un délai de confirmation pour limiter les faux
positifs. Logique pure (aucun réseau, aucun disque), donc testable isolément
comme `pulse_track/rules.py::RuleEngine`, dont ce module reprend l'esprit
général (anti-spam par état plutôt que par cooldown temporel, nettoyage des
pistes disparues) tout en étant spécialisé au domaine de la chute plutôt que
générique/configurable par règles.

ALGORITHME :
  1. Une "chute verticale rapide" est détectée quand le centroïde de la bbox
     descend de plus de `fall_min_vertical_px` en moins de
     `fall_detection_window_s` (cf. _check_fast_fall). Reste "valable"
     pendant `fall_trigger_grace_s`.
  2. Dès que la posture devient "lying", un chronomètre démarre.
  3. La confirmation exige : posture "lying" soutenue pendant au moins
     `fast_confirm_seconds` (si une chute rapide a été observée récemment)
     ou `confirm_seconds` (sinon, cas par défaut plus prudent) ET une
     immobilité du centroïde sur toute cette durée (cf. _is_still). Le
     mouvement ne réinitialise PAS le chronomètre de posture — il retarde
     simplement la confirmation jusqu'à ce que la personne cesse de bouger,
     évitant de repartir de zéro à chaque léger mouvement.
  4. Posture "uncertain" : aucun signal exploitable, ni remise à zéro ni
     avancée d'aucun chronomètre (zone de transition, ex: s'accroupir).

LIMITE HONNÊTE (documentée plutôt que masquée, même culture que
spectra/enhancer.py) :
  - Les seuils de déplacement (`fall_min_vertical_px`/`max_movement_px`)
    sont des pixels IMAGE bruts, non normalisés par la distance
    caméra/personne : une même chute produira un déplacement en pixels très
    différent selon le champ de vision et la distance à la caméra. À
    calibrer par caméra si nécessaire (cf. aegis.example.json).
  - `track_lost_after_s` (défaut généreux, 12s) part du principe qu'ARGUS
    peut lui-même perdre temporairement une personne au sol : les modèles
    de détection généralistes (COCO) sont entraînés très majoritairement
    sur des piétons debout, et la confiance de détection d'ARGUS peut
    chuter précisément au moment où une personne tombe et change
    radicalement de silhouette — l'instant où la fiabilité d'AEGIS importe
    le plus. Une clôture "track_lost" doit donc être vérifiée manuellement
    par l'opérateur, jamais interprétée comme "tout va bien".
  - AEGIS ne distingue pas une chute réelle d'une personne allongée
    calmement (canapé, tapis de sport...) par la seule vision : c'est un
    signal d'aide à la surveillance, pas un dispositif médical certifié. Cf.
    `monitored_camera_ids` (config.py) pour exclure les caméras où
    "allongé" est un état normal (chambre, salon...).
"""

import logging
import math
import threading
import time
from collections import deque
from typing import Optional

from .config import FallDetectionConfig
from .data_types import AegisEvent, PostureResult

logger = logging.getLogger("aegis.fall_tracker")

_TrackKey = tuple[str, int]  # (camera_id, track_id)


class FallStateTracker:
    """État de confirmation de chute par piste. Thread-safe (RLock) — la pipeline
    n'utilise qu'un seul thread ARGUS aujourd'hui, mais un verrou reste peu coûteux
    et évite une régression silencieuse si un futur appelant devient concurrent
    (même précaution que roster/store.py::PersonStore et pulse_track/rules.py)."""

    def __init__(self, config: FallDetectionConfig) -> None:
        self.config = config
        self._lock = threading.RLock()

        self._centroid_history: dict[_TrackKey, deque] = {}
        self._lying_since: dict[_TrackKey, float] = {}
        self._upright_since: dict[_TrackKey, float] = {}
        self._fall_trigger_ts: dict[_TrackKey, float] = {}
        self._confirmed: dict[_TrackKey, dict] = {}
        self._last_seen: dict[_TrackKey, float] = {}
        self._last_frame_id: dict[_TrackKey, int] = {}

    # ------------------------------------------------------------------ #
    # Points d'entrée
    # ------------------------------------------------------------------ #

    def update(
        self, camera_id: str, track_id: int, frame_id: int,
        result: PostureResult, cx: float, cy: float, now: Optional[float] = None,
    ) -> Optional[AegisEvent]:
        """Met à jour l'état de la piste (camera_id, track_id) et renvoie un AegisEvent si un changement d'état vient d'avoir lieu, sinon None."""
        now = now if now is not None else time.time()
        key = (camera_id, track_id)

        with self._lock:
            self._last_seen[key] = now
            self._last_frame_id[key] = frame_id
            self._update_centroid_history(key, now, cx, cy)
            fast_fall_recent = self._check_fast_fall(key, now)

            if result.posture == "lying":
                return self._handle_lying(key, camera_id, track_id, frame_id, result, now, fast_fall_recent)
            if result.posture == "upright":
                return self._handle_upright(key, camera_id, track_id, frame_id, result, now)
            return None  # "uncertain" : aucun signal exploitable (cf. docstring du module)

    def prune_stale(self, camera_id: str, seen_track_ids: set[int], now: Optional[float] = None) -> list[AegisEvent]:
        """
        À appeler une fois par DetectionEvent traité pour `camera_id`, avec
        l'ensemble des track_id de classe "person" présents dans CET
        évènement (indépendamment de leur confiance ou de leur posture, cf.
        pipeline.py) : ferme (avec `end_reason="track_lost"`) toute piste
        CONFIRMÉE dont ARGUS n'a plus signalé la présence depuis
        `track_lost_after_s`, et oublie son état.
        """
        now = now if now is not None else time.time()
        with self._lock:
            stale_keys = [
                key for key in list(self._last_seen)
                if key[0] == camera_id and key[1] not in seen_track_ids
                and (now - self._last_seen[key]) > self.config.track_lost_after_s
            ]

            events: list[AegisEvent] = []
            for key in stale_keys:
                _cam, track_id = key
                frame_id = self._last_frame_id.get(key, -1)
                state = self._confirmed.pop(key, None)
                self._forget(key)
                if state is not None:
                    duration = now - state["fall_started_at"]
                    logger.warning(
                        "AEGIS : piste %d perdue (caméra %s) pendant une alerte active — "
                        "clôture 'track_lost', vérification manuelle recommandée", track_id, camera_id,
                    )
                    events.append(AegisEvent(
                        event_type="fall_ended", camera_id=camera_id, track_id=track_id,
                        frame_id=frame_id, ts_triggered=now, fall_started_at=state["fall_started_at"],
                        posture=state["last_result"], fast_fall_observed=state["fast_fall_observed"],
                        end_reason="track_lost", duration_s=duration,
                    ))
            return events

    def active_alert_count(self) -> int:
        with self._lock:
            return len(self._confirmed)

    # ------------------------------------------------------------------ #
    # Gestion des deux postures qui font avancer un chronomètre (appelées sous verrou uniquement)
    # ------------------------------------------------------------------ #

    def _handle_lying(
        self, key: _TrackKey, camera_id: str, track_id: int, frame_id: int,
        result: PostureResult, now: float, fast_fall_recent: bool,
    ) -> Optional[AegisEvent]:
        self._upright_since.pop(key, None)  # tout retour à "upright" en cours est annulé par une rechute en "lying"

        if key not in self._lying_since:
            self._lying_since[key] = now
        lying_duration = now - self._lying_since[key]
        required_duration = self.config.fast_confirm_seconds if fast_fall_recent else self.config.confirm_seconds

        state = self._confirmed.get(key)
        if state is not None:
            state["last_result"] = result
            return None  # déjà confirmée : rien de nouveau à publier tant qu'elle reste "lying"

        if lying_duration >= required_duration and self._is_still(key, now, required_duration):
            self._confirmed[key] = {
                "fall_started_at": self._lying_since[key],
                "last_result": result,
                "fast_fall_observed": fast_fall_recent,
            }
            return AegisEvent(
                event_type="fall_confirmed", camera_id=camera_id, track_id=track_id,
                frame_id=frame_id, ts_triggered=now, fall_started_at=self._lying_since[key],
                posture=result, fast_fall_observed=fast_fall_recent,
            )
        return None

    def _handle_upright(
        self, key: _TrackKey, camera_id: str, track_id: int, frame_id: int,
        result: PostureResult, now: float,
    ) -> Optional[AegisEvent]:
        self._lying_since.pop(key, None)  # toute accumulation "lying" non confirmée repart de zéro

        state = self._confirmed.get(key)
        if state is None:
            return None  # jamais confirmée : un simple retour à "upright" est silencieux

        upright_since = self._upright_since.setdefault(key, now)
        if (now - upright_since) < self.config.recovery_confirm_seconds:
            return None  # encore trop tôt pour clore — évite qu'un bref sursaut ne mette fin à une vraie alerte

        del self._confirmed[key]
        self._upright_since.pop(key, None)
        duration = now - state["fall_started_at"]
        return AegisEvent(
            event_type="fall_ended", camera_id=camera_id, track_id=track_id,
            frame_id=frame_id, ts_triggered=now, fall_started_at=state["fall_started_at"],
            posture=result, fast_fall_observed=state["fast_fall_observed"],
            end_reason="posture_recovered", duration_s=duration,
        )

    # ------------------------------------------------------------------ #
    # Signaux de mouvement (appelées sous verrou uniquement)
    # ------------------------------------------------------------------ #

    def _update_centroid_history(self, key: _TrackKey, now: float, cx: float, cy: float) -> None:
        history = self._centroid_history.setdefault(key, deque())
        history.append((now, cx, cy))
        window = self.config.motion_window_s
        while history and (now - history[0][0]) > window:
            history.popleft()

    def _check_fast_fall(self, key: _TrackKey, now: float) -> bool:
        """
        Évalue le déplacement vertical UNIQUEMENT sur la fenêtre COURTE
        `fall_detection_window_s` (sous-ensemble de l'historique complet) —
        volontairement DISTINCTE de la fenêtre utilisée par `_is_still()`.
        Les deux ne peuvent pas partager la même fenêtre : le déplacement
        de la chute elle-même est précisément ce que `_is_still()` doit
        ignorer une fois la personne au sol, sous peine que la chute
        bloque elle-même sa propre confirmation. Renvoie True et
        rafraîchit `_fall_trigger_ts` si le déplacement vertical descendant
        dépasse `fall_min_vertical_px` sur cette fenêtre courte.
        """
        history = self._centroid_history.get(key)
        if history:
            window_s = self.config.fall_detection_window_s
            recent = [sample for sample in history if now - sample[0] <= window_s]
            if len(recent) >= 2:
                _ts_oldest, _cx_oldest, cy_oldest = recent[0]
                _ts_newest, _cx_newest, cy_newest = recent[-1]
                vertical_drop = cy_newest - cy_oldest  # positif = descente (repère image, y croît vers le bas)
                if vertical_drop >= self.config.fall_min_vertical_px:
                    self._fall_trigger_ts[key] = now

        return self._is_fall_trigger_still_valid(key, now)

    def _is_fall_trigger_still_valid(self, key: _TrackKey, now: float) -> bool:
        trigger_ts = self._fall_trigger_ts.get(key)
        return trigger_ts is not None and (now - trigger_ts) <= self.config.fall_trigger_grace_s

    def _is_still(self, key: _TrackKey, now: float, required_duration: float) -> bool:
        """
        Mesure la dispersion des positions sur la fenêtre GLISSANTE des
        `required_duration` dernières secondes (pas depuis le tout début de
        la posture "lying", qui peut elle-même contenir la fin du mouvement
        de chute) : c'est cette fenêtre glissante, remise à jour à chaque
        appel, qui permet à la confirmation de finir par passer une fois la
        personne réellement stabilisée, même si l'échantillon "lying" le
        plus ancien encore en mémoire correspondait à un instant où elle
        bougeait encore (cf. docstring de `_check_fast_fall()` pour le
        même principe appliqué à la détection de la chute elle-même).
        """
        history = self._centroid_history.get(key)
        if not history:
            return True

        window_start = now - required_duration
        relevant = [sample for sample in history if sample[0] >= window_start]
        if len(relevant) < 2:
            return True  # pas assez d'échantillons SUR CETTE fenêtre -> ne bloque pas la confirmation

        mean_x = sum(cx for _ts, cx, _cy in relevant) / len(relevant)
        mean_y = sum(cy for _ts, _cx, cy in relevant) / len(relevant)
        max_dist = max(math.hypot(cx - mean_x, cy - mean_y) for _ts, cx, cy in relevant)
        return max_dist <= self.config.max_movement_px

    def _forget(self, key: _TrackKey) -> None:
        self._lying_since.pop(key, None)
        self._upright_since.pop(key, None)
        self._fall_trigger_ts.pop(key, None)
        self._centroid_history.pop(key, None)
        self._last_seen.pop(key, None)
        self._last_frame_id.pop(key, None)
