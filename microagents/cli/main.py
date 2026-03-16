import asyncio
import typer
from rich.console import Console
from microagents.cli.commands import agent, roi
from microagents.core.runtime import AgentRuntime
from microagents.core.events import EventBus
from microagents.core.registry import AgentRegistry

console = Console()
app = typer.Typer(help="MicroAgents Platform CLI")

app.add_typer(agent.app, name="agent")
app.add_typer(roi.app, name="roi")

@app.command()
def version():
    """Display the version of MicroAgents Platform."""
    console.print("MicroAgents Platform v1.0.0")

@app.command()
def start():
    """Start the MicroAgents platform runtime."""
    console.print("[bold green]Starting MicroAgents Runtime...[/bold green]")
    bus = EventBus()
    registry = AgentRegistry()
    runtime = AgentRuntime(bus, registry)

    try:
        asyncio.run(runtime.start())
    except KeyboardInterrupt:
        runtime.stop()
        console.print("[yellow]Runtime stopped by user.[/yellow]")

if __name__ == "__main__":
    app()
