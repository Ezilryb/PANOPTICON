"""Client HTTP léger pour piloter PANOPTICON depuis la ligne de commande.

Ce module ne dépend d'aucune bibliothèque de vision (cv2, torch, ultralytics) :
il communique uniquement avec l'API FastAPI déjà démarrée (``panopticon.py
serve``), exactement comme le ferait NEXUS-V depuis un navigateur. La CLI et
le service restent ainsi deux processus séparés, comme docker/dockerd ou
systemctl/systemd.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx

from shared.config import settings


def _default_api_url() -> str:
    host = settings.panopticon_host
    if host in ("0.0.0.0", "::", ""):
        host = "localhost"
    return f"http://{host}:{settings.panopticon_port}"


DEFAULT_API_URL = os.environ.get("PANOPTICON_API_URL") or _default_api_url()


class PanopticonAPIError(RuntimeError):
    """Levée quand l'API PANOPTICON répond en erreur ou est injoignable."""


class PanopticonClient:
    """Enveloppe fine autour de l'API REST de PANOPTICON."""

    def __init__(self, base_url: str = DEFAULT_API_URL, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PanopticonClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise PanopticonAPIError(
                f"Impossible de contacter l'API PANOPTICON sur {self.base_url} "
                "(le service est-il démarré ? -> python panopticon.py serve)"
            ) from exc
        except httpx.TimeoutException as exc:
            raise PanopticonAPIError(f"Délai dépassé en contactant {self.base_url}{path}") from exc

        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except ValueError:
                pass
            raise PanopticonAPIError(f"{method} {path} -> HTTP {resp.status_code}: {detail}")

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # --- Santé / ressources -------------------------------------------------
    def health(self) -> dict:
        return self._request("GET", "/health")

    def resources(self) -> dict:
        return self._request("GET", "/api/daemon/resources")

    # --- Modules --------------------------------------------------------------
    def list_modules(self) -> list[dict]:
        return self._request("GET", "/api/daemon/modules")

    def start_module(self, name: str) -> dict:
        return self._request("POST", f"/api/daemon/modules/{name}/start")

    def stop_module(self, name: str) -> dict:
        return self._request("POST", f"/api/daemon/modules/{name}/stop")

    # --- Caméras ----------------------------------------------------------
    def list_cameras(self) -> list[dict]:
        return self._request("GET", "/api/cameras")

    def get_camera(self, camera_id: str | UUID) -> dict:
        return self._request("GET", f"/api/cameras/{camera_id}")

    def create_camera(
        self, name: str, connection_url: str, zone: str = "default", target_fps: int = 3
    ) -> dict:
        payload = {
            "name": name,
            "connection_url": connection_url,
            "zone": zone,
            "target_fps": target_fps,
        }
        return self._request("POST", "/api/cameras", json=payload)

    def delete_camera(self, camera_id: str | UUID) -> None:
        self._request("DELETE", f"/api/cameras/{camera_id}")

    def camera_health(self, camera_id: str | UUID) -> dict:
        return self._request("GET", f"/api/cameras/{camera_id}/health")

    # --- Événements ---------------------------------------------------------
    def list_events(
        self,
        limit: int = 50,
        camera_id: Optional[str] = None,
        event_type: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if camera_id:
            params["camera_id"] = camera_id
        if event_type:
            params["event_type"] = event_type
        if zone:
            params["zone"] = zone
        return self._request("GET", "/api/events", params=params)

    # --- SYS-LOG : résumé et actions opérateur ------------------------------
    def events_summary(self, hours: int = 24) -> dict:
        return self._request("GET", "/api/sys-log/summary", params={"hours": hours})

    def list_actions(self, limit: int = 50, action: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if action:
            params["action"] = action
        return self._request("GET", "/api/sys-log/actions", params=params)

    # --- ROSTER : personnes enrôlées ---------------------------------------
    def list_persons(self) -> list[dict]:
        return self._request("GET", "/api/persons")

    def enroll_person(self, name: str, consent: bool, photo_paths: list[str]) -> dict:
        files = [
            ("photos", (Path(p).name, open(p, "rb"), "image/jpeg")) for p in photo_paths  # noqa: SIM115
        ]
        try:
            return self._request(
                "POST",
                "/api/persons",
                data={"name": name, "consent": "true" if consent else "false"},
                files=files,
                timeout=120.0,  # 1er appel : charge le modèle facial local (+ téléchargement initial des poids)
            )
        finally:
            for _, (_name, fh, _ctype) in files:
                fh.close()

    def delete_person(self, person_id: str) -> None:
        self._request("DELETE", f"/api/persons/{person_id}")

    # --- PULSE_TRACK : règles et alertes -------------------------------------
    def list_rules(self) -> list[dict]:
        return self._request("GET", "/api/rules")

    def create_rule(
        self, name: str, conditions: dict, action: str, action_target: str, enabled: bool = True
    ) -> dict:
        payload = {
            "name": name,
            "conditions": conditions,
            "action": action,
            "action_target": action_target,
            "enabled": enabled,
        }
        return self._request("POST", "/api/rules", json=payload)

    def update_rule(self, rule_id: str, **fields: Any) -> dict:
        return self._request("PUT", f"/api/rules/{rule_id}", json={k: v for k, v in fields.items() if v is not None})

    def delete_rule(self, rule_id: str) -> None:
        self._request("DELETE", f"/api/rules/{rule_id}")

    def list_alerts(self, acknowledged: Optional[bool] = None, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if acknowledged is not None:
            params["acknowledged"] = acknowledged
        return self._request("GET", "/api/alerts", params=params)

    def acknowledge_alert(self, alert_id: str) -> dict:
        return self._request("POST", f"/api/alerts/{alert_id}/acknowledge")
