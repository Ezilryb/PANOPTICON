"""ROSTER — moteur de reconnaissance faciale, entièrement local.

Aucun appel réseau à l'exécution (même principe qu'ORACLE). Détection de
visage (MTCNN) et empreinte faciale (InceptionResnetV1 pré-entraîné sur
VGGFace2), via facenet-pytorch — tourne sur CPU, sur l'appareil.

Ne reconnaît QUE les personnes explicitement enrôlées avec consentement
horodaté (voir api/routes/persons.py). En l'absence de correspondance
suffisante avec une empreinte enrôlée, ``find_best_match`` ne retourne
RIEN : aucune tentative d'identification n'est faite sur une personne non
enrôlée.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Seuil de similarité cosinus par défaut pour considérer une correspondance.
# À calibrer sur des données réelles : plus haut = moins de faux positifs
# mais plus de faux négatifs (personne enrôlée non reconnue).
DEFAULT_MATCH_THRESHOLD = 0.65


@dataclass
class FaceEmbedding:
    vector: list[float]


class FaceEngine:
    """Détection + extraction d'empreinte faciale locale (MTCNN + InceptionResnetV1).

    Le modèle n'est chargé (et ses poids téléchargés si absents du cache
    local) qu'au premier appel à ``extract_embedding`` — jamais à l'import.
    """

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self._mtcnn = None
        self._resnet = None
        self._torch = None

    def _load(self) -> None:
        if self._mtcnn is not None:
            return
        import torch
        from facenet_pytorch import MTCNN, InceptionResnetV1

        logger.info("Chargement du moteur facial local ROSTER (MTCNN + InceptionResnetV1)")
        self._mtcnn = MTCNN(keep_all=False, device=self.device)
        self._resnet = InceptionResnetV1(pretrained="vggface2").eval().to(self.device)
        self._torch = torch

    def extract_embedding(self, image_bgr: np.ndarray) -> FaceEmbedding | None:
        """Détecte le plus grand visage de l'image et retourne son empreinte (ou None si aucun visage)."""
        self._load()
        import cv2
        from PIL import Image

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        face_tensor = self._mtcnn(Image.fromarray(rgb))
        if face_tensor is None:
            return None

        with self._torch.no_grad():
            embedding = self._resnet(face_tensor.unsqueeze(0).to(self.device))
        return FaceEmbedding(vector=embedding[0].tolist())


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def find_best_match(
    embedding: list[float],
    enrolled: list[tuple[str, list[float]]],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> tuple[str, float] | None:
    """Retourne (person_id, score) du meilleur match si au-dessus du seuil, sinon None.

    Ne retourne jamais de résultat pour une personne non enrôlée : c'est le
    mécanisme central qui empêche toute identification de visiteur non
    consentant.
    """
    best_id: str | None = None
    best_score = -1.0
    for person_id, ref_vector in enrolled:
        if not ref_vector:
            continue
        score = cosine_similarity(embedding, ref_vector)
        if score > best_score:
            best_id, best_score = person_id, score
    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None
