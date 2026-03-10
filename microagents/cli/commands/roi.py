import typer
from rich.console import Console
from microagents.core.business_value import calculate_roi

console = Console()
app = typer.Typer(help="ROI analysis commands")

@app.command("calculate")
def roi_calculate(
    investment: float = typer.Option(..., "--investment", "-i"),
    returns: float = typer.Option(..., "--returns", "-r")
):
    """Calculate ROI for a project."""
    roi = calculate_roi(investment, returns)
    console.print(f"Calculated ROI: [bold green]{roi:.2f}%[/bold green]")

if __name__ == "__main__":
    app()
