"""
panopticon/pulse_track/config.py

Configuration de PULSE_TRACK : règles (déclencheur, conditions, sévérité,
anti-spam), connexions ARGUS/ROSTER et bus de publication propre. Sans
règle configurée, PULSE_TRACK démarre et écoute mais ne déclenche jamais rien.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pulse_track.config")

# Déclencheurs reconnus par RuleCondition.trigger — vérifiés ici ET par la
# pipeline (défense en profondeur, même principe que PERSON_CLASSES côté
# ORACLE), pour ne jamais laisser une règle mal orthographiée s'installer
# silencieusement sans jamais se déclencher ni avertir personne.
VALID_TRIGGERS = frozenset({"known_person", "unknown_person", "object_class", "track_dwell"})


@dataclass
class RuleCondition:
    """
    Condition d'une règle. `trigger` détermine quels autres champs sont
    pertinents (les autres sont simplement ignorés) :
      - "known_person"   : une personne enrôlée est reconnue par ROSTER.
                            `person_names` vide = n'importe quelle personne
                            connue ; sinon liste blanche de noms.
      - "unknown_person"  : ROSTER rapporte un visage "unknown".
      - "object_class"    : ARGUS détecte une classe listée dans `object_classes`.
      - "track_dwell"     : une piste ARGUS (track_id) reste détectée en
                            continu depuis au moins `dwell_seconds`.
    """
    trigger: str
    camera_ids: list[str] = field(default_factory=list)       # vide = toutes les caméras
    person_names: list[str] = field(default_factory=list)     # "known_person" uniquement
    object_classes: list[str] = field(default_factory=list)   # "object_class"/"track_dwell"
    dwell_seconds: float = 10.0                                 # "track_dwell" uniquement
    min_confidence: float = 0.0                                 # confiance ARGUS minimale (object_class/track_dwell)
    hours_start: Optional[str] = None    # "HH:MM" ; plage horaire optionnelle (ex: "22:00" -> "06:00")
    hours_end: Optional[str] = None      # None des deux côtés = actif 24h/24


@dataclass
class RuleConfig:
    """Une règle complète : identité, condition de déclenchement, sévérité et anti-spam."""
    rule_id: str
    name: str
    condition: RuleCondition
    enabled: bool = True
    cooldown_s: float = 60.0        # délai minimal avant un nouveau déclenchement de LA MÊME règle pour LE MÊME contexte (piste/personne/caméra)
    severity: str = "info"          # libre ("info"/"warning"/"critical"...) — interprété par NEXUS-V, pas ici
    message_template: str = "{rule_name} déclenchée sur {camera_id}"


@dataclass
class ArgusConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 8765          # port du bus ArgusPublisher


@dataclass
class RosterConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 8766          # port du bus RosterPublisher


@dataclass
class PublisherConfig:
    host: str = "127.0.0.1"
    port: int = 8769          # port du bus de publication propre à PULSE_TRACK (PulseTrackEvent) —
                               # distinct d'ARGUS (8765), ROSTER (8766), SPECTRA (8767), ORACLE (8768)


@dataclass
class PulseTrackConfig:
    rules: list[RuleConfig] = field(default_factory=list)
    argus: ArgusConnectionConfig = field(default_factory=ArgusConnectionConfig)
    roster: RosterConnectionConfig = field(default_factory=RosterConnectionConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    log_stats_every_s: float = 10.0


def default_config() -> PulseTrackConfig:
    """Configuration prête à l'emploi : aucune règle. PULSE_TRACK démarre et se connecte, mais ne publie jamais rien tant qu'aucune règle n'est ajoutée."""
    return PulseTrackConfig()


def _parse_condition(raw: dict) -> RuleCondition:
    trigger = raw["trigger"]
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"Déclencheur de règle inconnu : {trigger!r} (attendu : {sorted(VALID_TRIGGERS)})")
    return RuleCondition(
        trigger=trigger,
        camera_ids=raw.get("camera_ids", []),
        person_names=raw.get("person_names", []),
        object_classes=raw.get("object_classes", []),
        dwell_seconds=raw.get("dwell_seconds", 10.0),
        min_confidence=raw.get("min_confidence", 0.0),
        hours_start=raw.get("hours_start"),
        hours_end=raw.get("hours_end"),
    )


def load_config(path: Optional[str]) -> PulseTrackConfig:
    """
    Charge la configuration depuis `path` (JSON). Si `path` est None ou que
    le fichier est introuvable, retombe sur `default_config()` (aucune règle)
    et journalise un avertissement plutôt que d'échouer — même principe que
    pour ARGUS/ROSTER/SPECTRA/ORACLE.

    Une règle individuellement invalide (déclencheur inconnu, champ requis
    absent) est rejetée et journalisée en erreur SANS faire échouer le
    chargement des autres règles valides du même fichier.
    """
    if not path:
        logger.warning("Aucun fichier de configuration fourni, utilisation de la configuration par défaut (aucune règle)")
        return default_config()

    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Fichier de configuration introuvable (%s), utilisation de la configuration par défaut", path)
        return default_config()

    raw = json.loads(file_path.read_text(encoding="utf-8"))

    rules: list[RuleConfig] = []
    for raw_rule in raw.get("rules", []):
        try:
            condition = _parse_condition(raw_rule["condition"])
            rules.append(RuleConfig(
                rule_id=raw_rule["rule_id"],
                name=raw_rule["name"],
                condition=condition,
                enabled=raw_rule.get("enabled", True),
                cooldown_s=raw_rule.get("cooldown_s", 60.0),
                severity=raw_rule.get("severity", "info"),
                message_template=raw_rule.get("message_template", "{rule_name} déclenchée sur {camera_id}"),
            ))
        except (KeyError, ValueError) as exc:
            logger.error("Règle ignorée dans %s (invalide, id=%s) : %s", path, raw_rule.get("rule_id", "?"), exc)

    argus = ArgusConnectionConfig(**raw.get("argus", {}))
    roster = RosterConnectionConfig(**raw.get("roster", {}))
    publisher = PublisherConfig(**raw.get("publisher", {}))

    config = PulseTrackConfig(
        rules=rules,
        argus=argus,
        roster=roster,
        publisher=publisher,
        log_stats_every_s=raw.get("log_stats_every_s", 10.0),
    )
    logger.info(
        "Configuration PULSE_TRACK chargée depuis %s (%d règle(s) active(s) / %d déclarée(s))",
        path, sum(1 for r in config.rules if r.enabled), len(config.rules),
    )
    return config