# PANOPTICON

Système de vision par ordinateur multi-caméras orchestré par **DAEMON**.

## Phase 1 MVP (implémentée)

- **PANOPTICON** — point d'entrée (`panopticon.py`)
- **DAEMON** — orchestrateur avec registre de modules, vérification ressources, isolation processus
- **ARGUS** — ingestion caméra, détection YOLOv8 + tracking, événements
- **SYS-LOG / VAULT** — stubs fonctionnels
- **NEXUS-V** — dashboard React (grille caméras, modules, timeline)
- **API FastAPI** — routes DAEMON, caméras, événements, WebSocket

## Prérequis

- Python 3.11+
- Node.js 18+ (pour NEXUS-V)
- Webcam ou flux RTSP (optionnel)

## Installation

```bash
# Backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env

# Frontend
cd nexus-v
npm install
```

## Lancement

```bash
# Terminal 1 — API + DAEMON
python panopticon.py

# Terminal 2 — Dashboard
cd nexus-v && npm run dev
```

- API : http://localhost:8000
- Docs : http://localhost:8000/docs
- NEXUS-V : http://localhost:5173

## Ajouter une caméra

Via NEXUS-V (formulaire) ou API :

```bash
curl -X POST http://localhost:8000/api/cameras ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"Webcam\",\"connection_url\":\"0\",\"zone\":\"bureau\",\"target_fps\":3}"
```

Puis démarrez ARGUS si nécessaire :

```bash
curl -X POST http://localhost:8000/api/daemon/modules/argus/start
```

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
| 1 ✅ | DAEMON + ARGUS (1 cam) + NEXUS-V basique |
| 2 | Multi-cam, zones, SYS-LOG complet |
| 3 | ORACLE, SPECTRA |
| 4 | ROSTER, PULSE_TRACK |
| 5 | AEGIS, VAULT durci, profils complets |

## Contraintes éthiques (hors-scope)

Voir `docs/privacy-notice-template.md` et le brief projet — pas de reconnaissance de personnes non enrôlées, pas de lecture d'écran tiers, pas d'inférence de dangerosité.

## Structure

```
panopticon.py          # Point d'entrée
daemon/                # Orchestrateur DAEMON
modules/               # ARGUS, SPECTRA, ORACLE…
api/                   # FastAPI
nexus-v/               # Frontend React
shared/                # Config, modèles Pydantic
docs/                  # Documentation
```
