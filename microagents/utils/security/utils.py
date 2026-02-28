"""
Security utilities for MicroAgents Platform.
Encryption, hashing, validation, and security headers.
"""

import asyncio
import base64
import binascii
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import random
import re
import secrets
import string
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import bcrypt
import cryptography
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.x509 import Certificate, load_pem_x509_certificate
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field, HttpUrl, SecretStr, ValidationError, validator

# Type aliases
JsonWebKey = Dict[str, Any]
KeyRotationSchedule = Dict[str, datetime]


class SecurityLevel(str, Enum):
    """Security levels for different operations."""
    
    LOW = "low"          # Internal, trusted environment
    MEDIUM = "medium"    # Standard web application
    HIGH = "high"        # Financial, healthcare
    CRITICAL = "critical"  # Military, state secrets


class HashAlgorithm(str, Enum):
    """Supported hash algorithms."""
    
    SHA256 = "sha256"
    SHA512 = "sha512"
    BCRYPT = "bcrypt"
    ARGON2 = "argon2"
    PBKDF2 = "pbkdf2"


class EncryptionAlgorithm(str, Enum):
    """Supported encryption algorithms."""
    
    AES_GCM = "aes-gcm"
    AES_CBC = "aes-cbc"
    CHACHA20 = "chacha20"
    RSA_OAEP = "rsa-oaep"
    RSA_PKCS1 = "rsa-pkcs1"


class PasswordPolicy(BaseModel):
    """Password policy configuration."""
    
    min_length: int = Field(default=12, ge=8, le=128)
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_digits: bool = True
    require_special: bool = True
    special_characters: str = Field(default="!@#$%^&*()_+-=[]{}|;:,.<>?")
    max_age_days: int = Field(default=90, ge=1, le=365)
    history_size: int = Field(default=5, ge=0, le=20)
    max_attempts: int = Field(default=5, ge=1, le=20)
    lockout_minutes: int = Field(default=30, ge=1, le=1440)
    
    @validator('special_characters')
    def validate_special_characters(cls, v):
        if not v:
            raise ValueError("Special characters cannot be empty")
        return v


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""
    
    requests_per_minute: int = Field(default=60, ge=1)
    requests_per_hour: int = Field(default=1000, ge=1)
    requests_per_day: int = Field(default=10000, ge=1)
    burst_size: int = Field(default=10, ge=1)
    block_duration_minutes: int = Field(default=15, ge=1)
    
    def for_security_level(self, level: SecurityLevel) -> 'RateLimitConfig':
        """Get rate limit config for security level."""
        if level == SecurityLevel.LOW:
            return RateLimitConfig(
                requests_per_minute=self.requests_per_minute * 2,
                requests_per_hour=self.requests_per_hour * 2,
                requests_per_day=self.requests_per_day * 2,
                burst_size=self.burst_size * 2,
                block_duration_minutes=self.block_duration_minutes,
            )
        elif level == SecurityLevel.HIGH:
            return RateLimitConfig(
                requests_per_minute=self.requests_per_minute // 2,
                requests_per_hour=self.requests_per_hour // 2,
                requests_per_day=self.requests_per_day // 2,
                burst_size=self.burst_size // 2,
                block_duration_minutes=self.block_duration_minutes * 2,
            )
        elif level == SecurityLevel.CRITICAL:
            return RateLimitConfig(
                requests_per_minute=self.requests_per_minute // 4,
                requests_per_hour=self.requests_per_hour // 4,
                requests_per_day=self.requests_per_day // 4,
                burst_size=max(1, self.burst_size // 4),
                block_duration_minutes=self.block_duration_minutes * 4,
            )
        return self


class SecurityHeaders(BaseModel):
    """Security headers configuration."""
    
    hsts_max_age: int = Field(default=31536000, description="HSTS max age in seconds")
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False
    
    content_security_policy: str = Field(
        default="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';",
        description="Content Security Policy"
    )
    
    x_frame_options: str = Field(default="DENY", description="X-Frame-Options")
    x_content_type_options: str = Field(default="nosniff", description="X-Content-Type-Options")
    x_xss_protection: str = Field(default="1; mode=block", description="X-XSS-Protection")
    
    referrer_policy: str = Field(default="strict-origin-when-cross-origin", description="Referrer-Policy")
    permissions_policy: str = Field(
        default="camera=(), microphone=(), geolocation=(), payment=()",
        description="Permissions-Policy"
    )
    
    cross_origin_opener_policy: str = Field(default="same-origin", description="Cross-Origin-Opener-Policy")
    cross_origin_resource_policy: str = Field(default="same-origin", description="Cross-Origin-Resource-Policy")
    
    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary of headers."""
        return {
            "Strict-Transport-Security": (
                f"max-age={self.hsts_max_age}"
                f"{'; includeSubDomains' if self.hsts_include_subdomains else ''}"
                f"{'; preload' if self.hsts_preload else ''}"
            ),
            "Content-Security-Policy": self.content_security_policy,
            "X-Frame-Options": self.x_frame_options,
            "X-Content-Type-Options": self.x_content_type_options,
            "X-XSS-Protection": self.x_xss_protection,
            "Referrer-Policy": self.referrer_policy,
            "Permissions-Policy": self.permissions_policy,
            "Cross-Origin-Opener-Policy": self.cross_origin_opener_policy,
            "Cross-Origin-Resource-Policy": self.cross_origin_resource_policy,
        }


class EncryptionManager:
    """Manager for encryption/decryption operations."""
    
    def __init__(
        self,
        key_rotation_days: int = 90,
        master_key_path: Optional[str] = None,
        security_level: SecurityLevel = SecurityLevel.MEDIUM,
    ):
        self.key_rotation_days = key_rotation_days
        self.security_level = security_level
        self.master_key_path = master_key_path or os.getenv("MASTER_KEY_PATH")
        
        # Initialize keys
        self._load_or_generate_keys()
        
        # Key rotation schedule
        self.key_rotation_schedule: KeyRotationSchedule = {}
        
    def _load_or_generate_keys(self) -> None:
        """Load or generate encryption keys."""
        # Load master key if exists
        if self.master_key_path and os.path.exists(self.master_key_path):
            with open(self.master_key_path, 'rb') as f:
                master_key = f.read()
        else:
            # Generate new master key
            master_key = Fernet.generate_key()
            if self.master_key_path:
                os.makedirs(os.path.dirname(self.master_key_path), exist_ok=True)
                with open(self.master_key_path, 'wb') as f:
                    f.write(master_key)
        
        # Generate derived keys based on security level
        self._generate_derived_keys(master_key)
        
    def _generate_derived_keys(self, master_key: bytes) -> None:
        """Generate derived keys from master key."""
        salt = b"microagents_salt_" + master_key[:16]
        
        # Generate AES key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend(),
        )
        self.aes_key = kdf.derive(master_key + b"aes")
        
        # Generate Fernet keys for different purposes
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend(),
        )
        fernet_key = kdf.derive(master_key + b"fernet")
        self.fernet = Fernet(fernet_key)
        
        # Generate RSA key pair for high security operations
        if self.security_level in [SecurityLevel.HIGH, SecurityLevel.CRITICAL]:
            self.rsa_private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=4096 if self.security_level == SecurityLevel.CRITICAL else 2048,
                backend=default_backend(),
            )
            self.rsa_public_key = self.rsa_private_key.public_key()
    
    def encrypt_aes_gcm(self, data: bytes, associated_data: Optional[bytes] = None) -> bytes:
        """
        Encrypt data using AES-GCM.
        
        Args:
            data: Data to encrypt
            associated_data: Associated authenticated data
            
        Returns:
            Encrypted data with nonce and tag
        """
        # Generate a random 96-bit nonce
        nonce = os.urandom(12)
        
        # Create cipher
        cipher = Cipher(
            algorithms.AES(self.aes_key),
            modes.GCM(nonce),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()
        
        # Add associated data if provided
        if associated_data:
            encryptor.authenticate_additional_data(associated_data)
        
        # Encrypt data
        ciphertext = encryptor.update(data) + encryptor.finalize()
        
        # Combine nonce, ciphertext, and tag
        return nonce + ciphertext + encryptor.tag
    
    def decrypt_aes_gcm(self, encrypted_data: bytes, associated_data: Optional[bytes] = None) -> bytes:
        """
        Decrypt data using AES-GCM.
        
        Args:
            encrypted_data: Encrypted data with nonce and tag
            associated_data: Associated authenticated data
            
        Returns:
            Decrypted data
            
        Raises:
            InvalidTag: If authentication fails
        """
        if len(encrypted_data) < 28:  # 12 nonce + 16 tag
            raise ValueError("Encrypted data too short")
        
        # Extract components
        nonce = encrypted_data[:12]
        tag = encrypted_data[-16:]
        ciphertext = encrypted_data[12:-16]
        
        # Create cipher
        cipher = Cipher(
            algorithms.AES(self.aes_key),
            modes.GCM(nonce, tag),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()
        
        # Add associated data if provided
        if associated_data:
            decryptor.authenticate_additional_data(associated_data)
        
        # Decrypt data
        try:
            return decryptor.update(ciphertext) + decryptor.finalize()
        except InvalidTag as e:
            raise InvalidTag("Authentication failed") from e
    
    def encrypt_rsa(self, data: bytes) -> bytes:
        """
        Encrypt data using RSA.
        
        Args:
            data: Data to encrypt
            
        Returns:
            Encrypted data
        """
        if not hasattr(self, 'rsa_public_key'):
            raise ValueError("RSA not available for current security level")
        
        # RSA can only encrypt small amounts of data
        # For larger data, use hybrid encryption
        max_size = 190 if self.security_level == SecurityLevel.CRITICAL else 214
        
        if len(data) > max_size:
            # Use hybrid encryption
            # Generate random AES key
            aes_key = os.urandom(32)
            nonce = os.urandom(12)
            
            # Encrypt data with AES
            cipher = Cipher(
                algorithms.AES(aes_key),
                modes.GCM(nonce),
                backend=default_backend(),
            )
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(data) + encryptor.finalize()
            
            # Encrypt AES key with RSA
            encrypted_key = self.rsa_public_key.encrypt(
                aes_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            
            return b"HYBRID:" + encrypted_key + nonce + encryptor.tag + ciphertext
        
        # Direct RSA encryption for small data
        return self.rsa_public_key.encrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    
    def decrypt_rsa(self, encrypted_data: bytes) -> bytes:
        """
        Decrypt data using RSA.
        
        Args:
            encrypted_data: Encrypted data
            
        Returns:
            Decrypted data
        """
        if not hasattr(self, 'rsa_private_key'):
            raise ValueError("RSA not available for current security level")
        
        # Check if it's hybrid encryption
        if encrypted_data.startswith(b"HYBRID:"):
            # Extract components
            encrypted_key = encrypted_data[7:7 + 512]  # 4096-bit RSA -> 512 bytes
            nonce = encrypted_data[7 + 512:7 + 512 + 12]
            tag = encrypted_data[7 + 512 + 12:7 + 512 + 12 + 16]
            ciphertext = encrypted_data[7 + 512 + 12 + 16:]
            
            # Decrypt AES key with RSA
            aes_key = self.rsa_private_key.decrypt(
                encrypted_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            
            # Decrypt data with AES
            cipher = Cipher(
                algorithms.AES(aes_key),
                modes.GCM(nonce, tag),
                backend=default_backend(),
            )
            decryptor = cipher.decryptor()
            
            return decryptor.update(ciphertext) + decryptor.finalize()
        
        # Direct RSA decryption
        return self.rsa_private_key.decrypt(
            encrypted_data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    
    def encrypt_fernet(self, data: bytes) -> bytes:
        """
        Encrypt data using Fernet (symmetric encryption).
        
        Args:
            data: Data to encrypt
            
        Returns:
            Encrypted token
        """
        return self.fernet.encrypt(data)
    
    def decrypt_fernet(self, token: bytes) -> bytes:
        """
        Decrypt data using Fernet.
        
        Args:
            token: Encrypted token
            
        Returns:
            Decrypted data
            
        Raises:
            InvalidToken: If token is invalid
        """
        try:
            return self.fernet.decrypt(token)
        except InvalidToken as e:
            raise InvalidToken("Invalid or corrupted token") from e
    
    def rotate_keys(self) -> bool:
        """
        Rotate encryption keys.
        
        Returns:
            True if rotation successful
        """
        try:
            # Backup old keys
            old_aes_key = self.aes_key
            old_fernet = self.fernet
            
            # Generate new keys
            master_key = Fernet.generate_key()
            self._generate_derived_keys(master_key)
            
            # Update rotation schedule
            self.key_rotation_schedule["last_rotation"] = datetime.now(timezone.utc)
            self.key_rotation_schedule["next_rotation"] = (
                datetime.now(timezone.utc) + timedelta(days=self.key_rotation_days)
            )
            
            # Save new master key
            if self.master_key_path:
                with open(self.master_key_path, 'wb') as f:
                    f.write(master_key)
            
            return True
            
        except Exception as e:
            # Restore old keys on failure
            self.aes_key = old_aes_key
            self.fernet = old_fernet
            raise RuntimeError(f"Key rotation failed: {e}")
    
    def should_rotate_keys(self) -> bool:
        """Check if keys should be rotated."""
        last_rotation = self.key_rotation_schedule.get("last_rotation")
        if not last_rotation:
            return True
        
        next_rotation = self.key_rotation_schedule.get("next_rotation")
        if next_rotation and datetime.now(timezone.utc) >= next_rotation:
            return True
        
        return False


class HashManager:
    """Manager for hashing operations."""
    
    def __init__(self, default_algorithm: HashAlgorithm = HashAlgorithm.BCRYPT):
        self.default_algorithm = default_algorithm
        
    def hash_password(self, password: str, algorithm: Optional[HashAlgorithm] = None) -> str:
        """
        Hash a password using specified algorithm.
        
        Args:
            password: Plain text password
            algorithm: Hash algorithm to use
            
        Returns:
            Hashed password
        """
        algorithm = algorithm or self.default_algorithm
        
        if algorithm == HashAlgorithm.BCRYPT:
            salt = bcrypt.gensalt(rounds=12)
            hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
            return f"$bcrypt${hashed.decode('utf-8')}"
        
        elif algorithm == HashAlgorithm.SHA256:
            # For passwords, always use salt with SHA256
            salt = secrets.token_bytes(32)
            hashed = hashlib.pbkdf2_hmac(
                'sha256',
                password.encode('utf-8'),
                salt,
                100000,
            )
            return f"$pbkdf2-sha256$100000${salt.hex()}${hashed.hex()}"
        
        elif algorithm == HashAlgorithm.SHA512:
            salt = secrets.token_bytes(64)
            hashed = hashlib.pbkdf2_hmac(
                'sha512',
                password.encode('utf-8'),
                salt,
                100000,
            )
            return f"$pbkdf2-sha512$100000${salt.hex()}${hashed.hex()}"
        
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")
    
    def verify_password(self, password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash.
        
        Args:
            password: Plain text password to verify
            hashed_password: Hashed password
            
        Returns:
            True if password matches
        """
        if hashed_password.startswith("$bcrypt$"):
            # BCrypt hash
            actual_hash = hashed_password[8:]  # Remove "$bcrypt$"
            return bcrypt.checkpw(password.encode('utf-8'), actual_hash.encode('utf-8'))
        
        elif hashed_password.startswith("$pbkdf2-sha256$"):
            # PBKDF2 SHA256 hash
            parts = hashed_password.split('$')
            if len(parts) != 5:
                return False
            
            iterations = int(parts[2])
            salt = bytes.fromhex(parts[3])
            expected_hash = bytes.fromhex(parts[4])
            
            actual_hash = hashlib.pbkdf2_hmac(
                'sha256',
                password.encode('utf-8'),
                salt,
                iterations,
            )
            
            return hmac.compare_digest(actual_hash, expected_hash)
        
        elif hashed_password.startswith("$pbkdf2-sha512$"):
            # PBKDF2 SHA512 hash
            parts = hashed_password.split('$')
            if len(parts) != 5:
                return False
            
            iterations = int(parts[2])
            salt = bytes.fromhex(parts[3])
            expected_hash = bytes.fromhex(parts[4])
            
            actual_hash = hashlib.pbkdf2_hmac(
                'sha512',
                password.encode('utf-8'),
                salt,
                iterations,
            )
            
            return hmac.compare_digest(actual_hash, expected_hash)
        
        else:
            # Unknown format, try bcrypt directly
            try:
                return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
            except (ValueError, binascii.Error):
                return False
    
    def hash_data(self, data: bytes, algorithm: HashAlgorithm = HashAlgorithm.SHA256) -> str:
        """
        Hash arbitrary data.
        
        Args:
            data: Data to hash
            algorithm: Hash algorithm
            
        Returns:
            Hex-encoded hash
        """
        if algorithm == HashAlgorithm.SHA256:
            return hashlib.sha256(data).hexdigest()
        elif algorithm == HashAlgorithm.SHA512:
            return hashlib.sha512(data).hexdigest()
        else:
            raise ValueError(f"Algorithm {algorithm} not supported for data hashing")
    
    def hmac_sign(self, data: bytes, key: bytes, algorithm: HashAlgorithm = HashAlgorithm.SHA256) -> str:
        """
        Create HMAC signature for data.
        
        Args:
            data: Data to sign
            key: Secret key
            algorithm: Hash algorithm
            
        Returns:
            Hex-encoded HMAC
        """
        if algorithm == HashAlgorithm.SHA256:
            return hmac.new(key, data, hashlib.sha256).hexdigest()
        elif algorithm == HashAlgorithm.SHA512:
            return hmac.new(key, data, hashlib.sha512).hexdigest()
        else:
            raise ValueError(f"Algorithm {algorithm} not supported for HMAC")


class JWTManager:
    """Manager for JWT token handling."""
    
    def __init__(
        self,
        secret_key: Optional[str] = None,
        algorithm: str = "HS256",
        access_token_expire_minutes: int = 30,
        refresh_token_expire_days: int = 7,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
    ):
        self.secret_key = secret_key or os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(64))
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes
        self.refresh_token_expire_days = refresh_token_expire_days
        self.issuer = issuer or os.getenv("JWT_ISSUER", "microagents.io")
        self.audience = audience or os.getenv("JWT_AUDIENCE", "microagents-clients")
        
        # Key rotation
        self.key_rotation_days = 30
        self.key_history: List[Tuple[datetime, str]] = []
        
    def create_access_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """
        Create access token.
        
        Args:
            data: Token payload data
            expires_delta: Optional custom expiration
            
        Returns:
            JWT token
        """
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=self.access_token_expire_minutes)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "iss": self.issuer,
            "aud": self.audience,
            "type": "access",
        })
        
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
    
    def create_refresh_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """
        Create refresh token.
        
        Args:
            data: Token payload data
            expires_delta: Optional custom expiration
            
        Returns:
            JWT refresh token
        """
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(days=self.refresh_token_expire_days)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "iss": self.issuer,
            "aud": self.audience,
            "type": "refresh",
        })
        
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
    
    def verify_token(self, token: str, token_type: str = "access") -> Dict[str, Any]:
        """
        Verify and decode JWT token.
        
        Args:
            token: JWT token to verify
            token_type: Expected token type
            
        Returns:
            Decoded token payload
            
        Raises:
            JWTError: If token is invalid
        """
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iat", "iss", "aud", "type"]},
            )
            
            # Check token type
            if payload.get("type") != token_type:
                raise JWTError(f"Invalid token type: expected {token_type}, got {payload.get('type')}")
            
            return payload
            
        except JWTError as e:
            # Try with historical keys
            for _, historical_key in self.key_history:
                try:
                    payload = jwt.decode(
                        token,
                        historical_key,
                        algorithms=[self.algorithm],
                        issuer=self.issuer,
                        audience=self.audience,
                        options={"require": ["exp", "iat", "iss", "aud", "type"]},
                    )
                    return payload
                except JWTError:
                    continue
            
            raise
    
    def rotate_secret_key(self) -> bool:
        """
        Rotate JWT secret key.
        
        Returns:
            True if rotation successful
        """
        try:
            # Add current key to history
            self.key_history.append((datetime.now(timezone.utc), self.secret_key))
            
            # Keep only recent keys (last 90 days)
            cutoff = datetime.now(timezone.utc) - timedelta(days=90)
            self.key_history = [(date, key) for date, key in self.key_history if date > cutoff]
            
            # Generate new key
            self.secret_key = secrets.token_urlsafe(64)
            
            return True
            
        except Exception as e:
            raise RuntimeError(f"Key rotation failed: {e}")
    
    def get_jwks(self) -> JsonWebKey:
        """
        Get JSON Web Key Set for public key verification.
        
        Returns:
            JWKS dictionary
        """
        # For HS256, we don't expose the secret
        # For RS256, we would expose the public key
        if self.algorithm.startswith("RS"):
            public_key = self.secret_key  # This would be the public key for RSA
            return {
                "keys": [{
                    "kty": "RSA",
                    "alg": self.algorithm,
                    "use": "sig",
                    "kid": "1",
                    "n": "...",  # RSA modulus
                    "e": "AQAB",  # RSA exponent
                }]
            }
        else:
            return {"keys": []}


class InputValidator:
    """Input validation and sanitization utilities."""
    
    # SQL injection patterns
    SQL_INJECTION_PATTERNS = [
        r"(?i)(\bSELECT\b.*\bFROM\b)",
        r"(?i)(\bINSERT\b.*\bINTO\b)",
        r"(?i)(\bUPDATE\b.*\bSET\b)",
        r"(?i)(\bDELETE\b.*\bFROM\b)",
        r"(?i)(\bDROP\b.*\bTABLE\b)",
        r"(?i)(\bUNION\b.*\bSELECT\b)",
        r"(?i)(\bOR\b.*\b=\b.*\bOR\b)",
        r"(?i)(\b--\b)",
        r"(?i)(\b/\*\b.*\b\*/\b)",
        r"(?i)(\bEXEC\b.*\()",
        r"(?i)(\bWAITFOR\b.*\bDELAY\b)",
        r"(\b;\b.*\b--\b)",
        r"(\b;\b.*\b/\*\b)",
    ]
    
    # XSS patterns
    XSS_PATTERNS = [
        r"<script\b[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<\s*iframe\b",
        r"<\s*object\b",
        r"<\s*embed\b",
        r"<\s*applet\b",
        r"<\s*frame\b",
        r"<\s*frameset\b",
        r"<\s*meta\b",
        r"<\s*link\b",
        r"<\s*style\b",
        r"expression\s*\(",
        r"vbscript:",
        r"<\s*base\b",
    ]
    
    # Path traversal patterns
    PATH_TRAVERSAL_PATTERNS = [
        r"\.\./",
        r"\.\.\\",
        r"\.\.%2f",
        r"\.\.%5c",
        r"%2e%2e/",
        r"%2e%2e\\",
    ]
    
    @classmethod
    def validate_email(cls, email: str) -> bool:
        """Validate email address."""
        try:
            EmailStr.validate(email)
            return True
        except ValidationError:
            return False
    
    @classmethod
    def validate_url(cls, url: str, allowed_schemes: List[str] = ["http", "https"]) -> bool:
        """Validate URL."""
        try:
            result = urlparse(url)
            return (
                result.scheme in allowed_schemes and
                result.netloc != "" and
                len(url) <= 2048  # Reasonable URL length limit
            )
        except Exception:
            return False
    
    @classmethod
    def sanitize_html(cls, html: str, allowed_tags: Optional[List[str]] = None) -> str:
        """
        Sanitize HTML to prevent XSS.
        
        Args:
            html: HTML string to sanitize
            allowed_tags: List of allowed HTML tags
            
        Returns:
            Sanitized HTML
        """
        if allowed_tags is None:
            allowed_tags = ["b", "i", "u", "em", "strong", "p", "br", "ul", "ol", "li", "a"]
        
        import html as html_module
        
        # First escape all HTML
        sanitized = html_module.escape(html)
        
        # Then allow specific tags
        for tag in allowed_tags:
            # Convert escaped tags back
            sanitized = sanitized.replace(f"&lt;{tag}&gt;", f"<{tag}>")
            sanitized = sanitized.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
            
            # Handle self-closing tags
            sanitized = sanitized.replace(f"&lt;{tag}/&gt;", f"<{tag}/>")
        
        # Remove any remaining dangerous patterns
        for pattern in cls.XSS_PATTERNS:
            sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)
        
        return sanitized
    
    @classmethod
    def prevent_sql_injection(cls, input_str: str) -> bool:
        """
        Check for SQL injection patterns.
        
        Args:
            input_str: String to check
            
        Returns:
            True if safe, False if suspicious
        """
        input_lower = input_str.lower()
        
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, input_lower):
                return False
        
        # Check for suspicious characters in combination
        suspicious_combinations = [
            ("'", "--"),
            ("'", "#"),
            ("'", "/*"),
            (";", "--"),
            (";", "/*"),
        ]
        
        for char1, char2 in suspicious_combinations:
            if char1 in input_lower and char2 in input_lower:
                return False
        
        return True
    
    @classmethod
    def prevent_xss(cls, input_str: str) -> bool:
        """
        Check for XSS patterns.
        
        Args:
            input_str: String to check
            
        Returns:
            True if safe, False if suspicious
        """
        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, input_str, re.IGNORECASE):
                return False
        
        # Check for encoded XSS attempts
        decoded = input_str
        for _ in range(3):  # Try decoding multiple times
            try:
                decoded = base64.b64decode(decoded).decode('utf-8', errors='ignore')
                for pattern in cls.XSS_PATTERNS:
                    if re.search(pattern, decoded, re.IGNORECASE):
                        return False
            except Exception:
                break
        
        return True
    
    @classmethod
    def prevent_path_traversal(cls, path: str) -> bool:
        """
        Check for path traversal attempts.
        
        Args:
            path: Path to check
            
        Returns:
            True if safe, False if suspicious
        """
        for pattern in cls.PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                return False
        
        # Check for absolute paths
        if path.startswith('/') or (len(path) > 1 and path[1] == ':'):
            return False
        
        return True
    
    @classmethod
    def validate_password_strength(cls, password: str, policy: PasswordPolicy) -> Tuple[bool, List[str]]:
        """
        Validate password against policy.
        
        Args:
            password: Password to validate
            policy: Password policy
            
        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []
        
        # Check length
        if len(password) < policy.min_length:
            errors.append(f"Password must be at least {policy.min_length} characters")
        
        # Check character requirements
        if policy.require_uppercase and not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")
        
        if policy.require_lowercase and not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")
        
        if policy.require_digits and not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")
        
        if policy.require_special and not any(c in policy.special_characters for c in password):
            errors.append(f"Password must contain at least one special character: {policy.special_characters}")
        
        # Check for common passwords (simplified)
        common_passwords = ["password", "123456", "qwerty", "admin", "letmein"]
        if password.lower() in common_passwords:
            errors.append("Password is too common")
        
        return len(errors) == 0, errors


class RateLimiter:
    """Rate limiting utilities."""
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        config: Optional[RateLimitConfig] = None,
    ):
        self.redis = redis_client
        self.config = config or RateLimitConfig()
        self.local_cache: Dict[str, List[float]] = {}
        
    def is_rate_limited(
        self,
        identifier: str,
        limit_type: str = "minute",
        increment: bool = True,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if request is rate limited.
        
        Args:
            identifier: Client identifier (IP, user_id, etc.)
            limit_type: Type of limit (minute, hour, day)
            increment: Whether to increment the counter
            
        Returns:
            Tuple of (is_limited, metadata)
        """
        if limit_type == "minute":
            limit = self.config.requests_per_minute
            window = 60
        elif limit_type == "hour":
            limit = self.config.requests_per_hour
            window = 3600
        elif limit_type == "day":
            limit = self.config.requests_per_day
            window = 86400
        else:
            raise ValueError(f"Invalid limit type: {limit_type}")
        
        current_time = time.time()
        window_key = f"{identifier}:{limit_type}"
        
        if self.redis:
            # Use Redis for distributed rate limiting
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(window_key, 0, current_time - window)
            pipe.zcard(window_key)
            
            if increment:
                pipe.zadd(window_key, {str(current_time): current_time})
                pipe.expire(window_key, window)
            
            results = pipe.execute()
            count = results[1]
            
            if increment:
                count += 1  # Account for the request we just added
        else:
            # Use local cache
            if window_key not in self.local_cache:
                self.local_cache[window_key] = []
            
            # Clean old entries
            self.local_cache[window_key] = [
                ts for ts in self.local_cache[window_key]
                if ts > current_time - window
            ]
            
            count = len(self.local_cache[window_key])
            
            if increment:
                self.local_cache[window_key].append(current_time)
                count += 1
        
        is_limited = count > limit
        
        metadata = {
            "identifier": identifier,
            "limit_type": limit_type,
            "current_count": count,
            "limit": limit,
            "window_seconds": window,
            "is_limited": is_limited,
        }
        
        return is_limited, metadata
    
    def check_burst_limit(self, identifier: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Check burst rate limiting.
        
        Args:
            identifier: Client identifier
            
        Returns:
            Tuple of (is_burst_limited, metadata)
        """
        current_time = time.time()
        burst_key = f"{identifier}:burst"
        
        if self.redis:
            burst_timestamps = self.redis.lrange(burst_key, 0, -1)
            burst_timestamps = [float(ts) for ts in burst_timestamps]
        else:
            burst_timestamps = self.local_cache.get(burst_key, [])
        
        # Clean old entries (last 10 seconds)
        recent_timestamps = [
            ts for ts in burst_timestamps
            if ts > current_time - 10
        ]
        
        is_burst_limited = len(recent_timestamps) >= self.config.burst_size
        
        if not is_burst_limited:
            recent_timestamps.append(current_time)
            
            if self.redis:
                self.redis.lpush(burst_key, current_time)
                self.redis.ltrim(burst_key, 0, self.config.burst_size - 1)
                self.redis.expire(burst_key, 10)
            else:
                self.local_cache[burst_key] = recent_timestamps
        
        metadata = {
            "identifier": identifier,
            "burst_count": len(recent_timestamps),
            "burst_limit": self.config.burst_size,
            "is_burst_limited": is_burst_limited,
        }
        
        return is_burst_limited, metadata


class CORSManager:
    """CORS configuration manager."""
    
    def __init__(
        self,
        allowed_origins: List[str] = None,
        allowed_methods: List[str] = None,
        allowed_headers: List[str] = None,
        expose_headers: List[str] = None,
        allow_credentials: bool = True,
        max_age: int = 600,
    ):
        self.allowed_origins = allowed_origins or ["*"]
        self.allowed_methods = allowed_methods or ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
        self.allowed_headers = allowed_headers or ["*"]
        self.expose_headers = expose_headers or []
        self.allow_credentials = allow_credentials
        self.max_age = max_age
        
    def get_cors_headers(self, origin: Optional[str] = None) -> Dict[str, str]:
        """
        Get CORS headers for a request.
        
        Args:
            origin: Request origin
            
        Returns:
            CORS headers dictionary
        """
        headers = {}
        
        # Check if origin is allowed
        if origin:
            if "*" in self.allowed_origins:
                headers["Access-Control-Allow-Origin"] = "*"
            elif origin in self.allowed_origins:
                headers["Access-Control-Allow-Origin"] = origin
                if self.allow_credentials:
                    headers["Access-Control-Allow-Credentials"] = "true"
        
        # Add other CORS headers
        headers["Access-Control-Allow-Methods"] = ", ".join(self.allowed_methods)
        headers["Access-Control-Allow-Headers"] = ", ".join(self.allowed_headers)
        
        if self.expose_headers:
            headers["Access-Control-Expose-Headers"] = ", ".join(self.expose_headers)
        
        headers["Access-Control-Max-Age"] = str(self.max_age)
        
        return headers
    
    def is_origin_allowed(self, origin: str) -> bool:
        """
        Check if origin is allowed.
        
        Args:
            origin: Origin to check
            
        Returns:
            True if allowed
        """
        if "*" in self.allowed_origins:
            return True
        
        return origin in self.allowed_origins
    
    def validate_origin(self, origin: str) -> bool:
        """
        Validate origin format and content.
        
        Args:
            origin: Origin to validate
            
        Returns:
            True if valid
        """
        if not origin:
            return False
        
        try:
            parsed = urlparse(origin)
            if not parsed.scheme or not parsed.netloc:
                return False
            
            # Only allow http/https
            if parsed.scheme not in ["http", "https"]:
                return False
            
            # Check for basic injection attempts
            if any(char in origin for char in ["\n", "\r", "\0", "'", '"', "<", ">"]):
                return False
            
            return True
            
        except Exception:
            return False


# Global security manager instance
_security_manager = None


class SecurityManager:
    """Main security manager integrating all utilities."""
    
    def __init__(
        self,
        encryption_manager: Optional[EncryptionManager] = None,
        hash_manager: Optional[HashManager] = None,
        jwt_manager: Optional[JWTManager] = None,
        input_validator: Optional[InputValidator] = None,
        rate_limiter: Optional[RateLimiter] = None,
        cors_manager: Optional[CORSManager] = None,
        security_headers: Optional[SecurityHeaders] = None,
        password_policy: Optional[PasswordPolicy] = None,
    ):
        self.encryption_manager = encryption_manager or EncryptionManager()
        self.hash_manager = hash_manager or HashManager()
        self.jwt_manager = jwt_manager or JWTManager()
        self.input_validator = input_validator or InputValidator()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.cors_manager = cors_manager or CORSManager()
        self.security_headers = security_headers or SecurityHeaders()
        self.password_policy = password_policy or PasswordPolicy()
        
        # Audit log
        self.audit_log: List[Dict[str, Any]] = []
        
    def audit_event(
        self,
        event_type: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        severity: str = "INFO",
    ) -> None:
        """
        Log security audit event.
        
        Args:
            event_type: Type of event
            user_id: User ID if applicable
            ip_address: IP address
            details: Additional details
            severity: Event severity
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "user_id": user_id,
            "ip_address": ip_address,
            "severity": severity,
            "details": details or {},
        }
        
        self.audit_log.append(event)
        
        # Keep only last 10,000 events
        if len(self.audit_log) > 10000:
            self.audit_log = self.audit_log[-10000:]
    
    def generate_compliance_report(self, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """
        Generate compliance report for time period.
        
        Args:
            start_date: Start date
            end_date: End date
            
        Returns:
            Compliance report
        """
        events_in_period = [
            event for event in self.audit_log
            if start_date <= datetime.fromisoformat(event["timestamp"]) <= end_date
        ]
        
        report = {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "total_events": len(events_in_period),
            "events_by_type": {},
            "events_by_severity": {},
            "unique_users": set(),
            "unique_ips": set(),
        }
        
        for event in events_in_period:
            # Count by type
            event_type = event["event_type"]
            report["events_by_type"][event_type] = report["events_by_type"].get(event_type, 0) + 1
            
            # Count by severity
            severity = event["severity"]
            report["events_by_severity"][severity] = report["events_by_severity"].get(severity, 0) + 1
            
            # Track unique users and IPs
            if event["user_id"]:
                report["unique_users"].add(event["user_id"])
            if event["ip_address"]:
                report["unique_ips"].add(event["ip_address"])
        
        report["unique_users"] = list(report["unique_users"])
        report["unique_ips"] = list(report["unique_ips"])
        
        return report
    
    def rotate_all_keys(self) -> Dict[str, bool]:
        """
        Rotate all security keys.
        
        Returns:
            Dictionary with rotation results
        """
        results = {}
        
        try:
            results["encryption"] = self.encryption_manager.rotate_keys()
        except Exception as e:
            results["encryption"] = False
            self.audit_event(
                "KEY_ROTATION_FAILED",
                details={"component": "encryption", "error": str(e)},
                severity="ERROR",
            )
        
        try:
            results["jwt"] = self.jwt_manager.rotate_secret_key()
        except Exception as e:
            results["jwt"] = False
            self.audit_event(
                "KEY_ROTATION_FAILED",
                details={"component": "jwt", "error": str(e)},
                severity="ERROR",
            )
        
        self.audit_event(
            "KEY_ROTATION_COMPLETED",
            details={"results": results},
            severity="INFO",
        )
        
        return results


def get_security_manager() -> SecurityManager:
    """Get or create global security manager."""
    global _security_manager
    
    if _security_manager is None:
        _security_manager = SecurityManager()
    
    return _security_manager


# Export public API
__all__ = [
    # Classes
    "SecurityManager",
    "EncryptionManager",
    "HashManager",
    "JWTManager",
    "InputValidator",
    "RateLimiter",
    "CORSManager",
    "SecurityHeaders",
    "PasswordPolicy",
    "RateLimitConfig",
    
    # Enums
    "SecurityLevel",
    "HashAlgorithm",
    "EncryptionAlgorithm",
    
    # Functions
    "get_security_manager",
    "sanitize_html",
    "prevent_sql_injection",
    "prevent_xss",
    "prevent_path_traversal",
    "validate_email",
    "validate_url",
    "validate_password_strength",
    
    # Utilities
    "audit_event",
    "generate_compliance_report",
    "rotate_all_keys",
]