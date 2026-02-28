"""
FastAPI dependencies for MicroAgents Platform.
Database sessions, authentication, rate limiting, and service clients.
"""

import asyncio
import functools
import inspect
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Type, TypeVar, Union

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery, HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from microagents.api.models.schemas import (
    ErrorResponse,
    TenantID,
    UserID,
    UserRole,
    ValidationErrorResponse,
)
from microagents.monitoring.logging.setup import get_logger
from microagents.monitoring.tracing.tracer import get_current_span, get_trace_manager
from microagents.utils.cache.manager import CacheManager, get_cache_manager
from microagents.utils.config.settings import Settings, get_settings
from microagents.utils.security.utils import (
    CORSManager,
    JWTManager,
    RateLimiter,
    SecurityManager,
    get_security_manager,
)

# Type variables
T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

# Context variables for request-scoped data
_current_user_id: ContextVar[Optional[UserID]] = ContextVar("current_user_id", default=None)
_current_tenant_id: ContextVar[Optional[TenantID]] = ContextVar("current_tenant_id", default=None)
_current_user_role: ContextVar[Optional[UserRole]] = ContextVar("current_user_role", default=None)
_request_start_time: ContextVar[Optional[float]] = ContextVar("request_start_time", default=None)

# Application state
class AppState(BaseModel):
    """Application state shared across dependencies."""
    
    settings: Settings
    security_manager: SecurityManager
    cache_manager: CacheManager
    database_engine: Any
    redis_client: Optional[Any] = None
    feature_flags: Dict[str, bool]
    service_clients: Dict[str, Any]
    
    model_config = {"arbitrary_types_allowed": True}


# ==================== DATABASE DEPENDENCIES ====================

class DatabaseManager:
    """Database connection and session management."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = None
        self.async_session_factory = None
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize database engine and session factory."""
        # Create async engine
        self.engine = create_async_engine(
            self.settings.DATABASE_URL,
            echo=self.settings.DEBUG,
            pool_size=self.settings.DATABASE_POOL_SIZE,
            max_overflow=self.settings.DATABASE_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        
        # Create async session factory
        self.async_session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    
    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get a database session with automatic cleanup.
        
        Yields:
            AsyncSession: Database session
            
        Raises:
            HTTPException: If database connection fails
        """
        session = self.async_session_factory()
        
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error: {str(e)}",
            )
        finally:
            await session.close()
    
    async def health_check(self) -> Dict[str, Any]:
        """Check database health."""
        try:
            async with self.get_session() as session:
                # Simple query to check connection
                result = await session.execute("SELECT 1")
                return {
                    "status": "healthy",
                    "connection": True,
                    "query_time_ms": 0,  # Would measure actual time
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "connection": False,
                "error": str(e),
            }


# ==================== AUTHENTICATION DEPENDENCIES ====================

class AuthManager:
    """Authentication and authorization management."""
    
    def __init__(self, security_manager: SecurityManager):
        self.security_manager = security_manager
        self.jwt_manager = security_manager.jwt_manager
        self.http_bearer = HTTPBearer(auto_error=False)
        
        # Multiple authentication methods
        self.api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
        self.api_key_query = APIKeyQuery(name="api_key", auto_error=False)
        self.api_key_cookie = APIKeyCookie(name="session_token", auto_error=False)
    
    async def authenticate(
        self,
        credentials: Optional[HTTPAuthorizationCredentials] = Security(HTTPBearer(auto_error=False)),
        api_key_header: Optional[str] = Security(APIKeyHeader(name="X-API-Key", auto_error=False)),
        api_key_query: Optional[str] = Security(APIKeyQuery(name="api_key", auto_error=False)),
        api_key_cookie: Optional[str] = Security(APIKeyCookie(name="session_token", auto_error=False)),
    ) -> Dict[str, Any]:
        """
        Authenticate user using multiple methods (Bearer, API Key, Cookie).
        
        Args:
            credentials: HTTP Bearer token
            api_key_header: API key from header
            api_key_query: API key from query parameter
            api_key_cookie: API key from cookie
            
        Returns:
            User authentication data
            
        Raises:
            HTTPException: If authentication fails
        """
        # Try all authentication methods
        auth_methods = [
            ("bearer", credentials),
            ("api_key_header", api_key_header),
            ("api_key_query", api_key_query),
            ("api_key_cookie", api_key_cookie),
        ]
        
        for method_name, auth_data in auth_methods:
            try:
                if auth_data:
                    user_data = await self._authenticate_method(method_name, auth_data)
                    
                    # Audit successful authentication
                    self.security_manager.audit_event(
                        event_type="AUTHENTICATION_SUCCESS",
                        user_id=user_data.get("user_id"),
                        ip_address=None,  # Would get from request
                        details={"method": method_name},
                        severity="INFO",
                    )
                    
                    return user_data
            except Exception as e:
                # Log failed attempt but try next method
                continue
        
        # All methods failed
        self.security_manager.audit_event(
            event_type="AUTHENTICATION_FAILED",
            user_id=None,
            ip_address=None,
            details={"methods_tried": [m[0] for m in auth_methods]},
            severity="WARNING",
        )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    async def _authenticate_method(self, method: str, auth_data: Any) -> Dict[str, Any]:
        """Authenticate using specific method."""
        if method == "bearer":
            # JWT Bearer token
            token = auth_data.credentials
            payload = self.jwt_manager.verify_token(token, token_type="access")
            
            # Additional validation
            if not payload.get("user_id"):
                raise JWTError("Missing user_id in token")
            
            return {
                "user_id": payload["user_id"],
                "tenant_id": payload.get("tenant_id"),
                "role": payload.get("role", UserRole.VIEWER),
                "token_type": "bearer",
                "payload": payload,
            }
        
        elif method.startswith("api_key"):
            # API Key authentication
            api_key = auth_data
            
            # Validate API key (simplified - would check against database)
            # In production, this would validate against stored API keys
            if not api_key or len(api_key) < 32:
                raise ValueError("Invalid API key")
            
            # Parse API key format: key_<user_id>_<hash>
            parts = api_key.split("_")
            if len(parts) != 3:
                raise ValueError("Invalid API key format")
            
            user_id = parts[1]
            
            return {
                "user_id": user_id,
                "tenant_id": None,  # API keys might be tenant-specific
                "role": UserRole.EDITOR,  # Default role for API keys
                "token_type": "api_key",
            }
        
        else:
            raise ValueError(f"Unknown authentication method: {method}")
    
    async def get_current_user(
        self,
        auth_data: Dict[str, Any] = Depends(authenticate),
        db_session: AsyncSession = Depends(get_database_session),
    ) -> Dict[str, Any]:
        """
        Get current authenticated user with database validation.
        
        Args:
            auth_data: Authentication data from authenticate()
            db_session: Database session
            
        Returns:
            Complete user data
            
        Raises:
            HTTPException: If user not found or inactive
        """
        user_id = auth_data.get("user_id")
        
        # In production, this would query the database
        # For now, return the auth data with additional validation
        
        # Check if user is active
        # This would be a database query in production
        user_active = True  # Would check against database
        
        if not user_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
            )
        
        # Set context variables
        _current_user_id.set(user_id)
        _current_user_role.set(auth_data.get("role"))
        _current_tenant_id.set(auth_data.get("tenant_id"))
        
        # Add user data to tracing span
        current_span = get_current_span()
        if current_span:
            current_span.set_attribute("user.id", user_id)
            current_span.set_attribute("user.role", auth_data.get("role"))
        
        return auth_data
    
    async def require_role(
        self,
        required_role: UserRole,
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Dependency to require specific user role.
        
        Args:
            required_role: Minimum required role
            current_user: Current user data
            
        Returns:
            User data if authorized
            
        Raises:
            HTTPException: If user lacks required role
        """
        user_role = current_user.get("role")
        
        # Role hierarchy
        role_hierarchy = {
            UserRole.VIEWER: 0,
            UserRole.EDITOR: 1,
            UserRole.ADMIN: 2,
            UserRole.OWNER: 3,
            UserRole.AUDITOR: 1,
            UserRole.BILLING: 1,
            UserRole.SUPPORT: 1,
        }
        
        user_level = role_hierarchy.get(user_role, 0)
        required_level = role_hierarchy.get(required_role, 0)
        
        if user_level < required_level:
            self.security_manager.audit_event(
                event_type="AUTHORIZATION_FAILED",
                user_id=current_user.get("user_id"),
                ip_address=None,
                details={
                    "required_role": required_role,
                    "user_role": user_role,
                },
                severity="WARNING",
            )
            
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {required_role.value} role or higher",
            )
        
        return current_user
    
    async def require_tenant_access(
        self,
        tenant_id: TenantID,
        current_user: Dict[str, Any] = Depends(get_current_user),
        db_session: AsyncSession = Depends(get_database_session),
    ) -> Dict[str, Any]:
        """
        Dependency to require access to specific tenant.
        
        Args:
            tenant_id: Tenant ID to check
            current_user: Current user data
            db_session: Database session
            
        Returns:
            User data if authorized
            
        Raises:
            HTTPException: If user lacks tenant access
        """
        user_tenant_id = current_user.get("tenant_id")
        
        # Check if user has access to this tenant
        # In production, this would query tenant membership
        if user_tenant_id and str(user_tenant_id) != str(tenant_id):
            # Check if user has cross-tenant access (e.g., admin)
            user_role = current_user.get("role")
            if user_role not in [UserRole.ADMIN, UserRole.OWNER]:
                self.security_manager.audit_event(
                    event_type="TENANT_ACCESS_DENIED",
                    user_id=current_user.get("user_id"),
                    ip_address=None,
                    details={
                        "requested_tenant": tenant_id,
                        "user_tenant": user_tenant_id,
                    },
                    severity="WARNING",
                )
                
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access to this tenant is denied",
                )
        
        # Set current tenant context
        _current_tenant_id.set(tenant_id)
        
        return current_user


# ==================== RATE LIMITING DEPENDENCIES ====================

class RateLimitManager:
    """Rate limiting dependency manager."""
    
    def __init__(self, security_manager: SecurityManager):
        self.security_manager = security_manager
        self.rate_limiter = security_manager.rate_limiter
        
    async def rate_limit(
        self,
        request: Request,
        current_user: Optional[Dict[str, Any]] = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> None:
        """
        Rate limiting dependency.
        
        Args:
            request: FastAPI request
            current_user: Current user (optional)
            settings: Application settings
            
        Raises:
            HTTPException: If rate limit exceeded
        """
        # Skip rate limiting for internal endpoints
        if request.url.path.startswith("/internal/"):
            return
        
        # Get client identifier
        identifier = self._get_client_identifier(request, current_user)
        
        # Check burst limit
        is_burst_limited, burst_metadata = self.rate_limiter.check_burst_limit(identifier)
        
        if is_burst_limited:
            self._handle_rate_limit_exceeded(
                request,
                identifier,
                "burst",
                burst_metadata,
                current_user,
            )
        
        # Check minute limit
        is_minute_limited, minute_metadata = self.rate_limiter.is_rate_limited(
            identifier,
            limit_type="minute",
            increment=True,
        )
        
        if is_minute_limited:
            self._handle_rate_limit_exceeded(
                request,
                identifier,
                "minute",
                minute_metadata,
                current_user,
            )
        
        # Check hourly limit for authenticated users
        if current_user:
            is_hour_limited, hour_metadata = self.rate_limiter.is_rate_limited(
                identifier,
                limit_type="hour",
                increment=False,  # Only increment on successful requests
            )
            
            if is_hour_limited:
                self._handle_rate_limit_exceeded(
                    request,
                    identifier,
                    "hour",
                    hour_metadata,
                    current_user,
                )
    
    def _get_client_identifier(
        self,
        request: Request,
        current_user: Optional[Dict[str, Any]],
    ) -> str:
        """Get unique identifier for rate limiting."""
        if current_user:
            # Authenticated user - use user ID
            return f"user:{current_user.get('user_id')}"
        else:
            # Anonymous user - use IP address
            client_ip = request.client.host if request.client else "unknown"
            
            # For IPv6, use first 64 bits to avoid tracking individual addresses
            if ":" in client_ip:
                # IPv6 - use /64 prefix
                parts = client_ip.split(":")
                if len(parts) >= 4:
                    client_ip = ":".join(parts[:4]) + "::/64"
            
            return f"ip:{client_ip}"
    
    def _handle_rate_limit_exceeded(
        self,
        request: Request,
        identifier: str,
        limit_type: str,
        metadata: Dict[str, Any],
        current_user: Optional[Dict[str, Any]],
    ) -> None:
        """Handle rate limit exceeded."""
        # Audit event
        self.security_manager.audit_event(
            event_type="RATE_LIMIT_EXCEEDED",
            user_id=current_user.get("user_id") if current_user else None,
            ip_address=request.client.host if request.client else None,
            details={
                "identifier": identifier,
                "limit_type": limit_type,
                "metadata": metadata,
                "path": request.url.path,
                "method": request.method,
            },
            severity="WARNING",
        )
        
        # Calculate retry after
        retry_after = 60  # Default 60 seconds
        if limit_type == "burst":
            retry_after = 10  # Burst limit resets after 10 seconds
        elif limit_type == "hour":
            retry_after = 3600  # Hour limit resets after 1 hour
        
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({limit_type})",
            headers={
                "X-RateLimit-Limit": str(metadata.get("limit")),
                "X-RateLimit-Remaining": str(metadata.get("limit") - metadata.get("current_count", 0)),
                "X-RateLimit-Reset": str(int(time.time() + retry_after)),
                "Retry-After": str(retry_after),
            },
        )


# ==================== FEATURE FLAG DEPENDENCIES ====================

class FeatureFlagManager:
    """Feature flag management."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_manager = get_cache_manager()
        self.cache_key = "feature_flags"
        self.cache_ttl = 300  # 5 minutes
        
    async def get_feature_flags(self) -> Dict[str, bool]:
        """
        Get all feature flags.
        
        Returns:
            Dictionary of feature flags
        """
        # Try cache first
        cached = await self.cache_manager.get(self.cache_key)
        if cached:
            return cached
        
        # In production, this would fetch from database or feature flag service
        # For now, use settings and hardcoded flags
        flags = {
            "beta_features": self.settings.ENABLE_BETA_FEATURES,
            "advanced_monitoring": True,
            "cost_optimization": True,
            "security_scanning": True,
            "multi_cloud": self.settings.ENABLE_MULTI_CLOUD,
            "agent_marketplace": False,
            "ai_insights": self.settings.ENABLE_AI_INSIGHTS,
            "real_time_alerts": True,
            "compliance_reporting": True,
            "custom_agents": True,
        }
        
        # Cache the flags
        await self.cache_manager.set(self.cache_key, flags, ttl=self.cache_ttl)
        
        return flags
    
    async def is_feature_enabled(
        self,
        feature_name: str,
        default: bool = False,
        feature_flags: Dict[str, bool] = Depends(get_feature_flags),
    ) -> bool:
        """
        Check if a feature is enabled.
        
        Args:
            feature_name: Name of the feature
            default: Default value if feature not found
            feature_flags: Feature flags dictionary
            
        Returns:
            True if feature is enabled
        """
        return feature_flags.get(feature_name, default)
    
    async def require_feature(
        self,
        feature_name: str,
        feature_enabled: bool = Depends(is_feature_enabled),
    ) -> None:
        """
        Dependency to require a feature flag.
        
        Args:
            feature_name: Name of the feature
            feature_enabled: Whether feature is enabled
            
        Raises:
            HTTPException: If feature is disabled
        """
        if not feature_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature '{feature_name}' is not enabled",
            )


# ==================== ORGANIZATION/TENANT DEPENDENCIES ====================

class OrganizationManager:
    """Organization and tenant context management."""
    
    async def get_current_tenant(
        self,
        current_user: Dict[str, Any] = Depends(get_current_user),
        db_session: AsyncSession = Depends(get_database_session),
    ) -> Dict[str, Any]:
        """
        Get current tenant/organization.
        
        Args:
            current_user: Current user data
            db_session: Database session
            
        Returns:
            Tenant data
            
        Raises:
            HTTPException: If tenant not found
        """
        tenant_id = current_user.get("tenant_id")
        
        if not tenant_id:
            # User might not be associated with a tenant
            return {
                "tenant_id": None,
                "name": "Personal",
                "plan": "free",
                "status": "active",
            }
        
        # In production, this would query the database
        # For now, return mock data
        tenant_data = {
            "tenant_id": tenant_id,
            "name": f"Tenant {tenant_id[:8]}",
            "plan": "professional",
            "status": "active",
            "created_at": datetime.utcnow() - timedelta(days=30),
            "settings": {
                "max_agents": 500,
                "max_users": 10,
                "enabled_features": ["monitoring", "cost", "security"],
            },
        }
        
        return tenant_data
    
    async def get_tenant_limits(
        self,
        current_tenant: Dict[str, Any] = Depends(get_current_tenant),
    ) -> Dict[str, Any]:
        """Get tenant resource limits."""
        return current_tenant.get("settings", {}).get("limits", {})
    
    async def check_tenant_resource(
        self,
        resource_type: str,
        current_usage: int,
        tenant_limits: Dict[str, Any] = Depends(get_tenant_limits),
    ) -> bool:
        """
        Check if tenant has capacity for a resource.
        
        Args:
            resource_type: Type of resource (agents, users, etc.)
            current_usage: Current usage count
            tenant_limits: Tenant limits
            
        Returns:
            True if within limits
            
        Raises:
            HTTPException: If limit exceeded
        """
        limit = tenant_limits.get(resource_type)
        
        if limit is not None and current_usage >= limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{resource_type.capitalize()} limit exceeded ({current_usage}/{limit})",
            )
        
        return True


# ==================== BILLING DEPENDENCIES ====================

class BillingManager:
    """Billing and subscription management."""
    
    async def get_subscription_status(
        self,
        current_tenant: Dict[str, Any] = Depends(get_current_tenant),
        db_session: AsyncSession = Depends(get_database_session),
    ) -> Dict[str, Any]:
        """
        Get current subscription status.
        
        Args:
            current_tenant: Current tenant data
            db_session: Database session
            
        Returns:
            Subscription data
        """
        tenant_id = current_tenant.get("tenant_id")
        
        if not tenant_id:
            # No tenant = free tier
            return {
                "plan": "free",
                "status": "active",
                "current_period_start": datetime.utcnow() - timedelta(days=30),
                "current_period_end": datetime.utcnow() + timedelta(days=365),
                "cancel_at_period_end": False,
                "payment_method": None,
                "billing_email": None,
            }
        
        # In production, this would query billing service
        # For now, return mock data based on tenant plan
        plan = current_tenant.get("plan", "free")
        
        subscription_data = {
            "plan": plan,
            "status": "active",
            "current_period_start": datetime.utcnow() - timedelta(days=30),
            "current_period_end": datetime.utcnow() + timedelta(days=365),
            "cancel_at_period_end": False,
            "payment_method": "card_****1234",
            "billing_email": "billing@example.com",
            "features": self._get_plan_features(plan),
        }
        
        return subscription_data
    
    def _get_plan_features(self, plan: str) -> List[str]:
        """Get features for a plan."""
        plans = {
            "free": ["basic_monitoring", "email_support"],
            "professional": [
                "advanced_monitoring", "cost_optimization", "security_scanning",
                "priority_support", "multi_cloud",
            ],
            "enterprise": [
                "advanced_monitoring", "cost_optimization", "security_scanning",
                "dedicated_support", "multi_cloud", "custom_agents", "sla",
                "compliance_reporting", "on_premise_deployment",
            ],
        }
        
        return plans.get(plan, plans["free"])
    
    async def require_subscription(
        self,
        required_plan: str,
        subscription_status: Dict[str, Any] = Depends(get_subscription_status),
    ) -> None:
        """
        Dependency to require minimum subscription plan.
        
        Args:
            required_plan: Minimum required plan
            subscription_status: Current subscription
            
        Raises:
            HTTPException: If subscription insufficient
        """
        current_plan = subscription_status.get("plan")
        
        plan_hierarchy = {
            "free": 0,
            "professional": 1,
            "enterprise": 2,
        }
        
        current_level = plan_hierarchy.get(current_plan, 0)
        required_level = plan_hierarchy.get(required_plan, 0)
        
        if current_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Requires {required_plan} plan or higher",
            )
    
    async def check_payment_status(
        self,
        subscription_status: Dict[str, Any] = Depends(get_subscription_status),
    ) -> None:
        """
        Check if payments are up to date.
        
        Args:
            subscription_status: Current subscription
            
        Raises:
            HTTPException: If payment overdue
        """
        status = subscription_status.get("status")
        
        if status in ["past_due", "unpaid", "canceled"]:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Subscription status: {status}. Please update payment method.",
            )


# ==================== PERFORMANCE MONITORING DEPENDENCIES ====================

class PerformanceMonitor:
    """Performance monitoring dependencies."""
    
    def __init__(self):
        self.logger = get_logger("performance")
    
    async def monitor_request(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        Middleware to monitor request performance.
        
        Args:
            request: FastAPI request
            call_next: Next middleware/endpoint
            
        Returns:
            Response with performance headers
        """
        # Start timing
        start_time = time.time()
        _request_start_time.set(start_time)
        
        # Get trace manager
        trace_manager = get_trace_manager()
        
        # Extract trace context
        headers = dict(request.headers)
        trace_context = trace_manager.extract_context(headers)
        
        # Create request span
        span_name = f"{request.method} {request.url.path}"
        span_attributes = {
            "http.method": request.method,
            "http.url": str(request.url),
            "http.client_ip": request.client.host if request.client else None,
            "http.user_agent": request.headers.get("user-agent"),
        }
        
        # Execute request with tracing
        with trace_manager.start_span(
            name=span_name,
            kind=2,  # SERVER
            attributes=span_attributes,
        ):
            try:
                response = await call_next(request)
                
                # Add performance headers
                duration = time.time() - start_time
                response.headers["X-Request-Duration"] = f"{duration:.3f}"
                response.headers["X-Request-Start-Time"] = str(start_time)
                
                # Log performance
                if duration > 1.0:  # Log slow requests
                    self.logger.warning(
                        "slow_request",
                        method=request.method,
                        path=request.url.path,
                        duration=duration,
                        status_code=response.status_code,
                    )
                
                return response
                
            except Exception as e:
                duration = time.time() - start_time
                self.logger.error(
                    "request_failed",
                    method=request.method,
                    path=request.url.path,
                    duration=duration,
                    error=str(e),
                )
                raise
    
    async def measure_operation(
        self,
        operation_name: str,
        db_session: AsyncSession = Depends(get_database_session),
    ) -> Callable:
        """
        Dependency to measure operation performance.
        
        Args:
            operation_name: Name of the operation
            db_session: Database session
            
        Returns:
            Context manager for measurement
        """
        @asynccontextmanager
        async def measurement_context():
            start_time = time.time()
            
            try:
                yield
                
                duration = time.time() - start_time
                self.logger.info(
                    "operation_completed",
                    operation=operation_name,
                    duration=duration,
                )
                
                # Record metric if span exists
                current_span = get_current_span()
                if current_span:
                    current_span.set_attribute(f"operation.{operation_name}.duration", duration)
                    
            except Exception as e:
                duration = time.time() - start_time
                self.logger.error(
                    "operation_failed",
                    operation=operation_name,
                    duration=duration,
                    error=str(e),
                )
                raise
        
        return measurement_context


# ==================== CACHE DEPENDENCIES ====================

class CacheDependency:
    """Cache access dependencies."""
    
    def __init__(self, cache_manager: CacheManager):
        self.cache_manager = cache_manager
    
    async def get_cached_data(
        self,
        cache_key: str,
        ttl: int = 300,
        cache_manager: CacheManager = Depends(get_cache_manager),
    ) -> Optional[Any]:
        """
        Get data from cache.
        
        Args:
            cache_key: Cache key
            ttl: Time to live in seconds
            cache_manager: Cache manager
            
        Returns:
            Cached data or None
        """
        return await cache_manager.get(cache_key)
    
    async def set_cached_data(
        self,
        cache_key: str,
        data: Any,
        ttl: int = 300,
        cache_manager: CacheManager = Depends(get_cache_manager),
    ) -> None:
        """
        Set data in cache.
        
        Args:
            cache_key: Cache key
            data: Data to cache
            ttl: Time to live in seconds
            cache_manager: Cache manager
        """
        await cache_manager.set(cache_key, data, ttl=ttl)
    
    async def cached_response(
        self,
        request: Request,
        ttl: int = 60,
        vary_by_user: bool = False,
        vary_by_tenant: bool = False,
        cache_manager: CacheManager = Depends(get_cache_manager),
        current_user: Optional[Dict[str, Any]] = Depends(get_current_user, use_cache=True),
    ) -> Optional[Response]:
        """
        Dependency for cached HTTP responses.
        
        Args:
            request: FastAPI request
            ttl: Cache TTL in seconds
            vary_by_user: Whether to vary cache by user
            vary_by_tenant: Whether to vary cache by tenant
            cache_manager: Cache manager
            current_user: Current user (optional)
            
        Returns:
            Cached response or None
        """
        # Build cache key
        cache_parts = [request.method, str(request.url)]
        
        if vary_by_user and current_user:
            cache_parts.append(f"user:{current_user.get('user_id')}")
        
        if vary_by_tenant:
            tenant_id = _current_tenant_id.get()
            if tenant_id:
                cache_parts.append(f"tenant:{tenant_id}")
        
        cache_key = f"http_response:{hash(':'.join(cache_parts))}"
        
        # Try to get from cache
        cached = await cache_manager.get(cache_key)
        if cached:
            return Response(
                content=cached["content"],
                status_code=cached["status_code"],
                headers=cached["headers"],
                media_type=cached["media_type"],
            )
        
        return None


# ==================== EXTERNAL SERVICE CLIENTS ====================

class ServiceClientManager:
    """External service client management."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.clients: Dict[str, Any] = {}
        
    async def get_aws_client(self, service_name: str) -> Any:
        """Get AWS service client."""
        import boto3
        from botocore.config import Config
        
        cache_key = f"aws_client:{service_name}"
        
        if cache_key not in self.clients:
            # Create AWS client with configuration
            config = Config(
                retries={
                    'max_attempts': 3,
                    'mode': 'standard',
                },
                connect_timeout=10,
                read_timeout=30,
            )
            
            self.clients[cache_key] = boto3.client(
                service_name,
                region_name=self.settings.AWS_REGION,
                aws_access_key_id=self.settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=self.settings.AWS_SECRET_ACCESS_KEY,
                config=config,
            )
        
        return self.clients[cache_key]
    
    async def get_azure_client(self, client_type: str) -> Any:
        """Get Azure service client."""
        # Azure client initialization
        # This would be implemented based on Azure SDK
        pass
    
    async def get_gcp_client(self, service_name: str) -> Any:
        """Get GCP service client."""
        # GCP client initialization
        # This would be implemented based on Google Cloud SDK
        pass
    
    async def get_http_client(self) -> Any:
        """Get async HTTP client."""
        import httpx
        
        cache_key = "http_client"
        
        if cache_key not in self.clients:
            timeout = httpx.Timeout(10.0, connect=5.0)
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            
            self.clients[cache_key] = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                follow_redirects=True,
            )
        
        return self.clients[cache_key]


# ==================== CONFIGURATION DEPENDENCIES ====================

class ConfigDependency:
    """Configuration access dependencies."""
    
    async def get_feature_config(
        self,
        feature_name: str,
        settings: Settings = Depends(get_settings),
    ) -> Dict[str, Any]:
        """
        Get configuration for a specific feature.
        
        Args:
            feature_name: Name of the feature
            settings: Application settings
            
        Returns:
            Feature configuration
        """
        # In production, this would load from database or config service
        feature_configs = {
            "monitoring": {
                "enabled": True,
                "retention_days": 30,
                "sampling_rate": 1.0,
            },
            "cost_optimization": {
                "enabled": True,
                "schedule": "0 */6 * * *",
                "threshold": 0.8,
            },
            "security_scanning": {
                "enabled": True,
                "frequency": "daily",
                "severity_level": "medium",
            },
        }
        
        return feature_configs.get(feature_name, {})
    
    async def get_agent_config(
        self,
        agent_id: str,
        db_session: AsyncSession = Depends(get_database_session),
    ) -> Dict[str, Any]:
        """
        Get configuration for a specific agent.
        
        Args:
            agent_id: Agent ID
            db_session: Database session
            
        Returns:
            Agent configuration
        """
        # In production, this would query the database
        # For now, return default configuration
        return {
            "agent_id": agent_id,
            "settings": {
                "enabled": True,
                "schedule": "* * * * *",
                "timeout": 300,
            },
            "metadata": {
                "version": "1.0.0",
                "created_at": datetime.utcnow(),
            },
        }


# ==================== MAIN DEPENDENCY FACTORY ====================

class DependencyFactory:
    """Main dependency factory for the application."""
    
    def __init__(self):
        self.settings = get_settings()
        self.security_manager = get_security_manager()
        self.cache_manager = get_cache_manager()
        
        # Initialize managers
        self.db_manager = DatabaseManager(self.settings)
        self.auth_manager = AuthManager(self.security_manager)
        self.rate_limit_manager = RateLimitManager(self.security_manager)
        self.feature_flag_manager = FeatureFlagManager(self.settings)
        self.org_manager = OrganizationManager()
        self.billing_manager = BillingManager()
        self.performance_monitor = PerformanceMonitor()
        self.cache_dependency = CacheDependency(self.cache_manager)
        self.service_client_manager = ServiceClientManager(self.settings)
        self.config_dependency = ConfigDependency()
        
        # Application state
        self.app_state = AppState(
            settings=self.settings,
            security_manager=self.security_manager,
            cache_manager=self.cache_manager,
            database_engine=self.db_manager.engine,
            feature_flags={},
            service_clients={},
        )
    
    # Database dependencies
    async def get_database_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get database session dependency."""
        async with self.db_manager.get_session() as session:
            yield session
    
    # Authentication dependencies
    async def get_current_user(self) -> Dict[str, Any]:
        """Get current authenticated user."""
        return await self.auth_manager.get_current_user()
    
    async def require_role(self, required_role: UserRole) -> Callable:
        """Require specific user role."""
        async def role_dependency(current_user: Dict[str, Any] = Depends(self.get_current_user)):
            return await self.auth_manager.require_role(required_role, current_user)
        return role_dependency
    
    async def require_tenant_access(self, tenant_id: TenantID) -> Callable:
        """Require access to specific tenant."""
        async def tenant_dependency(
            current_user: Dict[str, Any] = Depends(self.get_current_user),
            db_session: AsyncSession = Depends(self.get_database_session),
        ):
            return await self.auth_manager.require_tenant_access(tenant_id, current_user, db_session)
        return tenant_dependency
    
    # Rate limiting
    async def rate_limit(self) -> None:
        """Rate limiting dependency."""
        return Depends(self.rate_limit_manager.rate_limit)
    
    # Feature flags
    async def get_feature_flags(self) -> Dict[str, bool]:
        """Get all feature flags."""
        return await self.feature_flag_manager.get_feature_flags()
    
    async def require_feature(self, feature_name: str) -> Callable:
        """Require specific feature flag."""
        async def feature_dependency(
            feature_enabled: bool = Depends(
                functools.partial(self.feature_flag_manager.is_feature_enabled, feature_name)
            )
        ):
            return await self.feature_flag_manager.require_feature(feature_name, feature_enabled)
        return feature_dependency
    
    # Organization/Tenant
    async def get_current_tenant(self) -> Dict[str, Any]:
        """Get current tenant."""
        return await self.org_manager.get_current_tenant()
    
    async def check_tenant_resource(self, resource_type: str, current_usage: int) -> Callable:
        """Check tenant resource limit."""
        async def resource_dependency(
            tenant_limits: Dict[str, Any] = Depends(self.org_manager.get_tenant_limits),
        ):
            return await self.org_manager.check_tenant_resource(resource_type, current_usage, tenant_limits)
        return resource_dependency
    
    # Billing
    async def get_subscription_status(self) -> Dict[str, Any]:
        """Get subscription status."""
        return await self.billing_manager.get_subscription_status()
    
    async def require_subscription(self, required_plan: str) -> Callable:
        """Require minimum subscription plan."""
        async def subscription_dependency(
            subscription_status: Dict[str, Any] = Depends(self.get_subscription_status),
        ):
            return await self.billing_manager.require_subscription(required_plan, subscription_status)
        return subscription_dependency
    
    async def check_payment_status(self) -> Callable:
        """Check payment status."""
        async def payment_dependency(
            subscription_status: Dict[str, Any] = Depends(self.get_subscription_status),
        ):
            return await self.billing_manager.check_payment_status(subscription_status)
        return payment_dependency
    
    # Performance monitoring
    async def monitor_request(self) -> Callable:
        """Request monitoring middleware."""
        return self.performance_monitor.monitor_request
    
    async def measure_operation(self, operation_name: str) -> Callable:
        """Measure operation performance."""
        async def measurement_dependency(
            db_session: AsyncSession = Depends(self.get_database_session),
        ):
            return await self.performance_monitor.measure_operation(operation_name, db_session)
        return measurement_dependency
    
    # Cache
    async def cached_response(
        self,
        ttl: int = 60,
        vary_by_user: bool = False,
        vary_by_tenant: bool = False,
    ) -> Callable:
        """Cached response dependency."""
        async def cache_dependency(
            request: Request,
            cache_manager: CacheManager = Depends(get_cache_manager),
            current_user: Optional[Dict[str, Any]] = Depends(self.get_current_user, use_cache=True),
        ):
            return await self.cache_dependency.cached_response(
                request, ttl, vary_by_user, vary_by_tenant, cache_manager, current_user
            )
        return cache_dependency
    
    # External services
    async def get_service_client(self, service_type: str, service_name: str) -> Any:
        """Get external service client."""
        if service_type == "aws":
            return await self.service_client_manager.get_aws_client(service_name)
        elif service_type == "azure":
            return await self.service_client_manager.get_azure_client(service_name)
        elif service_type == "gcp":
            return await self.service_client_manager.get_gcp_client(service_name)
        elif service_type == "http":
            return await self.service_client_manager.get_http_client()
        else:
            raise ValueError(f"Unknown service type: {service_type}")
    
    # Configuration
    async def get_feature_config(self, feature_name: str) -> Dict[str, Any]:
        """Get feature configuration."""
        return await self.config_dependency.get_feature_config(feature_name)
    
    async def get_agent_config(self, agent_id: str) -> Dict[str, Any]:
        """Get agent configuration."""
        return await self.config_dependency.get_agent_config(agent_id)


# Global dependency factory instance
_dependency_factory: Optional[DependencyFactory] = None


def get_dependency_factory() -> DependencyFactory:
    """Get or create global dependency factory."""
    global _dependency_factory
    
    if _dependency_factory is None:
        _dependency_factory = DependencyFactory()
    
    return _dependency_factory


# Convenience functions for common dependencies
def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    factory = get_dependency_factory()
    return factory.get_database_session()


def get_current_user() -> Dict[str, Any]:
    """Get current authenticated user."""
    factory = get_dependency_factory()
    return factory.get_current_user()


def require_role(required_role: UserRole) -> Callable:
    """Require specific user role."""
    factory = get_dependency_factory()
    return factory.require_role(required_role)


def rate_limit() -> None:
    """Rate limiting dependency."""
    factory = get_dependency_factory()
    return factory.rate_limit()


# Export public API
__all__ = [
    # Dependency factory
    "DependencyFactory",
    "get_dependency_factory",
    
    # Convenience functions
    "get_db_session",
    "get_current_user",
    "require_role",
    "rate_limit",
    
    # Managers
    "DatabaseManager",
    "AuthManager",
    "RateLimitManager",
    "FeatureFlagManager",
    "OrganizationManager",
    "BillingManager",
    "PerformanceMonitor",
    "CacheDependency",
    "ServiceClientManager",
    "ConfigDependency",
    
    # Application state
    "AppState",
    
    # Context variables
    "_current_user_id",
    "_current_tenant_id",
    "_current_user_role",
    "_request_start_time",
]