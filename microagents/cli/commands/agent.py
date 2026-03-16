import typer
from rich.console import Console

console = Console()
app = typer.Typer(help="Agent management commands")

@app.command("list")
def list_agents():
    """List all available agents."""
    console.print("Listing agents...")
    console.print("- Agent 1 (active)")
    console.print("- Agent 2 (idle)")

@app.command("status")
def agent_status(agent_id: str):
    """Get the status of a specific agent."""
    console.print(f"Status for agent {agent_id}: [green]Healthy[/green]")

if __name__ == "__main__":
    app()
