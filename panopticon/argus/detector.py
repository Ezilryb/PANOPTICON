"""
panopticon/argus/detector.py

Détecteurs d'objets/personnes interchangeables derrière une interface
commune (BaseDetector). `MockDetector` utilise de la vision classique
(OpenCV, sans dépendance lourde) et sert de backend par défaut et de socle
de test. `YoloDetector` encapsule un modèle Ultralytics YOLO pour la
détection en conditions réelles (import différé : ARGUS démarre même si
`ultralytics` n'est pas installé, tant que ce backend n'est pas utilisé).
"""

import logging
from abc import ABC, abstractmethod

import cv2
import numpy as np

from .config import DetectorConfig
from .data_types import Detection

logger = logging.getLogger("argus.detector")


class BaseDetector(ABC):
    """Interface commune : tout backend de détection doit l'implémenter."""

    @abstractmethod
    def warmup(self) -> None:
        """Charge le modèle / prépare les ressources avant la première frame réelle."""

    @abstractmethod
    def detect_batch(self, images: list[np.ndarray]) -> list[list[Detection]]:
        """Détecte sur un batch d'images ; renvoie une liste de détections par image, même ordre."""


class MockDetector(BaseDetector):
    """
    Backend "sans dépendance lourde" : détecte les zones de couleur saturée
    (blobs) par seuillage HSV + contours OpenCV. Ne remplace pas un vrai
    modèle de détection, mais permet de valider tout le pipeline ARGUS
    (y compris avec la caméra synthétique) sans installer ultralytics/torch.
    """

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config

    def warmup(self) -> None:
        logger.info("MockDetector prêt (détection par seuillage HSV, aucun modèle à charger)")

    def detect_batch(self, images: list[np.ndarray]) -> list[list[Detection]]:
        return [self._detect_one(image) for image in images]

    def _detect_one(self, image: np.ndarray) -> list[Detection]:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        # Plage large englobant les couleurs vives (saturation/valeur élevées) : suffisant
        # pour retrouver le rectangle de la caméra synthétique ou tout objet très saturé.
        lower = np.array([0, 90, 90])
        upper = np.array([179, 255, 255])
        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[Detection] = []
        min_area = (image.shape[0] * image.shape[1]) * 0.002  # ignore le bruit sous 0.2% de l'image

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            confidence = min(0.99, 0.5 + area / (image.shape[0] * image.shape[1]))
            if confidence < self.config.confidence_threshold:
                continue
            if self.config.classes_filter and "object" not in self.config.classes_filter:
                continue
            detections.append(Detection(
                class_id=0,
                class_name="object",
                confidence=confidence,
                bbox=(float(x), float(y), float(x + w), float(y + h)),
            ))
        return detections


class YoloDetector(BaseDetector):
    """
    Backend de production : modèle Ultralytics YOLO (v8/v11). L'import de
    `ultralytics` est différé jusqu'à `warmup()` pour qu'ARGUS puisse être
    importé/testé même sans cette dépendance installée.
    """

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config
        self._model = None

    def warmup(self) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Le backend 'yolo' nécessite le paquet 'ultralytics' (pip install ultralytics). "
                "Utilisez le backend 'mock' en attendant, ou installez la dépendance."
            ) from exc

        logger.info("Chargement du modèle YOLO (%s, device=%s)...", self.config.weights, self.config.device)
        self._model = YOLO(self.config.weights)
        device = None if self.config.device == "auto" else self.config.device
        # Warmup réel : une inférence sur une image noire initialise CUDA/alloue les buffers
        # avant que la première vraie frame n'ait à supporter ce coût de démarrage.
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self._model.predict(dummy, device=device, verbose=False)
        logger.info("Modèle YOLO chargé et préchauffé")

    def detect_batch(self, images: list[np.ndarray]) -> list[list[Detection]]:
        if self._model is None:
            raise RuntimeError("YoloDetector.warmup() doit être appelé avant detect_batch()")
        if not images:
            return []

        device = None if self.config.device == "auto" else self.config.device
        results = self._model.predict(
            images,
            device=device,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            verbose=False,
        )

        all_detections: list[list[Detection]] = []
        for result in results:
            detections: list[Detection] = []
            names = result.names
            for box in result.boxes:
                class_id = int(box.cls[0])
                class_name = names.get(class_id, str(class_id)) if isinstance(names, dict) else names[class_id]
                if self.config.classes_filter and class_name not in self.config.classes_filter:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                detections.append(Detection(
                    class_id=class_id,
                    class_name=class_name,
                    confidence=float(box.conf[0]),
                    bbox=(x1, y1, x2, y2),
                ))
            all_detections.append(detections)
        return all_detections


def build_detector(config: DetectorConfig) -> BaseDetector:
    """Fabrique le backend demandé par la configuration."""
    if config.backend == "mock":
        return MockDetector(config)
    if config.backend == "yolo":
        return YoloDetector(config)
    raise ValueError(f"Backend de détection inconnu : {config.backend!r} (attendu : 'mock' ou 'yolo')")
