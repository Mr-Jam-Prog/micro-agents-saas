"""
Commandes pour le calculateur de ROI.
"""

import typer
from rich.console import Console
from rich.table import Table
from microagents.core.business_value.calculator import ROICalculator

console = Console()
app = typer.Typer(help="📈 Calculateur de ROI")

@app.command("calculate")
def calculate_roi(
    investment: float = typer.Option(..., "--investment", "-i", help="Investissement initial"),
    savings: float = typer.Option(..., "--savings", "-s", help="Économies mensuelles"),
    duration: int = typer.Option(12, "--duration", "-d", help="Durée en mois")
):
    """Calcule le ROI basé sur l'investissement et les économies."""
    calculator = ROICalculator()
    roi = calculator.calculate_roi(investment, savings * duration)

    table = Table(title="Résultat du calcul ROI")
    table.add_column("Métrique", style="cyan")
    table.add_column("Valeur", style="green")

    table.add_row("Investissement Initial", f"${investment:,.2f}")
    table.add_row("Économies Totales", f"${savings * duration:,.2f}")
    table.add_row("ROI (%)", f"{roi:.2f}%")

    console.print(table)

if __name__ == "__main__":
    app()
