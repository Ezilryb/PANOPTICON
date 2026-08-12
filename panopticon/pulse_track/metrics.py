"""
panopticon/pulse_track/metrics.py

Statistiques de PULSE_TRACK : nombre de déclenchements par règle/sévérité,
latence (déclenchement -> publication), et débit des flux ARGUS/ROSTER
effectivement consommés (utile pour distinguer "aucune règle ne se
déclenche" de "PULSE_TRACK ne reçoit plus aucun évènement"). Même structure
que argus/metrics.py, roster/metrics.py, spectra/metrics.py et oracle/metrics.py,
adaptée à un moteur de règles (pas de FPS/latence par caméra ici).
"""

import logging
import time
from collections import deque

logger = logging.getLogger("pulse_track.metrics")


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


class PulseTrackMetrics:
    """Agrège déclenchements par règle et débit des flux consommés, journalisation périodique."""

    def __init__(self, log_every_s: float = 10.0) -> None:
        self.log_every_s = log_every_s
        self._latency_ms: dict[str, RollingWindow] = {}          # par rule_id
        self._trigger_count: dict[str, int] = {}                 # par rule_id
        self._trigger_count_by_severity: dict[str, int] = {}     # par sévérité
        self._detection_events_seen = 0
        self._roster_events_seen = 0
        self._last_log_ts = time.time()

    def record_trigger(self, rule_id: str, severity: str, latency_ms: float) -> None:
        self._latency_ms.setdefault(rule_id, RollingWindow()).add(latency_ms)
        self._trigger_count[rule_id] = self._trigger_count.get(rule_id, 0) + 1
        self._trigger_count_by_severity[severity] = self._trigger_count_by_severity.get(severity, 0) + 1

    def record_detection_event_seen(self) -> None:
        self._detection_events_seen += 1

    def record_roster_event_seen(self) -> None:
        self._roster_events_seen += 1

    def maybe_log(self) -> None:
        now = time.time()
        if now - self._last_log_ts < self.log_every_s:
            return
        self._last_log_ts = now
        total_triggers = sum(self._trigger_count.values())
        logger.info(
            "PULSE_TRACK : %d évènement(s) ARGUS / %d évènement(s) ROSTER consommé(s) | "
            "%d déclenchement(s) au total (%s)",
            self._detection_events_seen, self._roster_events_seen, total_triggers,
            ", ".join(f"{sev}={n}" for sev, n in sorted(self._trigger_count_by_severity.items())) or "aucun",
        )
        for rule_id, count in self._trigger_count.items():
            latency = self._latency_ms.get(rule_id)
            logger.info(
                "  règle %s : %d déclenchement(s) | latence moy=%.1fms p95=%.1fms",
                rule_id, count, latency.avg if latency else 0.0, latency.p95 if latency else 0.0,
            )

    def snapshot(self) -> dict:
        return {
            "detection_events_seen": self._detection_events_seen,
            "roster_events_seen": self._roster_events_seen,
            "triggers_by_severity": dict(self._trigger_count_by_severity),
            "rules": {
                rule_id: {
                    "triggers": count,
                    "latency_avg_ms": round(self._latency_ms[rule_id].avg, 2) if rule_id in self._latency_ms else 0.0,
                    "latency_p95_ms": round(self._latency_ms[rule_id].p95, 2) if rule_id in self._latency_ms else 0.0,
                }
                for rule_id, count in self._trigger_count.items()
            },
        }