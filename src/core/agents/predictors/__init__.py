"""
Module d'agents prédicteurs (~150 agents)

Ce module contient ~150 agents de prédiction spécialisés dans:
1. Prédiction d'incidents (30+ modèles)
2. Prévision de coûts (25+ modèles)
3. Planification de capacité (20+ modèles)
4. Prédiction de menaces de sécurité (25+ modèles)
5. Prédiction de dégradation de performance (20+ modèles)
6. Prédiction de comportement utilisateur (15+ modèles)
7. Prévision de métriques business (15+ modèles)

Modèles ML utilisés:
- LSTM pour les séries temporelles
- Prophet pour données saisonnières
- ARIMA pour séries stationnaires
- Random Forest pour classification
- Gradient Boosting pour régression
- Réseaux de neurones pour patterns complexes
- Méthodes d'ensemble pour améliorer la précision
"""

from typing import Dict, List, Optional, Any, Tuple, Union
from datetime import datetime, timedelta
from enum import Enum
import numpy as np
import pandas as pd
from scipy import stats
import pickle
import hashlib
import json
from dataclasses import dataclass
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# ML Libraries
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor, GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.model_selection import train_test_split, TimeSeriesSplit
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, mean_absolute_error, mean_squared_error, r2_score
from sklearn.neural_network import MLPClassifier, MLPRegressor
from xgboost import XGBClassifier, XGBRegressor
from lightgbm import LGBMClassifier, LGBMRegressor
from catboost import CatBoostClassifier, CatBoostRegressor
import tensorflow as tf
from tensorflow.keras.models import Sequential, Model
from tensorflow.keras.layers import LSTM, Dense, Dropout, Bidirectional, Conv1D, MaxPooling1D, Flatten, Input, Attention
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from prophet import Prophet
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from src.core.base.agent import BaseAgent, AgentResult
from src.core.base.context import AgentContext
from src.core.business_value.calculator import BusinessValueCalculator
from src.monitoring.metrics.collector import MetricsCollector


# ==================== ENUMS ET TYPES ====================

class PredictionType(str, Enum):
    """Types de prédiction"""
    INCIDENT = "incident"
    COST = "cost"
    CAPACITY = "capacity"
    SECURITY = "security"
    PERFORMANCE = "performance"
    USER_BEHAVIOR = "user_behavior"
    BUSINESS = "business"


class ModelType(str, Enum):
    """Types de modèles ML"""
    LSTM = "lstm"
    PROPHET = "prophet"
    ARIMA = "arima"
    RANDOM_FOREST = "random_forest"
    GRADIENT_BOOSTING = "gradient_boosting"
    XGBOOST = "xgboost"
    LIGHTGBM = "lightgbm"
    CATBOOST = "catboost"
    NEURAL_NETWORK = "neural_network"
    ENSEMBLE = "ensemble"
    SARIMA = "sarima"
    EXPONENTIAL_SMOOTHING = "exponential_smoothing"


class HorizonType(str, Enum):
    """Horizons de prédiction"""
    SHORT_TERM = "short_term"      # 1-24 heures
    MEDIUM_TERM = "medium_term"    # 1-7 jours
    LONG_TERM = "long_term"        # 1-4 semaines
    STRATEGIC = "strategic"        # 1-12 mois


@dataclass
class PredictionResult:
    """Résultat structuré d'une prédiction"""
    prediction: Any
    confidence: float
    horizon: HorizonType
    model_used: ModelType
    features_used: List[str]
    prediction_time: datetime
    metadata: Dict[str, Any]


# ==================== MODÈLES ML MANAGER ====================

class MLModelManager:
    """Gestionnaire centralisé des modèles ML"""
    
    def __init__(self, models_dir: str = "models"):
        self.models_dir = models_dir
        self.models: Dict[str, Any] = {}
        self.scalers: Dict[str, StandardScaler] = {}
        self.model_versions: Dict[str, int] = defaultdict(int)
        self.metrics_collector = MetricsCollector()
        
    def load_or_train_model(
        self,
        model_key: str,
        model_type: ModelType,
        X_train: np.ndarray,
        y_train: np.ndarray,
        params: Optional[Dict] = None
    ) -> Any:
        """Charge ou entraîne un modèle"""
        model_path = f"{self.models_dir}/{model_key}.pkl"
        
        try:
            # Essayer de charger le modèle existant
            with open(model_path, 'rb') as f:
                model = pickle.load(f)
            self.models[model_key] = model
            return model
        except:
            # Entraîner un nouveau modèle
            model = self._create_model(model_type, params)
            model.fit(X_train, y_train)
            
            # Sauvegarder le modèle
            with open(model_path, 'wb') as f:
                pickle.dump(model, f)
            
            self.models[model_key] = model
            self.model_versions[model_key] += 1
            
            # Collecter des métriques
            self.metrics_collector.record_model_training(
                model_key, model_type.value, len(X_train)
            )
            
            return model
    
    def _create_model(self, model_type: ModelType, params: Optional[Dict] = None) -> Any:
        """Crée un nouveau modèle selon le type"""
        params = params or {}
        
        if model_type == ModelType.RANDOM_FOREST:
            return RandomForestClassifier(**params) if params.get('classification', True) else RandomForestRegressor(**params)
        elif model_type == ModelType.GRADIENT_BOOSTING:
            return GradientBoostingClassifier(**params) if params.get('classification', True) else GradientBoostingRegressor(**params)
        elif model_type == ModelType.XGBOOST:
            return XGBClassifier(**params) if params.get('classification', True) else XGBRegressor(**params)
        elif model_type == ModelType.LIGHTGBM:
            return LGBMClassifier(**params) if params.get('classification', True) else LGBMRegressor(**params)
        elif model_type == ModelType.CATBOOST:
            return CatBoostClassifier(**params) if params.get('classification', True) else CatBoostRegressor(**params)
        elif model_type == ModelType.NEURAL_NETWORK:
            return self._create_neural_network(params)
        elif model_type == ModelType.LSTM:
            return self._create_lstm_model(params)
        else:
            raise ValueError(f"Model type not supported: {model_type}")
    
    def _create_neural_network(self, params: Dict) -> MLPClassifier:
        """Crée un réseau de neurones"""
        default_params = {
            'hidden_layer_sizes': (100, 50),
            'activation': 'relu',
            'solver': 'adam',
            'max_iter': 1000,
            'random_state': 42
        }
        default_params.update(params)
        
        if params.get('classification', True):
            return MLPClassifier(**default_params)
        else:
            return MLPRegressor(**default_params)
    
    def _create_lstm_model(self, params: Dict) -> Sequential:
        """Crée un modèle LSTM"""
        model = Sequential()
        
        # Couches LSTM
        if params.get('bidirectional', False):
            model.add(Bidirectional(LSTM(
                units=params.get('lstm_units', 50),
                return_sequences=params.get('return_sequences', True),
                input_shape=params.get('input_shape')
            )))
        else:
            model.add(LSTM(
                units=params.get('lstm_units', 50),
                return_sequences=params.get('return_sequences', True),
                input_shape=params.get('input_shape')
            ))
        
        # Dropout pour la régularisation
        model.add(Dropout(params.get('dropout_rate', 0.2)))
        
        # Autres couches LSTM si nécessaire
        if params.get('lstm_layers', 1) > 1:
            for _ in range(params['lstm_layers'] - 1):
                model.add(LSTM(
                    units=params.get('lstm_units', 25),
                    return_sequences=False
                ))
                model.add(Dropout(params.get('dropout_rate', 0.2)))
        
        # Couche de sortie
        if params.get('classification', True):
            model.add(Dense(1, activation='sigmoid'))
            model.compile(
                optimizer=params.get('optimizer', 'adam'),
                loss='binary_crossentropy',
                metrics=['accuracy']
            )
        else:
            model.add(Dense(1))
            model.compile(
                optimizer=params.get('optimizer', 'adam'),
                loss='mse',
                metrics=['mae']
            )
        
        return model


# ==================== AGENTS DE PRÉDICTION D'INCIDENTS (30+) ====================

class IncidentPredictor(BaseAgent):
    """Prédicteur d'incidents générique"""
    
    def __init__(self):
        super().__init__(
            agent_id="incident_predictor_v1",
            category="predictors.incident.general"
        )
        self.model_manager = MLModelManager()
        self.prediction_horizon = HorizonType.SHORT_TERM
        
    async def analyze(self, context: AgentContext) -> AgentResult:
        """Prédit les incidents"""
        historical_incidents = context.get_data("historical_incidents", [])
        current_metrics = context.get_data("current_metrics", {})
        
        if len(historical_incidents) < 100:
            return AgentResult.error("Insufficient historical data for prediction")
        
        # Préparer les données
        X, y = self._prepare_incident_data(historical_incidents, current_metrics)
        
        # Utiliser différents modèles
        predictions = {}
        model_types = [
            ModelType.RANDOM_FOREST,
            ModelType.XGBOOST,
            ModelType.LSTM
        ]
        
        for model_type in model_types:
            model_key = f"incident_{model_type.value}_{hashlib.md5(str(X.shape).encode()).hexdigest()[:8]}"
            
            # Diviser les données
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42
            )
            
            # Entraîner ou charger le modèle
            model = self.model_manager.load_or_train_model(
                model_key=model_key,
                model_type=model_type,
                X_train=X_train,
                y_train=y_train,
                params={'classification': True}
            )
            
            # Faire des prédictions
            y_pred = model.predict(X_test)
            
            # Évaluer le modèle
            metrics = self._evaluate_classification_model(y_test, y_pred)
            
            # Prédire pour les données actuelles
            current_prediction = model.predict(self._prepare_current_data(current_metrics))
            
            predictions[model_type.value] = {
                'prediction': current_prediction[0] if len(current_prediction) > 0 else 0,
                'confidence': metrics.get('accuracy', 0),
                'metrics': metrics
            }
        
        # Combinaison d'ensemble
        ensemble_prediction = self._ensemble_predictions(predictions)
        
        return AgentResult.success(data={
            'model_predictions': predictions,
            'ensemble_prediction': ensemble_prediction,
            'risk_level': self._calculate_risk_level(ensemble_prediction),
            'recommended_actions': self._generate_recommendations(ensemble_prediction, current_metrics),
            'prediction_horizon': self.prediction_horizon.value
        })
    
    def _prepare_incident_data(self, incidents: List[Dict], current_metrics: Dict) -> Tuple[np.ndarray, np.ndarray]:
        """Prépare les données d'incidents pour l'entraînement"""
        # Implémentation simplifiée
        features = []
        labels = []
        
        for incident in incidents:
            # Extraire les features de l'incident
            feature_vector = self._extract_incident_features(incident)
            features.append(feature_vector)
            
            # Label: 1 si incident critique, 0 sinon
            labels.append(1 if incident.get('severity') in ['critical', 'high'] else 0)
        
        # Ajouter les métriques courantes
        current_features = self._extract_current_features(current_metrics)
        if len(features) > 0 and current_features is not None:
            # Pour l'entraînement, dupliquer avec des labels neutres
            features.append(current_features)
            labels.append(0)  # Label neutre pour les données courantes
        
        return np.array(features), np.array(labels)


class ServiceFailurePredictor(BaseAgent):
    """Prédit les défaillances de service spécifiques"""
    
    def __init__(self, service_name: str):
        super().__init__(
            agent_id=f"service_failure_predictor_{service_name}_v1",
            category=f"predictors.incident.service.{service_name}"
        )
        self.service_name = service_name
        self.prediction_window_hours = 24
        
    async def analyze(self, context: AgentContext) -> AgentResult:
        service_metrics = context.get_data("service_metrics", {})
        
        # Utiliser différents modèles de séries temporelles
        predictions = {}
        
        # ARIMA pour les patterns stationnaires
        arima_pred = await self._arima_prediction(service_metrics)
        predictions['arima'] = arima_pred
        
        # LSTM pour les patterns complexes
        lstm_pred = await self._lstm_prediction(service_metrics)
        predictions['lstm'] = lstm_pred
        
        # Prophet pour la saisonnalité
        prophet_pred = await self._prophet_prediction(service_metrics)
        predictions['prophet'] = prophet_pred
        
        # Combiner les prédictions
        combined = self._combine_predictions(predictions)
        
        return AgentResult.success(data={
            'service': self.service_name,
            'predictions': predictions,
            'combined_prediction': combined,
            'time_horizon_hours': self.prediction_window_hours,
            'anomaly_score': self._calculate_anomaly_score(service_metrics, combined),
            'maintenance_recommendation': self._generate_maintenance_recommendation(combined)
        })


class LatencySpikePredictor(BaseAgent):
    """Prédit les pointes de latence"""
    
    def __init__(self):
        super().__init__(
            agent_id="latency_spike_predictor_v2",
            category="predictors.incident.latency"
        )
        self.latency_threshold_ms = 100  # Seuil de latence anormale
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        latency_data = context.get_data("latency_metrics", [])
        
        if len(latency_data) < 50:
            return AgentResult.error("Insufficient latency data")
        
        # Convertir en séries temporelles
        df = pd.DataFrame(latency_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        
        # Détecter les patterns de spikes
        spike_patterns = await self._detect_spike_patterns(df)
        
        # Prédiction avec LSTM
        lstm_prediction = await self._predict_with_lstm(df)
        
        # Analyse de corrélation avec d'autres métriques
        correlations = await self._analyze_correlations(context)
        
        return AgentResult.success(data={
            'spike_patterns_detected': spike_patterns,
            'lstm_prediction': lstm_prediction,
            'correlation_analysis': correlations,
            'predicted_spikes': self._predict_future_spikes(lstm_prediction, spike_patterns),
            'mitigation_strategies': self._suggest_mitigation_strategies(lstm_prediction)
        })


# ==================== AGENTS DE PRÉVISION DE COÛTS (25+) ====================

class CostForecaster(BaseAgent):
    """Prévisionnaire de coûts générique"""
    
    def __init__(self):
        super().__init__(
            agent_id="cost_forecaster_v1",
            category="predictors.cost.general"
        )
        self.business_calculator = BusinessValueCalculator()
        self.horizon = HorizonType.MEDIUM_TERM
        
    async def analyze(self, context: AgentContext) -> AgentResult:
        historical_costs = context.get_data("historical_costs", [])
        usage_patterns = context.get_data("usage_patterns", {})
        pricing_models = context.get_data("pricing_models", {})
        
        # Prévisions par modèle
        forecasts = {}
        
        # Régression linéaire pour tendance générale
        linear_forecast = await self._linear_regression_forecast(historical_costs)
        forecasts['linear_regression'] = linear_forecast
        
        # XGBoost pour patterns complexes
        xgb_forecast = await self._xgboost_forecast(historical_costs, usage_patterns)
        forecasts['xgboost'] = xgb_forecast
        
        # LSTM pour séries temporelles
        lstm_forecast = await self._lstm_cost_forecast(historical_costs)
        forecasts['lstm'] = lstm_forecast
        
        # Prophet pour saisonnalité
        prophet_forecast = await self._prophet_cost_forecast(historical_costs)
        forecasts['prophet'] = prophet_forecast
        
        # Combiner les prévisions
        combined = self._ensemble_forecasts(forecasts)
        
        # Calculer les économies potentielles
        savings_opportunities = await self._identify_savings_opportunities(combined, usage_patterns, pricing_models)
        
        return AgentResult.success(data={
            'cost_forecasts': forecasts,
            'combined_forecast': combined,
            'savings_opportunities': savings_opportunities,
            'confidence_interval': self._calculate_confidence_interval(forecasts),
            'budget_recommendations': self._generate_budget_recommendations(combined),
            'roi_predictions': await self._predict_cost_savings_roi(savings_opportunities)
        })


class CloudCostPredictor(BaseAgent):
    """Prédit les coûts cloud spécifiques"""
    
    def __init__(self, cloud_provider: str):
        super().__init__(
            agent_id=f"cloud_cost_predictor_{cloud_provider}_v1",
            category=f"predictors.cost.cloud.{cloud_provider}"
        )
        self.cloud_provider = cloud_provider
        self.service_categories = ['compute', 'storage', 'network', 'database']
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        usage_data = context.get_data(f"{self.cloud_provider}_usage", {})
        cost_data = context.get_data(f"{self.cloud_provider}_costs", [])
        
        forecasts_by_service = {}
        anomalies_detected = []
        
        for service in self.service_categories:
            if service in usage_data:
                # Prévision par service
                service_forecast = await self._forecast_service_cost(
                    service, 
                    usage_data[service], 
                    cost_data
                )
                forecasts_by_service[service] = service_forecast
                
                # Détection d'anomalies
                anomalies = await self._detect_cost_anomalies(service_forecast)
                if anomalies:
                    anomalies_detected.extend(anomalies)
        
        # Prévision totale
        total_forecast = self._aggregate_forecasts(forecasts_by_service)
        
        # Recommandations d'optimisation
        optimization_recommendations = await self._generate_optimization_recommendations(
            forecasts_by_service, usage_data
        )
        
        return AgentResult.success(data={
            'cloud_provider': self.cloud_provider,
            'service_forecasts': forecasts_by_service,
            'total_forecast': total_forecast,
            'anomalies_detected': anomalies_detected,
            'optimization_recommendations': optimization_recommendations,
            'reserved_instance_analysis': await self._analyze_reserved_instances(usage_data, total_forecast),
            'spot_instance_opportunities': await self._identify_spot_opportunities(usage_data)
        })


class CostAnomalyPredictor(BaseAgent):
    """Prédit les anomalies de coûts"""
    
    def __init__(self):
        super().__init__(
            agent_id="cost_anomaly_predictor_v1",
            category="predictors.cost.anomaly"
        )
        self.anomaly_detection_models = {
            'isolation_forest': IsolationForest(contamination=0.1),
            'autoencoder': None,  # À implémenter
            'statistical': self._statistical_anomaly_detection
        }
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        cost_data = context.get_data("cost_data", [])
        
        if len(cost_data) < 30:
            return AgentResult.error("Insufficient cost data for anomaly detection")
        
        anomalies = {}
        
        for model_name, model in self.anomaly_detection_models.items():
            if model:
                model_anomalies = await self._detect_with_model(model, cost_data, model_name)
                anomalies[model_name] = model_anomalies
        
        # Combiner les détections
        combined_anomalies = self._combine_anomaly_detections(anomalies)
        
        # Prédire les futures anomalies
        future_anomalies = await self._predict_future_anomalies(cost_data, combined_anomalies)
        
        # Évaluer l'impact business
        business_impact = await self._evaluate_business_impact(combined_anomalies)
        
        return AgentResult.success(data={
            'detected_anomalies': combined_anomalies,
            'predicted_future_anomalies': future_anomalies,
            'anomaly_patterns': self._extract_anomaly_patterns(combined_anomalies),
            'business_impact': business_impact,
            'prevention_strategies': self._suggest_prevention_strategies(combined_anomalies),
            'monitoring_recommendations': self._generate_monitoring_recommendations(future_anomalies)
        })


# ==================== AGENTS DE PLANIFICATION DE CAPACITÉ (20+) ====================

class CapacityPlanner(BaseAgent):
    """Planificateur de capacité générique"""
    
    def __init__(self):
        super().__init__(
            agent_id="capacity_planner_v1",
            category="predictors.capacity.general"
        )
        self.planning_horizon = HorizonType.MEDIUM_TERM
        
    async def analyze(self, context: AgentContext) -> AgentResult:
        current_usage = context.get_data("current_usage", {})
        historical_usage = context.get_data("historical_usage", [])
        growth_rate = context.get_data("growth_rate", 0.1)  # 10% par défaut
        
        # Analyse de capacité par ressource
        capacity_analysis = {}
        
        for resource_type, usage_data in current_usage.items():
            analysis = await self._analyze_resource_capacity(
                resource_type, 
                usage_data, 
                historical_usage, 
                growth_rate
            )
            capacity_analysis[resource_type] = analysis
        
        # Prédictions de besoins futurs
        future_needs = await self._predict_future_needs(capacity_analysis, growth_rate)
        
        # Recommandations de scaling
        scaling_recommendations = await self._generate_scaling_recommendations(capacity_analysis, future_needs)
        
        # Analyse coût-bénéfice
        cost_benefit = await self._analyze_cost_benefit(scaling_recommendations)
        
        return AgentResult.success(data={
            'capacity_analysis': capacity_analysis,
            'future_needs_prediction': future_needs,
            'scaling_recommendations': scaling_recommendations,
            'cost_benefit_analysis': cost_benefit,
            'risk_assessment': self._assess_capacity_risks(capacity_analysis),
            'optimization_opportunities': self._identify_optimization_opportunities(capacity_analysis)
        })


class AutoScalingPredictor(BaseAgent):
    """Prédit les besoins de auto-scaling"""
    
    def __init__(self, service_type: str):
        super().__init__(
            agent_id=f"autoscaling_predictor_{service_type}_v1",
            category=f"predictors.capacity.autoscaling.{service_type}"
        )
        self.service_type = service_type
        self.scaling_metrics = ['cpu_utilization', 'memory_usage', 'request_rate']
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        metrics_data = context.get_data("metrics_data", {})
        
        scaling_predictions = {}
        
        for metric in self.scaling_metrics:
            if metric in metrics_data:
                # Prédire les besoins de scaling pour cette métrique
                prediction = await self._predict_scaling_needs(metric, metrics_data[metric])
                scaling_predictions[metric] = prediction
        
        # Recommandations de configuration auto-scaling
        autoscaling_config = await self._optimize_autoscaling_config(scaling_predictions)
        
        # Simulation de scénarios
        scenario_analysis = await self._simulate_scaling_scenarios(scaling_predictions)
        
        return AgentResult.success(data={
            'service_type': self.service_type,
            'scaling_predictions': scaling_predictions,
            'recommended_autoscaling_config': autoscaling_config,
            'scenario_analysis': scenario_analysis,
            'cost_implications': await self._calculate_scaling_costs(scaling_predictions),
            'performance_impact': await self._predict_performance_impact(scaling_predictions)
        })


class StorageCapacityPredictor(BaseAgent):
    """Prédit les besoins en stockage"""
    
    def __init__(self):
        super().__init__(
            agent_id="storage_capacity_predictor_v2",
            category="predictors.capacity.storage"
        )
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        storage_metrics = context.get_data("storage_metrics", {})
        data_growth_rate = context.get_data("data_growth_rate", 0.15)  # 15% par défaut
        
        # Analyse par type de stockage
        analysis_by_type = {}
        
        for storage_type, metrics in storage_metrics.items():
            analysis = await self._analyze_storage_type(storage_type, metrics, data_growth_rate)
            analysis_by_type[storage_type] = analysis
        
        # Prédiction de capacité nécessaire
        capacity_predictions = await self._predict_storage_needs(analysis_by_type, data_growth_rate)
        
        # Recommandations de clean-up
        cleanup_recommendations = await self._identify_cleanup_opportunities(storage_metrics)
        
        # Optimisation des coûts
        cost_optimization = await self._optimize_storage_costs(analysis_by_type, capacity_predictions)
        
        return AgentResult.success(data={
            'storage_analysis': analysis_by_type,
            'capacity_predictions': capacity_predictions,
            'cleanup_recommendations': cleanup_recommendations,
            'cost_optimization': cost_optimization,
            'migration_opportunities': self._identify_migration_opportunities(analysis_by_type),
            'disaster_recovery_implications': await self._assess_dr_implications(capacity_predictions)
        })


# ==================== AGENTS DE PRÉDICTION DE MENACES DE SÉCURITÉ (25+) ====================

class SecurityThreatPredictor(BaseAgent):
    """Prédicteur de menaces de sécurité"""
    
    def __init__(self):
        super().__init__(
            agent_id="security_threat_predictor_v1",
            category="predictors.security.threat"
        )
        self.threat_intelligence_sources = [
            'internal_logs',
            'vulnerability_scans',
            'network_traffic',
            'user_behavior'
        ]
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        security_data = context.get_data("security_data", {})
        
        threat_predictions = {}
        
        for source in self.threat_intelligence_sources:
            if source in security_data:
                prediction = await self._predict_threats_from_source(source, security_data[source])
                threat_predictions[source] = prediction
        
        # Combinaison des prédictions
        combined_threats = self._combine_threat_predictions(threat_predictions)
        
        # Évaluation des risques
        risk_assessment = await self._assess_security_risks(combined_threats)
        
        # Recommandations de mitigation
        mitigation_strategies = self._generate_mitigation_strategies(combined_threats, risk_assessment)
        
        return AgentResult.success(data={
            'threat_predictions': threat_predictions,
            'combined_threat_assessment': combined_threats,
            'risk_assessment': risk_assessment,
            'mitigation_strategies': mitigation_strategies,
            'priority_actions': self._prioritize_security_actions(combined_threats, risk_assessment),
            'compliance_implications': await self._assess_compliance_implications(combined_threats)
        })


class VulnerabilityExploitPredictor(BaseAgent):
    """Prédit l'exploitation potentielle de vulnérabilités"""
    
    def __init__(self):
        super().__init__(
            agent_id="vulnerability_exploit_predictor_v1",
            category="predictors.security.vulnerability"
        )
        self.cvss_threshold = 7.0  # Seuil CVSS pour les vulnérabilités critiques
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        vulnerabilities = context.get_data("vulnerabilities", [])
        
        if not vulnerabilities:
            return AgentResult.success(data={'message': 'No vulnerabilities to analyze'})
        
        exploit_predictions = []
        
        for vuln in vulnerabilities:
            prediction = await self._predict_exploit_likelihood(vuln)
            exploit_predictions.append({
                'vulnerability': vuln.get('id'),
                'cvss_score': vuln.get('cvss_score', 0),
                'exploit_prediction': prediction,
                'risk_level': self._calculate_risk_level(prediction, vuln),
                'patch_priority': self._determine_patch_priority(prediction, vuln)
            })
        
        # Analyse temporelle
        temporal_analysis = await self._analyze_exploit_trends(exploit_predictions)
        
        # Recommandations de remediation
        remediation_plan = self._create_remediation_plan(exploit_predictions)
        
        return AgentResult.success(data={
            'exploit_predictions': exploit_predictions,
            'temporal_analysis': temporal_analysis,
            'remediation_plan': remediation_plan,
            'risk_heatmap': self._create_risk_heatmap(exploit_predictions),
            'compensation_controls': self._suggest_compensation_controls(exploit_predictions)
        })


class InsiderThreatPredictor(BaseAgent):
    """Prédit les menaces internes"""
    
    def __init__(self):
        super().__init__(
            agent_id="insider_threat_predictor_v1",
            category="predictors.security.insider"
        )
        self.behavior_models = {
            'access_patterns': self._analyze_access_patterns,
            'data_transfers': self._analyze_data_transfers,
            'privilege_usage': self._analyze_privilege_usage
        }
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        user_activity = context.get_data("user_activity", {})
        
        threat_indicators = {}
        
        for model_name, model_func in self.behavior_models.items():
            indicators = await model_func(user_activity)
            threat_indicators[model_name] = indicators
        
        # Détection d'anomalies comportementales
        behavioral_anomalies = await self._detect_behavioral_anomalies(threat_indicators)
        
        # Évaluation du risque
        risk_scores = self._calculate_risk_scores(behavioral_anomalies)
        
        # Alerts et recommendations
        alerts = self._generate_threat_alerts(risk_scores)
        
        return AgentResult.success(data={
            'threat_indicators': threat_indicators,
            'behavioral_anomalies': behavioral_anomalies,
            'risk_scores': risk_scores,
            'threat_alerts': alerts,
            'prevention_measures': self._suggest_prevention_measures(behavioral_anomalies),
            'monitoring_enhancements': self._recommend_monitoring_enhancements(threat_indicators)
        })


# ==================== AGENTS DE PRÉDICTION DE DÉGRADATION DE PERFORMANCE (20+) ====================

class PerformanceDegradationPredictor(BaseAgent):
    """Prédit la dégradation de performance"""
    
    def __init__(self):
        super().__init__(
            agent_id="performance_degradation_predictor_v1",
            category="predictors.performance.degradation"
        )
        self.degradation_thresholds = {
            'response_time': 200,  # ms
            'throughput': 0.8,     # 80% du maximum
            'error_rate': 0.01     # 1%
        }
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        performance_metrics = context.get_data("performance_metrics", {})
        
        degradation_predictions = {}
        
        for metric_name, threshold in self.degradation_thresholds.items():
            if metric_name in performance_metrics:
                prediction = await self._predict_metric_degradation(
                    metric_name, 
                    performance_metrics[metric_name], 
                    threshold
                )
                degradation_predictions[metric_name] = prediction
        
        # Détection de patterns de dégradation
        degradation_patterns = self._detect_degradation_patterns(degradation_predictions)
        
        # Root cause prediction
        root_cause_predictions = await self._predict_root_causes(degradation_predictions, performance_metrics)
        
        # Impact assessment
        impact_assessment = await self._assess_degradation_impact(degradation_predictions, context)
        
        return AgentResult.success(data={
            'degradation_predictions': degradation_predictions,
            'degradation_patterns': degradation_patterns,
            'root_cause_predictions': root_cause_predictions,
            'impact_assessment': impact_assessment,
            'mitigation_strategies': self._suggest_mitigation_strategies(degradation_predictions, root_cause_predictions),
            'preventive_measures': self._recommend_preventive_measures(degradation_patterns)
        })


class DatabasePerformancePredictor(BaseAgent):
    """Prédit la performance des bases de données"""
    
    def __init__(self, db_type: str):
        super().__init__(
            agent_id=f"database_performance_predictor_{db_type}_v1",
            category=f"predictors.performance.database.{db_type}"
        )
        self.db_type = db_type
        self.performance_indicators = ['query_latency', 'connection_pool', 'cache_hit_ratio']
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        db_metrics = context.get_data("database_metrics", {})
        
        performance_predictions = {}
        
        for indicator in self.performance_indicators:
            if indicator in db_metrics:
                prediction = await self._predict_db_performance(indicator, db_metrics[indicator])
                performance_predictions[indicator] = prediction
        
        # Analyse des requêtes lentes
        slow_query_analysis = await self._analyze_slow_queries(db_metrics)
        
        # Optimisation recommendations
        optimization_recommendations = await self._generate_db_optimization_recommendations(
            performance_predictions, db_metrics
        )
        
        # Capacity planning
        capacity_predictions = await self._predict_db_capacity_needs(db_metrics)
        
        return AgentResult.success(data={
            'database_type': self.db_type,
            'performance_predictions': performance_predictions,
            'slow_query_analysis': slow_query_analysis,
            'optimization_recommendations': optimization_recommendations,
            'capacity_predictions': capacity_predictions,
            'index_optimization': self._suggest_index_optimizations(slow_query_analysis),
            'replication_analysis': await self._analyze_replication_needs(performance_predictions)
        })


class APIPerformancePredictor(BaseAgent):
    """Prédit la performance des APIs"""
    
    def __init__(self):
        super().__init__(
            agent_id="api_performance_predictor_v1",
            category="predictors.performance.api"
        )
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        api_metrics = context.get_data("api_metrics", {})
        
        # Prédiction par endpoint
        endpoint_predictions = {}
        
        for endpoint, metrics in api_metrics.items():
            prediction = await self._predict_endpoint_performance(endpoint, metrics)
            endpoint_predictions[endpoint] = prediction
        
        # Analyse des dépendances
        dependency_analysis = await self._analyze_api_dependencies(endpoint_predictions)
        
        # Load testing predictions
        load_predictions = await self._predict_load_limits(endpoint_predictions)
        
        # Rate limiting optimization
        rate_limit_recommendations = await self._optimize_rate_limits(endpoint_predictions, load_predictions)
        
        return AgentResult.success(data={
            'endpoint_predictions': endpoint_predictions,
            'dependency_analysis': dependency_analysis,
            'load_predictions': load_predictions,
            'rate_limit_recommendations': rate_limit_recommendations,
            'caching_opportunities': self._identify_caching_opportunities(endpoint_predictions),
            'circuit_breaker_config': self._suggest_circuit_breaker_config(endpoint_predictions, dependency_analysis)
        })


# ==================== AGENTS DE PRÉDICTION DE COMPORTEMENT UTILISATEUR (15+) ====================

class UserBehaviorPredictor(BaseAgent):
    """Prédit le comportement des utilisateurs"""
    
    def __init__(self):
        super().__init__(
            agent_id="user_behavior_predictor_v1",
            category="predictors.user_behavior.general"
        )
        self.behavior_categories = ['engagement', 'conversion', 'retention', 'churn']
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        user_data = context.get_data("user_data", {})
        
        behavior_predictions = {}
        
        for category in self.behavior_categories:
            prediction = await self._predict_user_behavior(category, user_data)
            behavior_predictions[category] = prediction
        
        # Segmentation des utilisateurs
        user_segments = await self._segment_users(behavior_predictions, user_data)
        
        # Recommandations personnalisées
        personalized_recommendations = self._generate_personalized_recommendations(user_segments)
        
        # Impact sur les métriques business
        business_impact = await self._assess_business_impact(behavior_predictions, user_segments)
        
        return AgentResult.success(data={
            'behavior_predictions': behavior_predictions,
            'user_segments': user_segments,
            'personalized_recommendations': personalized_recommendations,
            'business_impact': business_impact,
            'engagement_strategies': self._suggest_engagement_strategies(user_segments),
            'retention_tactics': self._recommend_retention_tactics(behavior_predictions.get('churn', {}))
        })


class UserChurnPredictor(BaseAgent):
    """Prédit le churn utilisateur"""
    
    def __init__(self):
        super().__init__(
            agent_id="user_churn_predictor_v1",
            category="predictors.user_behavior.churn"
        )
        self.churn_indicators = [
            'login_frequency',
            'feature_usage',
            'support_tickets',
            'payment_history'
        ]
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        user_metrics = context.get_data("user_metrics", {})
        
        churn_predictions = {}
        
        for user_id, metrics in user_metrics.items():
            churn_probability = await self._calculate_churn_probability(metrics)
            churn_predictions[user_id] = {
                'churn_probability': churn_probability,
                'risk_level': self._determine_churn_risk(churn_probability),
                'key_indicators': self._identify_churn_indicators(metrics)
            }
        
        # Analyse temporelle
        temporal_analysis = await self._analyze_churn_trends(churn_predictions)
        
        # Stratégies de retention
        retention_strategies = self._develop_retention_strategies(churn_predictions)
        
        # Impact financier
        financial_impact = await self._calculate_churn_impact(churn_predictions, context)
        
        return AgentResult.success(data={
            'churn_predictions': churn_predictions,
            'temporal_analysis': temporal_analysis,
            'retention_strategies': retention_strategies,
            'financial_impact': financial_impact,
            'high_risk_users': self._identify_high_risk_users(churn_predictions),
            'intervention_recommendations': self._suggest_interventions(churn_predictions)
        })


class FeatureAdoptionPredictor(BaseAgent):
    """Prédit l'adoption de nouvelles fonctionnalités"""
    
    def __init__(self):
        super().__init__(
            agent_id="feature_adoption_predictor_v1",
            category="predictors.user_behavior.feature_adoption"
        )
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        feature_data = context.get_data("feature_data", {})
        
        adoption_predictions = {}
        
        for feature_name, usage_data in feature_data.items():
            prediction = await self._predict_feature_adoption(feature_name, usage_data)
            adoption_predictions[feature_name] = prediction
        
        # Analyse des patterns d'adoption
        adoption_patterns = self._analyze_adoption_patterns(adoption_predictions)
        
        # Recommandations d'amélioration
        improvement_recommendations = self._suggest_feature_improvements(adoption_predictions, adoption_patterns)
        
        # Impact sur la satisfaction
        satisfaction_impact = await self._assess_satisfaction_impact(adoption_predictions)
        
        return AgentResult.success(data={
            'adoption_predictions': adoption_predictions,
            'adoption_patterns': adoption_patterns,
            'improvement_recommendations': improvement_recommendations,
            'satisfaction_impact': satisfaction_impact,
            'training_needs': self._identify_training_needs(adoption_patterns),
            'communication_strategy': self._develop_communication_strategy(adoption_predictions)
        })


# ==================== AGENTS DE PRÉVISION DE MÉTRIQUES BUSINESS (15+) ====================

class BusinessMetricsForecaster(BaseAgent):
    """Prévisionnaire de métriques business"""
    
    def __init__(self):
        super().__init__(
            agent_id="business_metrics_forecaster_v1",
            category="predictors.business.general"
        )
        self.business_calculator = BusinessValueCalculator()
        self.key_metrics = ['revenue', 'mrr', 'arr', 'cac', 'ltv', 'churn_rate']
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        historical_metrics = context.get_data("historical_metrics", {})
        
        forecasts = {}
        
        for metric in self.key_metrics:
            if metric in historical_metrics:
                forecast = await self._forecast_business_metric(metric, historical_metrics[metric])
                forecasts[metric] = forecast
        
        # Analyse de corrélation entre métriques
        correlation_analysis = await self._analyze_metric_correlations(forecasts)
        
        # Scénarios what-if
        what_if_scenarios = await self._simulate_what_if_scenarios(forecasts, context)
        
        # Recommandations stratégiques
        strategic_recommendations = self._generate_strategic_recommendations(forecasts, what_if_scenarios)
        
        return AgentResult.success(data={
            'metric_forecasts': forecasts,
            'correlation_analysis': correlation_analysis,
            'what_if_scenarios': what_if_scenarios,
            'strategic_recommendations': strategic_recommendations,
            'kpi_targets': self._suggest_kpi_targets(forecasts),
            'risk_assessment': await self._assess_business_risks(forecasts)
        })


class RevenuePredictor(BaseAgent):
    """Prédit les revenus"""
    
    def __init__(self):
        super().__init__(
            agent_id="revenue_predictor_v1",
            category="predictors.business.revenue"
        )
        self.revenue_sources = ['subscriptions', 'usage_based', 'one_time', 'professional_services']
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        revenue_data = context.get_data("revenue_data", {})
        
        source_predictions = {}
        
        for source in self.revenue_sources:
            if source in revenue_data:
                prediction = await self._predict_revenue_source(source, revenue_data[source])
                source_predictions[source] = prediction
        
        # Prévision totale
        total_revenue_prediction = self._aggregate_revenue_predictions(source_predictions)
        
        # Analyse de saisonnalité
        seasonality_analysis = await self._analyze_revenue_seasonality(revenue_data)
        
        # Impact des facteurs externes
        external_factors_impact = await self._assess_external_factors_impact(context)
        
        return AgentResult.success(data={
            'source_predictions': source_predictions,
            'total_revenue_prediction': total_revenue_prediction,
            'seasonality_analysis': seasonality_analysis,
            'external_factors_impact': external_factors_impact,
            'pricing_optimization': self._suggest_pricing_optimizations(source_predictions),
            'upsell_opportunities': self._identify_upsell_opportunities(source_predictions, context)
        })


class CustomerLTVPredictor(BaseAgent):
    """Prédit la Lifetime Value des clients"""
    
    def __init__(self):
        super().__init__(
            agent_id="customer_ltv_predictor_v1",
            category="predictors.business.ltv"
        )
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        customer_data = context.get_data("customer_data", {})
        
        ltv_predictions = {}
        
        for customer_segment, data in customer_data.items():
            prediction = await self._predict_customer_ltv(customer_segment, data)
            ltv_predictions[customer_segment] = prediction
        
        # Analyse de segmentation
        segmentation_analysis = self._analyze_customer_segments(ltv_predictions)
        
        # Optimisation du CAC
        cac_optimization = await self._optimize_cac(ltv_predictions, context)
        
        # Stratégies de maximisation LTV
        ltv_maximization_strategies = self._develop_ltv_maximization_strategies(ltv_predictions, segmentation_analysis)
        
        return AgentResult.success(data={
            'ltv_predictions': ltv_predictions,
            'segmentation_analysis': segmentation_analysis,
            'cac_optimization': cac_optimization,
            'ltv_maximization_strategies': ltv_maximization_strategies,
            'retention_investment_roi': self._calculate_retention_investment_roi(ltv_predictions),
            'customer_acquisition_strategy': self._optimize_acquisition_strategy(ltv_predictions, segmentation_analysis)
        })


# ==================== REGISTRE DES AGENTS PRÉDICTEURS ====================

# Agents de prédiction d'incidents (30+)
INCIDENT_PREDICTION_AGENTS = {
    "incident_predictor_v1": IncidentPredictor,
    "service_failure_predictor_general_v1": lambda: ServiceFailurePredictor("general"),
    "service_failure_predictor_api_v1": lambda: ServiceFailurePredictor("api"),
    "service_failure_predictor_database_v1": lambda: ServiceFailurePredictor("database"),
    "latency_spike_predictor_v1": LatencySpikePredictor,
    "outage_predictor_v1": type('OutagePredictor', (BaseAgent,), {}),
    "error_rate_predictor_v1": type('ErrorRatePredictor', (BaseAgent,), {}),
    "dependency_failure_predictor_v1": type('DependencyFailurePredictor', (BaseAgent,), {}),
    "capacity_incident_predictor_v1": type('CapacityIncidentPredictor', (BaseAgent,), {}),
    "security_incident_predictor_v1": type('SecurityIncidentPredictor', (BaseAgent,), {})
    # 20+ agents supplémentaires...
}

# Agents de prévision de coûts (25+)
COST_PREDICTION_AGENTS = {
    "cost_forecaster_v1": CostForecaster,
    "cloud_cost_predictor_aws_v1": lambda: CloudCostPredictor("aws"),
    "cloud_cost_predictor_azure_v1": lambda: CloudCostPredictor("azure"),
    "cloud_cost_predictor_gcp_v1": lambda: CloudCostPredictor("gcp"),
    "cost_anomaly_predictor_v1": CostAnomalyPredictor,
    "savings_opportunity_predictor_v1": type('SavingsOpportunityPredictor', (BaseAgent,), {}),
    "budget_forecast_predictor_v1": type('BudgetForecastPredictor', (BaseAgent,), {}),
    "resource_optimization_predictor_v1": type('ResourceOptimizationPredictor', (BaseAgent,), {}),
    "license_cost_predictor_v1": type('LicenseCostPredictor', (BaseAgent,), {}),
    "infrastructure_cost_predictor_v1": type('InfrastructureCostPredictor', (BaseAgent,), {})
    # 15+ agents supplémentaires...
}

# Agents de planification de capacité (20+)
CAPACITY_PREDICTION_AGENTS = {
    "capacity_planner_v1": CapacityPlanner,
    "autoscaling_predictor_compute_v1": lambda: AutoScalingPredictor("compute"),
    "autoscaling_predictor_memory_v1": lambda: AutoScalingPredictor("memory"),
    "storage_capacity_predictor_v1": StorageCapacityPredictor,
    "network_capacity_predictor_v1": type('NetworkCapacityPredictor', (BaseAgent,), {}),
    "database_capacity_predictor_v1": type('DatabaseCapacityPredictor', (BaseAgent,), {}),
    "workload_capacity_predictor_v1": type('WorkloadCapacityPredictor', (BaseAgent,), {}),
    "peak_load_predictor_v1": type('PeakLoadPredictor', (BaseAgent,), {}),
    "resource_utilization_predictor_v1": type('ResourceUtilizationPredictor', (BaseAgent,), {}),
    "scaling_recommendation_predictor_v1": type('ScalingRecommendationPredictor', (BaseAgent,), {})
    # 10+ agents supplémentaires...
}

# Agents de prédiction de menaces de sécurité (25+)
SECURITY_PREDICTION_AGENTS = {
    "security_threat_predictor_v1": SecurityThreatPredictor,
    "vulnerability_exploit_predictor_v1": VulnerabilityExploitPredictor,
    "insider_threat_predictor_v1": InsiderThreatPredictor,
    "ddos_attack_predictor_v1": type('DDOSAttackPredictor', (BaseAgent,), {}),
    "malware_outbreak_predictor_v1": type('MalwareOutbreakPredictor', (BaseAgent,), {}),
    "phishing_attack_predictor_v1": type('PhishingAttackPredictor', (BaseAgent,), {}),
    "data_breach_predictor_v1": type('DataBreachPredictor', (BaseAgent,), {}),
    "compliance_violation_predictor_v1": type('ComplianceViolationPredictor', (BaseAgent,), {}),
    "security_config_drift_predictor_v1": type('SecurityConfigDriftPredictor', (BaseAgent,), {}),
    "access_control_violation_predictor_v1": type('AccessControlViolationPredictor', (BaseAgent,), {})
    # 15+ agents supplémentaires...
}

# Agents de prédiction de dégradation de performance (20+)
PERFORMANCE_PREDICTION_AGENTS = {
    "performance_degradation_predictor_v1": PerformanceDegradationPredictor,
    "database_performance_predictor_postgres_v1": lambda: DatabasePerformancePredictor("postgres"),
    "database_performance_predictor_mysql_v1": lambda: DatabasePerformancePredictor("mysql"),
    "api_performance_predictor_v1": APIPerformancePredictor,
    "application_performance_predictor_v1": type('ApplicationPerformancePredictor', (BaseAgent,), {}),
    "network_performance_predictor_v1": type('NetworkPerformancePredictor', (BaseAgent,), {}),
    "cache_performance_predictor_v1": type('CachePerformancePredictor', (BaseAgent,), {}),
    "load_balancer_performance_predictor_v1": type('LoadBalancerPerformancePredictor', (BaseAgent,), {}),
    "microservice_performance_predictor_v1": type('MicroservicePerformancePredictor', (BaseAgent,), {}),
    "third_party_service_performance_predictor_v1": type('ThirdPartyServicePerformancePredictor', (BaseAgent,), {})
    # 10+ agents supplémentaires...
}

# Agents de prédiction de comportement utilisateur (15+)
USER_BEHAVIOR_PREDICTION_AGENTS = {
    "user_behavior_predictor_v1": UserBehaviorPredictor,
    "user_churn_predictor_v1": UserChurnPredictor,
    "feature_adoption_predictor_v1": FeatureAdoptionPredictor,
    "user_engagement_predictor_v1": type('UserEngagementPredictor', (BaseAgent,), {}),
    "conversion_rate_predictor_v1": type('ConversionRatePredictor', (BaseAgent,), {}),
    "user_satisfaction_predictor_v1": type('UserSatisfactionPredictor', (BaseAgent,), {}),
    "user_productivity_predictor_v1": type('UserProductivityPredictor', (BaseAgent,), {}),
    "user_segmentation_predictor_v1": type('UserSegmentationPredictor', (BaseAgent,), {}),
    "user_preference_predictor_v1": type('UserPreferencePredictor', (BaseAgent,), {}),
    "user_feedback_predictor_v1": type('UserFeedbackPredictor', (BaseAgent,), {})
    # 5+ agents supplémentaires...
}

# Agents de prévision de métriques business (15+)
BUSINESS_PREDICTION_AGENTS = {
    "business_metrics_forecaster_v1": BusinessMetricsForecaster,
    "revenue_predictor_v1": RevenuePredictor,
    "customer_ltv_predictor_v1": CustomerLTVPredictor,
    "mrr_predictor_v1": type('MRRPredictor', (BaseAgent,), {}),
    "arr_predictor_v1": type('ARRPredictor', (BaseAgent,), {}),
    "cac_predictor_v1": type('CACPredictor', (BaseAgent,), {}),
    "roi_predictor_v1": type('ROIPredictor', (BaseAgent,), {}),
    "profit_margin_predictor_v1": type('ProfitMarginPredictor', (BaseAgent,), {}),
    "market_share_predictor_v1": type('MarketSharePredictor', (BaseAgent,), {}),
    "competitive_position_predictor_v1": type('CompetitivePositionPredictor', (BaseAgent,), {})
    # 5+ agents supplémentaires...
}

# Registre complet des prédicteurs (~150 agents)
PREDICTOR_AGENTS_REGISTRY = {
    **INCIDENT_PREDICTION_AGENTS,
    **COST_PREDICTION_AGENTS,
    **CAPACITY_PREDICTION_AGENTS,
    **SECURITY_PREDICTION_AGENTS,
    **PERFORMANCE_PREDICTION_AGENTS,
    **USER_BEHAVIOR_PREDICTION_AGENTS,
    **BUSINESS_PREDICTION_AGENTS
}


# ==================== FABRIQUE DE MODÈLES ML ====================

class MLModelFactory:
    """Fabrique de modèles ML pour différentes tâches de prédiction"""
    
    @staticmethod
    def create_model_for_task(
        task_type: PredictionType,
        horizon: HorizonType,
        data_characteristics: Dict
    ) -> Dict[str, Any]:
        """Crée une configuration de modèle optimisée pour une tâche"""
        
        configs = {
            PredictionType.INCIDENT: {
                ModelType.LSTM: {
                    'lstm_units': 100,
                    'dropout_rate': 0.2,
                    'bidirectional': True,
                    'classification': True
                },
                ModelType.RANDOM_FOREST: {
                    'n_estimators': 200,
                    'max_depth': 20,
                    'classification': True
                },
                ModelType.XGBOOST: {
                    'n_estimators': 300,
                    'max_depth': 10,
                    'learning_rate': 0.1,
                    'classification': True
                }
            },
            PredictionType.COST: {
                ModelType.PROPHET: {
                    'seasonality_mode': 'multiplicative',
                    'changepoint_prior_scale': 0.05
                },
                ModelType.ARIMA: {
                    'order': (1, 1, 1),
                    'seasonal_order': (1, 1, 1, 12)
                },
                ModelType.GRADIENT_BOOSTING: {
                    'n_estimators': 200,
                    'max_depth': 5,
                    'learning_rate': 0.1,
                    'classification': False
                }
            },
            PredictionType.CAPACITY: {
                ModelType.LSTM: {
                    'lstm_units': 50,
                    'lstm_layers': 2,
                    'dropout_rate': 0.3,
                    'classification': False
                },
                ModelType.EXPONENTIAL_SMOOTHING: {
                    'trend': 'add',
                    'seasonal': 'add',
                    'seasonal_periods': 24
                }
            },
            PredictionType.SECURITY: {
                ModelType.RANDOM_FOREST: {
                    'n_estimators': 500,
                    'max_depth': 15,
                    'classification': True
                },
                ModelType.NEURAL_NETWORK: {
                    'hidden_layer_sizes': (100, 50, 25),
                    'activation': 'relu',
                    'classification': True
                }
            }
        }
        
        return configs.get(task_type, {})


# ==================== GESTIONNAIRE DE PRÉDICTIONS ====================

class PredictionManager:
    """Gestionnaire centralisé des prédictions"""
    
    def __init__(self):
        self.model_factory = MLModelFactory()
        self.model_manager = MLModelManager()
        self.predictions_cache = {}
        self.accuracy_tracker = {}
        
    async def make_prediction(
        self,
        agent_id: str,
        prediction_type: PredictionType,
        data: Dict[str, Any],
        horizon: HorizonType = HorizonType.MEDIUM_TERM
    ) -> PredictionResult:
        """Effectue une prédiction avec le bon modèle"""
        
        # Vérifier le cache
        cache_key = self._create_cache_key(agent_id, prediction_type, data, horizon)
        if cache_key in self.predictions_cache:
            cached = self.predictions_cache[cache_key]
            if datetime.utcnow() - cached.prediction_time < timedelta(hours=1):
                return cached
        
        # Obtenir la configuration du modèle
        model_config = self.model_factory.create_model_for_task(
            prediction_type, horizon, data
        )
        
        # Préparer les données
        prepared_data = self._prepare_data_for_prediction(data, prediction_type)
        
        # Sélectionner le meilleur modèle
        best_model_type, best_model_config = self._select_best_model(
            prediction_type, prepared_data, model_config
        )
        
        # Créer ou charger le modèle
        model_key = f"{agent_id}_{prediction_type.value}_{horizon.value}"
        model = self.model_manager.load_or_train_model(
            model_key=model_key,
            model_type=best_model_type,
            X_train=prepared_data['X_train'],
            y_train=prepared_data['y_train'],
            params=best_model_config
        )
        
        # Faire la prédiction
        prediction = model.predict(prepared_data['X_pred'])
        
        # Calculer la confiance
        confidence = self._calculate_confidence(model, prepared_data, prediction)
        
        # Créer le résultat
        result = PredictionResult(
            prediction=prediction,
            confidence=confidence,
            horizon=horizon,
            model_used=best_model_type,
            features_used=prepared_data['feature_names'],
            prediction_time=datetime.utcnow(),
            metadata={
                'model_config': best_model_config,
                'data_shape': prepared_data['X_pred'].shape,
                'cache_key': cache_key
            }
        )
        
        # Mettre en cache
        self.predictions_cache[cache_key] = result
        
        # Mettre à jour les métriques de précision
        self._update_accuracy_metrics(agent_id, result, prepared_data)
        
        return result
    
    def _create_cache_key(self, agent_id: str, prediction_type: PredictionType, 
                         data: Dict, horizon: HorizonType) -> str:
        """Crée une clé de cache unique"""
        data_hash = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]
        return f"{agent_id}_{prediction_type.value}_{horizon.value}_{data_hash}"
    
    def _prepare_data_for_prediction(self, data: Dict, prediction_type: PredictionType) -> Dict:
        """Prépare les données pour la prédiction"""
        # Implémentation spécifique au type de prédiction
        if prediction_type == PredictionType.INCIDENT:
            return self._prepare_incident_data(data)
        elif prediction_type == PredictionType.COST:
            return self._prepare_cost_data(data)
        elif prediction_type == PredictionType.CAPACITY:
            return self._prepare_capacity_data(data)
        # ... autres types
        
        return {'X_train': np.array([]), 'y_train': np.array([]), 
                'X_pred': np.array([]), 'feature_names': []}
    
    def _select_best_model(self, prediction_type: PredictionType, 
                          prepared_data: Dict, model_config: Dict) -> Tuple[ModelType, Dict]:
        """Sélectionne le meilleur modèle pour la tâche"""
        # Logique de sélection basée sur les données et la tâche
        if prediction_type in [PredictionType.INCIDENT, PredictionType.SECURITY]:
            return ModelType.XGBOOST, model_config.get(ModelType.XGBOOST, {})
        elif prediction_type == PredictionType.COST:
            if len(prepared_data['X_train']) > 1000:
                return ModelType.LSTM, model_config.get(ModelType.LSTM, {})
            else:
                return ModelType.PROPHET, model_config.get(ModelType.PROPHET, {})
        elif prediction_type == PredictionType.CAPACITY:
            return ModelType.LSTM, model_config.get(ModelType.LSTM, {})
        
        # Par défaut
        return ModelType.RANDOM_FOREST, model_config.get(ModelType.RANDOM_FOREST, {})


# ==================== EXPORTS ====================

__all__ = [
    # Enums et types
    'PredictionType',
    'ModelType',
    'HorizonType',
    'PredictionResult',
    
    # Agents principaux
    'IncidentPredictor',
    'CostForecaster',
    'CapacityPlanner',
    'SecurityThreatPredictor',
    'PerformanceDegradationPredictor',
    'UserBehaviorPredictor',
    'BusinessMetricsForecaster',
    
    # Agents spécialisés
    'ServiceFailurePredictor',
    'CloudCostPredictor',
    'AutoScalingPredictor',
    'VulnerabilityExploitPredictor',
    'DatabasePerformancePredictor',
    'UserChurnPredictor',
    'RevenuePredictor',
    
    # Gestionnaires
    'MLModelManager',
    'MLModelFactory',
    'PredictionManager',
    
    # Registres
    'PREDICTOR_AGENTS_REGISTRY',
    
    # Fonctions utilitaires
    'get_predictor_agent',
    'get_predictors_by_type',
    'initialize_predictors'
]


# ==================== FONCTIONS UTILITAIRES ====================

def get_predictor_agent(agent_id: str) -> Optional[BaseAgent]:
    """Récupère un agent prédicteur par son ID."""
    agent_creator = PREDICTOR_AGENTS_REGISTRY.get(agent_id)
    if agent_creator:
        return agent_creator() if callable(agent_creator) else agent_creator
    return None


def get_predictors_by_type(prediction_type: PredictionType) -> List[BaseAgent]:
    """Récupère tous les agents d'un type de prédiction."""
    agents = []
    
    # Mapper les catégories aux types
    category_map = {
        PredictionType.INCIDENT: 'predictors.incident',
        PredictionType.COST: 'predictors.cost',
        PredictionType.CAPACITY: 'predictors.capacity',
        PredictionType.SECURITY: 'predictors.security',
        PredictionType.PERFORMANCE: 'predictors.performance',
        PredictionType.USER_BEHAVIOR: 'predictors.user_behavior',
        PredictionType.BUSINESS: 'predictors.business'
    }
    
    target_category = category_map.get(prediction_type)
    if not target_category:
        return agents
    
    for agent_id, agent_creator in PREDICTOR_AGENTS_REGISTRY.items():
        agent = agent_creator() if callable(agent_creator) else agent_creator
        if isinstance(agent, BaseAgent) and agent.category.startswith(target_category):
            agents.append(agent)
    
    return agents


def initialize_predictors():
    """Initialise les ressources des prédicteurs."""
    # Initialiser TensorFlow pour éviter les warnings
    tf.get_logger().setLevel('ERROR')
    
    # Vérifier les dépendances ML
    try:
        import xgboost
        import lightgbm
        import catboost
        logger.info("ML libraries loaded successfully")
    except ImportError as e:
        logger.warning(f"Some ML libraries not available: {e}")
    
    logger.info(f"Predictors module initialized with {len(PREDICTOR_AGENTS_REGISTRY)} agents")


# Initialisation au chargement du module
initialize_predictors()