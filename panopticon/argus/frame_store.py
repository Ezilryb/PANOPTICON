"""
panopticon/argus/frame_store.py

Publication des frames brutes en mémoire partagée (multiprocessing.shared_memory) :
ARGUS encode chaque frame en JPEG et l'écrit dans un petit ring buffer par
caméra, sans passer par le socket de publication (trop lent pour de l'image).
Les futurs modules consommateurs (SPECTRA, ORACLE, ROSTER...) lisent la
frame correspondant à un DetectionEvent directement depuis cette mémoire
partagée, avec un mécanisme anti-« torn read » basé sur un numéro de version.
"""

import logging
import struct
from multiprocessing import resource_tracker, shared_memory
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("argus.frame_store")

# En-tête binaire de chaque slot : version(u64) frame_id(u64) ts_capture(f64) width(u32) height(u32) payload_len(u32)
_HEADER_FMT = "=QQdIII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Pointeur global (version, index du dernier slot écrit), stocké après tous les slots.
_POINTER_FMT = "=Qq"
_POINTER_SIZE = struct.calcsize(_POINTER_FMT)


class SharedFrameStore:
    """
    Écrivain (côté ARGUS) : un segment de mémoire partagée par caméra,
    organisé en `slots` ring-buffer. Chaque `write()` encode l'image en JPEG
    et l'écrit dans le slot suivant avec un numéro de version croissant.
    """

    def __init__(self, camera_id: str, slot_size_bytes: int = 2_000_000, slots: int = 3) -> None:
        self.camera_id = camera_id
        self.slots = slots
        self.slot_size = slot_size_bytes
        self._name = f"argus_frame_{camera_id}"
        self._next_slot = 0
        self._version = 0
        total = self.total_size(slot_size_bytes, slots)

        try:
            self._shm = shared_memory.SharedMemory(name=self._name, create=True, size=total)
        except FileExistsError:
            # Segment orphelin d'une exécution précédente (ARGUS tué sans nettoyage) :
            # on le recycle plutôt que d'échouer au démarrage.
            orphan = shared_memory.SharedMemory(name=self._name, create=False)
            orphan.close()
            orphan.unlink()
            self._shm = shared_memory.SharedMemory(name=self._name, create=True, size=total)

        # Pointeur à zéro tant qu'aucune frame n'a été écrite (lu comme "rien de disponible").
        self._shm.buf[-_POINTER_SIZE:] = struct.pack(_POINTER_FMT, 0, 0)

    def write(self, image: np.ndarray, frame_id: int, ts_capture: float, jpeg_quality: int = 80) -> None:
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            logger.warning("Caméra %s : échec de l'encodage JPEG pour frame_id=%d", self.camera_id, frame_id)
            return
        payload = encoded.tobytes()
        if _HEADER_SIZE + len(payload) > self.slot_size:
            logger.warning(
                "Caméra %s : frame trop volumineuse pour un slot (%d o > %d o dispo), frame ignorée",
                self.camera_id, len(payload), self.slot_size - _HEADER_SIZE,
            )
            return

        self._version += 1
        slot = self._next_slot
        self._next_slot = (self._next_slot + 1) % self.slots
        offset = slot * self.slot_size

        header = struct.pack(_HEADER_FMT, self._version, frame_id, ts_capture, image.shape[1], image.shape[0], len(payload))
        buf = self._shm.buf
        buf[offset:offset + _HEADER_SIZE] = header
        buf[offset + _HEADER_SIZE: offset + _HEADER_SIZE + len(payload)] = payload

        # Publie l'emplacement + version du dernier slot écrit : les lecteurs n'ont ainsi
        # jamais besoin de parcourir tous les slots pour trouver la frame la plus récente.
        buf[-_POINTER_SIZE:] = struct.pack(_POINTER_FMT, self._version, slot)

    def close(self) -> None:
        self._shm.close()
        try:
            self._shm.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def total_size(slot_size_bytes: int, slots: int) -> int:
        return slot_size_bytes * slots + _POINTER_SIZE


class FrameReader:
    """
    Lecteur (côté modules consommateurs) : se connecte au segment de mémoire
    partagée d'une caméra en lecture seule et expose `read_latest()`, qui
    renvoie la dernière frame décodée (JPEG -> ndarray BGR), ou None si rien
    n'a encore été publié ou si le segment n'existe pas (ARGUS pas démarré).
    """

    def __init__(self, camera_id: str, slot_size_bytes: int = 2_000_000, slots: int = 3) -> None:
        self.camera_id = camera_id
        self.slots = slots
        self.slot_size = slot_size_bytes
        self._name = f"argus_frame_{camera_id}"
        self._shm: Optional[shared_memory.SharedMemory] = None
        self._last_version = -1

    def _ensure_attached(self) -> bool:
        if self._shm is not None:
            return True
        try:
            self._shm = shared_memory.SharedMemory(name=self._name, create=False)
        except FileNotFoundError:
            return False

        # `SharedMemory(create=False)` enregistre quand même le segment auprès du
        # resource_tracker de CE process, comme s'il devait le supprimer à la fin.
        # Seul l'écrivain (SharedFrameStore, qui a fait le `create=True`) est
        # responsable de l'unlink final (voir son close()) : on désenregistre donc
        # ici pour éviter un faux "leaked shared_memory objects" à la fermeture
        # d'un simple lecteur (SPECTRA, ORACLE, ROSTER...).
        try:
            resource_tracker.unregister(self._shm._name, "shared_memory")
        except Exception:
            pass
        return True

    def read_latest(self) -> Optional[tuple[int, float, np.ndarray]]:
        """Renvoie (frame_id, ts_capture, image) pour la frame la plus récente, ou None (rien de nouveau/disponible)."""
        if not self._ensure_attached():
            return None

        version, slot = struct.unpack(_POINTER_FMT, bytes(self._shm.buf[-_POINTER_SIZE:]))
        if version == 0:
            return None  # rien n'a encore été écrit par l'écrivain

        for _attempt in range(3):
            offset = slot * self.slot_size
            header_bytes = bytes(self._shm.buf[offset:offset + _HEADER_SIZE])
            slot_version, frame_id, ts_capture, _width, _height, payload_len = struct.unpack(_HEADER_FMT, header_bytes)
            payload = bytes(self._shm.buf[offset + _HEADER_SIZE: offset + _HEADER_SIZE + payload_len])

            # Vérifie qu'aucune ré-écriture n'a eu lieu pendant la copie (torn read) :
            # le pointeur global doit toujours désigner ce même (slot, version) après coup.
            version_after, slot_after = struct.unpack(_POINTER_FMT, bytes(self._shm.buf[-_POINTER_SIZE:]))
            if version_after == version and slot_after == slot and slot_version == version:
                if version == self._last_version:
                    return None  # déjà lue, rien de nouveau depuis le dernier appel
                self._last_version = version
                image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    return None
                return frame_id, ts_capture, image

            # L'écrivain a tourné pendant la lecture : on relit le pointeur courant et on retente.
            version, slot = version_after, slot_after

        logger.debug("Caméra %s : lecture incohérente après plusieurs tentatives (écrivain très rapide)", self.camera_id)
        return None

    def close(self) -> None:
        if self._shm is not None:
            self._shm.close()
            self._shm = None
