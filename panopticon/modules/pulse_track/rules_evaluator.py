"""PULSE_TRACK — Phase 4 : évaluation des règles (correspondance conditions <-> événement)."""

from __future__ import annotations


def rule_matches(conditions: dict, event: dict) -> bool:
    """Une règle correspond si TOUTES les conditions spécifiées correspondent à l'événement.

    Conditions supportées (toutes optionnelles — absente = "n'importe quelle valeur") :
      - event_type      : type d'événement exact (ex. "person_entered_zone")
      - zone            : zone exacte (ex. "entree")
      - source_module   : module source exact (argus, spectra, oracle, roster…)

    Conditions inconnues : ignorées (permissif, pour rester tolérant aux
    évolutions futures du schéma plutôt que de rejeter silencieusement une
    règle existante).
    """
    if not isinstance(conditions, dict):
        return False
    if "event_type" in conditions and event.get("event_type") != conditions["event_type"]:
        return False
    if "zone" in conditions and event.get("zone") != conditions["zone"]:
        return False
    if "source_module" in conditions and event.get("source_module") != conditions["source_module"]:
        return False
    return True
