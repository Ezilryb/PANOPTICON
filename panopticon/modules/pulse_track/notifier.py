"""PULSE_TRACK — notifications push/email/webhook.

'push' = notification système locale (best-effort, aucun service cloud) ;
'webhook' = requête HTTP POST vers une URL au choix de l'opérateur (réseau
local ou internet, PANOPTICON ne présuppose aucun service particulier) ;
'email' = envoi SMTP vers le serveur configuré par l'opérateur (local ou
distant, à son choix).

Avertissement : send_email et la notification système (macOS/Windows) n'ont
pas pu être testées contre un vrai serveur SMTP ni un vrai bureau graphique
dans cet environnement de développement (sandbox Linux headless, sans accès
SMTP sortant) — la logique suit les APIs standard, mais à vérifier en
conditions réelles.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class NotificationError(RuntimeError):
    """Échec d'envoi d'une notification PULSE_TRACK."""


def send_webhook(url: str, payload: dict, timeout: float = 5.0) -> None:
    """POST JSON vers l'URL cible (locale ou distante, au choix de l'opérateur)."""
    data = json.dumps(payload, default=str).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        raise NotificationError(f"Webhook {url} -> HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise NotificationError(f"Webhook {url} injoignable: {exc.reason}") from exc


def send_email(smtp_config: dict, to_address: str, subject: str, body: str) -> None:
    """Envoie un email via le serveur SMTP configuré par l'opérateur (local ou distant).

    ``smtp_config`` attendu : {"host": str, "port": int, "username": str,
    "password": str, "use_tls": bool, "from": str}.
    """
    import smtplib
    from email.message import EmailMessage

    if "host" not in smtp_config:
        raise NotificationError("Configuration SMTP incomplète : 'host' manquant.")

    msg = EmailMessage()
    msg["From"] = smtp_config.get("from", "panopticon@localhost")
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body)

    host = smtp_config["host"]
    port = int(smtp_config.get("port", 587))
    use_tls = smtp_config.get("use_tls", True)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_tls:
                server.starttls()
            username, password = smtp_config.get("username"), smtp_config.get("password")
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise NotificationError(f"Envoi email vers {host}:{port} échoué: {exc}") from exc


def send_local_notification(title: str, message: str) -> bool:
    """Notification système locale (best-effort) — aucun service cloud.

    Retourne False si aucun mécanisme local n'est disponible (ex. serveur
    headless sans session graphique) : utilisez alors 'webhook' ou 'email'.
    """
    system = platform.system()
    try:
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=5, check=False)
            return True
        if system == "Darwin" and shutil.which("osascript"):
            script = f'display notification {message!r} with title {title!r}'
            subprocess.run(["osascript", "-e", script], timeout=5, check=False)
            return True
        if system == "Windows" and shutil.which("msg"):
            # Utilitaire Windows natif (msg.exe) : message envoyé à la session active.
            subprocess.run(["msg", "*", f"{title}: {message}"], timeout=5, check=False)
            return True
    except Exception:
        logger.exception("Échec de la notification système locale")
    logger.warning(
        "Notification locale indisponible sur ce système (%s, pas de session graphique ?) "
        "— utilisez 'webhook' ou 'email' pour un déploiement serveur.",
        system,
    )
    return False
