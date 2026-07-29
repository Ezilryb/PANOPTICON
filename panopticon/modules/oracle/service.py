"""Service ORACLE — identification fine des objets détectés par ARGUS, en local.

Ne se connecte à aucune caméra : surveille le flux d'événements déjà produit
par ARGUS (``data/argus/events.jsonl``), et pour chaque objet détecté
(hors "person"), relit sa miniature déjà sauvegardée et la fait classifier
localement, puis publie un événement ``object_identified`` enrichi.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from uuid import UUID

import cv2

from modules.event_sink import emit
from modules.oracle.object_identifier import EXCLUDED_LABELS, ObjectIdentifier
from shared.models import DetectionEvent

logger = logging.getLogger(__name__)

EVENTS_FILE = Path("./data/argus/events.jsonl")
POLL_INTERVAL = 1.0


def _maybe_identify(raw: dict, identifier: ObjectIdentifier) -> None:
    if raw.get("event_type") != "object_appeared":
        return

    metadata = raw.get("metadata") or {}
    label = metadata.get("label")
    if not label or label in EXCLUDED_LABELS:
        return  # jamais sur les personnes

    thumbnail_path = raw.get("thumbnail_path")
    if not thumbnail_path:
        return

    frame = cv2.imread(thumbnail_path)
    if frame is None:
        return

    try:
        results = identifier.identify(frame)
    except Exception:
        logger.exception("Échec d'identification ORACLE pour %s", thumbnail_path)
        return
    if not results:
        return

    best = results[0]
    try:
        camera_id = UUID(raw["camera_id"])
    except (KeyError, ValueError, TypeError):
        return

    event = DetectionEvent(
        camera_id=camera_id,
        source_module="oracle",
        event_type="object_identified",
        zone=raw.get("zone", "default"),
        thumbnail_path=thumbnail_path,
        metadata={
            "argus_label": label,
            "refined_label": best.label,
            "confidence": round(best.confidence, 3),
            "track_id": metadata.get("track_id"),
        },
    )
    try:
        emit(event)
    except Exception:
        logger.exception("Échec de publication d'événement ORACLE pour %s", thumbnail_path)
        return
    logger.info("Identification affinée: %s -> %s (%.0f%%)", label, best.label, best.confidence * 100)


def run_oracle() -> None:
    from shared.config import settings
    from shared.logging_utils import setup_logging

    setup_logging(settings.log_level)
    logger.info("ORACLE actif — identification locale (aucun appel réseau à l'exécution)")

    identifier = ObjectIdentifier()

    offset = 0
    if EVENTS_FILE.exists():
        offset = EVENTS_FILE.stat().st_size  # ne traite que les événements à venir, pas l'historique

    while True:
        if EVENTS_FILE.exists():
            with EVENTS_FILE.open("r", encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    _maybe_identify(raw, identifier)
                offset = f.tell()
        time.sleep(POLL_INTERVAL)
