"""
Cost Anomaly Detector Agent
Detects anomalous spending patterns across multi-cloud environments.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, validator
from scipy import stats
from sklearn.ensemble import IsolationForest
from sklearn.cluster import DBSCAN
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.arima.model import ARIMA
import tensorflow as tf
from tensorflow import keras

from ..base.agent import BaseAgent, AgentResult, AgentContext
from ...business_value.forecast.cost_forecaster import CostForecastResult

logger = logging.getLogger(__name__)


class AnomalyType(Enum):
    """Types of cost anomalies."""
    SPIKE = "spike"  # Sudden increase in spending
    DIP = "dip"  # Sudden decrease in spending
    TREND_CHANGE = "trend_change"  # Change in spending trend
    SEASONAL_DEVIATION = "seasonal_deviation"  # Deviation from seasonal pattern
    VOLUME_ANOMALY = "volume_anomaly"  # Anomaly in resource usage volume
    PRICE_ANOMALY = "price_anomaly"  # Unusual pricing change


class DetectionMethod(Enum):
    """Statistical methods for anomaly detection."""
    Z_SCORE = "z_score"
    IQR = "iqr"
    ISOLATION_FOREST = "isolation_forest"
    SEASONAL_DECOMPOSITION = "seasonal_decomposition"
    EXPONENTIAL_SMOOTHING = "exponential_smoothing"
    ARIMA = "arima"
    LSTM = "lstm"
    CLUSTERING = "clustering"


@dataclass
class CostDataPoint:
    """Represents a single cost data point."""
    timestamp: datetime
    cost: float
    currency: str = "USD"
    cloud_provider: Optional[str] = None
    service: Optional[str] = None
    region: Optional[str] = None
    tags: Dict[str, str] = None
    resource_id: Optional[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = {}


class DetectionConfig(BaseModel):
    """Configuration for anomaly detection."""
    
    class Config:
        arbitrary_types_allowed = True
    
    # Statistical detection parameters
    z_score_threshold: float = Field(3.0, ge=0.0, description="Z-score threshold for anomalies")
    iqr_multiplier: float = Field(1.5, ge=0.0, description="IQR multiplier for outlier detection")
    
    # ML model parameters
    isolation_forest_contamination: float = Field(0.1, ge=0.0, le=0.5)
    lstm_sequence_length: int = Field(30, ge=7, le=365)
    arima_order: Tuple[int, int, int] = Field((1, 1, 1))
    
    # Time series parameters
    seasonal_period: int = Field(7, ge=1, description="Seasonal period in days")
    forecast_horizon: int = Field(30, ge=1, le=365, description="Forecast horizon in days")
    
    # Cost allocation
    enable_tag_analysis: bool = True
    tag_importance_threshold: float = Field(0.7, ge=0.0, le=1.0)
    
    # Optimization parameters
    reserved_instance_threshold: float = Field(0.8, ge=0.0, le=1.0)
    savings_plan_coverage_target: float = Field(0.7, ge=0.0, le=1.0)
    
    # Budget parameters
    budget_variance_threshold: float = Field(0.1, ge=0.0, le=1.0)
    
    @validator("arima_order")
    def validate_arima_order(cls, v: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Validate ARIMA order parameters."""
        p, d, q = v
        if p < 0 or d < 0 or q < 0:
            raise ValueError("ARIMA order parameters must be non-negative")
        return v


class AnomalyResult(BaseModel):
    """Result of anomaly detection."""
    
    class Config:
        arbitrary_types_allowed = True
    
    anomaly_id: str
    timestamp: datetime
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    anomaly_type: AnomalyType
    detection_method: DetectionMethod
    confidence: float = Field(..., ge=0.0, le=1.0)
    
    # Cost information
    actual_cost: float
    expected_cost: Optional[float] = None
    deviation_percentage: float
    absolute_deviation: float
    
    # Context information
    cloud_provider: Optional[str] = None
    service: Optional[str] = None
    region: Optional[str] = None
    resource_id: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)
    
    # Analysis
    root_cause: Optional[str] = None
    impact_score: float = Field(0.0, ge=0.0, le=10.0)
    recommendations: List[str] = Field(default_factory=list)
    
    # Metadata
    model_metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @property
    def is_significant(self) -> bool:
        """Check if anomaly is significant based on confidence and deviation."""
        return self.confidence > 0.7 and abs(self.deviation_percentage) > 0.1


class OptimizationRecommendation(BaseModel):
    """Cost optimization recommendation."""
    
    class Config:
        arbitrary_types_allowed = True
    
    recommendation_id: str
    category: str  # "reserved_instances", "savings_plans", "idle_resources", "right_sizing"
    description: str
    estimated_savings: float
    confidence: float = Field(..., ge=0.0, le=1.0)
    implementation_effort: str  # "low", "medium", "high"
    payback_period_days: Optional[int] = None
    risk_level: str = "low"
    
    # Resource details
    cloud_provider: Optional[str] = None
    service: Optional[str] = None
    resource_ids: List[str] = Field(default_factory=list)
    
    # Implementation steps
    steps: List[str] = Field(default_factory=list)
    
    @property
    def roi(self) -> Optional[float]:
        """Calculate return on investment if payback period is known."""
        if self.payback_period_days:
            # Simple ROI: annualized savings / implementation effort factor
            effort_factor = {"low": 1.0, "medium": 1.5, "high": 2.0}
            annual_savings = self.estimated_savings * (365 / self.payback_period_days)
            return annual_savings / effort_factor.get(self.implementation_effort, 1.0)
        return None


class MultiCloudCostAnalysis(BaseModel):
    """Analysis results across multiple cloud providers."""
    
    class Config:
        arbitrary_types_allowed = True
    
    total_cost: float
    cost_by_provider: Dict[str, float]
    cost_by_service: Dict[str, float]
    cost_by_region: Dict[str, float]
    
    # Efficiency metrics
    cost_per_unit: Dict[str, float]  # e.g., cost per request, cost per GB
    utilization_rates: Dict[str, float]
    
    # Comparison metrics
    provider_comparison: Dict[str, Dict[str, float]]  # Cross-provider cost comparison
    optimization_potential: Dict[str, float]  # Potential savings by provider


class TagBasedAnalysis(BaseModel):
    """Analysis based on resource tags."""
    
    class Config:
        arbitrary_types_allowed = True
    
    tag_coverage: float  # Percentage of resources with tags
    top_cost_tags: List[Tuple[str, float]]  # (tag_key:tag_value, cost)
    untagged_resources_cost: float
    tag_compliance_score: float
    
    # Cost allocation
    cost_by_department: Dict[str, float]
    cost_by_project: Dict[str, float]
    cost_by_environment: Dict[str, float]
    
    # Anomalies by tag
    anomalies_by_tag: Dict[str, List[AnomalyResult]]


class BudgetForecast(BaseModel):
    """Budget forecast results."""
    
    class Config:
        arbitrary_types_allowed = True
    
    current_spend: float
    forecasted_spend: float
    budget_amount: float
    variance_percentage: float
    days_remaining: int
    burn_rate: float  # Daily spend rate
    
    # Forecast methods
    forecast_method: str
    confidence_interval: Tuple[float, float]
    
    # Risk assessment
    overspend_probability: float
    recommendations: List[str]


class CostAnomalyDetector(BaseAgent):
    """
    Advanced cost anomaly detection agent with ML capabilities.
    Implements statistical and machine learning methods for detecting
    anomalous spending patterns across multi-cloud environments.
    """
    
    def __init__(self, context: AgentContext):
        super().__init__(context)
        self.config = DetectionConfig()
        self._lstm_model: Optional[keras.Model] = None
        self._isolation_forest_model: Optional[IsolationForest] = None
        
    async def execute(self) -> AgentResult:
        """Execute cost anomaly detection."""
        try:
            logger.info(f"Starting cost anomaly detection for suite: {self.context.suite_id}")
            
            # Step 1: Collect cost data from multiple sources
            cost_data = await self._collect_cost_data()
            if not cost_data:
                return AgentResult(
                    success=False,
                    error="No cost data available",
                    metadata={"agent": "cost_anomaly_detector"}
                )
            
            # Step 2: Perform multi-cloud cost analysis
            multi_cloud_analysis = self._analyze_multi_cloud_costs(cost_data)
            
            # Step 3: Perform tag-based analysis
            tag_analysis = self._analyze_tags(cost_data)
            
            # Step 4: Detect anomalies using multiple methods
            anomalies = await self._detect_anomalies(cost_data)
            
            # Step 5: Generate optimization recommendations
            recommendations = await self._generate_recommendations(
                cost_data, anomalies, multi_cloud_analysis
            )
            
            # Step 6: Forecast budget
            budget_forecast = await self._forecast_budget(cost_data)
            
            # Step 7: Prepare detailed report
            report = self._prepare_detailed_report(
                anomalies=anomalies,
                recommendations=recommendations,
                multi_cloud_analysis=multi_cloud_analysis,
                tag_analysis=tag_analysis,
                budget_forecast=budget_forecast,
            )
            
            logger.info(f"Cost anomaly detection completed. Found {len(anomalies)} anomalies.")
            
            return AgentResult(
                success=True,
                output=report,
                metadata={
                    "agent": "cost_anomaly_detector",
                    "anomalies_detected": len(anomalies),
                    "estimated_savings": sum(r.estimated_savings for r in recommendations),
                    "processing_time": self._get_processing_time(),
                }
            )
            
        except Exception as e:
            logger.error(f"Cost anomaly detection failed: {e}")
            return AgentResult(
                success=False,
                error=str(e),
                metadata={"agent": "cost_anomaly_detector"}
            )
    
    async def _collect_cost_data(self) -> List[CostDataPoint]:
        """Collect cost data from multiple cloud providers."""
        cost_data = []
        
        # Collect from AWS
        aws_data = await self._collect_aws_cost_data()
        cost_data.extend(aws_data)
        
        # Collect from Azure
        azure_data = await self._collect_azure_cost_data()
        cost_data.extend(azure_data)
        
        # Collect from GCP
        gcp_data = await self._collect_gcp_cost_data()
        cost_data.extend(gcp_data)
        
        # Sort by timestamp
        cost_data.sort(key=lambda x: x.timestamp)
        
        logger.info(f"Collected {len(cost_data)} cost data points")
        return cost_data
    
    async def _collect_aws_cost_data(self) -> List[CostDataPoint]:
        """Collect cost data from AWS Cost Explorer."""
        # In production, this would integrate with AWS SDK
        # For now, return mock data or use context data
        if "aws_cost_data" in self.context.data:
            return self._parse_cost_data(self.context.data["aws_cost_data"], "aws")
        
        # Generate mock data for demonstration
        return self._generate_mock_cost_data("aws", days=90)
    
    async def _collect_azure_cost_data(self) -> List[CostDataPoint]:
        """Collect cost data from Azure Cost Management."""
        if "azure_cost_data" in self.context.data:
            return self._parse_cost_data(self.context.data["azure_cost_data"], "azure")
        return self._generate_mock_cost_data("azure", days=90)
    
    async def _collect_gcp_cost_data(self) -> List[CostDataPoint]:
        """Collect cost data from Google Cloud Billing."""
        if "gcp_cost_data" in self.context.data:
            return self._parse_cost_data(self.context.data["gcp_cost_data"], "gcp")
        return self._generate_mock_cost_data("gcp", days=90)
    
    def _parse_cost_data(self, raw_data: Any, provider: str) -> List[CostDataPoint]:
        """Parse raw cost data into standardized format."""
        # Implementation would depend on the actual data format
        # This is a placeholder implementation
        cost_points = []
        
        if isinstance(raw_data, list):
            for item in raw_data:
                try:
                    cost_point = CostDataPoint(
                        timestamp=datetime.fromisoformat(item["timestamp"]),
                        cost=float(item["cost"]),
                        currency=item.get("currency", "USD"),
                        cloud_provider=provider,
                        service=item.get("service"),
                        region=item.get("region"),
                        tags=item.get("tags", {}),
                        resource_id=item.get("resource_id"),
                    )
                    cost_points.append(cost_point)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Failed to parse cost data point: {e}")
        
        return cost_points
    
    def _generate_mock_cost_data(self, provider: str, days: int = 90) -> List[CostDataPoint]:
        """Generate mock cost data for testing."""
        cost_points = []
        base_date = datetime.utcnow() - timedelta(days=days)
        
        # Different patterns for different providers
        patterns = {
            "aws": {"base": 1000, "trend": 1.02, "seasonality": 200},
            "azure": {"base": 800, "trend": 1.015, "seasonality": 150},
            "gcp": {"base": 600, "trend": 1.01, "seasonality": 100},
        }
        
        pattern = patterns.get(provider, patterns["aws"])
        
        for i in range(days):
            date = base_date + timedelta(days=i)
            
            # Base cost with trend
            base_cost = pattern["base"] * (pattern["trend"] ** (i / 30))
            
            # Add seasonality (weekly pattern)
            seasonal_factor = pattern["seasonality"] * np.sin(2 * np.pi * i / 7)
            
            # Add some random noise
            noise = np.random.normal(0, base_cost * 0.05)
            
            # Combine components
            cost = base_cost + seasonal_factor + noise
            
            # Add some anomalies
            if i % 30 == 15:  # Spike every ~30 days
                cost *= 1.5
            elif i % 45 == 30:  # Dip every ~45 days
                cost *= 0.7
            
            cost_point = CostDataPoint(
                timestamp=date,
                cost=max(cost, 0),  # Ensure non-negative
                currency="USD",
                cloud_provider=provider,
                service=self._get_random_service(provider),
                region=self._get_random_region(provider),
                tags=self._generate_mock_tags(),
                resource_id=f"{provider}-resource-{i:04d}",
            )
            cost_points.append(cost_point)
        
        return cost_points
    
    def _get_random_service(self, provider: str) -> str:
        """Get random service name for provider."""
        services = {
            "aws": ["EC2", "S3", "RDS", "Lambda", "CloudFront"],
            "azure": ["Virtual Machines", "Blob Storage", "SQL Database", "Functions", "CDN"],
            "gcp": ["Compute Engine", "Cloud Storage", "Cloud SQL", "Cloud Functions", "CDN"],
        }
        return np.random.choice(services.get(provider, ["Unknown"]))
    
    def _get_random_region(self, provider: str) -> str:
        """Get random region for provider."""
        regions = {
            "aws": ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"],
            "azure": ["eastus", "westus2", "westeurope", "southeastasia"],
            "gcp": ["us-central1", "europe-west1", "asia-southeast1"],
        }
        return np.random.choice(regions.get(provider, ["unknown"]))
    
    def _generate_mock_tags(self) -> Dict[str, str]:
        """Generate mock resource tags."""
        departments = ["engineering", "marketing", "sales", "finance"]
        projects = ["project-alpha", "project-beta", "project-gamma"]
        environments = ["production", "staging", "development"]
        
        return {
            "department": np.random.choice(departments),
            "project": np.random.choice(projects),
            "environment": np.random.choice(environments),
            "owner": f"user-{np.random.randint(1, 100):03d}",
        }
    
    def _analyze_multi_cloud_costs(self, cost_data: List[CostDataPoint]) -> MultiCloudCostAnalysis:
        """Analyze costs across multiple cloud providers."""
        # Convert to DataFrame for analysis
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        
        # Calculate total cost
        total_cost = df["cost"].sum()
        
        # Cost by provider
        cost_by_provider = df.groupby("cloud_provider")["cost"].sum().to_dict()
        
        # Cost by service
        cost_by_service = df.groupby("service")["cost"].sum().to_dict()
        
        # Cost by region
        cost_by_region = df.groupby("region")["cost"].sum().to_dict()
        
        # Calculate efficiency metrics (simplified)
        cost_per_unit = {}
        utilization_rates = {}
        
        # Provider comparison
        provider_comparison = {}
        for provider in cost_by_provider.keys():
            provider_data = df[df["cloud_provider"] == provider]
            provider_comparison[provider] = {
                "avg_daily_cost": provider_data["cost"].mean(),
                "cost_growth_rate": self._calculate_growth_rate(provider_data["cost"]),
                "service_diversity": provider_data["service"].nunique(),
            }
        
        # Optimization potential
        optimization_potential = {}
        for provider, cost in cost_by_provider.items():
            # Simple heuristic: providers with >20% of total cost have optimization potential
            if cost / total_cost > 0.2:
                optimization_potential[provider] = cost * 0.15  # Assume 15% savings potential
        
        return MultiCloudCostAnalysis(
            total_cost=total_cost,
            cost_by_provider=cost_by_provider,
            cost_by_service=cost_by_service,
            cost_by_region=cost_by_region,
            cost_per_unit=cost_per_unit,
            utilization_rates=utilization_rates,
            provider_comparison=provider_comparison,
            optimization_potential=optimization_potential,
        )
    
    def _analyze_tags(self, cost_data: List[CostDataPoint]) -> TagBasedAnalysis:
        """Analyze costs based on resource tags."""
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        
        # Calculate tag coverage
        tagged_resources = df[df["tags"].apply(lambda x: bool(x))]
        tag_coverage = len(tagged_resources) / len(df) if len(df) > 0 else 0
        
        # Calculate cost by tag combinations
        cost_by_department = {}
        cost_by_project = {}
        cost_by_environment = {}
        
        for _, row in df.iterrows():
            tags = row["tags"]
            if tags:
                dept = tags.get("department", "untagged")
                proj = tags.get("project", "untagged")
                env = tags.get("environment", "untagged")
                
                cost_by_department[dept] = cost_by_department.get(dept, 0) + row["cost"]
                cost_by_project[proj] = cost_by_project.get(proj, 0) + row["cost"]
                cost_by_environment[env] = cost_by_environment.get(env, 0) + row["cost"]
        
        # Top cost tags
        all_tag_costs = {}
        for _, row in df.iterrows():
            for key, value in row["tags"].items():
                tag_key = f"{key}:{value}"
                all_tag_costs[tag_key] = all_tag_costs.get(tag_key, 0) + row["cost"]
        
        top_cost_tags = sorted(all_tag_costs.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Untagged resources cost
        untagged_resources = df[df["tags"].apply(lambda x: not x)]
        untagged_cost = untagged_resources["cost"].sum()
        
        # Tag compliance score
        required_tags = {"department", "project", "environment"}
        compliant_resources = 0
        
        for _, row in df.iterrows():
            tags = set(row["tags"].keys())
            if required_tags.issubset(tags):
                compliant_resources += 1
        
        tag_compliance_score = compliant_resources / len(df) if len(df) > 0 else 0
        
        return TagBasedAnalysis(
            tag_coverage=tag_coverage,
            top_cost_tags=top_cost_tags,
            untagged_resources_cost=untagged_cost,
            tag_compliance_score=tag_compliance_score,
            cost_by_department=cost_by_department,
            cost_by_project=cost_by_project,
            cost_by_environment=cost_by_environment,
            anomalies_by_tag={},  # Would be populated by anomaly detection
        )
    
    async def _detect_anomalies(self, cost_data: List[CostDataPoint]) -> List[AnomalyResult]:
        """Detect anomalies using multiple statistical and ML methods."""
        anomalies = []
        
        # Convert to time series
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        df.set_index("timestamp", inplace=True)
        
        # Resample to daily frequency
        daily_costs = df["cost"].resample("D").sum()
        
        # Method 1: Z-Score detection
        z_score_anomalies = self._detect_z_score_anomalies(daily_costs)
        anomalies.extend(z_score_anomalies)
        
        # Method 2: IQR detection
        iqr_anomalies = self._detect_iqr_anomalies(daily_costs)
        anomalies.extend(iqr_anomalies)
        
        # Method 3: Isolation Forest
        isolation_forest_anomalies = await self._detect_isolation_forest_anomalies(daily_costs)
        anomalies.extend(isolation_forest_anomalies)
        
        # Method 4: Seasonal Decomposition
        seasonal_anomalies = self._detect_seasonal_anomalies(daily_costs)
        anomalies.extend(seasonal_anomalies)
        
        # Method 5: Exponential Smoothing
        exp_smoothing_anomalies = await self._detect_exponential_smoothing_anomalies(daily_costs)
        anomalies.extend(exp_smoothing_anomalies)
        
        # Method 6: ARIMA
        arima_anomalies = await self._detect_arima_anomalies(daily_costs)
        anomalies.extend(arima_anomalies)
        
        # Method 7: LSTM (if enough data)
        if len(daily_costs) > self.config.lstm_sequence_length * 2:
            lstm_anomalies = await self._detect_lstm_anomalies(daily_costs)
            anomalies.extend(lstm_anomalies)
        
        # Remove duplicates (same timestamp, similar detection)
        anomalies = self._deduplicate_anomalies(anomalies)
        
        # Add context from original data
        anomalies = self._enrich_anomalies_with_context(anomalies, cost_data)
        
        return anomalies
    
    def _detect_z_score_anomalies(self, time_series: pd.Series) -> List[AnomalyResult]:
        """Detect anomalies using Z-score method."""
        anomalies = []
        z_scores = np.abs(stats.zscore(time_series.fillna(0)))
        
        for idx, (timestamp, cost) in enumerate(time_series.items()):
            z_score = z_scores[idx]
            
            if z_score > self.config.z_score_threshold:
                expected = time_series.mean()
                deviation = (cost - expected) / expected if expected != 0 else 0
                
                anomaly = AnomalyResult(
                    anomaly_id=f"zscore_{timestamp.date()}",
                    timestamp=timestamp,
                    anomaly_type=AnomalyType.SPIKE if deviation > 0 else AnomalyType.DIP,
                    detection_method=DetectionMethod.Z_SCORE,
                    confidence=min(z_score / self.config.z_score_threshold, 1.0),
                    actual_cost=cost,
                    expected_cost=expected,
                    deviation_percentage=deviation,
                    absolute_deviation=cost - expected,
                    impact_score=self._calculate_impact_score(cost, expected),
                    recommendations=[
                        "Review recent deployments or usage changes",
                        "Check for misconfigured auto-scaling",
                    ],
                    model_metadata={"z_score": float(z_score)},
                )
                anomalies.append(anomaly)
        
        return anomalies
    
    def _detect_iqr_anomalies(self, time_series: pd.Series) -> List[AnomalyResult]:
        """Detect anomalies using Interquartile Range (IQR) method."""
        anomalies = []
        
        Q1 = time_series.quantile(0.25)
        Q3 = time_series.quantile(0.75)
        IQR = Q3 - Q1
        
        lower_bound = Q1 - self.config.iqr_multiplier * IQR
        upper_bound = Q3 + self.config.iqr_multiplier * IQR
        
        for timestamp, cost in time_series.items():
            if cost < lower_bound or cost > upper_bound:
                expected = time_series.median()
                deviation = (cost - expected) / expected if expected != 0 else 0
                anomaly_type = AnomalyType.DIP if cost < lower_bound else AnomalyType.SPIKE
                
                # Calculate confidence based on distance from bounds
                if cost > upper_bound:
                    distance_ratio = (cost - upper_bound) / (time_series.max() - upper_bound)
                else:
                    distance_ratio = (lower_bound - cost) / (lower_bound - time_series.min())
                
                anomaly = AnomalyResult(
                    anomaly_id=f"iqr_{timestamp.date()}",
                    timestamp=timestamp,
                    anomaly_type=anomaly_type,
                    detection_method=DetectionMethod.IQR,
                    confidence=min(distance_ratio, 1.0),
                    actual_cost=cost,
                    expected_cost=expected,
                    deviation_percentage=deviation,
                    absolute_deviation=cost - expected,
                    impact_score=self._calculate_impact_score(cost, expected),
                    recommendations=[
                        "Analyze resource usage patterns",
                        "Check for data egress or API call spikes",
                    ],
                    model_metadata={
                        "Q1": float(Q1),
                        "Q3": float(Q3),
                        "IQR": float(IQR),
                        "bounds": (float(lower_bound), float(upper_bound)),
                    },
                )
                anomalies.append(anomaly)
        
        return anomalies
    
    async def _detect_isolation_forest_anomalies(self, time_series: pd.Series) -> List[AnomalyResult]:
        """Detect anomalies using Isolation Forest algorithm."""
        anomalies = []
        
        try:
            # Prepare features: value and rolling statistics
            features = pd.DataFrame({
                "value": time_series.values,
                "rolling_mean": time_series.rolling(window=7, min_periods=1).mean().values,
                "rolling_std": time_series.rolling(window=7, min_periods=1).std().fillna(0).values,
                "day_of_week": time_series.index.dayofweek.values,
            }).fillna(0)
            
            # Train Isolation Forest
            self._isolation_forest_model = IsolationForest(
                contamination=self.config.isolation_forest_contamination,
                random_state=42,
                n_estimators=100,
            )
            
            predictions = self._isolation_forest_model.fit_predict(features)
            anomaly_scores = -self._isolation_forest_model.score_samples(features)
            
            for idx, (timestamp, cost) in enumerate(time_series.items()):
                if predictions[idx] == -1:  # -1 indicates anomaly
                    expected = time_series.mean()
                    deviation = (cost - expected) / expected if expected != 0 else 0
                    
                    anomaly = AnomalyResult(
                        anomaly_id=f"iforest_{timestamp.date()}",
                        timestamp=timestamp,
                        anomaly_type=AnomalyType.SPIKE if deviation > 0 else AnomalyType.DIP,
                        detection_method=DetectionMethod.ISOLATION_FOREST,
                        confidence=min(anomaly_scores[idx] / anomaly_scores.max(), 1.0),
                        actual_cost=cost,
                        expected_cost=expected,
                        deviation_percentage=deviation,
                        absolute_deviation=cost - expected,
                        impact_score=self._calculate_impact_score(cost, expected),
                        recommendations=[
                            "Investigate unusual resource consumption patterns",
                            "Review access logs for suspicious activity",
                        ],
                        model_metadata={
                            "anomaly_score": float(anomaly_scores[idx]),
                            "contamination": self.config.isolation_forest_contamination,
                        },
                    )
                    anomalies.append(anomaly)
        
        except Exception as e:
            logger.error(f"Isolation Forest detection failed: {e}")
        
        return anomalies
    
    def _detect_seasonal_anomalies(self, time_series: pd.Series) -> List[AnomalyResult]:
        """Detect anomalies using seasonal-trend decomposition."""
        anomalies = []
        
        try:
            # Perform STL decomposition
            stl = STL(
                time_series.fillna(time_series.mean()),
                period=self.config.seasonal_period,
                robust=True,
            )
            result = stl.fit()
            
            # Calculate residuals
            residuals = result.resid
            
            # Detect anomalies in residuals
            residual_mean = residuals.mean()
            residual_std = residuals.std()
            
            for idx, (timestamp, cost) in enumerate(time_series.items()):
                residual = residuals.iloc[idx]
                z_score_residual = abs(residual - residual_mean) / residual_std if residual_std > 0 else 0
                
                if z_score_residual > self.config.z_score_threshold:
                    seasonal_component = result.seasonal.iloc[idx]
                    trend_component = result.trend.iloc[idx]
                    expected = trend_component + seasonal_component
                    
                    deviation = (cost - expected) / expected if expected != 0 else 0
                    
                    anomaly = AnomalyResult(
                        anomaly_id=f"seasonal_{timestamp.date()}",
                        timestamp=timestamp,
                        anomaly_type=AnomalyType.SEASONAL_DEVIATION,
                        detection_method=DetectionMethod.SEASONAL_DECOMPOSITION,
                        confidence=min(z_score_residual / self.config.z_score_threshold, 1.0),
                        actual_cost=cost,
                        expected_cost=expected,
                        deviation_percentage=deviation,
                        absolute_deviation=cost - expected,
                        impact_score=self._calculate_impact_score(cost, expected),
                        recommendations=[
                            "Check for deviations from typical weekly patterns",
                            "Review scheduled jobs or batch processes",
                        ],
                        model_metadata={
                            "residual": float(residual),
                            "seasonal": float(seasonal_component),
                            "trend": float(trend_component),
                        },
                    )
                    anomalies.append(anomaly)
        
        except Exception as e:
            logger.error(f"Seasonal decomposition failed: {e}")
        
        return anomalies
    
    async def _detect_exponential_smoothing_anomalies(self, time_series: pd.Series) -> List[AnomalyResult]:
        """Detect anomalies using exponential smoothing forecasts."""
        anomalies = []
        
        try:
            # Fit exponential smoothing model
            model = ExponentialSmoothing(
                time_series.fillna(time_series.mean()),
                seasonal_periods=self.config.seasonal_period,
                trend="add",
                seasonal="add",
            )
            fitted_model = model.fit()
            
            # Get predictions
            forecast = fitted_model.forecast(steps=1)
            last_forecast = forecast.iloc[-1]
            last_actual = time_series.iloc[-1]
            
            # Calculate prediction interval (simplified)
            residuals = fitted_model.resid.dropna()
            std_residual = residuals.std()
            
            if std_residual > 0:
                z_score = abs(last_actual - last_forecast) / std_residual
                
                if z_score > self.config.z_score_threshold:
                    deviation = (last_actual - last_forecast) / last_forecast if last_forecast != 0 else 0
                    
                    anomaly = AnomalyResult(
                        anomaly_id=f"expsmooth_{time_series.index[-1].date()}",
                        timestamp=time_series.index[-1],
                        anomaly_type=AnomalyType.SPIKE if deviation > 0 else AnomalyType.DIP,
                        detection_method=DetectionMethod.EXPONENTIAL_SMOOTHING,
                        confidence=min(z_score / self.config.z_score_threshold, 1.0),
                        actual_cost=last_actual,
                        expected_cost=last_forecast,
                        deviation_percentage=deviation,
                        absolute_deviation=last_actual - last_forecast,
                        impact_score=self._calculate_impact_score(last_actual, last_forecast),
                        recommendations=[
                            "Review recent forecast accuracy",
                            "Check for one-time charges or billing changes",
                        ],
                        model_metadata={
                            "forecast": float(last_forecast),
                            "residual_std": float(std_residual),
                            "smoothing_params": fitted_model.params,
                        },
                    )
                    anomalies.append(anomaly)
        
        except Exception as e:
            logger.error(f"Exponential smoothing detection failed: {e}")
        
        return anomalies
    
    async def _detect_arima_anomalies(self, time_series: pd.Series) -> List[AnomalyResult]:
        """Detect anomalies using ARIMA model."""
        anomalies = []
        
        try:
            # Fit ARIMA model
            model = ARIMA(
                time_series.fillna(time_series.mean()),
                order=self.config.arima_order,
            )
            fitted_model = model.fit()
            
            # Get predictions
            forecast = fitted_model.forecast(steps=1)
            last_forecast = forecast.iloc[-1]
            last_actual = time_series.iloc[-1]
            
            # Get prediction standard error
            forecast_results = fitted_model.get_forecast(steps=1)
            forecast_se = forecast_results.se_mean.iloc[-1]
            
            if forecast_se > 0:
                z_score = abs(last_actual - last_forecast) / forecast_se
                
                if z_score > self.config.z_score_threshold:
                    deviation = (last_actual - last_forecast) / last_forecast if last_forecast != 0 else 0
                    
                    anomaly = AnomalyResult(
                        anomaly_id=f"arima_{time_series.index[-1].date()}",
                        timestamp=time_series.index[-1],
                        anomaly_type=AnomalyType.SPIKE if deviation > 0 else AnomalyType.DIP,
                        detection_method=DetectionMethod.ARIMA,
                        confidence=min(z_score / self.config.z_score_threshold, 1.0),
                        actual_cost=last_actual,
                        expected_cost=last_forecast,
                        deviation_percentage=deviation,
                        absolute_deviation=last_actual - last_forecast,
                        impact_score=self._calculate_impact_score(last_actual, last_forecast),
                        recommendations=[
                            "Analyze time series patterns for structural breaks",
                            "Review external factors affecting costs",
                        ],
                        model_metadata={
                            "forecast": float(last_forecast),
                            "standard_error": float(forecast_se),
                            "arima_order": self.config.arima_order,
                            "aic": float(fitted_model.aic),
                        },
                    )
                    anomalies.append(anomaly)
        
        except Exception as e:
            logger.error(f"ARIMA detection failed: {e}")
        
        return anomalies
    
    async def _detect_lstm_anomalies(self, time_series: pd.Series) -> List[AnomalyResult]:
        """Detect anomalies using LSTM neural network."""
        anomalies = []
        
        try:
            # Prepare sequences
            sequence_length = self.config.lstm_sequence_length
            values = time_series.values.reshape(-1, 1)
            
            # Normalize
            from sklearn.preprocessing import MinMaxScaler
            scaler = MinMaxScaler()
            scaled_values = scaler.fit_transform(values)
            
            # Create sequences
            X, y = [], []
            for i in range(len(scaled_values) - sequence_length):
                X.append(scaled_values[i:i + sequence_length])
                y.append(scaled_values[i + sequence_length])
            
            if len(X) < 10:  # Not enough data for LSTM
                return anomalies
            
            X = np.array(X)
            y = np.array(y)
            
            # Build LSTM model
            self._lstm_model = keras.Sequential([
                keras.layers.LSTM(50, return_sequences=True, input_shape=(sequence_length, 1)),
                keras.layers.Dropout(0.2),
                keras.layers.LSTM(50, return_sequences=False),
                keras.layers.Dropout(0.2),
                keras.layers.Dense(25),
                keras.layers.Dense(1),
            ])
            
            self._lstm_model.compile(optimizer="adam", loss="mse")
            
            # Train model
            self._lstm_model.fit(
                X, y,
                epochs=10,
                batch_size=32,
                verbose=0,
                validation_split=0.1,
            )
            
            # Make predictions
            predictions = self._lstm_model.predict(X, verbose=0)
            
            # Calculate errors
            errors = y - predictions.flatten()
            error_mean = errors.mean()
            error_std = errors.std()
            
            # Detect anomalies in the last prediction
            last_error = errors[-1]
            if error_std > 0:
                z_score = abs(last_error - error_mean) / error_std
                
                if z_score > self.config.z_score_threshold:
                    last_actual = time_series.iloc[-1]
                    last_predicted = scaler.inverse_transform(predictions[-1].reshape(-1, 1))[0, 0]
                    
                    deviation = (last_actual - last_predicted) / last_predicted if last_predicted != 0 else 0
                    
                    anomaly = AnomalyResult(
                        anomaly_id=f"lstm_{time_series.index[-1].date()}",
                        timestamp=time_series.index[-1],
                        anomaly_type=AnomalyType.SPIKE if deviation > 0 else AnomalyType.DIP,
                        detection_method=DetectionMethod.LSTM,
                        confidence=min(z_score / self.config.z_score_threshold, 1.0),
                        actual_cost=last_actual,
                        expected_cost=last_predicted,
                        deviation_percentage=deviation,
                        absolute_deviation=last_actual - last_predicted,
                        impact_score=self._calculate_impact_score(last_actual, last_predicted),
                        recommendations=[
                            "Investigate complex temporal patterns",
                            "Review ML model performance for cost forecasting",
                        ],
                        model_metadata={
                            "prediction_error": float(last_error),
                            "model_architecture": "LSTM",
                            "sequence_length": sequence_length,
                        },
                    )
                    anomalies.append(anomaly)
        
        except Exception as e:
            logger.error(f"LSTM detection failed: {e}")
        
        return anomalies
    
    def _deduplicate_anomalies(self, anomalies: List[AnomalyResult]) -> List[AnomalyResult]:
        """Remove duplicate anomalies detected by multiple methods."""
        # Group by timestamp
        anomalies_by_timestamp = {}
        for anomaly in anomalies:
            key = anomaly.timestamp.date()
            if key not in anomalies_by_timestamp:
                anomalies_by_timestamp[key] = []
            anomalies_by_timestamp[key].append(anomaly)
        
        # Keep the highest confidence anomaly for each timestamp
        deduplicated = []
        for timestamp_anomalies in anomalies_by_timestamp.values():
            highest_confidence = max(timestamp_anomalies, key=lambda x: x.confidence)
            
            # Combine recommendations from all methods
            all_recommendations = set()
            for anomaly in timestamp_anomalies:
                all_recommendations.update(anomaly.recommendations)
            highest_confidence.recommendations = list(all_recommendations)
            
            # Update detection method to reflect multiple methods
            if len(timestamp_anomalies) > 1:
                highest_confidence.detection_method = DetectionMethod.ISOLATION_FOREST  # Default to ML method
                highest_confidence.model_metadata["combined_methods"] = [
                    a.detection_method.value for a in timestamp_anomalies
                ]
            
            deduplicated.append(highest_confidence)
        
        return deduplicated
    
    def _enrich_anomalies_with_context(
        self, 
        anomalies: List[AnomalyResult], 
        cost_data: List[CostDataPoint]
    ) -> List[AnomalyResult]:
        """Add context from original cost data to anomalies."""
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        
        for anomaly in anomalies:
            # Find cost data points for this timestamp
            anomaly_date = anomaly.timestamp.date()
            daily_data = df[
                df["timestamp"].dt.date == anomaly_date
            ]
            
            if not daily_data.empty:
                # Add cloud provider info
                providers = daily_data["cloud_provider"].unique()
                if len(providers) == 1:
                    anomaly.cloud_provider = providers[0]
                
                # Add service info
                top_service = daily_data.groupby("service")["cost"].sum().idxmax()
                anomaly.service = top_service
                
                # Add tags from highest cost resource
                highest_cost_row = daily_data.loc[daily_data["cost"].idxmax()]
                anomaly.tags = highest_cost_row["tags"]
                anomaly.resource_id = highest_cost_row["resource_id"]
                
                # Determine root cause based on patterns
                anomaly.root_cause = self._determine_root_cause(anomaly, daily_data)
        
        return anomalies
    
    def _determine_root_cause(self, anomaly: AnomalyResult, daily_data: pd.DataFrame) -> str:
        """Determine likely root cause of anomaly."""
        # Analyze patterns in daily data
        if anomaly.cloud_provider == "aws" and "EC2" in str(anomaly.service):
            return "EC2 instance scaling or reserved instance expiration"
        elif anomaly.cloud_provider == "azure" and "Virtual Machines" in str(anomaly.service):
            return "Azure VM resize or increased usage"
        elif anomaly.cloud_provider == "gcp" and "Compute Engine" in str(anomaly.service):
            return "GCE instance preemption or sustained use discount change"
        elif "S3" in str(anomaly.service) or "Storage" in str(anomaly.service):
            return "Data transfer or storage class change"
        elif "Data" in str(anomaly.service) or "Database" in str(anomaly.service):
            return "Database scaling or backup storage"
        
        return "Unknown - requires manual investigation"
    
    def _calculate_impact_score(self, actual: float, expected: float) -> float:
        """Calculate impact score for anomaly."""
        if expected == 0:
            return 10.0 if actual > 0 else 0.0
        
        deviation_ratio = abs(actual - expected) / expected
        
        # Scale to 0-10 range with logarithmic scaling
        impact = min(10.0, deviation_ratio * 5.0)
        
        # Boost impact for large absolute deviations
        if abs(actual - expected) > 10000:  # More than $10k deviation
            impact = min(10.0, impact + 2.0)
        
        return impact
    
    async def _generate_recommendations(
        self,
        cost_data: List[CostDataPoint],
        anomalies: List[AnomalyResult],
        multi_cloud_analysis: MultiCloudCostAnalysis,
    ) -> List[OptimizationRecommendation]:
        """Generate cost optimization recommendations."""
        recommendations = []
        
        # 1. Reserved Instance optimization
        ri_recommendations = await self._analyze_reserved_instances(cost_data)
        recommendations.extend(ri_recommendations)
        
        # 2. Savings Plans analysis
        sp_recommendations = await self._analyze_savings_plans(cost_data)
        recommendations.extend(sp_recommendations)
        
        # 3. Idle resource identification
        idle_recommendations = await self._identify_idle_resources(cost_data)
        recommendations.extend(idle_recommendations)
        
        # 4. Right-sizing recommendations
        rightsizing_recommendations = await self._analyze_right_sizing(cost_data)
        recommendations.extend(rightsizing_recommendations)
        
        # 5. Multi-cloud optimization
        multicloud_recommendations = self._generate_multicloud_recommendations(multi_cloud_analysis)
        recommendations.extend(multicloud_recommendations)
        
        # 6. Anomaly-specific recommendations
        anomaly_recommendations = self._generate_anomaly_recommendations(anomalies)
        recommendations.extend(anomaly_recommendations)
        
        # Sort by estimated savings
        recommendations.sort(key=lambda x: x.estimated_savings, reverse=True)
        
        return recommendations
    
    async def _analyze_reserved_instances(self, cost_data: List[CostDataPoint]) -> List[OptimizationRecommendation]:
        """Analyze reserved instance optimization opportunities."""
        recommendations = []
        
        # Group by service and region
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        
        for (provider, service, region), group in df.groupby(["cloud_provider", "service", "region"]):
            # Calculate usage patterns
            total_cost = group["cost"].sum()
            avg_daily_cost = group["cost"].mean()
            
            # Check if reserved instances would be beneficial
            # Simplified logic: if stable usage > threshold, recommend RI
            cost_std = group["cost"].std()
            cost_cv = cost_std / avg_daily_cost if avg_daily_cost > 0 else 0
            
            if avg_daily_cost > 100 and cost_cv < 0.3:  # Stable usage over $100/day
                # Assume 40% savings with RIs
                estimated_savings = total_cost * 0.4
                
                recommendation = OptimizationRecommendation(
                    recommendation_id=f"ri_{provider}_{service}_{region}",
                    category="reserved_instances",
                    description=f"Purchase Reserved Instances for {service} in {region} ({provider})",
                    estimated_savings=estimated_savings,
                    confidence=0.8 - cost_cv,  # Higher confidence for more stable usage
                    implementation_effort="medium",
                    payback_period_days=int(365 * 0.3),  # 30% ROI per year
                    risk_level="low",
                    cloud_provider=provider,
                    service=service,
                    resource_ids=group["resource_id"].unique().tolist(),
                    steps=[
                        f"Analyze 1-year usage forecast for {service}",
                        f"Purchase {provider} Reserved Instances for optimal term",
                        f"Monitor utilization and adjust as needed",
                    ],
                )
                recommendations.append(recommendation)
        
        return recommendations
    
    async def _analyze_savings_plans(self, cost_data: List[CostDataPoint]) -> List[OptimizationRecommendation]:
        """Analyze Savings Plans optimization opportunities."""
        recommendations = []
        
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        
        # Analyze by provider
        for provider, group in df.groupby("cloud_provider"):
            total_cost = group["cost"].sum()
            eligible_services = ["EC2", "Lambda", "Fargate"] if provider == "aws" else ["Compute"]
            
            # Check if services are eligible for Savings Plans
            eligible_cost = group[group["service"].isin(eligible_services)]["cost"].sum()
            coverage_ratio = eligible_cost / total_cost if total_cost > 0 else 0
            
            if coverage_ratio > self.config.savings_plan_coverage_target:
                # Assume 20% savings with Savings Plans
                estimated_savings = eligible_cost * 0.2
                
                recommendation = OptimizationRecommendation(
                    recommendation_id=f"sp_{provider}",
                    category="savings_plans",
                    description=f"Purchase {provider} Savings Plans for compute services",
                    estimated_savings=estimated_savings,
                    confidence=min(coverage_ratio, 0.9),
                    implementation_effort="low",
                    payback_period_days=int(365 * 0.25),  # 25% ROI per year
                    risk_level="low",
                    cloud_provider=provider,
                    service="Compute",
                    resource_ids=group["resource_id"].unique().tolist(),
                    steps=[
                        f"Analyze 1-year compute usage on {provider}",
                        f"Purchase appropriate Savings Plans commitment",
                        f"Monitor commitment utilization monthly",
                    ],
                )
                recommendations.append(recommendation)
        
        return recommendations
    
    async def _identify_idle_resources(self, cost_data: List[CostDataPoint]) -> List[OptimizationRecommendation]:
        """Identify idle resources that can be terminated."""
        recommendations = []
        
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        
        # Identify low-utilization resources (simplified heuristic)
        for resource_id, group in df.groupby("resource_id"):
            avg_daily_cost = group["cost"].mean()
            cost_variance = group["cost"].std() / avg_daily_cost if avg_daily_cost > 0 else 0
            
            # Flag as idle if low cost and low variance (steady low usage)
            if avg_daily_cost < 10 and cost_variance < 0.1:
                total_cost = group["cost"].sum()
                estimated_savings = total_cost * 0.9  # Assume 90% savings by terminating
                
                recommendation = OptimizationRecommendation(
                    recommendation_id=f"idle_{resource_id}",
                    category="idle_resources",
                    description=f"Terminate idle resource: {resource_id}",
                    estimated_savings=estimated_savings,
                    confidence=0.7,
                    implementation_effort="low",
                    payback_period_days=1,  # Immediate savings
                    risk_level="medium",
                    cloud_provider=group["cloud_provider"].iloc[0],
                    service=group["service"].iloc[0],
                    resource_ids=[resource_id],
                    steps=[
                        f"Verify resource {resource_id} is not in use",
                        "Create backup if needed",
                        f"Terminate resource in {group['cloud_provider'].iloc[0]} console",
                        "Update infrastructure documentation",
                    ],
                )
                recommendations.append(recommendation)
        
        return recommendations
    
    async def _analyze_right_sizing(self, cost_data: List[CostDataPoint]) -> List[OptimizationRecommendation]:
        """Analyze right-sizing opportunities."""
        recommendations = []
        
        # This would integrate with cloud provider utilization metrics
        # For now, generate placeholder recommendations
        
        recommendation = OptimizationRecommendation(
            recommendation_id="rightsize_general",
            category="right_sizing",
            description="Right-size underutilized EC2 instances",
            estimated_savings=5000.0,
            confidence=0.75,
            implementation_effort="medium",
            payback_period_days=30,
            risk_level="low",
            cloud_provider="aws",
            service="EC2",
            resource_ids=[],
            steps=[
                "Analyze CloudWatch metrics for CPU and memory utilization",
                "Identify instances with <30% utilization",
                "Downsize instances to appropriate instance types",
                "Test in staging environment before production",
            ],
        )
        recommendations.append(recommendation)
        
        return recommendations
    
    def _generate_multicloud_recommendations(
        self, 
        analysis: MultiCloudCostAnalysis
    ) -> List[OptimizationRecommendation]:
        """Generate multi-cloud optimization recommendations."""
        recommendations = []
        
        # Find providers with highest optimization potential
        for provider, potential in analysis.optimization_potential.items():
            if potential > 1000:  # More than $1000 potential savings
                recommendation = OptimizationRecommendation(
                    recommendation_id=f"multicloud_{provider}",
                    category="multi_cloud",
                    description=f"Optimize {provider} spending through cost allocation and monitoring",
                    estimated_savings=potential,
                    confidence=0.6,
                    implementation_effort="high",
                    payback_period_days=90,
                    risk_level="medium",
                    cloud_provider=provider,
                    service="All",
                    resource_ids=[],
                    steps=[
                        f"Implement detailed cost allocation tagging on {provider}",
                        f"Set up budget alerts and monitoring for {provider}",
                        f"Review and optimize storage classes on {provider}",
                        f"Consider reserved capacity purchases on {provider}",
                    ],
                )
                recommendations.append(recommendation)
        
        return recommendations
    
    def _generate_anomaly_recommendations(
        self, 
        anomalies: List[AnomalyResult]
    ) -> List[OptimizationRecommendation]:
        """Generate recommendations based on detected anomalies."""
        recommendations = []
        
        significant_anomalies = [a for a in anomalies if a.is_significant]
        
        if significant_anomalies:
            total_impact = sum(a.impact_score for a in significant_anomalies)
            avg_deviation = np.mean([abs(a.deviation_percentage) for a in significant_anomalies])
            
            recommendation = OptimizationRecommendation(
                recommendation_id="anomaly_response",
                category="anomaly_management",
                description=f"Implement anomaly detection and response system",
                estimated_savings=total_impact * 1000,  # Rough estimate
                confidence=min(avg_deviation, 0.9),
                implementation_effort="medium",
                payback_period_days=60,
                risk_level="low",
                steps=[
                    "Set up real-time cost monitoring",
                    "Configure automated alerts for anomalies",
                    "Create runbooks for common anomaly types",
                    "Implement automated remediation where possible",
                ],
            )
            recommendations.append(recommendation)
        
        return recommendations
    
    async def _forecast_budget(self, cost_data: List[CostDataPoint]) -> BudgetForecast:
        """Forecast budget based on historical spending."""
        df = pd.DataFrame([vars(cd) for cd in cost_data])
        df.set_index("timestamp", inplace=True)
        
        # Calculate daily spending
        daily_spend = df["cost"].resample("D").sum()
        
        # Simple exponential smoothing forecast
        model = ExponentialSmoothing(
            daily_spend.fillna(daily_spend.mean()),
            trend="add",
            seasonal="add",
            seasonal_periods=30,
        )
        fitted_model = model.fit()
        
        # Forecast next 30 days
        forecast = fitted_model.forecast(steps=30)
        forecasted_spend = forecast.sum()
        
        # Calculate current metrics
        current_spend = daily_spend.sum()
        today = datetime.utcnow().date()
        month_start = today.replace(day=1)
        days_in_month = (today - month_start).days + 1
        days_remaining = 30 - days_in_month
        
        # Calculate burn rate
        burn_rate = current_spend / days_in_month if days_in_month > 0 else 0
        
        # Calculate variance (assuming budget is 10% above current trend)
        budget_amount = current_spend * 1.1
        variance_percentage = (forecasted_spend - budget_amount) / budget_amount
        
        # Calculate overspend probability (simplified)
        overspend_probability = min(max(variance_percentage, 0), 1) if variance_percentage > 0 else 0
        
        # Generate recommendations
        recommendations = []
        if variance_percentage > self.config.budget_variance_threshold:
            recommendations.append("Consider implementing spending limits")
            recommendations.append("Review and optimize largest cost centers")
        if burn_rate > budget_amount / 30:
            recommendations.append("Reduce daily burn rate through optimization")
        
        return BudgetForecast(
            current_spend=current_spend,
            forecasted_spend=forecasted_spend,
            budget_amount=budget_amount,
            variance_percentage=variance_percentage,
            days_remaining=days_remaining,
            burn_rate=burn_rate,
            forecast_method="exponential_smoothing",
            confidence_interval=(forecasted_spend * 0.9, forecasted_spend * 1.1),
            overspend_probability=overspend_probability,
            recommendations=recommendations,
        )
    
    def _prepare_detailed_report(
        self,
        anomalies: List[AnomalyResult],
        recommendations: List[OptimizationRecommendation],
        multi_cloud_analysis: MultiCloudCostAnalysis,
        tag_analysis: TagBasedAnalysis,
        budget_forecast: BudgetForecast,
    ) -> Dict[str, Any]:
        """Prepare comprehensive cost analysis report."""
        
        # Calculate total potential savings
        total_potential_savings = sum(r.estimated_savings for r in recommendations)
        
        # Significant anomalies
        significant_anomalies = [a for a in anomalies if a.is_significant]
        
        # Top recommendations by ROI
        recommendations_with_roi = [
            r for r in recommendations if r.roi is not None
        ]
        top_recommendations = sorted(
            recommendations_with_roi,
            key=lambda x: x.roi or 0,
            reverse=True
        )[:5]
        
        return {
            "summary": {
                "total_anomalies_detected": len(anomalies),
                "significant_anomalies": len(significant_anomalies),
                "total_potential_savings": total_potential_savings,
                "optimization_recommendations": len(recommendations),
                "budget_risk": "high" if budget_forecast.overspend_probability > 0.7 else "medium",
            },
            "anomalies": {
                "by_type": self._group_anomalies_by_type(anomalies),
                "by_provider": self._group_anomalies_by_provider(anomalies),
                "significant_anomalies": [
                    a.dict() for a in significant_anomalies
                ],
            },
            "optimization": {
                "total_potential_savings": total_potential_savings,
                "by_category": self._group_recommendations_by_category(recommendations),
                "top_recommendations": [
                    {
                        "description": r.description,
                        "estimated_savings": r.estimated_savings,
                        "roi": r.roi,
                        "implementation_effort": r.implementation_effort,
                    }
                    for r in top_recommendations
                ],
            },
            "multi_cloud_analysis": multi_cloud_analysis.dict(),
            "tag_analysis": tag_analysis.dict(),
            "budget_forecast": budget_forecast.dict(),
            "methodology": {
                "detection_methods_used": list(set(a.detection_method.value for a in anomalies)),
                "statistical_thresholds": {
                    "z_score_threshold": self.config.z_score_threshold,
                    "iqr_multiplier": self.config.iqr_multiplier,
                },
                "ml_models_used": ["isolation_forest", "lstm"] if self._lstm_model else ["isolation_forest"],
            },
            "timestamp": datetime.utcnow().isoformat(),
            "agent_version": "1.0.0",
        }
    
    def _group_anomalies_by_type(self, anomalies: List[AnomalyResult]) -> Dict[str, int]:
        """Group anomalies by type."""
        grouped = {}
        for anomaly in anomalies:
            key = anomaly.anomaly_type.value
            grouped[key] = grouped.get(key, 0) + 1
        return grouped
    
    def _group_anomalies_by_provider(self, anomalies: List[AnomalyResult]) -> Dict[str, int]:
        """Group anomalies by cloud provider."""
        grouped = {}
        for anomaly in anomalies:
            provider = anomaly.cloud_provider or "unknown"
            grouped[provider] = grouped.get(provider, 0) + 1
        return grouped
    
    def _group_recommendations_by_category(
        self, 
        recommendations: List[OptimizationRecommendation]
    ) -> Dict[str, Dict[str, Any]]:
        """Group recommendations by category."""
        grouped = {}
        for rec in recommendations:
            if rec.category not in grouped:
                grouped[rec.category] = {
                    "count": 0,
                    "total_savings": 0.0,
                    "avg_confidence": 0.0,
                }
            
            grouped[rec.category]["count"] += 1
            grouped[rec.category]["total_savings"] += rec.estimated_savings
            grouped[rec.category]["avg_confidence"] = (
                grouped[rec.category]["avg_confidence"] * (grouped[rec.category]["count"] - 1) + rec.confidence
            ) / grouped[rec.category]["count"]
        
        return grouped
    
    def _calculate_growth_rate(self, series: pd.Series) -> float:
        """Calculate growth rate of a time series."""
        if len(series) < 2:
            return 0.0
        
        try:
            # Simple linear regression on log values for percentage growth
            x = np.arange(len(series))
            y = np.log(series.replace(0, np.nan).fillna(method="ffill").values)
            
            # Remove any remaining NaN/inf values
            mask = np.isfinite(y)
            if np.sum(mask) < 2:
                return 0.0
            
            x_clean = x[mask]
            y_clean = y[mask]
            
            if len(x_clean) < 2:
                return 0.0
            
            slope, _ = np.polyfit(x_clean, y_clean, 1)
            
            # Convert to daily percentage growth
            daily_growth = np.exp(slope) - 1
            
            # Annualize
            annual_growth = (1 + daily_growth) ** 365 - 1
            
            return annual_growth
            
        except Exception as e:
            logger.warning(f"Failed to calculate growth rate: {e}")
            return 0.0
    
    def _get_processing_time(self) -> float:
        """Calculate processing time for the agent."""
        # This would track actual execution time
        # For now, return placeholder
        return 5.0  # seconds