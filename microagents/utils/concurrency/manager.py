"""
Concurrency Manager pour MicroAgents Platform

Gestion avancée de la concurrence avec patterns de résilience,
optimisation des ressources et monitoring complet.
"""

import asyncio
import concurrent.futures
import threading
import time
import uuid
import logging
import math
import os
import psutil
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, IntEnum
from typing import (
    Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple,
    TypeVar, Generic, Union, cast
)
from functools import wraps
from concurrent.futures import Future, ThreadPoolExecutor, ProcessPoolExecutor
from contextlib import contextmanager
from threading import Lock, RLock, Semaphore, Event, Condition
from asyncio import (
    Queue, PriorityQueue, Semaphore as AsyncSemaphore,
    Lock as AsyncLock, Event as AsyncEvent, Condition as AsyncCondition,
    Task, CancelledError, TimeoutError
)

from pydantic import BaseModel, Field, validator
from prometheus_client import Counter, Gauge, Histogram, Summary

from microagents.utils.serialization.serializers import json_serializer

T = TypeVar('T')
R = TypeVar('R')

logger = logging.getLogger(__name__)


# ==================== METRICS ====================

class ConcurrencyMetrics:
    """Métriques Prometheus pour le monitoring de concurrence."""
    
    def __init__(self):
        # Counters
        self.tasks_submitted = Counter(
            'concurrency_tasks_submitted_total',
            'Total tasks submitted',
            ['pool_type', 'priority']
        )
        
        self.tasks_completed = Counter(
            'concurrency_tasks_completed_total',
            'Total tasks completed',
            ['pool_type', 'priority', 'status']
        )
        
        self.circuit_breaker_state_changes = Counter(
            'concurrency_circuit_breaker_state_changes_total',
            'Circuit breaker state changes',
            ['service', 'from_state', 'to_state']
        )
        
        self.retry_attempts = Counter(
            'concurrency_retry_attempts_total',
            'Total retry attempts',
            ['service', 'status']
        )
        
        # Gauges
        self.active_workers = Gauge(
            'concurrency_active_workers',
            'Number of active workers',
            ['pool_type']
        )
        
        self.queue_size = Gauge(
            'concurrency_queue_size',
            'Size of task queues',
            ['pool_type', 'priority']
        )
        
        self.circuit_breaker_state = Gauge(
            'concurrency_circuit_breaker_state',
            'Circuit breaker state (0=closed, 1=open, 2=half_open)',
            ['service']
        )
        
        # Histograms
        self.task_duration = Histogram(
            'concurrency_task_duration_seconds',
            'Task execution duration',
            ['pool_type', 'priority'],
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0]
        )
        
        self.queue_wait_time = Histogram(
            'concurrency_queue_wait_time_seconds',
            'Time tasks spend in queue',
            ['pool_type', 'priority'],
            buckets=[0.001, 0.01, 0.1, 1.0, 5.0, 10.0]
        )


# ==================== ENUMS ET MODÈLES ====================

class TaskPriority(IntEnum):
    """Priorité des tâches."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


class CircuitState(str, Enum):
    """État du circuit breaker."""
    CLOSED = "closed"      # Requêtes passent normalement
    OPEN = "open"          # Requêtes bloquées
    HALF_OPEN = "half_open" # Test de récupération


class TaskStatus(str, Enum):
    """Statut d'une tâche."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class TaskInfo:
    """Information sur une tâche."""
    id: str
    name: str
    submitted_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    worker_id: Optional[str] = None
    cpu_affinity: Optional[List[int]] = None
    memory_limit_mb: Optional[int] = None
    
    @property
    def duration(self) -> Optional[float]:
        """Durée d'exécution en secondes."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    @property
    def wait_time(self) -> Optional[float]:
        """Temps d'attente en file."""
        if self.submitted_at and self.started_at:
            return (self.started_at - self.submitted_at).total_seconds()
        return None


class CircuitBreakerConfig(BaseModel):
    """Configuration du circuit breaker."""
    failure_threshold: int = Field(default=5, ge=1)
    success_threshold: int = Field(default=3, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0)
    half_open_timeout_seconds: float = Field(default=30.0, gt=0)
    
    @validator('success_threshold')
    def validate_success_threshold(cls, v, values):
        if 'failure_threshold' in values and v >= values['failure_threshold']:
            raise ValueError('success_threshold doit être < failure_threshold')
        return v


class RateLimitConfig(BaseModel):
    """Configuration de rate limiting."""
    requests_per_second: float = Field(default=10.0, gt=0)
    burst_size: int = Field(default=20, ge=1)
    
    @property
    def interval(self) -> float:
        """Intervalle entre les requêtes en secondes."""
        return 1.0 / self.requests_per_second


class RetryConfig(BaseModel):
    """Configuration des retries."""
    max_attempts: int = Field(default=3, ge=1)
    initial_delay: float = Field(default=0.1, ge=0)
    max_delay: float = Field(default=30.0, gt=0)
    backoff_factor: float = Field(default=2.0, gt=1.0)
    jitter: float = Field(default=0.1, ge=0, le=1.0)
    
    def get_delay(self, attempt: int) -> float:
        """Calcule le délai pour un essai donné."""
        delay = self.initial_delay * (self.backoff_factor ** (attempt - 1))
        delay = min(delay, self.max_delay)
        
        # Ajout de jitter
        if self.jitter > 0:
            import random
            jitter_amount = delay * self.jitter
            delay += random.uniform(-jitter_amount, jitter_amount)
        
        return max(0, delay)


# ==================== CIRCUIT BREAKER ====================

class CircuitBreaker:
    """
    Implémentation du pattern Circuit Breaker.
    Protège les services en cas de défaillance.
    """
    
    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig,
        metrics: Optional[ConcurrencyMetrics] = None
    ):
        self.name = name
        self.config = config
        self.metrics = metrics
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state_lock = RLock()
        
        if metrics:
            metrics.circuit_breaker_state.labels(service=name).set(0)
    
    def record_success(self) -> None:
        """Enregistre un succès."""
        with self.state_lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                
                if self.success_count >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0  # Reset sur succès consécutif
    
    def record_failure(self) -> None:
        """Enregistre un échec."""
        with self.state_lock:
            self.failure_count += 1
            self.last_failure_time = datetime.utcnow()
            
            if self.state == CircuitState.CLOSED:
                if self.failure_count >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
            
            elif self.state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition vers un nouvel état."""
        old_state = self.state
        self.state = new_state
        
        # Reset counters
        if new_state == CircuitState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self.success_count = 0
        elif new_state == CircuitState.OPEN:
            self.failure_count = 0
        
        logger.info(
            f"Circuit breaker '{self.name}': {old_state} → {new_state}"
        )
        
        # Métriques
        if self.metrics:
            state_map = {
                CircuitState.CLOSED: 0,
                CircuitState.OPEN: 1,
                CircuitState.HALF_OPEN: 2
            }
            self.metrics.circuit_breaker_state_changes.labels(
                service=self.name,
                from_state=old_state.value,
                to_state=new_state.value
            ).inc()
            
            self.metrics.circuit_breaker_state.labels(
                service=self.name
            ).set(state_map[new_state])
    
    def allow_request(self) -> bool:
        """Vérifie si une requête est autorisée."""
        with self.state_lock:
            if self.state == CircuitState.CLOSED:
                return True
            
            elif self.state == CircuitState.OPEN:
                # Vérifier si le timeout est écoulé
                if self.last_failure_time:
                    elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
                    if elapsed >= self.config.timeout_seconds:
                        self._transition_to(CircuitState.HALF_OPEN)
                        return True
                return False
            
            elif self.state == CircuitState.HALF_OPEN:
                return True
            
            return False
    
    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Décorateur pour circuit breaker."""
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            if not self.allow_request():
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is OPEN"
                )
            
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure()
                raise
        
        return wrapper
    
    async def __call_async(self, func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        """Décorateur async pour circuit breaker."""
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            if not self.allow_request():
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is OPEN"
                )
            
            try:
                result = await func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure()
                raise
        
        return wrapper


class CircuitBreakerError(Exception):
    """Exception levée quand le circuit breaker est ouvert."""
    pass


# ==================== RATE LIMITER ====================

class RateLimiter:
    """
    Implémentation du rate limiting avec token bucket.
    """
    
    def __init__(
        self,
        config: RateLimitConfig,
        metrics: Optional[ConcurrencyMetrics] = None
    ):
        self.config = config
        self.metrics = metrics
        
        self.tokens = config.burst_size
        self.last_refill = time.monotonic()
        self.lock = Lock()
    
    def acquire(self, tokens: int = 1) -> bool:
        """
        Tente d'acquérir des tokens.
        
        Returns:
            True si les tokens ont été acquis, False sinon
        """
        with self.lock:
            self._refill_tokens()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    def _refill_tokens(self) -> None:
        """Recharge les tokens selon le temps écoulé."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        
        # Nombre de tokens à ajouter
        new_tokens = elapsed * self.config.requests_per_second
        
        if new_tokens > 0:
            self.tokens = min(
                self.config.burst_size,
                self.tokens + new_tokens
            )
            self.last_refill = now
    
    async def acquire_async(self, tokens: int = 1) -> bool:
        """Version async de acquire."""
        return await asyncio.get_event_loop().run_in_executor(
            None, self.acquire, tokens
        )
    
    @contextmanager
    def limit(self):
        """Context manager pour rate limiting."""
        acquired = False
        start_time = time.monotonic()
        
        while not acquired:
            acquired = self.acquire()
            if not acquired:
                time.sleep(self.config.interval)
        
        wait_time = time.monotonic() - start_time
        if self.metrics:
            # Métrique de wait time
            pass
        
        try:
            yield
        finally:
            pass
    
    async def limit_async(self):
        """Context manager async pour rate limiting."""
        acquired = False
        start_time = time.monotonic()
        
        while not acquired:
            acquired = await self.acquire_async()
            if not acquired:
                await asyncio.sleep(self.config.interval)
        
        wait_time = time.monotonic() - start_time
        
        class AsyncContext:
            async def __aenter__(self):
                pass
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
        
        return AsyncContext()


# ==================== RETRY MECHANISM ====================

class RetryManager:
    """
    Gestion des retries avec backoff exponentiel et jitter.
    """
    
    def __init__(
        self,
        config: RetryConfig,
        metrics: Optional[ConcurrencyMetrics] = None
    ):
        self.config = config
        self.metrics = metrics
    
    def execute(
        self,
        func: Callable[..., T],
        *args,
        **kwargs
    ) -> T:
        """
        Exécute une fonction avec retry.
        
        Args:
            func: Fonction à exécuter
            *args, **kwargs: Arguments de la fonction
            
        Returns:
            Résultat de la fonction
            
        Raises:
            Exception: Si tous les retries échouent
        """
        last_exception = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                result = func(*args, **kwargs)
                
                if self.metrics:
                    self.metrics.retry_attempts.labels(
                        service=func.__name__,
                        status="success"
                    ).inc()
                
                return result
                
            except Exception as e:
                last_exception = e
                
                if self.metrics:
                    self.metrics.retry_attempts.labels(
                        service=func.__name__,
                        status="failure"
                    ).inc()
                
                logger.warning(
                    f"Attempt {attempt} failed for {func.__name__}: {str(e)}"
                )
                
                # Dernier essai ?
                if attempt == self.config.max_attempts:
                    break
                
                # Calculer et attendre le délai
                delay = self.config.get_delay(attempt)
                logger.debug(f"Retrying in {delay:.2f}s...")
                time.sleep(delay)
        
        # Tous les essais ont échoué
        raise RetryError(
            f"Failed after {self.config.max_attempts} attempts"
        ) from last_exception
    
    async def execute_async(
        self,
        func: Callable[..., Awaitable[T]],
        *args,
        **kwargs
    ) -> T:
        """Version async de execute."""
        last_exception = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                result = await func(*args, **kwargs)
                
                if self.metrics:
                    self.metrics.retry_attempts.labels(
                        service=func.__name__,
                        status="success"
                    ).inc()
                
                return result
                
            except Exception as e:
                last_exception = e
                
                if self.metrics:
                    self.metrics.retry_attempts.labels(
                        service=func.__name__,
                        status="failure"
                    ).inc()
                
                logger.warning(
                    f"Async attempt {attempt} failed for {func.__name__}: {str(e)}"
                )
                
                if attempt == self.config.max_attempts:
                    break
                
                delay = self.config.get_delay(attempt)
                logger.debug(f"Retrying async in {delay:.2f}s...")
                await asyncio.sleep(delay)
        
        raise RetryError(
            f"Failed after {self.config.max_attempts} async attempts"
        ) from last_exception


class RetryError(Exception):
    """Exception levée quand tous les retries ont échoué."""
    pass


# ==================== BULKHEAD PATTERN ====================

class Bulkhead:
    """
    Pattern Bulkhead pour isoler les pannes.
    """
    
    def __init__(
        self,
        name: str,
        max_concurrent: int,
        max_queue_size: int = 0,
        metrics: Optional[ConcurrencyMetrics] = None
    ):
        self.name = name
        self.max_concurrent = max_concurrent
        self.max_queue_size = max_queue_size
        
        self.semaphore = Semaphore(max_concurrent)
        self.queue = deque()
        self.queue_lock = Lock()
        self.condition = Condition()
        
        self.active_tasks = 0
        self.queued_tasks = 0
        self.metrics = metrics
    
    def execute(
        self,
        func: Callable[..., T],
        *args,
        **kwargs
    ) -> T:
        """
        Exécute une fonction dans le bulkhead.
        
        Raises:
            BulkheadFullError: Si le bulkhead est plein
        """
        # Vérifier la file d'attente
        with self.queue_lock:
            if self.max_queue_size > 0 and self.queued_tasks >= self.max_queue_size:
                raise BulkheadFullError(
                    f"Bulkhead '{self.name}' queue is full"
                )
            self.queued_tasks += 1
        
        try:
            # Acquérir le semaphore
            acquired = self.semaphore.acquire(timeout=30.0)  # Timeout de 30s
            if not acquired:
                raise TimeoutError("Timeout acquiring bulkhead semaphore")
            
            with self.queue_lock:
                self.queued_tasks -= 1
                self.active_tasks += 1
            
            if self.metrics:
                self.metrics.active_workers.labels(
                    pool_type=f"bulkhead_{self.name}"
                ).set(self.active_tasks)
            
            # Exécuter la fonction
            return func(*args, **kwargs)
            
        finally:
            with self.queue_lock:
                self.active_tasks -= 1
            self.semaphore.release()
            
            if self.metrics:
                self.metrics.active_workers.labels(
                    pool_type=f"bulkhead_{self.name}"
                ).set(self.active_tasks)
    
    async def execute_async(
        self,
        func: Callable[..., Awaitable[T]],
        *args,
        **kwargs
    ) -> T:
        """Version async de execute."""
        # Implémentation similaire avec asyncio.Semaphore
        pass


class BulkheadFullError(Exception):
    """Exception levée quand le bulkhead est plein."""
    pass


# ==================== WORK STEALING QUEUE ====================

class WorkStealingQueue:
    """
    File de travail avec work stealing pour équilibrer la charge.
    """
    
    def __init__(self, capacity: int = 1000):
        self.capacity = capacity
        self.queue = deque(maxlen=capacity)
        self.lock = Lock()
        self.not_empty = Condition(self.lock)
        self.not_full = Condition(self.lock)
    
    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None) -> None:
        """Ajoute un élément à la file."""
        with self.lock:
            if not block and len(self.queue) >= self.capacity:
                raise Full("Queue is full")
            
            while len(self.queue) >= self.capacity:
                if timeout is not None:
                    if not self.not_full.wait(timeout):
                        raise Full("Timeout waiting for queue space")
                else:
                    self.not_full.wait()
            
            self.queue.append(item)
            self.not_empty.notify()
    
    def get(self, block: bool = True, timeout: Optional[float] = None) -> Any:
        """Récupère un élément de la file (FIFO)."""
        with self.lock:
            if not block and not self.queue:
                raise Empty("Queue is empty")
            
            while not self.queue:
                if timeout is not None:
                    if not self.not_empty.wait(timeout):
                        raise Empty("Timeout waiting for item")
                else:
                    self.not_empty.wait()
            
            item = self.queue.popleft()
            self.not_full.notify()
            return item
    
    def steal(self) -> Optional[Any]:
        """
        Vol de travail depuis la fin de la file (LIFO).
        Utilisé par les workers inactifs.
        """
        with self.lock:
            if self.queue:
                item = self.queue.pop()
                self.not_full.notify()
                return item
            return None
    
    def size(self) -> int:
        """Taille actuelle de la file."""
        with self.lock:
            return len(self.queue)
    
    def is_empty(self) -> bool:
        """Vérifie si la file est vide."""
        with self.lock:
            return len(self.queue) == 0


# ==================== THREAD POOL MANAGER ====================

class ThreadPoolManager:
    """
    Gestionnaire de pool de threads avec priorités et monitoring.
    """
    
    def __init__(
        self,
        name: str,
        max_workers: Optional[int] = None,
        initializer: Optional[Callable] = None,
        initargs: Tuple = (),
        metrics: Optional[ConcurrencyMetrics] = None,
        cpu_affinity: Optional[List[int]] = None,
        thread_name_prefix: str = "worker"
    ):
        self.name = name
        self.metrics = metrics
        
        # Déterminer le nombre de workers
        if max_workers is None:
            max_workers = self._get_optimal_worker_count()
        
        # Configuration CPU affinity
        self.cpu_affinity = cpu_affinity
        if cpu_affinity:
            self._set_process_affinity()
        
        # Pools par priorité
        self.pools: Dict[TaskPriority, ThreadPoolExecutor] = {}
        self.pool_sizes: Dict[TaskPriority, int] = {}
        
        # Répartir les workers par priorité
        total_weights = sum(p.value + 1 for p in TaskPriority)  # +1 pour éviter 0
        for priority in TaskPriority:
            weight = (TaskPriority.BACKGROUND.value - priority.value + 1)
            workers = max(1, int(max_workers * weight / total_weights))
            
            self.pools[priority] = ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix=f"{thread_name_prefix}_{priority.name.lower()}",
                initializer=initializer,
                initargs=initargs
            )
            self.pool_sizes[priority] = workers
        
        # File de tâches
        self.task_queue = PriorityQueue()
        self.task_queue_lock = Lock()
        
        # Suivi des tâches
        self.tasks: Dict[str, TaskInfo] = {}
        self.tasks_lock = Lock()
        
        # Deadlock detection
        self.deadlock_check_interval = 30.0  # secondes
        self.last_progress_check = time.monotonic()
        self.task_progress: Dict[str, float] = {}  # 0.0 à 1.0
        
        # Métriques
        if metrics:
            for priority in TaskPriority:
                metrics.queue_size.labels(
                    pool_type=f"thread_{name}",
                    priority=priority.name
                ).set(0)
        
        logger.info(
            f"ThreadPoolManager '{name}' initialisé avec {max_workers} workers totaux"
        )
    
    def _get_optimal_worker_count(self) -> int:
        """Calcule le nombre optimal de workers."""
        cpu_count = os.cpu_count() or 4
        
        # Formule: CPU * 2 pour les tâches I/O bound
        # Moins pour les tâches CPU bound
        optimal = cpu_count * 2
        
        # Prendre en compte la mémoire disponible
        memory_mb = psutil.virtual_memory().available / (1024 * 1024)
        memory_based = int(memory_mb / 100)  # 100MB par thread
        
        return min(optimal, memory_based, 100)  # Max 100 threads
    
    def _set_process_affinity(self) -> None:
        """Définit l'affinité CPU pour le processus."""
        if not self.cpu_affinity:
            return
        
        try:
            import os
            if hasattr(os, 'sched_setaffinity'):
                os.sched_setaffinity(0, self.cpu_affinity)
                logger.info(f"CPU affinity set to {self.cpu_affinity}")
        except Exception as e:
            logger.warning(f"Failed to set CPU affinity: {str(e)}")
    
    def submit(
        self,
        func: Callable[..., R],
        *args,
        priority: TaskPriority = TaskPriority.NORMAL,
        task_id: Optional[str] = None,
        name: Optional[str] = None,
        **kwargs
    ) -> Future:
        """
        Soumet une tâche au pool.
        
        Returns:
            Future pour suivre la tâche
        """
        if task_id is None:
            task_id = str(uuid.uuid4())
        
        if name is None:
            name = func.__name__
        
        # Créer l'info de tâche
        task_info = TaskInfo(
            id=task_id,
            name=name,
            submitted_at=datetime.utcnow(),
            priority=priority
        )
        
        with self.tasks_lock:
            self.tasks[task_id] = task_info
        
        # Métriques
        if self.metrics:
            self.metrics.tasks_submitted.labels(
                pool_type=f"thread_{self.name}",
                priority=priority.name
            ).inc()
            
            self.metrics.queue_size.labels(
                pool_type=f"thread_{self.name}",
                priority=priority.name
            ).inc()
        
        # Soumettre la tâche au pool approprié
        future = self.pools[priority].submit(
            self._wrap_task,
            func, args, kwargs, task_info
        )
        
        # Lier le future à l'info de tâche
        future.add_done_callback(
            lambda f: self._on_task_complete(task_id, f)
        )
        
        return future
    
    def _wrap_task(
        self,
        func: Callable[..., R],
        args: Tuple,
        kwargs: Dict[str, Any],
        task_info: TaskInfo
    ) -> R:
        """Wrapper pour exécuter une tâche avec monitoring."""
        task_info.started_at = datetime.utcnow()
        task_info.status = TaskStatus.RUNNING
        
        # Métriques - décrémenter la file
        if self.metrics:
            self.metrics.queue_size.labels(
                pool_type=f"thread_{self.name}",
                priority=task_info.priority.name
            ).dec()
        
        # Définir l'affinité CPU si spécifiée
        if task_info.cpu_affinity:
            try:
                import os
                pid = os.getpid()
                # Note: Nécessite les permissions appropriées
            except:
                pass
        
        # Exécuter la tâche
        try:
            result = func(*args, **kwargs)
            task_info.result = result
            task_info.status = TaskStatus.COMPLETED
            return result
            
        except Exception as e:
            task_info.error = str(e)
            task_info.status = TaskStatus.FAILED
            raise
            
        finally:
            task_info.completed_at = datetime.utcnow()
    
    def _on_task_complete(self, task_id: str, future: Future) -> None:
        """Callback appelé quand une tâche est terminée."""
        with self.tasks_lock:
            if task_id not in self.tasks:
                return
            
            task_info = self.tasks[task_id]
            
            # Mettre à jour le statut si échec
            if future.exception():
                task_info.status = TaskStatus.FAILED
                task_info.error = str(future.exception())
            
            # Métriques
            if self.metrics:
                self.metrics.tasks_completed.labels(
                    pool_type=f"thread_{self.name}",
                    priority=task_info.priority.name,
                    status=task_info.status.value
                ).inc()
                
                if task_info.duration:
                    self.metrics.task_duration.labels(
                        pool_type=f"thread_{self.name}",
                        priority=task_info.priority.name
                    ).observe(task_info.duration)
                
                if task_info.wait_time:
                    self.metrics.queue_wait_time.labels(
                        pool_type=f"thread_{self.name}",
                        priority=task_info.priority.name
                    ).observe(task_info.wait_time)
    
    def get_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """Récupère les informations d'une tâche."""
        with self.tasks_lock:
            return self.tasks.get(task_id)
    
    def get_active_tasks(self) -> List[TaskInfo]:
        """Récupère les tâches actives."""
        with self.tasks_lock:
            return [
                task for task in self.tasks.values()
                if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]
            ]
    
    def shutdown(self, wait: bool = True) -> None:
        """Arrête le pool de threads."""
        for pool in self.pools.values():
            pool.shutdown(wait=wait)
        
        logger.info(f"ThreadPoolManager '{self.name}' arrêté")
    
    def _check_deadlocks(self) -> None:
        """Vérifie les deadlocks potentiels."""
        current_time = time.monotonic()
        
        if current_time - self.last_progress_check < self.deadlock_check_interval:
            return
        
        self.last_progress_check = current_time
        
        with self.tasks_lock:
            # Vérifier les tâches bloquées
            for task_id, task_info in self.tasks.items():
                if task_info.status == TaskStatus.RUNNING:
                    # Vérifier si la tâche progresse
                    if task_id in self.task_progress:
                        old_progress = self.task_progress[task_id]
                        # Si pas de progrès depuis 2 checks...
                        pass
                    else:
                        self.task_progress[task_id] = 0.0


# ==================== ASYNC TASK MANAGER ====================

class AsyncTaskManager:
    """
    Gestionnaire de tâches asynchrones avec scheduling avancé.
    """
    
    def __init__(
        self,
        name: str,
        max_concurrent: int = 100,
        metrics: Optional[ConcurrencyMetrics] = None
    ):
        self.name = name
        self.max_concurrent = max_concurrent
        self.metrics = metrics
        
        # Sémaphore pour limiter la concurrence
        self.semaphore = AsyncSemaphore(max_concurrent)
        
        # Files par priorité
        self.queues: Dict[TaskPriority, PriorityQueue] = {}
        for priority in TaskPriority:
            self.queues[priority] = PriorityQueue()
        
        # Workers
        self.workers: List[asyncio.Task] = []
        self.is_running = False
        
        # Suivi des tâches
        self.tasks: Dict[str, TaskInfo] = {}
        self.tasks_lock = AsyncLock()
        
        # Work stealing
        self.work_stealing_enabled = True
        self.stealing_interval = 1.0  # secondes
        
        logger.info(
            f"AsyncTaskManager '{name}' initialisé (max_concurrent={max_concurrent})"
        )
    
    async def start(self) -> None:
        """Démarre le gestionnaire de tâches."""
        if self.is_running:
            return
        
        self.is_running = True
        
        # Démarrer les workers
        for i in range(self.max_concurrent):
            worker = asyncio.create_task(
                self._worker_loop(),
                name=f"async_worker_{i}"
            )
            self.workers.append(worker)
        
        # Démarrer le work stealing
        if self.work_stealing_enabled:
            asyncio.create_task(self._work_stealing_loop())
        
        logger.info(f"AsyncTaskManager '{self.name}' démarré")
    
    async def stop(self) -> None:
        """Arrête le gestionnaire de tâches."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # Annuler les workers
        for worker in self.workers:
            worker.cancel()
        
        # Attendre l'arrêt
        await asyncio.gather(*self.workers, return_exceptions=True)
        
        logger.info(f"AsyncTaskManager '{self.name}' arrêté")
    
    async def submit(
        self,
        coro: Callable[..., Awaitable[R]],
        *args,
        priority: TaskPriority = TaskPriority.NORMAL,
        task_id: Optional[str] = None,
        name: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs
    ) -> R:
        """
        Soumet une coroutine pour exécution.
        
        Returns:
            Résultat de la coroutine
        """
        if task_id is None:
            task_id = str(uuid.uuid4())
        
        if name is None:
            name = coro.__name__
        
        # Créer l'info de tâche
        task_info = TaskInfo(
            id=task_id,
            name=name,
            submitted_at=datetime.utcnow(),
            priority=priority
        )
        
        async with self.tasks_lock:
            self.tasks[task_id] = task_info
        
        # Métriques
        if self.metrics:
            self.metrics.tasks_submitted.labels(
                pool_type=f"async_{self.name}",
                priority=priority.name
            ).inc()
            
            self.metrics.queue_size.labels(
                pool_type=f"async_{self.name}",
                priority=priority.name
            ).inc()
        
        # Créer une future pour attendre le résultat
        future = asyncio.Future()
        
        # Ajouter à la file de priorité
        await self.queues[priority].put((
            priority.value,  # Pour le tri
            time.monotonic(),  # Timestamp pour FIFO dans la même priorité
            future,
            coro,
            args,
            kwargs,
            task_id,
            timeout
        ))
        
        # Attendre le résultat
        try:
            result = await future
            return result
            
        except asyncio.CancelledError:
            raise
            
        except Exception as e:
            raise
    
    async def _worker_loop(self) -> None:
        """Boucle d'exécution d'un worker."""
        while self.is_running:
            try:
                # Chercher du travail dans les files par ordre de priorité
                for priority in TaskPriority:
                    queue = self.queues[priority]
                    
                    try:
                        # Essayer de récupérer une tâche sans bloquer
                        (_, timestamp, future, coro, args, kwargs, task_id, timeout) = (
                            queue.get_nowait()
                        )
                        
                        # Exécuter la tâche
                        await self._execute_task(
                            future, coro, args, kwargs, task_id, timeout
                        )
                        
                        break  # Passer à la prochaine itération
                        
                    except asyncio.QueueEmpty:
                        continue  # Essayer la prochaine priorité
                
                else:
                    # Aucune tâche trouvée, attendre un peu
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {str(e)}")
                await asyncio.sleep(1.0)
    
    async def _execute_task(
        self,
        future: asyncio.Future,
        coro: Callable[..., Awaitable[R]],
        args: Tuple,
        kwargs: Dict[str, Any],
        task_id: str,
        timeout: Optional[float]
    ) -> None:
        """Exécute une tâche avec gestion d'erreur et timeout."""
        async with self.tasks_lock:
            if task_id not in self.tasks:
                future.set_exception(ValueError(f"Task {task_id} not found"))
                return
            
            task_info = self.tasks[task_id]
            task_info.started_at = datetime.utcnow()
            task_info.status = TaskStatus.RUNNING
        
        # Métriques - décrémenter la file
        if self.metrics:
            self.metrics.queue_size.labels(
                pool_type=f"async_{self.name}",
                priority=task_info.priority.name
            ).dec()
        
        # Acquérir le sémaphore pour limiter la concurrence
        async with self.semaphore:
            try:
                # Exécuter avec timeout si spécifié
                if timeout:
                    result = await asyncio.wait_for(
                        coro(*args, **kwargs),
                        timeout=timeout
                    )
                else:
                    result = await coro(*args, **kwargs)
                
                # Mettre à jour la tâche
                async with self.tasks_lock:
                    task_info.completed_at = datetime.utcnow()
                    task_info.status = TaskStatus.COMPLETED
                    task_info.result = result
                
                # Compléter la future
                if not future.done():
                    future.set_result(result)
                
                # Métriques
                if self.metrics:
                    self.metrics.tasks_completed.labels(
                        pool_type=f"async_{self.name}",
                        priority=task_info.priority.name,
                        status=task_info.status.value
                    ).inc()
                    
                    if task_info.duration:
                        self.metrics.task_duration.labels(
                            pool_type=f"async_{self.name}",
                            priority=task_info.priority.name
                        ).observe(task_info.duration)
                    
                    if task_info.wait_time:
                        self.metrics.queue_wait_time.labels(
                            pool_type=f"async_{self.name}",
                            priority=task_info.priority.name
                        ).observe(task_info.wait_time)
                
            except asyncio.TimeoutError:
                async with self.tasks_lock:
                    task_info.completed_at = datetime.utcnow()
                    task_info.status = TaskStatus.TIMEOUT
                    task_info.error = "Timeout"
                
                if not future.done():
                    future.set_exception(TimeoutError(f"Task timeout after {timeout}s"))
                
                if self.metrics:
                    self.metrics.tasks_completed.labels(
                        pool_type=f"async_{self.name}",
                        priority=task_info.priority.name,
                        status=TaskStatus.TIMEOUT.value
                    ).inc()
                
            except Exception as e:
                async with self.tasks_lock:
                    task_info.completed_at = datetime.utcnow()
                    task_info.status = TaskStatus.FAILED
                    task_info.error = str(e)
                
                if not future.done():
                    future.set_exception(e)
                
                if self.metrics:
                    self.metrics.tasks_completed.labels(
                        pool_type=f"async_{self.name}",
                        priority=task_info.priority.name,
                        status=TaskStatus.FAILED.value
                    ).inc()
    
    async def _work_stealing_loop(self) -> None:
        """Boucle de work stealing pour équilibrer la charge."""
        while self.is_running:
            try:
                await asyncio.sleep(self.stealing_interval)
                
                # Vérifier les files surchargées
                for priority in TaskPriority:
                    queue = self.queues[priority]
                    
                    # Si une file a beaucoup de tâches, voler certaines
                    if queue.qsize() > self.max_concurrent * 2:
                        stolen_tasks = []
                        
                        # Voler quelques tâches
                        for _ in range(min(10, queue.qsize() // 2)):
                            try:
                                task = queue.get_nowait()
                                stolen_tasks.append(task)
                            except asyncio.QueueEmpty:
                                break
                        
                        # Redistribuer aux autres files moins chargées
                        for other_priority in TaskPriority:
                            if other_priority == priority:
                                continue
                            
                            other_queue = self.queues[other_priority]
                            if other_queue.qsize() < self.max_concurrent:
                                # Redistribuer ici
                                for task in stolen_tasks[:5]:
                                    await other_queue.put(task)
                                    stolen_tasks.remove(task)
                                
                                if not stolen_tasks:
                                    break
                        
                        # Remettre les restes
                        for task in stolen_tasks:
                            await queue.put(task)
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in work stealing loop: {str(e)}")


# ==================== CONCURRENCY MANAGER PRINCIPAL ====================

class ConcurrencyManager:
    """
    Manager principal de concurrence qui orchestre tous les composants.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.metrics = ConcurrencyMetrics()
        
        # Thread pools
        self.thread_pools: Dict[str, ThreadPoolManager] = {}
        
        # Async managers
        self.async_managers: Dict[str, AsyncTaskManager] = {}
        
        # Circuit breakers
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        
        # Rate limiters
        self.rate_limiters: Dict[str, RateLimiter] = {}
        
        # Retry managers
        self.retry_managers: Dict[str, RetryManager] = {}
        
        # Bulkheads
        self.bulkheads: Dict[str, Bulkhead] = {}
        
        # Resource tracking
        self.resource_monitor = ResourceMonitor()
        
        # Deadlock detector
        self.deadlock_detector = DeadlockDetector()
        
        # Initialiser les pools par défaut
        self._init_default_pools()
    
    def _init_default_pools(self) -> None:
        """Initialise les pools par défaut."""
        # Pool CPU pour les tâches intensives
        cpu_cores = os.cpu_count() or 4
        self.create_thread_pool(
            name="cpu_intensive",
            max_workers=cpu_cores,
            thread_name_prefix="cpu_worker"
        )
        
        # Pool I/O pour les opérations réseau/disk
        self.create_thread_pool(
            name="io_bound",
            max_workers=cpu_cores * 4,
            thread_name_prefix="io_worker"
        )
        
        # Pool pour tâches de fond
        self.create_thread_pool(
            name="background",
            max_workers=10,
            thread_name_prefix="bg_worker"
        )
        
        # Async manager par défaut
        self.create_async_manager(
            name="default",
            max_concurrent=200
        )
    
    def create_thread_pool(
        self,
        name: str,
        max_workers: Optional[int] = None,
        **kwargs
    ) -> ThreadPoolManager:
        """Crée un nouveau pool de threads."""
        if name in self.thread_pools:
            raise ValueError(f"Thread pool '{name}' already exists")
        
        pool = ThreadPoolManager(
            name=name,
            max_workers=max_workers,
            metrics=self.metrics,
            **kwargs
        )
        
        self.thread_pools[name] = pool
        return pool
    
    def create_async_manager(
        self,
        name: str,
        max_concurrent: int = 100
    ) -> AsyncTaskManager:
        """Crée un nouveau gestionnaire async."""
        if name in self.async_managers:
            raise ValueError(f"Async manager '{name}' already exists")
        
        manager = AsyncTaskManager(
            name=name,
            max_concurrent=max_concurrent,
            metrics=self.metrics
        )
        
        self.async_managers[name] = manager
        return manager
    
    def create_circuit_breaker(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ) -> CircuitBreaker:
        """Crée un circuit breaker."""
        if name in self.circuit_breakers:
            raise ValueError(f"Circuit breaker '{name}' already exists")
        
        if config is None:
            config = CircuitBreakerConfig()
        
        breaker = CircuitBreaker(
            name=name,
            config=config,
            metrics=self.metrics
        )
        
        self.circuit_breakers[name] = breaker
        return breaker
    
    def create_rate_limiter(
        self,
        name: str,
        config: Optional[RateLimitConfig] = None
    ) -> RateLimiter:
        """Crée un rate limiter."""
        if name in self.rate_limiters:
            raise ValueError(f"Rate limiter '{name}' already exists")
        
        if config is None:
            config = RateLimitConfig()
        
        limiter = RateLimiter(
            config=config,
            metrics=self.metrics
        )
        
        self.rate_limiters[name] = limiter
        return limiter
    
    def create_retry_manager(
        self,
        name: str,
        config: Optional[RetryConfig] = None
    ) -> RetryManager:
        """Crée un gestionnaire de retry."""
        if name in self.retry_managers:
            raise ValueError(f"Retry manager '{name}' already exists")
        
        if config is None:
            config = RetryConfig()
        
        manager = RetryManager(
            config=config,
            metrics=self.metrics
        )
        
        self.retry_managers[name] = manager
        return manager
    
    def create_bulkhead(
        self,
        name: str,
        max_concurrent: int,
        max_queue_size: int = 0
    ) -> Bulkhead:
        """Crée un bulkhead."""
        if name in self.bulkheads:
            raise ValueError(f"Bulkhead '{name}' already exists")
        
        bulkhead = Bulkhead(
            name=name,
            max_concurrent=max_concurrent,
            max_queue_size=max_queue_size,
            metrics=self.metrics
        )
        
        self.bulkheads[name] = bulkhead
        return bulkhead
    
    def get_stats(self) -> Dict[str, Any]:
        """Récupère les statistiques de tous les composants."""
        stats = {
            "thread_pools": {},
            "async_managers": {},
            "circuit_breakers": {},
            "rate_limiters": {},
            "bulkheads": {},
            "system": self.resource_monitor.get_stats()
        }
        
        # Thread pools
        for name, pool in self.thread_pools.items():
            active_tasks = pool.get_active_tasks()
            stats["thread_pools"][name] = {
                "active_tasks": len(active_tasks),
                "total_workers": sum(pool.pool_sizes.values()),
                "tasks_by_priority": {
                    priority.name: len([t for t in active_tasks if t.priority == priority])
                    for priority in TaskPriority
                }
            }
        
        # Circuit breakers
        for name, breaker in self.circuit_breakers.items():
            stats["circuit_breakers"][name] = {
                "state": breaker.state.value,
                "failure_count": breaker.failure_count,
                "success_count": breaker.success_count
            }
        
        return stats
    
    async def shutdown(self) -> None:
        """Arrête tous les composants."""
        logger.info("Shutting down ConcurrencyManager...")
        
        # Arrêter les async managers
        for name, manager in self.async_managers.items():
            await manager.stop()
        
        # Arrêter les thread pools
        for name, pool in self.thread_pools.items():
            pool.shutdown(wait=True)
        
        logger.info("ConcurrencyManager shutdown complete")


# ==================== RESSOURCE MONITOR ====================

class ResourceMonitor:
    """
    Monitor les ressources système pour l'optimisation.
    """
    
    def __init__(self):
        self.process = psutil.Process()
        self.numa_info = self._get_numa_info()
    
    def _get_numa_info(self) -> Dict[str, Any]:
        """Récupère les informations NUMA."""
        info = {"available": False}
        
        try:
            import numa
            info["available"] = True
            info["num_nodes"] = numa.info.get_max_node() + 1
            info["current_node"] = numa.info.get_current_node()
        except ImportError:
            pass
        
        return info
    
    def get_cpu_usage(self) -> Dict[str, float]:
        """Récupère l'utilisation CPU."""
        cpu_percent = psutil.cpu_percent(interval=0.1, percpu=True)
        
        return {
            "per_core": cpu_percent,
            "average": sum(cpu_percent) / len(cpu_percent),
            "count": len(cpu_percent)
        }
    
    def get_memory_usage(self) -> Dict[str, float]:
        """Récupère l'utilisation mémoire."""
        process_memory = self.process.memory_info()
        system_memory = psutil.virtual_memory()
        
        return {
            "process_rss_mb": process_memory.rss / (1024 * 1024),
            "process_vms_mb": process_memory.vms / (1024 * 1024),
            "system_total_mb": system_memory.total / (1024 * 1024),
            "system_available_mb": system_memory.available / (1024 * 1024),
            "system_percent": system_memory.percent
        }
    
    def get_io_stats(self) -> Dict[str, Any]:
        """Récupère les statistiques I/O."""
        io_counters = psutil.disk_io_counters()
        net_io = psutil.net_io_counters()
        
        return {
            "disk_read_mb": io_counters.read_bytes / (1024 * 1024) if io_counters else 0,
            "disk_write_mb": io_counters.write_bytes / (1024 * 1024) if io_counters else 0,
            "network_sent_mb": net_io.bytes_sent / (1024 * 1024),
            "network_recv_mb": net_io.bytes_recv / (1024 * 1024)
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Récupère toutes les statistiques."""
        return {
            "cpu": self.get_cpu_usage(),
            "memory": self.get_memory_usage(),
            "io": self.get_io_stats(),
            "numa": self.numa_info,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def optimize_for_cpu_affinity(self, num_tasks: int) -> List[List[int]]:
        """
        Optimise l'affinité CPU pour un nombre donné de tâches.
        
        Returns:
            Liste de listes de cores pour chaque tâche
        """
        cpu_count = os.cpu_count() or 4
        
        if not self.numa_info["available"] or cpu_count <= 4:
            # Pas d'optimisation NUMA pour les petits systèmes
            return [[i % cpu_count] for i in range(num_tasks)]
        
        # Stratégie NUMA-aware
        numa_nodes = self.numa_info["num_nodes"]
        cores_per_node = cpu_count // numa_nodes
        
        affinity_list = []
        for i in range(num_tasks):
            node = i % numa_nodes
            core_in_node = (i // numa_nodes) % cores_per_node
            core_id = node * cores_per_node + core_in_node
            affinity_list.append([core_id])
        
        return affinity_list


# ==================== DEADLOCK DETECTOR ====================

class DeadlockDetector:
    """
    Détecteur de deadlocks.
    """
    
    def __init__(self, check_interval: float = 30.0):
        self.check_interval = check_interval
        self.last_check = time.monotonic()
        self.lock_graph = defaultdict(set)  # Graph des dépendances de locks
    
    def register_lock_acquisition(
        self,
        thread_id: int,
        lock_id: str
    ) -> None:
        """Enregistre l'acquisition d'un lock."""
        # À implémenter: construction du graph
        pass
    
    def register_lock_wait(
        self,
        thread_id: int,
        lock_id: str
    ) -> None:
        """Enregistre l'attente sur un lock."""
        # À implémenter: construction du graph
        pass
    
    def check_for_deadlocks(self) -> List[List[str]]:
        """
        Vérifie les deadlocks.
        
        Returns:
            Liste de cycles de deadlock
        """
        current_time = time.monotonic()
        
        if current_time - self.last_check < self.check_interval:
            return []
        
        self.last_check = current_time
        
        # À implémenter: détection de cycles dans le graph
        return []


# ==================== TASK DEPENDENCY RESOLVER ====================

class TaskDependency:
    """Dépendance entre tâches."""
    
    def __init__(self, task_id: str, depends_on: List[str]):
        self.task_id = task_id
        self.depends_on = depends_on
        self.dependents: List[str] = []


class DependencyResolver:
    """
    Résout les dépendances entre tâches.
    """
    
    def __init__(self):
        self.tasks: Dict[str, TaskDependency] = {}
        self.completed_tasks: Set[str] = set()
        self.lock = Lock()
    
    def add_task(self, task_id: str, depends_on: List[str]) -> None:
        """Ajoute une tâche avec ses dépendances."""
        with self.lock:
            if task_id in self.tasks:
                raise ValueError(f"Task {task_id} already exists")
            
            # Créer la tâche
            task = TaskDependency(task_id, depends_on)
            self.tasks[task_id] = task
            
            # Mettre à jour les dépendances
            for dep_id in depends_on:
                if dep_id in self.tasks:
                    self.tasks[dep_id].dependents.append(task_id)
    
    def mark_completed(self, task_id: str) -> List[str]:
        """
        Marque une tâche comme complétée.
        
        Returns:
            Liste des tâches devenues disponibles
        """
        with self.lock:
            if task_id not in self.tasks:
                return []
            
            self.completed_tasks.add(task_id)
            
            # Vérifier quelles tâches sont maintenant disponibles
            available = []
            for dependent_id in self.tasks[task_id].dependents:
                dependent = self.tasks[dependent_id]
                
                # Vérifier si toutes les dépendances sont satisfaites
                if all(dep in self.completed_tasks for dep in dependent.depends_on):
                    available.append(dependent_id)
            
            return available
    
    def get_ready_tasks(self) -> List[str]:
        """Récupère les tâches prêtes à être exécutées."""
        with self.lock:
            ready = []
            
            for task_id, task in self.tasks.items():
                if task_id in self.completed_tasks:
                    continue
                
                if all(dep in self.completed_tasks for dep in task.depends_on):
                    ready.append(task_id)
            
            return ready


# ==================== LOAD BALANCER ====================

class LoadBalancer:
    """
    Répartit la charge entre plusieurs workers.
    """
    
    def __init__(self, strategy: str = "round_robin"):
        self.strategy = strategy
        self.workers: List[Any] = []
        self.worker_stats: Dict[str, Dict[str, int]] = {}
        self.index = 0
        
        if strategy == "least_connections":
            self.select_worker = self._select_least_connections
        elif strategy == "weighted":
            self.select_worker = self._select_weighted
        else:  # round_robin
            self.select_worker = self._select_round_robin
    
    def add_worker(self, worker: Any, weight: int = 1) -> None:
        """Ajoute un worker."""
        worker_id = id(worker)
        self.workers.append(worker)
        self.worker_stats[worker_id] = {
            "active_tasks": 0,
            "total_tasks": 0,
            "weight": weight
        }
    
    def remove_worker(self, worker: Any) -> None:
        """Retire un worker."""
        worker_id = id(worker)
        if worker in self.workers:
            self.workers.remove(worker)
        if worker_id in self.worker_stats:
            del self.worker_stats[worker_id]
    
    def _select_round_robin(self) -> Any:
        """Sélection round-robin."""
        if not self.workers:
            raise ValueError("No workers available")
        
        worker = self.workers[self.index]
        self.index = (self.index + 1) % len(self.workers)
        return worker
    
    def _select_least_connections(self) -> Any:
        """Sélection par moindre connexions."""
        if not self.workers:
            raise ValueError("No workers available")
        
        # Trouver le worker avec le moins de tâches actives
        min_tasks = float('inf')
        selected = None
        
        for worker in self.workers:
            stats = self.worker_stats[id(worker)]
            if stats["active_tasks"] < min_tasks:
                min_tasks = stats["active_tasks"]
                selected = worker
        
        return selected
    
    def _select_weighted(self) -> Any:
        """Sélection pondérée."""
        if not self.workers:
            raise ValueError("No workers available")
        
        # Calculer le poids total
        total_weight = sum(
            self.worker_stats[id(w)]["weight"]
            for w in self.workers
        )
        
        # Sélection aléatoire pondérée
        import random
        r = random.uniform(0, total_weight)
        cumulative = 0
        
        for worker in self.workers:
            weight = self.worker_stats[id(worker)]["weight"]
            cumulative += weight
            if r <= cumulative:
                return worker
        
        return self.workers[-1]  # Fallback
    
    def task_started(self, worker: Any) -> None:
        """Enregistre le début d'une tâche."""
        worker_id = id(worker)
        if worker_id in self.worker_stats:
            self.worker_stats[worker_id]["active_tasks"] += 1
            self.worker_stats[worker_id]["total_tasks"] += 1
    
    def task_completed(self, worker: Any) -> None:
        """Enregistre la fin d'une tâche."""
        worker_id = id(worker)
        if worker_id in self.worker_stats:
            self.worker_stats[worker_id]["active_tasks"] -= 1


# ==================== EXEMPLE D'UTILISATION ====================

if __name__ == "__main__":
    # Créer le manager
    concurrency_manager = ConcurrencyManager()
    
    # Utiliser un thread pool
    cpu_pool = concurrency_manager.thread_pools["cpu_intensive"]
    
    # Soumettre une tâche
    def compute_pi(n: int) -> float:
        """Calcule Pi avec la formule de Leibniz."""
        pi = 0.0
        for i in range(n):
            pi += ((-1) ** i) / (2 * i + 1)
        return pi * 4
    
    future = cpu_pool.submit(
        compute_pi,
        1000000,
        priority=TaskPriority.NORMAL,
        name="compute_pi"
    )
    
    # Attendre le résultat
    result = future.result()
    print(f"Pi approximé: {result}")
    
    # Utiliser un circuit breaker
    breaker = concurrency_manager.create_circuit_breaker("external_api")
    
    @breaker
    def call_external_api():
        import requests
        response = requests.get("https://api.example.com", timeout=5)
        response.raise_for_status()
        return response.json()
    
    try:
        data = call_external_api()
        print(f"API data: {data}")
    except CircuitBreakerError as e:
        print(f"Circuit breaker ouvert: {e}")
    
    # Statistiques
    stats = concurrency_manager.get_stats()
    print(f"Statistiques système: {stats['system']}")
    
    # Arrêter proprement
    import asyncio
    asyncio.run(concurrency_manager.shutdown())