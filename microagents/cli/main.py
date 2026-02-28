"""
CLI principal pour MicroAgents Platform.
Interface en ligne de commande simplifiée.
"""

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from microagents.cli.commands.agent import app as agent_app
from microagents.cli.commands.roi import app as roi_app

# Initialisation
console = Console()
app = typer.Typer(help="🚀 MicroAgents Platform CLI", rich_markup_mode="rich")

# Version
__version__ = "1.0.0"

# Sous-commandes
app.add_typer(agent_app, name="agent", help="Gestion des agents")
app.add_typer(roi_app, name="roi", help="Calculateur de ROI")

@app.command("version")
def show_version():
    """Affiche la version du CLI."""
    console.print(Panel.fit(
        f"[bold cyan]MicroAgents Platform CLI[/bold cyan] v{__version__}",
        border_style="cyan"
    ))

@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Mode verbeux"),
):
    """
    MicroAgents Platform CLI - Gestion d'agents DevOps.
    """
    if verbose:
        console.print("[dim]Mode verbeux activé[/dim]")

if __name__ == "__main__":
    app()
