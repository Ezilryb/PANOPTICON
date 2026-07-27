"""Découverte ONVIF (WS-Discovery) — Phase 2.

Recherche les caméras IP compatibles ONVIF sur le réseau local via une sonde
multicast UDP (WS-Discovery), sans dépendance externe. Ne fournit que
l'adresse de gestion ONVIF de chaque caméra (XAddr) : la découverte ne se
connecte à aucun appareil et ne lit aucun flux vidéo ni contenu tiers — elle
se contente d'écouter les réponses des caméras à une sonde standard.

Obtenir le chemin RTSP réel nécessite ensuite une authentification propre à
chaque fabricant (hors périmètre de ce module) ; l'opérateur ajoute la
caméra manuellement via `camera add` une fois son adresse RTSP connue.
"""

from __future__ import annotations

import logging
import re
import socket
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

WS_DISCOVERY_ADDRESS = ("239.255.255.250", 3702)

_PROBE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{message_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""

_XADDRS_RE = re.compile(r"<[\w:]*XAddrs>(.*?)</[\w:]*XAddrs>", re.DOTALL | re.IGNORECASE)
_SCOPES_RE = re.compile(r"<[\w:]*Scopes[^>]*>(.*?)</[\w:]*Scopes>", re.DOTALL | re.IGNORECASE)


@dataclass
class OnvifDevice:
    xaddrs: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    source_ip: str = ""

    @property
    def primary_xaddr(self) -> str | None:
        return self.xaddrs[0] if self.xaddrs else None

    @property
    def name(self) -> str:
        for scope in self.scopes:
            if "/name/" in scope:
                return scope.split("/name/", 1)[1].replace("%20", " ")
        return self.source_ip or "Caméra ONVIF"


def _parse_probe_match(xml_text: str, source_ip: str) -> OnvifDevice | None:
    xaddrs_match = _XADDRS_RE.search(xml_text)
    if not xaddrs_match:
        return None
    scopes_match = _SCOPES_RE.search(xml_text)
    xaddrs = xaddrs_match.group(1).split()
    scopes = scopes_match.group(1).split() if scopes_match else []
    if not xaddrs:
        return None
    return OnvifDevice(xaddrs=xaddrs, scopes=scopes, source_ip=source_ip)


def discover_onvif_devices(timeout: int = 4) -> list[dict]:
    """Sonde WS-Discovery et retourne les caméras ONVIF répondant sur le réseau local.

    Retourne une liste de dicts {name, xaddr, scopes, source_ip}. Ne lève une
    exception que si le socket ne peut pas être créé/envoyé (réseau
    indisponible) ; l'absence de réponse dans le délai imparti retourne
    simplement une liste vide.
    """
    message_id = str(uuid.uuid4())
    probe = _PROBE_TEMPLATE.format(message_id=message_id).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        sock.sendto(probe, WS_DISCOVERY_ADDRESS)
    except OSError:
        sock.close()
        raise

    devices: dict[str, OnvifDevice] = {}
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            except OSError as exc:
                logger.debug("Erreur de réception WS-Discovery: %s", exc)
                break
            device = _parse_probe_match(data.decode("utf-8", errors="ignore"), addr[0])
            if device and device.primary_xaddr:
                devices[device.primary_xaddr] = device
    finally:
        sock.close()

    return [
        {"name": d.name, "xaddr": d.primary_xaddr, "scopes": d.scopes, "source_ip": d.source_ip}
        for d in devices.values()
    ]
