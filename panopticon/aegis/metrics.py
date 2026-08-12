"""
panopticon/aegis/metrics.py

Statistiques de performance d'AEGIS : nombre d'observations de posture par
caméra (ventilées upright/lying/uncertain) et nombre de déclenchements
d'alerte par type d'évènement, sur fenêtre glissante. Même structure que
`argus/metrics.py`, `roster/metrics.py`, `spectra/metrics.py`,
`oracle/metrics.py` et `pulse_track/metrics.py`.
"""

import logging
import time
from collections import deque

logger = logging.getLogger("aegis.metrics")


class RollingWindow:
    """Fenêtre glissante bornée en nombre d'échantillons, pour moyenne/percentile bon marché."""

    def __init__(self, maxlen: int = 200) -> None:
        self._values: deque = deque(maxlen=maxlen)

    def add(self, value: float) -> None:
        self._values.append(value)

    @property
    def avg(self) -> float:
        return sum(self._values) / len(self._values) if self._values else 0.0

    @property
    def p95(self) -> float:
        if not self._values:
            return 0.0
        ordered = sorted(self._values)
        idx = max(0, int(len(ordered) * 0.95) - 1)
        return ordered[idx]

    def __len__(self) -> int:
        return len(self._values)


class AegisMetrics:
    """Agrège observations de posture et déclenchements d'alerte, journalisation périodique (pas plus souvent que `log_every_s`)."""

    def __init__(self, log_every_s: float = 10.0) -> None:
        self.log_every_s = log_every_s
        self._latency_ms: dict[str, RollingWindow] = {}          # par event_type
        self._observations_by_posture: dict[str, int] = {}        # "upright"/"lying"/"uncertain" -> total
        self._observations_by_camera: dict[str, int] = {}
        self._triggers_by_type: dict[str, int] = {}                # "fall_confirmed"/"fall_ended" -> total
        self._last_log_ts = time.time()

    def record_observation(self, camera_id: str, posture: str) -> None:
        self._observations_by_posture[posture] = self._observations_by_posture.get(posture, 0) + 1
        self._observations_by_camera[camera_id] = self._observations_by_camera.get(camera_id, 0) + 1

    def record_trigger(self, event_type: str, latency_ms: float) -> None:
        self._triggers_by_type[event_type] = self._triggers_by_type.get(event_type, 0) + 1
        self._latency_ms.setdefault(event_type, RollingWindow()).add(latency_ms)

    def maybe_log(self) -> None:
        now = time.time()
        if now - self._last_log_ts < self.log_every_s:
            return
        self._last_log_ts = now
        total_observations = sum(self._observations_by_posture.values())
        logger.info(
            "AEGIS : %d observation(s) de posture (%s) | déclenchements : %s",
            total_observations,
            ", ".join(f"{p}={n}" for p, n in sorted(self._observations_by_posture.items())) or "aucune",
            ", ".join(f"{t}={n}" for t, n in sorted(self._triggers_by_type.items())) or "aucun",
        )

    def snapshot(self) -> dict:
        return {
            "observations_by_posture": dict(self._observations_by_posture),
            "observations_by_camera": dict(self._observations_by_camera),
            "triggers_by_type": dict(self._triggers_by_type),
            "latency_ms": {
                event_type: {"avg": round(w.avg, 2), "p95": round(w.p95, 2)}
                for event_type, w in self._latency_ms.items()
            },
        }
