"""
panopticon/aegis/posture_analyzer.py

Backends d'analyse de posture interchangeables derrière une interface
commune (BasePostureAnalyzer), même principe que `argus/detector.py`,
`roster/embedder.py`, `spectra/enhancer.py` et `oracle/identifier.py`.
`MockPostureAnalyzer` (défaut) ne dépend que du bbox ARGUS + OpenCV/NumPy
(zéro dépendance lourde). `YoloPoseAnalyzer` encapsule un modèle Ultralytics
YOLO-pose pour une estimation par points-clés en conditions réelles (import
différé : AEGIS démarre même si `ultralytics` n'est pas installé, tant que
ce backend n'est pas utilisé — même principe que YoloDetector côté ARGUS).

CHOIX D'ARCHITECTURE (écart assumé par rapport au brief projet) : le brief
(section 4, stack technique) recommande MediaPipe Pose pour AEGIS. Ce n'est
pas ce qui est implémenté ici : le backend de production utilise Ultralytics
YOLO avec un poids "-pose" (ex: yolo11n-pose.pt) plutôt que MediaPipe. Deux
raisons à ce choix, cohérentes avec le reste de l'architecture déjà en
place (même esprit que l'écart documenté dans spectra/pipeline.py) :
  1. Dépendance déjà présente : `ultralytics` est déjà nécessaire au backend
     "yolo" d'ARGUS (cf. argus/detector.py, requirements.txt), et
     `yolo11n.pt` est déjà présent à la racine du projet. Ajouter MediaPipe
     introduirait une DEUXIÈME bibliothèque de vision lourde pour un besoin
     que l'existante couvre déjà, avec le même import différé et la même
     API `.predict()` que le backend "yolo" d'ARGUS.
  2. Cohérence d'outillage : un opérateur qui a déjà réglé/testé le backend
     "yolo" d'ARGUS retrouve les mêmes réglages (device, poids, seuils) pour
     AEGIS, plutôt que deux stacks de vision différentes à maintenir.
Si une analyse plus fine par squelette (ex: distinguer un affaissement
contre un mur d'une chute à plat) devenait nécessaire, un backend MediaPipe
pourrait être ajouté derrière la même interface `BasePostureAnalyzer` sans
toucher au reste du pipeline — la porte reste ouverte, ce n'est pas exclu
par principe, seulement pas nécessaire pour la V1.

GARDE-FOU DE PÉRIMÈTRE (cf. data_types.py et section 2 du brief projet) :
les DEUX backends ci-dessous classifient une géométrie (debout/allongé),
jamais une émotion, une intention ou un niveau de dangerosité. Le
constructeur `PostureResult` lève une exception si `posture` sort du
vocabulaire fermé `VALID_POSTURES` — défense en profondeur, pas seulement
une discipline de code.
"""

import logging
import math
from abc import ABC, abstractmethod
from typing import Optional

import cv2
import numpy as np

from .config import AnalyzerConfig
from .data_types import BBox, PostureResult

logger = logging.getLogger("aegis.posture_analyzer")

# Indices des points-clés COCO (17 points), format Ultralytics YOLO-pose — utilisés
# uniquement par YoloPoseAnalyzer, cf. plus bas.
_LEFT_SHOULDER, _RIGHT_SHOULDER = 5, 6
_LEFT_HIP, _RIGHT_HIP = 11, 12


class BasePostureAnalyzer(ABC):
    """Interface commune : tout backend d'analyse de posture doit l'implémenter."""

    @abstractmethod
    def warmup(self) -> None:
        """Charge le modèle / prépare les ressources avant le premier recadrage réel."""

    @abstractmethod
    def analyze(self, crop: np.ndarray, bbox: BBox) -> PostureResult:
        """
        Analyse la posture d'UNE personne. `bbox` est la bbox ARGUS D'ORIGINE
        (coordonnées frame pleine résolution, sans marge) — utilisée pour le
        calcul d'aspect ratio, qui doit rester indépendant de toute marge de
        recadrage appliquée en amont par la pipeline. `crop` est l'image
        recadrée (avec marge) correspondante, utilisée pour l'estimation
        best-effort de l'orientation (mock) ou l'inférence de points-clés
        (yolo_pose).
        """


# ---------------------------------------------------------------------- #
# Fonctions pures partagées (classification par aspect ratio) — réutilisées
# par MockPostureAnalyzer ET par YoloPoseAnalyzer en repli (cf. plus bas),
# et testables isolément sans backend ni image (même esprit que
# oracle/identifier.py::GoogleVisionIdentifier._parse_web_detection).
# ---------------------------------------------------------------------- #

def classify_by_aspect_ratio(aspect_ratio: float, lying_threshold: float, upright_threshold: float) -> str:
    """largeur/hauteur >= lying_threshold -> "lying" ; <= upright_threshold -> "upright" ; sinon "uncertain"."""
    if aspect_ratio >= lying_threshold:
        return "lying"
    if aspect_ratio <= upright_threshold:
        return "upright"
    return "uncertain"


def confidence_from_aspect_ratio(aspect_ratio: float, lying_threshold: float, upright_threshold: float) -> float:
    """
    Confiance croissante avec la distance au seuil franchi (plus l'aspect
    ratio est loin de la frontière de décision, plus la classification est
    fiable), plafonnée à 0.95 — jamais 1.0 pour un simple ratio géométrique,
    qui reste une approximation grossière de la posture réelle.
    """
    if aspect_ratio >= lying_threshold:
        margin = (aspect_ratio - lying_threshold) / max(lying_threshold, 1e-6)
        return float(min(0.95, 0.55 + margin))
    if aspect_ratio <= upright_threshold:
        margin = (upright_threshold - aspect_ratio) / max(upright_threshold, 1e-6)
        return float(min(0.95, 0.55 + margin))
    # Zone "uncertain" : confiance faible, un peu plus élevée aux abords des seuils qu'en son centre.
    mid = (lying_threshold + upright_threshold) / 2.0
    half_span = max((lying_threshold - upright_threshold) / 2.0, 1e-6)
    dist_from_mid = abs(aspect_ratio - mid)
    return float(0.3 + 0.2 * min(1.0, dist_from_mid / half_span))


def estimate_orientation_deg(crop: np.ndarray) -> Optional[float]:
    """
    Estimation best-effort (0° = verticale, 90° = horizontale) de
    l'orientation principale du contenu de `crop`, via les moments d'image
    d'une carte de contours (Canny) — pas de segmentation dédiée (le fond
    d'une vraie photo n'est pas uniforme comme celui de la caméra
    synthétique de test) : une approximation diagnostique, jamais la base
    de la classification `posture` elle-même (cf. docstring du module).
    Renvoie None si le recadrage est trop petit ou trop peu texturé pour
    une estimation stable — jamais une valeur inventée.
    """
    if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    edges = cv2.Canny(gray, 50, 150)
    moments = cv2.moments(edges, binaryImage=True)

    if moments["m00"] < 1.0:
        return None  # quasi aucun contour exploitable (recadrage flou/uniforme)

    mu20 = moments["mu20"] / moments["m00"]
    mu02 = moments["mu02"] / moments["m00"]
    mu11 = moments["mu11"] / moments["m00"]

    if abs(mu20 - mu02) < 1e-6 and abs(mu11) < 1e-6:
        return None  # distribution ~isotrope : angle principal non défini de façon stable

    # Angle du grand axe par rapport à l'horizontale (repère image), converti en écart à la
    # verticale (0°=vertical, 90°=horizontal) pour rester cohérent avec YoloPoseAnalyzer.
    theta_deg = math.degrees(0.5 * math.atan2(2 * mu11, mu20 - mu02))
    orientation_deg = 90.0 - abs(theta_deg)
    return float(max(0.0, min(90.0, orientation_deg)))


class MockPostureAnalyzer(BasePostureAnalyzer):
    """
    Backend "sans dépendance lourde" (défaut) : classifie la posture à
    partir du SEUL aspect ratio de la bbox ARGUS (largeur/hauteur) — une
    personne debout/assise produit une bbox plus haute que large, une
    personne allongée une bbox plus large que haute. C'est une technique
    volontairement simple mais réelle (pas un artifice de démo comme le
    seuillage HSV de MockDetector), documentée comme telle. `orientation_deg`
    est calculé en best-effort sur le recadrage via `estimate_orientation_deg`
    à titre diagnostique, sans jamais peser sur la classification retenue.
    """

    def __init__(self, config: AnalyzerConfig) -> None:
        self.config = config

    def warmup(self) -> None:
        logger.info("MockPostureAnalyzer prêt (classification par aspect ratio, aucun modèle à charger)")

    def analyze(self, crop: np.ndarray, bbox: BBox) -> PostureResult:
        x1, y1, x2, y2 = bbox
        width = max(1e-6, x2 - x1)
        height = max(1e-6, y2 - y1)
        aspect_ratio = width / height

        posture = classify_by_aspect_ratio(
            aspect_ratio, self.config.lying_aspect_ratio_threshold, self.config.upright_aspect_ratio_threshold,
        )
        confidence = confidence_from_aspect_ratio(
            aspect_ratio, self.config.lying_aspect_ratio_threshold, self.config.upright_aspect_ratio_threshold,
        )
        orientation_deg = estimate_orientation_deg(crop)

        return PostureResult(
            posture=posture, confidence=confidence, aspect_ratio=aspect_ratio,
            orientation_deg=orientation_deg, source="mock",
        )


class YoloPoseAnalyzer(BasePostureAnalyzer):
    """
    Backend de production : modèle Ultralytics YOLO-pose (cf. CHOIX
    D'ARCHITECTURE dans la docstring du module pour la justification du
    remplacement de MediaPipe Pose suggéré par le brief). Calcule l'angle du
    tronc (ligne milieu-épaules -> milieu-hanches) par rapport à la
    verticale ; se replie sur la classification par aspect ratio (identique
    au backend mock) si trop peu de points-clés dépassent
    `keypoint_confidence_threshold` — dégradation explicite, jamais une
    valeur inventée à partir de points-clés non fiables.
    """

    def __init__(self, config: AnalyzerConfig) -> None:
        self.config = config
        self._model = None

    def warmup(self) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Le backend 'yolo_pose' nécessite le paquet 'ultralytics' (pip install ultralytics). "
                "Utilisez le backend 'mock' en attendant, ou installez la dépendance."
            ) from exc

        logger.info("Chargement du modèle YOLO-pose (%s, device=%s)...", self.config.pose_weights, self.config.device)
        self._model = YOLO(self.config.pose_weights)
        device = None if self.config.device == "auto" else self.config.device
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self._model.predict(dummy, device=device, verbose=False)
        logger.info("Modèle YOLO-pose chargé et préchauffé")

    def analyze(self, crop: np.ndarray, bbox: BBox) -> PostureResult:
        if self._model is None:
            raise RuntimeError("YoloPoseAnalyzer.warmup() doit être appelé avant analyze()")

        x1, y1, x2, y2 = bbox
        width = max(1e-6, x2 - x1)
        height = max(1e-6, y2 - y1)
        aspect_ratio = width / height

        angle_deg, keypoint_confidence = self._torso_angle_from_keypoints(crop)

        if angle_deg is None:
            # Repli explicite : mêmes seuils/fonctions que le backend mock, source restant
            # "yolo_pose" (orientation_deg=None signale sans ambiguïté à un consommateur que
            # le repli a été utilisé pour CET évènement précis).
            posture = classify_by_aspect_ratio(
                aspect_ratio, self.config.lying_aspect_ratio_threshold, self.config.upright_aspect_ratio_threshold,
            )
            confidence = confidence_from_aspect_ratio(
                aspect_ratio, self.config.lying_aspect_ratio_threshold, self.config.upright_aspect_ratio_threshold,
            )
            return PostureResult(posture=posture, confidence=confidence, aspect_ratio=aspect_ratio,
                                  orientation_deg=None, source="yolo_pose")

        posture = classify_by_aspect_ratio(
            angle_deg, self.config.lying_angle_threshold_deg, self.config.upright_angle_threshold_deg,
        )
        confidence = float(max(0.5, min(0.95, keypoint_confidence)))
        return PostureResult(posture=posture, confidence=confidence, aspect_ratio=aspect_ratio,
                              orientation_deg=angle_deg, source="yolo_pose")

    def _torso_angle_from_keypoints(self, crop: np.ndarray) -> tuple[Optional[float], float]:
        """Renvoie (angle_par_rapport_a_la_verticale_deg, confiance_moyenne) ou (None, 0.0) si indisponible."""
        if crop.size == 0:
            return None, 0.0

        device = None if self.config.device == "auto" else self.config.device
        results = self._model.predict(crop, device=device, verbose=False)
        if not results or results[0].keypoints is None:
            return None, 0.0

        kpts = results[0].keypoints
        if kpts.xy.shape[0] == 0:
            return None, 0.0

        xy = kpts.xy[0].cpu().numpy()          # (17, 2)
        conf = kpts.conf[0].cpu().numpy() if kpts.conf is not None else np.zeros(17)

        threshold = self.config.keypoint_confidence_threshold
        shoulder_pts = [xy[i] for i in (_LEFT_SHOULDER, _RIGHT_SHOULDER) if conf[i] >= threshold]
        hip_pts = [xy[i] for i in (_LEFT_HIP, _RIGHT_HIP) if conf[i] >= threshold]

        if not shoulder_pts or not hip_pts:
            return None, 0.0  # tronc non exploitable (occlusion, personne partiellement hors-cadre...)

        mid_shoulder = np.mean(shoulder_pts, axis=0)
        mid_hip = np.mean(hip_pts, axis=0)
        dx = float(mid_hip[0] - mid_shoulder[0])
        dy = float(mid_hip[1] - mid_shoulder[1])

        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return None, 0.0  # épaules et hanches confondues : mesure dégénérée

        angle_deg = math.degrees(math.atan2(abs(dx), abs(dy)))  # 0=vertical, 90=horizontal

        used_confidences = [conf[i] for i in (_LEFT_SHOULDER, _RIGHT_SHOULDER, _LEFT_HIP, _RIGHT_HIP) if conf[i] >= threshold]
        mean_confidence = float(np.mean(used_confidences)) if used_confidences else 0.0
        return angle_deg, mean_confidence


def build_analyzer(config: AnalyzerConfig) -> BasePostureAnalyzer:
    """Fabrique le backend demandé par la configuration."""
    if config.backend == "mock":
        return MockPostureAnalyzer(config)
    if config.backend == "yolo_pose":
        return YoloPoseAnalyzer(config)
    raise ValueError(f"Backend d'analyse de posture inconnu : {config.backend!r} (attendu : 'mock' ou 'yolo_pose')")
