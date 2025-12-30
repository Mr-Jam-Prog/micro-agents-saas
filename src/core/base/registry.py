"""
Module: Agent Registry
Description: Registre centralisé pour la gestion des micro-agents avec marketplace et versioning
Version: 2.0.0
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    Generic,
    TypeVar,
    Callable,
    AsyncGenerator,
    cast,
)
from uuid import UUID, uuid4

import semver
import structlog
from pydantic import BaseModel, Field, ValidationError, validator, model_validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Self

from .agent import MicroAgent
from .exceptions import (
    RegistryError,
    AgentNotFoundError,
    ValidationError as AgentValidationError,
    DependencyError
)
from .types import AgentIdentifier, AgentCapabilities, AgentDependency


# ====== Types et Enums ======

T = TypeVar("T", bound=MicroAgent)
AgentID = str


class AgentStatus(str, Enum):
    """Statut d'un agent dans le registre"""
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    PRIVATE = "private"
    BETA = "beta"
    STABLE = "stable"


class AgentCategory(str, Enum):
    """Catégories d'agents"""
    MONITORING = "monitoring"
    SECURITY = "security"
    COST_OPTIMIZATION = "cost_optimization"
    COMPLIANCE = "compliance"
    PERFORMANCE = "performance"
    DEPLOYMENT = "deployment"
    TESTING = "testing"
    ANALYTICS = "analytics"
    INTEGRATION = "integration"
    CUSTOM = "custom"


class LicenseType(str, Enum):
    """Types de licence"""
    MIT = "mit"
    APACHE_2 = "apache_2"
    GPL_3 = "gpl_3"
    AGPL_3 = "agpl_3"
    PROPRIETARY = "proprietary"
    COMMERCIAL = "commercial"
    CUSTOM = "custom"


class StorageBackend(str, Enum):
    """Backends de stockage supportés"""
    MEMORY = "memory"
    REDIS = "redis"
    POSTGRESQL = "postgresql"
    FILESYSTEM = "filesystem"
    S3 = "s3"
    DYNAMODB = "dynamodb"


class SearchOperator(str, Enum):
    """Opérateurs de recherche"""
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    IN = "in"
    NOT_IN = "not_in"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"


# ====== Modèles de données ======

@dataclass(frozen=True)
class AgentRating:
    """Note et review d'un agent"""
    rating_id: UUID = field(default_factory=uuid4)
    agent_id: AgentID
    version: str
    user_id: str
    rating: int = Field(ge=1, le=5)  # 1-5 étoiles
    review: Optional[str] = None
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    helpful_count: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def formatted_review(self) -> Optional[str]:
        """Retourne la review formatée"""
        if not self.review:
            return None
        
        # Ajouter pros/cons à la review
        parts = [self.review]
        
        if self.pros:
            parts.append(f"\nPros: {', '.join(self.pros)}")
        if self.cons:
            parts.append(f"\nCons: {', '.join(self.cons)}")
        
        return "\n".join(parts)
    
    def mark_helpful(self) -> AgentRating:
        """Marque la review comme utile (retourne nouvelle instance)"""
        return AgentRating(
            rating_id=self.rating_id,
            agent_id=self.agent_id,
            version=self.version,
            user_id=self.user_id,
            rating=self.rating,
            review=self.review,
            pros=self.pros,
            cons=self.cons,
            helpful_count=self.helpful_count + 1,
            created_at=self.created_at,
            updated_at=datetime.utcnow()
        )


@dataclass
class UsageStatistics:
    """Statistiques d'utilisation d'un agent"""
    agent_id: AgentID
    version: str
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_execution_time: float = 0.0
    avg_execution_time: float = 0.0
    unique_users: Set[str] = field(default_factory=set)
    unique_tenants: Set[str] = field(default_factory=set)
    last_execution: Optional[datetime] = None
    execution_trend: List[Tuple[datetime, int]] = field(default_factory=list)  # (timestamp, count)
    
    @property
    def success_rate(self) -> float:
        """Taux de succès"""
        if self.total_executions == 0:
            return 0.0
        return (self.successful_executions / self.total_executions) * 100
    
    @property
    def avg_daily_executions(self) -> float:
        """Exécutions moyennes par jour"""
        if not self.execution_trend:
            return 0.0
        
        days = len({date.date() for date, _ in self.execution_trend})
        if days == 0:
            return 0.0
        
        return self.total_executions / days
    
    def record_execution(
        self,
        user_id: str,
        tenant_id: str,
        success: bool,
        execution_time: float
    ) -> None:
        """Enregistre une exécution"""
        self.total_executions += 1
        
        if success:
            self.successful_executions += 1
        else:
            self.failed_executions += 1
        
        self.total_execution_time += execution_time
        self.avg_execution_time = self.total_execution_time / self.total_executions
        
        self.unique_users.add(user_id)
        self.unique_tenants.add(tenant_id)
        
        now = datetime.utcnow()
        self.last_execution = now
        
        # Garder seulement les 30 derniers jours
        cutoff = now - timedelta(days=30)
        self.execution_trend = [
            (ts, count) for ts, count in self.execution_trend
            if ts >= cutoff
        ]
        
        # Ajouter l'exécution actuelle (regroupée par heure)
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        found = False
        
        for i, (ts, count) in enumerate(self.execution_trend):
            if ts == current_hour:
                self.execution_trend[i] = (ts, count + 1)
                found = True
                break
        
        if not found:
            self.execution_trend.append((current_hour, 1))
        
        # Trier par date
        self.execution_trend.sort(key=lambda x: x[0])


@dataclass
class AgentMetadata:
    """Métadonnées d'un agent"""
    # Identification
    agent_id: AgentID
    name: str
    namespace: str = "default"
    version: str = "1.0.0"
    
    # Description
    display_name: str = ""
    description: str = ""
    long_description: Optional[str] = None
    documentation_url: Optional[str] = None
    
    # Classification
    category: AgentCategory = AgentCategory.CUSTOM
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    
    # Statut
    status: AgentStatus = AgentStatus.DRAFT
    visibility: str = "public"  # public, private, unlisted
    
    # Détails techniques
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    dependencies: List[AgentDependency] = field(default_factory=list)
    supported_platforms: List[str] = field(default_factory=lambda: ["linux", "windows", "macos"])
    python_version: str = ">=3.8"
    
    # Licensing
    license: LicenseType = LicenseType.MIT
    license_url: Optional[str] = None
    
    # Auteur
    author: str = ""
    author_email: Optional[str] = None
    maintainers: List[str] = field(default_factory=list)
    repository_url: Optional[str] = None
    issue_tracker_url: Optional[str] = None
    
    # Marketplace
    price_tier: str = "free"  # free, basic, pro, enterprise
    trial_days: int = 0
    featured: bool = False
    
    # Statistiques
    download_count: int = 0
    rating_average: float = 0.0
    rating_count: int = 0
    last_updated: datetime = field(default_factory=datetime.utcnow)
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    # ROI metrics
    avg_roi: Optional[float] = None
    avg_execution_time: Optional[float] = None
    avg_cost_saving: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        data = asdict(self)
        
        # Convertir les enums en strings
        for field_name, field_value in data.items():
            if isinstance(field_value, Enum):
                data[field_name] = field_value.value
        
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentMetadata:
        """Crée depuis un dictionnaire"""
        # Convertir les strings en enums
        if "category" in data and isinstance(data["category"], str):
            data["category"] = AgentCategory(data["category"])
        
        if "status" in data and isinstance(data["status"], str):
            data["status"] = AgentStatus(data["status"])
        
        if "license" in data and isinstance(data["license"], str):
            data["license"] = LicenseType(data["license"])
        
        return cls(**data)
    
    @property
    def full_name(self) -> str:
        """Nom complet de l'agent (namespace/name)"""
        return f"{self.namespace}/{self.name}"
    
    @property
    def identifier(self) -> AgentIdentifier:
        """Retourne l'identifiant d'agent"""
        return AgentIdentifier(
            name=self.name,
            version=self.version,
            namespace=self.namespace
        )
    
    @property
    def is_compatible(self) -> bool:
        """Vérifie la compatibilité avec la version Python courante"""
        import sys
        
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        
        # Vérifier les spécifications de version
        if self.python_version.startswith(">="):
            required = self.python_version[2:]
            return semver.compare(python_version, required) >= 0
        elif self.python_version.startswith(">"):
            required = self.python_version[1:]
            return semver.compare(python_version, required) > 0
        elif self.python_version.startswith("<="):
            required = self.python_version[2:]
            return semver.compare(python_version, required) <= 0
        elif self.python_version.startswith("<"):
            required = self.python_version[1:]
            return semver.compare(python_version, required) < 0
        elif self.python_version.startswith("=="):
            required = self.python_version[2:]
            return python_version == required
        
        return True
    
    def validate_version(self) -> bool:
        """Valide le format de version (semver)"""
        try:
            semver.VersionInfo.parse(self.version)
            return True
        except ValueError:
            return False
    
    def get_semantic_version(self) -> semver.VersionInfo:
        """Retourne la version au format semver"""
        return semver.VersionInfo.parse(self.version)


@dataclass
class SearchFilter:
    """Filtre de recherche"""
    field: str
    operator: SearchOperator
    value: Any
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            "field": self.field,
            "operator": self.operator.value,
            "value": self.value
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SearchFilter:
        """Crée depuis un dictionnaire"""
        return cls(
            field=data["field"],
            operator=SearchOperator(data["operator"]),
            value=data["value"]
        )


@dataclass
class SearchQuery:
    """Requête de recherche"""
    filters: List[SearchFilter] = field(default_factory=list)
    sort_by: str = "download_count"
    sort_order: str = "desc"
    page: int = 1
    page_size: int = 20
    include_deprecated: bool = False
    
    def add_filter(self, field: str, operator: SearchOperator, value: Any) -> None:
        """Ajoute un filtre"""
        self.filters.append(SearchFilter(field, operator, value))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            "filters": [f.to_dict() for f in self.filters],
            "sort_by": self.sort_by,
            "sort_order": self.sort_order,
            "page": self.page,
            "page_size": self.page_size,
            "include_deprecated": self.include_deprecated
        }


@dataclass
class SearchResult:
    """Résultat de recherche"""
    agents: List[AgentMetadata]
    total_count: int
    page: int
    page_size: int
    has_more: bool
    
    @property
    def total_pages(self) -> int:
        """Nombre total de pages"""
        if self.page_size == 0:
            return 0
        return (self.total_count + self.page_size - 1) // self.page_size


@dataclass
class PluginConfig:
    """Configuration d'un plugin de stockage"""
    backend: StorageBackend
    config: Dict[str, Any]
    priority: int = 100  # Priorité (plus bas = exécuté en premier)
    enabled: bool = True


# ====== Interface de stockage (Plugin System) ======

class StoragePlugin(ABC):
    """Interface pour les plugins de stockage"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = structlog.get_logger(
            plugin_name=self.__class__.__name__
        )
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialise le plugin"""
        pass
    
    @abstractmethod
    async def save_agent(self, agent_id: AgentID, version: str, data: bytes) -> None:
        """Sauvegarde un agent"""
        pass
    
    @abstractmethod
    async def load_agent(self, agent_id: AgentID, version: str) -> Optional[bytes]:
        """Charge un agent"""
        pass
    
    @abstractmethod
    async def delete_agent(self, agent_id: AgentID, version: str) -> bool:
        """Supprime un agent"""
        pass
    
    @abstractmethod
    async def list_agents(self) -> List[Tuple[AgentID, str]]:
        """Liste tous les agents"""
        pass
    
    @abstractmethod
    async def save_metadata(self, metadata: AgentMetadata) -> None:
        """Sauvegarde les métadonnées"""
        pass
    
    @abstractmethod
    async def load_metadata(self, agent_id: AgentID, version: str) -> Optional[AgentMetadata]:
        """Charge les métadonnées"""
        pass
    
    @abstractmethod
    async def search_metadata(self, query: SearchQuery) -> SearchResult:
        """Recherche dans les métadonnées"""
        pass
    
    async def close(self) -> None:
        """Ferme les connexions"""
        pass
    
    @property
    @abstractmethod
    def supports_transactions(self) -> bool:
        """Indique si le backend supporte les transactions"""
        pass


class MemoryStoragePlugin(StoragePlugin):
    """Plugin de stockage en mémoire (pour tests)"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._agents: Dict[Tuple[AgentID, str], bytes] = {}
        self._metadata: Dict[Tuple[AgentID, str], AgentMetadata] = {}
        self._metadata_index: List[AgentMetadata] = []
    
    async def initialize(self) -> None:
        """Initialise le plugin"""
        self.logger.info("memory_storage_initialized")
    
    async def save_agent(self, agent_id: AgentID, version: str, data: bytes) -> None:
        """Sauvegarde un agent"""
        key = (agent_id, version)
        self._agents[key] = data
    
    async def load_agent(self, agent_id: AgentID, version: str) -> Optional[bytes]:
        """Charge un agent"""
        return self._agents.get((agent_id, version))
    
    async def delete_agent(self, agent_id: AgentID, version: str) -> bool:
        """Supprime un agent"""
        key = (agent_id, version)
        if key in self._agents:
            del self._agents[key]
            del self._metadata[key]
            self._metadata_index = [
                m for m in self._metadata_index 
                if not (m.agent_id == agent_id and m.version == version)
            ]
            return True
        return False
    
    async def list_agents(self) -> List[Tuple[AgentID, str]]:
        """Liste tous les agents"""
        return list(self._agents.keys())
    
    async def save_metadata(self, metadata: AgentMetadata) -> None:
        """Sauvegarde les métadonnées"""
        key = (metadata.agent_id, metadata.version)
        self._metadata[key] = metadata
        
        # Mettre à jour l'index
        existing_index = next(
            (i for i, m in enumerate(self._metadata_index) 
             if m.agent_id == metadata.agent_id and m.version == metadata.version),
            None
        )
        
        if existing_index is not None:
            self._metadata_index[existing_index] = metadata
        else:
            self._metadata_index.append(metadata)
    
    async def load_metadata(self, agent_id: AgentID, version: str) -> Optional[AgentMetadata]:
        """Charge les métadonnées"""
        return self._metadata.get((agent_id, version))
    
    async def search_metadata(self, query: SearchQuery) -> SearchResult:
        """Recherche dans les métadonnées"""
        # Filtrer les agents
        filtered = self._metadata_index.copy()
        
        # Appliquer les filtres
        for filter_obj in query.filters:
            filtered = self._apply_filter(filtered, filter_obj)
        
        # Inclure les dépréciés ?
        if not query.include_deprecated:
            filtered = [
                m for m in filtered 
                if m.status != AgentStatus.DEPRECATED
            ]
        
        # Trier
        filtered.sort(
            key=lambda x: getattr(x, query.sort_by, 0),
            reverse=(query.sort_order == "desc")
        )
        
        # Pagination
        total_count = len(filtered)
        start_idx = (query.page - 1) * query.page_size
        end_idx = start_idx + query.page_size
        paginated = filtered[start_idx:end_idx]
        
        return SearchResult(
            agents=paginated,
            total_count=total_count,
            page=query.page,
            page_size=query.page_size,
            has_more=end_idx < total_count
        )
    
    def _apply_filter(self, agents: List[AgentMetadata], filter_obj: SearchFilter) -> List[AgentMetadata]:
        """Applique un filtre"""
        result = []
        
        for agent in agents:
            field_value = getattr(agent, filter_obj.field, None)
            
            if field_value is None:
                continue
            
            if filter_obj.operator == SearchOperator.EQUALS:
                if field_value == filter_obj.value:
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.NOT_EQUALS:
                if field_value != filter_obj.value:
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.CONTAINS:
                if isinstance(field_value, str) and filter_obj.value in field_value:
                    result.append(agent)
                elif isinstance(field_value, list) and filter_obj.value in field_value:
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.GREATER_THAN:
                if field_value > filter_obj.value:
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.LESS_THAN:
                if field_value < filter_obj.value:
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.IN:
                if field_value in filter_obj.value:
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.NOT_IN:
                if field_value not in filter_obj.value:
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.STARTS_WITH:
                if isinstance(field_value, str) and field_value.startswith(filter_obj.value):
                    result.append(agent)
            elif filter_obj.operator == SearchOperator.ENDS_WITH:
                if isinstance(field_value, str) and field_value.endswith(filter_obj.value):
                    result.append(agent)
        
        return result
    
    @property
    def supports_transactions(self) -> bool:
        return False


class FilesystemStoragePlugin(StoragePlugin):
    """Plugin de stockage sur filesystem"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_path = Path(config.get("base_path", "./agent_registry"))
        self.agents_path = self.base_path / "agents"
        self.metadata_path = self.base_path / "metadata"
        
    async def initialize(self) -> None:
        """Initialise le plugin"""
        self.agents_path.mkdir(parents=True, exist_ok=True)
        self.metadata_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(
            "filesystem_storage_initialized",
            base_path=str(self.base_path)
        )
    
    def _get_agent_path(self, agent_id: AgentID, version: str) -> Path:
        """Retourne le chemin pour un agent"""
        safe_agent_id = agent_id.replace("/", "_")
        return self.agents_path / safe_agent_id / f"{version}.agent"
    
    def _get_metadata_path(self, agent_id: AgentID, version: str) -> Path:
        """Retourne le chemin pour les métadonnées"""
        safe_agent_id = agent_id.replace("/", "_")
        return self.metadata_path / safe_agent_id / f"{version}.json"
    
    async def save_agent(self, agent_id: AgentID, version: str, data: bytes) -> None:
        """Sauvegarde un agent"""
        agent_path = self._get_agent_path(agent_id, version)
        agent_path.parent.mkdir(parents=True, exist_ok=True)
        
        agent_path.write_bytes(data)
    
    async def load_agent(self, agent_id: AgentID, version: str) -> Optional[bytes]:
        """Charge un agent"""
        agent_path = self._get_agent_path(agent_id, version)
        
        if agent_path.exists():
            return agent_path.read_bytes()
        return None
    
    async def delete_agent(self, agent_id: AgentID, version: str) -> bool:
        """Supprime un agent"""
        agent_path = self._get_agent_path(agent_id, version)
        metadata_path = self._get_metadata_path(agent_id, version)
        
        deleted = False
        
        if agent_path.exists():
            agent_path.unlink()
            deleted = True
            
            # Supprimer le dossier parent s'il est vide
            try:
                agent_path.parent.rmdir()
            except OSError:
                pass
        
        if metadata_path.exists():
            metadata_path.unlink()
            
            try:
                metadata_path.parent.rmdir()
            except OSError:
                pass
        
        return deleted
    
    async def list_agents(self) -> List[Tuple[AgentID, str]]:
        """Liste tous les agents"""
        agents = []
        
        for agent_dir in self.agents_path.iterdir():
            if agent_dir.is_dir():
                agent_id = agent_dir.name.replace("_", "/")
                
                for agent_file in agent_dir.glob("*.agent"):
                    version = agent_file.stem
                    agents.append((agent_id, version))
        
        return agents
    
    async def save_metadata(self, metadata: AgentMetadata) -> None:
        """Sauvegarde les métadonnées"""
        metadata_path = self._get_metadata_path(metadata.agent_id, metadata.version)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        
        metadata_dict = metadata.to_dict()
        metadata_path.write_text(
            json.dumps(metadata_dict, indent=2, default=str)
        )
    
    async def load_metadata(self, agent_id: AgentID, version: str) -> Optional[AgentMetadata]:
        """Charge les métadonnées"""
        metadata_path = self._get_metadata_path(agent_id, version)
        
        if metadata_path.exists():
            metadata_dict = json.loads(metadata_path.read_text())
            return AgentMetadata.from_dict(metadata_dict)
        
        return None
    
    async def search_metadata(self, query: SearchQuery) -> SearchResult:
        """Recherche dans les métadonnées (implémentation basique)"""
        # Pour une implémentation complète, il faudrait indexer les métadonnées
        # Ici on fait une recherche linéaire simple
        
        agents = []
        total_agents = 0
        
        for metadata_file in self.metadata_path.rglob("*.json"):
            try:
                metadata_dict = json.loads(metadata_file.read_text())
                metadata = AgentMetadata.from_dict(metadata_dict)
                
                # Appliquer les filtres basiques
                include = True
                
                for filter_obj in query.filters:
                    field_value = getattr(metadata, filter_obj.field, None)
                    
                    if not self._matches_filter(field_value, filter_obj):
                        include = False
                        break
                
                if include and (query.include_deprecated or metadata.status != AgentStatus.DEPRECATED):
                    agents.append(metadata)
                    total_agents += 1
                    
            except Exception as e:
                self.logger.error("metadata_load_error", path=str(metadata_file), error=str(e))
        
        # Trier
        agents.sort(
            key=lambda x: getattr(x, query.sort_by, 0),
            reverse=(query.sort_order == "desc")
        )
        
        # Pagination
        start_idx = (query.page - 1) * query.page_size
        end_idx = start_idx + query.page_size
        paginated = agents[start_idx:end_idx]
        
        return SearchResult(
            agents=paginated,
            total_count=total_agents,
            page=query.page,
            page_size=query.page_size,
            has_more=end_idx < total_agents
        )
    
    def _matches_filter(self, field_value: Any, filter_obj: SearchFilter) -> bool:
        """Vérifie si une valeur correspond au filtre"""
        if field_value is None:
            return False
        
        if filter_obj.operator == SearchOperator.EQUALS:
            return field_value == filter_obj.value
        elif filter_obj.operator == SearchOperator.NOT_EQUALS:
            return field_value != filter_obj.value
        elif filter_obj.operator == SearchOperator.CONTAINS:
            if isinstance(field_value, str):
                return filter_obj.value in field_value
            elif isinstance(field_value, list):
                return filter_obj.value in field_value
        elif filter_obj.operator == SearchOperator.GREATER_THAN:
            return field_value > filter_obj.value
        elif filter_obj.operator == SearchOperator.LESS_THAN:
            return field_value < filter_obj.value
        
        return True
    
    @property
    def supports_transactions(self) -> bool:
        return False


# ====== Registre principal ======

class AgentRegistry:
    """
    Registre centralisé pour la gestion des micro-agents.
    Supporte le versioning, les dépendances, la recherche et le marketplace.
    """
    
    def __init__(
        self,
        storage_plugins: Optional[List[PluginConfig]] = None,
        default_namespace: str = "official"
    ):
        self.logger = structlog.get_logger(
            registry_name="AgentRegistry",
            default_namespace=default_namespace
        )
        
        self.default_namespace = default_namespace
        self._plugins: List[StoragePlugin] = []
        self._active_plugin: Optional[StoragePlugin] = None
        
        # Cache
        self._agent_cache: Dict[Tuple[AgentID, str], MicroAgent] = {}
        self._metadata_cache: Dict[Tuple[AgentID, str], AgentMetadata] = {}
        
        # Statistiques
        self._usage_stats: Dict[Tuple[AgentID, str], UsageStatistics] = {}
        self._ratings: Dict[Tuple[AgentID, str], List[AgentRating]] = defaultdict(list)
        self._dependencies: Dict[AgentID, Set[AgentID]] = defaultdict(set)
        
        # Configuration des plugins
        self._plugin_configs = storage_plugins or [
            PluginConfig(
                backend=StorageBackend.MEMORY,
                config={},
                priority=100
            )
        ]
    
    async def initialize(self) -> None:
        """Initialise le registre et ses plugins"""
        self.logger.info("initializing_agent_registry")
        
        # Initialiser les plugins par ordre de priorité
        plugin_configs = sorted(self._plugin_configs, key=lambda x: x.priority)
        
        for config in plugin_configs:
            if not config.enabled:
                continue
            
            try:
                plugin = self._create_plugin(config)
                await plugin.initialize()
                self._plugins.append(plugin)
                
                self.logger.info(
                    "plugin_initialized",
                    plugin=plugin.__class__.__name__,
                    backend=config.backend.value
                )
                
                # Utiliser le premier plugin comme actif
                if self._active_plugin is None:
                    self._active_plugin = plugin
                    
            except Exception as e:
                self.logger.error(
                    "plugin_initialization_failed",
                    backend=config.backend.value,
                    error=str(e)
                )
        
        if not self._active_plugin:
            raise RegistryError("Aucun plugin de stockage fonctionnel")
        
        self.logger.info(
            "agent_registry_initialized",
            active_plugin=self._active_plugin.__class__.__name__,
            total_plugins=len(self._plugins)
        )
    
    def _create_plugin(self, config: PluginConfig) -> StoragePlugin:
        """Crée un plugin de stockage"""
        if config.backend == StorageBackend.MEMORY:
            return MemoryStoragePlugin(config.config)
        elif config.backend == StorageBackend.FILESYSTEM:
            return FilesystemStoragePlugin(config.config)
        elif config.backend == StorageBackend.REDIS:
            # Implémentation Redis (exemple)
            from .plugins.redis_plugin import RedisStoragePlugin
            return RedisStoragePlugin(config.config)
        elif config.backend == StorageBackend.POSTGRESQL:
            # Implémentation PostgreSQL (exemple)
            from .plugins.postgres_plugin import PostgresStoragePlugin
            return PostgresStoragePlugin(config.config)
        else:
            raise ValueError(f"Backend non supporté: {config.backend}")
    
    # ====== CRUD Operations ======
    
    async def register(
        self,
        agent: MicroAgent,
        metadata: Optional[AgentMetadata] = None
    ) -> AgentID:
        """
        Enregistre un nouvel agent.
        
        Args:
            agent: Agent à enregistrer
            metadata: Métadonnées optionnelles
            
        Returns:
            ID de l'agent
        """
        try:
            # Générer l'ID de l'agent
            agent_id = self._generate_agent_id(agent)
            
            # Créer les métadonnées si non fournies
            if metadata is None:
                metadata = self._create_default_metadata(agent, agent_id)
            else:
                # Valider les métadonnées
                self._validate_metadata(metadata)
            
            # Vérifier la compatibilité des dépendances
            await self._validate_dependencies(metadata.dependencies)
            
            # Vérifier si l'agent existe déjà
            existing = await self._active_plugin.load_metadata(agent_id, metadata.version)
            if existing:
                raise RegistryError(
                    f"Agent {agent_id} version {metadata.version} existe déjà"
                )
            
            # Sérialiser l'agent
            serialized_agent = self._serialize_agent(agent)
            
            # Sauvegarder via tous les plugins
            for plugin in self._plugins:
                await plugin.save_agent(agent_id, metadata.version, serialized_agent)
                await plugin.save_metadata(metadata)
            
            # Mettre à jour le cache
            cache_key = (agent_id, metadata.version)
            self._agent_cache[cache_key] = agent
            self._metadata_cache[cache_key] = metadata
            
            # Initialiser les statistiques
            self._usage_stats[cache_key] = UsageStatistics(
                agent_id=agent_id,
                version=metadata.version
            )
            
            # Mettre à jour les dépendances
            self._update_dependency_graph(agent_id, metadata.dependencies)
            
            self.logger.info(
                "agent_registered",
                agent_id=agent_id,
                version=metadata.version,
                name=metadata.name,
                category=metadata.category.value
            )
            
            return agent_id
            
        except Exception as e:
            self.logger.error(
                "agent_registration_failed",
                agent_name=getattr(agent, 'name', 'unknown'),
                error=str(e)
            )
            raise RegistryError(f"Échec de l'enregistrement: {e}") from e
    
    async def get(
        self,
        agent_id: AgentID,
        version: str = "latest",
        resolve_dependencies: bool = True
    ) -> MicroAgent:
        """
        Récupère un agent.
        
        Args:
            agent_id: ID de l'agent
            version: Version spécifique ou "latest"
            resolve_dependencies: Charger aussi les dépendances
            
        Returns:
            Instance de l'agent
        """
        try:
            # Trouver la version si "latest"
            if version == "latest":
                version = await self._get_latest_version(agent_id)
            
            cache_key = (agent_id, version)
            
            # Vérifier le cache
            if cache_key in self._agent_cache:
                agent = self._agent_cache[cache_key]
            else:
                # Charger depuis le storage
                serialized = await self._active_plugin.load_agent(agent_id, version)
                if not serialized:
                    raise AgentNotFoundError(agent_id)
                
                agent = self._deserialize_agent(serialized)
                self._agent_cache[cache_key] = agent
            
            # Charger les dépendances si demandé
            if resolve_dependencies:
                metadata = await self.get_metadata(agent_id, version)
                if metadata:
                    await self._load_dependencies(metadata.dependencies)
            
            return agent
            
        except AgentNotFoundError:
            raise
        except Exception as e:
            self.logger.error(
                "agent_load_failed",
                agent_id=agent_id,
                version=version,
                error=str(e)
            )
            raise RegistryError(f"Échec du chargement de l'agent: {e}") from e
    
    async def get_metadata(
        self,
        agent_id: AgentID,
        version: str = "latest"
    ) -> Optional[AgentMetadata]:
        """
        Récupère les métadonnées d'un agent.
        """
        try:
            if version == "latest":
                version = await self._get_latest_version(agent_id)
            
            cache_key = (agent_id, version)
            
            if cache_key in self._metadata_cache:
                return self._metadata_cache[cache_key]
            
            metadata = await self._active_plugin.load_metadata(agent_id, version)
            if metadata:
                self._metadata_cache[cache_key] = metadata
            
            return metadata
            
        except Exception as e:
            self.logger.error(
                "metadata_load_failed",
                agent_id=agent_id,
                version=version,
                error=str(e)
            )
            return None
    
    async def update(
        self,
        agent_id: AgentID,
        new_agent: MicroAgent,
        new_metadata: Optional[AgentMetadata] = None,
        release_notes: Optional[str] = None
    ) -> str:
        """
        Met à jour un agent avec une nouvelle version.
        
        Args:
            agent_id: ID de l'agent
            new_agent: Nouvelle version de l'agent
            new_metadata: Nouvelles métadonnées
            release_notes: Notes de version
            
        Returns:
            Nouvelle version
        """
        try:
            # Récupérer les métadonnées actuelles
            current_metadata = await self.get_metadata(agent_id)
            if not current_metadata:
                raise AgentNotFoundError(agent_id)
            
            # Créer de nouvelles métadonnées
            if new_metadata is None:
                new_metadata = self._create_default_metadata(new_agent, agent_id)
            
            # Vérifier que c'est une nouvelle version
            current_version = current_metadata.get_semantic_version()
            new_version = semver.VersionInfo.parse(new_metadata.version)
            
            if new_version <= current_version:
                raise RegistryError(
                    f"Nouvelle version {new_metadata.version} doit être supérieure à {current_metadata.version}"
                )
            
            # Vérifier la compatibilité
            await self._validate_update_compatibility(current_metadata, new_metadata)
            
            # Mettre à jour les release notes
            if release_notes:
                if not hasattr(new_metadata, 'release_notes'):
                    new_metadata.release_notes = []
                new_metadata.release_notes.append({
                    "version": new_metadata.version,
                    "date": datetime.utcnow().isoformat(),
                    "notes": release_notes
                })
            
            # Sérialiser le nouvel agent
            serialized_agent = self._serialize_agent(new_agent)
            
            # Sauvegarder via tous les plugins
            for plugin in self._plugins:
                await plugin.save_agent(agent_id, new_metadata.version, serialized_agent)
                await plugin.save_metadata(new_metadata)
            
            # Mettre à jour le cache
            new_cache_key = (agent_id, new_metadata.version)
            self._agent_cache[new_cache_key] = new_agent
            self._metadata_cache[new_cache_key] = new_metadata
            
            # Initialiser les statistiques pour la nouvelle version
            self._usage_stats[new_cache_key] = UsageStatistics(
                agent_id=agent_id,
                version=new_metadata.version
            )
            
            self.logger.info(
                "agent_updated",
                agent_id=agent_id,
                old_version=current_metadata.version,
                new_version=new_metadata.version
            )
            
            return new_metadata.version
            
        except Exception as e:
            self.logger.error(
                "agent_update_failed",
                agent_id=agent_id,
                error=str(e)
            )
            raise RegistryError(f"Échec de la mise à jour: {e}") from e
    
    async def deprecate(
        self,
        agent_id: AgentID,
        reason: str,
        replacement_id: Optional[AgentID] = None,
        replacement_version: Optional[str] = None
    ) -> None:
        """
        Marque un agent comme déprécié.
        
        Args:
            agent_id: ID de l'agent
            reason: Raison de la dépréciation
            replacement_id: ID de l'agent de remplacement
            replacement_version: Version de remplacement
        """
        try:
            # Marquer toutes les versions comme dépréciées
            versions = await self._get_all_versions(agent_id)
            
            for version in versions:
                metadata = await self.get_metadata(agent_id, version)
                if metadata:
                    metadata.status = AgentStatus.DEPRECATED
                    
                    # Ajouter la raison
                    metadata.deprecation_info = {
                        "reason": reason,
                        "date": datetime.utcnow().isoformat(),
                        "replacement_agent": replacement_id,
                        "replacement_version": replacement_version
                    }
                    
                    # Sauvegarder les métadonnées mises à jour
                    for plugin in self._plugins:
                        await plugin.save_metadata(metadata)
                    
                    # Mettre à jour le cache
                    cache_key = (agent_id, version)
                    if cache_key in self._metadata_cache:
                        self._metadata_cache[cache_key] = metadata
            
            self.logger.info(
                "agent_deprecated",
                agent_id=agent_id,
                reason=reason,
                replacement=replacement_id
            )
            
        except Exception as e:
            self.logger.error(
                "agent_deprecation_failed",
                agent_id=agent_id,
                error=str(e)
            )
            raise RegistryError(f"Échec de la dépréciation: {e}") from e
    
    async def delete(
        self,
        agent_id: AgentID,
        version: Optional[str] = None
    ) -> bool:
        """
        Supprime un agent ou une version spécifique.
        
        Args:
            agent_id: ID de l'agent
            version: Version spécifique (supprime toutes les versions si None)
            
        Returns:
            True si supprimé
        """
        try:
            deleted = False
            
            if version:
                # Supprimer une version spécifique
                for plugin in self._plugins:
                    if await plugin.delete_agent(agent_id, version):
                        deleted = True
                
                # Nettoyer le cache
                cache_key = (agent_id, version)
                self._agent_cache.pop(cache_key, None)
                self._metadata_cache.pop(cache_key, None)
                self._usage_stats.pop(cache_key, None)
                self._ratings.pop(cache_key, None)
                
            else:
                # Supprimer toutes les versions
                versions = await self._get_all_versions(agent_id)
                
                for v in versions:
                    for plugin in self._plugins:
                        await plugin.delete_agent(agent_id, v)
                    
                    # Nettoyer le cache
                    cache_key = (agent_id, v)
                    self._agent_cache.pop(cache_key, None)
                    self._metadata_cache.pop(cache_key, None)
                    self._usage_stats.pop(cache_key, None)
                    self._ratings.pop(cache_key, None)
                
                deleted = len(versions) > 0
            
            if deleted:
                self.logger.info(
                    "agent_deleted",
                    agent_id=agent_id,
                    version=version or "all"
                )
            
            return deleted
            
        except Exception as e:
            self.logger.error(
                "agent_deletion_failed",
                agent_id=agent_id,
                version=version,
                error=str(e)
            )
            raise RegistryError(f"Échec de la suppression: {e}") from e
    
    # ====== Search Capabilities ======
    
    async def search(self, query: SearchQuery) -> SearchResult:
        """
        Recherche des agents selon des critères.
        
        Args:
            query: Requête de recherche
            
        Returns:
            Résultats de recherche
        """
        try:
            # Utiliser le plugin actif pour la recherche
            result = await self._active_plugin.search_metadata(query)
            
            # Enrichir avec les statistiques
            for agent in result.agents:
                cache_key = (agent.agent_id, agent.version)
                if cache_key in self._usage_stats:
                    stats = self._usage_stats[cache_key]
                    agent.avg_execution_time = stats.avg_execution_time
            
            return result
            
        except Exception as e:
            self.logger.error(
                "search_failed",
                query=query.to_dict(),
                error=str(e)
            )
            raise RegistryError(f"Échec de la recherche: {e}") from e
    
    async def search_by_tags(self, tags: List[str], operator: str = "AND") -> List[AgentMetadata]:
        """
        Recherche par tags.
        
        Args:
            tags: Liste de tags
            operator: "AND" (tous les tags) ou "OR" (au moins un tag)
            
        Returns:
            Agents correspondants
        """
        query = SearchQuery()
        
        if operator == "AND":
            for tag in tags:
                query.add_filter("tags", SearchOperator.CONTAINS, tag)
        else:  # OR
            query.add_filter("tags", SearchOperator.IN, tags)
        
        result = await self.search(query)
        return result.agents
    
    async def search_by_capability(self, capability: str) -> List[AgentMetadata]:
        """
        Recherche par capacité.
        """
        # Recherche dans les noms des agents
        query = SearchQuery()
        query.add_filter("name", SearchOperator.CONTAINS, capability)
        
        result = await self.search(query)
        return result.agents
    
    async def search_by_roi(self, min_roi: float = 0.0) -> List[AgentMetadata]:
        """
        Recherche par ROI minimum.
        """
        query = SearchQuery()
        query.add_filter("avg_roi", SearchOperator.GREATER_THAN, min_roi)
        query.sort_by = "avg_roi"
        query.sort_order = "desc"
        
        result = await self.search(query)
        return result.agents
    
    # ====== Marketplace Features ======
    
    async def get_featured_agents(self, limit: int = 10) -> List[AgentMetadata]:
        """
        Récupère les agents en vedette.
        """
        query = SearchQuery()
        query.add_filter("featured", SearchOperator.EQUALS, True)
        query.sort_by = "download_count"
        query.sort_order = "desc"
        query.page_size = limit
        
        result = await self.search(query)
        return result.agents
    
    async def get_popular_agents(self, category: Optional[AgentCategory] = None) -> List[AgentMetadata]:
        """
        Récupère les agents populaires.
        """
        query = SearchQuery()
        
        if category:
            query.add_filter("category", SearchOperator.EQUALS, category)
        
        query.sort_by = "download_count"
        query.sort_order = "desc"
        query.page_size = 20
        
        result = await self.search(query)
        return result.agents
    
    async def get_new_agents(self, days: int = 30) -> List[AgentMetadata]:
        """
        Récupère les nouveaux agents.
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        # Cette recherche nécessiterait un index sur created_at
        # Pour l'instant, on fait une recherche simple
        all_agents = await self._get_all_metadata()
        
        new_agents = [
            agent for agent in all_agents
            if agent.created_at >= cutoff_date
        ]
        
        new_agents.sort(key=lambda x: x.created_at, reverse=True)
        return new_agents[:20]
    
    # ====== Rating & Review System ======
    
    async def add_rating(
        self,
        agent_id: AgentID,
        version: str,
        user_id: str,
        rating: int,
        review: Optional[str] = None,
        pros: Optional[List[str]] = None,
        cons: Optional[List[str]] = None
    ) -> AgentRating:
        """
        Ajoute une note et une review.
        """
        try:
            # Vérifier que l'agent existe
            metadata = await self.get_metadata(agent_id, version)
            if not metadata:
                raise AgentNotFoundError(agent_id)
            
            # Créer la review
            agent_rating = AgentRating(
                agent_id=agent_id,
                version=version,
                user_id=user_id,
                rating=rating,
                review=review,
                pros=pros or [],
                cons=cons or []
            )
            
            # Ajouter aux ratings
            cache_key = (agent_id, version)
            self._ratings[cache_key].append(agent_rating)
            
            # Mettre à jour la moyenne dans les métadonnées
            ratings = self._ratings[cache_key]
            metadata.rating_average = sum(r.rating for r in ratings) / len(ratings)
            metadata.rating_count = len(ratings)
            
            # Sauvegarder les métadonnées mises à jour
            for plugin in self._plugins:
                await plugin.save_metadata(metadata)
            
            # Mettre à jour le cache
            self._metadata_cache[cache_key] = metadata
            
            self.logger.info(
                "rating_added",
                agent_id=agent_id,
                version=version,
                user_id=user_id,
                rating=rating
            )
            
            return agent_rating
            
        except Exception as e:
            self.logger.error(
                "rating_addition_failed",
                agent_id=agent_id,
                version=version,
                user_id=user_id,
                error=str(e)
            )
            raise RegistryError(f"Échec de l'ajout de la note: {e}") from e
    
    async def get_ratings(
        self,
        agent_id: AgentID,
        version: str,
        limit: int = 50
    ) -> List[AgentRating]:
        """
        Récupère les ratings d'un agent.
        """
        cache_key = (agent_id, version)
        
        if cache_key in self._ratings:
            ratings = self._ratings[cache_key]
            return sorted(ratings, key=lambda x: x.helpful_count, reverse=True)[:limit]
        
        return []
    
    async def mark_rating_helpful(
        self,
        agent_id: AgentID,
        version: str,
        rating_id: UUID
    ) -> bool:
        """
        Marque une review comme utile.
        """
        cache_key = (agent_id, version)
        
        if cache_key in self._ratings:
            for i, rating in enumerate(self._ratings[cache_key]):
                if rating.rating_id == rating_id:
                    self._ratings[cache_key][i] = rating.mark_helpful()
                    return True
        
        return False
    
    # ====== Usage Statistics ======
    
    async def get_usage_stats(self, agent_id: AgentID, version: str = "latest") -> UsageStatistics:
        """
        Récupère les statistiques d'utilisation.
        """
        if version == "latest":
            version = await self._get_latest_version(agent_id)
        
        cache_key = (agent_id, version)
        
        if cache_key in self._usage_stats:
            return self._usage_stats[cache_key]
        
        # Retourner des statistiques vides
        return UsageStatistics(agent_id=agent_id, version=version)
    
    async def record_usage(
        self,
        agent_id: AgentID,
        version: str,
        user_id: str,
        tenant_id: str,
        success: bool,
        execution_time: float
    ) -> None:
        """
        Enregistre une utilisation d'agent.
        """
        if version == "latest":
            version = await self._get_latest_version(agent_id)
        
        cache_key = (agent_id, version)
        
        if cache_key not in self._usage_stats:
            self._usage_stats[cache_key] = UsageStatistics(
                agent_id=agent_id,
                version=version
            )
        
        self._usage_stats[cache_key].record_execution(
            user_id=user_id,
            tenant_id=tenant_id,
            success=success,
            execution_time=execution_time
        )
        
        # Mettre à jour le compteur de téléchargements dans les métadonnées
        metadata = await self.get_metadata(agent_id, version)
        if metadata:
            metadata.download_count += 1
            metadata.last_updated = datetime.utcnow()
            
            # Sauvegarder les métadonnées mises à jour
            for plugin in self._plugins:
                await plugin.save_metadata(metadata)
            
            # Mettre à jour le cache
            self._metadata_cache[cache_key] = metadata
    
    # ====== Compatibility Checking ======
    
    async def check_compatibility(
        self,
        agent_id: AgentID,
        version: str,
        target_platform: Optional[str] = None,
        python_version: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Vérifie la compatibilité d'un agent.
        """
        metadata = await self.get_metadata(agent_id, version)
        if not metadata:
            raise AgentNotFoundError(agent_id)
        
        result = {
            "agent": f"{agent_id}:{version}",
            "checks": []
        }
        
        # Vérifier la version Python
        if python_version:
            compatible = self._check_python_compatibility(metadata.python_version, python_version)
            result["checks"].append({
                "check": "python_version",
                "required": metadata.python_version,
                "actual": python_version,
                "compatible": compatible
            })
        
        # Vérifier la plateforme
        if target_platform:
            compatible = target_platform in metadata.supported_platforms
            result["checks"].append({
                "check": "platform",
                "supported": metadata.supported_platforms,
                "actual": target_platform,
                "compatible": compatible
            })
        
        # Vérifier les dépendances
        if metadata.dependencies:
            deps_compatibility = []
            all_compatible = True
            
            for dep in metadata.dependencies:
                dep_metadata = await self.get_metadata(dep.name, dep.version)
                if dep_metadata:
                    compatible = True
                else:
                    compatible = dep.optional  # Les dépendances optionnelles sont OK si manquantes
                    all_compatible = all_compatible and dep.optional
                
                deps_compatibility.append({
                    "dependency": str(dep),
                    "required": not dep.optional,
                    "available": dep_metadata is not None,
                    "compatible": compatible
                })
            
            result["checks"].append({
                "check": "dependencies",
                "dependencies": deps_compatibility,
                "all_compatible": all_compatible
            })
        
        # Calculer le score global
        compatible_checks = [c.get("compatible", False) for c in result["checks"]]
        all_compatible = all(compatible_checks) if compatible_checks else True
        
        result["overall_compatible"] = all_compatible
        result["compatibility_score"] = (
            sum(1 for c in compatible_checks if c) / len(compatible_checks) * 100
            if compatible_checks else 100
        )
        
        return result
    
    # ====== Import/Export ======
    
    async def export_agent(
        self,
        agent_id: AgentID,
        version: str,
        include_dependencies: bool = False
    ) -> Dict[str, Any]:
        """
        Exporte un agent et ses métadonnées.
        """
        # Charger l'agent et ses métadonnées
        agent = await self.get(agent_id, version, resolve_dependencies=False)
        metadata = await self.get_metadata(agent_id, version)
        
        if not metadata:
            raise AgentNotFoundError(agent_id)
        
        export_data = {
            "format_version": "2.0.0",
            "export_date": datetime.utcnow().isoformat(),
            "agent": {
                "id": agent_id,
                "version": version,
                "serialized": self._serialize_agent(agent).decode('latin-1')  # Pour JSON
            },
            "metadata": metadata.to_dict()
        }
        
        # Inclure les dépendances si demandé
        if include_dependencies and metadata.dependencies:
            export_data["dependencies"] = []
            
            for dep in metadata.dependencies:
                try:
                    dep_export = await self.export_agent(dep.name, dep.version, include_dependencies=False)
                    export_data["dependencies"].append(dep_export)
                except Exception as e:
                    self.logger.warning(
                        "dependency_export_failed",
                        dependency=str(dep),
                        error=str(e)
                    )
        
        return export_data
    
    async def import_agent(
        self,
        export_data: Dict[str, Any],
        overwrite: bool = False
    ) -> AgentID:
        """
        Importe un agent depuis des données d'export.
        """
        try:
            # Vérifier le format
            if export_data.get("format_version") != "2.0.0":
                raise RegistryError("Format d'export non supporté")
            
            agent_data = export_data["agent"]
            metadata_dict = export_data["metadata"]
            
            agent_id = agent_data["id"]
            version = agent_data["version"]
            
            # Vérifier si l'agent existe déjà
            existing = await self.get_metadata(agent_id, version)
            if existing and not overwrite:
                raise RegistryError(
                    f"Agent {agent_id}:{version} existe déjà. Utilisez overwrite=True pour écraser."
                )
            
            # Désérialiser l'agent
            serialized = agent_data["serialized"].encode('latin-1')
            agent = self._deserialize_agent(serialized)
            
            # Créer les métadonnées
            metadata = AgentMetadata.from_dict(metadata_dict)
            
            # Importer les dépendances si présentes
            if "dependencies" in export_data:
                for dep_export in export_data["dependencies"]:
                    await self.import_agent(dep_export, overwrite=overwrite)
            
            # Enregistrer l'agent
            return await self.register(agent, metadata)
            
        except Exception as e:
            self.logger.error(
                "agent_import_failed",
                error=str(e)
            )
            raise RegistryError(f"Échec de l'import: {e}") from e
    
    # ====== Méthodes utilitaires ======
    
    def _generate_agent_id(self, agent: MicroAgent) -> AgentID:
        """Génère un ID unique pour l'agent"""
        agent_name = getattr(agent, 'name', agent.__class__.__name__)
        namespace = getattr(agent, 'namespace', self.default_namespace)
        
        # Générer un hash pour éviter les collisions
        agent_hash = hashlib.md5(
            f"{namespace}:{agent_name}:{time.time()}".encode()
        ).hexdigest()[:8]
        
        return f"{namespace}/{agent_name}_{agent_hash}"
    
    def _create_default_metadata(
        self,
        agent: MicroAgent,
        agent_id: AgentID
    ) -> AgentMetadata:
        """Crée des métadonnées par défaut pour un agent"""
        return AgentMetadata(
            agent_id=agent_id,
            name=getattr(agent, 'name', agent.__class__.__name__),
            namespace=getattr(agent, 'namespace', self.default_namespace),
            version=getattr(agent, 'version', '1.0.0'),
            display_name=getattr(agent, 'display_name', ''),
            description=getattr(agent, 'description', ''),
            capabilities=getattr(agent, 'capabilities', AgentCapabilities()),
            dependencies=getattr(agent, 'dependencies', []),
            author=getattr(agent, 'author', ''),
            tags=getattr(agent, 'tags', [])
        )
    
    def _validate_metadata(self, metadata: AgentMetadata) -> None:
        """Valide les métadonnées d'un agent"""
        if not metadata.name:
            raise AgentValidationError("Le nom de l'agent est requis")
        
        if not metadata.validate_version():
            raise AgentValidationError(f"Version invalide: {metadata.version}")
        
        if not metadata.is_compatible:
            raise AgentValidationError(
                f"Agent incompatible avec Python {metadata.python_version}"
            )
    
    async def _validate_dependencies(self, dependencies: List[AgentDependency]) -> None:
        """Valide les dépendances d'un agent"""
        for dep in dependencies:
            if not dep.optional:
                # Vérifier que la dépendance existe
                dep_metadata = await self.get_metadata(dep.name, dep.version)
                if not dep_metadata:
                    raise DependencyError(
                        dependency=dep.name,
                        reason=f"Dépendance requise non trouvée: {dep.name}=={dep.version}"
                    )
    
    async def _validate_update_compatibility(
        self,
        current: AgentMetadata,
        new: AgentMetadata
    ) -> None:
        """Valide la compatibilité d'une mise à jour"""
        # Vérifier les breaking changes
        breaking_changes = []
        
        # Vérifier les changements de dépendances
        current_deps = {str(d) for d in current.dependencies}
        new_deps = {str(d) for d in new.dependencies}
        
        removed_deps = current_deps - new_deps
        if removed_deps:
            breaking_changes.append(f"Dépendances supprimées: {', '.join(removed_deps)}")
        
        # Vérifier les changements d'API
        if current.capabilities != new.capabilities:
            breaking_changes.append("Changements dans les capacités")
        
        if breaking_changes:
            raise RegistryError(
                f"Breaking changes détectés: {', '.join(breaking_changes)}"
            )
    
    async def _get_latest_version(self, agent_id: AgentID) -> str:
        """Retourne la dernière version d'un agent"""
        # Récupérer toutes les versions
        versions = await self._get_all_versions(agent_id)
        if not versions:
            raise AgentNotFoundError(agent_id)
        
        # Trouver la version la plus récente (semver)
        latest = max(
            versions,
            key=lambda v: semver.VersionInfo.parse(v) if self._is_semver(v) else (0, 0, 0)
        )
        
        return latest
    
    async def _get_all_versions(self, agent_id: AgentID) -> List[str]:
        """Retourne toutes les versions d'un agent"""
        # Utiliser le plugin actif
        agents = await self._active_plugin.list_agents()
        
        versions = [
            version for aid, version in agents
            if aid == agent_id
        ]
        
        return versions
    
    async def _get_all_metadata(self) -> List[AgentMetadata]:
        """Récupère toutes les métadonnées"""
        agents = await self._active_plugin.list_agents()
        
        all_metadata = []
        for agent_id, version in agents:
            metadata = await self.get_metadata(agent_id, version)
            if metadata:
                all_metadata.append(metadata)
        
        return all_metadata
    
    async def _load_dependencies(self, dependencies: List[AgentDependency]) -> None:
        """Charge toutes les dépendances"""
        for dep in dependencies:
            if not dep.optional:
                try:
                    await self.get(dep.name, dep.version, resolve_dependencies=True)
                except Exception as e:
                    self.logger.warning(
                        "dependency_load_failed",
                        dependency=str(dep),
                        error=str(e)
                    )
    
    def _update_dependency_graph(self, agent_id: AgentID, dependencies: List[AgentDependency]) -> None:
        """Met à jour le graphe de dépendances"""
        for dep in dependencies:
            if not dep.optional:
                self._dependencies[agent_id].add(dep.name)
    
    def _serialize_agent(self, agent: MicroAgent) -> bytes:
        """Sérialise un agent"""
        # Utiliser pickle pour la sérialisation
        import pickle
        return pickle.dumps(agent)
    
    def _deserialize_agent(self, data: bytes) -> MicroAgent:
        """Désérialise un agent"""
        import pickle
        return pickle.loads(data)
    
    def _check_python_compatibility(self, required: str, actual: str) -> bool:
        """Vérifie la compatibilité des versions Python"""
        try:
            actual_version = semver.VersionInfo.parse(actual)
            
            if required.startswith(">="):
                required_version = semver.VersionInfo.parse(required[2:])
                return actual_version >= required_version
            elif required.startswith(">"):
                required_version = semver.VersionInfo.parse(required[1:])
                return actual_version > required_version
            elif required.startswith("<="):
                required_version = semver.VersionInfo.parse(required[2:])
                return actual_version <= required_version
            elif required.startswith("<"):
                required_version = semver.VersionInfo.parse(required[1:])
                return actual_version < required_version
            elif required.startswith("=="):
                required_version = semver.VersionInfo.parse(required[2:])
                return actual_version == required_version
            
            return True
        except ValueError:
            return False
    
    def _is_semver(self, version: str) -> bool:
        """Vérifie si une chaîne est une version semver valide"""
        try:
            semver.VersionInfo.parse(version)
            return True
        except ValueError:
            return False
    
    async def close(self) -> None:
        """Ferme le registre et tous les plugins"""
        self.logger.info("closing_agent_registry")
        
        for plugin in self._plugins:
            try:
                await plugin.close()
            except Exception as e:
                self.logger.error(
                    "plugin_close_failed",
                    plugin=plugin.__class__.__name__,
                    error=str(e)
                )
        
        self._plugins.clear()
        self._active_plugin = None
        
        self.logger.info("agent_registry_closed")


# ====== Export ======

__all__ = [
    # Enums
    "AgentStatus",
    "AgentCategory",
    "LicenseType",
    "StorageBackend",
    "SearchOperator",
    
    # Dataclasses
    "AgentRating",
    "UsageStatistics",
    "AgentMetadata",
    "SearchFilter",
    "SearchQuery",
    "SearchResult",
    "PluginConfig",
    
    # Interfaces
    "StoragePlugin",
    
    # Implémentations
    "MemoryStoragePlugin",
    "FilesystemStoragePlugin",
    
    # Registre principal
    "AgentRegistry",
    
    # Types
    "AgentID",
]