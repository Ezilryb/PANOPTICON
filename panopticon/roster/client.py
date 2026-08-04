"""
panopticon/roster/client.py

Client minimal pour les futurs modules consommateurs de ROSTER
(PULSE_TRACK, SYS-LOG, NEXUS-V) : se connecte au bus de publication ROSTER
et lit les RosterEvent en continu. `read_frame()` réutilise le FrameReader
d'ARGUS (même fichier caméra que celui écrit par ArgusEngine) : ROSTER ne
duplique jamais les frames brutes, il ne fait que republier les résultats
de matching en s'appuyant sur `camera_id` pour retrouver la bonne image côté
ARGUS si un consommateur en a besoin (ex: NEXUS-V affichant un visage
encadré avec son étiquette `known:{nom}`).
"""

import json
import logging
import socket
from typing import Iterator, Optional

import numpy as np

from .data_types import RosterEvent

logger = logging.getLogger("roster.client")


class RosterClient:
    """Se connecte à RosterPublisher et permet d'itérer sur les RosterEvent reçus."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8766) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._frame_readers: dict = {}

    def connect(self, timeout_s: float = 5.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout_s)
        self._sock.settimeout(None)
        logger.info("RosterClient : connecté à %s:%d", self.host, self.port)

    def events(self) -> Iterator[RosterEvent]:
        """Générateur bloquant : produit un RosterEvent à chaque message reçu, jusqu'à fermeture de la connexion."""
        if self._sock is None:
            raise RuntimeError("RosterClient.connect() doit être appelé avant events()")

        while True:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    logger.info("RosterClient : connexion fermée par le serveur")
                    return
                self._buffer += chunk

            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line:
                continue
            payload = json.loads(line.decode("utf-8"))
            yield RosterEvent.from_dict(payload)

    def read_frame(self, event: RosterEvent) -> Optional[np.ndarray]:
        """
        Récupère l'image brute correspondant à l'évènement, via le même
        mécanisme de fichier que celui utilisé par ARGUS (`FrameReader`).
        Renvoie None si ARGUS n'a pas encore écrit de frame pour cette
        caméra, ou si le module `argus` n'est pas disponible dans cet
        environnement (import différé pour ne pas coupler ROSTER à ARGUS
        au niveau du module — seulement au niveau de cette fonctionnalité
        optionnelle).
        """
        try:
            from argus.frame_store import FrameReader
        except ImportError:
            logger.warning("Module 'argus' indisponible : impossible de relire la frame brute pour %s", event.camera_id)
            return None

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
