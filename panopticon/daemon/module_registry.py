"""
panopticon/daemon/module_registry.py

Registre déclaratif des modules gérés par DAEMON : codename, description,
dépendances et besoins en ressources approximatifs. ARGUS, ROSTER, SPECTRA,
ORACLE, PULSE_TRACK et AEGIS sont désormais implémentés (voir
panopticon/argus/, panopticon/roster/, panopticon/spectra/,
panopticon/oracle/, panopticon/pulse_track/ et panopticon/aegis/) ; les
autres modules restent des déclarations en attente de leur code métier
(`entry_point` et `implemented`).
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
    (section 2 du document de référence). ARGUS, ROSTER, SPECTRA, ORACLE,
    PULSE_TRACK et AEGIS sont marqués `implemented=True` ; les autres restent
    `implemented=False` : seule leur déclaration existe, aucun code de
    vision/traitement/règles n'est fourni.
    """
    registry = ModuleRegistry()

    registry.register(ModuleSpec(
        codename="ARGUS",
        description="Ingestion multi-caméras + détection d'objets et de personnes",
        depends_on=[],
        ram_mb=1500,
        cpu_cores=1.5,
        gpu_required=False,
        implemented=True,
        entry_point="argus/run_argus.py",
    ))
    registry.register(ModuleSpec(
        codename="SPECTRA",
        description="Amélioration d'image (faible luminosité, contraste, canaux couleur)",
        depends_on=["ARGUS"],
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
        # Backend "mock" (défaut) : uniquement l'aspect ratio de la bbox ARGUS + OpenCV/NumPy
        # pour une estimation d'orientation best-effort — aucun modèle, aucune dépendance
        # lourde. Backend "yolo_pose" : réutilise `ultralytics` (déjà nécessaire à ARGUS pour
        # son propre backend "yolo") avec un poids -pose (ex: yolo11n-pose.pt) chargé dans CE
        # process séparé — torch/ultralytics n'étant pas partagé entre processus, le budget
        # déclaré ici anticipe ce cas au même titre qu'ARGUS/ROSTER pour leurs backends lourds
        # respectifs, même si tu restes sur "mock" au démarrage.
        ram_mb=1400,
        cpu_cores=1.0,
        gpu_required=False,     # GPU optionnel (accélère "yolo_pose" si présent), jamais obligatoire
        implemented=True,
        entry_point="aegis/run_aegis.py",
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
