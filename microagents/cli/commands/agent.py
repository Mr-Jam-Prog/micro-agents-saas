"""
Commandes de gestion des agents (Version Minimale).
"""

import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer(help="🤖 Gestion des agents")

@app.command("list")
def list_agents():
    """Liste les agents disponibles."""
    table = Table(title="Agents disponibles")
    table.add_column("ID", style="cyan")
    table.add_column("Nom", style="green")
    table.add_column("Statut", style="yellow")

    table.add_row("agent-001", "Cost Optimizer", "Active")
    table.add_row("agent-002", "Security Scanner", "Active")

    console.print(table)

@app.command("status")
def agent_status(agent_id: str):
    """Affiche le statut d'un agent spécifique."""
    console.print(f"[bold]Statut de l'agent {agent_id}:[/bold] [green]En ligne[/green]")

if __name__ == "__main__":
    app()
