"""
Système de validation avancé pour MicroAgents Platform

Validation complète avec support de schémas, règles métier,
validation asynchrone, localisation et optimisation des performances.
"""

import asyncio
import functools
import inspect
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Type, TypeVar, Union,
    get_type_hints, Awaitable, Pattern
)
from uuid import UUID

import jsonschema
from pydantic import (
    BaseModel, Field, validator, root_validator, ValidationError,
    create_model
)
from pydantic.error_wrappers import ErrorWrapper
from pydantic.fields import ModelField
from pydantic.validators import _VALIDATORS
from sqlalchemy.orm import Session

from src.utils.concurrency.manager import AsyncTaskManager

T = TypeVar('T')
ValidatorFunc = Callable[..., Any]
AsyncValidatorFunc = Callable[..., Awaitable[Any]]

logger = logging.getLogger(__name__)


# ==================== ENUMS ET MODÈLES ====================

class ValidationSeverity(str, Enum):
    """Sévérité d'une erreur de validation."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationScope(str, Enum):
    """Portée de la validation."""
    AGENT_CONFIG = "agent_configuration"
    BUSINESS_VALUE = "business_value"
    PRICING = "pricing_model"
    DEPLOYMENT = "deployment_manifest"
    SECURITY = "security_policy"
    COMPLIANCE = "compliance_requirement"
    PERFORMANCE = "performance_constraint"
    COST = "cost_limit"
    CROSS_FIELD = "cross_field"
    CONDITIONAL = "conditional"


@dataclass
class ValidationErrorDetail:
    """Détail d'une erreur de validation."""
    field: Optional[str] = None
    message: str = ""
    severity: ValidationSeverity = ValidationSeverity.ERROR
    code: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    validator_name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire."""
        return {
            "field": self.field,
            "message": self.message,
            "severity": self.severity.value,
            "code": self.code,
            "context": self.context,
            "validator": self.validator_name
        }


@dataclass
class ValidationResult:
    """Résultat d'une validation."""
    is_valid: bool = True
    errors: List[ValidationErrorDetail] = field(default_factory=list)
    warnings: List[ValidationErrorDetail] = field(default_factory=list)
    infos: List[ValidationErrorDetail] = field(default_factory=list)
    validated_data: Optional[Dict[str, Any]] = None
    execution_time_ms: float = 0.0
    
    def add_error(
        self,
        message: str,
        field: Optional[str] = None,
        code: str = "",
        context: Optional[Dict[str, Any]] = None,
        validator_name: Optional[str] = None
    ) -> None:
        """Ajoute une erreur."""
        self.is_valid = False
        self.errors.append(ValidationErrorDetail(
            field=field,
            message=message,
            severity=ValidationSeverity.ERROR,
            code=code,
            context=context or {},
            validator_name=validator_name
        ))
    
    def add_warning(
        self,
        message: str,
        field: Optional[str] = None,
        code: str = "",
        context: Optional[Dict[str, Any]] = None,
        validator_name: Optional[str] = None
    ) -> None:
        """Ajoute un avertissement."""
        self.warnings.append(ValidationErrorDetail(
            field=field,
            message=message,
            severity=ValidationSeverity.WARNING,
            code=code,
            context=context or {},
            validator_name=validator_name
        ))
    
    def add_info(
        self,
        message: str,
        field: Optional[str] = None,
        code: str = "",
        context: Optional[Dict[str, Any]] = None,
        validator_name: Optional[str] = None
    ) -> None:
        """Ajoute une information."""
        self.infos.append(ValidationErrorDetail(
            field=field,
            message=message,
            severity=ValidationSeverity.INFO,
            code=code,
            context=context or {},
            validator_name=validator_name
        ))
    
    def merge(self, other: 'ValidationResult') -> None:
        """Fusionne avec un autre résultat."""
        self.is_valid = self.is_valid and other.is_valid
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.infos.extend(other.infos)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire."""
        return {
            "is_valid": self.is_valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "infos": [i.to_dict() for i in self.infos],
            "execution_time_ms": self.execution_time_ms,
            "validated_data": self.validated_data
        }


class ValidationContext(BaseModel):
    """Contexte de validation."""
    scope: ValidationScope
    locale: str = "en_US"
    strict_mode: bool = True
    db_session: Optional[Session] = None
    user_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        arbitrary_types_allowed = True


# ==================== REGISTRE DE VALIDATORS ====================

class ValidatorRegistry:
    """Registre central pour les validateurs."""
    
    def __init__(self):
        self.validators: Dict[str, Dict[ValidationScope, List[ValidatorFunc]]] = {}
        self.async_validators: Dict[str, Dict[ValidationScope, List[AsyncValidatorFunc]]] = {}
        self.schemas: Dict[str, Dict[str, Any]] = {}
        self.error_messages: Dict[str, Dict[str, str]] = {}
        
        # Initialiser les messages d'erreur par défaut
        self._init_default_messages()
    
    def _init_default_messages(self) -> None:
        """Initialise les messages d'erreur par défaut."""
        self.error_messages = {
            "en_US": {
                "required": "This field is required",
                "invalid_type": "Invalid type. Expected {expected}, got {actual}",
                "out_of_range": "Value must be between {min} and {max}",
                "invalid_format": "Invalid format",
                "unique_violation": "Value must be unique",
                "business_rule": "Violates business rule: {rule}",
                "security_violation": "Security violation: {violation}",
                "compliance_violation": "Compliance violation: {requirement}",
            },
            "fr_FR": {
                "required": "Ce champ est obligatoire",
                "invalid_type": "Type invalide. Attendu: {expected}, obtenu: {actual}",
                "out_of_range": "La valeur doit être entre {min} et {max}",
                "invalid_format": "Format invalide",
                "unique_violation": "La valeur doit être unique",
                "business_rule": "Violation de règle métier: {rule}",
                "security_violation": "Violation de sécurité: {violation}",
                "compliance_violation": "Violation de conformité: {requirement}",
            }
        }
    
    def register_validator(
        self,
        name: str,
        validator_func: ValidatorFunc,
        scopes: List[ValidationScope]
    ) -> None:
        """Enregistre un validateur synchrone."""
        if name not in self.validators:
            self.validators[name] = {}
        
        for scope in scopes:
            if scope not in self.validators[name]:
                self.validators[name][scope] = []
            
            self.validators[name][scope].append(validator_func)
    
    def register_async_validator(
        self,
        name: str,
        validator_func: AsyncValidatorFunc,
        scopes: List[ValidationScope]
    ) -> None:
        """Enregistre un validateur asynchrone."""
        if name not in self.async_validators:
            self.async_validators[name] = {}
        
        for scope in scopes:
            if scope not in self.async_validators[name]:
                self.async_validators[name][scope] = []
            
            self.async_validators[name][scope].append(validator_func)
    
    def register_schema(
        self,
        name: str,
        schema: Dict[str, Any]
    ) -> None:
        """Enregistre un schéma JSON."""
        self.schemas[name] = schema
    
    def get_validators(
        self,
        name: str,
        scope: ValidationScope
    ) -> List[ValidatorFunc]:
        """Récupère les validateurs pour un nom et scope donné."""
        if name in self.validators and scope in self.validators[name]:
            return self.validators[name][scope]
        return []
    
    def get_async_validators(
        self,
        name: str,
        scope: ValidationScope
    ) -> List[AsyncValidatorFunc]:
        """Récupère les validateurs async pour un nom et scope donné."""
        if name in self.async_validators and scope in self.async_validators[name]:
            return self.async_validators[name][scope]
        return []
    
    def get_schema(self, name: str) -> Optional[Dict[str, Any]]:
        """Récupère un schéma par nom."""
        return self.schemas.get(name)
    
    def get_error_message(
        self,
        code: str,
        locale: str = "en_US",
        **kwargs
    ) -> str:
        """
        Récupère un message d'erreur localisé.
        
        Args:
            code: Code d'erreur
            locale: Locale (ex: "en_US", "fr_FR")
            **kwargs: Variables à injecter dans le message
            
        Returns:
            Message localisé
        """
        locale_messages = self.error_messages.get(locale, self.error_messages["en_US"])
        message_template = locale_messages.get(code, code)
        
        try:
            return message_template.format(**kwargs)
        except KeyError:
            return message_template


# ==================== VALIDATORS DE BASE ====================

class BaseValidator(ABC):
    """Classe de base pour tous les validateurs."""
    
    def __init__(self, registry: ValidatorRegistry):
        self.registry = registry
    
    @abstractmethod
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        """Valide les données."""
        pass
    
    async def validate_async(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        """Valide les données de manière asynchrone."""
        # Par défaut, utilise la version synchrone
        return self.validate(data, context)
    
    def _get_error_message(
        self,
        code: str,
        context: ValidationContext,
        **kwargs
    ) -> str:
        """Récupère un message d'erreur localisé."""
        return self.registry.get_error_message(code, context.locale, **kwargs)


class SchemaValidator(BaseValidator):
    """Validateur basé sur JSON Schema."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Récupérer le schéma
        schema_name = context.metadata.get("schema_name")
        if not schema_name:
            result.add_error("Schema name not specified in context")
            return result
        
        schema = self.registry.get_schema(schema_name)
        if not schema:
            result.add_error(f"Schema '{schema_name}' not found")
            return result
        
        try:
            # Valider avec jsonschema
            jsonschema.validate(data, schema)
            result.validated_data = data
        except jsonschema.ValidationError as e:
            # Extraire les informations d'erreur
            field = ".".join(str(p) for p in e.path) if e.path else None
            
            message = e.message
            if e.validator:
                message = f"Validation failed for '{e.validator}': {message}"
            
            result.add_error(
                message=message,
                field=field,
                code="schema_validation",
                context={
                    "validator": e.validator,
                    "validator_value": e.validator_value,
                    "schema_path": list(e.schema_path),
                    "instance_path": list(e.path)
                }
            )
        except Exception as e:
            result.add_error(f"Schema validation error: {str(e)}")
        
        return result


class BusinessRuleValidator(BaseValidator):
    """Validateur de règles métier."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Récupérer les règles métier applicables
        rule_set = context.metadata.get("business_rules", [])
        
        for rule_name in rule_set:
            # Récupérer les validateurs pour cette règle
            validators = self.registry.get_validators(rule_name, context.scope)
            
            for validator_func in validators:
                try:
                    # Appeler le validateur
                    validator_func(data, context, result)
                except Exception as e:
                    logger.error(f"Business rule validator '{rule_name}' failed: {str(e)}")
                    result.add_error(
                        message=f"Business rule validation error: {str(e)}",
                        code="business_rule_error",
                        validator_name=rule_name
                    )
        
        return result
    
    async def validate_async(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        rule_set = context.metadata.get("business_rules", [])
        
        # Exécuter les validateurs async en parallèle
        async_tasks = []
        
        for rule_name in rule_set:
            validators = self.registry.get_async_validators(rule_name, context.scope)
            
            for validator_func in validators:
                async_tasks.append(
                    validator_func(data, context, result)
                )
        
        # Attendre la fin de tous les validateurs
        if async_tasks:
            try:
                await asyncio.gather(*async_tasks)
            except Exception as e:
                logger.error(f"Async business rule validation failed: {str(e)}")
                result.add_error(
                    message=f"Async validation error: {str(e)}",
                    code="async_validation_error"
                )
        
        return result


# ==================== VALIDATEURS SPÉCIFIQUES ====================

class AgentConfigurationValidator(BaseValidator):
    """Validateur de configuration d'agent."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Validation de structure de base
        if "agent_type" not in data:
            result.add_error(
                message=self._get_error_message("required", context),
                field="agent_type",
                code="required"
            )
        
        if "name" not in data:
            result.add_error(
                message=self._get_error_message("required", context),
                field="name",
                code="required"
            )
        
        # Validation des limites de ressources
        resources = data.get("resources", {})
        self._validate_resources(resources, context, result)
        
        # Validation des dépendances
        dependencies = data.get("dependencies", [])
        self._validate_dependencies(dependencies, context, result)
        
        # Validation des permissions
        permissions = data.get("permissions", [])
        self._validate_permissions(permissions, context, result)
        
        return result
    
    def _validate_resources(
        self,
        resources: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les ressources de l'agent."""
        # CPU
        cpu = resources.get("cpu", {})
        cpu_limit = cpu.get("limit", 1.0)
        if cpu_limit > 16.0:  # Max 16 cores
            result.add_error(
                message=self._get_error_message(
                    "out_of_range", context,
                    min=0.1, max=16.0
                ),
                field="resources.cpu.limit",
                code="out_of_range",
                context={"min": 0.1, "max": 16.0, "actual": cpu_limit}
            )
        
        # Mémoire
        memory = resources.get("memory", {})
        memory_limit = memory.get("limit", "512Mi")
        if not self._is_valid_memory_string(memory_limit):
            result.add_error(
                message="Invalid memory format. Use format like '512Mi', '1Gi'",
                field="resources.memory.limit",
                code="invalid_format"
            )
        
        # Stockage
        storage = resources.get("storage", {})
        storage_limit = storage.get("limit", "10Gi")
        if not self._is_valid_storage_string(storage_limit):
            result.add_error(
                message="Invalid storage format. Use format like '10Gi', '100Gi'",
                field="resources.storage.limit",
                code="invalid_format"
            )
    
    def _validate_dependencies(
        self,
        dependencies: List[str],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les dépendances de l'agent."""
        max_dependencies = 10
        
        if len(dependencies) > max_dependencies:
            result.add_error(
                message=f"Maximum {max_dependencies} dependencies allowed",
                field="dependencies",
                code="business_rule",
                context={"max": max_dependencies, "actual": len(dependencies)}
            )
        
        # Vérifier les références circulaires
        agent_name = context.metadata.get("agent_name", "")
        if agent_name in dependencies:
            result.add_error(
                message="Agent cannot depend on itself",
                field="dependencies",
                code="business_rule"
            )
    
    def _validate_permissions(
        self,
        permissions: List[str],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les permissions de l'agent."""
        allowed_permissions = {
            "read_config", "write_config", "execute_scripts",
            "access_network", "access_storage", "access_secrets"
        }
        
        for perm in permissions:
            if perm not in allowed_permissions:
                result.add_error(
                    message=f"Invalid permission: {perm}",
                    field="permissions",
                    code="invalid_value",
                    context={"allowed": list(allowed_permissions)}
                )
    
    def _is_valid_memory_string(self, memory_str: str) -> bool:
        """Valide une chaîne de mémoire Kubernetes-style."""
        pattern = r'^(\d+)([KMGTP]i?)$'
        return bool(re.match(pattern, memory_str))
    
    def _is_valid_storage_string(self, storage_str: str) -> bool:
        """Valide une chaîne de stockage."""
        pattern = r'^(\d+)([KMGTP]i?)$'
        return bool(re.match(pattern, storage_str))


class BusinessValueValidator(BaseValidator):
    """Validateur de calculs de valeur métier."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Validation des paramètres d'entrée
        inputs = data.get("inputs", {})
        self._validate_inputs(inputs, context, result)
        
        # Validation des métriques
        metrics = data.get("metrics", {})
        self._validate_metrics(metrics, context, result)
        
        # Validation des seuils
        thresholds = data.get("thresholds", {})
        self._validate_thresholds(thresholds, context, result)
        
        # Validation croisée
        self._validate_cross_field(data, context, result)
        
        return result
    
    def _validate_inputs(
        self,
        inputs: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les paramètres d'entrée."""
        required_inputs = ["revenue", "costs", "time_period"]
        
        for req_input in required_inputs:
            if req_input not in inputs:
                result.add_error(
                    message=f"Required input '{req_input}' is missing",
                    field=f"inputs.{req_input}",
                    code="required"
                )
        
        # Validation des types
        revenue = inputs.get("revenue")
        if revenue is not None and not isinstance(revenue, (int, float, Decimal)):
            result.add_error(
                message=self._get_error_message(
                    "invalid_type", context,
                    expected="number", actual=type(revenue).__name__
                ),
                field="inputs.revenue",
                code="invalid_type"
            )
        
        # Validation des valeurs
        if isinstance(revenue, (int, float, Decimal)):
            if revenue < 0:
                result.add_error(
                    message="Revenue must be non-negative",
                    field="inputs.revenue",
                    code="out_of_range"
                )
    
    def _validate_metrics(
        self,
        metrics: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les métriques de calcul."""
        # ROI validation
        roi = metrics.get("roi")
        if roi is not None:
            if not isinstance(roi, (int, float, Decimal)):
                result.add_error(
                    message="ROI must be a number",
                    field="metrics.roi",
                    code="invalid_type"
                )
            elif roi > 1000:  # ROI max 1000%
                result.add_warning(
                    message="ROI seems unusually high",
                    field="metrics.roi",
                    code="suspicious_value"
                )
        
        # Payback period validation
        payback = metrics.get("payback_period_months")
        if payback is not None:
            if not isinstance(payback, (int, float)):
                result.add_error(
                    message="Payback period must be a number",
                    field="metrics.payback_period_months",
                    code="invalid_type"
                )
            elif payback <= 0:
                result.add_error(
                    message="Payback period must be positive",
                    field="metrics.payback_period_months",
                    code="out_of_range"
                )
    
    def _validate_thresholds(
        self,
        thresholds: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les seuils d'acceptation."""
        min_roi = thresholds.get("min_roi")
        max_payback = thresholds.get("max_payback_months")
        
        if min_roi is not None and max_payback is not None:
            # Validation conditionnelle: si ROI min est défini, payback doit aussi l'être
            if min_roi > 0 and max_payback <= 0:
                result.add_error(
                    message="If min ROI is specified, max payback must be positive",
                    field="thresholds.max_payback_months",
                    code="conditional_validation"
                )
    
    def _validate_cross_field(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Validation croisée entre champs."""
        inputs = data.get("inputs", {})
        metrics = data.get("metrics", {})
        
        revenue = inputs.get("revenue")
        costs = inputs.get("costs")
        roi = metrics.get("roi")
        
        if all(v is not None for v in [revenue, costs, roi]):
            # Vérifier la cohérence ROI = (revenue - costs) / costs * 100
            expected_roi = ((revenue - costs) / costs * 100) if costs != 0 else 0
            
            if abs(float(roi) - expected_roi) > 0.01:  # Tolérance de 0.01%
                result.add_error(
                    message=f"ROI calculation inconsistent. Expected {expected_roi:.2f}%, got {roi}%",
                    field="metrics.roi",
                    code="cross_field_validation"
                )


class PricingModelValidator(BaseValidator):
    """Validateur de modèles de tarification."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Validation du type de modèle
        model_type = data.get("model_type")
        if model_type not in ["subscription", "usage_based", "tiered", "custom"]:
            result.add_error(
                message=f"Invalid model type: {model_type}",
                field="model_type",
                code="invalid_value"
            )
        
        # Validation selon le type de modèle
        if model_type == "subscription":
            self._validate_subscription_model(data, context, result)
        elif model_type == "usage_based":
            self._validate_usage_based_model(data, context, result)
        elif model_type == "tiered":
            self._validate_tiered_model(data, context, result)
        
        # Validation des prix
        self._validate_prices(data, context, result)
        
        # Validation des règles de facturation
        self._validate_billing_rules(data, context, result)
        
        return result
    
    def _validate_subscription_model(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide un modèle d'abonnement."""
        billing_cycle = data.get("billing_cycle")
        valid_cycles = ["monthly", "quarterly", "annual"]
        
        if billing_cycle not in valid_cycles:
            result.add_error(
                message=f"Invalid billing cycle: {billing_cycle}",
                field="billing_cycle",
                code="invalid_value",
                context={"valid_cycles": valid_cycles}
            )
        
        # Validation du prix de base
        base_price = data.get("base_price")
        if base_price is None:
            result.add_error(
                message="Base price is required for subscription model",
                field="base_price",
                code="required"
            )
        elif base_price < 0:
            result.add_error(
                message="Base price must be non-negative",
                field="base_price",
                code="out_of_range"
            )
    
    def _validate_usage_based_model(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide un modèle à l'usage."""
        unit_price = data.get("unit_price")
        if unit_price is None:
            result.add_error(
                message="Unit price is required for usage-based model",
                field="unit_price",
                code="required"
            )
        elif unit_price < 0:
            result.add_error(
                message="Unit price must be non-negative",
                field="unit_price",
                code="out_of_range"
            )
        
        # Validation des seuils
        tiers = data.get("tiers", [])
        for i, tier in enumerate(tiers):
            tier_field = f"tiers[{i}]"
            
            max_usage = tier.get("max_usage")
            if max_usage is not None and max_usage < 0:
                result.add_error(
                    message="Max usage must be non-negative",
                    field=f"{tier_field}.max_usage",
                    code="out_of_range"
                )
    
    def _validate_tiered_model(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide un modèle à paliers."""
        tiers = data.get("tiers", [])
        
        if not tiers:
            result.add_error(
                message="At least one tier is required for tiered model",
                field="tiers",
                code="required"
            )
            return
        
        # Vérifier l'ordre des paliers
        previous_max = -1
        for i, tier in enumerate(tiers):
            tier_field = f"tiers[{i}]"
            
            min_usage = tier.get("min_usage", 0)
            max_usage = tier.get("max_usage")
            
            if min_usage < previous_max:
                result.add_error(
                    message=f"Tier {i}: min_usage ({min_usage}) must be >= previous max_usage ({previous_max})",
                    field=f"{tier_field}.min_usage",
                    code="business_rule"
                )
            
            if max_usage is not None and max_usage <= min_usage:
                result.add_error(
                    message=f"Tier {i}: max_usage ({max_usage}) must be > min_usage ({min_usage})",
                    field=f"{tier_field}.max_usage",
                    code="business_rule"
                )
            
            if max_usage is not None:
                previous_max = max_usage
    
    def _validate_prices(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les prix et devises."""
        currency = data.get("currency", "USD")
        valid_currencies = ["USD", "EUR", "GBP", "JPY", "CAD"]
        
        if currency not in valid_currencies:
            result.add_error(
                message=f"Invalid currency: {currency}",
                field="currency",
                code="invalid_value",
                context={"valid_currencies": valid_currencies}
            )
        
        # Validation de cohérence des prix
        prices = []
        
        if "base_price" in data:
            prices.append(("base_price", data["base_price"]))
        
        if "unit_price" in data:
            prices.append(("unit_price", data["unit_price"]))
        
        for tier in data.get("tiers", []):
            if "price" in tier:
                prices.append(("tier.price", tier["price"]))
        
        for field_name, price in prices:
            if price is not None and price < 0:
                result.add_error(
                    message=f"Price must be non-negative: {field_name} = {price}",
                    field=field_name,
                    code="out_of_range"
                )
    
    def _validate_billing_rules(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les règles de facturation."""
        billing_rules = data.get("billing_rules", {})
        
        # Minimum charge
        min_charge = billing_rules.get("minimum_charge")
        if min_charge is not None and min_charge < 0:
            result.add_error(
                message="Minimum charge must be non-negative",
                field="billing_rules.minimum_charge",
                code="out_of_range"
            )
        
        # Proration rules
        proration = billing_rules.get("proration", True)
        if not isinstance(proration, bool):
            result.add_error(
                message="Proration must be a boolean",
                field="billing_rules.proration",
                code="invalid_type"
            )


class DeploymentManifestValidator(BaseValidator):
    """Validateur de manifestes de déploiement."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Validation de la version d'API
        api_version = data.get("apiVersion")
        if api_version != "apps/v1":
            result.add_error(
                message=f"Unsupported API version: {api_version}",
                field="apiVersion",
                code="invalid_value"
            )
        
        # Validation du kind
        kind = data.get("kind")
        if kind not in ["Deployment", "StatefulSet", "DaemonSet", "Job"]:
            result.add_error(
                message=f"Unsupported kind: {kind}",
                field="kind",
                code="invalid_value"
            )
        
        # Validation des métadonnées
        metadata = data.get("metadata", {})
        self._validate_metadata(metadata, context, result)
        
        # Validation de la spécification
        spec = data.get("spec", {})
        self._validate_spec(spec, kind, context, result)
        
        return result
    
    def _validate_metadata(
        self,
        metadata: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les métadonnées."""
        name = metadata.get("name")
        if not name:
            result.add_error(
                message="Name is required in metadata",
                field="metadata.name",
                code="required"
            )
        elif not self._is_valid_k8s_name(name):
            result.add_error(
                message=f"Invalid Kubernetes name: {name}",
                field="metadata.name",
                code="invalid_format"
            )
        
        namespace = metadata.get("namespace", "default")
        if not self._is_valid_k8s_namespace(namespace):
            result.add_error(
                message=f"Invalid namespace: {namespace}",
                field="metadata.namespace",
                code="invalid_format"
            )
    
    def _validate_spec(
        self,
        spec: Dict[str, Any],
        kind: str,
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide la spécification selon le kind."""
        if kind == "Deployment":
            self._validate_deployment_spec(spec, context, result)
        elif kind == "StatefulSet":
            self._validate_statefulset_spec(spec, context, result)
        elif kind == "Job":
            self._validate_job_spec(spec, context, result)
    
    def _validate_deployment_spec(
        self,
        spec: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide une spécification de Deployment."""
        replicas = spec.get("replicas", 1)
        if not isinstance(replicas, int) or replicas < 0 or replicas > 100:
            result.add_error(
                message="Replicas must be an integer between 0 and 100",
                field="spec.replicas",
                code="out_of_range"
            )
        
        # Validation du selector
        selector = spec.get("selector", {})
        match_labels = selector.get("matchLabels", {})
        
        if not match_labels:
            result.add_error(
                message="selector.matchLabels is required",
                field="spec.selector.matchLabels",
                code="required"
            )
        
        # Validation du template
        template = spec.get("template", {})
        if not template:
            result.add_error(
                message="template is required",
                field="spec.template",
                code="required"
            )
        else:
            self._validate_pod_template(template, context, result)
    
    def _validate_statefulset_spec(
        self,
        spec: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide une spécification de StatefulSet."""
        service_name = spec.get("serviceName")
        if not service_name:
            result.add_error(
                message="serviceName is required for StatefulSet",
                field="spec.serviceName",
                code="required"
            )
        
        volume_claim_templates = spec.get("volumeClaimTemplates", [])
        for i, template in enumerate(volume_claim_templates):
            self._validate_pvc_template(template, i, context, result)
    
    def _validate_job_spec(
        self,
        spec: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide une spécification de Job."""
        completions = spec.get("completions", 1)
        parallelism = spec.get("parallelism", 1)
        
        if completions < 1:
            result.add_error(
                message="completions must be at least 1",
                field="spec.completions",
                code="out_of_range"
            )
        
        if parallelism < 1:
            result.add_error(
                message="parallelism must be at least 1",
                field="spec.parallelism",
                code="out_of_range"
            )
        
        if parallelism > completions:
            result.add_warning(
                message="parallelism should not exceed completions",
                field="spec.parallelism",
                code="business_rule"
            )
    
    def _validate_pod_template(
        self,
        template: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide un template de pod."""
        spec = template.get("spec", {})
        
        containers = spec.get("containers", [])
        if not containers:
            result.add_error(
                message="At least one container is required",
                field="spec.template.spec.containers",
                code="required"
            )
        
        for i, container in enumerate(containers):
            self._validate_container(container, i, context, result)
    
    def _validate_container(
        self,
        container: Dict[str, Any],
        index: int,
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide une spécification de container."""
        container_field = f"spec.template.spec.containers[{index}]"
        
        name = container.get("name")
        if not name:
            result.add_error(
                message="Container name is required",
                field=f"{container_field}.name",
                code="required"
            )
        
        image = container.get("image")
        if not image:
            result.add_error(
                message="Container image is required",
                field=f"{container_field}.image",
                code="required"
            )
        
        # Validation des resources
        resources = container.get("resources", {})
        self._validate_container_resources(resources, container_field, context, result)
    
    def _validate_container_resources(
        self,
        resources: Dict[str, Any],
        container_field: str,
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les ressources d'un container."""
        limits = resources.get("limits", {})
        requests = resources.get("requests", {})
        
        # Validation CPU
        cpu_limit = limits.get("cpu")
        if cpu_limit and not self._is_valid_cpu_string(cpu_limit):
            result.add_error(
                message=f"Invalid CPU limit format: {cpu_limit}",
                field=f"{container_field}.resources.limits.cpu",
                code="invalid_format"
            )
        
        cpu_request = requests.get("cpu")
        if cpu_request and not self._is_valid_cpu_string(cpu_request):
            result.add_error(
                message=f"Invalid CPU request format: {cpu_request}",
                field=f"{container_field}.resources.requests.cpu",
                code="invalid_format"
            )
        
        # Validation mémoire
        memory_limit = limits.get("memory")
        if memory_limit and not self._is_valid_memory_string(memory_limit):
            result.add_error(
                message=f"Invalid memory limit format: {memory_limit}",
                field=f"{container_field}.resources.limits.memory",
                code="invalid_format"
            )
        
        memory_request = requests.get("memory")
        if memory_request and not self._is_valid_memory_string(memory_request):
            result.add_error(
                message=f"Invalid memory request format: {memory_request}",
                field=f"{container_field}.resources.requests.memory",
                code="invalid_format"
            )
    
    def _validate_pvc_template(
        self,
        template: Dict[str, Any],
        index: int,
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide un template de PVC."""
        pvc_field = f"spec.volumeClaimTemplates[{index}]"
        
        metadata = template.get("metadata", {})
        name = metadata.get("name")
        if not name:
            result.add_error(
                message="PVC name is required",
                field=f"{pvc_field}.metadata.name",
                code="required"
            )
        
        spec = template.get("spec", {})
        access_modes = spec.get("accessModes", [])
        valid_modes = ["ReadWriteOnce", "ReadOnlyMany", "ReadWriteMany"]
        
        for mode in access_modes:
            if mode not in valid_modes:
                result.add_error(
                    message=f"Invalid access mode: {mode}",
                    field=f"{pvc_field}.spec.accessModes",
                    code="invalid_value"
                )
        
        resources = spec.get("resources", {})
        requests = resources.get("requests", {})
        storage = requests.get("storage")
        
        if not storage:
            result.add_error(
                message="Storage request is required",
                field=f"{pvc_field}.spec.resources.requests.storage",
                code="required"
            )
        elif not self._is_valid_storage_string(storage):
            result.add_error(
                message=f"Invalid storage format: {storage}",
                field=f"{pvc_field}.spec.resources.requests.storage",
                code="invalid_format"
            )
    
    def _is_valid_k8s_name(self, name: str) -> bool:
        """Valide un nom Kubernetes."""
        pattern = r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$'
        return bool(re.match(pattern, name)) and len(name) <= 253
    
    def _is_valid_k8s_namespace(self, namespace: str) -> bool:
        """Valide un namespace Kubernetes."""
        pattern = r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
        return bool(re.match(pattern, namespace)) and len(namespace) <= 63
    
    def _is_valid_cpu_string(self, cpu: str) -> bool:
        """Valide une chaîne CPU Kubernetes."""
        pattern = r'^(\d+(\.\d+)?)(m)?$'
        return bool(re.match(pattern, cpu))
    
    def _is_valid_memory_string(self, memory: str) -> bool:
        """Valide une chaîne mémoire Kubernetes."""
        pattern = r'^(\d+)([KMGTP]i?)$'
        return bool(re.match(pattern, memory))


class SecurityPolicyValidator(BaseValidator):
    """Validateur de politiques de sécurité."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Validation des contrôles d'accès
        access_controls = data.get("access_controls", {})
        self._validate_access_controls(access_controls, context, result)
        
        # Validation du chiffrement
        encryption = data.get("encryption", {})
        self._validate_encryption(encryption, context, result)
        
        # Validation de l'audit
        audit = data.get("audit", {})
        self._validate_audit(audit, context, result)
        
        # Validation réseau
        network = data.get("network", {})
        self._validate_network(network, context, result)
        
        return result
    
    def _validate_access_controls(
        self,
        access_controls: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les contrôles d'accès."""
        # RBAC validation
        rbac = access_controls.get("rbac", {})
        
        roles = rbac.get("roles", [])
        for i, role in enumerate(roles):
            role_field = f"access_controls.rbac.roles[{i}]"
            
            name = role.get("name")
            if not name:
                result.add_error(
                    message="Role name is required",
                    field=f"{role_field}.name",
                    code="required"
                )
            
            permissions = role.get("permissions", [])
            if not permissions:
                result.add_warning(
                    message=f"Role '{name}' has no permissions",
                    field=f"{role_field}.permissions",
                    code="security_warning"
                )
        
        # MFA validation
        mfa = access_controls.get("mfa", {})
        mfa_enabled = mfa.get("enabled", False)
        
        if mfa_enabled:
            mfa_methods = mfa.get("methods", [])
            if not mfa_methods:
                result.add_error(
                    message="MFA methods required when MFA is enabled",
                    field="access_controls.mfa.methods",
                    code="required"
                )
    
    def _validate_encryption(
        self,
        encryption: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les paramètres de chiffrement."""
        # Encryption at rest
        at_rest = encryption.get("at_rest", {})
        enabled = at_rest.get("enabled", False)
        
        if enabled:
            algorithm = at_rest.get("algorithm")
            if algorithm not in ["AES-256", "ChaCha20"]:
                result.add_error(
                    message=f"Unsupported encryption algorithm: {algorithm}",
                    field="encryption.at_rest.algorithm",
                    code="invalid_value"
                )
            
            key_rotation_days = at_rest.get("key_rotation_days", 90)
            if key_rotation_days < 30:
                result.add_warning(
                    message="Key rotation period should be at least 30 days",
                    field="encryption.at_rest.key_rotation_days",
                    code="security_warning"
                )
        
        # Encryption in transit
        in_transit = encryption.get("in_transit", {})
        tls_required = in_transit.get("tls_required", True)
        
        if tls_required:
            min_tls_version = in_transit.get("min_tls_version", "1.2")
            if min_tls_version not in ["1.2", "1.3"]:
                result.add_error(
                    message=f"Unsupported TLS version: {min_tls_version}",
                    field="encryption.in_transit.min_tls_version",
                    code="invalid_value"
                )
    
    def _validate_audit(
        self,
        audit: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les paramètres d'audit."""
        enabled = audit.get("enabled", True)
        
        if enabled:
            retention_days = audit.get("retention_days", 365)
            if retention_days < 90:
                result.add_warning(
                    message="Audit retention should be at least 90 days for compliance",
                    field="audit.retention_days",
                    code="compliance_warning"
                )
            
            # Validation des événements audités
            events = audit.get("events", [])
            required_events = ["login", "logout", "data_access", "config_change"]
            
            for req_event in required_events:
                if req_event not in events:
                    result.add_warning(
                        message=f"Recommended audit event missing: {req_event}",
                        field="audit.events",
                        code="security_warning"
                    )
    
    def _validate_network(
        self,
        network: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les paramètres réseau."""
        firewall = network.get("firewall", {})
        
        # Règles de pare-feu
        rules = firewall.get("rules", [])
        for i, rule in enumerate(rules):
            rule_field = f"network.firewall.rules[{i}]"
            
            direction = rule.get("direction")
            if direction not in ["ingress", "egress"]:
                result.add_error(
                    message=f"Invalid direction: {direction}",
                    field=f"{rule_field}.direction",
                    code="invalid_value"
                )
            
            # Validation des ports
            ports = rule.get("ports", [])
            for port in ports:
                if not isinstance(port, int) or port < 1 or port > 65535:
                    result.add_error(
                        message=f"Invalid port: {port}",
                        field=f"{rule_field}.ports",
                        code="out_of_range"
                    )
        
        # Zero trust
        zero_trust = network.get("zero_trust", {})
        if zero_trust.get("enabled", False):
            # Vérifier les exigences zero trust
            if not network.get("microsegmentation", {}).get("enabled", False):
                result.add_error(
                    message="Microsegmentation required for zero trust",
                    field="network.microsegmentation.enabled",
                    code="security_requirement"
                )


class ComplianceValidator(BaseValidator):
    """Validateur de conformité réglementaire."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Récupérer les frameworks de conformité
        frameworks = data.get("compliance_frameworks", [])
        
        for framework in frameworks:
            if framework == "gdpr":
                self._validate_gdpr(data, context, result)
            elif framework == "hipaa":
                self._validate_hipaa(data, context, result)
            elif framework == "soc2":
                self._validate_soc2(data, context, result)
            elif framework == "iso27001":
                self._validate_iso27001(data, context, result)
            elif framework == "pci_dss":
                self._validate_pci_dss(data, context, result)
        
        return result
    
    def _validate_gdpr(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide la conformité GDPR."""
        gdpr = data.get("gdpr", {})
        
        # DPO requis
        dpo = gdpr.get("data_protection_officer", {})
        if not dpo.get("appointed", False):
            result.add_error(
                message="GDPR requires a Data Protection Officer",
                field="gdpr.data_protection_officer.appointed",
                code="compliance_requirement"
            )
        
        # Droit à l'oubli
        right_to_be_forgotten = gdpr.get("right_to_be_forgotten", {})
        if not right_to_be_forgotten.get("implemented", False):
            result.add_error(
                message="GDPR requires right to be forgotten implementation",
                field="gdpr.right_to_be_forgotten.implemented",
                code="compliance_requirement"
            )
        
        # Consentement
        consent = gdpr.get("consent_management", {})
        if not consent.get("explicit_consent_required", True):
            result.add_warning(
                message="GDPR recommends explicit consent",
                field="gdpr.consent_management.explicit_consent_required",
                code="compliance_warning"
            )
    
    def _validate_hipaa(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide la conformité HIPAA."""
        hipaa = data.get("hipaa", {})
        
        # BAAs requis
        baas = hipaa.get("business_associate_agreements", {})
        if not baas.get("required", True):
            result.add_error(
                message="HIPAA requires Business Associate Agreements",
                field="hipaa.business_associate_agreements.required",
                code="compliance_requirement"
            )
        
        # Audit des accès
        access_audit = hipaa.get("access_audit", {})
        if not access_audit.get("enabled", False):
            result.add_error(
                message="HIPAA requires access audit logging",
                field="hipaa.access_audit.enabled",
                code="compliance_requirement"
            )
        
        # Chiffrement des données au repos
        encryption = data.get("encryption", {})
        at_rest = encryption.get("at_rest", {})
        if not at_rest.get("enabled", False):
            result.add_error(
                message="HIPAA requires encryption at rest for PHI",
                field="encryption.at_rest.enabled",
                code="compliance_requirement"
            )
    
    def _validate_soc2(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide la conformité SOC2."""
        soc2 = data.get("soc2", {})
        
        # Principes SOC2
        principles = soc2.get("principles", {})
        
        # Security principle
        security = principles.get("security", {})
        if not security.get("implemented", False):
            result.add_error(
                message="SOC2 Security principle must be implemented",
                field="soc2.principles.security.implemented",
                code="compliance_requirement"
            )
        
        # Availability principle
        availability = principles.get("availability", {})
        sla = availability.get("sla_percentage", 99.9)
        if sla < 99.5:
            result.add_warning(
                message="SOC2 typically requires at least 99.5% availability",
                field="soc2.principles.availability.sla_percentage",
                code="compliance_warning"
            )
        
        # Audit trail
        audit = data.get("audit", {})
        if not audit.get("enabled", False) or audit.get("retention_days", 0) < 90:
            result.add_error(
                message="SOC2 requires 90+ days audit trail retention",
                field="audit.retention_days",
                code="compliance_requirement"
            )
    
    def _validate_iso27001(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide la conformité ISO27001."""
        iso27001 = data.get("iso27001", {})
        
        # ISMS requis
        isms = iso27001.get("information_security_management_system", {})
        if not isms.get("implemented", False):
            result.add_error(
                message="ISO27001 requires an Information Security Management System",
                field="iso27001.information_security_management_system.implemented",
                code="compliance_requirement"
            )
        
        # Risk assessment
        risk_assessment = iso27001.get("risk_assessment", {})
        if not risk_assessment.get("performed_annually", False):
            result.add_error(
                message="ISO27001 requires annual risk assessment",
                field="iso27001.risk_assessment.performed_annually",
                code="compliance_requirement"
            )
        
        # Continual improvement
        improvement = iso27001.get("continual_improvement", {})
        if not improvement.get("process_established", False):
            result.add_warning(
                message="ISO27001 recommends continual improvement process",
                field="iso27001.continual_improvement.process_established",
                code="compliance_warning"
            )
    
    def _validate_pci_dss(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide la conformité PCI DSS."""
        pci = data.get("pci_dss", {})
        
        # Firewall configuration
        network = data.get("network", {})
        firewall = network.get("firewall", {})
        
        if not firewall.get("configured", False):
            result.add_error(
                message="PCI DSS requires firewall configuration",
                field="network.firewall.configured",
                code="compliance_requirement"
            )
        
        # Encryption of cardholder data
        encryption = data.get("encryption", {})
        in_transit = encryption.get("in_transit", {})
        
        if not in_transit.get("tls_required", False):
            result.add_error(
                message="PCI DSS requires TLS for cardholder data in transit",
                field="encryption.in_transit.tls_required",
                code="compliance_requirement"
            )
        
        # Regular security testing
        security_testing = pci.get("security_testing", {})
        if not security_testing.get("vulnerability_scans_quarterly", False):
            result.add_error(
                message="PCI DSS requires quarterly vulnerability scans",
                field="pci_dss.security_testing.vulnerability_scans_quarterly",
                code="compliance_requirement"
            )


class PerformanceConstraintValidator(BaseValidator):
    """Validateur de contraintes de performance."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Validation des latences
        latency = data.get("latency", {})
        self._validate_latency(latency, context, result)
        
        # Validation du débit
        throughput = data.get("throughput", {})
        self._validate_throughput(throughput, context, result)
        
        # Validation de la disponibilité
        availability = data.get("availability", {})
        self._validate_availability(availability, context, result)
        
        # Validation de l'évolutivité
        scalability = data.get("scalability", {})
        self._validate_scalability(scalability, context, result)
        
        return result
    
    def _validate_latency(
        self,
        latency: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les contraintes de latence."""
        p95 = latency.get("p95_ms")
        p99 = latency.get("p99_ms")
        
        if p95 is not None and p99 is not None:
            if p99 <= p95:
                result.add_error(
                    message="P99 latency must be greater than P95",
                    field="latency.p99_ms",
                    code="business_rule"
                )
        
        # Validation des valeurs
        max_latency = latency.get("max_ms")
        if max_latency is not None and max_latency <= 0:
            result.add_error(
                message="Max latency must be positive",
                field="latency.max_ms",
                code="out_of_range"
            )
    
    def _validate_throughput(
        self,
        throughput: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les contraintes de débit."""
        requests_per_second = throughput.get("requests_per_second")
        if requests_per_second is not None and requests_per_second < 0:
            result.add_error(
                message="Requests per second must be non-negative",
                field="throughput.requests_per_second",
                code="out_of_range"
            )
        
        data_per_second = throughput.get("data_per_second_mb")
        if data_per_second is not None and data_per_second < 0:
            result.add_error(
                message="Data per second must be non-negative",
                field="throughput.data_per_second_mb",
                code="out_of_range"
            )
    
    def _validate_availability(
        self,
        availability: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les contraintes de disponibilité."""
        sla_percentage = availability.get("sla_percentage", 99.9)
        
        if sla_percentage < 0 or sla_percentage > 100:
            result.add_error(
                message="SLA percentage must be between 0 and 100",
                field="availability.sla_percentage",
                code="out_of_range"
            )
        
        if sla_percentage < 99.5:
            result.add_warning(
                message="SLA below 99.5% may not meet business requirements",
                field="availability.sla_percentage",
                code="performance_warning"
            )
        
        # Downtime calculée
        max_downtime = availability.get("max_downtime_minutes_per_month")
        if max_downtime is not None:
            # Calculer la disponibilité correspondante
            minutes_per_month = 30 * 24 * 60
            calculated_availability = 100 * (1 - max_downtime / minutes_per_month)
            
            if abs(calculated_availability - sla_percentage) > 0.1:
                result.add_warning(
                    message=f"Max downtime implies {calculated_availability:.2f}% availability, but SLA is {sla_percentage}%",
                    field="availability.max_downtime_minutes_per_month",
                    code="consistency_warning"
                )
    
    def _validate_scalability(
        self,
        scalability: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les contraintes d'évolutivité."""
        max_instances = scalability.get("max_instances")
        min_instances = scalability.get("min_instances", 1)
        
        if max_instances is not None:
            if max_instances < min_instances:
                result.add_error(
                    message="Max instances must be >= min instances",
                    field="scalability.max_instances",
                    code="business_rule"
                )
            
            if max_instances > 1000:
                result.add_warning(
                    message="Max instances > 1000 may require special architecture",
                    field="scalability.max_instances",
                    code="performance_warning"
                )
        
        # Auto-scaling validation
        auto_scaling = scalability.get("auto_scaling", {})
        if auto_scaling.get("enabled", False):
            target_cpu = auto_scaling.get("target_cpu_percentage", 70)
            if target_cpu < 10 or target_cpu > 90:
                result.add_error(
                    message="Target CPU percentage should be between 10% and 90%",
                    field="scalability.auto_scaling.target_cpu_percentage",
                    code="out_of_range"
                )


class CostLimitValidator(BaseValidator):
    """Validateur de limites de coût."""
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        result = ValidationResult()
        
        # Validation des budgets
        budgets = data.get("budgets", [])
        self._validate_budgets(budgets, context, result)
        
        # Validation des alertes
        alerts = data.get("alerts", [])
        self._validate_alerts(alerts, context, result)
        
        # Validation des optimisations
        optimizations = data.get("optimizations", {})
        self._validate_optimizations(optimizations, context, result)
        
        # Validation croisée
        self._validate_cross_cost(data, context, result)
        
        return result
    
    def _validate_budgets(
        self,
        budgets: List[Dict[str, Any]],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les définitions de budget."""
        total_monthly_budget = 0
        
        for i, budget in enumerate(budgets):
            budget_field = f"budgets[{i}]"
            
            amount = budget.get("amount")
            if amount is None or amount <= 0:
                result.add_error(
                    message="Budget amount must be positive",
                    field=f"{budget_field}.amount",
                    code="out_of_range"
                )
            
            currency = budget.get("currency", "USD")
            if currency not in ["USD", "EUR", "GBP", "JPY"]:
                result.add_error(
                    message=f"Unsupported currency: {currency}",
                    field=f"{budget_field}.currency",
                    code="invalid_value"
                )
            
            period = budget.get("period", "monthly")
            if period not in ["daily", "weekly", "monthly", "quarterly", "annual"]:
                result.add_error(
                    message=f"Invalid period: {period}",
                    field=f"{budget_field}.period",
                    code="invalid_value"
                )
            
            # Calcul du budget mensuel total
            if amount and period == "monthly":
                total_monthly_budget += amount
            elif amount and period == "annual":
                total_monthly_budget += amount / 12
        
        # Validation du budget total
        if total_monthly_budget > 1000000:  # 1 million par mois
            result.add_warning(
                message=f"Total monthly budget ${total_monthly_budget:,.2f} seems high",
                field="budgets",
                code="cost_warning"
            )
    
    def _validate_alerts(
        self,
        alerts: List[Dict[str, Any]],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les configurations d'alerte."""
        for i, alert in enumerate(alerts):
            alert_field = f"alerts[{i}]"
            
            threshold = alert.get("threshold_percentage")
            if threshold is None or threshold < 0 or threshold > 200:
                result.add_error(
                    message="Alert threshold must be between 0% and 200%",
                    field=f"{alert_field}.threshold_percentage",
                    code="out_of_range"
                )
            
            # Recommandation: au moins une alerte à 80%
            if threshold == 80:
                result.add_info(
                    message="80% threshold is a good practice for cost alerts",
                    field=f"{alert_field}.threshold_percentage",
                    code="best_practice"
                )
            
            channels = alert.get("notification_channels", [])
            if not channels:
                result.add_warning(
                    message="Alert without notification channels may be ineffective",
                    field=f"{alert_field}.notification_channels",
                    code="cost_warning"
                )
    
    def _validate_optimizations(
        self,
        optimizations: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Valide les règles d'optimisation."""
        auto_scaling = optimizations.get("auto_scaling", {})
        if auto_scaling.get("enabled", False):
            min_instances = auto_scaling.get("min_instances", 1)
            max_instances = auto_scaling.get("max_instances", 10)
            
            if max_instances / min_instances > 20:
                result.add_warning(
                    message="Large scaling ratio may lead to cost spikes",
                    field="optimizations.auto_scaling.max_instances",
                    code="cost_warning"
                )
        
        # Règles d'arrêt
        shutdown_rules = optimizations.get("shutdown_rules", [])
        for i, rule in enumerate(shutdown_rules):
            rule_field = f"optimizations.shutdown_rules[{i}]"
            
            idle_time_minutes = rule.get("idle_time_minutes", 30)
            if idle_time_minutes < 5:
                result.add_warning(
                    message="Very short idle time may cause frequent restarts",
                    field=f"{rule_field}.idle_time_minutes",
                    code="performance_warning"
                )
    
    def _validate_cross_cost(
        self,
        data: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult
    ) -> None:
        """Validation croisée des coûts."""
        budgets = data.get("budgets", [])
        alerts = data.get("alerts", [])
        
        # Vérifier que les alertes couvrent les budgets
        budget_amounts = [b.get("amount", 0) for b in budgets if b.get("period") == "monthly"]
        
        if budget_amounts and alerts:
            max_budget = max(budget_amounts)
            alert_thresholds = [a.get("threshold_percentage", 0) for a in alerts]
            
            # Vérifier qu'il y a une alerte avant 100%
            if not any(t < 100 for t in alert_thresholds):
                result.add_warning(
                    message="No alert configured before reaching 100% of budget",
                    field="alerts",
                    code="cost_warning"
                )


# ==================== VALIDATION CHAINING ====================

class ValidationChain:
    """
    Chaîne de validation pour exécuter plusieurs validateurs en séquence.
    """
    
    def __init__(self):
        self.validators: List[BaseValidator] = []
        self.stop_on_first_error = False
    
    def add_validator(self, validator: BaseValidator) -> 'ValidationChain':
        """Ajoute un validateur à la chaîne."""
        self.validators.append(validator)
        return self
    
    def set_stop_on_first_error(self, stop: bool) -> 'ValidationChain':
        """Configure l'arrêt au premier erreur."""
        self.stop_on_first_error = stop
        return self
    
    def validate(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        """Exécute la chaîne de validation."""
        result = ValidationResult()
        start_time = time.time()
        
        for validator in self.validators:
            validator_result = validator.validate(data, context)
            result.merge(validator_result)
            
            if self.stop_on_first_error and not validator_result.is_valid:
                break
        
        result.execution_time_ms = (time.time() - start_time) * 1000
        return result
    
    async def validate_async(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        """Exécute la chaîne de validation de manière asynchrone."""
        result = ValidationResult()
        start_time = time.time()
        
        for validator in self.validators:
            validator_result = await validator.validate_async(data, context)
            result.merge(validator_result)
            
            if self.stop_on_first_error and not validator_result.is_valid:
                break
        
        result.execution_time_ms = (time.time() - start_time) * 1000
        return result


# ==================== VALIDATION ENGINE ====================

class ValidationEngine:
    """
    Moteur de validation principal.
    """
    
    def __init__(self):
        self.registry = ValidatorRegistry()
        self.chains: Dict[str, ValidationChain] = {}
        self._init_default_validators()
        self._init_default_schemas()
        self._init_default_chains()
    
    def _init_default_validators(self) -> None:
        """Initialise les validateurs par défaut."""
        # Enregistrer les validateurs de base
        self.registry.register_validator(
            name="schema_validator",
            validator_func=SchemaValidator(self.registry).validate,
            scopes=list(ValidationScope)
        )
        
        # Enregistrer les validateurs spécifiques
        agent_validator = AgentConfigurationValidator(self.registry)
        self.registry.register_validator(
            name="agent_configuration",
            validator_func=agent_validator.validate,
            scopes=[ValidationScope.AGENT_CONFIG]
        )
        
        business_value_validator = BusinessValueValidator(self.registry)
        self.registry.register_validator(
            name="business_value",
            validator_func=business_value_validator.validate,
            scopes=[ValidationScope.BUSINESS_VALUE]
        )
    
    def _init_default_schemas(self) -> None:
        """Initialise les schémas par défaut."""
        # Schéma pour la configuration d'agent
        agent_schema = {
            "type": "object",
            "properties": {
                "agent_type": {"type": "string"},
                "name": {"type": "string"},
                "version": {"type": "string"},
                "resources": {
                    "type": "object",
                    "properties": {
                        "cpu": {"type": "object"},
                        "memory": {"type": "object"},
                        "storage": {"type": "object"}
                    }
                }
            },
            "required": ["agent_type", "name"]
        }
        self.registry.register_schema("agent_configuration", agent_schema)
    
    def _init_default_chains(self) -> None:
        """Initialise les chaînes de validation par défaut."""
        # Chaîne pour la configuration d'agent
        agent_chain = ValidationChain()
        agent_chain.add_validator(SchemaValidator(self.registry))
        agent_chain.add_validator(AgentConfigurationValidator(self.registry))
        self.chains["agent_configuration"] = agent_chain
        
        # Chaîne pour la valeur métier
        business_chain = ValidationChain()
        business_chain.add_validator(BusinessValueValidator(self.registry))
        self.chains["business_value"] = business_chain
        
        # Chaîne pour les modèles de prix
        pricing_chain = ValidationChain()
        pricing_chain.add_validator(PricingModelValidator(self.registry))
        self.chains["pricing_model"] = pricing_chain
        
        # Chaîne pour les déploiements
        deployment_chain = ValidationChain()
        deployment_chain.add_validator(DeploymentManifestValidator(self.registry))
        self.chains["deployment_manifest"] = deployment_chain
        
        # Chaîne pour la sécurité
        security_chain = ValidationChain()
        security_chain.add_validator(SecurityPolicyValidator(self.registry))
        self.chains["security_policy"] = security_chain
        
        # Chaîne pour la conformité
        compliance_chain = ValidationChain()
        compliance_chain.add_validator(ComplianceValidator(self.registry))
        self.chains["compliance_requirement"] = compliance_chain
        
        # Chaîne pour les performances
        performance_chain = ValidationChain()
        performance_chain.add_validator(PerformanceConstraintValidator(self.registry))
        self.chains["performance_constraint"] = performance_chain
        
        # Chaîne pour les coûts
        cost_chain = ValidationChain()
        cost_chain.add_validator(CostLimitValidator(self.registry))
        self.chains["cost_limit"] = cost_chain
    
    def get_chain(self, name: str) -> Optional[ValidationChain]:
        """Récupère une chaîne de validation par nom."""
        return self.chains.get(name)
    
    def create_chain(self, name: str) -> ValidationChain:
        """Crée une nouvelle chaîne de validation."""
        if name in self.chains:
            raise ValueError(f"Validation chain '{name}' already exists")
        
        chain = ValidationChain()
        self.chains[name] = chain
        return chain
    
    def validate(
        self,
        data: Dict[str, Any],
        scope: ValidationScope,
        context: Optional[ValidationContext] = None
    ) -> ValidationResult:
        """
        Valide des données avec le scope spécifié.
        
        Args:
            data: Données à valider
            scope: Scope de validation
            context: Contexte de validation
            
        Returns:
            Résultat de validation
        """
        if context is None:
            context = ValidationContext(scope=scope)
        
        # Utiliser la chaîne par défaut pour ce scope
        chain_name = scope.value
        chain = self.get_chain(chain_name)
        
        if chain:
            return chain.validate(data, context)
        else:
            # Validation de base si aucune chaîne spécifique
            result = ValidationResult()
            result.add_error(f"No validation chain configured for scope: {scope}")
            return result
    
    async def validate_async(
        self,
        data: Dict[str, Any],
        scope: ValidationScope,
        context: Optional[ValidationContext] = None
    ) -> ValidationResult:
        """Version asynchrone de validate."""
        if context is None:
            context = ValidationContext(scope=scope)
        
        chain_name = scope.value
        chain = self.get_chain(chain_name)
        
        if chain:
            return await chain.validate_async(data, context)
        else:
            result = ValidationResult()
            result.add_error(f"No validation chain configured for scope: {scope}")
            return result
    
    def register_custom_validator(
        self,
        name: str,
        validator_func: ValidatorFunc,
        scopes: List[ValidationScope]
    ) -> None:
        """Enregistre un validateur personnalisé."""
        self.registry.register_validator(name, validator_func, scopes)
    
    def register_custom_async_validator(
        self,
        name: str,
        validator_func: AsyncValidatorFunc,
        scopes: List[ValidationScope]
    ) -> None:
        """Enregistre un validateur async personnalisé."""
        self.registry.register_async_validator(name, validator_func, scopes)
    
    def add_validator_to_chain(
        self,
        chain_name: str,
        validator: BaseValidator
    ) -> None:
        """Ajoute un validateur à une chaîne existante."""
        chain = self.get_chain(chain_name)
        if chain:
            chain.add_validator(validator)
        else:
            raise ValueError(f"Chain '{chain_name}' not found")


# ==================== PERFORMANCE OPTIMIZATION ====================

class CachingValidator(BaseValidator):
    """Validateur avec cache pour optimiser les performances."""
    
    def __init__(self, registry: ValidatorRegistry, ttl_seconds: int = 300):
        super().__init__(registry)
        self.cache: Dict[str, Tuple[ValidationResult, float]] = {}
        self.ttl_seconds = ttl_seconds
        self.cache_lock = asyncio.Lock()
    
    def _get_cache_key(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> str:
        """Génère une clé de cache."""
        import hashlib
        import json
        
        # Créer une représentation stable des données
        stable_data = {
            "data": json.dumps(data, sort_keys=True),
            "scope": context.scope.value,
            "locale": context.locale,
            "strict_mode": context.strict_mode
        }
        
        # Hash pour la clé de cache
        key_string = json.dumps(stable_data, sort_keys=True)
        return hashlib.md5(key_string.encode()).hexdigest()
    
    async def validate_async(
        self,
        data: Dict[str, Any],
        context: ValidationContext
    ) -> ValidationResult:
        """Valide avec cache."""
        cache_key = self._get_cache_key(data, context)
        
        async with self.cache_lock:
            # Vérifier le cache
            if cache_key in self.cache:
                result, timestamp = self.cache[cache_key]
                
                # Vérifier l'expiration
                if time.time() - timestamp < self.ttl_seconds:
                    logger.debug(f"Cache hit for validation key: {cache_key}")
                    return result
                else:
                    # Cache expiré
                    del self.cache[cache_key]
        
        # Exécuter la validation
        result = await super().validate_async(data, context)
        
        # Mettre en cache
        async with self.cache_lock:
            self.cache[cache_key] = (result, time.time())
        
        return result


# ==================== EXEMPLE D'UTILISATION ====================

if __name__ == "__main__":
    # Créer le moteur de validation
    engine = ValidationEngine()
    
    # Exemple: Validation de configuration d'agent
    agent_config = {
        "agent_type": "monitoring",
        "name": "cost-analyzer",
        "version": "1.0.0",
        "resources": {
            "cpu": {"limit": 2.0},
            "memory": {"limit": "2Gi"},
            "storage": {"limit": "20Gi"}
        },
        "dependencies": ["data-collector", "alert-manager"],
        "permissions": ["read_config", "access_network"]
    }
    
    context = ValidationContext(
        scope=ValidationScope.AGENT_CONFIG,
        locale="en_US",
        metadata={"agent_name": "cost-analyzer"}
    )
    
    result = engine.validate(agent_config, ValidationScope.AGENT_CONFIG, context)
    
    print(f"Validation result: {result.is_valid}")
    if not result.is_valid:
        for error in result.errors:
            print(f"  Error: {error.message} (field: {error.field})")
    
    # Exemple: Validation de valeur métier
    business_data = {
        "inputs": {
            "revenue": 1000000,
            "costs": 500000,
            "time_period": 12
        },
        "metrics": {
            "roi": 100.0,  # (1M - 500k) / 500k * 100 = 100%
            "payback_period_months": 6
        },
        "thresholds": {
            "min_roi": 50,
            "max_payback_months": 12
        }
    }
    
    context = ValidationContext(scope=ValidationScope.BUSINESS_VALUE)
    result = engine.validate(business_data, ValidationScope.BUSINESS_VALUE, context)
    
    print(f"\nBusiness validation result: {result.is_valid}")
    
    # Exemple: Validation asynchrone
    async def async_example():
        result = await engine.validate_async(
            agent_config,
            ValidationScope.AGENT_CONFIG,
            context
        )
        print(f"Async validation result: {result.is_valid}")
    
    import asyncio
    asyncio.run(async_example())