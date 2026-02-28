"""
Authentication middleware for MicroAgents Platform.
JWT validation, RBAC, rate limiting, and multi-auth support.
"""

import asyncio
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from functools import wraps
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from uuid import UUID, uuid4

import bcrypt
import jwt
import pyotp
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    Security,
    status,
)
from fastapi.security import (
    APIKeyCookie,
    APIKeyHeader,
    APIKeyQuery,
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2,
    OAuth2PasswordBearer,
)
from fastapi.security.utils import get_authorization_scheme_param
from jose import JWTError, jwk, jwt
from jose.constants import ALGORITHMS
from pydantic import BaseModel, EmailStr, Field, SecretStr, ValidationError, validator
from sqlalchemy.ext.asyncio import AsyncSession

# Local imports
from microagents.utils.security.utils import (
    EncryptionManager,
    HashManager,
    JWTManager,
    RateLimiter,
    SecurityManager,
    get_security_manager,
)
from microagents.utils.serialization.serializers import serialize

# Type aliases
JsonWebKey = Dict[str, Any]
TokenPayload = Dict[str, Any]
UserContext = Dict[str, Any]


class AuthMethod(str, Enum):
    """Supported authentication methods."""
    
    JWT = "jwt"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    SAML = "saml"
    LDAP = "ldap"
    BASIC = "basic"
    SOCIAL = "social"
    BIOMETRIC = "biometric"


class TokenType(str, Enum):
    """Token types."""
    
    ACCESS = "access"
    REFRESH = "refresh"
    API_KEY = "api_key"
    MFA = "mfa"
    IMPERSONATION = "impersonation"


class UserRole(str, Enum):
    """User roles for RBAC."""
    
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    ANALYST = "analyst"
    VIEWER = "viewer"
    GUEST = "guest"


class Permission(str, Enum):
    """Permissions for fine-grained access control."""
    
    # Agent permissions
    AGENT_CREATE = "agent:create"
    AGENT_READ = "agent:read"
    AGENT_UPDATE = "agent:update"
    AGENT_DELETE = "agent:delete"
    AGENT_EXECUTE = "agent:execute"
    
    # API permissions
    API_READ = "api:read"
    API_WRITE = "api:write"
    API_DELETE = "api:delete"
    
    # User management
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    
    # Organization management
    ORG_CREATE = "org:create"
    ORG_READ = "org:read"
    ORG_UPDATE = "org:update"
    ORG_DELETE = "org:delete"
    
    # Billing
    BILLING_READ = "billing:read"
    BILLING_WRITE = "billing:write"
    
    # Security
    SECURITY_READ = "security:read"
    SECURITY_WRITE = "security:write"
    
    # Monitoring
    MONITORING_READ = "monitoring:read"
    MONITORING_WRITE = "monitoring:write"


class MFAMethod(str, Enum):
    """Multi-factor authentication methods."""
    
    TOTP = "totp"  # Time-based OTP
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    WEBAUTHN = "webauthn"  # FIDO2/WebAuthn
    BACKUP_CODE = "backup_code"


class AuthSession(BaseModel):
    """Authentication session model."""
    
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    email: EmailStr
    roles: List[UserRole] = Field(default_factory=list)
    permissions: Set[Permission] = Field(default_factory=set)
    
    # Authentication info
    auth_method: AuthMethod
    mfa_verified: bool = False
    mfa_method: Optional[MFAMethod] = None
    
    # Device info
    device_id: Optional[str] = None
    device_type: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    
    # Timing
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    
    # Security
    is_active: bool = True
    refresh_token_id: Optional[str] = None
    impersonator_id: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            set: list,
        }
    
    @validator('ip_address')
    def validate_ip_address(cls, v):
        if v:
            try:
                ipaddress.ip_address(v)
            except ValueError:
                raise ValueError("Invalid IP address")
        return v
    
    def has_permission(self, permission: Permission) -> bool:
        """Check if session has specific permission."""
        return permission in self.permissions
    
    def has_any_permission(self, *permissions: Permission) -> bool:
        """Check if session has any of the given permissions."""
        return any(p in self.permissions for p in permissions)
    
    def has_all_permissions(self, *permissions: Permission) -> bool:
        """Check if session has all of the given permissions."""
        return all(p in self.permissions for p in permissions)
    
    def has_role(self, role: UserRole) -> bool:
        """Check if session has specific role."""
        return role in self.roles
    
    def is_expired(self) -> bool:
        """Check if session is expired."""
        if self.expires_at:
            return datetime.now(timezone.utc) > self.expires_at
        return False
    
    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(timezone.utc)


class RateLimitScope(str, Enum):
    """Rate limiting scopes."""
    
    GLOBAL = "global"
    USER = "user"
    ORGANIZATION = "organization"
    ENDPOINT = "endpoint"
    IP = "ip"


class RateLimitRule(BaseModel):
    """Rate limiting rule."""
    
    scope: RateLimitScope
    identifier: str  # user_id, org_id, ip, etc.
    limit: int  # requests per period
    period: int  # seconds
    burst: int = 5  # burst allowance
    
    def get_key(self) -> str:
        """Get unique key for this rule."""
        return f"ratelimit:{self.scope.value}:{self.identifier}"


class OAuthProvider(str, Enum):
    """OAuth2/OIDC providers."""
    
    GOOGLE = "google"
    GITHUB = "github"
    MICROSOFT = "microsoft"
    FACEBOOK = "facebook"
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    OKTA = "okta"
    AUTH0 = "auth0"
    KEYCLOAK = "keycloak"


class OAuthConfig(BaseModel):
    """OAuth2/OIDC provider configuration."""
    
    provider: OAuthProvider
    client_id: str
    client_secret: SecretStr
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: Optional[str] = None
    scopes: List[str] = Field(default=["openid", "profile", "email"])
    
    @validator('authorization_endpoint', 'token_endpoint', 'userinfo_endpoint')
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError("URL must start with http:// or https://")
        return v


class SAMLConfig(BaseModel):
    """SAML configuration."""
    
    idp_metadata_url: str
    sp_entity_id: str
    acs_url: str  # Assertion Consumer Service URL
    slo_url: Optional[str] = None  # Single Logout URL
    
    @validator('idp_metadata_url', 'acs_url', 'slo_url')
    def validate_url(cls, v):
        if v and not v.startswith(('http://', 'https://')):
            raise ValueError("URL must start with http:// or https://")
        return v


class LDAPConfig(BaseModel):
    """LDAP configuration."""
    
    server_uri: str
    bind_dn: str
    bind_password: SecretStr
    user_search_base: str
    user_search_filter: str = "(uid={})"
    group_search_base: Optional[str] = None
    group_search_filter: Optional[str] = None
    
    @validator('server_uri')
    def validate_ldap_uri(cls, v):
        if not v.startswith(('ldap://', 'ldaps://')):
            raise ValueError("LDAP URI must start with ldap:// or ldaps://")
        return v


class BiometricConfig(BaseModel):
    """Biometric authentication configuration."""
    
    webauthn_rp_id: str  # Relying Party ID
    webauthn_rp_name: str
    webauthn_origin: str
    webauthn_timeout: int = 60000  # milliseconds


class AuthConfig(BaseModel):
    """Authentication configuration."""
    
    # JWT configuration
    jwt_secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr(os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(64)))
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    
    # Session configuration
    session_timeout_minutes: int = 120
    mfa_timeout_minutes: int = 5
    
    # Rate limiting
    rate_limit_per_minute: int = 60
    rate_limit_per_hour: int = 1000
    rate_limit_per_day: int = 10000
    
    # Security
    require_mfa: bool = False
    max_login_attempts: int = 5
    lockout_minutes: int = 30
    password_history_size: int = 5
    password_min_age_days: int = 1
    
    # API keys
    api_key_prefix: str = "ma_"
    api_key_length: int = 32
    
    # OAuth2/OIDC
    oauth_providers: Dict[OAuthProvider, OAuthConfig] = Field(default_factory=dict)
    
    # SAML
    saml_config: Optional[SAMLConfig] = None
    
    # LDAP
    ldap_config: Optional[LDAPConfig] = None
    
    # Biometric
    biometric_config: Optional[BiometricConfig] = None
    
    # CORS
    allowed_origins: List[str] = Field(default=["*"])
    
    class Config:
        json_encoders = {
            SecretStr: lambda v: v.get_secret_value() if v else None,
        }


class TokenValidator(ABC):
    """Abstract base class for token validators."""
    
    @abstractmethod
    async def validate(self, token: str, request: Request) -> TokenPayload:
        """Validate token and return payload."""
        pass
    
    @abstractmethod
    def get_scheme(self) -> str:
        """Get authentication scheme name."""
        pass


class JWTValidator(TokenValidator):
    """JWT token validator."""
    
    def __init__(self, config: AuthConfig, security_manager: SecurityManager):
        self.config = config
        self.security_manager = security_manager
        self.jwt_manager = security_manager.jwt_manager
        
        # JWKS for public key validation
        self.jwks: Optional[JsonWebKey] = None
        self.jwks_last_updated: Optional[datetime] = None
        
    def get_scheme(self) -> str:
        return "Bearer"
    
    async def validate(self, token: str, request: Request) -> TokenPayload:
        """Validate JWT token."""
        try:
            # Verify token
            payload = self.jwt_manager.verify_token(token, token_type="access")
            
            # Check token type
            if payload.get("type") != "access":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                )
            
            # Check expiration
            exp = payload.get("exp")
            if exp and datetime.fromtimestamp(exp, timezone.utc) < datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token expired",
                )
            
            # Check issuer
            iss = payload.get("iss")
            if iss != self.config.jwt_secret_key.get_secret_value()[:16]:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid issuer",
                )
            
            # Add request metadata
            payload["_auth_method"] = AuthMethod.JWT.value
            payload["_token_type"] = TokenType.ACCESS.value
            payload["_validated_at"] = datetime.now(timezone.utc).isoformat()
            
            return payload
            
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Token validation error: {str(e)}",
            )
    
    async def refresh_access_token(self, refresh_token: str) -> Tuple[str, str]:
        """Refresh access token using refresh token."""
        try:
            # Verify refresh token
            payload = self.jwt_manager.verify_token(refresh_token, token_type="refresh")
            
            # Create new access token
            user_data = {
                "sub": payload.get("sub"),
                "email": payload.get("email"),
                "roles": payload.get("roles", []),
                "permissions": payload.get("permissions", []),
            }
            
            new_access_token = self.jwt_manager.create_access_token(user_data)
            
            # Create new refresh token (optional: rotate refresh token)
            new_refresh_token = self.jwt_manager.create_refresh_token(user_data)
            
            return new_access_token, new_refresh_token
            
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid refresh token: {str(e)}",
            )


class APIKeyValidator(TokenValidator):
    """API key validator."""
    
    def __init__(self, config: AuthConfig, security_manager: SecurityManager):
        self.config = config
        self.security_manager = security_manager
        self.api_keys: Dict[str, Dict[str, Any]] = {}  # In production, use database
        
    def get_scheme(self) -> str:
        return "ApiKey"
    
    async def validate(self, token: str, request: Request) -> TokenPayload:
        """Validate API key."""
        # Check API key format
        if not token.startswith(self.config.api_key_prefix):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format",
            )
        
        # Extract key ID and secret
        parts = token.split("_")
        if len(parts) != 3:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format",
            )
        
        key_id = parts[1]
        key_secret = parts[2]
        
        # Look up API key (in production, query database)
        api_key_info = self.api_keys.get(key_id)
        if not api_key_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        
        # Verify key secret
        if not hmac.compare_digest(api_key_info["key_secret"], key_secret):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        
        # Check if key is active
        if not api_key_info.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key is inactive",
            )
        
        # Check expiration
        expires_at = api_key_info.get("expires_at")
        if expires_at and expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key expired",
            )
        
        # Check rate limits
        if await self._is_rate_limited(key_id, request):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
        
        # Create payload
        payload = {
            "sub": api_key_info["user_id"],
            "email": api_key_info.get("email", ""),
            "roles": api_key_info.get("roles", []),
            "permissions": api_key_info.get("permissions", []),
            "api_key_id": key_id,
            "type": TokenType.API_KEY.value,
            "_auth_method": AuthMethod.API_KEY.value,
            "_validated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Update last used
        api_key_info["last_used_at"] = datetime.now(timezone.utc)
        
        return payload
    
    async def _is_rate_limited(self, key_id: str, request: Request) -> bool:
        """Check if API key is rate limited."""
        # In production, use Redis or database
        # For now, use simple in-memory tracking
        return False
    
    def generate_api_key(
        self,
        user_id: str,
        name: str,
        expires_days: Optional[int] = None,
        permissions: Optional[List[Permission]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Generate new API key."""
        # Generate key ID and secret
        key_id = secrets.token_urlsafe(16)
        key_secret = secrets.token_urlsafe(32)
        
        # Create full API key
        api_key = f"{self.config.api_key_prefix}_{key_id}_{key_secret}"
        
        # Calculate expiration
        expires_at = None
        if expires_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
        
        # Store key info
        key_info = {
            "key_id": key_id,
            "key_secret": key_secret,
            "user_id": user_id,
            "name": name,
            "permissions": permissions or [],
            "created_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
            "last_used_at": None,
            "is_active": True,
        }
        
        self.api_keys[key_id] = key_info
        
        return api_key, key_info


class OAuth2Validator(TokenValidator):
    """OAuth2/OIDC token validator."""
    
    def __init__(self, config: AuthConfig, security_manager: SecurityManager):
        self.config = config
        self.security_manager = security_manager
        self.providers: Dict[OAuthProvider, OAuthConfig] = config.oauth_providers
        
        # Cache for provider configurations
        self.provider_configs: Dict[OAuthProvider, Dict[str, Any]] = {}
        
    def get_scheme(self) -> str:
        return "Bearer"
    
    async def validate(self, token: str, request: Request) -> TokenPayload:
        """Validate OAuth2 token."""
        # Extract provider from request
        provider_name = request.headers.get("X-OAuth-Provider")
        if not provider_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth provider not specified",
            )
        
        try:
            provider = OAuthProvider(provider_name.lower())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported OAuth provider: {provider_name}",
            )
        
        # Get provider config
        provider_config = self.providers.get(provider)
        if not provider_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OAuth provider not configured: {provider}",
            )
        
        try:
            # Verify token with provider
            user_info = await self._verify_with_provider(provider, token, provider_config)
            
            # Create payload
            payload = {
                "sub": user_info.get("sub") or user_info.get("id"),
                "email": user_info.get("email"),
                "name": user_info.get("name"),
                "picture": user_info.get("picture"),
                "provider": provider.value,
                "provider_user_id": user_info.get("sub") or user_info.get("id"),
                "type": TokenType.ACCESS.value,
                "_auth_method": AuthMethod.OAUTH2.value,
                "_validated_at": datetime.now(timezone.utc).isoformat(),
            }
            
            # Add roles and permissions based on provider/user mapping
            payload["roles"] = self._map_roles_from_provider(provider, user_info)
            payload["permissions"] = self._map_permissions_from_roles(payload["roles"])
            
            return payload
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"OAuth validation failed: {str(e)}",
            )
    
    async def _verify_with_provider(
        self,
        provider: OAuthProvider,
        token: str,
        config: OAuthConfig,
    ) -> Dict[str, Any]:
        """Verify token with OAuth provider."""
        import aiohttp
        
        # Get provider configuration
        if provider not in self.provider_configs:
            async with aiohttp.ClientSession() as session:
                async with session.get(config.jwks_uri) if config.jwks_uri else None:
                    # In production, fetch and cache provider config
                    pass
        
        # Verify token based on provider
        if provider == OAuthProvider.GOOGLE:
            return await self._verify_google_token(token, config)
        elif provider == OAuthProvider.GITHUB:
            return await self._verify_github_token(token, config)
        elif provider == OAuthProvider.MICROSOFT:
            return await self._verify_microsoft_token(token, config)
        else:
            # Generic OIDC verification
            return await self._verify_oidc_token(token, config)
    
    async def _verify_google_token(self, token: str, config: OAuthConfig) -> Dict[str, Any]:
        """Verify Google OAuth token."""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            # Verify token with Google
            async with session.get(
                "https://www.googleapis.com/oauth2/v3/tokeninfo",
                params={"access_token": token},
            ) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google token",
                    )
                
                token_info = await response.json()
                
                # Get user info
                async with session.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {token}"},
                ) as user_response:
                    if user_response.status != 200:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Failed to get user info",
                        )
                    
                    user_info = await user_response.json()
                    return user_info
    
    async def _verify_github_token(self, token: str, config: OAuthConfig) -> Dict[str, Any]:
        """Verify GitHub OAuth token."""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            }
            
            async with session.get(
                "https://api.github.com/user",
                headers=headers,
            ) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid GitHub token",
                    )
                
                user_info = await response.json()
                
                # Get email if not public
                if not user_info.get("email"):
                    async with session.get(
                        "https://api.github.com/user/emails",
                        headers=headers,
                    ) as email_response:
                        if email_response.status == 200:
                            emails = await email_response.json()
                            primary_email = next(
                                (email for email in emails if email.get("primary")),
                                emails[0] if emails else None,
                            )
                            if primary_email:
                                user_info["email"] = primary_email.get("email")
                
                return user_info
    
    async def _verify_microsoft_token(self, token: str, config: OAuthConfig) -> Dict[str, Any]:
        """Verify Microsoft OAuth token."""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {token}",
            }
            
            async with session.get(
                "https://graph.microsoft.com/v1.0/me",
                headers=headers,
            ) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Microsoft token",
                    )
                
                user_info = await response.json()
                
                # Get user photo
                try:
                    async with session.get(
                        "https://graph.microsoft.com/v1.0/me/photo/$value",
                        headers=headers,
                    ) as photo_response:
                        if photo_response.status == 200:
                            # Convert photo to base64
                            photo_data = await photo_response.read()
                            user_info["picture"] = f"data:image/jpeg;base64,{base64.b64encode(photo_data).decode()}"
                except:
                    pass
                
                return user_info
    
    async def _verify_oidc_token(self, token: str, config: OAuthConfig) -> Dict[str, Any]:
        """Verify generic OIDC token."""
        import aiohttp
        
        # Decode token to get kid
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        
        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing key ID",
            )
        
        # Get JWKS
        async with aiohttp.ClientSession() as session:
            async with session.get(config.jwks_uri) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to fetch JWKS",
                    )
                
                jwks = await response.json()
        
        # Find the key
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token key",
            )
        
        # Verify token
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=[headers.get("alg", "RS256")],
                audience=config.client_id,
                issuer=config.authorization_endpoint,
            )
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token verification failed: {str(e)}",
            )
        
        # Get user info
        async with aiohttp.ClientSession() as session:
            async with session.get(
                config.userinfo_endpoint,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status != 200:
                    # Try to use payload as user info
                    return payload
                
                user_info = await response.json()
                user_info.update(payload)
                return user_info
    
    def _map_roles_from_provider(self, provider: OAuthProvider, user_info: Dict[str, Any]) -> List[UserRole]:
        """Map provider user info to roles."""
        # Default mapping - in production, use database mapping
        email = user_info.get("email", "")
        
        if provider == OAuthProvider.GOOGLE:
            # Google-specific mapping
            if email.endswith("@microagents.io"):
                return [UserRole.ADMIN]
        
        elif provider == OAuthProvider.GITHUB:
            # GitHub-specific mapping
            orgs = user_info.get("organizations_url")
            # In production, fetch organizations and map
        
        # Default role
        return [UserRole.VIEWER]
    
    def _map_permissions_from_roles(self, roles: List[UserRole]) -> List[str]:
        """Map roles to permissions."""
        permissions = set()
        
        for role in roles:
            if role == UserRole.SUPER_ADMIN:
                permissions.update([p.value for p in Permission])
            elif role == UserRole.ADMIN:
                permissions.update([
                    Permission.AGENT_CREATE, Permission.AGENT_READ, Permission.AGENT_UPDATE,
                    Permission.AGENT_DELETE, Permission.AGENT_EXECUTE,
                    Permission.USER_CREATE, Permission.USER_READ, Permission.USER_UPDATE,
                    Permission.API_READ, Permission.API_WRITE,
                    Permission.MONITORING_READ, Permission.MONITORING_WRITE,
                ])
            elif role == UserRole.DEVELOPER:
                permissions.update([
                    Permission.AGENT_CREATE, Permission.AGENT_READ, Permission.AGENT_UPDATE,
                    Permission.AGENT_EXECUTE,
                    Permission.API_READ, Permission.API_WRITE,
                ])
            elif role == UserRole.OPERATOR:
                permissions.update([
                    Permission.AGENT_READ, Permission.AGENT_EXECUTE,
                    Permission.MONITORING_READ,
                ])
            elif role == UserRole.ANALYST:
                permissions.update([
                    Permission.AGENT_READ,
                    Permission.MONITORING_READ,
                ])
            elif role == UserRole.VIEWER:
                permissions.update([
                    Permission.AGENT_READ,
                    Permission.API_READ,
                ])
        
        return list(permissions)


class AuthenticationMiddleware:
    """Main authentication middleware."""
    
    def __init__(
        self,
        app: FastAPI,
        config: Optional[AuthConfig] = None,
        security_manager: Optional[SecurityManager] = None,
    ):
        self.app = app
        self.config = config or AuthConfig()
        self.security_manager = security_manager or get_security_manager()
        
        # Initialize validators
        self.validators: Dict[AuthMethod, TokenValidator] = {
            AuthMethod.JWT: JWTValidator(self.config, self.security_manager),
            AuthMethod.API_KEY: APIKeyValidator(self.config, self.security_manager),
            AuthMethod.OAUTH2: OAuth2Validator(self.config, self.security_manager),
        }
        
        # Session store (in production, use Redis/database)
        self.sessions: Dict[str, AuthSession] = {}
        
        # Rate limiter
        self.rate_limiter = RateLimiter()
        
        # MFA manager
        self.mfa_manager = MFAManager(self.config)
        
        # Setup middleware
        self._setup_middleware()
    
    def _setup_middleware(self) -> None:
        """Setup FastAPI middleware."""
        
        @self.app.middleware("http")
        async def authentication_middleware(request: Request, call_next) -> Response:
            # Skip authentication for public endpoints
            if await self._is_public_endpoint(request):
                return await call_next(request)
            
            try:
                # Extract and validate token
                auth_result = await self.authenticate_request(request)
                
                if auth_result.requires_mfa and not auth_result.mfa_verified:
                    # Require MFA
                    return Response(
                        content=json.dumps({
                            "error": "MFA_REQUIRED",
                            "message": "Multi-factor authentication required",
                            "mfa_methods": auth_result.available_mfa_methods,
                        }),
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        media_type="application/json",
                        headers={"WWW-Authenticate": "MFA realm=\"microagents\""},
                    )
                
                # Check rate limits
                if await self._is_rate_limited(request, auth_result):
                    return Response(
                        content=json.dumps({
                            "error": "RATE_LIMITED",
                            "message": "Rate limit exceeded",
                            "retry_after": 60,
                        }),
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        media_type="application/json",
                    )
                
                # Check permissions
                if not await self._has_permission(request, auth_result):
                    return Response(
                        content=json.dumps({
                            "error": "FORBIDDEN",
                            "message": "Insufficient permissions",
                        }),
                        status_code=status.HTTP_403_FORBIDDEN,
                        media_type="application/json",
                    )
                
                # Add authentication context to request state
                request.state.auth = auth_result
                request.state.user_id = auth_result.user_id
                request.state.roles = auth_result.roles
                request.state.permissions = auth_result.permissions
                request.state.session = auth_result.session
                
                # Audit log
                await self._audit_request(request, auth_result)
                
                # Process request
                response = await call_next(request)
                
                # Add security headers
                response = self._add_security_headers(response)
                
                return response
                
            except HTTPException as e:
                # Log authentication failures
                await self._audit_failure(request, str(e))
                raise e
            
            except Exception as e:
                # Log unexpected errors
                await self._audit_failure(request, str(e))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Authentication error",
                )
    
    async def authenticate_request(self, request: Request) -> 'AuthResult':
        """Authenticate request and return authentication result."""
        # Extract token from request
        token = await self._extract_token(request)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Determine auth method
        auth_method = await self._detect_auth_method(request, token)
        
        # Get appropriate validator
        validator = self.validators.get(auth_method)
        if not validator:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Unsupported authentication method: {auth_method}",
            )
        
        # Validate token
        try:
            payload = await validator.validate(token, request)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Authentication failed: {str(e)}",
            )
        
        # Get or create session
        session = await self._get_or_create_session(payload, request, auth_method)
        
        # Check if MFA is required
        requires_mfa = (
            self.config.require_mfa and
            not session.mfa_verified and
            auth_method != AuthMethod.API_KEY  # API keys don't use MFA
        )
        
        # Create auth result
        return AuthResult(
            user_id=payload.get("sub", ""),
            email=payload.get("email", ""),
            roles=[UserRole(r) for r in payload.get("roles", [])],
            permissions={Permission(p) for p in payload.get("permissions", [])},
            auth_method=auth_method,
            session=session,
            requires_mfa=requires_mfa,
            mfa_verified=session.mfa_verified,
            available_mfa_methods=self.mfa_manager.get_available_methods(session.user_id),
        )
    
    async def _extract_token(self, request: Request) -> Optional[str]:
        """Extract token from request."""
        # Check Authorization header
        auth_header = request.headers.get("Authorization")
        if auth_header:
            scheme, token = get_authorization_scheme_param(auth_header)
            if token:
                return token
        
        # Check API key in header
        api_key_header = request.headers.get("X-API-Key")
        if api_key_header:
            return api_key_header
        
        # Check API key in query
        api_key_query = request.query_params.get("api_key")
        if api_key_query:
            return api_key_query
        
        # Check cookie
        api_key_cookie = request.cookies.get("api_key")
        if api_key_cookie:
            return api_key_cookie
        
        return None
    
    async def _detect_auth_method(self, request: Request, token: str) -> AuthMethod:
        """Detect authentication method from token and request."""
        # Check for API key format
        if token.startswith(self.config.api_key_prefix):
            return AuthMethod.API_KEY
        
        # Check for OAuth provider header
        if request.headers.get("X-OAuth-Provider"):
            return AuthMethod.OAUTH2
        
        # Check Authorization header scheme
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            # Check if it's a JWT
            try:
                # Try to decode as JWT
                jwt.get_unverified_header(token)
                return AuthMethod.JWT
            except (JWTError, Exception):
                # Might be OAuth2 token
                return AuthMethod.OAUTH2
        
        # Default to JWT
        return AuthMethod.JWT
    
    async def _get_or_create_session(
        self,
        payload: TokenPayload,
        request: Request,
        auth_method: AuthMethod,
    ) -> AuthSession:
        """Get existing session or create new one."""
        user_id = payload.get("sub", "")
        session_id = payload.get("session_id") or self._generate_session_id(request)
        
        # Check for existing session
        if session_id in self.sessions:
            session = self.sessions[session_id]
            if not session.is_expired():
                session.update_activity()
                return session
        
        # Create new session
        session = AuthSession(
            session_id=session_id,
            user_id=user_id,
            email=payload.get("email", ""),
            roles=[UserRole(r) for r in payload.get("roles", [])],
            permissions={Permission(p) for p in payload.get("permissions", [])},
            auth_method=auth_method,
            device_id=request.headers.get("X-Device-ID"),
            device_type=request.headers.get("X-Device-Type"),
            user_agent=request.headers.get("User-Agent"),
            ip_address=request.client.host if request.client else None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=self.config.session_timeout_minutes),
        )
        
        # Store session
        self.sessions[session_id] = session
        
        return session
    
    def _generate_session_id(self, request: Request) -> str:
        """Generate unique session ID."""
        user_agent = request.headers.get("User-Agent", "")
        ip = request.client.host if request.client else "unknown"
        
        data = f"{user_agent}_{ip}_{time.time()}_{secrets.token_urlsafe(8)}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    async def _is_public_endpoint(self, request: Request) -> bool:
        """Check if endpoint is public (no authentication required)."""
        public_paths = [
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
            "/metrics",
            "/auth/login",
            "/auth/register",
            "/auth/oauth/callback",
            "/auth/saml/acs",
            "/auth/ldap/login",
        ]
        
        return any(request.url.path.startswith(path) for path in public_paths)
    
    async def _is_rate_limited(self, request: Request, auth_result: 'AuthResult') -> bool:
        """Check if request is rate limited."""
        identifiers = [
            f"ip:{request.client.host}" if request.client else "ip:unknown",
            f"user:{auth_result.user_id}",
            f"endpoint:{request.url.path}",
        ]
        
        for identifier in identifiers:
            is_limited, _ = self.rate_limiter.is_rate_limited(identifier, "minute")
            if is_limited:
                return True
        
        return False
    
    async def _has_permission(self, request: Request, auth_result: 'AuthResult') -> bool:
        """Check if user has permission for this endpoint."""
        # Get required permissions for endpoint
        required_permissions = self._get_required_permissions(request)
        
        if not required_permissions:
            return True
        
        # Check permissions
        return auth_result.has_all_permissions(*required_permissions)
    
    def _get_required_permissions(self, request: Request) -> Set[Permission]:
        """Get required permissions for endpoint."""
        # In production, load from route metadata
        # For now, use path-based permissions
        
        path = request.url.path
        
        if "/agents" in path:
            if request.method == "GET":
                return {Permission.AGENT_READ}
            elif request.method == "POST":
                return {Permission.AGENT_CREATE}
            elif request.method in ["PUT", "PATCH"]:
                return {Permission.AGENT_UPDATE}
            elif request.method == "DELETE":
                return {Permission.AGENT_DELETE}
        
        elif "/api" in path:
            if request.method == "GET":
                return {Permission.API_READ}
            elif request.method in ["POST", "PUT", "PATCH"]:
                return {Permission.API_WRITE}
            elif request.method == "DELETE":
                return {Permission.API_DELETE}
        
        elif "/users" in path:
            if request.method == "GET":
                return {Permission.USER_READ}
            elif request.method == "POST":
                return {Permission.USER_CREATE}
            elif request.method in ["PUT", "PATCH"]:
                return {Permission.USER_UPDATE}
            elif request.method == "DELETE":
                return {Permission.USER_DELETE}
        
        return set()
    
    async def _audit_request(self, request: Request, auth_result: 'AuthResult') -> None:
        """Audit log the request."""
        audit_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": auth_result.user_id,
            "email": auth_result.email,
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("User-Agent"),
            "auth_method": auth_result.auth_method.value,
            "roles": [r.value for r in auth_result.roles],
            "permissions": [p.value for p in auth_result.permissions],
            "session_id": auth_result.session.session_id,
        }
        
        # Log to security manager
        self.security_manager.audit_event(
            event_type="API_REQUEST",
            user_id=auth_result.user_id,
            ip_address=request.client.host if request.client else None,
            details=audit_data,
            severity="INFO",
        )
    
    async def _audit_failure(self, request: Request, error: str) -> None:
        """Audit log authentication failure."""
        audit_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("User-Agent"),
            "error": error,
        }
        
        self.security_manager.audit_event(
            event_type="AUTH_FAILURE",
            ip_address=request.client.host if request.client else None,
            details=audit_data,
            severity="WARNING",
        )
    
    def _add_security_headers(self, response: Response) -> Response:
        """Add security headers to response."""
        security_headers = self.security_manager.security_headers.to_dict()
        
        for header, value in security_headers.items():
            response.headers[header] = value
        
        # Add additional headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Add CORS headers if needed
        # response.headers["Access-Control-Allow-Origin"] = "*"
        # response.headers["Access-Control-Allow-Credentials"] = "true"
        
        return response
    
    # Public methods for route dependencies
    
    def require_auth(self, *permissions: Permission) -> Callable:
        """Decorator to require authentication and permissions."""
        
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(request: Request, *args, **kwargs):
                # Check if request has auth state
                if not hasattr(request.state, 'auth'):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Not authenticated",
                    )
                
                auth_result = request.state.auth
                
                # Check MFA if required
                if auth_result.requires_mfa and not auth_result.mfa_verified:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Multi-factor authentication required",
                    )
                
                # Check permissions
                if permissions and not auth_result.has_all_permissions(*permissions):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Insufficient permissions",
                    )
                
                return await func(request, *args, **kwargs)
            
            return wrapper
        
        return decorator
    
    def require_role(self, *roles: UserRole) -> Callable:
        """Decorator to require specific roles."""
        
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(request: Request, *args, **kwargs):
                if not hasattr(request.state, 'auth'):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Not authenticated",
                    )
                
                auth_result = request.state.auth
                
                # Check roles
                if not any(role in auth_result.roles for role in roles):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Insufficient role",
                    )
                
                return await func(request, *args, **kwargs)
            
            return wrapper
        
        return decorator
    
    def require_mfa(self, mfa_method: Optional[MFAMethod] = None) -> Callable:
        """Decorator to require MFA verification."""
        
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(request: Request, *args, **kwargs):
                if not hasattr(request.state, 'auth'):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Not authenticated",
                    )
                
                auth_result = request.state.auth
                session = auth_result.session
                
                # Check MFA
                if not session.mfa_verified:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Multi-factor authentication required",
                    )
                
                # Check specific MFA method if specified
                if mfa_method and session.mfa_method != mfa_method:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"Required MFA method: {mfa_method.value}",
                    )
                
                return await func(request, *args, **kwargs)
            
            return wrapper
        
        return decorator


class AuthResult(BaseModel):
    """Authentication result."""
    
    user_id: str
    email: str
    roles: List[UserRole]
    permissions: Set[Permission]
    auth_method: AuthMethod
    session: AuthSession
    requires_mfa: bool
    mfa_verified: bool
    available_mfa_methods: List[MFAMethod]
    
    def has_permission(self, permission: Permission) -> bool:
        return permission in self.permissions
    
    def has_all_permissions(self, *permissions: Permission) -> bool:
        return all(p in self.permissions for p in permissions)
    
    def has_any_permission(self, *permissions: Permission) -> bool:
        return any(p in self.permissions for p in permissions)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            set: list,
        }


class MFAManager:
    """Multi-factor authentication manager."""
    
    def __init__(self, config: AuthConfig):
        self.config = config
        self.totp_secrets: Dict[str, str] = {}  # user_id -> secret
        self.backup_codes: Dict[str, List[str]] = {}  # user_id -> codes
        self.push_devices: Dict[str, List[Dict[str, Any]]] = {}  # user_id -> devices
        
    def get_available_methods(self, user_id: str) -> List[MFAMethod]:
        """Get available MFA methods for user."""
        methods = []
        
        # TOTP
        if user_id in self.totp_secrets:
            methods.append(MFAMethod.TOTP)
        
        # Email (always available)
        methods.append(MFAMethod.EMAIL)
        
        # SMS (if phone number is registered)
        # methods.append(MFAMethod.SMS)
        
        # WebAuthn (if devices are registered)
        if user_id in self.push_devices:
            methods.append(MFAMethod.WEBAUTHN)
        
        # Backup codes (if generated)
        if user_id in self.backup_codes and self.backup_codes[user_id]:
            methods.append(MFAMethod.BACKUP_CODE)
        
        return methods
    
    def setup_totp(self, user_id: str) -> Dict[str, Any]:
        """Setup TOTP for user."""
        secret = pyotp.random_base32()
        self.totp_secrets[user_id] = secret
        
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=user_id,
            issuer_name="MicroAgents Platform",
        )
        
        return {
            "secret": secret,
            "provisioning_uri": provisioning_uri,
            "qr_code": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={provisioning_uri}",
        }
    
    def verify_totp(self, user_id: str, code: str) -> bool:
        """Verify TOTP code."""
        if user_id not in self.totp_secrets:
            return False
        
        secret = self.totp_secrets[user_id]
        totp = pyotp.TOTP(secret)
        
        # Allow some time drift
        return totp.verify(code, valid_window=1)
    
    def generate_backup_codes(self, user_id: str, count: int = 10) -> List[str]:
        """Generate backup codes for user."""
        codes = [secrets.token_urlsafe(10).replace('-', '').replace('_', '')[:8].upper()
                for _ in range(count)]
        self.backup_codes[user_id] = codes
        return codes
    
    def verify_backup_code(self, user_id: str, code: str) -> bool:
        """Verify backup code."""
        if user_id not in self.backup_codes:
            return False
        
        codes = self.backup_codes[user_id]
        if code in codes:
            # Remove used code
            codes.remove(code)
            self.backup_codes[user_id] = codes
            return True
        
        return False
    
    def send_email_code(self, user_id: str, email: str) -> str:
        """Send email verification code."""
        code = ''.join(str(secrets.randbelow(10)) for _ in range(6))
        
        # In production, send email
        # For now, just return code
        return code
    
    def send_sms_code(self, user_id: str, phone_number: str) -> str:
        """Send SMS verification code."""
        code = ''.join(str(secrets.randbelow(10)) for _ in range(6))
        
        # In production, send SMS
        # For now, just return code
        return code


# FastAPI dependencies for route injection
async def get_current_user(
    request: Request,
    auth_middleware: AuthenticationMiddleware = Depends(),
) -> AuthResult:
    """Dependency to get current authenticated user."""
    if not hasattr(request.state, 'auth'):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return request.state.auth


async def require_permissions(
    *permissions: Permission,
    request: Request,
    current_user: AuthResult = Depends(get_current_user),
) -> AuthResult:
    """Dependency to require specific permissions."""
    if not current_user.has_all_permissions(*permissions):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    return current_user


async def require_roles(
    *roles: UserRole,
    request: Request,
    current_user: AuthResult = Depends(get_current_user),
) -> AuthResult:
    """Dependency to require specific roles."""
    if not any(role in current_user.roles for role in roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient role",
        )
    return current_user


async def require_mfa(
    request: Request,
    current_user: AuthResult = Depends(get_current_user),
) -> AuthResult:
    """Dependency to require MFA verification."""
    if not current_user.mfa_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Multi-factor authentication required",
        )
    return current_user


# Security schemes for OpenAPI
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)
api_key_cookie = APIKeyCookie(name="api_key", auto_error=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_middleware() -> AuthenticationMiddleware:
    """Get authentication middleware instance."""
    # In production, this would be initialized with app
    return AuthenticationMiddleware(
        app=FastAPI(),
        config=AuthConfig(),
        security_manager=get_security_manager(),
    )


# Export public API
__all__ = [
    # Main classes
    "AuthenticationMiddleware",
    "AuthConfig",
    "AuthResult",
    "AuthSession",
    
    # Validators
    "JWTValidator",
    "APIKeyValidator",
    "OAuth2Validator",
    
    # MFA
    "MFAManager",
    
    # Models and enums
    "AuthMethod",
    "TokenType",
    "UserRole",
    "Permission",
    "MFAMethod",
    "RateLimitScope",
    "OAuthProvider",
    
    # Config models
    "OAuthConfig",
    "SAMLConfig",
    "LDAPConfig",
    "BiometricConfig",
    
    # FastAPI dependencies
    "get_current_user",
    "require_permissions",
    "require_roles",
    "require_mfa",
    
    # Security schemes
    "api_key_header",
    "api_key_query",
    "api_key_cookie",
    "oauth2_scheme",
    "bearer_scheme",
    
    # Factory function
    "get_auth_middleware",
]