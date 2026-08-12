"""
panopticon/pulse_track/run_pulse_track.py

Point d'entrée du processus PULSE_TRACK, lancé par DAEMON comme
sous-processus indépendant (voir `entry_point` dans module_registry.py).
Charge la configuration, démarre PulseTrackEngine, et s'arrête proprement
sur SIGINT/SIGTERM — même principe que argus/run_argus.py,
roster/run_roster.py, spectra/run_spectra.py et oracle/run_oracle.py.

IMPORTANT : DAEMON lance ce script sans aucun argument. Ce script cherche
donc automatiquement `panopticon/pulse_track.json` (à côté de ce dossier
`pulse_track/`) si aucun `--config` n'est fourni. Sans ce fichier, il
retombe sur la configuration par défaut (AUCUNE règle) : PULSE_TRACK démarre,
se connecte à ARGUS et ROSTER, consomme leurs flux, mais ne publie jamais
rien tant qu'aucune règle n'est ajoutée. Crée `pulse_track.json` (copie de
`pulse_track.example.json`) pour un usage réel.

PULSE_TRACK dépend d'ARGUS ET de ROSTER (`depends_on=["ARGUS", "ROSTER"]`
dans module_registry.py) : DAEMON ne le démarre que si les deux sont déjà
`running`. PulseTrackEngine gère malgré tout une petite tolérance de
reconnexion au démarrage pour chacun des deux flux (voir
`pipeline.py::_connect_with_retry`), au cas où leurs sockets de publisher
respectifs ne seraient pas encore prêts à la microseconde près.
"""

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Optional

# PULSE_TRACK est lancé en tant que script (`python3 pulse_track/run_pulse_track.py`),
# pas via `python3 -m`. Python place alors uniquement le dossier `pulse_track/` sur
# sys.path, pas son parent : on ajoute donc `panopticon/` explicitement pour que
# `from pulse_track.xxx import ...`, `from argus.xxx import ...` ET `from roster.xxx
# import ...` fonctionnent quel que soit le cwd du process appelant (même ajustement
# que run_roster.py/run_spectra.py/run_oracle.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from pulse_track.config import load_config  # noqa: E402  (import après l'ajustement de sys.path, volontaire)
from pulse_track.pipeline import PulseTrackEngine  # noqa: E402

logger = logging.getLogger("pulse_track.main")

# Emplacement conventionnel de la config réelle : panopticon/pulse_track.json.
# Distinct de pulse_track.example.json (modèle, jamais chargé automatiquement).
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "pulse_track.json"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PULSE_TRACK — moteur de règles et notifications")
    parser.add_argument("--config", type=str, default=None,
                         help="Chemin vers un fichier de configuration JSON "
                              "(par défaut : panopticon/pulse_track.json s'il existe, sinon config par défaut)")
    return parser.parse_args()


def _resolve_config_path(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        return explicit_path
    if _DEFAULT_CONFIG_PATH.is_file():
        logger.info("Aucun --config fourni, utilisation du fichier conventionnel : %s", _DEFAULT_CONFIG_PATH)
        return str(_DEFAULT_CONFIG_PATH)
    logger.warning(
        "Aucun --config fourni et %s introuvable : PULSE_TRACK démarre avec la configuration par "
        "défaut (aucune règle). Crée ce fichier (copie de pulse_track.example.json) et déclare des "
        "règles pour un usage réel.",
        _DEFAULT_CONFIG_PATH,
    )
    return None


def main() -> None:
    configure_logging()
    args = parse_args()
    config_path = _resolve_config_path(args.config)
    config = load_config(config_path)

    engine = PulseTrackEngine(config)
    stop_requested = threading.Event()

    def _handle_signal(signum: int, _frame: Optional[FrameType]) -> None:
        name = signal.Signals(signum).name
        logger.info("PULSE_TRACK : signal reçu (%s), arrêt en cours...", name)
        stop_requested.set()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        logger.warning("SIGTERM non disponible sur cette plateforme, SIGINT reste actif")

    engine.start()
    logger.info(
        "PULSE_TRACK opérationnel — connecté à ARGUS sur %s:%d et ROSTER sur %s:%d, publication sur %s:%d",
        config.argus.host, config.argus.port, config.roster.host, config.roster.port,
        config.publisher.host, config.publisher.port,
    )

    while not stop_requested.is_set():
        stop_requested.wait(0.5)

    engine.stop()
    logger.info("PULSE_TRACK terminé proprement")
    sys.exit(0)


if __name__ == "__main__":
    main()