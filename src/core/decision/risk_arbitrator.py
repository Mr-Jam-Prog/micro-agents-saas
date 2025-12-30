"""
Risk Arbitrator - Évalue et valide les risques opérationnels
Empêche les décisions dangereuses en analysant le blast radius, le rollback et la gouvernance.
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import logging
from enum import Enum
import math

from ..types import DecisionContext, AutomationDecision
from ..exceptions import RiskRejectedError

logger = logging.getLogger(__name__)


class RiskSeverity(str, Enum):
    """Niveaux de sévérité des risques."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RollbackCapability(str, Enum):
    """Capacité de rollback."""
    FULL = "full"          # Rollback complet et rapide
    PARTIAL = "partial"    # Rollback partiel ou limité
    NONE = "none"          # Aucun rollback possible
    UNKNOWN = "unknown"    # Capacité inconnue


@dataclass
class RiskAssessment:
    """Évaluation complète des risques."""
    approved: bool
    justification: str
    blast_radius: str
    severity: RiskSeverity
    rollback_capability: RollbackCapability
    requires_human_approval: bool
    risk_score: float  # 0.0 (sans risque) à 1.0 (risque maximal)
    mitigation_suggestions: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskThreshold:
    """Seuils de risque par environnement."""
    max_blast_radius: Dict[str, float]  # Métrique -> valeur max
    max_risk_score: float
    allowed_severities: List[RiskSeverity]
    min_rollback_for_auto: RollbackCapability


@dataclass
class RiskPolicyConfig:
    """Configuration des politiques de risque."""
    
    # Seuils par environnement
    environment_thresholds: Dict[str, RiskThreshold] = field(default_factory=lambda: {
        "production": RiskThreshold(
            max_blast_radius={
                "affected_users": 100,
                "data_volume_gb": 10,
                "downtime_minutes": 15,
                "cost_impact_usd": 1000
            },
            max_risk_score=0.3,
            allowed_severities=[RiskSeverity.LOW, RiskSeverity.MEDIUM],
            min_rollback_for_auto=RollbackCapability.FULL
        ),
        "staging": RiskThreshold(
            max_blast_radius={
                "affected_users": 1000,
                "data_volume_gb": 50,
                "downtime_minutes": 60,
                "cost_impact_usd": 5000
            },
            max_risk_score=0.5,
            allowed_severities=[RiskSeverity.LOW, RiskSeverity.MEDIUM, RiskSeverity.HIGH],
            min_rollback_for_auto=RollbackCapability.PARTIAL
        ),
        "development": RiskThreshold(
            max_blast_radius={
                "affected_users": 5000,
                "data_volume_gb": 100,
                "downtime_minutes": 240,
                "cost_impact_usd": 10000
            },
            max_risk_score=0.7,
            allowed_severities=[RiskSeverity.LOW, RiskSeverity.MEDIUM, RiskSeverity.HIGH],
            min_rollback_for_auto=RollbackCapability.NONE
        ),
        "sandbox": RiskThreshold(
            max_blast_radius={
                "affected_users": 10000,
                "data_volume_gb": 500,
                "downtime_minutes": 480,
                "cost_impact_usd": 50000
            },
            max_risk_score=0.9,
            allowed_severities=[RiskSeverity.LOW, RiskSeverity.MEDIUM, RiskSeverity.HIGH, RiskSeverity.CRITICAL],
            min_rollback_for_auto=RollbackCapability.NONE
        )
    })
    
    # Facteurs de pondération pour le calcul du score de risque
    risk_factors: Dict[str, float] = field(default_factory=lambda: {
        "blast_radius": 0.4,
        "rollback_capability": 0.3,
        "environment_sensitivity": 0.2,
        "time_sensitivity": 0.1
    })
    
    # Règles spécifiques par type d'intention
    intent_specific_rules: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "SECURITY_RISK_REDUCTION": {
            "max_allowed_severity": RiskSeverity.HIGH,
            "require_rollback": False,  # Les correctifs de sécurité peuvent être non réversibles
            "special_approval_required": True
        },
        "COMPLIANCE_ENFORCEMENT": {
            "max_allowed_severity": RiskSeverity.MEDIUM,
            "require_rollback": True,
            "special_approval_required": False
        },
        "COST_OPTIMIZATION": {
            "max_allowed_severity": RiskSeverity.MEDIUM,
            "require_rollback": True,
            "special_approval_required": False
        }
    })
    
    # Actions nécessitant toujours une approbation humaine
    always_human_approval: List[str] = field(default_factory=lambda: [
        "DELETE_PRODUCTION_DATA",
        "DISABLE_SECURITY_CONTROLS",
        "CHANGE_AUTHENTICATION_SYSTEM",
        "MODIFY_COMPLIANCE_SETTINGS"
    ])


class RiskArbitrator:
    """
    Arbitre de risques opérationnels.
    
    Responsabilités:
    1. Évaluer le blast radius (impact potentiel)
    2. Vérifier la capacité de rollback
    3. Appliquer les politiques de gouvernance
    4. Bloquer les actions non réversibles sans approbation humaine
    5. Renforcer les seuils en production
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialise l'arbitre de risques.
        
        Args:
            config_path: Chemin vers fichier YAML de configuration.
        """
        if config_path:
            self.config = self._load_config_from_file(config_path)
        else:
            self.config = RiskPolicyConfig()
        
        self.logger = logger
        
        # Valider la configuration
        self._validate_config()
        
        self.logger.info(
            "risk_arbitrator_initialized",
            environment_count=len(self.config.environment_thresholds),
            intent_specific_rules=len(self.config.intent_specific_rules)
        )
    
    def _load_config_from_file(self, config_path: str) -> RiskPolicyConfig:
        """Charge la configuration depuis un fichier YAML."""
        path = Path(config_path)
        if not path.exists():
            self.logger.warning(f"Config file not found: {config_path}, using defaults")
            return RiskPolicyConfig()
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config_dict = yaml.safe_load(f)
            
            # Convertir les seuils de dictionnaires en objets RiskThreshold
            if "environment_thresholds" in config_dict:
                for env, threshold_dict in config_dict["environment_thresholds"].items():
                    config_dict["environment_thresholds"][env] = RiskThreshold(**threshold_dict)
            
            return RiskPolicyConfig(**config_dict)
            
        except Exception as e:
            self.logger.error(f"Failed to load config from {config_path}: {e}")
            return RiskPolicyConfig()
    
    def _validate_config(self) -> None:
        """Valide la configuration."""
        errors = []
        
        # Vérifier que tous les environnements ont des seuils valides
        for env, threshold in self.config.environment_thresholds.items():
            if not isinstance(threshold, RiskThreshold):
                errors.append(f"Invalid threshold for environment {env}")
            elif threshold.max_risk_score < 0 or threshold.max_risk_score > 1:
                errors.append(f"Invalid max_risk_score for {env}: {threshold.max_risk_score}")
        
        # Vérifier les facteurs de risque
        total_weight = sum(self.config.risk_factors.values())
        if abs(total_weight - 1.0) > 0.001:
            errors.append(f"Risk factors must sum to 1.0, got {total_weight}")
        
        if errors:
            error_msg = f"Configuration validation failed: {'; '.join(errors)}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
    
    async def assess(
        self, 
        automation_decision: AutomationDecision,
        context: DecisionContext
    ) -> RiskAssessment:
        """
        Évalue les risques d'une décision d'automatisation.
        
        Args:
            automation_decision: Décision d'automatisation
            context: Contexte décisionnel
            
        Returns:
            RiskAssessment: Évaluation complète des risques
            
        Raises:
            RiskRejectedError: Si les risques sont inacceptables
        """
        try:
            self.logger.info(
                "risk_assessment_started",
                automation_level=automation_decision.level.value,
                context_id=id(context)
            )
            
            # 1. Collecter les données de risque
            risk_data = self._collect_risk_data(context)
            
            # 2. Calculer le blast radius
            blast_radius, blast_metrics = self._calculate_blast_radius(context, risk_data)
            
            # 3. Évaluer la capacité de rollback
            rollback_capability = self._assess_rollback_capability(context, risk_data)
            
            # 4. Calculer le score de risque
            risk_score = self._calculate_risk_score(
                blast_radius, 
                rollback_capability, 
                context, 
                risk_data
            )
            
            # 5. Déterminer la sévérité
            severity = self._determine_severity(risk_score, context)
            
            # 6. Appliquer les seuils de l'environnement
            environment = context.environment.get("environment", "production") if context.environment else "production"
            threshold = self.config.environment_thresholds.get(
                environment, 
                self.config.environment_thresholds["production"]
            )
            
            # 7. Appliquer les règles spécifiques à l'intention
            intent = context.intent or {}
            intent_type = intent.get("type")
            intent_rules = self.config.intent_specific_rules.get(intent_type, {})
            
            # 8. Évaluer si une approbation humaine est requise
            requires_human_approval = self._requires_human_approval(
                automation_decision,
                severity,
                rollback_capability,
                context,
                intent_rules
            )
            
            # 9. Déterminer si la décision est approuvée
            approved, justification = self._determine_approval(
                risk_score,
                severity,
                blast_radius,
                rollback_capability,
                threshold,
                requires_human_approval,
                automation_decision,
                context,
                intent_rules
            )
            
            # 10. Générer des suggestions de mitigation
            mitigation_suggestions = self._generate_mitigation_suggestions(
                risk_score,
                severity,
                blast_radius,
                rollback_capability,
                approved,
                requires_human_approval
            )
            
            # Créer l'évaluation de risque
            assessment = RiskAssessment(
                approved=approved,
                justification=justification,
                blast_radius=blast_radius,
                severity=severity,
                rollback_capability=rollback_capability,
                requires_human_approval=requires_human_approval,
                risk_score=risk_score,
                mitigation_suggestions=mitigation_suggestions,
                metadata={
                    "blast_metrics": blast_metrics,
                    "environment": environment,
                    "intent_type": intent_type,
                    "automation_level": automation_decision.level.value,
                    "threshold_used": threshold.max_risk_score
                }
            )
            
            self.logger.info(
                "risk_assessment_completed",
                approved=approved,
                risk_score=risk_score,
                severity=severity.value,
                requires_human_approval=requires_human_approval
            )
            
            if not approved:
                raise RiskRejectedError(
                    justification,
                    blast_radius=blast_radius,
                    severity=severity,
                    risk_score=risk_score
                )
            
            return assessment
            
        except RiskRejectedError:
            raise
        except Exception as e:
            self.logger.error(
                "risk_assessment_failed",
                error=str(e),
                error_type=e.__class__.__name__
            )
            # En cas d'erreur, rejeter par sécurité
            raise RiskRejectedError(
                f"Risk assessment failed: {str(e)}",
                blast_radius="unknown",
                severity=RiskSeverity.CRITICAL,
                risk_score=1.0
            )
    
    def _collect_risk_data(self, context: DecisionContext) -> Dict[str, Any]:
        """Collecte les données nécessaires à l'évaluation des risques."""
        risk_data = {
            "environment": context.environment.get("environment", "production") if context.environment else "production",
            "intent": context.intent or {},
            "client_preferences": context.client_preferences or {},
            "compliance_constraints": context.compliance_constraints or {},
            "system_state": context.system_state or {},
            "business_value": context.business_value or {}
        }
        
        # Estimer les métriques d'impact
        system_state = risk_data["system_state"]
        business_value = risk_data["business_value"]
        
        # Estimation du nombre d'utilisateurs affectés
        if "resource_utilization" in system_state:
            # Estimation basée sur la charge système
            cpu = system_state["resource_utilization"].get("cpu_percent", 50)
            affected_users = int(cpu * 100)  # Approximation
        else:
            affected_users = 1000  # Valeur par défaut
        
        # Estimation du volume de données
        if "resource_utilization" in system_state:
            disk = system_state["resource_utilization"].get("disk_percent", 50)
            data_volume_gb = disk * 2  # Approximation
        else:
            data_volume_gb = 100
        
        # Estimation de l'impact financier
        expected_roi = business_value.get("expected_roi", 1.0)
        cost_impact_usd = 1000 * expected_roi  # Approximation
        
        risk_data.update({
            "estimated_affected_users": affected_users,
            "estimated_data_volume_gb": data_volume_gb,
            "estimated_cost_impact_usd": cost_impact_usd,
            "estimated_downtime_minutes": 30  # Valeur par défaut
        })
        
        return risk_data
    
    def _calculate_blast_radius(self, context: DecisionContext, risk_data: Dict[str, Any]) -> Tuple[str, Dict[str, float]]:
        """Calcule le blast radius (impact potentiel)."""
        metrics = {
            "affected_users": risk_data["estimated_affected_users"],
            "data_volume_gb": risk_data["estimated_data_volume_gb"],
            "downtime_minutes": risk_data["estimated_downtime_minutes"],
            "cost_impact_usd": risk_data["estimated_cost_impact_usd"]
        }
        
        # Normaliser les métriques
        max_values = {
            "affected_users": 10000,
            "data_volume_gb": 500,
            "downtime_minutes": 480,
            "cost_impact_usd": 50000
        }
        
        normalized_metrics = {}
        for key, value in metrics.items():
            max_val = max_values.get(key, 1)
            normalized = value / max_val if max_val > 0 else 0
            normalized_metrics[key] = min(1.0, normalized)
        
        # Calculer un score composite
        weights = {
            "affected_users": 0.3,
            "data_volume_gb": 0.25,
            "downtime_minutes": 0.25,
            "cost_impact_usd": 0.2
        }
        
        composite_score = sum(
            normalized_metrics[key] * weights.get(key, 0) 
            for key in normalized_metrics
        )
        
        # Déterminer la catégorie de blast radius
        if composite_score < 0.1:
            category = "minimal"
        elif composite_score < 0.3:
            category = "small"
        elif composite_score < 0.6:
            category = "moderate"
        elif composite_score < 0.8:
            category = "large"
        else:
            category = "critical"
        
        return category, metrics
    
    def _assess_rollback_capability(self, context: DecisionContext, risk_data: Dict[str, Any]) -> RollbackCapability:
        """Évalue la capacité de rollback."""
        compliance = risk_data["compliance_constraints"]
        system_state = risk_data["system_state"]
        
        # Vérifier les contraintes de compliance
        rollback_required = compliance.get("rollback_required", True)
        
        if not rollback_required:
            return RollbackCapability.NONE
        
        # Vérifier l'état du système
        if "pending_changes" in system_state:
            pending_changes = system_state["pending_changes"]
            if pending_changes > 5:
                return RollbackCapability.PARTIAL
        
        # Vérifier les métadonnées de l'intention
        intent = risk_data["intent"]
        intent_type = intent.get("type")
        
        # Certaines intentions sont difficilement réversibles
        hard_to_rollback = [
            "SECURITY_RISK_REDUCTION",  # Les correctifs de sécurité peuvent être complexes à annuler
            "DATA_DELETION",  # Suppression de données
            "SCHEMA_MIGRATION"  # Migration de schéma de base de données
        ]
        
        if intent_type in hard_to_rollback:
            return RollbackCapability.PARTIAL
        
        # Vérifier l'environnement
        environment = risk_data["environment"]
        if environment == "production":
            # En production, on suppose un rollback complet si aucune indication contraire
            return RollbackCapability.FULL
        elif environment in ["staging", "development"]:
            # Dans les environnements de test, le rollback est plus facile
            return RollbackCapability.FULL
        else:
            return RollbackCapability.UNKNOWN
    
    def _calculate_risk_score(
        self, 
        blast_radius: str, 
        rollback_capability: RollbackCapability,
        context: DecisionContext,
        risk_data: Dict[str, Any]
    ) -> float:
        """Calcule un score de risque composite."""
        # Convertir le blast radius en score
        blast_radius_scores = {
            "minimal": 0.1,
            "small": 0.3,
            "moderate": 0.5,
            "large": 0.7,
            "critical": 0.9
        }
        blast_score = blast_radius_scores.get(blast_radius, 0.5)
        
        # Convertir la capacité de rollback en score
        rollback_scores = {
            RollbackCapability.FULL: 0.1,
            RollbackCapability.PARTIAL: 0.5,
            RollbackCapability.NONE: 0.9,
            RollbackCapability.UNKNOWN: 0.7
        }
        rollback_score = rollback_scores.get(rollback_capability, 0.5)
        
        # Évaluer la sensibilité de l'environnement
        environment = risk_data["environment"]
        env_sensitivity = {
            "production": 0.9,
            "staging": 0.5,
            "development": 0.3,
            "sandbox": 0.1
        }
        env_score = env_sensitivity.get(environment, 0.7)
        
        # Évaluer la sensibilité temporelle (heure de la journée)
        import datetime
        now = datetime.datetime.utcnow()
        hour = now.hour
        
        # Heures de bureau (9h-17h UTC) = plus sensible
        if 9 <= hour < 17:
            time_score = 0.8
        else:
            time_score = 0.3
        
        # Appliquer les pondérations
        factors = self.config.risk_factors
        
        risk_score = (
            factors.get("blast_radius", 0.4) * blast_score +
            factors.get("rollback_capability", 0.3) * rollback_score +
            factors.get("environment_sensitivity", 0.2) * env_score +
            factors.get("time_sensitivity", 0.1) * time_score
        )
        
        return min(1.0, max(0.0, risk_score))
    
    def _determine_severity(self, risk_score: float, context: DecisionContext) -> RiskSeverity:
        """Détermine la sévérité du risque basée sur le score."""
        if risk_score < 0.2:
            return RiskSeverity.LOW
        elif risk_score < 0.4:
            return RiskSeverity.MEDIUM
        elif risk_score < 0.7:
            return RiskSeverity.HIGH
        else:
            return RiskSeverity.CRITICAL
    
    def _requires_human_approval(
        self,
        automation_decision: AutomationDecision,
        severity: RiskSeverity,
        rollback_capability: RollbackCapability,
        context: DecisionContext,
        intent_rules: Dict[str, Any]
    ) -> bool:
        """Détermine si une approbation humaine est requise."""
        # Règle 1: Pas de rollback → approbation humaine obligatoire
        if rollback_capability in [RollbackCapability.NONE, RollbackCapability.UNKNOWN]:
            self.logger.debug("Human approval required: no rollback capability")
            return True
        
        # Règle 2: Sévérité critique → approbation humaine
        if severity == RiskSeverity.CRITICAL:
            self.logger.debug("Human approval required: critical severity")
            return True
        
        # Règle 3: Automation en production avec risque élevé
        environment = context.environment.get("environment", "production") if context.environment else "production"
        if (environment == "production" and 
            automation_decision.level.value in ["AUTO_EXECUTE", "AUTO_EXECUTE_WITH_MONITORING"] and
            severity == RiskSeverity.HIGH):
            self.logger.debug("Human approval required: high risk automation in production")
            return True
        
        # Règle 4: Règles spécifiques à l'intention
        if intent_rules.get("special_approval_required", False):
            self.logger.debug("Human approval required: intent-specific rule")
            return True
        
        # Règle 5: Vérifier la liste des actions toujours soumises à approbation
        intent = context.intent or {}
        intent_type = intent.get("type", "")
        if intent_type in self.config.always_human_approval:
            self.logger.debug(f"Human approval required: {intent_type} requires always human approval")
            return True
        
        return False
    
    def _determine_approval(
        self,
        risk_score: float,
        severity: RiskSeverity,
        blast_radius: str,
        rollback_capability: RollbackCapability,
        threshold: RiskThreshold,
        requires_human_approval: bool,
        automation_decision: AutomationDecision,
        context: DecisionContext,
        intent_rules: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Détermine si la décision est approuvée et génère une justification."""
        environment = context.environment.get("environment", "production") if context.environment else "production"
        intent = context.intent or {}
        intent_type = intent.get("type")
        
        # Vérifier le score de risque par rapport au seuil
        if risk_score > threshold.max_risk_score:
            justification = (
                f"Risk score {risk_score:.2f} exceeds maximum allowed {threshold.max_risk_score} "
                f"for {environment} environment"
            )
            return False, justification
        
        # Vérifier la sévérité par rapport aux seuils autorisés
        if severity not in threshold.allowed_severities:
            justification = (
                f"Risk severity {severity.value} not allowed in {environment} environment. "
                f"Allowed severities: {[s.value for s in threshold.allowed_severities]}"
            )
            return False, justification
        
        # Vérifier le blast radius par rapport aux seuils
        blast_metrics = self._get_blast_metrics(context)
        for metric, max_value in threshold.max_blast_radius.items():
            if metric in blast_metrics and blast_metrics[metric] > max_value:
                justification = (
                    f"Blast radius metric '{metric}' exceeds threshold: "
                    f"{blast_metrics[metric]} > {max_value} in {environment}"
                )
                return False, justification
        
        # Vérifier les règles spécifiques à l'intention
        max_allowed_severity = intent_rules.get("max_allowed_severity")
        if max_allowed_severity and severity.value > max_allowed_severity.value:
            justification = (
                f"Risk severity {severity.value} exceeds maximum allowed "
                f"{max_allowed_severity.value} for intent {intent_type}"
            )
            return False, justification
        
        require_rollback = intent_rules.get("require_rollback", False)
        if require_rollback and rollback_capability in [RollbackCapability.NONE, RollbackCapability.UNKNOWN]:
            justification = (
                f"Intent {intent_type} requires rollback capability, "
                f"but rollback is {rollback_capability.value}"
            )
            return False, justification
        
        # Vérifier la capacité de rollback pour l'automatisation
        if (automation_decision.level.value in ["AUTO_EXECUTE", "AUTO_EXECUTE_WITH_MONITORING"] and
            self._get_rollback_score(rollback_capability) < self._get_rollback_score(threshold.min_rollback_for_auto)):
            justification = (
                f"Automation level {automation_decision.level.value} requires at least "
                f"{threshold.min_rollback_for_auto.value} rollback capability, "
                f"but got {rollback_capability.value}"
            )
            return False, justification
        
        # Si une approbation humaine est requise mais non disponible, rejeter
        if requires_human_approval:
            # Si nous sommes dans un contexte où l'approbation humaine n'est pas disponible
            # (ex: traitement batch automatique), nous rejetons
            justification = (
                f"Human approval required but not available. "
                f"Risk score: {risk_score:.2f}, Severity: {severity.value}, "
                f"Rollback: {rollback_capability.value}"
            )
            return False, justification
        
        # Tous les critères sont satisfaits
        justification = (
            f"Risk assessment passed. Score: {risk_score:.2f}, "
            f"Severity: {severity.value}, Blast radius: {blast_radius}, "
            f"Rollback: {rollback_capability.value}"
        )
        return True, justification
    
    def _get_blast_metrics(self, context: DecisionContext) -> Dict[str, float]:
        """Extrait les métriques de blast radius du contexte."""
        # Dans une implémentation réelle, cela viendrait de l'analyse du contexte
        # Pour l'exemple, nous utilisons des valeurs par défaut
        return {
            "affected_users": 50,
            "data_volume_gb": 5,
            "downtime_minutes": 10,
            "cost_impact_usd": 500
        }
    
    def _get_rollback_score(self, rollback_capability: RollbackCapability) -> int:
        """Convertit la capacité de rollback en score numérique pour comparaison."""
        scores = {
            RollbackCapability.FULL: 3,
            RollbackCapability.PARTIAL: 2,
            RollbackCapability.NONE: 1,
            RollbackCapability.UNKNOWN: 0
        }
        return scores.get(rollback_capability, 0)
    
    def _generate_mitigation_suggestions(
        self,
        risk_score: float,
        severity: RiskSeverity,
        blast_radius: str,
        rollback_capability: RollbackCapability,
        approved: bool,
        requires_human_approval: bool
    ) -> List[str]:
        """Génère des suggestions pour mitiger les risques."""
        suggestions = []
        
        if risk_score > 0.5:
            suggestions.append("Consider implementing phased rollout")
            suggestions.append("Add additional monitoring during execution")
        
        if severity in [RiskSeverity.HIGH, RiskSeverity.CRITICAL]:
            suggestions.append("Execute during off-peak hours")
            suggestions.append("Ensure backup is available before proceeding")
        
        if blast_radius in ["large", "critical"]:
            suggestions.append("Notify affected stakeholders before execution")
            suggestions.append("Prepare rollback plan and team")
        
        if rollback_capability in [RollbackCapability.PARTIAL, RollbackCapability.NONE]:
            suggestions.append("Implement snapshot or backup before execution")
            suggestions.append("Consider dry-run in staging first")
        
        if requires_human_approval:
            suggestions.append("Ensure human approver is available during execution window")
        
        if not approved:
            suggestions.append("Re-evaluate with smaller scope or different approach")
            suggestions.append("Consult with subject matter experts")
        
        return suggestions
    
    def get_risk_assessment_report(
        self,
        automation_decision: AutomationDecision,
        context: DecisionContext
    ) -> Dict[str, Any]:
        """Génère un rapport détaillé d'évaluation des risques."""
        risk_data = self._collect_risk_data(context)
        blast_radius, blast_metrics = self._calculate_blast_radius(context, risk_data)
        rollback_capability = self._assess_rollback_capability(context, risk_data)
        risk_score = self._calculate_risk_score(blast_radius, rollback_capability, context, risk_data)
        severity = self._determine_severity(risk_score, context)
        
        environment = context.environment.get("environment", "production") if context.environment else "production"
        threshold = self.config.environment_thresholds.get(environment)
        
        intent = context.intent or {}
        intent_type = intent.get("type")
        intent_rules = self.config.intent_specific_rules.get(intent_type, {})
        
        requires_human_approval = self._requires_human_approval(
            automation_decision,
            severity,
            rollback_capability,
            context,
            intent_rules
        )
        
        approved, justification = self._determine_approval(
            risk_score,
            severity,
            blast_radius,
            rollback_capability,
            threshold,
            requires_human_approval,
            automation_decision,
            context,
            intent_rules
        )
        
        return {
            "risk_score": risk_score,
            "severity": severity.value,
            "blast_radius": blast_radius,
            "blast_metrics": blast_metrics,
            "rollback_capability": rollback_capability.value,
            "environment": environment,
            "threshold_max_risk": threshold.max_risk_score if threshold else None,
            "intent_type": intent_type,
            "intent_rules_applied": intent_rules,
            "requires_human_approval": requires_human_approval,
            "approved": approved,
            "justification": justification,
            "automation_level": automation_decision.level.value,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
    
    def update_config(self, config: RiskPolicyConfig) -> None:
        """Met à jour la configuration."""
        self.config = config
        self._validate_config()
        self.logger.info("Risk policy configuration updated")
    
    def get_config_summary(self) -> Dict[str, Any]:
        """Retourne un résumé de la configuration."""
        return {
            "environments": list(self.config.environment_thresholds.keys()),
            "risk_factors": self.config.risk_factors,
            "intent_specific_rules": list(self.config.intent_specific_rules.keys()),
            "always_human_approval_actions": self.config.always_human_approval
        }


# Singleton pour utilisation facile
_risk_arbitrator_instance = None

def get_risk_arbitrator(config_path: Optional[str] = None) -> RiskArbitrator:
    """Obtient l'instance singleton du RiskArbitrator."""
    global _risk_arbitrator_instance
    if _risk_arbitrator_instance is None:
        _risk_arbitrator_instance = RiskArbitrator(config_path)
    return _risk_arbitrator_instance


# Fonction utilitaire pour évaluation rapide
async def assess_risk(
    automation_decision: AutomationDecision,
    context: DecisionContext,
    config_path: Optional[str] = None
) -> RiskAssessment:
    """
    Fonction utilitaire pour évaluer les risques.
    
    Args:
        automation_decision: Décision d'automatisation
        context: Contexte décisionnel
        config_path: Chemin vers configuration YAML
        
    Returns:
        RiskAssessment: Évaluation des risques
    """
    arbitrator = RiskArbitrator(config_path)
    return await arbitrator.assess(automation_decision, context)


# Tests unitaires intégrés
if __name__ == "__main__":
    import asyncio
    import datetime
    
    async def test_risk_arbitrator():
        """Test basique du RiskArbitrator."""
        from ..types import AutomationDecision, AutomationLevel, DecisionContext
        
        # Créer une décision d'automatisation de test
        automation_decision = AutomationDecision(
            level=AutomationLevel.AUTO_EXECUTE_WITH_MONITORING,
            justification="High confidence cost optimization",
            metadata={"confidence_score": 0.85}
        )
        
        # Créer un contexte de test
        context = DecisionContext(
            client_preferences={
                "automation_preference": "high",
                "risk_tolerance": "medium"
            },
            business_value={"expected_roi": 2.5, "business_criticality": "medium"},
            system_state={
                "infrastructure_health": "healthy",
                "resource_utilization": {
                    "cpu_percent": 65.5,
                    "memory_percent": 72.3,
                    "disk_percent": 45.8
                },
                "pending_changes": 2
            },
            compliance_constraints={
                "rollback_required": True,
                "standards": ["SOC2", "ISO27001"]
            },
            decision_history=[],
            environment={"environment": "staging"},
            intent={"type": "COST_OPTIMIZATION", "priority": 8},
            metadata={}
        )
        
        # Tester l'arbitre
        arbitrator = RiskArbitrator()
        
        try:
            assessment = await arbitrator.assess(automation_decision, context)
            print(f"✓ Évaluation des risques: {'APPROUVÉ' if assessment.approved else 'REJETÉ'}")
            print(f"  Justification: {assessment.justification}")
            print(f"  Score de risque: {assessment.risk_score:.2f}")
            print(f"  Sévérité: {assessment.severity.value}")
            print(f"  Blast radius: {assessment.blast_radius}")
            print(f"  Rollback: {assessment.rollback_capability.value}")
            print(f"  Approbation humaine requise: {assessment.requires_human_approval}")
            print(f"  Suggestions: {assessment.mitigation_suggestions}")
            
            # Tester un scénario à risque élevé
            print("\n✗ Test d'un scénario à risque élevé...")
            high_risk_context = DecisionContext(
                client_preferences={"risk_tolerance": "low"},
                business_value={"business_criticality": "high"},
                system_state={"pending_changes": 10},
                compliance_constraints={"rollback_required": False},
                decision_history=[],
                environment={"environment": "production"},
                intent={"type": "DATA_DELETION"},
                metadata={}
            )
            
            try:
                high_risk_assessment = await arbitrator.assess(automation_decision, high_risk_context)
                print(f"  Résultat: {'APPROUVÉ' if high_risk_assessment.approved else 'REJETÉ'}")
                if high_risk_assessment.approved:
                    print("  ERROR: Should have been rejected")
            except RiskRejectedError as e:
                print(f"  ✓ Correctement rejeté: {e}")
            
            # Générer un rapport
            print("\n✓ Génération d'un rapport détaillé...")
            report = arbitrator.get_risk_assessment_report(automation_decision, context)
            print(f"  Score: {report['risk_score']:.2f}")
            print(f"  Sévérité: {report['severity']}")
            print(f"  Environnement: {report['environment']}")
            print(f"  Approuvé: {report['approved']}")
            
            print("\n✓ Tous les tests passent")
            
        except Exception as e:
            print(f"✗ Erreur: {e}")
            import traceback
            traceback.print_exc()
    
    asyncio.run(test_risk_arbitrator())