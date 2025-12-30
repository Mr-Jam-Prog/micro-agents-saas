"""
Types de base pour le système d'agents.
"""

from dataclasses import dataclass, field, asdict
from typing import (
    Any, Dict, List, Optional, Union, TypeVar, Generic, 
    Callable, Awaitable, ClassVar
)
from datetime import datetime
from enum import Enum, auto
import uuid

from pydantic import BaseModel, Field, validator


T = TypeVar('T')
R = TypeVar('R')


class AgentStatus(Enum):
    """Statut d'un agent"""
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    ERROR = auto()
    MAINTENANCE = auto()


class ExecutionPriority(Enum):
    """Priorité d'exécution"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class AgentIdentifier:
    """Identifiant unique d'un agent"""
    name: str
    version: str
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    namespace: str = "default"
    
    @property
    def full_name(self) -> str:
        """Retourne le nom complet de l'agent"""
        return f"{self.namespace}/{self.name}:{self.version}"
    
    def to_dict(self) -> Dict[str, str]:
        """Convertit en dictionnaire"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "AgentIdentifier":
        """Crée un identifiant à partir d'un dictionnaire"""
        return cls(**data)


@dataclass
class AgentResult(Generic[T]):
    """Résultat de l'exécution d'un agent"""
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    execution_time: float = 0.0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    context: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    
    @property
    def is_success(self) -> bool:
        """Vérifie si l'exécution a réussi"""
        return self.success
    
    @property
    def is_failure(self) -> bool:
        """Vérifie si l'exécution a échoué"""
        return not self.success
    
    def add_warning(self, warning: str):
        """Ajoute un avertissement"""
        self.warnings.append(warning)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        result = asdict(self)
        
        # Convertir les datetime en string
        if self.start_time:
            result['start_time'] = self.start_time.isoformat()
        if self.end_time:
            result['end_time'] = self.end_time.isoformat()
        
        return result
    
    @classmethod
    def success_result(cls, data: T, **kwargs) -> "AgentResult[T]":
        """Crée un résultat de succès"""
        return cls(
            success=True,
            data=data,
            start_time=datetime.now(),
            end_time=datetime.now(),
            **kwargs
        )
    
    @classmethod
    def failure_result(cls, error: str, error_type: str = "UnknownError", **kwargs) -> "AgentResult[Any]":
        """Crée un résultat d'échec"""
        return cls(
            success=False,
            error=error,
            error_type=error_type,
            start_time=datetime.now(),
            end_time=datetime.now(),
            **kwargs
        )


class AgentContext(BaseModel):
    """Modèle Pydantic pour le contexte d'exécution"""
    tenant_id: str = Field(..., description="Identifiant du tenant")
    correlation_id: str = Field(
        default_factory=lambda: f"corr_{uuid.uuid4().hex[:8]}"
    )
    user_id: Optional[str] = Field(None, description="Identifiant utilisateur")
    request_id: Optional[str] = Field(
        default_factory=lambda: f"req_{uuid.uuid4().hex[:8]}"
    )
    session_id: Optional[str] = Field(None, description="ID de session")
    environment: str = Field("production", description="Environnement d'exécution")
    priority: ExecutionPriority = Field(
        default=ExecutionPriority.NORMAL,
        description="Priorité d'exécution"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Horodatage de la requête"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées supplémentaires"
    )
    
    class Config:
        use_enum_values = True
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            ExecutionPriority: lambda p: p.name.lower()
        }
    
    @validator('tenant_id')
    def validate_tenant_id(cls, v):
        """Valide l'ID du tenant"""
        if not v or not v.strip():
            raise ValueError("tenant_id ne peut pas être vide")
        return v.strip()
    
    def enrich(self, **kwargs) -> "AgentContext":
        """Enrichit le contexte avec de nouvelles données"""
        data = self.dict()
        data.update(kwargs)
        return AgentContext(**data)
    
    def get_log_context(self) -> Dict[str, Any]:
        """Retourne le contexte pour le logging structuré"""
        return {
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "request_id": self.request_id,
            "environment": self.environment,
            "priority": self.priority.name.lower()
        }


@dataclass
class ValidationResult:
    """Résultat de validation"""
    valid: bool
    errors: Optional[Dict[str, str]] = None
    warnings: Optional[Dict[str, str]] = None
    suggestions: Optional[List[str]] = None
    
    def add_error(self, field: str, message: str) -> None:
        """Ajoute une erreur de validation"""
        if self.errors is None:
            self.errors = {}
        self.errors[field] = message
        self.valid = False
    
    def add_warning(self, field: str, message: str) -> None:
        """Ajoute un avertissement de validation"""
        if self.warnings is None:
            self.warnings = {}
        self.warnings[field] = message
    
    def add_suggestion(self, suggestion: str) -> None:
        """Ajoute une suggestion"""
        if self.suggestions is None:
            self.suggestions = []
        self.suggestions.append(suggestion)
    
    @classmethod
    def valid_result(cls) -> "ValidationResult":
        """Crée un résultat valide"""
        return cls(valid=True)
    
    @classmethod
    def invalid_result(cls, errors: Dict[str, str]) -> "ValidationResult":
        """Crée un résultat invalide"""
        return cls(valid=False, errors=errors)


@dataclass
class AgentCapabilities:
    """Capacités d'un agent"""
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    supported_formats: List[str] = field(default_factory=lambda: ["json"])
    max_input_size: Optional[int] = None  # bytes
    max_output_size: Optional[int] = None  # bytes
    concurrency_limit: Optional[int] = None
    memory_limit: Optional[int] = None  # MB
    timeout_supported: bool = True
    retry_supported: bool = True
    cache_supported: bool = True
    streaming_supported: bool = False
    batch_processing: bool = False
    
    def can_handle(self, input_size: int = 0) -> bool:
        """Vérifie si l'agent peut gérer une requête"""
        if self.max_input_size and input_size > self.max_input_size:
            return False
        return True


@dataclass
class AgentDependency:
    """Dépendance d'un agent"""
    name: str
    version: str
    optional: bool = False
    description: Optional[str] = None
    health_check: Optional[str] = None
    timeout: float = 5.0
    
    def __str__(self) -> str:
        return f"{self.name}=={self.version}"