"""
panopticon/spectra/client.py

Client minimal pour les futurs modules consommateurs de SPECTRA (ORACLE,
PULSE_TRACK, SYS-LOG, NEXUS-V) : se connecte au bus de publication SPECTRA
et lit les SpectraEvent en continu. `read_frame()` réutilise le FrameReader
d'ARGUS (même mécanisme fichier que celui écrit par SpectraEngine), mais sous
l'identifiant préfixé `spectra_camera_id(camera_id)` puisque c'est la frame
AMÉLIORÉE qui est stockée sous ce nom, distincte de la frame brute d'ARGUS
(lisible séparément, sous son camera_id d'origine, via `argus.client`).

Usage typique pour un futur module :

    from spectra.client import SpectraClient
    client = SpectraClient()
    client.connect()
    for event in client.events():
        frame = client.read_frame(event)   # image AMÉLIORÉE, si besoin
        # ... logique métier du module ...
"""

import json
import logging
import socket
from typing import Iterator, Optional

import numpy as np

from .data_types import SpectraEvent, spectra_camera_id

logger = logging.getLogger("spectra.client")


class SpectraClient:
    """Se connecte à SpectraPublisher et permet d'itérer sur les SpectraEvent reçus."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8767) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._frame_readers: dict = {}

    def connect(self, timeout_s: float = 5.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout_s)
        self._sock.settimeout(None)
        logger.info("SpectraClient : connecté à %s:%d", self.host, self.port)

    def events(self) -> Iterator[SpectraEvent]:
        """Générateur bloquant : produit un SpectraEvent à chaque message reçu, jusqu'à fermeture de la connexion."""
        if self._sock is None:
            raise RuntimeError("SpectraClient.connect() doit être appelé avant events()")

        while True:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    logger.info("SpectraClient : connexion fermée par le serveur")
                    return
                self._buffer += chunk

            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line:
                continue
            payload = json.loads(line.decode("utf-8"))
            yield SpectraEvent.from_dict(payload)

    def read_frame(self, event: SpectraEvent) -> Optional[np.ndarray]:
        """
        Récupère l'image AMÉLIORÉE correspondant à l'évènement, via le même
        mécanisme fichier que celui utilisé par ARGUS (`FrameReader`), sous
        l'identifiant préfixé `SPECTRA-{camera_id}`. Renvoie None si SPECTRA
        n'a pas encore écrit de frame pour cette caméra, ou si le module
        `argus` n'est pas disponible dans cet environnement (import différé
        pour ne pas coupler SPECTRA à ARGUS au niveau du module — seulement
        au niveau de cette fonctionnalité optionnelle, même principe que
        `roster/client.py`).
        """
        try:
            from argus.frame_store import FrameReader
        except ImportError:
            logger.warning(
                "Module 'argus' indisponible : impossible de relire la frame améliorée pour %s", event.camera_id
            )
            return None

        key = spectra_camera_id(event.camera_id)
        reader = self._frame_readers.get(key)
        if reader is None:
            reader = FrameReader(key)
            self._frame_readers[key] = reader
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