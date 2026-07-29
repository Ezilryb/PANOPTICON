"""Gestion des caméras ARGUS (processus worker)."""

import logging
import sqlite3
import time
from pathlib import Path
from uuid import UUID

import cv2

from modules.argus.object_detector import ObjectDetector, PERSON_CLASSES
from modules.argus.stream_reader import StreamReader
from modules.event_sink import emit
from shared.config import settings
from shared.models import DetectionEvent

logger = logging.getLogger(__name__)

# État partagé via fichiers pour communication inter-processus MVP
STATE_DIR = Path("./data/argus")
LATEST_FRAMES_DIR = STATE_DIR / "frames"


def _db_path() -> str:
    url = settings.database_url
    if url.startswith("sqlite"):
        return url.split("///")[-1]
    return "./data/panopticon.db"


def _load_cameras() -> list[dict]:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM cameras ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _update_camera_status(camera_id: str, status: str) -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute("UPDATE cameras SET status = ? WHERE id = ?", (status, camera_id))
    conn.commit()
    conn.close()


class CameraWorker:
    def __init__(self, camera: dict, detector: ObjectDetector):
        self.camera = camera
        self.camera_id = camera["id"]
        self.zone = camera.get("zone", "default")
        self.target_fps = max(1, int(camera.get("target_fps", 3)))
        self.reader = StreamReader(camera["connection_url"])
        self.detector = detector
        self._known_tracks: set[int] = set()
        self._frame_interval = 1.0 / self.target_fps

    def run_once(self) -> bool:
        if not self.reader.connect():
            _update_camera_status(self.camera_id, "reconnecting")
            return False
        _update_camera_status(self.camera_id, "online")
        ok, frame = self.reader.read()
        if not ok or frame is None:
            _update_camera_status(self.camera_id, "offline")
            return False

        if settings.spectra_enhance_frames:
            from modules.spectra.image_enhancer import enhance_frame

            frame = enhance_frame(
                frame,
                use_clahe=True,
                gamma=settings.spectra_gamma,
                use_denoise=settings.spectra_denoise,
            )

        # Sauvegarde frame pour streaming API
        LATEST_FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        frame_path = LATEST_FRAMES_DIR / f"{self.camera_id}.jpg"
        cv2.imwrite(str(frame_path), frame)

        detections = self.detector.detect_with_tracking(frame)
        current_tracks = {d.track_id for d in detections if d.track_id is not None}

        for det in detections:
            if det.track_id is None:
                continue
            if det.track_id not in self._known_tracks:
                self._known_tracks.add(det.track_id)
                event_type = (
                    "person_entered_zone"
                    if det.label in PERSON_CLASSES
                    else "object_appeared"
                )
                thumb = self._save_thumbnail(frame, det.bbox, det.track_id)
                event = DetectionEvent(
                    camera_id=UUID(self.camera_id),
                    source_module="argus",
                    event_type=event_type,
                    zone=self.zone,
                    thumbnail_path=str(thumb) if thumb else None,
                    metadata={
                        "label": det.label,
                        "confidence": det.confidence,
                        "track_id": det.track_id,
                        "bbox": det.bbox,
                    },
                )
                emit(event)
                logger.info("Événement %s track=%s label=%s", event_type, det.track_id, det.label)

        disappeared = self._known_tracks - current_tracks
        for track_id in disappeared:
            self._known_tracks.discard(track_id)
            event = DetectionEvent(
                camera_id=UUID(self.camera_id),
                source_module="argus",
                event_type="object_disappeared",
                zone=self.zone,
                metadata={"track_id": track_id},
            )
            emit(event)

        return True

    def _save_thumbnail(self, frame, bbox, track_id: int) -> Path | None:
        x1, y1, x2, y2 = bbox
        crop = frame[max(0, y1):y2, max(0, x1):x2]
        if crop.size == 0:
            return None
        thumb_dir = settings.storage_path / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        path = thumb_dir / f"{self.camera_id}_{track_id}.jpg"
        cv2.imwrite(str(path), crop)
        return path

    def loop(self) -> None:
        logger.info("Worker caméra %s démarré", self.camera.get("name"))
        while True:
            start = time.monotonic()
            try:
                self.run_once()
            except Exception:
                logger.exception("Erreur worker caméra %s", self.camera_id)
                _update_camera_status(self.camera_id, "offline")
            elapsed = time.monotonic() - start
            time.sleep(max(0.0, self._frame_interval - elapsed))


def run_argus() -> None:
    from shared.logging_utils import setup_logging

    setup_logging(settings.log_level)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    detector = ObjectDetector(model_name=settings.yolo_model)
    cameras = _load_cameras()

    if not cameras:
        logger.warning("Aucune caméra configurée — ARGUS en attente (poll 10s)")
        while True:
            time.sleep(10)
            cameras = _load_cameras()
            if cameras:
                break

    workers = [CameraWorker(cam, detector) for cam in cameras]
    if len(workers) == 1:
        workers[0].loop()
    else:
        import threading

        threads = [threading.Thread(target=w.loop, daemon=True) for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
