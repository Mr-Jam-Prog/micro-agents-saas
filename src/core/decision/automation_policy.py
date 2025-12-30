"""
Automation Policy - Détermine le niveau d'automatisation basé sur la confiance, le risque et les préférences.
Politique basée sur des règles, extensible via configuration YAML.
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import logging
from enum import Enum

from ..types import DecisionContext, AutomationDecision, AutomationLevel

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """Niveaux de risque."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TrustLevel(str, Enum):
    """Niveaux de confiance du client."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class AutomationRule:
    """Règle d'automatisation."""
    min_confidence: float
    max_confidence: float
    risk_levels: List[RiskLevel]
    default_decision: AutomationLevel
    justification_template: str
    overrides: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AutomationPolicyConfig:
    """Configuration de la politique d'automatisation."""
    
    # Règles par défaut (appliquées dans l'ordre)
    default_rules: List[AutomationRule] = field(default_factory=lambda: [
        AutomationRule(
            min_confidence=0.0,
            max_confidence=0.3,
            risk_levels=[RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL],
            default_decision=AutomationLevel.INVESTIGATE,
            justification_template="Confidence too low ({confidence:.2f} < 0.3) for automation"
        ),
        AutomationRule(
            min_confidence=0.3,
            max_confidence=0.7,
            risk_levels=[RiskLevel.LOW, RiskLevel.MEDIUM],
            default_decision=AutomationLevel.RECOMMEND,
            justification_template="Medium confidence ({confidence:.2f}) with {risk_level} risk"
        ),
        AutomationRule(
            min_confidence=0.3,
            max_confidence=0.7,
            risk_levels=[RiskLevel.HIGH, RiskLevel.CRITICAL],
            default_decision=AutomationLevel.INVESTIGATE,
            justification_template="Medium confidence ({confidence:.2f}) with high risk ({risk_level})"
        ),
        AutomationRule(
            min_confidence=0.7,
            max_confidence=0.95,
            risk_levels=[RiskLevel.LOW, RiskLevel.MEDIUM],
            default_decision=AutomationLevel.AUTO_EXECUTE_WITH_MONITORING,
            justification_template="High confidence ({confidence:.2f}) with {risk_level} risk"
        ),
        AutomationRule(
            min_confidence=0.7,
            max_confidence=0.95,
            risk_levels=[RiskLevel.HIGH, RiskLevel.CRITICAL],
            default_decision=AutomationLevel.RECOMMEND,
            justification_template="High confidence ({confidence:.2f}) but high risk ({risk_level})"
        ),
        AutomationRule(
            min_confidence=0.95,
            max_confidence=1.0,
            risk_levels=[RiskLevel.LOW, RiskLevel.MEDIUM],
            default_decision=AutomationLevel.AUTO_EXECUTE,
            justification_template="Very high confidence ({confidence:.2f} >= 0.95) with {risk_level} risk"
        ),
        AutomationRule(
            min_confidence=0.95,
            max_confidence=1.0,
            risk_levels=[RiskLevel.HIGH],
            default_decision=AutomationLevel.AUTO_EXECUTE_WITH_MONITORING,
            justification_template="Very high confidence ({confidence:.2f} >= 0.95) but high risk"
        ),
        AutomationRule(
            min_confidence=0.95,
            max_confidence=1.0,
            risk_levels=[RiskLevel.CRITICAL],
            default_decision=AutomationLevel.RECOMMEND,
            justification_template="Very high confidence ({confidence:.2f} >= 0.95) but critical risk"
        )
    ])
    
    # Overrides par niveau de confiance client
    client_trust_overrides: Dict[TrustLevel, Dict[str, Any]] = field(default_factory=lambda: {
        TrustLevel.LOW: {
            "downgrade_levels": {
                AutomationLevel.AUTO_EXECUTE: AutomationLevel.AUTO_EXECUTE_WITH_MONITORING,
                AutomationLevel.AUTO_EXECUTE_WITH_MONITORING: AutomationLevel.RECOMMEND,
                AutomationLevel.RECOMMEND: AutomationLevel.INVESTIGATE
            },
            "justification_addition": " (downgraded due to low client trust)"
        },
        TrustLevel.MEDIUM: {
            "downgrade_levels": {
                AutomationLevel.AUTO_EXECUTE: AutomationLevel.AUTO_EXECUTE_WITH_MONITORING
            },
            "justification_addition": " (slightly downgraded due to medium client trust)"
        }
    })
    
    # Règles spécifiques par type d'intention
    intent_specific_rules: Dict[str, List[AutomationRule]] = field(default_factory=lambda: {
        "SECURITY_RISK_REDUCTION": [
            AutomationRule(
                min_confidence=0.0,
                max_confidence=1.0,
                risk_levels=[RiskLevel.CRITICAL],
                default_decision=AutomationLevel.RECOMMEND,
                justification_template="Security-critical operations require recommendation only"
            )
        ],
        "COMPLIANCE_ENFORCEMENT": [
            AutomationRule(
                min_confidence=0.0,
                max_confidence=0.9,
                risk_levels=[RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL],
                default_decision=AutomationLevel.RECOMMEND,
                justification_template="Compliance operations require high confidence (>0.9) for automation"
            )
        ]
    })
    
    # Contraintes d'environnement
    environment_constraints: Dict[str, List[AutomationLevel]] = field(default_factory=lambda: {
        "production": [AutomationLevel.AUTO_EXECUTE_WITH_MONITORING, AutomationLevel.RECOMMEND, AutomationLevel.INVESTIGATE],
        "staging": [AutomationLevel.AUTO_EXECUTE, AutomationLevel.AUTO_EXECUTE_WITH_MONITORING, AutomationLevel.RECOMMEND, AutomationLevel.INVESTIGATE],
        "development": [AutomationLevel.AUTO_EXECUTE, AutomationLevel.AUTO_EXECUTE_WITH_MONITORING, AutomationLevel.RECOMMEND, AutomationLevel.INVESTIGATE],
        "sandbox": [AutomationLevel.AUTO_EXECUTE, AutomationLevel.AUTO_EXECUTE_WITH_MONITORING, AutomationLevel.RECOMMEND, AutomationLevel.INVESTIGATE]
    })


class AutomationPolicy:
    """
    Politique d'automatisation basée sur des règles.
    
    Responsabilités:
    1. Déterminer le niveau d'automatisation basé sur confiance, risque, préférences
    2. Appliquer les overrides selon la confiance client
    3. Respecter les contraintes d'environnement
    4. Fournir une justification détaillée
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialise la politique d'automatisation.
        
        Args:
            config_path: Chemin vers fichier YAML de configuration.
        """
        if config_path:
            self.config = self._load_config_from_file(config_path)
        else:
            self.config = AutomationPolicyConfig()
        
        self.logger = logger
        
        # Valider la configuration
        self._validate_config()
        
        self.logger.info(
            "automation_policy_initialized",
            rule_count=len(self.config.default_rules),
            intent_specific_rules=len(self.config.intent_specific_rules)
        )
    
    def _load_config_from_file(self, config_path: str) -> AutomationPolicyConfig:
        """Charge la configuration depuis un fichier YAML."""
        path = Path(config_path)
        if not path.exists():
            self.logger.warning(f"Config file not found: {config_path}, using defaults")
            return AutomationPolicyConfig()
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config_dict = yaml.safe_load(f)
            
            # Convertir le dictionnaire en AutomationPolicyConfig
            return AutomationPolicyConfig(**config_dict)
            
        except Exception as e:
            self.logger.error(f"Failed to load config from {config_path}: {e}")
            return AutomationPolicyConfig()
    
    def _validate_config(self) -> None:
        """Valide la configuration."""
        errors = []
        
        # Vérifier que les règles couvrent toute la plage 0-1
        coverage = self._check_confidence_coverage()
        if not coverage["complete"]:
            errors.append(f"Confidence coverage gaps: {coverage['gaps']}")
        
        # Vérifier les valeurs de confiance
        for i, rule in enumerate(self.config.default_rules):
            if not 0 <= rule.min_confidence <= 1:
                errors.append(f"Rule {i}: min_confidence {rule.min_confidence} not in [0,1]")
            if not 0 <= rule.max_confidence <= 1:
                errors.append(f"Rule {i}: max_confidence {rule.max_confidence} not in [0,1]")
            if rule.min_confidence >= rule.max_confidence:
                errors.append(f"Rule {i}: min_confidence >= max_confidence")
        
        if errors:
            error_msg = f"Configuration validation failed: {'; '.join(errors)}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
    
    def _check_confidence_coverage(self) -> Dict[str, Any]:
        """Vérifie la couverture de confiance de 0 à 1."""
        sorted_rules = sorted(self.config.default_rules, key=lambda r: r.min_confidence)
        
        gaps = []
        current_max = 0.0
        
        for rule in sorted_rules:
            if rule.min_confidence > current_max + 1e-9:  # Petite tolérance
                gaps.append((current_max, rule.min_confidence))
            current_max = max(current_max, rule.max_confidence)
        
        # Vérifier la couverture jusqu'à 1.0
        if current_max < 1.0 - 1e-9:
            gaps.append((current_max, 1.0))
        
        return {
            "complete": len(gaps) == 0,
            "gaps": gaps
        }
    
    async def decide(
        self, 
        confidence_score: float,
        context: DecisionContext
    ) -> AutomationDecision:
        """
        Détermine le niveau d'automatisation.
        
        Args:
            confidence_score: Score de confiance (0.0-1.0)
            context: Contexte décisionnel
            
        Returns:
            AutomationDecision: Décision avec justification
        """
        try:
            self.logger.info(
                "automation_decision_started",
                confidence_score=confidence_score,
                context_id=id(context)
            )
            
            # Valider les entrées
            self._validate_inputs(confidence_score, context)
            
            # 1. Déterminer le niveau de risque
            risk_level = self._determine_risk_level(context)
            
            # 2. Obtenir la décision de base
            base_decision, base_justification = self._get_base_decision(
                confidence_score, 
                risk_level,
                context
            )
            
            # 3. Appliquer les overrides client
            final_decision, final_justification = self._apply_client_overrides(
                base_decision, 
                base_justification,
                context
            )
            
            # 4. Appliquer les règles spécifiques à l'intention
            final_decision, final_justification = self._apply_intent_specific_rules(
                final_decision, 
                final_justification,
                confidence_score,
                risk_level,
                context
            )
            
            # 5. Appliquer les contraintes d'environnement
            final_decision, final_justification = self._apply_environment_constraints(
                final_decision, 
                final_justification,
                context
            )
            
            # Créer la décision finale
            decision = AutomationDecision(
                level=final_decision,
                justification=final_justification,
                metadata={
                    "confidence_score": confidence_score,
                    "risk_level": risk_level.value,
                    "base_decision": base_decision.value,
                    "client_trust_level": self._get_client_trust_level(context).value,
                    "environment": context.environment.get("environment", "unknown") if context.environment else "unknown",
                    "intent_type": context.intent.get("type") if context.intent else "unknown"
                }
            )
            
            self.logger.info(
                "automation_decision_made",
                final_decision=final_decision.value,
                confidence_score=confidence_score,
                risk_level=risk_level.value
            )
            
            return decision
            
        except Exception as e:
            self.logger.error(
                "automation_decision_failed",
                error=str(e),
                error_type=e.__class__.__name__
            )
            # En cas d'erreur, retourner la décision la plus conservative
            return AutomationDecision(
                level=AutomationLevel.INVESTIGATE,
                justification=f"Error in automation policy: {str(e)}",
                metadata={"error": True}
            )
    
    def _validate_inputs(self, confidence_score: float, context: DecisionContext) -> None:
        """Valide les entrées."""
        if not 0 <= confidence_score <= 1:
            raise ValueError(f"Confidence score must be between 0 and 1, got {confidence_score}")
        
        if not context:
            raise ValueError("Context is required")
    
    def _determine_risk_level(self, context: DecisionContext) -> RiskLevel:
        """Détermine le niveau de risque à partir du contexte."""
        # Récupérer le niveau de risque du contexte client
        client_prefs = context.client_preferences or {}
        risk_tolerance = client_prefs.get("risk_tolerance", "medium")
        
        # Mapper tolerance -> risk level
        tolerance_to_risk = {
            "high": RiskLevel.LOW,     # Haute tolérance = faible risque
            "medium": RiskLevel.MEDIUM,
            "low": RiskLevel.HIGH,      # Faible tolérance = risque élevé
            "none": RiskLevel.CRITICAL  # Aucune tolérance = risque critique
        }
        
        base_risk = tolerance_to_risk.get(risk_tolerance, RiskLevel.MEDIUM)
        
        # Ajuster basé sur le contexte business
        business_value = context.business_value or {}
        criticality = business_value.get("business_criticality", "medium")
        
        if criticality == "high":
            if base_risk == RiskLevel.LOW:
                base_risk = RiskLevel.MEDIUM
            elif base_risk == RiskLevel.MEDIUM:
                base_risk = RiskLevel.HIGH
            # HIGH et CRITICAL restent inchangés
        
        # Ajuster basé sur l'environnement
        environment = context.environment or {}
        env = environment.get("environment", "production")
        
        if env == "production" and base_risk == RiskLevel.LOW:
            # En production, même avec haute tolérance, on prend des précautions
            base_risk = RiskLevel.MEDIUM
        
        return base_risk
    
    def _get_base_decision(
        self, 
        confidence_score: float,
        risk_level: RiskLevel,
        context: DecisionContext
    ) -> Tuple[AutomationLevel, str]:
        """Obtient la décision de base basée sur confiance et risque."""
        # Chercher la première règle qui correspond
        for rule in self.config.default_rules:
            if (rule.min_confidence <= confidence_score <= rule.max_confidence and
                risk_level in rule.risk_levels):
                
                justification = rule.justification_template.format(
                    confidence=confidence_score,
                    risk_level=risk_level.value
                )
                return rule.default_decision, justification
        
        # Aucune règle trouvée (normalement impossible avec validation)
        self.logger.warning(
            "no_matching_rule",
            confidence_score=confidence_score,
            risk_level=risk_level.value
        )
        
        # Fallback conservatif
        return AutomationLevel.INVESTIGATE, f"No matching rule found (confidence: {confidence_score:.2f}, risk: {risk_level.value})"
    
    def _get_client_trust_level(self, context: DecisionContext) -> TrustLevel:
        """Détermine le niveau de confiance du client."""
        client_prefs = context.client_preferences or {}
        
        # Vérifier l'historique des décisions
        history = context.decision_history or []
        recent_successes = sum(1 for h in history[-10:] if h.get("success", False))
        total_recent = min(10, len(history))
        
        if total_recent > 0:
            success_rate = recent_successes / total_recent
        else:
            success_rate = 1.0  # Pas d'historique = on fait confiance
        
        # Vérifier les préférences explicites
        explicit_trust = client_prefs.get("trust_level")
        if explicit_trust:
            try:
                return TrustLevel(explicit_trust.lower())
            except ValueError:
                pass
        
        # Déterminer basé sur le taux de succès
        if success_rate >= 0.9:
            return TrustLevel.HIGH
        elif success_rate >= 0.7:
            return TrustLevel.MEDIUM
        else:
            return TrustLevel.LOW
    
    def _apply_client_overrides(
        self,
        base_decision: AutomationLevel,
        base_justification: str,
        context: DecisionContext
    ) -> Tuple[AutomationLevel, str]:
        """Applique les overrides basés sur la confiance client."""
        trust_level = self._get_client_trust_level(context)
        
        override_config = self.config.client_trust_overrides.get(trust_level)
        if not override_config:
            return base_decision, base_justification
        
        downgrade_levels = override_config.get("downgrade_levels", {})
        
        if base_decision in downgrade_levels:
            new_decision = downgrade_levels[base_decision]
            justification_addition = override_config.get("justification_addition", "")
            
            return new_decision, base_justification + justification_addition
        
        return base_decision, base_justification
    
    def _apply_intent_specific_rules(
        self,
        current_decision: AutomationLevel,
        current_justification: str,
        confidence_score: float,
        risk_level: RiskLevel,
        context: DecisionContext
    ) -> Tuple[AutomationLevel, str]:
        """Applique les règles spécifiques à l'intention."""
        intent = context.intent or {}
        intent_type = intent.get("type")
        
        if not intent_type or intent_type not in self.config.intent_specific_rules:
            return current_decision, current_justification
        
        rules = self.config.intent_specific_rules[intent_type]
        
        for rule in rules:
            if (rule.min_confidence <= confidence_score <= rule.max_confidence and
                risk_level in rule.risk_levels):
                
                justification = rule.justification_template.format(
                    confidence=confidence_score,
                    risk_level=risk_level.value,
                    intent_type=intent_type
                )
                return rule.default_decision, justification
        
        return current_decision, current_justification
    
    def _apply_environment_constraints(
        self,
        current_decision: AutomationLevel,
        current_justification: str,
        context: DecisionContext
    ) -> Tuple[AutomationLevel, str]:
        """Applique les contraintes de l'environnement."""
        environment = context.environment or {}
        env = environment.get("environment", "production")
        
        allowed_levels = self.config.environment_constraints.get(env, [])
        
        if not allowed_levels:
            self.logger.warning(f"No constraints defined for environment: {env}")
            return current_decision, current_justification
        
        if current_decision in allowed_levels:
            return current_decision, current_justification
        
        # Trouver le niveau autorisé le plus proche (plus conservatif)
        decision_order = [
            AutomationLevel.INVESTIGATE,
            AutomationLevel.RECOMMEND,
            AutomationLevel.AUTO_EXECUTE_WITH_MONITORING,
            AutomationLevel.AUTO_EXECUTE
        ]
        
        current_idx = decision_order.index(current_decision)
        
        # Chercher un niveau autorisé plus conservatif
        for i in range(current_idx, -1, -1):
            if decision_order[i] in allowed_levels:
                return decision_order[i], f"{current_justification} (downgraded for {env} environment)"
        
        # Si aucun niveau plus conservatif n'est trouvé, utiliser le plus conservatif autorisé
        most_conservative = min(
            allowed_levels, 
            key=lambda l: decision_order.index(l) if l in decision_order else len(decision_order)
        )
        
        return most_conservative, f"Environment {env} requires {most_conservative.value}"
    
    def get_allowed_decisions(
        self, 
        confidence_score: float,
        context: DecisionContext
    ) -> List[AutomationLevel]:
        """Retourne tous les niveaux d'automatisation possibles pour le contexte."""
        risk_level = self._determine_risk_level(context)
        trust_level = self._get_client_trust_level(context)
        
        # Récupérer les décisions de base pour ce risque/confiance
        base_decisions = []
        for rule in self.config.default_rules:
            if (rule.min_confidence <= confidence_score <= rule.max_confidence and
                risk_level in rule.risk_levels):
                base_decisions.append(rule.default_decision)
        
        # Appliquer les overrides client
        override_config = self.config.client_trust_overrides.get(trust_level, {})
        downgrade_levels = override_config.get("downgrade_levels", {})
        
        final_decisions = set()
        for decision in base_decisions:
            if decision in downgrade_levels:
                final_decisions.add(downgrade_levels[decision])
            else:
                final_decisions.add(decision)
        
        # Filtrer par environnement
        environment = context.environment or {}
        env = environment.get("environment", "production")
        allowed_env_levels = self.config.environment_constraints.get(env, [])
        
        if allowed_env_levels:
            final_decisions = [d for d in final_decisions if d in allowed_env_levels]
        
        return list(final_decisions)
    
    def explain_decision(
        self, 
        confidence_score: float,
        context: DecisionContext
    ) -> Dict[str, Any]:
        """Fournit une explication détaillée du processus décisionnel."""
        risk_level = self._determine_risk_level(context)
        trust_level = self._get_client_trust_level(context)
        
        explanation = {
            "confidence_score": confidence_score,
            "risk_level": risk_level.value,
            "client_trust_level": trust_level.value,
            "environment": context.environment.get("environment", "unknown") if context.environment else "unknown",
            "intent_type": context.intent.get("type") if context.intent else "unknown",
            "steps": []
        }
        
        # Étape 1: Décision de base
        base_decision, base_justification = self._get_base_decision(confidence_score, risk_level, context)
        explanation["steps"].append({
            "step": "base_decision",
            "decision": base_decision.value,
            "justification": base_justification
        })
        
        # Étape 2: Overrides client
        override_config = self.config.client_trust_overrides.get(trust_level, {})
        if override_config and base_decision in override_config.get("downgrade_levels", {}):
            new_decision = override_config["downgrade_levels"][base_decision]
            explanation["steps"].append({
                "step": "client_override",
                "original_decision": base_decision.value,
                "new_decision": new_decision.value,
                "reason": f"Client trust level: {trust_level.value}"
            })
            base_decision = new_decision
        
        # Étape 3: Règles spécifiques à l'intention
        intent = context.intent or {}
        intent_type = intent.get("type")
        if intent_type and intent_type in self.config.intent_specific_rules:
            rules = self.config.intent_specific_rules[intent_type]
            for rule in rules:
                if (rule.min_confidence <= confidence_score <= rule.max_confidence and
                    risk_level in rule.risk_levels):
                    explanation["steps"].append({
                        "step": "intent_specific_rule",
                        "decision": rule.default_decision.value,
                        "reason": f"Intent type: {intent_type}",
                        "justification": rule.justification_template.format(
                            confidence=confidence_score,
                            risk_level=risk_level.value,
                            intent_type=intent_type
                        )
                    })
                    base_decision = rule.default_decision
        
        # Étape 4: Contraintes d'environnement
        environment = context.environment or {}
        env = environment.get("environment", "production")
        allowed_levels = self.config.environment_constraints.get(env, [])
        
        if allowed_levels and base_decision not in allowed_levels:
            # Trouver le niveau autorisé le plus proche
            decision_order = [
                AutomationLevel.INVESTIGATE,
                AutomationLevel.RECOMMEND,
                AutomationLevel.AUTO_EXECUTE_WITH_MONITORING,
                AutomationLevel.AUTO_EXECUTE
            ]
            
            for i in range(decision_order.index(base_decision), -1, -1):
                if decision_order[i] in allowed_levels:
                    explanation["steps"].append({
                        "step": "environment_constraint",
                        "original_decision": base_decision.value,
                        "new_decision": decision_order[i].value,
                        "reason": f"Environment: {env}",
                        "allowed_levels": [l.value for l in allowed_levels]
                    })
                    base_decision = decision_order[i]
                    break
        
        explanation["final_decision"] = base_decision.value
        return explanation
    
    def update_config(self, config: AutomationPolicyConfig) -> None:
        """Met à jour la configuration."""
        self.config = config
        self._validate_config()
        self.logger.info("Automation policy configuration updated")
    
    def get_config_summary(self) -> Dict[str, Any]:
        """Retourne un résumé de la configuration."""
        return {
            "default_rule_count": len(self.config.default_rules),
            "client_trust_overrides": list(self.config.client_trust_overrides.keys()),
            "intent_specific_rules": list(self.config.intent_specific_rules.keys()),
            "environment_constraints": list(self.config.environment_constraints.keys())
        }


# Singleton pour utilisation facile
_automation_policy_instance = None

def get_automation_policy(config_path: Optional[str] = None) -> AutomationPolicy:
    """Obtient l'instance singleton du AutomationPolicy."""
    global _automation_policy_instance
    if _automation_policy_instance is None:
        _automation_policy_instance = AutomationPolicy(config_path)
    return _automation_policy_instance


# Fonction utilitaire pour décision rapide
async def decide_automation(
    confidence_score: float,
    context: DecisionContext,
    config_path: Optional[str] = None
) -> AutomationDecision:
    """
    Fonction utilitaire pour décider du niveau d'automatisation.
    
    Args:
        confidence_score: Score de confiance
        context: Contexte décisionnel
        config_path: Chemin vers configuration YAML
        
    Returns:
        AutomationDecision: Décision d'automatisation
    """
    policy = AutomationPolicy(config_path)
    return await policy.decide(confidence_score, context)


# Tests unitaires table-driven
TEST_CASES = [
    # (confidence, risk_tolerance, business_criticality, expected_decision)
    (0.2, "high", "medium", AutomationLevel.INVESTIGATE),
    (0.4, "medium", "medium", AutomationLevel.RECOMMEND),
    (0.4, "low", "medium", AutomationLevel.INVESTIGATE),
    (0.8, "high", "medium", AutomationLevel.AUTO_EXECUTE_WITH_MONITORING),
    (0.8, "medium", "high", AutomationLevel.RECOMMEND),
    (0.96, "high", "medium", AutomationLevel.AUTO_EXECUTE),
    (0.96, "medium", "medium", AutomationLevel.AUTO_EXECUTE_WITH_MONITORING),
    (0.96, "low", "medium", AutomationLevel.RECOMMEND),
]

if __name__ == "__main__":
    import asyncio
    
    async def run_table_tests():
        """Exécute les tests table-driven."""
        from ..types import DecisionContext
        
        print("Running table-driven tests...")
        print("=" * 80)
        
        policy = AutomationPolicy()
        all_passed = True
        
        for i, (confidence, risk_tolerance, business_criticality, expected) in enumerate(TEST_CASES, 1):
            # Créer un contexte de test
            context = DecisionContext(
                client_preferences={
                    "automation_preference": "high",
                    "risk_tolerance": risk_tolerance
                },
                business_value={"business_criticality": business_criticality},
                system_state={},
                compliance_constraints={},
                decision_history=[],
                environment={"environment": "staging"},
                intent={"type": "COST_OPTIMIZATION"},
                metadata={}
            )
            
            try:
                decision = await policy.decide(confidence, context)
                
                if decision.level == expected:
                    print(f"✓ Test {i}: PASS")
                    print(f"  Confidence: {confidence:.2f}, Risk: {risk_tolerance}, "
                          f"Criticality: {business_criticality}")
                    print(f"  Decision: {decision.level.value} (expected: {expected.value})")
                else:
                    print(f"✗ Test {i}: FAIL")
                    print(f"  Confidence: {confidence:.2f}, Risk: {risk_tolerance}, "
                          f"Criticality: {business_criticality}")
                    print(f"  Got: {decision.level.value}, Expected: {expected.value}")
                    print(f"  Justification: {decision.justification}")
                    all_passed = False
                
                print()
                
            except Exception as e:
                print(f"✗ Test {i}: ERROR - {e}")
                all_passed = False
                print()
        
        # Test des overrides client
        print("Testing client trust overrides...")
        print("-" * 40)
        
        low_trust_context = DecisionContext(
            client_preferences={"risk_tolerance": "high", "trust_level": "low"},
            business_value={"business_criticality": "medium"},
            system_state={},
            compliance_constraints={},
            decision_history=[],
            environment={"environment": "staging"},
            intent={"type": "COST_OPTIMIZATION"},
            metadata={}
        )
        
        decision = await policy.decide(0.96, low_trust_context)
        expected = AutomationLevel.AUTO_EXECUTE_WITH_MONITORING  # Downgraded from AUTO_EXECUTE
        
        if decision.level == expected:
            print(f"✓ Client override test: PASS")
            print(f"  Low trust downgraded AUTO_EXECUTE to AUTO_EXECUTE_WITH_MONITORING")
        else:
            print(f"✗ Client override test: FAIL")
            print(f"  Got: {decision.level.value}, Expected: {expected.value}")
            all_passed = False
        
        print()
        
        if all_passed:
            print("✓ All tests passed!")
        else:
            print("✗ Some tests failed")
        
        # Afficher un exemple d'explication
        print("\nExample decision explanation:")
        print("-" * 40)
        
        example_context = DecisionContext(
            client_preferences={"risk_tolerance": "medium"},
            business_value={"business_criticality": "high"},
            system_state={},
            compliance_constraints={},
            decision_history=[],
            environment={"environment": "production"},
            intent={"type": "SECURITY_RISK_REDUCTION"},
            metadata={}
        )
        
        explanation = policy.explain_decision(0.8, example_context)
        for step in explanation["steps"]:
            print(f"{step['step']}: {step.get('decision', 'N/A')} - {step.get('justification', step.get('reason', 'N/A'))}")
        print(f"Final decision: {explanation['final_decision']}")
    
    asyncio.run(run_table_tests())