"""
Configuration de base pour les agents.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set, Tuple, Type
import time


@dataclass
class AgentMetadata:
    """Métadonnées de l'agent"""
    name: str
    version: str = "1.0.0"
    description: Optional[str] = None
    author: Optional[str] = None
    license: Optional[str] = "Apache-2.0"
    tags: List[str] = field(default_factory=list)
    documentation_url: Optional[str] = None
    repository_url: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class CircuitBreakerConfig:
    """Configuration du circuit breaker"""
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_attempts: int = 3
    excluded_exceptions: Set[Type[Exception]] = field(default_factory=set)
    enable_monitoring: bool = True
    state_change_callback: Optional[str] = None


@dataclass
class RetryConfig:
    """Configuration des retries"""
    max_attempts: int = 3
    min_delay: float = 0.1
    max_delay: float = 10.0
    exponential_base: float = 2.0
    jitter: bool = True
    retry_on_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    no_retry_on_exceptions: Tuple[Type[Exception], ...] = (KeyboardInterrupt, SystemExit)


@dataclass
class CacheConfig:
    """Configuration du cache"""
    enabled: bool = False
    ttl: int = 300  # seconds
    max_size: int = 1000
    strategy: str = "lru"  # lru, fifo, lfu
    namespace: Optional[str] = None
    invalidate_on_failure: bool = True


@dataclass
class MonitoringConfig:
    """Configuration du monitoring"""
    enable_metrics: bool = True
    enable_tracing: bool = True
    enable_logging: bool = True
    metrics_prefix: str = "microagents"
    trace_sampling_rate: float = 0.1
    log_level: str = "INFO"
    structured_logging: bool = True


@dataclass
class SecurityConfig:
    """Configuration de sécurité"""
    tenant_isolation: bool = True
    input_validation: bool = True
    output_validation: bool = True
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 100
    rate_limit_period: int = 60  # seconds
    encrypt_sensitive_data: bool = False
    audit_logging: bool = True


@dataclass
class ExecutionConfig:
    """Configuration d'exécution complète"""
    # Performance
    timeout: Optional[float] = 60.0
    max_concurrent: Optional[int] = None
    memory_limit: Optional[int] = None  # MB
    
    # Résilience
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    
    # Cache
    cache: CacheConfig = field(default_factory=CacheConfig)
    
    # Monitoring
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    
    # Sécurité
    security: SecurityConfig = field(default_factory=SecurityConfig)
    
    # Autres
    validate_input: bool = True
    validate_output: bool = True
    enable_telemetry: bool = True
    telemetry_endpoint: Optional[str] = None
    feature_flags: Dict[str, bool] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit la configuration en dictionnaire"""
        import dataclasses
        return dataclasses.asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionConfig":
        """Crée une configuration à partir d'un dictionnaire"""
        return cls(**data)


@dataclass
class AgentConfig:
    """Configuration complète d'un agent"""
    metadata: AgentMetadata
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    dependencies: List[str] = field(default_factory=list)
    environment_variables: Dict[str, str] = field(default_factory=dict)
    secrets: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    
    def validate(self) -> bool:
        """Valide la configuration"""
        if not self.metadata.name:
            raise ValueError("Le nom de l'agent est requis")
        if self.execution.timeout and self.execution.timeout <= 0:
            raise ValueError("Le timeout doit être positif")
        return True