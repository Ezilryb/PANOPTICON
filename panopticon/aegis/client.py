"""
panopticon/aegis/client.py

Client minimal pour les futurs modules consommateurs d'AEGIS (SYS-LOG,
NEXUS-V) : se connecte au bus de publication AEGIS et lit les AegisEvent en
continu. Même pattern que `oracle/client.py` et `pulse_track/client.py` —
AEGIS ne réécrit aucune frame sur disque (aucune image dérivée, contrairement
à SPECTRA) : ce client n'a donc pas de `read_frame()`. Un futur module qui a
besoin de l'image au moment de l'alerte lit directement depuis
`argus.frame_store.FrameReader(event.camera_id)` (même primitive bas niveau
que celle utilisée en interne par roster/spectra/oracle/pulse_track) :

    from argus.frame_store import FrameReader
    from aegis.client import AegisClient

    readers = {}
    client = AegisClient()
    client.connect()
    for event in client.events():
        print(event.event_type, event.camera_id, event.track_id)
        reader = readers.setdefault(event.camera_id, FrameReader(event.camera_id))
        frame = reader.read_latest()  # (frame_id, ts_capture, image) le plus récent, ou None
"""

import json
import logging
import socket
from typing import Iterator, Optional

from .data_types import AegisEvent

logger = logging.getLogger("aegis.client")


class AegisClient:
    """Se connecte à AegisPublisher et permet d'itérer sur les AegisEvent reçus."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8770) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    def connect(self, timeout_s: float = 5.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout_s)
        self._sock.settimeout(None)
        logger.info("AegisClient : connecté à %s:%d", self.host, self.port)

    def events(self) -> Iterator[AegisEvent]:
        """Générateur bloquant : produit un AegisEvent à chaque message reçu, jusqu'à fermeture de la connexion."""
        if self._sock is None:
            raise RuntimeError("AegisClient.connect() doit être appelé avant events()")

        while True:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    logger.info("AegisClient : connexion fermée par le serveur")
                    return
                self._buffer += chunk

            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line:
                continue
            payload = json.loads(line.decode("utf-8"))
            yield AegisEvent.from_dict(payload)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
