#!/usr/bin/env python3
"""
PANOPTICON — Point d'entrée.

Vérifie la configuration, initialise DAEMON et lance l'API.
"""

import multiprocessing as mp

import uvicorn

from shared.config import settings
from shared.logging_utils import setup_logging


def main() -> None:
    mp.set_start_method("spawn", force=True)
    setup_logging(settings.log_level)
    uvicorn.run(
        "api.main:app",
        host=settings.panopticon_host,
        port=settings.panopticon_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
