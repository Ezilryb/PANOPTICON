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
python panopticon.py camera discover --user admin --password ****   # + URL RTSP réelle (GetStreamUri)
```

`camera discover` sonde le réseau (WS-Discovery) et affiche les caméras ONVIF
trouvées. Avec `--user`/`--password`, il tente en plus une authentification
ONVIF standard (GetCapabilities → GetProfiles → GetStreamUri) pour afficher
directement l'URL RTSP de chaque caméra — sinon, seule l'adresse de gestion
(XAddr) est montrée, à compléter manuellement selon le fabricant.

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
| 2 ✅ | Multi-cam (threads), zones, découverte ONVIF + GetStreamUri, SYS-LOG (actions + résumé) |
| 3 ✅ | SPECTRA (amélioration d'image + état d'écran), ORACLE (identification locale, sans API) |
| 4 ✅ | ROSTER (reconnaissance faciale locale, opt-in), PULSE_TRACK (règles + notifications) |
| 5 | AEGIS, VAULT durci, profils complets |

### ORACLE — identification fine d'objets, 100% locale

Contrainte volontaire : **aucun appel réseau à l'exécution**. Pas d'API
externe — ORACLE utilise un classifieur d'images pré-entraîné (torchvision,
MobileNetV3-Small, ImageNet-1k) qui tourne sur CPU, sur l'appareil.

- Ne se connecte à aucune caméra : surveille les événements déjà produits par
  ARGUS (`object_appeared`) et relit leur miniature déjà sauvegardée.
- **Ne traite jamais les détections "person"** — ORACLE ne fait aucune analyse
  de personnes ni de visages (rôle réservé à ROSTER, opt-in et local).
- Publie un événement `object_identified` (label affiné + confiance), visible
  comme tout autre événement via `events`/`syslog`/`monitor`.

**Une seule chose nécessite le réseau : le téléchargement initial des poids
du modèle**, une fois, au premier lancement — exactement comme YOLO le fait
déjà pour ARGUS (mise en cache locale ensuite, aucun appel réseau à
l'exécution). Pour un déploiement entièrement air-gapped :

```bash
# Sur une machine connectée, avec le même environnement :
python -c "from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small; mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.DEFAULT)"
# Les poids sont mis en cache dans ~/.cache/torch/hub/checkpoints/
# Copiez ce dossier vers le même chemin sur l'appareil final (hors-ligne).
```

### ROSTER — reconnaissance faciale locale, opt-in

Contrainte respectée : **aucun appel réseau à l'exécution**, aucune API
externe. Détection de visage (MTCNN) et empreinte faciale (InceptionResnetV1
pré-entraîné VGGFace2) via `facenet-pytorch`, sur CPU. Même remarque que pour
ORACLE concernant le téléchargement initial des poids (une fois, hors-ligne
possible ensuite) — voir la section ORACLE ci-dessus pour la marche à suivre.

**Garde-fous, non contournables par un simple paramètre :**
- Enrôlement **refusé sans consentement explicite** — vérifié côté CLI (le
  drapeau `--consent` doit être passé) *et* côté API (rejet HTTP 400 si
  `consent` n'est pas à `true`, même en appelant l'API directement).
- **Aucune tentative d'identification en l'absence de correspondance
  suffisante** : `find_best_match` retourne `None` si le meilleur score reste
  sous le seuil — ROSTER ne devine jamais qui pourrait être une personne non
  enrôlée.
- Droit à l'effacement : `roster remove` supprime l'enrôlement.

```bash
python panopticon.py roster enroll "Prénom Nom" --photo photo1.jpg --photo photo2.jpg --consent
python panopticon.py roster list
python panopticon.py roster remove <ID>
```

Le seuil de correspondance par défaut (similarité cosinus 0,65,
`DEFAULT_MATCH_THRESHOLD` dans `modules/roster/face_engine.py`) est un point
de départ raisonnable mais **n'a pas pu être calibré sur de vrais visages**
dans cet environnement de développement (modèle non téléchargeable ici) — à
ajuster selon vos premiers résultats réels (le monter réduit les faux
positifs, le descendre réduit les faux négatifs).

**Donnée sensible non chiffrée pour l'instant** : les empreintes faciales
sont actuellement stockées en clair dans SQLite — le chiffrement au repos
fait partie de VAULT durci (Phase 5), pas encore implémenté.

### PULSE_TRACK — règles et notifications

Évaluation des règles 100% locale. Trois actions possibles :

- `webhook` — POST JSON vers une URL au choix (réseau local ou internet,
  PANOPTICON ne présuppose aucun service particulier)
- `email` — envoi SMTP vers le serveur que vous configurez (local ou distant)
- `push` — notification système locale best-effort (`notify-send`/macOS/Windows) ;
  invisible sur un serveur headless sans session graphique — préférez
  webhook/email pour un déploiement serveur

```bash
python panopticon.py rule add "Entrée surveillée" --event-type person_entered_zone --zone entree \
  --action webhook --target http://localhost:8123/api/webhook/mon_hook
python panopticon.py rule list
python panopticon.py rule disable <ID>
python panopticon.py alerts list --unacknowledged
python panopticon.py alerts ack <ID>
```

Le format `--target` pour `email` est un JSON compact, ex. :
`'{"host":"smtp.example.com","port":587,"username":"...","password":"...","to":"moi@example.com"}'`.

**Non testé en conditions réelles** : `send_email` et la notification système
locale suivent les API standard mais n'ont pas pu être vérifiées contre un
vrai serveur SMTP ni un vrai bureau graphique dans ce sandbox (Linux headless,
sans accès SMTP sortant). Le rendu webhook, lui, a été testé de bout en bout
avec un vrai serveur HTTP local.

### SPECTRA — amélioration d'image et état d'écran

SPECTRA ne se connecte à aucune caméra directement : il relit les frames déjà
écrites par ARGUS (`data/argus/frames/`), ce qui évite une seconde connexion
vidéo par caméra (d'où sa dépendance à `argus`). Deux fonctions :

- **Amélioration d'image** (CLAHE, gamma, débruitage) — désactivée par défaut,
  activable via `.env` (`PANOPTICON_SPECTRA_ENHANCE_FRAMES=true`,
  `PANOPTICON_SPECTRA_GAMMA=1.4`, `PANOPTICON_SPECTRA_DENOISE=true`) : utile en
  faible luminosité, appliquée par ARGUS avant sauvegarde et détection.
- **État d'écran** (allumé/éteint, statique/dynamique) — événements
  `screen_state_changed` visibles via `events`/`syslog`/`monitor`, au même
  titre que ceux d'ARGUS. Uniquement des signaux photométriques globaux
  (luminosité moyenne, différence entre frames) : aucune lecture ni
  interprétation du contenu affiché, conformément aux contraintes éthiques du
  projet.

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
