"""
panopticon/oracle/client.py

Client minimal pour les futurs modules consommateurs d'ORACLE (PULSE_TRACK,
SYS-LOG, NEXUS-V) : se connecte au bus de publication ORACLE et lit les
OracleEvent en continu. Même pattern que `roster/client.py` et
`spectra/client.py` — un futur module n'a qu'à faire :

    from oracle.client import OracleClient
    client = OracleClient()
    client.connect()
    for event in client.events():
        for obj in event.objects:
            ...  # logique métier du module

ORACLE ne réécrit pas de frame sur disque (il ne produit pas d'image dérivée
comme SPECTRA) : ce client n'a donc pas de `read_frame()`. Un futur module
qui a besoin de l'image doit passer par `argus.client.ArgusClient.read_frame()`
directement, avec le `camera_id`/`frame_id` porté par l'OracleEvent.
"""

import json
import logging
import socket
from typing import Iterator, Optional

from .data_types import OracleEvent

logger = logging.getLogger("oracle.client")


class OracleClient:
    """Se connecte à OraclePublisher et permet d'itérer sur les OracleEvent reçus."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8768) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    def connect(self, timeout_s: float = 5.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout_s)
        self._sock.settimeout(None)
        logger.info("OracleClient : connecté à %s:%d", self.host, self.port)

    def events(self) -> Iterator[OracleEvent]:
        """Générateur bloquant : produit un OracleEvent à chaque message reçu, jusqu'à fermeture de la connexion."""
        if self._sock is None:
            raise RuntimeError("OracleClient.connect() doit être appelé avant events()")

        while True:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    logger.info("OracleClient : connexion fermée par le serveur")
                    return
                self._buffer += chunk

            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line:
                continue
            payload = json.loads(line.decode("utf-8"))
            yield OracleEvent.from_dict(payload)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
