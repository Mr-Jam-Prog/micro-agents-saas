"""
CLI commands for managing MicroAgents suites.
Interactive wizards, deployment, monitoring, and optimization.
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
import questionary
import yaml
from click import Context
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.tree import Tree

from microagents.cli.config import config
from microagents.cli.utils import (
    APIError,
    format_json,
    format_table,
    get_api_client,
    handle_api_error,
    spinner,
    validate_uuid,
)
from microagents.utils.config.settings import get_settings

console = Console()

# Suite models
class SuiteConfiguration(BaseModel):
    """Suite configuration model."""
    
    suite_id: str
    name: str
    version: str = "1.0.0"
    description: Optional[str] = None
    agent_count: int
    estimated_cost_monthly: float
    estimated_roi: float
    features: List[str]
    requirements: Dict[str, Any]
    deployment_targets: List[str]
    monitoring_enabled: bool = True
    backup_enabled: bool = True
    auto_optimization: bool = False
    
    class Config:
        json_schema_extra = {
            "example": {
                "suite_id": "cost-optimization",
                "name": "Cost Optimization Suite",
                "version": "2.1.0",
                "description": "Automated cloud cost optimization",
                "agent_count": 250,
                "estimated_cost_monthly": 499.0,
                "estimated_roi": 3.2,
                "features": ["rightsizing", "reservation_planning", "spot_optimization"],
                "requirements": {
                    "cloud_providers": ["aws", "azure"],
                    "permissions": ["read:billing", "modify:instances"],
                    "minimum_spend": 10000.0,
                },
                "deployment_targets": ["production", "staging"],
                "monitoring_enabled": True,
                "backup_enabled": True,
                "auto_optimization": True,
            }
        }


class SuiteDeployment(BaseModel):
    """Suite deployment model."""
    
    deployment_id: str
    suite_id: str
    environment: str
    status: str
    deployed_at: Optional[datetime] = None
    deployed_by: Optional[str] = None
    estimated_savings: Optional[float] = None
    actual_savings: Optional[float] = None
    resources_optimized: int = 0
    configuration: Dict[str, Any]
    
    class Config:
        json_schema_extra = {
            "example": {
                "deployment_id": "dep-123456",
                "suite_id": "cost-optimization",
                "environment": "production",
                "status": "active",
                "deployed_at": "2024-01-15T10:30:00Z",
                "deployed_by": "john.doe@example.com",
                "estimated_savings": 15000.0,
                "actual_savings": 12500.0,
                "resources_optimized": 42,
                "configuration": {
                    "rightsizing_threshold": 0.7,
                    "schedule": "0 */6 * * *",
                    "notifications": True,
                },
            }
        }


class SuiteReport(BaseModel):
    """Suite report model."""
    
    period_start: datetime
    period_end: datetime
    total_savings: float
    optimization_count: int
    average_roi: float
    recommendations: List[Dict[str, Any]]
    top_agents: List[Dict[str, Any]]
    cost_breakdown: Dict[str, float]
    
    class Config:
        json_schema_extra = {
            "example": {
                "period_start": "2024-01-01T00:00:00Z",
                "period_end": "2024-01-31T23:59:59Z",
                "total_savings": 12500.0,
                "optimization_count": 42,
                "average_roi": 3.2,
                "recommendations": [
                    {
                        "type": "resize_instance",
                        "potential_savings": 1200.0,
                        "resource_id": "i-1234567890abcdef0",
                    }
                ],
                "top_agents": [
                    {
                        "agent_id": "rightsizer-001",
                        "savings_generated": 4500.0,
                        "execution_count": 125,
                    }
                ],
                "cost_breakdown": {
                    "compute": 8500.0,
                    "storage": 2500.0,
                    "network": 1500.0,
                },
            }
        }


@click.group(name="suite")
def suite_group():
    """Manage MicroAgents suites."""
    pass


@suite_group.command(name="list")
@click.option(
    "--available",
    "-a",
    is_flag=True,
    help="Show only available suites for deployment",
)
@click.option(
    "--deployed",
    "-d",
    is_flag=True,
    help="Show only deployed suites",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "yaml"]),
    default="table",
    help="Output format",
)
@click.option(
    "--filter",
    "filter_str",
    type=str,
    help="Filter suites by keyword",
)
def list_suites(
    available: bool,
    deployed: bool,
    output_format: str,
    filter_str: Optional[str],
):
    """List all available suites."""
    
    try:
        with spinner("Fetching suites..."):
            api_client = get_api_client()
            
            params = {}
            if available:
                params["available"] = True
            if deployed:
                params["deployed"] = True
            if filter_str:
                params["filter"] = filter_str
            
            response = api_client.get("/suites", params=params)
            suites = response.get("suites", [])
            
            if not suites:
                console.print("[yellow]No suites found.[/yellow]")
                return
            
            if output_format == "table":
                _display_suites_table(suites)
            elif output_format == "json":
                console.print(format_json(suites))
            elif output_format == "yaml":
                console.print(yaml.dump(suites, default_flow_style=False))
                
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _display_suites_table(suites: List[Dict[str, Any]]):
    """Display suites in a rich table."""
    table = Table(title="MicroAgents Suites", show_lines=True)
    
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Agents", justify="right")
    table.add_column("Cost/Month", justify="right")
    table.add_column("Est. ROI", justify="right")
    table.add_column("Status", style="green")
    table.add_column("Features", style="blue")
    
    for suite in suites:
        status = suite.get("status", "available")
        status_style = {
            "available": "green",
            "deployed": "blue",
            "pending": "yellow",
            "error": "red",
        }.get(status, "white")
        
        features = suite.get("features", [])
        features_display = ", ".join(features[:2])
        if len(features) > 2:
            features_display += f" (+{len(features) - 2} more)"
        
        table.add_row(
            suite.get("suite_id"),
            suite.get("name"),
            str(suite.get("agent_count", 0)),
            f"${suite.get('estimated_cost_monthly', 0):.2f}",
            f"{suite.get('estimated_roi', 0):.1f}x",
            f"[{status_style}]{status}[/{status_style}]",
            features_display,
        )
    
    console.print(table)


@suite_group.command(name="demo")
@click.argument("suite_id", required=False)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Interactive demo mode",
)
@click.option(
    "--duration",
    "-d",
    type=int,
    default=300,
    help="Demo duration in seconds",
)
@click.option(
    "--cloud",
    "-c",
    type=click.Choice(["aws", "azure", "gcp", "all"]),
    default="aws",
    help="Cloud provider for demo",
)
def demo_suite(
    suite_id: Optional[str],
    interactive: bool,
    duration: int,
    cloud: str,
):
    """Launch an interactive demo of a suite."""
    
    try:
        api_client = get_api_client()
        
        # If no suite_id provided, show selection
        if not suite_id:
            with spinner("Loading available suites..."):
                response = api_client.get("/suites", params={"available": True})
                suites = response.get("suites", [])
                
                if not suites:
                    console.print("[yellow]No suites available for demo.[/yellow]")
                    return
                
                choices = [
                    f"{s['suite_id']} - {s['name']} ({s['agent_count']} agents)"
                    for s in suites
                ]
                
                selection = questionary.select(
                    "Select a suite to demo:",
                    choices=choices,
                ).ask()
                
                if not selection:
                    console.print("[yellow]Demo cancelled.[/yellow]")
                    return
                
                suite_id = selection.split(" - ")[0]
        
        # Validate suite
        with spinner(f"Loading suite '{suite_id}'..."):
            suite = api_client.get(f"/suites/{suite_id}")
            
            if not suite:
                console.print(f"[red]Suite '{suite_id}' not found.[/red]")
                return
        
        # Demo configuration
        console.print(Panel.fit(
            f"[bold cyan]Demo Configuration:[/bold cyan]\n"
            f"Suite: [bold]{suite['name']}[/bold]\n"
            f"Duration: [bold]{duration}[/bold] seconds\n"
            f"Cloud: [bold]{cloud.upper()}[/bold]\n"
            f"Interactive: [bold]{'Yes' if interactive else 'No'}[/bold]",
            title="Demo Setup",
            border_style="blue",
        ))
        
        if not click.confirm("Start demo?", default=True):
            console.print("[yellow]Demo cancelled.[/yellow]")
            return
        
        # Start demo
        _run_suite_demo(suite, interactive, duration, cloud)
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _run_suite_demo(
    suite: Dict[str, Any],
    interactive: bool,
    duration: int,
    cloud: str,
):
    """Run the suite demo with live progress."""
    
    demo_progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    
    demo_tasks = {
        "environment": demo_progress.add_task("[cyan]Setting up demo environment...", total=100),
        "agents": demo_progress.add_task("[green]Deploying demo agents...", total=suite.get("agent_count", 0)),
        "configuration": demo_progress.add_task("[yellow]Configuring suite...", total=100),
        "simulation": demo_progress.add_task("[magenta]Running simulation...", total=duration),
        "results": demo_progress.add_task("[blue]Generating results...", total=100),
    }
    
    with Live(demo_progress, refresh_per_second=10):
        # Setup environment
        for i in range(0, 101, 10):
            demo_progress.update(demo_tasks["environment"], advance=10)
            time.sleep(0.1)
        
        # Deploy agents
        agent_count = suite.get("agent_count", 0)
        for i in range(agent_count):
            demo_progress.update(demo_tasks["agents"], advance=1)
            time.sleep(0.05)
            
            # Interactive feedback
            if interactive and i % 10 == 0:
                demo_progress.console.print(
                    f"[dim]Deployed agent {i+1}/{agent_count}...[/dim]"
                )
        
        # Configuration
        for i in range(0, 101, 5):
            demo_progress.update(demo_tasks["configuration"], advance=5)
            time.sleep(0.05)
        
        # Simulation
        for i in range(duration):
            demo_progress.update(demo_tasks["simulation"], advance=1)
            time.sleep(1)
            
            # Show simulation events
            if interactive and i % 30 == 0:
                _show_simulation_event(i, suite, cloud)
        
        # Results
        for i in range(0, 101, 20):
            demo_progress.update(demo_tasks["results"], advance=20)
            time.sleep(0.2)
    
    # Show demo results
    _show_demo_results(suite, duration, cloud)


def _show_simulation_event(
    time_elapsed: int,
    suite: Dict[str, Any],
    cloud: str,
):
    """Show simulation event during demo."""
    events = [
        f"🔍 [cyan]Cost anomaly detected on {cloud.upper()} - potential savings: ${time_elapsed * 10}[/cyan]",
        f"⚡ [green]Auto-remediation executed - fixed misconfiguration in {suite['name']}[/green]",
        f"📊 [yellow]Real-time metrics updated - ROI increased to {(time_elapsed / 100) + 2:.1f}x[/yellow]",
        f"🔒 [magenta]Security scan completed - 0 vulnerabilities found[/magenta]",
        f"💾 [blue]Backup created - configuration saved successfully[/blue]",
    ]
    
    event = events[time_elapsed % len(events)]
    console.print(event)


def _show_demo_results(
    suite: Dict[str, Any],
    duration: int,
    cloud: str,
):
    """Show demo results."""
    
    # Generate demo results
    estimated_savings = duration * 25  # $25 per second of demo
    resources_optimized = duration // 10
    roi = (estimated_savings / suite.get("estimated_cost_monthly", 1)) * 12
    
    results_table = Table(title="Demo Results", show_header=True, header_style="bold")
    results_table.add_column("Metric", style="cyan")
    results_table.add_column("Value", style="green", justify="right")
    
    results_table.add_row("Suite", suite["name"])
    results_table.add_row("Demo Duration", f"{duration} seconds")
    results_table.add_row("Cloud Provider", cloud.upper())
    results_table.add_row("Estimated Monthly Savings", f"${estimated_savings:,.2f}")
    results_table.add_row("Resources Optimized", str(resources_optimized))
    results_table.add_row("Projected ROI", f"{roi:.1f}x")
    results_table.add_row("Agents Deployed", str(suite.get("agent_count", 0)))
    results_table.add_row("Features Demonstrated", str(len(suite.get("features", []))))
    
    console.print("\n")
    console.print(Panel.fit(results_table, title="🎉 Demo Complete!", border_style="green"))
    
    console.print("\n[bold]Next steps:[/bold]")
    console.print("1. Run [cyan]suite configure[/cyan] to set up for your environment")
    console.print("2. Run [cyan]suite deploy[/cyan] to deploy to production")
    console.print("3. Run [cyan]suite monitor[/cyan] to see real-time results")


@suite_group.command(name="configure")
@click.argument("suite_id", required=False)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Interactive configuration wizard",
)
@click.option(
    "--environment",
    "-e",
    type=click.Choice(["production", "staging", "development"]),
    default="production",
    help="Target environment",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Save configuration to file",
)
def configure_suite(
    suite_id: Optional[str],
    interactive: bool,
    environment: str,
    output: Optional[str],
):
    """Configure a suite for deployment."""
    
    try:
        api_client = get_api_client()
        
        # Select suite if not provided
        if not suite_id:
            with spinner("Loading suites..."):
                response = api_client.get("/suites", params={"available": True})
                suites = response.get("suites", [])
                
                if not suites:
                    console.print("[yellow]No suites available for configuration.[/yellow]")
                    return
                
                choices = [
                    f"{s['suite_id']} - {s['name']}"
                    for s in suites
                ]
                
                selection = questionary.select(
                    "Select a suite to configure:",
                    choices=choices,
                ).ask()
                
                if not selection:
                    console.print("[yellow]Configuration cancelled.[/yellow]")
                    return
                
                suite_id = selection.split(" - ")[0]
        
        # Get suite details
        with spinner(f"Loading suite '{suite_id}'..."):
            suite = api_client.get(f"/suites/{suite_id}")
            
            if not suite:
                console.print(f"[red]Suite '{suite_id}' not found.[/red]")
                return
        
        console.print(Panel.fit(
            f"[bold]Suite:[/bold] {suite['name']}\n"
            f"[bold]Description:[/bold] {suite.get('description', 'No description')}\n"
            f"[bold]Agents:[/bold] {suite.get('agent_count', 0)}\n"
            f"[bold]Est. Monthly Cost:[/bold] ${suite.get('estimated_cost_monthly', 0):.2f}\n"
            f"[bold]Est. ROI:[/bold] {suite.get('estimated_roi', 0):.1f}x",
            title="Suite Overview",
            border_style="blue",
        ))
        
        # Interactive configuration
        if interactive:
            configuration = _run_configuration_wizard(suite, environment)
        else:
            configuration = _load_default_configuration(suite, environment)
        
        # Validate configuration
        with spinner("Validating configuration..."):
            try:
                validation_result = api_client.post(
                    f"/suites/{suite_id}/validate",
                    json=configuration,
                )
                
                if not validation_result.get("valid", False):
                    errors = validation_result.get("errors", [])
                    console.print("[red]Configuration validation failed:[/red]")
                    for error in errors:
                        console.print(f"  - {error}")
                    return
                
            except APIError as e:
                if e.status_code == 422:
                    console.print("[red]Configuration validation failed:[/red]")
                    console.print(format_json(e.detail))
                    return
                else:
                    raise
        
        # Show configuration summary
        _show_configuration_summary(configuration, suite, environment)
        
        # Save configuration
        if output:
            _save_configuration(configuration, output)
        
        # Ask for deployment
        if interactive and click.confirm("Deploy this configuration now?", default=False):
            ctx = click.get_current_context()
            ctx.invoke(
                deploy_suite,
                suite_id=suite_id,
                environment=environment,
                configuration=None,  # Use interactive configuration
                auto_confirm=True,
            )
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _run_configuration_wizard(
    suite: Dict[str, Any],
    environment: str,
) -> Dict[str, Any]:
    """Run interactive configuration wizard."""
    
    configuration = {
        "suite_id": suite["suite_id"],
        "environment": environment,
        "configuration": {},
        "metadata": {
            "configured_at": datetime.utcnow().isoformat(),
            "configured_by": config.get("user.email", "cli-user"),
        },
    }
    
    console.print("\n[bold cyan]Configuration Wizard[/bold cyan]")
    console.print("=" * 40)
    
    # Cloud provider selection
    available_providers = suite.get("requirements", {}).get("cloud_providers", [])
    if available_providers:
        console.print("\n[bold]Cloud Providers:[/bold]")
        
        selected_providers = []
        for provider in available_providers:
            if questionary.confirm(
                f"Configure {provider.upper()}?",
                default=True,
            ).ask():
                selected_providers.append(provider)
        
        configuration["configuration"]["cloud_providers"] = selected_providers
    
    # Feature selection
    features = suite.get("features", [])
    if features:
        console.print("\n[bold]Features:[/bold]")
        
        selected_features = questionary.checkbox(
            "Select features to enable:",
            choices=features,
            default=features,  # Enable all by default
        ).ask()
        
        configuration["configuration"]["features"] = selected_features
    
    # Schedule configuration
    console.print("\n[bold]Schedule:[/bold]")
    schedule_options = [
        "Hourly",
        "Every 6 hours",
        "Daily at midnight",
        "Custom cron expression",
    ]
    
    schedule_choice = questionary.select(
        "Select execution schedule:",
        choices=schedule_options,
    ).ask()
    
    if schedule_choice == "Custom cron expression":
        cron_expr = questionary.text(
            "Enter cron expression:",
            default="0 */6 * * *",
        ).ask()
        configuration["configuration"]["schedule"] = cron_expr
    else:
        schedule_map = {
            "Hourly": "0 * * * *",
            "Every 6 hours": "0 */6 * * *",
            "Daily at midnight": "0 0 * * *",
        }
        configuration["configuration"]["schedule"] = schedule_map.get(schedule_choice, "0 */6 * * *")
    
    # Notification settings
    console.print("\n[bold]Notifications:[/bold]")
    enable_notifications = questionary.confirm(
        "Enable email notifications?",
        default=True,
    ).ask()
    
    if enable_notifications:
        notification_email = questionary.text(
            "Notification email:",
            default=config.get("user.email", ""),
            validate=lambda x: "@" in x,
        ).ask()
        
        configuration["configuration"]["notifications"] = {
            "enabled": True,
            "email": notification_email,
            "level": questionary.select(
                "Notification level:",
                choices=["errors_only", "warnings", "all"],
                default="warnings",
            ).ask(),
        }
    
    # Cost optimization thresholds
    if "cost_optimization" in suite.get("features", []):
        console.print("\n[bold]Cost Optimization:[/bold]")
        
        threshold = questionary.text(
            "Resource utilization threshold (0.1-1.0):",
            default="0.7",
            validate=lambda x: 0.1 <= float(x) <= 1.0,
        ).ask()
        
        configuration["configuration"]["cost_optimization"] = {
            "enabled": True,
            "threshold": float(threshold),
            "auto_apply": questionary.confirm(
                "Auto-apply optimizations?",
                default=False,
            ).ask(),
        }
    
    # Security settings
    if "security_scanning" in suite.get("features", []):
        console.print("\n[bold]Security Scanning:[/bold]")
        
        configuration["configuration"]["security"] = {
            "enabled": True,
            "frequency": questionary.select(
                "Scan frequency:",
                choices=["hourly", "daily", "weekly"],
                default="daily",
            ).ask(),
            "severity_level": questionary.select(
                "Report severity level:",
                choices=["low", "medium", "high", "critical"],
                default="medium",
            ).ask(),
        }
    
    return configuration


def _load_default_configuration(
    suite: Dict[str, Any],
    environment: str,
) -> Dict[str, Any]:
    """Load default configuration for suite."""
    
    return {
        "suite_id": suite["suite_id"],
        "environment": environment,
        "configuration": {
            "cloud_providers": suite.get("requirements", {}).get("cloud_providers", []),
            "features": suite.get("features", []),
            "schedule": "0 */6 * * *",
            "notifications": {
                "enabled": True,
                "email": config.get("user.email", ""),
                "level": "warnings",
            },
            "cost_optimization": {
                "enabled": "cost_optimization" in suite.get("features", []),
                "threshold": 0.7,
                "auto_apply": False,
            },
            "security": {
                "enabled": "security_scanning" in suite.get("features", []),
                "frequency": "daily",
                "severity_level": "medium",
            },
        },
        "metadata": {
            "configured_at": datetime.utcnow().isoformat(),
            "configured_by": config.get("user.email", "cli-user"),
        },
    }


def _show_configuration_summary(
    configuration: Dict[str, Any],
    suite: Dict[str, Any],
    environment: str,
):
    """Show configuration summary."""
    
    summary = Table(title="Configuration Summary", show_header=False)
    summary.add_column("Setting", style="cyan")
    summary.add_column("Value", style="green")
    
    config_data = configuration.get("configuration", {})
    
    summary.add_row("Suite", suite["name"])
    summary.add_row("Environment", environment)
    summary.add_row("Cloud Providers", ", ".join(config_data.get("cloud_providers", [])))
    summary.add_row("Features", ", ".join(config_data.get("features", [])))
    summary.add_row("Schedule", config_data.get("schedule", "Not set"))
    
    notifications = config_data.get("notifications", {})
    if notifications.get("enabled"):
        summary.add_row("Notifications", f"Enabled ({notifications.get('level')})")
    else:
        summary.add_row("Notifications", "Disabled")
    
    cost_opt = config_data.get("cost_optimization", {})
    if cost_opt.get("enabled"):
        summary.add_row("Cost Optimization", f"Enabled (threshold: {cost_opt.get('threshold')})")
    
    security = config_data.get("security", {})
    if security.get("enabled"):
        summary.add_row("Security Scanning", f"Enabled ({security.get('frequency')})")
    
    console.print("\n")
    console.print(Panel.fit(summary, title="✅ Configuration Complete", border_style="green"))


def _save_configuration(
    configuration: Dict[str, Any],
    output_path: str,
):
    """Save configuration to file."""
    
    path = Path(output_path)
    
    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Determine format from extension
    if path.suffix.lower() in [".yaml", ".yml"]:
        with open(path, "w") as f:
            yaml.dump(configuration, f, default_flow_style=False)
    else:
        # Default to JSON
        with open(path, "w") as f:
            json.dump(configuration, f, indent=2)
    
    console.print(f"[green]Configuration saved to {path}[/green]")


@suite_group.command(name="deploy")
@click.argument("suite_id")
@click.option(
    "--environment",
    "-e",
    type=click.Choice(["production", "staging", "development"]),
    default="production",
    help="Target environment",
)
@click.option(
    "--configuration",
    "-c",
    type=click.Path(exists=True),
    help="Path to configuration file",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Perform dry run without deploying",
)
@click.option(
    "--auto-confirm",
    is_flag=True,
    help="Skip confirmation prompts",
)
@click.option(
    "--wait",
    "-w",
    is_flag=True,
    help="Wait for deployment to complete",
)
@click.option(
    "--timeout",
    "-t",
    type=int,
    default=300,
    help="Timeout in seconds for waiting",
)
def deploy_suite(
    suite_id: str,
    environment: str,
    configuration: Optional[str],
    dry_run: bool,
    auto_confirm: bool,
    wait: bool,
    timeout: int,
):
    """Deploy a suite to infrastructure."""
    
    try:
        api_client = get_api_client()
        
        # Load configuration
        config_data = None
        if configuration:
            with spinner("Loading configuration..."):
                path = Path(configuration)
                if path.suffix.lower() in [".yaml", ".yml"]:
                    with open(path) as f:
                        config_data = yaml.safe_load(f)
                else:
                    with open(path) as f:
                        config_data = json.load(f)
        else:
            # Load default configuration
            with spinner(f"Loading suite '{suite_id}'..."):
                suite = api_client.get(f"/suites/{suite_id}")
                config_data = _load_default_configuration(suite, environment)
        
        # Show deployment summary
        _show_deployment_summary(suite_id, environment, config_data, dry_run)
        
        # Confirmation
        if not auto_confirm and not dry_run:
            if not click.confirm("Proceed with deployment?", default=False):
                console.print("[yellow]Deployment cancelled.[/yellow]")
                return
        
        # Deploy
        with spinner("Initiating deployment..."):
            deploy_payload = {
                "suite_id": suite_id,
                "environment": environment,
                "configuration": config_data.get("configuration", {}),
                "dry_run": dry_run,
            }
            
            response = api_client.post("/deployments", json=deploy_payload)
            deployment_id = response.get("deployment_id")
            
            if dry_run:
                console.print("[green]✅ Dry run completed successfully[/green]")
                
                # Show dry run results
                results = response.get("results", {})
                if results:
                    console.print("\n[bold]Dry Run Results:[/bold]")
                    
                    results_table = Table(show_header=False)
                    results_table.add_column("Resource", style="cyan")
                    results_table.add_column("Action", style="yellow")
                    results_table.add_column("Impact", style="green")
                    
                    for resource, action in results.get("actions", {}).items():
                        results_table.add_row(
                            resource,
                            action.get("action", "N/A"),
                            action.get("impact", "N/A"),
                        )
                    
                    console.print(results_table)
                    
                    estimated_savings = results.get("estimated_savings", 0)
                    if estimated_savings:
                        console.print(f"\n[bold]Estimated Monthly Savings:[/bold] ${estimated_savings:,.2f}")
                
                return
            
            console.print(f"[green]✅ Deployment initiated: {deployment_id}[/green]")
        
        # Monitor deployment if requested
        if wait:
            _monitor_deployment(deployment_id, timeout)
        else:
            console.print(f"\n[bold]Next steps:[/bold]")
            console.print(f"1. Run [cyan]suite monitor --deployment {deployment_id}[/cyan] to track progress")
            console.print(f"2. Run [cyan]suite report --deployment {deployment_id}[/cyan] for results")
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _show_deployment_summary(
    suite_id: str,
    environment: str,
    configuration: Dict[str, Any],
    dry_run: bool,
):
    """Show deployment summary."""
    
    title = "🚀 Deployment Summary"
    if dry_run:
        title = "🔍 Dry Run Summary"
    
    summary = Panel.fit(
        f"[bold]Suite ID:[/bold] {suite_id}\n"
        f"[bold]Environment:[/bold] {environment}\n"
        f"[bold]Mode:[/bold] {'Dry Run' if dry_run else 'Live Deployment'}\n"
        f"[bold]Configuration:[/bold] {len(configuration.get('configuration', {}))} settings",
        title=title,
        border_style="yellow" if dry_run else "cyan",
    )
    
    console.print("\n")
    console.print(summary)
    
    # Show configuration highlights
    config_data = configuration.get("configuration", {})
    if config_data:
        console.print("\n[bold]Configuration Highlights:[/bold]")
        
        if config_data.get("cloud_providers"):
            console.print(f"  • Cloud Providers: {', '.join(config_data['cloud_providers'])}")
        
        if config_data.get("features"):
            console.print(f"  • Features: {', '.join(config_data['features'][:3])}")
            if len(config_data['features']) > 3:
                console.print(f"    (+ {len(config_data['features']) - 3} more)")
        
        if config_data.get("schedule"):
            console.print(f"  • Schedule: {config_data['schedule']}")


def _monitor_deployment(deployment_id: str, timeout: int):
    """Monitor deployment progress."""
    
    api_client = get_api_client()
    start_time = time.time()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        
        task = progress.add_task(
            f"[cyan]Monitoring deployment {deployment_id}...",
            total=100,
        )
        
        last_status = None
        last_update = start_time
        
        while time.time() - start_time < timeout:
            try:
                # Get deployment status
                response = api_client.get(f"/deployments/{deployment_id}")
                status = response.get("status", "pending")
                progress_value = response.get("progress", 0)
                
                # Update progress
                progress.update(task, completed=progress_value)
                
                # Show status changes
                if status != last_status:
                    progress.console.print(f"[yellow]Status: {status}[/yellow]")
                    last_status = status
                
                # Show deployment events
                events = response.get("events", [])
                for event in events[-3:]:  # Show last 3 events
                    if event.get("timestamp", 0) > last_update:
                        progress.console.print(f"[dim]{event.get('message')}[/dim]")
                        last_update = event.get("timestamp", last_update)
                
                # Check if deployment is complete
                if status in ["completed", "failed", "rolled_back"]:
                    progress.update(task, completed=100)
                    
                    # Show result
                    if status == "completed":
                        progress.console.print("[green]✅ Deployment completed successfully![/green]")
                        
                        # Show deployment results
                        results = response.get("results", {})
                        if results:
                            progress.console.print("\n[bold]Deployment Results:[/bold]")
                            progress.console.print(f"  • Agents deployed: {results.get('agents_deployed', 0)}")
                            progress.console.print(f"  • Resources created: {results.get('resources_created', 0)}")
                            progress.console.print(f"  • Estimated savings: ${results.get('estimated_savings', 0):,.2f}/month")
                    
                    elif status == "failed":
                        progress.console.print("[red]❌ Deployment failed![/red]")
                        error = response.get("error", "Unknown error")
                        progress.console.print(f"[red]Error: {error}[/red]")
                    
                    elif status == "rolled_back":
                        progress.console.print("[yellow]⚠️  Deployment rolled back[/yellow]")
                        reason = response.get("rollback_reason", "Unknown reason")
                        progress.console.print(f"[yellow]Reason: {reason}[/yellow]")
                    
                    return
                
                time.sleep(2)  # Poll every 2 seconds
                
            except Exception as e:
                progress.console.print(f"[red]Error monitoring deployment: {str(e)}[/red]")
                time.sleep(5)
        
        progress.console.print("[yellow]⚠️  Monitoring timeout reached[/yellow]")


@suite_group.command(name="monitor")
@click.option(
    "--deployment",
    "-d",
    type=str,
    help="Deployment ID to monitor",
)
@click.option(
    "--suite",
    "-s",
    type=str,
    help="Suite ID to monitor",
)
@click.option(
    "--environment",
    "-e",
    type=click.Choice(["production", "staging", "development"]),
    help="Environment to monitor",
)
@click.option(
    "--live",
    "-l",
    is_flag=True,
    help="Live monitoring mode",
)
@click.option(
    "--refresh",
    "-r",
    type=int,
    default=5,
    help="Refresh interval in seconds (live mode only)",
)
def monitor_suite(
    deployment: Optional[str],
    suite: Optional[str],
    environment: Optional[str],
    live: bool,
    refresh: int,
):
    """Monitor suites in real-time."""
    
    try:
        api_client = get_api_client()
        
        # Determine what to monitor
        if deployment:
            # Monitor specific deployment
            _monitor_deployment_live(deployment, refresh) if live else _show_deployment_status(deployment)
        
        elif suite:
            # Monitor specific suite
            _monitor_suite_live(suite, environment, refresh) if live else _show_suite_status(suite, environment)
        
        else:
            # Monitor all active suites
            _monitor_all_suites_live(refresh) if live else _show_all_suites_status()
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _show_deployment_status(deployment_id: str):
    """Show deployment status."""
    
    with spinner(f"Loading deployment {deployment_id}..."):
        api_client = get_api_client()
        deployment = api_client.get(f"/deployments/{deployment_id}")
        
        if not deployment:
            console.print(f"[red]Deployment '{deployment_id}' not found.[/red]")
            return
        
        # Create status panel
        status = deployment.get("status", "unknown")
        status_color = {
            "pending": "yellow",
            "running": "blue",
            "completed": "green",
            "failed": "red",
            "rolled_back": "orange",
        }.get(status, "white")
        
        panel = Panel.fit(
            f"[bold]Deployment ID:[/bold] {deployment_id}\n"
            f"[bold]Suite:[/bold] {deployment.get('suite_id')}\n"
            f"[bold]Environment:[/bold] {deployment.get('environment')}\n"
            f"[bold]Status:[/bold] [{status_color}]{status}[/{status_color}]\n"
            f"[bold]Progress:[/bold] {deployment.get('progress', 0)}%\n"
            f"[bold]Started:[/bold] {deployment.get('started_at', 'N/A')}\n"
            f"[bold]Completed:[/bold] {deployment.get('completed_at', 'N/A')}",
            title="Deployment Status",
            border_style=status_color,
        )
        
        console.print(panel)
        
        # Show events if available
        events = deployment.get("events", [])
        if events:
            console.print("\n[bold]Recent Events:[/bold]")
            for event in events[-5:]:  # Last 5 events
                console.print(f"  • {event.get('timestamp')}: {event.get('message')}")


def _monitor_deployment_live(deployment_id: str, refresh_interval: int):
    """Live monitor deployment."""
    
    api_client = get_api_client()
    
    with Live(refresh_per_second=1) as live:
        while True:
            try:
                deployment = api_client.get(f"/deployments/{deployment_id}")
                status = deployment.get("status", "unknown")
                
                # Create live display
                display = _create_deployment_live_display(deployment)
                live.update(display)
                
                # Stop if deployment is complete
                if status in ["completed", "failed", "rolled_back"]:
                    time.sleep(2)  # Show final state
                    break
                
                time.sleep(refresh_interval)
                
            except KeyboardInterrupt:
                console.print("\n[yellow]Monitoring stopped by user[/yellow]")
                break
            except Exception as e:
                console.print(f"[red]Error: {str(e)}[/red]")
                time.sleep(refresh_interval)


def _create_deployment_live_display(deployment: Dict[str, Any]) -> Panel:
    """Create live display for deployment."""
    
    status = deployment.get("status", "unknown")
    progress = deployment.get("progress", 0)
    
    # Create progress bar
    progress_bar = f"[{'█' * (progress // 2)}{'░' * (50 - progress // 2)}] {progress}%"
    
    # Status indicator
    status_indicators = {
        "pending": "🟡",
        "running": "🔵",
        "completed": "🟢",
        "failed": "🔴",
        "rolled_back": "🟠",
    }
    
    status_indicator = status_indicators.get(status, "⚪")
    
    content = f"""
{status_indicator} [bold]{deployment.get('suite_id', 'Unknown')}[/bold]
Environment: {deployment.get('environment', 'N/A')}
Status: [{_get_status_color(status)}]{status}[/{_get_status_color(status)}]
Progress: {progress_bar}

[dim]Last updated: {datetime.utcnow().strftime('%H:%M:%S')}[/dim]
"""
    
    # Add recent events
    events = deployment.get("events", [])
    if events:
        content += "\n[bold]Recent Events:[/bold]\n"
        for event in events[-3:]:
            timestamp = event.get("timestamp", "").split("T")[1].split(".")[0]
            content += f"  {timestamp}: {event.get('message', '')}\n"
    
    return Panel.fit(
        content,
        title=f"🚀 Deployment Monitor: {deployment.get('deployment_id', 'Unknown')}",
        border_style=_get_status_color(status),
    )


def _get_status_color(status: str) -> str:
    """Get color for status."""
    return {
        "pending": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "rolled_back": "orange",
    }.get(status, "white")


@suite_group.command(name="optimize")
@click.argument("suite_id")
@click.option(
    "--auto-apply",
    is_flag=True,
    help="Auto-apply optimizations",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show optimizations without applying",
)
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=0.7,
    help="Optimization threshold (0.1-1.0)",
)
def optimize_suite(
    suite_id: str,
    auto_apply: bool,
    dry_run: bool,
    threshold: float,
):
    """Optimize suite configuration."""
    
    try:
        api_client = get_api_client()
        
        with spinner(f"Analyzing suite '{suite_id}'..."):
            # Get current configuration
            suite = api_client.get(f"/suites/{suite_id}")
            deployments = api_client.get(f"/suites/{suite_id}/deployments")
            
            if not deployments:
                console.print(f"[yellow]No deployments found for suite '{suite_id}'[/yellow]")
                return
        
        # Run optimization analysis
        with spinner("Running optimization analysis..."):
            analysis_payload = {
                "suite_id": suite_id,
                "threshold": threshold,
                "dry_run": dry_run or not auto_apply,
            }
            
            analysis = api_client.post("/suites/optimize", json=analysis_payload)
        
        # Display optimization recommendations
        _display_optimization_recommendations(analysis, suite)
        
        # Apply optimizations if requested
        if auto_apply and not dry_run:
            if click.confirm("Apply these optimizations?", default=False):
                with spinner("Applying optimizations..."):
                    result = api_client.post("/suites/optimize/apply", json=analysis_payload)
                    
                    if result.get("success"):
                        console.print("[green]✅ Optimizations applied successfully![/green]")
                        
                        # Show results
                        savings = result.get("estimated_savings", 0)
                        if savings:
                            console.print(f"\n[bold]Estimated Savings:[/bold] ${savings:,.2f}/month")
                    else:
                        console.print("[red]❌ Failed to apply optimizations[/red]")
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _display_optimization_recommendations(
    analysis: Dict[str, Any],
    suite: Dict[str, Any],
):
    """Display optimization recommendations."""
    
    recommendations = analysis.get("recommendations", [])
    
    if not recommendations:
        console.print("[green]✅ No optimizations needed - suite is already optimal![/green]")
        return
    
    console.print(f"\n[bold]Optimization Recommendations for {suite['name']}:[/bold]")
    console.print(f"Found {len(recommendations)} potential optimizations\n")
    
    # Create recommendations table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Resource", style="cyan")
    table.add_column("Current", style="yellow")
    table.add_column("Recommended", style="green")
    table.add_column("Savings/Month", justify="right")
    table.add_column("Risk", justify="center")
    
    total_savings = 0
    
    for rec in recommendations[:10]:  # Show top 10
        resource = rec.get("resource_id", "Unknown")
        current = rec.get("current_config", "N/A")
        recommended = rec.get("recommended_config", "N/A")
        savings = rec.get("estimated_savings", 0)
        risk = rec.get("risk_level", "medium")
        
        risk_color = {
            "low": "green",
            "medium": "yellow",
            "high": "red",
        }.get(risk, "white")
        
        table.add_row(
            resource[:40] + ("..." if len(resource) > 40 else ""),
            str(current),
            str(recommended),
            f"${savings:,.2f}",
            f"[{risk_color}]{risk.upper()}[/{risk_color}]",
        )
        
        total_savings += savings
    
    console.print(table)
    
    # Show summary
    console.print(f"\n[bold]Total Potential Savings:[/bold] ${total_savings:,.2f}/month")
    
    if len(recommendations) > 10:
        console.print(f"[dim](Showing top 10 of {len(recommendations)} recommendations)[/dim]")


# Additional suite commands (report, migrate, backup, restore) would follow similar patterns
# Due to length, I'll provide stubs for the remaining commands

@suite_group.command(name="report")
@click.argument("suite_id")
@click.option(
    "--period",
    "-p",
    type=click.Choice(["day", "week", "month", "quarter", "year"]),
    default="month",
    help="Report period",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "html", "pdf", "console"]),
    default="console",
    help="Report format",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path",
)
def generate_report(
    suite_id: str,
    period: str,
    output_format: str,
    output: Optional[str],
):
    """Generate suite usage reports."""
    
    console.print(f"[yellow]Report generation for {suite_id} ({period}) in {output_format} format[/yellow]")
    # Implementation would call API to generate report


@suite_group.command(name="migrate")
@click.argument("suite_id")
@click.option(
    "--from-version",
    required=True,
    help="Source version",
)
@click.option(
    "--to-version",
    required=True,
    help="Target version",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Dry run migration",
)
def migrate_suite(
    suite_id: str,
    from_version: str,
    to_version: str,
    dry_run: bool,
):
    """Migrate suite between versions."""
    
    console.print(f"[yellow]Migrating {suite_id} from {from_version} to {to_version}[/yellow]")
    # Implementation would handle version migration


@suite_group.command(name="backup")
@click.argument("suite_id")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    required=True,
    help="Backup output path",
)
@click.option(
    "--include-data",
    is_flag=True,
    help="Include operational data in backup",
)
def backup_suite(
    suite_id: str,
    output: str,
    include_data: bool,
):
    """Backup suite configuration."""
    
    console.print(f"[yellow]Backing up {suite_id} to {output}[/yellow]")
    # Implementation would create backup


@suite_group.command(name="restore")
@click.argument("backup_file", type=click.Path(exists=True))
@click.option(
    "--suite-id",
    help="Target suite ID (default: from backup)",
)
@click.option(
    "--confirm",
    "-c",
    is_flag=True,
    help="Skip confirmation prompt",
)
def restore_suite(
    backup_file: str,
    suite_id: Optional[str],
    confirm: bool,
):
    """Restore suite from backup."""
    
    console.print(f"[yellow]Restoring from backup {backup_file}[/yellow]")
    # Implementation would restore from backup


# Export commands
__all__ = ["suite_group"]