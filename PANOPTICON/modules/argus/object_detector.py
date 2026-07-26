"""Détection d'objets YOLOv8."""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

PERSON_CLASSES = {"person"}


@dataclass
class Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    track_id: int | None = None


class ObjectDetector:
    def __init__(self, model_name: str = "yolov8n.pt", conf_threshold: float = 0.4):
        self.model_name = model_name
        self.conf_threshold = conf_threshold
        self._model = None

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO

            logger.info("Chargement modèle YOLO: %s", self.model_name)
            self._model = YOLO(self.model_name)
        return self._model

    def detect(self, frame: np.ndarray) -> list[Detection]:
        model = self._load()
        results = model(frame, verbose=False, conf=self.conf_threshold)
        detections: list[Detection] = []
        for result in results:
            names = result.names
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label = names.get(cls_id, str(cls_id))
                conf = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                track_id = int(box.id[0]) if box.id is not None else None
                detections.append(
                    Detection(label=label, confidence=conf, bbox=(x1, y1, x2, y2), track_id=track_id)
                )
        return detections

    def detect_with_tracking(self, frame: np.ndarray) -> list[Detection]:
        model = self._load()
        results = model.track(frame, persist=True, verbose=False, conf=self.conf_threshold)
        detections: list[Detection] = []
        for result in results:
            names = result.names
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label = names.get(cls_id, str(cls_id))
                conf = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                track_id = int(box.id[0]) if box.id is not None else None
                detections.append(
                    Detection(label=label, confidence=conf, bbox=(x1, y1, x2, y2), track_id=track_id)
                )
        return detections
