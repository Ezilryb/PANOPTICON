"""
panopticon/spectra/run_spectra.py

Point d'entrée du processus SPECTRA, lancé par DAEMON comme sous-processus
indépendant (voir `entry_point` dans module_registry.py). Charge la
configuration, démarre SpectraEngine, et s'arrête proprement sur
SIGINT/SIGTERM — même principe que `argus/run_argus.py` et
`roster/run_roster.py`.

IMPORTANT : DAEMON lance ce script sans aucun argument. Ce script cherche
donc automatiquement `panopticon/spectra.json` (à côté de ce dossier
`spectra/`) si aucun `--config` n'est fourni. Sans ce fichier, il retombe sur
la configuration par défaut (backend "classic", connexion à ARGUS sur
127.0.0.1:8765, aucune zone-écran), ce qui permet de tester SPECTRA de bout
en bout sans rien configurer.

SPECTRA dépend d'ARGUS (`depends_on=["ARGUS"]` dans module_registry.py) :
DAEMON ne le démarre que si ARGUS est déjà `running`. SpectraEngine gère
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

# SPECTRA est lancé en tant que script (`python3 spectra/run_spectra.py`), pas
# via `python3 -m`. Python place alors uniquement le dossier `spectra/` sur
# sys.path, pas son parent : on ajoute donc `panopticon/` explicitement pour
# que `from spectra.xxx import ...` ET `from argus.xxx import ...` fonctionnent
# quel que soit le cwd du process appelant (même ajustement que run_argus.py
# et run_roster.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from spectra.config import load_config  # noqa: E402  (import après l'ajustement de sys.path, volontaire)
from spectra.pipeline import SpectraEngine  # noqa: E402

logger = logging.getLogger("spectra.main")

# Emplacement conventionnel de la config réelle : panopticon/spectra.json.
# Distinct de spectra.example.json (modèle, jamais chargé automatiquement).
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "spectra.json"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SPECTRA — amélioration d'image (faible luminosité, contraste, couleur, débruitage)"
    )
    parser.add_argument("--config", type=str, default=None,
                         help="Chemin vers un fichier de configuration JSON "
                              "(par défaut : panopticon/spectra.json s'il existe, sinon config par défaut)")
    return parser.parse_args()


def _resolve_config_path(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        return explicit_path
    if _DEFAULT_CONFIG_PATH.is_file():
        logger.info("Aucun --config fourni, utilisation du fichier conventionnel : %s", _DEFAULT_CONFIG_PATH)
        return str(_DEFAULT_CONFIG_PATH)
    logger.warning(
        "Aucun --config fourni et %s introuvable : SPECTRA démarre avec la configuration par défaut "
        "(backend 'classic', aucune zone-écran). Crée ce fichier (copie de spectra.example.json) pour "
        "ajuster les seuils ou déclarer des zones-écran.",
        _DEFAULT_CONFIG_PATH,
    )
    return None


def main() -> None:
    configure_logging()
    args = parse_args()
    config_path = _resolve_config_path(args.config)
    config = load_config(config_path)

    engine = SpectraEngine(config)
    stop_requested = threading.Event()

    def _handle_signal(signum: int, _frame: Optional[FrameType]) -> None:
        name = signal.Signals(signum).name
        logger.info("SPECTRA : signal reçu (%s), arrêt en cours...", name)
        stop_requested.set()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        logger.warning("SIGTERM non disponible sur cette plateforme, SIGINT reste actif")

    engine.start()
    logger.info(
        "SPECTRA opérationnel — connecté à ARGUS sur %s:%d, publication sur %s:%d",
        config.argus.host, config.argus.port, config.publisher.host, config.publisher.port,
    )

    while not stop_requested.is_set():
        stop_requested.wait(0.5)

    engine.stop()
    logger.info("SPECTRA terminé proprement")
    sys.exit(0)


if __name__ == "__main__":
    main()