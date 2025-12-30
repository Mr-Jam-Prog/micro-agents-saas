"""
Deployment commands for MicroAgents Platform.
Multi-cloud, multi-cluster deployment with validation, monitoring, and cost estimation.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
import questionary
import yaml
from click import Context
from pydantic import BaseModel, Field, ValidationError, field_validator
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
    TimeRemainingColumn,
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
    validate_uuid,
)
from src.utils.config.settings import get_settings

console = Console()

# Deployment models
class DeploymentTarget(str, click.Choice):
    """Deployment target types."""
    
    KUBERNETES = "kubernetes"
    DOCKER_SWARM = "docker_swarm"
    NOMAD = "nomad"
    CLOUD_RUN = "cloud_run"
    LAMBDA = "lambda"
    BARE_METAL = "bare_metal"
    HYBRID = "hybrid"
    
    AWS_EKS = "aws_eks"
    AZURE_AKS = "azure_aks"
    GOOGLE_GKE = "google_gke"
    
    def __iter__(self):
        return iter([
            self.KUBERNETES,
            self.AWS_EKS,
            self.AZURE_AKS,
            self.GOOGLE_GKE,
            self.DOCKER_SWARM,
            self.NOMAD,
            self.CLOUD_RUN,
            self.LAMBDA,
            self.BARE_METAL,
            self.HYBRID,
        ])


class DeploymentStrategy(str, click.Choice):
    """Deployment strategies."""
    
    ROLLING = "rolling"
    BLUE_GREEN = "blue_green"
    CANARY = "canary"
    RECREATE = "recreate"
    A_B_TESTING = "a_b_testing"
    SHADOW = "shadow"


class DeploymentConfiguration(BaseModel):
    """Deployment configuration model."""
    
    target: DeploymentTarget
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    namespace: str = "microagents"
    replicas: int = Field(ge=1, le=1000, default=3)
    resources: Dict[str, Any] = Field(
        default_factory=lambda: {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        }
    )
    
    # Cloud-specific configurations
    cloud_config: Dict[str, Any] = Field(default_factory=dict)
    
    # Kubernetes-specific
    k8s_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "service_type": "ClusterIP",
            "ingress_enabled": True,
            "autoscaling": {
                "enabled": True,
                "min_replicas": 2,
                "max_replicas": 10,
                "target_cpu_utilization": 70,
            },
        }
    )
    
    # Security configurations
    security: Dict[str, Any] = Field(
        default_factory=lambda: {
            "network_policies": True,
            "pod_security_standards": "restricted",
            "secrets_encryption": True,
        }
    )
    
    # Monitoring configurations
    monitoring: Dict[str, Any] = Field(
        default_factory=lambda: {
            "enabled": True,
            "metrics_scrape": True,
            "logs_collection": True,
            "tracing_enabled": True,
        }
    )
    
    # Backup configurations
    backup: Dict[str, Any] = Field(
        default_factory=lambda: {
            "enabled": True,
            "schedule": "0 2 * * *",  # Daily at 2 AM
            "retention_days": 30,
        }
    )
    
    @field_validator('namespace')
    @classmethod
    def validate_namespace(cls, v):
        """Validate Kubernetes namespace."""
        if not v.isalnum() and '-' not in v:
            raise ValueError("Namespace must be alphanumeric with hyphens")
        return v
    
    @field_validator('replicas')
    @classmethod
    def validate_replicas_for_strategy(cls, v, info):
        """Validate replicas based on deployment strategy."""
        strategy = info.data.get('strategy', DeploymentStrategy.ROLLING)
        
        if strategy == DeploymentStrategy.BLUE_GREEN and v < 2:
            raise ValueError("Blue-green deployment requires at least 2 replicas")
        
        if strategy == DeploymentStrategy.CANARY and v < 3:
            raise ValueError("Canary deployment requires at least 3 replicas")
        
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "target": "aws_eks",
                "strategy": "blue_green",
                "namespace": "microagents-prod",
                "replicas": 5,
                "resources": {
                    "requests": {"cpu": "200m", "memory": "256Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                },
                "cloud_config": {
                    "region": "us-east-1",
                    "node_type": "t3.medium",
                    "node_count": 3,
                },
                "k8s_config": {
                    "service_type": "LoadBalancer",
                    "ingress_enabled": True,
                    "autoscaling": {"enabled": True},
                },
                "security": {
                    "network_policies": True,
                    "pod_security_standards": "restricted",
                },
                "monitoring": {
                    "enabled": True,
                    "metrics_scrape": True,
                },
                "backup": {
                    "enabled": True,
                    "schedule": "0 2 * * *",
                },
            }
        }


class DeploymentStatus(BaseModel):
    """Deployment status model."""
    
    deployment_id: str
    status: str
    phase: str
    progress: int = Field(ge=0, le=100, default=0)
    pods_ready: str = "0/0"
    pods_total: int = 0
    services_ready: int = 0
    services_total: int = 0
    ingress_ready: bool = False
    health_checks_passed: int = 0
    health_checks_total: int = 0
    last_check: Optional[datetime] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)
    estimated_completion: Optional[datetime] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "deployment_id": "dep-123456",
                "status": "in_progress",
                "phase": "deploying_services",
                "progress": 65,
                "pods_ready": "3/5",
                "pods_total": 5,
                "services_ready": 2,
                "services_total": 3,
                "ingress_ready": False,
                "health_checks_passed": 4,
                "health_checks_total": 5,
                "last_check": "2024-01-15T10:30:00Z",
                "events": [
                    {"timestamp": "2024-01-15T10:25:00Z", "message": "Deployment started"},
                    {"timestamp": "2024-01-15T10:28:00Z", "message": "Pods created"},
                ],
                "estimated_completion": "2024-01-15T10:35:00Z",
            }
        }


class DeploymentCostEstimate(BaseModel):
    """Deployment cost estimate model."""
    
    monthly_cost: float = Field(ge=0)
    hourly_cost: float = Field(ge=0)
    cost_breakdown: Dict[str, float]
    savings_opportunities: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]
    
    class Config:
        json_schema_extra = {
            "example": {
                "monthly_cost": 1250.50,
                "hourly_cost": 1.71,
                "cost_breakdown": {
                    "compute": 850.0,
                    "storage": 250.0,
                    "network": 150.0,
                    "managed_services": 50.5,
                },
                "savings_opportunities": [
                    {"type": "rightsize", "potential_savings": 200.0, "description": "Reduce CPU requests"},
                ],
                "recommendations": [
                    {"type": "reserved_instance", "potential_savings": 300.0, "description": "Use reserved instances"},
                ],
            }
        }


@click.group(name="deploy")
def deploy_group():
    """Deploy MicroAgents to various platforms."""
    pass


@deploy_group.command(name="k8s")
@click.argument("suite_id")
@click.option(
    "--cluster",
    "-c",
    type=str,
    required=True,
    help="Kubernetes cluster name or context",
)
@click.option(
    "--namespace",
    "-n",
    type=str,
    default="microagents",
    help="Kubernetes namespace",
)
@click.option(
    "--strategy",
    "-s",
    type=click.Choice(["rolling", "blue-green", "canary", "recreate"]),
    default="rolling",
    help="Deployment strategy",
)
@click.option(
    "--replicas",
    "-r",
    type=int,
    default=3,
    help="Number of replicas",
)
@click.option(
    "--config-file",
    "-f",
    type=click.Path(exists=True),
    help="Deployment configuration file",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Dry run - show what would be deployed",
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
def deploy_kubernetes(
    suite_id: str,
    cluster: str,
    namespace: str,
    strategy: str,
    replicas: int,
    config_file: Optional[str],
    dry_run: bool,
    wait: bool,
    timeout: int,
):
    """Deploy to Kubernetes cluster."""
    
    try:
        api_client = get_api_client()
        
        # Load or create configuration
        if config_file:
            with spinner("Loading configuration..."):
                deployment_config = _load_deployment_config(config_file)
        else:
            deployment_config = _create_k8s_config(cluster, namespace, strategy, replicas)
        
        # Validate configuration
        with spinner("Validating deployment configuration..."):
            validation = api_client.post("/deployments/validate", json=deployment_config)
            
            if not validation.get("valid", False):
                console.print("[red]Deployment configuration validation failed:[/red]")
                for error in validation.get("errors", []):
                    console.print(f"  • {error}")
                return
        
        # Show deployment summary
        _show_k8s_deployment_summary(suite_id, deployment_config, dry_run)
        
        # Confirm deployment
        if not dry_run:
            if not click.confirm("Proceed with deployment?", default=False):
                console.print("[yellow]Deployment cancelled.[/yellow]")
                return
        
        # Deploy
        with spinner("Initiating Kubernetes deployment..."):
            deploy_payload = {
                "suite_id": suite_id,
                "target": "kubernetes",
                "configuration": deployment_config,
                "dry_run": dry_run,
            }
            
            response = api_client.post("/deployments/kubernetes", json=deploy_payload)
            deployment_id = response.get("deployment_id")
            
            if dry_run:
                console.print("[green]✅ Dry run completed successfully[/green]")
                _show_dry_run_results(response)
                return
            
            console.print(f"[green]✅ Deployment initiated: {deployment_id}[/green]")
        
        # Monitor deployment if requested
        if wait:
            _monitor_deployment(deployment_id, timeout)
        else:
            console.print(f"\n[bold]Next steps:[/bold]")
            console.print(f"1. Run [cyan]deploy status {deployment_id}[/cyan] to check progress")
            console.print(f"2. Run [cyan]deploy monitor {deployment_id}[/cyan] for real-time monitoring")
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _create_k8s_config(
    cluster: str,
    namespace: str,
    strategy: str,
    replicas: int,
) -> Dict[str, Any]:
    """Create Kubernetes deployment configuration."""
    
    # Determine cluster type
    cluster_type = "generic"
    if "eks" in cluster.lower():
        cluster_type = "aws_eks"
    elif "aks" in cluster.lower():
        cluster_type = "azure_aks"
    elif "gke" in cluster.lower():
        cluster_type = "google_gke"
    
    return {
        "target": cluster_type,
        "strategy": strategy,
        "namespace": namespace,
        "replicas": replicas,
        "cloud_config": {
            "cluster_name": cluster,
            "auto_detect_context": True,
        },
        "k8s_config": {
            "service_type": "LoadBalancer" if cluster_type != "generic" else "ClusterIP",
            "ingress_enabled": True,
            "autoscaling": {
                "enabled": True,
                "min_replicas": max(2, replicas // 2),
                "max_replicas": replicas * 2,
                "target_cpu_utilization": 70,
            },
            "readiness_probe": {
                "enabled": True,
                "initial_delay_seconds": 10,
                "period_seconds": 5,
            },
            "liveness_probe": {
                "enabled": True,
                "initial_delay_seconds": 30,
                "period_seconds": 10,
            },
        },
        "security": {
            "network_policies": True,
            "pod_security_standards": "restricted",
            "secrets_encryption": True,
            "image_scanning": True,
        },
        "monitoring": {
            "enabled": True,
            "metrics_scrape": True,
            "logs_collection": True,
            "tracing_enabled": True,
            "prometheus_enabled": True,
        },
        "backup": {
            "enabled": True,
            "schedule": "0 2 * * *",
            "retention_days": 30,
            "include_pvcs": True,
        },
    }


def _show_k8s_deployment_summary(
    suite_id: str,
    config: Dict[str, Any],
    dry_run: bool,
):
    """Show Kubernetes deployment summary."""
    
    title = "🚀 Kubernetes Deployment Summary"
    if dry_run:
        title = "🔍 Kubernetes Dry Run Summary"
    
    summary = Panel.fit(
        f"[bold]Suite:[/bold] {suite_id}\n"
        f"[bold]Cluster:[/bold] {config.get('cloud_config', {}).get('cluster_name', 'Unknown')}\n"
        f"[bold]Namespace:[/bold] {config.get('namespace', 'default')}\n"
        f"[bold]Strategy:[/bold] {config.get('strategy', 'rolling')}\n"
        f"[bold]Replicas:[/bold] {config.get('replicas', 1)}\n"
        f"[bold]Mode:[/bold] {'Dry Run' if dry_run else 'Live Deployment'}\n",
        title=title,
        border_style="yellow" if dry_run else "cyan",
    )
    
    console.print("\n")
    console.print(summary)
    
    # Show configuration highlights
    console.print("\n[bold]Configuration Highlights:[/bold]")
    
    k8s_config = config.get("k8s_config", {})
    if k8s_config.get("autoscaling", {}).get("enabled"):
        autoscale = k8s_config["autoscaling"]
        console.print(f"  • Auto-scaling: {autoscale['min_replicas']} → {autoscale['max_replicas']} pods")
    
    security = config.get("security", {})
    security_features = []
    if security.get("network_policies"):
        security_features.append("Network Policies")
    if security.get("secrets_encryption"):
        security_features.append("Secrets Encryption")
    if security_features:
        console.print(f"  • Security: {', '.join(security_features)}")
    
    monitoring = config.get("monitoring", {})
    if monitoring.get("enabled"):
        console.print(f"  • Monitoring: Metrics, Logs, Tracing enabled")


def _show_dry_run_results(response: Dict[str, Any]):
    """Show dry run results."""
    
    results = response.get("results", {})
    if not results:
        return
    
    console.print("\n[bold]Dry Run Results:[/bold]")
    
    resources = results.get("resources_to_create", [])
    if resources:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Resource Type", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Namespace", style="yellow")
        
        for resource in resources[:10]:  # Show first 10
            table.add_row(
                resource.get("kind", "Unknown"),
                resource.get("name", "Unknown"),
                resource.get("namespace", "default"),
            )
        
        if len(resources) > 10:
            table.add_row("...", f"+{len(resources) - 10} more", "...")
        
        console.print(table)
    
    # Show estimated resources
    estimates = results.get("resource_estimates", {})
    if estimates:
        console.print("\n[bold]Resource Estimates:[/bold]")
        console.print(f"  • CPU: {estimates.get('cpu', 'N/A')}")
        console.print(f"  • Memory: {estimates.get('memory', 'N/A')}")
        console.print(f"  • Storage: {estimates.get('storage', 'N/A')}")


@deploy_group.command(name="cloud")
@click.argument("suite_id")
@click.option(
    "--provider",
    "-p",
    type=click.Choice(["aws", "azure", "gcp", "multi"]),
    required=True,
    help="Cloud provider",
)
@click.option(
    "--region",
    "-r",
    type=str,
    default="us-east-1",
    help="Cloud region",
)
@click.option(
    "--environment",
    "-e",
    type=click.Choice(["production", "staging", "development"]),
    default="production",
    help="Environment type",
)
@click.option(
    "--config-file",
    "-f",
    type=click.Path(exists=True),
    help="Cloud deployment configuration",
)
@click.option(
    "--estimate-cost",
    is_flag=True,
    help="Show cost estimate before deploying",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Dry run deployment",
)
def deploy_cloud(
    suite_id: str,
    provider: str,
    region: str,
    environment: str,
    config_file: Optional[str],
    estimate_cost: bool,
    dry_run: bool,
):
    """Deploy to cloud provider."""
    
    try:
        api_client = get_api_client()
        
        # Load or create configuration
        if config_file:
            with spinner("Loading configuration..."):
                deployment_config = _load_deployment_config(config_file)
        else:
            deployment_config = _create_cloud_config(provider, region, environment)
        
        # Get cost estimate if requested
        if estimate_cost:
            with spinner("Calculating cost estimate..."):
                cost_estimate = _get_deployment_cost(api_client, suite_id, deployment_config)
                _show_cost_estimate(cost_estimate)
            
            if not click.confirm("Continue with deployment?", default=False):
                console.print("[yellow]Deployment cancelled.[/yellow]")
                return
        
        # Validate configuration
        with spinner("Validating cloud deployment..."):
            validation = api_client.post("/deployments/cloud/validate", json=deployment_config)
            
            if not validation.get("valid", False):
                console.print("[red]Cloud deployment validation failed:[/red]")
                for error in validation.get("errors", []):
                    console.print(f"  • {error}")
                return
        
        # Show deployment summary
        _show_cloud_deployment_summary(suite_id, deployment_config, dry_run)
        
        # Confirm deployment
        if not dry_run:
            if not click.confirm("Proceed with cloud deployment?", default=False):
                console.print("[yellow]Deployment cancelled.[/yellow]")
                return
        
        # Deploy
        with spinner(f"Deploying to {provider.upper()}..."):
            deploy_payload = {
                "suite_id": suite_id,
                "provider": provider,
                "configuration": deployment_config,
                "dry_run": dry_run,
            }
            
            response = api_client.post("/deployments/cloud", json=deploy_payload)
            deployment_id = response.get("deployment_id")
            
            if dry_run:
                console.print("[green]✅ Cloud deployment dry run completed[/green]")
                _show_cloud_dry_run_results(response, provider)
                return
            
            console.print(f"[green]✅ Cloud deployment initiated: {deployment_id}[/green]")
        
        console.print(f"\n[bold]Next steps:[/bold]")
        console.print(f"1. Run [cyan]deploy status {deployment_id}[/cyan] to check progress")
        console.print(f"2. Run [cyan]deploy monitor {deployment_id}[/cyan] for cloud monitoring")
        console.print(f"3. Run [cyan]deploy cost {deployment_id}[/cyan] for cost tracking")
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _create_cloud_config(
    provider: str,
    region: str,
    environment: str,
) -> Dict[str, Any]:
    """Create cloud deployment configuration."""
    
    base_config = {
        "provider": provider,
        "region": region,
        "environment": environment,
        "auto_scaling": True,
        "high_availability": environment == "production",
        "backup_enabled": True,
        "monitoring_enabled": True,
        "security_groups": [],
    }
    
    # Provider-specific configurations
    if provider == "aws":
        base_config.update({
            "instance_type": "t3.medium",
            "instance_count": 3 if environment == "production" else 2,
            "vpc_id": "auto",
            "subnets": "auto",
            "load_balancer": {
                "type": "application",
                "ssl_certificate": "auto",
            },
            "rds": {
                "enabled": True,
                "instance_class": "db.t3.small",
                "multi_az": environment == "production",
            },
            "s3": {
                "enabled": True,
                "bucket_name": f"microagents-{environment}-{region}",
            },
        })
    
    elif provider == "azure":
        base_config.update({
            "vm_size": "Standard_B2s",
            "vm_count": 3 if environment == "production" else 2,
            "vnet_name": f"vnet-microagents-{environment}",
            "load_balancer": {
                "sku": "Standard",
            },
            "database": {
                "enabled": True,
                "sku": "Basic",
                "zone_redundant": environment == "production",
            },
            "storage": {
                "enabled": True,
                "account_name": f"microagents{environment}",
            },
        })
    
    elif provider == "gcp":
        base_config.update({
            "machine_type": "e2-medium",
            "instance_count": 3 if environment == "production" else 2,
            "network": "default",
            "load_balancer": {
                "type": "external",
            },
            "cloud_sql": {
                "enabled": True,
                "tier": "db-f1-micro",
                "availability_type": "ZONAL",
            },
            "cloud_storage": {
                "enabled": True,
                "bucket_name": f"microagents-{environment}-{region}",
            },
        })
    
    return base_config


def _get_deployment_cost(
    api_client: Any,
    suite_id: str,
    config: Dict[str, Any],
) -> DeploymentCostEstimate:
    """Get deployment cost estimate."""
    
    response = api_client.post("/deployments/cost/estimate", json={
        "suite_id": suite_id,
        "configuration": config,
    })
    
    return DeploymentCostEstimate(**response)


def _show_cost_estimate(cost_estimate: DeploymentCostEstimate):
    """Show deployment cost estimate."""
    
    console.print("\n[bold]💰 Deployment Cost Estimate[/bold]")
    
    summary = Table(show_header=False)
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", style="green", justify="right")
    
    summary.add_row("Monthly Cost", f"${cost_estimate.monthly_cost:.2f}")
    summary.add_row("Hourly Cost", f"${cost_estimate.hourly_cost:.4f}")
    
    console.print(summary)
    
    # Cost breakdown
    console.print("\n[bold]Cost Breakdown:[/bold]")
    breakdown_table = Table(show_header=True, header_style="bold")
    breakdown_table.add_column("Category", style="cyan")
    breakdown_table.add_column("Monthly Cost", style="green", justify="right")
    breakdown_table.add_column("Percentage", style="yellow", justify="right")
    
    total = cost_estimate.monthly_cost
    for category, amount in cost_estimate.cost_breakdown.items():
        percentage = (amount / total * 100) if total > 0 else 0
        breakdown_table.add_row(
            category.replace("_", " ").title(),
            f"${amount:.2f}",
            f"{percentage:.1f}%",
        )
    
    console.print(breakdown_table)
    
    # Recommendations
    if cost_estimate.recommendations:
        console.print("\n[bold]💡 Cost Optimization Recommendations:[/bold]")
        for rec in cost_estimate.recommendations[:3]:  # Show top 3
            savings = rec.get("potential_savings", 0)
            console.print(f"  • {rec.get('description')} - Potential savings: ${savings:.2f}/month")


def _show_cloud_deployment_summary(
    suite_id: str,
    config: Dict[str, Any],
    dry_run: bool,
):
    """Show cloud deployment summary."""
    
    provider = config.get("provider", "unknown").upper()
    title = f"☁️  {provider} Deployment Summary"
    if dry_run:
        title = f"🔍 {provider} Dry Run Summary"
    
    summary = Panel.fit(
        f"[bold]Suite:[/bold] {suite_id}\n"
        f"[bold]Provider:[/bold] {provider}\n"
        f"[bold]Region:[/bold] {config.get('region', 'Unknown')}\n"
        f"[bold]Environment:[/bold] {config.get('environment', 'production')}\n"
        f"[bold]Mode:[/bold] {'Dry Run' if dry_run else 'Live Deployment'}\n",
        title=title,
        border_style="yellow" if dry_run else {
            "aws": "orange",
            "azure": "blue",
            "gcp": "green",
            "multi": "cyan",
        }.get(config.get("provider", ""), "white"),
    )
    
    console.print("\n")
    console.print(summary)
    
    # Show configuration highlights
    console.print("\n[bold]Configuration Highlights:[/bold]")
    
    if config.get("provider") == "aws":
        console.print(f"  • Instance Type: {config.get('instance_type', 't3.medium')}")
        console.print(f"  • Instance Count: {config.get('instance_count', 2)}")
        if config.get("rds", {}).get("enabled"):
            console.print(f"  • Database: RDS {config['rds'].get('instance_class', 'db.t3.small')}")
    
    elif config.get("provider") == "azure":
        console.print(f"  • VM Size: {config.get('vm_size', 'Standard_B2s')}")
        console.print(f"  • VM Count: {config.get('vm_count', 2)}")
    
    elif config.get("provider") == "gcp":
        console.print(f"  • Machine Type: {config.get('machine_type', 'e2-medium')}")
        console.print(f"  • Instance Count: {config.get('instance_count', 2)}")
    
    if config.get("auto_scaling"):
        console.print("  • Auto-scaling: Enabled")
    
    if config.get("high_availability"):
        console.print("  • High Availability: Enabled")
    
    if config.get("backup_enabled"):
        console.print("  • Backup: Enabled")


def _show_cloud_dry_run_results(
    response: Dict[str, Any],
    provider: str,
):
    """Show cloud dry run results."""
    
    results = response.get("results", {})
    if not results:
        return
    
    console.print(f"\n[bold]{provider.upper()} Dry Run Results:[/bold]")
    
    # Resources to create
    resources = results.get("resources_to_create", [])
    if resources:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Resource Type", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Details", style="yellow")
        
        for resource in resources[:10]:
            table.add_row(
                resource.get("type", "Unknown"),
                resource.get("name", "Unknown"),
                resource.get("description", ""),
            )
        
        if len(resources) > 10:
            table.add_row("...", f"+{len(resources) - 10} more", "...")
        
        console.print(table)
    
    # Estimated costs
    costs = results.get("estimated_costs", {})
    if costs:
        console.print("\n[bold]Estimated Monthly Costs:[/bold]")
        for service, cost in costs.items():
            if cost > 0:
                console.print(f"  • {service}: ${cost:.2f}")


@deploy_group.command(name="validate")
@click.argument("config_file", type=click.Path(exists=True))
@click.option(
    "--target",
    "-t",
    type=click.Choice(list(DeploymentTarget.__iter__())),
    help="Deployment target type",
)
@click.option(
    "--strict",
    "-s",
    is_flag=True,
    help="Strict validation (fail on warnings)",
)
def validate_deployment(
    config_file: str,
    target: Optional[str],
    strict: bool,
):
    """Validate deployment configuration."""
    
    try:
        api_client = get_api_client()
        
        with spinner("Loading configuration..."):
            config = _load_deployment_config(config_file)
            
            # Override target if specified
            if target:
                config["target"] = target
        
        # Validate configuration
        with spinner("Validating deployment configuration..."):
            validation_payload = {
                "configuration": config,
                "strict": strict,
            }
            
            response = api_client.post("/deployments/validate", json=validation_payload)
        
        # Display validation results
        _show_validation_results(response, config_file)
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _load_deployment_config(config_file: str) -> Dict[str, Any]:
    """Load deployment configuration from file."""
    
    path = Path(config_file)
    
    if path.suffix.lower() in [".yaml", ".yml"]:
        with open(path) as f:
            return yaml.safe_load(f)
    else:
        # Assume JSON
        with open(path) as f:
            return json.load(f)


def _show_validation_results(
    response: Dict[str, Any],
    config_file: str,
):
    """Show deployment validation results."""
    
    is_valid = response.get("valid", False)
    warnings = response.get("warnings", [])
    errors = response.get("errors", [])
    
    # Summary panel
    if is_valid and not errors:
        border_style = "green"
        title = "✅ Validation Passed"
        status_message = "Configuration is valid for deployment"
    else:
        border_style = "red"
        title = "❌ Validation Failed"
        status_message = f"Found {len(errors)} error(s)"
    
    summary = Panel.fit(
        f"[bold]Configuration:[/bold] {config_file}\n"
        f"[bold]Status:[/bold] {status_message}\n"
        f"[bold]Warnings:[/bold] {len(warnings)}\n"
        f"[bold]Errors:[/bold] {len(errors)}",
        title=title,
        border_style=border_style,
    )
    
    console.print("\n")
    console.print(summary)
    
    # Show errors
    if errors:
        console.print("\n[bold]Errors:[/bold]")
        for error in errors:
            console.print(f"  • [red]{error}[/red]")
    
    # Show warnings
    if warnings:
        console.print("\n[bold]Warnings:[/bold]")
        for warning in warnings:
            console.print(f"  • [yellow]{warning}[/yellow]")
    
    # Show recommendations
    recommendations = response.get("recommendations", [])
    if recommendations:
        console.print("\n[bold]💡 Recommendations:[/bold]")
        for rec in recommendations:
            console.print(f"  • {rec}")


@deploy_group.command(name="status")
@click.argument("deployment_id")
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    help="Watch mode - continuously update status",
)
@click.option(
    "--refresh",
    "-r",
    type=int,
    default=5,
    help="Refresh interval in seconds (watch mode only)",
)
def deployment_status(
    deployment_id: str,
    watch: bool,
    refresh: int,
):
    """Check deployment status."""
    
    try:
        api_client = get_api_client()
        
        if watch:
            _watch_deployment_status(deployment_id, refresh)
        else:
            _show_deployment_status(deployment_id)
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _show_deployment_status(deployment_id: str):
    """Show current deployment status."""
    
    with spinner(f"Fetching status for deployment {deployment_id}..."):
        api_client = get_api_client()
        status_data = api_client.get(f"/deployments/{deployment_id}/status")
        
        if not status_data:
            console.print(f"[red]Deployment {deployment_id} not found[/red]")
            return
        
        status = DeploymentStatus(**status_data)
    
    # Create status display
    _display_deployment_status(status)


def _display_deployment_status(status: DeploymentStatus):
    """Display deployment status."""
    
    # Status indicator
    status_colors = {
        "pending": "yellow",
        "in_progress": "blue",
        "completed": "green",
        "failed": "red",
        "rolled_back": "orange",
    }
    
    status_color = status_colors.get(status.status, "white")
    status_indicator = {
        "pending": "🟡",
        "in_progress": "🔵",
        "completed": "🟢",
        "failed": "🔴",
        "rolled_back": "🟠",
    }.get(status.status, "⚪")
    
    # Main status panel
    status_panel = Panel.fit(
        f"{status_indicator} [bold]{status.deployment_id}[/bold]\n"
        f"Status: [{status_color}]{status.status}[/{status_color}]\n"
        f"Phase: {status.phase}\n"
        f"Progress: {status.progress}%\n",
        title="Deployment Status",
        border_style=status_color,
    )
    
    console.print("\n")
    console.print(status_panel)
    
    # Resources table
    resources_table = Table(show_header=True, header_style="bold")
    resources_table.add_column("Resource", style="cyan")
    resources_table.add_column("Status", style="green", justify="center")
    resources_table.add_column("Details", style="white")
    
    pods_ready, pods_total = map(int, status.pods_ready.split('/'))
    resources_table.add_row(
        "Pods",
        "✅" if pods_ready == pods_total else "🟡" if pods_ready > 0 else "🔴",
        f"{status.pods_ready} ready",
    )
    
    resources_table.add_row(
        "Services",
        "✅" if status.services_ready == status.services_total else "🟡",
        f"{status.services_ready}/{status.services_total} ready",
    )
    
    resources_table.add_row(
        "Ingress",
        "✅" if status.ingress_ready else "🟡",
        "Ready" if status.ingress_ready else "Configuring",
    )
    
    resources_table.add_row(
        "Health Checks",
        "✅" if status.health_checks_passed == status.health_checks_total else "🟡",
        f"{status.health_checks_passed}/{status.health_checks_total} passed",
    )
    
    console.print(resources_table)
    
    # Events
    if status.events:
        console.print("\n[bold]Recent Events:[/bold]")
        for event in status.events[-5:]:  # Last 5 events
            timestamp = event.get("timestamp", "").split("T")[1].split(".")[0]
            console.print(f"  [{timestamp}] {event.get('message', '')}")
    
    # Estimated completion
    if status.estimated_completion:
        try:
            completion_time = datetime.fromisoformat(status.estimated_completion.replace('Z', '+00:00'))
            remaining = completion_time - datetime.utcnow()
            
            if remaining.total_seconds() > 0:
                minutes = int(remaining.total_seconds() / 60)
                console.print(f"\n[dim]Estimated completion: {minutes} minutes[/dim]")
        except:
            pass


def _watch_deployment_status(deployment_id: str, refresh_interval: int):
    """Watch deployment status with live updates."""
    
    api_client = get_api_client()
    
    try:
        with Live(refresh_per_second=1, console=console) as live:
            while True:
                try:
                    status_data = api_client.get(f"/deployments/{deployment_id}/status")
                    if not status_data:
                        live.update(Panel.fit(
                            f"[red]Deployment {deployment_id} not found[/red]",
                            title="Error",
                            border_style="red",
                        ))
                        break
                    
                    status = DeploymentStatus(**status_data)
                    
                    # Create live display
                    display = _create_live_status_display(status)
                    live.update(display)
                    
                    # Stop if deployment is complete
                    if status.status in ["completed", "failed", "rolled_back"]:
                        time.sleep(2)  # Show final state briefly
                        break
                    
                    time.sleep(refresh_interval)
                    
                except KeyboardInterrupt:
                    console.print("\n[yellow]Monitoring stopped[/yellow]")
                    break
                except Exception as e:
                    live.update(Panel.fit(
                        f"[red]Error: {str(e)}[/red]",
                        title="Error",
                        border_style="red",
                    ))
                    time.sleep(refresh_interval)
    
    except Exception as e:
        console.print(f"[red]Error in watch mode: {str(e)}[/red]")


def _create_live_status_display(status: DeploymentStatus) -> Panel:
    """Create live status display panel."""
    
    status_colors = {
        "pending": "yellow",
        "in_progress": "blue",
        "completed": "green",
        "failed": "red",
        "rolled_back": "orange",
    }
    
    status_color = status_colors.get(status.status, "white")
    
    # Progress bar
    progress_bar = f"[{'█' * (status.progress // 2)}{'░' * (50 - status.progress // 2)}] {status.progress}%"
    
    content = f"""
[bold]{status.deployment_id}[/bold]
Status: [{status_color}]{status.status}[/{status_color}]
Phase: {status.phase}
Progress: {progress_bar}

Pods: {status.pods_ready} ready
Services: {status.services_ready}/{status.services_total}
Health Checks: {status.health_checks_passed}/{status.health_checks_total}

[dim]Last updated: {datetime.utcnow().strftime('%H:%M:%S')}[/dim]
"""
    
    return Panel.fit(
        content,
        title="🚀 Deployment Monitor",
        border_style=status_color,
    )


@deploy_group.command(name="rollback")
@click.argument("deployment_id")
@click.option(
    "--to-version",
    "-v",
    type=str,
    help="Specific version to rollback to",
)
@click.option(
    "--reason",
    "-r",
    type=str,
    help="Reason for rollback",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Dry run rollback",
)
@click.option(
    "--wait",
    "-w",
    is_flag=True,
    help="Wait for rollback to complete",
)
def rollback_deployment(
    deployment_id: str,
    to_version: Optional[str],
    reason: Optional[str],
    dry_run: bool,
    wait: bool,
):
    """Rollback a deployment."""
    
    try:
        api_client = get_api_client()
        
        # Get deployment info
        with spinner(f"Fetching deployment {deployment_id}..."):
            deployment = api_client.get(f"/deployments/{deployment_id}")
            
            if not deployment:
                console.print(f"[red]Deployment {deployment_id} not found[/red]")
                return
            
            if deployment.get("status") not in ["in_progress", "completed"]:
                console.print(f"[yellow]Deployment must be in_progress or completed to rollback[/yellow]")
                return
        
        # Get available versions
        with spinner("Fetching available versions..."):
            versions = api_client.get(f"/deployments/{deployment_id}/versions")
            
            if not to_version:
                # Show version selection
                version_choices = [v["version"] for v in versions.get("versions", [])]
                
                if not version_choices:
                    console.print("[yellow]No previous versions available for rollback[/yellow]")
                    return
                
                to_version = questionary.select(
                    "Select version to rollback to:",
                    choices=version_choices,
                ).ask()
                
                if not to_version:
                    console.print("[yellow]Rollback cancelled[/yellow]")
                    return
        
        # Show rollback summary
        _show_rollback_summary(deployment_id, to_version, reason, dry_run)
        
        # Confirm rollback
        if not dry_run:
            if not click.confirm("Proceed with rollback?", default=False):
                console.print("[yellow]Rollback cancelled[/yellow]")
                return
        
        # Execute rollback
        with spinner("Initiating rollback..."):
            rollback_payload = {
                "deployment_id": deployment_id,
                "to_version": to_version,
                "reason": reason or "Manual rollback via CLI",
                "dry_run": dry_run,
            }
            
            response = api_client.post("/deployments/rollback", json=rollback_payload)
            rollback_id = response.get("rollback_id")
            
            if dry_run:
                console.print("[green]✅ Rollback dry run completed[/green]")
                _show_rollback_dry_run_results(response)
                return
            
            console.print(f"[green]✅ Rollback initiated: {rollback_id}[/green]")
        
        # Wait for rollback if requested
        if wait:
            _monitor_rollback(rollback_id)
        else:
            console.print(f"\n[bold]Next steps:[/bold]")
            console.print(f"1. Run [cyan]deploy status {rollback_id}[/cyan] to check rollback progress")
            console.print(f"2. Run [cyan]deploy monitor {rollback_id}[/cyan] for real-time monitoring")
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _show_rollback_summary(
    deployment_id: str,
    to_version: str,
    reason: Optional[str],
    dry_run: bool,
):
    """Show rollback summary."""
    
    title = "↩️  Rollback Summary"
    if dry_run:
        title = "🔍 Rollback Dry Run"
    
    summary = Panel.fit(
        f"[bold]Deployment:[/bold] {deployment_id}\n"
        f"[bold]Rollback to:[/bold] {to_version}\n"
        f"[bold]Reason:[/bold] {reason or 'Not specified'}\n"
        f"[bold]Mode:[/bold] {'Dry Run' if dry_run else 'Live Rollback'}\n",
        title=title,
        border_style="yellow" if dry_run else "orange",
    )
    
    console.print("\n")
    console.print(summary)


def _show_rollback_dry_run_results(response: Dict[str, Any]):
    """Show rollback dry run results."""
    
    results = response.get("results", {})
    if not results:
        return
    
    console.print("\n[bold]Rollback Dry Run Results:[/bold]")
    
    changes = results.get("changes", [])
    if changes:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Resource", style="cyan")
        table.add_column("Action", style="yellow")
        table.add_column("Details", style="white")
        
        for change in changes[:10]:
            table.add_row(
                change.get("resource", "Unknown"),
                change.get("action", "Unknown"),
                change.get("details", ""),
            )
        
        if len(changes) > 10:
            table.add_row("...", "...", f"+{len(changes) - 10} more changes")
        
        console.print(table)
    
    # Impact analysis
    impact = results.get("impact_analysis", {})
    if impact:
        console.print("\n[bold]Impact Analysis:[/bold]")
        console.print(f"  • Affected pods: {impact.get('affected_pods', 0)}")
        console.print(f"  • Estimated downtime: {impact.get('estimated_downtime', 'Unknown')}")
        
        risks = impact.get("risks", [])
        if risks:
            console.print("\n[bold]Potential Risks:[/bold]")
            for risk in risks[:3]:
                console.print(f"  • {risk}")


def _monitor_rollback(rollback_id: str):
    """Monitor rollback progress."""
    
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
            f"[cyan]Monitoring rollback {rollback_id}...",
            total=100,
        )
        
        while time.time() - start_time < 300:  # 5 minute timeout
            try:
                status = api_client.get(f"/rollbacks/{rollback_id}/status")
                
                if not status:
                    progress.console.print("[red]Rollback not found[/red]")
                    break
                
                rollback_status = status.get("status", "unknown")
                progress_value = status.get("progress", 0)
                
                progress.update(task, completed=progress_value)
                
                # Show status changes
                if rollback_status != "in_progress":
                    progress.console.print(f"[yellow]Status: {rollback_status}[/yellow]")
                    
                    if rollback_status == "completed":
                        progress.console.print("[green]✅ Rollback completed successfully[/green]")
                        break
                    elif rollback_status == "failed":
                        progress.console.print("[red]❌ Rollback failed[/red]")
                        error = status.get("error", "Unknown error")
                        progress.console.print(f"[red]Error: {error}[/red]")
                        break
                
                time.sleep(2)
                
            except Exception as e:
                progress.console.print(f"[red]Error monitoring rollback: {str(e)}[/red]")
                time.sleep(5)
        
        if time.time() - start_time >= 300:
            progress.console.print("[yellow]⚠️  Monitoring timeout reached[/yellow]")


@deploy_group.command(name="scale")
@click.argument("deployment_id")
@click.option(
    "--replicas",
    "-r",
    type=int,
    required=True,
    help="New number of replicas",
)
@click.option(
    "--wait",
    "-w",
    is_flag=True,
    help="Wait for scaling to complete",
)
def scale_deployment(
    deployment_id: str,
    replicas: int,
    wait: bool,
):
    """Scale deployment replicas."""
    
    try:
        api_client = get_api_client()
        
        # Get current deployment
        with spinner(f"Fetching deployment {deployment_id}..."):
            deployment = api_client.get(f"/deployments/{deployment_id}")
            
            if not deployment:
                console.print(f"[red]Deployment {deployment_id} not found[/red]")
                return
            
            current_replicas = deployment.get("configuration", {}).get("replicas", 1)
        
        # Show scaling summary
        _show_scaling_summary(deployment_id, current_replicas, replicas)
        
        # Confirm scaling
        if not click.confirm("Proceed with scaling?", default=False):
            console.print("[yellow]Scaling cancelled[/yellow]")
            return
        
        # Scale deployment
        with spinner("Scaling deployment..."):
            scale_payload = {
                "deployment_id": deployment_id,
                "replicas": replicas,
            }
            
            response = api_client.post("/deployments/scale", json=scale_payload)
            operation_id = response.get("operation_id")
            
            console.print(f"[green]✅ Scaling initiated: {operation_id}[/green]")
        
        # Wait for scaling if requested
        if wait:
            _monitor_scaling(operation_id)
        else:
            console.print(f"\n[bold]Next steps:[/bold]")
            console.print(f"1. Run [cyan]deploy status {deployment_id}[/cyan] to check scaling progress")
            console.print(f"2. Run [cyan]deploy monitor {deployment_id}[/cyan] for real-time monitoring")
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _show_scaling_summary(
    deployment_id: str,
    current_replicas: int,
    new_replicas: int,
):
    """Show scaling summary."""
    
    change = new_replicas - current_replicas
    direction = "up" if change > 0 else "down"
    
    summary = Panel.fit(
        f"[bold]Deployment:[/bold] {deployment_id}\n"
        f"[bold]Current Replicas:[/bold] {current_replicas}\n"
        f"[bold]New Replicas:[/bold] {new_replicas}\n"
        f"[bold]Change:[/bold] {direction} by {abs(change)} pods\n",
        title="📈 Scaling Operation",
        border_style="green" if change > 0 else "yellow",
    )
    
    console.print("\n")
    console.print(summary)
    
    # Show impact
    console.print("\n[bold]Expected Impact:[/bold]")
    if change > 0:
        console.print(f"  • Increased capacity by {change} pods")
        console.print(f"  • Better load distribution")
        console.print(f"  • Higher resource consumption")
    else:
        console.print(f"  • Reduced capacity by {abs(change)} pods")
        console.print(f"  • Lower resource consumption")
        console.print(f"  • Potential performance impact")


def _monitor_scaling(operation_id: str):
    """Monitor scaling operation."""
    
    api_client = get_api_client()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        
        task = progress.add_task(
            f"[cyan]Monitoring scaling operation {operation_id}...",
            total=100,
        )
        
        for _ in range(30):  # Max 30 checks
            try:
                status = api_client.get(f"/operations/{operation_id}")
                
                if not status:
                    progress.console.print("[red]Operation not found[/red]")
                    break
                
                op_status = status.get("status", "unknown")
                progress_value = status.get("progress", 0)
                
                progress.update(task, completed=progress_value)
                
                if op_status == "completed":
                    progress.console.print("[green]✅ Scaling completed successfully[/green]")
                    break
                elif op_status == "failed":
                    progress.console.print("[red]❌ Scaling failed[/red]")
                    error = status.get("error", "Unknown error")
                    progress.console.print(f"[red]Error: {error}[/red]")
                    break
                
                time.sleep(2)
                
            except Exception as e:
                progress.console.print(f"[red]Error monitoring scaling: {str(e)}[/red]")
                time.sleep(5)


@deploy_group.command(name="upgrade")
@click.argument("deployment_id")
@click.option(
    "--version",
    "-v",
    type=str,
    required=True,
    help="New version to upgrade to",
)
@click.option(
    "--strategy",
    "-s",
    type=click.Choice(["rolling", "blue-green", "canary"]),
    default="rolling",
    help="Upgrade strategy",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Dry run upgrade",
)
@click.option(
    "--wait",
    "-w",
    is_flag=True,
    help="Wait for upgrade to complete",
)
def upgrade_deployment(
    deployment_id: str,
    version: str,
    strategy: str,
    dry_run: bool,
    wait: bool,
):
    """Upgrade deployment to new version."""
    
    console.print(f"[yellow]Upgrading {deployment_id} to version {version} using {strategy} strategy[/yellow]")
    # Implementation would handle version upgrades


@deploy_group.command(name="monitor")
@click.argument("deployment_id")
@click.option(
    "--metrics",
    "-m",
    multiple=True,
    type=click.Choice(["cpu", "memory", "requests", "latency", "errors", "all"]),
    default=["all"],
    help="Metrics to monitor",
)
@click.option(
    "--duration",
    "-d",
    type=int,
    default=300,
    help="Monitoring duration in seconds",
)
@click.option(
    "--refresh",
    "-r",
    type=int,
    default=5,
    help="Refresh interval in seconds",
)
def monitor_deployment(
    deployment_id: str,
    metrics: List[str],
    duration: int,
    refresh: int,
):
    """Monitor deployment metrics in real-time."""
    
    console.print(f"[yellow]Monitoring {deployment_id} for {duration} seconds[/yellow]")
    # Implementation would show real-time metrics dashboard


@deploy_group.command(name="cost")
@click.argument("deployment_id")
@click.option(
    "--period",
    "-p",
    type=click.Choice(["hour", "day", "week", "month", "year"]),
    default="month",
    help="Cost period",
)
@click.option(
    "--detailed",
    "-d",
    is_flag=True,
    help="Detailed cost breakdown",
)
def deployment_cost(
    deployment_id: str,
    period: str,
    detailed: bool,
):
    """Show deployment cost information."""
    
    try:
        api_client = get_api_client()
        
        with spinner(f"Fetching cost data for {deployment_id}..."):
            cost_data = api_client.get(f"/deployments/{deployment_id}/cost", params={
                "period": period,
                "detailed": detailed,
            })
            
            if not cost_data:
                console.print(f"[yellow]No cost data available for {deployment_id}[/yellow]")
                return
            
            _show_deployment_cost(cost_data, deployment_id, period)
        
    except APIError as e:
        handle_api_error(e)
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")


def _show_deployment_cost(
    cost_data: Dict[str, Any],
    deployment_id: str,
    period: str,
):
    """Show deployment cost information."""
    
    period_display = {
        "hour": "hourly",
        "day": "daily",
        "week": "weekly",
        "month": "monthly",
        "year": "yearly",
    }.get(period, period)
    
    console.print(f"\n[bold]💰 Deployment Costs - {period_display.title()}[/bold]")
    console.print(f"Deployment: {deployment_id}")
    console.print("=" * 40)
    
    # Summary
    total_cost = cost_data.get("total_cost", 0)
    console.print(f"\n[bold]Total Cost:[/bold] ${total_cost:.2f}")
    
    # Breakdown
    breakdown = cost_data.get("breakdown", {})
    if breakdown:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Category", style="cyan")
        table.add_column("Cost", style="green", justify="right")
        table.add_column("Percentage", style="yellow", justify="right")
        
        for category, cost in breakdown.items():
            percentage = (cost / total_cost * 100) if total_cost > 0 else 0
            table.add_row(
                category.replace("_", " ").title(),
                f"${cost:.2f}",
                f"{percentage:.1f}%",
            )
        
        console.print(table)
    
    # Trends
    trends = cost_data.get("trends", {})
    if trends:
        console.print("\n[bold]📈 Cost Trends:[/bold]")
        
        change = trends.get("change_percentage", 0)
        if change > 0:
            trend_text = f"[red]↑ {change:.1f}% increase[/red]"
        elif change < 0:
            trend_text = f"[green]↓ {abs(change):.1f}% decrease[/green]"
        else:
            trend_text = "→ No change"
        
        console.print(f"  • Compared to last period: {trend_text}")
        
        forecast = trends.get("forecast", 0)
        if forecast:
            console.print(f"  • Next period forecast: ${forecast:.2f}")
    
    # Recommendations
    recommendations = cost_data.get("recommendations", [])
    if recommendations:
        console.print("\n[bold]💡 Cost Optimization Opportunities:[/bold]")
        for rec in recommendations[:3]:
            savings = rec.get("potential_savings", 0)
            console.print(f"  • {rec.get('description')} - Save ${savings:.2f}/{period}")


@deploy_group.command(name="secure")
@click.argument("deployment_id")
@click.option(
    "--scan",
    "-s",
    is_flag=True,
    help="Run security scan",
)
@click.option(
    "--apply",
    "-a",
    is_flag=True,
    help="Apply security recommendations",
)
@click.option(
    "--compliance",
    "-c",
    type=click.Choice(["soc2", "hipaa", "gdpr", "pci", "all"]),
    help="Check compliance standards",
)
def secure_deployment(
    deployment_id: str,
    scan: bool,
    apply: bool,
    compliance: Optional[str],
):
    """Apply security configurations to deployment."""
    
    console.print(f"[yellow]Securing deployment {deployment_id}[/yellow]")
    # Implementation would apply security configurations


# Helper function used by other commands
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
        
        while time.time() - start_time < timeout:
            try:
                status_data = api_client.get(f"/deployments/{deployment_id}/status")
                
                if not status_data:
                    progress.console.print("[red]Deployment not found[/red]")
                    break
                
                status = DeploymentStatus(**status_data)
                progress.update(task, completed=status.progress)
                
                # Check if deployment is complete
                if status.status in ["completed", "failed", "rolled_back"]:
                    progress.update(task, completed=100)
                    
                    if status.status == "completed":
                        progress.console.print("[green]✅ Deployment completed successfully![/green]")
                    elif status.status == "failed":
                        progress.console.print("[red]❌ Deployment failed![/red]")
                        # Show error details if available
                        error = status_data.get("error")
                        if error:
                            progress.console.print(f"[red]Error: {error}[/red]")
                    elif status.status == "rolled_back":
                        progress.console.print("[yellow]⚠️  Deployment rolled back[/yellow]")
                        reason = status_data.get("rollback_reason")
                        if reason:
                            progress.console.print(f"[yellow]Reason: {reason}[/yellow]")
                    
                    break
                
                time.sleep(2)
                
            except Exception as e:
                progress.console.print(f"[red]Error monitoring deployment: {str(e)}[/red]")
                time.sleep(5)
        
        if time.time() - start_time >= timeout:
            progress.console.print("[yellow]⚠️  Monitoring timeout reached[/yellow]")


# Export commands
__all__ = [
    "deploy_group",
    "DeploymentTarget",
    "DeploymentStrategy",
    "DeploymentConfiguration",
    "DeploymentStatus",
    "DeploymentCostEstimate",
]