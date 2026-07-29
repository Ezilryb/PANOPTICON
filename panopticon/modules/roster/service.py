"""Service ROSTER — reconnaissance des personnes enrôlées, en local.

Ne se connecte à aucune caméra : surveille le flux d'événements déjà produit
par ARGUS pour les entrées de personnes (``person_entered_zone`` — jamais
``object_appeared``, structurellement distinct dès la détection ARGUS), relit
la miniature déjà sauvegardée, et compare son empreinte faciale à celles des
personnes enrôlées. N'émet un événement que si une correspondance suffisante
est trouvée : en son absence, aucune tentative d'identification n'est faite.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from uuid import UUID

import cv2

from modules.event_sink import emit
from modules.roster.face_engine import DEFAULT_MATCH_THRESHOLD, FaceEngine, find_best_match
from shared.models import DetectionEvent

logger = logging.getLogger(__name__)

EVENTS_FILE = Path("./data/argus/events.jsonl")
POLL_INTERVAL = 1.0
ENROLLED_CACHE_REFRESH_EVERY = 15  # ~15s à 1s/itération


def _db_path() -> str:
    from shared.config import settings

    url = settings.database_url
    if url.startswith("sqlite"):
        return url.split("///")[-1]
    return "./data/panopticon.db"


def _load_enrolled() -> dict[str, tuple[str, list[float]]]:
    """Charge {person_id: (name, embedding)} pour toutes les personnes enrôlées."""
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, name, face_embedding_json FROM enrolled_persons").fetchall()
        conn.close()
        result = {}
        for r in rows:
            try:
                embedding = json.loads(r["face_embedding_json"]) if r["face_embedding_json"] else []
            except json.JSONDecodeError:
                embedding = []
            if embedding:
                result[r["id"]] = (r["name"], embedding)
        return result
    except Exception:
        logger.exception("Impossible de charger les personnes enrôlées (ROSTER)")
        return {}


def _maybe_recognize(raw: dict, engine: FaceEngine, enrolled: dict[str, tuple[str, list[float]]]) -> None:
    if raw.get("event_type") != "person_entered_zone":
        return  # ROSTER ne traite jamais autre chose qu'une entrée de personne

    thumbnail_path = raw.get("thumbnail_path")
    if not thumbnail_path or not enrolled:
        return

    frame = cv2.imread(thumbnail_path)
    if frame is None:
        return

    try:
        face = engine.extract_embedding(frame)
    except Exception:
        logger.exception("Échec d'extraction d'empreinte ROSTER pour %s", thumbnail_path)
        return
    if face is None:
        return  # aucun visage exploitable dans la miniature

    candidates = [(pid, emb) for pid, (_name, emb) in enrolled.items()]
    match = find_best_match(face.vector, candidates, threshold=DEFAULT_MATCH_THRESHOLD)
    if match is None:
        return  # pas de correspondance suffisante -> aucune tentative d'identification

    person_id, score = match
    name = enrolled[person_id][0]

    try:
        camera_id = UUID(raw["camera_id"])
    except (KeyError, ValueError, TypeError):
        return

    event = DetectionEvent(
        camera_id=camera_id,
        source_module="roster",
        event_type="person_recognized",
        zone=raw.get("zone", "default"),
        thumbnail_path=thumbnail_path,
        metadata={"person_id": person_id, "name": name, "confidence": round(score, 3)},
    )
    try:
        emit(event)
    except Exception:
        logger.exception("Échec de publication d'événement ROSTER pour %s", thumbnail_path)
        return
    logger.info("Personne reconnue: %s (%.0f%%)", name, score * 100)


def run_roster() -> None:
    from shared.config import settings
    from shared.logging_utils import setup_logging

    setup_logging(settings.log_level)
    logger.info("ROSTER actif — reconnaissance locale des personnes enrôlées uniquement")

    engine = FaceEngine()
    enrolled = _load_enrolled()
    iterations = 0

    offset = 0
    if EVENTS_FILE.exists():
        offset = EVENTS_FILE.stat().st_size

    while True:
        iterations += 1
        if iterations % ENROLLED_CACHE_REFRESH_EVERY == 0:
            enrolled = _load_enrolled()

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
                    _maybe_recognize(raw, engine, enrolled)
                offset = f.tell()
        time.sleep(POLL_INTERVAL)
