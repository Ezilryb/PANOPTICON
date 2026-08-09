"""
panopticon/daemon/module_registry.py

Registre déclaratif des modules gérés par DAEMON : codename, description,
dépendances et besoins en ressources approximatifs. ARGUS, ROSTER, SPECTRA,
ORACLE et PULSE_TRACK sont désormais implémentés (voir panopticon/argus/,
panopticon/roster/, panopticon/spectra/, panopticon/oracle/ et
panopticon/pulse_track/) ; les autres modules restent des déclarations en
attente de leur code métier (`entry_point` et `implemented`).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModuleSpec:
    """Déclaration d'un module gérable par DAEMON (aucune logique métier ici)."""

    codename: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    ram_mb: Optional[float] = None        # besoin RAM approx. déclaré (Mo) — à renseigner par le module réel
    cpu_cores: Optional[float] = None     # besoin CPU approx. déclaré (coeurs)
    gpu_required: bool = False
    implemented: bool = False             # False tant que le module réel n'est pas codé
    entry_point: Optional[str] = None     # chemin du script à lancer, une fois codé


class ModuleRegistry:
    """Registre central des ModuleSpec connus de DAEMON."""

    def __init__(self) -> None:
        self._modules: dict[str, ModuleSpec] = {}

    def register(self, spec: ModuleSpec) -> None:
        self._modules[spec.codename.upper()] = spec

    def get(self, codename: str) -> Optional[ModuleSpec]:
        return self._modules.get(codename.upper())

    def all(self) -> list[ModuleSpec]:
        return list(self._modules.values())

    def exists(self, codename: str) -> bool:
        return codename.upper() in self._modules


def build_default_registry() -> ModuleRegistry:
    """
    Construit le registre avec les modules cités dans la cartographie du projet
    (section 2 du document de référence). ARGUS, ROSTER, SPECTRA, ORACLE et
    PULSE_TRACK sont marqués `implemented=True` ; les autres restent
    `implemented=False` : seule leur déclaration existe, aucun code de
    vision/traitement/règles n'est fourni.
    """
    registry = ModuleRegistry()

    registry.register(ModuleSpec(
        codename="ARGUS",
        description="Ingestion multi-caméras + détection d'objets et de personnes",
        depends_on=[],
        # Le backend "yolo" charge torch + un modèle Ultralytics en mémoire (~1-1.5 Go
        # rien que pour torch/CPU, avant même le modèle) : budget relevé en conséquence.
        # Le backend "mock" (HSV, sans torch) tiendrait largement dans ce budget, donc
        # aucun risque à le déclarer large ici même si tu repasses en "mock" plus tard.
        # NOTE : le mode "detect_and_track" (cf. argus/config.py::TrackingModeConfig)
        # réduit le CPU/GPU consommé par frame, PAS la RAM déclarée ici — le modèle
        # chargé (si backend="yolo") reste identique quel que soit le mode temporel.
        ram_mb=1500,
        cpu_cores=1.5,
        gpu_required=False,     # GPU optionnel (accélère le backend "yolo" si présent), jamais obligatoire
        implemented=True,
        entry_point="argus/run_argus.py",
    ))
    registry.register(ModuleSpec(
        codename="SPECTRA",
        description="Amélioration d'image (faible luminosité, contraste, canaux couleur)",
        depends_on=["ARGUS"],
        # Backend "classic" : OpenCV seul (CLAHE, LUT gamma, filtre bilatéral, gray-world),
        # aucun modèle à charger, aucune dépendance lourde (pas de torch/ultralytics/dlib
        # comme ARGUS/ROSTER) : budget nettement plus léger que les deux autres modules.
        ram_mb=350,
        cpu_cores=0.75,
        gpu_required=False,
        implemented=True,
        entry_point="spectra/run_spectra.py",
    ))
    registry.register(ModuleSpec(
        codename="ORACLE",
        description="Identification fine d'objets (marque/modèle) via API externe",
        depends_on=["ARGUS"],
        # Aucun modèle local chargé, quel que soit le backend : "mock" ne fait que du hash
        # perceptuel (OpenCV/NumPy, déjà comptés pour ARGUS) et "google_vision" se contente
        # d'appels HTTP (paquet `requests`, très léger) — module borné par le réseau/l'API,
        # pas par le calcul local. Budget nettement inférieur à ARGUS/ROSTER.
        ram_mb=250,
        cpu_cores=0.5,
        gpu_required=False,
        implemented=True,
        entry_point="oracle/run_oracle.py",
    ))
    registry.register(ModuleSpec(
        codename="ROSTER",
        description="Reconnaissance de personnes connues (opt-in, 100% local)",
        depends_on=["ARGUS"],
        # Le backend "face_recognition" (dlib) charge un modèle de landmarks + un modèle
        # d'encodage ResNet ~ quelques centaines de Mo. Le backend "mock" (Haar Cascade,
        # sans dlib) tiendrait largement dans ce budget, donc aucun risque à le déclarer
        # large ici même si tu repasses en "mock" plus tard (même logique qu'ARGUS/yolo).
        ram_mb=400,
        cpu_cores=1.0,
        gpu_required=False,
        implemented=True,
        entry_point="roster/run_roster.py",
    ))
    registry.register(ModuleSpec(
        codename="PULSE_TRACK",
        description="Moteur de règles et notifications",
        depends_on=["ARGUS", "ROSTER"],
        # Pure logique Python (RuleEngine, cf. pulse_track/rules.py) : aucune image jamais
        # décodée ni traitée (contrairement à ARGUS/ROSTER/SPECTRA/ORACLE), seulement deux
        # connexions socket client (ARGUS + ROSTER) et quelques dict en mémoire pour le
        # cooldown/suivi de présence. Budget le plus léger de tous les modules implémentés.
        ram_mb=150,
        cpu_cores=0.25,
        gpu_required=False,
        implemented=True,
        entry_point="pulse_track/run_pulse_track.py",
    ))
    registry.register(ModuleSpec(
        codename="AEGIS",
        description="Détection de chute / urgence par analyse de posture",
        depends_on=["ARGUS"],
    ))
    registry.register(ModuleSpec(
        codename="VAULT",
        description="Stockage, politique de rétention, chiffrement, contrôle d'accès",
        depends_on=["ARGUS", "SPECTRA", "ORACLE", "ROSTER", "PULSE_TRACK", "AEGIS"],
    ))
    registry.register(ModuleSpec(
        codename="SYS-LOG",
        description="Journal unifié des actions opérateur et événements de tous les modules",
        depends_on=["ARGUS", "SPECTRA", "ORACLE", "ROSTER", "PULSE_TRACK", "AEGIS", "VAULT"],
    ))
    registry.register(ModuleSpec(
        codename="NEXUS-V",
        description="Dashboard : visualisation simultanée de tous les modules actifs",
        depends_on=["ARGUS", "SPECTRA", "ORACLE", "ROSTER", "PULSE_TRACK", "AEGIS", "VAULT", "SYS-LOG"],
    ))

    return registry