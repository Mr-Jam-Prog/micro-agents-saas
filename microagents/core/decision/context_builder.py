"""
Context Builder - Agrège le contexte décisionnel immutable
Assemble toutes les données nécessaires à la prise de décision sans transformation métier.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field, fields
from functools import wraps
import logging
from enum import Enum

from ..types import DecisionContext, Intent

logger = logging.getLogger(__name__)


class DataSource(Enum):
    """Sources de données pour le contexte."""
    CLIENT_PREFERENCES = "client_preferences"
    BUSINESS_VALUE = "business_value"
    SYSTEM_STATE = "system_state"
    COMPLIANCE_CONSTRAINTS = "compliance_constraints"
    DECISION_HISTORY = "decision_history"
    ENVIRONMENT = "environment"


@dataclass
class FetcherConfig:
    """Configuration pour un fetch de données."""
    source: DataSource
    is_critical: bool = True
    timeout_seconds: float = 5.0
    max_retries: int = 1
    default_value: Any = None
    cache_ttl_seconds: int = 60  # Cache de 1 minute par défaut


@dataclass
class FetchResult:
    """Résultat d'un fetch de données."""
    source: DataSource
    data: Any
    success: bool
    error: Optional[str] = None
    cached: bool = False
    fetch_duration: float = 0.0


class DataFetcher:
    """Fetcher abstrait pour les données de contexte."""
    
    def __init__(self, config: FetcherConfig):
        self.config = config
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None
    
    async def fetch(self, intent: Intent, context_hints: Dict[str, Any] = None) -> FetchResult:
        """Fetch les données depuis la source."""
        start_time = datetime.utcnow()
        
        # Vérifier le cache
        if self._cache is not None and self._cache_timestamp is not None:
            cache_age = (datetime.utcnow() - self._cache_timestamp).total_seconds()
            if cache_age < self.config.cache_ttl_seconds:
                return FetchResult(
                    source=self.config.source,
                    data=self._cache,
                    success=True,
                    cached=True,
                    fetch_duration=(datetime.utcnow() - start_time).total_seconds()
                )
        
        try:
            data = await self._fetch_impl(intent, context_hints or {})
            self._cache = data
            self._cache_timestamp = datetime.utcnow()
            
            return FetchResult(
                source=self.config.source,
                data=data,
                success=True,
                fetch_duration=(datetime.utcnow() - start_time).total_seconds()
            )
        except Exception as e:
            if self.config.default_value is not None:
                logger.warning(f"Fetch failed for {self.config.source}, using default: {e}")
                return FetchResult(
                    source=self.config.source,
                    data=self.config.default_value,
                    success=False,
                    error=str(e),
                    fetch_duration=(datetime.utcnow() - start_time).total_seconds()
                )
            raise
    
    async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> Any:
        """Implémentation spécifique à la source. À surcharger."""
        raise NotImplementedError


class ClientPreferencesFetcher(DataFetcher):
    """Fetch les préférences client."""
    
    async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> Dict[str, Any]:
        # En production: appeler une API, base de données, etc.
        client_id = context_hints.get("client_id", "default")
        
        # Simuler un fetch
        await asyncio.sleep(0.1)
        
        return {
            "client_id": client_id,
            "automation_preference": "high",  # high, medium, low, none
            "risk_tolerance": "medium",  # low, medium, high
            "notification_channels": ["email", "slack"],
            "preferred_language": "fr",
            "timezone": "Europe/Paris",
            "contact_email": f"admin@{client_id}.com",
            "budget_constraints": {
                "monthly_limit": 5000,
                "alert_threshold": 0.8,
                "currency": "USD"
            },
            "compliance_requirements": ["GDPR", "SOC2"],
            "data_retention_days": 90,
            "backup_frequency": "daily"
        }


class BusinessValueFetcher(DataFetcher):
    """Fetch la valeur business et ROI."""
    
    async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> Dict[str, Any]:
        # Calculer ou récupérer le ROI basé sur l'intention
        await asyncio.sleep(0.1)
        
        # ROI différent selon le type d'intention
        roi_map = {
            "COST_OPTIMIZATION": {"expected_roi": 2.5, "timeframe_months": 3},
            "INCIDENT_REDUCTION": {"expected_roi": 5.0, "timeframe_months": 6},
            "SECURITY_RISK_REDUCTION": {"expected_roi": 10.0, "timeframe_months": 12},
            "PERFORMANCE_IMPROVEMENT": {"expected_roi": 1.5, "timeframe_months": 2},
        }
        
        roi_data = roi_map.get(intent.type, {"expected_roi": 1.0, "timeframe_months": 6})
        
        return {
            "expected_roi": roi_data["expected_roi"],
            "timeframe_months": roi_data["timeframe_months"],
            "business_criticality": "high" if intent.priority >= 8 else "medium",
            "revenue_impact": "direct" if intent.type in ["COST_OPTIMIZATION", "INCIDENT_REDUCTION"] else "indirect",
            "customer_impact_score": intent.priority / 10.0,
            "strategic_alignment": {
                "cost_optimization": intent.type == "COST_OPTIMIZATION",
                "reliability": intent.type == "INCIDENT_REDUCTION",
                "security": intent.type == "SECURITY_RISK_REDUCTION",
                "performance": intent.type == "PERFORMANCE_IMPROVEMENT"
            }
        }


class SystemStateFetcher(DataFetcher):
    """Fetch l'état du système."""
    
    async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> Dict[str, Any]:
        # Récupérer l'état actuel des systèmes
        await asyncio.sleep(0.2)
        
        # Simuler un état système
        current_time = datetime.utcnow()
        
        return {
            "timestamp": current_time.isoformat(),
            "infrastructure_health": "healthy",  # healthy, degraded, critical
            "resource_utilization": {
                "cpu_percent": 65.5,
                "memory_percent": 72.3,
                "disk_percent": 45.8,
                "network_in_mbps": 125.4,
                "network_out_mbps": 89.7
            },
            "service_status": {
                "api_gateway": "operational",
                "database": "operational",
                "cache": "operational",
                "monitoring": "operational",
                "logging": "operational"
            },
            "pending_changes": 2,
            "maintenance_window": None,
            "incidents_active": 0,
            "last_deployment_time": (current_time - timedelta(hours=2)).isoformat(),
            "security_alerts": 0,
            "compliance_checks_passed": True
        }


class ComplianceConstraintsFetcher(DataFetcher):
    """Fetch les contraintes de compliance."""
    
    async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.1)
        
        return {
            "standards": ["SOC2", "ISO27001", "GDPR", "HIPAA"],
            "data_encryption_required": True,
            "data_retention_days": 365,
            "audit_logging_required": True,
            "change_approval_required": intent.priority >= 8,
            "rollback_required": True,
            "blame_radius": {
                "max_users_affected": 100,
                "max_data_volume_gb": 10,
                "max_downtime_minutes": 15
            },
            "geographic_constraints": ["EU", "US"],
            "data_privacy_levels": ["PII", "confidential"],
            "access_controls": {
                "mfa_required": True,
                "min_approvers": 2 if intent.priority >= 9 else 1,
                "separation_of_duties": True
            },
            "monitoring_requirements": {
                "real_time_alerts": True,
                "compliance_reporting": "daily",
                "incident_response_time": "15min"
            }
        }


class DecisionHistoryFetcher(DataFetcher):
    """Fetch l'historique des décisions."""
    
    def __init__(self, config: FetcherConfig, history_store):
        super().__init__(config)
        self.history_store = history_store
    
    async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> List[Dict[str, Any]]:
        client_id = context_hints.get("client_id", "default")
        
        # Récupérer les 10 dernières décisions similaires
        await asyncio.sleep(0.1)
        
        # En production: query la base de données
        # Simulation
        current_time = datetime.utcnow()
        
        return [
            {
                "timestamp": (current_time - timedelta(hours=i)).isoformat(),
                "intent_type": intent.type,
                "action": "EXECUTE" if i % 2 == 0 else "RECOMMEND",
                "confidence": 0.7 + (i * 0.03),
                "success": i % 3 != 0,  # 2/3 de succès
                "user_feedback": "positive" if i % 2 == 0 else "neutral",
                "execution_time_seconds": 45 + i
            }
            for i in range(10, 0, -1)
        ]


class EnvironmentFetcher(DataFetcher):
    """Fetch l'environnement d'exécution."""
    
    async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "environment": context_hints.get("environment", "production"),
            "region": context_hints.get("region", "eu-west-1"),
            "deployment_tier": context_hints.get("deployment_tier", "production"),
            "deployment_id": context_hints.get("deployment_id", "v1.2.3"),
            "team_contact": context_hints.get("team_contact", "devops@example.com"),
            "business_hours": context_hints.get("business_hours", "09:00-18:00"),
            "on_call_schedule": {
                "primary": "alice@example.com",
                "secondary": "bob@example.com"
            },
            "change_window": {
                "start": "02:00",
                "end": "04:00",
                "timezone": "UTC"
            }
        }


class ContextBuilder:
    """
    Builder de contexte décisionnel immutable.
    
    Responsabilités:
    1. Agrège les données depuis plusieurs sources
    2. Valide l'intégrité du contexte
    3. Produit un DecisionContext frozen
    4. Gère les erreurs et fallbacks
    """
    
    def __init__(
        self,
        fetchers: Dict[DataSource, DataFetcher] = None,
        validation_rules: Optional[List[Callable]] = None
    ):
        """
        Initialise le ContextBuilder.
        
        Args:
            fetchers: Dictionnaire de fetchers par DataSource
            validation_rules: Règles de validation personnalisées
        """
        self.fetchers = fetchers or self._create_default_fetchers()
        self.validation_rules = validation_rules or []
        self.logger = logger
    
    def _create_default_fetchers(self) -> Dict[DataSource, DataFetcher]:
        """Crée les fetchers par défaut."""
        return {
            DataSource.CLIENT_PREFERENCES: ClientPreferencesFetcher(
                FetcherConfig(
                    source=DataSource.CLIENT_PREFERENCES,
                    is_critical=True,
                    timeout_seconds=3.0,
                    default_value={"automation_preference": "medium"}
                )
            ),
            DataSource.BUSINESS_VALUE: BusinessValueFetcher(
                FetcherConfig(
                    source=DataSource.BUSINESS_VALUE,
                    is_critical=True,
                    timeout_seconds=3.0,
                    default_value={"expected_roi": 1.0}
                )
            ),
            DataSource.SYSTEM_STATE: SystemStateFetcher(
                FetcherConfig(
                    source=DataSource.SYSTEM_STATE,
                    is_critical=True,
                    timeout_seconds=5.0,
                    default_value={"infrastructure_health": "unknown"}
                )
            ),
            DataSource.COMPLIANCE_CONSTRAINTS: ComplianceConstraintsFetcher(
                FetcherConfig(
                    source=DataSource.COMPLIANCE_CONSTRAINTS,
                    is_critical=True,
                    timeout_seconds=3.0,
                    default_value={"standards": [], "rollback_required": True}
                )
            ),
            DataSource.DECISION_HISTORY: DecisionHistoryFetcher(
                FetcherConfig(
                    source=DataSource.DECISION_HISTORY,
                    is_critical=False,
                    timeout_seconds=2.0,
                    default_value=[]
                ),
                history_store=None  # À remplacer en production
            ),
            DataSource.ENVIRONMENT: EnvironmentFetcher(
                FetcherConfig(
                    source=DataSource.ENVIRONMENT,
                    is_critical=False,
                    timeout_seconds=1.0,
                    default_value={"environment": "production"}
                )
            )
        }
    
    async def build(self, intent: Intent, context_hints: Dict[str, Any] = None) -> DecisionContext:
        """
        Construit le contexte décisionnel.
        
        Args:
            intent: Intention canonique
            context_hints: Indices additionnels (client_id, environment, etc.)
            
        Returns:
            DecisionContext: Contexte immutable
            
        Raises:
            ValueError: Si le contexte critique est incomplet
        """
        start_time = datetime.utcnow()
        context_hints = context_hints or {}
        
        self.logger.info(
            "building_context",
            intent_type=intent.type,
            intent_priority=intent.priority,
            context_hints=context_hints
        )
        
        try:
            # 1. Fetch parallèle des données
            fetch_tasks = []
            for source, fetcher in self.fetchers.items():
                task = self._fetch_with_timeout(
                    fetcher, 
                    intent, 
                    context_hints, 
                    fetcher.config.timeout_seconds
                )
                fetch_tasks.append((source, task))
            
            # Exécuter en parallèle
            results = {}
            for source, task in fetch_tasks:
                try:
                    result = await task
                    results[source] = result
                except Exception as e:
                    # Si critique et pas de fallback, l'erreur a déjà été levée
                    self.logger.error(f"Failed to fetch {source}: {e}")
                    raise
            
            # 2. Vérifier les résultats critiques
            critical_failures = []
            for source, result in results.items():
                fetcher = self.fetchers[source]
                if fetcher.config.is_critical and not result.success:
                    critical_failures.append(f"{source.value}: {result.error}")
            
            if critical_failures:
                error_msg = f"Critical context data incomplete: {', '.join(critical_failures)}"
                self.logger.error(error_msg)
                raise ValueError(error_msg)
            
            # 3. Extraire les données
            context_data = {
                "client_preferences": results[DataSource.CLIENT_PREFERENCES].data,
                "business_value": results[DataSource.BUSINESS_VALUE].data,
                "system_state": results[DataSource.SYSTEM_STATE].data,
                "compliance_constraints": results[DataSource.COMPLIANCE_CONSTRAINTS].data,
                "decision_history": results[DataSource.DECISION_HISTORY].data,
                "environment": results[DataSource.ENVIRONMENT].data,
                "intent": intent.dict(),
                "metadata": {
                    "build_timestamp": datetime.utcnow().isoformat(),
                    "build_duration_seconds": (datetime.utcnow() - start_time).total_seconds(),
                    "fetcher_results": {
                        source.value: {
                            "success": result.success,
                            "cached": result.cached,
                            "duration": result.fetch_duration
                        }
                        for source, result in results.items()
                    }
                }
            }
            
            # 4. Appliquer les règles de validation
            self._validate_context(context_data)
            
            # 5. Appliquer les règles de validation personnalisées
            for rule in self.validation_rules:
                rule(context_data)
            
            # 6. Créer le contexte immutable
            context = DecisionContext(**context_data)
            
            self.logger.info(
                "context_built",
                intent_type=intent.type,
                build_duration=(datetime.utcnow() - start_time).total_seconds(),
                critical_sources=len([s for s, f in self.fetchers.items() if f.config.is_critical]),
                context_id=id(context)  # Pour le tracing
            )
            
            return context
            
        except asyncio.TimeoutError as e:
            error_msg = f"Context building timeout: {str(e)}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        except ValueError as e:
            # Re-lancer les ValueError déjà formatées
            raise
        except Exception as e:
            error_msg = f"Unexpected error building context: {str(e)}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
    
    async def _fetch_with_timeout(
        self, 
        fetcher: DataFetcher, 
        intent: Intent, 
        context_hints: Dict[str, Any], 
        timeout: float
    ) -> FetchResult:
        """Exécute un fetch avec timeout."""
        try:
            return await asyncio.wait_for(
                fetcher.fetch(intent, context_hints),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            if fetcher.config.is_critical and fetcher.config.default_value is None:
                raise ValueError(f"Timeout fetching critical data: {fetcher.config.source.value}")
            
            self.logger.warning(
                f"Timeout fetching {fetcher.config.source.value}, using default"
            )
            return FetchResult(
                source=fetcher.config.source,
                data=fetcher.config.default_value,
                success=False,
                error="timeout",
                fetch_duration=timeout
            )
    
    def _validate_context(self, context_data: Dict[str, Any]) -> None:
        """Valide l'intégrité du contexte."""
        errors = []
        
        # Vérifier les champs critiques
        critical_fields = [
            ("client_preferences", dict),
            ("business_value", dict),
            ("system_state", dict),
            ("compliance_constraints", dict)
        ]
        
        for field_name, expected_type in critical_fields:
            if field_name not in context_data:
                errors.append(f"Missing field: {field_name}")
            elif not isinstance(context_data[field_name], expected_type):
                errors.append(f"Invalid type for {field_name}: expected {expected_type}")
            elif not context_data[field_name]:  # Vérifier non vide
                errors.append(f"Empty field: {field_name}")
        
        # Validation spécifique: client_preferences doit contenir automation_preference
        prefs = context_data.get("client_preferences", {})
        if "automation_preference" not in prefs:
            errors.append("client_preferences missing automation_preference")
        
        # Validation spécifique: business_value doit contenir expected_roi
        business = context_data.get("business_value", {})
        if "expected_roi" not in business:
            errors.append("business_value missing expected_roi")
        else:
            try:
                roi = float(business["expected_roi"])
                if roi <= 0:
                    errors.append("expected_roi must be positive")
            except (ValueError, TypeError):
                errors.append("expected_roi must be a number")
        
        # Validation spécifique: compliance_constraints doit contenir rollback_required
        compliance = context_data.get("compliance_constraints", {})
        if "rollback_required" not in compliance:
            errors.append("compliance_constraints missing rollback_required")
        
        if errors:
            error_msg = f"Context validation failed: {', '.join(errors)}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
    
    def add_validation_rule(self, rule: Callable) -> None:
        """Ajoute une règle de validation personnalisée."""
        self.validation_rules.append(rule)
    
    def update_fetcher(self, source: DataSource, fetcher: DataFetcher) -> None:
        """Met à jour ou ajoute un fetcher."""
        self.fetchers[source] = fetcher
    
    def get_fetcher_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques sur les fetchers."""
        return {
            source.value: {
                "is_critical": fetcher.config.is_critical,
                "timeout": fetcher.config.timeout_seconds,
                "cache_ttl": fetcher.config.cache_ttl_seconds,
                "has_default": fetcher.config.default_value is not None
            }
            for source, fetcher in self.fetchers.items()
        }


# Factory et fonctions utilitaires
def create_context_builder(
    custom_fetchers: Optional[Dict[DataSource, DataFetcher]] = None,
    validation_rules: Optional[List[Callable]] = None
) -> ContextBuilder:
    """Factory pour créer un ContextBuilder."""
    return ContextBuilder(
        fetchers=custom_fetchers,
        validation_rules=validation_rules
    )


# Singleton pour utilisation facile
_context_builder_instance = None

def get_context_builder() -> ContextBuilder:
    """Obtient l'instance singleton du ContextBuilder."""
    global _context_builder_instance
    if _context_builder_instance is None:
        _context_builder_instance = ContextBuilder()
    return _context_builder_instance


# Fonctions pures pour une utilisation simple (testable)
async def build_context(intent: Intent, context_hints: Dict[str, Any] = None) -> DecisionContext:
    """
    Fonction pure pour construire un contexte.
    
    Args:
        intent: Intention canonique
        context_hints: Indices additionnels
        
    Returns:
        DecisionContext: Contexte immutable
    """
    builder = ContextBuilder()
    return await builder.build(intent, context_hints or {})


# Exemple de règles de validation personnalisées
def validate_production_constraints(context_data: Dict[str, Any]) -> None:
    """Règle de validation spécifique à l'environnement de production."""
    environment = context_data.get("environment", {})
    if environment.get("environment") == "production":
        compliance = context_data["compliance_constraints"]
        if not compliance.get("rollback_required", False):
            raise ValueError("Production environment requires rollback capability")
        
        prefs = context_data["client_preferences"]
        if prefs.get("automation_preference") == "none":
            raise ValueError("Production cannot have 'none' automation preference")


def validate_cost_optimization_intent(context_data: Dict[str, Any]) -> None:
    """Règle de validation pour les intentions de réduction de coûts."""
    intent = context_data.get("intent", {})
    if intent.get("type") == "COST_OPTIMIZATION":
        business = context_data["business_value"]
        if business.get("expected_roi", 0) < 1.0:
            raise ValueError("Cost optimization requires ROI >= 1.0")


# Tests unitaires intégrés
if __name__ == "__main__":
    import asyncio
    
    async def test_context_builder():
        """Test basique du ContextBuilder."""
        # Créer un intent de test
        from ..types import Intent
        
        test_intent = Intent(
            type="COST_OPTIMIZATION",
            priority=8,
            success_metrics={"cost_reduction": ">20%"}
        )
        
        # Tester avec hints
        context_hints = {
            "client_id": "test_client_123",
            "environment": "staging",
            "region": "eu-west-1"
        }
        
        # Construire le contexte
        builder = ContextBuilder()
        context = await builder.build(test_intent, context_hints)
        
        print("✓ Contexte construit avec succès")
        print(f"  Client ID: {context.client_preferences.get('client_id')}")
        print(f"  Expected ROI: {context.business_value.get('expected_roi')}")
        print(f"  Infrastructure Health: {context.system_state.get('infrastructure_health')}")
        print(f"  Compliance Standards: {context.compliance_constraints.get('standards')}")
        print(f"  Decision History Count: {len(context.decision_history)}")
        
        # Tester la validation
        print("\n✓ Validation du contexte...")
        assert context.client_preferences["automation_preference"] in ["high", "medium", "low", "none"]
        assert context.business_value["expected_roi"] > 0
        assert context.system_state["infrastructure_health"] in ["healthy", "degraded", "critical", "unknown"]
        assert isinstance(context.compliance_constraints["rollback_required"], bool)
        
        print("✓ Tous les tests passent")
        
        # Tester les erreurs
        print("\n✗ Test des erreurs...")
        try:
            # Créer un fetcher qui échoue toujours
            class FailingFetcher(DataFetcher):
                async def _fetch_impl(self, intent: Intent, context_hints: Dict[str, Any]) -> Any:
                    raise Exception("Simulated fetch failure")
            
            failing_builder = ContextBuilder({
                DataSource.CLIENT_PREFERENCES: FailingFetcher(
                    FetcherConfig(
                        source=DataSource.CLIENT_PREFERENCES,
                        is_critical=True,
                        default_value=None  # Pas de fallback
                    )
                )
            })
            
            await failing_builder.build(test_intent, context_hints)
            print("  ERROR: Should have raised ValueError")
        except ValueError as e:
            print(f"  ✓ Correctly raised ValueError: {str(e)[:50]}...")
        
        print("\n✓ Tests complétés avec succès")
    
    asyncio.run(test_context_builder())