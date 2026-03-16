"""
Module: Agent Context Management
Description: Classe de contexte immuable pour les agents avec toutes les métadonnées nécessaires
Version: 2.0.0
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import pickle
import zlib
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from typing import (
    Any,
    ClassVar,
    Dict,
    FrozenSet,
    List,
    Optional,
    Tuple,
    Union,
    cast,
    get_type_hints,
)
from uuid import UUID, uuid4

import msgpack
import orjson
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from typing_extensions import Self


# ====== Types de base ======

class Environment(str, Enum):
    """Environnements d'exécution"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    CANARY = "canary"
    TESTING = "testing"


class ComplianceStandard(str, Enum):
    """Standards de compliance supportés"""
    GDPR = "gdpr"
    HIPAA = "hipaa"
    SOC2 = "soc2"
    ISO27001 = "iso27001"
    PCI_DSS = "pci_dss"
    FEDRAMP = "fedramp"


class BusinessObjective(str, Enum):
    """Objectifs business standardisés"""
    COST_OPTIMIZATION = "cost_optimization"
    PERFORMANCE = "performance"
    SECURITY = "security"
    RELIABILITY = "reliability"
    COMPLIANCE = "compliance"
    INNOVATION = "innovation"
    TIME_TO_MARKET = "time_to_market"


class BudgetCategory(str, Enum):
    """Catégories de budget"""
    INFRASTRUCTURE = "infrastructure"
    LICENSING = "licensing"
    PERSONNEL = "personnel"
    TRAINING = "training"
    MAINTENANCE = "maintenance"
    INNOVATION = "innovation"
    CONTINGENCY = "contingency"


class MetricType(str, Enum):
    """Types de métriques"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    RATE = "rate"


# ====== Modèles de base ======

@dataclass(frozen=True)
class Metric:
    """Métrique structurée avec metadata"""
    name: str
    value: Union[int, float]
    type: MetricType = MetricType.GAUGE
    unit: str = ""
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    labels: Dict[str, str] = field(default_factory=dict)
    description: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire sérialisable"""
        return {
            "name": self.name,
            "value": self.value,
            "type": self.type.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
            "labels": self.labels,
            "description": self.description
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Metric:
        """Crée une métrique depuis un dictionnaire"""
        return cls(
            name=data["name"],
            value=data["value"],
            type=MetricType(data["type"]),
            unit=data.get("unit", ""),
            timestamp=datetime.datetime.fromisoformat(data["timestamp"]),
            labels=data.get("labels", {}),
            description=data.get("description")
        )


@dataclass(frozen=True)
class PerformanceBaseline:
    """Ligne de base de performance"""
    metric_name: str
    current_value: float
    baseline_value: float
    threshold_min: float
    threshold_max: float
    unit: str = ""
    calculated_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    
    @property
    def deviation_percentage(self) -> float:
        """Retourne l'écart en pourcentage par rapport à la baseline"""
        if self.baseline_value == 0:
            return 0.0
        return ((self.current_value - self.baseline_value) / self.baseline_value) * 100
    
    @property
    def is_within_threshold(self) -> bool:
        """Vérifie si la valeur est dans les seuils"""
        return self.threshold_min <= self.current_value <= self.threshold_max
    
    @property
    def status(self) -> str:
        """Retourne le statut de performance"""
        if not self.is_within_threshold:
            return "critical"
        elif abs(self.deviation_percentage) > 10:
            return "warning"
        return "normal"


@dataclass(frozen=True)
class BudgetConstraint:
    """Contrainte budgétaire"""
    category: BudgetCategory
    allocated: float
    spent: float = 0.0
    currency: str = "USD"
    period_start: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    period_end: Optional[datetime.datetime] = None
    alerts_at: Tuple[float, ...] = (0.5, 0.75, 0.9, 0.95)  # Pourcentages pour alertes
    
    @property
    def remaining(self) -> float:
        """Budget restant"""
        return max(0.0, self.allocated - self.spent)
    
    @property
    def utilization_percentage(self) -> float:
        """Pourcentage d'utilisation"""
        if self.allocated == 0:
            return 0.0
        return (self.spent / self.allocated) * 100
    
    @property
    def alert_levels(self) -> List[Dict[str, Any]]:
        """Niveaux d'alerte atteints"""
        alerts = []
        for threshold in sorted(self.alerts_at):
            if self.utilization_percentage >= threshold * 100:
                alerts.append({
                    "threshold": threshold,
                    "utilization": self.utilization_percentage,
                    "message": f"Budget {self.category.value} à {self.utilization_percentage:.1f}%"
                })
        return alerts
    
    def can_spend(self, amount: float) -> bool:
        """Vérifie si le budget peut supporter la dépense"""
        return self.spent + amount <= self.allocated


@dataclass(frozen=True)
class ROITarget:
    """Cible de ROI"""
    objective: BusinessObjective
    target_roi: float  # Pourcentage
    timeframe_days: int
    current_roi: float = 0.0
    calculated_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    confidence_score: float = 0.0  # Score de confiance 0-1
    
    @property
    def progress_percentage(self) -> float:
        """Progression vers la cible"""
        if self.target_roi == 0:
            return 0.0
        return (self.current_roi / self.target_roi) * 100
    
    @property
    def days_remaining(self) -> int:
        """Jours restants"""
        delta = datetime.timedelta(days=self.timeframe_days)
        end_date = self.calculated_at + delta
        remaining = (end_date - datetime.datetime.utcnow()).days
        return max(0, remaining)
    
    @property
    def roi_gap(self) -> float:
        """Écart de ROI à combler"""
        return self.target_roi - self.current_roi


@dataclass(frozen=True)
class ComplianceRequirement:
    """Exigence de compliance"""
    standard: ComplianceStandard
    requirement_id: str
    requirement: str
    status: str = "pending"  # pending, implemented, validated, certified
    implemented_at: Optional[datetime.datetime] = None
    validated_at: Optional[datetime.datetime] = None
    evidence_refs: List[str] = field(default_factory=list)
    
    @property
    def is_compliant(self) -> bool:
        """Vérifie si l'exigence est compliant"""
        return self.status in ["validated", "certified"]


@dataclass(frozen=True)
class FeatureFlag:
    """Feature flag avec conditions"""
    name: str
    enabled: bool = False
    rollout_percentage: float = 0.0  # 0-100
    conditions: Dict[str, Any] = field(default_factory=dict)
    description: Optional[str] = None
    created_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    
    def is_enabled_for(self, context: Dict[str, Any]) -> bool:
        """Vérifie si le flag est activé pour le contexte donné"""
        if not self.enabled:
            return False
        
        if self.rollout_percentage < 100:
            # Vérifier le rollout basé sur le hash du client
            client_hash = hashlib.md5(
                context.get("client_id", "").encode()
            ).hexdigest()
            client_value = int(client_hash[:8], 16) / 0xFFFFFFFF
            if client_value * 100 > self.rollout_percentage:
                return False
        
        # Vérifier les conditions
        for key, expected_value in self.conditions.items():
            actual_value = context.get(key)
            if actual_value != expected_value:
                return False
        
        return True


@dataclass(frozen=True)
class ClientConfiguration:
    """Configuration spécifique au client"""
    client_id: str
    environment: Environment
    settings: Dict[str, Any] = field(default_factory=dict)
    limits: Dict[str, Any] = field(default_factory=dict)
    preferences: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Récupère un setting avec valeur par défaut"""
        return self.settings.get(key, default)
    
    def get_limit(self, key: str, default: Any = None) -> Any:
        """Récupère une limite avec valeur par défaut"""
        return self.limits.get(key, default)
    
    def validate_limit(self, key: str, value: Any) -> bool:
        """Valide une valeur par rapport à une limite"""
        limit = self.get_limit(key)
        if limit is None:
            return True
        
        if isinstance(limit, dict):
            min_val = limit.get("min")
            max_val = limit.get("max")
            
            if min_val is not None and value < min_val:
                return False
            if max_val is not None and value > max_val:
                return False
            
            return True
        
        return value <= limit


@dataclass(frozen=True)
class ExecutionHistoryEntry:
    """Entrée d'historique d'exécution"""
    execution_id: str
    agent_name: str
    started_at: datetime.datetime
    completed_at: Optional[datetime.datetime] = None
    success: Optional[bool] = None
    duration_ms: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    
    @property
    def is_completed(self) -> bool:
        """Vérifie si l'exécution est terminée"""
        return self.completed_at is not None
    
    @property
    def is_successful(self) -> bool:
        """Vérifie si l'exécution a réussi"""
        return self.success is True


# ====== Modèle Pydantic principal ======

class AgentContext(BaseModel):
    """
    Contexte immuable pour les agents.
    Contient toutes les métadonnées nécessaires pour l'exécution.
    """
    
    # === Identifiants ===
    context_id: UUID = Field(default_factory=uuid4)
    client_id: str = Field(..., min_length=1, max_length=100)
    tenant_id: str = Field(default="default")
    environment: Environment = Field(default=Environment.PRODUCTION)
    correlation_id: str = Field(default_factory=lambda: f"corr_{uuid4().hex[:8]}")
    request_id: str = Field(default_factory=lambda: f"req_{uuid4().hex[:8]}")
    
    # === Métriques ===
    metrics: Dict[str, Metric] = Field(default_factory=dict)
    performance_baselines: Dict[str, PerformanceBaseline] = Field(default_factory=dict)
    
    # === Configuration ===
    client_config: ClientConfiguration = Field(...)
    feature_flags: Dict[str, FeatureFlag] = Field(default_factory=dict)
    
    # === Contraintes business ===
    budget_constraints: Dict[BudgetCategory, BudgetConstraint] = Field(default_factory=dict)
    roi_targets: Dict[BusinessObjective, ROITarget] = Field(default_factory=dict)
    
    # === Compliance ===
    compliance_requirements: Dict[str, ComplianceRequirement] = Field(default_factory=dict)
    
    # === Historique ===
    execution_history: List[ExecutionHistoryEntry] = Field(default_factory=list)
    
    # === Secrets (chiffrés) ===
    _encrypted_secrets: Dict[str, str] = Field(default_factory=dict, alias="encrypted_secrets")
    _secret_keys: FrozenSet[str] = Field(default_factory=frozenset, alias="secret_keys")
    
    # === Métadonnées ===
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    version: int = Field(default=1)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # === Config Pydantic ===
    model_config = ConfigDict(
        frozen=True,  # Immutabilité
        from_attributes=True,
        json_encoders={
            datetime.datetime: lambda dt: dt.isoformat(),
            UUID: lambda u: str(u),
        },
        arbitrary_types_allowed=True,
        protected_namespaces=(),
    )
    
    # === Validators ===
    
    @field_validator('client_id', 'tenant_id')
    @classmethod
    def validate_ids(cls, v: str) -> str:
        """Valide les IDs"""
        if not v or not v.strip():
            raise ValueError("ID ne peut pas être vide")
        return v.strip().lower()
    
    @field_validator('metrics')
    @classmethod
    def validate_metrics(cls, v: Dict[str, Metric]) -> Dict[str, Metric]:
        """Valide les métriques"""
        for name, metric in v.items():
            if name != metric.name:
                raise ValueError(f"Nom de métrique incohérent: {name} != {metric.name}")
        return v
    
    @field_validator('performance_baselines')
    @classmethod
    def validate_baselines(cls, v: Dict[str, PerformanceBaseline]) -> Dict[str, PerformanceBaseline]:
        """Valide les baselines"""
        for name, baseline in v.items():
            if name != baseline.metric_name:
                raise ValueError(f"Nom de baseline incohérent: {name} != {baseline.metric_name}")
        return v
    
    @model_validator(mode='after')
    def validate_business_constraints(self) -> Self:
        """Valide les contraintes business"""
        # Vérifier que tous les budgets ont une catégorie unique
        categories = set()
        for constraint in self.budget_constraints.values():
            if constraint.category in categories:
                raise ValueError(f"Catégorie de budget dupliquée: {constraint.category}")
            categories.add(constraint.category)
        
        # Vérifier les ROI targets
        for objective, target in self.roi_targets.items():
            if objective != target.objective:
                raise ValueError(f"Objectif de ROI incohérent: {objective} != {target.objective}")
        
        return self
    
    # === Propriétés calculées ===
    
    @property
    def total_budget_allocated(self) -> float:
        """Budget total alloué"""
        return sum(c.allocated for c in self.budget_constraints.values())
    
    @property
    def total_budget_spent(self) -> float:
        """Budget total dépensé"""
        return sum(c.spent for c in self.budget_constraints.values())
    
    @property
    def overall_budget_utilization(self) -> float:
        """Utilisation globale du budget"""
        if self.total_budget_allocated == 0:
            return 0.0
        return (self.total_budget_spent / self.total_budget_allocated) * 100
    
    @property
    def average_roi_progress(self) -> float:
        """Progression moyenne des ROI targets"""
        if not self.roi_targets:
            return 0.0
        return sum(t.progress_percentage for t in self.roi_targets.values()) / len(self.roi_targets)
    
    @property
    def compliance_score(self) -> float:
        """Score de compliance (0-100)"""
        if not self.compliance_requirements:
            return 100.0
        
        compliant = sum(1 for r in self.compliance_requirements.values() if r.is_compliant)
        return (compliant / len(self.compliance_requirements)) * 100
    
    @property
    def performance_health_score(self) -> float:
        """Score de santé des performances (0-100)"""
        if not self.performance_baselines:
            return 100.0
        
        within_threshold = sum(
            1 for b in self.performance_baselines.values() 
            if b.is_within_threshold
        )
        return (within_threshold / len(self.performance_baselines)) * 100
    
    @property
    def business_health_score(self) -> float:
        """Score de santé business global (0-100)"""
        scores = [
            (100 - min(self.overall_budget_utilization, 100)) * 0.3,  # Budget
            self.average_roi_progress * 0.3,  # ROI
            self.compliance_score * 0.2,  # Compliance
            self.performance_health_score * 0.2  # Performance
        ]
        return sum(scores)
    
    # === Méthodes de manipulation (retournent de nouvelles instances) ===
    
    def with_metric(self, metric: Metric) -> AgentContext:
        """Ajoute une métrique (retourne nouvelle instance)"""
        new_metrics = dict(self.metrics)
        new_metrics[metric.name] = metric
        
        return self.model_copy(
            update={
                "metrics": new_metrics,
                "updated_at": datetime.datetime.utcnow()
            }
        )
    
    def with_performance_baseline(self, baseline: PerformanceBaseline) -> AgentContext:
        """Ajoute une baseline de performance"""
        new_baselines = dict(self.performance_baselines)
        new_baselines[baseline.metric_name] = baseline
        
        return self.model_copy(
            update={
                "performance_baselines": new_baselines,
                "updated_at": datetime.datetime.utcnow()
            }
        )
    
    def with_budget_spent(self, category: BudgetCategory, amount: float) -> AgentContext:
        """Enregistre une dépense (retourne nouvelle instance)"""
        if category not in self.budget_constraints:
            raise ValueError(f"Catégorie de budget non trouvée: {category}")
        
        constraint = self.budget_constraints[category]
        if not constraint.can_spend(amount):
            raise ValueError(
                f"Dépense dépasserait le budget pour {category}: "
                f"{constraint.spent + amount} > {constraint.allocated}"
            )
        
        new_constraint = BudgetConstraint(
            category=constraint.category,
            allocated=constraint.allocated,
            spent=constraint.spent + amount,
            currency=constraint.currency,
            period_start=constraint.period_start,
            period_end=constraint.period_end,
            alerts_at=constraint.alerts_at
        )
        
        new_constraints = dict(self.budget_constraints)
        new_constraints[category] = new_constraint
        
        return self.model_copy(
            update={
                "budget_constraints": new_constraints,
                "updated_at": datetime.datetime.utcnow()
            }
        )
    
    def with_execution_history(self, entry: ExecutionHistoryEntry) -> AgentContext:
        """Ajoute une entrée d'historique (retourne nouvelle instance)"""
        # Garder seulement les 1000 dernières entrées
        new_history = [entry] + self.execution_history[:999]
        
        return self.model_copy(
            update={
                "execution_history": new_history,
                "updated_at": datetime.datetime.utcnow()
            }
        )
    
    def with_feature_flag(self, flag: FeatureFlag) -> AgentContext:
        """Ajoute ou met à jour un feature flag"""
        new_flags = dict(self.feature_flags)
        new_flags[flag.name] = flag
        
        return self.model_copy(
            update={
                "feature_flags": new_flags,
                "updated_at": datetime.datetime.utcnow()
            }
        )
    
    def is_feature_enabled(self, feature_name: str, context: Optional[Dict[str, Any]] = None) -> bool:
        """Vérifie si une feature est activée"""
        flag = self.feature_flags.get(feature_name)
        if not flag:
            return False
        
        eval_context = context or {
            "client_id": self.client_id,
            "environment": self.environment.value,
            "tenant_id": self.tenant_id
        }
        
        return flag.is_enabled_for(eval_context)
    
    # === Gestion des secrets ===
    
    def with_secret(self, key: str, value: str, encryption_key: Optional[str] = None) -> AgentContext:
        """
        Ajoute un secret (chiffré).
        
        Args:
            key: Clé du secret
            value: Valeur du secret (en clair)
            encryption_key: Clé de chiffrement (optionnelle, utilise le hash du contexte par défaut)
        """
        if encryption_key is None:
            # Utiliser le hash du contexte comme clé de chiffrement
            context_hash = hashlib.sha256(
                f"{self.client_id}:{self.tenant_id}:{self.environment}".encode()
            ).hexdigest()
            encryption_key = context_hash[:32]  # 32 caractères pour AES-256
        
        # Chiffrement simple (en production, utiliser une lib de chiffrement)
        encrypted = base64.b64encode(
            self._xor_encrypt(value.encode(), encryption_key.encode())
        ).decode()
        
        new_secrets = dict(self._encrypted_secrets)
        new_secrets[key] = encrypted
        
        new_keys = set(self._secret_keys)
        new_keys.add(key)
        
        return self.model_copy(
            update={
                "_encrypted_secrets": new_secrets,
                "_secret_keys": frozenset(new_keys),
                "updated_at": datetime.datetime.utcnow()
            }
        )
    
    def get_secret(self, key: str, decryption_key: Optional[str] = None) -> Optional[str]:
        """
        Récupère un secret (déchiffré).
        
        Args:
            key: Clé du secret
            decryption_key: Clé de déchiffrement
            
        Returns:
            Valeur du secret ou None si non trouvé
        """
        if key not in self._encrypted_secrets:
            return None
        
        if decryption_key is None:
            # Utiliser le hash du contexte comme clé de déchiffrement
            context_hash = hashlib.sha256(
                f"{self.client_id}:{self.tenant_id}:{self.environment}".encode()
            ).hexdigest()
            decryption_key = context_hash[:32]
        
        encrypted = base64.b64decode(self._encrypted_secrets[key])
        
        try:
            decrypted = self._xor_encrypt(encrypted, decryption_key.encode())
            return decrypted.decode()
        except Exception:
            return None
    
    def has_secret(self, key: str) -> bool:
        """Vérifie si un secret existe"""
        return key in self._encrypted_secrets
    
    @staticmethod
    def _xor_encrypt(data: bytes, key: bytes) -> bytes:
        """Chiffrement XOR simple (à remplacer par AES en production)"""
        return bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])
    
    # === Sérialisation optimisée ===
    
    def serialize(self, format: str = "msgpack", compress: bool = True) -> bytes:
        """
        Sérialise le contexte.
        
        Args:
            format: Format de sérialisation (json, msgpack, pickle)
            compress: Activer la compression
        
        Returns:
            Données sérialisées
        """
        # Convertir en dictionnaire
        data = self.model_dump(
            mode="json",
            exclude_none=True,
            exclude={"model_config"} if hasattr(self, "model_config") else set()
        )
        
        # Sérialiser selon le format
        if format == "json":
            serialized = orjson.dumps(data)
        elif format == "msgpack":
            serialized = msgpack.packb(data, use_bin_type=True)
        elif format == "pickle":
            serialized = pickle.dumps(data)
        else:
            raise ValueError(f"Format non supporté: {format}")
        
        # Compresser si demandé
        if compress:
            serialized = zlib.compress(serialized, level=9)
        
        return serialized
    
    @classmethod
    def deserialize(cls, data: bytes, format: str = "msgpack", compressed: bool = True) -> AgentContext:
        """
        Désérialise le contexte.
        
        Args:
            data: Données sérialisées
            format: Format de sérialisation
            compressed: Les données sont-elles compressées?
        
        Returns:
            Instance d'AgentContext
        """
        # Décompresser si nécessaire
        if compressed:
            try:
                data = zlib.decompress(data)
            except zlib.error:
                # Peut-être pas compressé
                pass
        
        # Désérialiser selon le format
        if format == "json":
            decoded = orjson.loads(data)
        elif format == "msgpack":
            decoded = msgpack.unpackb(data, raw=False)
        elif format == "pickle":
            decoded = pickle.loads(data)
        else:
            raise ValueError(f"Format non supporté: {format}")
        
        # Convertir les types spéciaux
        decoded = cls._restore_types(decoded)
        
        return cls(**decoded)
    
    @staticmethod
    def _restore_types(data: Dict[str, Any]) -> Dict[str, Any]:
        """Restaure les types spéciaux depuis la sérialisation"""
        # Restaurer les enums
        if "environment" in data:
            data["environment"] = Environment(data["environment"])
        
        # Restaurer les UUID
        for key in ["context_id"]:
            if key in data and isinstance(data[key], str):
                data[key] = UUID(data[key])
        
        # Restaurer les datetime
        datetime_fields = ["created_at", "updated_at"]
        for field in datetime_fields:
            if field in data and isinstance(data[field], str):
                data[field] = datetime.datetime.fromisoformat(data[field].replace('Z', '+00:00'))
        
        # Restaurer les métriques
        if "metrics" in data:
            restored_metrics = {}
            for name, metric_data in data["metrics"].items():
                restored_metrics[name] = Metric.from_dict(metric_data)
            data["metrics"] = restored_metrics
        
        # Restaurer les performance baselines
        if "performance_baselines" in data:
            restored_baselines = {}
            for name, baseline_data in data["performance_baselines"].items():
                restored_baselines[name] = PerformanceBaseline(
                    metric_name=baseline_data["metric_name"],
                    current_value=baseline_data["current_value"],
                    baseline_value=baseline_data["baseline_value"],
                    threshold_min=baseline_data["threshold_min"],
                    threshold_max=baseline_data["threshold_max"],
                    unit=baseline_data.get("unit", ""),
                    calculated_at=datetime.datetime.fromisoformat(
                        baseline_data["calculated_at"].replace('Z', '+00:00')
                    )
                )
            data["performance_baselines"] = restored_baselines
        
        return data
    
    # === Méthodes utilitaires ===
    
    def to_summary(self) -> Dict[str, Any]:
        """Retourne un résumé du contexte"""
        return {
            "context_id": str(self.context_id),
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "environment": self.environment.value,
            "business_health_score": round(self.business_health_score, 2),
            "budget_utilization": round(self.overall_budget_utilization, 2),
            "roi_progress": round(self.average_roi_progress, 2),
            "compliance_score": round(self.compliance_score, 2),
            "performance_score": round(self.performance_health_score, 2),
            "metrics_count": len(self.metrics),
            "baselines_count": len(self.performance_baselines),
            "executions_count": len(self.execution_history),
            "secrets_count": len(self._secret_keys),
            "feature_flags_enabled": sum(
                1 for f in self.feature_flags.values() 
                if f.is_enabled_for({"client_id": self.client_id})
            ),
            "created_at": self.created_at.isoformat(),
            "version": self.version
        }
    
    def validate_against_limits(self, operation: str, value: Any) -> bool:
        """Valide une opération par rapport aux limites du client"""
        return self.client_config.validate_limit(operation, value)
    
    def get_all_alerts(self) -> List[Dict[str, Any]]:
        """Récupère toutes les alertes du contexte"""
        alerts = []
        
        # Alertes de budget
        for constraint in self.budget_constraints.values():
            alerts.extend(constraint.alert_levels)
        
        # Alertes de performance
        for baseline in self.performance_baselines.values():
            if not baseline.is_within_threshold:
                alerts.append({
                    "type": "performance",
                    "metric": baseline.metric_name,
                    "current": baseline.current_value,
                    "baseline": baseline.baseline_value,
                    "deviation": f"{baseline.deviation_percentage:.1f}%",
                    "status": baseline.status
                })
        
        # Alertes de ROI
        for target in self.roi_targets.values():
            if target.days_remaining < 7 and target.progress_percentage < 80:
                alerts.append({
                    "type": "roi",
                    "objective": target.objective.value,
                    "progress": f"{target.progress_percentage:.1f}%",
                    "days_remaining": target.days_remaining,
                    "roi_gap": f"{target.roi_gap:.1f}%"
                })
        
        return alerts
    
    # === Factory methods ===
    
    @classmethod
    def create_for_client(
        cls,
        client_id: str,
        environment: Environment = Environment.PRODUCTION,
        tenant_id: str = "default",
        initial_budget: Optional[Dict[BudgetCategory, float]] = None,
        roi_targets: Optional[Dict[BusinessObjective, float]] = None,
        compliance_standards: Optional[List[ComplianceStandard]] = None
    ) -> AgentContext:
        """Crée un contexte initial pour un client"""
        
        # Configuration client par défaut
        client_config = ClientConfiguration(
            client_id=client_id,
            environment=environment,
            settings={
                "timezone": "UTC",
                "currency": "USD",
                "language": "en"
            },
            limits={
                "max_concurrent_agents": 100,
                "max_execution_time": 300,  # seconds
                "daily_executions": 10000
            }
        )
        
        # Budgets par défaut
        budget_constraints = {}
        if initial_budget:
            for category, amount in initial_budget.items():
                budget_constraints[category] = BudgetConstraint(
                    category=category,
                    allocated=amount,
                    spent=0.0,
                    currency="USD"
                )
        
        # ROI targets par défaut
        roi_targets_dict = {}
        if roi_targets:
            for objective, target in roi_targets.items():
                roi_targets_dict[objective] = ROITarget(
                    objective=objective,
                    target_roi=target,
                    timeframe_days=365
                )
        
        # Compliance requirements par défaut
        compliance_requirements = {}
        if compliance_standards:
            for standard in compliance_standards:
                req_id = f"{standard.value}_001"
                compliance_requirements[req_id] = ComplianceRequirement(
                    standard=standard,
                    requirement_id=req_id,
                    requirement=f"Implémenter les contrôles de base pour {standard.value}"
                )
        
        return cls(
            client_id=client_id,
            tenant_id=tenant_id,
            environment=environment,
            client_config=client_config,
            budget_constraints=budget_constraints,
            roi_targets=roi_targets_dict,
            compliance_requirements=compliance_requirements
        )


# ====== Gestionnaire de contexte ======

class ContextManager:
    """Gestionnaire pour les contextes d'agents"""
    
    def __init__(self, storage_backend: Optional[Any] = None):
        self.storage_backend = storage_backend
        self._context_cache: Dict[UUID, AgentContext] = {}
        self._client_contexts: Dict[str, List[UUID]] = {}
    
    def save_context(self, context: AgentContext) -> UUID:
        """Sauvegarde un contexte"""
        self._context_cache[context.context_id] = context
        
        # Mettre à jour l'index client
        client_key = f"{context.client_id}:{context.tenant_id}"
        if client_key not in self._client_contexts:
            self._client_contexts[client_key] = []
        
        if context.context_id not in self._client_contexts[client_key]:
            self._client_contexts[client_key].append(context.context_id)
        
        # Sauvegarder dans le backend si configuré
        if self.storage_backend:
            serialized = context.serialize(format="msgpack", compress=True)
            self.storage_backend.save(
                key=str(context.context_id),
                data=serialized,
                metadata=context.to_summary()
            )
        
        return context.context_id
    
    def load_context(self, context_id: UUID) -> Optional[AgentContext]:
        """Charge un contexte"""
        # Vérifier le cache
        if context_id in self._context_cache:
            return self._context_cache[context_id]
        
        # Charger depuis le backend
        if self.storage_backend:
            data = self.storage_backend.load(key=str(context_id))
            if data:
                context = AgentContext.deserialize(data, format="msgpack", compressed=True)
                self._context_cache[context_id] = context
                return context
        
        return None
    
    def get_client_contexts(
        self, 
        client_id: str, 
        tenant_id: str = "default",
        limit: int = 100
    ) -> List[AgentContext]:
        """Récupère les contextes d'un client"""
        client_key = f"{client_id}:{tenant_id}"
        context_ids = self._client_contexts.get(client_key, [])
        
        contexts = []
        for context_id in context_ids[:limit]:
            if context := self.load_context(context_id):
                contexts.append(context)
        
        return contexts
    
    def cleanup_old_contexts(self, max_age_hours: int = 24) -> int:
        """Nettoie les anciens contextes"""
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=max_age_hours)
        removed = 0
        
        for context_id, context in list(self._context_cache.items()):
            if context.updated_at < cutoff:
                del self._context_cache[context_id]
                removed += 1
                
                # Nettoyer l'index client
                client_key = f"{context.client_id}:{context.tenant_id}"
                if context_id in self._client_contexts.get(client_key, []):
                    self._client_contexts[client_key].remove(context_id)
        
        return removed


# ====== Export ======

__all__ = [
    # Enums
    "Environment",
    "ComplianceStandard",
    "BusinessObjective",
    "BudgetCategory",
    "MetricType",
    
    # Dataclasses
    "Metric",
    "PerformanceBaseline",
    "BudgetConstraint",
    "ROITarget",
    "ComplianceRequirement",
    "FeatureFlag",
    "ClientConfiguration",
    "ExecutionHistoryEntry",
    
    # Classe principale
    "AgentContext",
    
    # Gestionnaire
    "ContextManager",
]