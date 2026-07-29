"""WebSocket temps réel NEXUS-V."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from daemon.orchestrator import orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

EVENTS_FILE = Path("./data/argus/events.jsonl")


@router.websocket("/ws/live")
async def live_feed(websocket: WebSocket) -> None:
    await websocket.accept()
    offset = 0
    if EVENTS_FILE.exists():
        offset = EVENTS_FILE.stat().st_size

    try:
        while True:
            payload = {
                "type": "status",
                "modules": [m.model_dump(mode="json") for m in orchestrator.list_modules()],
                "resources": orchestrator.get_resources().model_dump(mode="json"),
            }
            await websocket.send_json(payload)

            if EVENTS_FILE.exists():
                with EVENTS_FILE.open("r", encoding="utf-8") as f:
                    f.seek(offset)
                    for line in f:
                        line = line.strip()
                        if line:
                            await websocket.send_json({"type": "event", "data": json.loads(line)})
                    offset = f.tell()

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        logger.debug("Client WebSocket déconnecté")
    except Exception:
        logger.exception("Erreur WebSocket")
