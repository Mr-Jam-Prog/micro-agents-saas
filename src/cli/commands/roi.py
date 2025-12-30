"""
ROI calculation and analysis commands for MicroAgents Platform.
Advanced ROI modeling, simulation, and optimization.
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import questionary
import yaml
from click import Context
from pydantic import BaseModel, ValidationError, field_validator
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.tree import Tree

from src.cli.config import config
from src.cli.utils import (
    APIError,
    format_json,
    format_table,
    get_api_client,
    handle_api_error,
    spinner,
)
from src.utils.config.settings import get_settings

console = Console()

# ROI Models
class ROIParameters(BaseModel):
    """ROI calculation parameters."""
    
    # Cost parameters
    implementation_cost: float = Field(ge=0, description="One-time implementation cost")
    monthly_cost: float = Field(ge=0, description="Monthly subscription cost")
    additional_costs: Dict[str, float] = Field(default_factory=dict, description="Additional costs")
    
    # Benefit parameters
    monthly_savings: float = Field(ge=0, description="Monthly operational savings")
    productivity_gains: float = Field(ge=0, description="Monthly productivity gains (value)")
    risk_reduction: float = Field(ge=0, description="Monthly risk reduction value")
    revenue_increase: float = Field(ge=0, description="Monthly revenue increase")
    
    # Time parameters
    analysis_period_months: int = Field(ge=1, le=120, default=36, description="Analysis period in months")
    implementation_months: int = Field(ge=0, default=3, description="Implementation period in months")
    
    # Risk parameters
    confidence_level: float = Field(ge=0.5, le=1.0, default=0.95, description="Confidence level for simulations")
    risk_free_rate: float = Field(ge=0.0, le=0.2, default=0.03, description="Annual risk-free rate")
    discount_rate: float = Field(ge=0.0, le=0.5, default=0.1, description="Discount rate for NPV")
    
    # Sensitivity parameters
    sensitivity_variables: List[str] = Field(
        default_factory=lambda: ["monthly_savings", "implementation_cost", "monthly_cost"],
        description="Variables for sensitivity analysis",
    )
    sensitivity_range: float = Field(ge=0.0, le=1.0, default=0.2, description="+/- range for sensitivity analysis")
    
    @field_validator('additional_costs')
    @classmethod
    def validate_additional_costs(cls, v):
        """Validate additional costs are positive."""
        for key, value in v.items():
            if value < 0:
                raise ValueError(f"Additional cost '{key}' cannot be negative: {value}")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "implementation_cost": 50000.0,
                "monthly_cost": 499.0,
                "additional_costs": {
                    "training": 5000.0,
                    "integration": 10000.0,
                },
                "monthly_savings": 15000.0,
                "productivity_gains": 5000.0,
                "risk_reduction": 2000.0,
                "revenue_increase": 10000.0,
                "analysis_period_months": 36,
                "implementation_months": 3,
                "confidence_level": 0.95,
                "risk_free_rate": 0.03,
                "discount_rate": 0.1,
                "sensitivity_variables": ["monthly_savings", "implementation_cost"],
                "sensitivity_range": 0.2,
            }
        }


class ROIResult(BaseModel):
    """ROI calculation result."""
    
    # Core metrics
    net_present_value: float
    internal_rate_of_return: float
    payback_period_months: float
    roi_percentage: float
    break_even_month: float
    
    # Time-series data
    cumulative_cash_flow: List[float]
    monthly_cash_flow: List[float]
    discounted_cash_flow: List[float]
    
    # Risk metrics
    npv_confidence_interval: Tuple[float, float]
    irr_confidence_interval: Tuple[float, float]
    value_at_risk_95: float
    expected_shortfall: float
    
    # Sensitivity analysis
    sensitivity_matrix: Dict[str, Dict[str, float]]
    tornado_chart_data: Dict[str, List[Tuple[str, float]]]
    
    # Comparative metrics
    vs_industry_benchmark: Optional[float] = None
    vs_alternative_solutions: Optional[Dict[str, float]] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "net_present_value": 452150.25,
                "internal_rate_of_return": 0.42,
                "payback_period_months": 8.5,
                "roi_percentage": 320.5,
                "break_even_month": 7.2,
                "cumulative_cash_flow": [ -55000, -40000, -25000, ... ],
                "monthly_cash_flow": [ -55000, 15000, 15000, ... ],
                "discounted_cash_flow": [ -55000, 13636, 12396, ... ],
                "npv_confidence_interval": (385000, 520000),
                "irr_confidence_interval": (0.35, 0.48),
                "value_at_risk_95": -25000,
                "expected_shortfall": -35000,
                "sensitivity_matrix": {
                    "monthly_savings": {"npv": 550000, "irr": 0.48, "payback": 6.5},
                    "implementation_cost": {"npv": 380000, "irr": 0.38, "payback": 10.2},
                },
                "tornado_chart_data": {
                    "npv": [("monthly_savings", 120000), ("implementation_cost", -70000)],
                },
                "vs_industry_benchmark": 1.25,
                "vs_alternative_solutions": {"solution_a": 1.8, "solution_b": 2.3},
            }
        }


class ROIForecast(BaseModel):
    """ROI forecast model."""
    
    forecast_periods: int
    baseline_roi: float
    forecasted_roi: List[float]
    confidence_intervals: List[Tuple[float, float]]
    trend_line: List[float]
    seasonality_factor: Optional[float] = None
    growth_rate: float
    
    class Config:
        json_schema_extra = {
            "example": {
                "forecast_periods": 24,
                "baseline_roi": 320.5,
                "forecasted_roi": [340.2, 360.8, 385.1, ...],
                "confidence_intervals": [(315, 365), (335, 385), (360, 410), ...],
                "trend_line": [320.5, 335.2, 350.8, ...],
                "seasonality_factor": 0.15,
                "growth_rate": 0.08,
            }
        }


@click.group(name="roi")
def roi_group():
    """ROI calculation and analysis tools."""
    pass


@roi_group.command(name="calculate")
@click.argument("target", required=False)
@click.option(
    "--suite",
    "-s",
    type=str,
    help="Suite ID to calculate ROI for",
)
@click.option(
    "--agent",
    "-a",
    type=str,
    help="Agent ID to calculate ROI for",
)
@click.option(
    "--deployment",
    "-d",
    type=str,
    help="Deployment ID to calculate ROI for",
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Interactive parameter input",
)
@click.option(
    "--config-file",
    "-c",
    type=click.Path(exists=True),
    help="ROI configuration file",
)
@click.option(
    "--period",
    "-p",
    type=int,
    default=36,
    help="Analysis period in months",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["json", "yaml", "table", "detailed"]),
    default="table",
    help="Output format",
)
@click.option(
    "--save",
    type=click.Path(),
    help="Save results to file",
)
def calculate_roi(
    target: Optional[str],
    suite: Optional[str],
    agent: Optional[str],
    deployment: Optional[str],
    interactive: bool,
    config_file: Optional[str],
    period: int,
    output: str,
    save: Optional[str],
):
    """Calculate ROI for an agent, suite, or deployment."""
    
    try:
        api_client = get_api_client()
        
        # Determine target
        target_id, target_type = _resolve_target(target, suite, agent, deployment)
        
        if not target_id:
            console.print("[red]Please specify a target (suite, agent, or deployment)[/red]")
            return
        
        # Get ROI parameters
        if config_file:
            with spinner("Loading configuration..."):
                roi_params = _load_roi_config(config_file)
        elif interactive:
            roi_params = _interactive_roi_config(target_id, target_type, period)
        else:
            # Get default parameters from API
            with spinner(f"Loading default parameters for {target_type} '{target_id}'..."):
                roi_params = _get_default_roi_params(api_client, target_id, target_type, period)
        
        # Validate parameters
        try:
            validated_params = ROIParameters(**roi_params)
        except ValidationError as e:
            console.print("[red]Invalid ROI parameters:[/red]")
            console.print(format_json(e.errors()))
            return
        
        # Calculate ROI
        with spinner("Calculating ROI..."):
            result = _calculate_roi(api_client, target_id, target_type, validated_params)
        
        # Display results
        _display_roi_results(result, output, target_id, target_type)
        
        # Save results if requested
        if save:
            _save_roi_results(result, save, output)
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _resolve_target(
    target: Optional[str],
    suite: Optional[str],
    agent: Optional[str],
    deployment: Optional[str],
) -> Tuple[Optional[str], str]:
    """Resolve target ID and type."""
    
    if suite:
        return suite, "suite"
    elif agent:
        return agent, "agent"
    elif deployment:
        return deployment, "deployment"
    elif target:
        # Try to auto-detect type
        if target.startswith("suite-"):
            return target, "suite"
        elif target.startswith("agent-"):
            return target, "agent"
        elif target.startswith("dep-"):
            return target, "deployment"
        else:
            # Default to suite
            return target, "suite"
    else:
        return None, "unknown"


def _load_roi_config(config_file: str) -> Dict[str, Any]:
    """Load ROI configuration from file."""
    
    path = Path(config_file)
    
    if path.suffix.lower() in [".yaml", ".yml"]:
        with open(path) as f:
            return yaml.safe_load(f)
    else:
        # Assume JSON
        with open(path) as f:
            return json.load(f)


def _interactive_roi_config(
    target_id: str,
    target_type: str,
    default_period: int,
) -> Dict[str, Any]:
    """Interactive ROI configuration wizard."""
    
    console.print(f"\n[bold cyan]ROI Configuration for {target_type} '{target_id}'[/bold cyan]")
    console.print("=" * 60)
    
    config = {}
    
    # Cost parameters
    console.print("\n[bold]📊 Cost Parameters[/bold]")
    
    config["implementation_cost"] = questionary.text(
        "Implementation cost (one-time):",
        default="50000.0",
        validate=lambda x: float(x) >= 0,
    ).ask()
    config["implementation_cost"] = float(config["implementation_cost"])
    
    config["monthly_cost"] = questionary.text(
        "Monthly subscription cost:",
        default="499.0",
        validate=lambda x: float(x) >= 0,
    ).ask()
    config["monthly_cost"] = float(config["monthly_cost"])
    
    # Additional costs
    additional_costs = {}
    while questionary.confirm("Add additional costs?", default=False).ask():
        cost_name = questionary.text("Cost name:").ask()
        cost_amount = questionary.text(
            f"Amount for {cost_name}:",
            validate=lambda x: float(x) >= 0,
        ).ask()
        additional_costs[cost_name] = float(cost_amount)
    
    if additional_costs:
        config["additional_costs"] = additional_costs
    
    # Benefit parameters
    console.print("\n[bold]💰 Benefit Parameters[/bold]")
    
    config["monthly_savings"] = questionary.text(
        "Monthly operational savings:",
        default="15000.0",
        validate=lambda x: float(x) >= 0,
    ).ask()
    config["monthly_savings"] = float(config["monthly_savings"])
    
    config["productivity_gains"] = questionary.text(
        "Monthly productivity gains (monetary value):",
        default="5000.0",
        validate=lambda x: float(x) >= 0,
    ).ask()
    config["productivity_gains"] = float(config["productivity_gains"])
    
    config["risk_reduction"] = questionary.text(
        "Monthly risk reduction value:",
        default="2000.0",
        validate=lambda x: float(x) >= 0,
    ).ask()
    config["risk_reduction"] = float(config["risk_reduction"])
    
    config["revenue_increase"] = questionary.text(
        "Monthly revenue increase:",
        default="10000.0",
        validate=lambda x: float(x) >= 0,
    ).ask()
    config["revenue_increase"] = float(config["revenue_increase"])
    
    # Time parameters
    console.print("\n[bold]⏰ Time Parameters[/bold]")
    
    config["analysis_period_months"] = questionary.text(
        "Analysis period (months):",
        default=str(default_period),
        validate=lambda x: 1 <= int(x) <= 120,
    ).ask()
    config["analysis_period_months"] = int(config["analysis_period_months"])
    
    config["implementation_months"] = questionary.text(
        "Implementation period (months):",
        default="3",
        validate=lambda x: 0 <= int(x) <= config["analysis_period_months"],
    ).ask()
    config["implementation_months"] = int(config["implementation_months"])
    
    # Risk parameters
    console.print("\n[bold]🎲 Risk Parameters[/bold]")
    
    config["confidence_level"] = questionary.text(
        "Confidence level (0.5-1.0):",
        default="0.95",
        validate=lambda x: 0.5 <= float(x) <= 1.0,
    ).ask()
    config["confidence_level"] = float(config["confidence_level"])
    
    config["risk_free_rate"] = questionary.text(
        "Annual risk-free rate (e.g., 0.03 for 3%):",
        default="0.03",
        validate=lambda x: 0.0 <= float(x) <= 0.2,
    ).ask()
    config["risk_free_rate"] = float(config["risk_free_rate"])
    
    config["discount_rate"] = questionary.text(
        "Discount rate for NPV (e.g., 0.1 for 10%):",
        default="0.1",
        validate=lambda x: 0.0 <= float(x) <= 0.5,
    ).ask()
    config["discount_rate"] = float(config["discount_rate"])
    
    # Sensitivity analysis
    console.print("\n[bold]📈 Sensitivity Analysis[/bold]")
    
    default_vars = ["monthly_savings", "implementation_cost", "monthly_cost"]
    config["sensitivity_variables"] = questionary.checkbox(
        "Variables for sensitivity analysis:",
        choices=[
            "implementation_cost",
            "monthly_cost",
            "monthly_savings",
            "productivity_gains",
            "risk_reduction",
            "revenue_increase",
        ],
        default=default_vars,
    ).ask()
    
    config["sensitivity_range"] = questionary.text(
        "Sensitivity range (+/- fraction, e.g., 0.2 for 20%):",
        default="0.2",
        validate=lambda x: 0.0 <= float(x) <= 1.0,
    ).ask()
    config["sensitivity_range"] = float(config["sensitivity_range"])
    
    return config


def _get_default_roi_params(
    api_client: Any,
    target_id: str,
    target_type: str,
    period: int,
) -> Dict[str, Any]:
    """Get default ROI parameters from API."""
    
    endpoint = f"/{target_type}s/{target_id}/roi/parameters"
    response = api_client.get(endpoint)
    
    params = response.get("parameters", {})
    params["analysis_period_months"] = period
    
    return params


def _calculate_roi(
    api_client: Any,
    target_id: str,
    target_type: str,
    params: ROIParameters,
) -> ROIResult:
    """Calculate ROI using API."""
    
    endpoint = f"/{target_type}s/{target_id}/roi/calculate"
    response = api_client.post(endpoint, json=params.dict())
    
    return ROIResult(**response)


def _display_roi_results(
    result: ROIResult,
    output_format: str,
    target_id: str,
    target_type: str,
):
    """Display ROI results in specified format."""
    
    if output_format == "table":
        _display_roi_table(result, target_id, target_type)
    elif output_format == "detailed":
        _display_detailed_roi(result, target_id, target_type)
    elif output_format == "json":
        console.print(format_json(result.dict()))
    elif output_format == "yaml":
        console.print(yaml.dump(result.dict(), default_flow_style=False))


def _display_roi_table(
    result: ROIResult,
    target_id: str,
    target_type: str,
):
    """Display ROI results in a table."""
    
    # Main metrics table
    metrics_table = Table(title=f"📊 ROI Analysis for {target_type} '{target_id}'", show_lines=True)
    
    metrics_table.add_column("Metric", style="cyan", no_wrap=True)
    metrics_table.add_column("Value", style="green", justify="right")
    metrics_table.add_column("Interpretation", style="white")
    
    metrics_table.add_row(
        "Net Present Value (NPV)",
        f"${result.net_present_value:,.2f}",
        _interpret_npv(result.net_present_value),
    )
    
    metrics_table.add_row(
        "Internal Rate of Return (IRR)",
        f"{result.internal_rate_of_return*100:.1f}%",
        _interpret_irr(result.internal_rate_of_return),
    )
    
    metrics_table.add_row(
        "Payback Period",
        f"{result.payback_period_months:.1f} months",
        _interpret_payback(result.payback_period_months),
    )
    
    metrics_table.add_row(
        "ROI Percentage",
        f"{result.roi_percentage:.1f}%",
        _interpret_roi(result.roi_percentage),
    )
    
    metrics_table.add_row(
        "Break-even Month",
        f"Month {result.break_even_month:.1f}",
        f"Investment recovered by month {result.break_even_month:.0f}",
    )
    
    # Risk metrics table
    risk_table = Table(title="🎲 Risk Metrics", show_header=True, header_style="bold")
    risk_table.add_column("Metric", style="cyan")
    risk_table.add_column("Value", style="yellow", justify="right")
    risk_table.add_column("Confidence", style="dim")
    
    npv_low, npv_high = result.npv_confidence_interval
    risk_table.add_row(
        "NPV 95% CI",
        f"${npv_low:,.0f} - ${npv_high:,.0f}",
        "95% confidence interval",
    )
    
    irr_low, irr_high = result.irr_confidence_interval
    risk_table.add_row(
        "IRR 95% CI",
        f"{irr_low*100:.1f}% - {irr_high*100:.1f}%",
        "95% confidence interval",
    )
    
    risk_table.add_row(
        "Value at Risk (95%)",
        f"${result.value_at_risk_95:,.0f}",
        "Worst-case 5% scenario",
    )
    
    risk_table.add_row(
        "Expected Shortfall",
        f"${result.expected_shortfall:,.0f}",
        "Average loss in worst 5%",
    )
    
    # Display tables
    console.print("\n")
    console.print(metrics_table)
    console.print("\n")
    console.print(risk_table)
    
    # Sensitivity analysis summary
    if result.sensitivity_matrix:
        console.print("\n[bold]📈 Sensitivity Analysis (Top Variables):[/bold]")
        
        sens_table = Table(show_header=True, header_style="bold")
        sens_table.add_column("Variable", style="cyan")
        sens_table.add_column("Δ NPV", style="green", justify="right")
        sens_table.add_column("Δ IRR", style="yellow", justify="right")
        sens_table.add_column("Impact", style="white")
        
        for var, impacts in list(result.sensitivity_matrix.items())[:5]:
            npv_impact = impacts.get("npv", 0)
            irr_impact = impacts.get("irr", 0)
            
            impact_level = "High" if abs(npv_impact) > result.net_present_value * 0.1 else "Medium"
            
            sens_table.add_row(
                var.replace("_", " ").title(),
                f"{npv_impact:+,.0f}",
                f"{irr_impact*100:+.1f}%",
                impact_level,
            )
        
        console.print(sens_table)


def _interpret_npv(npv: float) -> str:
    """Interpret NPV value."""
    if npv > 1000000:
        return "🎯 Excellent investment"
    elif npv > 100000:
        return "✅ Strong positive value"
    elif npv > 0:
        return "👍 Positive return"
    elif npv > -100000:
        return "⚠️  Marginal, consider carefully"
    else:
        return "❌ Negative value - not recommended"


def _interpret_irr(irr: float) -> str:
    """Interpret IRR value."""
    if irr > 0.5:
        return "🎯 Exceptional returns"
    elif irr > 0.25:
        return "✅ Very attractive"
    elif irr > 0.15:
        return "👍 Good investment"
    elif irr > 0.08:
        return "⚠️  Acceptable, depends on risk"
    else:
        return "❌ Below typical hurdle rates"


def _interpret_payback(months: float) -> str:
    """Interpret payback period."""
    if months < 6:
        return "🚀 Very quick return"
    elif months < 12:
        return "✅ Fast payback"
    elif months < 24:
        return "👍 Reasonable timeframe"
    elif months < 36:
        return "⚠️  Long but acceptable"
    else:
        return "❌ Very long payback period"


def _interpret_roi(roi_percent: float) -> str:
    """Interpret ROI percentage."""
    if roi_percent > 500:
        return "🎯 Exceptional ROI"
    elif roi_percent > 200:
        return "✅ Excellent return"
    elif roi_percent > 100:
        return "👍 Good return"
    elif roi_percent > 50:
        return "⚠️  Moderate return"
    else:
        return "❌ Low return on investment"


def _display_detailed_roi(
    result: ROIResult,
    target_id: str,
    target_type: str,
):
    """Display detailed ROI analysis."""
    
    # Display main metrics
    _display_roi_table(result, target_id, target_type)
    
    # Cash flow analysis
    console.print("\n[bold]💰 Cash Flow Analysis[/bold]")
    
    cash_table = Table(show_header=True, header_style="bold")
    cash_table.add_column("Month", style="cyan", justify="right")
    cash_table.add_column("Cash Flow", style="yellow", justify="right")
    cash_table.add_column("Cumulative", style="green", justify="right")
    cash_table.add_column("Discounted", style="blue", justify="right")
    
    months_to_show = min(12, len(result.monthly_cash_flow))
    for month in range(months_to_show):
        cash_flow = result.monthly_cash_flow[month]
        cumulative = result.cumulative_cash_flow[month]
        discounted = result.discounted_cash_flow[month]
        
        # Highlight break-even
        style = "bold" if month >= result.break_even_month and cumulative >= 0 else ""
        
        cash_table.add_row(
            f"{month+1}",
            f"{cash_flow:+,.0f}",
            f"{cumulative:+,.0f}",
            f"{discounted:+,.0f}",
            style=style,
        )
    
    if len(result.monthly_cash_flow) > months_to_show:
        cash_table.add_row("...", "...", "...", "...", style="dim")
        
        # Show final values
        final_month = len(result.monthly_cash_flow) - 1
        cash_table.add_row(
            f"{final_month+1}",
            f"{result.monthly_cash_flow[final_month]:+,.0f}",
            f"{result.cumulative_cash_flow[final_month]:+,.0f}",
            f"{result.discounted_cash_flow[final_month]:+,.0f}",
        )
    
    console.print(cash_table)
    
    # Comparative analysis
    if result.vs_industry_benchmark or result.vs_alternative_solutions:
        console.print("\n[bold]📊 Comparative Analysis[/bold]")
        
        comp_table = Table(show_header=True, header_style="bold")
        comp_table.add_column("Comparison", style="cyan")
        comp_table.add_column("Ratio", style="green", justify="right")
        comp_table.add_column("Interpretation", style="white")
        
        if result.vs_industry_benchmark:
            ratio = result.vs_industry_benchmark
            if ratio > 1.5:
                interpretation = "🎯 Significantly better than industry"
            elif ratio > 1.2:
                interpretation = "✅ Better than industry average"
            elif ratio > 0.8:
                interpretation = "👍 On par with industry"
            else:
                interpretation = "⚠️  Below industry average"
            
            comp_table.add_row(
                "vs Industry Benchmark",
                f"{ratio:.2f}x",
                interpretation,
            )
        
        if result.vs_alternative_solutions:
            for solution, ratio in result.vs_alternative_solutions.items():
                comp_table.add_row(
                    f"vs {solution.replace('_', ' ').title()}",
                    f"{ratio:.2f}x",
                    "Better" if ratio > 1 else "Worse",
                )
        
        console.print(comp_table)


def _save_roi_results(
    result: ROIResult,
    save_path: str,
    output_format: str,
):
    """Save ROI results to file."""
    
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    if output_format == "json":
        with open(path, "w") as f:
            json.dump(result.dict(), f, indent=2, default=str)
    elif output_format == "yaml":
        with open(path, "w") as f:
            yaml.dump(result.dict(), f, default_flow_style=False)
    else:
        # Save as text report
        _generate_text_report(result, path)
    
    console.print(f"[green]✅ Results saved to {path}[/green]")


def _generate_text_report(result: ROIResult, path: Path):
    """Generate text report of ROI results."""
    
    with open(path, "w") as f:
        f.write("=" * 60 + "\n")
        f.write("ROI ANALYSIS REPORT\n")
        f.write("=" * 60 + "\n\n")
        
        f.write("SUMMARY METRICS\n")
        f.write("-" * 40 + "\n")
        f.write(f"Net Present Value (NPV): ${result.net_present_value:,.2f}\n")
        f.write(f"Internal Rate of Return (IRR): {result.internal_rate_of_return*100:.1f}%\n")
        f.write(f"Payback Period: {result.payback_period_months:.1f} months\n")
        f.write(f"ROI Percentage: {result.roi_percentage:.1f}%\n")
        f.write(f"Break-even Month: {result.break_even_month:.1f}\n\n")
        
        f.write("RISK METRICS\n")
        f.write("-" * 40 + "\n")
        npv_low, npv_high = result.npv_confidence_interval
        f.write(f"NPV 95% Confidence Interval: ${npv_low:,.0f} - ${npv_high:,.0f}\n")
        irr_low, irr_high = result.irr_confidence_interval
        f.write(f"IRR 95% Confidence Interval: {irr_low*100:.1f}% - {irr_high*100:.1f}%\n")
        f.write(f"Value at Risk (95%): ${result.value_at_risk_95:,.0f}\n")
        f.write(f"Expected Shortfall: ${result.expected_shortfall:,.0f}\n\n")
        
        f.write("CASH FLOW ANALYSIS (First 12 months)\n")
        f.write("-" * 40 + "\n")
        f.write(f"{'Month':>6} {'Cash Flow':>12} {'Cumulative':>12} {'Discounted':>12}\n")
        
        for month in range(min(12, len(result.monthly_cash_flow))):
            cf = result.monthly_cash_flow[month]
            cum = result.cumulative_cash_flow[month]
            disc = result.discounted_cash_flow[month]
            
            f.write(f"{month+1:6d} {cf:12,.0f} {cum:12,.0f} {disc:12,.0f}\n")
        
        f.write("\nGenerated on: " + datetime.now().isoformat() + "\n")


@roi_group.command(name="compare")
@click.argument("targets", nargs=-1)
@click.option(
    "--suite",
    "-s",
    multiple=True,
    help="Suite IDs to compare",
)
@click.option(
    "--agent",
    "-a",
    multiple=True,
    help="Agent IDs to compare",
)
@click.option(
    "--baseline",
    "-b",
    type=str,
    help="Baseline for comparison",
)
@click.option(
    "--metric",
    "-m",
    type=click.Choice(["npv", "irr", "roi", "payback", "all"]),
    default="all",
    help="Metric to compare",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "chart", "json"]),
    default="table",
    help="Output format",
)
@click.option(
    "--save-chart",
    type=click.Path(),
    help="Save chart to file",
)
def compare_roi(
    targets: List[str],
    suite: List[str],
    agent: List[str],
    baseline: Optional[str],
    metric: str,
    output: str,
    save_chart: Optional[str],
):
    """Compare ROI between different options."""
    
    try:
        api_client = get_api_client()
        
        # Collect all targets
        all_targets = list(targets) + list(suite) + list(agent)
        
        if len(all_targets) < 2:
            console.print("[red]Please provide at least 2 targets to compare[/red]")
            return
        
        # Calculate ROI for each target
        results = {}
        with Progress() as progress:
            task = progress.add_task(
                "Calculating ROI for comparison...",
                total=len(all_targets),
            )
            
            for target in all_targets:
                progress.update(task, description=f"Calculating ROI for {target}...")
                
                # Get default parameters
                target_id, target_type = _resolve_target(target, None, None, None)
                params = _get_default_roi_params(api_client, target_id, target_type, 36)
                
                # Calculate ROI
                validated_params = ROIParameters(**params)
                result = _calculate_roi(api_client, target_id, target_type, validated_params)
                
                results[target] = result
                progress.update(task, advance=1)
        
        # Display comparison
        if output == "table":
            _display_comparison_table(results, baseline, metric)
        elif output == "chart":
            _display_comparison_chart(results, metric, save_chart)
        elif output == "json":
            comparison_data = {
                target: {
                    "npv": result.net_present_value,
                    "irr": result.internal_rate_of_return,
                    "roi": result.roi_percentage,
                    "payback": result.payback_period_months,
                }
                for target, result in results.items()
            }
            console.print(format_json(comparison_data))
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _display_comparison_table(
    results: Dict[str, ROIResult],
    baseline: Optional[str],
    metric: str,
):
    """Display ROI comparison table."""
    
    # Determine baseline
    if baseline and baseline in results:
        baseline_result = results[baseline]
    else:
        # Use first result as baseline
        baseline = list(results.keys())[0]
        baseline_result = results[baseline]
    
    # Create comparison table
    table = Table(title="📊 ROI Comparison Analysis", show_lines=True)
    
    table.add_column("Target", style="cyan", no_wrap=True)
    table.add_column("NPV", style="green", justify="right")
    table.add_column("IRR", style="yellow", justify="right")
    table.add_column("ROI %", style="blue", justify="right")
    table.add_column("Payback (mo)", style="magenta", justify="right")
    table.add_column("vs Baseline", style="white", justify="right")
    
    for target, result in results.items():
        # Calculate vs baseline
        if target == baseline:
            vs_baseline = "BASELINE"
        else:
            npv_diff = result.net_present_value - baseline_result.net_present_value
            npv_pct = (npv_diff / abs(baseline_result.net_present_value)) * 100
            
            if npv_pct > 0:
                vs_baseline = f"[green]+{npv_pct:.1f}%[/green]"
            else:
                vs_baseline = f"[red]{npv_pct:.1f}%[/red]"
        
        # Highlight best in each category
        best_npv = max(r.net_present_value for r in results.values())
        best_irr = max(r.internal_rate_of_return for r in results.values())
        best_roi = max(r.roi_percentage for r in results.values())
        best_payback = min(r.payback_period_months for r in results.values())
        
        npv_style = "bold green" if result.net_present_value == best_npv else "green"
        irr_style = "bold yellow" if result.internal_rate_of_return == best_irr else "yellow"
        roi_style = "bold blue" if result.roi_percentage == best_roi else "blue"
        payback_style = "bold magenta" if result.payback_period_months == best_payback else "magenta"
        
        table.add_row(
            f"[cyan]{target}[/cyan]" + (" [dim](baseline)[/dim]" if target == baseline else ""),
            f"[{npv_style}]${result.net_present_value:,.0f}[/{npv_style}]",
            f"[{irr_style}]{result.internal_rate_of_return*100:.1f}%[/{irr_style}]",
            f"[{roi_style}]{result.roi_percentage:.1f}%[/{roi_style}]",
            f"[{payback_style}]{result.payback_period_months:.1f}[/{payback_style}]",
            vs_baseline,
        )
    
    console.print(table)
    
    # Summary
    console.print("\n[bold]🎯 Recommendation Summary:[/bold]")
    
    best_target = max(results.items(), key=lambda x: x[1].net_present_value)[0]
    console.print(f"  • Best NPV: [bold green]{best_target}[/bold green] (${results[best_target].net_present_value:,.0f})")
    
    best_irr_target = max(results.items(), key=lambda x: x[1].internal_rate_of_return)[0]
    console.print(f"  • Best IRR: [bold yellow]{best_irr_target}[/bold yellow] ({results[best_irr_target].internal_rate_of_return*100:.1f}%)")
    
    best_payback_target = min(results.items(), key=lambda x: x[1].payback_period_months)[0]
    console.print(f"  • Fastest Payback: [bold magenta]{best_payback_target}[/bold magenta] ({results[best_payback_target].payback_period_months:.1f} months)")


def _display_comparison_chart(
    results: Dict[str, ROIResult],
    metric: str,
    save_path: Optional[str],
):
    """Display ROI comparison chart."""
    
    try:
        import matplotlib.pyplot as plt
        import numpy as np
        
        targets = list(results.keys())
        
        if metric == "all":
            # Create subplot grid
            fig, axes = plt.subplots(2, 2, figsize=(12, 8))
            fig.suptitle("ROI Metrics Comparison", fontsize=16, fontweight='bold')
            
            # NPV comparison
            npv_values = [r.net_present_value for r in results.values()]
            axes[0, 0].bar(targets, npv_values, color='lightgreen', edgecolor='darkgreen')
            axes[0, 0].set_title('Net Present Value (NPV)')
            axes[0, 0].set_ylabel('USD')
            axes[0, 0].tick_params(axis='x', rotation=45)
            
            # IRR comparison
            irr_values = [r.internal_rate_of_return * 100 for r in results.values()]
            axes[0, 1].bar(targets, irr_values, color='lightblue', edgecolor='darkblue')
            axes[0, 1].set_title('Internal Rate of Return (IRR)')
            axes[0, 1].set_ylabel('Percent (%)')
            axes[0, 1].tick_params(axis='x', rotation=45)
            
            # ROI comparison
            roi_values = [r.roi_percentage for r in results.values()]
            axes[1, 0].bar(targets, roi_values, color='lightcoral', edgecolor='darkred')
            axes[1, 0].set_title('ROI Percentage')
            axes[1, 0].set_ylabel('Percent (%)')
            axes[1, 0].tick_params(axis='x', rotation=45)
            
            # Payback comparison
            payback_values = [r.payback_period_months for r in results.values()]
            axes[1, 1].bar(targets, payback_values, color='lightyellow', edgecolor='goldenrod')
            axes[1, 1].set_title('Payback Period')
            axes[1, 1].set_ylabel('Months')
            axes[1, 1].tick_params(axis='x', rotation=45)
            
        else:
            # Single metric chart
            fig, ax = plt.subplots(figsize=(10, 6))
            
            metric_titles = {
                "npv": "Net Present Value (NPV) Comparison",
                "irr": "Internal Rate of Return (IRR) Comparison",
                "roi": "ROI Percentage Comparison",
                "payback": "Payback Period Comparison",
            }
            
            metric_data = {
                "npv": [r.net_present_value for r in results.values()],
                "irr": [r.internal_rate_of_return * 100 for r in results.values()],
                "roi": [r.roi_percentage for r in results.values()],
                "payback": [r.payback_period_months for r in results.values()],
            }
            
            colors = {
                "npv": "lightgreen",
                "irr": "lightblue",
                "roi": "lightcoral",
                "payback": "lightyellow",
            }
            
            ax.bar(targets, metric_data[metric], color=colors[metric], edgecolor='black')
            ax.set_title(metric_titles[metric], fontsize=14, fontweight='bold')
            ax.set_xlabel('Targets')
            ax.set_ylabel('USD' if metric == 'npv' else 'Percent (%)' if metric in ['irr', 'roi'] else 'Months')
            ax.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            console.print(f"[green]✅ Chart saved to {save_path}[/green]")
        else:
            plt.show()
            
    except ImportError:
        console.print("[yellow]⚠️  matplotlib not installed. Install with: pip install matplotlib[/yellow]")
    except Exception as e:
        console.print(f"[red]Error generating chart: {str(e)}[/red]")


@roi_group.command(name="optimize")
@click.argument("target")
@click.option(
    "--parameters",
    "-p",
    multiple=True,
    help="Parameters to optimize (e.g., monthly_savings)",
)
@click.option(
    "--constraints",
    "-c",
    type=str,
    help="Constraints file (JSON/YAML)",
)
@click.option(
    "--iterations",
    "-i",
    type=int,
    default=1000,
    help="Number of optimization iterations",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "json", "config"]),
    default="table",
    help="Output format",
)
def optimize_roi(
    target: str,
    parameters: List[str],
    constraints: Optional[str],
    iterations: int,
    output: str,
):
    """Find ROI-maximizing configuration."""
    
    try:
        api_client = get_api_client()
        
        target_id, target_type = _resolve_target(target, None, None, None)
        
        # Get current configuration
        with spinner("Analyzing current configuration..."):
            current_params = _get_default_roi_params(api_client, target_id, target_type, 36)
            current_result = _calculate_roi(
                api_client, 
                target_id, 
                target_type, 
                ROIParameters(**current_params)
            )
        
        # Determine parameters to optimize
        if not parameters:
            # Use all numeric parameters
            parameters = [
                "monthly_savings",
                "productivity_gains",
                "risk_reduction",
                "revenue_increase",
                "implementation_cost",
                "monthly_cost",
            ]
        
        # Load constraints
        constraints_dict = {}
        if constraints:
            path = Path(constraints)
            if path.suffix.lower() in [".yaml", ".yml"]:
                with open(path) as f:
                    constraints_dict = yaml.safe_load(f)
            else:
                with open(path) as f:
                    constraints_dict = json.load(f)
        
        # Run optimization
        with spinner(f"Running optimization ({iterations} iterations)..."):
            optimization_result = _run_roi_optimization(
                api_client,
                target_id,
                target_type,
                parameters,
                constraints_dict,
                iterations,
            )
        
        # Display results
        _display_optimization_results(
            optimization_result,
            current_result,
            parameters,
            output,
        )
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _run_roi_optimization(
    api_client: Any,
    target_id: str,
    target_type: str,
    parameters: List[str],
    constraints: Dict[str, Any],
    iterations: int,
) -> Dict[str, Any]:
    """Run ROI optimization using Monte Carlo simulation."""
    
    # This is a simplified optimization - in production, would use proper optimization algorithms
    best_config = None
    best_npv = float('-inf')
    
    # Parameter ranges (simplified - would come from constraints)
    param_ranges = {
        "monthly_savings": (1000, 50000),
        "productivity_gains": (500, 25000),
        "risk_reduction": (100, 10000),
        "revenue_increase": (1000, 50000),
        "implementation_cost": (1000, 100000),
        "monthly_cost": (100, 10000),
    }
    
    results = []
    
    with Progress() as progress:
        task = progress.add_task(
            "Optimizing ROI...",
            total=iterations,
        )
        
        for i in range(iterations):
            # Generate random configuration within constraints
            config = {}
            for param in parameters:
                if param in param_ranges:
                    min_val, max_val = param_ranges[param]
                    
                    # Apply constraints if specified
                    if param in constraints:
                        const = constraints[param]
                        if "min" in const:
                            min_val = max(min_val, const["min"])
                        if "max" in const:
                            max_val = min(max_val, const["max"])
                    
                    # Generate random value
                    config[param] = np.random.uniform(min_val, max_val)
            
            # Calculate ROI with this configuration
            try:
                # Start with default parameters
                base_params = _get_default_roi_params(api_client, target_id, target_type, 36)
                base_params.update(config)
                
                roi_params = ROIParameters(**base_params)
                result = _calculate_roi(api_client, target_id, target_type, roi_params)
                
                results.append({
                    "config": config,
                    "npv": result.net_present_value,
                    "irr": result.internal_rate_of_return,
                    "roi": result.roi_percentage,
                })
                
                # Track best configuration
                if result.net_present_value > best_npv:
                    best_npv = result.net_present_value
                    best_config = config
                    best_result = result
                
            except Exception as e:
                # Skip invalid configurations
                continue
            
            progress.update(task, advance=1)
    
    return {
        "best_configuration": best_config,
        "best_result": best_result,
        "all_results": results[:100],  # Limit to first 100 for display
        "improvement_percent": ((best_npv - _get_default_roi_params(
            api_client, target_id, target_type, 36
        ).get("net_present_value", 0)) / abs(best_npv)) * 100 if best_npv != 0 else 0,
    }


def _display_optimization_results(
    optimization_result: Dict[str, Any],
    current_result: ROIResult,
    parameters: List[str],
    output_format: str,
):
    """Display optimization results."""
    
    best_config = optimization_result.get("best_configuration", {})
    best_result = optimization_result.get("best_result")
    improvement = optimization_result.get("improvement_percent", 0)
    
    if output_format == "table":
        # Improvement summary
        console.print("\n[bold]🎯 ROI Optimization Results[/bold]")
        console.print("=" * 60)
        
        summary = Table(show_header=False)
        summary.add_column("Metric", style="cyan")
        summary.add_column("Current", style="yellow", justify="right")
        summary.add_column("Optimized", style="green", justify="right")
        summary.add_column("Improvement", style="bold", justify="right")
        
        summary.add_row(
            "Net Present Value (NPV)",
            f"${current_result.net_present_value:,.0f}",
            f"${best_result.net_present_value:,.0f}",
            f"[green]+{improvement:.1f}%[/green]" if improvement > 0 else f"[red]{improvement:.1f}%[/red]",
        )
        
        summary.add_row(
            "Internal Rate of Return (IRR)",
            f"{current_result.internal_rate_of_return*100:.1f}%",
            f"{best_result.internal_rate_of_return*100:.1f}%",
            f"[green]+{(best_result.internal_rate_of_return - current_result.internal_rate_of_return)*100:.1f}%[/green]",
        )
        
        summary.add_row(
            "ROI Percentage",
            f"{current_result.roi_percentage:.1f}%",
            f"{best_result.roi_percentage:.1f}%",
            f"[green]+{(best_result.roi_percentage - current_result.roi_percentage):.1f}%[/green]",
        )
        
        console.print(summary)
        
        # Optimal configuration
        console.print("\n[bold]⚙️  Optimal Configuration:[/bold]")
        
        config_table = Table(show_header=True, header_style="bold")
        config_table.add_column("Parameter", style="cyan")
        config_table.add_column("Optimal Value", style="green", justify="right")
        config_table.add_column("Unit", style="dim")
        
        for param in parameters:
            if param in best_config:
                value = best_config[param]
                unit = "$" if "cost" in param or "savings" in param or "revenue" in param else ""
                config_table.add_row(
                    param.replace("_", " ").title(),
                    f"{unit}{value:,.0f}" if unit else f"{value:,.0f}",
                    "USD" if unit else "units",
                )
        
        console.print(config_table)
        
        # Recommendations
        console.print("\n[bold]💡 Recommendations:[/bold]")
        
        for param, value in best_config.items():
            current_value = getattr(current_result, param, None)
            if current_value and value > current_value * 1.1:
                console.print(f"  • Increase {param.replace('_', ' ')} by {(value/current_value - 1)*100:.0f}%")
            elif current_value and value < current_value * 0.9:
                console.print(f"  • Reduce {param.replace('_', ' ')} by {(1 - value/current_value)*100:.0f}%")
    
    elif output_format == "json":
        output_data = {
            "optimal_configuration": best_config,
            "optimal_results": {
                "npv": best_result.net_present_value,
                "irr": best_result.internal_rate_of_return,
                "roi": best_result.roi_percentage,
                "payback": best_result.payback_period_months,
            },
            "improvement_percent": improvement,
        }
        console.print(format_json(output_data))
    
    elif output_format == "config":
        # Output as configuration file
        config_output = {
            "roi_optimization": {
                "optimal_configuration": best_config,
                "estimated_improvement_percent": improvement,
                "generated_at": datetime.now().isoformat(),
            }
        }
        console.print(yaml.dump(config_output, default_flow_style=False))


@roi_group.command(name="forecast")
@click.argument("target")
@click.option(
    "--periods",
    "-p",
    type=int,
    default=24,
    help="Forecast periods (months)",
)
@click.option(
    "--confidence",
    "-c",
    type=float,
    default=0.95,
    help="Confidence level",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["chart", "table", "json"]),
    default="chart",
    help="Output format",
)
@click.option(
    "--save",
    type=click.Path(),
    help="Save forecast to file",
)
def forecast_roi(
    target: str,
    periods: int,
    confidence: float,
    output: str,
    save: Optional[str],
):
    """Forecast future ROI based on trends."""
    
    console.print(f"[yellow]ROI forecast for {target} ({periods} months)[/yellow]")
    # Implementation would generate time-series forecast


@roi_group.command(name="report")
@click.argument("target")
@click.option(
    "--format",
    "report_format",
    type=click.Choice(["pdf", "html", "ppt", "excel"]),
    default="pdf",
    help="Report format",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    required=True,
    help="Output file path",
)
@click.option(
    "--template",
    "-t",
    type=click.Path(exists=True),
    help="Custom report template",
)
def generate_roi_report(
    target: str,
    report_format: str,
    output: str,
    template: Optional[str],
):
    """Generate detailed ROI reports."""
    
    console.print(f"[yellow]Generating {report_format} ROI report for {target}[/yellow]")
    # Implementation would generate comprehensive report


@roi_group.command(name="benchmark")
@click.argument("target")
@click.option(
    "--industry",
    "-i",
    type=click.Choice(["saas", "fintech", "healthcare", "manufacturing", "all"]),
    default="saas",
    help="Industry benchmark",
)
@click.option(
    "--size",
    "-s",
    type=click.Choice(["startup", "sme", "enterprise"]),
    help="Company size",
)
@click.option(
    "--region",
    "-r",
    type=click.Choice(["na", "eu", "apac", "global"]),
    default="global",
    help="Geographic region",
)
def benchmark_roi(
    target: str,
    industry: str,
    size: Optional[str],
    region: str,
):
    """Compare ROI vs industry benchmarks."""
    
    console.print(f"[yellow]Benchmarking {target} vs {industry} industry[/yellow]")
    # Implementation would compare against benchmarks


@roi_group.command(name="simulate")
@click.argument("target")
@click.option(
    "--scenarios",
    "-s",
    type=int,
    default=1000,
    help="Number of Monte Carlo simulations",
)
@click.option(
    "--what-if",
    "-w",
    multiple=True,
    help="What-if scenario (e.g., 'monthly_savings+20%')",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["distribution", "sensitivity", "all"]),
    default="all",
    help="Output analysis",
)
def simulate_roi(
    target: str,
    scenarios: int,
    what_if: List[str],
    output: str,
):
    """Run Monte Carlo simulations for ROI."""
    
    console.print(f"[yellow]Running {scenarios} Monte Carlo simulations for {target}[/yellow]")
    # Implementation would run Monte Carlo simulations


@roi_group.command(name="validate")
@click.argument("roi_result_file", type=click.Path(exists=True))
@click.option(
    "--method",
    "-m",
    type=click.Choice(["cross-check", "sensitivity", "backtest", "all"]),
    default="all",
    help="Validation method",
)
def validate_roi(
    roi_result_file: str,
    method: str,
):
    """Validate ROI calculations."""
    
    console.print(f"[yellow]Validating ROI results in {roi_result_file}[/yellow]")
    # Implementation would validate ROI calculations


@roi_group.command(name="export")
@click.argument("target")
@click.option(
    "--format",
    "export_format",
    type=click.Choice(["csv", "excel", "json", "sql"]),
    default="csv",
    help="Export format",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    required=True,
    help="Output file path",
)
@click.option(
    "--include-raw",
    is_flag=True,
    help="Include raw simulation data",
)
def export_roi(
    target: str,
    export_format: str,
    output: str,
    include_raw: bool,
):
    """Export ROI data for analysis."""
    
    console.print(f"[yellow]Exporting ROI data for {target} in {export_format} format[/yellow]")
    # Implementation would export ROI data


@roi_group.command(name="dashboard")
@click.argument("target", required=False)
@click.option(
    "--port",
    "-p",
    type=int,
    default=8050,
    help="Dashboard port",
)
@click.option(
    "--live",
    "-l",
    is_flag=True,
    help="Live updating dashboard",
)
def launch_dashboard(
    target: Optional[str],
    port: int,
    live: bool,
):
    """Launch interactive ROI dashboard."""
    
    console.print(f"[yellow]Launching ROI dashboard on port {port}[/yellow]")
    # Implementation would launch interactive dashboard


# Export commands
__all__ = ["roi_group"]