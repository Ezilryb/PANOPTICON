"""
panopticon/roster/metrics.py

Statistiques de performance de ROSTER : latence bout-en-bout (capture
caméra ARGUS -> évènement ROSTER publié) et nombre de visages traités par
caméra, sur fenêtre glissante. Même structure que `argus/metrics.py`.
"""

import logging
import time
from collections import deque

logger = logging.getLogger("roster.metrics")


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


class RosterMetrics:
    """Agrège latence et débit par caméra, avec journalisation périodique (pas plus souvent que `log_every_s`)."""

    def __init__(self, log_every_s: float = 10.0) -> None:
        self.log_every_s = log_every_s
        self._latency_ms: dict[str, RollingWindow] = {}
        self._event_count: dict[str, int] = {}
        self._known_count: dict[str, int] = {}
        self._unknown_count: dict[str, int] = {}
        self._last_log_ts = time.time()

    def record_event(self, camera_id: str, latency_ms: float, known_count: int, unknown_count: int) -> None:
        self._latency_ms.setdefault(camera_id, RollingWindow()).add(latency_ms)
        self._event_count[camera_id] = self._event_count.get(camera_id, 0) + 1
        self._known_count[camera_id] = self._known_count.get(camera_id, 0) + known_count
        self._unknown_count[camera_id] = self._unknown_count.get(camera_id, 0) + unknown_count

    def maybe_log(self) -> None:
        now = time.time()
        if now - self._last_log_ts < self.log_every_s:
            return
        self._last_log_ts = now
        for camera_id in self._event_count:
            latency = self._latency_ms.get(camera_id)
            logger.info(
                "Caméra %s : %d évènement(s) ROSTER | connus=%d inconnus=%d | latence moy=%.1fms p95=%.1fms",
                camera_id,
                self._event_count[camera_id],
                self._known_count.get(camera_id, 0),
                self._unknown_count.get(camera_id, 0),
                latency.avg if latency else 0.0,
                latency.p95 if latency else 0.0,
            )

    def snapshot(self) -> dict[str, dict]:
        return {
            camera_id: {
                "events": self._event_count.get(camera_id, 0),
                "known": self._known_count.get(camera_id, 0),
                "unknown": self._unknown_count.get(camera_id, 0),
                "latency_avg_ms": round(self._latency_ms[camera_id].avg, 2) if camera_id in self._latency_ms else 0.0,
                "latency_p95_ms": round(self._latency_ms[camera_id].p95, 2) if camera_id in self._latency_ms else 0.0,
            }
            for camera_id in self._event_count
        }
