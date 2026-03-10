import typer
import sys
from rich.console import Console
from rich.panel import Panel
from microagents.core.business_value import BusinessValueCalculator, calculate_roi

console = Console()
app = typer.Typer(help="MicroAgents Platform CLI")

@app.command()
def version():
    """Display the version of MicroAgents Platform."""
    console.print("MicroAgents Platform v1.0.0")

@app.command()
def calculate(
    investment: float = typer.Option(..., "--investment", "-i", help="Initial investment"),
    returns: float = typer.Option(..., "--returns", "-r", help="Total returns")
):
    """Calculate ROI based on investment and returns."""
    try:
        roi = calculate_roi(investment, returns)
        console.print(Panel(f"ROI: [bold green]{roi:.2f}%[/bold green]", title="Calculation Result"))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

if __name__ == "__main__":
    app()
