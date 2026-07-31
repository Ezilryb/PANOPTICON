"""
panopticon/argus/client.py

Client minimal pour les futurs modules consommateurs d'ARGUS (SPECTRA,
ORACLE, ROSTER, PULSE_TRACK, AEGIS) : se connecte au bus de publication,
lit les DetectionEvent en continu, et sait aller chercher la frame brute
correspondante en mémoire partagée via FrameReader.
"""

import json
import logging
import socket
from typing import Iterator, Optional

import numpy as np

from .data_types import DetectionEvent
from .frame_store import FrameReader

logger = logging.getLogger("argus.client")


class ArgusClient:
    """Se connecte à ArgusPublisher et permet d'itérer sur les DetectionEvent reçus."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._frame_readers: dict[str, FrameReader] = {}

    def connect(self, timeout_s: float = 5.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout_s)
        self._sock.settimeout(None)
        logger.info("ArgusClient : connecté à %s:%d", self.host, self.port)

    def events(self) -> Iterator[DetectionEvent]:
        """Générateur bloquant : produit un DetectionEvent à chaque message reçu, jusqu'à fermeture de la connexion."""
        if self._sock is None:
            raise RuntimeError("ArgusClient.connect() doit être appelé avant events()")

        while True:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    logger.info("ArgusClient : connexion fermée par le serveur")
                    return
                self._buffer += chunk

            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line:
                continue
            payload = json.loads(line.decode("utf-8"))
            yield DetectionEvent.from_dict(payload)

    def read_frame(self, event: DetectionEvent) -> Optional[np.ndarray]:
        """Récupère l'image brute correspondant à l'évènement depuis la mémoire partagée (None si indisponible)."""
        reader = self._frame_readers.get(event.camera_id)
        if reader is None:
            reader = FrameReader(event.camera_id)
            self._frame_readers[event.camera_id] = reader
        result = reader.read_latest()
        if result is None:
            return None
        _frame_id, _ts, image = result
        return image

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        for reader in self._frame_readers.values():
            reader.close()
        self._frame_readers.clear()
