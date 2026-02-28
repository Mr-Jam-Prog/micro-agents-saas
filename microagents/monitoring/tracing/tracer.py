"""
OpenTelemetry integration for MicroAgents Platform.
Distributed tracing, metrics collection, and performance instrumentation.
"""

import asyncio
import contextlib
import contextvars
import functools
import inspect
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TypeVar, Union

import opentelemetry
from opentelemetry import metrics, trace
from opentelemetry.baggage import get_baggage, set_baggage
from opentelemetry.context import Context, attach, detach
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.zipkin.json import ZipkinExporter
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.dbapi import DatabaseApiInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient, GrpcInstrumentorServer
from opentelemetry.instrumentation.kafka import KafkaInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.metrics import Counter, Histogram, Meter, ObservableCounter, ObservableGauge
from opentelemetry.propagate import extract, inject
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.trace import (
    Link,
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
    set_span_in_context,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.trace.span import INVALID_SPAN_ID
from pydantic import BaseModel

# Type variables
T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

# Global tracer provider
_tracer_provider: Optional[TracerProvider] = None
_meter_provider: Optional[MeterProvider] = None

# Context variable for current span
_current_span: contextvars.ContextVar[Optional[Span]] = contextvars.ContextVar("current_span", default=None)

# Configuration
class TraceSamplingStrategy(str, Enum):
    """Sampling strategies for distributed tracing."""
    
    ALWAYS_ON = "always_on"
    ALWAYS_OFF = "always_off"
    PROBABILISTIC = "probabilistic"
    RATE_LIMITING = "rate_limiting"
    PARENT_BASED = "parent_based"
    REMOTE_CONTROLLED = "remote_controlled"


class ExportFormat(str, Enum):
    """Export formats for telemetry data."""
    
    OTLP_GRPC = "otlp_grpc"
    OTLP_HTTP = "otlp_http"
    ZIPKIN = "zipkin"
    JAEGER = "jaeger"
    CONSOLE = "console"
    CLOUDWATCH = "cloudwatch"
    DATADOG = "datadog"


class TraceConfig(BaseModel):
    """Configuration for distributed tracing."""
    
    enabled: bool = True
    service_name: str = "microagents-platform"
    service_version: str = "1.0.0"
    environment: str = os.getenv("ENVIRONMENT", "development")
    
    # Sampling configuration
    sampling_strategy: TraceSamplingStrategy = TraceSamplingStrategy.PARENT_BASED
    sampling_rate: float = 0.1  # 10% for probabilistic sampling
    max_samples_per_second: int = 100  # For rate limiting
    
    # Export configuration
    export_format: ExportFormat = ExportFormat.OTLP_GRPC
    export_endpoint: Optional[str] = os.getenv("OTLP_ENDPOINT", "http://localhost:4317")
    export_timeout: int = 30
    export_batch_size: int = 512
    export_schedule_delay: int = 5000  # ms
    
    # Instrumentation configuration
    instrument_http: bool = True
    instrument_db: bool = True
    instrument_redis: bool = True
    instrument_kafka: bool = True
    instrument_grpc: bool = True
    instrument_boto: bool = True
    instrument_aiohttp: bool = True
    
    # Performance configuration
    collect_metrics: bool = True
    collect_logs: bool = True
    slow_query_threshold_ms: int = 1000
    error_collection_enabled: bool = True
    
    # Resource attributes
    resource_attributes: Dict[str, str] = {}
    
    class Config:
        frozen = True


class AgentExecutionSpan(BaseModel):
    """Span data for agent execution tracing."""
    
    agent_id: str
    agent_type: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = {}


class BusinessValueSpan(BaseModel):
    """Span data for business value calculations."""
    
    value_type: str  # "cost_savings", "revenue", "efficiency", "compliance"
    amount: float
    currency: str = "USD"
    confidence: float = 1.0
    factors: List[str] = []
    metadata: Dict[str, Any] = {}


class PerformanceMetrics:
    """Performance metrics collector."""
    
    def __init__(self, meter: Meter):
        self.meter = meter
        
        # Agent execution metrics
        self.agent_execution_duration = meter.create_histogram(
            name="agent.execution.duration",
            description="Duration of agent execution in milliseconds",
            unit="ms",
        )
        
        self.agent_execution_count = meter.create_counter(
            name="agent.execution.count",
            description="Count of agent executions",
            unit="1",
        )
        
        self.agent_success_count = meter.create_counter(
            name="agent.execution.success.count",
            description="Count of successful agent executions",
            unit="1",
        )
        
        self.agent_error_count = meter.create_counter(
            name="agent.execution.error.count",
            description="Count of failed agent executions",
            unit="1",
        )
        
        # Business value metrics
        self.cost_savings = meter.create_counter(
            name="business.cost_savings",
            description="Total cost savings generated",
            unit="USD",
        )
        
        self.revenue_generated = meter.create_counter(
            name="business.revenue_generated",
            description="Total revenue generated",
            unit="USD",
        )
        
        # API call metrics
        self.api_call_duration = meter.create_histogram(
            name="api.call.duration",
            description="Duration of API calls in milliseconds",
            unit="ms",
        )
        
        self.api_call_count = meter.create_counter(
            name="api.call.count",
            description="Count of API calls",
            unit="1",
        )
        
        # Database metrics
        self.db_query_duration = meter.create_histogram(
            name="db.query.duration",
            description="Duration of database queries in milliseconds",
            unit="ms",
        )
        
        self.db_query_count = meter.create_counter(
            name="db.query.count",
            description="Count of database queries",
            unit="1",
        )
        
        # Cache metrics
        self.cache_hit_count = meter.create_counter(
            name="cache.hit.count",
            description="Count of cache hits",
            unit="1",
        )
        
        self.cache_miss_count = meter.create_counter(
            name="cache.miss.count",
            description="Count of cache misses",
            unit="1",
        )
        
        # Message queue metrics
        self.mq_message_processed = meter.create_counter(
            name="mq.message.processed",
            description="Count of messages processed",
            unit="1",
        )
        
        self.mq_processing_duration = meter.create_histogram(
            name="mq.processing.duration",
            description="Duration of message processing in milliseconds",
            unit="ms",
        )
        
        # System metrics
        self.active_agents = meter.create_observable_gauge(
            name="system.active_agents",
            description="Number of active agents",
            unit="1",
            callbacks=[self._get_active_agents],
        )
        
        self.memory_usage = meter.create_observable_gauge(
            name="system.memory.usage",
            description="Memory usage in bytes",
            unit="bytes",
            callbacks=[self._get_memory_usage],
        )
        
        self.cpu_usage = meter.create_observable_gauge(
            name="system.cpu.usage",
            description="CPU usage percentage",
            unit="percent",
            callbacks=[self._get_cpu_usage],
        )
    
    def _get_active_agents(self) -> List[metrics.Observation]:
        """Get active agents count."""
        # This would be implemented to get actual agent count
        return [metrics.Observation(100, {"agent.type": "total"})]
    
    def _get_memory_usage(self) -> List[metrics.Observation]:
        """Get memory usage."""
        import psutil
        process = psutil.Process()
        memory = process.memory_info().rss
        return [metrics.Observation(memory, {})]
    
    def _get_cpu_usage(self) -> List[metrics.Observation]:
        """Get CPU usage."""
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        return [metrics.Observation(cpu_percent, {})]
    
    def record_agent_execution(
        self,
        agent_id: str,
        agent_type: str,
        duration_ms: float,
        success: bool,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record agent execution metrics."""
        attrs = {
            "agent.id": agent_id,
            "agent.type": agent_type,
            "success": str(success),
        }
        
        if attributes:
            attrs.update(attributes)
        
        self.agent_execution_duration.record(duration_ms, attrs)
        self.agent_execution_count.add(1, attrs)
        
        if success:
            self.agent_success_count.add(1, attrs)
        else:
            self.agent_error_count.add(1, attrs)
    
    def record_business_value(
        self,
        value_type: str,
        amount: float,
        currency: str = "USD",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record business value metrics."""
        attrs = {
            "value.type": value_type,
            "currency": currency,
        }
        
        if attributes:
            attrs.update(attributes)
        
        if value_type == "cost_savings":
            self.cost_savings.add(amount, attrs)
        elif value_type == "revenue":
            self.revenue_generated.add(amount, attrs)


class CustomSpanProcessor(SpanProcessor):
    """Custom span processor for additional processing."""
    
    def __init__(self, config: TraceConfig):
        self.config = config
        self.slow_spans: List[Tuple[str, float]] = []
        
    def on_start(self, span: Span, parent_context: Optional[Context] = None) -> None:
        """Called when a span starts."""
        # Add custom attributes
        span.set_attribute("service.environment", self.config.environment)
        span.set_attribute("service.version", self.config.service_version)
        
        # Add baggage items as attributes
        baggage = get_baggage()
        if baggage:
            for key, value in baggage.get_all():
                span.set_attribute(f"baggage.{key}", value)
    
    def on_end(self, span: ReadableSpan) -> None:
        """Called when a span ends."""
        # Detect slow spans
        duration_ms = span.end_time - span.start_time
        if duration_ms > self.config.slow_query_threshold_ms:
            self.slow_spans.append((span.name, duration_ms))
            
            # Log slow spans
            logging.warning(
                f"Slow span detected: {span.name} took {duration_ms:.2f}ms",
                extra={
                    "span_name": span.name,
                    "duration_ms": duration_ms,
                    "span_id": span.context.span_id,
                    "trace_id": span.context.trace_id,
                },
            )
        
        # Export error spans if error collection is enabled
        if self.config.error_collection_enabled and span.status.status_code == StatusCode.ERROR:
            self._export_error_span(span)
    
    def _export_error_span(self, span: ReadableSpan) -> None:
        """Export error span details for analysis."""
        error_details = {
            "span_name": span.name,
            "trace_id": span.context.trace_id,
            "span_id": span.context.span_id,
            "error_message": span.status.description,
            "attributes": dict(span.attributes),
            "start_time": span.start_time,
            "end_time": span.end_time,
            "duration_ms": span.end_time - span.start_time,
        }
        
        # In production, this would send to error tracking service
        logging.error(f"Error span: {error_details}")
    
    def shutdown(self) -> None:
        """Shutdown the processor."""
        self.slow_spans.clear()
    
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush pending spans."""
        return True


class TraceManager:
    """Main manager for distributed tracing."""
    
    def __init__(self, config: Optional[TraceConfig] = None):
        self.config = config or TraceConfig()
        self.tracer_provider: Optional[TracerProvider] = None
        self.meter_provider: Optional[MeterProvider] = None
        self.performance_metrics: Optional[PerformanceMetrics] = None
        self.custom_processor: Optional[CustomSpanProcessor] = None
        
        # Initialize if enabled
        if self.config.enabled:
            self._initialize()
    
    def _initialize(self) -> None:
        """Initialize OpenTelemetry with configured settings."""
        # Create resource
        resource_attributes = {
            ResourceAttributes.SERVICE_NAME: self.config.service_name,
            ResourceAttributes.SERVICE_VERSION: self.config.service_version,
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: self.config.environment,
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.language": "python",
            "telemetry.sdk.version": opentelemetry.__version__,
        }
        
        # Add custom resource attributes
        resource_attributes.update(self.config.resource_attributes)
        
        resource = Resource(attributes=resource_attributes)
        
        # Create tracer provider
        self.tracer_provider = TracerProvider(
            resource=resource,
            sampler=self._create_sampler(),
        )
        
        # Create custom span processor
        self.custom_processor = CustomSpanProcessor(self.config)
        self.tracer_provider.add_span_processor(self.custom_processor)
        
        # Add export processor
        exporter = self._create_exporter()
        if exporter:
            span_processor = BatchSpanProcessor(
                exporter,
                max_export_batch_size=self.config.export_batch_size,
                schedule_delay_millis=self.config.export_schedule_delay,
                export_timeout_millis=self.config.export_timeout * 1000,
            )
            self.tracer_provider.add_span_processor(span_processor)
        
        # Set global tracer provider
        trace.set_tracer_provider(self.tracer_provider)
        
        # Initialize metrics if enabled
        if self.config.collect_metrics:
            self._initialize_metrics()
        
        # Auto-instrument libraries if enabled
        self._instrument_libraries()
        
        global _tracer_provider
        _tracer_provider = self.tracer_provider
    
    def _create_sampler(self) -> Any:
        """Create sampler based on configuration."""
        from opentelemetry.sdk.trace.sampling import (
            ALWAYS_OFF,
            ALWAYS_ON,
            ParentBased,
            TraceIdRatioBased,
        )
        
        if self.config.sampling_strategy == TraceSamplingStrategy.ALWAYS_ON:
            return ALWAYS_ON
        elif self.config.sampling_strategy == TraceSamplingStrategy.ALWAYS_OFF:
            return ALWAYS_OFF
        elif self.config.sampling_strategy == TraceSamplingStrategy.PROBABILISTIC:
            return TraceIdRatioBased(self.config.sampling_rate)
        elif self.config.sampling_strategy == TraceSamplingStrategy.PARENT_BASED:
            return ParentBased(
                root=TraceIdRatioBased(self.config.sampling_rate),
                remote_parent_sampled=TraceIdRatioBased(self.config.sampling_rate),
                remote_parent_not_sampled=ALWAYS_OFF,
                local_parent_sampled=TraceIdRatioBased(self.config.sampling_rate),
                local_parent_not_sampled=ALWAYS_OFF,
            )
        else:
            # Default to parent-based
            return ParentBased(TraceIdRatioBased(self.config.sampling_rate))
    
    def _create_exporter(self) -> Optional[Any]:
        """Create exporter based on configuration."""
        if self.config.export_format == ExportFormat.OTLP_GRPC:
            if self.config.export_endpoint:
                return OTLPSpanExporter(
                    endpoint=self.config.export_endpoint,
                    insecure=True,  # Use SSL in production
                )
        
        elif self.config.export_format == ExportFormat.ZIPKIN:
            if self.config.export_endpoint:
                return ZipkinExporter(
                    endpoint=self.config.export_endpoint,
                )
        
        elif self.config.export_format == ExportFormat.CONSOLE:
            return ConsoleSpanExporter()
        
        # No exporter if endpoint not configured
        return None
    
    def _initialize_metrics(self) -> None:
        """Initialize metrics collection."""
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        
        # Create meter provider
        self.meter_provider = MeterProvider()
        
        # Create metric exporter
        if self.config.export_format == ExportFormat.OTLP_GRPC and self.config.export_endpoint:
            metric_exporter = OTLPMetricExporter(
                endpoint=self.config.export_endpoint,
                insecure=True,
            )
            metric_reader = PeriodicExportingMetricReader(
                exporter=metric_exporter,
                export_interval_millis=self.config.export_schedule_delay,
            )
            self.meter_provider._metric_readers.append(metric_reader)
        
        # Set global meter provider
        metrics.set_meter_provider(self.meter_provider)
        
        # Create performance metrics
        meter = self.meter_provider.get_meter("microagents.metrics")
        self.performance_metrics = PerformanceMetrics(meter)
        
        global _meter_provider
        _meter_provider = self.meter_provider
    
    def _instrument_libraries(self) -> None:
        """Auto-instrument libraries based on configuration."""
        try:
            if self.config.instrument_http:
                RequestsInstrumentor().instrument()
            
            if self.config.instrument_db:
                DatabaseApiInstrumentor().instrument()
            
            if self.config.instrument_redis:
                RedisInstrumentor().instrument()
            
            if self.config.instrument_kafka:
                KafkaInstrumentor().instrument()
            
            if self.config.instrument_grpc:
                GrpcInstrumentorClient().instrument()
                GrpcInstrumentorServer().instrument()
            
            if self.config.instrument_boto:
                BotocoreInstrumentor().instrument()
            
            if self.config.instrument_aiohttp:
                AioHttpClientInstrumentor().instrument()
            
            # Instrument asyncpg for PostgreSQL
            AsyncPGInstrumentor().instrument()
            
            # Instrument FastAPI if installed
            try:
                import fastapi
                FastAPIInstrumentor().instrument()
            except ImportError:
                pass
                
        except Exception as e:
            logging.warning(f"Failed to instrument library: {e}")
    
    def get_tracer(self, name: str = "microagents") -> Tracer:
        """Get a tracer instance."""
        if not self.config.enabled or not self.tracer_provider:
            return trace.get_tracer_provider().get_tracer(name)
        
        return self.tracer_provider.get_tracer(name)
    
    def get_meter(self, name: str = "microagents") -> Optional[Meter]:
        """Get a meter instance for metrics."""
        if not self.config.collect_metrics or not self.meter_provider:
            return None
        
        return self.meter_provider.get_meter(name)
    
    @contextmanager
    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
        links: Optional[List[Link]] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> Generator[Span, None, None]:
        """Start a new span as a context manager."""
        if not self.config.enabled:
            yield NonRecordingSpan(SpanContext(INVALID_SPAN_ID, INVALID_SPAN_ID, is_remote=False))
            return
        
        tracer = self.get_tracer()
        span = tracer.start_span(
            name=name,
            kind=kind,
            attributes=attributes,
            links=links,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        )
        
        # Store in context variable
        token = _current_span.set(span)
        
        try:
            with trace.use_span(span, end_on_exit=True):
                yield span
        finally:
            _current_span.reset(token)
    
    @asynccontextmanager
    async def start_async_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
        links: Optional[List[Link]] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> Generator[Span, None, None]:
        """Start a new async span as a context manager."""
        if not self.config.enabled:
            yield NonRecordingSpan(SpanContext(INVALID_SPAN_ID, INVALID_SPAN_ID, is_remote=False))
            return
        
        tracer = self.get_tracer()
        span = tracer.start_span(
            name=name,
            kind=kind,
            attributes=attributes,
            links=links,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        )
        
        # Store in context variable
        token = _current_span.set(span)
        
        try:
            with trace.use_span(span, end_on_exit=True):
                yield span
        finally:
            _current_span.reset(token)
    
    def trace_agent_execution(
        self,
        agent_id: str,
        agent_type: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Callable:
        """Decorator to trace agent execution."""
        def decorator(func: F) -> F:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                
                span_attrs = {
                    "agent.id": agent_id,
                    "agent.type": agent_type,
                    "span.type": "agent_execution",
                }
                
                if attributes:
                    span_attrs.update(attributes)
                
                with self.start_span(
                    name=f"agent.{agent_type}.execute",
                    attributes=span_attrs,
                    kind=SpanKind.INTERNAL,
                ) as span:
                    try:
                        result = func(*args, **kwargs)
                        duration = time.time() - start_time
                        
                        # Record metrics
                        if self.performance_metrics:
                            self.performance_metrics.record_agent_execution(
                                agent_id=agent_id,
                                agent_type=agent_type,
                                duration_ms=duration * 1000,
                                success=True,
                                attributes=attributes,
                            )
                        
                        # Add agent execution data to span
                        agent_data = AgentExecutionSpan(
                            agent_id=agent_id,
                            agent_type=agent_type,
                            success=True,
                        )
                        span.set_attribute("agent.execution.data", agent_data.json())
                        span.set_attribute("agent.execution.duration_ms", duration * 1000)
                        
                        return result
                    
                    except Exception as e:
                        duration = time.time() - start_time
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        
                        # Record error metrics
                        if self.performance_metrics:
                            self.performance_metrics.record_agent_execution(
                                agent_id=agent_id,
                                agent_type=agent_type,
                                duration_ms=duration * 1000,
                                success=False,
                                attributes=attributes,
                            )
                        
                        # Add error to span
                        agent_data = AgentExecutionSpan(
                            agent_id=agent_id,
                            agent_type=agent_type,
                            success=False,
                            error_message=str(e),
                        )
                        span.set_attribute("agent.execution.data", agent_data.json())
                        span.set_attribute("agent.execution.duration_ms", duration * 1000)
                        
                        raise
            
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                
                span_attrs = {
                    "agent.id": agent_id,
                    "agent.type": agent_type,
                    "span.type": "agent_execution",
                }
                
                if attributes:
                    span_attrs.update(attributes)
                
                async with self.start_async_span(
                    name=f"agent.{agent_type}.execute",
                    attributes=span_attrs,
                    kind=SpanKind.INTERNAL,
                ) as span:
                    try:
                        result = await func(*args, **kwargs)
                        duration = time.time() - start_time
                        
                        # Record metrics
                        if self.performance_metrics:
                            self.performance_metrics.record_agent_execution(
                                agent_id=agent_id,
                                agent_type=agent_type,
                                duration_ms=duration * 1000,
                                success=True,
                                attributes=attributes,
                            )
                        
                        # Add agent execution data to span
                        agent_data = AgentExecutionSpan(
                            agent_id=agent_id,
                            agent_type=agent_type,
                            success=True,
                        )
                        span.set_attribute("agent.execution.data", agent_data.json())
                        span.set_attribute("agent.execution.duration_ms", duration * 1000)
                        
                        return result
                    
                    except Exception as e:
                        duration = time.time() - start_time
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        
                        # Record error metrics
                        if self.performance_metrics:
                            self.performance_metrics.record_agent_execution(
                                agent_id=agent_id,
                                agent_type=agent_type,
                                duration_ms=duration * 1000,
                                success=False,
                                attributes=attributes,
                            )
                        
                        # Add error to span
                        agent_data = AgentExecutionSpan(
                            agent_id=agent_id,
                            agent_type=agent_type,
                            success=False,
                            error_message=str(e),
                        )
                        span.set_attribute("agent.execution.data", agent_data.json())
                        span.set_attribute("agent.execution.duration_ms", duration * 1000)
                        
                        raise
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        
        return decorator
    
    def trace_business_value(
        self,
        value_type: str,
        amount: float,
        currency: str = "USD",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Callable:
        """Decorator to trace business value calculations."""
        def decorator(func: F) -> F:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                span_attrs = {
                    "business.value.type": value_type,
                    "business.value.amount": amount,
                    "business.value.currency": currency,
                    "span.type": "business_value",
                }
                
                if attributes:
                    span_attrs.update(attributes)
                
                with self.start_span(
                    name=f"business.{value_type}.calculate",
                    attributes=span_attrs,
                    kind=SpanKind.INTERNAL,
                ) as span:
                    try:
                        result = func(*args, **kwargs)
                        
                        # Record metrics
                        if self.performance_metrics:
                            self.performance_metrics.record_business_value(
                                value_type=value_type,
                                amount=amount,
                                currency=currency,
                                attributes=attributes,
                            )
                        
                        # Add business value data to span
                        business_data = BusinessValueSpan(
                            value_type=value_type,
                            amount=amount,
                            currency=currency,
                        )
                        span.set_attribute("business.value.data", business_data.json())
                        
                        return result
                    
                    except Exception as e:
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        raise
            
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                span_attrs = {
                    "business.value.type": value_type,
                    "business.value.amount": amount,
                    "business.value.currency": currency,
                    "span.type": "business_value",
                }
                
                if attributes:
                    span_attrs.update(attributes)
                
                async with self.start_async_span(
                    name=f"business.{value_type}.calculate",
                    attributes=span_attrs,
                    kind=SpanKind.INTERNAL,
                ) as span:
                    try:
                        result = await func(*args, **kwargs)
                        
                        # Record metrics
                        if self.performance_metrics:
                            self.performance_metrics.record_business_value(
                                value_type=value_type,
                                amount=amount,
                                currency=currency,
                                attributes=attributes,
                            )
                        
                        # Add business value data to span
                        business_data = BusinessValueSpan(
                            value_type=value_type,
                            amount=amount,
                            currency=currency,
                        )
                        span.set_attribute("business.value.data", business_data.json())
                        
                        return result
                    
                    except Exception as e:
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        raise
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        
        return decorator
    
    def trace_external_call(
        self,
        service: str,
        endpoint: str,
        method: str = "GET",
    ) -> Callable:
        """Decorator to trace external API calls."""
        def decorator(func: F) -> F:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                
                span_attrs = {
                    SpanAttributes.HTTP_METHOD: method,
                    SpanAttributes.HTTP_URL: f"{service}/{endpoint}",
                    "span.type": "external_api",
                    "external.service": service,
                    "external.endpoint": endpoint,
                }
                
                with self.start_span(
                    name=f"external.{service}.{endpoint}",
                    attributes=span_attrs,
                    kind=SpanKind.CLIENT,
                ) as span:
                    try:
                        result = func(*args, **kwargs)
                        duration = time.time() - start_time
                        
                        # Record metrics
                        if self.performance_metrics:
                            self.performance_metrics.api_call_count.add(1, span_attrs)
                            self.performance_metrics.api_call_duration.record(duration * 1000, span_attrs)
                        
                        span.set_attribute("http.duration_ms", duration * 1000)
                        
                        return result
                    
                    except Exception as e:
                        duration = time.time() - start_time
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        span.set_attribute("http.duration_ms", duration * 1000)
                        
                        raise
            
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                
                span_attrs = {
                    SpanAttributes.HTTP_METHOD: method,
                    SpanAttributes.HTTP_URL: f"{service}/{endpoint}",
                    "span.type": "external_api",
                    "external.service": service,
                    "external.endpoint": endpoint,
                }
                
                async with self.start_async_span(
                    name=f"external.{service}.{endpoint}",
                    attributes=span_attrs,
                    kind=SpanKind.CLIENT,
                ) as span:
                    try:
                        result = await func(*args, **kwargs)
                        duration = time.time() - start_time
                        
                        # Record metrics
                        if self.performance_metrics:
                            self.performance_metrics.api_call_count.add(1, span_attrs)
                            self.performance_metrics.api_call_duration.record(duration * 1000, span_attrs)
                        
                        span.set_attribute("http.duration_ms", duration * 1000)
                        
                        return result
                    
                    except Exception as e:
                        duration = time.time() - start_time
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        span.set_attribute("http.duration_ms", duration * 1000)
                        
                        raise
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        
        return decorator
    
    def inject_context(self, carrier: Dict[str, str]) -> None:
        """Inject trace context into carrier for propagation."""
        if not self.config.enabled:
            return
        
        propagator = CompositePropagator([
            TraceContextTextMapPropagator(),
        ])
        
        current_span = _current_span.get()
        if current_span:
            context = set_span_in_context(current_span)
            propagator.inject(carrier, context)
    
    def extract_context(self, carrier: Dict[str, str]) -> Optional[Context]:
        """Extract trace context from carrier."""
        if not self.config.enabled:
            return None
        
        propagator = CompositePropagator([
            TraceContextTextMapPropagator(),
        ])
        
        return propagator.extract(carrier)
    
    def get_current_span(self) -> Optional[Span]:
        """Get the current active span."""
        return _current_span.get()
    
    def set_baggage(self, key: str, value: str) -> None:
        """Set baggage item for propagation."""
        if not self.config.enabled:
            return
        
        current_context = Context.current()
        new_context = set_baggage(key, value, context=current_context)
        attach(new_context)
    
    def get_baggage(self, key: str) -> Optional[str]:
        """Get baggage item."""
        if not self.config.enabled:
            return None
        
        baggage = get_baggage()
        return baggage.get_entry(key)
    
    def shutdown(self) -> None:
        """Shutdown the trace manager."""
        if self.tracer_provider:
            self.tracer_provider.shutdown()
        
        if self.meter_provider:
            self.meter_provider.shutdown()
        
        if self.custom_processor:
            self.custom_processor.shutdown()


# Global trace manager instance
_trace_manager: Optional[TraceManager] = None


def get_trace_manager(config: Optional[TraceConfig] = None) -> TraceManager:
    """Get or create the global trace manager."""
    global _trace_manager
    
    if _trace_manager is None:
        _trace_manager = TraceManager(config)
    
    return _trace_manager


def get_tracer(name: str = "microagents") -> Tracer:
    """Get a tracer from the global trace manager."""
    manager = get_trace_manager()
    return manager.get_tracer(name)


def get_current_span() -> Optional[Span]:
    """Get the current active span."""
    return _current_span.get()


@contextmanager
def trace_span(
    name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
    links: Optional[List[Link]] = None,
) -> Generator[Span, None, None]:
    """Convenience function to start a trace span."""
    manager = get_trace_manager()
    
    with manager.start_span(
        name=name,
        kind=kind,
        attributes=attributes,
        links=links,
    ) as span:
        yield span


@asynccontextmanager
async def trace_async_span(
    name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
    links: Optional[List[Link]] = None,
) -> Generator[Span, None, None]:
    """Convenience function to start an async trace span."""
    manager = get_trace_manager()
    
    async with manager.start_async_span(
        name=name,
        kind=kind,
        attributes=attributes,
        links=links,
    ) as span:
        yield span


def trace_agent(
    agent_id: str,
    agent_type: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> Callable:
    """Convenience decorator to trace agent execution."""
    manager = get_trace_manager()
    return manager.trace_agent_execution(agent_id, agent_type, attributes)


def trace_business(
    value_type: str,
    amount: float,
    currency: str = "USD",
    attributes: Optional[Dict[str, Any]] = None,
) -> Callable:
    """Convenience decorator to trace business value."""
    manager = get_trace_manager()
    return manager.trace_business_value(value_type, amount, currency, attributes)


def trace_external(
    service: str,
    endpoint: str,
    method: str = "GET",
) -> Callable:
    """Convenience decorator to trace external calls."""
    manager = get_trace_manager()
    return manager.trace_external_call(service, endpoint, method)


# Initialize trace manager on module import
# Can be overridden by calling get_trace_manager() with custom config
get_trace_manager()

# Export public API
__all__ = [
    "get_trace_manager",
    "get_tracer",
    "get_current_span",
    "trace_span",
    "trace_async_span",
    "trace_agent",
    "trace_business",
    "trace_external",
    "TraceConfig",
    "TraceSamplingStrategy",
    "ExportFormat",
    "AgentExecutionSpan",
    "BusinessValueSpan",
    "PerformanceMetrics",
    "TraceManager",
    "inject_context",
    "extract_context",
    "set_baggage",
    "get_baggage",
]