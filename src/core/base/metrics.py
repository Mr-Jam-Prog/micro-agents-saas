"""
Collecte de métriques pour les agents.
"""

import time
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Callable, Awaitable
from enum import Enum, IntEnum
from collections import defaultdict, deque
import statistics

import structlog
from opentelemetry import metrics
from prometheus_client import (
    Counter, Gauge, Histogram, Summary, CollectorRegistry, generate_latest
)


class MetricType(Enum):
    """Types de métriques supportés"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    RATE = "rate"


class CircuitState(IntEnum):
    """États du circuit breaker pour les métriques"""
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


@dataclass
class ExecutionMetrics:
    """Métriques détaillées d'une exécution"""
    agent_name: str
    tenant_id: str
    correlation_id: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    execution_time: float = 0.0
    success: bool = False
    error_type: Optional[str] = None
    cache_hit: bool = False
    retry_count: int = 0
    circuit_state: CircuitState = CircuitState.CLOSED
    memory_usage_mb: Optional[float] = None
    custom_metrics: Dict[str, float] = field(default_factory=dict)
    
    def record_start(self):
        """Enregistre le début de l'exécution"""
        self.start_time = time.time()
    
    def record_completion(
        self,
        success: bool = True,
        error_type: Optional[str] = None,
        cache_hit: bool = False,
        retry_count: int = 0,
        circuit_state: CircuitState = CircuitState.CLOSED,
        memory_usage_mb: Optional[float] = None
    ):
        """Enregistre la fin de l'exécution"""
        self.end_time = time.time()
        self.execution_time = self.end_time - self.start_time
        self.success = success
        self.error_type = error_type
        self.cache_hit = cache_hit
        self.retry_count = retry_count
        self.circuit_state = circuit_state
        self.memory_usage_mb = memory_usage_mb
    
    def add_custom_metric(self, name: str, value: float):
        """Ajoute une métrique personnalisée"""
        self.custom_metrics[name] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            "agent_name": self.agent_name,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "execution_time": self.execution_time,
            "success": self.success,
            "error_type": self.error_type,
            "cache_hit": self.cache_hit,
            "retry_count": self.retry_count,
            "circuit_state": self.circuit_state.name,
            "memory_usage_mb": self.memory_usage_mb,
            "custom_metrics": self.custom_metrics,
            "timestamp": datetime.fromtimestamp(self.start_time).isoformat()
        }


@dataclass
class AgentMetrics:
    """Métriques agrégées pour un agent"""
    agent_name: str
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_execution_time: float = 0.0
    avg_execution_time: float = 0.0
    min_execution_time: float = float('inf')
    max_execution_time: float = 0.0
    cache_hit_rate: float = 0.0
    circuit_open_count: int = 0
    retry_rate: float = 0.0
    error_distribution: Dict[str, int] = field(default_factory=dict)
    execution_times: deque = field(default_factory=lambda: deque(maxlen=1000))
    
    def update(self, metrics: ExecutionMetrics):
        """Met à jour les métriques avec une nouvelle exécution"""
        self.total_executions += 1
        
        if metrics.success:
            self.successful_executions += 1
        else:
            self.failed_executions += 1
            if metrics.error_type:
                self.error_distribution[metrics.error_type] = (
                    self.error_distribution.get(metrics.error_type, 0) + 1
                )
        
        self.total_execution_time += metrics.execution_time
        self.execution_times.append(metrics.execution_time)
        
        # Mettre à jour les min/max
        if metrics.execution_time < self.min_execution_time:
            self.min_execution_time = metrics.execution_time
        if metrics.execution_time > self.max_execution_time:
            self.max_execution_time = metrics.execution_time
        
        # Calculer la moyenne
        self.avg_execution_time = statistics.mean(self.execution_times)
        
        # Mettre à jour le taux de cache
        if metrics.cache_hit:
            cache_hits = sum(1 for m in self.execution_times if getattr(m, 'cache_hit', False))
            self.cache_hit_rate = cache_hits / len(self.execution_times) if self.execution_times else 0
        
        # Circuit breaker
        if metrics.circuit_state == CircuitState.OPEN:
            self.circuit_open_count += 1
        
        # Taux de retry
        if metrics.retry_count > 0:
            retry_executions = sum(1 for m in self.execution_times if getattr(m, 'retry_count', 0) > 0)
            self.retry_rate = retry_executions / len(self.execution_times) if self.execution_times else 0


class AgentMetricsCollector:
    """Collecteur de métriques avancé pour les agents"""
    
    def __init__(
        self,
        registry: Optional[CollectorRegistry] = None,
        enable_prometheus: bool = True,
        enable_opentelemetry: bool = True,
        retention_period: timedelta = timedelta(hours=24)
    ):
        self.logger = structlog.get_logger(__name__)
        self.enable_prometheus = enable_prometheus
        self.enable_opentelemetry = enable_opentelemetry
        self.retention_period = retention_period
        
        # Stockage des métriques
        self.agent_metrics: Dict[str, AgentMetrics] = {}
        self.execution_history: Dict[str, List[ExecutionMetrics]] = defaultdict(list)
        
        # Métriques Prometheus
        if enable_prometheus:
            self.registry = registry or CollectorRegistry()
            self._init_prometheus_metrics()
        
        # Métriques OpenTelemetry
        if enable_opentelemetry:
            self.meter = metrics.get_meter(__name__)
            self._init_opentelemetry_metrics()
        
        # Tâche de nettoyage
        self._cleanup_task: Optional[asyncio.Task] = None
    
    def _init_prometheus_metrics(self):
        """Initialise les métriques Prometheus"""
        # Compteurs
        self.execution_counter = Counter(
            'microagents_executions_total',
            'Total agent executions',
            ['agent_name', 'tenant_id', 'status'],
            registry=self.registry
        )
        
        self.error_counter = Counter(
            'microagents_errors_total',
            'Total agent errors',
            ['agent_name', 'tenant_id', 'error_type'],
            registry=self.registry
        )
        
        # Gauges
        self.circuit_state_gauge = Gauge(
            'microagents_circuit_state',
            'Circuit breaker state',
            ['agent_name', 'tenant_id'],
            registry=self.registry
        )
        
        self.cache_hit_gauge = Gauge(
            'microagents_cache_hit_ratio',
            'Cache hit ratio',
            ['agent_name', 'tenant_id'],
            registry=self.registry
        )
        
        # Histograms
        self.execution_duration = Histogram(
            'microagents_execution_duration_seconds',
            'Execution duration distribution',
            ['agent_name', 'tenant_id'],
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0),
            registry=self.registry
        )
        
        self.retry_count_histogram = Histogram(
            'microagents_retry_count',
            'Number of retries per execution',
            ['agent_name', 'tenant_id'],
            buckets=(0, 1, 2, 3, 5, 10),
            registry=self.registry
        )
    
    def _init_opentelemetry_metrics(self):
        """Initialise les métriques OpenTelemetry"""
        # Compteurs OpenTelemetry
        self.otel_execution_counter = self.meter.create_counter(
            name="microagents.executions.total",
            description="Total agent executions",
            unit="1"
        )
        
        # Histogrammes OpenTelemetry
        self.otel_execution_duration = self.meter.create_histogram(
            name="microagents.execution.duration",
            description="Execution duration",
            unit="s"
        )
    
    async def record_execution(self, metrics: ExecutionMetrics):
        """Enregistre une exécution d'agent"""
        # Mettre à jour les métriques Prometheus
        if self.enable_prometheus:
            status = "success" if metrics.success else "failure"
            
            self.execution_counter.labels(
                agent_name=metrics.agent_name,
                tenant_id=metrics.tenant_id,
                status=status
            ).inc()
            
            if not metrics.success and metrics.error_type:
                self.error_counter.labels(
                    agent_name=metrics.agent_name,
                    tenant_id=metrics.tenant_id,
                    error_type=metrics.error_type
                ).inc()
            
            self.execution_duration.labels(
                agent_name=metrics.agent_name,
                tenant_id=metrics.tenant_id
            ).observe(metrics.execution_time)
            
            self.circuit_state_gauge.labels(
                agent_name=metrics.agent_name,
                tenant_id=metrics.tenant_id
            ).set(metrics.circuit_state.value)
            
            self.retry_count_histogram.labels(
                agent_name=metrics.agent_name,
                tenant_id=metrics.tenant_id
            ).observe(metrics.retry_count)
        
        # Mettre à jour les métriques OpenTelemetry
        if self.enable_opentelemetry:
            attributes = {
                "agent.name": metrics.agent_name,
                "tenant.id": metrics.tenant_id,
                "success": str(metrics.success).lower()
            }
            
            self.otel_execution_counter.add(1, attributes)
            self.otel_execution_duration.record(
                metrics.execution_time,
                attributes
            )
        
        # Mettre à jour les métriques internes
        agent_key = f"{metrics.agent_name}:{metrics.tenant_id}"
        
        if agent_key not in self.agent_metrics:
            self.agent_metrics[agent_key] = AgentMetrics(
                agent_name=metrics.agent_name
            )
        
        self.agent_metrics[agent_key].update(metrics)
        self.execution_history[agent_key].append(metrics)
        
        # Nettoyer l'historique si nécessaire
        self._cleanup_old_metrics()
    
    def _cleanup_old_metrics(self):
        """Nettoie les métriques plus anciennes que la période de rétention"""
        cutoff_time = time.time() - self.retention_period.total_seconds()
        
        for agent_key, executions in list(self.execution_history.items()):
            # Filtrer les exécutions récentes
            recent_executions = [
                m for m in executions if m.start_time >= cutoff_time
            ]
            
            if recent_executions:
                self.execution_history[agent_key] = recent_executions
            else:
                del self.execution_history[agent_key]
                if agent_key in self.agent_metrics:
                    del self.agent_metrics[agent_key]
    
    def get_agent_metrics(self, agent_name: str, tenant_id: str) -> Optional[AgentMetrics]:
        """Récupère les métriques d'un agent spécifique"""
        agent_key = f"{agent_name}:{tenant_id}"
        return self.agent_metrics.get(agent_key)
    
    def get_all_metrics(self) -> Dict[str, AgentMetrics]:
        """Récupère toutes les métriques"""
        return self.agent_metrics.copy()
    
    def get_prometheus_metrics(self) -> bytes:
        """Retourne les métriques Prometheus au format texte"""
        if not self.enable_prometheus:
            return b""
        return generate_latest(self.registry)
    
    async def start_cleanup_task(self, interval: int = 300):
        """Démarre la tâche périodique de nettoyage"""
        async def cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(interval)
                    self._cleanup_old_metrics()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error("cleanup_task_failed", error=str(e))
        
        self._cleanup_task = asyncio.create_task(cleanup_loop())
    
    async def stop_cleanup_task(self):
        """Arrête la tâche de nettoyage"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            finally:
                self._cleanup_task = None