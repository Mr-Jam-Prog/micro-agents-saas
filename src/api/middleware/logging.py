"""
Logging middleware for MicroAgents Platform.
Request/response logging, performance metrics, and audit trails.
"""

import asyncio
import functools
import gzip
import hashlib
import inspect
import io
import json
import logging
import re
import sys
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from functools import wraps
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

import orjson
import structlog
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, validator
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Local imports
from src.monitoring.logging.setup import get_logger, log_performance_metric, log_security_event
from src.monitoring.tracing.tracer import get_trace_manager, trace_span
from src.utils.security.utils import SecurityManager, get_security_manager

# Type aliases
LogRecord = Dict[str, Any]
PerformanceMetrics = Dict[str, Union[int, float, Dict[str, Any]]]
BusinessContext = Dict[str, Any]


class LogLevel(str, Enum):
    """Log levels for middleware."""
    
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogSamplingStrategy(str, Enum):
    """Log sampling strategies."""
    
    ALL = "all"  # Log everything
    PROBABILISTIC = "probabilistic"  # Sample based on probability
    ADAPTIVE = "adaptive"  # Adjust based on load
    SMART = "smart"  # Based on request characteristics
    CUSTOM = "custom"  # Custom rules


class AnomalyType(str, Enum):
    """Types of anomalies to detect."""
    
    HIGH_LATENCY = "high_latency"
    ERROR_SPIKE = "error_spike"
    UNUSUAL_PAYLOAD_SIZE = "unusual_payload_size"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    UNUSUAL_USER_AGENT = "unusual_user_agent"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    DATA_LEAKAGE = "data_leakage"
    MALICIOUS_INPUT = "malicious_input"


class ComplianceStandard(str, Enum):
    """Compliance standards for audit trails."""
    
    GDPR = "gdpr"
    HIPAA = "hipaa"
    SOC2 = "soc2"
    ISO27001 = "iso27001"
    PCI_DSS = "pci_dss"
    FERPA = "ferpa"
    CCPA = "ccpa"
    NIST = "nist"


class LogConfig(BaseModel):
    """Configuration for logging middleware."""
    
    # Basic settings
    enabled: bool = True
    level: LogLevel = LogLevel.INFO
    format_json: bool = True
    include_headers: bool = True
    include_body: bool = False  # Be careful with sensitive data
    max_body_size: int = 1024 * 10  # 10KB
    
    # Sampling
    sampling_strategy: LogSamplingStrategy = LogSamplingStrategy.ADAPTIVE
    sampling_rate: float = 0.1  # 10% for probabilistic sampling
    adaptive_threshold: int = 1000  # Requests per minute
    
    # Performance metrics
    collect_performance_metrics: bool = True
    latency_threshold_ms: int = 1000  # Alert threshold
    percentile_thresholds: List[float] = Field(default=[50, 95, 99])
    
    # Anomaly detection
    anomaly_detection_enabled: bool = True
    anomaly_window_size: int = 1000  # Requests in window
    anomaly_confidence_threshold: float = 0.95
    
    # Compliance
    compliance_standards: Set[ComplianceStandard] = Field(default_factory=set)
    retention_days: int = 90
    audit_trail_enabled: bool = True
    
    # Filtering
    exclude_paths: List[str] = Field(default=[
        "/health",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
    ])
    exclude_status_codes: List[int] = Field(default=[401, 404])
    
    # Security
    mask_sensitive_data: bool = True
    sensitive_fields: List[str] = Field(default=[
        "password",
        "token",
        "secret",
        "key",
        "authorization",
        "cookie",
        "credit_card",
        "ssn",
        "phone",
        "email",
    ])
    
    # Correlation
    correlation_header: str = "X-Correlation-ID"
    trace_header: str = "X-Trace-ID"
    
    # Business metrics
    collect_business_metrics: bool = True
    business_metric_prefix: str = "business"
    
    # Error tracking
    capture_exceptions: bool = True
    error_grouping_window: int = 60  # seconds
    
    class Config:
        json_encoders = {
            set: list,
        }
    
    @validator('sampling_rate')
    def validate_sampling_rate(cls, v):
        if not 0 <= v <= 1:
            raise ValueError("Sampling rate must be between 0 and 1")
        return v
    
    @validator('latency_threshold_ms')
    def validate_latency_threshold(cls, v):
        if v < 0:
            raise ValueError("Latency threshold must be positive")
        return v


class PerformanceBreakdown(BaseModel):
    """Performance breakdown for a request."""
    
    total_duration_ms: float
    network_latency_ms: Optional[float] = None
    processing_time_ms: Optional[float] = None
    database_time_ms: Optional[float] = None
    cache_time_ms: Optional[float] = None
    external_api_time_ms: Optional[float] = None
    serialization_time_ms: Optional[float] = None
    
    # Percentiles
    percentile_50: Optional[float] = None
    percentile_95: Optional[float] = None
    percentile_99: Optional[float] = None
    
    class Config:
        json_encoders = {
            float: lambda v: round(v, 2),
        }


class BusinessMetric(BaseModel):
    """Business metric for tracking."""
    
    metric_name: str
    value: Union[int, float]
    unit: str = "count"
    dimensions: Dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


class AuditEvent(BaseModel):
    """Audit event for compliance."""
    
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    action: str
    status: str  # success, failure, error
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    compliance_standards: List[ComplianceStandard] = Field(default_factory=list)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            set: list,
        }


class AnomalyDetectionRule(BaseModel):
    """Rule for anomaly detection."""
    
    anomaly_type: AnomalyType
    threshold: Union[int, float]
    window_size: int = 1000  # requests
    confidence_level: float = 0.95
    enabled: bool = True
    
    @validator('confidence_level')
    def validate_confidence(cls, v):
        if not 0 <= v <= 1:
            raise ValueError("Confidence level must be between 0 and 1")
        return v


class LogSampler(ABC):
    """Abstract base class for log samplers."""
    
    @abstractmethod
    def should_sample(self, request: Request, config: LogConfig) -> bool:
        """Determine if request should be logged."""
        pass


class ProbabilisticSampler(LogSampler):
    """Probabilistic log sampler."""
    
    def should_sample(self, request: Request, config: LogConfig) -> bool:
        """Sample based on probability."""
        import random
        return random.random() < config.sampling_rate


class AdaptiveSampler(LogSampler):
    """Adaptive log sampler based on load."""
    
    def __init__(self):
        self.request_counts = defaultdict(int)
        self.last_reset = datetime.now(timezone.utc)
        self.current_rate = 0
    
    def should_sample(self, request: Request, config: LogConfig) -> bool:
        """Adapt sampling based on request rate."""
        current_time = datetime.now(timezone.utc)
        
        # Reset counter every minute
        if (current_time - self.last_reset).seconds >= 60:
            self.request_counts.clear()
            self.last_reset = current_time
        
        # Track request
        minute_key = current_time.strftime("%Y-%m-%d-%H-%M")
        self.request_counts[minute_key] += 1
        
        # Calculate current rate
        total_requests = sum(self.request_counts.values())
        minutes_passed = max(1, (current_time - self.last_reset).seconds / 60)
        self.current_rate = total_requests / minutes_passed
        
        # Adjust sampling based on rate
        if self.current_rate > config.adaptive_threshold:
            # High load, sample less
            target_rate = max(0.01, 1.0 / (self.current_rate / config.adaptive_threshold))
            import random
            return random.random() < target_rate
        else:
            # Normal load, sample everything
            return True


class SmartSampler(LogSampler):
    """Smart sampler based on request characteristics."""
    
    def should_sample(self, request: Request, config: LogConfig) -> bool:
        """Smart sampling based on request type."""
        path = request.url.path
        
        # Always sample important endpoints
        important_patterns = [
            r"^/api/v1/.*/critical",
            r"^/admin/",
            r"^/auth/",
            r"^/billing/",
        ]
        
        for pattern in important_patterns:
            if re.match(pattern, path):
                return True
        
        # Sample errors
        if hasattr(request.state, 'will_error'):
            return True
        
        # Sample slow requests
        if hasattr(request.state, 'is_slow'):
            return True
        
        # Sample based on user role if available
        if hasattr(request.state, 'auth'):
            auth = request.state.auth
            if hasattr(auth, 'roles'):
                # Sample more for admin users
                if 'admin' in [r.value for r in auth.roles]:
                    return True
        
        # Default: probabilistic sampling
        import random
        return random.random() < config.sampling_rate


class AnomalyDetector:
    """Anomaly detection for requests."""
    
    def __init__(self, config: LogConfig):
        self.config = config
        self.rules: Dict[AnomalyType, AnomalyDetectionRule] = {}
        self.metrics_window: List[Dict[str, Any]] = []
        self.max_window_size = config.anomaly_window_size
        
        # Initialize default rules
        self._initialize_default_rules()
    
    def _initialize_default_rules(self) -> None:
        """Initialize default anomaly detection rules."""
        default_rules = [
            AnomalyDetectionRule(
                anomaly_type=AnomalyType.HIGH_LATENCY,
                threshold=1000,  # 1 second
                window_size=100,
            ),
            AnomalyDetectionRule(
                anomaly_type=AnomalyType.ERROR_SPIKE,
                threshold=0.05,  # 5% error rate
                window_size=100,
            ),
            AnomalyDetectionRule(
                anomaly_type=AnomalyType.UNUSUAL_PAYLOAD_SIZE,
                threshold=1024 * 1024,  # 1MB
                window_size=100,
            ),
        ]
        
        for rule in default_rules:
            self.rules[rule.anomaly_type] = rule
    
    def add_metric(self, metric: Dict[str, Any]) -> None:
        """Add metric to detection window."""
        self.metrics_window.append(metric)
        
        # Keep window size limited
        if len(self.metrics_window) > self.max_window_size:
            self.metrics_window.pop(0)
    
    def detect_anomalies(self, current_metric: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Detect anomalies in current request."""
        if not self.metrics_window or len(self.metrics_window) < 10:
            return []
        
        anomalies = []
        
        # Check each rule
        for rule in self.rules.values():
            if not rule.enabled:
                continue
            
            anomaly = self._check_rule(rule, current_metric)
            if anomaly:
                anomalies.append(anomaly)
        
        return anomalies
    
    def _check_rule(self, rule: AnomalyDetectionRule, current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check a specific anomaly rule."""
        if rule.anomaly_type == AnomalyType.HIGH_LATENCY:
            return self._check_high_latency(rule, current)
        elif rule.anomaly_type == AnomalyType.ERROR_SPIKE:
            return self._check_error_spike(rule, current)
        elif rule.anomaly_type == AnomalyType.UNUSUAL_PAYLOAD_SIZE:
            return self._check_payload_size(rule, current)
        elif rule.anomaly_type == AnomalyType.SUSPICIOUS_ACTIVITY:
            return self._check_suspicious_activity(rule, current)
        
        return None
    
    def _check_high_latency(self, rule: AnomalyDetectionRule, current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check for high latency anomalies."""
        latency = current.get('duration_ms', 0)
        
        if latency > rule.threshold:
            # Calculate percentile
            latencies = [m.get('duration_ms', 0) for m in self.metrics_window]
            latencies.append(latency)
            sorted_latencies = sorted(latencies)
            
            p95_index = int(len(sorted_latencies) * 0.95)
            p95 = sorted_latencies[p95_index] if p95_index < len(sorted_latencies) else 0
            
            if latency > p95 * 2:  # More than 2x the 95th percentile
                return {
                    'type': AnomalyType.HIGH_LATENCY.value,
                    'detected_value': latency,
                    'threshold': rule.threshold,
                    'percentile_95': p95,
                    'confidence': rule.confidence_level,
                    'details': {
                        'path': current.get('path'),
                        'method': current.get('method'),
                        'user_id': current.get('user_id'),
                    }
                }
        
        return None
    
    def _check_error_spike(self, rule: AnomalyDetectionRule, current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check for error rate spikes."""
        # Count errors in window
        error_count = sum(1 for m in self.metrics_window if m.get('status_code', 200) >= 400)
        total_count = len(self.metrics_window)
        
        current_error_rate = error_count / total_count if total_count > 0 else 0
        
        if current_error_rate > rule.threshold:
            # Check if current request is also an error
            if current.get('status_code', 200) >= 400:
                return {
                    'type': AnomalyType.ERROR_SPIKE.value,
                    'detected_value': current_error_rate,
                    'threshold': rule.threshold,
                    'error_count': error_count,
                    'total_count': total_count,
                    'confidence': rule.confidence_level,
                    'details': {
                        'recent_errors': [
                            {'path': m.get('path'), 'status': m.get('status_code')}
                            for m in self.metrics_window[-10:] if m.get('status_code', 200) >= 400
                        ]
                    }
                }
        
        return None
    
    def _check_payload_size(self, rule: AnomalyDetectionRule, current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check for unusual payload sizes."""
        request_size = current.get('request_size', 0)
        response_size = current.get('response_size', 0)
        
        if request_size > rule.threshold or response_size > rule.threshold:
            # Calculate average
            avg_request_size = sum(m.get('request_size', 0) for m in self.metrics_window) / len(self.metrics_window)
            avg_response_size = sum(m.get('response_size', 0) for m in self.metrics_window) / len(self.metrics_window)
            
            threshold_multiplier = 10  # 10x average is unusual
            
            if request_size > avg_request_size * threshold_multiplier:
                return {
                    'type': AnomalyType.UNUSUAL_PAYLOAD_SIZE.value,
                    'detected_value': request_size,
                    'threshold': rule.threshold,
                    'average': avg_request_size,
                    'multiplier': threshold_multiplier,
                    'confidence': rule.confidence_level,
                    'details': {
                        'path': current.get('path'),
                        'method': current.get('method'),
                        'payload_type': 'request',
                    }
                }
            
            if response_size > avg_response_size * threshold_multiplier:
                return {
                    'type': AnomalyType.UNUSUAL_PAYLOAD_SIZE.value,
                    'detected_value': response_size,
                    'threshold': rule.threshold,
                    'average': avg_response_size,
                    'multiplier': threshold_multiplier,
                    'confidence': rule.confidence_level,
                    'details': {
                        'path': current.get('path'),
                        'method': current.get('method'),
                        'payload_type': 'response',
                    }
                }
        
        return None
    
    def _check_suspicious_activity(self, rule: AnomalyDetectionRule, current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check for suspicious activity patterns."""
        # This is a simplified version
        # In production, you'd use more sophisticated pattern detection
        
        ip_address = current.get('ip_address')
        user_agent = current.get('user_agent', '')
        
        if not ip_address or not user_agent:
            return None
        
        # Check for known malicious patterns
        malicious_patterns = [
            r"(?i)sql.*injection",
            r"(?i)xss",
            r"(?i)\.\./",  # Path traversal
            r"(?i)<script>",
            r"(?i)union.*select",
        ]
        
        for pattern in malicious_patterns:
            if re.search(pattern, user_agent) or re.search(pattern, str(current.get('request_body', ''))):
                return {
                    'type': AnomalyType.SUSPICIOUS_ACTIVITY.value,
                    'detected_value': pattern,
                    'threshold': rule.threshold,
                    'confidence': rule.confidence_level,
                    'details': {
                        'pattern': pattern,
                        'ip_address': ip_address,
                        'user_agent': user_agent,
                        'path': current.get('path'),
                    }
                }
        
        return None


class BusinessMetricsCollector:
    """Collector for business metrics."""
    
    def __init__(self, config: LogConfig):
        self.config = config
        self.metrics: List[BusinessMetric] = []
        self.metrics_lock = asyncio.Lock()
        
    async def add_metric(self, metric: BusinessMetric) -> None:
        """Add business metric."""
        async with self.metrics_lock:
            self.metrics.append(metric)
            
            # Keep metrics manageable
            if len(self.metrics) > 10000:
                self.metrics = self.metrics[-5000:]
    
    async def extract_from_request(self, request: Request, response: Response, 
                                 duration_ms: float) -> List[BusinessMetric]:
        """Extract business metrics from request/response."""
        metrics = []
        
        # Extract user context
        user_id = None
        user_email = None
        if hasattr(request.state, 'auth'):
            auth = request.state.auth
            user_id = getattr(auth, 'user_id', None)
            user_email = getattr(auth, 'email', None)
        
        # Path-based metrics
        path = request.url.path
        
        # Agent execution metrics
        if "/agents/" in path and "/execute" in path:
            metrics.append(BusinessMetric(
                metric_name="agent_execution_count",
                value=1,
                unit="count",
                dimensions={
                    "path": path,
                    "method": request.method,
                    "user_id": user_id or "anonymous",
                }
            ))
        
        # API call metrics
        if path.startswith("/api/"):
            metrics.append(BusinessMetric(
                metric_name="api_call_count",
                value=1,
                unit="count",
                dimensions={
                    "path": path,
                    "method": request.method,
                    "status_code": str(response.status_code),
                }
            ))
            
            # API latency
            metrics.append(BusinessMetric(
                metric_name="api_latency_ms",
                value=duration_ms,
                unit="milliseconds",
                dimensions={
                    "path": path,
                    "method": request.method,
                }
            ))
        
        # User activity metrics
        if user_id:
            metrics.append(BusinessMetric(
                metric_name="user_activity_count",
                value=1,
                unit="count",
                dimensions={
                    "user_id": user_id,
                    "path": path,
                    "method": request.method,
                }
            ))
        
        # Error metrics
        if response.status_code >= 400:
            metrics.append(BusinessMetric(
                metric_name="error_count",
                value=1,
                unit="count",
                dimensions={
                    "status_code": str(response.status_code),
                    "path": path,
                    "method": request.method,
                }
            ))
        
        # Throughput metrics
        metrics.append(BusinessMetric(
            metric_name="request_throughput",
            value=1,
            unit="count",
            dimensions={
                "minute": datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M"),
            }
        ))
        
        return metrics


class ComplianceAuditor:
    """Compliance auditor for audit trails."""
    
    def __init__(self, config: LogConfig, security_manager: SecurityManager):
        self.config = config
        self.security_manager = security_manager
        self.audit_events: List[AuditEvent] = []
        self.audit_lock = asyncio.Lock()
        
    async def audit_request(self, request: Request, response: Response, 
                          user_context: Dict[str, Any]) -> Optional[AuditEvent]:
        """Create audit event for request."""
        if not self.config.audit_trail_enabled:
            return None
        
        # Check if this endpoint requires auditing
        if not self._requires_audit(request):
            return None
        
        # Extract audit data
        event_type = self._determine_event_type(request, response)
        action = self._determine_action(request)
        resource_type, resource_id = self._extract_resource_info(request)
        
        # Create audit event
        audit_event = AuditEvent(
            event_type=event_type,
            user_id=user_context.get('user_id'),
            user_email=user_context.get('email'),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get('user-agent'),
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            status="success" if response.status_code < 400 else "failure",
            details={
                "method": request.method,
                "path": request.url.path,
                "query_params": dict(request.query_params),
                "status_code": response.status_code,
                "response_size": self._get_response_size(response),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            compliance_standards=list(self.config.compliance_standards),
        )
        
        # Store audit event
        async with self.audit_lock:
            self.audit_events.append(audit_event)
            
            # Trim old events
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)
            self.audit_events = [e for e in self.audit_events if e.timestamp > cutoff]
        
        # Also log to security manager
        self.security_manager.audit_event(
            event_type=event_type,
            user_id=user_context.get('user_id'),
            ip_address=request.client.host if request.client else None,
            details=audit_event.dict(),
            severity="INFO" if response.status_code < 400 else "WARNING",
        )
        
        return audit_event
    
    def _requires_audit(self, request: Request) -> bool:
        """Check if request requires auditing."""
        path = request.url.path
        
        # Always audit sensitive endpoints
        sensitive_patterns = [
            r"^/auth/",
            r"^/users/",
            r"^/admin/",
            r"^/billing/",
            r"^/api/v[0-9]+/.*/(create|update|delete)",
            r"^/data/export",
            r"^/audit/",
        ]
        
        for pattern in sensitive_patterns:
            if re.match(pattern, path):
                return True
        
        # Audit based on method
        if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            return True
        
        return False
    
    def _determine_event_type(self, request: Request, response: Response) -> str:
        """Determine event type from request and response."""
        path = request.url.path
        
        if path.startswith("/auth/"):
            return "authentication"
        elif path.startswith("/users/"):
            return "user_management"
        elif path.startswith("/agents/"):
            return "agent_operation"
        elif path.startswith("/api/"):
            return "api_call"
        elif path.startswith("/data/"):
            return "data_operation"
        elif path.startswith("/billing/"):
            return "billing_operation"
        elif path.startswith("/admin/"):
            return "admin_operation"
        else:
            return "general_request"
    
    def _determine_action(self, request: Request) -> str:
        """Determine action from request method."""
        method = request.method
        
        if method == "GET":
            return "read"
        elif method == "POST":
            return "create"
        elif method in ["PUT", "PATCH"]:
            return "update"
        elif method == "DELETE":
            return "delete"
        else:
            return method.lower()
    
    def _extract_resource_info(self, request: Request) -> Tuple[Optional[str], Optional[str]]:
        """Extract resource type and ID from request."""
        path = request.url.path
        
        # Extract from path patterns
        patterns = [
            (r"^/users/([^/]+)", "user"),
            (r"^/agents/([^/]+)", "agent"),
            (r"^/organizations/([^/]+)", "organization"),
            (r"^/projects/([^/]+)", "project"),
            (r"^/api/v[0-9]+/([^/]+)/([^/]+)", "api_resource"),
        ]
        
        for pattern, resource_type in patterns:
            match = re.match(pattern, path)
            if match:
                resource_id = match.group(1) if len(match.groups()) >= 1 else None
                return resource_type, resource_id
        
        return None, None
    
    def _get_response_size(self, response: Response) -> Optional[int]:
        """Get response size if available."""
        if hasattr(response, '__len__'):
            return len(response)
        elif hasattr(response, 'body'):
            return len(response.body) if response.body else 0
        return None


class LoggingMiddleware(BaseHTTPMiddleware):
    """Main logging middleware for FastAPI."""
    
    def __init__(
        self,
        app: ASGIApp,
        config: Optional[LogConfig] = None,
        security_manager: Optional[SecurityManager] = None,
    ):
        super().__init__(app)
        self.config = config or LogConfig()
        self.security_manager = security_manager or get_security_manager()
        
        # Initialize components
        self.logger = get_logger("api.middleware.logging")
        self.trace_manager = get_trace_manager()
        
        # Initialize samplers
        self.samplers = {
            LogSamplingStrategy.ALL: lambda r, c: True,
            LogSamplingStrategy.PROBABILISTIC: ProbabilisticSampler(),
            LogSamplingStrategy.ADAPTIVE: AdaptiveSampler(),
            LogSamplingStrategy.SMART: SmartSampler(),
        }
        
        # Initialize detectors and collectors
        self.anomaly_detector = AnomalyDetector(self.config) if self.config.anomaly_detection_enabled else None
        self.business_collector = BusinessMetricsCollector(self.config) if self.config.collect_business_metrics else None
        self.compliance_auditor = ComplianceAuditor(self.config, self.security_manager)
        
        # Performance metrics
        self.request_duration = Histogram(
            'http_request_duration_seconds',
            'HTTP request duration in seconds',
            ['method', 'endpoint', 'status_code']
        )
        self.request_count = Counter(
            'http_requests_total',
            'Total HTTP requests',
            ['method', 'endpoint', 'status_code']
        )
        self.request_size = Histogram(
            'http_request_size_bytes',
            'HTTP request size in bytes',
            ['method', 'endpoint']
        )
        self.response_size = Histogram(
            'http_response_size_bytes',
            'HTTP response size in bytes',
            ['method', 'endpoint', 'status_code']
        )
        
        # Error tracking
        self.error_counter = Counter(
            'http_errors_total',
            'Total HTTP errors',
            ['method', 'endpoint', 'status_code']
        )
        
        # Business metrics
        self.business_counter = Counter(
            'business_events_total',
            'Total business events',
            ['event_type', 'user_type']
        )
        
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request and log details."""
        start_time = time.time()
        
        # Check if logging is disabled for this path
        if self._should_exclude(request):
            return await call_next(request)
        
        # Generate correlation IDs
        correlation_id = self._get_correlation_id(request)
        trace_id = self._get_trace_id(request)
        
        # Store in request state
        request.state.correlation_id = correlation_id
        request.state.trace_id = trace_id
        request.state.start_time = start_time
        
        # Add correlation headers
        request.headers.__dict__["_list"].append(
            (b"x-correlation-id", correlation_id.encode())
        )
        
        # Get user context
        user_context = self._extract_user_context(request)
        
        # Check if we should sample this request
        should_log = self._should_sample(request)
        
        # Prepare request info
        request_info = await self._capture_request_info(request, should_log)
        
        # Process request with error handling
        try:
            response = await call_next(request)
            
            # Calculate duration
            duration = time.time() - start_time
            duration_ms = duration * 1000
            
            # Capture response info
            response_info = await self._capture_response_info(response, request, duration_ms)
            
            # Combine info
            log_data = {
                **request_info,
                **response_info,
                "correlation_id": correlation_id,
                "trace_id": trace_id,
                "user_context": user_context,
                "duration_ms": duration_ms,
                "should_log": should_log,
            }
            
            # Collect performance metrics
            await self._collect_performance_metrics(request, response, duration_ms, log_data)
            
            # Collect business metrics
            if self.business_collector:
                business_metrics = await self.business_collector.extract_from_request(
                    request, response, duration_ms
                )
                for metric in business_metrics:
                    await self.business_collector.add_metric(metric)
            
            # Detect anomalies
            if self.anomaly_detector and should_log:
                self.anomaly_detector.add_metric(log_data)
                anomalies = self.anomaly_detector.detect_anomalies(log_data)
                if anomalies:
                    log_data["anomalies"] = anomalies
                    await self._handle_anomalies(anomalies, request, response)
            
            # Create audit trail
            audit_event = await self.compliance_auditor.audit_request(request, response, user_context)
            if audit_event:
                log_data["audit_event"] = audit_event.dict()
            
            # Log the request
            if should_log:
                await self._log_request(log_data)
            
            # Track errors
            if response.status_code >= 400:
                self._track_error(request, response, log_data)
            
            # Add correlation headers to response
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Trace-ID"] = trace_id
            
            # Log performance metric
            if duration_ms > self.config.latency_threshold_ms:
                log_performance_metric(
                    operation=f"{request.method} {request.url.path}",
                    duration_ms=duration_ms,
                    success=response.status_code < 400,
                    details={
                        "correlation_id": correlation_id,
                        "user_id": user_context.get('user_id'),
                        "status_code": response.status_code,
                    }
                )
            
            return response
            
        except HTTPException as e:
            # Handle HTTP exceptions
            duration = time.time() - start_time
            duration_ms = duration * 1000
            
            error_info = self._capture_exception_info(e, duration_ms)
            log_data = {
                **request_info,
                **error_info,
                "correlation_id": correlation_id,
                "trace_id": trace_id,
                "user_context": user_context,
                "duration_ms": duration_ms,
                "should_log": should_log,
            }
            
            if should_log:
                await self._log_error(log_data)
            
            raise e
            
        except Exception as e:
            # Handle unexpected exceptions
            duration = time.time() - start_time
            duration_ms = duration * 1000
            
            error_info = self._capture_exception_info(e, duration_ms)
            log_data = {
                **request_info,
                **error_info,
                "correlation_id": correlation_id,
                "trace_id": trace_id,
                "user_context": user_context,
                "duration_ms": duration_ms,
                "should_log": should_log,
            }
            
            if should_log:
                await self._log_error(log_data)
            
            # Re-raise the exception
            raise e
    
    def _should_exclude(self, request: Request) -> bool:
        """Check if request should be excluded from logging."""
        path = request.url.path
        
        # Check excluded paths
        if any(path.startswith(excluded) for excluded in self.config.exclude_paths):
            return True
        
        # Check for health/readiness endpoints
        if path in ["/health", "/ready", "/live", "/metrics"]:
            return True
        
        # Check for static files
        if any(path.endswith(ext) for ext in [".css", ".js", ".png", ".jpg", ".ico", ".svg"]):
            return True
        
        return False
    
    def _should_sample(self, request: Request) -> bool:
        """Determine if request should be logged based on sampling strategy."""
        sampler = self.samplers.get(self.config.sampling_strategy)
        if callable(sampler):
            return sampler(request, self.config)
        elif hasattr(sampler, 'should_sample'):
            return sampler.should_sample(request, self.config)
        return True  # Default to logging everything
    
    def _get_correlation_id(self, request: Request) -> str:
        """Get or generate correlation ID."""
        # Check header
        correlation_id = request.headers.get(self.config.correlation_header)
        if correlation_id:
            return correlation_id
        
        # Check query parameter
        correlation_id = request.query_params.get("correlation_id")
        if correlation_id:
            return correlation_id
        
        # Generate new correlation ID
        return str(uuid.uuid4())
    
    def _get_trace_id(self, request: Request) -> str:
        """Get or generate trace ID."""
        # Check header
        trace_id = request.headers.get(self.config.trace_header)
        if trace_id:
            return trace_id
        
        # Generate new trace ID
        return str(uuid.uuid4())
    
    def _extract_user_context(self, request: Request) -> Dict[str, Any]:
        """Extract user context from request."""
        user_context = {
            "user_id": None,
            "email": None,
            "roles": [],
            "permissions": [],
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "device_id": request.headers.get("x-device-id"),
            "session_id": request.headers.get("x-session-id"),
        }
        
        # Try to get from authentication middleware
        if hasattr(request.state, 'auth'):
            auth = request.state.auth
            user_context["user_id"] = getattr(auth, 'user_id', None)
            user_context["email"] = getattr(auth, 'email', None)
            user_context["roles"] = [r.value for r in getattr(auth, 'roles', [])]
            user_context["permissions"] = [p.value for p in getattr(auth, 'permissions', [])]
        
        # Try to get from JWT token
        elif authorization := request.headers.get("authorization"):
            try:
                # Extract token and decode (without verification for context)
                token = authorization.replace("Bearer ", "")
                # Simple extraction without verification
                import jwt
                payload = jwt.decode(token, options={"verify_signature": False})
                user_context["user_id"] = payload.get("sub")
                user_context["email"] = payload.get("email")
                user_context["roles"] = payload.get("roles", [])
                user_context["permissions"] = payload.get("permissions", [])
            except Exception:
                pass
        
        return user_context
    
    async def _capture_request_info(self, request: Request, should_log: bool) -> Dict[str, Any]:
        """Capture request information."""
        request_info = {
            "type": "request",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "referer": request.headers.get("referer"),
            "content_type": request.headers.get("content-type"),
            "content_length": request.headers.get("content-length"),
        }
        
        # Add headers if configured
        if self.config.include_headers:
            headers = dict(request.headers)
            # Mask sensitive headers
            if self.config.mask_sensitive_data:
                headers = self._mask_sensitive_data(headers)
            request_info["headers"] = headers
        
        # Add body if configured and should log
        if self.config.include_body and should_log:
            try:
                body = await request.body()
                if body:
                    # Limit body size
                    if len(body) > self.config.max_body_size:
                        body = body[:self.config.max_body_size]
                    
                    # Try to parse as JSON
                    try:
                        body_json = json.loads(body.decode('utf-8'))
                        # Mask sensitive fields
                        if self.config.mask_sensitive_data:
                            body_json = self._mask_sensitive_data(body_json)
                        request_info["body"] = body_json
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Store as text or base64
                        try:
                            request_info["body_text"] = body.decode('utf-8', errors='ignore')[:1000]
                        except:
                            request_info["body_base64"] = body[:500].hex()
                    
                    request_info["request_size"] = len(body)
            except Exception as e:
                request_info["body_error"] = str(e)
        
        return request_info
    
    async def _capture_response_info(self, response: Response, request: Request, 
                                   duration_ms: float) -> Dict[str, Any]:
        """Capture response information."""
        response_info = {
            "type": "response",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": response.status_code,
            "status_message": self._get_status_message(response.status_code),
            "duration_ms": duration_ms,
            "response_time": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add response headers
        if self.config.include_headers:
            headers = dict(response.headers)
            # Mask sensitive headers
            if self.config.mask_sensitive_data:
                headers = self._mask_sensitive_data(headers)
            response_info["headers"] = headers
        
        # Try to capture response body for errors
        if response.status_code >= 400 and self.config.capture_exceptions:
            try:
                # Get response body
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                
                # Restore the body iterator
                response.body_iterator = self._restore_body_iterator(body)
                
                if body:
                    # Limit body size
                    if len(body) > self.config.max_body_size:
                        body = body[:self.config.max_body_size]
                    
                    # Try to parse as JSON
                    try:
                        body_json = json.loads(body.decode('utf-8'))
                        response_info["response_body"] = body_json
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        response_info["response_text"] = body.decode('utf-8', errors='ignore')[:1000]
                    
                    response_info["response_size"] = len(body)
            except Exception as e:
                response_info["response_error"] = str(e)
        
        # Check for high latency
        if duration_ms > self.config.latency_threshold_ms:
            response_info["high_latency"] = True
            response_info["latency_threshold_ms"] = self.config.latency_threshold_ms
        
        return response_info
    
    async def _restore_body_iterator(self, body: bytes):
        """Restore response body iterator."""
        async def body_iterator():
            yield body
        return body_iterator()
    
    def _capture_exception_info(self, exception: Exception, duration_ms: float) -> Dict[str, Any]:
        """Capture exception information."""
        error_info = {
            "type": "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_type": exception.__class__.__name__,
            "error_message": str(exception),
            "duration_ms": duration_ms,
        }
        
        # Add traceback for unexpected exceptions
        if not isinstance(exception, HTTPException):
            error_info["traceback"] = traceback.format_exc()
        
        # Add HTTP exception details
        if isinstance(exception, HTTPException):
            error_info["status_code"] = exception.status_code
            error_info["detail"] = exception.detail
            error_info["headers"] = dict(exception.headers) if hasattr(exception, 'headers') else {}
        
        return error_info
    
    def _mask_sensitive_data(self, data: Any) -> Any:
        """Mask sensitive data in logs."""
        if isinstance(data, dict):
            masked = {}
            for key, value in data.items():
                key_lower = str(key).lower()
                # Check if key contains sensitive patterns
                if any(sensitive in key_lower for sensitive in self.config.sensitive_fields):
                    masked[key] = "***MASKED***"
                else:
                    masked[key] = self._mask_sensitive_data(value)
            return masked
        elif isinstance(data, list):
            return [self._mask_sensitive_data(item) for item in data]
        elif isinstance(data, str):
            # Check for patterns like tokens, keys, etc.
            sensitive_patterns = [
                r"eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*",  # JWT tokens
                r"sk_[a-zA-Z0-9]{24}",  # Stripe-like keys
                r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{4}",  # Credit cards
                r"[0-9]{3}-[0-9]{2}-[0-9]{4}",  # SSN
            ]
            
            for pattern in sensitive_patterns:
                if re.search(pattern, data):
                    return "***MASKED***"
            
            return data
        else:
            return data
    
    async def _collect_performance_metrics(self, request: Request, response: Response,
                                         duration_ms: float, log_data: Dict[str, Any]) -> None:
        """Collect performance metrics."""
        if not self.config.collect_performance_metrics:
            return
        
        # Prometheus metrics
        endpoint = request.url.path
        method = request.method
        status_code = str(response.status_code)
        
        # Request duration
        self.request_duration.labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
        ).observe(duration_ms / 1000)  # Convert to seconds
        
        # Request count
        self.request_count.labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
        ).inc()
        
        # Request size
        request_size = log_data.get("request_size", 0)
        if request_size:
            self.request_size.labels(
                method=method,
                endpoint=endpoint,
            ).observe(request_size)
        
        # Response size
        response_size = log_data.get("response_size", 0)
        if response_size:
            self.response_size.labels(
                method=method,
                endpoint=endpoint,
                status_code=status_code,
            ).observe(response_size)
    
    async def _handle_anomalies(self, anomalies: List[Dict[str, Any]], 
                              request: Request, response: Response) -> None:
        """Handle detected anomalies."""
        for anomaly in anomalies:
            # Log anomaly
            self.logger.warning(
                "anomaly_detected",
                anomaly_type=anomaly["type"],
                detected_value=anomaly["detected_value"],
                threshold=anomaly["threshold"],
                confidence=anomaly["confidence"],
                path=request.url.path,
                method=request.method,
                user_id=getattr(request.state, 'user_id', None),
                ip_address=request.client.host if request.client else None,
            )
            
            # Log security event for suspicious activity
            if anomaly["type"] == AnomalyType.SUSPICIOUS_ACTIVITY.value:
                log_security_event(
                    event_type="suspicious_activity_detected",
                    severity="high",
                    source_ip=request.client.host if request.client else None,
                    user_id=getattr(request.state, 'user_id', None),
                    details=anomaly,
                )
    
    async def _log_request(self, log_data: Dict[str, Any]) -> None:
        """Log request data."""
        # Determine log level based on status code
        status_code = log_data.get("status_code", 200)
        
        if status_code >= 500:
            log_level = "error"
        elif status_code >= 400:
            log_level = "warning"
        else:
            log_level = "info"
        
        # Prepare log message
        log_message = {
            "event": "http_request",
            "level": log_level,
            "correlation_id": log_data["correlation_id"],
            "trace_id": log_data["trace_id"],
            "method": log_data["method"],
            "path": log_data["path"],
            "status_code": status_code,
            "duration_ms": log_data["duration_ms"],
            "user_id": log_data["user_context"].get("user_id"),
            "ip_address": log_data["user_context"].get("ip_address"),
            "user_agent": log_data["user_context"].get("user_agent"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add query params for debugging
        if log_level in ["error", "warning"]:
            log_message["query_params"] = log_data.get("query_params", {})
        
        # Add anomalies if present
        if "anomalies" in log_data:
            log_message["anomalies"] = log_data["anomalies"]
        
        # Add audit event if present
        if "audit_event" in log_data:
            log_message["audit_event"] = log_data["audit_event"]
        
        # Log using appropriate level
        if log_level == "error":
            self.logger.error("http_request", **log_message)
        elif log_level == "warning":
            self.logger.warning("http_request", **log_message)
        else:
            self.logger.info("http_request", **log_message)
    
    async def _log_error(self, log_data: Dict[str, Any]) -> None:
        """Log error data."""
        error_message = {
            "event": "http_error",
            "level": "error",
            "correlation_id": log_data["correlation_id"],
            "trace_id": log_data["trace_id"],
            "method": log_data["method"],
            "path": log_data["path"],
            "error_type": log_data.get("error_type"),
            "error_message": log_data.get("error_message"),
            "status_code": log_data.get("status_code"),
            "duration_ms": log_data["duration_ms"],
            "user_id": log_data["user_context"].get("user_id"),
            "ip_address": log_data["user_context"].get("ip_address"),
            "user_agent": log_data["user_context"].get("user_agent"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traceback": log_data.get("traceback"),
        }
        
        self.logger.error("http_error", **error_message)
        
        # Track error in Prometheus
        endpoint = log_data["path"]
        method = log_data["method"]
        status_code = str(log_data.get("status_code", 500))
        
        self.error_counter.labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
        ).inc()
    
    def _track_error(self, request: Request, response: Response, log_data: Dict[str, Any]) -> None:
        """Track error for monitoring."""
        endpoint = request.url.path
        method = request.method
        status_code = str(response.status_code)
        
        self.error_counter.labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
        ).inc()
    
    def _get_status_message(self, status_code: int) -> str:
        """Get status message from status code."""
        return status.HTTP_STATUS_CODES.get(status_code, "Unknown")


# FastAPI dependency for accessing logging context
async def get_logging_context(request: Request) -> Dict[str, Any]:
    """Get logging context from request."""
    return {
        "correlation_id": getattr(request.state, 'correlation_id', None),
        "trace_id": getattr(request.state, 'trace_id', None),
        "user_id": getattr(request.state, 'user_id', None),
        "start_time": getattr(request.state, 'start_time', None),
    }


# Decorator for manual logging
def log_endpoint(
    level: LogLevel = LogLevel.INFO,
    include_request: bool = True,
    include_response: bool = True,
    custom_fields: Optional[Dict[str, Any]] = None,
):
    """Decorator to add custom logging to endpoint."""
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Find request in args or kwargs
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            
            if not request:
                request = kwargs.get('request')
            
            start_time = time.time()
            
            try:
                # Call original function
                result = await func(*args, **kwargs)
                
                duration = time.time() - start_time
                duration_ms = duration * 1000
                
                # Extract response if it's a Response object
                response = result if isinstance(result, Response) else None
                
                # Get logging context
                context = await get_logging_context(request) if request else {}
                
                # Create log data
                log_data = {
                    "event": "endpoint_execution",
                    "level": level.value,
                    "function": func.__name__,
                    "module": func.__module__,
                    "duration_ms": duration_ms,
                    **context,
                }
                
                if custom_fields:
                    log_data.update(custom_fields)
                
                if include_request and request:
                    log_data.update({
                        "method": request.method,
                        "path": request.url.path,
                        "query_params": dict(request.query_params),
                    })
                
                if include_response and response:
                    log_data.update({
                        "status_code": response.status_code,
                        "response_size": len(response.body) if hasattr(response, 'body') else None,
                    })
                
                # Log using appropriate level
                logger = get_logger(func.__module__)
                if level == LogLevel.ERROR:
                    logger.error("endpoint_execution", **log_data)
                elif level == LogLevel.WARNING:
                    logger.warning("endpoint_execution", **log_data)
                elif level == LogLevel.INFO:
                    logger.info("endpoint_execution", **log_data)
                else:
                    logger.debug("endpoint_execution", **log_data)
                
                return result
                
            except Exception as e:
                duration = time.time() - start_time
                duration_ms = duration * 1000
                
                # Log error
                error_data = {
                    "event": "endpoint_error",
                    "level": LogLevel.ERROR.value,
                    "function": func.__name__,
                    "module": func.__module__,
                    "error_type": e.__class__.__name__,
                    "error_message": str(e),
                    "duration_ms": duration_ms,
                    "traceback": traceback.format_exc(),
                }
                
                if custom_fields:
                    error_data.update(custom_fields)
                
                logger = get_logger(func.__module__)
                logger.error("endpoint_error", **error_data)
                
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Similar logic for sync functions
            # ... (omitted for brevity)
            return func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


# Helper function to add middleware to FastAPI app
def setup_logging_middleware(
    app: FastAPI,
    config: Optional[LogConfig] = None,
    security_manager: Optional[SecurityManager] = None,
) -> LoggingMiddleware:
    """Setup logging middleware for FastAPI app."""
    middleware = LoggingMiddleware(app, config, security_manager)
    
    # Add middleware to app
    app.add_middleware(LoggingMiddleware, config=config, security_manager=security_manager)
    
    # Add metrics endpoint
    @app.get("/metrics")
    async def metrics_endpoint():
        """Prometheus metrics endpoint."""
        return Response(
            content=generate_latest(),
            media_type="text/plain",
        )
    
    # Add health endpoint
    @app.get("/health")
    async def health_endpoint():
        """Health check endpoint."""
        return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}
    
    return middleware


# Export public API
__all__ = [
    # Main classes
    "LoggingMiddleware",
    "LogConfig",
    "PerformanceBreakdown",
    "BusinessMetric",
    "AuditEvent",
    "AnomalyDetectionRule",
    
    # Enums
    "LogLevel",
    "LogSamplingStrategy",
    "AnomalyType",
    "ComplianceStandard",
    
    # Components
    "AnomalyDetector",
    "BusinessMetricsCollector",
    "ComplianceAuditor",
    "LogSampler",
    "ProbabilisticSampler",
    "AdaptiveSampler",
    "SmartSampler",
    
    # FastAPI dependencies
    "get_logging_context",
    
    # Decorators
    "log_endpoint",
    
    # Setup function
    "setup_logging_middleware",
]