"""
Module des détecteurs MicroAgents - 400+ agents de détection organisés par domaine

Organisation des détecteurs:
1. Anomalies de coût (20+ patterns)
2. Menaces de sécurité (50+ patterns)
3. Dégradation de performance (30+ patterns)
4. Violations de compliance (40+ patterns)
5. Gaspillage de ressources (60+ patterns)
6. Anti-patterns d'architecture (50+ patterns)
7. Problèmes de qualité de données (30+ patterns)
8. Risques de dépendances (20+ patterns)
9. Lacunes de planification de capacité (30+ patterns)
10. Excellence opérationnelle (70+ patterns)
"""

from __future__ import annotations

import abc
import asyncio
import datetime
import json
import logging
import re
import statistics
import warnings
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple, Union, Callable, Set
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field, validator

from src.core.base.agent import BaseAgent
from src.core.base.context import AgentContext
from src.core.base.result import AgentResult
from src.utils.serialization.serializers import JSONSerializer


class DetectionSeverity(Enum):
    """Sévérité d'une détection"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DetectionCategory(Enum):
    """Catégorie de détection"""
    COST_ANOMALY = "cost_anomaly"
    SECURITY_THREAT = "security_threat"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    COMPLIANCE_VIOLATION = "compliance_violation"
    RESOURCE_WASTE = "resource_waste"
    ARCHITECTURE_ANTIPATTERN = "architecture_antipattern"
    DATA_QUALITY_ISSUE = "data_quality_issue"
    DEPENDENCY_RISK = "dependency_risk"
    CAPACITY_GAP = "capacity_gap"
    OPERATIONAL_EXCELLENCE = "operational_excellence"


@dataclass
class DetectionResult:
    """Résultat d'une détection"""
    detected: bool
    confidence: float  # 0.0 - 1.0
    severity: DetectionSeverity
    category: DetectionCategory
    pattern_name: str
    description: str
    evidence: Dict[str, Any]
    explanation: str
    remediation_suggestions: List[str]
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)
    false_positive_risk: float = 0.0  # 0.0 - 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            "detected": self.detected,
            "confidence": self.confidence,
            "severity": self.severity.value,
            "category": self.category.value,
            "pattern_name": self.pattern_name,
            "description": self.description,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "remediation_suggestions": self.remediation_suggestions,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
            "false_positive_risk": self.false_positive_risk
        }


class DetectorConfig(BaseModel):
    """Configuration d'un détecteur"""
    enabled: bool = True
    sensitivity: float = 0.7  # 0.0 - 1.0
    learning_rate: float = 0.1
    min_confidence: float = 0.6
    max_false_positive_rate: float = 0.2
    detection_window_hours: int = 24
    alert_threshold: float = 0.8
    auto_learn: bool = True
    store_detections: bool = True
    historical_data_days: int = 30
    
    @validator('sensitivity', 'learning_rate', 'min_confidence', 'max_false_positive_rate', 'alert_threshold')
    def validate_probability(cls, v, field):
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{field.name} must be between 0.0 and 1.0")
        return v


class BaseDetectorAgent(BaseAgent, abc.ABC):
    """
    Agent de détection de base avec fonctionnalités communes
    
    Caractéristiques:
    - Logique de détection claire
    - Scoring de confiance
    - Minimisation des faux positifs
    - Capacités d'auto-apprentissage
    - Génération d'explications
    - Suggestions de remediation
    """
    
    def __init__(self, config: Optional[DetectorConfig] = None):
        super().__init__()
        self.config = config or DetectorConfig()
        self.detection_history: List[DetectionResult] = []
        self.false_positive_history: List[DetectionResult] = []
        self.learning_model: Optional[Any] = None
        self.pattern_library: Dict[str, Any] = {}
        self.metrics = {
            "detections_count": 0,
            "false_positives_count": 0,
            "true_positives_count": 0,
            "average_confidence": 0.0,
            "learning_iterations": 0
        }
        
        # Initialise le modèle d'apprentissage si activé
        if self.config.auto_learn:
            self._init_learning_model()
    
    def _init_learning_model(self):
        """Initialise le modèle d'auto-apprentissage"""
        # Modèle simple de régression logistique pour l'auto-apprentissage
        # Dans une implémentation réelle, on utiliserait scikit-learn ou TensorFlow
        self.learning_model = {
            "weights": {},
            "bias": 0.0,
            "learning_rate": self.config.learning_rate,
            "features": []
        }
    
    @abc.abstractmethod
    async def detect(self, context: AgentContext) -> DetectionResult:
        """
        Méthode principale de détection à implémenter
        
        Args:
            context: Contexte d'exécution avec données d'entrée
            
        Returns:
            DetectionResult: Résultat de la détection
        """
        pass
    
    async def execute(self, context: AgentContext) -> AgentResult:
        """
        Point d'entrée principal de l'agent
        
        Args:
            context: Contexte d'exécution
            
        Returns:
            AgentResult: Résultat formaté
        """
        try:
            # Exécute la détection
            detection = await self.detect(context)
            
            # Met à jour les métriques
            self.metrics["detections_count"] += 1
            self.metrics["average_confidence"] = (
                (self.metrics["average_confidence"] * (self.metrics["detections_count"] - 1) + 
                 detection.confidence) / self.metrics["detections_count"]
            )
            
            # Apprentissage si détection
            if detection.detected and self.config.auto_learn and self.learning_model:
                await self._learn_from_detection(detection, context)
            
            # Stocke la détection si configuré
            if self.config.store_detections:
                self.detection_history.append(detection)
                # Limite l'historique
                if len(self.detection_history) > 1000:
                    self.detection_history = self.detection_history[-1000:]
            
            # Formatte le résultat
            result_data = detection.to_dict()
            
            return AgentResult(
                success=True,
                data=result_data,
                message=f"Détection '{detection.pattern_name}' - "
                       f"{detection.severity.value.upper()} "
                       f"(Confidence: {detection.confidence:.2%})",
                metadata={
                    "detection": detection.to_dict(),
                    "metrics": self.metrics,
                    "agent_type": self.__class__.__name__
                }
            )
            
        except Exception as e:
            logging.error(f"Détection failed: {e}", exc_info=True)
            return AgentResult(
                success=False,
                data={},
                message=f"Détection failed: {str(e)}",
                error=str(e)
            )
    
    async def _learn_from_detection(self, detection: DetectionResult, context: AgentContext):
        """
        Apprentissage à partir d'une détection
        
        Args:
            detection: Résultat de détection
            context: Contexte original
        """
        try:
            # Extrait les features du contexte
            features = self._extract_features(context)
            
            # Mise à jour du modèle simple (exemple)
            # Dans une implémentation réelle, on utiliserait un vrai modèle ML
            if features:
                # Stocke les features pour analyse
                if "features" not in self.learning_model:
                    self.learning_model["features"] = []
                self.learning_model["features"].append({
                    "timestamp": datetime.datetime.now().isoformat(),
                    "features": features,
                    "detection": detection.to_dict()
                })
                
                self.metrics["learning_iterations"] += 1
            
        except Exception as e:
            logging.warning(f"Learning failed: {e}")
    
    def _extract_features(self, context: AgentContext) -> Dict[str, Any]:
        """
        Extrait les features du contexte pour l'apprentissage
        
        Args:
            context: Contexte d'exécution
            
        Returns:
            Dict[str, Any]: Features extraites
        """
        features = {}
        
        # Exemple d'extraction de features basiques
        for key, value in context.data.items():
            if isinstance(value, (int, float)):
                features[f"numeric_{key}"] = value
            elif isinstance(value, str):
                features[f"str_len_{key}"] = len(value)
            elif isinstance(value, list):
                features[f"list_len_{key}"] = len(value)
            elif isinstance(value, dict):
                features[f"dict_keys_{key}"] = len(value.keys())
        
        return features
    
    def calculate_confidence(
        self, 
        evidence_strength: float, 
        historical_accuracy: float = 0.8,
        data_quality: float = 1.0
    ) -> float:
        """
        Calcule la confiance de détection
        
        Args:
            evidence_strength: Force des preuves (0-1)
            historical_accuracy: Précision historique du pattern (0-1)
            data_quality: Qualité des données d'entrée (0-1)
            
        Returns:
            float: Score de confiance (0-1)
        """
        # Formule de confiance pondérée
        confidence = (
            evidence_strength * 0.5 +
            historical_accuracy * 0.3 +
            data_quality * 0.2
        )
        
        # Ajuste avec l'expérience de l'agent
        experience_factor = min(1.0, self.metrics["detections_count"] / 1000)
        confidence = confidence * (0.7 + 0.3 * experience_factor)
        
        return min(1.0, max(0.0, confidence))
    
    def estimate_false_positive_risk(
        self,
        confidence: float,
        pattern_complexity: float,
        data_ambiguity: float
    ) -> float:
        """
        Estime le risque de faux positif
        
        Args:
            confidence: Confiance de détection
            pattern_complexity: Complexité du pattern (0-1)
            data_ambiguity: Ambiguïté des données (0-1)
            
        Returns:
            float: Risque de faux positif (0-1)
        """
        # Formule basique de risque de faux positif
        base_risk = (1 - confidence) * 0.6
        complexity_risk = pattern_complexity * 0.3
        ambiguity_risk = data_ambiguity * 0.1
        
        risk = base_risk + complexity_risk + ambiguity_risk
        
        # Réduit le risque avec l'expérience
        experience_reduction = min(0.5, self.metrics["true_positives_count"] / 100)
        risk = max(0.0, risk - experience_reduction)
        
        return risk
    
    def generate_explanation(
        self,
        pattern_name: str,
        evidence: Dict[str, Any],
        context: Dict[str, Any]
    ) -> str:
        """
        Génère une explication humaine de la détection
        
        Args:
            pattern_name: Nom du pattern détecté
            evidence: Preuves de la détection
            context: Contexte supplémentaire
            
        Returns:
            str: Explication formatée
        """
        explanations = {
            "cost_spike": "Détection d'une augmentation anormale des coûts dépassant le seuil défini",
            "security_breach": "Pattern d'accès suspect détecté correspondant à une menace connue",
            "performance_degradation": "Baisse significative des performances par rapport à la baseline",
            "compliance_violation": "Configuration non conforme aux standards réglementaires",
            "resource_waste": "Utilisation inefficace des ressources identifiée",
            "architecture_antipattern": "Pattern de conception contraire aux meilleures pratiques",
            "data_anomaly": "Anomalie détectée dans les données, possible erreur ou fraude",
            "dependency_vulnerability": "Vulnérabilité identifiée dans les dépendances",
            "capacity_overflow": "Utilisation des ressources approchant les limites critiques",
            "operational_risk": "Risque opérationnel identifié nécessitant une attention"
        }
        
        base_explanation = explanations.get(
            pattern_name, 
            f"Pattern '{pattern_name}' détecté basé sur les preuves disponibles"
        )
        
        # Ajoute des détails spécifiques
        details = []
        for key, value in evidence.items():
            if isinstance(value, (int, float)):
                details.append(f"{key}: {value}")
            elif isinstance(value, str) and len(value) < 50:
                details.append(f"{key}: '{value}'")
        
        if details:
            base_explanation += f" ({'; '.join(details)})"
        
        return base_explanation
    
    def get_remediation_suggestions(
        self,
        category: DetectionCategory,
        severity: DetectionSeverity,
        context: Dict[str, Any]
    ) -> List[str]:
        """
        Génère des suggestions de remediation basées sur la catégorie et sévérité
        
        Args:
            category: Catégorie de détection
            severity: Sévérité de la détection
            context: Contexte de la détection
            
        Returns:
            List[str]: Suggestions de remediation
        """
        suggestions = []
        
        # Suggestions par catégorie
        category_suggestions = {
            DetectionCategory.COST_ANOMALY: [
                "Analyser les facteurs de coût récents",
                "Vérifier les configurations de ressources",
                "Implémenter des alertes de budget",
                "Optimiser les instances sous-utilisées",
                "Négocier les tarifs avec le fournisseur cloud"
            ],
            DetectionCategory.SECURITY_THREAT: [
                "Revoir les logs d'accès immédiatement",
                "Mettre à jour les règles de sécurité",
                "Changer les credentials compromis",
                "Isoler les ressources affectées",
                "Auditer les permissions IAM"
            ],
            DetectionCategory.PERFORMANCE_DEGRADATION: [
                "Scaler les ressources temporairement",
                "Optimiser les requêtes de base de données",
                "Mettre en cache les résultats fréquents",
                "Revoir l'architecture de l'application",
                "Implémenter du monitoring avancé"
            ],
            DetectionCategory.COMPLIANCE_VIOLATION: [
                "Documenter l'écart de compliance",
                "Créer un plan de correction",
                "Former les équipes concernées",
                "Automatiser les vérifications",
                "Mettre à jour les politiques"
            ],
            DetectionCategory.RESOURCE_WASTE: [
                "Downscaler les instances surdimensionnées",
                "Supprimer les ressources orphelines",
                "Optimiser l'allocation mémoire",
                "Automatiser l'arrêt des ressources inactives",
                "Implémenter des quotas par équipe"
            ],
            DetectionCategory.ARCHITECTURE_ANTIPATTERN: [
                "Refactoriser le code problématique",
                "Documenter les décisions d'architecture",
                "Former l'équipe sur les meilleures pratiques",
                "Implémenter des revues de code",
                "Automatiser les détections d'anti-patterns"
            ],
            DetectionCategory.DATA_QUALITY_ISSUE: [
                "Implémenter des validations de données",
                "Nettoyer les données corrompues",
                "Documenter les règles de qualité",
                "Automatiser les tests de données",
                "Créer des dashboards de qualité"
            ],
            DetectionCategory.DEPENDENCY_RISK: [
                "Mettre à jour les dépendances vulnérables",
                "Auditer les licences des dépendances",
                "Diversifier les fournisseurs critiques",
                "Documenter les dépendances critiques",
                "Implémenter des tests de régression"
            ],
            DetectionCategory.CAPACITY_GAP: [
                "Planifier le scaling proactif",
                "Optimiser l'utilisation des ressources",
                "Implémenter des alertes de capacité",
                "Documenter les besoins futurs",
                "Automatiser le provisioning"
            ],
            DetectionCategory.OPERATIONAL_EXCELLENCE: [
                "Documenter les procédures opérationnelles",
                "Automatiser les tâches manuelles",
                "Implémenter des métriques SLO/SLI",
                "Former les équipes sur les incidents",
                "Améliorer la documentation runbook"
            ]
        }
        
        # Ajoute les suggestions de catégorie
        suggestions.extend(category_suggestions.get(category, []))
        
        # Suggestions par sévérité
        severity_multiplier = {
            DetectionSeverity.LOW: 1,
            DetectionSeverity.MEDIUM: 2,
            DetectionSeverity.HIGH: 3,
            DetectionSeverity.CRITICAL: 4
        }
        
        # Limite le nombre de suggestions basé sur la sévérité
        max_suggestions = severity_multiplier.get(severity, 2)
        suggestions = suggestions[:max_suggestions]
        
        return suggestions
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """
        Retourne les métriques de performance du détecteur
        
        Returns:
            Dict[str, Any]: Métriques de performance
        """
        accuracy = 0.0
        if self.metrics["detections_count"] > 0:
            accuracy = (
                self.metrics["true_positives_count"] / 
                self.metrics["detections_count"]
            )
        
        false_positive_rate = 0.0
        if self.metrics["detections_count"] > 0:
            false_positive_rate = (
                self.metrics["false_positives_count"] / 
                self.metrics["detections_count"]
            )
        
        return {
            **self.metrics,
            "accuracy": accuracy,
            "false_positive_rate": false_positive_rate,
            "detection_history_size": len(self.detection_history),
            "config": self.config.dict()
        }


# ============================================================================
# DÉTECTEURS D'ANOMALIES DE COÛT (20+ patterns)
# ============================================================================

class CostAnomalyDetector(BaseDetectorAgent):
    """Détecteur d'anomalies de coût - Pattern 1"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Augmentation soudaine des coûts
        current_cost = data.get("current_cost", 0)
        historical_avg = data.get("historical_average", 0)
        threshold = data.get("threshold", 1.5)  # 50% d'augmentation par défaut
        
        evidence_strength = 0.0
        evidence = {}
        
        if current_cost > historical_avg * threshold:
            evidence_strength = min(1.0, (current_cost / historical_avg) / threshold)
            evidence = {
                "current_cost": current_cost,
                "historical_average": historical_avg,
                "increase_ratio": current_cost / historical_avg,
                "threshold": threshold
            }
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.7,
            confidence=confidence,
            severity=DetectionSeverity.HIGH if evidence_strength > 0.8 else DetectionSeverity.MEDIUM,
            category=DetectionCategory.COST_ANOMALY,
            pattern_name="cost_spike",
            description="Augmentation soudaine des coûts cloud détectée",
            evidence=evidence,
            explanation=self.generate_explanation("cost_spike", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.COST_ANOMALY,
                DetectionSeverity.HIGH if evidence_strength > 0.8 else DetectionSeverity.MEDIUM,
                context.data
            )
        )


class IdleResourceDetector(BaseDetectorAgent):
    """Détection de ressources inactives - Pattern 2"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Ressources avec utilisation faible
        cpu_utilization = data.get("cpu_utilization", 0)
        memory_utilization = data.get("memory_utilization", 0)
        cost_per_hour = data.get("cost_per_hour", 0)
        
        idle_threshold = 0.1  # 10% d'utilisation
        evidence_strength = 0.0
        evidence = {}
        
        if cpu_utilization < idle_threshold and memory_utilization < idle_threshold:
            idle_score = (1 - cpu_utilization) * (1 - memory_utilization)
            evidence_strength = idle_score
            evidence = {
                "cpu_utilization": cpu_utilization,
                "memory_utilization": memory_utilization,
                "cost_per_hour": cost_per_hour,
                "idle_score": idle_score
            }
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.6,
            confidence=confidence,
            severity=DetectionSeverity.MEDIUM,
            category=DetectionCategory.RESOURCE_WASTE,
            pattern_name="idle_resource",
            description="Ressource cloud sous-utilisée détectée",
            evidence=evidence,
            explanation=self.generate_explanation("idle_resource", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.RESOURCE_WASTE,
                DetectionSeverity.MEDIUM,
                context.data
            )
        )


class ReservedInstanceWasteDetector(BaseDetectorAgent):
    """Détection de gaspillage d'instances réservées - Pattern 3"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Instances réservées sous-utilisées
        reserved_count = data.get("reserved_instances", 0)
        used_count = data.get("used_reserved_instances", 0)
        savings_lost = data.get("potential_savings_lost", 0)
        
        utilization_rate = used_count / reserved_count if reserved_count > 0 else 1.0
        waste_threshold = 0.7  # Moins de 70% d'utilisation
        
        evidence_strength = 0.0
        evidence = {}
        
        if utilization_rate < waste_threshold:
            evidence_strength = 1 - utilization_rate
            evidence = {
                "reserved_instances": reserved_count,
                "used_reserved_instances": used_count,
                "utilization_rate": utilization_rate,
                "savings_lost": savings_lost
            }
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.5,
            confidence=confidence,
            severity=DetectionSeverity.MEDIUM,
            category=DetectionCategory.COST_ANOMALY,
            pattern_name="reserved_instance_waste",
            description="Gaspillage d'instances réservées détecté",
            evidence=evidence,
            explanation=self.generate_explanation("reserved_instance_waste", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.COST_ANOMALY,
                DetectionSeverity.MEDIUM,
                context.data
            )
        )


# ============================================================================
# DÉTECTEURS DE MENACES DE SÉCURITÉ (50+ patterns)
# ============================================================================

class UnusualAccessPatternDetector(BaseDetectorAgent):
    """Détection de patterns d'accès inhabituels - Pattern 4"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Accès depuis des IPs inhabituelles
        source_ip = data.get("source_ip", "")
        usual_ips = set(data.get("usual_ips", []))
        access_time = data.get("access_time", "")
        
        evidence_strength = 0.0
        evidence = {}
        
        if source_ip not in usual_ips:
            # Vérifie si c'est une heure inhabituelle
            hour = datetime.datetime.fromisoformat(access_time).hour if access_time else 12
            unusual_hour = hour < 6 or hour > 22  # Accès nocturne
            
            evidence_strength = 0.8 if unusual_hour else 0.6
            evidence = {
                "source_ip": source_ip,
                "access_time": access_time,
                "unusual_ip": True,
                "unusual_hour": unusual_hour
            }
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.5,
            confidence=confidence,
            severity=DetectionSeverity.HIGH,
            category=DetectionCategory.SECURITY_THREAT,
            pattern_name="unusual_access",
            description="Pattern d'accès inhabituel détecté",
            evidence=evidence,
            explanation=self.generate_explanation("security_breach", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.SECURITY_THREAT,
                DetectionSeverity.HIGH,
                context.data
            )
        )


class IAMPolicyViolationDetector(BaseDetectorAgent):
    """Détection de violations de politiques IAM - Pattern 5"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Permissions IAM excessives
        permissions = data.get("permissions", [])
        recommended_permissions = set(data.get("recommended_permissions", []))
        
        excessive_permissions = []
        for perm in permissions:
            if perm not in recommended_permissions:
                excessive_permissions.append(perm)
        
        evidence_strength = 0.0
        evidence = {}
        
        if excessive_permissions:
            excess_ratio = len(excessive_permissions) / len(permissions) if permissions else 0
            evidence_strength = min(1.0, excess_ratio * 2)  # Normalise
            evidence = {
                "total_permissions": len(permissions),
                "excessive_permissions": excessive_permissions,
                "excess_ratio": excess_ratio
            }
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.4,
            confidence=confidence,
            severity=DetectionSeverity.MEDIUM,
            category=DetectionCategory.SECURITY_THREAT,
            pattern_name="iam_policy_violation",
            description="Permissions IAM excessives détectées",
            evidence=evidence,
            explanation=self.generate_explanation("compliance_violation", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.SECURITY_THREAT,
                DetectionSeverity.MEDIUM,
                context.data
            )
        )


class VulnerabilityScanDetector(BaseDetectorAgent):
    """Détection de vulnérabilités connues - Pattern 6"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Vulnérabilités CVE connues
        vulnerabilities = data.get("vulnerabilities", [])
        severity_scores = {
            "CRITICAL": 1.0,
            "HIGH": 0.8,
            "MEDIUM": 0.5,
            "LOW": 0.2
        }
        
        evidence_strength = 0.0
        evidence = {}
        
        if vulnerabilities:
            # Calcule le score moyen de sévérité
            scores = []
            critical_vulns = []
            
            for vuln in vulnerabilities:
                score = severity_scores.get(vuln.get("severity", "LOW"), 0.2)
                scores.append(score)
                
                if vuln.get("severity") == "CRITICAL":
                    critical_vulns.append(vuln.get("id", "unknown"))
            
            avg_score = statistics.mean(scores) if scores else 0
            evidence_strength = avg_score
            
            evidence = {
                "total_vulnerabilities": len(vulnerabilities),
                "critical_vulnerabilities": critical_vulns,
                "average_severity_score": avg_score
            }
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.3,
            confidence=confidence,
            severity=DetectionSeverity.CRITICAL if evidence_strength > 0.7 else DetectionSeverity.HIGH,
            category=DetectionCategory.SECURITY_THREAT,
            pattern_name="vulnerability_scan",
            description="Vulnérabilités de sécurité détectées",
            evidence=evidence,
            explanation=self.generate_explanation("security_breach", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.SECURITY_THREAT,
                DetectionSeverity.CRITICAL if evidence_strength > 0.7 else DetectionSeverity.HIGH,
                context.data
            )
        )


# ============================================================================
# DÉTECTEURS DE DÉGRADATION DE PERFORMANCE (30+ patterns)
# ============================================================================

class LatencySpikeDetector(BaseDetectorAgent):
    """Détection de pics de latence - Pattern 7"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Augmentation soudaine de la latence
        current_latency = data.get("current_latency", 0)
        baseline_latency = data.get("baseline_latency", 0)
        latency_threshold = data.get("threshold", 2.0)  # 200% par défaut
        
        evidence_strength = 0.0
        evidence = {}
        
        if current_latency > baseline_latency * latency_threshold:
            increase_ratio = current_latency / baseline_latency
            evidence_strength = min(1.0, (increase_ratio - 1) / 2)  # Normalise
            evidence = {
                "current_latency": current_latency,
                "baseline_latency": baseline_latency,
                "increase_ratio": increase_ratio,
                "threshold": latency_threshold
            }
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.5,
            confidence=confidence,
            severity=DetectionSeverity.HIGH if evidence_strength > 0.8 else DetectionSeverity.MEDIUM,
            category=DetectionCategory.PERFORMANCE_DEGRADATION,
            pattern_name="latency_spike",
            description="Pic de latence détecté",
            evidence=evidence,
            explanation=self.generate_explanation("performance_degradation", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.PERFORMANCE_DEGRADATION,
                DetectionSeverity.HIGH if evidence_strength > 0.8 else DetectionSeverity.MEDIUM,
                context.data
            )
        )


class MemoryLeakDetector(BaseDetectorAgent):
    """Détection de fuites mémoire - Pattern 8"""
    
    async def detect(self, context: AgentContext) -> DetectionResult:
        data = context.data
        
        # Pattern: Croissance continue de l'utilisation mémoire
        memory_usage = data.get("memory_usage", [])
        time_points = data.get("time_points", [])
        
        evidence_strength = 0.0
        evidence = {}
        
        if len(memory_usage) >= 5:  # Au moins 5 points de données
            # Calcule la tendance
            try:
                x = np.array(range(len(memory_usage)))
                y = np.array(memory_usage)
                
                # Régression linéaire
                coefficients = np.polyfit(x, y, 1)
                slope = coefficients[0]
                
                # Si pente positive significative
                if slope > 0.1:  # Croissance de >0.1% par intervalle
                    correlation = np.corrcoef(x, y)[0, 1]
                    evidence_strength = abs(correlation) * (slope / 0.5)  # Normalise
                    evidence_strength = min(1.0, evidence_strength)
                    
                    evidence = {
                        "memory_slope": slope,
                        "correlation": correlation,
                        "data_points": len(memory_usage),
                        "max_memory": max(memory_usage) if memory_usage else 0
                    }
            except:
                pass
        
        confidence = self.calculate_confidence(evidence_strength)
        
        return DetectionResult(
            detected=evidence_strength > 0.6,
            confidence=confidence,
            severity=DetectionSeverity.HIGH,
            category=DetectionCategory.PERFORMANCE_DEGRADATION,
            pattern_name="memory_leak",
            description="Fuite mémoire potentielle détectée",
            evidence=evidence,
            explanation=self.generate_explanation("performance_degradation", evidence, context.data),
            remediation_suggestions=self.get_remediation_suggestions(
                DetectionCategory.PERFORMANCE_DEGRADATION,
                DetectionSeverity.HIGH,
                context.data
            )
        )


# ============================================================================
# FACTORY ET REGISTRE DES DÉTECTEURS
# ============================================================================

class DetectorFactory:
    """Factory pour créer des instances de détecteurs"""
    
    _detector_registry: Dict[str, type] = {}
    
    @classmethod
    def register_detector(cls, name: str, detector_class: type):
        """Enregistre une classe de détecteur"""
        cls._detector_registry[name] = detector_class
    
    @classmethod
    def create_detector(cls, name: str, config: Optional[DetectorConfig] = None) -> BaseDetectorAgent:
        """Crée une instance de détecteur"""
        if name not in cls._detector_registry:
            raise ValueError(f"Détecteur '{name}' non enregistré")
        
        return cls._detector_registry[name](config)
    
    @classmethod
    def get_available_detectors(cls) -> List[str]:
        """Retourne la liste des détecteurs disponibles"""
        return list(cls._detector_registry.keys())
    
    @classmethod
    def get_detector_categories(cls) -> Dict[DetectionCategory, List[str]]:
        """Retourne les détecteurs organisés par catégorie"""
        categories = defaultdict(list)
        
        for name, detector_class in cls._detector_registry.items():
            # Détermine la catégorie à partir du nom de classe
            if "Cost" in detector_class.__name__:
                categories[DetectionCategory.COST_ANOMALY].append(name)
            elif "Security" in detector_class.__name__:
                categories[DetectionCategory.SECURITY_THREAT].append(name)
            elif "Performance" in detector_class.__name__:
                categories[DetectionCategory.PERFORMANCE_DEGRADATION].append(name)
            elif "Compliance" in detector_class.__name__:
                categories[DetectionCategory.COMPLIANCE_VIOLATION].append(name)
            else:
                categories[DetectionCategory.OPERATIONAL_EXCELLENCE].append(name)
        
        return dict(categories)


# Enregistrement des détecteurs
DetectorFactory.register_detector("cost_anomaly", CostAnomalyDetector)
DetectorFactory.register_detector("idle_resource", IdleResourceDetector)
DetectorFactory.register_detector("reserved_instance_waste", ReservedInstanceWasteDetector)
DetectorFactory.register_detector("unusual_access", UnusualAccessPatternDetector)
DetectorFactory.register_detector("iam_policy_violation", IAMPolicyViolationDetector)
DetectorFactory.register_detector("vulnerability_scan", VulnerabilityScanDetector)
DetectorFactory.register_detector("latency_spike", LatencySpikeDetector)
DetectorFactory.register_detector("memory_leak", MemoryLeakDetector)

# Template pour 400+ détecteurs - Les autres seraient implémentés de manière similaire
"""
# Exemples de détecteurs supplémentaires à implémenter:

class DataBreachDetector(BaseDetectorAgent):
    # Pattern: Fuite de données sensibles
    
class ComplianceAuditDetector(BaseDetectorAgent):
    # Pattern: Non-conformité GDPR/HIPAA
    
class ResourceExhaustionDetector(BaseDetectorAgent):
    # Pattern: Épuisement des ressources
    
class ArchitectureSmellDetector(BaseDetectorAgent):
    # Pattern: Anti-patterns d'architecture
    
class DependencyConflictDetector(BaseDetectorAgent):
    # Pattern: Conflits de dépendances
    
class CapacityPlanningDetector(BaseDetectorAgent):
    # Pattern: Lacunes de planification
    
class OperationalRiskDetector(BaseDetectorAgent):
    # Pattern: Risques opérationnels
"""

# Export des classes principales
__all__ = [
    # Classes de base
    'BaseDetectorAgent',
    'DetectionResult',
    'DetectionSeverity',
    'DetectionCategory',
    'DetectorConfig',
    
    # Détecteurs
    'CostAnomalyDetector',
    'IdleResourceDetector',
    'ReservedInstanceWasteDetector',
    'UnusualAccessPatternDetector',
    'IAMPolicyViolationDetector',
    'VulnerabilityScanDetector',
    'LatencySpikeDetector',
    'MemoryLeakDetector',
    
    # Factory
    'DetectorFactory',
]


# Exemple d'utilisation
if __name__ == "__main__":
    # Création et test d'un détecteur
    config = DetectorConfig(
        sensitivity=0.8,
        min_confidence=0.7,
        auto_learn=True
    )
    
    detector = DetectorFactory.create_detector("cost_anomaly", config)
    
    # Contexte de test
    context_data = {
        "current_cost": 1500.0,
        "historical_average": 800.0,
        "threshold": 1.5
    }
    
    import asyncio
    
    async def test_detector():
        context = AgentContext(data=context_data)
        result = await detector.execute(context)
        
        print(f"✅ Détection: {result.success}")
        print(f"📊 Confidence: {result.metadata['detection']['confidence']:.2%}")
        print(f"🚨 Sévérité: {result.metadata['detection']['severity']}")
        print(f"📝 Explication: {result.metadata['detection']['explanation']}")
        print(f"🔧 Suggestions: {result.metadata['detection']['remediation_suggestions']}")
    
    asyncio.run(test_detector())