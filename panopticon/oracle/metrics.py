"""
panopticon/oracle/metrics.py

Statistiques de performance d'ORACLE : latence bout-en-bout (capture caméra
ARGUS -> évènement ORACLE publié), nombre d'objets traités et taux de
cache-hit (indicateur direct d'économie d'appels API), par caméra, sur
fenêtre glissante. Même structure que `argus/metrics.py`, `roster/metrics.py`
et `spectra/metrics.py`.
"""

import logging
import time
from collections import deque

logger = logging.getLogger("oracle.metrics")


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


class OracleMetrics:
    """Agrège latence, volume d'objets et cache-hit rate par caméra, journalisation périodique."""

    def __init__(self, log_every_s: float = 10.0) -> None:
        self.log_every_s = log_every_s
        self._latency_ms: dict[str, RollingWindow] = {}
        self._event_count: dict[str, int] = {}
        self._objects_identified: dict[str, int] = {}
        self._objects_from_cache: dict[str, int] = {}
        self._objects_unidentified: dict[str, int] = {}
        self._api_calls_skipped_rate_limit: dict[str, int] = {}
        self._last_log_ts = time.time()

    def record_event(self, camera_id: str, latency_ms: float, objects: list) -> None:
        self._latency_ms.setdefault(camera_id, RollingWindow()).add(latency_ms)
        self._event_count[camera_id] = self._event_count.get(camera_id, 0) + 1

        for obj in objects:
            if obj.identification is None:
                self._objects_unidentified[camera_id] = self._objects_unidentified.get(camera_id, 0) + 1
            elif obj.from_cache:
                self._objects_from_cache[camera_id] = self._objects_from_cache.get(camera_id, 0) + 1
            else:
                self._objects_identified[camera_id] = self._objects_identified.get(camera_id, 0) + 1

    def record_rate_limit_skip(self, camera_id: str) -> None:
        self._api_calls_skipped_rate_limit[camera_id] = self._api_calls_skipped_rate_limit.get(camera_id, 0) + 1

    def maybe_log(self) -> None:
        now = time.time()
        if now - self._last_log_ts < self.log_every_s:
            return
        self._last_log_ts = now
        for camera_id in self._event_count:
            latency = self._latency_ms.get(camera_id)
            identified = self._objects_identified.get(camera_id, 0)
            from_cache = self._objects_from_cache.get(camera_id, 0)
            total_hits = identified + from_cache
            cache_hit_rate = (from_cache / total_hits * 100.0) if total_hits else 0.0
            logger.info(
                "Caméra %s : %d évènement(s) ORACLE | identifiés(API)=%d cache=%d (%.0f%% cache-hit) "
                "non-identifiés=%d | latence moy=%.1fms p95=%.1fms",
                camera_id, self._event_count[camera_id], identified, from_cache, cache_hit_rate,
                self._objects_unidentified.get(camera_id, 0),
                latency.avg if latency else 0.0,
                latency.p95 if latency else 0.0,
            )

    def snapshot(self) -> dict[str, dict]:
        result = {}
        for camera_id in self._event_count:
            identified = self._objects_identified.get(camera_id, 0)
            from_cache = self._objects_from_cache.get(camera_id, 0)
            total_hits = identified + from_cache
            result[camera_id] = {
                "events": self._event_count.get(camera_id, 0),
                "objects_identified_api": identified,
                "objects_from_cache": from_cache,
                "objects_unidentified": self._objects_unidentified.get(camera_id, 0),
                "cache_hit_rate_pct": round(from_cache / total_hits * 100.0, 1) if total_hits else 0.0,
                "rate_limit_skips": self._api_calls_skipped_rate_limit.get(camera_id, 0),
                "latency_avg_ms": round(self._latency_ms[camera_id].avg, 2) if camera_id in self._latency_ms else 0.0,
                "latency_p95_ms": round(self._latency_ms[camera_id].p95, 2) if camera_id in self._latency_ms else 0.0,
            }
        return result
