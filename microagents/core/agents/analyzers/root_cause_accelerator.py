"""
Root Cause Accelerator Agent
Advanced root cause analysis using correlation, ML, and causal inference.
Accelerates incident resolution by identifying root causes with confidence scores.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, validator
from scipy import stats
from scipy.sparse import csr_matrix
from scipy.spatial.distance import cosine
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
import causalnex
from causalnex.structure import StructureModel
from causalnex.network import BayesianNetwork

from ..base.agent import BaseAgent, AgentResult, AgentContext

logger = logging.getLogger(__name__)


class RCAEntityType(Enum):
    """Types of entities in root cause analysis."""
    SERVICE = "service"
    CONTAINER = "container"
    POD = "pod"
    NODE = "node"
    DATABASE = "database"
    QUEUE = "queue"
    API_ENDPOINT = "api_endpoint"
    CACHE = "cache"
    LOAD_BALANCER = "load_balancer"
    NETWORK = "network"
    CONFIGURATION = "configuration"
    DEPENDENCY = "dependency"


class EvidenceType(Enum):
    """Types of evidence for root cause analysis."""
    METRIC_ANOMALY = "metric_anomaly"
    LOG_ERROR = "log_error"
    TRACE_ERROR = "trace_error"
    CONFIG_CHANGE = "config_change"
    DEPLOYMENT = "deployment"
    INCIDENT_HISTORY = "incident_history"
    DEPENDENCY_FAILURE = "dependency_failure"
    ALERT = "alert"
    USER_REPORT = "user_report"
    PERFORMANCE_DEGRADATION = "performance_degradation"


class HypothesisStatus(Enum):
    """Status of a root cause hypothesis."""
    GENERATED = "generated"
    TESTING = "testing"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass
class RCAEntity:
    """Entity involved in root cause analysis."""
    entity_id: str
    entity_type: RCAEntityType
    name: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)  # IDs of dependent entities
    metrics: Dict[str, float] = field(default_factory=dict)
    health_score: float = 1.0  # 0.0-1.0, higher is healthier
    
    def __post_init__(self):
        if self.health_score < 0.0 or self.health_score > 1.0:
            raise ValueError("health_score must be between 0.0 and 1.0")


@dataclass
class Evidence:
    """Evidence for root cause analysis."""
    evidence_id: str
    evidence_type: EvidenceType
    timestamp: datetime
    source: str
    entity_id: Optional[str] = None
    description: str = ""
    severity: float = 0.5  # 0.0-1.0
    confidence: float = 0.8  # 0.0-1.0
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.severity < 0.0 or self.severity > 1.0:
            raise ValueError("severity must be between 0.0 and 1.0")
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass
class CorrelationResult:
    """Result of correlation analysis."""
    entity_a: str
    entity_b: str
    correlation_score: float  # -1.0 to 1.0
    correlation_type: str  # "pearson", "spearman", "cross_correlation", "granger"
    lag: Optional[timedelta] = None  # Time lag if applicable
    p_value: Optional[float] = None  # Statistical significance
    is_causal: bool = False  # Whether correlation suggests causality
    
    @property
    def is_significant(self) -> bool:
        """Check if correlation is statistically significant."""
        return self.p_value is not None and self.p_value < 0.05
    
    @property
    def absolute_strength(self) -> float:
        """Get absolute strength of correlation."""
        return abs(self.correlation_score)


class Hypothesis(BaseModel):
    """Root cause hypothesis."""
    
    class Config:
        arbitrary_types_allowed = True
    
    hypothesis_id: str
    description: str
    root_cause_entity_id: str
    root_cause_type: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    status: HypothesisStatus = HypothesisStatus.GENERATED
    
    # Supporting evidence
    supporting_evidence: List[str] = Field(default_factory=list)  # Evidence IDs
    contradicting_evidence: List[str] = Field(default_factory=list)
    explanation: str = ""
    
    # Causal chain
    causal_chain: List[str] = Field(default_factory=list)  # Entity IDs in causal order
    impact_radius: int = 0  # How many entities are affected
    
    # Statistical measures
    bayesian_probability: Optional[float] = None
    statistical_significance: Optional[float] = None
    
    # ML classification
    ml_confidence: Optional[float] = None
    feature_importance: Dict[str, float] = Field(default_factory=dict)
    
    # Recommendations
    remediation_actions: List[str] = Field(default_factory=list)
    verification_steps: List[str] = Field(default_factory=list)
    
    # Metadata
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    
    @validator("confidence")
    def validate_confidence(cls, v: float) -> float:
        """Validate confidence score."""
        if v < 0.0 or v > 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v
    
    @property
    def is_high_confidence(self) -> bool:
        """Check if hypothesis has high confidence."""
        return self.confidence > 0.7
    
    @property
    def overall_confidence(self) -> float:
        """Calculate overall confidence combining multiple factors."""
        base_confidence = self.confidence
        
        # Boost for ML confidence if available
        if self.ml_confidence is not None:
            base_confidence = (base_confidence + self.ml_confidence) / 2
        
        # Boost for Bayesian probability if available
        if self.bayesian_probability is not None:
            base_confidence = (base_confidence + self.bayesian_probability) / 2
        
        # Penalize for contradicting evidence
        if self.contradicting_evidence:
            penalty = len(self.contradicting_evidence) * 0.1
            base_confidence = max(0.0, base_confidence - penalty)
        
        return min(1.0, base_confidence)


class TimelineEvent(BaseModel):
    """Event in the incident timeline."""
    
    class Config:
        arbitrary_types_allowed = True
    
    event_id: str
    timestamp: datetime
    entity_id: str
    event_type: str
    description: str
    severity: float = Field(0.5, ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    related_events: List[str] = Field(default_factory=list)
    
    @property
    def is_critical(self) -> bool:
        """Check if event is critical."""
        return self.severity > 0.7


class RCAResult(BaseModel):
    """Complete root cause analysis result."""
    
    class Config:
        arbitrary_types_allowed = True
    
    incident_id: str
    analysis_start: datetime
    analysis_end: datetime
    
    # Entities and evidence
    entities: Dict[str, RCAEntity] = Field(default_factory=dict)
    evidence: Dict[str, Evidence] = Field(default_factory=dict)
    
    # Analysis results
    correlation_matrix: Dict[str, Dict[str, CorrelationResult]] = Field(default_factory=dict)
    dependency_graph: Dict[str, List[str]] = Field(default_factory=dict)
    timeline: List[TimelineEvent] = Field(default_factory=list)
    
    # Hypotheses
    hypotheses: List[Hypothesis] = Field(default_factory=list)
    top_hypothesis: Optional[Hypothesis] = None
    
    # Patterns
    similar_incidents: List[Dict[str, Any]] = Field(default_factory=list)
    recurring_patterns: List[str] = Field(default_factory=list)
    
    # Statistical insights
    statistical_tests: Dict[str, float] = Field(default_factory=dict)
    ml_predictions: Dict[str, float] = Field(default_factory=dict)
    
    # Recommendations
    immediate_actions: List[str] = Field(default_factory=list)
    long_term_recommendations: List[str] = Field(default_factory=list)
    
    # Metadata
    analysis_methods_used: List[str] = Field(default_factory=list)
    confidence_score: float = Field(0.0, ge=0.0, le=1.0)
    
    @property
    def is_conclusive(self) -> bool:
        """Check if analysis reached a conclusive result."""
        if not self.top_hypothesis:
            return False
        return self.top_hypothesis.overall_confidence > 0.6
    
    def get_entity_health_summary(self) -> Dict[str, float]:
        """Get health scores for all entities."""
        return {
            entity_id: entity.health_score
            for entity_id, entity in self.entities.items()
        }


class GNNModel(nn.Module):
    """Graph Neural Network for entity relationship analysis."""
    
    def __init__(self, input_dim: int = 64, hidden_dim: int = 128, output_dim: int = 32):
        super().__init__()
        
        # Graph convolution layers
        self.conv1 = nn.Linear(input_dim, hidden_dim)
        self.conv2 = nn.Linear(hidden_dim, hidden_dim)
        self.conv3 = nn.Linear(hidden_dim, output_dim)
        
        # Batch normalization
        self.bn1 = nn.BatchNorm1d(hidden_dim)
        self.bn2 = nn.BatchNorm1d(hidden_dim)
        
        # Dropout for regularization
        self.dropout = nn.Dropout(0.3)
        
        # Output layer for anomaly detection
        self.output_layer = nn.Linear(output_dim, 1)
        
    def forward(self, x: torch.Tensor, adj_matrix: torch.Tensor) -> torch.Tensor:
        """Forward pass through the GNN.
        
        Args:
            x: Node features [num_nodes, input_dim]
            adj_matrix: Adjacency matrix [num_nodes, num_nodes]
        
        Returns:
            Node embeddings [num_nodes, output_dim]
        """
        # First convolution with graph propagation
        x = F.relu(self.bn1(self.conv1(x)))
        x = self.dropout(x)
        x = torch.matmul(adj_matrix, x)  # Graph propagation
        
        # Second convolution
        x = F.relu(self.bn2(self.conv2(x)))
        x = self.dropout(x)
        x = torch.matmul(adj_matrix, x)  # Graph propagation
        
        # Third convolution
        x = self.conv3(x)
        
        return x
    
    def predict_anomaly(self, embeddings: torch.Tensor) -> torch.Tensor:
        """Predict anomaly scores from embeddings."""
        return torch.sigmoid(self.output_layer(embeddings)).squeeze()


class LogAnalyzer:
    """NLP-based log analysis using BERT embeddings."""
    
    def __init__(self, model_name: str = "bert-base-uncased"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name)
        self.vectorizer = TfidfVectorizer(max_features=1000, stop_words="english")
        
        # Predefined error patterns
        self.error_patterns = [
            r"(?i)error",
            r"(?i)exception",
            r"(?i)failed",
            r"(?i)timeout",
            r"(?i)connection refused",
            r"(?i)out of memory",
            r"(?i)disk full",
            r"(?i)permission denied",
            r"(?i)not found",
            r"(?i)bad gateway",
        ]
        
        # Pre-trained log patterns from historical incidents
        self.known_patterns = self._load_known_patterns()
    
    def _load_known_patterns(self) -> Dict[str, List[str]]:
        """Load known log patterns from historical incidents."""
        # In production, this would load from a database
        return {
            "database_connection": [
                "connection to database failed",
                "could not connect to postgres",
                "database timeout",
            ],
            "memory_leak": [
                "out of memory",
                "heap space exhausted",
                "gc overhead limit exceeded",
            ],
            "network_issue": [
                "connection refused",
                "network unreachable",
                "socket timeout",
            ],
            "config_error": [
                "configuration not found",
                "invalid configuration",
                "missing required parameter",
            ],
        }
    
    def extract_entities(self, log_line: str) -> List[str]:
        """Extract entities (service names, IDs, etc.) from log line."""
        entities = []
        
        # Extract service names (common patterns)
        service_patterns = [
            r"service[=:\s]+([\w-]+)",
            r"microservice[=:\s]+([\w-]+)",
            r"pod[=:\s]+([\w-]+)",
            r"container[=:\s]+([\w-]+)",
        ]
        
        for pattern in service_patterns:
            matches = re.findall(pattern, log_line, re.IGNORECASE)
            entities.extend(matches)
        
        # Extract error codes
        error_codes = re.findall(r"error[=:\s]+(\w+)", log_line, re.IGNORECASE)
        entities.extend(error_codes)
        
        # Extract numeric IDs
        ids = re.findall(r"id[=:\s]+([\w-]+)", log_line, re.IGNORECASE)
        entities.extend(ids)
        
        return list(set(entities))  # Remove duplicates
    
    def get_bert_embedding(self, text: str) -> np.ndarray:
        """Get BERT embedding for text."""
        try:
            inputs = self.tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            
            with torch.no_grad():
                outputs = self.model(**inputs)
                # Use mean of last hidden states as embedding
                embedding = outputs.last_hidden_state.mean(dim=1).squeeze().numpy()
            
            return embedding
            
        except Exception as e:
            logger.warning(f"BERT embedding failed: {e}")
            # Return zero vector as fallback
            return np.zeros(768)  # BERT base dimension
    
    def classify_log_pattern(self, log_line: str) -> Tuple[str, float]:
        """Classify log line into known patterns."""
        best_match = "unknown"
        best_score = 0.0
        
        # Check for error patterns first
        is_error = any(re.search(pattern, log_line) for pattern in self.error_patterns)
        
        if not is_error:
            return "normal", 1.0
        
        # Compare with known patterns using TF-IDF
        all_patterns = []
        pattern_names = []
        
        for pattern_name, examples in self.known_patterns.items():
            all_patterns.extend(examples)
            pattern_names.extend([pattern_name] * len(examples))
        
        # Add current log line
        all_patterns.append(log_line)
        
        # Calculate TF-IDF matrix
        tfidf_matrix = self.vectorizer.fit_transform(all_patterns)
        
        # Find similarity with known patterns
        current_vector = tfidf_matrix[-1]
        pattern_vectors = tfidf_matrix[:-1]
        
        similarities = cosine_similarity(current_vector, pattern_vectors).flatten()
        
        if len(similarities) > 0:
            best_idx = np.argmax(similarities)
            best_score = similarities[best_idx]
            best_match = pattern_names[best_idx]
            
            # Only return if similarity is above threshold
            if best_score > 0.3:
                return best_match, best_score
        
        return "unknown_error", 0.5 if is_error else 0.0
    
    def extract_keywords(self, log_lines: List[str], top_n: int = 10) -> List[Tuple[str, float]]:
        """Extract top keywords from log lines using TF-IDF."""
        if not log_lines:
            return []
        
        # Fit TF-IDF
        tfidf_matrix = self.vectorizer.fit_transform(log_lines)
        
        # Get feature names and scores
        feature_names = self.vectorizer.get_feature_names_out()
        scores = np.asarray(tfidf_matrix.mean(axis=0)).flatten()
        
        # Get top N keywords
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_keywords = [(feature_names[i], scores[i]) for i in top_indices]
        
        return top_keywords


class BayesianInferenceEngine:
    """Bayesian inference for root cause probability estimation."""
    
    def __init__(self):
        self.network = BayesianNetwork()
        self.structure_model = StructureModel()
        
        # Prior probabilities (from historical data)
        self.prior_probabilities = {
            "database_issue": 0.15,
            "network_issue": 0.12,
            "configuration_error": 0.10,
            "memory_leak": 0.08,
            "cpu_saturation": 0.07,
            "disk_full": 0.05,
            "dependency_failure": 0.18,
            "code_bug": 0.10,
            "deployment_issue": 0.08,
            "security_incident": 0.02,
        }
        
        # Conditional probability tables (simplified)
        self.cpt = self._build_conditional_probability_tables()
    
    def _build_conditional_probability_tables(self) -> Dict[str, Dict[str, float]]:
        """Build conditional probability tables for Bayesian network."""
        # In production, these would be learned from historical data
        return {
            "high_latency": {
                "database_issue": 0.8,
                "network_issue": 0.7,
                "cpu_saturation": 0.6,
                "dependency_failure": 0.5,
                "other": 0.2,
            },
            "error_rate_increase": {
                "code_bug": 0.9,
                "configuration_error": 0.7,
                "dependency_failure": 0.6,
                "database_issue": 0.4,
                "other": 0.1,
            },
            "memory_usage_spike": {
                "memory_leak": 0.95,
                "code_bug": 0.6,
                "deployment_issue": 0.4,
                "other": 0.1,
            },
            "cpu_usage_spike": {
                "cpu_saturation": 0.9,
                "code_bug": 0.5,
                "deployment_issue": 0.4,
                "security_incident": 0.3,
                "other": 0.1,
            },
        }
    
    def infer_root_cause_probabilities(
        self,
        symptoms: Dict[str, bool],
        evidence_strength: Dict[str, float] = None,
    ) -> Dict[str, float]:
        """Infer probabilities of root causes given symptoms.
        
        Args:
            symptoms: Dictionary of symptom presence (True/False)
            evidence_strength: Optional strength of each piece of evidence
            
        Returns:
            Dictionary of root cause probabilities
        """
        if evidence_strength is None:
            evidence_strength = {symptom: 1.0 for symptom in symptoms}
        
        # Initialize with prior probabilities
        probabilities = self.prior_probabilities.copy()
        
        # Apply Bayes' theorem for each symptom
        for symptom, is_present in symptoms.items():
            if not is_present:
                continue
            
            strength = evidence_strength.get(symptom, 1.0)
            
            for cause, prior in self.prior_probabilities.items():
                # Get likelihood P(symptom | cause)
                likelihood = self.cpt.get(symptom, {}).get(cause, 0.1)
                
                # Apply evidence strength
                adjusted_likelihood = 0.5 + (likelihood - 0.5) * strength
                
                # Update probability using Bayes' theorem (simplified)
                # P(cause | symptom) ∝ P(symptom | cause) * P(cause)
                probabilities[cause] *= adjusted_likelihood
        
        # Normalize probabilities
        total = sum(probabilities.values())
        if total > 0:
            probabilities = {k: v / total for k, v in probabilities.items()}
        
        # Sort by probability
        return dict(sorted(probabilities.items(), key=lambda x: x[1], reverse=True))
    
    def calculate_explanation_confidence(
        self,
        hypothesis: Hypothesis,
        evidence: Dict[str, Evidence],
    ) -> float:
        """Calculate confidence score for a hypothesis explanation."""
        
        # Base confidence from hypothesis
        confidence = hypothesis.confidence
        
        # Boost for supporting evidence
        if hypothesis.supporting_evidence:
            support_strength = sum(
                evidence[eid].severity * evidence[eid].confidence
                for eid in hypothesis.supporting_evidence
                if eid in evidence
            ) / len(hypothesis.supporting_evidence)
            
            confidence = (confidence + support_strength) / 2
        
        # Penalize for contradicting evidence
        if hypothesis.contradicting_evidence:
            contradiction_strength = sum(
                evidence[eid].severity * evidence[eid].confidence
                for eid in hypothesis.contradicting_evidence
                if eid in evidence
            ) / len(hypothesis.contradicting_evidence)
            
            confidence = max(0.0, confidence - contradiction_strength * 0.5)
        
        return confidence


class RootCauseAccelerator(BaseAgent):
    """
    Advanced Root Cause Analysis agent.
    Accelerates incident resolution through multi-method correlation,
    ML classification, and causal inference.
    """
    
    def __init__(self, context: AgentContext):
        super().__init__(context)
        
        # Analysis components
        self.log_analyzer = LogAnalyzer()
        self.bayesian_engine = BayesianInferenceEngine()
        
        # ML models
        self.random_forest = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42,
        )
        
        self.isolation_forest = IsolationForest(
            contamination=0.1,
            random_state=42,
        )
        
        # Graph Neural Network
        self.gnn_model = GNNModel()
        
        # Analysis state
        self.incident_start_time: Optional[datetime] = None
        self.analysis_window: timedelta = timedelta(hours=1)
        
        # Cache for similarity calculations
        self.similarity_cache: Dict[str, Dict[str, float]] = {}
        
        # Historical incident patterns
        self.historical_patterns = self._load_historical_patterns()
    
    async def execute(self) -> AgentResult:
        """Execute root cause analysis."""
        try:
            logger.info(f"Starting root cause analysis for suite: {self.context.suite_id}")
            
            # Step 1: Collect and parse incident data
            incident_data = await self._collect_incident_data()
            if not incident_data:
                return AgentResult(
                    success=False,
                    error="No incident data available",
                    metadata={"agent": "root_cause_accelerator"}
                )
            
            # Step 2: Extract entities and build dependency graph
            entities, dependency_graph = await self._extract_entities_and_dependencies(incident_data)
            
            # Step 3: Collect evidence from multiple sources
            evidence = await self._collect_evidence(incident_data, entities)
            
            # Step 4: Perform correlation analysis
            correlation_matrix = await self._perform_correlation_analysis(entities, evidence)
            
            # Step 5: Reconstruct timeline
            timeline = await self._reconstruct_timeline(evidence)
            
            # Step 6: Apply Bayesian inference
            bayesian_results = await self._apply_bayesian_inference(evidence, entities)
            
            # Step 7: Apply ML classification
            ml_results = await self._apply_ml_classification(entities, evidence)
            
            # Step 8: Generate and rank hypotheses
            hypotheses = await self._generate_hypotheses(
                entities, evidence, correlation_matrix, bayesian_results, ml_results
            )
            
            # Step 9: Find similar historical incidents
            similar_incidents = await self._find_similar_incidents(evidence, timeline)
            
            # Step 10: Generate recommendations
            recommendations = await self._generate_recommendations(
                hypotheses, evidence, similar_incidents
            )
            
            # Step 11: Prepare final analysis result
            result = self._prepare_analysis_result(
                incident_id=self.context.execution_id,
                entities=entities,
                evidence=evidence,
                correlation_matrix=correlation_matrix,
                dependency_graph=dependency_graph,
                timeline=timeline,
                hypotheses=hypotheses,
                bayesian_results=bayesian_results,
                ml_results=ml_results,
                similar_incidents=similar_incidents,
                recommendations=recommendations,
            )
            
            logger.info(f"Root cause analysis completed. Generated {len(hypotheses)} hypotheses.")
            
            return AgentResult(
                success=True,
                output=result.dict(),
                metadata={
                    "agent": "root_cause_accelerator",
                    "hypotheses_generated": len(hypotheses),
                    "analysis_time": self._get_processing_time(),
                    "confidence_score": result.confidence_score,
                }
            )
            
        except Exception as e:
            logger.error(f"Root cause analysis failed: {e}")
            return AgentResult(
                success=False,
                error=str(e),
                metadata={"agent": "root_cause_accelerator"}
            )
    
    async def _collect_incident_data(self) -> Dict[str, Any]:
        """Collect incident data from multiple sources."""
        incident_data = {
            "metrics": await self._collect_metrics(),
            "logs": await self._collect_logs(),
            "traces": await self._collect_traces(),
            "alerts": await self._collect_alerts(),
            "config_changes": await self._collect_config_changes(),
            "deployments": await self._collect_deployment_info(),
            "dependencies": await self._collect_dependency_info(),
        }
        
        # Set incident start time from earliest evidence
        self._determine_incident_start_time(incident_data)
        
        return incident_data
    
    async def _collect_metrics(self) -> List[Dict[str, Any]]:
        """Collect metrics from monitoring systems."""
        metrics = []
        
        # Check for metrics in context
        if "metrics" in self.context.data:
            metrics.extend(self.context.data["metrics"])
        
        # In production, this would query Prometheus, CloudWatch, etc.
        # For now, generate sample metrics
        
        sample_metrics = [
            {
                "metric": "cpu_usage",
                "value": 0.85,
                "timestamp": datetime.utcnow() - timedelta(minutes=30),
                "entity": "service-a",
                "labels": {"instance": "pod-1", "namespace": "production"},
            },
            {
                "metric": "memory_usage",
                "value": 0.92,
                "timestamp": datetime.utcnow() - timedelta(minutes=25),
                "entity": "service-a",
                "labels": {"instance": "pod-1", "namespace": "production"},
            },
            {
                "metric": "request_latency",
                "value": 2.5,  # seconds
                "timestamp": datetime.utcnow() - timedelta(minutes=20),
                "entity": "service-b",
                "labels": {"endpoint": "/api/users", "method": "GET"},
            },
            {
                "metric": "error_rate",
                "value": 0.15,  # 15%
                "timestamp": datetime.utcnow() - timedelta(minutes=15),
                "entity": "service-b",
                "labels": {"endpoint": "/api/users", "status": "500"},
            },
        ]
        
        metrics.extend(sample_metrics)
        return metrics
    
    async def _collect_logs(self) -> List[Dict[str, Any]]:
        """Collect logs from logging systems."""
        logs = []
        
        if "logs" in self.context.data:
            logs.extend(self.context.data["logs"])
        
        # Sample logs for demonstration
        sample_logs = [
            {
                "timestamp": datetime.utcnow() - timedelta(minutes=28),
                "message": "ERROR: Database connection timeout after 30 seconds",
                "entity": "service-a",
                "level": "ERROR",
                "source": "application.log",
            },
            {
                "timestamp": datetime.utcnow() - timedelta(minutes=26),
                "message": "WARN: High memory usage detected: 92%",
                "entity": "service-a",
                "level": "WARN",
                "source": "system.log",
            },
            {
                "timestamp": datetime.utcnow() - timedelta(minutes=22),
                "message": "ERROR: Failed to process request: Connection refused",
                "entity": "service-b",
                "level": "ERROR",
                "source": "application.log",
            },
            {
                "timestamp": datetime.utcnow() - timedelta(minutes=18),
                "message": "INFO: Retrying database connection...",
                "entity": "service-a",
                "level": "INFO",
                "source": "application.log",
            },
        ]
        
        logs.extend(sample_logs)
        return logs
    
    async def _collect_traces(self) -> List[Dict[str, Any]]:
        """Collect traces from distributed tracing systems."""
        traces = []
        
        if "traces" in self.context.data:
            traces.extend(self.context.data["traces"])
        
        # Sample trace data
        sample_traces = [
            {
                "trace_id": "trace-001",
                "span_id": "span-001",
                "parent_span_id": None,
                "operation": "GET /api/users",
                "start_time": datetime.utcnow() - timedelta(minutes=30),
                "duration": 3.2,  # seconds
                "service": "service-b",
                "tags": {"http.status_code": "500", "error": "true"},
            },
            {
                "trace_id": "trace-001",
                "span_id": "span-002",
                "parent_span_id": "span-001",
                "operation": "db.query users",
                "start_time": datetime.utcnow() - timedelta(minutes=30, seconds=0.5),
                "duration": 2.8,  # seconds
                "service": "database",
                "tags": {"db.statement": "SELECT * FROM users", "error": "true"},
            },
        ]
        
        traces.extend(sample_traces)
        return traces
    
    async def _collect_alerts(self) -> List[Dict[str, Any]]:
        """Collect alerts from alerting systems."""
        alerts = []
        
        if "alerts" in self.context.data:
            alerts.extend(self.context.data["alerts"])
        
        return alerts
    
    async def _collect_config_changes(self) -> List[Dict[str, Any]]:
        """Collect configuration changes."""
        config_changes = []
        
        if "config_changes" in self.context.data:
            config_changes.extend(self.context.data["config_changes"])
        
        return config_changes
    
    async def _collect_deployment_info(self) -> List[Dict[str, Any]]:
        """Collect deployment information."""
        deployments = []
        
        if "deployments" in self.context.data:
            deployments.extend(self.context.data["deployments"])
        
        return deployments
    
    async def _collect_dependency_info(self) -> List[Dict[str, Any]]:
        """Collect dependency information."""
        dependencies = []
        
        if "dependencies" in self.context.data:
            dependencies.extend(self.context.data["dependencies"])
        
        # Sample dependency data
        sample_dependencies = [
            {
                "source": "service-a",
                "target": "database",
                "type": "database",
                "health": "unhealthy",
            },
            {
                "source": "service-b",
                "target": "service-a",
                "type": "http",
                "health": "degraded",
            },
            {
                "source": "service-c",
                "target": "service-b",
                "type": "grpc",
                "health": "degraded",
            },
        ]
        
        dependencies.extend(sample_dependencies)
        return dependencies
    
    def _determine_incident_start_time(self, incident_data: Dict[str, Any]) -> None:
        """Determine incident start time from earliest evidence."""
        earliest_time = None
        
        # Check metrics
        for metric in incident_data.get("metrics", []):
            timestamp = metric.get("timestamp")
            if timestamp and (earliest_time is None or timestamp < earliest_time):
                earliest_time = timestamp
        
        # Check logs
        for log in incident_data.get("logs", []):
            timestamp = log.get("timestamp")
            if timestamp and (earliest_time is None or timestamp < earliest_time):
                earliest_time = timestamp
        
        # Check alerts
        for alert in incident_data.get("alerts", []):
            timestamp = alert.get("timestamp")
            if timestamp and (earliest_time is None or timestamp < earliest_time):
                earliest_time = timestamp
        
        self.incident_start_time = earliest_time or datetime.utcnow()
    
    async def _extract_entities_and_dependencies(
        self,
        incident_data: Dict[str, Any]
    ) -> Tuple[Dict[str, RCAEntity], Dict[str, List[str]]]:
        """Extract entities and build dependency graph from incident data."""
        entities = {}
        dependency_graph = defaultdict(list)
        
        # Extract from metrics
        for metric in incident_data.get("metrics", []):
            entity_id = metric.get("entity")
            if entity_id and entity_id not in entities:
                entity = RCAEntity(
                    entity_id=entity_id,
                    entity_type=RCAEntityType.SERVICE,
                    name=entity_id,
                    metrics={"cpu_usage": 0.0, "memory_usage": 0.0},
                )
                entities[entity_id] = entity
        
        # Extract from logs
        for log in incident_data.get("logs", []):
            entity_id = log.get("entity")
            if entity_id and entity_id not in entities:
                entity = RCAEntity(
                    entity_id=entity_id,
                    entity_type=RCAEntityType.SERVICE,
                    name=entity_id,
                )
                entities[entity_id] = entity
        
        # Extract from traces
        for trace in incident_data.get("traces", []):
            service = trace.get("service")
            if service and service not in entities:
                entity = RCAEntity(
                    entity_id=service,
                    entity_type=RCAEntityType.SERVICE,
                    name=service,
                )
                entities[service] = entity
        
        # Build dependency graph from dependency info
        for dep in incident_data.get("dependencies", []):
            source = dep.get("source")
            target = dep.get("target")
            
            if source in entities and target in entities:
                # Add to dependency graph
                dependency_graph[source].append(target)
                
                # Update entity dependencies
                if target not in entities[source].dependencies:
                    entities[source].dependencies.append(target)
        
        # Update entity health scores based on evidence
        await self._update_entity_health_scores(entities, incident_data)
        
        return dict(entities), dict(dependency_graph)
    
    async def _update_entity_health_scores(
        self,
        entities: Dict[str, RCAEntity],
        incident_data: Dict[str, Any]
    ) -> None:
        """Update entity health scores based on incident data."""
        
        for entity_id, entity in entities.items():
            health_score = 1.0  # Start with perfect health
            
            # Check for error logs
            error_logs = [
                log for log in incident_data.get("logs", [])
                if log.get("entity") == entity_id and log.get("level") == "ERROR"
            ]
            
            if error_logs:
                health_score *= 0.7  # 30% penalty for errors
            
            # Check for high latency in traces
            high_latency_traces = [
                trace for trace in incident_data.get("traces", [])
                if trace.get("service") == entity_id and trace.get("duration", 0) > 2.0
            ]
            
            if high_latency_traces:
                health_score *= 0.8  # 20% penalty for high latency
            
            # Check for metric anomalies
            high_cpu = [
                metric for metric in incident_data.get("metrics", [])
                if metric.get("entity") == entity_id 
                and metric.get("metric") == "cpu_usage"
                and metric.get("value", 0) > 0.8
            ]
            
            if high_cpu:
                health_score *= 0.85  # 15% penalty for high CPU
            
            high_memory = [
                metric for metric in incident_data.get("metrics", [])
                if metric.get("entity") == entity_id 
                and metric.get("metric") == "memory_usage"
                and metric.get("value", 0) > 0.9
            ]
            
            if high_memory:
                health_score *= 0.8  # 20% penalty for high memory
            
            # Update entity health score
            entity.health_score = max(0.0, min(1.0, health_score))
    
    async def _collect_evidence(
        self,
        incident_data: Dict[str, Any],
        entities: Dict[str, RCAEntity],
    ) -> Dict[str, Evidence]:
        """Collect and structure evidence from incident data."""
        evidence = {}
        evidence_counter = 0
        
        # Process metrics as evidence
        for metric in incident_data.get("metrics", []):
            evidence_id = f"metric_{evidence_counter}"
            
            # Determine severity based on metric value
            severity = 0.5
            metric_name = metric.get("metric", "")
            metric_value = metric.get("value", 0)
            
            if metric_name in ["cpu_usage", "memory_usage"]:
                if metric_value > 0.9:
                    severity = 0.9
                elif metric_value > 0.8:
                    severity = 0.7
                elif metric_value > 0.7:
                    severity = 0.5
            
            evidence_obj = Evidence(
                evidence_id=evidence_id,
                evidence_type=EvidenceType.METRIC_ANOMALY,
                timestamp=metric.get("timestamp", datetime.utcnow()),
                source="metrics",
                entity_id=metric.get("entity"),
                description=f"{metric_name}: {metric_value}",
                severity=severity,
                confidence=0.9,
                raw_data=metric,
            )
            evidence[evidence_id] = evidence_obj
            evidence_counter += 1
        
        # Process logs as evidence
        for log in incident_data.get("logs", []):
            evidence_id = f"log_{evidence_counter}"
            
            # Analyze log using NLP
            log_message = log.get("message", "")
            pattern, confidence = self.log_analyzer.classify_log_pattern(log_message)
            
            # Determine severity based on log level and pattern
            severity = 0.3
            log_level = log.get("level", "INFO").upper()
            
            if log_level == "ERROR":
                severity = 0.8
            elif log_level == "WARN":
                severity = 0.5
            elif log_level == "INFO":
                severity = 0.2
            
            # Boost severity for known error patterns
            if pattern != "normal" and pattern != "unknown":
                severity = min(1.0, severity + 0.2)
            
            evidence_obj = Evidence(
                evidence_id=evidence_id,
                evidence_type=EvidenceType.LOG_ERROR,
                timestamp=log.get("timestamp", datetime.utcnow()),
                source="logs",
                entity_id=log.get("entity"),
                description=f"{log_level}: {log_message[:100]}...",
                severity=severity,
                confidence=confidence,
                raw_data=log,
            )
            evidence[evidence_id] = evidence_obj
            evidence_counter += 1
        
        # Process traces as evidence
        for trace in incident_data.get("traces", []):
            evidence_id = f"trace_{evidence_counter}"
            
            # Check for errors in traces
            has_error = trace.get("tags", {}).get("error") == "true"
            duration = trace.get("duration", 0)
            
            severity = 0.3
            if has_error:
                severity = 0.8
            elif duration > 2.0:  # High latency
                severity = 0.6
            
            evidence_obj = Evidence(
                evidence_id=evidence_id,
                evidence_type=EvidenceType.TRACE_ERROR,
                timestamp=trace.get("start_time", datetime.utcnow()),
                source="traces",
                entity_id=trace.get("service"),
                description=f"Trace {trace.get('operation', '')}: duration={duration}s",
                severity=severity,
                confidence=0.8,
                raw_data=trace,
            )
            evidence[evidence_id] = evidence_obj
            evidence_counter += 1
        
        # Process dependencies as evidence
        for dep in incident_data.get("dependencies", []):
            evidence_id = f"dep_{evidence_counter}"
            
            health = dep.get("health", "healthy")
            severity = 0.3
            if health == "unhealthy":
                severity = 0.9
            elif health == "degraded":
                severity = 0.6
            
            evidence_obj = Evidence(
                evidence_id=evidence_id,
                evidence_type=EvidenceType.DEPENDENCY_FAILURE,
                timestamp=datetime.utcnow(),
                source="dependencies",
                entity_id=dep.get("source"),
                description=f"Dependency {dep.get('target')} is {health}",
                severity=severity,
                confidence=0.7,
                raw_data=dep,
            )
            evidence[evidence_id] = evidence_obj
            evidence_counter += 1
        
        return evidence
    
    async def _perform_correlation_analysis(
        self,
        entities: Dict[str, RCAEntity],
        evidence: Dict[str, Evidence],
    ) -> Dict[str, Dict[str, CorrelationResult]]:
        """Perform correlation analysis between entities and evidence."""
        correlation_matrix = defaultdict(dict)
        
        # Extract time series data for each entity
        entity_timeseries = self._extract_entity_timeseries(entities, evidence)
        
        # Calculate correlations between all entity pairs
        entity_ids = list(entities.keys())
        
        for i, entity_a in enumerate(entity_ids):
            for j, entity_b in enumerate(entity_ids[i+1:], start=i+1):
                if entity_a == entity_b:
                    continue
                
                # Get time series for both entities
                ts_a = entity_timeseries.get(entity_a, {})
                ts_b = entity_timeseries.get(entity_b, {})
                
                if not ts_a or not ts_b:
                    continue
                
                # Calculate multiple types of correlations
                correlations = await self._calculate_correlations(ts_a, ts_b, entity_a, entity_b)
                
                # Store best correlation
                best_corr = max(correlations, key=lambda x: x.absolute_strength)
                correlation_matrix[entity_a][entity_b] = best_corr
                correlation_matrix[entity_b][entity_a] = best_corr
        
        return dict(correlation_matrix)
    
    def _extract_entity_timeseries(
        self,
        entities: Dict[str, RCAEntity],
        evidence: Dict[str, Evidence],
    ) -> Dict[str, Dict[str, List[Tuple[datetime, float]]]]:
        """Extract time series data for entities from evidence."""
        timeseries = defaultdict(lambda: defaultdict(list))
        
        # Group evidence by entity and type
        for ev in evidence.values():
            entity_id = ev.entity_id
            if not entity_id or entity_id not in entities:
                continue
            
            # Extract value from evidence based on type
            value = self._extract_value_from_evidence(ev)
            if value is not None:
                timeseries[entity_id][ev.evidence_type.value].append(
                    (ev.timestamp, value)
                )
        
        # Sort each time series by timestamp
        for entity in timeseries:
            for ev_type in timeseries[entity]:
                timeseries[entity][ev_type].sort(key=lambda x: x[0])
        
        return dict(timeseries)
    
    def _extract_value_from_evidence(self, evidence: Evidence) -> Optional[float]:
        """Extract numeric value from evidence."""
        if evidence.evidence_type == EvidenceType.METRIC_ANOMALY:
            # Parse metric value from description
            desc = evidence.description
            if ":" in desc:
                try:
                    value_str = desc.split(":")[-1].strip()
                    return float(value_str)
                except (ValueError, IndexError):
                    pass
        
        elif evidence.evidence_type == EvidenceType.LOG_ERROR:
            # Use severity as proxy value
            return evidence.severity
        
        elif evidence.evidence_type == EvidenceType.TRACE_ERROR:
            # Parse duration from description
            desc = evidence.description
            if "duration=" in desc:
                try:
                    dur_str = desc.split("duration=")[-1].split("s")[0]
                    return float(dur_str)
                except (ValueError, IndexError):
                    pass
        
        return None
    
    async def _calculate_correlations(
        self,
        ts_a: Dict[str, List[Tuple[datetime, float]]],
        ts_b: Dict[str, List[Tuple[datetime, float]]],
        entity_a: str,
        entity_b: str,
    ) -> List[CorrelationResult]:
        """Calculate multiple types of correlations between time series."""
        correlations = []
        
        # Try to align time series
        aligned_ts = self._align_time_series(ts_a, ts_b)
        
        if not aligned_ts:
            return correlations
        
        ts_a_aligned, ts_b_aligned = aligned_ts
        
        # 1. Pearson correlation
        if len(ts_a_aligned) > 2:
            pearson_corr, pearson_p = stats.pearsonr(ts_a_aligned, ts_b_aligned)
            correlations.append(
                CorrelationResult(
                    entity_a=entity_a,
                    entity_b=entity_b,
                    correlation_score=pearson_corr,
                    correlation_type="pearson",
                    p_value=pearson_p,
                    is_causal=False,
                )
            )
        
        # 2. Spearman correlation (non-parametric)
        if len(ts_a_aligned) > 2:
            spearman_corr, spearman_p = stats.spearmanr(ts_a_aligned, ts_b_aligned)
            correlations.append(
                CorrelationResult(
                    entity_a=entity_a,
                    entity_b=entity_b,
                    correlation_score=spearman_corr,
                    correlation_type="spearman",
                    p_value=spearman_p,
                    is_causal=False,
                )
            )
        
        # 3. Cross-correlation (with time lag)
        if len(ts_a_aligned) > 10:
            max_lag = min(20, len(ts_a_aligned) // 2)
            cross_corr = self._calculate_cross_correlation(ts_a_aligned, ts_b_aligned, max_lag)
            
            if cross_corr:
                correlations.append(cross_corr)
        
        # 4. Granger causality test (simplified)
        if len(ts_a_aligned) > 30:
            granger_result = self._test_granger_causality(ts_a_aligned, ts_b_aligned)
            if granger_result:
                correlations.append(granger_result)
        
        return correlations
    
    def _align_time_series(
        self,
        ts_a: Dict[str, List[Tuple[datetime, float]]],
        ts_b: Dict[str, List[Tuple[datetime, float]]],
    ) -> Optional[Tuple[List[float], List[float]]]:
        """Align two time series to common timestamps."""
        # For simplicity, use the most common evidence type
        common_types = set(ts_a.keys()) & set(ts_b.keys())
        if not common_types:
            return None
        
        ev_type = list(common_types)[0]  # Use first common type
        
        ts_a_points = ts_a.get(ev_type, [])
        ts_b_points = ts_b.get(ev_type, [])
        
        if not ts_a_points or not ts_b_points:
            return None
        
        # Create time windows for alignment
        window_size = timedelta(minutes=5)
        
        # Find time range
        all_times = [t for t, _ in ts_a_points] + [t for t, _ in ts_b_points]
        start_time = min(all_times)
        end_time = max(all_times)
        
        # Create bins
        current_time = start_time
        bins = []
        while current_time <= end_time:
            bins.append(current_time)
            current_time += window_size
        
        # Aggregate values in each bin
        a_values = []
        b_values = []
        
        for bin_start in bins:
            bin_end = bin_start + window_size
            
            # Find values in this bin for ts_a
            a_bin_vals = [v for t, v in ts_a_points if bin_start <= t < bin_end]
            a_values.append(np.mean(a_bin_vals) if a_bin_vals else np.nan)
            
            # Find values in this bin for ts_b
            b_bin_vals = [v for t, v in ts_b_points if bin_start <= t < bin_end]
            b_values.append(np.mean(b_bin_vals) if b_bin_vals else np.nan)
        
        # Remove bins where either series has NaN
        valid_indices = [
            i for i in range(len(bins))
            if not np.isnan(a_values[i]) and not np.isnan(b_values[i])
        ]
        
        if len(valid_indices) < 3:
            return None
        
        aligned_a = [a_values[i] for i in valid_indices]
        aligned_b = [b_values[i] for i in valid_indices]
        
        return aligned_a, aligned_b
    
    def _calculate_cross_correlation(
        self,
        series_a: List[float],
        series_b: List[float],
        max_lag: int,
    ) -> Optional[CorrelationResult]:
        """Calculate cross-correlation with time lag."""
        try:
            # Normalize series
            a_norm = (series_a - np.mean(series_a)) / (np.std(series_a) + 1e-10)
            b_norm = (series_b - np.mean(series_b)) / (np.std(series_b) + 1e-10)
            
            best_corr = 0.0
            best_lag = 0
            
            for lag in range(-max_lag, max_lag + 1):
                if lag < 0:
                    a_shifted = a_norm[-lag:]
                    b_shifted = b_norm[:lag]
                elif lag > 0:
                    a_shifted = a_norm[:-lag]
                    b_shifted = b_norm[lag:]
                else:
                    a_shifted = a_norm
                    b_shifted = b_norm
                
                if len(a_shifted) < 2 or len(b_shifted) < 2:
                    continue
                
                # Ensure equal length
                min_len = min(len(a_shifted), len(b_shifted))
                a_shifted = a_shifted[:min_len]
                b_shifted = b_shifted[:min_len]
                
                corr = np.corrcoef(a_shifted, b_shifted)[0, 1]
                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_lag = lag
            
            if abs(best_corr) > 0.3:  # Only return if correlation is meaningful
                return CorrelationResult(
                    entity_a="entity_a",  # Will be filled by caller
                    entity_b="entity_b",
                    correlation_score=best_corr,
                    correlation_type="cross_correlation",
                    lag=timedelta(minutes=best_lag * 5),  # 5 minutes per lag unit
                    p_value=None,
                    is_causal=abs(best_lag) > 0,  # Non-zero lag suggests possible causality
                )
        
        except Exception as e:
            logger.warning(f"Cross-correlation calculation failed: {e}")
        
        return None
    
    def _test_granger_causality(
        self,
        series_a: List[float],
        series_b: List[float],
        max_lag: int = 5,
    ) -> Optional[CorrelationResult]:
        """Simplified Granger causality test."""
        try:
            # This is a simplified version - in production would use statsmodels
            n = len(series_a)
            
            if n < max_lag * 3:
                return None
            
            # Calculate correlation with different lags
            best_corr = 0.0
            best_lag = 0
            
            for lag in range(1, max_lag + 1):
                # Create lagged series
                x_lagged = series_a[lag:]
                y_current = series_b[:-lag]
                
                if len(x_lagged) < 2 or len(y_current) < 2:
                    continue
                
                corr = np.corrcoef(x_lagged, y_current)[0, 1]
                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_lag = lag
            
            if abs(best_corr) > 0.4:  # Strong correlation with lag suggests causality
                return CorrelationResult(
                    entity_a="entity_a",
                    entity_b="entity_b",
                    correlation_score=best_corr,
                    correlation_type="granger",
                    lag=timedelta(minutes=best_lag * 5),
                    p_value=0.05,  # Placeholder
                    is_causal=True,
                )
        
        except Exception as e:
            logger.warning(f"Granger causality test failed: {e}")
        
        return None
    
    async def _reconstruct_timeline(self, evidence: Dict[str, Evidence]) -> List[TimelineEvent]:
        """Reconstruct timeline of events from evidence."""
        timeline_events = []
        
        # Sort evidence by timestamp
        sorted_evidence = sorted(evidence.values(), key=lambda x: x.timestamp)
        
        # Group evidence by time windows
        window_size = timedelta(minutes=5)
        current_window_start = None
        window_events = []
        
        for ev in sorted_evidence:
            if current_window_start is None:
                current_window_start = ev.timestamp
            
            # Check if event is in current window
            if ev.timestamp - current_window_start <= window_size:
                window_events.append(ev)
            else:
                # Create timeline event for completed window
                if window_events:
                    timeline_event = self._create_timeline_event(window_events, current_window_start)
                    timeline_events.append(timeline_event)
                
                # Start new window
                current_window_start = ev.timestamp
                window_events = [ev]
        
        # Create event for last window
        if window_events:
            timeline_event = self._create_timeline_event(window_events, current_window_start)
            timeline_events.append(timeline_event)
        
        # Link related events
        timeline_events = self._link_related_events(timeline_events)
        
        return timeline_events
    
    def _create_timeline_event(
        self,
        window_events: List[Evidence],
        window_start: datetime,
    ) -> TimelineEvent:
        """Create a timeline event from evidence in a time window."""
        
        # Find most severe evidence in window
        most_severe = max(window_events, key=lambda x: x.severity)
        
        # Count evidence types
        evidence_counts = {}
        for ev in window_events:
            ev_type = ev.evidence_type.value
            evidence_counts[ev_type] = evidence_counts.get(ev_type, 0) + 1
        
        # Create description
        desc_parts = []
        for ev_type, count in evidence_counts.items():
            desc_parts.append(f"{count} {ev_type}")
        
        description = f"Multiple events: {', '.join(desc_parts)}"
        
        # Calculate average severity
        avg_severity = np.mean([ev.severity for ev in window_events])
        
        return TimelineEvent(
            event_id=f"timeline_{len(window_events)}_{window_start.timestamp()}",
            timestamp=window_start,
            entity_id=most_severe.entity_id or "multiple",
            event_type=most_severe.evidence_type.value,
            description=description,
            severity=avg_severity,
            evidence_ids=[ev.evidence_id for ev in window_events],
        )
    
    def _link_related_events(self, timeline_events: List[TimelineEvent]) -> List[TimelineEvent]:
        """Link related events in the timeline."""
        for i, event in enumerate(timeline_events):
            # Find related events (same entity within 15 minutes)
            related = []
            for j, other_event in enumerate(timeline_events):
                if i == j:
                    continue
                
                time_diff = abs((event.timestamp - other_event.timestamp).total_seconds())
                same_entity = event.entity_id == other_event.entity_id
                
                if same_entity and time_diff < 900:  # 15 minutes
                    related.append(other_event.event_id)
            
            event.related_events = related
        
        return timeline_events
    
    async def _apply_bayesian_inference(
        self,
        evidence: Dict[str, Evidence],
        entities: Dict[str, RCAEntity],
    ) -> Dict[str, float]:
        """Apply Bayesian inference to estimate root cause probabilities."""
        
        # Extract symptoms from evidence
        symptoms = self._extract_symptoms_from_evidence(evidence, entities)
        
        # Calculate evidence strength
        evidence_strength = {}
        for symptom, is_present in symptoms.items():
            if is_present:
                # Calculate average severity of evidence supporting this symptom
                supporting_evidence = [
                    ev for ev in evidence.values()
                    if self._evidence_supports_symptom(ev, symptom)
                ]
                if supporting_evidence:
                    evidence_strength[symptom] = np.mean([
                        ev.severity * ev.confidence
                        for ev in supporting_evidence
                    ])
        
        # Run Bayesian inference
        probabilities = self.bayesian_engine.infer_root_cause_probabilities(
            symptoms=symptoms,
            evidence_strength=evidence_strength,
        )
        
        return probabilities
    
    def _extract_symptoms_from_evidence(
        self,
        evidence: Dict[str, Evidence],
        entities: Dict[str, RCAEntity],
    ) -> Dict[str, bool]:
        """Extract symptoms from evidence for Bayesian inference."""
        symptoms = {
            "high_latency": False,
            "error_rate_increase": False,
            "memory_usage_spike": False,
            "cpu_usage_spike": False,
        }
        
        # Check for high latency
        high_latency_evidence = [
            ev for ev in evidence.values()
            if ev.evidence_type == EvidenceType.TRACE_ERROR
            and "duration" in ev.description.lower()
        ]
        
        if high_latency_evidence:
            # Check if any trace has duration > 2 seconds
            for ev in high_latency_evidence:
                if "duration=" in ev.description:
                    try:
                        dur_str = ev.description.split("duration=")[1].split("s")[0]
                        if float(dur_str) > 2.0:
                            symptoms["high_latency"] = True
                            break
                    except (ValueError, IndexError):
                        pass
        
        # Check for error rate increase
        error_evidence = [
            ev for ev in evidence.values()
            if ev.evidence_type == EvidenceType.LOG_ERROR
            and ev.severity > 0.7
        ]
        
        if len(error_evidence) >= 3:  # Multiple errors
            symptoms["error_rate_increase"] = True
        
        # Check for memory usage spike
        memory_evidence = [
            ev for ev in evidence.values()
            if ev.evidence_type == EvidenceType.METRIC_ANOMALY
            and "memory_usage" in ev.description.lower()
        ]
        
        for ev in memory_evidence:
            if ": 0.9" in ev.description or ": 0.8" in ev.description:
                symptoms["memory_usage_spike"] = True
                break
        
        # Check for CPU usage spike
        cpu_evidence = [
            ev for ev in evidence.values()
            if ev.evidence_type == EvidenceType.METRIC_ANOMALY
            and "cpu_usage" in ev.description.lower()
        ]
        
        for ev in cpu_evidence:
            if ": 0.9" in ev.description or ": 0.8" in ev.description:
                symptoms["cpu_usage_spike"] = True
                break
        
        return symptoms
    
    def _evidence_supports_symptom(self, evidence: Evidence, symptom: str) -> bool:
        """Check if evidence supports a specific symptom."""
        if symptom == "high_latency":
            return evidence.evidence_type == EvidenceType.TRACE_ERROR
        
        elif symptom == "error_rate_increase":
            return evidence.evidence_type == EvidenceType.LOG_ERROR
        
        elif symptom == "memory_usage_spike":
            return (
                evidence.evidence_type == EvidenceType.METRIC_ANOMALY
                and "memory" in evidence.description.lower()
            )
        
        elif symptom == "cpu_usage_spike":
            return (
                evidence.evidence_type == EvidenceType.METRIC_ANOMALY
                and "cpu" in evidence.description.lower()
            )
        
        return False
    
    async def _apply_ml_classification(
        self,
        entities: Dict[str, RCAEntity],
        evidence: Dict[str, Evidence],
    ) -> Dict[str, Any]:
        """Apply machine learning classification for root cause analysis."""
        
        # Prepare features for ML
        features, labels = self._prepare_ml_features(entities, evidence)
        
        if not features or len(features) < 10:
            logger.warning("Not enough data for ML classification")
            return {"predictions": {}, "feature_importance": {}}
        
        try:
            # Train Random Forest classifier
            X = np.array(features)
            y = np.array(labels)
            
            self.random_forest.fit(X, y)
            
            # Make predictions for each entity
            predictions = {}
            feature_importance = {}
            
            entity_ids = list(entities.keys())
            for i, entity_id in enumerate(entity_ids):
                if i < len(features):
                    entity_features = features[i]
                    proba = self.random_forest.predict_proba([entity_features])[0]
                    
                    # Get probability of being root cause (class 1)
                    root_cause_prob = proba[1] if len(proba) > 1 else 0.0
                    predictions[entity_id] = root_cause_prob
            
            # Get feature importance
            if hasattr(self.random_forest, "feature_importances_"):
                importance = self.random_forest.feature_importances_
                feature_names = [f"feature_{i}" for i in range(len(importance))]
                feature_importance = dict(zip(feature_names, importance))
            
            return {
                "predictions": predictions,
                "feature_importance": feature_importance,
                "model_score": self.random_forest.score(X, y),
            }
            
        except Exception as e:
            logger.error(f"ML classification failed: {e}")
            return {"predictions": {}, "feature_importance": {}, "error": str(e)}
    
    def _prepare_ml_features(
        self,
        entities: Dict[str, RCAEntity],
        evidence: Dict[str, Evidence],
    ) -> Tuple[List[List[float]], List[int]]:
        """Prepare features for machine learning classification."""
        features = []
        labels = []
        
        # Feature engineering
        for entity_id, entity in entities.items():
            entity_features = []
            
            # 1. Entity health score
            entity_features.append(entity.health_score)
            
            # 2. Number of dependencies
            entity_features.append(len(entity.dependencies))
            
            # 3. Evidence count for this entity
            entity_evidence = [
                ev for ev in evidence.values()
                if ev.entity_id == entity_id
            ]
            entity_features.append(len(entity_evidence))
            
            # 4. Average severity of evidence
            if entity_evidence:
                avg_severity = np.mean([ev.severity for ev in entity_evidence])
                entity_features.append(avg_severity)
            else:
                entity_features.append(0.0)
            
            # 5. Maximum severity of evidence
            if entity_evidence:
                max_severity = max([ev.severity for ev in entity_evidence])
                entity_features.append(max_severity)
            else:
                entity_features.append(0.0)
            
            # 6. Number of error logs
            error_logs = [
                ev for ev in entity_evidence
                if ev.evidence_type == EvidenceType.LOG_ERROR
                and ev.severity > 0.7
            ]
            entity_features.append(len(error_logs))
            
            # 7. Is this a leaf node in dependency graph?
            # (Leaf nodes are less likely to be root causes of widespread issues)
            is_leaf = len(entity.dependencies) > 0 and all(
                dep not in entities or len(entities[dep].dependencies) == 0
                for dep in entity.dependencies
            )
            entity_features.append(1.0 if is_leaf else 0.0)
            
            # 8. Number of dependent entities (reverse dependencies)
            dependent_count = sum(
                1 for other_entity in entities.values()
                if entity_id in other_entity.dependencies
            )
            entity_features.append(dependent_count)
            
            # Label: Is this entity the likely root cause?
            # In production, this would use historical data
            # For now, use heuristic: entity with worst health score and most evidence
            label = 0  # Not root cause by default
            
            # Heuristic: entity with health score < 0.5 and most severe evidence
            if entity.health_score < 0.5 and entity_evidence:
                max_sev_entity = max(entity_evidence, key=lambda x: x.severity)
                if max_sev_entity.severity > 0.8:
                    label = 1  # Likely root cause
            
            features.append(entity_features)
            labels.append(label)
        
        return features, labels
    
    async def _generate_hypotheses(
        self,
        entities: Dict[str, RCAEntity],
        evidence: Dict[str, Evidence],
        correlation_matrix: Dict[str, Dict[str, CorrelationResult]],
        bayesian_results: Dict[str, float],
        ml_results: Dict[str, Any],
    ) -> List[Hypothesis]:
        """Generate and rank root cause hypotheses."""
        hypotheses = []
        hypothesis_counter = 0
        
        # Generate hypotheses based on different methods
        
        # 1. Bayesian inference hypotheses
        for cause, probability in bayesian_results.items():
            if probability > 0.2:  # Only consider likely causes
                # Find entities related to this cause
                related_entities = self._find_entities_for_cause(cause, entities, evidence)
                
                for entity_id in related_entities:
                    hypothesis_id = f"bayesian_{hypothesis_counter}"
                    hypothesis_counter += 1
                    
                    hypothesis = Hypothesis(
                        hypothesis_id=hypothesis_id,
                        description=f"{cause} affecting {entity_id}",
                        root_cause_entity_id=entity_id,
                        root_cause_type=cause,
                        confidence=float(probability),
                        bayesian_probability=float(probability),
                        supporting_evidence=self._find_supporting_evidence(
                            entity_id, cause, evidence
                        ),
                        explanation=self._generate_explanation(cause, entity_id, evidence),
                        causal_chain=self._infer_causal_chain(entity_id, entities, correlation_matrix),
                        impact_radius=len(self._find_affected_entities(entity_id, entities)),
                        remediation_actions=self._generate_remediation_actions(cause),
                        verification_steps=self._generate_verification_steps(cause),
                    )
                    
                    hypotheses.append(hypothesis)
        
        # 2. ML-based hypotheses
        ml_predictions = ml_results.get("predictions", {})
        for entity_id, prediction in ml_predictions.items():
            if prediction > 0.3:  # Only consider likely root causes
                hypothesis_id = f"ml_{hypothesis_counter}"
                hypothesis_counter += 1
                
                # Determine most likely cause type based on evidence
                cause_type = self._determine_cause_type(entity_id, evidence)
                
                hypothesis = Hypothesis(
                    hypothesis_id=hypothesis_id,
                    description=f"ML-predicted issue with {entity_id}",
                    root_cause_entity_id=entity_id,
                    root_cause_type=cause_type,
                    confidence=float(prediction),
                    ml_confidence=float(prediction),
                    supporting_evidence=self._find_supporting_evidence(
                        entity_id, cause_type, evidence
                    ),
                    explanation=f"Machine learning model predicts {entity_id} as root cause with {prediction:.1%} confidence",
                    causal_chain=self._infer_causal_chain(entity_id, entities, correlation_matrix),
                    impact_radius=len(self._find_affected_entities(entity_id, entities)),
                    feature_importance=ml_results.get("feature_importance", {}),
                    remediation_actions=self._generate_remediation_actions(cause_type),
                    verification_steps=self._generate_verification_steps(cause_type),
                )
                
                hypotheses.append(hypothesis)
        
        # 3. Correlation-based hypotheses
        for entity_a in correlation_matrix:
            for entity_b, correlation in correlation_matrix[entity_a].items():
                if (correlation.is_causal and correlation.absolute_strength > 0.6
                    and correlation.is_significant):
                    
                    # Entity A might be causing issues in Entity B
                    hypothesis_id = f"correlation_{hypothesis_counter}"
                    hypothesis_counter += 1
                    
                    hypothesis = Hypothesis(
                        hypothesis_id=hypothesis_id,
                        description=f"{entity_a} causing issues in {entity_b}",
                        root_cause_entity_id=entity_a,
                        root_cause_type="dependency_issue",
                        confidence=float(correlation.absolute_strength * 0.8),
                        statistical_significance=correlation.p_value,
                        supporting_evidence=self._find_correlation_evidence(
                            entity_a, entity_b, evidence
                        ),
                        explanation=(
                            f"Strong {correlation.correlation_type} correlation "
                            f"(r={correlation.correlation_score:.2f}, p={correlation.p_value:.3f}) "
                            f"with {correlation.lag} lag suggests causal relationship"
                        ),
                        causal_chain=[entity_a, entity_b],
                        impact_radius=len(self._find_affected_entities(entity_a, entities)),
                        remediation_actions=[
                            f"Check health of {entity_a}",
                            f"Verify connection between {entity_a} and {entity_b}",
                            f"Monitor {entity_b} after fixing {entity_a}",
                        ],
                        verification_steps=[
                            f"Check metrics for {entity_a}",
                            f"Test connectivity from {entity_a} to {entity_b}",
                            f"Verify configuration of {entity_a}",
                        ],
                    )
                    
                    hypotheses.append(hypothesis)
        
        # Rank hypotheses by overall confidence
        hypotheses.sort(key=lambda x: x.overall_confidence, reverse=True)
        
        # Limit to top 10 hypotheses
        return hypotheses[:10]
    
    def _find_entities_for_cause(
        self,
        cause: str,
        entities: Dict[str, RCAEntity],
        evidence: Dict[str, Evidence],
    ) -> List[str]:
        """Find entities related to a specific cause."""
        related_entities = []
        
        for entity_id, entity in entities.items():
            # Check evidence for this entity
            entity_evidence = [
                ev for ev in evidence.values()
                if ev.entity_id == entity_id
            ]
            
            # Simple heuristic based on cause type
            if cause == "database_issue":
                # Check for database-related evidence
                db_evidence = [
                    ev for ev in entity_evidence
                    if "database" in ev.description.lower()
                    or "connection" in ev.description.lower()
                ]
                if db_evidence:
                    related_entities.append(entity_id)
            
            elif cause == "network_issue":
                # Check for network-related evidence
                network_evidence = [
                    ev for ev in entity_evidence
                    if "connection" in ev.description.lower()
                    or "timeout" in ev.description.lower()
                    or "refused" in ev.description.lower()
                ]
                if network_evidence:
                    related_entities.append(entity_id)
            
            elif cause == "memory_leak":
                # Check for memory-related evidence
                memory_evidence = [
                    ev for ev in entity_evidence
                    if "memory" in ev.description.lower()
                ]
                if memory_evidence:
                    related_entities.append(entity_id)
            
            else:
                # For other causes, include entities with low health score
                if entity.health_score < 0.7:
                    related_entities.append(entity_id)
        
        return related_entities[:3]  # Limit to top 3 entities
    
    def _find_supporting_evidence(
        self,
        entity_id: str,
        cause_type: str,
        evidence: Dict[str, Evidence],
    ) -> List[str]:
        """Find evidence supporting a hypothesis."""
        supporting = []
        
        for ev_id, ev in evidence.items():
            if ev.entity_id != entity_id:
                continue
            
            # Check if evidence supports the cause type
            if cause_type == "database_issue":
                if "database" in ev.description.lower():
                    supporting.append(ev_id)
            
            elif cause_type == "network_issue":
                if any(keyword in ev.description.lower() 
                      for keyword in ["connection", "timeout", "refused", "network"]):
                    supporting.append(ev_id)
            
            elif cause_type == "memory_leak":
                if "memory" in ev.description.lower():
                    supporting.append(ev_id)
            
            elif cause_type == "configuration_error":
                if any(keyword in ev.description.lower()
                      for keyword in ["config", "parameter", "setting"]):
                    supporting.append(ev_id)
            
            else:
                # For unknown causes, use severity threshold
                if ev.severity > 0.7:
                    supporting.append(ev_id)
        
        return supporting[:5]  # Limit to top 5 evidence
    
    def _generate_explanation(
        self,
        cause: str,
        entity_id: str,
        evidence: Dict[str, Evidence],
    ) -> str:
        """Generate explanation for a hypothesis."""
        
        explanations = {
            "database_issue": (
                f"Database connectivity or performance issue detected in {entity_id}. "
                "This could be due to connection pool exhaustion, slow queries, "
                "or database server problems."
            ),
            "network_issue": (
                f"Network connectivity issue affecting {entity_id}. "
                "This could be due to firewall rules, DNS problems, "
                "network congestion, or infrastructure failures."
            ),
            "memory_leak": (
                f"Memory leak detected in {entity_id}. "
                "Application may be holding references to objects that are no longer needed, "
                "causing gradual memory exhaustion."
            ),
            "configuration_error": (
                f"Configuration issue in {entity_id}. "
                "Recent configuration changes or deployment may have introduced "
                "incorrect settings or missing parameters."
            ),
            "dependency_failure": (
                f"Dependency failure affecting {entity_id}. "
                "A downstream service or resource that this service depends on "
                "is unavailable or responding slowly."
            ),
            "code_bug": (
                f"Code defect in {entity_id}. "
                "Recent code changes may have introduced a bug that manifests "
                "under specific conditions or load patterns."
            ),
        }
        
        return explanations.get(
            cause,
            f"Unknown issue type affecting {entity_id}. "
            "Further investigation is required to determine the exact cause."
        )
    
    def _infer_causal_chain(
        self,
        root_entity: str,
        entities: Dict[str, RCAEntity],
        correlation_matrix: Dict[str, Dict[str, CorrelationResult]],
    ) -> List[str]:
        """Infer causal chain starting from root entity."""
        chain = [root_entity]
        visited = {root_entity}
        
        # Use BFS to find affected entities
        queue = deque([root_entity])
        
        while queue:
            current = queue.popleft()
            
            # Check correlations with other entities
            for other_entity, correlation in correlation_matrix.get(current, {}).items():
                if (other_entity not in visited 
                    and correlation.is_causal 
                    and correlation.correlation_score > 0.5):
                    
                    chain.append(other_entity)
                    visited.add(other_entity)
                    queue.append(other_entity)
            
            # Check dependencies
            if current in entities:
                for dependency in entities[current].dependencies:
                    if dependency not in visited and dependency in entities:
                        chain.append(dependency)
                        visited.add(dependency)
                        queue.append(dependency)
        
        return chain
    
    def _find_affected_entities(
        self,
        root_entity: str,
        entities: Dict[str, RCAEntity],
    ) -> List[str]:
        """Find entities affected by issues in root entity."""
        affected = set()
        
        # Find entities that depend on the root entity
        for entity_id, entity in entities.items():
            if root_entity in entity.dependencies:
                affected.add(entity_id)
        
        return list(affected)
    
    def _determine_cause_type(
        self,
        entity_id: str,
        evidence: Dict[str, Evidence],
    ) -> str:
        """Determine most likely cause type based on evidence."""
        
        # Count evidence by type
        type_scores = defaultdict(float)
        
        for ev in evidence.values():
            if ev.entity_id != entity_id:
                continue
            
            # Score evidence types
            if "database" in ev.description.lower():
                type_scores["database_issue"] += ev.severity
            elif "memory" in ev.description.lower():
                type_scores["memory_leak"] += ev.severity
            elif any(keyword in ev.description.lower()
                    for keyword in ["connection", "timeout", "network"]):
                type_scores["network_issue"] += ev.severity
            elif any(keyword in ev.description.lower()
                    for keyword in ["config", "parameter"]):
                type_scores["configuration_error"] += ev.severity
            elif ev.evidence_type == EvidenceType.DEPENDENCY_FAILURE:
                type_scores["dependency_failure"] += ev.severity
        
        if not type_scores:
            return "unknown"
        
        # Return cause type with highest score
        return max(type_scores.items(), key=lambda x: x[1])[0]
    
    def _find_correlation_evidence(
        self,
        entity_a: str,
        entity_b: str,
        evidence: Dict[str, Evidence],
    ) -> List[str]:
        """Find evidence supporting correlation between two entities."""
        supporting = []
        
        # Find evidence for both entities around the same time
        evidence_a = [ev for ev in evidence.values() if ev.entity_id == entity_a]
        evidence_b = [ev for ev in evidence.values() if ev.entity_id == entity_b]
        
        # Find temporal correlations
        for ev_a in evidence_a:
            for ev_b in evidence_b:
                time_diff = abs((ev_a.timestamp - ev_b.timestamp).total_seconds())
                
                # If events are within 5 minutes and both have high severity
                if (time_diff < 300 
                    and ev_a.severity > 0.7 
                    and ev_b.severity > 0.7):
                    
                    supporting.extend([ev_a.evidence_id, ev_b.evidence_id])
        
        return list(set(supporting))[:4]  # Remove duplicates, limit to 4
    
    def _generate_remediation_actions(self, cause: str) -> List[str]:
        """Generate remediation actions for a cause type."""
        
        actions = {
            "database_issue": [
                "Check database connection pool settings",
                "Review slow query logs",
                "Verify database server health and resources",
                "Consider database connection retry logic",
            ],
            "network_issue": [
                "Check firewall rules and security groups",
                "Verify DNS resolution",
                "Test network connectivity between services",
                "Review load balancer health checks",
            ],
            "memory_leak": [
                "Analyze heap dumps if available",
                "Review recent code changes for memory management",
                "Consider increasing memory limits temporarily",
                "Implement memory usage monitoring and alerts",
            ],
            "configuration_error": [
                "Verify configuration files and environment variables",
                "Check for missing or incorrect parameters",
                "Roll back recent configuration changes if any",
                "Validate configuration against schema",
            ],
            "dependency_failure": [
                "Check health of dependent services",
                "Verify API contracts and versions",
                "Implement circuit breaker pattern",
                "Add fallback mechanisms for critical dependencies",
            ],
        }
        
        return actions.get(cause, [
            "Investigate service logs and metrics",
            "Check recent deployments and changes",
            "Verify infrastructure health",
            "Engage subject matter experts",
        ])
    
    def _generate_verification_steps(self, cause: str) -> List[str]:
        """Generate verification steps for a hypothesis."""
        
        steps = {
            "database_issue": [
                "Test database connectivity from affected service",
                "Run database performance diagnostics",
                "Check database error logs",
                "Verify database user permissions",
            ],
            "network_issue": [
                "Run network connectivity tests",
                "Check firewall and security group logs",
                "Test DNS resolution",
                "Verify network latency and packet loss",
            ],
            "memory_leak": [
                "Monitor memory usage over time",
                "Analyze garbage collection logs",
                "Test with increased heap size",
                "Profile application memory usage",
            ],
        }
        
        return steps.get(cause, [
            "Monitor service metrics after remediation",
            "Verify error rate reduction",
            "Check user-reported issues",
            "Validate system stability",
        ])
    
    def _load_historical_patterns(self) -> List[Dict[str, Any]]:
        """Load historical incident patterns."""
        # In production, this would load from a database
        return [
            {
                "pattern_id": "pattern-001",
                "description": "Database connection pool exhaustion",
                "symptoms": ["high_latency", "connection_errors"],
                "root_cause": "database_issue",
                "entities": ["service-a", "database"],
                "remediation": "Increase connection pool size, implement connection retry",
                "occurrence_count": 5,
            },
            {
                "pattern_id": "pattern-002",
                "description": "Memory leak after deployment",
                "symptoms": ["memory_usage_spike", "restarts"],
                "root_cause": "memory_leak",
                "entities": ["service-b"],
                "remediation": "Roll back deployment, fix memory management",
                "occurrence_count": 3,
            },
        ]
    
    async def _find_similar_incidents(
        self,
        evidence: Dict[str, Evidence],
        timeline: List[TimelineEvent],
    ) -> List[Dict[str, Any]]:
        """Find similar historical incidents."""
        similar = []
        
        # Extract key features from current incident
        current_features = self._extract_incident_features(evidence, timeline)
        
        # Compare with historical patterns
        for pattern in self.historical_patterns:
            similarity = self._calculate_pattern_similarity(current_features, pattern)
            
            if similarity > 0.6:  # Threshold for similarity
                pattern_copy = pattern.copy()
                pattern_copy["similarity_score"] = similarity
                similar.append(pattern_copy)
        
        # Sort by similarity
        similar.sort(key=lambda x: x["similarity_score"], reverse=True)
        
        return similar[:5]  # Return top 5 similar incidents
    
    def _extract_incident_features(
        self,
        evidence: Dict[str, Evidence],
        timeline: List[TimelineEvent],
    ) -> Dict[str, Any]:
        """Extract features from current incident for pattern matching."""
        
        features = {
            "evidence_types": defaultdict(int),
            "entity_count": 0,
            "max_severity": 0.0,
            "timeline_events": len(timeline),
            "error_keywords": [],
        }
        
        # Count evidence types
        for ev in evidence.values():
            ev_type = ev.evidence_type.value
            features["evidence_types"][ev_type] += 1
            features["max_severity"] = max(features["max_severity"], ev.severity)
        
        # Extract error keywords from logs
        error_logs = [
            ev for ev in evidence.values()
            if ev.evidence_type == EvidenceType.LOG_ERROR
        ]
        
        for log in error_logs:
            # Simple keyword extraction
            keywords = re.findall(r'\b\w+error\b|\b\w+exception\b|\bfailed\b|\btimeout\b', 
                                 log.description.lower())
            features["error_keywords"].extend(keywords)
        
        # Count unique entities
        entities = set(ev.entity_id for ev in evidence.values() if ev.entity_id)
        features["entity_count"] = len(entities)
        
        return features
    
    def _calculate_pattern_similarity(
        self,
        current_features: Dict[str, Any],
        historical_pattern: Dict[str, Any],
    ) -> float:
        """Calculate similarity between current incident and historical pattern."""
        similarity = 0.0
        
        # Compare evidence types (simplified)
        current_evidence_dist = current_features["evidence_types"]
        
        # Check if pattern symptoms match current features
        pattern_symptoms = historical_pattern.get("symptoms", [])
        
        if pattern_symptoms:
            # Simple symptom matching
            matched_symptoms = 0
            
            for symptom in pattern_symptoms:
                if symptom == "high_latency" and current_features["max_severity"] > 0.7:
                    matched_symptoms += 1
                elif symptom == "connection_errors" and "error" in current_features["error_keywords"]:
                    matched_symptoms += 1
                elif symptom == "memory_usage_spike" and "memory" in str(current_features["error_keywords"]):
                    matched_symptoms += 1
            
            symptom_similarity = matched_symptoms / len(pattern_symptoms)
            similarity += symptom_similarity * 0.6
        
        # Consider entity count similarity
        pattern_entities = len(historical_pattern.get("entities", []))
        current_entities = current_features["entity_count"]
        
        if pattern_entities > 0 and current_entities > 0:
            entity_similarity = 1.0 - abs(pattern_entities - current_entities) / max(pattern_entities, current_entities)
            similarity += entity_similarity * 0.4
        
        return min(1.0, similarity)
    
    async def _generate_recommendations(
        self,
        hypotheses: List[Hypothesis],
        evidence: Dict[str, Evidence],
        similar_incidents: List[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        """Generate recommendations based on analysis."""
        
        immediate_actions = []
        long_term_recommendations = []
        
        if not hypotheses:
            immediate_actions.append("Collect more diagnostic data")
            immediate_actions.append("Engage subject matter experts")
            return {
                "immediate_actions": immediate_actions,
                "long_term_recommendations": long_term_recommendations,
            }
        
        # Get top hypothesis
        top_hypothesis = hypotheses[0]
        
        # Immediate actions from top hypothesis
        immediate_actions.extend(top_hypothesis.remediation_actions[:3])
        
        # Add verification steps
        immediate_actions.extend([
            f"Verify hypothesis: {top_hypothesis.description}",
            f"Check {top_hypothesis.root_cause_entity_id} metrics and logs",
        ])
        
        # Long-term recommendations
        if similar_incidents:
            # Learn from similar incidents
            most_similar = similar_incidents[0]
            long_term_recommendations.append(
                f"Implement permanent fix for pattern: {most_similar['description']}"
            )
            long_term_recommendations.append(
                f"Add monitoring for {most_similar['root_cause']} scenarios"
            )
        
        # General recommendations
        long_term_recommendations.extend([
            "Implement automated root cause analysis in CI/CD pipeline",
            "Create runbooks for common failure patterns",
            "Improve observability with distributed tracing",
            "Conduct regular chaos engineering experiments",
        ])
        
        return {
            "immediate_actions": immediate_actions,
            "long_term_recommendations": long_term_recommendations,
        }
    
    def _prepare_analysis_result(
        self,
        incident_id: str,
        entities: Dict[str, RCAEntity],
        evidence: Dict[str, Evidence],
        correlation_matrix: Dict[str, Dict[str, CorrelationResult]],
        dependency_graph: Dict[str, List[str]],
        timeline: List[TimelineEvent],
        hypotheses: List[Hypothesis],
        bayesian_results: Dict[str, float],
        ml_results: Dict[str, Any],
        similar_incidents: List[Dict[str, Any]],
        recommendations: Dict[str, List[str]],
    ) -> RCAResult:
        """Prepare final analysis result."""
        
        # Calculate overall confidence
        confidence_score = 0.0
        if hypotheses:
            confidence_score = hypotheses[0].overall_confidence
        
        # Prepare statistical tests results
        statistical_tests = {}
        for entity_a in correlation_matrix:
            for entity_b, correlation in correlation_matrix[entity_a].items():
                if correlation.p_value is not None:
                    key = f"{entity_a}_{entity_b}_{correlation.correlation_type}"
                    statistical_tests[key] = correlation.p_value
        
        return RCAResult(
            incident_id=incident_id,
            analysis_start=datetime.utcnow() - timedelta(minutes=5),
            analysis_end=datetime.utcnow(),
            entities=entities,
            evidence=evidence,
            correlation_matrix=correlation_matrix,
            dependency_graph=dependency_graph,
            timeline=timeline,
            hypotheses=hypotheses,
            top_hypothesis=hypotheses[0] if hypotheses else None,
            similar_incidents=similar_incidents,
            statistical_tests=statistical_tests,
            ml_predictions=ml_results.get("predictions", {}),
            immediate_actions=recommendations.get("immediate_actions", []),
            long_term_recommendations=recommendations.get("long_term_recommendations", []),
            analysis_methods_used=[
                "bayesian_inference",
                "machine_learning",
                "correlation_analysis",
                "timeline_reconstruction",
                "pattern_matching",
            ],
            confidence_score=confidence_score,
        )
    
    def _get_processing_time(self) -> float:
        """Calculate processing time for the agent."""
        # This would track actual execution time
        # For now, return placeholder
        return 10.0  # seconds