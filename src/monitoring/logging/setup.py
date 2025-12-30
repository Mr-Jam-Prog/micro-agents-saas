"""
Logging configuration for MicroAgents Platform.
Structured logging with JSON format, correlation IDs, and security features.
"""

import asyncio
import json
import logging
import logging.config
import logging.handlers
import os
import sys
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import structlog
from structlog.types import EventDict, Processor

# Context variables for distributed tracing
_REQUEST_ID: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_SESSION_ID: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
_USER_ID: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
_AGENT_ID: ContextVar[Optional[str]] = ContextVar("agent_id", default=None)
_CORRELATION_ID: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
_TENANT_ID: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)

# Performance tracking
_REQUEST_START_TIME: ContextVar[Optional[float]] = ContextVar("request_start_time", default=None)


class LogLevel(str, Enum):
    """Log levels for the application."""
    
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SecurityLevel(str, Enum):
    """Security levels for log masking."""
    
    PUBLIC = "public"  # No sensitive data
    INTERNAL = "internal"  # Some internal data
    CONFIDENTIAL = "confidential"  # Sensitive data (masked)
    RESTRICTED = "restricted"  # Highly sensitive (not logged)


# Patterns for sensitive data detection (regex-like patterns)
_SENSITIVE_PATTERNS = [
    r"password", r"secret", r"token", r"key", r"auth", r"credential",
    r"ssn", r"social.*security", r"credit.*card", r"cvv", r"cvc",
    r"phone", r"email", r"address", r"birth.*date", r"driver.*license",
    r"passport", r"iban", r"swift", r"routing.*number", r"account.*number",
]

_MASK_STRING = "***MASKED***"


def mask_sensitive_data(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    """
    Processor to mask sensitive data in log events.
    
    Args:
        event_dict: The event dictionary to process.
    
    Returns:
        Masked event dictionary.
    """
    def _mask_value(value: Any) -> Any:
        if isinstance(value, str):
            # Check if field name indicates sensitive data
            if any(pattern in key.lower() for pattern in _SENSITIVE_PATTERNS 
                   for key in event_dict.keys()):
                return _MASK_STRING
            # Check if value looks like a token/secret
            if len(value) > 20 and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
                return _MASK_STRING
        elif isinstance(value, dict):
            return {k: _mask_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_mask_value(v) for v in value]
        return value
    
    return {k: _mask_value(v) for k, v in event_dict.items()}


def add_correlation_ids(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    """
    Add correlation IDs to log events.
    
    Args:
        event_dict: The event dictionary to enrich.
    
    Returns:
        Enriched event dictionary.
    """
    # Add context variables if they exist
    if request_id := _REQUEST_ID.get():
        event_dict["request_id"] = request_id
    if session_id := _SESSION_ID.get():
        event_dict["session_id"] = session_id
    if user_id := _USER_ID.get():
        event_dict["user_id"] = user_id
    if agent_id := _AGENT_ID.get():
        event_dict["agent_id"] = agent_id
    if correlation_id := _CORRELATION_ID.get():
        event_dict["correlation_id"] = correlation_id
    if tenant_id := _TENANT_ID.get():
        event_dict["tenant_id"] = tenant_id
    
    # Add performance metrics if request start time is set
    if start_time := _REQUEST_START_TIME.get():
        event_dict["duration_ms"] = int((time.time() - start_time) * 1000)
    
    return event_dict


def add_business_metrics(event_dict: EventDict) -> EventDict:
    """
    Add business metrics to log events.
    
    Args:
        event_dict: The event dictionary to enrich.
    
    Returns:
        Enriched event dictionary.
    """
    # Add business metrics based on event type
    if event_dict.get("event") == "agent_execution":
        event_dict.setdefault("metrics", {})["agents_active"] = 1
        event_dict.setdefault("metrics", {})["execution_count"] = 1
    
    elif event_dict.get("event") == "api_request":
        event_dict.setdefault("metrics", {})["api_calls"] = 1
        event_dict.setdefault("metrics", {})["endpoint"] = event_dict.get("endpoint", "unknown")
    
    elif event_dict.get("event") == "cost_optimization":
        event_dict.setdefault("metrics", {})["cost_savings"] = event_dict.get("savings", 0)
        event_dict.setdefault("metrics", {})["resources_optimized"] = event_dict.get("resources", 0)
    
    return event_dict


def add_elasticsearch_fields(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    """
    Add Elasticsearch specific fields for better indexing.
    
    Args:
        event_dict: The event dictionary to enrich.
    
    Returns:
        Enriched event dictionary.
    """
    # Add @timestamp field for Elasticsearch
    if "timestamp" in event_dict:
        event_dict["@timestamp"] = event_dict["timestamp"]
    
    # Add log level as keyword for filtering
    event_dict["log.level"] = event_dict.get("level", "INFO").lower()
    
    # Add service name
    event_dict["service.name"] = "microagents-platform"
    event_dict["service.type"] = "devops-intelligence"
    event_dict["service.environment"] = os.getenv("ENVIRONMENT", "development")
    
    # Add cloud provider info if available
    if cloud_provider := os.getenv("CLOUD_PROVIDER"):
        event_dict["cloud.provider"] = cloud_provider.lower()
    
    # Add Kubernetes pod info if running in Kubernetes
    if pod_name := os.getenv("KUBERNETES_POD_NAME"):
        event_dict["kubernetes.pod.name"] = pod_name
        event_dict["kubernetes.namespace"] = os.getenv("KUBERNETES_NAMESPACE", "default")
    
    return event_dict


def drop_debug_logs_in_production(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    """
    Drop DEBUG logs in production environments.
    
    Args:
        event_dict: The event dictionary to process.
    
    Returns:
        Event dictionary or empty dict if dropped.
    """
    environment = os.getenv("ENVIRONMENT", "development")
    log_level = event_dict.get("level", "").upper()
    
    if environment == "production" and log_level == "DEBUG":
        # Return empty dict to drop the event
        return {}
    
    return event_dict


def format_stack_traces(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    """
    Format stack traces for better readability in JSON logs.
    
    Args:
        event_dict: The event dictionary to process.
    
    Returns:
        Processed event dictionary.
    """
    if exc_info := event_dict.get("exc_info"):
        if isinstance(exc_info, tuple) and len(exc_info) == 3:
            import traceback
            event_dict["exception"] = {
                "type": exc_info[0].__name__ if exc_info[0] else "Unknown",
                "message": str(exc_info[1]) if exc_info[1] else "",
                "stack_trace": traceback.format_exception(*exc_info),
            }
            # Remove the original exc_info field
            event_dict.pop("exc_info", None)
    
    return event_dict


class JSONRenderer:
    """Custom JSON renderer with performance optimizations."""
    
    def __init__(self, indent: Optional[int] = None, ensure_ascii: bool = False):
        self.indent = indent
        self.ensure_ascii = ensure_ascii
        # Cache for performance
        self._encoder = json.JSONEncoder(
            indent=indent,
            ensure_ascii=ensure_ascii,
            separators=(",", ":") if not indent else None,
        )
    
    def __call__(self, _: Any, __: Any, event_dict: EventDict) -> str:
        """Render event dict as JSON string."""
        # Add timestamp if not present
        if "timestamp" not in event_dict:
            event_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Add structured fields for ELK stack
        event_dict = add_elasticsearch_fields(None, None, event_dict)
        
        # Add business metrics
        event_dict = add_business_metrics(event_dict)
        
        try:
            return self._encoder.encode(event_dict)
        except (TypeError, ValueError):
            # Fallback for non-serializable objects
            safe_dict = self._make_serializable(event_dict)
            return self._encoder.encode(safe_dict)
    
    def _make_serializable(self, obj: Any) -> Any:
        """Convert non-serializable objects to strings."""
        if isinstance(obj, Mapping):
            return {k: self._make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple, set)):
            return [self._make_serializable(v) for v in obj]
        elif isinstance(obj, (datetime,)):
            return obj.isoformat()
        elif isinstance(obj, (uuid.UUID,)):
            return str(obj)
        elif hasattr(obj, "__dict__"):
            return str(obj)
        else:
            return obj


class AlertingHandler(logging.Handler):
    """
    Handler for log-based alerting.
    Triggers alerts based on log patterns and thresholds.
    """
    
    def __init__(
        self,
        level: Union[int, str] = logging.WARNING,
        alert_patterns: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(level)
        self.alert_patterns = alert_patterns or []
        self.alert_counts: Dict[str, int] = {}
        self.last_alert_time: Dict[str, float] = {}
        self.cooldown_period = 300  # 5 minutes cooldown between alerts
        
    def emit(self, record: logging.LogRecord) -> None:
        """Check log record against alert patterns."""
        log_entry = self.format(record)
        
        for pattern in self.alert_patterns:
            pattern_name = pattern.get("name", "unknown")
            
            # Check if pattern matches
            if self._matches_pattern(record, pattern):
                current_time = time.time()
                last_time = self.last_alert_time.get(pattern_name, 0)
                
                # Apply cooldown
                if current_time - last_time > self.cooldown_period:
                    self._trigger_alert(pattern_name, record, log_entry)
                    self.last_alert_time[pattern_name] = current_time
    
    def _matches_pattern(self, record: logging.LogRecord, pattern: Dict[str, Any]) -> bool:
        """Check if log record matches alert pattern."""
        # Check log level
        if min_level := pattern.get("min_level"):
            if record.levelno < getattr(logging, min_level.upper(), logging.WARNING):
                return False
        
        # Check message pattern
        if message_pattern := pattern.get("message_pattern"):
            if message_pattern not in record.getMessage():
                return False
        
        # Check logger name
        if logger_pattern := pattern.get("logger_pattern"):
            if logger_pattern not in record.name:
                return False
        
        # Check threshold
        if threshold := pattern.get("threshold"):
            pattern_name = pattern.get("name", "unknown")
            self.alert_counts[pattern_name] = self.alert_counts.get(pattern_name, 0) + 1
            
            if self.alert_counts[pattern_name] < threshold:
                return False
            else:
                # Reset count after threshold is reached
                self.alert_counts[pattern_name] = 0
        
        return True
    
    def _trigger_alert(
        self,
        pattern_name: str,
        record: logging.LogRecord,
        log_entry: str,
    ) -> None:
        """Trigger an alert."""
        # In a real implementation, this would send alerts to:
        # - Slack/Teams
        # - PagerDuty/OpsGenie
        # - Email
        # - Webhook
        
        alert_data = {
            "pattern": pattern_name,
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "log_entry": log_entry,
        }
        
        # Log the alert (in production, this would be sent to alerting system)
        print(f"ALERT: {json.dumps(alert_data, indent=2)}")


def setup_logging(
    level: Union[str, int] = LogLevel.INFO,
    json_output: bool = True,
    enable_alerting: bool = True,
    log_file: Optional[str] = None,
    max_file_size: int = 100 * 1024 * 1024,  # 100 MB
    backup_count: int = 10,
    retention_days: int = 30,
) -> None:
    """
    Configure structured logging for the application.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: Whether to output logs in JSON format
        enable_alerting: Whether to enable log-based alerting
        log_file: Path to log file (if None, logs to stdout only)
        max_file_size: Maximum size of log file before rotation (bytes)
        backup_count: Number of backup files to keep
        retention_days: Number of days to keep log files
    """
    # Convert string level to logging constant
    if isinstance(level, str):
        level = getattr(logging, level.upper())
    
    # Define alert patterns
    alert_patterns = [
        {
            "name": "error_rate_high",
            "min_level": "ERROR",
            "threshold": 10,
            "message_pattern": "",
        },
        {
            "name": "authentication_failures",
            "min_level": "WARNING",
            "threshold": 5,
            "message_pattern": "authentication",
        },
        {
            "name": "performance_degradation",
            "min_level": "WARNING",
            "message_pattern": "duration_ms",
        },
        {
            "name": "cost_anomaly",
            "min_level": "WARNING",
            "message_pattern": "cost",
        },
    ]
    
    # Configure processors for structlog
    processors: List[Processor] = [
        structlog.stdlib.filter_by_level,
        drop_debug_logs_in_production,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        format_stack_traces,
        add_correlation_ids,
        mask_sensitive_data,
    ]
    
    if json_output:
        processors.append(JSONRenderer(indent=None, ensure_ascii=False))
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    # Configure structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    # Configure standard logging
    handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    handlers.append(console_handler)
    
    # File handler with rotation
    if log_file:
        # Create log directory if it doesn't exist
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        handlers.append(file_handler)
    
    # Alerting handler
    if enable_alerting:
        alert_handler = AlertingHandler(
            level=logging.WARNING,
            alert_patterns=alert_patterns,
        )
        handlers.append(alert_handler)
    
    # Configure root logger
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "structlog.stdlib.ProcessorFormatter",
                "processor": JSONRenderer(indent=None, ensure_ascii=False),
                "foreign_pre_chain": processors[:-1],
            },
            "console": {
                "()": "structlog.stdlib.ProcessorFormatter",
                "processor": structlog.dev.ConsoleRenderer(),
                "foreign_pre_chain": processors[:-1],
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "console" if not json_output else "json",
                "stream": sys.stdout,
                "level": level,
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "json",
                "filename": log_file if log_file else "/dev/null",
                "maxBytes": max_file_size,
                "backupCount": backup_count,
                "level": level,
            },
            "alert": {
                "()": AlertingHandler,
                "alert_patterns": alert_patterns,
                "level": logging.WARNING,
            },
        },
        "loggers": {
            "": {  # Root logger
                "handlers": ["console"] + (["file"] if log_file else []) + (["alert"] if enable_alerting else []),
                "level": level,
                "propagate": True,
            },
            "microagents": {
                "level": level,
                "propagate": True,
            },
            "uvicorn": {
                "level": logging.WARNING,
                "propagate": True,
            },
            "fastapi": {
                "level": logging.WARNING,
                "propagate": True,
            },
            "sqlalchemy": {
                "level": logging.WARNING,
                "propagate": True,
            },
            "aioredis": {
                "level": logging.WARNING,
                "propagate": True,
            },
            "botocore": {
                "level": logging.WARNING,
                "propagate": True,
            },
        },
    })
    
    # Log configuration
    logger = structlog.get_logger(__name__)
    logger.info(
        "logging_configured",
        level=logging.getLevelName(level),
        json_output=json_output,
        enable_alerting=enable_alerting,
        log_file=log_file,
        max_file_size=human_readable_size(max_file_size),
        backup_count=backup_count,
        retention_days=retention_days,
    )


def human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


# Context managers for correlation IDs
@contextmanager
def request_context(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
):
    """
    Context manager for request-scoped logging context.
    
    Args:
        request_id: Unique request identifier
        user_id: User identifier
        session_id: Session identifier
        agent_id: Agent identifier
        tenant_id: Tenant identifier
    """
    # Generate IDs if not provided
    request_id = request_id or str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    
    # Set context variables
    request_token = _REQUEST_ID.set(request_id)
    correlation_token = _CORRELATION_ID.set(correlation_id)
    user_token = _USER_ID.set(user_id) if user_id else None
    session_token = _SESSION_ID.set(session_id) if session_id else None
    agent_token = _AGENT_ID.set(agent_id) if agent_id else None
    tenant_token = _TENANT_ID.set(tenant_id) if tenant_id else None
    
    # Set request start time for performance tracking
    start_time_token = _REQUEST_START_TIME.set(time.time())
    
    try:
        logger = structlog.get_logger(__name__)
        logger.info(
            "request_started",
            request_id=request_id,
            correlation_id=correlation_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )
        yield
    finally:
        # Clean up context variables
        _REQUEST_ID.reset(request_token)
        _CORRELATION_ID.reset(correlation_token)
        if user_token:
            _USER_ID.reset(user_token)
        if session_token:
            _SESSION_ID.reset(session_token)
        if agent_token:
            _AGENT_ID.reset(agent_token)
        if tenant_token:
            _TENANT_ID.reset(tenant_token)
        
        _REQUEST_START_TIME.reset(start_time_token)
        
        logger = structlog.get_logger(__name__)
        logger.info(
            "request_completed",
            request_id=request_id,
            correlation_id=correlation_id,
        )


@asynccontextmanager
async def async_request_context(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
):
    """
    Async context manager for request-scoped logging context.
    
    Args:
        request_id: Unique request identifier
        user_id: User identifier
        session_id: Session identifier
        agent_id: Agent identifier
        tenant_id: Tenant identifier
    """
    # Generate IDs if not provided
    request_id = request_id or str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    
    # Set context variables
    request_token = _REQUEST_ID.set(request_id)
    correlation_token = _CORRELATION_ID.set(correlation_id)
    user_token = _USER_ID.set(user_id) if user_id else None
    session_token = _SESSION_ID.set(session_id) if session_id else None
    agent_token = _AGENT_ID.set(agent_id) if agent_id else None
    tenant_token = _TENANT_ID.set(tenant_id) if tenant_id else None
    
    # Set request start time for performance tracking
    start_time_token = _REQUEST_START_TIME.set(time.time())
    
    try:
        logger = structlog.get_logger(__name__)
        logger.info(
            "request_started",
            request_id=request_id,
            correlation_id=correlation_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )
        yield
    finally:
        # Clean up context variables
        _REQUEST_ID.reset(request_token)
        _CORRELATION_ID.reset(correlation_token)
        if user_token:
            _USER_ID.reset(user_token)
        if session_token:
            _SESSION_ID.reset(session_token)
        if agent_token:
            _AGENT_ID.reset(agent_token)
        if tenant_token:
            _TENANT_ID.reset(tenant_token)
        
        _REQUEST_START_TIME.reset(start_time_token)
        
        logger = structlog.get_logger(__name__)
        logger.info(
            "request_completed",
            request_id=request_id,
            correlation_id=correlation_id,
        )


# Convenience function to get logger
def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.
    
    Args:
        name: Logger name (defaults to caller module name)
    
    Returns:
        Bound logger instance.
    """
    if name is None:
        # Get caller module name
        import inspect
        frame = inspect.currentframe()
        try:
            if frame and frame.f_back:
                name = frame.f_back.f_globals.get("__name__", "__main__")
            else:
                name = "__main__"
        finally:
            del frame
    
    return structlog.get_logger(name)


# Convenience functions for common log patterns
def log_performance_metric(
    operation: str,
    duration_ms: float,
    success: bool = True,
    details: Optional[Dict[str, Any]] = None,
    logger_name: str = "performance",
) -> None:
    """
    Log a performance metric.
    
    Args:
        operation: Name of the operation
        duration_ms: Duration in milliseconds
        success: Whether the operation succeeded
        details: Additional details
        logger_name: Logger name
    """
    logger = get_logger(logger_name)
    log_data = {
        "event": "performance_metric",
        "operation": operation,
        "duration_ms": round(duration_ms, 2),
        "success": success,
        "level": "INFO" if success else "WARNING",
    }
    
    if details:
        log_data.update(details)
    
    logger.info("performance_metric", **log_data)


def log_business_event(
    event_type: str,
    entity_type: str,
    entity_id: str,
    action: str,
    changes: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    logger_name: str = "business",
) -> None:
    """
    Log a business event for audit trail.
    
    Args:
        event_type: Type of event (created, updated, deleted, etc.)
        entity_type: Type of entity (user, agent, tenant, etc.)
        entity_id: ID of the entity
        action: Business action performed
        changes: What changed
        user_id: User who performed the action
        logger_name: Logger name
    """
    logger = get_logger(logger_name)
    log_data = {
        "event": "business_event",
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "user_id": user_id or _USER_ID.get(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    if changes:
        log_data["changes"] = changes
    
    logger.info("business_event", **log_data)


def log_security_event(
    event_type: str,
    severity: str,
    source_ip: Optional[str] = None,
    user_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    logger_name: str = "security",
) -> None:
    """
    Log a security event.
    
    Args:
        event_type: Type of security event
        severity: Severity level (low, medium, high, critical)
        source_ip: Source IP address
        user_id: User ID if applicable
        details: Additional details
        logger_name: Logger name
    """
    logger = get_logger(logger_name)
    log_data = {
        "event": "security_event",
        "event_type": event_type,
        "severity": severity,
        "source_ip": source_ip,
        "user_id": user_id or _USER_ID.get(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "WARNING" if severity in ["low", "medium"] else "ERROR",
    }
    
    if details:
        log_data.update(details)
    
    if severity in ["high", "critical"]:
        logger.error("security_event", **log_data)
    else:
        logger.warning("security_event", **log_data)


# Initialize logging on module import
# This can be overridden by calling setup_logging() with custom parameters
setup_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_output=os.getenv("LOG_JSON", "true").lower() == "true",
    enable_alerting=os.getenv("LOG_ALERTING", "true").lower() == "true",
    log_file=os.getenv("LOG_FILE"),
    max_file_size=int(os.getenv("LOG_MAX_SIZE", 100 * 1024 * 1024)),
    backup_count=int(os.getenv("LOG_BACKUP_COUNT", "10")),
)

# Export public API
__all__ = [
    "setup_logging",
    "get_logger",
    "request_context",
    "async_request_context",
    "log_performance_metric",
    "log_business_event",
    "log_security_event",
    "LogLevel",
    "SecurityLevel",
    "AlertingHandler",
]