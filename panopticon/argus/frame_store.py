"""
panopticon/argus/frame_store.py

Publication des frames brutes via fichier sur disque : ARGUS encode chaque
frame en JPEG et l'écrit dans un fichier dédié par caméra, de façon atomique
(écriture dans un fichier temporaire puis os.replace()), sans passer par le
socket de publication (trop lent pour de l'image). Les futurs modules
consommateurs (SPECTRA, ORACLE, ROSTER...) lisent la frame correspondant à un
DetectionEvent directement depuis ce fichier.

Remplace l'implémentation initiale basée sur multiprocessing.shared_memory,
qui s'est révélée non fonctionnelle entre processus séparés sur certaines
configurations Windows (restriction antivirus/politique système sur les
objets de mémoire partagée nommés). L'écriture atomique via os.replace()
offre la même garantie anti-« torn read » que le mécanisme à base de version
utilisé précédemment, sans dépendre de shared_memory.
"""

import logging
import os
import struct
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("argus.frame_store")

# En-tête binaire de chaque fichier : version(u64) frame_id(u64) ts_capture(f64) width(u32) height(u32) payload_len(u32)
_HEADER_FMT = "=QQdIII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Répertoire commun à tous les process PANOPTICON pour l'échange de frames.
_FRAME_CACHE_DIR = Path(tempfile.gettempdir()) / "panopticon_frames"


def _frame_path(camera_id: str) -> Path:
    return _FRAME_CACHE_DIR / f"argus_frame_{camera_id}.bin"


class SharedFrameStore:
    """
    Écrivain (côté ARGUS) : un fichier par caméra, réécrit de façon atomique
    à chaque `write()`. Les paramètres slot_size_bytes/slots sont conservés
    pour compatibilité d'API ; slot_size_bytes sert de borne maximale : une
    frame encodée plus volumineuse est ignorée (logged) plutôt qu'écrite,
    ce qui correspond au contrat attendu par FrameReader et les tests.
    """

    def __init__(self, camera_id: str, slot_size_bytes: int = 2_000_000, slots: int = 3) -> None:
        self.camera_id = camera_id
        self.slot_size = slot_size_bytes  # borne maximale de taille de payload JPEG accepté
        self._version = 0

        _FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._path = _frame_path(camera_id)

        # Nettoie un fichier orphelin d'une exécution précédente (ARGUS tué sans nettoyage).
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

    def write(self, image: np.ndarray, frame_id: int, ts_capture: float, jpeg_quality: int = 80) -> None:
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            logger.warning("Caméra %s : échec de l'encodage JPEG pour frame_id=%d", self.camera_id, frame_id)
            return
        payload = encoded.tobytes()

        # --- CORRECTIF : vérification de taille manquante dans la version précédente ---
        # Une frame trop grande pour le slot configuré est ignorée (et loguée) plutôt
        # qu'écrite quand même, ce qui respectait l'API slot_size_bytes déclarée et
        # fait passer test_oversized_frame_is_ignored_not_crashed.
        if len(payload) > self.slot_size:
            logger.warning(
                "Caméra %s : frame trop volumineuse pour le slot configuré (%d > %d octets), frame ignorée",
                self.camera_id, len(payload), self.slot_size,
            )
            return

        self._version += 1
        header = struct.pack(_HEADER_FMT, self._version, frame_id, ts_capture, image.shape[1], image.shape[0], len(payload))

        # Écriture dans un fichier temporaire du MÊME répertoire (nécessaire pour que
        # os.replace() reste atomique, y compris sur Windows), puis remplacement en une
        # opération : un lecteur ne peut jamais observer un fichier à moitié écrit.
        fd, tmp_path = tempfile.mkstemp(dir=str(_FRAME_CACHE_DIR), prefix=f".{self.camera_id}_", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(header)
                f.write(payload)
            os.replace(tmp_path, self._path)
        except OSError:
            logger.warning("Caméra %s : échec d'écriture de la frame sur disque", self.camera_id)
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    def close(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def total_size(slot_size_bytes: int, slots: int) -> int:
        # Conservé pour compat API (ancien code basé sur shared_memory) ; sans objet ici.
        return slot_size_bytes * slots


class FrameReader:
    """
    Lecteur (côté modules consommateurs) : lit le fichier écrit par
    SharedFrameStore pour une caméra donnée et expose `read_latest()`, qui
    renvoie la dernière frame décodée (JPEG -> ndarray BGR), ou None si rien
    n'a encore été publié, si le fichier n'existe pas (ARGUS pas démarré),
    ou si la frame a déjà été lue précédemment.
    """

    def __init__(self, camera_id: str, slot_size_bytes: int = 2_000_000, slots: int = 3) -> None:
        self.camera_id = camera_id
        self._path = _frame_path(camera_id)
        self._last_version = -1

    def read_latest(self) -> Optional[tuple[int, float, np.ndarray]]:
        """Renvoie (frame_id, ts_capture, image) pour la frame la plus récente, ou None."""
        try:
            with open(self._path, "rb") as f:
                header_bytes = f.read(_HEADER_SIZE)
                if len(header_bytes) < _HEADER_SIZE:
                    return None  # fichier en cours d'écriture concurrente (rarissime, os.replace() protège déjà)
                version, frame_id, ts_capture, _width, _height, payload_len = struct.unpack(_HEADER_FMT, header_bytes)
                payload = f.read(payload_len)
        except FileNotFoundError:
            return None
        except OSError:
            logger.debug("Caméra %s : lecture du fichier de frame échouée", self.camera_id)
            return None

        if len(payload) != payload_len:
            return None  # lecture incomplète (fichier remplacé pendant la lecture) : on retentera au prochain appel

        if version == self._last_version:
            return None  # déjà lue, rien de nouveau depuis le dernier appel
        self._last_version = version

        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        return frame_id, ts_capture, image

    def close(self) -> None:
        pass  # rien à libérer côté fichier (pas de handle persistant)
