"""Interface en ligne de commande PANOPTICON.

Toutes les opérations passent par cette CLI : démarrage du service, gestion
des caméras et des modules DAEMON, consultation des événements, et tableau de
bord temps réel dans le terminal. Aucune interface web n'est nécessaire pour
piloter le système au quotidien — NEXUS-V reste disponible en option.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from cli.client import DEFAULT_API_URL, PanopticonAPIError, PanopticonClient
from cli.dashboard import (
    actions_table,
    cameras_table,
    events_table,
    modules_table,
    resources_panel,
    run_live_monitor,
    summary_panel,
)
from cli.service import service_app

console = Console()

app = typer.Typer(
    name="panopticon",
    help="PANOPTICON — pilotage du système de vision multi-caméras depuis la ligne de commande.",
    no_args_is_help=True,
    add_completion=False,
)

camera_app = typer.Typer(help="Gestion des caméras ARGUS.", no_args_is_help=True)
module_app = typer.Typer(help="Gestion des modules DAEMON.", no_args_is_help=True)
events_app = typer.Typer(help="Consultation des événements.", no_args_is_help=True)
syslog_app = typer.Typer(help="SYS-LOG — résumé des événements et actions opérateur.", no_args_is_help=True)

app.add_typer(camera_app, name="camera")
app.add_typer(module_app, name="module")
app.add_typer(events_app, name="events")
app.add_typer(syslog_app, name="syslog")
app.add_typer(service_app, name="service")

ApiUrlOption = typer.Option(
    DEFAULT_API_URL, "--api-url", envvar="PANOPTICON_API_URL", help="URL de l'API PANOPTICON."
)


def _fail(exc: Exception) -> None:
    console.print(f"[bold red]Erreur:[/bold red] {escape(str(exc))}")
    raise typer.Exit(code=1)


def _ws_url(api_url: str) -> str:
    return api_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/live"


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, help="Interface d'écoute (défaut : .env / config)."),
    port: Optional[int] = typer.Option(None, help="Port d'écoute (défaut : .env / config)."),
    reload: bool = typer.Option(False, help="Rechargement automatique (développement uniquement)."),
) -> None:
    """Démarre le service PANOPTICON (API FastAPI + orchestrateur DAEMON).

    C'est la seule commande qui doit rester active en continu : lancez-la dans
    un terminal (ou en arrière-plan / service système), puis pilotez tout le
    reste depuis d'autres commandes (status, camera, module, events, monitor).
    """
    import multiprocessing as mp

    import uvicorn

    from shared.config import settings
    from shared.logging_utils import setup_logging

    mp.set_start_method("spawn", force=True)
    setup_logging(settings.log_level)
    console.print(
        f"[bold blue]PANOPTICON[/bold blue] — démarrage (profil [yellow]{settings.panopticon_profile}[/yellow])"
    )
    uvicorn.run(
        "api.main:app",
        host=host or settings.panopticon_host,
        port=port or settings.panopticon_port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command()
def status(api_url: str = ApiUrlOption) -> None:
    """Affiche un état instantané : ressources, modules, caméras."""
    try:
        with PanopticonClient(api_url) as client:
            health = client.health()
            resources = client.resources()
            modules = client.list_modules()
            cameras = client.list_cameras()
    except PanopticonAPIError as exc:
        _fail(exc)
        return

    console.print(
        f"[bold]PANOPTICON[/bold] — statut [green]{health['status']}[/green] "
        f"· profil [yellow]{health['profile']}[/yellow]"
    )
    console.print(resources_panel(resources))
    console.print(modules_table(modules))
    console.print(cameras_table(cameras))


@app.command()
def monitor(
    api_url: str = ApiUrlOption,
    ws_url: Optional[str] = typer.Option(None, help="URL WebSocket (défaut : dérivée de --api-url)."),
) -> None:
    """Tableau de bord temps réel dans le terminal (équivalent de NEXUS-V, sans navigateur)."""
    resolved = ws_url or _ws_url(api_url)
    console.print(f"[dim]Connexion à {resolved} — Ctrl+C pour quitter[/dim]")
    try:
        asyncio.run(run_live_monitor(resolved, api_url))
    except KeyboardInterrupt:
        console.print("\n[dim]Monitoring arrêté.[/dim]")


# --------------------------------------------------------------------------- #
# Caméras
# --------------------------------------------------------------------------- #


@camera_app.command("list")
def camera_list(api_url: str = ApiUrlOption) -> None:
    """Liste les caméras enregistrées."""
    try:
        with PanopticonClient(api_url) as client:
            cameras = client.list_cameras()
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(cameras_table(cameras))


@camera_app.command("add")
def camera_add(
    name: str = typer.Argument(..., help="Nom de la caméra."),
    url: str = typer.Argument(..., help="URL de connexion ('0' = webcam locale, ou rtsp://…)."),
    zone: str = typer.Option("default", help="Zone associée."),
    fps: int = typer.Option(3, help="FPS cible."),
    api_url: str = ApiUrlOption,
) -> None:
    """Ajoute une caméra."""
    try:
        with PanopticonClient(api_url) as client:
            cam = client.create_camera(name, url, zone, fps)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(f"[green]Caméra créée:[/green] {escape(cam['name'])} ({cam['id']})")


@camera_app.command("remove")
def camera_remove(
    camera_id: str = typer.Argument(..., help="ID de la caméra."),
    api_url: str = ApiUrlOption,
) -> None:
    """Supprime une caméra."""
    try:
        with PanopticonClient(api_url) as client:
            client.delete_camera(camera_id)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(f"[green]Caméra {camera_id} supprimée.[/green]")


@camera_app.command("show")
def camera_show(
    camera_id: str = typer.Argument(..., help="ID de la caméra."),
    api_url: str = ApiUrlOption,
) -> None:
    """Détail et santé d'une caméra."""
    try:
        with PanopticonClient(api_url) as client:
            cam = client.get_camera(camera_id)
            health = client.camera_health(camera_id)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(cam)
    console.print(health)


@camera_app.command("discover")
def camera_discover(
    timeout: int = typer.Option(4, help="Durée d'écoute des réponses ONVIF, en secondes."),
) -> None:
    """Recherche des caméras ONVIF sur le réseau local (WS-Discovery, sans configuration)."""
    from modules.argus.onvif_discovery import discover_onvif_devices

    console.print(f"[dim]Sondage ONVIF (multicast 239.255.255.250:3702) — {timeout}s…[/dim]")
    try:
        devices = discover_onvif_devices(timeout=timeout)
    except OSError as exc:
        console.print(f"[bold red]Découverte impossible:[/bold red] {escape(str(exc))}")
        raise typer.Exit(code=1)

    if not devices:
        console.print(
            "[yellow]Aucune caméra ONVIF détectée.[/yellow] "
            "[dim](réseau sans multicast, caméras non-ONVIF, ou pare-feu — "
            "ajoutez-les manuellement avec 'camera add').[/dim]"
        )
        return

    table = Table(title="Caméras ONVIF détectées", expand=True, border_style="grey50")
    table.add_column("Nom")
    table.add_column("Adresse ONVIF (XAddr)")
    table.add_column("IP source")
    for d in devices:
        table.add_row(Text(d["name"]), d["xaddr"] or "-", d["source_ip"])
    console.print(table)
    console.print(
        "[dim]ONVIF ne fournit que l'adresse de gestion de la caméra, pas le chemin RTSP "
        "(spécifique au fabricant). Ajoutez-la avec :[/dim]\n"
        '  python panopticon.py camera add "<nom>" rtsp://<user>:<mdp>@<ip>:554/<chemin> --zone <zone>'
    )


# --------------------------------------------------------------------------- #
# Modules
# --------------------------------------------------------------------------- #


@module_app.command("list")
def module_list(api_url: str = ApiUrlOption) -> None:
    """État de tous les modules DAEMON."""
    try:
        with PanopticonClient(api_url) as client:
            modules = client.list_modules()
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(modules_table(modules))


@module_app.command("start")
def module_start(
    name: str = typer.Argument(..., help="Nom du module (ex. argus, vault, roster…)."),
    api_url: str = ApiUrlOption,
) -> None:
    """Démarre un module."""
    try:
        with PanopticonClient(api_url) as client:
            mod = client.start_module(name)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    suffix = f" — {escape(mod['message'])}" if mod.get("message") else ""
    console.print(f"[green]{mod['name']}[/green] → {mod['status']}{suffix}")


@module_app.command("stop")
def module_stop(
    name: str = typer.Argument(..., help="Nom du module."),
    api_url: str = ApiUrlOption,
) -> None:
    """Arrête un module."""
    try:
        with PanopticonClient(api_url) as client:
            mod = client.stop_module(name)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(f"[yellow]{mod['name']}[/yellow] → {mod['status']}")


# --------------------------------------------------------------------------- #
# Événements
# --------------------------------------------------------------------------- #


@events_app.command("list")
def events_list(
    limit: int = typer.Option(20, help="Nombre d'événements à afficher."),
    camera_id: Optional[str] = typer.Option(None, "--camera", help="Filtrer par ID de caméra."),
    event_type: Optional[str] = typer.Option(None, "--type", help="Filtrer par type d'événement."),
    zone: Optional[str] = typer.Option(None, help="Filtrer par zone."),
    api_url: str = ApiUrlOption,
) -> None:
    """Liste les derniers événements détectés."""
    try:
        with PanopticonClient(api_url) as client:
            events = client.list_events(limit=limit, camera_id=camera_id, event_type=event_type, zone=zone)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(events_table(events, limit=limit))


@events_app.command("tail")
def events_tail(api_url: str = ApiUrlOption) -> None:
    """Suit les événements en direct (flux WebSocket), une ligne par événement."""
    resolved = _ws_url(api_url)
    console.print(f"[dim]Suivi en direct de {resolved} — Ctrl+C pour quitter[/dim]")

    async def _tail() -> None:
        import json

        import websockets

        async with websockets.connect(resolved) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "event":
                    ev = msg["data"]
                    console.print(
                        f"[cyan]{ev.get('timestamp', '')}[/cyan] "
                        f"[bold]{ev.get('event_type', '')}[/bold] "
                        f"zone={escape(ev.get('zone', ''))} module={ev.get('source_module', '')}"
                    )

    try:
        asyncio.run(_tail())
    except KeyboardInterrupt:
        console.print("\n[dim]Suivi arrêté.[/dim]")


# --------------------------------------------------------------------------- #
# SYS-LOG (résumé + actions opérateur)
# --------------------------------------------------------------------------- #


@syslog_app.command("summary")
def syslog_summary(
    hours: int = typer.Option(24, help="Fenêtre glissante à résumer, en heures."),
    api_url: str = ApiUrlOption,
) -> None:
    """Résumé des événements sur une période (comptes par type / zone / module)."""
    try:
        with PanopticonClient(api_url) as client:
            summary = client.events_summary(hours=hours)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(summary_panel(summary))


@syslog_app.command("actions")
def syslog_actions(
    limit: int = typer.Option(50, help="Nombre d'actions à afficher."),
    action: Optional[str] = typer.Option(None, "--action", help="Filtrer par type d'action."),
    api_url: str = ApiUrlOption,
) -> None:
    """Journal des actions opérateur (modules démarrés/arrêtés, caméras ajoutées/supprimées…)."""
    try:
        with PanopticonClient(api_url) as client:
            actions = client.list_actions(limit=limit, action=action)
    except PanopticonAPIError as exc:
        _fail(exc)
        return
    console.print(actions_table(actions))


if __name__ == "__main__":
    app()
