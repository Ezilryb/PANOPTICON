"""cli/service.py — Installation de PANOPTICON en tant que service système.

Objectif : que ``panopticon.py serve`` puisse tourner en continu sans terminal
ouvert, avec redémarrage automatique en cas de crash. Cette commande ne fait
qu'afficher / écrire localement les fichiers de configuration nécessaires —
elle n'exécute jamais de commande privilégiée (sudo, etc.) à la place de
l'opérateur : les étapes d'installation restent explicites et sous son
contrôle.
"""

from __future__ import annotations

import getpass
import platform
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

service_app = typer.Typer(
    help="Installation de PANOPTICON en tant que service système (démarrage auto, redémarrage sur crash).",
    no_args_is_help=True,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _venv_python() -> Path:
    root = _project_root()
    for candidate in (root / ".venv" / "bin" / "python", root / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _systemd_unit(user: str) -> str:
    root = _project_root()
    python = _venv_python()
    env_file = root / ".env"
    lines = [
        "[Unit]",
        "Description=PANOPTICON — vision multi-caméras",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"WorkingDirectory={root}",
        f'ExecStart={python} {root / "panopticon.py"} serve',
        "Restart=on-failure",
        "RestartSec=5",
    ]
    if env_file.exists():
        lines.append(f"EnvironmentFile={env_file}")
    lines += ["", "[Install]", "WantedBy=multi-user.target", ""]
    return "\n".join(lines)


def _windows_task_command(task_name: str) -> str:
    python = _venv_python()
    root = _project_root()
    return (
        f'schtasks /Create /TN "{task_name}" '
        f'/TR "\\"{python}\\" \\"{root / "panopticon.py"}\\" serve" '
        f"/SC ONLOGON /RL LIMITED /F"
    )


def _launchd_plist(label: str) -> str:
    python = _venv_python()
    root = _project_root()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>Label</key><string>{label}</string>\n"
        "  <key>ProgramArguments</key><array>\n"
        f"    <string>{python}</string>\n"
        f'    <string>{root / "panopticon.py"}</string>\n'
        "    <string>serve</string>\n"
        "  </array>\n"
        f"  <key>WorkingDirectory</key><string>{root}</string>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><true/>\n"
        f'  <key>StandardOutPath</key><string>{root / "panopticon.out.log"}</string>\n'
        f'  <key>StandardErrorPath</key><string>{root / "panopticon.err.log"}</string>\n'
        "</dict></plist>\n"
    )


@service_app.command("install")
def service_install(
    write: bool = typer.Option(False, "--write", help="Écrire le fichier de service localement."),
    user: Optional[str] = typer.Option(None, help="Utilisateur système Linux sous lequel exécuter le service."),
) -> None:
    """Génère la configuration pour démarrer PANOPTICON automatiquement, sans terminal ouvert."""
    system = platform.system()
    root = _project_root()

    if system == "Linux":
        service_user = user or getpass.getuser()
        content = _systemd_unit(service_user)
        console.print(Panel(content, title="Service systemd (Linux)", border_style="blue"))
        target = root / "panopticon.service"
        if write:
            target.write_text(content, encoding="utf-8")
            console.print(f"[green]Écrit dans {target}[/green]\n")
        ref = str(target) if write else "<contenu ci-dessus, à enregistrer dans un fichier>"
        console.print(
            "Installation :\n"
            f"  sudo cp {ref} /etc/systemd/system/panopticon.service\n"
            "  sudo systemctl daemon-reload\n"
            "  sudo systemctl enable --now panopticon\n"
            "  sudo systemctl status panopticon      # vérifier l'état\n"
            "  sudo journalctl -u panopticon -f       # suivre les logs\n\n"
            "[dim]Astuce : lancez d'abord 'python panopticon.py service install --write' "
            "pour générer le fichier localement avant de le copier.[/dim]"
        )

    elif system == "Windows":
        task_name = "PANOPTICON"
        cmd = _windows_task_command(task_name)
        console.print(Panel(cmd, title="Tâche planifiée Windows (démarrage à l'ouverture de session)", border_style="blue"))
        console.print(
            "À exécuter une fois dans PowerShell (droits standard suffisants pour /SC ONLOGON) :\n\n"
            f"  {cmd}\n\n"
            "Gestion ensuite :\n"
            f'  schtasks /Run /TN "{task_name}"                  # démarrer immédiatement\n'
            f'  schtasks /End /TN "{task_name}"                  # arrêter\n'
            f'  schtasks /Query /TN "{task_name}" /V /FO LIST    # état\n'
            f'  schtasks /Delete /TN "{task_name}" /F            # désinstaller\n\n'
            "[dim]Non testable depuis cet environnement (sandbox Linux) — vérifiez sur votre "
            "machine Windows ; si /SC ONLOGON échoue, essayez d'ouvrir PowerShell en administrateur.[/dim]"
        )

    elif system == "Darwin":
        label = "com.panopticon.serve"
        plist = _launchd_plist(label)
        console.print(Panel(plist, title="Agent launchd (macOS)", border_style="blue"))
        target = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plist, encoding="utf-8")
            console.print(f"[green]Écrit dans {target}[/green]\n")
        console.print(
            "Installation :\n"
            f"  cp <contenu ci-dessus> {target}\n"
            f"  launchctl load {target}\n"
            f"  launchctl start {label}"
        )

    else:
        console.print(f"[yellow]Système '{system}' non reconnu — configuration manuelle nécessaire.[/yellow]")
        raise typer.Exit(code=1)


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Affiche les commandes pour retirer le service installé."""
    system = platform.system()
    if system == "Linux":
        console.print(
            "sudo systemctl disable --now panopticon\n"
            "sudo rm /etc/systemd/system/panopticon.service\n"
            "sudo systemctl daemon-reload"
        )
    elif system == "Windows":
        console.print('schtasks /Delete /TN "PANOPTICON" /F')
    elif system == "Darwin":
        label = "com.panopticon.serve"
        target = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        console.print(f"launchctl unload {target}\nrm {target}")
    else:
        console.print(f"[yellow]Système '{system}' non reconnu.[/yellow]")
