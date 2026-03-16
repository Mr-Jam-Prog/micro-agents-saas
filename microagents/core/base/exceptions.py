"""
Exceptions personnalisées pour le système d'agents.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass


@dataclass
class ErrorDetail:
    """Détail d'une erreur"""
    code: str
    message: str
    field: Optional[str] = None
    value: Optional[Any] = None
    suggestion: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        result = {
            "code": self.code,
            "message": self.message
        }
        if self.field:
            result["field"] = self.field
        if self.value is not None:
            result["value"] = self.value
        if self.suggestion:
            result["suggestion"] = self.suggestion
        return result


class AgentError(Exception):
    """Exception de base pour toutes les erreurs d'agent"""
    
    def __init__(
        self,
        message: str,
        code: str = "AGENT_ERROR",
        details: Optional[List[ErrorDetail]] = None,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or []
        self.context = context or {}
        self.cause = cause
    
    def add_detail(self, detail: ErrorDetail):
        """Ajoute un détail d'erreur"""
        self.details.append(detail)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'exception en dictionnaire"""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": [d.to_dict() for d in self.details],
                "context": self.context
            }
        }
    
    def __str__(self) -> str:
        details = f", details: {self.details}" if self.details else ""
        context = f", context: {self.context}" if self.context else ""
        return f"{self.code}: {self.message}{details}{context}"


class ConfigurationError(AgentError):
    """Erreur de configuration"""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="CONFIGURATION_ERROR", **kwargs)


class ValidationError(AgentError):
    """Erreur de validation"""
    def __init__(self, message: str, field: Optional[str] = None, **kwargs):
        if field:
            detail = ErrorDetail(
                code="VALIDATION_ERROR",
                message=message,
                field=field
            )
            kwargs.setdefault("details", []).append(detail)
        super().__init__(message, code="VALIDATION_ERROR", **kwargs)


class ContextValidationError(ValidationError):
    """Erreur de validation du contexte"""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="CONTEXT_VALIDATION_ERROR", **kwargs)


class ExecutionError(AgentError):
    """Erreur lors de l'exécution de l'agent"""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="EXECUTION_ERROR", **kwargs)


class TimeoutError(ExecutionError):
    """Timeout de l'exécution"""
    def __init__(self, timeout: float, **kwargs):
        message = f"Exécution timeout après {timeout} secondes"
        super().__init__(message, code="TIMEOUT_ERROR", **kwargs)


class CircuitOpenError(ExecutionError):
    """Erreur lorsque le circuit breaker est ouvert"""
    def __init__(self, agent_name: str, tenant_id: str, **kwargs):
        message = f"Circuit breaker ouvert pour l'agent {agent_name} (tenant: {tenant_id})"
        super().__init__(message, code="CIRCUIT_OPEN_ERROR", **kwargs)


class ResourceExhaustedError(ExecutionError):
    """Erreur lorsque les ressources sont épuisées"""
    def __init__(self, resource: str, limit: Any, current: Any, **kwargs):
        message = f"Ressource {resource} épuisée (limite: {limit}, actuel: {current})"
        super().__init__(message, code="RESOURCE_EXHAUSTED_ERROR", **kwargs)


class DependencyError(ExecutionError):
    """Erreur de dépendance"""
    def __init__(self, dependency: str, reason: str, **kwargs):
        message = f"Dépendance {dependency} échouée: {reason}"
        super().__init__(message, code="DEPENDENCY_ERROR", **kwargs)


class SecurityError(AgentError):
    """Erreur de sécurité"""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="SECURITY_ERROR", **kwargs)


class TenantIsolationError(SecurityError):
    """Erreur d'isolation de tenant"""
    def __init__(self, tenant_id: str, **kwargs):
        message = f"Violation d'isolation pour le tenant {tenant_id}"
        super().__init__(message, code="TENANT_ISOLATION_ERROR", **kwargs)


class RateLimitExceededError(SecurityError):
    """Erreur de rate limiting"""
    def __init__(self, limit: int, period: int, **kwargs):
        message = f"Rate limit dépassé: {limit} requêtes par {period} secondes"
        super().__init__(message, code="RATE_LIMIT_EXCEEDED_ERROR", **kwargs)


class CacheError(AgentError):
    """Erreur de cache"""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="CACHE_ERROR", **kwargs)


class SerializationError(AgentError):
    """Erreur de sérialisation"""
    def __init__(self, message: str, data_type: str, **kwargs):
        message = f"Erreur de sérialisation pour {data_type}: {message}"
        super().__init__(message, code="SERIALIZATION_ERROR", **kwargs)


class RegistryError(AgentError):
    """Erreur du registre"""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="REGISTRY_ERROR", **kwargs)


class AgentNotFoundError(RegistryError):
    """Erreur lorsque l'agent n'est pas trouvé"""
    def __init__(self, agent_name: str, **kwargs):
        message = f"Agent non trouvé: {agent_name}"
        super().__init__(message, code="AGENT_NOT_FOUND_ERROR", **kwargs)


class HealthCheckError(AgentError):
    """Erreur de health check"""
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="HEALTH_CHECK_ERROR", **kwargs)


class RetryExhaustedError(ExecutionError):
    """Erreur lorsque toutes les tentatives de retry sont épuisées"""
    def __init__(self, max_attempts: int, last_error: Exception, **kwargs):
        message = f"Échec après {max_attempts} tentatives. Dernière erreur: {last_error}"
        super().__init__(message, code="RETRY_EXHAUSTED_ERROR", **kwargs)