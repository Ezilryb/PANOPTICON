"""Point d'écriture partagé des événements de détection (SQLite + fichier JSONL).

Utilisé par tous les modules qui produisent des ``DetectionEvent`` (ARGUS,
SPECTRA…) afin qu'ils apparaissent de façon unifiée dans l'API, la CLI
(``events``/``syslog``/``monitor``) et NEXUS-V, quel que soit le module
d'origine. Fonctionne en accès direct SQLite (synchrone) car ces modules
tournent dans des sous-processus séparés du processus API asynchrone.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from shared.config import settings
from shared.models import DetectionEvent

STATE_DIR = Path("./data/argus")


def _db_path() -> str:
    url = settings.database_url
    if url.startswith("sqlite"):
        return url.split("///")[-1]
    return "./data/panopticon.db"


def save_event(event: DetectionEvent) -> None:
    conn = sqlite3.connect(_db_path())
    try:
        conn.execute(
            """
            INSERT INTO events (id, camera_id, source_module, event_type, zone, timestamp, thumbnail_path, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.id),
                str(event.camera_id),
                event.source_module,
                event.event_type,
                event.zone,
                event.timestamp.isoformat(),
                event.thumbnail_path,
                json.dumps(event.metadata),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def publish_event_file(event: DetectionEvent) -> None:
    events_file = STATE_DIR / "events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    with events_file.open("a", encoding="utf-8") as f:
        f.write(event.model_dump_json() + "\n")


def emit(event: DetectionEvent) -> None:
    """Persiste l'événement (SQLite) et le publie pour le flux temps réel (JSONL -> /ws/live)."""
    save_event(event)
    publish_event_file(event)
