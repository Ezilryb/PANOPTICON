"""
panopticon/argus/run_argus.py

Point d'entrée du processus ARGUS, lancé par DAEMON comme sous-processus
indépendant (voir `entry_point` dans module_registry.py). Charge la
configuration, démarre ArgusEngine, et s'arrête proprement sur SIGINT/SIGTERM
— DAEMON envoie SIGTERM via `subprocess.terminate()` lors d'un stop_module()
ou d'un stop_all() : ce script gère donc sa propre extinction, indépendamment
du Killswitch de DAEMON qui ne supervise que le processus DAEMON lui-même.
"""

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Optional

# ARGUS est lancé en tant que script (`python3 argus/run_argus.py`), pas via
# `python3 -m`. Python place alors uniquement le dossier `argus/` sur sys.path,
# pas son parent : on ajoute donc `panopticon/` explicitement pour que
# `from argus.xxx import ...` fonctionne quel que soit le cwd du process appelant.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from argus.config import load_config  # noqa: E402  (import après l'ajustement de sys.path, volontaire)
from argus.pipeline import ArgusEngine  # noqa: E402

logger = logging.getLogger("argus.main")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ARGUS — ingestion multi-caméras + détection d'objets/personnes")
    parser.add_argument("--config", type=str, default=None, help="Chemin vers un fichier de configuration JSON")
    return parser.parse_args()


def main() -> None:
    configure_logging()
    args = parse_args()
    config = load_config(args.config)

    engine = ArgusEngine(config)
    stop_requested = threading.Event()

    def _handle_signal(signum: int, _frame: Optional[FrameType]) -> None:
        name = signal.Signals(signum).name
        logger.info("ARGUS : signal reçu (%s), arrêt en cours...", name)
        stop_requested.set()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        logger.warning("SIGTERM non disponible sur cette plateforme, SIGINT reste actif")

    engine.start()
    logger.info(
        "ARGUS opérationnel — %d caméra(s), publication sur %s:%d",
        len(config.cameras), config.publisher.host, config.publisher.port,
    )

    # Boucle d'attente passive : tout le travail se déroule dans les threads
    # d'ArgusEngine, ce thread principal ne fait qu'attendre un signal d'arrêt.
    while not stop_requested.is_set():
        stop_requested.wait(0.5)

    engine.stop()
    logger.info("ARGUS terminé proprement")
    sys.exit(0)


if __name__ == "__main__":
    main()
