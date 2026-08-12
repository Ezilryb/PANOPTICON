"""
panopticon/roster/matcher.py

Matching d'un visage observé contre la base des personnes enrôlées : calcule
la distance euclidienne entre l'embedding observé et CHAQUE embedding de
référence de CHAQUE personne enrôlée, retient la distance minimale. Sous le
seuil configuré -> `known:{nom}` ; sinon -> `unknown`. Aucune trace de
l'embedding observé n'est conservée après le calcul (pas de champ de
stockage ici) : seul le résultat (FaceMatch) survit, conformément au
critère "aucune donnée persistée sur les inconnus" (section 5 du brief).
"""

import logging

import numpy as np

from .config import MatcherConfig
from .data_types import Embedding, FaceMatch, FaceObservation
from .store import PersonStore

logger = logging.getLogger("roster.matcher")


class FaceMatcher:
    """Compare un embedding observé aux embeddings de référence de toutes les personnes enrôlées."""

    def __init__(self, store: PersonStore, config: MatcherConfig) -> None:
        self.store = store
        self.config = config

    def match(self, observation: FaceObservation) -> FaceMatch:
        return self.match_embedding(observation.embedding)

    def match_embedding(self, embedding: Embedding) -> FaceMatch:
        persons = self.store.all()
        if not persons:
            return FaceMatch(matched=False)

        query = np.asarray(embedding, dtype=np.float64)

        best_distance = float("inf")
        best_person = None

        for person in persons:
            if not person.embeddings:
                continue
            refs = np.asarray(person.embeddings, dtype=np.float64)
            if refs.shape[1] != query.shape[0]:
                # Embeddings incompatibles (backend différent entre enrôlement et matching en cours) :
                # on ignore cette personne plutôt que de lever une exception qui stopperait le pipeline.
                logger.warning(
                    "Dimension d'embedding incompatible pour '%s' (%d vs %d attendu) — backend "
                    "différent entre enrôlement et matching ? Personne ignorée pour ce match.",
                    person.name, refs.shape[1], query.shape[0],
                )
                continue

            distances = np.linalg.norm(refs - query, axis=1)
            person_min = float(distances.min())
            if person_min < best_distance:
                best_distance = person_min
                best_person = person

        if best_person is not None and best_distance <= self.config.distance_threshold:
            return FaceMatch(
                matched=True,
                person_id=best_person.person_id,
                name=best_person.name,
                distance=best_distance,
            )

        return FaceMatch(matched=False, distance=best_distance if best_person is not None else None)
