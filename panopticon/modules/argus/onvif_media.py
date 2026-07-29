"""ONVIF — récupération du flux RTSP réel d'une caméra découverte (service Media).

Complète ``onvif_discovery.py`` (WS-Discovery) : une fois l'adresse de
gestion (XAddr) d'une caméra connue, ces fonctions l'interrogent en
SOAP/HTTP (authentification WS-Security, digest) pour obtenir l'URL RTSP
réelle de son flux vidéo.

Avertissement : implémentation conforme à la spécification ONVIF
Core/Media ver10, mais non testée contre une caméra physique dans cet
environnement (aucun matériel ONVIF disponible ici). Certains fabricants
s'écartent légèrement de la spec — à valider sur le matériel réel.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope">\n'
    "<e:Header>{header}</e:Header>\n"
    "<e:Body>{body}</e:Body>\n"
    "</e:Envelope>"
)


class OnvifError(RuntimeError):
    """Erreur de communication ou d'authentification avec une caméra ONVIF."""


def _ws_security_header(username: str, password: str) -> str:
    """En-tête WS-Security UsernameToken (PasswordDigest), tel que requis par ONVIF."""
    nonce_bytes = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce_bytes).decode()
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce_bytes + created.encode("utf-8") + password.encode("utf-8")).digest()
    ).decode()
    return (
        '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
        'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{username}</wsse:Username>"
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</wsse:Password>"
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{nonce_b64}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security>"
    )


def _soap_call(xaddr: str, body: str, username: str | None, password: str | None, timeout: float = 5.0) -> str:
    header = _ws_security_header(username, password) if username else ""
    envelope = _ENVELOPE.format(header=header, body=body)
    req = urllib.request.Request(
        xaddr,
        data=envelope.encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:500]
        raise OnvifError(f"HTTP {exc.code} sur {xaddr}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OnvifError(f"Impossible de contacter {xaddr}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise OnvifError(f"Délai dépassé en contactant {xaddr}") from exc


def get_media_xaddr(device_xaddr: str, username: str | None = None, password: str | None = None) -> str:
    """GetCapabilities sur le service Device : retourne l'adresse (XAddr) du service Media."""
    body = (
        '<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        "<tds:Category>Media</tds:Category>"
        "</tds:GetCapabilities>"
    )
    xml = _soap_call(device_xaddr, body, username, password)
    match = re.search(r"<(?:[\w]+:)?Media[^>]*>\s*<(?:[\w]+:)?XAddr>(.*?)</(?:[\w]+:)?XAddr>", xml, re.DOTALL)
    if not match:
        raise OnvifError("Adresse du service Media introuvable dans la réponse GetCapabilities")
    return match.group(1).strip()


def get_first_profile_token(media_xaddr: str, username: str | None, password: str | None) -> str:
    """GetProfiles sur le service Media : retourne le token du premier profil disponible."""
    body = '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>'
    xml = _soap_call(media_xaddr, body, username, password)
    match = re.search(r'<(?:[\w]+:)?Profiles[^>]*\stoken="([^"]+)"', xml)
    if not match:
        raise OnvifError("Aucun profil média trouvé dans la réponse GetProfiles")
    return match.group(1)


def get_stream_uri(
    device_xaddr: str,
    username: str,
    password: str,
    profile_token: str | None = None,
) -> str:
    """Retourne l'URL RTSP (RTP-Unicast) d'une caméra ONVIF authentifiée.

    Enchaîne GetCapabilities -> (GetProfiles si profile_token non fourni) -> GetStreamUri.
    """
    media_xaddr = get_media_xaddr(device_xaddr, username, password)
    token = profile_token or get_first_profile_token(media_xaddr, username, password)

    body = (
        '<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
        "<trt:StreamSetup>"
        '<tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>'
        '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema"><tt:Protocol>RTSP</tt:Protocol></tt:Transport>'
        "</trt:StreamSetup>"
        f"<trt:ProfileToken>{token}</trt:ProfileToken>"
        "</trt:GetStreamUri>"
    )
    xml = _soap_call(media_xaddr, body, username, password)
    match = re.search(r"<(?:[\w]+:)?Uri>(.*?)</(?:[\w]+:)?Uri>", xml, re.DOTALL)
    if not match:
        raise OnvifError("URL de flux introuvable dans la réponse GetStreamUri")
    return match.group(1).strip()
