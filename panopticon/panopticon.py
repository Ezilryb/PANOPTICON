"""
panopticon/panopticon.py

Point d'entrée de PANOPTICON. Vérifie la configuration, initialise DAEMON,
arme le killswitch (arrêt propre), puis lance la boucle de commande en
ligne de commande permettant de choisir les modules à démarrer/arrêter.
"""

import logging
import sys
from datetime import datetime

from daemon import Daemon, Killswitch


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("panopticon.log", encoding="utf-8"),
        ],
    )


def check_configuration() -> bool:
    """
    Vérification minimale au démarrage. Aucune configuration externe n'est
    requise à ce stade (aucun module réel à configurer) — point d'extension
    futur pour la lecture d'un fichier de config (caméras, seuils, etc.).
    """
    return True


def print_header() -> None:
    print("=" * 62)
    print("  PANOPTICON — Système de supervision multi-modules")
    print("=" * 62)


def print_modules(daemon: Daemon) -> None:
    daemon.refresh_crashed()
    print("\n--- Modules enregistrés ---")
    print(f"{'#':<3}{'Codename':<14}{'Statut':<18}{'Dépend de'}")
    for i, row in enumerate(daemon.list_modules(), start=1):
        deps = ", ".join(row["depends_on"]) if row["depends_on"] else "—"
        print(f"{i:<3}{row['codename']:<14}{row['status']:<18}{deps}")


def print_resources(daemon: Daemon) -> None:
    r = daemon.resource_monitor.snapshot()
    print("\n--- Ressources système ---")
    print(f"CPU utilisé    : {r.cpu_percent_used:.1f}% ({r.cpu_cores_total} coeurs)")
    print(f"RAM disponible : {r.ram_available_mb / 1024:.1f} Go / {r.ram_total_mb / 1024:.1f} Go")
    if r.gpu_available:
        print(f"GPU            : {r.gpu_name} ({r.gpu_free_mb / 1024:.1f} Go libres)")
    else:
        print("GPU            : non détecté")


def print_help() -> None:
    print("\n--- Commandes ---")
    print(" start <codename>   Démarrer un module (ex: start ARGUS)")
    print(" stop <codename>    Arrêter un module")
    print(" status             Rafraîchir modules + ressources")
    print(" stop-all           Arrêt propre de tous les modules actifs")
    print(" help               Afficher cette aide")
    print(" quit               Arrêter DAEMON et quitter PANOPTICON")


def main() -> None:
    configure_logging()
    logger = logging.getLogger("panopticon")

    print_header()

    print("Vérification de la configuration... ", end="")
    if not check_configuration():
        print("ÉCHEC")
        sys.exit(1)
    print("OK")

    print("Initialisation de DAEMON... ", end="")
    daemon = Daemon()
    print("OK")
    logger.info("PANOPTICON démarré à %s", datetime.now().isoformat())

    killswitch = Killswitch(daemon)
    killswitch.arm()

    print_resources(daemon)
    print_modules(daemon)
    print_help()

    while True:
        try:
            raw = input("\nDAEMON> ").strip()
        except EOFError:
            raw = "quit"

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd == "quit":
            killswitch.trigger(exit_code=0)

        elif cmd == "stop-all":
            for line in daemon.stop_all():
                print(f"  - {line}")

        elif cmd == "start":
            if not arg:
                print("Usage : start <codename>")
                continue
            print(daemon.start_module(arg))

        elif cmd == "stop":
            if not arg:
                print("Usage : stop <codename>")
                continue
            print(daemon.stop_module(arg))

        elif cmd == "status":
            print_resources(daemon)
            print_modules(daemon)

        elif cmd == "help":
            print_help()

        else:
            print(f"Commande inconnue : {cmd!r}. Tapez 'help' pour la liste des commandes.")


if __name__ == "__main__":
    main()
