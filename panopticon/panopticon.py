#!/usr/bin/env python3
"""
PANOPTICON — Point d'entrée.

Logiciel interne, piloté depuis l'invite de commande (aucune interface web
n'est requise). Toutes les opérations passent par les sous-commandes définies
dans ``cli/app.py`` :

    python panopticon.py serve              # démarre le service (API + DAEMON)
    python panopticon.py status              # état instantané du système
    python panopticon.py camera add NOM URL  # ajoute une caméra
    python panopticon.py camera list         # liste les caméras
    python panopticon.py module start argus  # démarre un module
    python panopticon.py events tail         # suit les événements en direct
    python panopticon.py monitor             # tableau de bord temps réel

Lancez « python panopticon.py --help » (ou --help sur n'importe quelle
sous-commande) pour la liste complète des options.
"""

from cli.app import app

if __name__ == "__main__":
    app()
