"""
Module: Agent Core Base Class
Description: Classe abstraite MicroAgent avec patterns d'architecture avancés
Version: 1.0.0
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum, auto
from functools import wraps
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)

import structlog
from opentelemetry import trace
from pydantic import BaseModel, Field, ValidationError, validator
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import AgentConfig, ExecutionContextConfig
from ..metrics import AgentMetricsCollector, ExecutionMetrics
from ..registry import AgentRegistry
from ..types import AgentContext, AgentResult, ValidationResult

# Type variables pour le générique
T = TypeVar("T", bound=BaseModel)  # Type du résultat
C = TypeVar("C", bound=BaseModel)  # Type du contexte
R = TypeVar("R")  # Type de retour générique

# Type alias
Logger = structlog.BoundLogger
Tracer = trace.Tracer


class AgentLifecycleState(Enum):
    """États du cycle de vie d'un agent"""
    CREATED = auto()
    INITIALIZED = auto()
    VALIDATING = auto()
    EXECUTING = auto()
    COMPLETED = auto()
    FAILED = auto()
    RETRYING = auto()
    CIRCUIT_OPEN = auto()
    TIMED_OUT = auto()


class CircuitBreakerState(Enum):
    """États du circuit breaker"""
    CLOSED = "closed"  # Opérations normales
    OPEN = "open"      # Court-circuit, échecs répétés
    HALF_OPEN = "half_open"  # Test de récupération


@dataclass
class CircuitBreakerConfig:
    """Configuration du circuit breaker"""
    failure_threshold: int = 5  # Nombre d'échecs avant ouverture
    recovery_timeout: float = 30.0  # Secondes avant tentative de récupération
    half_open_max_attempts: int = 3  # Tentatives en half-open
    excluded_exceptions: Set[Type[Exception]] = field(default_factory=set)


@dataclass
class RetryConfig:
    """Configuration des retries"""
    max_attempts: int = 3
    min_delay: float = 0.1  # secondes
    max_delay: float = 10.0  # secondes
    exponential_base: float = 2.0
    retry_on_exceptions: Tuple[Type[Exception], ...] = (Exception,)


@dataclass
class ExecutionConfig:
    """Configuration d'exécution"""
    timeout: Optional[float] = 60.0  # Timeout en secondes
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    validate_input: bool = True
    validate_output: bool = True
    enable_metrics: bool = True
    enable_tracing: bool = True
    enable_logging: bool = True
    tenant_isolation: bool = True
    cache_enabled: bool = False
    cache_ttl: int = 300  # secondes


class AgentContextModel(BaseModel):
    """Modèle Pydantic pour le contexte de l'agent"""
    tenant_id: str = Field(..., description="Identifiant du tenant")
    correlation_id: str = Field(default_factory=lambda: f"corr_{int(time.time())}")
    user_id: Optional[str] = Field(None, description="Identifiant utilisateur")
    request_id: Optional[str] = Field(None, description="ID de la requête")
    environment: str = Field("production", description="Environnement d'exécution")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        extra = "allow"  # Permet des champs supplémentaires


class MicroAgent(ABC, Generic[T, C]):
    """
    Classe abstraite de base pour tous les MicroAgents.
    
    Implémente des patterns avancés:
    - Template Method Pattern pour le lifecycle
    - Circuit Breaker Pattern pour la résilience
    - Retry Logic avec exponential backoff
    - Validation automatique avec Pydantic
    - Multi-tenancy avec isolation
    - Métriques et tracing
    """
    
    # Métadonnées de l'agent
    name: ClassVar[str]
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = ""
    tags: ClassVar[List[str]] = field(default_factory=list)
    
    # Configuration par défaut
    default_config: ClassVar[ExecutionConfig] = ExecutionConfig()
    
    # Registre des instances
    _instances: ClassVar[Dict[str, MicroAgent]] = {}
    _circuit_state: Dict[str, CircuitBreakerState] = defaultdict(
        lambda: CircuitBreakerState.CLOSED
    )
    _failure_counts: Dict[str, int] = defaultdict(int)
    _last_failure_time: Dict[str, float] = defaultdict(float)
    
    def __init_subclass__(cls, **kwargs):
        """Enregistre automatiquement les sous-classes"""
        super().__init_subclass__(**kwargs)
        if hasattr(cls, 'name'):
            cls._instances[cls.name] = cls
    
    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
        logger: Optional[Logger] = None,
        tracer: Optional[Tracer] = None,
        metrics_collector: Optional[AgentMetricsCollector] = None,
        registry: Optional[AgentRegistry] = None,
    ):
        """
        Initialise l'agent.
        
        Args:
            config: Configuration d'exécution
            logger: Logger structuré
            tracer: Tracer OpenTelemetry
            metrics_collector: Collecteur de métriques
            registry: Registre des agents
        """
        self.config = config or self.default_config
        self.logger = logger or structlog.get_logger(
            agent_name=self.__class__.__name__
        )
        self.tracer = tracer or trace.get_tracer(__name__)
        self.metrics_collector = metrics_collector
        self.registry = registry
        self._state = AgentLifecycleState.CREATED
        self._current_context: Optional[AgentContextModel] = None
        self._execution_metrics: Optional[ExecutionMetrics] = None
        self._cache: Dict[str, Tuple[datetime, Any]] = {}
        
        self._init_logger()
        self.logger.info(
            "agent_initialized",
            agent_name=self.name,
            version=self.version,
            config=self.config
        )
    
    def _init_logger(self) -> None:
        """Initialise le logger avec des bindings spécifiques"""
        self.logger = self.logger.bind(
            agent_name=self.name,
            agent_version=self.version,
            agent_class=self.__class__.__name__
        )
    
    async def execute_with_context(
        self,
        context: Union[AgentContextModel, Dict[str, Any]],
        **kwargs: Any,
    ) -> AgentResult[T]:
        """
        Méthode principale d'exécution avec contexte.
        
        Template Method Pattern: définit le skeleton de l'algorithme.
        
        Args:
            context: Contexte d'exécution
            **kwargs: Arguments supplémentaires
            
        Returns:
            Résultat de l'exécution
        """
        start_time = time.time()
        self._state = AgentLifecycleState.VALIDATING
        
        try:
            # 1. Validation et préparation du contexte
            validated_context = await self._validate_and_prepare_context(context)
            self._current_context = validated_context
            
            # 2. Logging structuré avec contexte
            self.logger = self.logger.bind(
                tenant_id=validated_context.tenant_id,
                correlation_id=validated_context.correlation_id,
                request_id=validated_context.request_id
            )
            
            # 3. Vérification du circuit breaker
            circuit_key = f"{self.name}:{validated_context.tenant_id}"
            if not await self._check_circuit_breaker(circuit_key):
                self._state = AgentLifecycleState.CIRCUIT_OPEN
                raise CircuitOpenError(
                    f"Circuit breaker ouvert pour {circuit_key}"
                )
            
            # 4. Exécution avec tracing et métriques
            with self._create_execution_span(validated_context) as span:
                result = await self._execute_with_retry(
                    validated_context, 
                    span,
                    **kwargs
                )
            
            # 5. Post-execution et métriques
            await self._post_execution(result, validated_context)
            
            # 6. Réinitialisation du circuit breaker en cas de succès
            self._reset_circuit_breaker(circuit_key)
            
            execution_time = time.time() - start_time
            return AgentResult(
                success=True,
                data=result,
                execution_time=execution_time,
                context=validated_context,
                metadata={
                    "agent": self.name,
                    "version": self.version,
                    "tenant": validated_context.tenant_id
                }
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            self._state = AgentLifecycleState.FAILED
            
            # Mise à jour du circuit breaker en cas d'échec
            if isinstance(context, AgentContextModel):
                circuit_key = f"{self.name}:{context.tenant_id}"
                await self._record_failure(circuit_key, e)
            
            self.logger.error(
                "agent_execution_failed",
                error=str(e),
                error_type=type(e).__name__,
                execution_time=execution_time,
                exc_info=True
            )
            
            return AgentResult(
                success=False,
                error=str(e),
                error_type=type(e).__name__,
                execution_time=execution_time,
                context=self._current_context
            )
    
    async def _validate_and_prepare_context(
        self, 
        context: Union[AgentContextModel, Dict[str, Any]]
    ) -> AgentContextModel:
        """
        Valide et prépare le contexte d'exécution.
        
        Args:
            context: Contexte à valider
            
        Returns:
            Contexte validé
        """
        try:
            if isinstance(context, dict):
                validated = AgentContextModel(**context)
            else:
                validated = context
            
            # Validation supplémentaire spécifique à l'agent
            await self._validate_context(validated)
            
            # Isolation multi-tenant
            if self.config.tenant_isolation:
                await self._ensure_tenant_isolation(validated.tenant_id)
            
            return validated
            
        except ValidationError as e:
            self.logger.error(
                "context_validation_failed",
                errors=e.errors(),
                context=context
            )
            raise ContextValidationError(
                f"Validation du contexte échouée: {e}"
            ) from e
    
    async def _check_circuit_breaker(self, circuit_key: str) -> bool:
        """
        Vérifie l'état du circuit breaker.
        
        Args:
            circuit_key: Clé d'identification du circuit
            
        Returns:
            True si le circuit est fermé, False sinon
        """
        state = self._circuit_state[circuit_key]
        
        if state == CircuitBreakerState.OPEN:
            # Vérifier si le timeout de récupération est écoulé
            last_failure = self._last_failure_time.get(circuit_key, 0)
            recovery_time = self.config.circuit_breaker.recovery_timeout
            
            if time.time() - last_failure > recovery_time:
                # Passer en half-open pour tester
                self._circuit_state[circuit_key] = CircuitBreakerState.HALF_OPEN
                self._failure_counts[circuit_key] = 0
                return True
            return False
        
        elif state == CircuitBreakerState.HALF_OPEN:
            # Limiter les tentatives en half-open
            if self._failure_counts[circuit_key] >= self.config.circuit_breaker.half_open_max_attempts:
                self._circuit_state[circuit_key] = CircuitBreakerState.OPEN
                return False
            return True
        
        return True  # Circuit fermé
    
    async def _record_failure(self, circuit_key: str, error: Exception) -> None:
        """
        Enregistre un échec pour le circuit breaker.
        
        Args:
            circuit_key: Clé du circuit
            error: Exception survenue
        """
        # Ignorer certaines exceptions
        if type(error) in self.config.circuit_breaker.excluded_exceptions:
            return
        
        self._failure_counts[circuit_key] += 1
        self._last_failure_time[circuit_key] = time.time()
        
        if self._failure_counts[circuit_key] >= self.config.circuit_breaker.failure_threshold:
            self._circuit_state[circuit_key] = CircuitBreakerState.OPEN
            self.logger.warning(
                "circuit_breaker_opened",
                circuit_key=circuit_key,
                failure_count=self._failure_counts[circuit_key]
            )
    
    def _reset_circuit_breaker(self, circuit_key: str) -> None:
        """
        Réinitialise le circuit breaker après un succès.
        
        Args:
            circuit_key: Clé du circuit
        """
        if self._circuit_state[circuit_key] != CircuitBreakerState.CLOSED:
            self._circuit_state[circuit_key] = CircuitBreakerState.CLOSED
            self._failure_counts[circuit_key] = 0
            self.logger.info(
                "circuit_breaker_reset",
                circuit_key=circuit_key
            )
    
    @contextlib.contextmanager
    def _create_execution_span(
        self, 
        context: AgentContextModel
    ) -> AsyncGenerator[trace.Span, None]:
        """
        Crée un span OpenTelemetry pour le tracing.
        
        Args:
            context: Contexte d'exécution
        """
        if not self.config.enable_tracing:
            yield trace.get_current_span()
            return
        
        span_name = f"{self.name}.execute"
        span_attributes = {
            "agent.name": self.name,
            "agent.version": self.version,
            "tenant.id": context.tenant_id,
            "correlation.id": context.correlation_id,
        }
        
        with self.tracer.start_as_current_span(
            span_name,
            attributes=span_attributes,
            kind=trace.SpanKind.INTERNAL
        ) as span:
            try:
                yield span
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                raise
            finally:
                span.set_status(trace.Status(trace.StatusCode.OK))
    
    async def _execute_with_retry(
        self,
        context: AgentContextModel,
        span: trace.Span,
        **kwargs: Any,
    ) -> T:
        """
        Exécute l'agent avec retry logic.
        
        Args:
            context: Contexte validé
            span: Span OpenTelemetry
            **kwargs: Arguments supplémentaires
            
        Returns:
            Résultat de l'exécution
        """
        # Vérifier le cache
        if self.config.cache_enabled:
            cache_key = self._generate_cache_key(context, kwargs)
            if cached_result := self._get_from_cache(cache_key):
                span.add_event("cache_hit", {"cache_key": cache_key})
                self.logger.debug("cache_hit", cache_key=cache_key)
                return cached_result
        
        span.add_event("cache_miss" if self.config.cache_enabled else "execution_start")
        
        # Configuration des retries
        retry_config = self.config.retry
        
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(retry_config.max_attempts),
                wait=wait_exponential(
                    min=retry_config.min_delay,
                    max=retry_config.max_delay,
                    multiplier=retry_config.exponential_base
                ),
                retry=retry_if_exception_type(retry_config.retry_on_exceptions),
                reraise=True,
            ):
                with attempt:
                    self._state = AgentLifecycleState.EXECUTING
                    
                    if attempt.retry_state.attempt_number > 1:
                        self._state = AgentLifecycleState.RETRYING
                        self.logger.warning(
                            "retry_attempt",
                            attempt=attempt.retry_state.attempt_number,
                            delay=attempt.retry_state.idle_for
                        )
                    
                    # Hooks pre-execute
                    await self.pre_execute(context)
                    
                    # Exécution principale avec timeout
                    if self.config.timeout:
                        result = await asyncio.wait_for(
                            self._execute(context, **kwargs),
                            timeout=self.config.timeout
                        )
                    else:
                        result = await self._execute(context, **kwargs)
                    
                    # Validation du résultat
                    if self.config.validate_output:
                        result = await self._validate_output(result)
                    
                    # Mise en cache
                    if self.config.cache_enabled:
                        cache_key = self._generate_cache_key(context, kwargs)
                        self._add_to_cache(cache_key, result)
                    
                    return result
                    
        except asyncio.TimeoutError:
            self._state = AgentLifecycleState.TIMED_OUT
            span.set_status(trace.Status(
                trace.StatusCode.ERROR, 
                "Execution timeout"
            ))
            raise TimeoutError(
                f"Agent {self.name} timeout après {self.config.timeout}s"
            )
            
        except RetryError as e:
            span.set_status(trace.Status(
                trace.StatusCode.ERROR,
                f"Échec après {retry_config.max_attempts} tentatives"
            ))
            raise ExecutionError(
                f"Échec de l'exécution après {retry_config.max_attempts} tentatives"
            ) from e.last_attempt.exception()
    
    def _generate_cache_key(
        self, 
        context: AgentContextModel, 
        kwargs: Dict[str, Any]
    ) -> str:
        """
        Génère une clé de cache unique.
        
        Args:
            context: Contexte d'exécution
            kwargs: Arguments
            
        Returns:
            Clé de cache
        """
        import hashlib
        
        # Créer une représentation sérialisable
        cache_data = {
            "agent": self.name,
            "context": context.dict(),
            "kwargs": kwargs,
            "version": self.version
        }
        
        # Convertir en JSON et hasher
        cache_json = json.dumps(
            cache_data, 
            sort_keys=True, 
            default=str
        )
        return hashlib.sha256(cache_json.encode()).hexdigest()
    
    def _get_from_cache(self, cache_key: str) -> Optional[T]:
        """
        Récupère un résultat du cache.
        
        Args:
            cache_key: Clé de cache
            
        Returns:
            Résultat en cache ou None
        """
        if cache_key in self._cache:
            timestamp, result = self._cache[cache_key]
            if datetime.now() - timestamp < timedelta(
                seconds=self.config.cache_ttl
            ):
                return result
            else:
                # Expiration du cache
                del self._cache[cache_key]
        return None
    
    def _add_to_cache(self, cache_key: str, result: T) -> None:
        """
        Ajoute un résultat au cache.
        
        Args:
            cache_key: Clé de cache
            result: Résultat à cacher
        """
        # Limiter la taille du cache
        max_cache_size = 1000
        if len(self._cache) >= max_cache_size:
            # Supprimer les plus anciennes entrées
            oldest_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k][0]
            )[:max_cache_size // 4]
            for key in oldest_keys:
                del self._cache[key]
        
        self._cache[cache_key] = (datetime.now(), result)
    
    async def _post_execution(
        self, 
        result: T, 
        context: AgentContextModel
    ) -> None:
        """
        Post-traitement après exécution.
        
        Args:
            result: Résultat de l'exécution
            context: Contexte d'exécution
        """
        self._state = AgentLifecycleState.COMPLETED
        
        # Hook post-execute
        await self.post_execute(result, context)
        
        # Collecte des métriques
        if self.config.enable_metrics and self.metrics_collector:
            await self.metrics_collector.record_execution(
                agent_name=self.name,
                tenant_id=context.tenant_id,
                success=True,
                execution_time=self._execution_metrics.execution_time
                if self._execution_metrics else 0.0,
                metadata={
                    "correlation_id": context.correlation_id,
                    "cache_hit": self.config.cache_enabled
                }
            )
        
        # Logging structuré
        self.logger.info(
            "agent_execution_completed",
            success=True,
            execution_time=time.time() - (self._execution_metrics.start_time 
                if self._execution_metrics else time.time()),
            tenant_id=context.tenant_id,
            result_type=type(result).__name__
        )
    
    # ====== Méthodes abstraites et hooks ======
    
    @abstractmethod
    async def _execute(self, context: C, **kwargs) -> T:
        """
        Méthode d'exécution principale à implémenter.
        
        Args:
            context: Contexte d'exécution
            **kwargs: Arguments supplémentaires
            
        Returns:
            Résultat de l'exécution
        """
        pass
    
    async def _validate_context(self, context: AgentContextModel) -> None:
        """
        Validation supplémentaire du contexte.
        Peut être override par les sous-classes.
        
        Args:
            context: Contexte à valider
        """
        pass
    
    async def _validate_output(self, result: Any) -> T:
        """
        Validation du résultat.
        
        Args:
            result: Résultat à valider
            
        Returns:
            Résultat validé
        """
        # Par défaut, retourne tel quel
        # Les sous-classes peuvent implémenter une validation spécifique
        return result
    
    async def _ensure_tenant_isolation(self, tenant_id: str) -> None:
        """
        Assure l'isolation multi-tenant.
        
        Args:
            tenant_id: Identifiant du tenant
        """
        # Implémentation par défaut: vérifications de base
        if not tenant_id:
            raise ValueError("tenant_id est requis")
        
        # Ici on pourrait vérifier les permissions, quotas, etc.
        # Cette méthode peut être override par les sous-classes
        pass
    
    async def pre_execute(self, context: AgentContextModel) -> None:
        """
        Hook exécuté avant l'exécution principale.
        
        Args:
            context: Contexte d'exécution
        """
        # Par défaut vide, peut être override
        pass
    
    async def post_execute(
        self, 
        result: T, 
        context: AgentContextModel
    ) -> None:
        """
        Hook exécuté après l'exécution principale.
        
        Args:
            result: Résultat de l'exécution
            context: Contexte d'exécution
        """
        # Par défaut vide, peut être override
        pass
    
    # ====== Méthodes utilitaires ======
    
    def get_state(self) -> AgentLifecycleState:
        """Retourne l'état courant de l'agent"""
        return self._state
    
    def get_current_context(self) -> Optional[AgentContextModel]:
        """Retourne le contexte d'exécution courant"""
        return self._current_context
    
    def clear_cache(self) -> None:
        """Vide le cache de l'agent"""
        self._cache.clear()
        self.logger.info("cache_cleared")
    
    @classmethod
    def get_agent_class(cls, name: str) -> Optional[Type["MicroAgent"]]:
        """Récupère une classe d'agent par son nom"""
        return cls._instances.get(name)
    
    @classmethod
    def list_agents(cls) -> List[str]:
        """Liste tous les agents enregistrés"""
        return list(cls._instances.keys())


# ====== Exceptions personnalisées ======

class AgentError(Exception):
    """Exception de base pour les erreurs d'agent"""
    pass


class ContextValidationError(AgentError):
    """Erreur de validation du contexte"""
    pass


class ExecutionError(AgentError):
    """Erreur d'exécution de l'agent"""
    pass


class CircuitOpenError(AgentError):
    """Erreur lorsque le circuit breaker est ouvert"""
    pass


class TimeoutError(AgentError):
    """Timeout de l'exécution de l'agent"""
    pass


# ====== Décorateurs utilitaires ======

def agent_metrics(name: str = None):
    """
    Décorateur pour ajouter automatiquement des métriques à un agent.
    
    Args:
        name: Nom personnalisé pour les métriques
    """
    def decorator(cls):
        original_execute = cls._execute
        
        @wraps(original_execute)
        async def wrapped_execute(self, context, **kwargs):
            start_time = time.time()
            
            try:
                result = await original_execute(self, context, **kwargs)
                execution_time = time.time() - start_time
                
                # Enregistrer les métriques
                if self.metrics_collector:
                    await self.metrics_collector.record_execution(
                        agent_name=name or self.name,
                        tenant_id=context.tenant_id,
                        success=True,
                        execution_time=execution_time
                    )
                
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                
                if self.metrics_collector:
                    await self.metrics_collector.record_execution(
                        agent_name=name or self.name,
                        tenant_id=context.tenant_id,
                        success=False,
                        execution_time=execution_time,
                        error_type=type(e).__name__
                    )
                
                raise
        
        cls._execute = wrapped_execute
        return cls
    
    return decorator


def validate_context(schema: Type[BaseModel]):
    """
    Décorateur pour valider automatiquement le contexte.
    
    Args:
        schema: Schéma Pydantic pour validation
    """
    def decorator(cls):
        original_validate = cls._validate_context
        
        async def wrapped_validate(self, context):
            # Validation avec le schéma fourni
            try:
                validated = schema(**context.dict())
                # Mettre à jour le contexte avec les valeurs validées
                for field in schema.__fields__:
                    if hasattr(validated, field):
                        setattr(context, field, getattr(validated, field))
            except ValidationError as e:
                raise ContextValidationError(
                    f"Validation du contexte échouée: {e}"
                ) from e
            
            # Appeler la validation originale si elle existe
            if original_validate:
                await original_validate(self, context)
        
        cls._validate_context = wrapped_validate
        return cls
    
    return decorator