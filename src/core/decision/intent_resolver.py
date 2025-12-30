"""
Intent Resolver - Transforme les entrées en intentions canoniques
Utilise des règles légères et extensibles sans ML lourd.
"""

import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
from dataclasses import dataclass
import logging
from enum import Enum

from ..types import Intent

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """Types d'intentions canoniques."""
    COST_OPTIMIZATION = "COST_OPTIMIZATION"
    INCIDENT_REDUCTION = "INCIDENT_REDUCTION"
    SECURITY_RISK_REDUCTION = "SECURITY_RISK_REDUCTION"
    PERFORMANCE_IMPROVEMENT = "PERFORMANCE_IMPROVEMENT"
    COMPLIANCE_ENFORCEMENT = "COMPLIANCE_ENFORCEMENT"
    CAPACITY_PLANNING = "CAPACITY_PLANNING"
    BACKUP_RECOVERY = "BACKUP_RECOVERY"
    DISASTER_RECOVERY = "DISASTER_RECOVERY"
    AUTOMATION_REQUEST = "AUTOMATION_REQUEST"
    REPORT_GENERATION = "REPORT_GENERATION"
    UNKNOWN = "UNKNOWN"


@dataclass
class IntentPattern:
    """Modèle pour un pattern d'intention."""
    intent_type: IntentType
    priority: int
    success_metrics: Dict[str, str]
    text_patterns: List[str]  # regex patterns
    event_patterns: Dict[str, Any]  # clé -> valeur attendue
    exact_matches: List[str]  # correspondances exactes
    case_sensitive: bool = False


class IntentResolver:
    """
    Résolveur d'intention basé sur des règles et regex.
    Extensible via fichiers YAML/JSON.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialise le résolveur avec configuration.
        
        Args:
            config_path: Chemin vers fichier YAML/JSON de configuration.
                        Si None, utilise la configuration par défaut.
        """
        self.patterns: Dict[IntentType, IntentPattern] = {}
        self.fallback_intent = Intent(
            type=IntentType.UNKNOWN,
            priority=5,
            success_metrics={}
        )
        
        if config_path:
            self._load_config_from_file(config_path)
        else:
            self._load_default_config()
    
    def _load_default_config(self):
        """Charge la configuration par défaut."""
        default_config = {
            "intents": [
                {
                    "type": "COST_OPTIMIZATION",
                    "priority": 8,
                    "success_metrics": {"cost_reduction": ">20%", "timeframe": "<30d"},
                    "text_patterns": [
                        r".*(coût|facture|trop cher|économiser|réduction|budget|dépenses?|aws bill|cost).*",
                        r".*(too high|expensive|spend|optimize cost).*",
                    ],
                    "event_patterns": {
                        "event_type": ["cost_alert", "budget_exceeded", "anomaly_detected"],
                        "severity": ["high", "critical"]
                    },
                    "exact_matches": ["high_cost_alert", "budget_exceeded"],
                    "case_sensitive": False
                },
                {
                    "type": "INCIDENT_REDUCTION",
                    "priority": 9,
                    "success_metrics": {"downtime": "<5min", "mttr": "<10min"},
                    "text_patterns": [
                        r".*(incident|panne|erreur|down|indisponible|problème|issue|bug).*",
                        r".*(outage|failure|crash|hang|unresponsive).*",
                    ],
                    "event_patterns": {
                        "event_type": ["incident", "alert", "monitoring"],
                        "status": ["firing", "critical"]
                    },
                    "exact_matches": ["production_incident", "service_down"],
                    "case_sensitive": False
                },
                {
                    "type": "SECURITY_RISK_REDUCTION",
                    "priority": 10,
                    "success_metrics": {"response_time": "<10min", "containment": "100%"},
                    "text_patterns": [
                        r".*(sécurité|security|vulnérabilité|vulnerability|attaque|attack|malware|ransomware).*",
                        r".*(breach|intrusion|threat|compromise|exploit).*",
                    ],
                    "event_patterns": {
                        "event_type": ["security_alert", "vulnerability", "threat_detected"],
                        "severity": ["critical", "high"]
                    },
                    "exact_matches": ["security_incident", "vulnerability_scan"],
                    "case_sensitive": False
                },
                {
                    "type": "PERFORMANCE_IMPROVEMENT",
                    "priority": 7,
                    "success_metrics": {"latency": "<100ms", "throughput": ">1000rps"},
                    "text_patterns": [
                        r".*(performance|slow|latence|latency|rapide|fast|optimisation|optimization).*",
                        r".*(bottleneck|throughput|response time|load time).*",
                    ],
                    "event_patterns": {
                        "event_type": ["performance_alert", "slow_query", "high_latency"]
                    },
                    "exact_matches": ["performance_degradation", "slow_response"],
                    "case_sensitive": False
                },
                {
                    "type": "COMPLIANCE_ENFORCEMENT",
                    "priority": 8,
                    "success_metrics": {"compliance_score": "100%", "audit_passed": "yes"},
                    "text_patterns": [
                        r".*(compliance|conformité|audit|réglementation|regulation|standard).*",
                        r".*(gdpr|hipaa|soc2|iso27001|pci).*",
                    ],
                    "event_patterns": {
                        "event_type": ["compliance_violation", "audit_finding"]
                    },
                    "exact_matches": ["compliance_check_failed", "audit_required"],
                    "case_sensitive": False
                }
            ]
        }
        
        self._load_config_from_dict(default_config)
    
    def _load_config_from_file(self, config_path: str):
        """Charge la configuration depuis un fichier YAML/JSON."""
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"Config file not found: {config_path}, using defaults")
            self._load_default_config()
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                if path.suffix.lower() in ['.yaml', '.yml']:
                    config = yaml.safe_load(f)
                elif path.suffix.lower() == '.json':
                    import json
                    config = json.load(f)
                else:
                    logger.error(f"Unsupported config file format: {path.suffix}")
                    self._load_default_config()
                    return
                
                self._load_config_from_dict(config)
                logger.info(f"Loaded intent config from {config_path}")
                
        except Exception as e:
            logger.error(f"Failed to load config from {config_path}: {e}")
            self._load_default_config()
    
    def _load_config_from_dict(self, config: Dict):
        """Charge la configuration depuis un dictionnaire."""
        for intent_config in config.get("intents", []):
            try:
                intent_type = IntentType(intent_config["type"])
                pattern = IntentPattern(
                    intent_type=intent_type,
                    priority=intent_config["priority"],
                    success_metrics=intent_config.get("success_metrics", {}),
                    text_patterns=intent_config.get("text_patterns", []),
                    event_patterns=intent_config.get("event_patterns", {}),
                    exact_matches=intent_config.get("exact_matches", []),
                    case_sensitive=intent_config.get("case_sensitive", False)
                )
                self.patterns[intent_type] = pattern
            except (KeyError, ValueError) as e:
                logger.error(f"Invalid intent config: {intent_config}, error: {e}")
        
        logger.info(f"Loaded {len(self.patterns)} intent patterns")
    
    async def resolve(self, input_data: Union[str, Dict]) -> Intent:
        """
        Résout l'intention à partir de l'entrée.
        
        Args:
            input_data: Texte libre ou payload d'événement
            
        Returns:
            Intent: Intention canonique résolue
        """
        try:
            if isinstance(input_data, str):
                return self._resolve_from_text(input_data)
            elif isinstance(input_data, dict):
                return self._resolve_from_event(input_data)
            else:
                logger.warning(f"Unsupported input type: {type(input_data)}")
                return self.fallback_intent
        except Exception as e:
            logger.error(f"Error resolving intent: {e}")
            return self.fallback_intent
    
    def _resolve_from_text(self, text: str) -> Intent:
        """Résout l'intention à partir d'un texte."""
        # Nettoyer le texte
        cleaned_text = text.strip()
        if not cleaned_text:
            logger.warning("Empty text input")
            return self.fallback_intent
        
        # Vérifier les correspondances exactes d'abord
        for intent_type, pattern in self.patterns.items():
            if self._check_exact_matches(cleaned_text, pattern):
                logger.debug(f"Exact match for '{cleaned_text}': {intent_type.value}")
                return Intent(
                    type=intent_type.value,
                    priority=pattern.priority,
                    success_metrics=pattern.success_metrics
                )
        
        # Vérifier les regex patterns
        best_match = None
        highest_priority = 0
        
        for intent_type, pattern in self.patterns.items():
            if self._check_text_patterns(cleaned_text, pattern):
                if pattern.priority > highest_priority:
                    highest_priority = pattern.priority
                    best_match = (intent_type, pattern)
        
        if best_match:
            intent_type, pattern = best_match
            logger.debug(f"Pattern match for '{cleaned_text}': {intent_type.value}")
            return Intent(
                type=intent_type.value,
                priority=pattern.priority,
                success_metrics=pattern.success_metrics
            )
        
        # Aucune correspondance
        logger.info(f"No intent pattern matched for text: '{cleaned_text}'")
        return self.fallback_intent
    
    def _resolve_from_event(self, event: Dict) -> Intent:
        """Résout l'intention à partir d'un événement."""
        if not event:
            logger.warning("Empty event input")
            return self.fallback_intent
        
        # Vérifier les patterns d'événement
        best_match = None
        highest_priority = 0
        
        for intent_type, pattern in self.patterns.items():
            if self._check_event_patterns(event, pattern):
                if pattern.priority > highest_priority:
                    highest_priority = pattern.priority
                    best_match = (intent_type, pattern)
        
        if best_match:
            intent_type, pattern = best_match
            logger.debug(f"Event match: {intent_type.value}")
            return Intent(
                type=intent_type.value,
                priority=pattern.priority,
                success_metrics=pattern.success_metrics
            )
        
        # Vérifier si l'événement contient du texte
        text_fields = []
        for key, value in event.items():
            if isinstance(value, str) and len(value.split()) > 1:
                text_fields.append(value)
            elif key in ['message', 'description', 'summary', 'title']:
                text_fields.append(str(value))
        
        # Essayer de résoudre à partir du texte dans l'événement
        for text in text_fields:
            intent = self._resolve_from_text(text)
            if intent.type != IntentType.UNKNOWN.value:
                logger.debug(f"Resolved from event text field: {intent.type}")
                return intent
        
        logger.info(f"No intent pattern matched for event: {event}")
        return self.fallback_intent
    
    def _check_exact_matches(self, text: str, pattern: IntentPattern) -> bool:
        """Vérifie les correspondances exactes."""
        if not pattern.exact_matches:
            return False
        
        check_text = text if pattern.case_sensitive else text.lower()
        
        for exact_match in pattern.exact_matches:
            match = exact_match if pattern.case_sensitive else exact_match.lower()
            if check_text == match:
                return True
        
        return False
    
    def _check_text_patterns(self, text: str, pattern: IntentPattern) -> bool:
        """Vérifie les patterns regex."""
        if not pattern.text_patterns:
            return False
        
        check_text = text if pattern.case_sensitive else text.lower()
        flags = 0 if pattern.case_sensitive else re.IGNORECASE
        
        for regex_pattern in pattern.text_patterns:
            try:
                if re.search(regex_pattern, check_text, flags=flags):
                    return True
            except re.error as e:
                logger.warning(f"Invalid regex pattern: {regex_pattern}, error: {e}")
        
        return False
    
    def _check_event_patterns(self, event: Dict, pattern: IntentPattern) -> bool:
        """Vérifie les patterns d'événement."""
        if not pattern.event_patterns:
            return False
        
        for key, expected_values in pattern.event_patterns.items():
            if key not in event:
                return False
            
            event_value = event[key]
            
            # Si expected_values est une liste, vérifier si event_value est dedans
            if isinstance(expected_values, list):
                if not isinstance(event_value, (list, str, int, float)):
                    return False
                
                if isinstance(event_value, list):
                    # Pour les listes, vérifier l'intersection
                    if not any(v in expected_values for v in event_value):
                        return False
                else:
                    # Pour les valeurs simples
                    if event_value not in expected_values:
                        return False
            else:
                # Pour les valeurs simples attendues
                if event_value != expected_values:
                    return False
        
        return True
    
    def add_custom_pattern(
        self,
        intent_type: str,
        priority: int,
        success_metrics: Dict[str, str],
        text_patterns: Optional[List[str]] = None,
        event_patterns: Optional[Dict] = None,
        exact_matches: Optional[List[str]] = None,
        case_sensitive: bool = False
    ) -> bool:
        """
        Ajoute un pattern d'intention personnalisé dynamiquement.
        
        Args:
            intent_type: Type d'intention (doit être dans IntentType ou nouveau)
            priority: Priorité 1-10
            success_metrics: Métriques de succès
            text_patterns: Patterns regex
            event_patterns: Patterns d'événement
            exact_matches: Correspondances exactes
            case_sensitive: Sensible à la casse
            
        Returns:
            bool: True si ajouté avec succès
        """
        try:
            # Créer un nouvel IntentType si nécessaire
            if intent_type not in IntentType._value2member_map_:
                # Ajouter dynamiquement à l'enum (limitation Python)
                logger.warning(f"Intent type {intent_type} not in enum, using string")
                resolved_type = intent_type
            else:
                resolved_type = IntentType(intent_type)
            
            pattern = IntentPattern(
                intent_type=resolved_type if isinstance(resolved_type, IntentType) else resolved_type,
                priority=max(1, min(10, priority)),
                success_metrics=success_metrics,
                text_patterns=text_patterns or [],
                event_patterns=event_patterns or {},
                exact_matches=exact_matches or [],
                case_sensitive=case_sensitive
            )
            
            key = resolved_type if isinstance(resolved_type, IntentType) else resolved_type
            self.patterns[key] = pattern
            logger.info(f"Added custom pattern for intent: {intent_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add custom pattern: {e}")
            return False
    
    def get_available_intents(self) -> List[Dict]:
        """Retourne la liste des intentions disponibles avec leurs patterns."""
        result = []
        for intent_type, pattern in self.patterns.items():
            result.append({
                "type": intent_type.value if isinstance(intent_type, IntentType) else intent_type,
                "priority": pattern.priority,
                "success_metrics": pattern.success_metrics,
                "text_patterns_count": len(pattern.text_patterns),
                "event_patterns_count": len(pattern.event_patterns),
                "exact_matches_count": len(pattern.exact_matches)
            })
        return result


# Instance singleton pour utilisation facile
_intent_resolver_instance = None

def get_intent_resolver(config_path: Optional[str] = None) -> IntentResolver:
    """Obtient l'instance singleton du IntentResolver."""
    global _intent_resolver_instance
    if _intent_resolver_instance is None:
        _intent_resolver_instance = IntentResolver(config_path)
    return _intent_resolver_instance


# Fonctions pures pour une utilisation facile (testable)
async def resolve_intent(input_data: Union[str, Dict]) -> Intent:
    """
    Fonction pure pour résoudre une intention.
    
    Args:
        input_data: Texte ou événement
        
    Returns:
        Intent: Intention résolue
    """
    resolver = IntentResolver()
    return await resolver.resolve(input_data)


# Tests unitaires intégrés (pour vérification rapide)
if __name__ == "__main__":
    # Tests basiques
    async def run_tests():
        resolver = IntentResolver()
        
        test_cases = [
            # (input, expected_intent_type, expected_priority)
            ("facture AWS trop élevée", "COST_OPTIMIZATION", 8),
            ("incident réseau production", "INCIDENT_REDUCTION", 9),
            ("attaque sécurité détectée", "SECURITY_RISK_REDUCTION", 10),
            ("performance lente sur API", "PERFORMANCE_IMPROVEMENT", 7),
            ("audit compliance SOC2", "COMPLIANCE_ENFORCEMENT", 8),
            ("chose random inconnue", "UNKNOWN", 5),
        ]
        
        for text, expected_type, expected_priority in test_cases:
            intent = await resolver.resolve(text)
            print(f"Input: '{text}'")
            print(f"  Intent: {intent.type} (expected: {expected_type})")
            print(f"  Priority: {intent.priority} (expected: {expected_priority})")
            print(f"  Success metrics: {intent.success_metrics}")
            print()
        
        # Test avec événement
        event = {"event_type": "cost_alert", "severity": "high"}
        intent = await resolver.resolve(event)
        print(f"Event input: {event}")
        print(f"  Intent: {intent.type}")
        print(f"  Priority: {intent.priority}")
    
    import asyncio
    asyncio.run(run_tests())