"""
panopticon/oracle/publisher.py

Bus de publication d'ORACLE : petit serveur TCP local (JSON-lines) qui
diffuse chaque OracleEvent à tous les modules consommateurs connectés
(PULSE_TRACK, SYS-LOG, NEXUS-V...). Même architecture que `argus/publisher.py`,
`roster/publisher.py` et `spectra/publisher.py` : un abonné lent ou bloqué ne
doit jamais ralentir la boucle d'identification d'ORACLE — chaque client a sa
propre file d'attente bornée, avec perte des messages les plus anciens en cas
de saturation plutôt qu'un blocage du serveur. Port distinct d'ARGUS (8765),
ROSTER (8766) et SPECTRA (8767) : ORACLE est un bus à part entière.
"""

import json
import logging
import queue
import socket
import threading
from typing import Optional

from .data_types import OracleEvent

logger = logging.getLogger("oracle.publisher")

_CLIENT_QUEUE_MAXSIZE = 100


class _ClientHandler:
    """Un client connecté = une file d'attente + un thread d'envoi dédiés, pour isoler les lenteurs."""

    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr
        self.queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
        self.alive = True

    def enqueue(self, payload: bytes) -> None:
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(payload)
            except queue.Full:
                pass

    def run(self) -> None:
        try:
            while self.alive:
                payload = self.queue.get()
                if payload is None:
                    break
                self.sock.sendall(payload)
        except OSError:
            pass
        finally:
            self.alive = False
            try:
                self.sock.close()
            except OSError:
                pass

    def stop(self) -> None:
        self.alive = False
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass


class OraclePublisher:
    """Serveur TCP local diffusant les OracleEvent en JSON-lines à tous les clients connectés."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8768) -> None:
        self.host = host
        self.port = port
        self._server_socket: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._clients: list[_ClientHandler] = []
        self._clients_lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(8)
        self._server_socket.settimeout(0.5)

        self._stop_event.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, name="oracle-publisher-accept", daemon=True)
        self._accept_thread.start()
        logger.info("OraclePublisher : en écoute sur %s:%d", self.host, self.port)

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                sock, addr = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            handler = _ClientHandler(sock, addr)
            with self._clients_lock:
                self._clients.append(handler)
            threading.Thread(target=handler.run, name=f"oracle-publisher-client-{addr}", daemon=True).start()
            logger.info("OraclePublisher : nouveau client connecté (%s)", addr)

    def publish(self, event: OracleEvent) -> None:
        payload = (json.dumps(event.to_dict()) + "\n").encode("utf-8")
        with self._clients_lock:
            self._clients = [c for c in self._clients if c.alive]
            for client in self._clients:
                client.enqueue(payload)

    def stop(self) -> None:
        self._stop_event.set()
        with self._clients_lock:
            for client in self._clients:
                client.stop()
            self._clients.clear()
        if self._server_socket is not None:
            self._server_socket.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2)
        logger.info("OraclePublisher : arrêté")

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return len([c for c in self._clients if c.alive])
