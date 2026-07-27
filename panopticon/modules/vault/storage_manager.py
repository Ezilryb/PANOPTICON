"""Service VAULT — stockage local."""

import logging
import time

from shared.config import settings

logger = logging.getLogger(__name__)


def run_vault() -> None:
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    (settings.storage_path / "thumbnails").mkdir(parents=True, exist_ok=True)
    logger.info("VAULT actif — stockage: %s", settings.storage_path)
    while True:
        time.sleep(300)
