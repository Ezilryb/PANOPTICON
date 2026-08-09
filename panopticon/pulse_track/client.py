"""
panopticon/pulse_track/client.py

Client minimal pour les futurs modules consommateurs de PULSE_TRACK
(SYS-LOG, NEXUS-V) : se connecte au bus de publication PULSE_TRACK et lit
les PulseTrackEvent en continu. Même pattern que oracle/client.py —
PULSE_TRACK ne réécrit aucune frame sur disque (aucune image dérivée,
contrairement à SPECTRA) : ce client n'a donc pas de read_frame(). Un futur
module qui a besoin de l'image lit directement depuis
argus.frame_store.FrameReader(event.camera_id) (même primitive bas niveau
que celle utilisée en interne par roster/client.py et spectra/client.py) :

    from argus.frame_store import FrameReader
    from pulse_track.client import PulseTrackClient

    readers = {}
    client = PulseTrackClient()
    client.connect()
    for event in client.events():
        print(event.message)
        reader = readers.setdefault(event.camera_id, FrameReader(event.camera_id))
        frame = reader.read_latest()  # (frame_id, ts_capture, image) le plus récent, ou None
"""

import json
import logging
import socket
from typing import Iterator, Optional

from .data_types import PulseTrackEvent

logger = logging.getLogger("pulse_track.client")


class PulseTrackClient:
    """Se connecte à PulseTrackPublisher et permet d'itérer sur les PulseTrackEvent reçus."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8769) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    def connect(self, timeout_s: float = 5.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout_s)
        self._sock.settimeout(None)
        logger.info("PulseTrackClient : connecté à %s:%d", self.host, self.port)

    def events(self) -> Iterator[PulseTrackEvent]:
        """Générateur bloquant : produit un PulseTrackEvent à chaque message reçu, jusqu'à fermeture de la connexion."""
        if self._sock is None:
            raise RuntimeError("PulseTrackClient.connect() doit être appelé avant events()")

        while True:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    logger.info("PulseTrackClient : connexion fermée par le serveur")
                    return
                self._buffer += chunk

            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line:
                continue
            payload = json.loads(line.decode("utf-8"))
            yield PulseTrackEvent.from_dict(payload)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None