"""
panopticon/oracle/run_oracle.py

Point d'entrée du processus ORACLE, lancé par DAEMON comme sous-processus
indépendant (voir `entry_point` dans module_registry.py). Charge la
configuration, démarre OracleEngine, et s'arrête proprement sur
SIGINT/SIGTERM — même principe que `argus/run_argus.py`, `roster/run_roster.py`
et `spectra/run_spectra.py`.

IMPORTANT : DAEMON lance ce script sans aucun argument. Ce script cherche
donc automatiquement `panopticon/oracle.json` (à côté de ce dossier
`oracle/`) si aucun `--config` n'est fourni. Sans ce fichier, il retombe sur
la configuration par défaut (backend identifier "mock", aucune clé API
requise), ce qui permet de tester ORACLE de bout en bout sans compte API.

ORACLE dépend d'ARGUS (`depends_on=["ARGUS"]` dans module_registry.py) :
DAEMON ne le démarre que si ARGUS est déjà `running`. OracleEngine gère
malgré tout une petite tolérance de reconnexion au démarrage (voir
`pipeline.py::_connect_to_argus_with_retry`), même principe que ROSTER/SPECTRA.
"""

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Optional

# ORACLE est lancé en tant que script (`python3 oracle/run_oracle.py`), pas via
# `python3 -m`. Python place alors uniquement le dossier `oracle/` sur sys.path,
# pas son parent : on ajoute donc `panopticon/` explicitement pour que
# `from oracle.xxx import ...` ET `from argus.xxx import ...` fonctionnent quel
# que soit le cwd du process appelant (même ajustement que run_roster.py/run_spectra.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from oracle.config import load_config  # noqa: E402  (import après l'ajustement de sys.path, volontaire)
from oracle.pipeline import OracleEngine  # noqa: E402

logger = logging.getLogger("oracle.main")

# Emplacement conventionnel de la config réelle : panopticon/oracle.json.
# Distinct de oracle.example.json (modèle, jamais chargé automatiquement).
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "oracle.json"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ORACLE — identification fine d'objets (marque/modèle) via API externe")
    parser.add_argument("--config", type=str, default=None,
                         help="Chemin vers un fichier de configuration JSON "
                              "(par défaut : panopticon/oracle.json s'il existe, sinon config par défaut)")
    return parser.parse_args()


def _resolve_config_path(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        return explicit_path
    if _DEFAULT_CONFIG_PATH.is_file():
        logger.info("Aucun --config fourni, utilisation du fichier conventionnel : %s", _DEFAULT_CONFIG_PATH)
        return str(_DEFAULT_CONFIG_PATH)
    logger.warning(
        "Aucun --config fourni et %s introuvable : ORACLE démarre avec la configuration par défaut "
        "(backend identifier 'mock', aucun appel réseau). Crée ce fichier (copie de oracle.example.json) "
        "et configure une clé API pour un usage réel.",
        _DEFAULT_CONFIG_PATH,
    )
    return None


def main() -> None:
    configure_logging()
    args = parse_args()
    config_path = _resolve_config_path(args.config)
    config = load_config(config_path)

    engine = OracleEngine(config)
    stop_requested = threading.Event()

    def _handle_signal(signum: int, _frame: Optional[FrameType]) -> None:
        name = signal.Signals(signum).name
        logger.info("ORACLE : signal reçu (%s), arrêt en cours...", name)
        stop_requested.set()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        logger.warning("SIGTERM non disponible sur cette plateforme, SIGINT reste actif")

    engine.start()
    logger.info(
        "ORACLE opérationnel — backend=%s, connecté à ARGUS sur %s:%d, publication sur %s:%d",
        config.identifier.backend, config.argus.host, config.argus.port,
        config.publisher.host, config.publisher.port,
    )

    while not stop_requested.is_set():
        stop_requested.wait(0.5)

    engine.stop()
    logger.info("ORACLE terminé proprement")
    sys.exit(0)


if __name__ == "__main__":
    main()
