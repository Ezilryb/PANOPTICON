"""
panopticon/oracle/identifier.py

Backends d'identification fine d'objets (marque/modèle) interchangeables
derrière une interface commune (BaseIdentifier), même principe que
`argus/detector.py` et `roster/embedder.py`. `MockIdentifier` ne fait AUCUN
appel réseau et sert de socle de test/pipeline. `GoogleVisionIdentifier`
interroge l'API Google Cloud Vision (Web Detection) — "l'équivalent
officiel" d'une recherche d'image inversée côté Google (cf. section 5 du
brief projet, qui liste aussi Bing Visual Search et SerpApi comme
alternatives possibles derrière la même interface).

RAPPEL DE PÉRIMÈTRE (critère d'acceptation section 10 du brief projet) :
ORACLE ne s'exécute JAMAIS sur un crop contenant un visage. Ce fichier ne
fait qu'identifier un objet dans un crop qu'on lui donne — c'est
`pipeline.py` qui garantit qu'un crop de "person" ne lui est jamais transmis
(double garde-fou : liste blanche de classes + exclusion en dur, cf.
config.py::PERSON_CLASSES). Aucun backend ici ne doit être appelé
directement sur une frame entière ou un crop de personne.
"""

import base64
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Optional

import cv2
import numpy as np

from .config import IdentifierConfig
from .data_types import ObjectIdentification

logger = logging.getLogger("oracle.identifier")


class BaseIdentifier(ABC):
    """Interface commune : tout backend d'identification doit l'implémenter."""

    @abstractmethod
    def warmup(self) -> None:
        """Vérifie les prérequis (dépendances, clé API...) avant le premier crop réel."""

    @abstractmethod
    def identify(self, image_crop: np.ndarray) -> Optional[ObjectIdentification]:
        """Identifie `image_crop` (BGR, uint8) ; renvoie None si aucune identification exploitable."""


class MockIdentifier(BaseIdentifier):
    """
    Backend "sans dépendance lourde, zéro réseau" : ne prétend identifier
    AUCUNE vraie marque/modèle. Dérive une étiquette déterministe de la
    couleur moyenne et de la taille du crop, uniquement pour valider tout le
    pipeline ORACLE de bout en bout (cache, débit limité, publication) sans
    clé API ni connexion réseau — même rôle que MockDetector pour ARGUS et
    MockEmbedder pour ROSTER.
    """

    def warmup(self) -> None:
        logger.info("MockIdentifier prêt (étiquette déterministe locale, aucun appel réseau)")

    def identify(self, image_crop: np.ndarray) -> Optional[ObjectIdentification]:
        if image_crop.size == 0:
            return None
        mean_color = image_crop.reshape(-1, image_crop.shape[-1]).mean(axis=0) if image_crop.ndim == 3 else [image_crop.mean()]
        digest = hashlib.sha1(image_crop.tobytes()).hexdigest()[:6]
        label = f"objet-test-{digest}"
        return ObjectIdentification(
            label=label,
            confidence=0.75,
            source="mock",
            candidates=[f"variante-{digest[:3]}", f"{'-'.join(f'{int(c):02x}' for c in mean_color[:3])}"],
        )


class GoogleVisionIdentifier(BaseIdentifier):
    """
    Backend de production : Google Cloud Vision API, fonctionnalité "Web
    Detection", appelée directement en REST (clé API + `requests`) plutôt
    que via le SDK complet `google-cloud-vision` — dépendance nettement plus
    légère, suffisante pour ce seul appel. Import de `requests` différé
    jusqu'à `warmup()`, même principe que YoloDetector/FaceRecognitionEmbedder :
    ORACLE reste important/testable (backend "mock") même sans `requests`
    installé tant que ce backend n'est pas utilisé.

    La clé API est lue depuis la variable d'environnement désignée par
    `config.api_key_env_var` — JAMAIS depuis le fichier de configuration
    JSON, pour ne jamais risquer de committer un secret dans oracle.json.
    """

    def __init__(self, config: IdentifierConfig) -> None:
        self.config = config
        self._requests = None
        self._api_key: Optional[str] = None

    def warmup(self) -> None:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "Le backend 'google_vision' nécessite le paquet 'requests' (pip install requests). "
                "Utilisez le backend 'mock' en attendant, ou installez la dépendance."
            ) from exc

        import os
        api_key = os.environ.get(self.config.api_key_env_var)
        if not api_key:
            raise RuntimeError(
                f"Le backend 'google_vision' nécessite la variable d'environnement "
                f"'{self.config.api_key_env_var}' (clé API Google Cloud Vision). Elle n'a pas été trouvée. "
                f"Utilisez le backend 'mock' en attendant, ou exportez cette variable avant de démarrer ORACLE."
            )

        self._requests = requests
        self._api_key = api_key
        logger.info("GoogleVisionIdentifier prêt (endpoint=%s)", self.config.endpoint)

    def identify(self, image_crop: np.ndarray) -> Optional[ObjectIdentification]:
        if self._requests is None or self._api_key is None:
            raise RuntimeError("GoogleVisionIdentifier.warmup() doit être appelé avant identify()")
        if image_crop.size == 0:
            return None

        ok, encoded = cv2.imencode(".jpg", image_crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            logger.warning("Échec de l'encodage JPEG du crop, identification ignorée")
            return None
        content_b64 = base64.b64encode(encoded.tobytes()).decode("ascii")

        body = {
            "requests": [{
                "image": {"content": content_b64},
                "features": [{"type": "WEB_DETECTION", "maxResults": self.config.max_results}],
            }]
        }

        try:
            response = self._requests.post(
                self.config.endpoint,
                params={"key": self._api_key},
                json=body,
                timeout=self.config.timeout_s,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 — un appel API qui échoue ne doit jamais interrompre la pipeline ORACLE
            logger.warning("Appel à l'API Google Vision échoué : %s", exc)
            return None

        return self._parse_web_detection(payload)

    def _parse_web_detection(self, payload: dict) -> Optional[ObjectIdentification]:
        responses = payload.get("responses", [])
        if not responses:
            return None
        web_detection = responses[0].get("webDetection", {})

        entities = [
            e for e in web_detection.get("webEntities", [])
            if e.get("description")
        ]
        entities.sort(key=lambda e: e.get("score", 0.0), reverse=True)

        if entities:
            top = entities[0]
            label = top["description"]
            confidence = float(min(1.0, max(0.0, top.get("score", 0.0))))
            candidates = [e["description"] for e in entities[1:self.config.max_results]]
        else:
            # webEntities vide : on retombe sur bestGuessLabels si disponible, mais l'API ne
            # fournit aucun score pour ce champ — confiance heuristique fixe et volontairement
            # prudente (documentée ici comme telle, pas un vrai score calibré par Google).
            best_guess = web_detection.get("bestGuessLabels", [])
            if not best_guess:
                return None
            label = best_guess[0]["label"]
            confidence = 0.5
            candidates = []

        if confidence < self.config.confidence_threshold:
            logger.debug("Identification '%s' sous le seuil de confiance (%.2f < %.2f), ignorée",
                         label, confidence, self.config.confidence_threshold)
            return None

        return ObjectIdentification(label=label, confidence=confidence, source="google_vision", candidates=candidates)


def build_identifier(config: IdentifierConfig) -> BaseIdentifier:
    """Fabrique le backend demandé par la configuration."""
    if config.backend == "mock":
        return MockIdentifier()
    if config.backend == "google_vision":
        return GoogleVisionIdentifier(config)
    raise ValueError(f"Backend d'identification inconnu : {config.backend!r} (attendu : 'mock' ou 'google_vision')")
