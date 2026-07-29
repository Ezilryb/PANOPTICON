"""Rendu terminal (tables et tableau de bord temps réel) pour la CLI PANOPTICON."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime
from typing import Deque, Optional

import websockets
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

_STATUS_STYLE = {
    "running": "bold green",
    "online": "bold green",
    "stopped": "grey62",
    "offline": "grey62",
    "crashed": "bold red",
    "starting": "yellow",
    "reconnecting": "yellow",
}


def _status_text(value: str) -> Text:
    return Text(value, style=_STATUS_STYLE.get(value, "white"))


def cameras_table(cameras: list[dict]) -> Table:
    table = Table(title="Caméras", expand=True, border_style="grey50")
    table.add_column("Nom")
    table.add_column("Zone")
    table.add_column("FPS", justify="right")
    table.add_column("Statut")
    table.add_column("ID", style="dim", overflow="fold")
    if not cameras:
        table.add_row(Text("—"), Text("—"), "—", Text("aucune caméra configurée"), "—")
        return table
    for cam in cameras:
        table.add_row(
            Text(cam["name"]),
            Text(cam.get("zone", "-")),
            str(cam.get("target_fps", "-")),
            _status_text(cam["status"]),
            cam["id"],
        )
    return table


def modules_table(modules: list[dict]) -> Table:
    table = Table(title="Modules DAEMON", expand=True, border_style="grey50")
    table.add_column("Module")
    table.add_column("Statut")
    table.add_column("CPU %", justify="right")
    table.add_column("RAM (MB)", justify="right")
    table.add_column("Message")
    for mod in modules:
        cpu = f"{mod['cpu_percent']:.0f}" if mod.get("cpu_percent") is not None else "-"
        ram = f"{mod['ram_mb']:.0f}" if mod.get("ram_mb") is not None else "-"
        table.add_row(
            mod["name"],
            _status_text(mod["status"]),
            cpu,
            ram,
            Text(mod.get("message") or "", style="yellow"),
        )
    return table


def resources_panel(res: Optional[dict]) -> Panel:
    if not res:
        return Panel("en attente…", title="Ressources système", border_style="blue")
    gpu = res.get("gpu_name") if res.get("gpu_available") else None
    text = (
        f"CPU : {res['cpu_percent']:.0f}%\n"
        f"RAM : {res['ram_used_mb']:.0f} / {res['ram_total_mb']:.0f} MB\n"
        f"GPU : {gpu or 'indisponible'}"
    )
    return Panel(text, title="Ressources système", border_style="blue")


def _format_ts(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%H:%M:%S")
    except Exception:
        return raw[:8]


def events_table(events: list[dict], limit: int = 15) -> Table:
    table = Table(title=f"Derniers événements (max {limit})", expand=True, border_style="grey50")
    table.add_column("Heure", width=10)
    table.add_column("Type")
    table.add_column("Zone")
    table.add_column("Module")
    for ev in events[:limit]:
        table.add_row(
            _format_ts(ev.get("timestamp", "")),
            ev.get("event_type", ""),
            Text(ev.get("zone", "")),
            ev.get("source_module", ""),
        )
    if not events:
        table.add_row("—", "en attente d'événements…", "—", "—")
    return table


def summary_panel(summary: dict) -> Panel:
    lines = [f"Fenêtre : dernières {summary['period_hours']}h — {summary['total_events']} événement(s)"]

    def _section(title: str, counts: dict) -> None:
        if not counts:
            return
        lines.append("")
        lines.append(f"{title} :")
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {key:<28} {count}")

    _section("Par type", summary.get("by_type", {}))
    _section("Par zone", summary.get("by_zone", {}))
    _section("Par module", summary.get("by_module", {}))
    return Panel("\n".join(lines), title="Résumé SYS-LOG", border_style="magenta")


def actions_table(actions: list[dict]) -> Table:
    table = Table(title="Actions opérateur", expand=True, border_style="grey50")
    table.add_column("Heure", width=10)
    table.add_column("Action")
    table.add_column("Cible")
    for a in actions:
        table.add_row(_format_ts(a.get("timestamp", "")), a.get("action", ""), Text(a.get("target", "")))
    if not actions:
        table.add_row("—", "aucune action enregistrée", "—")
    return table


def persons_table(persons: list[dict]) -> Table:
    table = Table(title="Personnes enrôlées (ROSTER)", expand=True, border_style="grey50")
    table.add_column("Nom")
    table.add_column("Consentement")
    table.add_column("Photos", justify="right")
    table.add_column("ID", style="dim", overflow="fold")
    for p in persons:
        table.add_row(
            Text(p["name"]),
            _format_ts(p.get("consent_confirmed_at", "")),
            str(len(p.get("reference_photo_paths", []))),
            p["id"],
        )
    if not persons:
        table.add_row("—", "—", "—", "aucune personne enrôlée")
    return table


def rules_table(rules: list[dict]) -> Table:
    table = Table(title="Règles PULSE_TRACK", expand=True, border_style="grey50")
    table.add_column("Nom")
    table.add_column("Conditions")
    table.add_column("Action")
    table.add_column("Cible")
    table.add_column("Activée")
    table.add_column("ID", style="dim", overflow="fold")
    for r in rules:
        cond = ", ".join(f"{k}={v}" for k, v in (r.get("conditions") or {}).items()) or "(toute)"
        table.add_row(
            Text(r["name"]),
            Text(cond),
            r["action"],
            Text(r["action_target"]),
            _status_text("running" if r["enabled"] else "stopped"),
            r["id"],
        )
    if not rules:
        table.add_row("—", "—", "—", "—", "—", "aucune règle")
    return table


def alerts_table(alerts: list[dict]) -> Table:
    table = Table(title="Alertes PULSE_TRACK", expand=True, border_style="grey50")
    table.add_column("Heure", width=10)
    table.add_column("Règle")
    table.add_column("Type d'événement")
    table.add_column("Ack.")
    table.add_column("ID", style="dim", overflow="fold")
    for a in alerts:
        payload = a.get("payload") or {}
        event = payload.get("event") or {}
        table.add_row(
            _format_ts(a.get("triggered_at", "")),
            Text(payload.get("rule_name", "")),
            event.get("event_type", ""),
            _status_text("running" if a.get("acknowledged") else "starting"),
            a["id"],
        )
    if not alerts:
        table.add_row("—", "—", "—", "—", "aucune alerte")
    return table


def _build_layout() -> Layout:
    layout = Layout(name="root")
    layout.split(Layout(name="header", size=3), Layout(name="body"))
    layout["body"].split_row(Layout(name="left", ratio=2), Layout(name="right", ratio=3))
    layout["left"].split(Layout(name="resources", size=6), Layout(name="modules"))
    layout["right"].split(Layout(name="cameras", ratio=1), Layout(name="events", ratio=2))
    return layout


def _update_layout(
    layout: Layout,
    modules: list[dict],
    resources: Optional[dict],
    cameras: list[dict],
    events: "Deque[dict]",
    connected: bool,
) -> None:
    state = "connecté" if connected else "reconnexion en cours…"
    layout["header"].update(
        Panel(
            Text(f"PANOPTICON — monitoring en direct ({state})", justify="center", style="bold white"),
            style="on blue",
        )
    )
    layout["resources"].update(resources_panel(resources))
    layout["modules"].update(modules_table(modules))
    layout["cameras"].update(cameras_table(cameras))
    layout["events"].update(events_table(list(events), limit=20))


async def run_live_monitor(ws_url: str, api_base: str) -> None:
    """Se connecte à /ws/live et affiche un tableau de bord terminal mis à jour en direct."""
    from cli.client import PanopticonClient  # import local pour éviter les imports circulaires

    recent_events: "Deque[dict]" = deque(maxlen=200)
    modules: list[dict] = []
    resources: Optional[dict] = None
    cameras: list[dict] = []

    with PanopticonClient(api_base) as client:
        try:
            cameras = client.list_cameras()
        except Exception:
            pass
        try:
            recent_events.extend(client.list_events(limit=20))
        except Exception:
            pass

    layout = _build_layout()
    _update_layout(layout, modules, resources, cameras, recent_events, connected=False)

    with Live(layout, console=console, refresh_per_second=4, screen=True):
        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=20) as ws:
                    _update_layout(layout, modules, resources, cameras, recent_events, connected=True)
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "status":
                            modules = msg.get("modules", modules)
                            resources = msg.get("resources", resources)
                        elif msg.get("type") == "event":
                            recent_events.appendleft(msg["data"])
                        _update_layout(layout, modules, resources, cameras, recent_events, connected=True)
            except (OSError, websockets.WebSocketException):
                _update_layout(layout, modules, resources, cameras, recent_events, connected=False)
                await asyncio.sleep(3)
