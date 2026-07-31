"""
panopticon/daemon/module_registry.py

Registre déclaratif des modules gérés par DAEMON : codename, description,
dépendances et besoins en ressources approximatifs. ARGUS est désormais
implémenté (voir panopticon/argus/) ; les autres modules restent des
déclarations en attente de leur code métier (`entry_point` et `implemented`).
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
    (section 2 du document de référence). ARGUS est marqué `implemented=True`
    (voir panopticon/argus/run_argus.py) ; les autres restent `implemented=False` :
    seule leur déclaration existe, aucun code de vision/traitement n'est fourni.
    """
    registry = ModuleRegistry()

    registry.register(ModuleSpec(
        codename="ARGUS",
        description="Ingestion multi-caméras + détection d'objets et de personnes",
        depends_on=[],
        ram_mb=1500,             # backend "mock" : quelques centaines de Mo ; backend "yolo" : prévoir 1.5-3 Go selon le modèle
        cpu_cores=1.0,           # base réaliste (le backend "mock" est très léger) ; à relever si backend "yolo" + de nombreuses caméras
        gpu_required=False,     # GPU optionnel (accélère le backend "yolo" si présent), jamais obligatoire
        implemented=True,
        entry_point="argus/run_argus.py",
    ))
    registry.register(ModuleSpec(
        codename="SPECTRA",
        description="Amélioration d'image (faible luminosité, contraste, canaux couleur)",
        depends_on=["ARGUS"],
    ))
    registry.register(ModuleSpec(
        codename="ORACLE",
        description="Identification fine d'objets (marque/modèle) via API externe",
        depends_on=["ARGUS"],
    ))
    registry.register(ModuleSpec(
        codename="ROSTER",
        description="Reconnaissance de personnes connues (opt-in, 100% local)",
        depends_on=["ARGUS"],
    ))
    registry.register(ModuleSpec(
        codename="PULSE_TRACK",
        description="Moteur de règles et notifications",
        depends_on=["ARGUS", "ROSTER"],
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
