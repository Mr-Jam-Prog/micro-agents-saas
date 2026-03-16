"""
Confidence Engine - Calcule la fiabilité décisionnelle
Score basé sur l'agrégation heuristique, pas une probabilité de succès.
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import logging
import numpy as np
from enum import Enum

from ..types import AgentChain, DecisionContext
from ..exceptions import LowConfidenceError

logger = logging.getLogger(__name__)


class PenaltyReason(Enum):
    """Raisons de pénalités de confiance."""
    MISSING_DATA = "missing_data"
    LONG_CHAIN = "long_chain"
    UNSTABLE_DEPENDENCIES = "unstable_dependencies"
    LOW_AGENT_HEALTH = "low_agent_health"
    HIGH_EXECUTION_TIME = "high_execution_time"
    LOW_SUCCESS_RATE = "low_success_rate"
    COMPATIBILITY_ISSUES = "compatibility_issues"
    ENVIRONMENT_RISK = "environment_risk"


@dataclass
class ConfidenceBreakdown:
    """Détail du calcul de confiance."""
    base_score: float
    penalties: Dict[PenaltyReason, float]
    adjustments: Dict[str, float]
    final_score: float
    explanations: List[str]


@dataclass
class ConfidenceConfig:
    """Configuration du moteur de confiance."""
    # Seuils et poids
    min_confidence_threshold: float = 0.1
    base_weights: Dict[str, float] = field(default_factory=lambda: {
        "agent_success_rate": 0.3,
        "data_completeness": 0.25,
        "agent_health": 0.2,
        "compatibility": 0.15,
        "environment_safety": 0.1
    })
    
    # Pénalités
    penalties: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "missing_data": {
            "threshold": 0.2,  # 20% de données manquantes
            "penalty_per_unit": 0.1,  # 0.1 par 10% manquants
            "max_penalty": 0.4
        },
        "long_chain": {
            "threshold": 3,  # plus de 3 agents
            "penalty_per_agent": 0.05,  # 0.05 par agent supplémentaire
            "max_penalty": 0.3
        },
        "unstable_dependencies": {
            "penalty_per_beta": 0.15,
            "penalty_per_alpha": 0.25,
            "max_penalty": 0.5
        },
        "low_health": {
            "degraded_penalty": 0.1,
            "unhealthy_penalty": 0.3,
            "unknown_penalty": 0.2
        },
        "high_execution_time": {
            "threshold_seconds": 300,  # 5 minutes
            "penalty_per_minute": 0.02,
            "max_penalty": 0.2
        }
    })
    
    # Ajustements positifs
    adjustments: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "high_success_history": {
            "threshold": 0.9,
            "adjustment": 0.1,
            "min_executions": 10
        },
        "proven_compatibility": {
            "adjustment": 0.15,
            "min_successful_runs": 5
        },
        "recent_success": {
            "adjustment": 0.08,
            "timeframe_hours": 24
        }
    })


class ConfidenceEngine:
    """
    Moteur de calcul de fiabilité décisionnelle.
    
    Responsabilités:
    1. Calculer un score de confiance entre 0.0 et 1.0
    2. Fournir un breakdown détaillé des facteurs
    3. Appliquer des pénalités et ajustements
    4. Lever LowConfidenceError si score < 0.1
    5. Support configuration YAML
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialise le ConfidenceEngine.
        
        Args:
            config_path: Chemin vers fichier YAML de configuration.
        """
        if config_path:
            self.config = self._load_config_from_file(config_path)
        else:
            self.config = ConfidenceConfig()
        
        self.logger = logger
        self._cache: Dict[str, Tuple[float, ConfidenceBreakdown]] = {}
        
        self.logger.info(
            "confidence_engine_initialized",
            min_threshold=self.config.min_confidence_threshold,
            base_weights=self.config.base_weights
        )
    
    def _load_config_from_file(self, config_path: str) -> ConfidenceConfig:
        """Charge la configuration depuis un fichier YAML."""
        path = Path(config_path)
        if not path.exists():
            self.logger.warning(f"Config file not found: {config_path}, using defaults")
            return ConfidenceConfig()
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config_dict = yaml.safe_load(f)
            
            # Convertir le dictionnaire en ConfidenceConfig
            return ConfidenceConfig(**config_dict)
            
        except Exception as e:
            self.logger.error(f"Failed to load config from {config_path}: {e}")
            return ConfidenceConfig()
    
    async def compute(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> float:
        """
        Calcule le score de confiance pour une chaîne d'agents.
        
        Args:
            chain: Chaîne d'agents à évaluer
            context: Contexte décisionnel
            
        Returns:
            float: Score de confiance entre 0.0 et 1.0
            
        Raises:
            LowConfidenceError: Si score < min_confidence_threshold
        """
        # Créer une clé de cache
        cache_key = self._create_cache_key(chain, context)
        
        if cache_key in self._cache:
            score, breakdown = self._cache[cache_key]
            self.logger.debug(f"Using cached confidence score: {score}")
            return score
        
        try:
            self.logger.info(
                "computing_confidence",
                chain_length=len(chain.agents),
                context_id=id(context)
            )
            
            # 1. Calculer le score de base
            base_score, base_explanations = self._calculate_base_score(chain, context)
            
            # 2. Appliquer les pénalités
            penalties, penalty_explanations = self._calculate_penalties(chain, context)
            total_penalty = sum(penalties.values())
            
            # 3. Appliquer les ajustements positifs
            adjustments, adjustment_explanations = self._calculate_adjustments(chain, context)
            total_adjustment = sum(adjustments.values())
            
            # 4. Calculer le score final
            final_score = base_score - total_penalty + total_adjustment
            final_score = max(0.0, min(1.0, final_score))  # Clamper entre 0 et 1
            
            # 5. Créer le breakdown
            breakdown = ConfidenceBreakdown(
                base_score=base_score,
                penalties=penalties,
                adjustments=adjustments,
                final_score=final_score,
                explanations=base_explanations + penalty_explanations + adjustment_explanations
            )
            
            # 6. Vérifier le seuil minimum
            if final_score < self.config.min_confidence_threshold:
                self.logger.warning(
                    "low_confidence_score",
                    score=final_score,
                    threshold=self.config.min_confidence_threshold,
                    breakdown=breakdown
                )
                raise LowConfidenceError(
                    f"Confidence score {final_score:.3f} below threshold "
                    f"{self.config.min_confidence_threshold}",
                    score=final_score,
                    breakdown=breakdown
                )
            
            # 7. Mettre en cache
            self._cache[cache_key] = (final_score, breakdown)
            
            self.logger.info(
                "confidence_calculated",
                score=final_score,
                base_score=base_score,
                total_penalty=total_penalty,
                total_adjustment=total_adjustment,
                chain_length=len(chain.agents)
            )
            
            return final_score
            
        except LowConfidenceError:
            raise
        except Exception as e:
            self.logger.error(f"Error computing confidence: {e}")
            # En cas d'erreur, retourner un score bas mais pas 0
            return 0.1  # Juste au-dessus du seuil minimum
    
    def _create_cache_key(self, chain: AgentChain, context: DecisionContext) -> str:
        """Crée une clé de cache unique pour la chaîne et le contexte."""
        # Utiliser les IDs d'agents et un hash du contexte
        agent_ids = "_".join(chain.agents)
        context_hash = hash(str(context.dict()))
        return f"{agent_ids}_{context_hash}"
    
    def _calculate_base_score(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> Tuple[float, List[str]]:
        """Calcule le score de base basé sur plusieurs facteurs."""
        explanations = []
        scores = {}
        
        # 1. Taux de succès moyen des agents
        success_score = self._calculate_success_rate_score(chain, context)
        scores["agent_success_rate"] = success_score
        explanations.append(f"Success rate score: {success_score:.3f}")
        
        # 2. Complétude des données
        data_score = self._calculate_data_completeness_score(context)
        scores["data_completeness"] = data_score
        explanations.append(f"Data completeness score: {data_score:.3f}")
        
        # 3. Santé des agents
        health_score = self._calculate_agent_health_score(chain, context)
        scores["agent_health"] = health_score
        explanations.append(f"Agent health score: {health_score:.3f}")
        
        # 4. Compatibilité
        compatibility_score = self._calculate_compatibility_score(chain, context)
        scores["compatibility"] = compatibility_score
        explanations.append(f"Compatibility score: {compatibility_score:.3f}")
        
        # 5. Sécurité de l'environnement
        environment_score = self._calculate_environment_safety_score(context)
        scores["environment_safety"] = environment_score
        explanations.append(f"Environment safety score: {environment_score:.3f}")
        
        # 6. Calcul du score pondéré
        weighted_score = 0.0
        for factor, weight in self.config.base_weights.items():
            if factor in scores:
                weighted_score += scores[factor] * weight
            else:
                self.logger.warning(f"Missing score for factor: {factor}")
        
        explanations.append(f"Weighted base score: {weighted_score:.3f}")
        return weighted_score, explanations
    
    def _calculate_success_rate_score(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> float:
        """Calcule le score basé sur le taux de succès historique des agents."""
        # Dans une implémentation réelle, on récupérerait les stats des agents
        # Pour l'exemple, on simule avec les métadonnées
        metadata = chain.metadata or {}
        
        # Si on a un taux de succès estimé dans les métadonnées
        if "estimated_success" in metadata:
            success_rate = metadata["estimated_success"]
        else:
            # Fallback: utiliser une estimation basée sur le nombre d'agents
            # Plus d'agents = plus de risques
            base_rate = 0.85
            length_penalty = 0.95 ** (len(chain.agents) - 1)
            success_rate = base_rate * length_penalty
        
        return max(0.0, min(1.0, success_rate))
    
    def _calculate_data_completeness_score(
        self, 
        context: DecisionContext
    ) -> float:
        """Calcule le score basé sur la complétude des données."""
        # Vérifier la présence de données critiques
        critical_fields = [
            ("client_preferences", "automation_preference"),
            ("business_value", "expected_roi"),
            ("system_state", "infrastructure_health"),
            ("compliance_constraints", "rollback_required")
        ]
        
        present_fields = 0
        for section, field in critical_fields:
            data = getattr(context, section, {})
            if field in data and data[field] not in [None, "", []]:
                present_fields += 1
        
        completeness = present_fields / len(critical_fields) if critical_fields else 1.0
        
        # Ajuster: données complètes = score élevé
        if completeness >= 0.9:
            return 1.0
        elif completeness >= 0.7:
            return 0.8
        elif completeness >= 0.5:
            return 0.6
        else:
            return 0.3
    
    def _calculate_agent_health_score(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> float:
        """Calcule le score basé sur la santé des agents."""
        # Dans une implémentation réelle, on vérifierait la santé des agents
        # Pour l'exemple, on utilise les métadonnées de la chaîne
        metadata = chain.metadata or {}
        
        if "health_score" in metadata:
            return metadata["health_score"]
        
        # Fallback: simulation
        base_health = 0.9
        
        # Pénalité pour chaîne longue
        if len(chain.agents) > 3:
            penalty = (len(chain.agents) - 3) * 0.05
            base_health -= penalty
        
        return max(0.5, base_health)  # Minimum 0.5
    
    def _calculate_compatibility_score(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> float:
        """Calcule le score basé sur la compatibilité des agents."""
        metadata = chain.metadata or {}
        
        if "compatibility_score" in metadata:
            return metadata["compatibility_score"]
        
        # Fallback: simulation basée sur la longueur de chaîne
        # Plus la chaîne est courte, meilleure est la compatibilité supposée
        if len(chain.agents) == 1:
            return 1.0
        elif len(chain.agents) == 2:
            return 0.9
        elif len(chain.agents) == 3:
            return 0.8
        else:
            return 0.6
    
    def _calculate_environment_safety_score(
        self, 
        context: DecisionContext
    ) -> float:
        """Calcule le score basé sur la sécurité de l'environnement."""
        environment = context.environment or {}
        env_name = environment.get("environment", "unknown")
        
        # Score basé sur l'environnement
        env_scores = {
            "production": 0.7,  # Production = risque mais stable
            "staging": 0.9,     # Staging = plus sûr pour les tests
            "development": 0.95, # Dev = très sûr
            "testing": 0.9,
            "sandbox": 1.0
        }
        
        return env_scores.get(env_name, 0.5)
    
    def _calculate_penalties(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> Tuple[Dict[PenaltyReason, float], List[str]]:
        """Calcule les pénalités à appliquer."""
        penalties = {}
        explanations = []
        
        # 1. Pénalité pour données manquantes
        missing_data_penalty = self._calculate_missing_data_penalty(context)
        if missing_data_penalty > 0:
            penalties[PenaltyReason.MISSING_DATA] = missing_data_penalty
            explanations.append(f"Missing data penalty: {missing_data_penalty:.3f}")
        
        # 2. Pénalité pour chaîne longue
        long_chain_penalty = self._calculate_long_chain_penalty(chain)
        if long_chain_penalty > 0:
            penalties[PenaltyReason.LONG_CHAIN] = long_chain_penalty
            explanations.append(f"Long chain penalty: {long_chain_penalty:.3f}")
        
        # 3. Pénalité pour dépendances instables
        unstable_deps_penalty = self._calculate_unstable_deps_penalty(chain)
        if unstable_deps_penalty > 0:
            penalties[PenaltyReason.UNSTABLE_DEPENDENCIES] = unstable_deps_penalty
            explanations.append(f"Unstable dependencies penalty: {unstable_deps_penalty:.3f}")
        
        # 4. Pénalité pour santé faible des agents
        low_health_penalty = self._calculate_low_health_penalty(chain)
        if low_health_penalty > 0:
            penalties[PenaltyReason.LOW_AGENT_HEALTH] = low_health_penalty
            explanations.append(f"Low agent health penalty: {low_health_penalty:.3f}")
        
        # 5. Pénalité pour temps d'exécution élevé
        exec_time_penalty = self._calculate_execution_time_penalty(chain)
        if exec_time_penalty > 0:
            penalties[PenaltyReason.HIGH_EXECUTION_TIME] = exec_time_penalty
            explanations.append(f"High execution time penalty: {exec_time_penalty:.3f}")
        
        return penalties, explanations
    
    def _calculate_missing_data_penalty(self, context: DecisionContext) -> float:
        """Calcule la pénalité pour données manquantes."""
        penalty_config = self.config.penalties.get("missing_data", {})
        
        # Calculer le pourcentage de données manquantes
        expected_fields = [
            "client_preferences", "business_value", 
            "system_state", "compliance_constraints"
        ]
        
        missing_count = 0
        for field in expected_fields:
            data = getattr(context, field, {})
            if not data:  # Dictionnaire vide
                missing_count += 1
        
        missing_ratio = missing_count / len(expected_fields)
        
        if missing_ratio > penalty_config.get("threshold", 0.2):
            penalty = (missing_ratio - penalty_config["threshold"]) * penalty_config.get("penalty_per_unit", 0.1) * 10
            return min(penalty, penalty_config.get("max_penalty", 0.4))
        
        return 0.0
    
    def _calculate_long_chain_penalty(self, chain: AgentChain) -> float:
        """Calcule la pénalité pour chaîne trop longue."""
        penalty_config = self.config.penalties.get("long_chain", {})
        threshold = penalty_config.get("threshold", 3)
        
        if len(chain.agents) > threshold:
            extra_agents = len(chain.agents) - threshold
            penalty = extra_agents * penalty_config.get("penalty_per_agent", 0.05)
            return min(penalty, penalty_config.get("max_penalty", 0.3))
        
        return 0.0
    
    def _calculate_unstable_deps_penalty(self, chain: AgentChain) -> float:
        """Calcule la pénalité pour dépendances instables."""
        penalty_config = self.config.penalties.get("unstable_dependencies", {})
        
        # Dans une implémentation réelle, on vérifierait les versions des agents
        # Pour l'exemple, on simule
        beta_count = 0
        alpha_count = 0
        
        # Simulation: premier agent en bêta si chaîne longue
        if len(chain.agents) > 2:
            beta_count = 1
        
        penalty = (
            beta_count * penalty_config.get("penalty_per_beta", 0.15) +
            alpha_count * penalty_config.get("penalty_per_alpha", 0.25)
        )
        
        return min(penalty, penalty_config.get("max_penalty", 0.5))
    
    def _calculate_low_health_penalty(self, chain: AgentChain) -> float:
        """Calcule la pénalité pour santé faible des agents."""
        penalty_config = self.config.penalties.get("low_health", {})
        
        # Simulation: pénalité basée sur la longueur de chaîne
        # Plus la chaîne est longue, plus on suppose de problèmes de santé
        if len(chain.agents) > 3:
            return penalty_config.get("degraded_penalty", 0.1)
        elif len(chain.agents) > 5:
            return penalty_config.get("unhealthy_penalty", 0.3)
        
        return 0.0
    
    def _calculate_execution_time_penalty(self, chain: AgentChain) -> float:
        """Calcule la pénalité pour temps d'exécution élevé."""
        penalty_config = self.config.penalties.get("high_execution_time", {})
        
        # Estimation du temps total
        # Dans une implémentation réelle, on aurait les temps d'exécution moyens
        estimated_time = len(chain.agents) * 60  # 60 secondes par agent
        
        threshold = penalty_config.get("threshold_seconds", 300)
        if estimated_time > threshold:
            extra_minutes = (estimated_time - threshold) / 60
            penalty = extra_minutes * penalty_config.get("penalty_per_minute", 0.02)
            return min(penalty, penalty_config.get("max_penalty", 0.2))
        
        return 0.0
    
    def _calculate_adjustments(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> Tuple[Dict[str, float], List[str]]:
        """Calcule les ajustements positifs."""
        adjustments = {}
        explanations = []
        
        # 1. Ajustement pour historique de succès élevé
        success_adj = self._calculate_high_success_adjustment(chain)
        if success_adj > 0:
            adjustments["high_success_history"] = success_adj
            explanations.append(f"High success history adjustment: {success_adj:.3f}")
        
        # 2. Ajustement pour compatibilité prouvée
        proven_adj = self._calculate_proven_compatibility_adjustment(chain)
        if proven_adj > 0:
            adjustments["proven_compatibility"] = proven_adj
            explanations.append(f"Proven compatibility adjustment: {proven_adj:.3f}")
        
        # 3. Ajustement pour succès récent
        recent_adj = self._calculate_recent_success_adjustment(chain)
        if recent_adj > 0:
            adjustments["recent_success"] = recent_adj
            explanations.append(f"Recent success adjustment: {recent_adj:.3f}")
        
        return adjustments, explanations
    
    def _calculate_high_success_adjustment(self, chain: AgentChain) -> float:
        """Calcule l'ajustement pour historique de succès élevé."""
        adj_config = self.config.adjustments.get("high_success_history", {})
        
        metadata = chain.metadata or {}
        success_rate = metadata.get("estimated_success", 0.5)
        
        if success_rate >= adj_config.get("threshold", 0.9):
            return adj_config.get("adjustment", 0.1)
        
        return 0.0
    
    def _calculate_proven_compatibility_adjustment(self, chain: AgentChain) -> float:
        """Calcule l'ajustement pour compatibilité prouvée."""
        adj_config = self.config.adjustments.get("proven_compatibility", {})
        
        metadata = chain.metadata or {}
        compatibility = metadata.get("compatibility_score", 0.5)
        
        if compatibility >= 0.9:
            return adj_config.get("adjustment", 0.15)
        
        return 0.0
    
    def _calculate_recent_success_adjustment(self, chain: AgentChain) -> float:
        """Calcule l'ajustement pour succès récent."""
        adj_config = self.config.adjustments.get("recent_success", {})
        
        # Dans une implémentation réelle, on vérifierait l'historique récent
        # Pour l'exemple, on donne un petit ajustement si la chaîne est courte
        if len(chain.agents) <= 2:
            return adj_config.get("adjustment", 0.08)
        
        return 0.0
    
    def get_confidence_breakdown(
        self, 
        chain: AgentChain, 
        context: DecisionContext
    ) -> Optional[ConfidenceBreakdown]:
        """Récupère le breakdown de confiance pour une chaîne."""
        cache_key = self._create_cache_key(chain, context)
        if cache_key in self._cache:
            _, breakdown = self._cache[cache_key]
            return breakdown
        return None
    
    def clear_cache(self) -> None:
        """Vide le cache de confiance."""
        self._cache.clear()
        self.logger.info("Confidence cache cleared")
    
    def update_config(self, config: ConfidenceConfig) -> None:
        """Met à jour la configuration du moteur."""
        self.config = config
        self.clear_cache()
        self.logger.info("Confidence configuration updated")
    
    def get_config_summary(self) -> Dict[str, Any]:
        """Retourne un résumé de la configuration."""
        return {
            "min_confidence_threshold": self.config.min_confidence_threshold,
            "base_weights": self.config.base_weights,
            "penalties": {
                reason: config 
                for reason, config in self.config.penalties.items()
            },
            "cache_size": len(self._cache)
        }


# Singleton pour utilisation facile
_confidence_engine_instance = None

def get_confidence_engine(config_path: Optional[str] = None) -> ConfidenceEngine:
    """Obtient l'instance singleton du ConfidenceEngine."""
    global _confidence_engine_instance
    if _confidence_engine_instance is None:
        _confidence_engine_instance = ConfidenceEngine(config_path)
    return _confidence_engine_instance


# Fonction utilitaire pour calcul rapide
async def compute_confidence(
    chain: AgentChain, 
    context: DecisionContext,
    config_path: Optional[str] = None
) -> float:
    """
    Fonction utilitaire pour calculer la confiance.
    
    Args:
        chain: Chaîne d'agents
        context: Contexte décisionnel
        config_path: Chemin vers configuration YAML
        
    Returns:
        float: Score de confiance
    """
    engine = ConfidenceEngine(config_path)
    return await engine.compute(chain, context)


# Tests unitaires intégrés
if __name__ == "__main__":
    import asyncio
    
    async def test_confidence_engine():
        """Test basique du ConfidenceEngine."""
        from ..types import AgentChain, DecisionContext
        
        # Créer une chaîne de test
        chain = AgentChain(
            agents=["agent1", "agent2", "agent3"],
            expected_value=2.5,
            metadata={
                "estimated_success": 0.8,
                "compatibility_score": 0.9,
                "health_score": 0.85,
                "selection_timestamp": "2024-01-01T00:00:00"
            }
        )
        
        # Créer un contexte de test
        context = DecisionContext(
            client_preferences={
                "automation_preference": "high",
                "budget_constraints": {"monthly_limit": 5000}
            },
            business_value={"expected_roi": 2.5},
            system_state={"infrastructure_health": "healthy"},
            compliance_constraints={"rollback_required": True},
            decision_history=[],
            environment={"environment": "staging"},
            intent={"type": "COST_OPTIMIZATION", "priority": 8},
            metadata={}
        )
        
        # Tester le moteur
        engine = ConfidenceEngine()
        
        try:
            score = await engine.compute(chain, context)
            print(f"✓ Score de confiance: {score:.3f}")
            
            # Récupérer le breakdown
            breakdown = engine.get_confidence_breakdown(chain, context)
            if breakdown:
                print(f"  Score de base: {breakdown.base_score:.3f}")
                print(f"  Pénalités totales: {sum(breakdown.penalties.values()):.3f}")
                print(f"  Ajustements totaux: {sum(breakdown.adjustments.values()):.3f}")
                print(f"  Explications: {breakdown.explanations}")
            
            # Tester avec un score bas
            print("\n✗ Test avec score bas...")
            low_score_chain = AgentChain(
                agents=["agent1", "agent2", "agent3", "agent4", "agent5"],
                expected_value=1.0,
                metadata={"estimated_success": 0.1}
            )
            
            try:
                low_score = await engine.compute(low_score_chain, context)
                print(f"  Score: {low_score:.3f}")
            except LowConfidenceError as e:
                print(f"  ✓ Correctement rejeté: {e}")
            
            # Tester la configuration
            config_summary = engine.get_config_summary()
            print(f"\n✓ Configuration chargée:")
            print(f"  Seuil minimum: {config_summary['min_confidence_threshold']}")
            print(f"  Poids de base: {config_summary['base_weights']}")
            
            print("\n✓ Tous les tests passent")
            
        except Exception as e:
            print(f"✗ Erreur: {e}")
    
    asyncio.run(test_confidence_engine())