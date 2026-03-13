"""
Rate limiting middleware for MicroAgents Platform.
Advanced rate limiting with multiple algorithms and dynamic adjustment.
"""

import asyncio
import hashlib
import heapq
import math
import random
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
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

import aioredis
import orjson
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, PositiveFloat, PositiveInt, validator
from starlette.middleware.base import BaseHTTPMiddleware

# Local imports
from microagents.monitoring.logging.setup import get_logger
from microagents.api.middleware.auth import AuthResult, get_current_user
from microagents.utils.security.utils import SecurityManager, get_security_manager

# Type aliases
LimiterKey = str
RateLimitResult = Tuple[bool, Dict[str, Any]]
QuotaUpdate = Dict[str, Any]


class RateLimitAlgorithm(str, Enum):
    """Rate limiting algorithms."""
    
    TOKEN_BUCKET = "token_bucket"
    LEAKY_BUCKET = "leaky_bucket"
    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    ADAPTIVE = "adaptive"
    COST_BASED = "cost_based"
    CONCURRENCY = "concurrency"


class RateLimitTier(str, Enum):
    """Rate limit tiers based on subscription."""
    
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    UNLIMITED = "unlimited"
    CUSTOM = "custom"


class RateLimitScope(str, Enum):
    """Scope for rate limiting."""
    
    USER = "user"
    ORGANIZATION = "organization"
    IP_ADDRESS = "ip_address"
    ENDPOINT = "endpoint"
    GLOBAL = "global"
    API_KEY = "api_key"
    SESSION = "session"


class AbuseType(str, Enum):
    """Types of abuse patterns."""
    
    BURST_ATTACK = "burst_attack"
    DISTRIBUTED_ATTACK = "distributed_attack"
    SLOW_LORIS = "slow_loris"
    API_SCAN = "api_scan"
    CREDENTIAL_STUFFING = "credential_stuffing"
    DATA_SCRAPING = "data_scraping"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    COST_OPTIMIZATION_ABUSE = "cost_optimization_abuse"


class QuotaUnit(str, Enum):
    """Units for quota measurement."""
    
    REQUESTS = "requests"
    TOKENS = "tokens"  # For LLM/agent tokens
    SECONDS = "seconds"  # For compute time
    BYTES = "bytes"  # For data transfer
    COST = "cost"  # Monetary cost in USD
    COMPUTE_UNITS = "compute_units"  # Abstract compute units


class GracefulDegradationLevel(str, Enum):
    """Levels of graceful degradation."""
    
    FULL = "full"  # Full functionality
    LIMITED = "limited"  # Some features disabled
    DEGRADED = "degraded"  # Essential features only
    READ_ONLY = "read_only"  # Read operations only
    MAINTENANCE = "maintenance"  # Critical operations only


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit rule."""
    
    # Identification
    name: str
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.TOKEN_BUCKET
    
    # Limits
    rate: PositiveInt  # Requests per period
    period: PositiveInt  # Period in seconds
    burst: PositiveInt = 1  # Burst allowance
    
    # Scope
    scope: RateLimitScope = RateLimitScope.USER
    tier: RateLimitTier = RateLimitTier.FREE
    
    # Cost-based limiting
    cost_per_request: float = 0.0
    max_cost_per_period: Optional[float] = None
    
    # Concurrency limiting
    max_concurrent: Optional[int] = None
    
    # Adaptive settings
    min_rate: Optional[int] = None
    max_rate: Optional[int] = None
    adjustment_factor: float = 0.1
    
    # Abuse detection
    abuse_threshold: Optional[float] = None
    abuse_cooldown: int = 300  # seconds
    
    # Graceful degradation
    degrade_on_limit: bool = True
    degradation_level: GracefulDegradationLevel = GracefulDegradationLevel.LIMITED
    
    # Metadata
    description: str = ""
    enabled: bool = True
    
    def __post_init__(self):
        """Validate configuration."""
        if self.burst > self.rate:
            raise ValueError("Burst cannot exceed rate")
        
        if self.algorithm == RateLimitAlgorithm.COST_BASED and self.cost_per_request <= 0:
            raise ValueError("Cost-based limiting requires positive cost_per_request")
        
        if self.min_rate is not None and self.max_rate is not None:
            if self.min_rate > self.max_rate:
                raise ValueError("min_rate cannot exceed max_rate")
    
    def get_key(self, identifier: str) -> str:
        """Get Redis key for this rate limit."""
        return f"ratelimit:{self.scope.value}:{self.name}:{identifier}"
    
    def get_cost_key(self, identifier: str) -> str:
        """Get Redis key for cost tracking."""
        return f"ratelimit:cost:{self.scope.value}:{self.name}:{identifier}"


@dataclass
class Quota:
    """Quota for a user/organization."""
    
    tier: RateLimitTier
    rate_limit_configs: List[RateLimitConfig] = field(default_factory=list)
    
    # Usage tracking
    current_usage: Dict[str, float] = field(default_factory=dict)  # quota_type -> usage
    reset_time: Dict[str, datetime] = field(default_factory=dict)  # quota_type -> reset time
    
    # Billing integration
    billing_account_id: Optional[str] = None
    balance: float = 0.0  # USD balance
    credit_limit: Optional[float] = None
    
    # Dynamic adjustment
    dynamic_adjustment_enabled: bool = False
    adjustment_history: List[Dict[str, Any]] = field(default_factory=list)
    
    def get_remaining(self, quota_type: QuotaUnit) -> float:
        """Get remaining quota."""
        usage = self.current_usage.get(quota_type.value, 0)
        config = self.get_config_for_quota(quota_type)
        
        if not config:
            return float('inf')
        
        if quota_type == QuotaUnit.COST:
            if self.credit_limit:
                return self.credit_limit - usage
            return float('inf')
        
        return config.rate - usage
    
    def get_config_for_quota(self, quota_type: QuotaUnit) -> Optional[RateLimitConfig]:
        """Get rate limit config for quota type."""
        for config in self.rate_limit_configs:
            if quota_type == QuotaUnit.COST and config.cost_per_request > 0:
                return config
            elif quota_type == QuotaUnit.REQUESTS and config.cost_per_request == 0:
                return config
        return None
    
    def can_afford(self, cost: float) -> bool:
        """Check if quota can afford the cost."""
        if self.credit_limit is None:
            return True
        
        current_cost = self.current_usage.get(QuotaUnit.COST.value, 0)
        return current_cost + cost <= self.credit_limit


@dataclass
class RateLimitResult:
    """Result of rate limit check."""
    
    allowed: bool
    limit: int
    remaining: int
    reset: int  # Unix timestamp
    retry_after: Optional[int] = None  # seconds
    
    # Cost information
    cost_used: float = 0.0
    cost_remaining: Optional[float] = None
    cost_limit: Optional[float] = None
    
    # Concurrency information
    concurrent_used: Optional[int] = None
    concurrent_limit: Optional[int] = None
    
    # Graceful degradation
    degradation_level: Optional[GracefulDegradationLevel] = None
    degraded_features: List[str] = field(default_factory=list)
    
    # Abuse detection
    abuse_score: float = 0.0
    abuse_warning: bool = False
    
    def to_headers(self) -> Dict[str, str]:
        """Convert to HTTP headers."""
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset),
        }
        
        if self.retry_after:
            headers["Retry-After"] = str(self.retry_after)
            headers["X-RateLimit-Retry-After"] = str(self.retry_after)
        
        if self.cost_remaining is not None:
            headers["X-RateLimit-Cost-Remaining"] = str(self.cost_remaining)
        
        if self.cost_limit is not None:
            headers["X-RateLimit-Cost-Limit"] = str(self.cost_limit)
        
        if self.degradation_level:
            headers["X-RateLimit-Degradation-Level"] = self.degradation_level.value
        
        return headers
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "allowed": self.allowed,
            "limit": self.limit,
            "remaining": self.remaining,
            "reset": self.reset,
            "retry_after": self.retry_after,
            "cost_used": self.cost_used,
            "cost_remaining": self.cost_remaining,
            "cost_limit": self.cost_limit,
            "concurrent_used": self.concurrent_used,
            "concurrent_limit": self.concurrent_limit,
            "degradation_level": self.degradation_level.value if self.degradation_level else None,
            "degraded_features": self.degraded_features,
            "abuse_score": self.abuse_score,
            "abuse_warning": self.abuse_warning,
        }


class RateLimiter(ABC):
    """Abstract base class for rate limiters."""
    
    @abstractmethod
    async def check(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check if request is allowed."""
        pass
    
    @abstractmethod
    async def increment(self, key: str, config: RateLimitConfig, cost: float = 0.0) -> None:
        """Increment usage counter."""
        pass
    
    @abstractmethod
    async def get_usage(self, key: str, config: RateLimitConfig) -> Dict[str, Any]:
        """Get current usage statistics."""
        pass


class TokenBucketLimiter(RateLimiter):
    """Token bucket rate limiter."""
    
    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self.redis = redis_client
        self.local_buckets: Dict[str, Dict[str, Any]] = {}  # For local testing
        
    async def check(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check using token bucket algorithm."""
        if self.redis:
            return await self._check_redis(key, config)
        else:
            return await self._check_local(key, config)
    
    async def _check_redis(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check using Redis implementation."""
        bucket_key = f"{key}:tokens"
        last_update_key = f"{key}:last_update"
        
        async with self.redis.pipeline(transaction=True) as pipe:
            # Get current state
            await pipe.get(bucket_key)
            await pipe.get(last_update_key)
            current_time = time.time()
            await pipe.set(last_update_key, current_time)  # Update last update
            
            results = await pipe.execute()
            current_tokens = float(results[0] or config.burst)
            last_update = float(results[1] or current_time)
            
            # Calculate new tokens
            time_passed = current_time - last_update
            new_tokens = time_passed * (config.rate / config.period)
            current_tokens = min(config.burst, current_tokens + new_tokens)
            
            # Check if request can be processed
            if current_tokens >= 1:
                allowed = True
                current_tokens -= 1
                retry_after = None
            else:
                allowed = False
                deficit = 1 - current_tokens
                retry_after = math.ceil(deficit * (config.period / config.rate))
            
            # Update bucket
            await self.redis.set(bucket_key, current_tokens)
            
            # Calculate remaining
            remaining = int(current_tokens)
            reset_time = int(current_time + (config.burst - current_tokens) * (config.period / config.rate))
            
            return RateLimitResult(
                allowed=allowed,
                limit=config.rate,
                remaining=remaining,
                reset=reset_time,
                retry_after=retry_after,
            )
    
    async def _check_local(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check using local implementation."""
        current_time = time.time()
        
        if key not in self.local_buckets:
            self.local_buckets[key] = {
                "tokens": config.burst,
                "last_update": current_time,
            }
        
        bucket = self.local_buckets[key]
        
        # Calculate new tokens
        time_passed = current_time - bucket["last_update"]
        new_tokens = time_passed * (config.rate / config.period)
        bucket["tokens"] = min(config.burst, bucket["tokens"] + new_tokens)
        bucket["last_update"] = current_time
        
        # Check if request can be processed
        if bucket["tokens"] >= 1:
            allowed = True
            bucket["tokens"] -= 1
            retry_after = None
        else:
            allowed = False
            deficit = 1 - bucket["tokens"]
            retry_after = math.ceil(deficit * (config.period / config.rate))
        
        remaining = int(bucket["tokens"])
        reset_time = int(current_time + (config.burst - bucket["tokens"]) * (config.period / config.rate))
        
        return RateLimitResult(
            allowed=allowed,
            limit=config.rate,
            remaining=remaining,
            reset=reset_time,
            retry_after=retry_after,
        )
    
    async def increment(self, key: str, config: RateLimitConfig, cost: float = 0.0) -> None:
        """Increment is handled in check() for token bucket."""
        pass
    
    async def get_usage(self, key: str, config: RateLimitConfig) -> Dict[str, Any]:
        """Get current usage."""
        if self.redis:
            bucket_key = f"{key}:tokens"
            tokens = await self.redis.get(bucket_key)
            return {"tokens": float(tokens or config.burst)}
        else:
            if key in self.local_buckets:
                return {"tokens": self.local_buckets[key]["tokens"]}
            return {"tokens": config.burst}


class SlidingWindowLimiter(RateLimiter):
    """Sliding window rate limiter."""
    
    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self.redis = redis_client
        self.local_windows: Dict[str, deque] = {}
        
    async def check(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check using sliding window algorithm."""
        current_time = time.time()
        window_start = current_time - config.period
        
        if self.redis:
            return await self._check_redis(key, config, current_time, window_start)
        else:
            return await self._check_local(key, config, current_time, window_start)
    
    async def _check_redis(self, key: str, config: RateLimitConfig, 
                          current_time: float, window_start: float) -> RateLimitResult:
        """Check using Redis implementation."""
        window_key = f"{key}:window"
        
        # Clean old entries and add current request
        async with self.redis.pipeline(transaction=True) as pipe:
            # Add current timestamp
            await pipe.zadd(window_key, {str(current_time): current_time})
            # Remove old entries
            await pipe.zremrangebyscore(window_key, 0, window_start)
            # Count entries in window
            await pipe.zcard(window_key)
            # Set expiry
            await pipe.expire(window_key, config.period)
            
            results = await pipe.execute()
            request_count = results[2]
        
        allowed = request_count <= config.rate
        remaining = max(0, config.rate - request_count)
        
        # Calculate reset time (oldest request + period)
        if request_count > 0:
            oldest = await self.redis.zrange(window_key, 0, 0, withscores=True)
            if oldest:
                reset_time = int(oldest[0][1] + config.period)
            else:
                reset_time = int(current_time + config.period)
        else:
            reset_time = int(current_time + config.period)
        
        # Calculate retry after if not allowed
        retry_after = None
        if not allowed and request_count > 0:
            # Find when a slot will be available
            oldest_idx = min(request_count - 1, config.rate)
            timestamps = await self.redis.zrange(window_key, 0, oldest_idx, withscores=True)
            if timestamps:
                retry_after = int(timestamps[-1][1] + config.period - current_time)
        
        return RateLimitResult(
            allowed=allowed,
            limit=config.rate,
            remaining=remaining,
            reset=reset_time,
            retry_after=retry_after,
        )
    
    async def _check_local(self, key: str, config: RateLimitConfig,
                          current_time: float, window_start: float) -> RateLimitResult:
        """Check using local implementation."""
        if key not in self.local_windows:
            self.local_windows[key] = deque()
        
        window = self.local_windows[key]
        
        # Remove old entries
        while window and window[0] < window_start:
            window.popleft()
        
        # Check if allowed
        allowed = len(window) < config.rate
        remaining = max(0, config.rate - len(window))
        
        # Calculate reset time
        if window:
            reset_time = int(window[0] + config.period)
        else:
            reset_time = int(current_time + config.period)
        
        # Calculate retry after if not allowed
        retry_after = None
        if not allowed and window:
            # Find when a slot will be available
            oldest_idx = min(len(window) - 1, config.rate - 1)
            retry_after = int(window[oldest_idx] + config.period - current_time)
        
        # Add current request if allowed
        if allowed:
            window.append(current_time)
        
        return RateLimitResult(
            allowed=allowed,
            limit=config.rate,
            remaining=remaining,
            reset=reset_time,
            retry_after=retry_after,
        )
    
    async def increment(self, key: str, config: RateLimitConfig, cost: float = 0.0) -> None:
        """Increment is handled in check() for sliding window."""
        pass
    
    async def get_usage(self, key: str, config: RateLimitConfig) -> Dict[str, Any]:
        """Get current usage."""
        current_time = time.time()
        window_start = current_time - config.period
        
        if self.redis:
            window_key = f"{key}:window"
            await self.redis.zremrangebyscore(window_key, 0, window_start)
            count = await self.redis.zcard(window_key)
            return {"requests_in_window": count}
        else:
            if key in self.local_windows:
                window = self.local_windows[key]
                # Remove old entries
                while window and window[0] < window_start:
                    window.popleft()
                return {"requests_in_window": len(window)}
            return {"requests_in_window": 0}


class AdaptiveRateLimiter(RateLimiter):
    """Adaptive rate limiter that adjusts based on system load and user behavior."""
    
    def __init__(self, base_limiter: RateLimiter, redis_client: Optional[aioredis.Redis] = None):
        self.base_limiter = base_limiter
        self.redis = redis_client
        self.adaptive_state: Dict[str, Dict[str, Any]] = {}
        
        # Load factors
        self.load_factors = {
            "cpu": 1.0,
            "memory": 1.0,
            "network": 1.0,
            "database": 1.0,
        }
        
        # User behavior tracking
        self.user_scores: Dict[str, float] = {}  # user_id -> trust score (0-1)
        
    async def check(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check with adaptive adjustments."""
        # Get base result
        base_result = await self.base_limiter.check(key, config)
        
        # Apply adaptive adjustments
        adjusted_result = await self._adjust_limits(base_result, config, key)
        
        return adjusted_result
    
    async def _adjust_limits(self, base_result: RateLimitResult, 
                           config: RateLimitConfig, key: str) -> RateLimitResult:
        """Adjust rate limits based on various factors."""
        # Calculate system load factor
        load_factor = await self._calculate_load_factor()
        
        # Calculate user trust factor
        user_id = self._extract_user_id(key)
        trust_factor = await self._calculate_trust_factor(user_id) if user_id else 1.0
        
        # Calculate combined adjustment factor
        adjustment = load_factor * trust_factor
        
        # Adjust limits if config allows
        if config.min_rate is not None and config.max_rate is not None:
            # Calculate adjusted rate
            adjusted_rate = int(config.rate * adjustment)
            adjusted_rate = max(config.min_rate, min(config.max_rate, adjusted_rate))
            
            # Adjust remaining and limit
            if adjusted_rate != config.rate:
                ratio = adjusted_rate / config.rate
                base_result.limit = adjusted_rate
                base_result.remaining = int(base_result.remaining * ratio)
        
        # Store adjustment info
        await self._record_adjustment(key, config, adjustment)
        
        return base_result
    
    async def _calculate_load_factor(self) -> float:
        """Calculate system load adjustment factor."""
        # In production, these would be actual system metrics
        # For now, simulate with random values
        import random
        import psutil
        
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1) / 100
            memory_percent = psutil.virtual_memory().percent / 100
            
            # Load factor: 1.0 at 50% load, decreasing to 0.5 at 100% load
            cpu_factor = 1.5 - cpu_percent  # 1.0 at 50%, 0.5 at 100%
            memory_factor = 1.5 - memory_percent
            
            # Use the more constrained factor
            load_factor = min(cpu_factor, memory_factor, 1.0)
            load_factor = max(0.5, load_factor)  # Don't go below 0.5
            
            return load_factor
            
        except Exception:
            # Fallback to random if metrics unavailable
            return random.uniform(0.7, 1.0)
    
    async def _calculate_trust_factor(self, user_id: str) -> float:
        """Calculate user trust factor."""
        if not user_id:
            return 1.0
        
        # Get or calculate trust score
        if self.redis:
            score_key = f"trust_score:{user_id}"
            score = await self.redis.get(score_key)
            if score:
                return float(score)
        
        # Calculate new score
        score = await self._compute_trust_score(user_id)
        
        # Cache the score
        if self.redis:
            score_key = f"trust_score:{user_id}"
            await self.redis.setex(score_key, 3600, score)  # Cache for 1 hour
        
        return score
    
    async def _compute_trust_score(self, user_id: str) -> float:
        """Compute trust score for user."""
        # Factors to consider:
        # 1. Account age
        # 2. Payment history
        # 3. Usage patterns
        # 4. Abuse reports
        # 5. Support interactions
        
        # Default score
        base_score = 0.7
        
        # Adjust based on factors (simplified)
        # In production, this would query databases
        adjustments = []
        
        # Account age adjustment (older accounts more trusted)
        # Assume we have account creation date
        # account_age_days = (datetime.now() - account_created).days
        # age_factor = min(1.0, account_age_days / 365)  # 1 year to reach full trust
        # adjustments.append(0.2 * age_factor)
        
        # Payment history adjustment
        # has_payment_history = True  # Would check billing system
        # if has_payment_history:
        #     adjustments.append(0.1)
        
        # Apply adjustments
        trust_score = base_score + sum(adjustments)
        
        # Clamp between 0.1 and 1.0
        return max(0.1, min(1.0, trust_score))
    
    def _extract_user_id(self, key: str) -> Optional[str]:
        """Extract user ID from rate limit key."""
        # Key format: ratelimit:scope:name:identifier
        parts = key.split(":")
        if len(parts) >= 4:
            identifier = parts[3]
            # Check if identifier is a user ID (starts with "user_")
            if identifier.startswith("user_"):
                return identifier
        return None
    
    async def _record_adjustment(self, key: str, config: RateLimitConfig, adjustment: float):
        """Record adjustment for monitoring."""
        adjustment_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "key": key,
            "original_rate": config.rate,
            "adjustment": adjustment,
            "load_factors": self.load_factors.copy(),
        }
        
        if self.redis:
            adjustment_key = f"adjustment_log:{key}:{int(time.time())}"
            await self.redis.setex(adjustment_key, 86400, orjson.dumps(adjustment_record))
        else:
            # Store locally
            if key not in self.adaptive_state:
                self.adaptive_state[key] = {"adjustments": []}
            self.adaptive_state[key]["adjustments"].append(adjustment_record)
            
            # Keep only last 100 adjustments
            if len(self.adaptive_state[key]["adjustments"]) > 100:
                self.adaptive_state[key]["adjustments"] = self.adaptive_state[key]["adjustments"][-100:]
    
    async def increment(self, key: str, config: RateLimitConfig, cost: float = 0.0) -> None:
        """Defer to base limiter."""
        await self.base_limiter.increment(key, config, cost)
    
    async def get_usage(self, key: str, config: RateLimitConfig) -> Dict[str, Any]:
        """Get usage from base limiter."""
        return await self.base_limiter.get_usage(key, config)


class CostBasedLimiter(RateLimiter):
    """Cost-based rate limiter for API calls with monetary costs."""
    
    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self.redis = redis_client
        self.local_costs: Dict[str, Dict[str, float]] = {}
        
    async def check(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check if user has enough balance/credit for the request."""
        cost_key = config.get_cost_key(key.split(":")[-1])  # Extract identifier
        
        # Get current cost usage
        if self.redis:
            current_cost = float(await self.redis.get(cost_key) or 0)
        else:
            current_cost = self.local_costs.get(cost_key, {}).get("cost", 0)
        
        # Calculate remaining budget
        if config.max_cost_per_period:
            remaining_budget = max(0, config.max_cost_per_period - current_cost)
            can_afford = remaining_budget >= config.cost_per_request
            
            if not can_afford:
                # Calculate when budget resets (simplified: at period end)
                reset_time = int(time.time() + config.period)
                return RateLimitResult(
                    allowed=False,
                    limit=int(config.max_cost_per_period / config.cost_per_request) if config.cost_per_request > 0 else 0,
                    remaining=0,
                    reset=reset_time,
                    retry_after=config.period,
                    cost_used=current_cost,
                    cost_remaining=0,
                    cost_limit=config.max_cost_per_period,
                )
        
        # Also check regular rate limit
        # We'll combine with another limiter, so just return allowed for cost check
        return RateLimitResult(
            allowed=True,
            limit=config.rate,
            remaining=config.rate,  # This will be adjusted by combined limiter
            reset=int(time.time() + config.period),
            cost_used=current_cost,
            cost_remaining=config.max_cost_per_period - current_cost if config.max_cost_per_period else None,
            cost_limit=config.max_cost_per_period,
        )
    
    async def increment(self, key: str, config: RateLimitConfig, cost: float = 0.0) -> None:
        """Increment cost usage."""
        actual_cost = cost if cost > 0 else config.cost_per_request
        if actual_cost <= 0:
            return
        
        cost_key = config.get_cost_key(key.split(":")[-1])
        
        if self.redis:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.incrbyfloat(cost_key, actual_cost)
                await pipe.expire(cost_key, config.period)
                await pipe.execute()
        else:
            if cost_key not in self.local_costs:
                self.local_costs[cost_key] = {"cost": 0, "expiry": time.time() + config.period}
            
            self.local_costs[cost_key]["cost"] += actual_cost
            
            # Clean expired entries
            current_time = time.time()
            expired_keys = [k for k, v in self.local_costs.items() if v["expiry"] < current_time]
            for k in expired_keys:
                del self.local_costs[k]
    
    async def get_usage(self, key: str, config: RateLimitConfig) -> Dict[str, Any]:
        """Get cost usage."""
        cost_key = config.get_cost_key(key.split(":")[-1])
        
        if self.redis:
            current_cost = float(await self.redis.get(cost_key) or 0)
        else:
            current_cost = self.local_costs.get(cost_key, {}).get("cost", 0)
        
        return {
            "cost_used": current_cost,
            "cost_limit": config.max_cost_per_period,
            "cost_per_request": config.cost_per_request,
        }


class ConcurrencyLimiter(RateLimiter):
    """Concurrency limiter for simultaneous requests."""
    
    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self.redis = redis_client
        self.local_concurrent: Dict[str, Set[str]] = {}  # key -> set of request IDs
        
    async def check(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check concurrency limit."""
        if config.max_concurrent is None:
            # No concurrency limit
            return RateLimitResult(
                allowed=True,
                limit=0,
                remaining=0,
                reset=int(time.time() + 3600),
                concurrent_used=0,
                concurrent_limit=0,
            )
        
        request_id = str(uuid.uuid4())
        concurrency_key = f"{key}:concurrent"
        
        # Get current concurrent requests
        if self.redis:
            current_count = await self.redis.scard(concurrency_key)
        else:
            current_count = len(self.local_concurrent.get(concurrency_key, set()))
        
        # Check if under limit
        if current_count < config.max_concurrent:
            allowed = True
            # Store request ID
            if self.redis:
                await self.redis.sadd(concurrency_key, request_id)
                await self.redis.expire(concurrency_key, 300)  # 5 minute expiry
            else:
                if concurrency_key not in self.local_concurrent:
                    self.local_concurrent[concurrency_key] = set()
                self.local_concurrent[concurrency_key].add(request_id)
        else:
            allowed = False
        
        # Clean old entries periodically
        if random.random() < 0.01:  # 1% chance to clean
            await self._clean_old_entries(concurrency_key)
        
        return RateLimitResult(
            allowed=allowed,
            limit=config.max_concurrent,
            remaining=max(0, config.max_concurrent - current_count),
            reset=int(time.time() + 300),  # 5 minute window
            retry_after=10 if not allowed else None,  # Retry after 10 seconds
            concurrent_used=current_count,
            concurrent_limit=config.max_concurrent,
        )
    
    async def increment(self, key: str, config: RateLimitConfig, cost: float = 0.0) -> None:
        """Concurrency is handled in check()."""
        pass
    
    async def release(self, key: str, request_id: str) -> None:
        """Release a concurrent request slot."""
        concurrency_key = f"{key}:concurrent"
        
        if self.redis:
            await self.redis.srem(concurrency_key, request_id)
        else:
            if concurrency_key in self.local_concurrent:
                self.local_concurrent[concurrency_key].discard(request_id)
    
    async def _clean_old_entries(self, key: str) -> None:
        """Clean old entries from concurrency set."""
        # For Redis, we rely on expiry
        # For local, clean empty sets
        if not self.redis:
            keys_to_remove = [k for k, v in self.local_concurrent.items() if not v]
            for k in keys_to_remove:
                del self.local_concurrent[k]
    
    async def get_usage(self, key: str, config: RateLimitConfig) -> Dict[str, Any]:
        """Get concurrency usage."""
        concurrency_key = f"{key}:concurrent"
        
        if self.redis:
            current_count = await self.redis.scard(concurrency_key)
        else:
            current_count = len(self.local_concurrent.get(concurrency_key, set()))
        
        return {
            "concurrent_requests": current_count,
            "concurrent_limit": config.max_concurrent,
        }


class CombinedRateLimiter(RateLimiter):
    """Combines multiple rate limiters for comprehensive limiting."""
    
    def __init__(self, limiters: Dict[RateLimitAlgorithm, RateLimiter]):
        self.limiters = limiters
        
    async def check(self, key: str, config: RateLimitConfig) -> RateLimitResult:
        """Check all applicable limiters."""
        results = []
        
        # Always check base limiter (token bucket or sliding window)
        if config.algorithm in [RateLimitAlgorithm.TOKEN_BUCKET, RateLimitAlgorithm.SLIDING_WINDOW]:
            base_limiter = self.limiters.get(config.algorithm)
            if base_limiter:
                results.append(await base_limiter.check(key, config))
        
        # Check adaptive limiter if configured
        if config.algorithm == RateLimitAlgorithm.ADAPTIVE:
            adaptive_limiter = self.limiters.get(RateLimitAlgorithm.ADAPTIVE)
            if adaptive_limiter:
                results.append(await adaptive_limiter.check(key, config))
        
        # Check cost-based limiter if cost is involved
        if config.cost_per_request > 0:
            cost_limiter = self.limiters.get(RateLimitAlgorithm.COST_BASED)
            if cost_limiter:
                results.append(await cost_limiter.check(key, config))
        
        # Check concurrency limiter if configured
        if config.max_concurrent is not None:
            concurrency_limiter = self.limiters.get(RateLimitAlgorithm.CONCURRENCY)
            if concurrency_limiter:
                results.append(await concurrency_limiter.check(key, config))
        
        # Combine results
        if not results:
            # No limiters applied
            return RateLimitResult(
                allowed=True,
                limit=0,
                remaining=0,
                reset=int(time.time() + 3600),
            )
        
        # Find the most restrictive result
        final_result = results[0]
        for result in results[1:]:
            if not result.allowed and final_result.allowed:
                final_result = result
            elif not result.allowed and not final_result.allowed:
                # Both denied, take the one with longer retry_after
                if result.retry_after and final_result.retry_after:
                    if result.retry_after > final_result.retry_after:
                        final_result = result
        
        # Merge metadata from all results
        for result in results:
            if result.cost_used > 0:
                final_result.cost_used = result.cost_used
            if result.cost_remaining is not None:
                final_result.cost_remaining = result.cost_remaining
            if result.cost_limit is not None:
                final_result.cost_limit = result.cost_limit
            if result.concurrent_used is not None:
                final_result.concurrent_used = result.concurrent_used
            if result.concurrent_limit is not None:
                final_result.concurrent_limit = result.concurrent_limit
        
        return final_result
    
    async def increment(self, key: str, config: RateLimitConfig, cost: float = 0.0) -> None:
        """Increment all applicable limiters."""
        # Increment base limiter
        if config.algorithm in [RateLimitAlgorithm.TOKEN_BUCKET, RateLimitAlgorithm.SLIDING_WINDOW]:
            base_limiter = self.limiters.get(config.algorithm)
            if base_limiter:
                await base_limiter.increment(key, config, cost)
        
        # Increment cost-based limiter
        if config.cost_per_request > 0 or cost > 0:
            cost_limiter = self.limiters.get(RateLimitAlgorithm.COST_BASED)
            if cost_limiter:
                await cost_limiter.increment(key, config, cost)
    
    async def get_usage(self, key: str, config: RateLimitConfig) -> Dict[str, Any]:
        """Get combined usage from all limiters."""
        usage = {}
        
        # Get usage from each limiter
        for algorithm, limiter in self.limiters.items():
            if algorithm == config.algorithm or (
                algorithm == RateLimitAlgorithm.COST_BASED and config.cost_per_request > 0
            ) or (
                algorithm == RateLimitAlgorithm.CONCURRENCY and config.max_concurrent is not None
            ):
                limiter_usage = await limiter.get_usage(key, config)
                usage.update(limiter_usage)
        
        return usage


class AbuseDetector:
    """Detects abuse patterns in API usage."""
    
    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self.redis = redis_client
        self.patterns: Dict[AbuseType, Callable] = {
            AbuseType.BURST_ATTACK: self._detect_burst_attack,
            AbuseType.DISTRIBUTED_ATTACK: self._detect_distributed_attack,
            AbuseType.SLOW_LORIS: self._detect_slow_loris,
            AbuseType.API_SCAN: self._detect_api_scan,
            AbuseType.CREDENTIAL_STUFFING: self._detect_credential_stuffing,
            AbuseType.DATA_SCRAPING: self._detect_data_scraping,
            AbuseType.RESOURCE_EXHAUSTION: self._detect_resource_exhaustion,
            AbuseType.COST_OPTIMIZATION_ABUSE: self._detect_cost_optimization_abuse,
        }
        
        # Abuse scores per identifier
        self.abuse_scores: Dict[str, float] = {}
        
    async def analyze_request(self, request: Request, user_context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze request for abuse patterns."""
        scores = {}
        total_score = 0
        
        for abuse_type, detector in self.patterns.items():
            score = await detector(request, user_context)
            scores[abuse_type.value] = score
            total_score += score
        
        # Calculate weighted total
        weighted_total = total_score * self._get_weight_factor(request)
        
        # Store abuse score
        identifier = self._get_identifier(request, user_context)
        if identifier:
            self.abuse_scores[identifier] = weighted_total
        
        return {
            "scores": scores,
            "total_score": total_score,
            "weighted_total": weighted_total,
            "abuse_detected": weighted_total > 0.7,  # Threshold
            "identifier": identifier,
        }
    
    def _get_identifier(self, request: Request, user_context: Dict[str, Any]) -> Optional[str]:
        """Get identifier for abuse tracking."""
        if user_context.get("user_id"):
            return f"user:{user_context['user_id']}"
        elif request.client:
            return f"ip:{request.client.host}"
        return None
    
    def _get_weight_factor(self, request: Request) -> float:
        """Get weight factor based on request characteristics."""
        weight = 1.0
        
        # Sensitive endpoints have higher weight
        sensitive_paths = ["/auth/", "/admin/", "/billing/", "/data/"]
        if any(request.url.path.startswith(path) for path in sensitive_paths):
            weight *= 1.5
        
        # POST/PUT/DELETE have higher weight
        if request.method in ["POST", "PUT", "DELETE"]:
            weight *= 1.3
        
        return weight
    
    async def _detect_burst_attack(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect burst attack patterns."""
        identifier = self._get_identifier(request, user_context)
        if not identifier or not self.redis:
            return 0.0
        
        burst_key = f"abuse:burst:{identifier}"
        current_time = time.time()
        
        # Track request timestamps
        await self.redis.zadd(burst_key, {str(current_time): current_time})
        await self.redis.zremrangebyscore(burst_key, 0, current_time - 10)  # 10 second window
        await self.redis.expire(burst_key, 60)
        
        # Count requests in last second
        recent = await self.redis.zcount(burst_key, current_time - 1, current_time)
        
        # High burst detection
        if recent > 50:  # 50 requests per second
            return 1.0
        elif recent > 20:  # 20 requests per second
            return 0.5
        
        return 0.0
    
    async def _detect_distributed_attack(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect distributed attack from multiple IPs."""
        # This would require more sophisticated analysis
        # For now, check for multiple user agents from same user
        user_id = user_context.get("user_id")
        if not user_id or not self.redis:
            return 0.0
        
        user_agent = request.headers.get("user-agent", "")
        ua_key = f"abuse:user_agents:{user_id}"
        
        # Track unique user agents
        await self.redis.sadd(ua_key, user_agent)
        await self.redis.expire(ua_key, 3600)
        
        unique_agents = await self.redis.scard(ua_key)
        
        if unique_agents > 5:  # More than 5 different user agents in an hour
            return 0.8
        elif unique_agents > 3:
            return 0.3
        
        return 0.0
    
    async def _detect_slow_loris(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect slow loris attacks."""
        # Check for unusually slow requests
        # This would need to track request duration
        return 0.0  # Simplified
    
    async def _detect_api_scan(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect API scanning patterns."""
        path = request.url.path
        
        # Check for common scanning patterns
        scanning_patterns = [
            "/admin", "/phpmyadmin", "/wp-admin", "/.env", "/config",
            "/.git", "/.svn", "/backup", "/sql", "/debug",
        ]
        
        for pattern in scanning_patterns:
            if pattern in path.lower():
                return 0.9
        
        # Check for enumeration patterns
        if re.search(r"/\d+/", path) and len(re.findall(r"/\d+/", path)) > 3:
            return 0.6
        
        return 0.0
    
    async def _detect_credential_stuffing(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect credential stuffing attacks."""
        if request.url.path != "/auth/login":
            return 0.0
        
        ip_address = request.client.host if request.client else None
        if not ip_address or not self.redis:
            return 0.0
        
        login_key = f"abuse:logins:{ip_address}"
        
        # Track login attempts
        await self.redis.incr(login_key)
        await self.redis.expire(login_key, 300)  # 5 minute window
        
        attempts = int(await self.redis.get(login_key) or 0)
        
        if attempts > 10:  # More than 10 login attempts in 5 minutes
            return 1.0
        elif attempts > 5:
            return 0.5
        
        return 0.0
    
    async def _detect_data_scraping(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect data scraping patterns."""
        if request.method != "GET":
            return 0.0
        
        # Check for pagination abuse
        page_size = request.query_params.get("limit")
        if page_size and int(page_size) > 1000:  # Unusually large page size
            return 0.7
        
        # Check for rapid sequential access
        # This would need to track access patterns
        return 0.0
    
    async def _detect_resource_exhaustion(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect resource exhaustion attempts."""
        # Check for expensive operations
        expensive_patterns = [
            ("/agents/", "execute"),
            ("/data/", "export"),
            ("/reports/", "generate"),
        ]
        
        for path_pattern, operation in expensive_patterns:
            if path_pattern in request.url.path and operation in request.url.path:
                return 0.4
        
        # Check for large payloads
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB
            return 0.3
        
        return 0.0
    
    async def _detect_cost_optimization_abuse(self, request: Request, user_context: Dict[str, Any]) -> float:
        """Detect abuse of cost optimization features."""
        if "/cost-optimization" not in request.url.path:
            return 0.0
        
        # Check for excessive optimization requests
        user_id = user_context.get("user_id")
        if not user_id or not self.redis:
            return 0.0
        
        opt_key = f"abuse:optimizations:{user_id}"
        await self.redis.incr(opt_key)
        await self.redis.expire(opt_key, 60)  # 1 minute window
        
        attempts = int(await self.redis.get(opt_key) or 0)
        
        if attempts > 20:  # More than 20 optimization requests per minute
            return 0.8
        elif attempts > 10:
            return 0.4
        
        return 0.0
    
    async def get_abuse_score(self, identifier: str) -> float:
        """Get current abuse score for identifier."""
        return self.abuse_scores.get(identifier, 0.0)
    
    async def reset_abuse_score(self, identifier: str) -> None:
        """Reset abuse score for identifier."""
        if identifier in self.abuse_scores:
            del self.abuse_scores[identifier]


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""
    
    def __init__(
        self,
        app,
        redis_client: Optional[aioredis.Redis] = None,
        configs: Optional[List[RateLimitConfig]] = None,
        security_manager: Optional[SecurityManager] = None,
    ):
        super().__init__(app)
        self.redis = redis_client
        self.security_manager = security_manager or get_security_manager()
        self.logger = get_logger("api.middleware.rate_limiting")
        
        # Default configurations
        self.configs = configs or self._get_default_configs()
        
        # Initialize limiters
        self.limiters = self._initialize_limiters()
        self.combined_limiter = CombinedRateLimiter(self.limiters)
        
        # Abuse detector
        self.abuse_detector = AbuseDetector(redis_client)
        
        # Request tracking for graceful degradation
        self.request_tracker: Dict[str, List[float]] = defaultdict(list)
        
        # Billing integration (simplified)
        self.billing_cache: Dict[str, Quota] = {}
        
    def _get_default_configs(self) -> List[RateLimitConfig]:
        """Get default rate limit configurations."""
        return [
            # Free tier
            RateLimitConfig(
                name="free_tier",
                algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
                rate=100,  # 100 requests
                period=3600,  # per hour
                burst=10,
                tier=RateLimitTier.FREE,
                scope=RateLimitScope.USER,
            ),
            
            # Basic tier
            RateLimitConfig(
                name="basic_tier",
                algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
                rate=1000,  # 1000 requests
                period=3600,  # per hour
                burst=50,
                tier=RateLimitTier.BASIC,
                scope=RateLimitScope.USER,
            ),
            
            # Professional tier
            RateLimitConfig(
                name="professional_tier",
                algorithm=RateLimitAlgorithm.ADAPTIVE,
                rate=10000,  # 10000 requests
                period=3600,  # per hour
                burst=100,
                tier=RateLimitTier.PROFESSIONAL,
                scope=RateLimitScope.USER,
                min_rate=5000,
                max_rate=20000,
            ),
            
            # Enterprise tier
            RateLimitConfig(
                name="enterprise_tier",
                algorithm=RateLimitAlgorithm.ADAPTIVE,
                rate=100000,  # 100000 requests
                period=3600,  # per hour
                burst=1000,
                tier=RateLimitTier.ENTERPRISE,
                scope=RateLimitScope.ORGANIZATION,
                min_rate=50000,
                max_rate=200000,
            ),
            
            # IP-based rate limiting (abuse prevention)
            RateLimitConfig(
                name="ip_protection",
                algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
                rate=1000,  # 1000 requests
                period=60,  # per minute
                burst=100,
                scope=RateLimitScope.IP_ADDRESS,
            ),
            
            # Cost-based limiting for expensive operations
            RateLimitConfig(
                name="cost_based",
                algorithm=RateLimitAlgorithm.COST_BASED,
                rate=100,  # Not used for cost-based
                period=86400,  # Daily budget
                cost_per_request=0.01,  # $0.01 per request
                max_cost_per_period=10.0,  # $10 daily limit
                scope=RateLimitScope.USER,
            ),
            
            # Concurrency limiting for resource-intensive endpoints
            RateLimitConfig(
                name="concurrency",
                algorithm=RateLimitAlgorithm.CONCURRENCY,
                rate=100,  # Not used for concurrency
                period=60,
                max_concurrent=5,
                scope=RateLimitScope.USER,
            ),
        ]
    
    def _initialize_limiters(self) -> Dict[RateLimitAlgorithm, RateLimiter]:
        """Initialize rate limiters."""
        token_bucket = TokenBucketLimiter(self.redis)
        sliding_window = SlidingWindowLimiter(self.redis)
        adaptive = AdaptiveRateLimiter(token_bucket, self.redis)
        cost_based = CostBasedLimiter(self.redis)
        concurrency = ConcurrencyLimiter(self.redis)
        
        return {
            RateLimitAlgorithm.TOKEN_BUCKET: token_bucket,
            RateLimitAlgorithm.SLIDING_WINDOW: sliding_window,
            RateLimitAlgorithm.ADAPTIVE: adaptive,
            RateLimitAlgorithm.COST_BASED: cost_based,
            RateLimitAlgorithm.CONCURRENCY: concurrency,
        }
    
    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting."""
        # Skip rate limiting for certain paths
        if await self._should_skip_rate_limiting(request):
            return await call_next(request)
        
        # Extract user context
        user_context = await self._extract_user_context(request)
        
        # Get applicable rate limit configurations
        configs = await self._get_applicable_configs(request, user_context)
        
        # Check rate limits
        rate_limit_results = []
        for config in configs:
            # Generate key for this rate limit
            key = self._generate_key(config, request, user_context)
            
            # Check rate limit
            result = await self.combined_limiter.check(key, config)
            rate_limit_results.append((config, result))
            
            # If not allowed, return rate limit error
            if not result.allowed:
                return await self._handle_rate_limit_exceeded(
                    request, config, result, rate_limit_results
                )
        
        # Check for abuse patterns
        abuse_analysis = await self.abuse_detector.analyze_request(request, user_context)
        if abuse_analysis["abuse_detected"]:
            return await self._handle_abuse_detected(request, abuse_analysis)
        
        # Process request
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time
        
        # Calculate request cost
        request_cost = await self._calculate_request_cost(request, response, duration)
        
        # Increment rate limiters
        for config, result in rate_limit_results:
            key = self._generate_key(config, request, user_context)
            await self.combined_limiter.increment(key, config, request_cost)
        
        # Add rate limit headers
        if rate_limit_results:
            # Use the most restrictive result for headers
            most_restrictive = min(
                rate_limit_results,
                key=lambda x: (0 if x[1].allowed else 1, x[1].retry_after or float('inf'))
            )[1]
            
            for header, value in most_restrictive.to_headers().items():
                response.headers[header] = value
        
        # Add abuse detection headers if abuse was detected
        if abuse_analysis["total_score"] > 0:
            response.headers["X-Abuse-Score"] = str(abuse_analysis["weighted_total"])
            response.headers["X-Abuse-Detected"] = str(abuse_analysis["abuse_detected"]).lower()
        
        # Track request for graceful degradation
        await self._track_request(request, user_context, duration)
        
        return response
    
    async def _should_skip_rate_limiting(self, request: Request) -> bool:
        """Check if rate limiting should be skipped for this request."""
        skip_paths = [
            "/health",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
        ]
        
        if request.url.path in skip_paths:
            return True
        
        # Skip for internal services
        if request.headers.get("X-Internal-Service") == "true":
            return True
        
        # Skip for certain HTTP methods
        if request.method == "OPTIONS":
            return True
        
        return False
    
    async def _extract_user_context(self, request: Request) -> Dict[str, Any]:
        """Extract user context from request."""
        user_context = {
            "user_id": None,
            "organization_id": None,
            "tier": RateLimitTier.FREE,
            "ip_address": request.client.host if request.client else None,
            "api_key": None,
            "session_id": None,
        }
        
        # Try to get from authentication middleware
        if hasattr(request.state, 'auth'):
            auth = request.state.auth
            user_context["user_id"] = getattr(auth, 'user_id', None)
            user_context["organization_id"] = getattr(auth, 'session', {}).get("organization_id")
            # Determine tier from roles or other attributes
            if hasattr(auth, 'roles'):
                if 'admin' in [r.value for r in auth.roles]:
                    user_context["tier"] = RateLimitTier.ENTERPRISE
                elif 'premium' in [r.value for r in auth.roles]:
                    user_context["tier"] = RateLimitTier.PROFESSIONAL
        
        # Extract API key
        api_key = request.headers.get("X-API-Key")
        if api_key:
            user_context["api_key"] = api_key
            # Determine tier from API key
            if api_key.startswith("ma_enterprise_"):
                user_context["tier"] = RateLimitTier.ENTERPRISE
            elif api_key.startswith("ma_pro_"):
                user_context["tier"] = RateLimitTier.PROFESSIONAL
            elif api_key.startswith("ma_basic_"):
                user_context["tier"] = RateLimitTier.BASIC
        
        # Extract session ID
        session_id = request.headers.get("X-Session-ID") or request.cookies.get("session_id")
        if session_id:
            user_context["session_id"] = session_id
        
        return user_context
    
    async def _get_applicable_configs(self, request: Request, 
                                    user_context: Dict[str, Any]) -> List[RateLimitConfig]:
        """Get rate limit configurations applicable to this request."""
        applicable = []
        
        for config in self.configs:
            if not config.enabled:
                continue
            
            # Check scope matching
            if config.scope == RateLimitScope.GLOBAL:
                applicable.append(config)
            elif config.scope == RateLimitScope.USER and user_context["user_id"]:
                applicable.append(config)
            elif config.scope == RateLimitScope.ORGANIZATION and user_context["organization_id"]:
                applicable.append(config)
            elif config.scope == RateLimitScope.IP_ADDRESS and user_context["ip_address"]:
                applicable.append(config)
            elif config.scope == RateLimitScope.API_KEY and user_context["api_key"]:
                applicable.append(config)
            elif config.scope == RateLimitScope.SESSION and user_context["session_id"]:
                applicable.append(config)
            elif config.scope == RateLimitScope.ENDPOINT:
                # Apply to specific endpoints
                if self._is_endpoint_limited(request.url.path, config):
                    applicable.append(config)
        
        # Filter by tier
        user_tier = user_context["tier"]
        filtered = []
        for config in applicable:
            if config.tier == RateLimitTier.UNLIMITED:
                continue
            elif config.tier == user_tier:
                filtered.append(config)
            elif user_tier == RateLimitTier.ENTERPRISE:
                # Enterprise gets all lower tiers
                filtered.append(config)
            elif user_tier == RateLimitTier.PROFESSIONAL and config.tier in [RateLimitTier.BASIC, RateLimitTier.FREE]:
                filtered.append(config)
            elif user_tier == RateLimitTier.BASIC and config.tier == RateLimitTier.FREE:
                filtered.append(config)
        
        return filtered
    
    def _is_endpoint_limited(self, path: str, config: RateLimitConfig) -> bool:
        """Check if endpoint matches rate limit configuration."""
        # Simple implementation - could be extended with regex patterns
        endpoint_patterns = {
            "free_tier": ["/api/v1/"],
            "basic_tier": ["/api/v1/", "/api/v2/"],
            "professional_tier": ["/api/"],
            "enterprise_tier": ["/"],
            "ip_protection": ["/"],
            "cost_based": ["/agents/execute", "/data/process", "/reports/generate"],
            "concurrency": ["/agents/execute", "/data/export", "/reports/generate"],
        }
        
        patterns = endpoint_patterns.get(config.name, [])
        for pattern in patterns:
            if path.startswith(pattern):
                return True
        
        return False
    
    def _generate_key(self, config: RateLimitConfig, request: Request, 
                     user_context: Dict[str, Any]) -> str:
        """Generate key for rate limiting."""
        identifier = self._get_identifier(config.scope, user_context)
        return config.get_key(identifier)
    
    def _get_identifier(self, scope: RateLimitScope, user_context: Dict[str, Any]) -> str:
        """Get identifier for rate limiting scope."""
        if scope == RateLimitScope.USER:
            return user_context["user_id"] or "anonymous"
        elif scope == RateLimitScope.ORGANIZATION:
            return user_context["organization_id"] or "no_org"
        elif scope == RateLimitScope.IP_ADDRESS:
            return user_context["ip_address"] or "unknown"
        elif scope == RateLimitScope.API_KEY:
            return user_context["api_key"] or "no_key"
        elif scope == RateLimitScope.SESSION:
            return user_context["session_id"] or "no_session"
        elif scope == RateLimitScope.ENDPOINT:
            return "global"  # Endpoint-specific would need path
        else:  # GLOBAL
            return "global"
    
    async def _calculate_request_cost(self, request: Request, response: Response, 
                                    duration: float) -> float:
        """Calculate cost of request for cost-based limiting."""
        base_cost = 0.0
        
        # Cost based on endpoint
        path = request.url.path
        if "/agents/execute" in path:
            base_cost = 0.01  # $0.01 per agent execution
        elif "/data/process" in path:
            base_cost = 0.005  # $0.005 per data processing
        elif "/reports/generate" in path:
            base_cost = 0.02  # $0.02 per report
        
        # Adjust based on duration
        if duration > 5.0:  # More than 5 seconds
            base_cost *= 2
        
        # Adjust based on response size
        if hasattr(response, 'body'):
            response_size = len(response.body) if response.body else 0
            if response_size > 10 * 1024 * 1024:  # More than 10MB
                base_cost *= 1.5
        
        return base_cost
    
    async def _handle_rate_limit_exceeded(self, request: Request, config: RateLimitConfig,
                                        result: RateLimitResult, 
                                        all_results: List[Tuple[RateLimitConfig, RateLimitResult]]) -> Response:
        """Handle rate limit exceeded."""
        self.logger.warning(
            "rate_limit_exceeded",
            path=request.url.path,
            method=request.method,
            user_id=getattr(request.state, 'user_id', None),
            ip_address=request.client.host if request.client else None,
            config_name=config.name,
            scope=config.scope.value,
            tier=config.tier.value,
            limit=result.limit,
            remaining=result.remaining,
            retry_after=result.retry_after,
        )
        
        # Check if graceful degradation should be applied
        if config.degrade_on_limit:
            degraded_response = await self._apply_graceful_degradation(request, config)
            if degraded_response:
                # Add rate limit headers
                for header, value in result.to_headers().items():
                    degraded_response.headers[header] = value
                degraded_response.headers["X-RateLimit-Degraded"] = "true"
                return degraded_response
        
        # Return rate limit error
        error_detail = {
            "error": "rate_limit_exceeded",
            "message": f"Rate limit exceeded for {config.scope.value}",
            "limit": result.limit,
            "remaining": result.remaining,
            "reset": result.reset,
            "retry_after": result.retry_after,
            "scope": config.scope.value,
            "tier": config.tier.value,
        }
        
        if result.cost_remaining is not None:
            error_detail["cost_remaining"] = result.cost_remaining
            error_detail["cost_limit"] = result.cost_limit
        
        headers = result.to_headers()
        headers["Content-Type"] = "application/json"
        
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=error_detail,
            headers=headers,
        )
    
    async def _handle_abuse_detected(self, request: Request, 
                                   abuse_analysis: Dict[str, Any]) -> Response:
        """Handle detected abuse."""
        self.logger.warning(
            "abuse_detected",
            path=request.url.path,
            method=request.method,
            ip_address=request.client.host if request.client else None,
            abuse_scores=abuse_analysis["scores"],
            total_score=abuse_analysis["total_score"],
            identifier=abuse_analysis["identifier"],
        )
        
        # Log security event
        log_security_event(
            event_type="api_abuse_detected",
            severity="high",
            source_ip=request.client.host if request.client else None,
            user_id=getattr(request.state, 'user_id', None),
            details=abuse_analysis,
        )
        
        # Return abuse detection error
        error_detail = {
            "error": "abuse_detected",
            "message": "Suspicious activity detected",
            "abuse_score": abuse_analysis["weighted_total"],
            "retry_after": 60,  # 1 minute cooldown
        }
        
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=error_detail,
            headers={
                "Retry-After": "60",
                "X-Abuse-Detected": "true",
                "X-Abuse-Score": str(abuse_analysis["weighted_total"]),
            },
        )
    
    async def _apply_graceful_degradation(self, request: Request, 
                                        config: RateLimitConfig) -> Optional[Response]:
        """Apply graceful degradation when rate limit is exceeded."""
        if config.degradation_level == GracefulDegradationLevel.READ_ONLY:
            # Only allow GET requests
            if request.method != "GET":
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": "degraded_mode",
                        "message": "Service is in read-only mode due to rate limiting",
                    },
                )
        
        elif config.degradation_level == GracefulDegradationLevel.LIMITED:
            # Reduce functionality
            if "/agents/execute" in request.url.path:
                # Limit agent execution to simple agents only
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": "degraded_mode",
                        "message": "Complex agent execution disabled due to rate limiting",
                    },
                )
        
        elif config.degradation_level == GracefulDegradationLevel.DEGRADED:
            # Only essential features
            essential_paths = ["/health", "/auth/login", "/api/v1/essential"]
            if not any(request.url.path.startswith(p) for p in essential_paths):
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "error": "degraded_mode",
                        "message": "Service degraded due to rate limiting",
                    },
                )
        
        return None
    
    async def _track_request(self, request: Request, user_context: Dict[str, Any], 
                           duration: float) -> None:
        """Track request for monitoring and dynamic adjustment."""
        identifier = user_context.get("user_id") or user_context.get("ip_address")
        if not identifier:
            return
        
        current_time = time.time()
        
        # Track request timing
        if identifier not in self.request_tracker:
            self.request_tracker[identifier] = []
        
        self.request_tracker[identifier].append(current_time)
        
        # Keep only last hour of requests
        cutoff = current_time - 3600
        self.request_tracker[identifier] = [t for t in self.request_tracker[identifier] if t > cutoff]
        
        # Clean old identifiers
        old_identifiers = [id for id, timestamps in self.request_tracker.items() 
                          if not timestamps or timestamps[-1] < cutoff]
        for id in old_identifiers:
            del self.request_tracker[id]
    
    async def get_rate_limit_status(self, identifier: str, scope: RateLimitScope) -> Dict[str, Any]:
        """Get rate limit status for identifier."""
        status = {}
        
        for config in self.configs:
            if config.scope != scope:
                continue
            
            key = config.get_key(identifier)
            usage = await self.combined_limiter.get_usage(key, config)
            
            status[config.name] = {
                "config": config.__dict__,
                "usage": usage,
            }
        
        return status
    
    async def reset_rate_limits(self, identifier: str, scope: RateLimitScope) -> bool:
        """Reset rate limits for identifier."""
        # This would clear Redis keys or local state
        # Implementation depends on storage backend
        return True


# FastAPI dependency for rate limiting
async def check_rate_limit(
    request: Request,
    scope: RateLimitScope = RateLimitScope.USER,
    tier: Optional[RateLimitTier] = None,
) -> RateLimitResult:
    """Dependency to check rate limits in route handlers."""
    # This would integrate with the middleware's limiter
    # For now, return a dummy result
    return RateLimitResult(
        allowed=True,
        limit=100,
        remaining=99,
        reset=int(time.time() + 3600),
    )


# Decorator for custom rate limiting
def rate_limited(
    config_name: str,
    scope: RateLimitScope = RateLimitScope.USER,
):
    """Decorator for custom rate limiting on specific endpoints."""
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Find request in args/kwargs
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            
            if not request:
                request = kwargs.get('request')
            
            if not request:
                raise RuntimeError("Request object not found")
            
            # Get rate limiting middleware from app state
            app = request.app
            if not hasattr(app.state, 'rate_limiting_middleware'):
                return await func(*args, **kwargs)
            
            middleware = app.state.rate_limiting_middleware
            
            # Extract user context
            user_context = await middleware._extract_user_context(request)
            
            # Find config
            config = None
            for cfg in middleware.configs:
                if cfg.name == config_name and cfg.scope == scope:
                    config = cfg
                    break
            
            if not config:
                return await func(*args, **kwargs)
            
            # Check rate limit
            key = middleware._generate_key(config, request, user_context)
            result = await middleware.combined_limiter.check(key, config)
            
            if not result.allowed:
                # Return rate limit error
                error_detail = {
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded for {config_name}",
                    "limit": result.limit,
                    "remaining": result.remaining,
                    "retry_after": result.retry_after,
                }
                
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content=error_detail,
                    headers=result.to_headers(),
                )
            
            # Process request
            response = await func(*args, **kwargs)
            
            # Increment rate limit
            cost = await middleware._calculate_request_cost(request, response, 0.0)
            await middleware.combined_limiter.increment(key, config, cost)
            
            # Add headers
            for header, value in result.to_headers().items():
                response.headers[header] = value
            
            return response
        
        return wrapper
    
    return decorator


# Setup function
def setup_rate_limiting_middleware(
    app: FastAPI,
    redis_url: Optional[str] = None,
    configs: Optional[List[RateLimitConfig]] = None,
) -> RateLimitingMiddleware:
    """Setup rate limiting middleware for FastAPI app."""
    # Create Redis client if URL provided
    redis_client = None
    if redis_url:
        import aioredis
        redis_client = aioredis.from_url(redis_url, decode_responses=False)
    
    # Create middleware
    middleware = RateLimitingMiddleware(
        app,
        redis_client=redis_client,
        configs=configs,
    )
    
    # Add middleware to app
    app.add_middleware(RateLimitingMiddleware, redis_client=redis_client, configs=configs)
    
    # Store in app state for access in dependencies
    app.state.rate_limiting_middleware = middleware
    
    # Add rate limit status endpoint
    @app.get("/rate-limit/status")
    async def get_rate_limit_status(
        request: Request,
        scope: RateLimitScope = RateLimitScope.USER,
        identifier: Optional[str] = None,
    ):
        """Get rate limit status for identifier."""
        if not identifier:
            # Use current user
            user_context = await middleware._extract_user_context(request)
            identifier = middleware._get_identifier(scope, user_context)
        
        status = await middleware.get_rate_limit_status(identifier, scope)
        return status
    
    return middleware


# Export public API
__all__ = [
    # Main classes
    "RateLimitingMiddleware",
    "RateLimitConfig",
    "RateLimitResult",
    "Quota",
    
    # Limiters
    "RateLimiter",
    "TokenBucketLimiter",
    "SlidingWindowLimiter",
    "AdaptiveRateLimiter",
    "CostBasedLimiter",
    "ConcurrencyLimiter",
    "CombinedRateLimiter",
    
    # Abuse detection
    "AbuseDetector",
    
    # Enums
    "RateLimitAlgorithm",
    "RateLimitTier",
    "RateLimitScope",
    "AbuseType",
    "QuotaUnit",
    "GracefulDegradationLevel",
    
    # FastAPI dependencies
    "check_rate_limit",
    
    # Decorators
    "rate_limited",
    
    # Setup function
    "setup_rate_limiting_middleware",
]