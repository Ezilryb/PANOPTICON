"""
panopticon/roster/embedder.py

Backends interchangeables pour la détection de visage + calcul d'embedding,
même principe que `argus/detector.py` (MockDetector / YoloDetector) : un
backend "mock" sans dépendance lourde (Haar Cascade OpenCV + descripteur
pixel) pour développer/tester tout le pipeline ROSTER sans installer dlib,
et un backend "face_recognition" (dlib, import différé) pour la précision de
production. Le format d'embedding (liste de floats) est le même quel que
soit le backend, mais un embedding calculé par un backend n'est JAMAIS
comparable à un embedding calculé par l'autre : `RosterConfig.embedder`
doit rester identique entre l'enrôlement et le matching.
"""

import logging
from abc import ABC, abstractmethod

import cv2
import numpy as np

from .config import EmbedderConfig
from .data_types import BBox, Embedding

logger = logging.getLogger("roster.embedder")


class BaseEmbedder(ABC):
    """Interface commune : tout backend de reconnaissance faciale doit l'implémenter."""

    @abstractmethod
    def warmup(self) -> None:
        """Charge le modèle / prépare les ressources avant la première image réelle."""

    @abstractmethod
    def detect_and_embed(self, image: np.ndarray) -> list[tuple[BBox, Embedding]]:
        """Détecte les visages dans `image` et renvoie une liste de (bbox, embedding)."""

    def embed_single_face(self, image: np.ndarray) -> Embedding | None:
        """
        Utilisé à l'enrôlement : suppose une seule photo de référence par visage
        et renvoie l'embedding du plus grand visage détecté (None si aucun visage).
        """
        results = self.detect_and_embed(image)
        if not results:
            return None
        # Le plus grand visage détecté est retenu comme sujet principal de la photo.
        results.sort(key=lambda r: (r[0][2] - r[0][0]) * (r[0][3] - r[0][1]), reverse=True)
        return results[0][1]


class MockEmbedder(BaseEmbedder):
    """
    Backend "sans dépendance lourde" : détection de visage par Haar Cascade
    (fourni avec OpenCV, aucun poids externe à télécharger) et embedding
    déterministe dérivé des pixels du visage recadré (niveaux de gris,
    redimensionné, aplati, normalisé). Ne remplace pas un vrai modèle de
    reconnaissance faciale — sert à valider tout le pipeline ROSTER
    (enrôlement -> matching -> évènements) sans installer dlib/face_recognition.
    """

    _EMBED_SIZE = 32  # visage recadré en 32x32 -> embedding de dimension 1024

    def __init__(self, config: EmbedderConfig) -> None:
        self.config = config
        self._cascade: cv2.CascadeClassifier | None = None

    def warmup(self) -> None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)
        if self._cascade.empty():
            raise RuntimeError(f"Impossible de charger le classifieur Haar Cascade ({cascade_path})")
        logger.info("MockEmbedder prêt (détection Haar Cascade, embedding par descripteur pixel)")

    def detect_and_embed(self, image: np.ndarray) -> list[tuple[BBox, Embedding]]:
        if self._cascade is None:
            raise RuntimeError("MockEmbedder.warmup() doit être appelé avant detect_and_embed()")

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20))

        results: list[tuple[BBox, Embedding]] = []
        for (x, y, w, h) in faces:
            crop = gray[y:y + h, x:x + w]
            embedding = self._embed_crop(crop)
            bbox: BBox = (float(x), float(y), float(x + w), float(y + h))
            results.append((bbox, embedding))
        return results

    def _embed_crop(self, face_gray: np.ndarray) -> Embedding:
        resized = cv2.resize(face_gray, (self._EMBED_SIZE, self._EMBED_SIZE))
        # Égalisation d'histogramme pour limiter la sensibilité à la luminosité,
        # puis normalisation [0, 1] : reste un descripteur simple, pas un modèle appris.
        equalized = cv2.equalizeHist(resized)
        normalized = equalized.astype(np.float64) / 255.0
        return normalized.flatten().tolist()


class FaceRecognitionEmbedder(BaseEmbedder):
    """
    Backend de production : `face_recognition` (dlib), 100% local, aucun appel
    réseau (exigence stricte ROSTER, cf. brief projet section 5/10). L'import
    est différé jusqu'à `warmup()` pour qu'ARGUS/ROSTER restent importables
    même sans `face_recognition`/`dlib` installés tant que ce backend n'est
    pas utilisé (même principe que YoloDetector côté ARGUS).
    """

    def __init__(self, config: EmbedderConfig) -> None:
        self.config = config
        self._face_recognition = None

    def warmup(self) -> None:
        try:
            import face_recognition
        except ImportError as exc:
            raise RuntimeError(
                "Le backend 'face_recognition' nécessite les paquets 'face_recognition' et 'dlib' "
                "(pip install face_recognition). Utilisez le backend 'mock' en attendant, ou installez "
                "les dépendances."
            ) from exc
        self._face_recognition = face_recognition
        logger.info(
            "FaceRecognitionEmbedder prêt (modèle=%s, upsample=%d, jitters=%d)",
            self.config.model, self.config.upsample_times, self.config.num_jitters,
        )

    def detect_and_embed(self, image: np.ndarray) -> list[tuple[BBox, Embedding]]:
        if self._face_recognition is None:
            raise RuntimeError("FaceRecognitionEmbedder.warmup() doit être appelé avant detect_and_embed()")

        # face_recognition attend du RGB ; nos images caméra/ARGUS sont en BGR (convention OpenCV).
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB) if image.ndim == 3 else image

        locations = self._face_recognition.face_locations(
            rgb, number_of_times_to_upsample=self.config.upsample_times, model=self.config.model,
        )
        if not locations:
            return []

        encodings = self._face_recognition.face_encodings(
            rgb, known_face_locations=locations, num_jitters=self.config.num_jitters,
        )

        results: list[tuple[BBox, Embedding]] = []
        for (top, right, bottom, left), encoding in zip(locations, encodings):
            bbox: BBox = (float(left), float(top), float(right), float(bottom))
            results.append((bbox, encoding.tolist()))
        return results


def build_embedder(config: EmbedderConfig) -> BaseEmbedder:
    """Fabrique le backend demandé par la configuration."""
    if config.backend == "mock":
        return MockEmbedder(config)
    if config.backend == "face_recognition":
        return FaceRecognitionEmbedder(config)
    raise ValueError(f"Backend d'embedding inconnu : {config.backend!r} (attendu : 'mock' ou 'face_recognition')")
