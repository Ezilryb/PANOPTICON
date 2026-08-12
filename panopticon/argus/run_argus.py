"""
panopticon/argus/run_argus.py

Point d'entrée du processus ARGUS, lancé par DAEMON comme sous-processus
indépendant (voir `entry_point` dans module_registry.py). Charge la
configuration, démarre ArgusEngine, et s'arrête proprement sur SIGINT/SIGTERM
— DAEMON envoie SIGTERM via `subprocess.terminate()` lors d'un stop_module()
ou d'un stop_all() : ce script gère donc sa propre extinction, indépendamment
du Killswitch de DAEMON qui ne supervise que le processus DAEMON lui-même.

IMPORTANT : DAEMON lance ce script sans aucun argument (`Popen([sys.executable,
str(script_path)])`, voir orchestrator.py::_launch). Un `--config` passé en
ligne de commande ne sert donc que pour un lancement manuel
(`python argus/run_argus.py --config ...`). Pour que DAEMON utilise tes
vraies caméras, ce script cherche automatiquement `panopticon/cameras.json`
(à côté de ce dossier `argus/`) si aucun `--config` n'est fourni. Sans ce
fichier, il retombe sur la configuration par défaut (caméra synthétique
"DEMO-0"), ce qui explique le rectangle vert vu dans les snapshots tant que
`cameras.json` n'existe pas encore.
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
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from argus.config import load_config  # noqa: E402  (import après l'ajustement de sys.path, volontaire)
from argus.pipeline import ArgusEngine  # noqa: E402

logger = logging.getLogger("argus.main")

# Emplacement conventionnel de la config réelle : panopticon/cameras.json.
# Volontairement distinct de cameras.example.json (fourni comme modèle,
# jamais chargé automatiquement) pour ne jamais écraser un fichier d'exemple
# versionné avec des identifiants/réglages personnels.
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "cameras.json"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ARGUS — ingestion multi-caméras + détection d'objets/personnes")
    parser.add_argument("--config", type=str, default=None,
                         help="Chemin vers un fichier de configuration JSON "
                              "(par défaut : panopticon/cameras.json s'il existe, sinon caméra synthétique)")
    return parser.parse_args()


def _resolve_config_path(explicit_path: Optional[str]) -> Optional[str]:
    """
    Détermine quel fichier de config charger : priorité au `--config` explicite
    (lancement manuel) ; sinon `cameras.json` conventionnel s'il existe (cas
    DAEMON, qui ne passe jamais d'argument) ; sinon None (-> caméra synthétique).
    """
    if explicit_path:
        return explicit_path
    if _DEFAULT_CONFIG_PATH.is_file():
        logger.info("Aucun --config fourni, utilisation du fichier conventionnel : %s", _DEFAULT_CONFIG_PATH)
        return str(_DEFAULT_CONFIG_PATH)
    logger.warning(
        "Aucun --config fourni et %s introuvable : ARGUS démarre avec la caméra "
        "synthétique de démonstration (DEMO-0). Crée ce fichier (copie de "
        "cameras.example.json) pour utiliser ta vraie caméra.",
        _DEFAULT_CONFIG_PATH,
    )
    return None


def main() -> None:
    configure_logging()
    args = parse_args()
    config_path = _resolve_config_path(args.config)
    config = load_config(config_path)

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
