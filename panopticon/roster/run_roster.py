"""
panopticon/roster/run_roster.py

Point d'entrée du processus ROSTER, lancé par DAEMON comme sous-processus
indépendant (voir `entry_point` dans module_registry.py). Charge la
configuration, démarre RosterEngine, et s'arrête proprement sur
SIGINT/SIGTERM — même principe que `argus/run_argus.py`.

IMPORTANT : DAEMON lance ce script sans aucun argument. Ce script cherche
donc automatiquement `panopticon/roster.json` (à côté de ce dossier
`roster/`) si aucun `--config` n'est fourni. Sans ce fichier, il retombe sur
la configuration par défaut (backend embedder "mock", stockage local sous
`roster/roster_data/`), ce qui permet de tester ROSTER de bout en bout sans
avoir enrôlé qui que ce soit ni installé `face_recognition`.

ROSTER dépend d'ARGUS (`depends_on=["ARGUS"]` dans module_registry.py) :
DAEMON ne le démarre que si ARGUS est déjà `running`. RosterEngine gère
malgré tout une petite tolérance de reconnexion au démarrage (voir
`pipeline.py::_connect_to_argus_with_retry`), au cas où le socket du
publisher d'ARGUS ne serait pas encore prêt à la microseconde près.
"""

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Optional

# ROSTER est lancé en tant que script (`python3 roster/run_roster.py`), pas via
# `python3 -m`. Python place alors uniquement le dossier `roster/` sur sys.path,
# pas son parent : on ajoute donc `panopticon/` explicitement pour que
# `from roster.xxx import ...` ET `from argus.xxx import ...` fonctionnent quel
# que soit le cwd du process appelant (même ajustement que run_argus.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from roster.config import load_config  # noqa: E402  (import après l'ajustement de sys.path, volontaire)
from roster.pipeline import RosterEngine  # noqa: E402

logger = logging.getLogger("roster.main")

# Emplacement conventionnel de la config réelle : panopticon/roster.json.
# Distinct de roster.example.json (modèle, jamais chargé automatiquement).
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "roster.json"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ROSTER — reconnaissance de personnes connues (opt-in, 100% local)")
    parser.add_argument("--config", type=str, default=None,
                         help="Chemin vers un fichier de configuration JSON "
                              "(par défaut : panopticon/roster.json s'il existe, sinon config par défaut)")
    return parser.parse_args()


def _resolve_config_path(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        return explicit_path
    if _DEFAULT_CONFIG_PATH.is_file():
        logger.info("Aucun --config fourni, utilisation du fichier conventionnel : %s", _DEFAULT_CONFIG_PATH)
        return str(_DEFAULT_CONFIG_PATH)
    logger.warning(
        "Aucun --config fourni et %s introuvable : ROSTER démarre avec la configuration par défaut "
        "(backend embedder 'mock', aucune personne enrôlée). Crée ce fichier (copie de "
        "roster.example.json) et enrôle des personnes pour un usage réel.",
        _DEFAULT_CONFIG_PATH,
    )
    return None


def main() -> None:
    configure_logging()
    args = parse_args()
    config_path = _resolve_config_path(args.config)
    config = load_config(config_path)

    engine = RosterEngine(config)
    stop_requested = threading.Event()

    def _handle_signal(signum: int, _frame: Optional[FrameType]) -> None:
        name = signal.Signals(signum).name
        logger.info("ROSTER : signal reçu (%s), arrêt en cours...", name)
        stop_requested.set()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        logger.warning("SIGTERM non disponible sur cette plateforme, SIGINT reste actif")

    engine.start()
    logger.info(
        "ROSTER opérationnel — %d personne(s) enrôlée(s), publication sur %s:%d",
        len(engine.store), config.publisher.host, config.publisher.port,
    )

    while not stop_requested.is_set():
        stop_requested.wait(0.5)

    engine.stop()
    logger.info("ROSTER terminé proprement")
    sys.exit(0)


if __name__ == "__main__":
    main()
