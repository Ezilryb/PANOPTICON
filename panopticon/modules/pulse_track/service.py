"""Service PULSE_TRACK — évalue les règles sur le flux d'événements et déclenche les actions.

Surveille le même flux d'événements (``data/argus/events.jsonl``) que
SYS-LOG/ORACLE/ROSTER, sans se connecter à aucune caméra. Pour chaque
nouvel événement, évalue toutes les règles activées ; en cas de
correspondance, déclenche l'action configurée (webhook/email/push) et
enregistre systématiquement une alerte (que l'envoi ait réussi ou non).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from modules.pulse_track import notifier
from modules.pulse_track.rules_evaluator import rule_matches

logger = logging.getLogger(__name__)

EVENTS_FILE = Path("./data/argus/events.jsonl")
POLL_INTERVAL = 1.0
RULES_CACHE_REFRESH_EVERY = 10


def _db_path() -> str:
    from shared.config import settings

    url = settings.database_url
    if url.startswith("sqlite"):
        return url.split("///")[-1]
    return "./data/panopticon.db"


def _load_rules() -> list[dict]:
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM rules WHERE enabled = 1").fetchall()
        conn.close()
    except Exception:
        logger.exception("Impossible de charger les règles PULSE_TRACK")
        return []

    rules = []
    for r in rows:
        d = dict(r)
        try:
            d["conditions"] = json.loads(d.get("conditions_json") or "{}")
        except json.JSONDecodeError:
            d["conditions"] = {}
        rules.append(d)
    return rules


def _record_alert(rule_id: str, payload: dict) -> None:
    try:
        conn = sqlite3.connect(_db_path())
        conn.execute(
            "INSERT INTO alerts (id, rule_id, triggered_at, payload_json, acknowledged) VALUES (?, ?, ?, ?, 0)",
            (str(uuid4()), rule_id, datetime.utcnow().isoformat(), json.dumps(payload, default=str)),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("Impossible d'enregistrer l'alerte PULSE_TRACK pour la règle %s", rule_id)


def _trigger(rule: dict, event: dict) -> None:
    action = rule["action"]
    target = rule["action_target"]
    payload = {"rule_name": rule["name"], "event": event}

    try:
        if action == "webhook":
            notifier.send_webhook(target, payload)
        elif action == "email":
            smtp_config = json.loads(target)
            notifier.send_email(
                smtp_config,
                smtp_config["to"],
                f"PANOPTICON — {rule['name']}",
                json.dumps(event, indent=2, default=str),
            )
        elif action == "push":
            notifier.send_local_notification(f"PANOPTICON — {rule['name']}", event.get("event_type", ""))
        else:
            logger.warning("Action inconnue pour la règle '%s': %s", rule["name"], action)
        logger.info("Règle '%s' déclenchée (action=%s)", rule["name"], action)
    except notifier.NotificationError:
        logger.exception("Échec de notification pour la règle '%s'", rule["name"])
    except Exception:
        logger.exception("Erreur inattendue en déclenchant la règle '%s'", rule["name"])
    finally:
        _record_alert(rule["id"], payload)


def run_pulse_track() -> None:
    from shared.config import settings
    from shared.logging_utils import setup_logging

    setup_logging(settings.log_level)
    logger.info("PULSE_TRACK actif — évaluation des règles en local")

    rules = _load_rules()
    iterations = 0

    offset = 0
    if EVENTS_FILE.exists():
        offset = EVENTS_FILE.stat().st_size

    while True:
        iterations += 1
        if iterations % RULES_CACHE_REFRESH_EVERY == 0:
            rules = _load_rules()

        if EVENTS_FILE.exists() and rules:
            with EVENTS_FILE.open("r", encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for rule in rules:
                        if rule_matches(rule["conditions"], event):
                            _trigger(rule, event)
                offset = f.tell()
        elif EVENTS_FILE.exists():
            # Pas de règle active : avance quand même le curseur pour ne pas
            # accumuler un traitement rétroactif massif si des règles sont
            # ajoutées plus tard.
            offset = EVENTS_FILE.stat().st_size

        time.sleep(POLL_INTERVAL)
