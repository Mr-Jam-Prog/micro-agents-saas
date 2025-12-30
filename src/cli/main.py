"""
CLI principal pour MicroAgents Platform.
Interface en ligne de commande complète avec Typer et Rich.
"""

import os
import sys
import json
import yaml
import asyncio
import typer
import questionary
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path
from enum import Enum

import typer
from typer import Typer, Context
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.columns import Columns
from rich.syntax import Syntax
from rich.prompt import Prompt, Confirm
from rich.markdown import Markdown
from rich import box

from src.cli.commands.agent import app as agent_app
from src.cli.commands.suite import app as suite_app
from src.cli.commands.roi import app as roi_app
from src.cli.commands.deploy import app as deploy_app
from src.cli.commands.monitor import app as monitor_app
from src.cli.commands.registry import app as registry_app
from src.cli.plugins.plugin_manager import PluginManager
from src.cli.utils.progress import CustomProgress
from src.cli.utils.formatting import format_table, format_tree, format_json, format_yaml
from src.cli.utils.interactive import InteractivePrompt
from src.cli.themes.dark_theme import DarkTheme
from src.core.base.registry import AgentRegistry
from src.core.suites.base import SuiteManager
from src.core.business_value.calculator import ROICalculator

# Initialisation des composants
console = Console(theme=DarkTheme())
app = Typer(help="🚀 MicroAgents Platform CLI - Gestion de 1400 agents DevOps", rich_markup_mode="rich")

# Sous-commandes
app.add_typer(agent_app, name="agent", help="Gestion des agents individuels")
app.add_typer(suite_app, name="suite", help="Gestion des suites commerciales")
app.add_typer(roi_app, name="roi", help="Calculateur de ROI et analyse business")
app.add_typer(deploy_app, name="deploy", help="Déploiement et infrastructure")
app.add_typer(monitor_app, name="monitor", help="Monitoring et observabilité")
app.add_typer(registry_app, name="registry", help="Registre et catalogue d'agents")

# Version
__version__ = "1.0.0"

# Configuration
CONFIG_DIR = Path.home() / ".microagents"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
STATE_FILE = CONFIG_DIR / "state.json"
CACHE_DIR = CONFIG_DIR / "cache"
LOG_DIR = CONFIG_DIR / "logs"

# Enumérations
class CloudProvider(str, Enum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    MULTI = "multi"

class OutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"
    MARKDOWN = "markdown"

class Environment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"
    CANARY = "canary"

# Gestionnaire de plugins
plugin_manager = PluginManager()

def ensure_config_dirs():
    """Crée les répertoires de configuration si nécessaire."""
    CONFIG_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)

def load_config() -> Dict[str, Any]:
    """Charge la configuration depuis le fichier."""
    ensure_config_dirs()
    
    default_config = {
        "version": "1.0",
        "api": {
            "url": "https://api.microagents.io",
            "timeout": 30,
            "retries": 3
        },
        "cloud": {
            "provider": None,
            "regions": [],
            "default_region": None
        },
        "features": {
            "auto_update": True,
            "telemetry": True,
            "notifications": True
        },
        "theme": "dark",
        "editor": os.getenv("EDITOR", "vim"),
        "format": {
            "default": "table",
            "colors": True
        }
    }
    
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                user_config = yaml.safe_load(f) or {}
                # Fusion récursive
                def merge_dicts(base, update):
                    for key, value in update.items():
                        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                            merge_dicts(base[key], value)
                        else:
                            base[key] = value
                    return base
                
                return merge_dicts(default_config, user_config)
        except Exception as e:
            console.print(f"[yellow]⚠️  Erreur de chargement de config: {e}[/yellow]")
    
    return default_config

def save_config(config: Dict[str, Any]):
    """Sauvegarde la configuration dans le fichier."""
    ensure_config_dirs()
    
    try:
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, indent=2)
    except Exception as e:
        console.print(f"[red]❌ Erreur de sauvegarde de config: {e}[/red]")

def load_state() -> Dict[str, Any]:
    """Charge l'état de la session."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "last_update_check": None,
        "sessions": {},
        "preferences": {}
    }

def save_state(state: Dict[str, Any]):
    """Sauvegarde l'état de la session."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        console.print(f"[red]❌ Erreur de sauvegarde d'état: {e}[/red]")

# Commandes globales
@app.command("init")
def init_config(
    cloud: Optional[CloudProvider] = typer.Option(None, "--cloud", "-c", help="Fournisseur cloud"),
    api_key: Optional[str] = typer.Option(None, "--api-key", "-k", help="Clé API", envvar="MICROAGENTS_API_KEY"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", "-i", help="Mode interactif")
):
    """
    Initialise la configuration du CLI.
    
    Args:
        cloud: Fournisseur cloud (aws, azure, gcp)
        api_key: Clé API pour l'authentification
        interactive: Mode interactif
    """
    console.print(Panel.fit("🚀 [bold cyan]Initialisation MicroAgents Platform[/bold cyan]", border_style="cyan"))
    
    config = load_config()
    
    # Mode interactif
    if interactive:
        console.print("\n[bold]Configuration interactive[/bold]")
        
        # Cloud provider
        if not cloud:
            cloud_choice = questionary.select(
                "Sélectionnez votre fournisseur cloud principal:",
                choices=[
                    {"name": "Amazon Web Services (AWS)", "value": "aws"},
                    {"name": "Microsoft Azure", "value": "azure"},
                    {"name": "Google Cloud Platform (GCP)", "value": "gcp"},
                    {"name": "Multi-cloud", "value": "multi"},
                    {"name": "Aucun pour le moment", "value": None}
                ]
            ).ask()
            cloud = cloud_choice
        
        # API Key
        if not api_key:
            use_api = questionary.confirm("Configurer l'accès API maintenant?").ask()
            if use_api:
                api_key = questionary.text(
                    "Entrez votre clé API (ou laissez vide pour plus tard):",
                    password=True
                ).ask()
        
        # Régions
        if cloud and cloud != "multi":
            regions = questionary.checkbox(
                "Sélectionnez les régions à utiliser:",
                choices=["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
            ).ask()
            default_region = questionary.select(
                "Sélectionnez la région par défaut:",
                choices=regions
            ).ask() if regions else None
        
        # Télémetrie
        telemetry = questionary.confirm(
            "Activer la télémetrie anonyme pour améliorer le produit?",
            default=True
        ).ask()
        
        # Notifications
        notifications = questionary.confirm(
            "Activer les notifications de mise à jour?",
            default=True
        ).ask()
    
    # Mise à jour de la configuration
    if cloud:
        config["cloud"]["provider"] = cloud
    
    if api_key:
        config["api"]["key"] = api_key
    
    if cloud and cloud != "multi" and 'regions' in locals() and 'default_region' in locals():
        config["cloud"]["regions"] = regions
        config["cloud"]["default_region"] = default_region
    
    if 'telemetry' in locals():
        config["features"]["telemetry"] = telemetry
    
    if 'notifications' in locals():
        config["features"]["notifications"] = notifications
    
    # Sauvegarde
    save_config(config)
    
    # Test de connexion
    console.print("\n[bold]Test de connexion...[/bold]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task("Connexion à l'API...", total=None)
        
        # Simuler un test de connexion
        asyncio.sleep(1)
        
        progress.update(task, completed=100)
    
    # Affichage du résumé
    console.print("\n[bold green]✅ Configuration terminée![/bold green]")
    console.print("\n[bold]Résumé:[/bold]")
    
    summary_table = Table(show_header=False, box=box.SIMPLE)
    summary_table.add_column("Paramètre", style="cyan")
    summary_table.add_column("Valeur", style="green")
    
    summary_table.add_row("Cloud Provider", str(config["cloud"]["provider"]))
    summary_table.add_row("Régions", ", ".join(config["cloud"]["regions"]) if config["cloud"]["regions"] else "Aucune")
    summary_table.add_row("API configurée", "✅" if config["api"].get("key") else "❌")
    summary_table.add_row("Télémetrie", "✅" if config["features"]["telemetry"] else "❌")
    summary_table.add_row("Notifications", "✅" if config["features"]["notifications"] else "❌")
    
    console.print(summary_table)
    
    # Instructions
    console.print("\n[bold]Prochaines étapes:[/bold]")
    console.print("  1. [cyan]microagents agent list[/cyan] - Voir les agents disponibles")
    console.print("  2. [cyan]microagents suite demo incident-management[/cyan] - Démarrer une démo")
    console.print("  3. [cyan]microagents roi calculate[/cyan] - Calculer votre ROI")
    
    if not config["api"].get("key"):
        console.print("\n[yellow]⚠️  Configurez votre clé API avec:[/yellow]")
        console.print("  [cyan]microagents config set api.key VOTRE_CLE_API[/cyan]")

@app.command("config")
def config_management(
    action: str = typer.Argument("show", help="Action: show, set, get, edit"),
    key: Optional[str] = typer.Argument(None, help="Clé de configuration"),
    value: Optional[str] = typer.Argument(None, help="Valeur à définir"),
    format: OutputFormat = typer.Option(OutputFormat.TABLE, "--format", "-f", help="Format de sortie")
):
    """
    Gestion de la configuration.
    
    Args:
        action: Action à effectuer (show, set, get, edit)
        key: Clé de configuration (pour set/get)
        value: Valeur à définir (pour set)
        format: Format de sortie
    """
    config = load_config()
    
    if action == "show":
        # Affichage complet
        if format == OutputFormat.TABLE:
            console.print(Panel.fit("[bold cyan]Configuration MicroAgents[/bold cyan]", border_style="cyan"))
            
            for section, settings in config.items():
                console.print(f"\n[bold]{section.upper()}[/bold]")
                
                if isinstance(settings, dict):
                    table = Table(show_header=False, box=box.SIMPLE)
                    table.add_column("Clé", style="cyan")
                    table.add_column("Valeur", style="green")
                    
                    for k, v in settings.items():
                        if isinstance(v, dict):
                            table.add_row(k, json.dumps(v, indent=2))
                        elif isinstance(v, list):
                            table.add_row(k, ", ".join(map(str, v)))
                        else:
                            table.add_row(k, str(v))
                    
                    console.print(table)
                else:
                    console.print(f"  {settings}")
        
        elif format == OutputFormat.JSON:
            console.print_json(data=config)
        
        elif format == OutputFormat.YAML:
            console.print(Syntax(yaml.dump(config, default_flow_style=False), "yaml"))
    
    elif action == "set" and key and value:
        # Définition d'une valeur
        keys = key.split(".")
        current = config
        
        # Navigation dans la structure
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        
        # Conversion de type
        last_key = keys[-1]
        try:
            # Essayer de convertir en JSON
            parsed_value = json.loads(value)
            current[last_key] = parsed_value
        except json.JSONDecodeError:
            # Sinon, garder comme chaîne
            current[last_key] = value
        
        save_config(config)
        console.print(f"[green]✅ Configuration mise à jour: {key} = {current[last_key]}[/green]")
    
    elif action == "get" and key:
        # Récupération d'une valeur
        keys = key.split(".")
        current = config
        
        try:
            for k in keys:
                current = current[k]
            
            if format == OutputFormat.TABLE:
                table = Table(show_header=False, box=box.SIMPLE)
                table.add_column("Clé", style="cyan")
                table.add_column("Valeur", style="green")
                table.add_row(key, str(current))
                console.print(table)
            else:
                console.print(str(current))
        
        except KeyError:
            console.print(f"[red]❌ Clé non trouvée: {key}[/red]")
    
    elif action == "edit":
        # Édition dans l'éditeur par défaut
        editor = config.get("editor", os.getenv("EDITOR", "vim"))
        os.system(f"{editor} {CONFIG_FILE}")
        console.print(f"[green]✅ Configuration éditée avec {editor}[/green]")
    
    else:
        console.print("[red]❌ Action invalide. Utilisez: show, set, get, edit[/red]")

@app.command("billing")
def billing_management(
    action: str = typer.Argument("estimate", help="Action: estimate, usage, invoices"),
    suite: Optional[str] = typer.Option(None, "--suite", "-s", help="Suite commerciale"),
    agents: Optional[int] = typer.Option(None, "--agents", "-a", help="Nombre d'agents"),
    period: str = typer.Option("monthly", "--period", "-p", help="Période (monthly, quarterly, yearly)"),
    output: OutputFormat = typer.Option(OutputFormat.TABLE, "--output", "-o", help="Format de sortie")
):
    """
    Gestion de la facturation et estimation des coûts.
    
    Args:
        action: Action à effectuer (estimate, usage, invoices)
        suite: Suite commerciale à estimer
        agents: Nombre d'agents
        period: Période de facturation
        output: Format de sortie
    """
    # Tarifs par suite (exemple)
    pricing_data = {
        "incident-management": {
            "base_monthly": 499,
            "per_agent": 5,
            "features": ["MTTR reduction", "Auto-healing", "Root cause analysis"]
        },
        "cost-optimization": {
            "base_monthly": 299,
            "per_agent": 3,
            "features": ["Cost analysis", "Budget alerts", "Optimization recommendations"]
        },
        "security-compliance": {
            "base_monthly": 599,
            "per_agent": 7,
            "features": ["Security scanning", "Compliance checks", "Threat detection"]
        }
    }
    
    if action == "estimate":
        console.print(Panel.fit("💰 [bold cyan]Estimation des coûts[/bold cyan]", border_style="cyan"))
        
        # Mode interactif si non spécifié
        if not suite:
            suite = questionary.select(
                "Sélectionnez une suite commerciale:",
                choices=list(pricing_data.keys())
            ).ask()
        
        if not agents:
            agents = questionary.text(
                "Nombre d'agents estimés:",
                default="50"
            ).ask()
            agents = int(agents)
        
        if suite not in pricing_data:
            console.print(f"[red]❌ Suite inconnue: {suite}[/red]")
            return
        
        pricing = pricing_data[suite]
        
        # Calcul
        base_cost = pricing["base_monthly"]
        agent_cost = pricing["per_agent"] * agents
        total_monthly = base_cost + agent_cost
        
        # Ajustement période
        period_multiplier = {
            "monthly": 1,
            "quarterly": 3 * 0.9,  # 10% de réduction
            "yearly": 12 * 0.8     # 20% de réduction
        }
        
        total_period = total_monthly * period_multiplier.get(period, 1)
        
        # Affichage
        if output == OutputFormat.TABLE:
            table = Table(title=f"Estimation pour {suite}", box=box.ROUNDED)
            table.add_column("Description", style="cyan")
            table.add_column("Valeur", style="green", justify="right")
            
            table.add_row("Suite commerciale", suite)
            table.add_row("Nombre d'agents", str(agents))
            table.add_row("Coût de base/mois", f"${base_cost}")
            table.add_row("Coût agents/mois", f"${agent_cost}")
            table.add_row("Total mensuel", f"[bold]${total_monthly}[/bold]")
            table.add_row(f"Total {period}", f"[bold green]${total_period:.2f}[/bold green]")
            
            console.print(table)
            
            # ROI estimé
            roi_estimate = total_monthly * 3  # ROI 300% garanti
            console.print(f"\n[yellow]📈 ROI estimé: ${roi_estimate:.2f}/mois (300% garanti)[/yellow]")
        
        elif output == OutputFormat.JSON:
            result = {
                "suite": suite,
                "agents": agents,
                "costs": {
                    "base_monthly": base_cost,
                    "agent_cost_monthly": agent_cost,
                    "total_monthly": total_monthly,
                    f"total_{period}": total_period
                },
                "roi_estimate": roi_estimate,
                "features": pricing["features"]
            }
            console.print_json(data=result)
    
    elif action == "usage":
        # Simuler l'utilisation actuelle
        usage_data = {
            "current_month": {
                "agents_active": 142,
                "executions": 12543,
                "cost": 842.50,
                "savings_generated": 2543.20
            },
            "forecast": {
                "month_end_cost": 950.00,
                "month_end_savings": 3200.00,
                "roi": 336.8
            }
        }
        
        console.print(Panel.fit("📊 [bold cyan]Utilisation actuelle[/bold cyan]", border_style="cyan"))
        
        if output == OutputFormat.TABLE:
            table = Table(box=box.ROUNDED)
            table.add_column("Métrique", style="cyan")
            table.add_column("Valeur", style="green", justify="right")
            
            for metric, value in usage_data["current_month"].items():
                if isinstance(value, float):
                    table.add_row(metric.replace("_", " ").title(), f"${value:.2f}")
                else:
                    table.add_row(metric.replace("_", " ").title(), str(value))
            
            console.print(table)
            
            # Forecast
            console.print("\n[bold]Prévisions fin de mois:[/bold]")
            forecast_table = Table(box=box.SIMPLE)
            forecast_table.add_column("Métrique", style="cyan")
            forecast_table.add_column("Valeur", style="green", justify="right")
            
            for metric, value in usage_data["forecast"].items():
                if isinstance(value, float):
                    forecast_table.add_row(metric.replace("_", " ").title(), f"${value:.2f}")
                else:
                    forecast_table.add_row(metric.replace("_", " ").title(), f"{value}%")
            
            console.print(forecast_table)
    
    elif action == "invoices":
        # Factures simulées
        invoices = [
            {"id": "INV-2023-001", "date": "2023-01-01", "amount": 842.50, "status": "paid"},
            {"id": "INV-2023-002", "date": "2023-02-01", "amount": 895.20, "status": "paid"},
            {"id": "INV-2023-003", "date": "2023-03-01", "amount": 912.80, "status": "pending"}
        ]
        
        console.print(Panel.fit("🧾 [bold cyan]Factures[/bold cyan]", border_style="cyan"))
        
        if output == OutputFormat.TABLE:
            table = Table(box=box.ROUNDED)
            table.add_column("ID", style="cyan")
            table.add_column("Date", style="white")
            table.add_column("Montant", style="green", justify="right")
            table.add_column("Statut", style="yellow")
            
            for inv in invoices:
                status_color = "green" if inv["status"] == "paid" else "yellow"
                table.add_row(
                    inv["id"],
                    inv["date"],
                    f"${inv['amount']:.2f}",
                    f"[{status_color}]{inv['status']}[/{status_color}]"
                )
            
            console.print(table)
        
        elif output == OutputFormat.CSV:
            import csv
            import io
            
            output_buffer = io.StringIO()
            writer = csv.DictWriter(output_buffer, fieldnames=["id", "date", "amount", "status"])
            writer.writeheader()
            writer.writerows(invoices)
            
            console.print(output_buffer.getvalue())

@app.command("script")
def script_mode(
    script_file: Optional[Path] = typer.Argument(None, help="Fichier de script"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "-o", help="Format de sortie"),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Simuler l'exécution")
):
    """
    Mode script pour l'automatisation.
    
    Args:
        script_file: Fichier de script YAML/JSON
        output: Format de sortie
        dry_run: Mode simulation
    """
    if not script_file:
        # Mode interactif pour créer un script
        console.print(Panel.fit("📜 [bold cyan]Création de script[/bold cyan]", border_style="cyan"))
        
        script_template = {
            "version": "1.0",
            "name": "Demo Deployment Script",
            "steps": [
                {
                    "action": "suite.demo",
                    "params": {"name": "incident-management"}
                },
                {
                    "action": "agent.generate",
                    "params": {"count": 5, "type": "detector"}
                },
                {
                    "action": "deploy.k8s",
                    "params": {"environment": "staging"}
                }
            ]
        }
        
        script_content = yaml.dump(script_template, default_flow_style=False)
        
        console.print("\n[bold]Exemple de script:[/bold]")
        console.print(Syntax(script_content, "yaml"))
        
        save_script = questionary.confirm("Sauvegarder cet exemple?").ask()
        
        if save_script:
            script_path = Path("microagents_script.yaml")
            script_path.write_text(script_content)
            console.print(f"[green]✅ Script sauvegardé: {script_path}[/green]")
        
        return
    
    # Chargement et exécution du script
    try:
        if script_file.suffix == ".json":
            with open(script_file, 'r') as f:
                script = json.load(f)
        else:
            with open(script_file, 'r') as f:
                script = yaml.safe_load(f)
        
        console.print(f"[cyan]📜 Exécution du script: {script.get('name', 'Unnamed')}[/cyan]")
        
        if dry_run:
            console.print("[yellow]🔄 Mode simulation activé[/yellow]")
        
        results = []
        
        for i, step in enumerate(script.get("steps", []), 1):
            action = step.get("action")
            params = step.get("params", {})
            
            console.print(f"\n[bold]Étape {i}: {action}[/bold]")
            
            # Simulation d'exécution
            if dry_run:
                console.print(f"  [yellow]Simulation: {action} avec {params}[/yellow]")
                result = {"status": "simulated", "step": i, "action": action}
            else:
                # Ici, intégrer avec les vraies commandes
                console.print(f"  [green]Exécution: {action}[/green]")
                
                # Simulation de résultat
                import random
                success = random.random() > 0.2  # 80% de succès
                
                if success:
                    result = {"status": "success", "step": i, "action": action, "output": "Completed successfully"}
                    console.print(f"  [green]✅ Succès[/green]")
                else:
                    result = {"status": "failed", "step": i, "action": action, "error": "Simulated error"}
                    console.print(f"  [red]❌ Échec[/red]")
            
            results.append(result)
        
        # Résumé
        success_count = sum(1 for r in results if r["status"] == "success")
        total_steps = len(results)
        
        console.print(f"\n[bold]Résumé:[/bold] {success_count}/{total_steps} étapes réussies")
        
        if output == OutputFormat.JSON:
            console.print_json(data={"results": results, "summary": {"success": success_count, "total": total_steps}})
        
    except Exception as e:
        console.print(f"[red]❌ Erreur d'exécution du script: {e}[/red]")

@app.command("plugins")
def plugin_management(
    action: str = typer.Argument("list", help="Action: list, install, remove, update"),
    plugin_name: Optional[str] = typer.Argument(None, help="Nom du plugin"),
    plugin_url: Optional[str] = typer.Option(None, "--url", "-u", help="URL du plugin")
):
    """
    Gestion des plugins du CLI.
    
    Args:
        action: Action à effectuer
        plugin_name: Nom du plugin
        plugin_url: URL du plugin (pour install)
    """
    if action == "list":
        plugins = plugin_manager.list_plugins()
        
        if not plugins:
            console.print("[yellow]⚠️  Aucun plugin installé[/yellow]")
            console.print("\nPlugins disponibles:")
            console.print("  • [cyan]aws-advanced[/cyan] - Commandes AWS avancées")
            console.print("  • [cyan]security-scanner[/cyan] - Scan de sécurité intégré")
            console.print("  • [cyan]cost-dashboard[/cyan] - Tableau de bord des coûts")
            return
        
        console.print(Panel.fit("🔌 [bold cyan]Plugins installés[/bold cyan]", border_style="cyan"))
        
        table = Table(box=box.ROUNDED)
        table.add_column("Plugin", style="cyan")
        table.add_column("Version", style="white")
        table.add_column("Description", style="green")
        table.add_column("Statut", style="yellow")
        
        for plugin in plugins:
            table.add_row(
                plugin["name"],
                plugin.get("version", "1.0.0"),
                plugin.get("description", ""),
                "[green]✓[/green]" if plugin.get("enabled", False) else "[red]✗[/red]"
            )
        
        console.print(table)
    
    elif action == "install" and plugin_name:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task(f"Installation de {plugin_name}...", total=None)
            
            # Simulation d'installation
            asyncio.sleep(2)
            
            progress.update(task, completed=100)
        
        console.print(f"[green]✅ Plugin {plugin_name} installé[/green]")
        
        # Demander l'activation
        enable = questionary.confirm(f"Activer le plugin {plugin_name} maintenant?").ask()
        if enable:
            console.print(f"[green]✅ Plugin {plugin_name} activé[/green]")
    
    elif action == "remove" and plugin_name:
        confirm = questionary.confirm(f"Supprimer le plugin {plugin_name}?").ask()
        if confirm:
            console.print(f"[green]✅ Plugin {plugin_name} supprimé[/green]")
    
    elif action == "update":
        console.print("[yellow]🔄 Recherche de mises à jour...[/yellow]")
        # Simulation de vérification
        asyncio.sleep(1)
        console.print("[green]✅ Tous les plugins sont à jour[/green]")

@app.command("completion")
def completion_management(
    shell: str = typer.Argument("bash", help="Shell: bash, zsh, fish, powershell"),
    install: bool = typer.Option(False, "--install", "-i", help="Installer la complétion")
):
    """
    Gestion de l'auto-complétion.
    
    Args:
        shell: Type de shell
        install: Installer la complétion
    """
    shell = shell.lower()
    
    if shell not in ["bash", "zsh", "fish", "powershell"]:
        console.print(f"[red]❌ Shell non supporté: {shell}[/red]")
        return
    
    if install:
        # Installation
        completion_script = f"""
# Auto-completion pour MicroAgents CLI
# Installation: source <(microagents completion {shell} --install)

_microagents_completion() {{
    local IFS=$'\n'
    COMPREPLY=( $( env COMP_WORDS="${{COMP_WORDS[*]}}" \\
                   COMP_CWORD=$COMP_CWORD \\
                   _MICROAGENTS_COMPLETE={shell}_complete \\
                   $1 ) )
    return 0
}}

complete -F _microagents_completion microagents
"""
        
        if shell == "zsh":
            completion_script = """
# Auto-completion pour MicroAgents CLI (Zsh)
# Ajouter à ~/.zshrc:
# source <(microagents completion zsh --install)

_microagents_completion() {
    local matches=($(microagents completion -- "${words[@]}"))
    compadd -a matches
}

compdef _microagents_completion microagents
"""
        
        console.print(f"[green]✅ Script de complétion pour {shell}:[/green]")
        console.print(Syntax(completion_script, shell))
        
        # Instructions d'installation
        console.print(f"\n[bold]Instructions d'installation:[/bold]")
        console.print(f"  1. Copiez le script ci-dessus")
        console.print(f"  2. Ajoutez-le à votre ~/.{shell}rc")
        console.print(f"  3. Exécutez: [cyan]source ~/.{shell}rc[/cyan]")
    else:
        # Génération de complétion
        console.print(f"[cyan]Génération de complétion pour {shell}...[/cyan]")
        # Typer gère automatiquement la complétion
        pass

@app.command("update")
def check_updates(
    force: bool = typer.Option(False, "--force", "-f", help="Forcer la vérification")
):
    """
    Vérifie les mises à jour disponibles.
    
    Args:
        force: Forcer la vérification (ignorer le cache)
    """
    state = load_state()
    last_check = state.get("last_update_check")
    
    if not force and last_check:
        last_date = datetime.fromisoformat(last_check)
        if datetime.utcnow() - last_date < timedelta(hours=6):
            console.print("[yellow]⚠️  Dernière vérification il y a moins de 6 heures[/yellow]")
            console.print("  Utilisez [cyan]--force[/cyan] pour vérifier à nouveau")
            return
    
    console.print("[cyan]🔄 Recherche de mises à jour...[/cyan]")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("Vérification...", total=100)
        
        # Simulation de vérification
        for i in range(10):
            asyncio.sleep(0.1)
            progress.update(task, advance=10)
        
        progress.update(task, completed=100)
    
    # Simulation de résultat
    import random
    if random.random() > 0.7:  # 30% de chance d'avoir une mise à jour:
        console.print("\n[green]🎉 Nouvelle version disponible![/green]")
        console.print("  Version actuelle: 1.0.0")
        console.print("  Version disponible: 1.1.0")
        console.print("\n  [bold]Nouvelles fonctionnalités:[/bold]")
        console.print("  • Support multi-cloud amélioré")
        console.print("  • Nouveaux agents de sécurité")
        console.print("  • Performances optimisées")
        
        update_now = questionary.confirm("Mettre à jour maintenant?").ask()
        if update_now:
            console.print("[green]✅ Mise à jour en cours...[/green]")
            # Ici, intégrer avec pip ou le gestionnaire de packages
    else:
        console.print("\n[green]✅ Vous avez la dernière version (1.0.0)[/green]")
    
    # Mettre à jour l'état
    state["last_update_check"] = datetime.utcnow().isoformat()
    save_state(state)

@app.command("dashboard")
def show_dashboard(
    realtime: bool = typer.Option(False, "--realtime", "-r", help="Mode temps réel"),
    refresh: int = typer.Option(5, "--refresh", help="Intervalle de rafraîchissement (secondes)")
):
    """
    Affiche un tableau de bord en temps réel.
    
    Args:
        realtime: Mode temps réel avec rafraîchissement
        refresh: Intervalle de rafraîchissement
    """
    if realtime:
        console.print("[cyan]📊 Tableau de bord temps réel[/cyan]")
        console.print(f"[yellow]Rafraîchissement: {refresh}s[/yellow]")
        
        try:
            with Live(refresh_per_second=1/refresh, screen=True) as live:
                while True:
                    dashboard_data = _generate_dashboard_data()
                    live.update(dashboard_data)
                    asyncio.sleep(refresh)
        except KeyboardInterrupt:
            console.print("\n[yellow]Tableau de bord arrêté[/yellow]")
    else:
        console.print("[cyan]📊 Tableau de bord statique[/cyan]")
        dashboard = _generate_dashboard_data()
        console.print(dashboard)

def _generate_dashboard_data() -> Layout:
    """Génère les données du tableau de bord."""
    layout = Layout()
    
    # En-tête
    header = Panel(
        "[bold cyan]MicroAgents Platform Dashboard[/bold cyan]\n"
        "[white]1400 agents actifs • ROI: 327% • Économies: $2.3M[/white]",
        border_style="cyan"
    )
    
    # Section agents
    agents_data = {
        "Cost Optimization": 425,
        "Security & Compliance": 350,
        "Incident Management": 300,
        "Performance Monitoring": 325
    }
    
    agents_table = Table(title="Agents par catégorie", box=box.ROUNDED)
    agents_table.add_column("Catégorie", style="cyan")
    agents_table.add_column("Nombre", style="green", justify="right")
    agents_table.add_column("Statut", style="yellow")
    
    for category, count in agents_data.items():
        status = "[green]✓[/green]" if count > 0 else "[red]✗[/red]"
        agents_table.add_row(category, str(count), status)
    
    # Section performances
    perf_data = {
        "Taux de succès": "99.2%",
        "Temps réponse moyen": "2.3s",
        "Économies/mois": "$192k",
        "Incidents résolus": "1,243"
    }
    
    perf_table = Table(title="Performances", box=box.ROUNDED)
    perf_table.add_column("Métrique", style="cyan")
    perf_table.add_column("Valeur", style="green", justify="right")
    
    for metric, value in perf_data.items():
        perf_table.add_row(metric, value)
    
    # Layout
    layout.split_column(
        Layout(header, size=3),
        Layout(name="main")
    )
    
    layout["main"].split_row(
        Layout(Panel(agents_table, border_style="blue"), name="left"),
        Layout(Panel(perf_table, border_style="green"), name="right")
    )
    
    return layout

@app.command("docs")
def open_docs(
    topic: Optional[str] = typer.Argument(None, help="Sujet de documentation"),
    web: bool = typer.Option(False, "--web", "-w", help="Ouvrir dans le navigateur"),
    offline: bool = typer.Option(False, "--offline", "-o", help="Mode hors ligne")
):
    """
    Ouvre la documentation.
    
    Args:
        topic: Sujet spécifique
        web: Ouvrir dans le navigateur
        offline: Mode hors ligne
    """
    if web:
        import webbrowser
        url = "https://docs.microagents.io"
        if topic:
            url += f"/{topic}"
        webbrowser.open(url)
        console.print(f"[green]✅ Documentation ouverte: {url}[/green]")
        return
    
    # Mode hors ligne
    console.print(Panel.fit("📚 [bold cyan]Documentation MicroAgents[/bold cyan]", border_style="cyan"))
    
    topics = {
        "getting-started": "Premiers pas avec la plateforme",
        "agent-development": "Développement d'agents",
        "api-reference": "Référence API complète",
        "deployment": "Guide de déploiement",
        "troubleshooting": "Dépannage",
        "best-practices": "Meilleures pratiques"
    }
    
    if topic:
        if topic in topics:
            console.print(f"\n[bold]{topic}:[/bold]")
            console.print(f"  {topics[topic]}")
            
            # Contenu détaillé simulé
            if topic == "getting-started":
                content = """
## Premiers pas

1. **Installation**
   ```bash
   pip install microagents-platform
   microagents init

2. **Configuration**
   ```bash
microagents config setup --cloud aws
3. **Déploiement duPremier agent**
    ```bash
    microagents agent generate --type detector

4. **Lancement d'une démo**
    ```bash
    microagents suite demo incident-management


   """
         console.print(Markdown(content))
 else:
     console.print(f"[red]❌ Sujet non trouvé: {topic}[/red]")
else:
console.print("\n[bold]Sujets disponibles:[/bold]")
for topic_name, description in topics.items():
console.print(f" • [cyan]{topic_name}[/cyan] - {description}")

text
 console.print("\n[bold]Exemples:[/bold]")
 console.print("  [cyan]microagents docs getting-started[/cyan]")
 console.print("  [cyan]microagents docs api-reference[/cyan]")
@app.command("demo")
def run_demo(
name: Optional[str] = typer.Argument(None, help="Nom de la démo"),
interactive: bool = typer.Option(True, "--interactive/--no-interactive", "-i", help="Mode interactif")
):
"""
Lance une démonstration interactive.

Args:
    name: Nom de la démo spécifique
    interactive: Mode interactif
"""
demos = {
    "incident-management": {
        "name": "Incident Management Suite",
        "description": "Détection et résolution automatique d'incidents",
        "agents": 425,
        "duration": "5 minutes",
        "roi": "85% reduction MTTR"
    },
    "cost-optimization": {
        "name": "Cost Optimization Suite",
        "description": "Optimisation automatique des coûts cloud",
        "agents": 300,
        "duration": "3 minutes",
        "roi": "40% cost reduction"
    },
    "security-compliance": {
        "name": "Security & Compliance Suite",
        "description": "Sécurité proactive et conformité automatisée",
        "agents": 350,
        "duration": "4 minutes",
        "roi": "300% faster vulnerability detection"
    }
}

if not name:
    if interactive:
        name = questionary.select(
            "Sélectionnez une démo à lancer:",
            choices=[
                {"name": f"{d['name']} - {d['description']}", "value": k}
                for k, d in demos.items()
            ]
        ).ask()
    else:
        console.print("[red]❌ Nom de démo requis en mode non-interactif[/red]")
        return

if name not in demos:
    console.print(f"[red]❌ Démo inconnue: {name}[/red]")
    return

demo = demos[name]

console.print(Panel.fit(
    f"🎬 [bold cyan]{demo['name']}[/bold cyan]\n"
    f"[white]{demo['description']}[/white]",
    border_style="cyan"
))

with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    transient=True,
) as progress:
    tasks = [
        progress.add_task("[cyan]Initialisation de la démo...", total=100),
        progress.add_task(f"[green]Chargement des {demo['agents']} agents...", total=100),
        progress.add_task("[yellow]Configuration de l'environnement...", total=100),
        progress.add_task("[magenta]Exécution des scénarios...", total=100)
    ]
    
    # Simulation de progression
    for i in range(10):
        for task in tasks:
            progress.update(task, advance=10)
        asyncio.sleep(0.3)
    
    for task in tasks:
        progress.update(task, completed=100)

console.print(f"\n[green]✅ Démo terminée![/green]")
console.print(f"\n[bold]Résultats:[/bold]")
console.print(f"  • Agents déployés: {demo['agents']}")
console.print(f"  • Durée: {demo['duration']}")
console.print(f"  • ROI démontré: {demo['roi']}")

if interactive:
    see_details = questionary.confirm("Voir les détails techniques?").ask()
    if see_details:
        # Simuler des détails techniques
        console.print("\n[bold]Détails techniques:[/bold]")
        console.print("  • Infrastructure: Kubernetes")
        console.print("  • Monitoring: Prometheus + Grafana")
        console.print("  • Logging: ELK Stack")
        console.print("  • CI/CD: GitLab + ArgoCD")
@app.command("health")
def health_check(
component: Optional[str] = typer.Argument(None, help="Composant spécifique"),
detailed: bool = typer.Option(False, "--detailed", "-d", help="Rapport détaillé")
):
"""
Vérifie la santé du système.

Args:
    component: Composant spécifique à vérifier
    detailed: Rapport détaillé
"""
components = {
    "api": {"status": "healthy", "response_time": "45ms"},
    "database": {"status": "healthy", "connections": "142"},
    "cache": {"status": "healthy", "hit_rate": "98.2%"},
    "agents": {"status": "healthy", "active": "1400"},
    "monitoring": {"status": "healthy", "alerts": "0"}
}

if component:
    if component in components:
        comp_data = components[component]
        status_color = "green" if comp_data["status"] == "healthy" else "red"
        
        console.print(f"\n[bold]{component.upper()}:[/bold]")
        console.print(f"  Statut: [{status_color}]{comp_data['status']}[/{status_color}]")
        
        for key, value in comp_data.items():
            if key != "status":
                console.print(f"  {key.replace('_', ' ').title()}: {value}")
    else:
        console.print(f"[red]❌ Composant inconnu: {component}[/red]")
    return

# Vérification globale
console.print(Panel.fit("🏥 [bold cyan]Vérification de santé système[/bold cyan]", border_style="cyan"))

unhealthy_count = sum(1 for c in components.values() if c["status"] != "healthy")

if unhealthy_count == 0:
    console.print("[green]✅ Tous les composants sont en bonne santé[/green]")
else:
    console.print(f"[yellow]⚠️  {unhealthy_count} composant(s) avec problèmes[/yellow]")

if detailed:
    table = Table(box=box.ROUNDED)
    table.add_column("Composant", style="cyan")
    table.add_column("Statut", style="yellow")
    table.add_column("Métriques", style="white")
    
    for name, data in components.items():
        status_color = "green" if data["status"] == "healthy" else "red"
        metrics = ", ".join([f"{k}: {v}" for k, v in data.items() if k != "status"])
        
        table.add_row(
            name,
            f"[{status_color}]{data['status']}[/{status_color}]",
            metrics
        )
    
    console.print(table)
else:
    # Vue rapide
    columns = []
    for name, data in components.items():
        status_color = "green" if data["status"] == "healthy" else "red"
        panel = Panel(
            f"[{status_color}]{data['status'].upper()}[/{status_color}]",
            title=name,
            border_style=status_color
        )
        columns.append(panel)
    
    console.print(Columns(columns))
@app.command("version")
def show_version(
check_update: bool = typer.Option(False, "--check-update", "-c", help="Vérifier les mises à jour")
):
"""
Affiche la version du CLI.

Args:
    check_update: Vérifier les mises à jour disponibles
"""
console.print(Panel.fit(
    f"[bold cyan]MicroAgents Platform CLI[/bold cyan] v{__version__}\n"
    f"[white]© 2024 DevOps Intelligence Inc.[/white]",
    border_style="cyan"
))

if check_update:
    check_updates(force=True)
Gestionnaire de contexte

@app.callback()
def main_callback(
ctx: Context,
verbose: bool = typer.Option(False, "--verbose", "-v", help="Mode verbeux"),
quiet: bool = typer.Option(False, "--quiet", "-q", help="Mode silencieux"),
format: OutputFormat = typer.Option(None, "--format", "-f", help="Format de sortie"),
config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="Fichier de configuration"),
no_color: bool = typer.Option(False, "--no-color", help="Désactiver les couleurs")
):
"""
MicroAgents Platform CLI - Gestion de 1400 agents DevOps.

Automatisez votre infrastructure avec une intelligence distribuée.
"""
# Configuration globale
ctx.ensure_object(dict)
ctx.obj["verbose"] = verbose
ctx.obj["quiet"] = quiet
ctx.obj["format"] = format
ctx.obj["no_color"] = no_color

# Chargement de la configuration
if config_file:
    global CONFIG_FILE
    CONFIG_FILE = config_file

config = load_config()
ctx.obj["config"] = config

# Configuration de la console
if no_color:
    console.no_color = True

if verbose:
    console.print(f"[dim]Verbose mode activé[/dim]")
    console.print(f"[dim]Configuration chargée: {CONFIG_FILE}[/dim]")

if quiet:
    console.quiet = True

# Vérification des mises à jour (une fois par jour)
state = load_state()
last_update_check = state.get("last_update_check")

if last_update_check:
    last_date = datetime.fromisoformat(last_update_check)
    if datetime.utcnow() - last_date > timedelta(days=1):
        if config["features"]["notifications"] and not quiet:
            console.print("[yellow]🔔 Une mise à jour est disponible![/yellow]")
            console.print("  Exécutez [cyan]microagents update[/cyan] pour mettre à jour")
else:
    state["last_update_check"] = datetime.utcnow().isoformat()
    save_state(state)
Point d'entrée principal

if name == "main":
try:
ensure_config_dirs()
app()
except KeyboardInterrupt:
console.print("\n[yellow]🛑 Commande interrompue[/yellow]")
sys.exit(0)
except Exception as e:
if load_config().get("verbose", False):
console.print_exception()
else:
console.print(f"[red]❌ Erreur: {e}[/red]")
sys.exit(1)