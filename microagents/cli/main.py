import typer
from rich.console import Console
from microagents.cli.commands import agent, roi

console = Console()
app = typer.Typer(help="MicroAgents Platform CLI")

app.add_typer(agent.app, name="agent")
app.add_typer(roi.app, name="roi")

@app.command()
def version():
    """Display the version of MicroAgents Platform."""
    console.print("MicroAgents Platform v1.0.0")

if __name__ == "__main__":
    app()
