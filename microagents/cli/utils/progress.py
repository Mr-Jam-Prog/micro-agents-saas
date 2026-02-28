"""
Advanced progress tracking utilities for MicroAgents Platform CLI.
Multi-step progress, nested bars, ETA, error recovery, and persistence.
"""

import asyncio
import json
import os
import pickle
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable, Coroutine, Generator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
)

import psutil
from pydantic import BaseModel, Field, validator
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.live import Live
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .formatting import Formatter, get_formatter

# Type variables
T = TypeVar("T")
TaskResult = TypeVar("TaskResult")

# Global console
_console = Console()


class TaskStatus(str, Enum):
    """Status of a progress task."""
    
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class TaskPriority(int, Enum):
    """Priority of a progress task."""
    
    LOWEST = 0
    LOW = 1
    NORMAL = 2
    HIGH = 3
    HIGHEST = 4
    CRITICAL = 5


class ResourceType(str, Enum):
    """Types of resources to track."""
    
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    NETWORK = "network"
    COST = "cost"
    AGENTS = "agents"
    API_CALLS = "api_calls"


@dataclass
class ResourceUsage:
    """Resource usage metrics."""
    
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    disk_read_mb: float = 0.0
    disk_write_mb: float = 0.0
    network_sent_mb: float = 0.0
    network_received_mb: float = 0.0
    cost_usd: float = 0.0
    agents_active: int = 0
    api_calls: int = 0
    
    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return {
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "disk_read_mb": self.disk_read_mb,
            "disk_write_mb": self.disk_write_mb,
            "network_sent_mb": self.network_sent_mb,
            "network_received_mb": self.network_received_mb,
            "cost_usd": self.cost_usd,
            "agents_active": self.agents_active,
            "api_calls": self.api_calls,
        }


class TaskDependency(BaseModel):
    """Dependency between tasks."""
    
    task_id: str
    depends_on: List[str] = Field(default_factory=list)
    required_for: List[str] = Field(default_factory=list)
    
    @validator("depends_on", "required_for")
    def validate_no_self_reference(cls, v: List[str], values: Dict[str, Any]) -> List[str]:
        """Validate no self-references in dependencies."""
        task_id = values.get("task_id")
        if task_id and task_id in v:
            raise ValueError(f"Task cannot depend on itself: {task_id}")
        return v


class TaskMetrics(BaseModel):
    """Performance metrics for a task."""
    
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    iterations_per_second: float = 0.0
    error_count: int = 0
    retry_count: int = 0
    resource_usage: ResourceUsage = Field(default_factory=ResourceUsage)
    custom_metrics: Dict[str, Any] = Field(default_factory=dict)
    
    @property
    def is_running(self) -> bool:
        """Check if task is currently running."""
        return self.start_time is not None and self.end_time is None
    
    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds."""
        if self.start_time is None:
            return 0.0
        elif self.end_time is not None:
            return self.duration_seconds
        else:
            return (datetime.now() - self.start_time).total_seconds()
    
    def start(self) -> None:
        """Start timing the task."""
        self.start_time = datetime.now()
        self.end_time = None
    
    def stop(self) -> None:
        """Stop timing the task."""
        if self.start_time:
            self.end_time = datetime.now()
            self.duration_seconds = (self.end_time - self.start_time).total_seconds()


class ProgressTask(BaseModel):
    """A single progress tracking task."""
    
    # Basic properties
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    
    # Progress tracking
    total: float = 100.0
    completed: float = 0.0
    progress_percent: float = 0.0
    
    # Timing
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    estimated_duration: Optional[float] = None  # seconds
    actual_duration: Optional[float] = None  # seconds
    
    # Dependencies
    dependencies: TaskDependency = Field(default_factory=lambda: TaskDependency(task_id=""))
    parent_id: Optional[str] = None
    children_ids: List[str] = Field(default_factory=list)
    
    # Error handling
    error_message: Optional[str] = None
    error_stack_trace: Optional[str] = None
    max_retries: int = 3
    current_retry: int = 0
    auto_recover: bool = True
    
    # Metrics
    metrics: TaskMetrics = Field(default_factory=TaskMetrics)
    
    # Custom data
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Persistence
    checkpoint_data: Optional[Dict[str, Any]] = None
    last_checkpoint: Optional[datetime] = None
    
    class Config:
        arbitrary_types_allowed = True
    
    @property
    def is_completed(self) -> bool:
        """Check if task is completed."""
        return self.status == TaskStatus.COMPLETED
    
    @property
    def is_failed(self) -> bool:
        """Check if task failed."""
        return self.status == TaskStatus.FAILED
    
    @property
    def is_running(self) -> bool:
        """Check if task is running."""
        return self.status == TaskStatus.RUNNING
    
    @property
    def can_start(self) -> bool:
        """Check if task can start (dependencies satisfied)."""
        return self.status in [TaskStatus.PENDING, TaskStatus.PAUSED]
    
    @property
    def can_resume(self) -> bool:
        """Check if task can be resumed."""
        return self.status in [TaskStatus.PAUSED, TaskStatus.FAILED] and self.current_retry < self.max_retries
    
    @property
    def eta(self) -> Optional[datetime]:
        """Calculate estimated time of arrival/completion."""
        if not self.is_running or not self.started_at:
            return None
        
        if self.progress_percent > 0:
            elapsed = (datetime.now() - self.started_at).total_seconds()
            total_estimated = elapsed / (self.progress_percent / 100.0)
            remaining = total_estimated - elapsed
            
            if remaining > 0:
                return datetime.now() + timedelta(seconds=remaining)
        
        return None
    
    @property
    def speed(self) -> Optional[float]:
        """Calculate progress speed (units per second)."""
        if not self.is_running or not self.started_at:
            return None
        
        elapsed = (datetime.now() - self.started_at).total_seconds()
        if elapsed > 0:
            return self.completed / elapsed
        
        return None
    
    def update_progress(self, completed: float, total: Optional[float] = None) -> None:
        """Update task progress."""
        if total is not None:
            self.total = total
        
        self.completed = min(completed, self.total)
        self.progress_percent = (self.completed / self.total) * 100.0 if self.total > 0 else 0.0
    
    def start(self) -> None:
        """Start the task."""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
        self.metrics.start()
    
    def complete(self) -> None:
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        self.completed = self.total
        self.progress_percent = 100.0
        self.metrics.stop()
        
        if self.started_at:
            self.actual_duration = (self.completed_at - self.started_at).total_seconds()
    
    def fail(self, error: Exception) -> None:
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.error_message = str(error)
        self.metrics.error_count += 1
        self.metrics.stop()
    
    def pause(self) -> None:
        """Pause the task."""
        if self.is_running:
            self.status = TaskStatus.PAUSED
            self.metrics.stop()
    
    def resume(self) -> None:
        """Resume the task."""
        if self.status == TaskStatus.PAUSED:
            self.status = TaskStatus.RUNNING
            # Don't restart metrics, just continue timing
            if not self.metrics.is_running:
                self.metrics.start()
    
    def retry(self) -> bool:
        """Retry the task if possible."""
        if self.current_retry < self.max_retries:
            self.current_retry += 1
            self.status = TaskStatus.PENDING
            self.error_message = None
            self.error_stack_trace = None
            self.metrics.retry_count += 1
            return True
        return False
    
    def create_checkpoint(self, data: Dict[str, Any]) -> None:
        """Create a checkpoint for resume capability."""
        self.checkpoint_data = data
        self.last_checkpoint = datetime.now()
    
    def get_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Get checkpoint data."""
        return self.checkpoint_data


class ProgressReporter(ABC):
    """Abstract base class for progress reporters."""
    
    @abstractmethod
    def report_start(self, task: ProgressTask) -> None:
        """Report task start."""
        pass
    
    @abstractmethod
    def report_progress(self, task: ProgressTask) -> None:
        """Report task progress."""
        pass
    
    @abstractmethod
    def report_complete(self, task: ProgressTask) -> None:
        """Report task completion."""
        pass
    
    @abstractmethod
    def report_error(self, task: ProgressTask, error: Exception) -> None:
        """Report task error."""
        pass


class ConsoleReporter(ProgressReporter):
    """Reporter that outputs to console using Rich."""
    
    def __init__(self, formatter: Optional[Formatter] = None):
        self.formatter = formatter or get_formatter()
        self.task_bars: Dict[str, TaskID] = {}
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.formatter.console,
        )
        self.live: Optional[Live] = None
    
    def report_start(self, task: ProgressTask) -> None:
        """Report task start to console."""
        if not self.live:
            self.live = Live(self.progress, console=self.formatter.console, refresh_per_second=10)
            self.live.start()
        
        description = f"{task.name}"
        if task.description:
            description += f": {task.description}"
        
        task_id = self.progress.add_task(
            description,
            total=task.total,
            completed=task.completed,
        )
        self.task_bars[task.id] = task_id
    
    def report_progress(self, task: ProgressTask) -> None:
        """Update progress bar."""
        if task.id in self.task_bars:
            task_id = self.task_bars[task.id]
            
            # Update description with ETA if available
            description = f"{task.name}"
            if task.description:
                description += f": {task.description}"
            
            if eta := task.eta:
                description += f" | ETA: {eta.strftime('%H:%M:%S')}"
            
            if speed := task.speed:
                description += f" | Speed: {speed:.2f}/s"
            
            self.progress.update(
                task_id,
                description=description,
                completed=task.completed,
                total=task.total,
            )
    
    def report_complete(self, task: ProgressTask) -> None:
        """Mark progress bar as complete."""
        if task.id in self.task_bars:
            task_id = self.task_bars[task.id]
            self.progress.update(
                task_id,
                description=f"[green]✓ {task.name}[/green]",
                completed=task.total,
            )
            self.progress.stop_task(task_id)
    
    def report_error(self, task: ProgressTask, error: Exception) -> None:
        """Mark progress bar as errored."""
        if task.id in self.task_bars:
            task_id = self.task_bars[task.id]
            self.progress.update(
                task_id,
                description=f"[red]✗ {task.name}: {error}[/red]",
                completed=task.completed,
            )


class WebhookReporter(ProgressReporter):
    """Reporter that sends progress updates via webhook."""
    
    def __init__(self, webhook_url: str, headers: Optional[Dict[str, str]] = None):
        self.webhook_url = webhook_url
        self.headers = headers or {}
        
    def report_start(self, task: ProgressTask) -> None:
        """Send webhook for task start."""
        self._send_webhook("start", task)
    
    def report_progress(self, task: ProgressTask) -> None:
        """Send webhook for task progress."""
        self._send_webhook("progress", task)
    
    def report_complete(self, task: ProgressTask) -> None:
        """Send webhook for task completion."""
        self._send_webhook("complete", task)
    
    def report_error(self, task: ProgressTask, error: Exception) -> None:
        """Send webhook for task error."""
        self._send_webhook("error", task, error=error)
    
    def _send_webhook(self, event_type: str, task: ProgressTask, **kwargs) -> None:
        """Send webhook request."""
        import httpx
        
        payload = {
            "event": event_type,
            "timestamp": datetime.now().isoformat(),
            "task": task.dict(),
            **kwargs,
        }
        
        try:
            httpx.post(self.webhook_url, json=payload, headers=self.headers, timeout=5)
        except Exception:
            # Silently fail webhook errors
            pass


class LogReporter(ProgressReporter):
    """Reporter that logs progress updates."""
    
    def __init__(self, logger_name: str = "progress"):
        import logging
        self.logger = logging.getLogger(logger_name)
    
    def report_start(self, task: ProgressTask) -> None:
        """Log task start."""
        self.logger.info(f"Task started: {task.name} (ID: {task.id})")
    
    def report_progress(self, task: ProgressTask) -> None:
        """Log task progress."""
        if task.progress_percent % 10 == 0:  # Log every 10%
            self.logger.info(
                f"Task progress: {task.name} - {task.progress_percent:.1f}% "
                f"(ETA: {task.eta.strftime('%H:%M:%S') if task.eta else 'N/A'})"
            )
    
    def report_complete(self, task: ProgressTask) -> None:
        """Log task completion."""
        duration = task.actual_duration or 0
        self.logger.info(
            f"Task completed: {task.name} in {duration:.2f}s "
            f"(Speed: {task.speed:.2f}/s if task.speed else 'N/A')"
        )
    
    def report_error(self, task: ProgressTask, error: Exception) -> None:
        """Log task error."""
        self.logger.error(f"Task failed: {task.name} - {error}", exc_info=True)


class ProgressGroup:
    """Group of related progress tasks."""
    
    def __init__(
        self,
        group_id: str,
        name: str,
        description: Optional[str] = None,
    ):
        self.group_id = group_id
        self.name = name
        self.description = description
        self.tasks: Dict[str, ProgressTask] = {}
        self.dependencies: Dict[str, TaskDependency] = {}
        self.resource_tracker = ResourceTracker()
        
        # Task execution order
        self.execution_order: List[str] = []
        
        # Reporters
        self.reporters: List[ProgressReporter] = []
        
        # Performance metrics
        self.metrics = GroupMetrics()
        
        # Lock for thread safety
        self._lock = threading.RLock()
    
    def add_task(
        self,
        name: str,
        total: float = 100.0,
        description: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
        **kwargs,
    ) -> ProgressTask:
        """Add a new task to the group."""
        with self._lock:
            task = ProgressTask(
                name=name,
                description=description,
                total=total,
                **kwargs,
            )
            
            # Set up dependencies
            task.dependencies = TaskDependency(
                task_id=task.id,
                depends_on=dependencies or [],
            )
            
            # Set parent if provided
            if parent_id:
                task.parent_id = parent_id
                if parent_id in self.tasks:
                    self.tasks[parent_id].children_ids.append(task.id)
            
            # Store task
            self.tasks[task.id] = task
            self.dependencies[task.id] = task.dependencies
            
            return task
    
    def remove_task(self, task_id: str) -> bool:
        """Remove a task from the group."""
        with self._lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                
                # Remove from parent's children
                if task.parent_id and task.parent_id in self.tasks:
                    self.tasks[task.parent_id].children_ids.remove(task_id)
                
                # Remove from dependencies
                if task_id in self.dependencies:
                    del self.dependencies[task_id]
                
                # Remove task
                del self.tasks[task_id]
                
                return True
            return False
    
    def get_task(self, task_id: str) -> Optional[ProgressTask]:
        """Get a task by ID."""
        with self._lock:
            return self.tasks.get(task_id)
    
    def get_tasks_by_status(self, status: TaskStatus) -> List[ProgressTask]:
        """Get all tasks with given status."""
        with self._lock:
            return [task for task in self.tasks.values() if task.status == status]
    
    def get_tasks_by_tag(self, tag: str) -> List[ProgressTask]:
        """Get all tasks with given tag."""
        with self._lock:
            return [task for task in self.tasks.values() if tag in task.tags]
    
    def add_reporter(self, reporter: ProgressReporter) -> None:
        """Add a progress reporter."""
        with self._lock:
            self.reporters.append(reporter)
    
    def remove_reporter(self, reporter: ProgressReporter) -> None:
        """Remove a progress reporter."""
        with self._lock:
            if reporter in self.reporters:
                self.reporters.remove(reporter)
    
    def start_task(self, task_id: str) -> bool:
        """Start a task."""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            
            # Check dependencies
            if not self._check_dependencies(task_id):
                return False
            
            # Start task
            task.start()
            
            # Update metrics
            self.metrics.tasks_started += 1
            
            # Notify reporters
            for reporter in self.reporters:
                try:
                    reporter.report_start(task)
                except Exception:
                    pass
            
            return True
    
    def update_task_progress(
        self,
        task_id: str,
        completed: float,
        total: Optional[float] = None,
        resource_usage: Optional[ResourceUsage] = None,
    ) -> bool:
        """Update task progress."""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            
            # Update progress
            task.update_progress(completed, total)
            
            # Update resource usage
            if resource_usage:
                task.metrics.resource_usage = resource_usage
            
            # Update group metrics
            self.metrics.update_from_task(task)
            self.resource_tracker.update(resource_usage or ResourceUsage())
            
            # Notify reporters
            for reporter in self.reporters:
                try:
                    reporter.report_progress(task)
                except Exception:
                    pass
            
            return True
    
    def complete_task(self, task_id: str) -> bool:
        """Mark a task as completed."""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            
            # Mark as completed
            task.complete()
            
            # Update metrics
            self.metrics.tasks_completed += 1
            
            # Update resource usage
            current_usage = self.resource_tracker.get_current_usage()
            task.metrics.resource_usage = current_usage
            
            # Notify reporters
            for reporter in self.reporters:
                try:
                    reporter.report_complete(task)
                except Exception:
                    pass
            
            # Start dependent tasks
            self._start_dependent_tasks(task_id)
            
            return True
    
    def fail_task(self, task_id: str, error: Exception) -> bool:
        """Mark a task as failed."""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            
            # Mark as failed
            task.fail(error)
            
            # Update metrics
            self.metrics.tasks_failed += 1
            
            # Notify reporters
            for reporter in self.reporters:
                try:
                    reporter.report_error(task, error)
                except Exception:
                    pass
            
            # Handle error recovery
            if task.auto_recover and task.retry():
                # Schedule retry
                self._schedule_retry(task_id)
            
            return True
    
    def pause_task(self, task_id: str) -> bool:
        """Pause a task."""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            task.pause()
            return True
    
    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task."""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            
            if task.can_resume:
                task.resume()
                return True
            
            return False
    
    def create_checkpoint(self, task_id: str, data: Dict[str, Any]) -> bool:
        """Create a checkpoint for a task."""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            task.create_checkpoint(data)
            return True
    
    def get_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get checkpoint data for a task."""
        with self._lock:
            if task_id not in self.tasks:
                return None
            
            task = self.tasks[task_id]
            return task.get_checkpoint()
    
    def calculate_execution_order(self) -> List[str]:
        """Calculate optimal execution order based on dependencies."""
        with self._lock:
            # Use topological sort for dependencies
            visited = set()
            temp_visited = set()
            order = []
            
            def visit(task_id: str) -> None:
                if task_id in temp_visited:
                    raise ValueError(f"Circular dependency detected involving task {task_id}")
                
                if task_id not in visited:
                    temp_visited.add(task_id)
                    
                    # Visit dependencies first
                    if task_id in self.dependencies:
                        for dep_id in self.dependencies[task_id].depends_on:
                            if dep_id in self.tasks:
                                visit(dep_id)
                    
                    temp_visited.remove(task_id)
                    visited.add(task_id)
                    order.append(task_id)
            
            # Visit all tasks
            for task_id in self.tasks:
                if task_id not in visited:
                    visit(task_id)
            
            self.execution_order = order
            return order
    
    def get_task_dependency_graph(self) -> Dict[str, List[str]]:
        """Get dependency graph for visualization."""
        with self._lock:
            graph = {}
            for task_id, dep in self.dependencies.items():
                graph[task_id] = dep.depends_on.copy()
            return graph
    
    def get_group_progress(self) -> Dict[str, Any]:
        """Get overall group progress."""
        with self._lock:
            total_tasks = len(self.tasks)
            completed_tasks = len(self.get_tasks_by_status(TaskStatus.COMPLETED))
            running_tasks = len(self.get_tasks_by_status(TaskStatus.RUNNING))
            failed_tasks = len(self.get_tasks_by_status(TaskStatus.FAILED))
            
            # Calculate overall progress
            total_progress = 0.0
            total_weight = 0.0
            
            for task in self.tasks.values():
                weight = task.priority.value + 1  # Higher priority = more weight
                total_progress += task.progress_percent * weight
                total_weight += weight
            
            overall_progress = (total_progress / total_weight) if total_weight > 0 else 0.0
            
            return {
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "running_tasks": running_tasks,
                "failed_tasks": failed_tasks,
                "overall_progress": overall_progress,
                "resource_usage": self.resource_tracker.get_current_usage().to_dict(),
                "metrics": self.metrics.dict(),
            }
    
    def _check_dependencies(self, task_id: str) -> bool:
        """Check if all dependencies are satisfied."""
        if task_id not in self.dependencies:
            return True
        
        dep = self.dependencies[task_id]
        
        for dep_id in dep.depends_on:
            if dep_id not in self.tasks:
                return False
            
            dep_task = self.tasks[dep_id]
            if not dep_task.is_completed:
                return False
        
        return True
    
    def _start_dependent_tasks(self, task_id: str) -> None:
        """Start tasks that depend on the completed task."""
        for other_id, other_dep in self.dependencies.items():
            if task_id in other_dep.depends_on:
                # Check if all dependencies are now satisfied
                if self._check_dependencies(other_id):
                    self.start_task(other_id)
    
    def _schedule_retry(self, task_id: str) -> None:
        """Schedule a task retry."""
        # In a real implementation, this would use a scheduler
        # For now, just restart immediately
        import threading
        
        def retry_task() -> None:
            time.sleep(1)  # Small delay before retry
            if task_id in self.tasks:
                task = self.tasks[task_id]
                if task.status == TaskStatus.FAILED and task.current_retry < task.max_retries:
                    task.status = TaskStatus.PENDING
                    self.start_task(task_id)
        
        thread = threading.Thread(target=retry_task, daemon=True)
        thread.start()


class GroupMetrics(BaseModel):
    """Metrics for a progress group."""
    
    start_time: datetime = Field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    
    tasks_total: int = 0
    tasks_started: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_retried: int = 0
    
    total_duration_seconds: float = 0.0
    average_task_duration_seconds: float = 0.0
    
    total_cost_usd: float = 0.0
    total_api_calls: int = 0
    peak_memory_mb: float = 0.0
    peak_cpu_percent: float = 0.0
    
    custom_metrics: Dict[str, Any] = Field(default_factory=dict)
    
    def update_from_task(self, task: ProgressTask) -> None:
        """Update metrics from a task."""
        self.tasks_total = max(self.tasks_total, self.tasks_started)
        
        # Update resource peaks
        usage = task.metrics.resource_usage
        self.peak_memory_mb = max(self.peak_memory_mb, usage.memory_mb)
        self.peak_cpu_percent = max(self.peak_cpu_percent, usage.cpu_percent)
        
        # Update totals
        self.total_cost_usd += usage.cost_usd
        self.total_api_calls += usage.api_calls
        
        # Update task metrics
        self.tasks_retried += task.metrics.retry_count
        
        # Update custom metrics
        for key, value in task.metrics.custom_metrics.items():
            if key not in self.custom_metrics:
                self.custom_metrics[key] = value
            elif isinstance(value, (int, float)):
                self.custom_metrics[key] = self.custom_metrics.get(key, 0) + value
    
    def complete(self) -> None:
        """Mark group metrics as complete."""
        self.end_time = datetime.now()
        
        if self.start_time and self.end_time:
            self.total_duration_seconds = (self.end_time - self.start_time).total_seconds()
        
        if self.tasks_completed > 0:
            self.average_task_duration_seconds = self.total_duration_seconds / self.tasks_completed


class ResourceTracker:
    """Tracks resource usage across tasks."""
    
    def __init__(self):
        self.current_usage = ResourceUsage()
        self.history: List[Tuple[datetime, ResourceUsage]] = []
        self.peak_usage = ResourceUsage()
        self._lock = threading.RLock()
        
        # Start background monitoring
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        
    def start_monitoring(self, interval_seconds: float = 1.0) -> None:
        """Start background resource monitoring."""
        with self._lock:
            if not self._monitoring:
                self._monitoring = True
                self._monitor_thread = threading.Thread(
                    target=self._monitor_resources,
                    args=(interval_seconds,),
                    daemon=True,
                )
                self._monitor_thread.start()
    
    def stop_monitoring(self) -> None:
        """Stop background resource monitoring."""
        with self._lock:
            self._monitoring = False
            if self._monitor_thread:
                self._monitor_thread.join(timeout=1.0)
                self._monitor_thread = None
    
    def update(self, usage: ResourceUsage) -> None:
        """Update current resource usage."""
        with self._lock:
            self.current_usage = usage
            self.history.append((datetime.now(), usage))
            
            # Update peaks
            self.peak_usage.cpu_percent = max(self.peak_usage.cpu_percent, usage.cpu_percent)
            self.peak_usage.memory_mb = max(self.peak_usage.memory_mb, usage.memory_mb)
            self.peak_usage.disk_read_mb = max(self.peak_usage.disk_read_mb, usage.disk_read_mb)
            self.peak_usage.disk_write_mb = max(self.peak_usage.disk_write_mb, usage.disk_write_mb)
            self.peak_usage.network_sent_mb = max(self.peak_usage.network_sent_mb, usage.network_sent_mb)
            self.peak_usage.network_received_mb = max(self.peak_usage.network_received_mb, usage.network_received_mb)
            self.peak_usage.cost_usd = max(self.peak_usage.cost_usd, usage.cost_usd)
            self.peak_usage.agents_active = max(self.peak_usage.agents_active, usage.agents_active)
            self.peak_usage.api_calls = max(self.peak_usage.api_calls, usage.api_calls)
    
    def get_current_usage(self) -> ResourceUsage:
        """Get current resource usage."""
        with self._lock:
            return ResourceUsage(**self.current_usage.to_dict())
    
    def get_peak_usage(self) -> ResourceUsage:
        """Get peak resource usage."""
        with self._lock:
            return ResourceUsage(**self.peak_usage.to_dict())
    
    def get_usage_history(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Tuple[datetime, ResourceUsage]]:
        """Get resource usage history within time range."""
        with self._lock:
            filtered = []
            for timestamp, usage in self.history:
                if start_time and timestamp < start_time:
                    continue
                if end_time and timestamp > end_time:
                    continue
                filtered.append((timestamp, usage))
            return filtered
    
    def _monitor_resources(self, interval_seconds: float) -> None:
        """Background thread to monitor system resources."""
        process = psutil.Process()
        
        while self._monitoring:
            try:
                # Get system resource usage
                cpu_percent = psutil.cpu_percent(interval=None)
                memory_info = process.memory_info()
                memory_mb = memory_info.rss / (1024 * 1024)
                
                # Get disk I/O
                disk_io = psutil.disk_io_counters()
                disk_read_mb = disk_io.read_bytes / (1024 * 1024) if disk_io else 0
                disk_write_mb = disk_io.write_bytes / (1024 * 1024) if disk_io else 0
                
                # Get network I/O
                net_io = psutil.net_io_counters()
                network_sent_mb = net_io.bytes_sent / (1024 * 1024) if net_io else 0
                network_received_mb = net_io.bytes_recv / (1024 * 1024) if net_io else 0
                
                # Create usage object
                usage = ResourceUsage(
                    cpu_percent=cpu_percent,
                    memory_mb=memory_mb,
                    disk_read_mb=disk_read_mb,
                    disk_write_mb=disk_write_mb,
                    network_sent_mb=network_sent_mb,
                    network_received_mb=network_received_mb,
                )
                
                self.update(usage)
                
            except Exception:
                # Silently fail resource monitoring errors
                pass
            
            time.sleep(interval_seconds)


class ProgressPersistence:
    """Handles persistence of progress state."""
    
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path.home() / ".microagents" / "progress"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # SQLite database for structured storage
        self.db_path = self.storage_path / "progress.db"
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize the SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS progress_groups (
                group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                status TEXT,
                data BLOB
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS progress_tasks (
                task_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                name TEXT NOT NULL,
                data BLOB,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES progress_groups (group_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS progress_checkpoints (
                checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                checkpoint_data BLOB,
                created_at TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES progress_tasks (task_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS progress_metrics (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                task_id TEXT,
                metric_type TEXT,
                metric_value REAL,
                recorded_at TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES progress_groups (group_id),
                FOREIGN KEY (task_id) REFERENCES progress_tasks (task_id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def save_group(self, group: ProgressGroup) -> bool:
        """Save a progress group to persistence."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Serialize group data
            group_data = {
                "name": group.name,
                "description": group.description,
                "tasks": {task_id: task.dict() for task_id, task in group.tasks.items()},
                "dependencies": group.dependencies,
                "metrics": group.metrics.dict(),
            }
            
            data_blob = pickle.dumps(group_data)
            
            # Upsert group
            cursor.execute("""
                INSERT OR REPLACE INTO progress_groups 
                (group_id, name, description, created_at, updated_at, status, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                group.group_id,
                group.name,
                group.description,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
                "active",
                data_blob,
            ))
            
            # Save tasks
            for task_id, task in group.tasks.items():
                task_data = pickle.dumps(task.dict())
                
                cursor.execute("""
                    INSERT OR REPLACE INTO progress_tasks
                    (task_id, group_id, name, data, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    task_id,
                    group.group_id,
                    task.name,
                    task_data,
                    task.created_at.isoformat(),
                    datetime.now().isoformat(),
                ))
                
                # Save checkpoint if exists
                if task.checkpoint_data:
                    checkpoint_data = pickle.dumps(task.checkpoint_data)
                    
                    cursor.execute("""
                        INSERT INTO progress_checkpoints
                        (task_id, checkpoint_data, created_at)
                        VALUES (?, ?, ?)
                    """, (
                        task_id,
                        checkpoint_data,
                        datetime.now().isoformat(),
                    ))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            print(f"Failed to save group: {e}")
            return False
    
    def load_group(self, group_id: str) -> Optional[ProgressGroup]:
        """Load a progress group from persistence."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Load group
            cursor.execute(
                "SELECT name, description, data FROM progress_groups WHERE group_id = ?",
                (group_id,)
            )
            group_row = cursor.fetchone()
            
            if not group_row:
                return None
            
            name, description, data_blob = group_row
            group_data = pickle.loads(data_blob)
            
            # Create group
            group = ProgressGroup(group_id, name, description)
            
            # Load tasks
            cursor.execute(
                "SELECT data FROM progress_tasks WHERE group_id = ?",
                (group_id,)
            )
            
            for (task_data_blob,) in cursor.fetchall():
                task_dict = pickle.loads(task_data_blob)
                task = ProgressTask(**task_dict)
                group.tasks[task.id] = task
            
            # Load dependencies
            if "dependencies" in group_data:
                group.dependencies = group_data["dependencies"]
            
            # Load metrics
            if "metrics" in group_data:
                group.metrics = GroupMetrics(**group_data["metrics"])
            
            conn.close()
            return group
            
        except Exception as e:
            print(f"Failed to load group: {e}")
            return None
    
    def delete_group(self, group_id: str) -> bool:
        """Delete a progress group from persistence."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM progress_groups WHERE group_id = ?", (group_id,))
            cursor.execute("DELETE FROM progress_tasks WHERE group_id = ?", (group_id,))
            cursor.execute("DELETE FROM progress_checkpoints WHERE task_id IN (SELECT task_id FROM progress_tasks WHERE group_id = ?)", (group_id,))
            cursor.execute("DELETE FROM progress_metrics WHERE group_id = ?", (group_id,))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception:
            return False
    
    def list_groups(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List progress groups."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            query = "SELECT group_id, name, description, created_at, updated_at, status FROM progress_groups"
            params = []
            
            if status:
                query += " WHERE status = ?"
                params.append(status)
            
            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            groups = []
            for row in rows:
                groups.append({
                    "group_id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                    "status": row[5],
                })
            
            conn.close()
            return groups
            
        except Exception:
            return []


class ProgressManager:
    """Main manager for progress tracking."""
    
    def __init__(self, persistence: Optional[ProgressPersistence] = None):
        self.groups: Dict[str, ProgressGroup] = {}
        self.persistence = persistence or ProgressPersistence()
        self.default_reporters: List[ProgressReporter] = [
            ConsoleReporter(),
            LogReporter(),
        ]
        self._lock = threading.RLock()
        
        # Auto-save interval (seconds)
        self.auto_save_interval = 30
        self._auto_save_thread: Optional[threading.Thread] = None
        self._auto_save_running = False
        
    def create_group(
        self,
        name: str,
        description: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> ProgressGroup:
        """Create a new progress group."""
        with self._lock:
            group_id = group_id or str(uuid.uuid4())
            
            group = ProgressGroup(
                group_id=group_id,
                name=name,
                description=description,
            )
            
            # Add default reporters
            for reporter in self.default_reporters:
                group.add_reporter(reporter)
            
            self.groups[group_id] = group
            
            # Save to persistence
            self.persistence.save_group(group)
            
            return group
    
    def get_group(self, group_id: str) -> Optional[ProgressGroup]:
        """Get a progress group by ID."""
        with self._lock:
            # Try memory first
            if group_id in self.groups:
                return self.groups[group_id]
            
            # Try persistence
            group = self.persistence.load_group(group_id)
            if group:
                self.groups[group_id] = group
                return group
            
            return None
    
    def delete_group(self, group_id: str) -> bool:
        """Delete a progress group."""
        with self._lock:
            if group_id in self.groups:
                del self.groups[group_id]
            
            return self.persistence.delete_group(group_id)
    
    def list_groups(self, **kwargs) -> List[Dict[str, Any]]:
        """List all progress groups."""
        return self.persistence.list_groups(**kwargs)
    
    def start_auto_save(self) -> None:
        """Start automatic saving of progress groups."""
        with self._lock:
            if not self._auto_save_running:
                self._auto_save_running = True
                self._auto_save_thread = threading.Thread(
                    target=self._auto_save_loop,
                    daemon=True,
                )
                self._auto_save_thread.start()
    
    def stop_auto_save(self) -> None:
        """Stop automatic saving."""
        with self._lock:
            self._auto_save_running = False
            if self._auto_save_thread:
                self._auto_save_thread.join(timeout=2.0)
                self._auto_save_thread = None
    
    def save_all(self) -> int:
        """Save all groups to persistence."""
        with self._lock:
            saved_count = 0
            for group in self.groups.values():
                if self.persistence.save_group(group):
                    saved_count += 1
            return saved_count
    
    def _auto_save_loop(self) -> None:
        """Background thread for auto-saving."""
        while self._auto_save_running:
            time.sleep(self.auto_save_interval)
            self.save_all()


# Global progress manager
_progress_manager: Optional[ProgressManager] = None


def get_progress_manager() -> ProgressManager:
    """Get or create the global progress manager."""
    global _progress_manager
    
    if _progress_manager is None:
        _progress_manager = ProgressManager()
        _progress_manager.start_auto_save()
    
    return _progress_manager


@contextmanager
def track_progress(
    name: str,
    description: Optional[str] = None,
    total: float = 100.0,
    group_id: Optional[str] = None,
    **kwargs,
) -> Generator[ProgressTask, None, None]:
    """
    Context manager for tracking progress of a single task.
    
    Args:
        name: Task name
        description: Task description
        total: Total progress units
        group_id: Optional group ID
        **kwargs: Additional task parameters
    
    Yields:
        ProgressTask instance
    """
    manager = get_progress_manager()
    
    # Get or create group
    if group_id:
        group = manager.get_group(group_id)
        if not group:
            group = manager.create_group(f"Auto-Group-{group_id}", group_id=group_id)
    else:
        group = manager.create_group(f"Auto-Group-{uuid.uuid4()}")
    
    # Create task
    task = group.add_task(name, total, description, **kwargs)
    
    # Start task
    group.start_task(task.id)
    
    try:
        yield task
        group.complete_task(task.id)
    except Exception as e:
        group.fail_task(task.id, e)
        raise


@asynccontextmanager
async def track_progress_async(
    name: str,
    description: Optional[str] = None,
    total: float = 100.0,
    group_id: Optional[str] = None,
    **kwargs,
) -> Generator[ProgressTask, None, None]:
    """
    Async context manager for tracking progress of a single task.
    
    Args:
        name: Task name
        description: Task description
        total: Total progress units
        group_id: Optional group ID
        **kwargs: Additional task parameters
    
    Yields:
        ProgressTask instance
    """
    manager = get_progress_manager()
    
    # Get or create group
    if group_id:
        group = manager.get_group(group_id)
        if not group:
            group = manager.create_group(f"Auto-Group-{group_id}", group_id=group_id)
    else:
        group = manager.create_group(f"Auto-Group-{uuid.uuid4()}")
    
    # Create task
    task = group.add_task(name, total, description, **kwargs)
    
    # Start task
    group.start_task(task.id)
    
    try:
        yield task
        group.complete_task(task.id)
    except Exception as e:
        group.fail_task(task.id, e)
        raise


def update_progress(
    task: ProgressTask,
    completed: float,
    total: Optional[float] = None,
    resource_usage: Optional[ResourceUsage] = None,
) -> None:
    """Update progress of a task."""
    # This would typically be called from within the task
    # For simplicity, we'll just update the task directly
    task.update_progress(completed, total)
    
    # In a real implementation, this would notify the group
    # group.update_task_progress(task.id, completed, total, resource_usage)


# Initialize progress manager on module import
get_progress_manager()

# Export public API
__all__ = [
    "get_progress_manager",
    "track_progress",
    "track_progress_async",
    "update_progress",
    "TaskStatus",
    "TaskPriority",
    "ResourceType",
    "ResourceUsage",
    "ProgressTask",
    "ProgressGroup",
    "ProgressReporter",
    "ConsoleReporter",
    "WebhookReporter",
    "LogReporter",
    "ResourceTracker",
    "ProgressPersistence",
    "ProgressManager",
    "TaskDependency",
    "TaskMetrics",
    "GroupMetrics",
]