"""Service SYS-LOG — agrégation des événements."""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)
EVENTS_FILE = Path("./data/argus/events.jsonl")


def run_sys_log() -> None:
    logger.info("SYS-LOG actif — surveillance %s", EVENTS_FILE)
    offset = 0
    while True:
        if EVENTS_FILE.exists():
            with EVENTS_FILE.open("r", encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    line = line.strip()
                    if line:
                        logger.debug("SYS-LOG event: %s", line[:200])
                offset = f.tell()
        time.sleep(2)
