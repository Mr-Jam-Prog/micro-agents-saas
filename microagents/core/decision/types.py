"""
Types de base pour le système décisionnel - Contrats souverains
Définitions strictes avec validation pydantic pour garantir l'intégrité des données.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


# ============================================================================
# ENUMS FONDAMENTAUX
# ============================================================================

class DecisionState(str, Enum):
    """États de la machine à états décisionnelle."""
    INIT = "init"
    INTENT_RESOLVED = "intent_resolved"
    CONTEXT_BUILT = "context_built"
    CHAIN_SELECTED = "chain_selected"
    CONFIDENCE_COMPUTED = "confidence_computed"
    AUTOMATION_DECIDED = "automation_decided"
    RISK_ASSESSED = "risk_assessed"
    WAITING_HUMAN = "waiting_human"
    EXECUTED = "executed"
    RISK_REJECTED = "risk_rejected"
    HUMAN_REJECTED = "human_rejected"
    FAILED = "failed"


class IntentType(str, Enum):
    """Types d'intentions canoniques."""
    COST_OPTIMIZATION = "COST_OPTIMIZATION"
    SECURITY_RISK_REDUCTION = "SECURITY_RISK_REDUCTION"
    INCIDENT_REDUCTION = "INCIDENT_REDUCTION"
    PERFORMANCE_IMPROVEMENT = "PERFORMANCE_IMPROVEMENT"
    COMPLIANCE_CHECK = "COMPLIANCE_CHECK"
    CAPACITY_PLANNING = "CAPACITY_PLANNING"
    DISASTER_RECOVERY = "DISASTER_RECOVERY"
    UNKNOWN = "UNKNOWN"


class AutomationLevel(str, Enum):
    """Niveaux d'automatisation."""
    INVESTIGATE = "investigate"  # Investigation uniquement
    RECOMMEND = "recommend"  # Recommandation sans exécution
    AUTO_EXECUTE_WITH_MONITORING = "auto_execute_with_monitoring"  # Exécution avec monitoring renforcé
    AUTO_EXECUTE = "auto_execute"  # Exécution automatique complète


class RiskLevel(str, Enum):
    """Niveaux de risque."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionType(str, Enum):
    """Types d'actions finales."""
    EXECUTE = "execute"
    EXECUTE_WITH_MONITORING = "execute_with_monitoring"
    RECOMMEND = "recommend"
    INVESTIGATE = "investigate"
    REJECTED = "rejected"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"


# ============================================================================
# TYPES DE BASE
# ============================================================================

class DecisionInput(BaseModel):
    """Entrée d'une décision - texte libre, événement ou payload structuré."""
    
    raw_input: Union[str, Dict[str, Any]] = Field(
        ...,
        description="Input brut: texte libre ou payload structuré"
    )
    source: str = Field(
        default="user",
        description="Source de l'input: user, system, api, webhook, etc."
    )
    additional_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Contexte additionnel optionnel"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp de réception de l'input"
    )
    
    @field_validator("raw_input")
    @classmethod
    def validate_raw_input(cls, v):
        """Valide que l'input n'est pas vide."""
        if isinstance(v, str) and not v.strip():
            raise ValueError("raw_input ne peut pas être une chaîne vide")
        if isinstance(v, dict) and not v:
            raise ValueError("raw_input ne peut pas être un dictionnaire vide")
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "raw_input": "facture AWS trop élevée",
                "source": "user",
                "additional_context": {"urgency": "high", "environment": "production"}
            }
        }
    )


class Intent(BaseModel):
    """Intention canonique résolue à partir de l'input."""
    
    type: IntentType = Field(
        ...,
        description="Type d'intention canonique"
    )
    priority: int = Field(
        ...,
        ge=1,
        le=10,
        description="Priorité de 1 (bas) à 10 (haut)"
    )
    success_metrics: Dict[str, str] = Field(
        default_factory=dict,
        description="Métriques de succès attendues pour cette intention"
    )
    original_input: Optional[str] = Field(
        default=None,
        description="Input original pour référence"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confiance dans la résolution d'intention"
    )
    
    @field_validator("success_metrics")
    @classmethod
    def validate_success_metrics(cls, v):
        """Valide le format des métriques de succès."""
        for key, value in v.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("success_metrics doit être un dict de str: str")
        return v
    
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "type": "COST_OPTIMIZATION",
                "priority": 8,
                "success_metrics": {"cost_reduction": ">20%", "timeframe": "7d"}
            }
        }
    )


class DecisionContext(BaseModel):
    """Contexte décisionnel complet et immuable."""
    
    # Contexte client
    client_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Préférences client, restrictions, etc."
    )
    
    # Contexte métier
    business_value_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="ROI, priorités métier, OKRs"
    )
    
    # État système
    system_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="État actuel du système (load, health, resources)"
    )
    
    # Historique décisionnel
    decision_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Historique des décisions récentes"
    )
    
    # Contraintes de gouvernance
    governance_constraints: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contraintes compliance (SOC2, ISO27001, etc.)"
    )
    
    # Risk context
    risk_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contexte de risque (blast radius, rollback capability)"
    )
    
    # Metadata
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles"
    )
    
    @model_validator(mode='after')
    def validate_context_integrity(self):
        """Valide l'intégrité du contexte."""
        # Vérifie que le contexte n'est pas vide
        if not any([
            self.client_context,
            self.business_value_context,
            self.system_state,
            self.governance_constraints
        ]):
            raise ValueError("Le contexte doit contenir au moins une source de données")
        
        # Vérifie les invariants de risque
        if self.risk_context.get("level") == "CRITICAL":
            if self.client_context.get("automation_preference") == "FULL":
                raise ValueError("CRITICAL risk cannot have FULL automation preference")
        
        return self
    
    model_config = ConfigDict(
        frozen=True,  # IMMUTABLE
        json_schema_extra={
            "example": {
                "client_context": {"automation": "high", "notification_pref": "email"},
                "business_value_context": {"roi_threshold": 1.5, "priority_business_unit": "infra"},
                "system_state": {"load": "medium", "available_resources": 8},
                "governance_constraints": {"soc2": True, "rollback_required": True}
            }
        }
    )


class AgentChain(BaseModel):
    """Chaîne d'agents sélectionnée pour exécuter l'intention."""
    
    agents: List[str] = Field(
        ...,
        min_length=1,
        description="Liste ordonnée des agents dans la chaîne"
    )
    expected_value: float = Field(
        ...,
        gt=0.0,
        description="Valeur attendue (ROI) de l'exécution de cette chaîne"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées de la chaîne (health, contracts, etc.)"
    )
    
    @field_validator("agents")
    @classmethod
    def validate_agents(cls, v):
        """Valide la liste des agents."""
        if len(v) > 10:
            raise ValueError("Une chaîne ne peut pas contenir plus de 10 agents")
        
        # Valide le format des noms d'agents
        for agent in v:
            if not isinstance(agent, str) or not agent.strip():
                raise ValueError(f"Nom d'agent invalide: {agent}")
        
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agents": ["cost_analyzer", "resource_optimizer", "terraform_applier"],
                "expected_value": 2.5,
                "metadata": {"estimated_duration": "30m", "risk_level": "medium"}
            }
        }
    )


class DecisionConfidence(BaseModel):
    """Score de confiance avec breakdown détaillé."""
    
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score de confiance global (0.0 à 1.0)"
    )
    breakdown: Dict[str, float] = Field(
        default_factory=dict,
        description="Breakdown détaillé des composants du score"
    )
    penalties: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Liste des pénalités appliquées"
    )
    raw_inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Inputs bruts utilisés pour le calcul"
    )
    
    @model_validator(mode='after')
    def validate_confidence_consistency(self):
        """Valide la cohérence du score de confiance."""
        # Le score doit être cohérent avec le breakdown
        if self.breakdown:
            avg_breakdown = sum(self.breakdown.values()) / len(self.breakdown)
            if abs(self.score - avg_breakdown) > 0.3:
                raise ValueError(f"Incohérence entre le score global ({self.score}) et le breakdown ({avg_breakdown})")
        
        # Si le score est très bas, il doit y avoir des pénalités
        if self.score < 0.1 and not self.penalties:
            raise ValueError("Un score < 0.1 doit avoir des pénalités documentées")
        
        return self
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "score": 0.85,
                "breakdown": {
                    "data_completeness": 0.9,
                    "chain_reliability": 0.8,
                    "agent_health": 0.95
                },
                "penalties": [
                    {"reason": "missing_data", "penalty": -0.1},
                    {"reason": "long_chain", "penalty": -0.05}
                ]
            }
        }
    )


class AutomationDecision(BaseModel):
    """Décision sur le niveau d'automatisation."""
    
    level: AutomationLevel = Field(
        ...,
        description="Niveau d'automatisation décidé"
    )
    justification: str = Field(
        ...,
        min_length=10,
        description="Justification détaillée de la décision"
    )
    overrides: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Overrides appliqués (par ex. client-specific)"
    )
    requires_human_approval: bool = Field(
        default=False,
        description="Si une approbation humaine est requise"
    )
    
    @field_validator("justification")
    @classmethod
    def validate_justification(cls, v):
        """Valide que la justification est suffisamment détaillée."""
        if len(v.split()) < 3:
            raise ValueError("La justification doit contenir au moins 3 mots")
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "level": "auto_execute_with_monitoring",
                "justification": "High confidence score (0.85) but risk level is medium, downgrading from auto_execute",
                "requires_human_approval": False
            }
        }
    )


class RiskAssessment(BaseModel):
    """Évaluation des risques d'une décision."""
    
    approved: bool = Field(
        ...,
        description="Si le risque est approuvé"
    )
    justification: str = Field(
        ...,
        description="Justification de l'évaluation"
    )
    blast_radius: str = Field(
        ...,
        description="Impact potentiel (ex: 'single_service', 'multiple_services', 'entire_system')"
    )
    rollback_possible: bool = Field(
        default=True,
        description="Si un rollback est possible"
    )
    risk_level: RiskLevel = Field(
        ...,
        description="Niveau de risque estimé"
    )
    mitigations: List[str] = Field(
        default_factory=list,
        description="Mesures de mitigation disponibles"
    )
    
    @model_validator(mode='after')
    def validate_risk_consistency(self):
        """Valide la cohérence de l'évaluation de risque."""
        if not self.approved and not self.justification:
            raise ValueError("Un risque rejeté doit avoir une justification")
        
        if self.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL] and not self.mitigations:
            raise ValueError(f"Risque {self.risk_level} doit avoir des mitigations définies")
        
        if self.blast_radius == "entire_system" and self.approved and not self.rollback_possible:
            raise ValueError("Impact système entier sans rollback ne peut pas être approuvé")
        
        return self
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "approved": True,
                "justification": "Blast radius limited to single service, rollback available",
                "blast_radius": "single_service",
                "rollback_possible": True,
                "risk_level": "medium"
            }
        }
    )


class HumanGateResponse(BaseModel):
    """Réponse du human gate."""
    
    required: bool = Field(
        ...,
        description="Si une approbation humaine est requise"
    )
    approved: bool = Field(
        default=False,
        description="Si l'approbation humaine a été donnée"
    )
    justification: str = Field(
        default="",
        description="Justification de la réponse"
    )
    approver: Optional[str] = Field(
        default=None,
        description="Identifiant de l'approbateur"
    )
    requested_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp de la demande"
    )
    responded_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp de la réponse"
    )
    timeout_hours: float = Field(
        default=1.0,
        description="Timeout en heures pour l'approbation"
    )
    
    @model_validator(mode='after')
    def validate_response_consistency(self):
        """Valide la cohérence de la réponse."""
        if self.approved and not self.required:
            raise ValueError("Ne peut pas être approuvé si non requis")
        
        if self.approved and not self.approver:
            raise ValueError("Une approbation doit avoir un approbateur")
        
        if self.responded_at and self.responded_at < self.requested_at:
            raise ValueError("responded_at ne peut pas être avant requested_at")
        
        return self


class DecisionPlan(BaseModel):
    """Plan de décision versionné pour reprise et audit."""
    
    version: str = Field(
        default="1.0",
        pattern=r"^\d+\.\d+$",  # Format semver simple
        description="Version du plan (format: X.Y)"
    )
    steps: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Étapes du plan dans l'ordre d'exécution"
    )
    chain: AgentChain = Field(
        ...,
        description="Chaîne d'agents sélectionnée"
    )
    policies: Dict[str, Any] = Field(
        default_factory=dict,
        description="Politiques appliquées (automation, risk, etc.)"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp de création du plan"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles"
    )
    
    @model_validator(mode='after')
    def validate_plan_integrity(self):
        """Valide l'intégrité du plan."""
        # Vérifie que le plan a au moins une étape
        if not self.steps:
            raise ValueError("Un plan doit avoir au moins une étape")
        
        # Vérifie que les politiques contiennent les clés requises
        required_policies = ["automation", "risk"]
        for policy in required_policies:
            if policy not in self.policies:
                raise ValueError(f"Le plan doit contenir la politique {policy}")
        
        return self
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "version": "1.0",
                "steps": [
                    {"module": "intent_resolver", "result": {"type": "COST_OPTIMIZATION"}},
                    {"module": "context_builder", "result": "context_built"}
                ],
                "chain": {
                    "agents": ["cost_analyzer"],
                    "expected_value": 2.5
                },
                "policies": {
                    "automation": {"level": "auto_execute_with_monitoring"},
                    "risk": {"approved": True}
                },
                "created_at": "2024-01-15T10:30:00Z"
            }
        }
    )


class DecisionOutcome(BaseModel):
    """Résultat final d'une décision."""
    
    action: ActionType = Field(
        ...,
        description="Action finale décidée"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score de confiance final"
    )
    justification: str = Field(
        ...,
        min_length=10,
        description="Justification détaillée de la décision"
    )
    trace_id: str = Field(
        ...,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="UUID pour traçabilité complète"
    )
    final_state: DecisionState = Field(
        ...,
        description="État final de la décision"
    )
    duration_seconds: float = Field(
        ...,
        gt=0.0,
        description="Durée totale de la décision en secondes"
    )
    plan: Optional[DecisionPlan] = Field(
        default=None,
        description="Plan de décision complet (si disponible)"
    )
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Détails additionnels du résultat"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp du résultat"
    )
    
    @model_validator(mode='after')
    def validate_outcome_consistency(self):
        """Valide la cohérence du résultat."""
        # Vérifie la cohérence entre l'action et l'état
        state_action_mapping = {
            DecisionState.EXECUTED: [ActionType.EXECUTE, ActionType.EXECUTE_WITH_MONITORING],
            DecisionState.RISK_REJECTED: [ActionType.REJECTED],
            DecisionState.HUMAN_REJECTED: [ActionType.REJECTED],
            DecisionState.FAILED: [ActionType.FAILED],
            DecisionState.WAITING_HUMAN: [ActionType.AWAITING_APPROVAL],
        }
        
        if self.final_state in state_action_mapping:
            allowed_actions = state_action_mapping[self.final_state]
            if self.action not in allowed_actions:
                raise ValueError(
                    f"État {self.final_state} incompatible avec action {self.action}. "
                    f"Actions autorisées: {allowed_actions}"
                )
        
        # Vérifie que les décisions rejetées ont une justification spécifique
        if self.action == ActionType.REJECTED and "rejected" not in self.justification.lower():
            raise ValueError("Les décisions rejetées doivent mentionner 'rejected' dans la justification")
        
        # Vérifie la cohérence de la confiance
        if self.confidence < 0.1 and self.action == ActionType.EXECUTE:
            raise ValueError("Action EXECUTE ne peut pas avoir une confiance < 0.1")
        
        return self
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "action": "execute_with_monitoring",
                "confidence": 0.85,
                "justification": "Cost optimization approved with monitoring due to medium risk level",
                "trace_id": "123e4567-e89b-12d3-a456-426614174000",
                "final_state": "executed",
                "duration_seconds": 5.2,
                "timestamp": "2024-01-15T10:30:05Z"
            }
        }
    )


# ============================================================================
# TYPES DE SUPPORT
# ============================================================================

class DecisionLogEntry(BaseModel):
    """Entrée de log pour audit trail."""
    
    trace_id: str = Field(
        ...,
        description="ID de traçage"
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp de l'événement"
    )
    level: str = Field(
        default="INFO",
        pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        description="Niveau de log"
    )
    component: str = Field(
        ...,
        description="Composant source"
    )
    event: str = Field(
        ...,
        description="Type d'événement"
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Données de l'événement"
    )
    state_transition: Optional[Dict[str, str]] = Field(
        default=None,
        description="Transition d'état associée (si applicable)"
    )
    
    model_config = ConfigDict(
        frozen=True,  # Logs immuables
        json_schema_extra={
            "example": {
                "trace_id": "123e4567-e89b-12d3-a456-426614174000",
                "timestamp": "2024-01-15T10:30:00Z",
                "level": "INFO",
                "component": "brain",
                "event": "state_transition",
                "data": {"from": "init", "to": "intent_resolved"},
                "state_transition": {"from": "init", "to": "intent_resolved", "reason": "Intent resolved"}
            }
        }
    )


class HealthCheck(BaseModel):
    """État de santé d'un composant."""
    
    component: str = Field(
        ...,
        description="Nom du composant"
    )
    status: str = Field(
        ...,
        pattern=r"^(healthy|degraded|unhealthy)$",
        description="État de santé"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp du check"
    )
    metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métriques de santé"
    )
    dependencies: List[str] = Field(
        default_factory=list,
        description="Dépendances du composant"
    )
    
    @model_validator(mode='after')
    def validate_health_status(self):
        """Valide l'état de santé."""
        if self.status == "unhealthy" and not self.metrics.get("error"):
            raise ValueError("Un composant unhealthy doit avoir une erreur dans les métriques")
        return self


class ErrorDetails(BaseModel):
    """Détails d'une erreur structurée."""
    
    error_type: str = Field(
        ...,
        description="Type d'erreur"
    )
    error_message: str = Field(
        ...,
        description="Message d'erreur"
    )
    trace_id: Optional[str] = Field(
        default=None,
        description="ID de traçage associé"
    )
    component: str = Field(
        ...,
        description="Composant source de l'erreur"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp de l'erreur"
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contexte de l'erreur"
    )
    recoverable: bool = Field(
        default=True,
        description="Si l'erreur est récupérable"
    )


# ============================================================================
# EXPORT
# ============================================================================

__all__ = [
    # Enums
    "DecisionState",
    "IntentType",
    "AutomationLevel",
    "RiskLevel",
    "ActionType",
    
    # Types principaux
    "DecisionInput",
    "Intent",
    "DecisionContext",
    "AgentChain",
    "DecisionConfidence",
    "AutomationDecision",
    "RiskAssessment",
    "HumanGateResponse",
    "DecisionPlan",
    "DecisionOutcome",
    
    # Types de support
    "DecisionLogEntry",
    "HealthCheck",
    "ErrorDetails",
]