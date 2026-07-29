"""ORACLE — Phase 3 : identification fine d'objets, entièrement locale.

Aucun appel réseau à l'exécution : utilise un classifieur d'images
pré-entraîné (torchvision, ImageNet-1k, ~2,5 M de paramètres) qui tourne sur
CPU, sur l'appareil. Comme YOLO le fait déjà pour ARGUS, les poids doivent
être téléchargés une seule fois avant la première utilisation (mise en cache
locale ensuite) — voir le README pour un déploiement entièrement hors-ligne
(pré-téléchargement sur une machine connectée, puis copie du cache).

Ne traite JAMAIS les détections étiquetées "person" par ARGUS : ORACLE ne
fait aucune analyse de personnes ni de visages — ce rôle reste réservé à
ROSTER (opt-in, consentement explicite, traitement local).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Étiquettes ARGUS/YOLO qu'ORACLE ne traite jamais.
EXCLUDED_LABELS = {"person"}


@dataclass
class Identification:
    label: str
    confidence: float


class ObjectIdentifier:
    """Classifieur d'images local (torchvision MobileNetV3-Small, ImageNet-1k).

    Le modèle n'est chargé (et ses poids téléchargés si absents du cache
    local) qu'au premier appel à ``identify`` — jamais à l'import du module.
    """

    def __init__(self, top_k: int = 1, device: str = "cpu") -> None:
        self.top_k = top_k
        self.device = device
        self._model = None
        self._categories: list[str] | None = None
        self._transform = None
        self._torch = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from torchvision import models
        from torchvision.models import MobileNet_V3_Small_Weights

        logger.info("Chargement du classifieur local ORACLE (MobileNetV3-Small, ImageNet-1k)")
        weights = MobileNet_V3_Small_Weights.DEFAULT
        model = models.mobilenet_v3_small(weights=weights)
        model.eval().to(self.device)

        self._model = model
        self._categories = weights.meta["categories"]
        self._transform = weights.transforms()
        self._torch = torch

    def identify(self, image_bgr: np.ndarray) -> list[Identification]:
        """Classifie un crop d'objet (BGR, format OpenCV).

        Ne jamais appeler sur un crop de personne — filtrer en amont via
        ``EXCLUDED_LABELS`` (c'est ce que fait ``modules/oracle/service.py``).
        """
        self._load()
        import cv2
        from PIL import Image

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        tensor = self._transform(Image.fromarray(rgb)).unsqueeze(0).to(self.device)

        with self._torch.no_grad():
            logits = self._model(tensor)
            probs = self._torch.nn.functional.softmax(logits[0], dim=0)

        top = self._torch.topk(probs, min(self.top_k, probs.shape[0]))
        return [
            Identification(label=self._categories[idx], confidence=float(prob))
            for prob, idx in zip(top.values.tolist(), top.indices.tolist())
        ]
