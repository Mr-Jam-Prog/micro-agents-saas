"""
Jinja2 Template Engine for MicroAgents Platform

Moteur de template avancé avec support pour:
- Chargement et cache de templates
- Résolution de variables de contexte
- Filtres et extensions personnalisés
- Support d'internationalisation
- Fonctionnalités de sécurité
- Optimisations de performance
- Reporting d'erreurs détaillé
- Héritage de templates
- Système de macros
- Intégration avec framework de tests

Extensions personnalisées:
- Filtres de logique métier
- Filtres de formatage de code
- Filtres de validation de sécurité
- Macros d'optimisation de performance
- Helpers de génération de documentation
- Générateurs de code de test
- Générateurs de configuration de déploiement
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Union,
    Pattern, TypeVar, Generic, cast
)
from functools import lru_cache, wraps
from contextlib import contextmanager
from collections import defaultdict
import inspect
import textwrap

import jinja2
from jinja2 import (
    Environment, FileSystemLoader, PackageLoader, ChoiceLoader,
    Template, TemplateNotFound, TemplateSyntaxError, TemplateRuntimeError,
    UndefinedError, meta
)
from jinja2.ext import Extension, InternationalizationExtension
from jinja2.sandbox import SandboxedEnvironment
from jinja2.nodes import Node
from jinja2.lexer import TokenStream
from jinja2.exceptions import SecurityError
import pydantic
from pydantic import BaseModel, Field, validator, root_validator

from src.utils.serialization.serializers import json_serializer
from src.utils.security.utils import sanitize_html, escape_shell
from src.utils.validation.validators import validate_email, validate_url

logger = logging.getLogger(__name__)

T = TypeVar('T')


class TemplateEngineMode(Enum):
    """Modes de fonctionnement du moteur de template"""
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class TemplateCacheStrategy(Enum):
    """Stratégies de cache des templates"""
    NONE = "none"
    MEMORY = "memory"
    REDIS = "redis"
    FILESYSTEM = "filesystem"


class SecurityLevel(Enum):
    """Niveaux de sécurité pour le rendering"""
    SANDBOXED = "sandboxed"  # Environnement sandbox complet
    RESTRICTED = "restricted"  # Accès limité aux fonctions safe
    TRUSTED = "trusted"  # Environnement de confiance
    UNSAFE = "unsafe"  # Pas de restrictions (dangereux)


class LanguageCode(Enum):
    """Codes de langue supportés"""
    EN = "en"  # Anglais
    FR = "fr"  # Français
    DE = "de"  # Allemand
    ES = "es"  # Espagnol
    JA = "ja"  # Japonais
    ZH = "zh"  # Chinois


@dataclass
class TemplateConfig:
    """Configuration du moteur de template"""
    
    # Chemins et loaders
    template_dirs: List[Path] = field(default_factory=lambda: [Path("templates")])
    package_loader_paths: List[str] = field(default_factory=list)
    
    # Cache
    cache_strategy: TemplateCacheStrategy = TemplateCacheStrategy.MEMORY
    cache_ttl_seconds: int = 300
    max_cache_size: int = 1000
    
    # Sécurité
    security_level: SecurityLevel = SecurityLevel.SANDBOXED
    autoescape: bool = True
    strip_whitespace: bool = True
    lstrip_blocks: bool = True
    trim_blocks: bool = True
    
    # Performance
    enable_bytecode_cache: bool = True
    bytecode_cache_size: int = 100
    optimize_whitespace: bool = True
    
    # Internationalisation
    default_language: LanguageCode = LanguageCode.EN
    enable_i18n: bool = True
    translation_dirs: List[Path] = field(default_factory=list)
    
    # Debug
    debug: bool = False
    auto_reload: bool = True
    strict_undefined: bool = True
    
    # Extensions
    custom_extensions: List[str] = field(default_factory=list)
    jinja2_extensions: List[str] = field(default_factory=lambda: [
        "jinja2.ext.do",
        "jinja2.ext.loopcontrols",
        "jinja2.ext.i18n",
        "jinja2.ext.debug"
    ])
    
    # Validation
    max_template_size_mb: int = 10
    max_context_size_mb: int = 5
    allowed_functions: Set[str] = field(default_factory=lambda: {
        'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'tuple',
        'min', 'max', 'sum', 'sorted', 'reversed', 'enumerate', 'zip',
        'range', 'round', 'abs'
    })
    
    # Monitoring
    enable_metrics: bool = True
    log_rendering_times: bool = True


class TemplateMetrics(BaseModel):
    """Métriques de performance des templates"""
    
    render_count: int = 0
    total_render_time_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    error_count: int = 0
    avg_render_time_ms: float = 0.0
    
    class Config:
        frozen = True


class TemplateRenderResult(BaseModel):
    """Résultat du rendering d'un template"""
    
    content: str
    render_time_ms: float
    cached: bool = False
    template_name: str
    context_hash: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    class Config:
        frozen = True


class TemplateError(Exception):
    """Exception de base pour les erreurs de template"""
    
    def __init__(
        self,
        message: str,
        template_name: Optional[str] = None,
        line_number: Optional[int] = None,
        error_type: str = "TEMPLATE_ERROR"
    ):
        self.message = message
        self.template_name = template_name
        self.line_number = line_number
        self.error_type = error_type
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'erreur en dictionnaire"""
        return {
            "error_type": self.error_type,
            "message": self.message,
            "template": self.template_name,
            "line": self.line_number,
            "timestamp": datetime.utcnow().isoformat()
        }


class TemplateSecurityError(TemplateError):
    """Erreur de sécurité dans un template"""
    
    def __init__(self, message: str, template_name: Optional[str] = None):
        super().__init__(message, template_name, error_type="SECURITY_ERROR")


class TemplateValidationError(TemplateError):
    """Erreur de validation de template"""
    
    def __init__(self, message: str, template_name: Optional[str] = None):
        super().__init__(message, template_name, error_type="VALIDATION_ERROR")


class TemplatePerformanceError(TemplateError):
    """Erreur de performance de template"""
    
    def __init__(self, message: str, template_name: Optional[str] = None):
        super().__init__(message, template_name, error_type="PERFORMANCE_ERROR")


# =============================================================================
# CUSTOM FILTERS
# =============================================================================

class BusinessLogicFilters:
    """Filtres de logique métier pour les templates"""
    
    @staticmethod
    def calculate_roi(cost: float, revenue: float) -> float:
        """Calcule le ROI en pourcentage"""
        if cost == 0:
            return float('inf') if revenue > 0 else 0.0
        return ((revenue - cost) / cost) * 100
    
    @staticmethod
    def format_currency(amount: float, currency: str = "USD") -> str:
        """Formate un montant en devise"""
        currency_symbols = {
            "USD": "$",
            "EUR": "€",
            "GBP": "£",
            "JPY": "¥",
            "CAD": "C$"
        }
        symbol = currency_symbols.get(currency.upper(), currency)
        return f"{symbol}{amount:,.2f}"
    
    @staticmethod
    def calculate_savings(current_cost: float, optimized_cost: float) -> Dict[str, Any]:
        """Calcule les économies et pourcentage de réduction"""
        savings = current_cost - optimized_cost
        if current_cost == 0:
            percentage = 0.0
        else:
            percentage = (savings / current_cost) * 100
        
        return {
            "amount": savings,
            "percentage": percentage,
            "formatted": f"${savings:,.2f} ({percentage:.1f}%)"
        }
    
    @staticmethod
    def prioritize_issues(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Priorise les problèmes par sévérité et impact"""
        severity_score = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        
        def score_issue(issue: Dict[str, Any]) -> float:
            severity = severity_score.get(issue.get("severity", "low"), 1)
            impact = issue.get("impact", 1)
            frequency = issue.get("frequency", 1)
            return severity * impact * frequency
        
        return sorted(issues, key=score_issue, reverse=True)
    
    @staticmethod
    def generate_cost_report(costs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Génère un rapport de coûts agrégé"""
        total = sum(item.get("amount", 0) for item in costs)
        by_service = defaultdict(float)
        by_region = defaultdict(float)
        
        for item in costs:
            service = item.get("service", "unknown")
            region = item.get("region", "unknown")
            amount = item.get("amount", 0)
            
            by_service[service] += amount
            by_region[region] += amount
        
        return {
            "total": total,
            "by_service": dict(by_service),
            "by_region": dict(by_region),
            "average_per_service": total / len(by_service) if by_service else 0,
            "service_count": len(by_service)
        }


class CodeFormattingFilters:
    """Filtres de formatage de code"""
    
    @staticmethod
    def indent_code(code: str, spaces: int = 4) -> str:
        """Indente du code"""
        lines = code.split('\n')
        indented = [f"{' ' * spaces}{line}" for line in lines]
        return '\n'.join(indented)
    
    @staticmethod
    def snake_to_camel(snake_str: str) -> str:
        """Convertit snake_case en CamelCase"""
        components = snake_str.split('_')
        return ''.join(x.title() for x in components)
    
    @staticmethod
    def camel_to_snake(camel_str: str) -> str:
        """Convertit CamelCase en snake_case"""
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', camel_str)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    
    @staticmethod
    def format_python_code(code: str) -> str:
        """Formate du code Python (simplifié)"""
        try:
            import black
            return black.format_str(code, mode=black.FileMode())
        except ImportError:
            # Fallback basique
            return textwrap.dedent(code).strip()
    
    @staticmethod
    def escape_special_chars(text: str) -> str:
        """Échappe les caractères spéciaux pour différents contextes"""
        escapes = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
            '`': '&#96;'
        }
        
        for char, escape in escapes.items():
            text = text.replace(char, escape)
        
        return text
    
    @staticmethod
    def truncate_code(code: str, max_lines: int = 50) -> str:
        """Tronque du code après un certain nombre de lignes"""
        lines = code.split('\n')
        if len(lines) <= max_lines:
            return code
        
        truncated = lines[:max_lines]
        truncated.append(f"# ... truncated {len(lines) - max_lines} more lines")
        return '\n'.join(truncated)


class SecurityValidationFilters:
    """Filtres de validation de sécurité"""
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Valide une adresse email"""
        return validate_email(email)
    
    @staticmethod
    def validate_url(url: str) -> bool:
        """Valide une URL"""
        return validate_url(url)
    
    @staticmethod
    def sanitize_html(html: str) -> str:
        """Nettoie le HTML pour éviter les XSS"""
        return sanitize_html(html)
    
    @staticmethod
    def escape_shell(command: str) -> str:
        """Échappe une commande shell"""
        return escape_shell(command)
    
    @staticmethod
    def mask_sensitive_data(data: str, visible_chars: int = 4) -> str:
        """Masque les données sensibles"""
        if len(data) <= visible_chars * 2:
            return '*' * len(data)
        
        visible_start = data[:visible_chars]
        visible_end = data[-visible_chars:] if visible_chars > 0 else ""
        masked_middle = '*' * (len(data) - visible_chars * 2)
        
        return f"{visible_start}{masked_middle}{visible_end}"
    
    @staticmethod
    def hash_sensitive_data(data: str, algorithm: str = "sha256") -> str:
        """Hash des données sensibles"""
        hash_func = getattr(hashlib, algorithm, hashlib.sha256)
        return hash_func(data.encode()).hexdigest()


class TemplateHelpers:
    """Helpers utilitaires pour les templates"""
    
    @staticmethod
    def timestamp_to_datetime(timestamp: Union[int, float]) -> datetime:
        """Convertit un timestamp en datetime"""
        return datetime.fromtimestamp(timestamp)
    
    @staticmethod
    def timedelta_to_human(delta: timedelta) -> str:
        """Convertit un timedelta en format humain"""
        total_seconds = int(delta.total_seconds())
        
        if total_seconds < 60:
            return f"{total_seconds} secondes"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''}"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            return f"{hours} heure{'s' if hours > 1 else ''}"
        else:
            days = total_seconds // 86400
            return f"{days} jour{'s' if days > 1 else ''}"
    
    @staticmethod
    def pluralize(count: int, singular: str, plural: Optional[str] = None) -> str:
        """Génère la forme plurielle appropriée"""
        if count == 1:
            return singular
        return plural or singular + "s"
    
    @staticmethod
    def truncate(text: str, length: int = 100, suffix: str = "...") -> str:
        """Tronque un texte à une longueur donnée"""
        if len(text) <= length:
            return text
        return text[:length - len(suffix)] + suffix
    
    @staticmethod
    def generate_id(prefix: str = "id") -> str:
        """Génère un ID unique"""
        return f"{prefix}_{int(time.time() * 1000)}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}"


# =============================================================================
# CUSTOM EXTENSIONS
# =============================================================================

class BusinessLogicExtension(Extension):
    """Extension pour la logique métier des agents"""
    
    tags = {"business_rule", "cost_analysis", "roi_calculation"}
    
    def __init__(self, environment: Environment):
        super().__init__(environment)
        
        # Ajout des filtres
        environment.filters['calculate_roi'] = BusinessLogicFilters.calculate_roi
        environment.filters['format_currency'] = BusinessLogicFilters.format_currency
        environment.filters['calculate_savings'] = BusinessLogicFilters.calculate_savings
        environment.filters['prioritize_issues'] = BusinessLogicFilters.prioritize_issues
        environment.filters['generate_cost_report'] = BusinessLogicFilters.generate_cost_report
    
    def parse(self, parser: jinja2.parser.Parser) -> Node:
        """Parse les tags personnalisés"""
        token = parser.stream.current
        lineno = token.lineno
        
        if token.value == "business_rule":
            return self._parse_business_rule(parser, lineno)
        elif token.value == "cost_analysis":
            return self._parse_cost_analysis(parser, lineno)
        elif token.value == "roi_calculation":
            return self._parse_roi_calculation(parser, lineno)
        
        parser.fail(f"Tag inconnu: {token.value}", token.lineno)
    
    def _parse_business_rule(self, parser: jinja2.parser.Parser, lineno: int) -> Node:
        """Parse le tag business_rule"""
        parser.stream.expect("name:business_rule")
        args = [parser.parse_expression()]
        
        if parser.stream.skip_if("comma"):
            args.append(parser.parse_expression())
        
        parser.stream.expect("name:endbusiness_rule")
        body = parser.parse_statements(["name:endbusiness_rule"], drop_needle=True)
        
        return jinja2.nodes.CallBlock(
            self.call_method("_render_business_rule", args),
            [], [], body
        ).set_lineno(lineno)
    
    def _render_business_rule(self, rule_name: str, context: Optional[Dict] = None, caller: Optional[Callable] = None) -> str:
        """Render une règle métier"""
        if caller is None:
            return ""
        
        rule_content = caller()
        return f"""
        <div class="business-rule" data-rule="{rule_name}">
            <h4>Règle Métier: {rule_name}</h4>
            <div class="rule-content">{rule_content}</div>
            <div class="rule-context">
                <small>Contexte: {json.dumps(context or {})}</small>
            </div>
        </div>
        """


class CodeGenerationExtension(Extension):
    """Extension pour la génération de code"""
    
    tags = {"python_code", "test_case", "api_endpoint"}
    
    def __init__(self, environment: Environment):
        super().__init__(environment)
        
        # Ajout des filtres
        environment.filters['indent_code'] = CodeFormattingFilters.indent_code
        environment.filters['snake_to_camel'] = CodeFormattingFilters.snake_to_camel
        environment.filters['camel_to_snake'] = CodeFormattingFilters.camel_to_snake
        environment.filters['format_python_code'] = CodeFormattingFilters.format_python_code
        environment.filters['escape_special_chars'] = CodeFormattingFilters.escape_special_chars
        environment.filters['truncate_code'] = CodeFormattingFilters.truncate_code
    
    def parse(self, parser: jinja2.parser.Parser) -> Node:
        """Parse les tags de génération de code"""
        token = parser.stream.current
        lineno = token.lineno
        
        if token.value == "python_code":
            return self._parse_python_code(parser, lineno)
        elif token.value == "test_case":
            return self._parse_test_case(parser, lineno)
        elif token.value == "api_endpoint":
            return self._parse_api_endpoint(parser, lineno)
        
        parser.fail(f"Tag inconnu: {token.value}", token.lineno)
    
    def _parse_python_code(self, parser: jinja2.parser.Parser, lineno: int) -> Node:
        """Parse le tag python_code"""
        parser.stream.expect("name:python_code")
        
        args = []
        if parser.stream.current.type != "block_end":
            args.append(parser.parse_expression())
        
        parser.stream.expect("name:endpython_code")
        body = parser.parse_statements(["name:endpython_code"], drop_needle=True)
        
        return jinja2.nodes.CallBlock(
            self.call_method("_render_python_code", args),
            [], [], body
        ).set_lineno(lineno)
    
    def _render_python_code(self, language: str = "python", caller: Optional[Callable] = None) -> str:
        """Render du code Python"""
        if caller is None:
            return ""
        
        code = caller().strip()
        formatted_code = CodeFormattingFilters.format_python_code(code)
        
        return f"""
        ```{language}
        {formatted_code}
        ```
        """


class SecurityExtension(Extension):
    """Extension pour les fonctionnalités de sécurité"""
    
    tags = {"secure_section", "validate_input", "mask_data"}
    
    def __init__(self, environment: Environment):
        super().__init__(environment)
        
        # Ajout des filtres
        environment.filters['validate_email'] = SecurityValidationFilters.validate_email
        environment.filters['validate_url'] = SecurityValidationFilters.validate_url
        environment.filters['sanitize_html'] = SecurityValidationFilters.sanitize_html
        environment.filters['escape_shell'] = SecurityValidationFilters.escape_shell
        environment.filters['mask_sensitive_data'] = SecurityValidationFilters.mask_sensitive_data
        environment.filters['hash_sensitive_data'] = SecurityValidationFilters.hash_sensitive_data
    
    def parse(self, parser: jinja2.parser.Parser) -> Node:
        """Parse les tags de sécurité"""
        token = parser.stream.current
        lineno = token.lineno
        
        if token.value == "secure_section":
            return self._parse_secure_section(parser, lineno)
        elif token.value == "validate_input":
            return self._parse_validate_input(parser, lineno)
        elif token.value == "mask_data":
            return self._parse_mask_data(parser, lineno)
        
        parser.fail(f"Tag inconnu: {token.value}", token.lineno)


class PerformanceMacroExtension(Extension):
    """Extension pour les macros d'optimisation de performance"""
    
    tags = {"cache_fragment", "lazy_load", "batch_processing"}
    
    def __init__(self, environment: Environment):
        super().__init__(environment)
        
        # Ajout des macros de performance
        environment.globals['measure_performance'] = self._measure_performance
        environment.globals['batch_process'] = self._batch_process
    
    @staticmethod
    def _measure_performance(func: Callable, *args, **kwargs) -> Tuple[Any, float]:
        """Mesure le temps d'exécution d'une fonction"""
        start_time = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start_time
        return result, elapsed
    
    @staticmethod
    def _batch_process(items: List[Any], batch_size: int = 100) -> List[List[Any]]:
        """Divise une liste en batches"""
        return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


class DocumentationExtension(Extension):
    """Extension pour la génération de documentation"""
    
    tags = {"api_doc", "parameter", "return_value", "example"}
    
    def __init__(self, environment: Environment):
        super().__init__(environment)
        
        # Ajout des helpers de documentation
        environment.globals['generate_docstring'] = self._generate_docstring
        environment.globals['format_signature'] = self._format_signature
    
    @staticmethod
    def _generate_docstring(
        description: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
        returns: Optional[Dict[str, Any]] = None,
        examples: Optional[List[str]] = None
    ) -> str:
        """Génère une docstring formatée"""
        doc_lines = [description, ""]
        
        if parameters:
            doc_lines.append("Args:")
            for param in parameters:
                name = param.get('name', '')
                type_ = param.get('type', 'Any')
                desc = param.get('description', '')
                doc_lines.append(f"    {name} ({type_}): {desc}")
            doc_lines.append("")
        
        if returns:
            doc_lines.append("Returns:")
            type_ = returns.get('type', 'Any')
            desc = returns.get('description', '')
            doc_lines.append(f"    {type_}: {desc}")
            doc_lines.append("")
        
        if examples:
            doc_lines.append("Examples:")
            for i, example in enumerate(examples, 1):
                doc_lines.append(f"    Example {i}:")
                doc_lines.append(f"        {example}")
        
        return '\n'.join(doc_lines)
    
    @staticmethod
    def _format_signature(
        name: str,
        parameters: List[Dict[str, Any]],
        return_type: str = "None"
    ) -> str:
        """Formate une signature de fonction"""
        param_strs = []
        for param in parameters:
            name = param['name']
            type_ = param.get('type', 'Any')
            default = param.get('default')
            
            if default is not None:
                param_strs.append(f"{name}: {type_} = {default}")
            else:
                param_strs.append(f"{name}: {type_}")
        
        return f"def {name}({', '.join(param_strs)}) -> {return_type}:"


class TestingExtension(Extension):
    """Extension pour la génération de code de test"""
    
    tags = {"test_case", "test_fixture", "assertion"}
    
    def __init__(self, environment: Environment):
        super().__init__(environment)
        
        # Ajout des générateurs de test
        environment.globals['generate_unit_test'] = self._generate_unit_test
        environment.globals['generate_integration_test'] = self._generate_integration_test
        environment.globals['generate_mock'] = self._generate_mock
    
    @staticmethod
    def _generate_unit_test(
        function_name: str,
        test_cases: List[Dict[str, Any]],
        module_name: str = "test_module"
    ) -> str:
        """Génère un test unitaire"""
        test_code = [
            f"import pytest",
            f"from {module_name} import {function_name}",
            f"",
            f"",
            f"class Test{function_name.title()}:",
            f"    \"\"\"Tests unitaires pour {function_name}\"\"\"",
            f""
        ]
        
        for i, test_case in enumerate(test_cases, 1):
            test_name = test_case.get('name', f'test_case_{i}')
            input_data = test_case.get('input', {})
            expected = test_case.get('expected', {})
            
            test_code.extend([
                f"    def test_{test_name}(self):",
                f"        \"\"\"Test: {test_case.get('description', '')}\"\"\"",
                f"        # Arrange",
                f"        input_data = {json.dumps(input_data, indent=8)}",
                f"        ",
                f"        # Act",
                f"        result = {function_name}(**input_data)",
                f"        ",
                f"        # Assert",
                f"        expected = {json.dumps(expected, indent=8)}",
                f"        assert result == expected",
                f""
            ])
        
        return '\n'.join(test_code)
    
    @staticmethod
    def _generate_mock(
        target: str,
        return_value: Any = None,
        side_effect: Optional[Callable] = None
    ) -> str:
        """Génère du code de mock"""
        mock_code = [
            f"from unittest.mock import Mock, patch",
            f"",
            f"# Mock pour {target}",
            f"mock_{target} = Mock(",
            f"    name='{target}',",
        ]
        
        if return_value is not None:
            mock_code.append(f"    return_value={return_value},")
        
        if side_effect is not None:
            mock_code.append(f"    side_effect={side_effect.__name__},")
        
        mock_code.extend([
            f")",
            f"",
            f"# Utilisation avec patch",
            f"with patch('{target}', mock_{target}):",
            f"    # Code à tester ici",
            f"    pass"
        ])
        
        return '\n'.join(mock_code)


class DeploymentExtension(Extension):
    """Extension pour la génération de configuration de déploiement"""
    
    tags = {"docker_config", "kubernetes_config", "ci_cd_pipeline"}
    
    def __init__(self, environment: Environment):
        super().__init__(environment)
        
        # Ajout des générateurs de déploiement
        environment.globals['generate_dockerfile'] = self._generate_dockerfile
        environment.globals['generate_k8s_deployment'] = self._generate_k8s_deployment
        environment.globals['generate_github_actions'] = self._generate_github_actions
    
    @staticmethod
    def _generate_dockerfile(
        base_image: str = "python:3.12-slim",
        workdir: str = "/app",
        requirements_file: str = "requirements.txt",
        entrypoint: str = "python main.py"
    ) -> str:
        """Génère un Dockerfile"""
        return f"""
# Dockerfile généré par MicroAgents Platform
FROM {base_image}

# Installer les dépendances système
RUN apt-get update && apt-get install -y \\
    gcc \\
    g++ \\
    && rm -rf /var/lib/apt/lists/*

# Définir le répertoire de travail
WORKDIR {workdir}

# Copier les fichiers de dépendances
COPY {requirements_file} .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r {requirements_file}

# Copier le code source
COPY . .

# Variables d'environnement
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Exposer le port (ajuster selon l'application)
EXPOSE 8000

# Commande d'entrée
ENTRYPOINT ["{entrypoint}"]
"""
    
    @staticmethod
    def _generate_k8s_deployment(
        name: str,
        image: str,
        replicas: int = 2,
        port: int = 8000
    ) -> str:
        """Génère un déploiement Kubernetes"""
        return f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels:
    app: {name}
    component: microagent
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
      - name: {name}
        image: {image}
        ports:
        - containerPort: {port}
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        env:
        - name: ENVIRONMENT
          value: "production"
        - name: LOG_LEVEL
          value: "INFO"
        livenessProbe:
          httpGet:
            path: /health
            port: {port}
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health/ready
            port: {port}
          initialDelaySeconds: 5
          periodSeconds: 5
"""


# =============================================================================
# TEMPLATE CACHE
# =============================================================================

class TemplateCache:
    """Cache intelligent pour les templates"""
    
    def __init__(self, strategy: TemplateCacheStrategy, ttl: int = 300, max_size: int = 1000):
        self.strategy = strategy
        self.ttl = ttl
        self.max_size = max_size
        self._cache: Dict[str, Tuple[Template, datetime]] = {}
        self._hits = 0
        self._misses = 0
        self._access_times: Dict[str, datetime] = {}
    
    def get(self, key: str) -> Optional[Template]:
        """Récupère un template du cache"""
        if key not in self._cache:
            self._misses += 1
            return None
        
        template, timestamp = self._cache[key]
        
        # Vérifier l'expiration
        if datetime.utcnow() - timestamp > timedelta(seconds=self.ttl):
            del self._cache[key]
            del self._access_times[key]
            self._misses += 1
            return None
        
        self._hits += 1
        self._access_times[key] = datetime.utcnow()
        return template
    
    def set(self, key: str, template: Template) -> None:
        """Stocke un template dans le cache"""
        # Éviction si nécessaire
        if len(self._cache) >= self.max_size:
            self._evict_oldest()
        
        self._cache[key] = (template, datetime.utcnow())
        self._access_times[key] = datetime.utcnow()
    
    def _evict_oldest(self) -> None:
        """Évicte les entrées les plus anciennes"""
        if not self._access_times:
            return
        
        # Trouver la clé la plus ancienne
        oldest_key = min(self._access_times.items(), key=lambda x: x[1])[0]
        
        # Supprimer
        if oldest_key in self._cache:
            del self._cache[oldest_key]
        if oldest_key in self._access_times:
            del self._access_times[oldest_key]
    
    def clear(self) -> None:
        """Vide le cache"""
        self._cache.clear()
        self._access_times.clear()
        self._hits = 0
        self._misses = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """Récupère les statistiques du cache"""
        hit_rate = self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0
        
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "strategy": self.strategy.value,
            "ttl_seconds": self.ttl,
            "max_size": self.max_size
        }


# =============================================================================
# MAIN TEMPLATE ENGINE
# =============================================================================

class JinjaTemplateEngine:
    """
    Moteur de template Jinja2 avancé pour MicroAgents Platform.
    
    Features:
    - Chargement et cache de templates avec stratégies multiples
    - Résolution intelligente de variables de contexte
    - Filtres et extensions personnalisés pour la logique métier
    - Support d'internationalisation complète
    - Fonctionnalités de sécurité avancées (sandboxing, auto-escaping)
    - Optimisations de performance (bytecode cache, lazy loading)
    - Reporting d'erreurs détaillé avec stack traces
    - Héritage de templates et système de macros
    - Intégration avec framework de tests
    """
    
    def __init__(self, config: Optional[TemplateConfig] = None):
        self.config = config or TemplateConfig()
        self.cache = TemplateCache(self.config.cache_strategy, self.config.cache_ttl_seconds)
        self.metrics = TemplateMetrics()
        
        # Initialiser l'environnement Jinja2
        self._environment = self._create_environment()
        
        # Charger les traductions
        self._translations = self._load_translations()
        
        # Initialiser les extensions
        self._register_extensions()
        self._register_filters()
        self._register_globals()
        
        logger.info(f"Template Engine initialisé en mode {self.config.security_level.value}")
    
    def _create_environment(self) -> Environment:
        """Crée l'environnement Jinja2 avec la configuration appropriée"""
        
        # Créer les loaders
        loaders = []
        
        # FileSystemLoader pour les chemins personnalisés
        if self.config.template_dirs:
            fs_dirs = [str(path) for path in self.config.template_dirs if path.exists()]
            if fs_dirs:
                loaders.append(FileSystemLoader(fs_dirs))
        
        # PackageLoader pour les templates dans les packages
        for package_path in self.config.package_loader_paths:
            try:
                loaders.append(PackageLoader(*package_path.split(':')))
            except ImportError:
                logger.warning(f"Package non trouvé: {package_path}")
        
        if not loaders:
            raise TemplateError("Aucun loader de template configuré")
        
        loader = ChoiceLoader(loaders)
        
        # Choisir la classe d'environnement selon le niveau de sécurité
        if self.config.security_level == SecurityLevel.SANDBOXED:
            environment_class = SandboxedEnvironment
        elif self.config.security_level == SecurityLevel.RESTRICTED:
            environment_class = SandboxedEnvironment
        else:
            environment_class = Environment
        
        # Configurer l'environnement
        env = environment_class(
            loader=loader,
            autoescape=self.config.autoescape,
            trim_blocks=self.config.trim_blocks,
            lstrip_blocks=self.config.lstrip_blocks,
            undefined=jinja2.StrictUndefined if self.config.strict_undefined else jinja2.Undefined,
            extensions=self.config.jinja2_extensions + self.config.custom_extensions,
            cache_size=self.config.bytecode_cache_size if self.config.enable_bytecode_cache else 0,
            auto_reload=self.config.auto_reload,
            optimized=self.config.optimize_whitespace
        )
        
        return env
    
    def _load_translations(self) -> Dict[LanguageCode, Dict[str, str]]:
        """Charge les fichiers de traduction"""
        translations = {}
        
        if not self.config.enable_i18n:
            return translations
        
        for translation_dir in self.config.translation_dirs:
            if not translation_dir.exists():
                continue
            
            for lang in LanguageCode:
                translation_file = translation_dir / f"{lang.value}.json"
                if translation_file.exists():
                    try:
                        with open(translation_file, 'r', encoding='utf-8') as f:
                            translations[lang] = json.load(f)
                    except Exception as e:
                        logger.warning(f"Erreur chargement traduction {lang}: {str(e)}")
        
        return translations
    
    def _register_extensions(self) -> None:
        """Enregistre les extensions personnalisées"""
        extensions = [
            BusinessLogicExtension,
            CodeGenerationExtension,
            SecurityExtension,
            PerformanceMacroExtension,
            DocumentationExtension,
            TestingExtension,
            DeploymentExtension
        ]
        
        for extension in extensions:
            self._environment.add_extension(extension)
    
    def _register_filters(self) -> None:
        """Enregistre les filtres personnalisés"""
        
        # Filtres utilitaires
        self._environment.filters['to_json'] = json.dumps
        self._environment.filters['from_json'] = json.loads
        self._environment.filters['to_pretty_json'] = lambda x: json.dumps(x, indent=2)
        
        # Filtres de formatage
        self._environment.filters['format_datetime'] = lambda dt, fmt='%Y-%m-%d %H:%M:%S': dt.strftime(fmt) if dt else ''
        self._environment.filters['format_number'] = lambda x: f"{x:,}"
        self._environment.filters['format_percentage'] = lambda x: f"{x:.1f}%"
        
        # Filtres de texte
        self._environment.filters['truncate'] = TemplateHelpers.truncate
        self._environment.filters['pluralize'] = TemplateHelpers.pluralize
        self._environment.filters['capitalize_words'] = lambda s: ' '.join(word.capitalize() for word in s.split())
        
        # Filtres de liste
        self._environment.filters['sort_by'] = lambda items, key: sorted(items, key=lambda x: x.get(key, ''))
        self._environment.filters['group_by'] = lambda items, key: {
            k: list(g) for k, g in itertools.groupby(sorted(items, key=lambda x: x.get(key, '')), lambda x: x.get(key, ''))
        } if items else {}
        
        # Filtres de sécurité
        self._environment.filters['escape_html'] = lambda s: jinja2.escape(s) if s else ''
        
        import itertools  # Pour group_by
    
    def _register_globals(self) -> None:
        """Enregistre les variables globales"""
        
        # Constantes
        self._environment.globals['now'] = datetime.utcnow
        self._environment.globals['timestamp'] = lambda: int(time.time())
        
        # Helpers
        self._environment.globals['generate_id'] = TemplateHelpers.generate_id
        self._environment.globals['timedelta_to_human'] = TemplateHelpers.timedelta_to_human
        self._environment.globals['timestamp_to_datetime'] = TemplateHelpers.timestamp_to_datetime
        
        # Configuration
        self._environment.globals['config'] = {
            'mode': self.config.security_level.value,
            'debug': self.config.debug,
            'default_language': self.config.default_language.value
        }
        
        # Internationalisation
        if self.config.enable_i18n:
            self._environment.globals['_'] = self._translate
            self._environment.globals['gettext'] = self._translate
            self._environment.globals['ngettext'] = self._translate_plural
    
    def _translate(self, text: str, language: Optional[LanguageCode] = None) -> str:
        """Traduit un texte"""
        if not self.config.enable_i18n:
            return text
        
        lang = language or self.config.default_language
        translations = self._translations.get(lang, {})
        
        return translations.get(text, text)
    
    def _translate_plural(self, singular: str, plural: str, count: int, language: Optional[LanguageCode] = None) -> str:
        """Traduit avec gestion du pluriel"""
        if count == 1:
            return self._translate(singular, language)
        return self._translate(plural, language)
    
    # =========================================================================
    # PUBLIC API
    # =========================================================================
    
    def get_template(self, template_name: str) -> Template:
        """
        Récupère un template avec cache.
        
        Args:
            template_name: Nom du template
            
        Returns:
            Instance de template
            
        Raises:
            TemplateNotFound: Si le template n'existe pas
            TemplateError: Pour les autres erreurs
        """
        cache_key = f"template:{template_name}"
        
        # Essayer le cache
        cached_template = self.cache.get(cache_key)
        if cached_template:
            return cached_template
        
        try:
            # Charger le template
            template = self._environment.get_template(template_name)
            
            # Mettre en cache
            self.cache.set(cache_key, template)
            
            return template
            
        except TemplateNotFound as e:
            logger.error(f"Template non trouvé: {template_name}", exc_info=True)
            raise TemplateError(
                f"Template non trouvé: {template_name}",
                template_name=template_name
            )
        except TemplateSyntaxError as e:
            logger.error(f"Erreur syntaxique dans le template {template_name}: {str(e)}", exc_info=True)
            raise TemplateError(
                f"Erreur syntaxique: {str(e)}",
                template_name=template_name,
                line_number=e.lineno
            )
        except Exception as e:
            logger.error(f"Erreur chargement template {template_name}: {str(e)}", exc_info=True)
            raise TemplateError(
                f"Erreur chargement template: {str(e)}",
                template_name=template_name
            )
    
    async def render(
        self,
        template_name: str,
        context: Optional[Dict[str, Any]] = None,
        language: Optional[LanguageCode] = None
    ) -> TemplateRenderResult:
        """
        Render un template avec le contexte fourni.
        
        Args:
            template_name: Nom du template
            context: Variables de contexte
            language: Langue pour l'internationalisation
            
        Returns:
            Résultat du rendering
        """
        start_time = time.time()
        context = context or {}
        context_hash = self._compute_context_hash(context)
        
        try:
            # Validation du contexte
            self._validate_context(context)
            
            # Ajouter les variables de langue
            if language:
                context['_language'] = language.value
                context['_'] = lambda x: self._translate(x, language)
            elif self.config.enable_i18n:
                context['_language'] = self.config.default_language.value
                context['_'] = self._translate
            
            # Obtenir le template
            template = self.get_template(template_name)
            
            # Render
            content = template.render(**context)
            
            # Post-processing
            if self.config.strip_whitespace:
                content = self._strip_excess_whitespace(content)
            
            render_time = (time.time() - start_time) * 1000  # en ms
            
            # Mettre à jour les métriques
            self.metrics.render_count += 1
            self.metrics.total_render_time_ms += render_time
            self.metrics.avg_render_time_ms = (
                self.metrics.total_render_time_ms / self.metrics.render_count
            )
            
            if self.config.log_rendering_times and render_time > 1000:  # > 1s
                logger.warning(
                    f"Rendering lent détecté: {template_name} a pris {render_time:.1f}ms",
                    extra={
                        "template": template_name,
                        "render_time_ms": render_time,
                        "context_size": len(str(context))
                    }
                )
            
            return TemplateRenderResult(
                content=content,
                render_time_ms=render_time,
                cached=False,  # Le template est caché, pas le rendering
                template_name=template_name,
                context_hash=context_hash,
                metadata={
                    "template_size": len(content),
                    "context_size": len(str(context)),
                    "language": language.value if language else self.config.default_language.value
                }
            )
            
        except SecurityError as e:
            self.metrics.error_count += 1
            raise TemplateSecurityError(
                f"Violation de sécurité: {str(e)}",
                template_name=template_name
            )
        except UndefinedError as e:
            self.metrics.error_count += 1
            raise TemplateError(
                f"Variable non définie: {str(e)}",
                template_name=template_name
            )
        except TemplateRuntimeError as e:
            self.metrics.error_count += 1
            raise TemplateError(
                f"Erreur d'exécution: {str(e)}",
                template_name=template_name
            )
        except Exception as e:
            self.metrics.error_count += 1
            logger.error(
                f"Erreur rendering template {template_name}: {str(e)}",
                exc_info=True
            )
            raise TemplateError(
                f"Erreur rendering: {str(e)}",
                template_name=template_name
            )
    
    async def render_string(
        self,
        template_string: str,
        context: Optional[Dict[str, Any]] = None,
        template_name: str = "inline"
    ) -> TemplateRenderResult:
        """
        Render une chaîne de template.
        
        Args:
            template_string: Chaîne contenant le template
            context: Variables de contexte
            template_name: Nom pour le debugging
            
        Returns:
            Résultat du rendering
        """
        # Valider la taille du template
        if len(template_string) > self.config.max_template_size_mb * 1024 * 1024:
            raise TemplateValidationError(
                f"Template trop grand: {len(template_string)} bytes",
                template_name=template_name
            )
        
        try:
            # Créer un template à partir de la chaîne
            template = self._environment.from_string(template_string)
            
            # Render
            start_time = time.time()
            content = template.render(**(context or {}))
            render_time = (time.time() - start_time) * 1000
            
            return TemplateRenderResult(
                content=content,
                render_time_ms=render_time,
                template_name=template_name,
                metadata={"source": "string"}
            )
            
        except Exception as e:
            raise TemplateError(
                f"Erreur rendering string: {str(e)}",
                template_name=template_name
            )
    
    def validate_template(self, template_name: str) -> List[str]:
        """
        Valide un template pour les erreurs potentielles.
        
        Args:
            template_name: Nom du template
            
        Returns:
            Liste des avertissements
        """
        warnings = []
        
        try:
            # Obtenir le source
            source, _, _ = self._environment.loader.get_source(self._environment, template_name)
            
            # Analyser pour les variables non définies
            ast = self._environment.parse(source)
            variables = meta.find_undeclared_variables(ast)
            
            # Vérifier les variables potentiellement non définies
            for var in variables:
                if not var.startswith('_') and var not in self._environment.globals:
                    warnings.append(f"Variable potentiellement non définie: {var}")
            
            # Vérifier la taille
            if len(source) > 1024 * 1024:  # 1MB
                warnings.append("Template très volumineux, risque de performance")
            
            # Vérifier les boucles potentiellement infinies
            if 'for' in source and 'break' not in source and 'range' not in source:
                warnings.append("Boucle for sans break ou range détectée")
            
            return warnings
            
        except Exception as e:
            raise TemplateError(
                f"Erreur validation template: {str(e)}",
                template_name=template_name
            )
    
    def list_templates(self, extension: str = ".jinja") -> List[str]:
        """
        Liste tous les templates disponibles.
        
        Args:
            extension: Extension des templates à lister
            
        Returns:
            Liste des noms de templates
        """
        try:
            templates = []
            
            # Pour chaque loader, lister les templates
            if hasattr(self._environment.loader, 'list_templates'):
                templates.extend(self._environment.loader.list_templates())
            elif isinstance(self._environment.loader, ChoiceLoader):
                for loader in self._environment.loader.loaders:
                    if hasattr(loader, 'list_templates'):
                        templates.extend(loader.list_templates())
            
            # Filtrer par extension
            if extension:
                templates = [t for t in templates if t.endswith(extension)]
            
            return sorted(set(templates))
            
        except Exception as e:
            logger.error(f"Erreur listing templates: {str(e)}")
            return []
    
    # =========================================================================
    # CONTEXT RESOLUTION
    # =========================================================================
    
    def resolve_context(
        self,
        base_context: Dict[str, Any],
        additional_context: Dict[str, Any],
        merge_strategy: str = "deep"
    ) -> Dict[str, Any]:
        """
        Résout et fusionne les contextes.
        
        Args:
            base_context: Contexte de base
            additional_context: Contexte additionnel
            merge_strategy: Stratégie de fusion ("shallow", "deep", "replace")
            
        Returns:
            Contexte fusionné
        """
        if merge_strategy == "shallow":
            return {**base_context, **additional_context}
        elif merge_strategy == "deep":
            return self._deep_merge(base_context, additional_context)
        elif merge_strategy == "replace":
            return additional_context
        else:
            raise TemplateError(f"Stratégie de fusion inconnue: {merge_strategy}")
    
    @staticmethod
    def _deep_merge(base: Dict, update: Dict) -> Dict:
        """Fusionne récursivement deux dictionnaires"""
        result = base.copy()
        
        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = JinjaTemplateEngine._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    # =========================================================================
    # SECURITY VALIDATION
    # =========================================================================
    
    def _validate_context(self, context: Dict[str, Any]) -> None:
        """
        Valide le contexte pour des problèmes de sécurité.
        
        Args:
            context: Contexte à valider
            
        Raises:
            TemplateSecurityError: Si des problèmes de sécurité sont détectés
        """
        # Vérifier la taille
        context_size = len(str(context).encode('utf-8'))
        max_size = self.config.max_context_size_mb * 1024 * 1024
        
        if context_size > max_size:
            raise TemplateSecurityError(
                f"Contexte trop grand: {context_size} bytes (max: {max_size})"
            )
        
        # Vérifier les types potentiellement dangereux
        dangerous_types = (type(open), type(eval), type(exec), type(__import__))
        
        for key, value in context.items():
            if isinstance(value, dangerous_types):
                raise TemplateSecurityError(
                    f"Type dangereux dans le contexte: {key}={type(value).__name__}"
                )
    
    # =========================================================================
    # PERFORMANCE OPTIMIZATION
    # =========================================================================
    
    @staticmethod
    def _strip_excess_whitespace(content: str) -> str:
        """Supprime les espaces blancs excessifs"""
        # Supprimer les lignes vides multiples
        content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
        
        # Supprimer les espaces en fin de ligne
        content = re.sub(r'[ \t]+\n', '\n', content)
        
        return content.strip()
    
    def _compute_context_hash(self, context: Dict[str, Any]) -> str:
        """Calcule un hash du contexte pour le caching"""
        context_str = json.dumps(context, sort_keys=True, default=str)
        return hashlib.md5(context_str.encode()).hexdigest()
    
    # =========================================================================
    # ERROR REPORTING
    # =========================================================================
    
    def get_detailed_error_report(
        self,
        error: Exception,
        template_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Génère un rapport d'erreur détaillé.
        
        Args:
            error: Exception capturée
            template_name: Nom du template (si applicable)
            context: Contexte du rendering (si applicable)
            
        Returns:
            Rapport d'erreur structuré
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "engine_version": "2.0.0",
            "config": {
                "security_level": self.config.security_level.value,
                "mode": "production" if not self.config.debug else "debug"
            }
        }
        
        if template_name:
            report["template"] = {
                "name": template_name,
                "cached": template_name in [k.split(':')[1] for k in self.cache._cache.keys() if k.startswith('template:')]
            }
        
        if context:
            # Masquer les données sensibles
            safe_context = self._sanitize_context_for_error_report(context)
            report["context"] = {
                "keys": list(safe_context.keys()),
                "sample": dict(list(safe_context.items())[:3]),  # Premières 3 entrées
                "size_bytes": len(str(safe_context).encode('utf-8'))
            }
        
        if hasattr(error, '__traceback__'):
            import traceback
            report["stack_trace"] = traceback.format_exception(type(error), error, error.__traceback__)
        
        # Statistiques du moteur
        report["engine_stats"] = {
            "render_count": self.metrics.render_count,
            "error_count": self.metrics.error_count,
            "avg_render_time_ms": self.metrics.avg_render_time_ms,
            "cache_stats": self.cache.get_stats()
        }
        
        return report
    
    @staticmethod
    def _sanitize_context_for_error_report(context: Dict[str, Any]) -> Dict[str, Any]:
        """Nettoie le contexte pour le rapport d'erreur"""
        safe_context = {}
        
        for key, value in context.items():
            if isinstance(value, str):
                # Masquer les données potentiellement sensibles
                lower_key = key.lower()
                if any(sensitive in lower_key for sensitive in ['password', 'secret', 'token', 'key']):
                    safe_context[key] = "***MASKED***"
                elif len(value) > 100:
                    safe_context[key] = value[:100] + "...[truncated]"
                else:
                    safe_context[key] = value
            elif isinstance(value, (int, float, bool, type(None))):
                safe_context[key] = value
            else:
                safe_context[key] = f"[{type(value).__name__}]"
        
        return safe_context
    
    # =========================================================================
    # TESTING INTEGRATION
    # =========================================================================
    
    def create_test_environment(self) -> 'JinjaTemplateEngine':
        """
        Crée un environnement de test isolé.
        
        Returns:
            Nouvelle instance du moteur pour les tests
        """
        test_config = TemplateConfig(
            debug=True,
            auto_reload=False,
            security_level=SecurityLevel.TRUSTED,  # Plus permissif pour les tests
            enable_metrics=False,
            cache_strategy=TemplateCacheStrategy.NONE
        )
        
        return JinjaTemplateEngine(test_config)
    
    async def run_template_tests(
        self,
        test_specifications: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Exécute une suite de tests sur des templates.
        
        Args:
            test_specifications: Spécifications des tests
            
        Returns:
            Résultats des tests
        """
        results = {
            "total": len(test_specifications),
            "passed": 0,
            "failed": 0,
            "errors": [],
            "start_time": datetime.utcnow().isoformat()
        }
        
        for test_spec in test_specifications:
            test_name = test_spec.get("name", "unnamed_test")
            template_name = test_spec.get("template")
            context = test_spec.get("context", {})
            expected = test_spec.get("expected", {})
            
            try:
                # Render le template
                result = await self.render(template_name, context)
                
                # Valider le résultat
                if self._validate_test_result(result.content, expected):
                    results["passed"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append({
                        "test": test_name,
                        "issue": "Le résultat ne correspond pas aux attentes",
                        "actual_sample": result.content[:200] if result.content else "",
                        "expected_sample": str(expected)[:200] if expected else ""
                    })
                    
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "test": test_name,
                    "issue": f"Erreur d'exécution: {str(e)}",
                    "error_type": type(e).__name__
                })
        
        results["end_time"] = datetime.utcnow().isoformat()
        results["success_rate"] = (
            results["passed"] / results["total"] * 100
            if results["total"] > 0 else 0
        )
        
        return results
    
    @staticmethod
    def _validate_test_result(actual: str, expected: Dict[str, Any]) -> bool:
        """Valide le résultat d'un test de template"""
        # Validation basique - à étendre selon les besoins
        if "contains" in expected:
            return expected["contains"] in actual
        elif "matches_regex" in expected:
            import re
            return bool(re.match(expected["matches_regex"], actual))
        elif "exact_match" in expected:
            return actual == expected["exact_match"]
        
        return True  # Si aucune validation spécifiée
    
    # =========================================================================
    # MONITORING AND METRICS
    # =========================================================================
    
    def get_metrics(self) -> TemplateMetrics:
        """Récupère les métriques du moteur"""
        return self.metrics
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Récupère les statistiques du cache"""
        return self.cache.get_stats()
    
    def reset_metrics(self) -> None:
        """Réinitialise les métriques"""
        self.metrics = TemplateMetrics()
    
    # =========================================================================
    # TEMPLATE INHERITANCE AND MACROS
    # =========================================================================
    
    def get_template_dependencies(self, template_name: str) -> List[str]:
        """
        Récupère les dépendances d'un template (extends, includes, imports).
        
        Args:
            template_name: Nom du template
            
        Returns:
            Liste des noms de templates dépendants
        """
        try:
            source, _, _ = self._environment.loader.get_source(self._environment, template_name)
            ast = self._environment.parse(source)
            
            dependencies = set()
            
            # Analyser les nodes pour trouver les dépendances
            for node in ast.find_all((jinja2.nodes.Extends, jinja2.nodes.Include, jinja2.nodes.Import, jinja2.nodes.FromImport)):
                if hasattr(node, 'template'):
                    if isinstance(node.template, jinja2.nodes.Const):
                        dependencies.add(node.template.value)
            
            return sorted(dependencies)
            
        except Exception as e:
            logger.warning(f"Erreur analyse dépendances {template_name}: {str(e)}")
            return []
    
    def extract_macros(self, template_name: str) -> Dict[str, str]:
        """
        Extrait les macros d'un template.
        
        Args:
            template_name: Nom du template
            
        Returns:
            Dictionnaire {nom_macro: source_macro}
        """
        try:
            source, _, _ = self._environment.loader.get_source(self._environment, template_name)
            
            # Recherche basique des macros (simplifiée)
            macros = {}
            macro_pattern = r'{%\s*macro\s+(\w+)\([^)]*\)\s*%}(.*?){%\s*endmacro\s*%}'
            
            for match in re.finditer(macro_pattern, source, re.DOTALL):
                macro_name = match.group(1)
                macro_body = match.group(2).strip()
                macros[macro_name] = macro_body
            
            return macros
            
        except Exception as e:
            logger.warning(f"Erreur extraction macros {template_name}: {str(e)}")
            return {}


# =============================================================================
# FACTORY AND CONFIGURATION HELPERS
# =============================================================================

class TemplateEngineFactory:
    """Factory pour créer des instances de moteur de template"""
    
    @staticmethod
    def create_for_mode(mode: TemplateEngineMode) -> JinjaTemplateEngine:
        """
        Crée un moteur de template configuré pour un mode spécifique.
        
        Args:
            mode: Mode de fonctionnement
            
        Returns:
            Instance du moteur configurée
        """
        config_map = {
            TemplateEngineMode.DEVELOPMENT: TemplateConfig(
                debug=True,
                auto_reload=True,
                security_level=SecurityLevel.RESTRICTED,
                cache_strategy=TemplateCacheStrategy.MEMORY,
                cache_ttl_seconds=60
            ),
            TemplateEngineMode.TESTING: TemplateConfig(
                debug=True,
                auto_reload=False,
                security_level=SecurityLevel.TRUSTED,
                cache_strategy=TemplateCacheStrategy.NONE,
                enable_metrics=False
            ),
            TemplateEngineMode.STAGING: TemplateConfig(
                debug=False,
                auto_reload=False,
                security_level=SecurityLevel.SANDBOXED,
                cache_strategy=TemplateCacheStrategy.MEMORY,
                cache_ttl_seconds=300
            ),
            TemplateEngineMode.PRODUCTION: TemplateConfig(
                debug=False,
                auto_reload=False,
                security_level=SecurityLevel.SANDBOXED,
                cache_strategy=TemplateCacheStrategy.MEMORY,
                cache_ttl_seconds=600,
                enable_bytecode_cache=True,
                max_template_size_mb=5,
                max_context_size_mb=2
            )
        }
        
        config = config_map.get(mode, TemplateConfig())
        return JinjaTemplateEngine(config)
    
    @staticmethod
    def create_from_yaml(config_path: Path) -> JinjaTemplateEngine:
        """
        Crée un moteur de template à partir d'un fichier YAML.
        
        Args:
            config_path: Chemin vers le fichier de configuration
            
        Returns:
            Instance du moteur configurée
        """
        try:
            import yaml
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
            
            # Convertir en TemplateConfig
            config = TemplateConfig(**config_data)
            return JinjaTemplateEngine(config)
            
        except Exception as e:
            logger.error(f"Erreur chargement configuration YAML: {str(e)}")
            raise


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

async def example_usage():
    """Exemple d'utilisation du moteur de template"""
    
    # Création du moteur
    engine = TemplateEngineFactory.create_for_mode(TemplateEngineMode.DEVELOPMENT)
    
    # 1. Render un template simple
    context = {
        "agent_name": "CostAnomalyDetector",
        "version": "2.1.0",
        "description": "Détecte les anomalies de coûts en temps réel",
        "features": ["ML", "Real-time", "Multi-cloud", "Auto-remediation"],
        "cost_savings": 15000.50
    }
    
    try:
        result = await engine.render("agent_template.jinja", context)
        print(f"✅ Template renderisé en {result.render_time_ms:.1f}ms")
        print(f"   Taille: {len(result.content)} caractères")
        
    except TemplateError as e:
        print(f"❌ Erreur: {e.message}")
    
    # 2. Lister les templates disponibles
    templates = engine.list_templates()
    print(f"\n📁 Templates disponibles: {len(templates)}")
    
    # 3. Valider un template
    warnings = engine.validate_template("agent_template.jinja")
    if warnings:
        print(f"\n⚠️  Avertissements de validation:")
        for warning in warnings:
            print(f"   - {warning}")
    
    # 4. Obtenir les métriques
    metrics = engine.get_metrics()
    print(f"\n📊 Métriques:")
    print(f"   Render count: {metrics.render_count}")
    print(f"   Avg render time: {metrics.avg_render_time_ms:.1f}ms")
    
    # 5. Tester un template
    test_specs = [
        {
            "name": "test_basic_render",
            "template": "agent_template.jinja",
            "context": {"agent_name": "TestAgent"},
            "expected": {"contains": "TestAgent"}
        }
    ]
    
    test_results = await engine.run_template_tests(test_specs)
    print(f"\n🧪 Tests: {test_results['passed']}/{test_results['total']} réussis")


if __name__ == "__main__":
    # Configuration du logging
    logging.basicConfig(level=logging.INFO)
    
    # Exécuter l'exemple
    asyncio.run(example_usage())