"""
Alerting Rules Engine - Système de gestion d'alertes intelligent pour la plateforme MicroAgents

Gère:
1. Alertes basées sur des seuils
2. Détection d'anomalies
3. Logique métier
4. Violations SLA
5. Dépassements de coûts
6. Incidents de sécurité
7. Dégradations de performance
8. Violations de conformité
9. Planification de capacité
10. Alertes personnalisées
"""

import asyncio
import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from uuid import UUID, uuid4

import orjson
from croniter import croniter
from pydantic import BaseModel, Field, validator
from structlog import get_logger

from microagents.monitoring.metrics.collector import (
    BusinessValueMetrics,
    CostTracking,
    ROITracking,
    SLAMetrics,
)

logger = get_logger(__name__)


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class AlertSeverity(str, Enum):
    """Sévérité des alertes"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertStatus(str, Enum):
    """Statut des alertes"""
    FIRING = "firing"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"
    SILENCED = "silenced"


class AlertSource(str, Enum):
    """Sources des alertes"""
    METRICS = "metrics"
    LOGS = "logs"
    EVENTS = "events"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    BUSINESS = "business"
    EXTERNAL = "external"


class NotificationChannel(BaseModel):
    """Canal de notification"""
    id: UUID = Field(default_factory=uuid4)
    name: str
    type: str  # "email", "slack", "pagerduty", "webhook", "sms"
    config: Dict[str, Any]
    enabled: bool = True
    priority_filter: Optional[List[AlertSeverity]] = None
    
    @validator("config")
    def validate_config(cls, v, values):
        """Valide la configuration du canal"""
        channel_type = values.get("type")
        
        if channel_type == "email":
            required = ["recipients", "smtp_server"]
        elif channel_type == "slack":
            required = ["webhook_url", "channel"]
        elif channel_type == "pagerduty":
            required = ["integration_key"]
        elif channel_type == "webhook":
            required = ["url"]
        elif channel_type == "sms":
            required = ["phone_numbers", "provider"]
        else:
            raise ValueError(f"Type de canal inconnu: {channel_type}")
        
        for field in required:
            if field not in v:
                raise ValueError(f"Champ requis manquant pour {channel_type}: {field}")
        
        return v


class EscalationPolicy(BaseModel):
    """Politique d'escalade"""
    id: UUID = Field(default_factory=uuid4)
    name: str
    steps: List[Dict[str, Any]]
    max_escalations: int = 3
    auto_acknowledge: bool = False
    
    @validator("steps")
    def validate_steps(cls, v):
        """Valide les étapes d'escalade"""
        if not v:
            raise ValueError("Les politiques d'escalade doivent avoir au moins une étape")
        
        for step in v:
            if "delay_minutes" not in step:
                raise ValueError("Chaque étape doit avoir un délai")
            if "notify_channels" not in step:
                raise ValueError("Chaque étape doit spécifier les canaux de notification")
        
        return v


class AutoRemediationAction(BaseModel):
    """Action de réparation automatique"""
    id: UUID = Field(default_factory=uuid4)
    name: str
    type: str  # "scale", "restart", "failover", "script", "webhook"
    config: Dict[str, Any]
    conditions: Dict[str, Any]  # Conditions d'exécution
    enabled: bool = True
    
    def should_execute(self, alert: "Alert") -> bool:
        """Détermine si l'action doit être exécutée"""
        # Vérifie les conditions
        for key, expected in self.conditions.items():
            if key in alert.labels:
                if alert.labels[key] != expected:
                    return False
            elif key in alert.annotations:
                if alert.annotations[key] != expected:
                    return False
        
        return True


class AlertTemplate(BaseModel):
    """Template d'alerte"""
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str
    severity: AlertSeverity
    source: AlertSource
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    expression: Optional[str] = None  # Pour les règles PromQL
    notification_templates: Dict[str, str] = Field(default_factory=dict)


class Alert(BaseModel):
    """Instance d'alerte"""
    id: UUID = Field(default_factory=uuid4)
    fingerprint: str  # Pour la déduplication
    rule_id: UUID
    name: str
    description: str
    severity: AlertSeverity
    status: AlertStatus = AlertStatus.FIRING
    source: AlertSource
    starts_at: datetime
    ends_at: Optional[datetime] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    value: Optional[float] = None
    generator_url: Optional[str] = None  # Lien vers la source
    
    # Métadonnées
    tenant_id: Optional[UUID] = None
    service: Optional[str] = None
    environment: Optional[str] = None
    
    # Gestion
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    silenced_until: Optional[datetime] = None
    
    # Relations
    escalation_policy_id: Optional[UUID] = None
    auto_remediation_id: Optional[UUID] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }
    
    @validator("fingerprint")
    def generate_fingerprint(cls, v, values):
        """Génère un fingerprint pour la déduplication"""
        if v:
            return v
        
        # Génère un hash basé sur les caractéristiques de l'alerte
        fingerprint_data = {
            "rule_id": values.get("rule_id"),
            "name": values.get("name"),
            "labels": values.get("labels", {}),
            "tenant_id": str(values.get("tenant_id")) if values.get("tenant_id") else "",
        }
        
        fingerprint_str = orjson.dumps(fingerprint_data, sort_keys=True).decode()
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]


class AlertRule(BaseModel):
    """Règle d'alerte"""
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str
    enabled: bool = True
    
    # Configuration
    condition: Dict[str, Any]  # Condition d'évaluation
    severity: AlertSeverity
    source: AlertSource
    for_duration: str = "0s"  # Durée avant déclenchement (ex: "5m", "1h")
    
    # Filtrage
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    
    # Gestion
    notification_channels: List[UUID] = Field(default_factory=list)
    escalation_policy_id: Optional[UUID] = None
    auto_remediation_ids: List[UUID] = Field(default_factory=list)
    
    # Planification
    schedule: Optional[str] = None  # Expression cron
    active_period: Optional[Dict[str, str]] = None  # {"start": "09:00", "end": "18:00"}
    
    # Métadonnées
    created_by: str = "system"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }
    
    @validator("for_duration")
    def validate_duration(cls, v):
        """Valide la durée"""
        if not v.endswith(("s", "m", "h")):
            raise ValueError("Duration must end with s, m, or h")
        return v
    
    @validator("schedule")
    def validate_schedule(cls, v):
        """Valide l'expression cron"""
        if v and not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v}")
        return v
    
    def is_active(self) -> bool:
        """Vérifie si la règle est active selon sa planification"""
        if not self.schedule and not self.active_period:
            return True
        
        now = datetime.now()
        
        # Vérifie l'expression cron
        if self.schedule:
            cron = croniter(self.schedule, now)
            prev_run = cron.get_prev(datetime)
            next_run = cron.get_next(datetime)
            
            # Si maintenant n'est pas entre les runs, la règle est inactive
            if not (prev_run <= now <= next_run):
                return False
        
        # Vérifie la période active
        if self.active_period:
            start_time = datetime.strptime(self.active_period["start"], "%H:%M").time()
            end_time = datetime.strptime(self.active_period["end"], "%H:%M").time()
            current_time = now.time()
            
            if not (start_time <= current_time <= end_time):
                return False
        
        return True


# ============================================================================
# TYPES DE RÈGLES SPÉCIALISÉES
# ============================================================================


class ThresholdRule(AlertRule):
    """Règle basée sur des seuils"""
    threshold_type: str  # "above", "below", "between"
    threshold_value: Union[float, Tuple[float, float]]
    
    def evaluate(self, value: float) -> bool:
        """Évalue si la valeur déclenche l'alerte"""
        if self.threshold_type == "above":
            return value > float(self.threshold_value)
        elif self.threshold_type == "below":
            return value < float(self.threshold_value)
        elif self.threshold_type == "between":
            low, high = self.threshold_value
            return low <= value <= high
        return False


class AnomalyRule(AlertRule):
    """Règle de détection d'anomalies"""
    algorithm: str  # "zscore", "iqr", "mad", "seasonal"
    sensitivity: float = 3.0  # Seuil de sensibilité
    window_size: int = 100  # Taille de la fenêtre
    training_period: str = "7d"  # Période d'entraînement
    
    def evaluate(self, value: float, historical_values: List[float]) -> bool:
        """Évalue si la valeur est anormale"""
        if len(historical_values) < self.window_size:
            return False
        
        if self.algorithm == "zscore":
            return self._zscore_anomaly(value, historical_values)
        elif self.algorithm == "iqr":
            return self._iqr_anomaly(value, historical_values)
        elif self.algorithm == "mad":
            return self._mad_anomaly(value, historical_values)
        
        return False
    
    def _zscore_anomaly(self, value: float, historical: List[float]) -> bool:
        """Détection d'anomalie par Z-score"""
        mean = sum(historical) / len(historical)
        variance = sum((x - mean) ** 2 for x in historical) / len(historical)
        std_dev = variance ** 0.5
        
        if std_dev == 0:
            return False
        
        z_score = abs((value - mean) / std_dev)
        return z_score > self.sensitivity
    
    def _iqr_anomaly(self, value: float, historical: List[float]) -> bool:
        """Détection d'anomalie par IQR"""
        sorted_vals = sorted(historical)
        n = len(sorted_vals)
        
        q1 = sorted_vals[n // 4]
        q3 = sorted_vals[3 * n // 4]
        iqr = q3 - q1
        
        lower_bound = q1 - self.sensitivity * iqr
        upper_bound = q3 + self.sensitivity * iqr
        
        return value < lower_bound or value > upper_bound
    
    def _mad_anomaly(self, value: float, historical: List[float]) -> bool:
        """Détection d'anomalie par Median Absolute Deviation"""
        median = sorted(historical)[len(historical) // 2]
        absolute_deviations = [abs(x - median) for x in historical]
        mad = sorted(absolute_deviations)[len(absolute_deviations) // 2]
        
        if mad == 0:
            return False
        
        modified_zscore = 0.6745 * abs(value - median) / mad
        return modified_zscore > self.sensitivity


class BusinessLogicRule(AlertRule):
    """Règle de logique métier"""
    business_metric: str  # "roi", "customer_satisfaction", "conversion_rate"
    expected_trend: str  # "increasing", "decreasing", "stable"
    comparison_period: str = "7d"  # Période de comparaison
    threshold_percentage: float = 10.0  # % Variation seuil
    
    def evaluate(self, current_value: float, previous_value: float) -> bool:
        """Évalue la logique métier"""
        if previous_value == 0:
            return False
        
        percentage_change = ((current_value - previous_value) / abs(previous_value)) * 100
        
        if self.expected_trend == "increasing":
            return percentage_change < -self.threshold_percentage
        elif self.expected_trend == "decreasing":
            return percentage_change > self.threshold_percentage
        elif self.expected_trend == "stable":
            return abs(percentage_change) > self.threshold_percentage
        
        return False


class SLARule(AlertRule):
    """Règle de violation SLA"""
    sla_type: str  # "availability", "response_time", "resolution_time"
    sla_target: float  # Cible (ex: 99.9 pour 99.9%)
    measurement_window: str = "1h"  # Fenêtre de mesure
    consecutive_violations: int = 3  # Violations consécutives avant alerte
    
    def evaluate(self, current_compliance: float, violation_count: int) -> bool:
        """Évalue la violation SLA"""
        if current_compliance < self.sla_target:
            return violation_count >= self.consecutive_violations
        return False


class CostRule(AlertRule):
    """Règle de dépassement de coûts"""
    budget_type: str  # "monthly", "quarterly", "annual"
    budget_amount: float
    warning_threshold: float = 0.8  # 80% du budget
    alert_threshold: float = 1.0  # 100% du budget
    forecast_enabled: bool = True  # Utilise les prévisions
    
    def evaluate(self, current_spend: float, forecasted_spend: Optional[float] = None) -> Tuple[bool, AlertSeverity]:
        """Évalue les dépassements de coûts"""
        budget_utilization = current_spend / self.budget_amount
        
        if forecasted_spend and self.forecast_enabled:
            forecast_utilization = forecasted_spend / self.budget_amount
            if forecast_utilization >= 1.0:
                return True, AlertSeverity.CRITICAL
            elif forecast_utilization >= self.alert_threshold:
                return True, AlertSeverity.HIGH
            elif forecast_utilization >= self.warning_threshold:
                return True, AlertSeverity.MEDIUM
        
        if budget_utilization >= 1.0:
            return True, AlertSeverity.CRITICAL
        elif budget_utilization >= self.alert_threshold:
            return True, AlertSeverity.HIGH
        elif budget_utilization >= self.warning_threshold:
            return True, AlertSeverity.MEDIUM
        
        return False, AlertSeverity.LOW


class SecurityRule(AlertRule):
    """Règle d'incident de sécurité"""
    security_category: str  # "access", "vulnerability", "compliance", "threat"
    severity_score: Optional[float] = None  # Score CVSS ou similaire
    detection_method: str  # "signature", "behavioral", "anomaly"
    required_response_time: str = "15m"  # Temps de réponse requis
    
    def evaluate(self, event_data: Dict[str, Any]) -> bool:
        """Évalue les incidents de sécurité"""
        # Logique spécifique au type de sécurité
        if self.security_category == "access":
            return self._evaluate_access_event(event_data)
        elif self.security_category == "vulnerability":
            return self._evaluate_vulnerability_event(event_data)
        elif self.security_category == "threat":
            return self._evaluate_threat_event(event_data)
        
        return True  # Par défaut, déclenche l'alerte
    
    def _evaluate_access_event(self, event_data: Dict[str, Any]) -> bool:
        """Évalue les événements d'accès"""
        # Exemple: multiples échecs de connexion
        failed_attempts = event_data.get("failed_attempts", 0)
        return failed_attempts >= 5
    
    def _evaluate_vulnerability_event(self, event_data: Dict[str, Any]) -> bool:
        """Évalue les vulnérabilités"""
        cvss_score = event_data.get("cvss_score", 0.0)
        if self.severity_score:
            return cvss_score >= self.severity_score
        return cvss_score >= 7.0  # Seuil par défaut pour haute criticité
    
    def _evaluate_threat_event(self, event_data: Dict[str, Any]) -> bool:
        """Évalue les menaces"""
        threat_level = event_data.get("threat_level", "low")
        threat_levels = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        return threat_levels.get(threat_level, 0) >= threat_levels.get("high", 3)


# ============================================================================
# MOTEUR D'ALERTES
# ============================================================================


class AlertingEngine:
    """Moteur de gestion des alertes"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.rules: Dict[UUID, AlertRule] = {}
        self.active_alerts: Dict[str, Alert] = {}  # Indexé par fingerprint
        self.alert_history: List[Alert] = []
        
        # Gestion des canaux
        self.notification_channels: Dict[UUID, NotificationChannel] = {}
        self.escalation_policies: Dict[UUID, EscalationPolicy] = {}
        self.auto_remediations: Dict[UUID, AutoRemediationAction] = {}
        
        # Templates
        self.templates: Dict[UUID, AlertTemplate] = {}
        
        # Groupes et silences
        self.silences: Dict[UUID, Dict[str, Any]] = {}
        self.alert_groups: Dict[str, List[Alert]] = {}
        
        # Configuration
        self.history_max_size = self.config.get("history_max_size", 10000)
        self.alert_ttl = self.config.get("alert_ttl_hours", 168)  # 7 jours
        
        # Services
        self.notification_service = NotificationService()
        self.remediation_service = RemediationService()
        
        logger.info("AlertingEngine initialisé")
    
    # ============================================================================
    # GESTION DES RÈGLES
    # ============================================================================
    
    def add_rule(self, rule: AlertRule) -> UUID:
        """Ajoute une règle d'alerte"""
        self.rules[rule.id] = rule
        logger.info("Règle d'alerte ajoutée", rule_id=str(rule.id), name=rule.name)
        return rule.id
    
    def update_rule(self, rule_id: UUID, updates: Dict[str, Any]) -> bool:
        """Met à jour une règle d'alerte"""
        if rule_id not in self.rules:
            return False
        
        rule = self.rules[rule_id]
        
        # Met à jour les champs autorisés
        for key, value in updates.items():
            if hasattr(rule, key) and key not in ["id", "created_at", "created_by"]:
                if key == "updated_at":
                    setattr(rule, key, datetime.now())
                else:
                    setattr(rule, key, value)
        
        logger.info("Règle d'alerte mise à jour", rule_id=str(rule_id))
        return True
    
    def delete_rule(self, rule_id: UUID) -> bool:
        """Supprime une règle d'alerte"""
        if rule_id in self.rules:
            del self.rules[rule_id]
            logger.info("Règle d'alerte supprimée", rule_id=str(rule_id))
            return True
        return False
    
    def enable_rule(self, rule_id: UUID) -> bool:
        """Active une règle"""
        return self.update_rule(rule_id, {"enabled": True})
    
    def disable_rule(self, rule_id: UUID) -> bool:
        """Désactive une règle"""
        return self.update_rule(rule_id, {"enabled": False})
    
    # ============================================================================
    # ÉVALUATION DES RÈGLES
    # ============================================================================
    
    async def evaluate_metrics(self, metrics_data: Dict[str, Any]) -> List[Alert]:
        """Évalue les métriques par rapport aux règles"""
        alerts = []
        
        for rule_id, rule in self.rules.items():
            if not rule.enabled or not rule.is_active():
                continue
            
            try:
                # Évalue la règle selon son type
                if isinstance(rule, ThresholdRule):
                    alert = await self._evaluate_threshold_rule(rule, metrics_data)
                elif isinstance(rule, AnomalyRule):
                    alert = await self._evaluate_anomaly_rule(rule, metrics_data)
                elif isinstance(rule, SLARule):
                    alert = await self._evaluate_sla_rule(rule, metrics_data)
                elif isinstance(rule, CostRule):
                    alert = await self._evaluate_cost_rule(rule, metrics_data)
                elif isinstance(rule, SecurityRule):
                    alert = await self._evaluate_security_rule(rule, metrics_data)
                elif isinstance(rule, BusinessLogicRule):
                    alert = await self._evaluate_business_rule(rule, metrics_data)
                else:
                    # Règle générique
                    alert = await self._evaluate_generic_rule(rule, metrics_data)
                
                if alert:
                    alerts.append(alert)
                    
            except Exception as e:
                logger.error(
                    "Erreur lors de l'évaluation de la règle",
                    rule_id=str(rule_id),
                    rule_name=rule.name,
                    error=str(e)
                )
        
        return alerts
    
    async def _evaluate_threshold_rule(
        self, 
        rule: ThresholdRule, 
        metrics: Dict[str, Any]
    ) -> Optional[Alert]:
        """Évalue une règle de seuil"""
        metric_value = metrics.get("value")
        if metric_value is None:
            return None
        
        if rule.evaluate(metric_value):
            return self._create_alert_from_rule(
                rule,
                value=metric_value,
                annotations={
                    "threshold_type": rule.threshold_type,
                    "threshold_value": str(rule.threshold_value),
                    "current_value": str(metric_value)
                }
            )
        
        return None
    
    async def _evaluate_anomaly_rule(
        self, 
        rule: AnomalyRule, 
        metrics: Dict[str, Any]
    ) -> Optional[Alert]:
        """Évalue une règle d'anomalie"""
        # Récupère les valeurs historiques (simulé)
        historical_values = metrics.get("historical_values", [])
        current_value = metrics.get("value")
        
        if current_value is None or not historical_values:
            return None
        
        if rule.evaluate(current_value, historical_values):
            return self._create_alert_from_rule(
                rule,
                value=current_value,
                annotations={
                    "algorithm": rule.algorithm,
                    "sensitivity": str(rule.sensitivity),
                    "current_value": str(current_value)
                }
            )
        
        return None
    
    async def _evaluate_sla_rule(
        self, 
        rule: SLARule, 
        metrics: Dict[str, Any]
    ) -> Optional[Alert]:
        """Évalue une règle SLA"""
        compliance = metrics.get("compliance_percentage")
        violation_count = metrics.get("violation_count", 0)
        
        if compliance is None:
            return None
        
        if rule.evaluate(compliance, violation_count):
            return self._create_alert_from_rule(
                rule,
                value=compliance,
                annotations={
                    "sla_type": rule.sla_type,
                    "sla_target": str(rule.sla_target),
                    "current_compliance": str(compliance),
                    "violation_count": str(violation_count)
                }
            )
        
        return None
    
    async def _evaluate_cost_rule(
        self, 
        rule: CostRule, 
        metrics: Dict[str, Any]
    ) -> Optional[Alert]:
        """Évalue une règle de coûts"""
        current_spend = metrics.get("current_spend")
        forecasted_spend = metrics.get("forecasted_spend")
        
        if current_spend is None:
            return None
        
        should_alert, severity = rule.evaluate(current_spend, forecasted_spend)
        
        if should_alert:
            alert = self._create_alert_from_rule(rule, value=current_spend)
            alert.severity = severity
            alert.annotations.update({
                "budget_type": rule.budget_type,
                "budget_amount": str(rule.budget_amount),
                "current_spend": str(current_spend),
                "forecasted_spend": str(forecasted_spend) if forecasted_spend else "N/A"
            })
            return alert
        
        return None
    
    async def _evaluate_security_rule(
        self, 
        rule: SecurityRule, 
        metrics: Dict[str, Any]
    ) -> Optional[Alert]:
        """Évalue une règle de sécurité"""
        if rule.evaluate(metrics):
            return self._create_alert_from_rule(
                rule,
                annotations={
                    "security_category": rule.security_category,
                    "detection_method": rule.detection_method,
                    "severity_score": str(rule.severity_score) if rule.severity_score else "N/A"
                }
            )
        
        return None
    
    async def _evaluate_business_rule(
        self, 
        rule: BusinessLogicRule, 
        metrics: Dict[str, Any]
    ) -> Optional[Alert]:
        """Évalue une règle de logique métier"""
        current_value = metrics.get("current_value")
        previous_value = metrics.get("previous_value")
        
        if current_value is None or previous_value is None:
            return None
        
        if rule.evaluate(current_value, previous_value):
            percentage_change = ((current_value - previous_value) / abs(previous_value)) * 100
            
            return self._create_alert_from_rule(
                rule,
                value=current_value,
                annotations={
                    "business_metric": rule.business_metric,
                    "expected_trend": rule.expected_trend,
                    "current_value": str(current_value),
                    "previous_value": str(previous_value),
                    "percentage_change": f"{percentage_change:.2f}%",
                    "threshold": f"{rule.threshold_percentage}%"
                }
            )
        
        return None
    
    async def _evaluate_generic_rule(
        self, 
        rule: AlertRule, 
        metrics: Dict[str, Any]
    ) -> Optional[Alert]:
        """Évalue une règle générique"""
        # Logique d'évaluation basée sur la condition
        condition = rule.condition
        condition_type = condition.get("type")
        
        if condition_type == "expression":
            # Évalue une expression (simplifiée)
            expression = condition.get("expression", "")
            # Dans une implémentation réelle, utiliserait un évaluateur d'expression
            return self._create_alert_from_rule(rule)
        
        elif condition_type == "pattern":
            # Recherche de pattern dans les logs/métriques
            pattern = condition.get("pattern", "")
            data = metrics.get("data", "")
            
            if pattern in str(data):
                return self._create_alert_from_rule(
                    rule,
                    annotations={"pattern_matched": pattern}
                )
        
        return None
    
    def _create_alert_from_rule(
        self, 
        rule: AlertRule, 
        value: Optional[float] = None,
        **kwargs
    ) -> Alert:
        """Crée une alerte à partir d'une règle"""
        annotations = rule.annotations.copy()
        annotations.update(kwargs.get("annotations", {}))
        
        labels = rule.labels.copy()
        labels.update(kwargs.get("labels", {}))
        
        alert = Alert(
            rule_id=rule.id,
            name=rule.name,
            description=rule.description,
            severity=rule.severity,
            source=rule.source,
            starts_at=datetime.now(),
            labels=labels,
            annotations=annotations,
            value=value,
            escalation_policy_id=rule.escalation_policy_id
        )
        
        # Applique les templates si disponibles
        if rule.id in self.templates:
            template = self.templates[rule.id]
            alert.annotations.update(template.annotations)
            alert.labels.update(template.labels)
        
        return alert
    
    # ============================================================================
    # GESTION DES ALERTES
    # ============================================================================
    
    async def process_alerts(self, alerts: List[Alert]) -> List[Alert]:
        """Traite une liste d'alertes (déduplication, grouping, etc.)"""
        processed_alerts = []
        
        for alert in alerts:
            # Vérifie les silences
            if self._is_silenced(alert):
                alert.status = AlertStatus.SILENCED
                self._add_to_history(alert)
                continue
            
            # Déduplication
            existing_alert = self.active_alerts.get(alert.fingerprint)
            if existing_alert:
                # Met à jour l'alerte existante
                await self._update_existing_alert(existing_alert, alert)
                continue
            
            # Grouping
            group_key = self._get_group_key(alert)
            if group_key:
                self.alert_groups.setdefault(group_key, []).append(alert)
            
            # Ajoute à la liste active
            self.active_alerts[alert.fingerprint] = alert
            processed_alerts.append(alert)
            
            # Déclenche les actions
            await self._trigger_alert_actions(alert)
        
        return processed_alerts
    
    async def _trigger_alert_actions(self, alert: Alert):
        """Déclenche les actions associées à une alerte"""
        # Notifications
        await self._send_notifications(alert)
        
        # Escalade
        if alert.escalation_policy_id:
            await self._start_escalation(alert)
        
        # Auto-réparation
        if alert.auto_remediation_id:
            await self._trigger_remediation(alert)
        
        # Webhooks
        await self._trigger_webhooks(alert)
    
    async def _send_notifications(self, alert: Alert):
        """Envoie les notifications pour une alerte"""
        rule = self.rules.get(alert.rule_id)
        if not rule:
            return
        
        for channel_id in rule.notification_channels:
            channel = self.notification_channels.get(channel_id)
            if channel and channel.enabled:
                # Filtre par priorité
                if channel.priority_filter and alert.severity not in channel.priority_filter:
                    continue
                
                await self.notification_service.send_notification(channel, alert)
    
    async def _start_escalation(self, alert: Alert):
        """Démarre le processus d'escalade"""
        if not alert.escalation_policy_id:
            return
        
        policy = self.escalation_policies.get(alert.escalation_policy_id)
        if not policy:
            return
        
        # Implémentation simplifiée - dans la réalité, utiliserait un scheduler
        logger.info(
            "Démarrage de l'escalade",
            alert_id=str(alert.id),
            policy_id=str(policy.id)
        )
    
    async def _trigger_remediation(self, alert: Alert):
        """Déclenche les actions de réparation automatique"""
        rule = self.rules.get(alert.rule_id)
        if not rule:
            return
        
        for remediation_id in rule.auto_remediation_ids:
            remediation = self.auto_remediations.get(remediation_id)
            if remediation and remediation.enabled and remediation.should_execute(alert):
                await self.remediation_service.execute_remediation(remediation, alert)
    
    async def _trigger_webhooks(self, alert: Alert):
        """Déclenche les webhooks pour une alerte"""
        webhook_channels = [
            c for c in self.notification_channels.values() 
            if c.type == "webhook" and c.enabled
        ]
        
        for channel in webhook_channels:
            await self.notification_service.send_webhook(channel, alert)
    
    async def _update_existing_alert(self, existing: Alert, new: Alert):
        """Met à jour une alerte existante"""
        # Met à jour le timestamp de fin
        existing.ends_at = datetime.now()
        
        # Met à jour les annotations si nécessaire
        if new.annotations:
            existing.annotations.update(new.annotations)
        
        # Si l'alerte est résolue (plus de déclenchement)
        # Cette logique serait plus complexe en réalité
        existing.value = new.value
    
    def _is_silenced(self, alert: Alert) -> bool:
        """Vérifie si une alerte est silencée"""
        for silence_id, silence in self.silences.items():
            # Vérifie les correspondances
            matches = True
            for key, value in silence.get("matchers", {}).items():
                if key in alert.labels:
                    if alert.labels[key] != value:
                        matches = False
                        break
                else:
                    matches = False
                    break
            
            if matches:
                # Vérifie la période de silence
                starts_at = silence.get("starts_at")
                ends_at = silence.get("ends_at")
                
                if starts_at and ends_at:
                    now = datetime.now()
                    if starts_at <= now <= ends_at:
                        return True
        
        return False
    
    def _get_group_key(self, alert: Alert) -> Optional[str]:
        """Calcule une clé de groupe pour les alertes"""
        # Regroupe par service et sévérité par défaut
        if alert.service and alert.severity:
            return f"{alert.service}:{alert.severity}"
        return None
    
    def _add_to_history(self, alert: Alert):
        """Ajoute une alerte à l'historique"""
        self.alert_history.append(alert)
        
        # Nettoie l'historique ancien
        cutoff = datetime.now() - timedelta(hours=self.alert_ttl)
        self.alert_history = [
            a for a in self.alert_history 
            if a.starts_at >= cutoff
        ]
        
        # Limite la taille
        if len(self.alert_history) > self.history_max_size:
            self.alert_history = self.alert_history[-self.history_max_size:]
    
    # ============================================================================
    # GESTION DES SILENCES
    # ============================================================================
    
    def create_silence(
        self,
        matchers: Dict[str, str],
        starts_at: datetime,
        ends_at: datetime,
        created_by: str,
        comment: str = ""
    ) -> UUID:
        """Crée un silence"""
        silence_id = uuid4()
        
        self.silences[silence_id] = {
            "id": silence_id,
            "matchers": matchers,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "created_by": created_by,
            "created_at": datetime.now(),
            "comment": comment
        }
        
        # Nettoie les silences expirés
        self._cleanup_expired_silences()
        
        logger.info(
            "Silence créé",
            silence_id=str(silence_id),
            created_by=created_by,
            duration=f"{(ends_at - starts_at).total_seconds() / 3600:.1f}h"
        )
        
        return silence_id
    
    def delete_silence(self, silence_id: UUID) -> bool:
        """Supprime un silence"""
        if silence_id in self.silences:
            del self.silences[silence_id]
            logger.info("Silence supprimé", silence_id=str(silence_id))
            return True
        return False
    
    def _cleanup_expired_silences(self):
        """Nettoie les silences expirés"""
        now = datetime.now()
        expired = []
        
        for silence_id, silence in self.silences.items():
            ends_at = silence.get("ends_at")
            if ends_at and ends_at < now:
                expired.append(silence_id)
        
        for silence_id in expired:
            del self.silences[silence_id]
    
    # ============================================================================
    # ANALYTIQUES D'ALERTES
    # ============================================================================
    
    def get_alert_statistics(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        tenant_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Retourne des statistiques sur les alertes"""
        if not start_time:
            start_time = datetime.now() - timedelta(days=7)
        if not end_time:
            end_time = datetime.now()
        
        # Filtre l'historique
        filtered_alerts = [
            a for a in self.alert_history
            if start_time <= a.starts_at <= end_time
            and (tenant_id is None or a.tenant_id == tenant_id)
        ]
        
        # Calcule les statistiques
        total_alerts = len(filtered_alerts)
        
        by_severity = {}
        by_source = {}
        by_status = {}
        
        for alert in filtered_alerts:
            by_severity[alert.severity] = by_severity.get(alert.severity, 0) + 1
            by_source[alert.source] = by_source.get(alert.source, 0) + 1
            by_status[alert.status] = by_status.get(alert.status, 0) + 1
        
        mttr = self._calculate_mttr(filtered_alerts)
        
        return {
            "total_alerts": total_alerts,
            "time_period": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            },
            "by_severity": by_severity,
            "by_source": by_source,
            "by_status": by_status,
            "mttr_hours": mttr,
            "active_alerts": len(self.active_alerts)
        }
    
    def _calculate_mttr(self, alerts: List[Alert]) -> float:
        """Calcule le Mean Time To Resolution (MTTR)"""
        resolved_alerts = [a for a in alerts if a.status == AlertStatus.RESOLVED and a.ends_at]
        
        if not resolved_alerts:
            return 0.0
        
        total_resolution_time = sum(
            (a.ends_at - a.starts_at).total_seconds()
            for a in resolved_alerts
        )
        
        return total_resolution_time / len(resolved_alerts) / 3600  # En heures
    
    # ============================================================================
    # MÉTHODES UTILITAIRES
    # ============================================================================
    
    def acknowledge_alert(self, alert_id: UUID, user: str) -> bool:
        """Marque une alerte comme acquittée"""
        for alert in self.active_alerts.values():
            if alert.id == alert_id:
                alert.status = AlertStatus.ACKNOWLEDGED
                alert.acknowledged_by = user
                alert.acknowledged_at = datetime.now()
                logger.info("Alerte acquittée", alert_id=str(alert_id), user=user)
                return True
        
        return False
    
    def resolve_alert(self, alert_id: UUID) -> bool:
        """Marque une alerte comme résolue"""
        for alert in self.active_alerts.values():
            if alert.id == alert_id:
                alert.status = AlertStatus.RESOLVED
                alert.ends_at = datetime.now()
                
                # Retire de la liste active
                if alert.fingerprint in self.active_alerts:
                    del self.active_alerts[alert.fingerprint]
                
                # Ajoute à l'historique
                self._add_to_history(alert)
                
                logger.info("Alerte résolue", alert_id=str(alert_id))
                return True
        
        return False
    
    def get_active_alerts(
        self,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Alert]:
        """Retourne les alertes actives avec filtres"""
        alerts = list(self.active_alerts.values())
        
        if not filters:
            return alerts
        
        filtered_alerts = []
        for alert in alerts:
            matches = True
            
            for key, value in filters.items():
                if key == "severity":
                    if alert.severity != value:
                        matches = False
                        break
                elif key == "tenant_id":
                    if alert.tenant_id != value:
                        matches = False
                        break
                elif key == "service":
                    if alert.service != value:
                        matches = False
                        break
                elif key in alert.labels:
                    if alert.labels[key] != value:
                        matches = False
                        break
            
            if matches:
                filtered_alerts.append(alert)
        
        return filtered_alerts


# ============================================================================
# SERVICES AUXILIAIRES
# ============================================================================


class NotificationService:
    """Service de gestion des notifications"""
    
    async def send_notification(self, channel: NotificationChannel, alert: Alert):
        """Envoie une notification via un canal"""
        try:
            if channel.type == "email":
                await self._send_email(channel, alert)
            elif channel.type == "slack":
                await self._send_slack(channel, alert)
            elif channel.type == "pagerduty":
                await self._send_pagerduty(channel, alert)
            elif channel.type == "webhook":
                await self._send_webhook(channel, alert)
            elif channel.type == "sms":
                await self._send_sms(channel, alert)
            
            logger.info(
                "Notification envoyée",
                channel_type=channel.type,
                alert_id=str(alert.id),
                channel_name=channel.name
            )
            
        except Exception as e:
            logger.error(
                "Erreur lors de l'envoi de la notification",
                channel_type=channel.type,
                alert_id=str(alert.id),
                error=str(e)
            )
    
    async def _send_email(self, channel: NotificationChannel, alert: Alert):
        """Envoie un email"""
        # Implémentation simplifiée
        recipients = channel.config["recipients"]
        subject = f"[{alert.severity.upper()}] {alert.name}"
        body = self._format_alert_message(alert)
        
        # Dans une implémentation réelle, utiliserait SMTP
        logger.debug(
            "Email prêt à être envoyé",
            recipients=recipients,
            subject=subject
        )
    
    async def _send_slack(self, channel: NotificationChannel, alert: Alert):
        """Envoie une notification Slack"""
        webhook_url = channel.config["webhook_url"]
        channel_name = channel.config.get("channel", "#alerts")
        
        message = {
            "channel": channel_name,
            "username": "MicroAgents Alerting",
            "icon_emoji": self._get_slack_emoji(alert.severity),
            "attachments": [{
                "color": self._get_slack_color(alert.severity),
                "title": alert.name,
                "text": alert.description,
                "fields": [
                    {"title": "Severity", "value": alert.severity, "short": True},
                    {"title": "Status", "value": alert.status, "short": True},
                    {"title": "Service", "value": alert.service or "N/A", "short": True},
                    {"title": "Environment", "value": alert.environment or "N/A", "short": True},
                ],
                "footer": "MicroAgents Platform",
                "ts": datetime.now().timestamp()
            }]
        }
        
        # Dans une implémentation réelle, enverrait via webhook
        logger.debug("Slack message préparé", channel=channel_name)
    
    async def _send_pagerduty(self, channel: NotificationChannel, alert: Alert):
        """Envoie une alerte PagerDuty"""
        integration_key = channel.config["integration_key"]
        
        event = {
            "routing_key": integration_key,
            "event_action": "trigger",
            "dedup_key": alert.fingerprint,
            "payload": {
                "summary": f"{alert.name}: {alert.description}",
                "source": alert.source,
                "severity": alert.severity,
                "custom_details": alert.annotations,
                "timestamp": datetime.now().isoformat()
            }
        }
        
        logger.debug("PagerDuty event préparé", dedup_key=alert.fingerprint)
    
    async def send_webhook(self, channel: NotificationChannel, alert: Alert):
        """Envoie un webhook"""
        url = channel.config["url"]
        headers = channel.config.get("headers", {})
        
        payload = {
            "alert": alert.dict(),
            "timestamp": datetime.now().isoformat(),
            "source": "microagents_alerting"
        }
        
        # Dans une implémentation réelle, utiliserait httpx
        logger.debug("Webhook préparé", url=url)
    
    async def _send_sms(self, channel: NotificationChannel, alert: Alert):
        """Envoie un SMS"""
        phone_numbers = channel.config["phone_numbers"]
        provider = channel.config["provider"]
        
        message = f"Alert: {alert.name} - {alert.description[:100]}..."
        
        logger.debug(
            "SMS préparé",
            provider=provider,
            recipients=len(phone_numbers)
        )
    
    def _format_alert_message(self, alert: Alert) -> str:
        """Formate le message d'alerte"""
        return f"""
Alert: {alert.name}
Description: {alert.description}
Severity: {alert.severity}
Status: {alert.status}
Service: {alert.service or 'N/A'}
Environment: {alert.environment or 'N/A'}
Timestamp: {alert.starts_at.isoformat()}

Labels:
{chr(10).join(f'  {k}: {v}' for k, v in alert.labels.items())}

Annotations:
{chr(10).join(f'  {k}: {v}' for k, v in alert.annotations.items())}
        """
    
    def _get_slack_color(self, severity: AlertSeverity) -> str:
        """Retourne la couleur Slack pour la sévérité"""
        colors = {
            AlertSeverity.CRITICAL: "#FF0000",
            AlertSeverity.HIGH: "#FF6B00",
            AlertSeverity.MEDIUM: "#FFD700",
            AlertSeverity.LOW: "#1E90FF",
            AlertSeverity.INFO: "#808080"
        }
        return colors.get(severity, "#808080")
    
    def _get_slack_emoji(self, severity: AlertSeverity) -> str:
        """Retourne l'emoji Slack pour la sévérité"""
        emojis = {
            AlertSeverity.CRITICAL: ":fire:",
            AlertSeverity.HIGH: ":warning:",
            AlertSeverity.MEDIUM: ":exclamation:",
            AlertSeverity.LOW: ":information_source:",
            AlertSeverity.INFO: ":bell:"
        }
        return emojis.get(severity, ":bell:")


class RemediationService:
    """Service d'exécution des réparations automatiques"""
    
    async def execute_remediation(self, action: AutoRemediationAction, alert: Alert):
        """Exécute une action de réparation"""
        try:
            logger.info(
                "Exécution de la réparation automatique",
                action_id=str(action.id),
                action_name=action.name,
                alert_id=str(alert.id)
            )
            
            if action.type == "scale":
                await self._execute_scale(action, alert)
            elif action.type == "restart":
                await self._execute_restart(action, alert)
            elif action.type == "failover":
                await self._execute_failover(action, alert)
            elif action.type == "script":
                await self._execute_script(action, alert)
            elif action.type == "webhook":
                await self._execute_remediation_webhook(action, alert)
            
        except Exception as e:
            logger.error(
                "Erreur lors de l'exécution de la réparation",
                action_id=str(action.id),
                error=str(e)
            )
    
    async def _execute_scale(self, action: AutoRemediationAction, alert: Alert):
        """Exécute un scaling"""
        # Logique de scaling
        logger.debug("Scaling action exécutée")
    
    async def _execute_restart(self, action: AutoRemediationAction, alert: Alert):
        """Exécute un restart"""
        # Logique de restart
        logger.debug("Restart action exécutée")
    
    async def _execute_failover(self, action: AutoRemediationAction, alert: Alert):
        """Exécute un failover"""
        # Logique de failover
        logger.debug("Failover action exécutée")
    
    async def _execute_script(self, action: AutoRemediationAction, alert: Alert):
        """Exécute un script"""
        # Logique d'exécution de script
        logger.debug("Script action exécutée")
    
    async def _execute_remediation_webhook(self, action: AutoRemediationAction, alert: Alert):
        """Exécute un webhook de réparation"""
        # Logique de webhook
        logger.debug("Remediation webhook exécuté")


# ============================================================================
# FONCTIONS D'USAGE
# ============================================================================


def create_default_alerting_engine() -> AlertingEngine:
    """Crée un moteur d'alertes avec configuration par défaut"""
    config = {
        "history_max_size": 10000,
        "alert_ttl_hours": 168
    }
    
    engine = AlertingEngine(config)
    
    # Ajoute des canaux de notification par défaut
    email_channel = NotificationChannel(
        name="Admin Email",
        type="email",
        config={
            "recipients": ["admin@example.com"],
            "smtp_server": "smtp.example.com"
        },
        priority_filter=[AlertSeverity.CRITICAL, AlertSeverity.HIGH]
    )
    
    slack_channel = NotificationChannel(
        name="DevOps Slack",
        type="slack",
        config={
            "webhook_url": "https://hooks.slack.com/services/...",
            "channel": "#alerts"
        }
    )
    
    engine.notification_channels[email_channel.id] = email_channel
    engine.notification_channels[slack_channel.id] = slack_channel
    
    # Ajoute des règles par défaut
    high_cpu_rule = ThresholdRule(
        name="High CPU Utilization",
        description="CPU utilization above 80% for 5 minutes",
        condition={"type": "threshold"},
        severity=AlertSeverity.HIGH,
        source=AlertSource.METRICS,
        threshold_type="above",
        threshold_value=80.0,
        for_duration="5m",
        labels={"component": "infrastructure", "metric": "cpu_usage"},
        notification_channels=[email_channel.id, slack_channel.id]
    )
    
    engine.add_rule(high_cpu_rule)
    
    sla_violation_rule = SLARule(
        name="SLA Violation - Availability",
        description="Service availability below 99.9% for 1 hour",
        condition={"type": "sla"},
        severity=AlertSeverity.CRITICAL,
        source=AlertSource.BUSINESS,
        sla_type="availability",
        sla_target=99.9,
        measurement_window="1h",
        consecutive_violations=3,
        labels={"sla": "availability", "priority": "p1"}
    )
    
    engine.add_rule(sla_violation_rule)
    
    return engine


# Exemple d'utilisation
if __name__ == "__main__":
    import asyncio
    
    async def demo():
        engine = create_default_alerting_engine()
        
        # Simule des métriques
        metrics = {
            "value": 85.0,  # CPU à 85%
            "resource_type": "cpu",
            "tenant_id": "12345678-1234-1234-1234-123456789012"
        }
        
        # Évalue les règles
        alerts = await engine.evaluate_metrics(metrics)
        
        # Traite les alertes
        processed_alerts = await engine.process_alerts(alerts)
        
        print(f"Alertes générées: {len(processed_alerts)}")
        
        # Affiche les statistiques
        stats = engine.get_alert_statistics()
        print("Statistiques:", stats)
        
        # Liste les alertes actives
        active_alerts = engine.get_active_alerts()
        print(f"Alertes actives: {len(active_alerts)}")
    
    asyncio.run(demo())