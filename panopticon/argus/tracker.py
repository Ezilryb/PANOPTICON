"""
panopticon/argus/tracker.py

Tracking multi-objets par caméra : associe les détections d'une frame aux
pistes (tracks) existantes via IoU, sans dépendance externe (pas de scipy).
Fournit un track_id stable à chaque objet suivi, condition nécessaire pour
que PULSE_TRACK, ROSTER et AEGIS puissent raisonner sur un même objet à
travers le temps plutôt que sur des détections isolées.
"""

import itertools
import logging
from dataclasses import dataclass

from .data_types import BBox, Detection

logger = logging.getLogger("argus.tracker")


def iou(a: BBox, b: BBox) -> float:
    """Intersection-over-Union de deux bounding boxes (x1, y1, x2, y2)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    return inter_area / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: int
    class_name: str
    bbox: BBox
    hits: int = 1                 # nb total de frames où cette piste a été mise à jour
    time_since_update: int = 0    # nb de frames consécutives sans correspondance
    age_frames: int = 0           # âge total en frames depuis la création


class IouTracker:
    """
    Tracker glouton par IoU : une instance gère les pistes d'UNE seule caméra.
    À chaque frame, `update()` associe chaque détection à la piste existante
    la plus proche (même classe, IoU maximal), crée une nouvelle piste pour
    les détections non associées, et incrémente l'âge des pistes non
    retrouvées avant de les supprimer après `max_age_frames` échecs.

    `hits`/`time_since_update`/`age_frames` ne comptent QUE les appels à
    `update()` (donc, en mode "detect_and_track", uniquement les frames
    lourdes) : `sync_track_position()` met à jour la position connue d'une
    piste SANS toucher à ces compteurs, cf. sa docstring.
    """

    def __init__(self, iou_threshold: float = 0.3, max_age_frames: int = 15) -> None:
        self.iou_threshold = iou_threshold
        self.max_age_frames = max_age_frames
        self._tracks: dict[int, Track] = {}
        self._id_counter = itertools.count(1)

    def update(self, detections: list[Detection]) -> list[Detection]:
        """
        Met à jour les pistes à partir des détections de la frame courante et
        renvoie les MÊMES détections enrichies de `track_id`. Ne modifie ni
        l'ordre ni le contenu autrement.
        """
        unmatched_det_indices = list(range(len(detections)))
        candidate_track_ids = set(self._tracks.keys())

        # Construit tous les couples (score IoU, det_idx, track_id) valides (même classe, IoU > seuil).
        candidates: list[tuple[float, int, int]] = []
        for det_idx in unmatched_det_indices:
            det = detections[det_idx]
            for track_id in candidate_track_ids:
                track = self._tracks[track_id]
                if track.class_name != det.class_name:
                    continue
                score = iou(det.bbox, track.bbox)
                if score >= self.iou_threshold:
                    candidates.append((score, det_idx, track_id))

        # Appariement glouton : meilleurs scores en premier, chaque détection et
        # chaque piste ne pouvant être utilisées qu'une seule fois.
        candidates.sort(key=lambda c: c[0], reverse=True)
        matched_dets: set[int] = set()
        matched_tracks: set[int] = set()

        for _score, det_idx, track_id in candidates:
            if det_idx in matched_dets or track_id in matched_tracks:
                continue
            det = detections[det_idx]
            track = self._tracks[track_id]
            track.bbox = det.bbox
            track.hits += 1
            track.time_since_update = 0
            det.track_id = track_id
            matched_dets.add(det_idx)
            matched_tracks.add(track_id)

        # Détections non appariées -> nouvelles pistes.
        for det_idx in unmatched_det_indices:
            if det_idx in matched_dets:
                continue
            det = detections[det_idx]
            new_id = next(self._id_counter)
            self._tracks[new_id] = Track(track_id=new_id, class_name=det.class_name, bbox=det.bbox)
            det.track_id = new_id

        # Pistes non retrouvées cette frame : vieillissement puis suppression au-delà du seuil.
        stale_ids = []
        for track_id, track in self._tracks.items():
            if track_id in matched_tracks:
                track.age_frames += 1
                continue
            track.time_since_update += 1
            track.age_frames += 1
            if track.time_since_update > self.max_age_frames:
                stale_ids.append(track_id)

        for track_id in stale_ids:
            del self._tracks[track_id]

        return detections

    def sync_track_position(self, track_id: int, bbox: BBox) -> bool:
        """
        Met à jour la position connue d'UNE piste SANS passer par le cycle
        complet de update() (pas de ré-appariement IoU, aucun effet sur
        hits/time_since_update/age_frames). Appelé par la pipeline en mode
        "detect_and_track" à chaque frame légère (cf. pipeline.py::
        _update_light_tracks) : sans ça, la PROCHAINE frame lourde
        comparerait sa nouvelle détection au bbox vieux de plusieurs frames
        laissé par la dernière frame lourde, l'IoU tomberait trop souvent
        sous `iou_threshold`, et un nouveau track_id serait créé à tort à
        chaque recalibration au lieu de prolonger la piste existante.

        Renvoie False (no-op) si `track_id` n'existe plus (ex: piste
        supprimée entre-temps) — jamais d'exception.
        """
        track = self._tracks.get(track_id)
        if track is None:
            return False
        track.bbox = bbox
        return True

    @property
    def active_track_count(self) -> int:
        return len(self._tracks)