# PANOPTICON

Système de vision par ordinateur multi-caméras orchestré par **DAEMON**.

Logiciel interne, sans interface web obligatoire : le pilotage (démarrage du
service, gestion des caméras et des modules, consultation des événements,
tableau de bord temps réel) se fait entièrement depuis l'invite de commande.
NEXUS-V (dashboard web) reste disponible en option.

## Phase 1 MVP (implémentée)

- **PANOPTICON** — point d'entrée CLI (`panopticon.py`)
- **DAEMON** — orchestrateur avec registre de modules, vérification ressources, isolation processus
- **ARGUS** — ingestion caméra, détection YOLOv8 + tracking, événements
- **SYS-LOG / VAULT** — stubs fonctionnels
- **CLI** — pilotage complet en ligne de commande (`cli/`)
- **NEXUS-V** — dashboard web React (optionnel)
- **API FastAPI** — routes DAEMON, caméras, événements, WebSocket

## Prérequis

- Python 3.11+
- Node.js 18+ (uniquement si vous utilisez NEXUS-V)
- Webcam ou flux RTSP (optionnel)

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
copy .env.example .env          # Windows — ou : cp .env.example .env
```

## Démarrage

Dans un premier terminal, démarrez le service (à laisser actif en continu) :

```bash
python panopticon.py serve
```

- API : http://localhost:8000
- Docs interactives : http://localhost:8000/docs

Dans un second terminal, pilotez le système :

```bash
python panopticon.py status
```

> `python panopticon.py` sans argument affiche désormais l'aide (liste des
> sous-commandes) plutôt que de démarrer le service — utilisez `serve`
> explicitement, comme ci-dessus.

## Utilisation en ligne de commande

Toutes les commandes acceptent `--api-url` (ou la variable d'environnement
`PANOPTICON_API_URL`) si l'API n'écoute pas sur `localhost:8000` — utile pour
piloter le système depuis une autre machine du réseau local.

### État général

```bash
python panopticon.py status     # ressources + modules + caméras, en un coup d'œil
python panopticon.py monitor    # tableau de bord temps réel plein écran (Ctrl+C pour quitter)
```

### Caméras

```bash
python panopticon.py camera add "Webcam bureau" 0 --zone bureau
python panopticon.py camera add "Entrée" rtsp://user:pass@192.168.1.50/stream --zone entree --fps 5
python panopticon.py camera list
python panopticon.py camera show <ID>
python panopticon.py camera remove <ID>
python panopticon.py camera discover           # recherche des caméras ONVIF sur le réseau local
```

`camera discover` sonde le réseau (WS-Discovery) et affiche les caméras ONVIF
trouvées avec leur adresse de gestion. Il ne fournit pas le chemin RTSP
(propre à chaque fabricant) : ajoutez la caméra ensuite avec `camera add` une
fois l'URL RTSP connue.

### Modules DAEMON

```bash
python panopticon.py module list
python panopticon.py module start argus
python panopticon.py module stop argus
```

### Événements

```bash
python panopticon.py events list --limit 30 --zone bureau
python panopticon.py events tail          # suivi en direct, une ligne par événement
```

### SYS-LOG (résumé + actions opérateur)

```bash
python panopticon.py syslog summary --hours 24    # comptes par type / zone / module
python panopticon.py syslog actions --limit 50    # démarrages/arrêts de module, caméras ajoutées…
```

Toute action déclenchée par un opérateur (via la CLI ou l'API — démarrage ou
arrêt d'un module, ajout/modification/suppression d'une caméra, y compris les
refus faute de ressources ou de dépendance) est journalisée et consultable
via `syslog actions`, indépendamment des événements de détection ARGUS.

### Service système (démarrage automatique, sans terminal ouvert)

```bash
python panopticon.py service install              # affiche la configuration (Linux/Windows/macOS)
python panopticon.py service install --write      # l'écrit aussi localement
python panopticon.py service uninstall            # commandes de désinstallation
```

Détecte l'OS et génère la configuration adaptée : unité **systemd** sous
Linux (avec `Restart=on-failure`), tâche planifiée **Task Scheduler** sous
Windows (démarrage à l'ouverture de session), agent **launchd** sous macOS.
La commande n'exécute jamais elle-même de commande privilégiée (`sudo`…) :
elle affiche les étapes exactes à lancer, sous votre contrôle.

Chaque commande dispose de `--help` pour le détail des options
(ex. `python panopticon.py camera add --help`).

## Dashboard web NEXUS-V (optionnel)

```bash
cd nexus-v
npm install
npm run dev
```

- NEXUS-V : http://localhost:5173

## Profils machine

Variable `PANOPTICON_PROFILE` dans `.env` :

| Profil | Modules auto-démarrés |
|--------|----------------------|
| `light` | argus, vault, nexus_v |
| `standard` | + roster, pulse_track, sys_log |
| `full` | tous les modules |

## Roadmap

| Phase | Contenu |
|-------|---------|
| 1 ✅ | DAEMON + ARGUS (1 cam) + CLI + NEXUS-V basique |
| 2 🟡 | Multi-cam ✅ (threads), zones ✅, découverte ONVIF ✅, SYS-LOG (actions + résumé) ✅ — reste : intégration ONVIF → RTSP automatique |
| 3 | ORACLE, SPECTRA |
| 4 | ROSTER, PULSE_TRACK |
| 5 | AEGIS, VAULT durci, profils complets |

## Contraintes éthiques (hors-scope)

Voir `docs/privacy-notice-template.md` et le brief projet — pas de reconnaissance de personnes non enrôlées, pas de lecture d'écran tiers, pas d'inférence de dangerosité.

## Structure

```
panopticon.py           # Point d'entrée CLI
cli/                    # Commandes CLI (client HTTP, tableau de bord terminal, service système)
daemon/                 # Orchestrateur DAEMON
modules/                # ARGUS, SPECTRA, ORACLE…
api/                    # FastAPI
nexus-v/                # Frontend React (optionnel)
shared/                 # Config, modèles Pydantic
docs/                   # Documentation
```

## Notes techniques

- **Casse du dossier `daemon/`** : l'orchestrateur doit se trouver dans un
  dossier nommé `daemon/` (minuscules), pas `DAEMON/`. Les imports Python
  (`from daemon.orchestrator import ...`) sont sensibles à la casse sur
  Linux/macOS et dans l'image Docker (`python:3.11-slim`, Debian), même si
  Windows masque le problème en développement grâce à son système de fichiers
  insensible à la casse. Si votre copie locale contient encore `DAEMON/`,
  renommez-le en `daemon/` avant de déployer sur un serveur Linux ou en Docker.
- Quelques dossiers vides à la racine (`AEGIS/`, `ARGUS/`, `PULSE_TRACK/`,
  `SNIFFER-CORE/`, `SPECTRA/`, `SYS-LOG/`, chacun ne contenant qu'un
  "Nouveau Document texte.txt") sont des reliquats de scaffolding sans lien
  avec le code réel (qui vit dans `modules/`) : ils peuvent être supprimés
  sans risque.
