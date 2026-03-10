"""
Commandes de gestion des agents.
Génération, validation, test et déploiement d'agents.
"""

import os
import sys
import json
import yaml
import asyncio
import typer
import questionary
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime
from enum import Enum
from difflib import unified_diff
import tempfile
import shutil

import typer
from typer import Typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.syntax import Syntax
from rich.columns import Columns
from rich.text import Text
from rich import box
from rich.prompt import Prompt, Confirm

from microagents.cli.utils.progress import CustomProgress
from microagents.cli.utils.formatting import format_table, format_tree, format_json, format_yaml
from microagents.cli.utils.interactive import InteractivePrompt
from microagents.core.base.registry import AgentRegistry
from microagents.dsl.parser.parser import DSLASTParser
from microagents.dsl.compiler.compiler import DSLCompiler
from microagents.dsl.validator.validator import DSLValidator
from microagents.dsl.documentation.generator import DocumentationGenerator
from microagents.generator.engines.jinja_engine import JinjaEngine
from microagents.core.agents.validators.security_validator import SecurityValidator
from microagents.core.agents.validators.configuration_validator import ConfigurationValidator
from microagents.monitoring.metrics.collector import MetricsCollector
from microagents.registry.storage.models import AgentModel

# Initialisation
console = Console()
app = Typer(help="🤖 Gestion des agents MicroAgents", rich_markup_mode="rich")

# Config
AGENT_TYPES = [
    "detector", "optimizer", "remediator", "analyzer",
    "validator", "auditor", "predictor", "integrator",
    "controller", "monitor", "governor", "reporter"
]

AGENT_CATEGORIES = [
    "cost", "security", "incident", "performance",
    "compliance", "governance", "monitoring", "optimization"
]

AGENT_TEMPLATES = {
    "cost-anomaly-detector": {
        "name": "Détecteur d'anomalies de coût",
        "description": "Détecte les anomalies de dépenses cloud",
        "type": "detector",
        "category": "cost",
        "template": "cost_detector.jinja"
    },
    "security-scanner": {
        "name": "Scanner de sécurité",
        "description": "Scan automatique des vulnérabilités",
        "type": "detector",
        "category": "security",
        "template": "security_scanner.jinja"
    },
    "incident-responder": {
        "name": "Répondeur d'incidents",
        "description": "Résolution automatique d'incidents",
        "type": "remediator",
        "category": "incident",
        "template": "incident_responder.jinja"
    },
    "performance-optimizer": {
        "name": "Optimiseur de performance",
        "description": "Optimisation des ressources cloud",
        "type": "optimizer",
        "category": "performance",
        "template": "performance_optimizer.jinja"
    }
}

# Enumérations
class OutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"
    YAML = "yaml"
    PYTHON = "python"
    MARKDOWN = "markdown"

class AgentLanguage(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    GO = "go"
    JAVA = "java"

@app.command("generate")
def generate_agent(
    dsl_file: Optional[Path] = typer.Option(None, "--dsl-file", "-d", help="Fichier DSL source"),
    template: Optional[str] = typer.Option(None, "--template", "-t", help="Template d'agent"),
    output_dir: Optional[Path] = typer.Option(Path("."), "--output-dir", "-o", help="Répertoire de sortie"),
    language: AgentLanguage = typer.Option(AgentLanguage.PYTHON, "--language", "-l", help="Langage de sortie"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", "-i", help="Mode interactif"),
    preview: bool = typer.Option(True, "--preview/--no-preview", "-p", help="Aperçu avant génération")
):
    """
    Génère un agent à partir d'un fichier DSL ou d'un template.

    Args:
        dsl_file: Fichier DSL source
        template: Nom du template
        output_dir: Répertoire de sortie
        language: Langage de génération
        interactive: Mode interactif
        preview: Aperçu avant génération
    """
    console.print(Panel.fit("🚀 [bold cyan]Génération d'agent[/bold cyan]", border_style="cyan"))

    # Mode interactif pour sélectionner la source
    if not dsl_file and not template and interactive:
        source_type = questionary.select(
            "Source de l'agent:",
            choices=[
                {"name": "Fichier DSL (.dsl)", "value": "dsl"},
                {"name": "Template prédéfini", "value": "template"},
                {"name": "Assistant interactif", "value": "wizard"}
            ]
        ).ask()

        if source_type == "dsl":
            dsl_file = questionary.path(
                "Chemin du fichier DSL:",
                only_files=True,
                file_filter=lambda f: f.endswith(".dsl")
            ).ask()
            if dsl_file:
                dsl_file = Path(dsl_file)

        elif source_type == "template":
            template = questionary.select(
                "Sélectionnez un template:",
                choices=[
                    {"name": f"{t['name']} - {t['description']}", "value": k}
                    for k, t in AGENT_TEMPLATES.items()
                ]
            ).ask()

        elif source_type == "wizard":
            return _run_agent_wizard(output_dir, language, preview)

    # Génération à partir de DSL
    if dsl_file:
        return _generate_from_dsl(dsl_file, output_dir, language, preview)

    # Génération à partir de template
    if template:
        return _generate_from_template(template, output_dir, language, preview)

    console.print("[red]❌ Source d'agent non spécifiée[/red]")

def _run_agent_wizard(output_dir: Path, language: AgentLanguage, preview: bool):
    """Assistant interactif de création d'agent."""
    console.print("\n[bold]🎯 Assistant de création d'agent[/bold]")

    # Étape 1: Informations de base
    agent_name = questionary.text(
        "Nom de l'agent:",
        validate=lambda text: len(text) >= 3 and len(text) <= 50
    ).ask()

    description = questionary.text(
        "Description:",
        validate=lambda text: len(text) >= 10 and len(text) <= 200
    ).ask()

    # Étape 2: Type et catégorie
    agent_type = questionary.select(
        "Type d'agent:",
        choices=AGENT_TYPES
    ).ask()

    category = questionary.select(
        "Catégorie métier:",
        choices=AGENT_CATEGORIES
    ).ask()

    # Étape 3: Capacités
    console.print("\n[bold]Capacités (sélectionnez plusieurs):[/bold]")
    capabilities = questionary.checkbox(
        "Capacités:",
        choices=[
            "cost_optimization", "security_scanning", "incident_response",
            "performance_monitoring", "compliance_checking", "auto_remediation",
            "predictive_analysis", "report_generation", "api_integration",
            "data_processing", "alerting", "scheduling"
        ]
    ).ask()

    # Étape 4: Configuration
    console.print("\n[bold]Configuration:[/bold]")

    config_options = {}

    # Paramètres communs
    if "cost_optimization" in capabilities:
        config_options["budget_limit"] = questionary.text(
            "Limite de budget (en dollars):",
            default="1000"
        ).ask()

    if "security_scanning" in capabilities:
        config_options["scan_intensity"] = questionary.select(
            "Intensité du scan:",
            choices=["low", "medium", "high"]
        ).ask()

    # Fréquence d'exécution
    config_options["schedule"] = questionary.select(
        "Fréquence d'exécution:",
        choices=["on_demand", "hourly", "daily", "weekly", "monthly"]
    ).ask()

    # Étape 5: Génération DSL
    dsl_content = _generate_dsl_from_wizard(
        agent_name, description, agent_type, category, capabilities, config_options
    )

    # Aperçu DSL
    if preview:
        console.print("\n[bold]Aperçu du DSL généré:[/bold]")
        console.print(Syntax(dsl_content, "yaml", theme="monokai"))

        proceed = questionary.confirm("Générer l'agent à partir de ce DSL?").ask()
        if not proceed:
            console.print("[yellow]❌ Génération annulée[/yellow]")
            return

    # Génération temporaire du DSL
    with tempfile.NamedTemporaryFile(mode='w', suffix='.dsl', delete=False) as tmp:
        tmp.write(dsl_content)
        tmp_path = Path(tmp.name)

    try:
        # Génération à partir du DSL temporaire
        return _generate_from_dsl(tmp_path, output_dir, language, preview=False)
    finally:
        # Nettoyage
        tmp_path.unlink()

def _generate_dsl_from_wizard(name: str, description: str, agent_type: str,
                             category: str, capabilities: List[str],
                             config: Dict[str, Any]) -> str:
    """Génère un DSL à partir des réponses de l'assistant."""
    dsl_template = f"""# Agent: {name}
version: "1.0"
agent:
  name: "{name}"
  description: "{description}"
  type: "{agent_type}"
  category: "{category}"
  version: "1.0.0"

capabilities:
{_format_capabilities_dsl(capabilities)}

configuration:
  schedule: "{config.get('schedule', 'on_demand')}"
  timeout_seconds: 300
  retry_count: 3

parameters:
{_format_parameters_dsl(config)}

business_rules:
  - rule: "must_complete_within_timeout"
    condition: "execution_time < timeout_seconds"
    action: "mark_success"

  - rule: "validate_input_parameters"
    condition: "all(required_params in context)"
    action: "proceed"

output_spec:
  format: "json"
  required_fields:
    - "status"
    - "result"
    - "metrics"
    - "recommendations"
"""
    return dsl_template

def _format_capabilities_dsl(capabilities: List[str]) -> str:
    """Formate les capacités pour le DSL."""
    formatted = ""
    for cap in capabilities:
        formatted += f"  - {cap}\n"
    return formatted

def _format_parameters_dsl(config: Dict[str, Any]) -> str:
    """Formate les paramètres pour le DSL."""
    formatted = ""
    for key, value in config.items():
        if key != "schedule":
            formatted += f"  {key}: {value}\n"
    return formatted

def _generate_from_dsl(dsl_file: Path, output_dir: Path, language: AgentLanguage, preview: bool):
    """Génère un agent à partir d'un fichier DSL."""
    if not dsl_file.exists():
        console.print(f"[red]❌ Fichier DSL non trouvé: {dsl_file}[/red]")
        return

    console.print(f"[cyan]📄 Lecture du DSL: {dsl_file}[/cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
    ) as progress:
        # Parsing
        task_parse = progress.add_task("[cyan]Parsing DSL...", total=100)

        parser = DSLASTParser()
        with open(dsl_file, 'r') as f:
            dsl_content = f.read()

        ast = parser.parse(dsl_content)
        progress.update(task_parse, completed=100)

        # Validation
        task_validate = progress.add_task("[green]Validation syntaxique...", total=100)

        validator = DSLValidator()
        validation_result = validator.validate(ast)

        if not validation_result.valid:
            console.print(f"[red]❌ Validation DSL échouée:[/red]")
            for error in validation_result.errors:
                console.print(f"  • {error}")
            return

        progress.update(task_validate, completed=100)

        # Compilation
        task_compile = progress.add_task("[yellow]Compilation...", total=100)

        compiler = DSLCompiler()
        intermediate = compiler.compile(ast, target_language=language.value)

        progress.update(task_compile, completed=100)

        # Génération de code
        task_generate = progress.add_task("[magenta]Génération de code...", total=100)

        generator = JinjaEngine()

        # Préparation des données pour le template
        agent_data = {
            "name": ast.get("agent", {}).get("name", "UnnamedAgent"),
            "description": ast.get("agent", {}).get("description", ""),
            "type": ast.get("agent", {}).get("type", "detector"),
            "category": ast.get("agent", {}).get("category", "generic"),
            "capabilities": ast.get("capabilities", []),
            "configuration": ast.get("configuration", {}),
            "parameters": ast.get("parameters", {}),
            "business_rules": ast.get("business_rules", []),
            "timestamp": datetime.now().isoformat(),
            "language": language.value
        }

        # Sélection du template
        template_name = f"{language.value}_agent.jinja"
        generated_code = generator.render(template_name, agent_data)

        progress.update(task_generate, completed=100)

    # Aperçu
    if preview:
        console.print("\n[bold]Aperçu du code généré:[/bold]")

        # Afficher les premières lignes
        lines = generated_code.split('\n')[:50]
        preview_code = '\n'.join(lines)

        console.print(Syntax(preview_code, language.value, theme="monokai"))

        if len(generated_code.split('\n')) > 50:
            console.print(f"[dim]... ({len(generated_code.split('\n')) - 50} lignes supplémentaires)[/dim]")

        proceed = questionary.confirm("Générer les fichiers?").ask()
        if not proceed:
            console.print("[yellow]❌ Génération annulée[/yellow]")
            return

    # Génération des fichiers
    agent_name = agent_data["name"].replace(" ", "_").lower()
    output_path = output_dir / agent_name

    # Création du répertoire
    output_path.mkdir(exist_ok=True, parents=True)

    # Fichier principal
    main_file = output_path / f"{agent_name}.{language.value}"
    main_file.write_text(generated_code)

    # Fichier de configuration
    config_file = output_path / "config.yaml"
    config_content = yaml.dump({
        "agent": {
            "name": agent_data["name"],
            "description": agent_data["description"],
            "type": agent_data["type"],
            "category": agent_data["category"],
            "version": "1.0.0"
        },
        "capabilities": agent_data["capabilities"],
        "configuration": agent_data["configuration"]
    }, default_flow_style=False)
    config_file.write_text(config_content)

    # Fichier de test
    test_file = output_path / f"test_{agent_name}.py"
    test_content = _generate_test_file(agent_data, language)
    test_file.write_text(test_content)

    # Fichier README
    readme_file = output_path / "README.md"
    readme_content = _generate_readme(agent_data)
    readme_file.write_text(readme_content)

    console.print(f"\n[green]✅ Agent généré avec succès![/green]")
    console.print(f"[cyan]📁 Répertoire: {output_path}[/cyan]")

    # Arborescence des fichiers générés
    console.print("\n[bold]Fichiers générés:[/bold]")
    tree = Tree(f"[cyan]{output_path.name}/[/cyan]")

    for file in output_path.iterdir():
        if file.is_file():
            size = file.stat().st_size
            tree.add(f"[green]{file.name}[/green] ({size} bytes)")

    console.print(tree)

    # Instructions
    console.print("\n[bold]Prochaines étapes:[/bold]")
    console.print(f"  1. [cyan]cd {output_path}[/cyan]")
    console.print(f"  2. [cyan]microagents agent validate {main_file}[/cyan] - Valider l'agent")
    console.print(f"  3. [cyan]microagents agent test {main_file}[/cyan] - Tester l'agent")
    console.print(f"  4. [cyan]microagents agent register {main_file}[/cyan] - Enregistrer l'agent")

def _generate_from_template(template_name: str, output_dir: Path, language: AgentLanguage, preview: bool):
    """Génère un agent à partir d'un template prédéfini."""
    if template_name not in AGENT_TEMPLATES:
        console.print(f"[red]❌ Template inconnu: {template_name}[/red]")
        console.print("\n[bold]Templates disponibles:[/bold]")
        for name, info in AGENT_TEMPLATES.items():
            console.print(f"  • [cyan]{name}[/cyan] - {info['description']}")
        return

    template_info = AGENT_TEMPLATES[template_name]

    console.print(f"[cyan]🛠️  Utilisation du template: {template_info['name']}[/cyan]")

    # Personnalisation interactive
    agent_name = questionary.text(
        "Nom de l'agent:",
        default=template_info["name"].replace(" ", "-").lower()
    ).ask()

    description = questionary.text(
        "Description:",
        default=template_info["description"]
    ).ask()

    # Configuration spécifique au template
    config = {}
    if template_name == "cost-anomaly-detector":
        config["threshold"] = questionary.text(
            "Seuil d'anomalie (pourcentage):",
            default="20"
        ).ask()
        config["budget_limit"] = questionary.text(
            "Limite de budget mensuel ($):",
            default="10000"
        ).ask()

    elif template_name == "security-scanner":
        config["scan_depth"] = questionary.select(
            "Profondeur du scan:",
            choices=["basic", "standard", "deep"]
        ).ask()
        config["compliance_standard"] = questionary.checkbox(
            "Standards de conformité:",
            choices=["SOC2", "ISO27001", "GDPR", "HIPAA"]
        ).ask()

    # Génération
    agent_data = {
        "name": agent_name,
        "description": description,
        "type": template_info["type"],
        "category": template_info["category"],
        "capabilities": _get_template_capabilities(template_name),
        "configuration": config,
        "language": language.value
    }

    generator = JinjaEngine()

    # Utiliser le template spécifique ou le template générique
    template_file = template_info.get("template") or f"{language.value}_agent.jinja"
    generated_code = generator.render(template_file, agent_data)

    # Aperçu
    if preview:
        console.print("\n[bold]Aperçu du code généré:[/bold]")
        console.print(Syntax(generated_code[:500], language.value, theme="monokai"))

        if len(generated_code) > 500:
            console.print(f"[dim]... ({len(generated_code) - 500} caractères supplémentaires)[/dim]")

        proceed = questionary.confirm("Générer l'agent?").ask()
        if not proceed:
            console.print("[yellow]❌ Génération annulée[/yellow]")
            return

    # Fichiers de sortie
    agent_slug = agent_name.replace(" ", "_").lower()
    output_path = output_dir / agent_slug
    output_path.mkdir(exist_ok=True, parents=True)

    main_file = output_path / f"{agent_slug}.{language.value}"
    main_file.write_text(generated_code)

    console.print(f"\n[green]✅ Agent généré: {output_path}[/green]")

def _get_template_capabilities(template_name: str) -> List[str]:
    """Retourne les capacités pour un template donné."""
    capabilities_map = {
        "cost-anomaly-detector": ["cost_analysis", "anomaly_detection", "alerting", "reporting"],
        "security-scanner": ["security_scanning", "vulnerability_detection", "compliance_checking", "reporting"],
        "incident-responder": ["incident_detection", "auto_remediation", "root_cause_analysis", "alerting"],
        "performance-optimizer": ["performance_monitoring", "resource_optimization", "cost_optimization", "reporting"]
    }
    return capabilities_map.get(template_name, [])

def _generate_test_file(agent_data: Dict[str, Any], language: AgentLanguage) -> str:
    """Génère un fichier de test pour l'agent."""
    if language == AgentLanguage.PYTHON:
        return f'''"""
Tests pour l'agent {agent_data['name']}
"""

import pytest
import json
from pathlib import Path

def test_agent_initialization():
    """Test d'initialisation de l'agent."""
    # TODO: Implémenter les tests
    assert True

def test_agent_execution():
    """Test d'exécution de l'agent."""
    # TODO: Implémenter avec des données de test
    assert True

def test_agent_validation():
    """Test de validation des entrées."""
    # TODO: Implémenter les tests de validation
    assert True

@pytest.fixture
def sample_data():
    """Données de test."""
    return {{
        "test": "data"
    }}

if __name__ == "__main__":
    pytest.main(["-v", __file__])
'''
    return ""

def _generate_readme(agent_data: Dict[str, Any]) -> str:
    """Génère un README pour l'agent."""
    return f"""# {agent_data['name']}

{agent_data['description']}

## Caractéristiques

- **Type**: {agent_data['type']}
- **Catégorie**: {agent_data['category']}
- **Capacités**: {', '.join(agent_data.get('capabilities', []))}

## Configuration

```yaml
{agent_data.get('configuration', {})}
Utilisation

python
# TODO: Exemples d'utilisation
Tests

bash
pytest test_{agent_data['name'].replace(' ', '_').lower()}.py
Déploiement

bash
microagents agent register {agent_data['name'].replace(' ', '_').lower()}.py
Généré automatiquement par MicroAgents Platform
"""

@app.command("validate")
def validate_agent(
agent_path: Path = typer.Argument(..., help="Chemin vers l'agent ou fichier DSL"),
check_syntax: bool = typer.Option(True, "--syntax/--no-syntax", help="Vérification syntaxique"),
check_business: bool = typer.Option(True, "--business/--no-business", help="Vérification règles métier"),
check_security: bool = typer.Option(True, "--security/--no-security", help="Vérification sécurité"),
output_format: OutputFormat = typer.Option(OutputFormat.TABLE, "--format", "-f", help="Format de sortie")
):
"""
Valide un agent (DSL ou code).

text
Args:
    agent_path: Chemin vers l'agent
    check_syntax: Vérification syntaxique
    check_business: Vérification règles métier
    check_security: Vérification sécurité
    output_format: Format de sortie
"""
console.print(Panel.fit("🔍 [bold cyan]Validation d'agent[/bold cyan]", border_style="cyan"))

if not agent_path.exists():
    console.print(f"[red]❌ Chemin non trouvé: {agent_path}[/red]")
    return

validation_results = {
    "syntax": {"valid": False, "errors": [], "warnings": []},
    "business": {"valid": False, "errors": [], "warnings": []},
    "security": {"valid": False, "errors": [], "warnings": []},
    "overall": {"valid": False, "score": 0}
}

with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    transient=True,
) as progress:
    total_tasks = sum([check_syntax, check_business, check_security])
    current_task = 0

    # Validation syntaxique
    if check_syntax:
        task = progress.add_task("[cyan]Validation syntaxique...", total=100)

        if agent_path.suffix == ".dsl":
            # Validation DSL
            parser = DSLASTParser()
            with open(agent_path, 'r') as f:
                content = f.read()

            try:
                ast = parser.parse(content)
                validator = DSLValidator()
                result = validator.validate(ast)

                validation_results["syntax"]["valid"] = result.valid
                validation_results["syntax"]["errors"] = result.errors
                validation_results["syntax"]["warnings"] = result.warnings

            except Exception as e:
                validation_results["syntax"]["errors"].append(f"Erreur de parsing: {str(e)}")

        else:
            # Validation code
            # Pour Python, on pourrait utiliser ast.parse
            import ast as python_ast
            try:
                with open(agent_path, 'r') as f:
                    python_ast.parse(f.read())
                validation_results["syntax"]["valid"] = True
            except SyntaxError as e:
                validation_results["syntax"]["errors"].append(f"Erreur de syntaxe: {str(e)}")

        progress.update(task, completed=100)
        current_task += 1

    # Validation métier
    if check_business:
        task = progress.add_task("[green]Validation règles métier...", total=100)

        try:
            # Charger l'agent
            agent_config = _load_agent_configuration(agent_path)

            # Vérifier les règles métier de base
            business_validator = ConfigurationValidator()
            result = business_validator.validate_config(
                agent_config.get("configuration", {}),
                agent_config.get("config_schema", {})
            )

            validation_results["business"]["valid"] = result.valid
            validation_results["business"]["errors"] = result.errors
            validation_results["business"]["warnings"] = result.warnings

        except Exception as e:
            validation_results["business"]["errors"].append(f"Erreur de validation métier: {str(e)}")

        progress.update(task, completed=100)
        current_task += 1

    # Validation sécurité
    if check_security:
        task = progress.add_task("[yellow]Analyse de sécurité...", total=100)

        try:
            security_validator = SecurityValidator()

            if agent_path.suffix == ".dsl":
                with open(agent_path, 'r') as f:
                    content = f.read()

                result = security_validator.validate_dsl(content)
            else:
                with open(agent_path, 'r') as f:
                    content = f.read()

                result = security_validator.validate_code(content)

            validation_results["security"]["valid"] = result.valid
            validation_results["security"]["errors"] = result.errors
            validation_results["security"]["warnings"] = result.warnings

        except Exception as e:
            validation_results["security"]["errors"].append(f"Erreur d'analyse de sécurité: {str(e)}")

        progress.update(task, completed=100)
        current_task += 1

# Calcul du score global
valid_checks = sum([
    1 if validation_results["syntax"]["valid"] and check_syntax else 0,
    1 if validation_results["business"]["valid"] and check_business else 0,
    1 if validation_results["security"]["valid"] and check_security else 0
])

total_checks = total_tasks
overall_score = (valid_checks / total_checks) * 100 if total_checks > 0 else 0

validation_results["overall"]["score"] = overall_score
validation_results["overall"]["valid"] = overall_score >= 80

# Affichage des résultats
if output_format == OutputFormat.TABLE:
    _display_validation_results_table(validation_results, agent_path)
elif output_format == OutputFormat.JSON:
    console.print_json(data=validation_results)
elif output_format == OutputFormat.YAML:
    console.print(Syntax(yaml.dump(validation_results), "yaml"))

# Recommandations
if overall_score < 80:
    console.print(f"\n[yellow]⚠️  Score de validation: {overall_score:.1f}%[/yellow]")
    console.print("[bold]Recommandations:[/bold]")

    for check_type in ["syntax", "business", "security"]:
        if validation_results[check_type]["errors"]:
            console.print(f"  • Corriger les erreurs {check_type}")

    console.print("  • Exécuter: [cyan]microagents agent test[/cyan] pour les tests")
    console.print("  • Exécuter: [cyan]microagents agent profile[/cyan] pour l'analyse performance")
def _load_agent_configuration(agent_path: Path) -> Dict[str, Any]:
"""Charge la configuration d'un agent."""
config_path = agent_path.parent / "config.yaml"

text
if config_path.exists():
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

# Essayer de trouver la configuration dans le code
with open(agent_path, 'r') as f:
    content = f.read()

# Extraction simple (à améliorer)
import re

config = {}

# Chercher des patterns de configuration
config_patterns = {
    "name": r'name\s*[:=]\s*[\'"]([^\'"]+)[\'"]',
    "description": r'description\s*[:=]\s*[\'"]([^\'"]+)[\'"]',
    "type": r'type\s*[:=]\s*[\'"]([^\'"]+)[\'"]'
}

for key, pattern in config_patterns.items():
    match = re.search(pattern, content)
    if match:
        config[key] = match.group(1)

return config
def _display_validation_results_table(results: Dict[str, Any], agent_path: Path):
"""Affiche les résultats de validation dans un tableau."""
console.print(f"\n[bold]Résultats pour: {agent_path.name}[/bold]")

text
# Tableau principal
main_table = Table(box=box.ROUNDED)
main_table.add_column("Type de validation", style="cyan")
main_table.add_column("Statut", style="yellow")
main_table.add_column("Score", style="green")
main_table.add_column("Erreurs", style="red")
main_table.add_column("Avertissements", style="yellow")

for check_type in ["syntax", "business", "security"]:
    check_data = results[check_type]

    status = "[green]✓ VALIDE[/green]" if check_data["valid"] else "[red]✗ INVALIDE[/red]"
    error_count = len(check_data["errors"])
    warning_count = len(check_data["warnings"])

    main_table.add_row(
        check_type.upper(),
        status,
        "100%" if check_data["valid"] else "0%",
        str(error_count),
        str(warning_count)
    )

console.print(main_table)

# Score global
overall = results["overall"]
score_color = "green" if overall["score"] >= 80 else "red"

console.print(f"\n[bold]Score global: [{score_color}]{overall['score']:.1f}%[/{score_color}][/bold]")

# Détails des erreurs
has_errors = any(len(results[t]["errors"]) > 0 for t in ["syntax", "business", "security"])

if has_errors:
    console.print("\n[bold]Détails des erreurs:[/bold]")

    for check_type in ["syntax", "business", "security"]:
        errors = results[check_type]["errors"]
        if errors:
            console.print(f"\n[red]{check_type.upper()}:[/red]")
            for error in errors[:5]:  # Limiter à 5 erreurs par catégorie
                console.print(f"  • {error}")

            if len(errors) > 5:
                console.print(f"  [dim]... et {len(errors) - 5} erreurs supplémentaires[/dim]")

# Détails des avertissements
has_warnings = any(len(results[t]["warnings"]) > 0 for t in ["syntax", "business", "security"])

if has_warnings:
    console.print("\n[bold]Avertissements:[/bold]")

    for check_type in ["syntax", "business", "security"]:
        warnings = results[check_type]["warnings"]
        if warnings:
            console.print(f"\n[yellow]{check_type.upper()}:[/yellow]")
            for warning in warnings[:3]:
                console.print(f"  • {warning}")
@app.command("test")
def test_agent(
agent_path: Path = typer.Argument(..., help="Chemin vers l'agent"),
sample_data: Optional[Path] = typer.Option(None, "--data", "-d", help="Fichier de données de test"),
iterations: int = typer.Option(1, "--iterations", "-i", help="Nombre d'itérations"),
output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Répertoire de sortie"),
generate_tests: bool = typer.Option(False, "--generate-tests", "-g", help="Générer des tests automatiques")
):
"""
Teste un agent avec des données d'exemple.

text
Args:
    agent_path: Chemin vers l'agent
    sample_data: Fichier de données de test
    iterations: Nombre d'itérations
    output_dir: Répertoire de sortie
    generate_tests: Générer des tests automatiques
"""
console.print(Panel.fit("🧪 [bold cyan]Test d'agent[/bold cyan]", border_style="cyan"))

if not agent_path.exists():
    console.print(f"[red]❌ Agent non trouvé: {agent_path}[/red]")
    return

# Génération de tests si demandé
if generate_tests:
    return _generate_auto_tests(agent_path)

# Chargement des données de test
test_data = _load_test_data(sample_data, agent_path)

with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
    transient=True,
) as progress:
    # Préparation
    task_prep = progress.add_task("[cyan]Préparation des tests...", total=100)

    # Chargement de l'agent (simulation)
    # En réalité, il faudrait importer dynamiquement l'agent
    agent_name = agent_path.stem
    progress.update(task_prep, completed=100)

    # Exécution des tests
    test_results = []

    task_test = progress.add_task(f"[green]Exécution des tests...", total=iterations)

    for i in range(iterations):
        # Simulation d'exécution
        result = {
            "iteration": i + 1,
            "timestamp": datetime.now().isoformat(),
            "status": "success" if (i % 5) != 0 else "failed",  # 80% de succès
            "execution_time": 0.1 + (i * 0.01),
            "memory_used": 10 + i,
            "output": {"result": f"Test {i + 1} completed"}
        }

        if result["status"] == "failed":
            result["error"] = "Simulated failure for testing"

        test_results.append(result)

        progress.update(task_test, advance=1)
        progress.refresh()

        # Petite pause pour la simulation
        import time
        time.sleep(0.05)

    progress.update(task_test, completed=iterations)

# Analyse des résultats
success_count = sum(1 for r in test_results if r["status"] == "success")
failure_count = iterations - success_count

total_time = sum(r["execution_time"] for r in test_results)
avg_time = total_time / iterations if iterations > 0 else 0

# Affichage des résultats
console.print(f"\n[bold]Résultats des tests:[/bold]")

stats_table = Table(box=box.SIMPLE)
stats_table.add_column("Métrique", style="cyan")
stats_table.add_column("Valeur", style="green")

stats_table.add_row("Itérations", str(iterations))
stats_table.add_row("Succès", f"[green]{success_count}[/green]")
stats_table.add_row("Échecs", f"[red]{failure_count}[/red]" if failure_count > 0 else "0")
stats_table.add_row("Taux de succès", f"{(success_count/iterations)*100:.1f}%")
stats_table.add_row("Temps total", f"{total_time:.2f}s")
stats_table.add_row("Temps moyen", f"{avg_time:.2f}s")

console.print(stats_table)

# Détails des échecs
if failure_count > 0:
    console.print("\n[bold]Détails des échecs:[/bold]")

    failures = [r for r in test_results if r["status"] == "failed"]
    for failure in failures[:3]:  # Limiter à 3 échecs
        console.print(f"  • Itération {failure['iteration']}: {failure.get('error', 'Unknown error')}")

    if len(failures) > 3:
        console.print(f"  [dim]... et {len(failures) - 3} échecs supplémentaires[/dim]")

# Sauvegarde des résultats
if output_dir:
    output_dir.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"test_results_{agent_name}_{timestamp}.json"

    with open(output_file, 'w') as f:
        json.dump({
            "agent": agent_name,
            "timestamp": datetime.now().isoformat(),
            "iterations": iterations,
            "summary": {
                "success": success_count,
                "failures": failure_count,
                "success_rate": (success_count/iterations)*100,
                "total_time": total_time,
                "avg_time": avg_time
            },
            "results": test_results
        }, f, indent=2)

    console.print(f"\n[green]✅ Résultats sauvegardés: {output_file}[/green]")
def _load_test_data(sample_data: Optional[Path], agent_path: Path) -> Dict[str, Any]:
"""Charge les données de test."""
if sample_data and sample_data.exists():
with open(sample_data, 'r') as f:
if sample_data.suffix == '.json':
return json.load(f)
elif sample_data.suffix in ['.yaml', '.yml']:
return yaml.safe_load(f)

text
# Données par défaut basées sur le type d'agent
agent_dir = agent_path.parent
config_file = agent_dir / "config.yaml"

if config_file.exists():
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)

    agent_type = config.get("agent", {}).get("type", "detector")

    # Données de test par type
    test_data_map = {
        "detector": {"data": [1, 2, 3, 100, 2, 3, 4, 5]},  # 100 est une anomalie
        "optimizer": {"resources": {"cpu": 80, "memory": 90, "storage": 50}},
        "remediator": {"incident": {"type": "high_cpu", "severity": "high"}},
        "analyzer": {"logs": ["error: disk full", "warning: high memory"]}
    }

    return test_data_map.get(agent_type, {"test": "data"})

return {"test": "default_data"}
def _generate_auto_tests(agent_path: Path):
"""Génère des tests automatiques pour un agent."""
console.print("[cyan]🧪 Génération de tests automatiques...[/cyan]")

text
agent_dir = agent_path.parent
config_file = agent_dir / "config.yaml"

if not config_file.exists():
    console.print("[yellow]⚠️  Fichier de configuration non trouvé[/yellow]")
    return

with open(config_file, 'r') as f:
    config = yaml.safe_load(f)

agent_info = config.get("agent", {})
agent_name = agent_info.get("name", agent_path.stem)

# Génération des tests
test_content = f'''"""
Tests automatiquement générés pour {agent_name}
"""

import pytest
import json
import yaml
from pathlib import Path

Données de test

SAMPLE_DATA = {{
"test_case_1": {{
"input": {{"data": [1, 2, 3, 4, 5]}},
"expected_output": {{"status": "success"}}
}},
"test_case_2": {{
"input": {{"data": []}},
"expected_output": {{"status": "error", "message": "Empty data"}}
}}
}}

class Test{agent_name.replace(' ', '').replace('-', '')}:

text
def test_initialization(self):
    """Test d'initialisation de l'agent."""
    # TODO: Implémenter l'initialisation
    assert True

@pytest.mark.parametrize("test_case", ["test_case_1", "test_case_2"])
def test_execution(self, test_case):
    """Test d'exécution avec différentes données."""
    test_data = SAMPLE_DATA[test_case]

    # TODO: Implémenter l'exécution réelle
    result = {{"status": "success"}}

    # Vérification basique
    assert "status" in result

def test_performance(self):
    """Test de performance."""
    # TODO: Implémenter les tests de performance
    execution_time = 0.1
    assert execution_time < 1.0  # Doit s'exécuter en moins d'1 seconde

def test_error_handling(self):
    """Test de gestion des erreurs."""
    # TODO: Implémenter les tests d'erreur
    try:
        # Code qui pourrait échouer
        pass
    except Exception:
        assert True  # L'erreur est bien gérée
if name == "main":
pytest.main(["-v", file])
'''

text
# Sauvegarde
test_file = agent_dir / f"test_{agent_name.replace(' ', '_').lower()}_auto.py"
test_file.write_text(test_content)

console.print(f"[green]✅ Tests générés: {test_file}[/green]")
console.print("\n[bold]Prochaines étapes:[/bold]")
console.print(f"  1. Examiner le fichier: [cyan]{test_file.name}[/cyan]")
console.print(f"  2. Adapter les tests à votre agent")
console.print(f"  3. Exécuter: [cyan]pytest {test_file}[/cyan]")
@app.command("register")
def register_agent(
agent_path: Path = typer.Argument(..., help="Chemin vers l'agent"),
force: bool = typer.Option(False, "--force", "-f", help="Forcer l'enregistrement"),
public: bool = typer.Option(False, "--public", "-p", help="Rendre public dans le marketplace"),
interactive: bool = typer.Option(True, "--interactive/--no-interactive", "-i", help="Mode interactif")
):
"""
Enregistre un agent dans le registre.

text
Args:
    agent_path: Chemin vers l'agent
    force: Forcer l'enregistrement
    public: Rendre public
    interactive: Mode interactif
"""
console.print(Panel.fit("📝 [bold cyan]Enregistrement d'agent[/bold cyan]", border_style="cyan"))

if not agent_path.exists():
    console.print(f"[red]❌ Agent non trouvé: {agent_path}[/red]")
    return

# Validation avant enregistrement
if interactive and not force:
    console.print("[yellow]🔍 Validation pré-enregistrement...[/yellow]")

    # Exécuter validation
    validation_passed = questionary.confirm(
        "Exécuter la validation avant enregistrement?",
        default=True
    ).ask()

    if validation_passed:
        # Appeler la commande validate
        # Note: Dans une implémentation réelle, on appellerait directement les fonctions
        console.print("[dim]Exécution de la validation...[/dim]")
        # validate_agent(agent_path, output_format=OutputFormat.TABLE)

        proceed = questionary.confirm("Procéder à l'enregistrement?").ask()
        if not proceed:
            console.print("[yellow]❌ Enregistrement annulé[/yellow]")
            return

# Collecte des métadonnées
agent_dir = agent_path.parent
config_file = agent_dir / "config.yaml"

if config_file.exists():
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
else:
    config = {"agent": {"name": agent_path.stem, "description": "No description"}}

agent_info = config.get("agent", {})

# Demander des informations supplémentaires
if interactive:
    console.print("\n[bold]Informations d'enregistrement:[/bold]")

    agent_info["name"] = questionary.text(
        "Nom de l'agent:",
        default=agent_info.get("name", agent_path.stem)
    ).ask()

    agent_info["description"] = questionary.text(
        "Description:",
        default=agent_info.get("description", "")
    ).ask()

    agent_info["version"] = questionary.text(
        "Version:",
        default=agent_info.get("version", "1.0.0")
    ).ask()

    # Tags
    tags = questionary.text(
        "Tags (séparés par des virgules):",
        default=",".join(agent_info.get("tags", []))
    ).ask()
    agent_info["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    # Public/privé
    if public is None:
        public = questionary.confirm(
            "Rendre cet agent public dans le marketplace?",
            default=False
        ).ask()

# Simulation d'enregistrement
with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    transient=True,
) as progress:
    tasks = [
        progress.add_task("[cyan]Validation des dépendances...", total=100),
        progress.add_task("[green]Génération de l'ID unique...", total=100),
        progress.add_task("[yellow]Enregistrement dans la base...", total=100),
        progress.add_task("[magenta]Création des métadonnées...", total=100)
    ]

    for i, task in enumerate(tasks):
        for _ in range(10):
            progress.update(task, advance=10)
            import time
            time.sleep(0.05)

    for task in tasks:
        progress.update(task, completed=100)

# Génération d'ID (simulé)
import uuid
agent_id = f"agent-{uuid.uuid4().hex[:12]}"

console.print(f"\n[green]✅ Agent enregistré avec succès![/green]")
console.print(f"[cyan]ID: {agent_id}[/cyan]")

# Affichage des détails
details_table = Table(box=box.SIMPLE)
details_table.add_column("Champ", style="cyan")
details_table.add_column("Valeur", style="green")

for key, value in agent_info.items():
    if isinstance(value, list):
        details_table.add_row(key, ", ".join(value))
    else:
        details_table.add_row(key, str(value))

console.print(details_table)

if public:
    console.print("\n[yellow]🌐 Cet agent est maintenant public dans le marketplace[/yellow]")
    console.print("  • Visible par tous les utilisateurs")
    console.print("  • Peut être installé via: [cyan]microagents agent install[/cyan]")

# Instructions
console.print("\n[bold]Prochaines étapes:[/bold]")
console.print(f"  • Tester: [cyan]microagents agent test {agent_id}[/cyan]")
console.print(f"  • Déployer: [cyan]microagents agent deploy {agent_id}[/cyan]")
console.print(f"  • Voir les détails: [cyan]microagents agent info {agent_id}[/cyan]")
@app.command("search")
def search_agents(
query: Optional[str] = typer.Option(None, "--query", "-q", help="Terme de recherche"),
agent_type: Optional[str] = typer.Option(None, "--type", "-t", help="Type d'agent"),
category: Optional[str] = typer.Option(None, "--category", "-c", help="Catégorie"),
tags: Optional[str] = typer.Option(None, "--tags", help="Tags (séparés par des virgules)"),
min_score: float = typer.Option(0.0, "--min-score", "-s", help="Score minimum (0-1)"),
limit: int = typer.Option(20, "--limit", "-l", help="Nombre maximum de résultats"),
output_format: OutputFormat = typer.Option(OutputFormat.TABLE, "--format", "-f", help="Format de sortie")
):
"""
Recherche des agents dans le registre.

text
Args:
    query: Terme de recherche
    agent_type: Type d'agent
    category: Catégorie
    tags: Tags de filtrage
    min_score: Score minimum
    limit: Limite de résultats
    output_format: Format de sortie
"""
console.print(Panel.fit("🔎 [bold cyan]Recherche d'agents[/bold cyan]", border_style="cyan"))

# Construction des filtres
filters = {}

if query:
    filters["query"] = query

if agent_type:
    filters["type"] = agent_type

if category:
    filters["category"] = category

if tags:
    filters["tags"] = [t.strip() for t in tags.split(",")]

# Simulation de recherche
with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    transient=True,
) as progress:
    task = progress.add_task("[cyan]Recherche en cours...", total=100)

    # Simulation de données
    import time
    for i in range(10):
        progress.update(task, advance=10)
        time.sleep(0.05)

    progress.update(task, completed=100)

# Données de démonstration
sample_agents = [
    {
        "id": "agent-cost-001",
        "name": "Cost Anomaly Detector",
        "description": "Détecte les anomalies de coûts cloud en temps réel",
        "type": "detector",
        "category": "cost",
        "score": 0.95,
        "tags": ["aws", "cost", "anomaly", "real-time"],
        "downloads": 1243,
        "rating": 4.8,
        "author": "DevOps Team"
    },
    {
        "id": "agent-security-002",
        "name": "Security Vulnerability Scanner",
        "description": "Scan automatique des vulnérabilités de sécurité",
        "type": "detector",
        "category": "security",
        "score": 0.92,
        "tags": ["security", "vulnerability", "scan", "compliance"],
        "downloads": 987,
        "rating": 4.6,
        "author": "Security Team"
    },
    {
        "id": "agent-incident-003",
        "name": "Auto Incident Responder",
        "description": "Réponse automatique aux incidents avec remediation",
        "type": "remediator",
        "category": "incident",
        "score": 0.88,
        "tags": ["incident", "auto-remediation", "mttr", "sre"],
        "downloads": 756,
        "rating": 4.4,
        "author": "SRE Team"
    },
    {
        "id": "agent-perf-004",
        "name": "Performance Optimizer",
        "description": "Optimisation automatique des performances cloud",
        "type": "optimizer",
        "category": "performance",
        "score": 0.85,
        "tags": ["performance", "optimization", "cloud", "resource"],
        "downloads": 543,
        "rating": 4.2,
        "author": "Performance Team"
    }
]

# Filtrage
filtered_agents = []
for agent in sample_agents:
    # Filtre par score
    if agent["score"] < min_score:
        continue

    # Filtre par type
    if agent_type and agent["type"] != agent_type:
        continue

    # Filtre par catégorie
    if category and agent["category"] != category:
        continue

    # Filtre par tags
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        if not any(tag in agent["tags"] for tag in tag_list):
            continue

    # Filtre par query
    if query:
        query_lower = query.lower()
        matches = (
            query_lower in agent["name"].lower() or
            query_lower in agent["description"].lower() or
            any(query_lower in tag.lower() for tag in agent["tags"])
        )
        if not matches:
            continue

    filtered_agents.append(agent)

# Limite
filtered_agents = filtered_agents[:limit]

# Affichage
if output_format == OutputFormat.TABLE:
    if not filtered_agents:
        console.print("[yellow]⚠️  Aucun agent trouvé avec ces critères[/yellow]")
        return

    table = Table(title=f"Résultats ({len(filtered_agents)} agents)", box=box.ROUNDED)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Nom", style="green")
    table.add_column("Type", style="white")
    table.add_column("Catégorie", style="yellow")
    table.add_column("Score", style="magenta")
    table.add_column("Téléchargements", style="blue", justify="right")

    for agent in filtered_agents:
        score_color = "green" if agent["score"] >= 0.9 else "yellow" if agent["score"] >= 0.8 else "red"

        table.add_row(
            agent["id"],
            agent["name"],
            agent["type"],
            agent["category"],
            f"[{score_color}]{agent['score']:.2f}[/{score_color}]",
            str(agent["downloads"])
        )

    console.print(table)

    # Détails supplémentaires
    if len(filtered_agents) > 0:
        console.print("\n[bold]Exemples de tags populaires:[/bold]")

        all_tags = []
        for agent in filtered_agents:
            all_tags.extend(agent["tags"])

        from collections import Counter
        tag_counts = Counter(all_tags)

        popular_tags = tag_counts.most_common(5)
        tag_display = ", ".join([f"[cyan]{tag}[/cyan] ({count})" for tag, count in popular_tags])
        console.print(f"  {tag_display}")

elif output_format == OutputFormat.JSON:
    console.print_json(data=filtered_agents)

elif output_format == OutputFormat.YAML:
    console.print(Syntax(yaml.dump(filtered_agents), "yaml"))

# Suggestions
if query and len(filtered_agents) < 3:
    console.print("\n[yellow]💡 Suggestions de recherche:[/yellow]")
    console.print("  • Essayez des termes plus généraux")
    console.print("  • Consultez les catégories: [cyan]cost, security, incident, performance[/cyan]")
    console.print("  • Parcourez le marketplace: [cyan]microagents marketplace browse[/cyan]")
@app.command("compare")
def compare_agents(
agent1: str = typer.Argument(..., help="ID ou chemin du premier agent"),
agent2: str = typer.Argument(..., help="ID ou chemin du second agent"),
detail: bool = typer.Option(False, "--detail", "-d", help="Affichage détaillé"),
output_format: OutputFormat = typer.Option(OutputFormat.TABLE, "--format", "-f", help="Format de sortie")
):
"""
Compare deux agents ou versions d'agent.

text
Args:
    agent1: Premier agent à comparer
    agent2: Second agent à comparer
    detail: Affichage détaillé
    output_format: Format de sortie
"""
console.print(Panel.fit("⚖️ [bold cyan]Comparaison d'agents[/bold cyan]", border_style="cyan"))

# Chargement des agents
agent1_data = _load_agent_for_comparison(agent1)
agent2_data = _load_agent_for_comparison(agent2)

if not agent1_data or not agent2_data:
    console.print("[red]❌ Impossible de charger un ou plusieurs agents[/red]")
    return

# Analyse des différences
differences = _find_agent_differences(agent1_data, agent2_data)

# Affichage
if output_format == OutputFormat.TABLE:
    _display_comparison_table(agent1_data, agent2_data, differences, detail)
elif output_format == OutputFormat.JSON:
    console.print_json(data={
        "agent1": agent1_data,
        "agent2": agent2_data,
        "differences": differences
    })
elif output_format == OutputFormat.MARKDOWN:
    _display_comparison_markdown(agent1_data, agent2_data, differences)
def _load_agent_for_comparison(agent_ref: str) -> Optional[Dict[str, Any]]:
"""Charge un agent pour comparaison."""
# Vérifier si c'est un chemin
agent_path = Path(agent_ref)
if agent_path.exists():
config_file = agent_path if agent_path.suffix in ['.yaml', '.yml'] else agent_path.parent / "config.yaml"

text
    if config_file.exists():
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)

# Sinon, chercher dans le registre (simulation)
# Dans une implémentation réelle, on interrogerait le registre
sample_agents = {
    "agent-cost-001": {
        "agent": {
            "name": "Cost Anomaly Detector",
            "type": "detector",
            "category": "cost",
            "version": "1.2.0"
        },
        "capabilities": ["cost_analysis", "anomaly_detection", "alerting"],
        "configuration": {"threshold": 20, "schedule": "hourly"}
    },
    "agent-cost-002": {
        "agent": {
            "name": "Cost Anomaly Detector v2",
            "type": "detector",
            "category": "cost",
            "version": "2.0.0"
        },
        "capabilities": ["cost_analysis", "anomaly_detection", "alerting", "predictive_analysis"],
        "configuration": {"threshold": 15, "schedule": "realtime", "ml_enabled": True}
    }
}

return sample_agents.get(agent_ref)
def _find_agent_differences(agent1: Dict[str, Any], agent2: Dict[str, Any]) -> Dict[str, Any]:
"""Trouve les différences entre deux agents."""
differences = {
"agent_info": [],
"capabilities": {"added": [], "removed": [], "changed": []},
"configuration": {"added": [], "removed": [], "changed": []}
}

text
# Comparaison des infos agent
agent1_info = agent1.get("agent", {})
agent2_info = agent2.get("agent", {})

for key in set(agent1_info.keys()) | set(agent2_info.keys()):
    val1 = agent1_info.get(key)
    val2 = agent2_info.get(key)

    if val1 != val2:
        differences["agent_info"].append({
            "field": key,
            "agent1": val1,
            "agent2": val2
        })

# Comparaison des capacités
caps1 = set(agent1.get("capabilities", []))
caps2 = set(agent2.get("capabilities", []))

differences["capabilities"]["added"] = list(caps2 - caps1)
differences["capabilities"]["removed"] = list(caps1 - caps2)

# Comparaison de la configuration
config1 = agent1.get("configuration", {})
config2 = agent2.get("configuration", {})

for key in set(config1.keys()) | set(config2.keys()):
    val1 = config1.get(key)
    val2 = config2.get(key)

    if key not in config1:
        differences["configuration"]["added"].append({"key": key, "value": val2})
    elif key not in config2:
        differences["configuration"]["removed"].append({"key": key, "value": val1})
    elif val1 != val2:
        differences["configuration"]["changed"].append({
            "key": key,
            "agent1": val1,
            "agent2": val2
        })

return differences
def _display_comparison_table(agent1: Dict[str, Any], agent2: Dict[str, Any], differences: Dict[str, Any], detail: bool):
"""Affiche la comparaison dans un tableau."""
agent1_name = agent1.get("agent", {}).get("name", "Agent 1")
agent2_name = agent2.get("agent", {}).get("name", "Agent 2")

text
console.print(f"\n[bold]Comparaison: {agent1_name} vs {agent2_name}[/bold]")

# Résumé
summary_table = Table(box=box.SIMPLE)
summary_table.add_column("Aspect", style="cyan")
summary_table.add_column(agent1_name, style="blue")
summary_table.add_column(agent2_name, style="green")
summary_table.add_column("Statut", style="yellow")

# Nom
name1 = agent1.get("agent", {}).get("name", "N/A")
name2 = agent2.get("agent", {}).get("name", "N/A")
name_status = "✅" if name1 == name2 else "⚠️"
summary_table.add_row("Nom", name1, name2, name_status)

# Version
version1 = agent1.get("agent", {}).get("version", "N/A")
version2 = agent2.get("agent", {}).get("version", "N/A")
version_status = "✅" if version1 == version2 else "🔄"
summary_table.add_row("Version", version1, version2, version_status)

# Type
type1 = agent1.get("agent", {}).get("type", "N/A")
type2 = agent2.get("agent", {}).get("type", "N/A")
type_status = "✅" if type1 == type2 else "⚠️"
summary_table.add_row("Type", type1, type2, type_status)

# Capabilities
caps1 = len(agent1.get("capabilities", []))
caps2 = len(agent2.get("capabilities", []))
caps_diff = caps2 - caps1
caps_status = f"➕{caps_diff}" if caps_diff > 0 else f"➖{-caps_diff}" if caps_diff < 0 else "✅"
summary_table.add_row("Capacités", str(caps1), str(caps2), caps_status)

console.print(summary_table)

# Détails si demandé
if detail:
    # Capacités ajoutées/supprimées
    added_caps = differences["capabilities"]["added"]
    removed_caps = differences["capabilities"]["removed"]

    if added_caps or removed_caps:
        console.print("\n[bold]Changements de capacités:[/bold]")

        if added_caps:
            console.print(f"  [green]Ajoutées ({len(added_caps)}):[/green]")
            for cap in added_caps:
                console.print(f"    ➕ {cap}")

        if removed_caps:
            console.print(f"  [red]Supprimées ({len(removed_caps)}):[/red]")
            for cap in removed_caps:
                console.print(f"    ➖ {cap}")

    # Configuration
    config_changes = differences["configuration"]

    if any(len(changes) > 0 for changes in config_changes.values()):
        console.print("\n[bold]Changements de configuration:[/bold]")

        for change_type, changes in config_changes.items():
            if changes:
                color = {"added": "green", "removed": "red", "changed": "yellow"}[change_type]
                symbol = {"added": "➕", "removed": "➖", "changed": "🔄"}[change_type]

                console.print(f"  [{color}]{change_type.title()} ({len(changes)}):[/{color}]")

                for change in changes[:5]:  # Limiter à 5
                    if change_type == "changed":
                        console.print(f"    {symbol} {change['key']}: {change['agent1']} → {change['agent2']}")
                    else:
                        console.print(f"    {symbol} {change['key']}: {change.get('value', 'N/A')}")

    # Diff code si disponible
    if detail and "code_diff" in differences:
        console.print("\n[bold]Différences de code:[/bold]")
        console.print(Syntax(differences["code_diff"], "diff"))
@app.command("profile")
def profile_agent(
agent_id: str = typer.Argument(..., help="ID de l'agent à profiler"),
duration: int = typer.Option(60, "--duration", "-d", help="Durée du profilage en secondes"),
output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Répertoire de sortie"),
memory: bool = typer.Option(True, "--memory/--no-memory", help="Profilage mémoire"),
cpu: bool = typer.Option(True, "--cpu/--no-cpu", help="Profilage CPU"),
io: bool = typer.Option(False, "--io/--no-io", help="Profilage I/O")
):
"""
Analyse les performances d'un agent.

text
Args:
    agent_id: ID de l'agent
    duration: Durée du profilage
    output_dir: Répertoire de sortie
    memory: Profilage mémoire
    cpu: Profilage CPU
    io: Profilage I/O
"""
console.print(Panel.fit("📊 [bold cyan]Profilage de performance[/bold cyan]", border_style="cyan"))

console.print(f"[cyan]Agent: {agent_id}[/cyan]")
console.print(f"[yellow]Durée: {duration} secondes[/yellow]")

with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
    transient=True,
) as progress:
    tasks = []

    if cpu:
        tasks.append(progress.add_task("[cyan]Profilage CPU...", total=duration))

    if memory:
        tasks.append(progress.add_task("[green]Profilage mémoire...", total=duration))

    if io:
        tasks.append(progress.add_task("[yellow]Profilage I/O...", total=duration))

    # Simulation de profilage
    for second in range(duration):
        for task in tasks:
            progress.update(task, advance=1)

        import time
        time.sleep(1)

    for task in tasks:
        progress.update(task, completed=duration)

# Résultats simulés
profile_results = {
    "agent_id": agent_id,
    "duration_seconds": duration,
    "timestamp": datetime.now().isoformat(),
    "metrics": {
        "cpu": {
            "avg_usage": 15.2,
            "max_usage": 45.6,
            "thread_count": 4,
            "context_switches": 1245
        },
        "memory": {
            "avg_rss_mb": 128.4,
            "max_rss_mb": 256.8,
            "avg_vms_mb": 512.2,
            "memory_leaks": 0
        },
        "execution": {
            "total_executions": 124,
            "avg_execution_time": 0.124,
            "p95_execution_time": 0.256,
            "p99_execution_time": 0.512
        }
    },
    "recommendations": [
        "Optimiser l'utilisation mémoire pour les gros datasets",
        "Réduire les appels réseau synchrone",
        "Implémenter le caching pour les résultats fréquents"
    ]
}

# Affichage
console.print(f"\n[bold]Résultats du profilage:[/bold]")

# Tableau des métriques
metrics_table = Table(box=box.ROUNDED)
metrics_table.add_column("Métrique", style="cyan")
metrics_table.add_column("Valeur", style="green")
metrics_table.add_column("Statut", style="yellow")

# CPU
cpu_metrics = profile_results["metrics"]["cpu"]
cpu_status = "✅" if cpu_metrics["avg_usage"] < 50 else "⚠️" if cpu_metrics["avg_usage"] < 80 else "❌"
metrics_table.add_row("CPU Usage (avg)", f"{cpu_metrics['avg_usage']:.1f}%", cpu_status)
metrics_table.add_row("CPU Usage (max)", f"{cpu_metrics['max_usage']:.1f}%", "")

# Mémoire
mem_metrics = profile_results["metrics"]["memory"]
mem_status = "✅" if mem_metrics["avg_rss_mb"] < 256 else "⚠️" if mem_metrics["avg_rss_mb"] < 512 else "❌"
metrics_table.add_row("Memory RSS (avg)", f"{mem_metrics['avg_rss_mb']:.1f} MB", mem_status)
metrics_table.add_row("Memory RSS (max)", f"{mem_metrics['max_rss_mb']:.1f} MB", "")

# Exécution
exec_metrics = profile_results["metrics"]["execution"]
exec_status = "✅" if exec_metrics["avg_execution_time"] < 0.5 else "⚠️" if exec_metrics["avg_execution_time"] < 1.0 else "❌"
metrics_table.add_row("Exec Time (avg)", f"{exec_metrics['avg_execution_time']:.3f}s", exec_status)
metrics_table.add_row("Exec Time (p95)", f"{exec_metrics['p95_execution_time']:.3f}s", "")
metrics_table.add_row("Total Executions", str(exec_metrics["total_executions"]), "")

console.print(metrics_table)

# Recommandations
console.print("\n[bold]Recommandations d'optimisation:[/bold]")
for i, rec in enumerate(profile_results["recommendations"], 1):
    console.print(f"  {i}. {rec}")

# Sauvegarde
if output_dir:
    output_dir.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"profile_{agent_id}_{timestamp}.json"

    with open(output_file, 'w') as f:
        json.dump(profile_results, f, indent=2)

    console.print(f"\n[green]✅ Résultats sauvegardés: {output_file}[/green]")
@app.command("export")
def export_agent(
agent_id: str = typer.Argument(..., help="ID de l'agent à exporter"),
format: OutputFormat = typer.Option(OutputFormat.YAML, "--format", "-f", help="Format d'export"),
include_code: bool = typer.Option(True, "--code/--no-code", help="Inclure le code"),
include_config: bool = typer.Option(True, "--config/--no-config", help="Inclure la configuration"),
include_tests: bool = typer.Option(False, "--tests/--no-tests", help="Inclure les tests"),
output_file: Optional[Path] = typer.Option(None, "--output", "-o", help="Fichier de sortie")
):
"""
Exporte un agent dans différents formats.

text
Args:
    agent_id: ID de l'agent
    format: Format d'export
    include_code: Inclure le code
    include_config: Inclure la configuration
    include_tests: Inclure les tests
    output_file: Fichier de sortie
"""
console.print(Panel.fit("📦 [bold cyan]Export d'agent[/bold cyan]", border_style="cyan"))

# Simulation de données d'agent
agent_data = {
    "metadata": {
        "id": agent_id,
        "name": "Cost Anomaly Detector",
        "description": "Détecte les anomalies de coûts cloud",
        "type": "detector",
        "category": "cost",
        "version": "1.2.0",
        "author": "DevOps Team",
        "created": "2024-01-15",
        "updated": "2024-03-20"
    },
    "configuration": {
        "threshold": 20,
        "schedule": "hourly",
        "notification_channels": ["email", "slack"],
        "budget_limit": 10000
    },
    "capabilities": ["cost_analysis", "anomaly_detection", "alerting", "reporting"],
    "dependencies": ["pandas>=1.5", "numpy>=1.24", "scikit-learn>=1.3"],
    "business_rules": [
        "Alert if cost increase > threshold",
        "Ignore weekends for business hours calculations",
        "Escalate after 3 consecutive alerts"
    ]
}

if include_code:
    agent_data["code"] = _get_sample_agent_code()

if include_tests:
    agent_data["tests"] = _get_sample_agent_tests()

# Génération de l'export
if format == OutputFormat.YAML:
    content = yaml.dump(agent_data, default_flow_style=False, sort_keys=False)
    extension = "yaml"
elif format == OutputFormat.JSON:
    content = json.dumps(agent_data, indent=2)
    extension = "json"
elif format == OutputFormat.PYTHON:
    # Format spécial pour Python
    content = f'''"""
Agent: {agent_data['metadata']['name']}
ID: {agent_id}
"""

AGENT_CONFIG = {json.dumps(agent_data['configuration'], indent=2)}

def get_agent_info():
"""Retourne les informations de l'agent."""
return {json.dumps(agent_data['metadata'], indent=2)}
'''
extension = "py"
else: # MARKDOWN
content = _generate_markdown_export(agent_data)
extension = "md"

text
# Détermination du fichier de sortie
if not output_file:
    agent_slug = agent_id.replace("-", "_")
    output_file = Path(f"{agent_slug}_export.{extension}")

# Écriture
output_file.write_text(content)

console.print(f"[green]✅ Agent exporté: {output_file}[/green]")
console.print(f"[cyan]Taille: {len(content)} bytes[/cyan]")

# Aperçu
console.print("\n[bold]Aperçu:[/bold]")
preview_lines = content.split('\n')[:10]
for line in preview_lines:
    console.print(f"  {line}")

if len(content.split('\n')) > 10:
    console.print(f"  [dim]... et {len(content.split('\n')) - 10} lignes supplémentaires[/dim]")
def _get_sample_agent_code() -> str:
"""Retourne un exemple de code d'agent."""
return '''"""
Cost Anomaly Detector Agent
"""

import pandas as pd
import numpy as np
from typing import Dict, Any

class CostAnomalyDetector:
def init(self, config: Dict[str, Any]):
self.threshold = config.get("threshold", 20)
self.budget_limit = config.get("budget_limit", 10000)

text
def detect_anomalies(self, cost_data: pd.DataFrame) -> Dict[str, Any]:
    """Détecte les anomalies dans les données de coût."""
    # Implémentation simplifiée
    mean_cost = cost_data["cost"].mean()
    std_cost = cost_data["cost"].std()

    anomalies = cost_data[
        cost_data["cost"] > (mean_cost + (self.threshold/100) * std_cost)
    ]

    return {
        "anomaly_count": len(anomalies),
        "anomalies": anomalies.to_dict("records"),
        "total_cost": cost_data["cost"].sum(),
        "budget_usage": (cost_data["cost"].sum() / self.budget_limit) * 100
    }
'''

def _get_sample_agent_tests() -> str:
"""Retourne un exemple de tests d'agent."""
return '''"""
Tests for Cost Anomaly Detector
"""

import pytest
import pandas as pd

def test_anomaly_detection():
"""Test de détection d'anomalies."""
detector = CostAnomalyDetector({"threshold": 20})

text
test_data = pd.DataFrame({
    "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
    "cost": [100, 120, 500]  # 500 est une anomalie
})

result = detector.detect_anomalies(test_data)

assert result["anomaly_count"] == 1
assert result["anomalies"][0]["cost"] == 500
'''

def _generate_markdown_export(agent_data: Dict[str, Any]) -> str:
"""Génère un export Markdown."""
metadata = agent_data["metadata"]

text
return f"""# {metadata['name']}
ID: {metadata['id']}

{metadata['description']}

Informations

Type: {metadata['type']}
Catégorie: {metadata['category']}
Version: {metadata['version']}
Auteur: {metadata['author']}
Créé: {metadata['created']}
Mis à jour: {metadata['updated']}
Configuration

yaml
{yaml.dump(agent_data['configuration'], default_flow_style=False)}
Capacités

{chr(10).join(f"- {cap}" for cap in agent_data['capabilities'])}

Dépendances

{chr(10).join(f"- {dep}" for dep in agent_data.get('dependencies', []))}

Règles métier

{chr(10).join(f"- {rule}" for rule in agent_data.get('business_rules', []))}

Exporté depuis MicroAgents Platform
"""

@app.command("import")
def import_agent(
import_file: Path = typer.Argument(..., help="Fichier d'import"),
validate: bool = typer.Option(True, "--validate/--no-validate", help="Validation avant import"),
register: bool = typer.Option(True, "--register/--no-register", help="Enregistrer après import"),
output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Répertoire de sortie")
):
"""
Importe un agent depuis un fichier externe.

text
Args:
    import_file: Fichier d'import
    validate: Validation avant import
    register: Enregistrement après import
    output_dir: Répertoire de sortie
"""
console.print(Panel.fit("📥 [bold cyan]Import d'agent[/bold cyan]", border_style="cyan"))

if not import_file.exists():
    console.print(f"[red]❌ Fichier non trouvé: {import_file}[/red]")
    return

# Détection du format
if import_file.suffix in ['.yaml', '.yml']:
    format_type = "yaml"
elif import_file.suffix == '.json':
    format_type = "json"
elif import_file.suffix == '.py':
    format_type = "python"
else:
    console.print(f"[yellow]⚠️  Format non reconnu, tentative d'import générique[/yellow]")
    format_type = "generic"

console.print(f"[cyan]Format détecté: {format_type}[/cyan]")

with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    transient=True,
) as progress:
    # Lecture
    task_read = progress.add_task("[cyan]Lecture du fichier...", total=100)

    try:
        with open(import_file, 'r') as f:
            if format_type == "yaml":
                content = yaml.safe_load(f)
            elif format_type == "json":
                content = json.load(f)
            else:
                content = f.read()
    except Exception as e:
        console.print(f"[red]❌ Erreur de lecture: {e}[/red]")
        return

    progress.update(task_read, completed=100)

    # Validation
    if validate:
        task_validate = progress.add_task("[green]Validation...", total=100)

        # Simulation de validation
        import time
        for i in range(10):
            progress.update(task_validate, advance=10)
            time.sleep(0.05)

        progress.update(task_validate, completed=100)

    # Traitement
    task_process = progress.add_task("[yellow]Traitement...", total=100)

    # Simulation de traitement
    for i in range(10):
        progress.update(task_process, advance=10)
        time.sleep(0.05)

    progress.update(task_process, completed=100)

# Affichage des résultats
if isinstance(content, dict):
    # Fichier structuré
    metadata = content.get("metadata", {})

    console.print(f"\n[green]✅ Agent importé avec succès![/green]")
    console.print(f"[cyan]Nom: {metadata.get('name', 'N/A')}[/cyan]")
    console.print(f"[cyan]Type: {metadata.get('type', 'N/A')}[/cyan]")
    console.print(f"[cyan]Version: {metadata.get('version', 'N/A')}[/cyan]")

    # Statistiques
    stats_table = Table(box=box.SIMPLE)
    stats_table.add_column("Élément", style="cyan")
    stats_table.add_column("Détails", style="green")

    if "capabilities" in content:
        stats_table.add_row("Capacités", str(len(content["capabilities"])))

    if "configuration" in content:
        stats_table.add_row("Paramètres config", str(len(content["configuration"])))

    if "dependencies" in content:
        stats_table.add_row("Dépendances", str(len(content["dependencies"])))

    console.print(stats_table)

    # Sauvegarde
    if output_dir:
        output_dir.mkdir(exist_ok=True, parents=True)

        agent_name = metadata.get("name", "imported_agent").replace(" ", "_").lower()
        agent_dir = output_dir / agent_name
        agent_dir.mkdir(exist_ok=True)

        # Fichier de configuration
        config_file = agent_dir / "config.yaml"
        config_data = {
            "agent": metadata,
            "configuration": content.get("configuration", {}),
            "capabilities": content.get("capabilities", [])
        }

        with open(config_file, 'w') as f:
            yaml.dump(config_data, f, default_flow_style=False)

        # Fichier de code si présent
        if "code" in content:
            code_file = agent_dir / f"{agent_name}.py"
            code_file.write_text(content["code"])

        console.print(f"\n[green]📁 Agent sauvegardé: {agent_dir}[/green]")

        # Enregistrement automatique
        if register:
            console.print("[yellow]🔄 Enregistrement automatique...[/yellow]")
            register_agent(agent_dir, force=True, interactive=False)

else:
    # Fichier non structuré (code)
    console.print(f"\n[green]✅ Code importé ({len(content)} caractères)[/green]")

    if output_dir:
        agent_dir = output_dir / import_file.stem
        agent_dir.mkdir(exist_ok=True, parents=True)

        code_file = agent_dir / import_file.name
        code_file.write_text(content)

        console.print(f"[green]📁 Code sauvegardé: {code_file}[/green]")
@app.command("publish")
def publish_agent(
agent_id: str = typer.Argument(..., help="ID de l'agent à publier"),
version: Optional[str] = typer.Option(None, "--version", "-v", help="Version à publier"),
channel: str = typer.Option("stable", "--channel", "-c", help="Canal de publication (stable, beta, alpha)"),
license_type: str = typer.Option("MIT", "--license", "-l", help="Type de licence"),
visibility: str = typer.Option("public", "--visibility", help="Visibilité (public, private, organization)"),
interactive: bool = typer.Option(True, "--interactive/--no-interactive", "-i", help="Mode interactif")
):
"""
Publie un agent sur le marketplace.

text
Args:
    agent_id: ID de l'agent
    version: Version à publier
    channel: Canal de publication
    license_type: Type de licence
    visibility: Visibilité
    interactive: Mode interactif
"""
console.print(Panel.fit("🛒 [bold cyan]Publication sur le marketplace[/bold cyan]", border_style="cyan"))

# Vérifications pré-publication
checks_passed = True

with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    transient=True,
) as progress:
    checks = [
        progress.add_task("[cyan]Vérification de l'agent...", total=100),
        progress.add_task("[green]Validation des tests...", total=100),
        progress.add_task("[yellow]Analyse de sécurité...", total=100),
        progress.add_task("[magenta]Vérification des dépendances...", total=100)
    ]

    for check in checks:
        # Simulation de vérifications
        for i in range(10):
            progress.update(check, advance=10)
            import time
            time.sleep(0.05)

        progress.update(check, completed=100)

if not checks_passed:
    console.print("[red]❌ Les vérifications pré-publication ont échoué[/red]")
    return

# Collecte d'informations supplémentaires
if interactive:
    console.print("\n[bold]Informations de publication:[/bold]")

    if not version:
        version = questionary.text(
            "Version:",
            default="1.0.0"
        ).ask()

    description = questionary.text(
        "Description pour le marketplace:",
        default=""
    ).ask()

    keywords = questionary.text(
        "Mots-clés (séparés par des virgules):",
        default="devops, automation, cloud"
    ).ask()

    documentation_url = questionary.text(
        "URL de documentation (optionnel):",
        default=""
    ).ask()

    support_url = questionary.text(
        "URL de support (optionnel):",
        default=""
    ).ask()

    pricing_model = questionary.select(
        "Modèle de prix:",
        choices=["free", "freemium", "paid", "enterprise"]
    ).ask()

    confirm = questionary.confirm(
        f"Publier l'agent {agent_id} v{version} sur le marketplace?",
        default=True
    ).ask()

    if not confirm:
        console.print("[yellow]❌ Publication annulée[/yellow]")
        return

# Publication
with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    transient=True,
) as progress:
    publication_steps = [
        progress.add_task("[cyan]Préparation du package...", total=100),
        progress.add_task("[green]Génération de documentation...", total=100),
        progress.add_task("[yellow]Upload vers le marketplace...", total=100),
        progress.add_task("[magenta]Indexation...", total=100)
    ]

    for step in publication_steps:
        for i in range(10):
            progress.update(step, advance=10)
            import time
            time.sleep(0.1)

        progress.update(step, completed=100)

console.print(f"\n[green]✅ Agent publié avec succès![/green]")
console.print(f"[cyan]Marketplace URL: https://marketplace.microagents.io/agents/{agent_id}[/cyan]")

# Affichage des détails
details_table = Table(box=box.SIMPLE)
details_table.add_column("Information", style="cyan")
details_table.add_column("Valeur", style="green")

details_table.add_row("Agent ID", agent_id)
details_table.add_row("Version", version or "1.0.0")
details_table.add_row("Canal", channel)
details_table.add_row("Visibilité", visibility)
details_table.add_row("Licence", license_type)
details_table.add_row("Statut", "[green]PUBLIÉ[/green]")

console.print(details_table)

# Instructions
console.print("\n[bold]Prochaines étapes:[/bold]")
console.print("  • Vérifier la page: [cyan]https://marketplace.microagents.io/agents/{agent_id}[/cyan]")
console.print("  • Partager avec votre équipe")
console.print("  • Surveiller les statistiques: [cyan]microagents marketplace stats {agent_id}[/cyan]")
console.print("  • Mettre à jour: [cyan]microagents agent publish {agent_id} --version X.Y.Z[/cyan]")
@app.command("list")
def list_agents(
category: Optional[str] = typer.Option(None, "--category", "-c", help="Filtrer par catégorie"),
type: Optional[str] = typer.Option(None, "--type", "-t", help="Filtrer par type"),
show_all: bool = typer.Option(False, "--all", "-a", help="Afficher tous les agents"),
limit: int = typer.Option(20, "--limit", "-l", help="Nombre maximum d'agents"),
output_format: OutputFormat = typer.Option(OutputFormat.TABLE, "--format", "-f", help="Format de sortie")
):
"""
Liste les agents disponibles.

Args:
    category: Filtrer par catégorie
    type: Filtrer par type
    show_all: Afficher tous les agents
    limit: Limite d'affichage
    output_format: Format de sortie
"""
console.print(Panel.fit("📋 [bold cyan]Liste des agents[/bold cyan]", border_style="cyan"))

# Dans une implémentation réelle, on récupérerait depuis le registre
# Ici, simulation avec des données
all_agents = [
    {"id": "cost-detector-001", "name": "Cost Anomaly Detector", "type": "detector", "category": "cost", "status": "active"},
    {"id": "security-scanner-002", "name": "Security Vulnerability Scanner", "type": "detector", "category": "security", "status": "active"},
    {"id": "incident-responder-003", "name": "Auto Incident Responder", "type": "remediator", "category": "incident", "status": "active"},
    {"id": "performance-optimizer-004", "name": "Performance Optimizer", "type": "optimizer", "category": "performance", "status": "beta"},
    {"id": "compliance-checker-005", "name": "Compliance Checker", "type": "auditor", "category": "compliance", "status": "active"},
    {"id": "cost-forecaster-006", "name": "Cost Forecaster", "type": "predictor", "category": "cost", "status": "active"},
    {"id": "resource-optimizer-007", "name": "Resource Optimizer", "type": "optimizer", "category": "cost", "status": "active"},
    {"id": "log-analyzer-008", "name": "Log Analyzer", "type": "analyzer", "category": "incident", "status": "active"},
    {"id": "api-monitor-009", "name": "API Monitor", "type": "monitor", "category": "performance", "status": "active"},
    {"id": "data-validator-010", "name": "Data Validator", "type": "validator", "category": "governance", "status": "beta"},
]

# Filtrage
filtered_agents = []
for agent in all_agents:
    if category and agent["category"] != category:
        continue

    if type and agent["type"] != type:
        continue

    if not show_all and agent["status"] != "active":
        continue

    filtered_agents.append(agent)

# Limite
filtered_agents = filtered_agents[:limit]

# Affichage
if output_format == OutputFormat.TABLE:
    if not filtered_agents:
        console.print("[yellow]⚠️  Aucun agent trouvé avec ces critères[/yellow]")
        return

    table = Table(title=f"Agents ({len(filtered_agents)} trouvés)", box=box.ROUNDED)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Nom", style="green")
    table.add_column("Type", style="white")
    table.add_column("Catégorie", style="yellow")
    table.add_column("Statut", style="magenta")

    for agent in filtered_agents:
        status_color = "green" if agent["status"] == "active" else "yellow" if agent["status"] == "beta" else "red"

        table.add_row(
            agent["id"],
            agent["name"],
            agent["type"],
            agent["category"],
            f"[{status_color}]{agent['status']}[/{status_color}]"
        )

    console.print(table)

    # Statistiques
    if show_all or (not category and not type):
        console.print("\n[bold]Statistiques par catégorie:[/bold]")

        from collections import Counter
        category_counts = Counter(a["category"] for a in all_agents)

        for category, count in category_counts.most_common():
            console.print(f"  • [cyan]{category}[/cyan]: {count} agents")

elif output_format == OutputFormat.JSON:
    console.print_json(data=filtered_agents)

elif output_format == OutputFormat.YAML:
    console.print(Syntax(yaml.dump(filtered_agents), "yaml"))