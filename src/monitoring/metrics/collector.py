"""
Metrics Collector - Système centralisé de collecte de métriques pour la plateforme MicroAgents

Fournit des métriques détaillées pour:
1. Performance des agents
2. Valeur business
3. Utilisation des ressources
4. Suivi des coûts
5. Conformité SLA
"""

import asyncio
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import UUID

import orjson
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
    start_http_server,
)
from pydantic import BaseModel, Field
from structlog import get_logger

from src.utils.serialization.serializers import JSONSerializer

logger = get_logger(__name__)


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class MetricType(str, Enum):
    """Types de métriques supportées"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    BUSINESS_VALUE = "business_value"
    ROI = "roi"
    SLA = "sla"


class AgentExecutionMetrics(BaseModel):
    """Métriques d'exécution d'agent"""
    agent_id: UUID
    agent_type: str
    execution_id: UUID
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None
    success: bool = True
    error_message: Optional[str] = None
    resource_usage: Dict[str, float] = Field(default_factory=dict)
    custom_metrics: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


class BusinessValueMetrics(BaseModel):
    """Métriques de valeur business"""
    tenant_id: UUID
    metric_type: str  # "cost_savings", "revenue_impact", "time_savings", etc.
    value: float
    currency: str = "USD"
    period: str  # "daily", "weekly", "monthly"
    timestamp: datetime
    attribution: Dict[str, float] = Field(default_factory=dict)  # Attribution par agent
    confidence_score: float = 0.95  # Score de confiance de la métrique
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


class ResourceUtilization(BaseModel):
    """Utilisation des ressources"""
    tenant_id: UUID
    resource_type: str  # "cpu", "memory", "storage", "network"
    used: float
    allocated: float
    utilization_percentage: float
    timestamp: datetime
    agent_distribution: Optional[Dict[str, float]] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


class CostTracking(BaseModel):
    """Suivi des coûts"""
    tenant_id: UUID
    cost_type: str  # "infrastructure", "license", "support", "cloud"
    amount: float
    currency: str = "USD"
    period: str
    timestamp: datetime
    breakdown: Dict[str, float] = Field(default_factory=dict)  # Détail par service
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


class SLAMetrics(BaseModel):
    """Métriques de conformité SLA"""
    tenant_id: UUID
    sla_type: str  # "availability", "response_time", "resolution_time"
    target: float  # Valeur cible (ex: 99.9 pour 99.9%)
    actual: float  # Valeur réelle
    compliance_percentage: float
    period: str
    timestamp: datetime
    violations: int = 0
    violation_details: List[Dict[str, Any]] = Field(default_factory=list)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


class ROITracking(BaseModel):
    """Suivi ROI par client"""
    tenant_id: UUID
    investment: float  # Investissement total
    returns: float  # Retours totaux
    roi_percentage: float
    period: str  # Période de calcul
    timestamp: datetime
    breakdown: Dict[str, Dict[str, float]] = Field(default_factory=dict)  # Détail par agent/suite
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


# ============================================================================
# COLLECTEUR DE MÉTRIQUES
# ============================================================================


class MetricsCollector:
    """Collecteur central de métriques avec support multi-backend"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.serializer = JSONSerializer()
        self._metrics_cache: Dict[str, List[Tuple[datetime, Any]]] = defaultdict(list)
        self._cache_max_size = self.config.get("cache_max_size", 10000)
        
        # Métriques Prometheus
        self._init_prometheus_metrics()
        
        # Configuration des exporteurs
        self.exporters: List[MetricsExporter] = self._init_exporters()
        
        # Détection d'anomalies
        self.anomaly_detector = AnomalyDetector()
        
        logger.info("MetricsCollector initialisé", exporters=len(self.exporters))
    
    def _init_prometheus_metrics(self):
        """Initialise les métriques Prometheus"""
        # Métriques d'exécution d'agent
        self.agent_execution_duration = Histogram(
            'agent_execution_duration_seconds',
            'Durée d\'exécution des agents',
            ['agent_type', 'tenant_id']
        )
        
        self.agent_execution_total = Counter(
            'agent_execution_total',
            'Nombre total d\'exécutions d\'agents',
            ['agent_type', 'tenant_id', 'status']
        )
        
        self.agent_execution_errors = Counter(
            'agent_execution_errors_total',
            'Nombre d\'erreurs d\'exécution',
            ['agent_type', 'tenant_id', 'error_type']
        )
        
        # Métriques de ressources
        self.resource_utilization = Gauge(
            'resource_utilization_percentage',
            'Utilisation des ressources en pourcentage',
            ['resource_type', 'tenant_id']
        )
        
        # Métriques business
        self.cost_savings = Gauge(
            'cost_savings_usd',
            'Économies de coûts en USD',
            ['tenant_id', 'source']
        )
        
        self.roi_percentage = Gauge(
            'roi_percentage',
            'ROI en pourcentage',
            ['tenant_id', 'period']
        )
        
        # Métriques SLA
        self.sla_compliance = Gauge(
            'sla_compliance_percentage',
            'Conformité SLA en pourcentage',
            ['tenant_id', 'sla_type']
        )
        
        # Métriques personnalisées
        self.custom_metrics_registry: Dict[str, Any] = {}
    
    def _init_exporters(self) -> List['MetricsExporter']:
        """Initialise les exporteurs configurés"""
        exporters = []
        exporter_configs = self.config.get("exporters", [])
        
        for config in exporter_configs:
            exporter_type = config.get("type")
            if exporter_type == "prometheus":
                exporters.append(PrometheusExporter(config))
            elif exporter_type == "opentelemetry":
                exporters.append(OpenTelemetryExporter(config))
            elif exporter_type == "cloud":
                cloud_provider = config.get("provider")
                if cloud_provider == "aws":
                    exporters.append(CloudWatchExporter(config))
                elif cloud_provider == "azure":
                    exporters.append(AzureMonitorExporter(config))
                elif cloud_provider == "gcp":
                    exporters.append(StackdriverExporter(config))
            elif exporter_type == "custom":
                exporters.append(CustomDashboardExporter(config))
        
        return exporters
    
    # ============================================================================
    # COLLECTE DE MÉTRIQUES
    # ============================================================================
    
    async def record_agent_execution(self, metrics: AgentExecutionMetrics):
        """Enregistre les métriques d'exécution d'un agent"""
        try:
            # Mise à jour des métriques Prometheus
            labels = {
                'agent_type': metrics.agent_type,
                'tenant_id': str(metrics.agent_id),  # À adapter avec le vrai tenant_id
                'status': 'success' if metrics.success else 'error'
            }
            
            if metrics.duration_ms:
                self.agent_execution_duration.labels(**labels).observe(
                    metrics.duration_ms / 1000
                )
            
            self.agent_execution_total.labels(**labels).inc()
            
            if not metrics.success and metrics.error_message:
                error_labels = labels.copy()
                error_labels['error_type'] = self._classify_error(metrics.error_message)
                self.agent_execution_errors.labels(**error_labels).inc()
            
            # Cache pour l'analyse de tendances
            cache_key = f"agent_execution:{metrics.agent_type}"
            self._add_to_cache(cache_key, metrics)
            
            # Export vers les différents backends
            await self._export_metrics("agent_execution", metrics)
            
            # Détection d'anomalies
            anomaly_score = await self.anomaly_detector.detect_anomaly(
                metric_type="agent_execution",
                data=metrics.dict(),
                context={"agent_type": metrics.agent_type}
            )
            
            if anomaly_score > 0.8:
                logger.warning(
                    "Anomalie détectée dans l'exécution d'agent",
                    agent_id=str(metrics.agent_id),
                    agent_type=metrics.agent_type,
                    score=anomaly_score
                )
            
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement des métriques d'agent", error=str(e))
    
    async def record_business_value(self, metrics: BusinessValueMetrics):
        """Enregistre les métriques de valeur business"""
        try:
            # Mise à jour des métriques Prometheus
            if metrics.metric_type == "cost_savings":
                self.cost_savings.labels(
                    tenant_id=str(metrics.tenant_id),
                    source="business_value"
                ).set(metrics.value)
            
            # Cache pour l'analyse
            cache_key = f"business_value:{metrics.tenant_id}:{metrics.metric_type}"
            self._add_to_cache(cache_key, metrics)
            
            # Export
            await self._export_metrics("business_value", metrics)
            
            # Calcul du ROI si applicable
            if metrics.metric_type in ["cost_savings", "revenue_impact"]:
                await self._update_roi_metrics(metrics)
            
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement des métriques business", error=str(e))
    
    async def record_resource_utilization(self, metrics: ResourceUtilization):
        """Enregistre l'utilisation des ressources"""
        try:
            # Mise à jour Prometheus
            self.resource_utilization.labels(
                resource_type=metrics.resource_type,
                tenant_id=str(metrics.tenant_id)
            ).set(metrics.utilization_percentage)
            
            # Cache
            cache_key = f"resource:{metrics.tenant_id}:{metrics.resource_type}"
            self._add_to_cache(cache_key, metrics)
            
            # Export
            await self._export_metrics("resource_utilization", metrics)
            
            # Alerte si utilisation élevée
            if metrics.utilization_percentage > 80.0:
                logger.warning(
                    "Utilisation élevée de ressources détectée",
                    tenant_id=str(metrics.tenant_id),
                    resource_type=metrics.resource_type,
                    utilization=metrics.utilization_percentage
                )
            
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement des métriques de ressources", error=str(e))
    
    async def record_cost_tracking(self, metrics: CostTracking):
        """Enregistre le suivi des coûts"""
        try:
            # Cache
            cache_key = f"cost:{metrics.tenant_id}:{metrics.cost_type}"
            self._add_to_cache(cache_key, metrics)
            
            # Export
            await self._export_metrics("cost_tracking", metrics)
            
            # Analyse des tendances de coûts
            trend = await self._analyze_cost_trend(metrics)
            if trend == "increasing_rapidly":
                logger.warning(
                    "Augmentation rapide des coûts détectée",
                    tenant_id=str(metrics.tenant_id),
                    cost_type=metrics.cost_type,
                    amount=metrics.amount
                )
            
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement du suivi des coûts", error=str(e))
    
    async def record_sla_metrics(self, metrics: SLAMetrics):
        """Enregistre les métriques SLA"""
        try:
            # Mise à jour Prometheus
            self.sla_compliance.labels(
                tenant_id=str(metrics.tenant_id),
                sla_type=metrics.sla_type
            ).set(metrics.compliance_percentage)
            
            # Cache
            cache_key = f"sla:{metrics.tenant_id}:{metrics.sla_type}"
            self._add_to_cache(cache_key, metrics)
            
            # Export
            await self._export_metrics("sla_metrics", metrics)
            
            # Alerte si SLA non respecté
            if metrics.compliance_percentage < metrics.target:
                logger.error(
                    "SLA non respecté",
                    tenant_id=str(metrics.tenant_id),
                    sla_type=metrics.sla_type,
                    target=metrics.target,
                    actual=metrics.compliance_percentage
                )
            
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement des métriques SLA", error=str(e))
    
    async def record_roi_tracking(self, metrics: ROITracking):
        """Enregistre le suivi ROI"""
        try:
            # Mise à jour Prometheus
            self.roi_percentage.labels(
                tenant_id=str(metrics.tenant_id),
                period=metrics.period
            ).set(metrics.roi_percentage)
            
            # Cache
            cache_key = f"roi:{metrics.tenant_id}"
            self._add_to_cache(cache_key, metrics)
            
            # Export
            await self._export_metrics("roi_tracking", metrics)
            
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement du suivi ROI", error=str(e))
    
    # ============================================================================
    # MÉTHODES UTILITAIRES
    # ============================================================================
    
    def _add_to_cache(self, key: str, data: Any):
        """Ajoute des données au cache pour analyse"""
        self._metrics_cache[key].append((datetime.now(), data))
        
        # Limite la taille du cache
        if len(self._metrics_cache[key]) > self._cache_max_size:
            self._metrics_cache[key] = self._metrics_cache[key][-self._cache_max_size:]
    
    def _classify_error(self, error_message: str) -> str:
        """Classifie le type d'erreur"""
        error_lower = error_message.lower()
        
        if any(keyword in error_lower for keyword in ["timeout", "timed out"]):
            return "timeout"
        elif any(keyword in error_lower for keyword in ["connection", "network"]):
            return "network"
        elif any(keyword in error_lower for keyword in ["memory", "out of memory"]):
            return "memory"
        elif any(keyword in error_lower for keyword in ["permission", "access denied"]):
            return "permission"
        elif any(keyword in error_lower for keyword in ["validation", "invalid"]):
            return "validation"
        else:
            return "unknown"
    
    async def _export_metrics(self, metric_type: str, data: BaseModel):
        """Exporte les métriques vers tous les exporteurs configurés"""
        export_tasks = []
        
        for exporter in self.exporters:
            try:
                export_tasks.append(
                    exporter.export(metric_type, data.dict())
                )
            except Exception as e:
                logger.error(
                    "Erreur lors de l'export des métriques",
                    exporter=exporter.__class__.__name__,
                    error=str(e)
                )
        
        if export_tasks:
            await asyncio.gather(*export_tasks, return_exceptions=True)
    
    async def _update_roi_metrics(self, business_value: BusinessValueMetrics):
        """Met à jour les métriques ROI basées sur la valeur business"""
        # Implémentation simplifiée - en production, utiliserait une base de données
        roi_key = f"roi_calculation:{business_value.tenant_id}"
        
        # Récupère l'historique d'investissement
        # (dans une implémentation réelle, cela viendrait d'une base de données)
        investment_history = self._get_investment_history(business_value.tenant_id)
        
        # Calcule le ROI
        total_investment = sum(inv['amount'] for inv in investment_history)
        total_returns = business_value.value  # Simplifié
        
        if total_investment > 0:
            roi_percentage = (total_returns - total_investment) / total_investment * 100
            
            roi_metrics = ROITracking(
                tenant_id=business_value.tenant_id,
                investment=total_investment,
                returns=total_returns,
                roi_percentage=roi_percentage,
                period=business_value.period,
                timestamp=datetime.now(),
                breakdown={
                    "business_value": {
                        "type": business_value.metric_type,
                        "value": business_value.value
                    }
                }
            )
            
            await self.record_roi_tracking(roi_metrics)
    
    async def _analyze_cost_trend(self, cost_metrics: CostTracking) -> str:
        """Analyse la tendance des coûts"""
        cache_key = f"cost:{cost_metrics.tenant_id}:{cost_metrics.cost_type}"
        history = self._metrics_cache.get(cache_key, [])
        
        if len(history) < 3:
            return "insufficient_data"
        
        # Extrait les valeurs récentes
        recent_values = [data[1].amount for _, data in history[-3:]]
        
        # Calcule la tendance
        if len(recent_values) >= 2:
            increase_rate = (recent_values[-1] - recent_values[-2]) / recent_values[-2] * 100
            
            if increase_rate > 50:
                return "increasing_rapidly"
            elif increase_rate > 20:
                return "increasing_moderately"
            elif increase_rate < -20:
                return "decreasing"
            else:
                return "stable"
        
        return "stable"
    
    def _get_investment_history(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """Récupère l'historique d'investissement (simulé)"""
        # En production, cela viendrait d'une base de données
        return [
            {"amount": 10000.0, "timestamp": datetime.now() - timedelta(days=30), "type": "initial"},
            {"amount": 5000.0, "timestamp": datetime.now() - timedelta(days=15), "type": "additional"}
        ]
    
    # ============================================================================
    # MÉTHODES DE REQUÊTE ET ANALYSE
    # ============================================================================
    
    async def get_agent_performance_summary(
        self, 
        agent_type: Optional[str] = None,
        tenant_id: Optional[UUID] = None,
        period_hours: int = 24
    ) -> Dict[str, Any]:
        """Retourne un résumé de performance des agents"""
        cutoff = datetime.now() - timedelta(hours=period_hours)
        
        # Filtre le cache
        relevant_metrics = []
        for key, entries in self._metrics_cache.items():
            if key.startswith("agent_execution:"):
                if agent_type and f":{agent_type}" not in key:
                    continue
                
                for timestamp, metrics in entries:
                    if timestamp >= cutoff:
                        if tenant_id and metrics.tenant_id != tenant_id:
                            continue
                        relevant_metrics.append(metrics)
        
        # Calcule les statistiques
        if not relevant_metrics:
            return {"count": 0, "error_rate": 0.0, "avg_duration": 0.0}
        
        total = len(relevant_metrics)
        errors = sum(1 for m in relevant_metrics if not m.success)
        durations = [m.duration_ms for m in relevant_metrics if m.duration_ms]
        
        return {
            "count": total,
            "error_rate": errors / total * 100,
            "avg_duration": sum(durations) / len(durations) if durations else 0.0,
            "p95_duration": self._calculate_percentile(durations, 95) if durations else 0.0,
            "p99_duration": self._calculate_percentile(durations, 99) if durations else 0.0
        }
    
    async def get_business_value_summary(
        self,
        tenant_id: UUID,
        period: str = "monthly"
    ) -> Dict[str, Any]:
        """Retourne un résumé de la valeur business"""
        cache_key_prefix = f"business_value:{tenant_id}:"
        
        summary = {}
        for key in self._metrics_cache:
            if key.startswith(cache_key_prefix):
                metric_type = key.split(":")[-1]
                values = [data[1].value for _, data in self._metrics_cache[key]]
                
                if values:
                    summary[metric_type] = {
                        "total": sum(values),
                        "average": sum(values) / len(values),
                        "count": len(values),
                        "trend": self._calculate_trend(values[-5:]) if len(values) >= 5 else "insufficient_data"
                    }
        
        return summary
    
    def _calculate_percentile(self, values: List[float], percentile: float) -> float:
        """Calcule un percentile"""
        if not values:
            return 0.0
        
        sorted_values = sorted(values)
        index = (len(sorted_values) - 1) * percentile / 100
        lower = int(index)
        upper = lower + 1
        
        if upper >= len(sorted_values):
            return sorted_values[lower]
        
        weight = index - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
    
    def _calculate_trend(self, values: List[float]) -> str:
        """Calcule la tendance d'une série de valeurs"""
        if len(values) < 2:
            return "stable"
        
        # Régression linéaire simple
        x = list(range(len(values)))
        y = values
        
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        sum_x2 = sum(x_i * x_i for x_i in x)
        
        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x * sum_x)
        
        if slope > 0.1:
            return "increasing"
        elif slope < -0.1:
            return "decreasing"
        else:
            return "stable"
    
    def get_prometheus_metrics(self) -> bytes:
        """Retourne les métriques Prometheus au format texte"""
        return generate_latest()
    
    def start_prometheus_server(self, port: int = 8000):
        """Démarre le serveur Prometheus HTTP"""
        start_http_server(port)
        logger.info(f"Serveur Prometheus démarré sur le port {port}")


# ============================================================================
# EXPORTEURS
# ============================================================================


class MetricsExporter(ABC):
    """Interface de base pour les exporteurs de métriques"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", self.__class__.__name__)
    
    @abstractmethod
    async def export(self, metric_type: str, data: Dict[str, Any]) -> None:
        """Exporte les métriques"""
        pass
    
    @abstractmethod
    def format_metric(self, metric_type: str, data: Dict[str, Any]) -> Any:
        """Formate les métriques pour l'export"""
        pass


class PrometheusExporter(MetricsExporter):
    """Exporteur Prometheus"""
    
    async def export(self, metric_type: str, data: Dict[str, Any]) -> None:
        """Exporte vers Prometheus (déjà fait par le collector)"""
        # Les métriques Prometheus sont déjà gérées par le collector
        pass
    
    def format_metric(self, metric_type: str, data: Dict[str, Any]) -> str:
        """Formate pour Prometheus"""
        # Formatage spécifique Prometheus
        labels = self._extract_labels(data)
        value = self._extract_value(data)
        
        metric_name = f"microagents_{metric_type}"
        labels_str = ",".join([f'{k}="{v}"' for k, v in labels.items()])
        
        return f'{metric_name}{{{labels_str}}} {value}'
    
    def _extract_labels(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Extrait les labels pour Prometheus"""
        labels = {}
        
        # Récupère les champs communs
        for field in ['tenant_id', 'agent_type', 'agent_id', 'sla_type', 'resource_type', 'cost_type']:
            if field in data and data[field]:
                labels[field] = str(data[field])
        
        return labels
    
    def _extract_value(self, data: Dict[str, Any]) -> float:
        """Extrait la valeur pour Prometheus"""
        # Cherche les champs de valeur communs
        value_fields = ['value', 'amount', 'duration_ms', 'utilization_percentage', 
                       'compliance_percentage', 'roi_percentage']
        
        for field in value_fields:
            if field in data:
                return float(data[field])
        
        return 0.0


class OpenTelemetryExporter(MetricsExporter):
    """Exporteur OpenTelemetry"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        try:
            from opentelemetry import metrics
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            
            self.exporter = OTLPMetricExporter(
                endpoint=config.get("endpoint", "http://localhost:4317"),
                insecure=config.get("insecure", True)
            )
            
            reader = PeriodicExportingMetricReader(self.exporter)
            self.provider = MeterProvider(metric_readers=[reader])
            metrics.set_meter_provider(self.provider)
            
            self.meter = metrics.get_meter("microagents")
            
            # Crée des instruments réutilisables
            self.instruments: Dict[str, Any] = {}
            
        except ImportError:
            logger.warning("OpenTelemetry non disponible, exporter désactivé")
            self.exporter = None
    
    async def export(self, metric_type: str, data: Dict[str, Any]) -> None:
        if not self.exporter:
            return
        
        try:
            formatted = self.format_metric(metric_type, data)
            
            # Utilise l'instrument approprié
            if metric_type not in self.instruments:
                self._create_instrument(metric_type, data)
            
            instrument = self.instruments.get(metric_type)
            if instrument:
                value = self._extract_value(data)
                instrument.record(value, self._extract_attributes(data))
                
        except Exception as e:
            logger.error("Erreur lors de l'export OpenTelemetry", error=str(e))
    
    def _create_instrument(self, metric_type: str, data: Dict[str, Any]):
        """Crée un instrument OpenTelemetry approprié"""
        if 'duration_ms' in data:
            self.instruments[metric_type] = self.meter.create_histogram(
                name=f"microagents.{metric_type}",
                unit="ms",
                description=f"Metrics for {metric_type}"
            )
        elif 'value' in data or 'amount' in data:
            self.instruments[metric_type] = self.meter.create_gauge(
                name=f"microagents.{metric_type}",
                unit="1",
                description=f"Metrics for {metric_type}"
            )
    
    def _extract_attributes(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Extrait les attributs pour OpenTelemetry"""
        attributes = {}
        
        for key, value in data.items():
            if isinstance(value, (str, int, float, bool)) and key not in ['value', 'amount', 'duration_ms']:
                attributes[key] = str(value)
        
        return attributes


class CloudWatchExporter(MetricsExporter):
    """Exporteur AWS CloudWatch"""
    
    async def export(self, metric_type: str, data: Dict[str, Any]) -> None:
        try:
            import boto3
            from botocore.exceptions import ClientError
            
            cloudwatch = boto3.client('cloudwatch', region_name=self.config.get("region", "us-east-1"))
            
            namespace = self.config.get("namespace", "MicroAgents")
            dimensions = self._extract_dimensions(data)
            value = self._extract_value(data)
            timestamp = datetime.now()
            
            metric_data = {
                'MetricName': metric_type,
                'Dimensions': dimensions,
                'Timestamp': timestamp,
                'Value': value,
                'Unit': self._get_unit(metric_type, data)
            }
            
            # Dans une implémentation réelle, on regrouperait les métriques
            cloudwatch.put_metric_data(
                Namespace=namespace,
                MetricData=[metric_data]
            )
            
        except (ImportError, ClientError) as e:
            logger.error("Erreur lors de l'export CloudWatch", error=str(e))
    
    def _extract_dimensions(self, data: Dict[str, Any]) -> List[Dict[str, str]]:
        """Extrait les dimensions pour CloudWatch"""
        dimensions = []
        
        dimension_fields = ['tenant_id', 'agent_type', 'sla_type', 'resource_type']
        for field in dimension_fields:
            if field in data and data[field]:
                dimensions.append({
                    'Name': field,
                    'Value': str(data[field])
                })
        
        return dimensions
    
    def _get_unit(self, metric_type: str, data: Dict[str, Any]) -> str:
        """Détermine l'unité CloudWatch"""
        if 'duration_ms' in data:
            return 'Milliseconds'
        elif 'percentage' in metric_type or 'utilization' in metric_type:
            return 'Percent'
        elif 'currency' in data and data['currency'] == 'USD':
            return 'USD'
        else:
            return 'Count'


class AzureMonitorExporter(MetricsExporter):
    """Exporteur Azure Monitor"""
    
    async def export(self, metric_type: str, data: Dict[str, Any]) -> None:
        # Implémentation Azure Monitor
        logger.debug("Export Azure Monitor", metric_type=metric_type, data_keys=list(data.keys()))


class StackdriverExporter(MetricsExporter):
    """Exporteur GCP Stackdriver/Cloud Monitoring"""
    
    async def export(self, metric_type: str, data: Dict[str, Any]) -> None:
        # Implémentation Stackdriver
        logger.debug("Export Stackdriver", metric_type=metric_type, data_keys=list(data.keys()))


class CustomDashboardExporter(MetricsExporter):
    """Exporteur pour dashboards personnalisés"""
    
    async def export(self, metric_type: str, data: Dict[str, Any]) -> None:
        """Exporte vers un backend de dashboard personnalisé"""
        try:
            import httpx
            
            endpoint = self.config.get("endpoint")
            api_key = self.config.get("api_key")
            
            if not endpoint:
                return
            
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            payload = {
                "metric_type": metric_type,
                "data": data,
                "timestamp": datetime.now().isoformat(),
                "source": "microagents"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(
                        "Erreur lors de l'export vers le dashboard",
                        status=response.status_code,
                        response=response.text[:200]
                    )
                    
        except Exception as e:
            logger.error("Erreur lors de l'export dashboard", error=str(e))


# ============================================================================
# DÉTECTION D'ANOMALIES
# ============================================================================


class AnomalyDetector:
    """Détecteur d'anomalies pour les métriques"""
    
    def __init__(self):
        self.windows: Dict[str, List[float]] = defaultdict(list)
        self.window_size = 100  # Nombre de points pour l'analyse
        
    async def detect_anomaly(
        self, 
        metric_type: str, 
        data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> float:
        """Détecte les anomalies dans les métriques"""
        try:
            # Extrait la valeur à analyser
            value = self._extract_metric_value(data)
            if value is None:
                return 0.0
            
            # Maintient une fenêtre glissante
            key = f"{metric_type}:{context.get('agent_type', 'global')}" if context else metric_type
            self.windows[key].append(value)
            
            if len(self.windows[key]) > self.window_size:
                self.windows[key] = self.windows[key][-self.window_size:]
            
            # Détection statistique (Z-score)
            if len(self.windows[key]) >= 10:
                mean = sum(self.windows[key]) / len(self.windows[key])
                variance = sum((x - mean) ** 2 for x in self.windows[key]) / len(self.windows[key])
                std_dev = variance ** 0.5
                
                if std_dev > 0:
                    z_score = abs((value - mean) / std_dev)
                    
                    # Seuil d'anomalie (3 sigma)
                    if z_score > 3:
                        return min(1.0, z_score / 6)  # Normalisé entre 0 et 1
            
            return 0.0
            
        except Exception as e:
            logger.error("Erreur dans la détection d'anomalies", error=str(e))
            return 0.0
    
    def _extract_metric_value(self, data: Dict[str, Any]) -> Optional[float]:
        """Extrait une valeur numérique des données de métriques"""
        value_fields = ['duration_ms', 'value', 'amount', 'utilization_percentage', 
                       'compliance_percentage', 'roi_percentage']
        
        for field in value_fields:
            if field in data and data[field] is not None:
                return float(data[field])
        
        return None


# ============================================================================
# FONCTIONS D'USAGE
# ============================================================================


def create_default_collector() -> MetricsCollector:
    """Crée un collecteur de métriques avec configuration par défaut"""
    config = {
        "cache_max_size": 10000,
        "exporters": [
            {
                "type": "prometheus",
                "name": "prometheus"
            },
            {
                "type": "opentelemetry",
                "name": "otel",
                "endpoint": "http://localhost:4317"
            },
            {
                "type": "cloud",
                "name": "aws_cloudwatch",
                "provider": "aws",
                "region": "us-east-1",
                "namespace": "MicroAgents"
            },
            {
                "type": "custom",
                "name": "internal_dashboard",
                "endpoint": "http://dashboard.internal/metrics"
            }
        ]
    }
    
    return MetricsCollector(config)


# Exemple d'utilisation
if __name__ == "__main__":
    import asyncio
    
    async def demo():
        collector = create_default_collector()
        
        # Démarre le serveur Prometheus
        collector.start_prometheus_server(port=9090)
        
        # Exemple: Enregistre une exécution d'agent
        agent_metrics = AgentExecutionMetrics(
            agent_id=UUID("12345678-1234-1234-1234-123456789012"),
            agent_type="cost_optimizer",
            execution_id=UUID("87654321-4321-4321-4321-210987654321"),
            start_time=datetime.now(),
            end_time=datetime.now(),
            duration_ms=150.5,
            success=True,
            resource_usage={"cpu": 0.5, "memory": 128},
            custom_metrics={"cost_savings": 1000.0}
        )
        
        await collector.record_agent_execution(agent_metrics)
        
        # Exemple: Métriques business
        business_metrics = BusinessValueMetrics(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            metric_type="cost_savings",
            value=5000.0,
            period="monthly",
            timestamp=datetime.now(),
            attribution={"cost_optimizer": 5000.0}
        )
        
        await collector.record_business_value(business_metrics)
        
        # Récupère le résumé de performance
        summary = await collector.get_agent_performance_summary()
        print("Résumé de performance:", summary)
        
        # Exporte les métriques Prometheus
        prometheus_data = collector.get_prometheus_metrics()
        print("Métriques Prometheus disponibles sur http://localhost:9090")
    
    asyncio.run(demo())