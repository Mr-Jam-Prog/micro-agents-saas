"""
Performance Bottleneck Analyzer Agent

Agent spécialisé dans l'analyse des goulets d'étranglement de performance
avec tracing de transaction end-to-end, analyse de graphe de dépendances,
détection de contention de ressources, et optimisation des requêtes.
"""

import asyncio
import json
import logging
import statistics
import time
import tracemalloc
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Any, Tuple, Union, Deque
from dataclasses import dataclass, field
import concurrent.futures
import hashlib

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, validator
import networkx as nx
from scipy import stats
import prometheus_client
from sqlparse import parse as sqlparse

from microagents.core.base.agent import BaseAgent, AgentResult
from microagents.core.base.context import AgentContext
from microagents.core.ai.anomaly_detector_advanced import AdvancedAnomalyDetector
from microagents.monitoring.metrics.collector import MetricsCollector
from microagents.monitoring.tracing.tracer import OpenTelemetryTracer
from microagents.utils.concurrency.manager import ConcurrencyManager
from microagents.utils.validation.validators import validate_performance_threshold

logger = logging.getLogger(__name__)


class PerformanceMetric(Enum):
    """Métriques de performance"""
    RESPONSE_TIME = "response_time"
    THROUGHPUT = "throughput"
    ERROR_RATE = "error_rate"
    CPU_USAGE = "cpu_usage"
    MEMORY_USAGE = "memory_usage"
    NETWORK_LATENCY = "network_latency"
    DATABASE_LATENCY = "database_latency"
    GARBAGE_COLLECTION = "gc_time"
    I_O_OPERATIONS = "io_operations"
    CONCURRENT_REQUESTS = "concurrent_requests"


class BottleneckType(Enum):
    """Types de goulets d'étranglement"""
    CPU_BOUND = "cpu_bound"
    MEMORY_BOUND = "memory_bound"
    I_O_BOUND = "io_bound"
    NETWORK_BOUND = "network_bound"
    DATABASE_BOUND = "database_bound"
    LOCK_CONTENTION = "lock_contention"
    GARBAGE_COLLECTION = "garbage_collection"
    CONCURRENCY = "concurrency"
    CASCADE_FAILURE = "cascade_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


class SeverityLevel(Enum):
    """Niveaux de sévérité"""
    CRITICAL = "critical"  # Impact immédiat sur les utilisateurs
    HIGH = "high"          # Impact significatif
    MEDIUM = "medium"      # Impact modéré
    LOW = "low"            # Impact minimal
    INFO = "info"          # À surveiller


@dataclass
class PerformanceIssue:
    """Problème de performance identifié"""
    issue_id: str
    timestamp: datetime
    bottleneck_type: BottleneckType
    severity: SeverityLevel
    component: str
    description: str
    impact_score: float
    confidence: float
    metrics: Dict[str, float]
    root_cause: Optional[str] = None
    recommendations: List[str] = field(default_factory=list)
    related_traces: List[str] = field(default_factory=list)
    related_logs: List[str] = field(default_factory=list)
    business_impact: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            'issue_id': self.issue_id,
            'timestamp': self.timestamp.isoformat(),
            'bottleneck_type': self.bottleneck_type.value,
            'severity': self.severity.value,
            'component': self.component,
            'description': self.description,
            'impact_score': self.impact_score,
            'confidence': self.confidence,
            'metrics': self.metrics,
            'root_cause': self.root_cause,
            'recommendations': self.recommendations,
            'business_impact': self.business_impact
        }


@dataclass
class TransactionTrace:
    """Trace de transaction complète"""
    trace_id: str
    start_time: datetime
    end_time: datetime
    duration_ms: float
    service_name: str
    operation_name: str
    status_code: str
    spans: List[Dict[str, Any]]
    parent_trace_id: Optional[str] = None
    user_id: Optional[str] = None
    business_context: Optional[str] = None
    
    def get_critical_path(self) -> List[Dict[str, Any]]:
        """Calcule le chemin critique de la transaction"""
        # Identification des spans avec le plus long temps d'exécution
        critical_spans = sorted(
            self.spans,
            key=lambda x: x.get('duration_ms', 0),
            reverse=True
        )[:3]  # Top 3 spans les plus longs
        
        return critical_spans
    
    def calculate_percentiles(self) -> Dict[str, float]:
        """Calcule les percentiles de durée des spans"""
        durations = [span.get('duration_ms', 0) for span in self.spans]
        if not durations:
            return {}
        
        return {
            'p50': np.percentile(durations, 50),
            'p90': np.percentile(durations, 90),
            'p95': np.percentile(durations, 95),
            'p99': np.percentile(durations, 99),
            'max': max(durations),
            'min': min(durations),
            'mean': np.mean(durations)
        }


class TransactionTracer:
    """Tracer de transactions end-to-end"""
    
    def __init__(self, tracer: OpenTelemetryTracer):
        """
        Initialise le tracer de transactions.
        
        Args:
            tracer: Tracer OpenTelemetry
        """
        self.tracer = tracer
        self.transaction_cache = {}
        self.trace_buffer = deque(maxlen=10000)
        
        # Configuration
        self.slow_transaction_threshold = 1000  # ms
        self.error_transaction_threshold = 0.05  # 5%
        
        logger.info("TransactionTracer initialisé")
    
    async def analyze_trace(self, trace_data: Dict[str, Any]) -> TransactionTrace:
        """
        Analyse une trace de transaction.
        
        Args:
            trace_data: Données de trace OpenTelemetry
            
        Returns:
            TransactionTrace analysée
        """
        try:
            trace_id = trace_data.get('trace_id', 'unknown')
            
            # Extraction des spans
            spans = self._extract_spans(trace_data)
            
            # Calcul des métriques
            start_time, end_time, duration = self._calculate_trace_timing(spans)
            
            # Identification du service principal
            service_name = self._identify_main_service(spans)
            
            # Création de la transaction trace
            transaction = TransactionTrace(
                trace_id=trace_id,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration,
                service_name=service_name,
                operation_name=trace_data.get('operation_name', 'unknown'),
                status_code=trace_data.get('status_code', '200'),
                spans=spans,
                parent_trace_id=trace_data.get('parent_trace_id'),
                user_id=trace_data.get('user_id'),
                business_context=trace_data.get('business_context')
            )
            
            # Mise en cache
            self.transaction_cache[trace_id] = transaction
            self.trace_buffer.append(transaction)
            
            return transaction
            
        except Exception as e:
            logger.error(f"Erreur analyse trace: {str(e)}")
            raise
    
    def _extract_spans(self, trace_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrait et normalise les spans d'une trace"""
        spans = []
        
        for span_data in trace_data.get('spans', []):
            span = {
                'span_id': span_data.get('span_id'),
                'name': span_data.get('name', 'unknown'),
                'start_time': datetime.fromisoformat(span_data.get('start_time')),
                'end_time': datetime.fromisoformat(span_data.get('end_time')),
                'duration_ms': span_data.get('duration_ms', 0),
                'attributes': span_data.get('attributes', {}),
                'events': span_data.get('events', []),
                'status': span_data.get('status', {}),
                'kind': span_data.get('kind', 'INTERNAL'),
                'parent_span_id': span_data.get('parent_span_id')
            }
            
            # Ajout de métriques spécifiques
            span['metrics'] = self._extract_span_metrics(span_data)
            spans.append(span)
        
        return spans
    
    def _extract_span_metrics(self, span_data: Dict[str, Any]) -> Dict[str, float]:
        """Extrait les métriques d'un span"""
        metrics = {}
        
        # Extraction des métriques custom
        attributes = span_data.get('attributes', {})
        
        # Temps CPU
        if 'cpu.time' in attributes:
            metrics['cpu_time_ms'] = float(attributes['cpu.time'])
        
        # Utilisation mémoire
        if 'memory.usage' in attributes:
            metrics['memory_usage_mb'] = float(attributes['memory.usage'])
        
        # I/O
        if 'io.operations' in attributes:
            metrics['io_operations'] = int(attributes['io.operations'])
        
        # Erreurs
        if 'error.count' in attributes:
            metrics['error_count'] = int(attributes['error.count'])
        
        return metrics
    
    def _calculate_trace_timing(
        self,
        spans: List[Dict[str, Any]]
    ) -> Tuple[datetime, datetime, float]:
        """Calcule le timing d'une trace"""
        if not spans:
            now = datetime.utcnow()
            return now, now, 0.0
        
        start_times = [span['start_time'] for span in spans]
        end_times = [span['end_time'] for span in spans]
        
        start_time = min(start_times)
        end_time = max(end_times)
        duration = (end_time - start_time).total_seconds() * 1000  # ms
        
        return start_time, end_time, duration
    
    def _identify_main_service(self, spans: List[Dict[str, Any]]) -> str:
        """Identifie le service principal d'une trace"""
        service_counts = defaultdict(int)
        
        for span in spans:
            service = span['attributes'].get('service.name', 'unknown')
            service_counts[service] += 1
        
        if service_counts:
            return max(service_counts.items(), key=lambda x: x[1])[0]
        
        return 'unknown'
    
    def detect_slow_transactions(
        self,
        window_minutes: int = 15
    ) -> List[TransactionTrace]:
        """
        Détecte les transactions lentes.
        
        Args:
            window_minutes: Fenêtre temporelle
            
        Returns:
            Transactions lentes
        """
        cutoff_time = datetime.utcnow() - timedelta(minutes=window_minutes)
        
        slow_transactions = []
        for transaction in self.trace_buffer:
            if (transaction.start_time > cutoff_time and 
                transaction.duration_ms > self.slow_transaction_threshold):
                slow_transactions.append(transaction)
        
        return slow_transactions
    
    def analyze_dependency_patterns(
        self,
        window_minutes: int = 30
    ) -> Dict[str, Any]:
        """
        Analyse les patterns de dépendance.
        
        Args:
            window_minutes: Fenêtre temporelle
            
        Returns:
            Analyse des dépendances
        """
        cutoff_time = datetime.utcnow() - timedelta(minutes=window_minutes)
        recent_transactions = [
            t for t in self.trace_buffer 
            if t.start_time > cutoff_time
        ]
        
        if not recent_transactions:
            return {'error': 'No recent transactions'}
        
        # Construction du graphe de dépendances
        dependency_graph = nx.DiGraph()
        service_metrics = defaultdict(lambda: {
            'total_calls': 0,
            'total_duration': 0,
            'error_count': 0,
            'called_services': set()
        })
        
        for transaction in recent_transactions:
            for span in transaction.spans:
                service = span['attributes'].get('service.name', 'unknown')
                operation = span['name']
                
                # Mise à jour des métriques du service
                service_metrics[service]['total_calls'] += 1
                service_metrics[service]['total_duration'] += span['duration_ms']
                
                if span['status'].get('code') != 'OK':
                    service_metrics[service]['error_count'] += 1
                
                # Ajout des dépendances
                parent_span = next(
                    (s for s in transaction.spans 
                     if s['span_id'] == span.get('parent_span_id')),
                    None
                )
                
                if parent_span:
                    parent_service = parent_span['attributes'].get('service.name', 'unknown')
                    if parent_service != service:
                        # Ajout de l'arête dans le graphe
                        if not dependency_graph.has_edge(parent_service, service):
                            dependency_graph.add_edge(parent_service, service, weight=0)
                        
                        # Mise à jour du poids
                        dependency_graph[parent_service][service]['weight'] += 1
                        service_metrics[parent_service]['called_services'].add(service)
        
        # Analyse du graphe
        critical_services = self._identify_critical_services(dependency_graph)
        
        return {
            'dependency_graph': {
                'nodes': list(dependency_graph.nodes()),
                'edges': [
                    {
                        'source': u,
                        'target': v,
                        'weight': data['weight']
                    }
                    for u, v, data in dependency_graph.edges(data=True)
                ],
                'centrality': dict(nx.degree_centrality(dependency_graph))
            },
            'service_metrics': service_metrics,
            'critical_services': critical_services,
            'transaction_count': len(recent_transactions)
        }
    
    def _identify_critical_services(
        self,
        dependency_graph: nx.DiGraph
    ) -> List[Dict[str, Any]]:
        """Identifie les services critiques"""
        if not dependency_graph.nodes():
            return []
        
        critical_services = []
        
        # Calcul de la centralité
        degree_centrality = nx.degree_centrality(dependency_graph)
        betweenness_centrality = nx.betweenness_centrality(dependency_graph)
        
        for service in dependency_graph.nodes():
            # Score de criticité composite
            criticality_score = (
                degree_centrality.get(service, 0) * 0.4 +
                betweenness_centrality.get(service, 0) * 0.6
            )
            
            # Nombre de dépendances entrantes et sortantes
            in_degree = dependency_graph.in_degree(service)
            out_degree = dependency_graph.out_degree(service)
            
            if criticality_score > 0.3:  # Seuil de criticité
                critical_services.append({
                    'service': service,
                    'criticality_score': criticality_score,
                    'in_degree': in_degree,
                    'out_degree': out_degree,
                    'total_dependencies': in_degree + out_degree
                })
        
        # Tri par criticité
        return sorted(critical_services, key=lambda x: x['criticality_score'], reverse=True)


class DatabaseQueryAnalyzer:
    """Analyseur de requêtes de base de données"""
    
    def __init__(self):
        """Initialise l'analyseur de requêtes"""
        self.query_cache = {}
        self.query_patterns = defaultdict(lambda: {
            'count': 0,
            'total_time': 0,
            'min_time': float('inf'),
            'max_time': 0,
            'errors': 0
        })
        
        # Seuils d'alerte
        self.slow_query_threshold = 100  # ms
        self.frequent_query_threshold = 100  # requêtes/minute
        
        logger.info("DatabaseQueryAnalyzer initialisé")
    
    def analyze_query(self, query_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyse une requête de base de données.
        
        Args:
            query_data: Données de la requête
            
        Returns:
            Analyse de la requête
        """
        try:
            query_text = query_data.get('query', '')
            normalized_query = self._normalize_query(query_text)
            query_hash = hashlib.md5(normalized_query.encode(), usedforsecurity=False).hexdigest()
            
            # Analyse de la requête
            query_analysis = {
                'query_hash': query_hash,
                'original_query': query_text[:500],  # Limite de taille
                'normalized_query': normalized_query,
                'execution_time': query_data.get('execution_time_ms', 0),
                'rows_returned': query_data.get('rows_returned'),
                'timestamp': query_data.get('timestamp', datetime.utcnow()),
                'database': query_data.get('database', 'unknown'),
                'user': query_data.get('user', 'unknown'),
                'application': query_data.get('application', 'unknown')
            }
            
            # Analyse structurelle
            query_analysis['structural_analysis'] = self._analyze_query_structure(query_text)
            
            # Détection de problèmes
            query_analysis['issues'] = self._detect_query_issues(query_analysis)
            
            # Mise à jour des statistiques
            self._update_query_statistics(query_hash, query_analysis)
            
            return query_analysis
            
        except Exception as e:
            logger.error(f"Erreur analyse requête: {str(e)}")
            return {'error': str(e)}
    
    def _normalize_query(self, query: str) -> str:
        """Normalise une requête SQL pour le regroupement"""
        import re
        try:
            # Parsing SQL
            parsed = sqlparse(query)
            
            # Normalisation
            normalized = []
            for stmt in parsed:
                # Suppression des valeurs littérales
                normalized_stmt = str(stmt).lower()
                
                # Remplacement des valeurs
                normalized_stmt = re.sub(r'\b\d+\b', '?', normalized_stmt)
                normalized_stmt = re.sub(r"'[^']*'", '?', normalized_stmt)
                normalized_stmt = re.sub(r'"[^"]*"', '?', normalized_stmt)
                
                # Suppression des espaces excessifs
                normalized_stmt = ' '.join(normalized_stmt.split())
                
                normalized.append(normalized_stmt)
            
            return '; '.join(normalized)
            
        except Exception:
            # Fallback: suppression simple des valeurs
            normalized = query.lower()
            normalized = re.sub(r'\b\d+\b', '?', normalized)
            normalized = re.sub(r"'[^']*'", '?', normalized)
            normalized = re.sub(r'"[^"]*"', '?', normalized)
            return ' '.join(normalized.split())
    
    def _analyze_query_structure(self, query: str) -> Dict[str, Any]:
        """Analyse la structure d'une requête SQL"""
        analysis = {
            'has_joins': False,
            'has_subqueries': False,
            'has_group_by': False,
            'has_order_by': False,
            'has_limit': False,
            'table_count': 0,
            'join_count': 0,
            'complexity_score': 0
        }
        
        query_lower = query.lower()
        
        # Détection des patterns
        analysis['has_joins'] = any(
            keyword in query_lower 
            for keyword in [' join ', ' inner join ', ' left join ', ' right join ']
        )
        
        analysis['has_subqueries'] = 'select' in query_lower[query_lower.find(' from ') + 6:]
        analysis['has_group_by'] = 'group by' in query_lower
        analysis['has_order_by'] = 'order by' in query_lower
        analysis['has_limit'] = 'limit' in query_lower
        
        # Comptage des tables
        from_match = re.search(r'from\s+([\w,\s]+)(?:\s+where|\s+group|\s+order|\s+limit|$)', query_lower)
        if from_match:
            tables = from_match.group(1).split(',')
            analysis['table_count'] = len([t.strip() for t in tables if t.strip()])
        
        # Comptage des JOINs
        analysis['join_count'] = len(re.findall(r'\bjoin\b', query_lower))
        
        # Calcul du score de complexité
        complexity = 0
        complexity += analysis['table_count'] * 2
        complexity += analysis['join_count'] * 3
        complexity += 5 if analysis['has_subqueries'] else 0
        complexity += 3 if analysis['has_group_by'] else 0
        complexity += 2 if analysis['has_order_by'] else 0
        
        analysis['complexity_score'] = complexity
        
        return analysis
    
    def _detect_query_issues(self, query_analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Détecte les problèmes dans une requête"""
        issues = []
        
        execution_time = query_analysis.get('execution_time', 0)
        structure = query_analysis.get('structural_analysis', {})
        
        # Requête lente
        if execution_time > self.slow_query_threshold:
            issues.append({
                'type': 'slow_query',
                'severity': SeverityLevel.HIGH if execution_time > 500 else SeverityLevel.MEDIUM,
                'description': f'Query execution time {execution_time}ms exceeds threshold {self.slow_query_threshold}ms',
                'execution_time': execution_time,
                'threshold': self.slow_query_threshold
            })
        
        # Requête complexe sans LIMIT
        if (structure.get('complexity_score', 0) > 10 and 
            not structure.get('has_limit') and
            'select' in query_analysis.get('normalized_query', '').lower()):
            issues.append({
                'type': 'missing_limit',
                'severity': SeverityLevel.MEDIUM,
                'description': 'Complex SELECT query missing LIMIT clause',
                'complexity_score': structure['complexity_score']
            })
        
        # CROSS JOIN implicite
        if structure.get('table_count', 0) > 1 and not structure.get('has_joins'):
            issues.append({
                'type': 'implicit_cross_join',
                'severity': SeverityLevel.HIGH,
                'description': f'Implicit CROSS JOIN detected between {structure["table_count"]} tables',
                'table_count': structure['table_count']
            })
        
        # SELECT *
        if 'select *' in query_analysis.get('normalized_query', ''):
            issues.append({
                'type': 'select_star',
                'severity': SeverityLevel.LOW,
                'description': 'SELECT * usage detected - consider specifying columns'
            })
        
        # Sous-requêtes corrélées
        if structure.get('has_subqueries'):
            # Détection simplifiée de sous-requêtes corrélées
            query_lower = query_analysis.get('original_query', '').lower()
            if re.search(r'where\s+exists\s*\(', query_lower) or re.search(r'where\s+.*\s+in\s*\(', query_lower):
                issues.append({
                    'type': 'correlated_subquery',
                    'severity': SeverityLevel.MEDIUM,
                    'description': 'Potential correlated subquery detected - consider JOIN alternative'
                })
        
        return issues
    
    def _update_query_statistics(
        self,
        query_hash: str,
        query_analysis: Dict[str, Any]
    ) -> None:
        """Met à jour les statistiques des requêtes"""
        stats = self.query_patterns[query_hash]
        
        execution_time = query_analysis.get('execution_time', 0)
        
        stats['count'] += 1
        stats['total_time'] += execution_time
        stats['min_time'] = min(stats['min_time'], execution_time)
        stats['max_time'] = max(stats['max_time'], execution_time)
        
        if query_analysis.get('issues'):
            stats['errors'] += 1
    
    def generate_query_recommendations(
        self,
        query_hash: str
    ) -> List[str]:
        """Génère des recommandations d'optimisation pour une requête"""
        stats = self.query_patterns.get(query_hash)
        if not stats or stats['count'] < 10:
            return []
        
        recommendations = []
        avg_time = stats['total_time'] / stats['count']
        
        if avg_time > self.slow_query_threshold:
            recommendations.append(
                f"Query average time {avg_time:.1f}ms exceeds threshold. "
                "Consider adding indexes or optimizing query structure."
            )
        
        if stats['max_time'] > avg_time * 5:
            recommendations.append(
                f"Query execution time varies significantly (min: {stats['min_time']:.1f}ms, "
                f"max: {stats['max_time']:.1f}ms). Check for data skew or locking issues."
            )
        
        return recommendations
    
    def get_top_slow_queries(
        self,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Retourne les requêtes les plus lentes"""
        slow_queries = []
        
        for query_hash, stats in self.query_patterns.items():
            if stats['count'] > 0:
                avg_time = stats['total_time'] / stats['count']
                slow_queries.append({
                    'query_hash': query_hash,
                    'average_time_ms': avg_time,
                    'total_executions': stats['count'],
                    'min_time_ms': stats['min_time'],
                    'max_time_ms': stats['max_time'],
                    'error_rate': stats['errors'] / stats['count'] if stats['count'] > 0 else 0
                })
        
        # Tri par temps moyen décroissant
        return sorted(slow_queries, key=lambda x: x['average_time_ms'], reverse=True)[:limit]


class ResourceAnalyzer:
    """Analyseur de contention de ressources"""
    
    def __init__(self):
        """Initialise l'analyseur de ressources"""
        self.cpu_history = deque(maxlen=1000)
        self.memory_history = deque(maxlen=1000)
        self.io_history = deque(maxlen=1000)
        self.network_history = deque(maxlen=1000)
        
        # Seuils
        self.cpu_threshold = 80.0  # %
        self.memory_threshold = 90.0  # %
        self.io_threshold = 1000  # IOPS
        self.network_threshold = 100  # MB/s
        
        logger.info("ResourceAnalyzer initialisé")
    
    def analyze_resource_metrics(
        self,
        metrics_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Analyse les métriques de ressources.
        
        Args:
            metrics_data: Données de métriques
            
        Returns:
            Analyse des ressources
        """
        analysis = {
            'timestamp': datetime.utcnow(),
            'cpu_analysis': {},
            'memory_analysis': {},
            'io_analysis': {},
            'network_analysis': {},
            'contention_issues': []
        }
        
        # Analyse CPU
        if 'cpu' in metrics_data:
            cpu_metrics = metrics_data['cpu']
            analysis['cpu_analysis'] = self._analyze_cpu_metrics(cpu_metrics)
            
            if self._detect_cpu_contention(cpu_metrics):
                analysis['contention_issues'].append({
                    'type': 'cpu_contention',
                    'severity': SeverityLevel.HIGH,
                    'description': f'CPU contention detected: {cpu_metrics.get("usage_percent", 0)}% usage'
                })
        
        # Analyse mémoire
        if 'memory' in metrics_data:
            memory_metrics = metrics_data['memory']
            analysis['memory_analysis'] = self._analyze_memory_metrics(memory_metrics)
            
            if self._detect_memory_leak(memory_metrics):
                analysis['contention_issues'].append({
                    'type': 'memory_leak',
                    'severity': SeverityLevel.CRITICAL,
                    'description': 'Potential memory leak detected'
                })
        
        # Analyse I/O
        if 'io' in metrics_data:
            io_metrics = metrics_data['io']
            analysis['io_analysis'] = self._analyze_io_metrics(io_metrics)
            
            if self._detect_io_bottleneck(io_metrics):
                analysis['contention_issues'].append({
                    'type': 'io_bottleneck',
                    'severity': SeverityLevel.HIGH,
                    'description': 'I/O bottleneck detected'
                })
        
        # Analyse réseau
        if 'network' in metrics_data:
            network_metrics = metrics_data['network']
            analysis['network_analysis'] = self._analyze_network_metrics(network_metrics)
            
            if self._detect_network_congestion(network_metrics):
                analysis['contention_issues'].append({
                    'type': 'network_congestion',
                    'severity': SeverityLevel.MEDIUM,
                    'description': 'Network congestion detected'
                })
        
        return analysis
    
    def _analyze_cpu_metrics(self, cpu_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Analyse les métriques CPU"""
        analysis = {
            'usage_percent': cpu_metrics.get('usage_percent', 0),
            'load_average': cpu_metrics.get('load_average', []),
            'context_switches': cpu_metrics.get('context_switches', 0),
            'interrupts': cpu_metrics.get('interrupts', 0)
        }
        
        # Calcul de la charge CPU normalisée
        if 'load_average' in cpu_metrics and isinstance(cpu_metrics['load_average'], list):
            load_avg = cpu_metrics['load_average']
            if len(load_avg) >= 1:
                analysis['load_normalized'] = load_avg[0] / analysis['usage_percent'] if analysis['usage_percent'] > 0 else 0
        
        # Détection de saturation
        analysis['saturated'] = analysis['usage_percent'] > self.cpu_threshold
        
        return analysis
    
    def _analyze_memory_metrics(self, memory_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Analyse les métriques mémoire"""
        analysis = {
            'used_percent': memory_metrics.get('used_percent', 0),
            'used_bytes': memory_metrics.get('used_bytes', 0),
            'available_bytes': memory_metrics.get('available_bytes', 0),
            'swap_used_percent': memory_metrics.get('swap_used_percent', 0),
            'page_faults': memory_metrics.get('page_faults', 0),
            'cache_usage': memory_metrics.get('cache_usage', 0)
        }
        
        # Détection de pression mémoire
        analysis['under_pressure'] = (
            analysis['used_percent'] > self.memory_threshold or
            analysis['swap_used_percent'] > 50
        )
        
        return analysis
    
    def _analyze_io_metrics(self, io_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Analyse les métriques I/O"""
        analysis = {
            'read_iops': io_metrics.get('read_iops', 0),
            'write_iops': io_metrics.get('write_iops', 0),
            'read_throughput_mbps': io_metrics.get('read_throughput_mbps', 0),
            'write_throughput_mbps': io_metrics.get('write_throughput_mbps', 0),
            'await_time_ms': io_metrics.get('await_time_ms', 0),
            'utilization_percent': io_metrics.get('utilization_percent', 0)
        }
        
        # Détection de bottleneck
        analysis['bottleneck'] = (
            analysis['utilization_percent'] > 90 or
            analysis['await_time_ms'] > 100 or
            (analysis['read_iops'] + analysis['write_iops']) > self.io_threshold
        )
        
        return analysis
    
    def _analyze_network_metrics(self, network_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Analyse les métriques réseau"""
        analysis = {
            'rx_mbps': network_metrics.get('rx_mbps', 0),
            'tx_mbps': network_metrics.get('tx_mbps', 0),
            'packets_in': network_metrics.get('packets_in', 0),
            'packets_out': network_metrics.get('packets_out', 0),
            'error_rate': network_metrics.get('error_rate', 0),
            'retransmit_rate': network_metrics.get('retransmit_rate', 0)
        }
        
        # Détection de congestion
        analysis['congested'] = (
            (analysis['rx_mbps'] + analysis['tx_mbps']) > self.network_threshold or
            analysis['error_rate'] > 0.01 or
            analysis['retransmit_rate'] > 0.05
        )
        
        return analysis
    
    def _detect_cpu_contention(self, cpu_metrics: Dict[str, Any]) -> bool:
        """Détecte la contention CPU"""
        usage = cpu_metrics.get('usage_percent', 0)
        load_avg = cpu_metrics.get('load_average', [])
        
        if not load_avg:
            return usage > self.cpu_threshold
        
        # Vérifie si la charge système est élevée par rapport à l'utilisation CPU
        if len(load_avg) >= 1 and usage > 70:
            # Ratio charge/utilisation élevé indique des processus en attente
            load_ratio = load_avg[0] / max(usage, 1)
            return load_ratio > 1.5
        
        return usage > self.cpu_threshold
    
    def _detect_memory_leak(self, memory_metrics: Dict[str, Any]) -> bool:
        """Détecte les fuites mémoire"""
        # Historique simple - dans une implémentation réelle, on analyserait la tendance
        current_usage = memory_metrics.get('used_percent', 0)
        self.memory_history.append(current_usage)
        
        if len(self.memory_history) < 100:
            return False
        
        # Vérifie la tendance à la hausse
        recent_trend = list(self.memory_history)[-50:]
        older_trend = list(self.memory_history)[-100:-50]
        
        if not older_trend or not recent_trend:
            return False
        
        recent_avg = statistics.mean(recent_trend)
        older_avg = statistics.mean(older_trend)
        
        # Si l'utilisation a augmenté de plus de 20% sur la période
        return (recent_avg - older_avg) > 20
    
    def _detect_io_bottleneck(self, io_metrics: Dict[str, Any]) -> bool:
        """Détecte les bottlenecks I/O"""
        await_time = io_metrics.get('await_time_ms', 0)
        utilization = io_metrics.get('utilization_percent', 0)
        total_iops = io_metrics.get('read_iops', 0) + io_metrics.get('write_iops', 0)
        
        return (
            await_time > 100 or  # Latence élevée
            utilization > 90 or   # Utilisation élevée
            total_iops > self.io_threshold  # Nombre élevé d'IOPS
        )
    
    def _detect_network_congestion(self, network_metrics: Dict[str, Any]) -> bool:
        """Détecte la congestion réseau"""
        total_throughput = network_metrics.get('rx_mbps', 0) + network_metrics.get('tx_mbps', 0)
        error_rate = network_metrics.get('error_rate', 0)
        retransmit_rate = network_metrics.get('retransmit_rate', 0)
        
        return (
            total_throughput > self.network_threshold or
            error_rate > 0.01 or
            retransmit_rate > 0.05
        )
    
    def detect_resource_contention_patterns(
        self,
        window_minutes: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Détecte les patterns de contention de ressources.
        
        Args:
            window_minutes: Fenêtre temporelle
            
        Returns:
            Patterns de contention
        """
        patterns = []
        
        # Analyse CPU
        if len(self.cpu_history) > 100:
            cpu_trend = self._analyze_trend(list(self.cpu_history)[-100:])
            if cpu_trend['trend'] == 'increasing' and cpu_trend['slope'] > 0.5:
                patterns.append({
                    'type': 'cpu_trend_increasing',
                    'severity': SeverityLevel.MEDIUM,
                    'description': f'CPU usage trending upward (slope: {cpu_trend["slope"]:.2f})',
                    'current_usage': self.cpu_history[-1] if self.cpu_history else 0
                })
        
        # Analyse mémoire
        if len(self.memory_history) > 100:
            memory_trend = self._analyze_trend(list(self.memory_history)[-100:])
            if memory_trend['trend'] == 'increasing' and memory_trend['slope'] > 0.3:
                patterns.append({
                    'type': 'memory_trend_increasing',
                    'severity': SeverityLevel.HIGH,
                    'description': f'Memory usage trending upward (slope: {memory_trend["slope"]:.2f})',
                    'current_usage': self.memory_history[-1] if self.memory_history else 0
                })
        
        return patterns
    
    def _analyze_trend(self, data: List[float]) -> Dict[str, Any]:
        """Analyse la tendance d'une série de données"""
        if len(data) < 2:
            return {'trend': 'stable', 'slope': 0.0}
        
        # Régression linéaire simple
        x = list(range(len(data)))
        y = data
        
        slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
        
        # Détermination de la tendance
        if abs(slope) < 0.1:
            trend = 'stable'
        elif slope > 0:
            trend = 'increasing'
        else:
            trend = 'decreasing'
        
        return {
            'trend': trend,
            'slope': slope,
            'r_squared': r_value ** 2,
            'p_value': p_value
        }


class PerformanceBottleneckAnalyzer(BaseAgent):
    """Analyseur de goulets d'étranglement de performance"""
    
    def __init__(
        self,
        agent_id: str,
        context: AgentContext,
        tracing_endpoint: Optional[str] = None,
        metrics_endpoint: Optional[str] = None
    ):
        """
        Initialise l'analyseur de performance.
        
        Args:
            agent_id: ID de l'agent
            context: Contexte de l'agent
            tracing_endpoint: Endpoint pour les traces OpenTelemetry
            metrics_endpoint: Endpoint pour les métriques
        """
        super().__init__(agent_id, context)
        
        # Composants d'analyse
        self.tracer = OpenTelemetryTracer(endpoint=tracing_endpoint)
        self.transaction_tracer = TransactionTracer(self.tracer)
        self.query_analyzer = DatabaseQueryAnalyzer()
        self.resource_analyzer = ResourceAnalyzer()
        
        # Détecteur d'anomalies
        self.anomaly_detector = AdvancedAnomalyDetector()
        
        # Métriques
        self.metrics = MetricsCollector()
        self.performance_issues = deque(maxlen=1000)
        
        # Configuration
        self.performance_thresholds = {
            'response_time_p95': 1000,  # ms
            'error_rate': 0.01,  # 1%
            'cpu_usage': 80.0,  # %
            'memory_usage': 90.0,  # %
            'database_latency_p95': 500,  # ms
            'gc_pause_time': 100  # ms
        }
        
        # Cache pour les analyses récurrentes
        self.analysis_cache = {}
        self.cache_ttl = 300  # 5 minutes
        
        logger.info(f"PerformanceBottleneckAnalyzer {agent_id} initialisé")
    
    async def execute(self, input_data: Dict[str, Any]) -> AgentResult:
        """
        Exécute l'analyse de performance.
        
        Args:
            input_data: Données d'entrée (traces, métriques, logs)
            
        Returns:
            Résultats de l'analyse
        """
        start_time = time.time()
        
        try:
            # Analyse des différentes sources de données
            analysis_results = await self._analyze_performance_data(input_data)
            
            # Détection des goulets d'étranglement
            bottlenecks = await self._detect_bottlenecks(analysis_results)
            
            # Analyse de l'impact business
            business_impact = await self._assess_business_impact(bottlenecks, input_data)
            
            # Génération du rapport
            report = self._generate_performance_report(
                analysis_results, bottlenecks, business_impact
            )
            
            # Mise à jour des métriques
            self._update_performance_metrics(start_time, bottlenecks)
            
            return AgentResult(
                success=True,
                data=report,
                metadata={
                    'processing_time': time.time() - start_time,
                    'bottlenecks_detected': len(bottlenecks),
                    'critical_issues': len([b for b in bottlenecks if b.severity in [SeverityLevel.CRITICAL, SeverityLevel.HIGH]])
                }
            )
            
        except Exception as e:
            logger.error(f"Erreur analyse performance: {str(e)}")
            return AgentResult(
                success=False,
                error=str(e),
                metadata={'processing_time': time.time() - start_time}
            )
    
    async def _analyze_performance_data(
        self,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Analyse les données de performance.
        
        Args:
            input_data: Données d'entrée
            
        Returns:
            Résultats de l'analyse
        """
        analysis_results = {
            'transaction_analysis': [],
            'resource_analysis': {},
            'query_analysis': [],
            'network_analysis': {},
            'memory_analysis': {},
            'gc_analysis': {},
            'concurrency_analysis': {}
        }
        
        # Analyse des transactions
        if 'traces' in input_data:
            for trace_data in input_data['traces']:
                try:
                    transaction = await self.transaction_tracer.analyze_trace(trace_data)
                    analysis_results['transaction_analysis'].append({
                        'transaction': transaction,
                        'critical_path': transaction.get_critical_path(),
                        'percentiles': transaction.calculate_percentiles()
                    })
                except Exception as e:
                    logger.error(f"Erreur analyse trace: {str(e)}")
        
        # Analyse des ressources
        if 'resource_metrics' in input_data:
            analysis_results['resource_analysis'] = self.resource_analyzer.analyze_resource_metrics(
                input_data['resource_metrics']
            )
            
            # Détection des patterns de contention
            contention_patterns = self.resource_analyzer.detect_resource_contention_patterns()
            analysis_results['resource_analysis']['contention_patterns'] = contention_patterns
        
        # Analyse des requêtes de base de données
        if 'database_queries' in input_data:
            for query_data in input_data['database_queries']:
                try:
                    query_analysis = self.query_analyzer.analyze_query(query_data)
                    analysis_results['query_analysis'].append(query_analysis)
                except Exception as e:
                    logger.error(f"Erreur analyse requête: {str(e)}")
            
            # Top des requêtes lentes
            analysis_results['query_analysis_summary'] = {
                'top_slow_queries': self.query_analyzer.get_top_slow_queries(10),
                'total_queries_analyzed': len(analysis_results['query_analysis'])
            }
        
        # Analyse réseau
        if 'network_metrics' in input_data:
            analysis_results['network_analysis'] = self._analyze_network_performance(
                input_data['network_metrics']
            )
        
        # Analyse mémoire et GC
        if 'memory_metrics' in input_data:
            analysis_results['memory_analysis'] = self._analyze_memory_performance(
                input_data['memory_metrics']
            )
            
            if 'gc_metrics' in input_data['memory_metrics']:
                analysis_results['gc_analysis'] = self._analyze_garbage_collection(
                    input_data['memory_metrics']['gc_metrics']
                )
        
        # Analyse de concurrence
        if 'concurrency_metrics' in input_data:
            analysis_results['concurrency_analysis'] = self._analyze_concurrency_issues(
                input_data['concurrency_metrics']
            )
        
        # Analyse des dépendances
        if analysis_results['transaction_analysis']:
            dependency_analysis = self.transaction_tracer.analyze_dependency_patterns()
            analysis_results['dependency_analysis'] = dependency_analysis
        
        return analysis_results
    
    def _analyze_network_performance(
        self,
        network_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyse les performances réseau"""
        analysis = {
            'latency_analysis': {},
            'throughput_analysis': {},
            'error_analysis': {},
            'bottlenecks': []
        }
        
        # Analyse de latence
        if 'latencies' in network_metrics:
            latencies = network_metrics['latencies']
            if latencies:
                analysis['latency_analysis'] = {
                    'p50': np.percentile(latencies, 50),
                    'p90': np.percentile(latencies, 90),
                    'p95': np.percentile(latencies, 95),
                    'p99': np.percentile(latencies, 99),
                    'max': max(latencies),
                    'min': min(latencies),
                    'mean': np.mean(latencies)
                }
                
                # Détection de latence élevée
                if analysis['latency_analysis']['p95'] > 100:  # ms
                    analysis['bottlenecks'].append({
                        'type': 'network_latency',
                        'severity': SeverityLevel.HIGH,
                        'description': f'Network latency p95: {analysis["latency_analysis"]["p95"]:.1f}ms',
                        'threshold': 100
                    })
        
        # Analyse de débit
        if 'throughput' in network_metrics:
            throughput = network_metrics['throughput']
            analysis['throughput_analysis'] = {
                'current_mbps': throughput.get('current_mbps', 0),
                'max_mbps': throughput.get('max_mbps', 0),
                'utilization_percent': (throughput.get('current_mbps', 0) / max(throughput.get('max_mbps', 1), 1)) * 100
            }
            
            # Détection de saturation
            if analysis['throughput_analysis']['utilization_percent'] > 80:
                analysis['bottlenecks'].append({
                    'type': 'network_saturation',
                    'severity': SeverityLevel.MEDIUM,
                    'description': f'Network utilization: {analysis["throughput_analysis"]["utilization_percent"]:.1f}%',
                    'threshold': 80
                })
        
        # Analyse d'erreurs
        if 'errors' in network_metrics:
            errors = network_metrics['errors']
            total_packets = errors.get('total_packets', 1)
            error_rate = (errors.get('error_packets', 0) / total_packets) * 100
            
            analysis['error_analysis'] = {
                'error_rate_percent': error_rate,
                'total_packets': total_packets,
                'error_packets': errors.get('error_packets', 0)
            }
            
            # Détection d'erreurs élevées
            if error_rate > 1.0:
                analysis['bottlenecks'].append({
                    'type': 'network_errors',
                    'severity': SeverityLevel.HIGH,
                    'description': f'Network error rate: {error_rate:.2f}%',
                    'threshold': 1.0
                })
        
        return analysis
    
    def _analyze_memory_performance(
        self,
        memory_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyse les performances mémoire"""
        analysis = {
            'usage_analysis': {},
            'leak_detection': {},
            'fragmentation_analysis': {},
            'issues': []
        }
        
        # Analyse d'utilisation
        if 'usage' in memory_metrics:
            usage = memory_metrics['usage']
            analysis['usage_analysis'] = {
                'used_percent': usage.get('used_percent', 0),
                'used_mb': usage.get('used_mb', 0),
                'available_mb': usage.get('available_mb', 0),
                'total_mb': usage.get('total_mb', 0)
            }
            
            # Détection d'utilisation élevée
            if usage.get('used_percent', 0) > 90:
                analysis['issues'].append({
                    'type': 'high_memory_usage',
                    'severity': SeverityLevel.HIGH,
                    'description': f'Memory usage: {usage.get("used_percent", 0):.1f}%',
                    'threshold': 90
                })
        
        # Détection de fuites mémoire
        if 'trend' in memory_metrics:
            trend = memory_metrics['trend']
            analysis['leak_detection'] = {
                'trend_slope': trend.get('slope', 0),
                'trend_r_squared': trend.get('r_squared', 0),
                'period_hours': trend.get('period_hours', 24)
            }
            
            # Détection de fuite
            if trend.get('slope', 0) > 0.5 and trend.get('r_squared', 0) > 0.7:
                analysis['issues'].append({
                    'type': 'memory_leak',
                    'severity': SeverityLevel.CRITICAL,
                    'description': f'Memory leak detected (slope: {trend.get("slope", 0):.2f}%/hour)',
                    'confidence': trend.get('r_squared', 0)
                })
        
        # Analyse de fragmentation
        if 'fragmentation' in memory_metrics:
            fragmentation = memory_metrics['fragmentation']
            analysis['fragmentation_analysis'] = {
                'fragmentation_percent': fragmentation.get('fragmentation_percent', 0),
                'free_blocks': fragmentation.get('free_blocks', 0),
                'avg_block_size': fragmentation.get('avg_block_size', 0)
            }
            
            # Détection de fragmentation élevée
            if fragmentation.get('fragmentation_percent', 0) > 30:
                analysis['issues'].append({
                    'type': 'memory_fragmentation',
                    'severity': SeverityLevel.MEDIUM,
                    'description': f'Memory fragmentation: {fragmentation.get("fragmentation_percent", 0):.1f}%',
                    'threshold': 30
                })
        
        return analysis
    
    def _analyze_garbage_collection(
        self,
        gc_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyse le garbage collection"""
        analysis = {
            'collection_analysis': {},
            'pause_analysis': {},
            'throughput_analysis': {},
            'issues': []
        }
        
        # Analyse des collections
        if 'collections' in gc_metrics:
            collections = gc_metrics['collections']
            analysis['collection_analysis'] = {
                'total_collections': collections.get('total', 0),
                'full_collections': collections.get('full', 0),
                'young_collections': collections.get('young', 0),
                'collection_rate_per_minute': collections.get('rate_per_minute', 0)
            }
            
            # Détection de collections fréquentes
            if collections.get('rate_per_minute', 0) > 10:
                analysis['issues'].append({
                    'type': 'frequent_gc',
                    'severity': SeverityLevel.MEDIUM,
                    'description': f'Frequent GC: {collections.get("rate_per_minute", 0):.1f} collections/minute',
                    'threshold': 10
                })
        
        # Analyse des pauses
        if 'pauses' in gc_metrics:
            pauses = gc_metrics['pauses']
            if pauses.get('durations'):
                durations = pauses['durations']
                analysis['pause_analysis'] = {
                    'p50_ms': np.percentile(durations, 50),
                    'p90_ms': np.percentile(durations, 90),
                    'p95_ms': np.percentile(durations, 95),
                    'p99_ms': np.percentile(durations, 99),
                    'max_ms': max(durations),
                    'total_pause_time_ms': sum(durations)
                }
                
                # Détection de pauses longues
                if analysis['pause_analysis']['p95_ms'] > 100:
                    analysis['issues'].append({
                        'type': 'long_gc_pauses',
                        'severity': SeverityLevel.HIGH,
                        'description': f'Long GC pauses p95: {analysis["pause_analysis"]["p95_ms"]:.1f}ms',
                        'threshold': 100
                    })
        
        # Analyse de débit
        if 'throughput' in gc_metrics:
            throughput = gc_metrics['throughput']
            analysis['throughput_analysis'] = {
                'gc_time_percent': throughput.get('gc_time_percent', 0),
                'application_time_percent': throughput.get('application_time_percent', 0),
                'throughput_percent': 100 - throughput.get('gc_time_percent', 0)
            }
            
            # Détection de faible débit
            if throughput.get('gc_time_percent', 0) > 10:
                analysis['issues'].append({
                    'type': 'low_gc_throughput',
                    'severity': SeverityLevel.MEDIUM,
                    'description': f'GC overhead: {throughput.get("gc_time_percent", 0):.1f}%',
                    'threshold': 10
                })
        
        return analysis
    
    def _analyze_concurrency_issues(
        self,
        concurrency_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyse les problèmes de concurrence"""
        analysis = {
            'lock_analysis': {},
            'thread_analysis': {},
            'deadlock_detection': {},
            'contention_analysis': {},
            'issues': []
        }
        
        # Analyse des verrous
        if 'locks' in concurrency_metrics:
            locks = concurrency_metrics['locks']
            analysis['lock_analysis'] = {
                'total_locks': locks.get('total', 0),
                'waiting_locks': locks.get('waiting', 0),
                'held_locks': locks.get('held', 0),
                'wait_time_avg_ms': locks.get('avg_wait_time_ms', 0),
                'contention_rate': (locks.get('waiting', 0) / max(locks.get('total', 1), 1)) * 100
            }
            
            # Détection de contention de verrous
            if analysis['lock_analysis']['contention_rate'] > 10:
                analysis['issues'].append({
                    'type': 'lock_contention',
                    'severity': SeverityLevel.HIGH,
                    'description': f'Lock contention: {analysis["lock_analysis"]["contention_rate"]:.1f}%',
                    'threshold': 10
                })
        
        # Analyse des threads
        if 'threads' in concurrency_metrics:
            threads = concurrency_metrics['threads']
            analysis['thread_analysis'] = {
                'total_threads': threads.get('total', 0),
                'active_threads': threads.get('active', 0),
                'blocked_threads': threads.get('blocked', 0),
                'waiting_threads': threads.get('waiting', 0),
                'thread_creation_rate': threads.get('creation_rate', 0)
            }
            
            # Détection de threads bloqués
            if threads.get('blocked', 0) > (threads.get('total', 0) * 0.3):
                analysis['issues'].append({
                    'type': 'thread_blocking',
                    'severity': SeverityLevel.HIGH,
                    'description': f'High thread blocking: {threads.get("blocked", 0)}/{threads.get("total", 0)} threads',
                    'threshold': 0.3
                })
        
        # Détection de deadlocks
        if 'deadlocks' in concurrency_metrics:
            deadlocks = concurrency_metrics['deadlocks']
            analysis['deadlock_detection'] = {
                'detected': deadlocks.get('detected', False),
                'deadlock_count': deadlocks.get('count', 0),
                'involved_threads': deadlocks.get('involved_threads', []),
                'involved_resources': deadlocks.get('involved_resources', [])
            }
            
            if deadlocks.get('detected', False):
                analysis['issues'].append({
                    'type': 'deadlock_detected',
                    'severity': SeverityLevel.CRITICAL,
                    'description': f'Deadlock detected involving {len(deadlocks.get("involved_threads", []))} threads',
                    'threads': deadlocks.get('involved_threads', [])
                })
        
        # Analyse de contention
        if 'contention' in concurrency_metrics:
            contention = concurrency_metrics['contention']
            analysis['contention_analysis'] = {
                'hotspots': contention.get('hotspots', []),
                'contention_score': contention.get('score', 0),
                'resources_contended': len(contention.get('hotspots', []))
            }
            
            if contention.get('score', 0) > 50:
                analysis['issues'].append({
                    'type': 'high_contention',
                    'severity': SeverityLevel.MEDIUM,
                    'description': f'High resource contention score: {contention.get("score", 0)}',
                    'hotspots': contention.get('hotspots', [])[:5]
                })
        
        return analysis
    
    async def _detect_bottlenecks(
        self,
        analysis_results: Dict[str, Any]
    ) -> List[PerformanceIssue]:
        """
        Détecte les goulets d'étranglement.
        
        Args:
            analysis_results: Résultats de l'analyse
            
        Returns:
            Problèmes de performance détectés
        """
        bottlenecks = []
        
        # Détection basée sur les transactions
        for tx_analysis in analysis_results.get('transaction_analysis', []):
            transaction = tx_analysis.get('transaction')
            if not transaction:
                continue
            
            # Transactions lentes
            if transaction.duration_ms > self.performance_thresholds['response_time_p95']:
                bottlenecks.append(
                    self._create_transaction_bottleneck(transaction, tx_analysis)
                )
            
            # Analyse du chemin critique
            critical_path = tx_analysis.get('critical_path', [])
            if critical_path:
                for span in critical_path[:2]:  # Top 2 spans les plus longs
                    bottleneck = self._analyze_critical_span(span, transaction)
                    if bottleneck:
                        bottlenecks.append(bottleneck)
        
        # Détection basée sur les ressources
        resource_analysis = analysis_results.get('resource_analysis', {})
        resource_bottlenecks = self._detect_resource_bottlenecks(resource_analysis)
        bottlenecks.extend(resource_bottlenecks)
        
        # Détection basée sur les requêtes de base de données
        for query_analysis in analysis_results.get('query_analysis', []):
            query_bottlenecks = self._detect_query_bottlenecks(query_analysis)
            bottlenecks.extend(query_bottlenecks)
        
        # Détection basée sur l'analyse réseau
        network_analysis = analysis_results.get('network_analysis', {})
        network_bottlenecks = self._detect_network_bottlenecks(network_analysis)
        bottlenecks.extend(network_bottlenecks)
        
        # Détection basée sur l'analyse mémoire
        memory_analysis = analysis_results.get('memory_analysis', {})
        memory_bottlenecks = self._detect_memory_bottlenecks(memory_analysis)
        bottlenecks.extend(memory_bottlenecks)
        
        # Détection basée sur l'analyse GC
        gc_analysis = analysis_results.get('gc_analysis', {})
        gc_bottlenecks = self._detect_gc_bottlenecks(gc_analysis)
        bottlenecks.extend(gc_bottlenecks)
        
        # Détection basée sur l'analyse de concurrence
        concurrency_analysis = analysis_results.get('concurrency_analysis', {})
        concurrency_bottlenecks = self._detect_concurrency_bottlenecks(concurrency_analysis)
        bottlenecks.extend(concurrency_bottlenecks)
        
        # Élimination des doublons
        unique_bottlenecks = self._deduplicate_bottlenecks(bottlenecks)
        
        # Calcul des scores d'impact
        for bottleneck in unique_bottlenecks:
            bottleneck.impact_score = self._calculate_impact_score(bottleneck)
        
        # Tri par sévérité et impact
        unique_bottlenecks.sort(
            key=lambda x: (
                0 if x.severity == SeverityLevel.CRITICAL else
                1 if x.severity == SeverityLevel.HIGH else
                2 if x.severity == SeverityLevel.MEDIUM else
                3 if x.severity == SeverityLevel.LOW else 4,
                -x.impact_score
            )
        )
        
        return unique_bottlenecks
    
    def _create_transaction_bottleneck(
        self,
        transaction: TransactionTrace,
        tx_analysis: Dict[str, Any]
    ) -> PerformanceIssue:
        """Crée un problème de performance pour une transaction lente"""
        issue_id = f"TX_{transaction.trace_id}"
        
        # Identification du type de bottleneck
        bottleneck_type = self._identify_transaction_bottleneck_type(transaction, tx_analysis)
        
        # Calcul de la sévérité
        severity = self._determine_transaction_severity(transaction)
        
        # Génération des recommandations
        recommendations = self._generate_transaction_recommendations(transaction, tx_analysis)
        
        return PerformanceIssue(
            issue_id=issue_id,
            timestamp=transaction.end_time,
            bottleneck_type=bottleneck_type,
            severity=severity,
            component=transaction.service_name,
            description=f"Slow transaction: {transaction.operation_name} took {transaction.duration_ms:.1f}ms",
            impact_score=0.0,  # Calculé plus tard
            confidence=0.85,
            metrics={
                'duration_ms': transaction.duration_ms,
                'threshold_ms': self.performance_thresholds['response_time_p95']
            },
            root_cause=self._identify_transaction_root_cause(tx_analysis),
            recommendations=recommendations,
            related_traces=[transaction.trace_id]
        )
    
    def _identify_transaction_bottleneck_type(
        self,
        transaction: TransactionTrace,
        tx_analysis: Dict[str, Any]
    ) -> BottleneckType:
        """Identifie le type de bottleneck pour une transaction"""
        critical_path = tx_analysis.get('critical_path', [])
        
        if not critical_path:
            return BottleneckType.CPU_BOUND
        
        # Analyse du span le plus long
        longest_span = critical_path[0]
        span_attributes = longest_span.get('attributes', {})
        
        # Vérification des métriques du span
        if 'db.statement' in span_attributes:
            return BottleneckType.DATABASE_BOUND
        elif 'http.url' in span_attributes:
            return BottleneckType.NETWORK_BOUND
        elif longest_span.get('metrics', {}).get('cpu_time_ms', 0) > longest_span.get('duration_ms', 0) * 0.8:
            return BottleneckType.CPU_BOUND
        elif longest_span.get('metrics', {}).get('memory_usage_mb', 0) > 100:
            return BottleneckType.MEMORY_BOUND
        elif longest_span.get('metrics', {}).get('io_operations', 0) > 100:
            return BottleneckType.I_O_BOUND
        
        return BottleneckType.CPU_BOUND
    
    def _determine_transaction_severity(
        self,
        transaction: TransactionTrace
    ) -> SeverityLevel:
        """Détermine la sévérité d'une transaction lente"""
        duration = transaction.duration_ms
        threshold = self.performance_thresholds['response_time_p95']
        
        if duration > threshold * 5:
            return SeverityLevel.CRITICAL
        elif duration > threshold * 3:
            return SeverityLevel.HIGH
        elif duration > threshold * 2:
            return SeverityLevel.MEDIUM
        elif duration > threshold:
            return SeverityLevel.LOW
        else:
            return SeverityLevel.INFO
    
    def _generate_transaction_recommendations(
        self,
        transaction: TransactionTrace,
        tx_analysis: Dict[str, Any]
    ) -> List[str]:
        """Génère des recommandations pour une transaction"""
        recommendations = []
        critical_path = tx_analysis.get('critical_path', [])
        
        if not critical_path:
            return ["Profile the transaction to identify bottlenecks"]
        
        # Recommandations basées sur le chemin critique
        for i, span in enumerate(critical_path[:2]):
            span_name = span.get('name', 'unknown')
            span_duration = span.get('duration_ms', 0)
            
            recommendations.append(
                f"Optimize span '{span_name}' (took {span_duration:.1f}ms, {span_duration/transaction.duration_ms*100:.1f}% of total)"
            )
        
        # Recommandations spécifiques
        if transaction.status_code != '200':
            recommendations.append(f"Fix error in transaction: status {transaction.status_code}")
        
        if len(critical_path) > 10:
            recommendations.append("Consider reducing transaction complexity")
        
        return recommendations
    
    def _identify_transaction_root_cause(
        self,
        tx_analysis: Dict[str, Any]
    ) -> Optional[str]:
        """Identifie la cause racine d'une transaction"""
        critical_path = tx_analysis.get('critical_path', [])
        
        if not critical_path:
            return None
        
        longest_span = critical_path[0]
        span_name = longest_span.get('name', 'unknown')
        span_attributes = longest_span.get('attributes', {})
        
        if 'db.statement' in span_attributes:
            return f"Database query in span '{span_name}'"
        elif 'http.url' in span_attributes:
            return f"HTTP call to {span_attributes.get('http.url')}"
        elif 'rpc.method' in span_attributes:
            return f"RPC call: {span_attributes.get('rpc.method')}"
        
        return f"Span '{span_name}'"
    
    def _analyze_critical_span(
        self,
        span: Dict[str, Any],
        transaction: TransactionTrace
    ) -> Optional[PerformanceIssue]:
        """Analyse un span critique"""
        span_duration = span.get('duration_ms', 0)
        
        # Seuil pour les spans critiques (20% du temps total)
        if span_duration < transaction.duration_ms * 0.2:
            return None
        
        issue_id = f"SPAN_{span.get('span_id', 'unknown')}"
        
        # Identification du type de bottleneck
        bottleneck_type = self._identify_span_bottleneck_type(span)
        
        # Calcul de la sévérité
        severity = self._determine_span_severity(span, transaction)
        
        return PerformanceIssue(
            issue_id=issue_id,
            timestamp=transaction.end_time,
            bottleneck_type=bottleneck_type,
            severity=severity,
            component=span.get('attributes', {}).get('service.name', 'unknown'),
            description=f"Critical span '{span.get('name', 'unknown')}' took {span_duration:.1f}ms ({span_duration/transaction.duration_ms*100:.1f}% of total)",
            impact_score=span_duration / transaction.duration_ms,
            confidence=0.8,
            metrics={
                'span_duration_ms': span_duration,
                'transaction_duration_ms': transaction.duration_ms,
                'percentage_of_total': (span_duration / transaction.duration_ms) * 100
            },
            related_traces=[transaction.trace_id],
            recommendations=[f"Optimize span '{span.get('name', 'unknown')}'"]
        )
    
    def _identify_span_bottleneck_type(self, span: Dict[str, Any]) -> BottleneckType:
        """Identifie le type de bottleneck pour un span"""
        span_attributes = span.get('attributes', {})
        span_metrics = span.get('metrics', {})
        
        if 'db.statement' in span_attributes:
            return BottleneckType.DATABASE_BOUND
        elif 'http.url' in span_attributes:
            return BottleneckType.NETWORK_BOUND
        elif span_metrics.get('cpu_time_ms', 0) > span.get('duration_ms', 0) * 0.8:
            return BottleneckType.CPU_BOUND
        elif span_metrics.get('memory_usage_mb', 0) > 100:
            return BottleneckType.MEMORY_BOUND
        elif span_metrics.get('io_operations', 0) > 100:
            return BottleneckType.I_O_BOUND
        
        return BottleneckType.CPU_BOUND
    
    def _determine_span_severity(
        self,
        span: Dict[str, Any],
        transaction: TransactionTrace
    ) -> SeverityLevel:
        """Détermine la sévérité d'un span"""
        span_percentage = span.get('duration_ms', 0) / max(transaction.duration_ms, 1)
        
        if span_percentage > 0.5:
            return SeverityLevel.HIGH
        elif span_percentage > 0.3:
            return SeverityLevel.MEDIUM
        elif span_percentage > 0.2:
            return SeverityLevel.LOW
        else:
            return SeverityLevel.INFO
    
    def _detect_resource_bottlenecks(
        self,
        resource_analysis: Dict[str, Any]
    ) -> List[PerformanceIssue]:
        """Détecte les goulets d'étranglement liés aux ressources"""
        bottlenecks = []
        
        # Analyse CPU
        cpu_analysis = resource_analysis.get('cpu_analysis', {})
        if cpu_analysis.get('saturated', False):
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"CPU_{int(time.time())}",
                    timestamp=datetime.utcnow(),
                    bottleneck_type=BottleneckType.CPU_BOUND,
                    severity=SeverityLevel.HIGH,
                    component="System",
                    description=f"CPU saturated: {cpu_analysis.get('usage_percent', 0):.1f}% usage",
                    impact_score=0.7,
                    confidence=0.9,
                    metrics=cpu_analysis,
                    recommendations=[
                        "Scale up CPU resources",
                        "Optimize CPU-intensive operations",
                        "Implement auto-scaling"
                    ]
                )
            )
        
        # Analyse mémoire
        memory_analysis = resource_analysis.get('memory_analysis', {})
        if memory_analysis.get('under_pressure', False):
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"MEM_{int(time.time())}",
                    timestamp=datetime.utcnow(),
                    bottleneck_type=BottleneckType.MEMORY_BOUND,
                    severity=SeverityLevel.HIGH,
                    component="System",
                    description=f"Memory under pressure: {memory_analysis.get('used_percent', 0):.1f}% used",
                    impact_score=0.8,
                    confidence=0.85,
                    metrics=memory_analysis,
                    recommendations=[
                        "Increase memory allocation",
                        "Optimize memory usage",
                        "Implement caching strategies"
                    ]
                )
            )
        
        # Contention de ressources
        contention_patterns = resource_analysis.get('contention_patterns', [])
        for pattern in contention_patterns:
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"CONT_{int(time.time())}_{len(bottlenecks)}",
                    timestamp=datetime.utcnow(),
                    bottleneck_type=BottleneckType.RESOURCE_EXHAUSTION,
                    severity=pattern.get('severity', SeverityLevel.MEDIUM),
                    component="System",
                    description=pattern.get('description', 'Resource contention detected'),
                    impact_score=0.6,
                    confidence=0.75,
                    metrics={},
                    recommendations=[
                        "Monitor resource usage trends",
                        "Implement resource quotas",
                        "Optimize resource allocation"
                    ]
                )
            )
        
        return bottlenecks
    
    def _detect_query_bottlenecks(
        self,
        query_analysis: Dict[str, Any]
    ) -> List[PerformanceIssue]:
        """Détecte les goulets d'étranglement liés aux requêtes"""
        bottlenecks = []
        
        issues = query_analysis.get('issues', [])
        for issue in issues:
            bottleneck_type = BottleneckType.DATABASE_BOUND
            severity = issue.get('severity', SeverityLevel.MEDIUM)
            
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"QUERY_{query_analysis.get('query_hash', 'unknown')}",
                    timestamp=query_analysis.get('timestamp', datetime.utcnow()),
                    bottleneck_type=bottleneck_type,
                    severity=severity,
                    component="Database",
                    description=issue.get('description', 'Database query issue'),
                    impact_score=0.5,
                    confidence=0.8,
                    metrics={
                        'execution_time_ms': query_analysis.get('execution_time', 0),
                        'query_type': query_analysis.get('structural_analysis', {}).get('complexity_score', 0)
                    },
                    root_cause=issue.get('type'),
                    recommendations=self.query_analyzer.generate_query_recommendations(
                        query_analysis.get('query_hash', '')
                    )
                )
            )
        
        return bottlenecks
    
    def _detect_network_bottlenecks(
        self,
        network_analysis: Dict[str, Any]
    ) -> List[PerformanceIssue]:
        """Détecte les goulets d'étranglement réseau"""
        bottlenecks = []
        
        network_bottlenecks = network_analysis.get('bottlenecks', [])
        for bottleneck in network_bottlenecks:
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"NET_{int(time.time())}_{len(bottlenecks)}",
                    timestamp=datetime.utcnow(),
                    bottleneck_type=BottleneckType.NETWORK_BOUND,
                    severity=bottleneck.get('severity', SeverityLevel.MEDIUM),
                    component="Network",
                    description=bottleneck.get('description', 'Network bottleneck'),
                    impact_score=0.6,
                    confidence=0.8,
                    metrics=network_analysis.get('latency_analysis', {}),
                    recommendations=[
                        "Optimize network configuration",
                        "Implement CDN",
                        "Use connection pooling"
                    ]
                )
            )
        
        return bottlenecks
    
    def _detect_memory_bottlenecks(
        self,
        memory_analysis: Dict[str, Any]
    ) -> List[PerformanceIssue]:
        """Détecte les goulets d'étranglement mémoire"""
        bottlenecks = []
        
        memory_issues = memory_analysis.get('issues', [])
        for issue in memory_issues:
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"MEM_{issue.get('type', 'unknown')}_{int(time.time())}",
                    timestamp=datetime.utcnow(),
                    bottleneck_type=BottleneckType.MEMORY_BOUND,
                    severity=issue.get('severity', SeverityLevel.MEDIUM),
                    component="Memory",
                    description=issue.get('description', 'Memory issue'),
                    impact_score=0.7,
                    confidence=issue.get('confidence', 0.7),
                    metrics=memory_analysis.get('usage_analysis', {}),
                    recommendations=[
                        "Profile memory usage",
                        "Implement memory pooling",
                        "Optimize data structures"
                    ]
                )
            )
        
        return bottlenecks
    
    def _detect_gc_bottlenecks(
        self,
        gc_analysis: Dict[str, Any]
    ) -> List[PerformanceIssue]:
        """Détecte les goulets d'étranglement GC"""
        bottlenecks = []
        
        gc_issues = gc_analysis.get('issues', [])
        for issue in gc_issues:
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"GC_{issue.get('type', 'unknown')}_{int(time.time())}",
                    timestamp=datetime.utcnow(),
                    bottleneck_type=BottleneckType.GARBAGE_COLLECTION,
                    severity=issue.get('severity', SeverityLevel.MEDIUM),
                    component="Garbage Collector",
                    description=issue.get('description', 'GC issue'),
                    impact_score=0.5,
                    confidence=0.75,
                    metrics=gc_analysis.get('pause_analysis', {}),
                    recommendations=[
                        "Tune GC parameters",
                        "Reduce object allocations",
                        "Implement object pooling"
                    ]
                )
            )
        
        return bottlenecks
    
    def _detect_concurrency_bottlenecks(
        self,
        concurrency_analysis: Dict[str, Any]
    ) -> List[PerformanceIssue]:
        """Détecte les goulets d'étranglement de concurrence"""
        bottlenecks = []
        
        concurrency_issues = concurrency_analysis.get('issues', [])
        for issue in concurrency_issues:
            bottleneck_type = (
                BottleneckType.LOCK_CONTENTION 
                if issue.get('type') == 'lock_contention' 
                else BottleneckType.CONCURRENCY
            )
            
            bottlenecks.append(
                PerformanceIssue(
                    issue_id=f"CONC_{issue.get('type', 'unknown')}_{int(time.time())}",
                    timestamp=datetime.utcnow(),
                    bottleneck_type=bottleneck_type,
                    severity=issue.get('severity', SeverityLevel.MEDIUM),
                    component="Concurrency",
                    description=issue.get('description', 'Concurrency issue'),
                    impact_score=0.6,
                    confidence=0.8,
                    metrics=concurrency_analysis.get('lock_analysis', {}),
                    recommendations=[
                        "Implement lock-free algorithms",
                        "Reduce lock granularity",
                        "Use concurrent data structures"
                    ]
                )
            )
        
        return bottlenecks
    
    def _deduplicate_bottlenecks(
        self,
        bottlenecks: List[PerformanceIssue]
    ) -> List[PerformanceIssue]:
        """Élimine les goulets d'étranglement en double"""
        unique_bottlenecks = []
        seen_descriptions = set()
        
        for bottleneck in bottlenecks:
            # Création d'une clé unique basée sur la description et le composant
            key = f"{bottleneck.component}:{bottleneck.description[:100]}"
            
            if key not in seen_descriptions:
                seen_descriptions.add(key)
                unique_bottlenecks.append(bottleneck)
            else:
                # Fusion des métriques pour les doublons
                for existing in unique_bottlenecks:
                    if (existing.component == bottleneck.component and 
                        existing.description[:100] == bottleneck.description[:100]):
                        # Mise à jour de la confiance et des métriques
                        existing.confidence = max(existing.confidence, bottleneck.confidence)
                        existing.metrics.update(bottleneck.metrics)
                        break
        
        return unique_bottlenecks
    
    def _calculate_impact_score(self, bottleneck: PerformanceIssue) -> float:
        """Calcule le score d'impact d'un goulet d'étranglement"""
        base_score = 0.0
        
        # Score basé sur la sévérité
        severity_scores = {
            SeverityLevel.CRITICAL: 1.0,
            SeverityLevel.HIGH: 0.8,
            SeverityLevel.MEDIUM: 0.5,
            SeverityLevel.LOW: 0.2,
            SeverityLevel.INFO: 0.0
        }
        base_score += severity_scores.get(bottleneck.severity, 0.0) * 0.4
        
        # Score basé sur la confiance
        base_score += bottleneck.confidence * 0.3
        
        # Score basé sur les métriques
        metrics_score = self._calculate_metrics_score(bottleneck.metrics)
        base_score += metrics_score * 0.3
        
        return min(base_score, 1.0)
    
    def _calculate_metrics_score(self, metrics: Dict[str, Any]) -> float:
        """Calcule un score basé sur les métriques"""
        if not metrics:
            return 0.0
        
        score = 0.0
        
        # Score pour la durée
        if 'duration_ms' in metrics:
            duration = metrics['duration_ms']
            threshold = self.performance_thresholds.get('response_time_p95', 1000)
            duration_ratio = min(duration / max(threshold, 1), 5.0)  # Cap à 5x
            score += duration_ratio * 0.3
        
        # Score pour l'utilisation CPU
        if 'usage_percent' in metrics:
            cpu_usage = metrics['usage_percent']
            cpu_ratio = min(cpu_usage / 100, 1.0)
            score += cpu_ratio * 0.3
        
        # Score pour l'utilisation mémoire
        if 'used_percent' in metrics:
            mem_usage = metrics['used_percent']
            mem_ratio = min(mem_usage / 100, 1.0)
            score += mem_ratio * 0.2
        
        # Score pour les erreurs
        if 'error_rate' in metrics:
            error_rate = metrics['error_rate']
            error_ratio = min(error_rate / 0.1, 1.0)  # 10% d'erreur = score max
            score += error_ratio * 0.2
        
        return min(score, 1.0)
    
    async def _assess_business_impact(
        self,
        bottlenecks: List[PerformanceIssue],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Évalue l'impact business des goulets d'étranglement.
        
        Args:
            bottlenecks: Goulets d'étranglement détectés
            input_data: Données d'entrée
            
        Returns:
            Évaluation de l'impact business
        """
        if not bottlenecks:
            return {
                'overall_impact': 'none',
                'affected_users': 0,
                'revenue_impact': 0.0,
                'sla_violations': 0
            }
        
        # Calcul des métriques d'impact
        critical_bottlenecks = [
            b for b in bottlenecks 
            if b.severity in [SeverityLevel.CRITICAL, SeverityLevel.HIGH]
        ]
        
        # Estimation des utilisateurs affectés
        affected_users = self._estimate_affected_users(bottlenecks, input_data)
        
        # Estimation de l'impact sur le revenu
        revenue_impact = self._estimate_revenue_impact(bottlenecks, input_data)
        
        # Détection des violations SLA
        sla_violations = self._detect_sla_violations(bottlenecks, input_data)
        
        # Impact global
        overall_impact = self._determine_overall_impact(
            critical_bottlenecks, affected_users, revenue_impact, sla_violations
        )
        
        return {
            'overall_impact': overall_impact,
            'affected_users': affected_users,
            'revenue_impact': revenue_impact,
            'sla_violations': sla_violations,
            'critical_bottlenecks_count': len(critical_bottlenecks),
            'bottleneck_distribution': {
                'cpu': len([b for b in bottlenecks if b.bottleneck_type == BottleneckType.CPU_BOUND]),
                'memory': len([b for b in bottlenecks if b.bottleneck_type == BottleneckType.MEMORY_BOUND]),
                'database': len([b for b in bottlenecks if b.bottleneck_type == BottleneckType.DATABASE_BOUND]),
                'network': len([b for b in bottlenecks if b.bottleneck_type == BottleneckType.NETWORK_BOUND]),
                'io': len([b for b in bottlenecks if b.bottleneck_type == BottleneckType.I_O_BOUND])
            }
        }
    
    def _estimate_affected_users(
        self,
        bottlenecks: List[PerformanceIssue],
        input_data: Dict[str, Any]
    ) -> int:
        """Estime le nombre d'utilisateurs affectés"""
        if 'user_metrics' not in input_data:
            return 0
        
        user_metrics = input_data['user_metrics']
        active_users = user_metrics.get('active_users', 0)
        
        if not active_users:
            return 0
        
        # Pourcentage estimé d'utilisateurs affectés basé sur la sévérité
        severity_impact = {
            SeverityLevel.CRITICAL: 0.8,
            SeverityLevel.HIGH: 0.5,
            SeverityLevel.MEDIUM: 0.2,
            SeverityLevel.LOW: 0.1,
            SeverityLevel.INFO: 0.0
        }
        
        total_impact = 0.0
        for bottleneck in bottlenecks:
            impact = severity_impact.get(bottleneck.severity, 0.0)
            total_impact += impact
        
        # Normalisation
        avg_impact = total_impact / max(len(bottlenecks), 1)
        
        return int(active_users * avg_impact)
    
    def _estimate_revenue_impact(
        self,
        bottlenecks: List[PerformanceIssue],
        input_data: Dict[str, Any]
    ) -> float:
        """Estime l'impact sur le revenu"""
        if 'business_metrics' not in input_data:
            return 0.0
        
        business_metrics = input_data['business_metrics']
        daily_revenue = business_metrics.get('daily_revenue', 0)
        
        if not daily_revenue:
            return 0.0
        
        # Impact basé sur la sévérité et la durée
        total_impact = 0.0
        for bottleneck in bottlenecks:
            severity_factor = {
                SeverityLevel.CRITICAL: 0.05,  # 5% du revenu journalier
                SeverityLevel.HIGH: 0.02,      # 2%
                SeverityLevel.MEDIUM: 0.01,    # 1%
                SeverityLevel.LOW: 0.005,      # 0.5%
                SeverityLevel.INFO: 0.0
            }.get(bottleneck.severity, 0.0)
            
            # Ajustement basé sur la durée
            duration = bottleneck.metrics.get('duration_ms', 0)
            duration_factor = min(duration / (60 * 1000), 1.0)  # Normalisé à 1 minute
            
            total_impact += daily_revenue * severity_factor * duration_factor
        
        return total_impact
    
    def _detect_sla_violations(
        self,
        bottlenecks: List[PerformanceIssue],
        input_data: Dict[str, Any]
    ) -> int:
        """Détecte les violations SLA"""
        if 'sla_metrics' not in input_data:
            return 0
        
        sla_metrics = input_data['sla_metrics']
        response_time_sla = sla_metrics.get('response_time_ms', 1000)
        availability_sla = sla_metrics.get('availability_percent', 99.9)
        
        violations = 0
        
        for bottleneck in bottlenecks:
            # Vérification du temps de réponse
            if bottleneck.metrics.get('duration_ms', 0) > response_time_sla:
                violations += 1
            
            # Vérification de la disponibilité
            if (bottleneck.severity == SeverityLevel.CRITICAL and 
                bottleneck.bottleneck_type in [BottleneckType.RESOURCE_EXHAUSTION, BottleneckType.CASCADE_FAILURE]):
                violations += 1
        
        return violations
    
    def _determine_overall_impact(
        self,
        critical_bottlenecks: List[PerformanceIssue],
        affected_users: int,
        revenue_impact: float,
        sla_violations: int
    ) -> str:
        """Détermine l'impact global"""
        if not critical_bottlenecks:
            return 'low'
        
        score = 0
        
        # Score basé sur le nombre de goulets d'étranglement critiques
        score += len(critical_bottlenecks) * 2
        
        # Score basé sur les utilisateurs affectés
        if affected_users > 1000:
            score += 3
        elif affected_users > 100:
            score += 2
        elif affected_users > 10:
            score += 1
        
        # Score basé sur l'impact revenu
        if revenue_impact > 10000:
            score += 3
        elif revenue_impact > 1000:
            score += 2
        elif revenue_impact > 100:
            score += 1
        
        # Score basé sur les violations SLA
        score += sla_violations
        
        # Détermination du niveau d'impact
        if score >= 8:
            return 'critical'
        elif score >= 5:
            return 'high'
        elif score >= 3:
            return 'medium'
        else:
            return 'low'
    
    def _generate_performance_report(
        self,
        analysis_results: Dict[str, Any],
        bottlenecks: List[PerformanceIssue],
        business_impact: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Génère un rapport de performance"""
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'summary': {
                'total_bottlenecks': len(bottlenecks),
                'critical_bottlenecks': len([b for b in bottlenecks if b.severity == SeverityLevel.CRITICAL]),
                'high_bottlenecks': len([b for b in bottlenecks if b.severity == SeverityLevel.HIGH]),
                'medium_bottlenecks': len([b for b in bottlenecks if b.severity == SeverityLevel.MEDIUM]),
                'low_bottlenecks': len([b for b in bottlenecks if b.severity == SeverityLevel.LOW])
            },
            'bottlenecks': [b.to_dict() for b in bottlenecks],
            'business_impact': business_impact,
            'analysis_highlights': {
                'slowest_transaction': self._get_slowest_transaction(analysis_results),
                'top_slow_queries': analysis_results.get('query_analysis_summary', {}).get('top_slow_queries', []),
                'resource_contention': analysis_results.get('resource_analysis', {}).get('contention_patterns', []),
                'critical_services': analysis_results.get('dependency_analysis', {}).get('critical_services', [])
            },
            'recommendations': self._generate_overall_recommendations(bottlenecks, analysis_results),
            'metadata': {
                'agent_id': self.agent_id,
                'analysis_duration_ms': analysis_results.get('analysis_duration_ms', 0),
                'data_sources_analyzed': list(analysis_results.keys())
            }
        }
    
    def _get_slowest_transaction(
        self,
        analysis_results: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Récupère la transaction la plus lente"""
        transactions = analysis_results.get('transaction_analysis', [])
        if not transactions:
            return None
        
        slowest = max(
            transactions,
            key=lambda x: x.get('transaction', TransactionTrace('', datetime.utcnow(), datetime.utcnow(), 0, '', '', '', [])).duration_ms
        )
        
        transaction = slowest.get('transaction')
        if not transaction:
            return None
        
        return {
            'trace_id': transaction.trace_id,
            'duration_ms': transaction.duration_ms,
            'service': transaction.service_name,
            'operation': transaction.operation_name,
            'critical_path': slowest.get('critical_path', [])[:3]
        }
    
    def _generate_overall_recommendations(
        self,
        bottlenecks: List[PerformanceIssue],
        analysis_results: Dict[str, Any]
    ) -> List[str]:
        """Génère des recommandations globales"""
        recommendations = []
        
        # Recommandations basées sur les goulets d'étranglement
        bottleneck_types = {b.bottleneck_type for b in bottlenecks}
        
        if BottleneckType.DATABASE_BOUND in bottleneck_types:
            recommendations.append("Optimize database queries and add indexes")
        
        if BottleneckType.CPU_BOUND in bottleneck_types:
            recommendations.append("Scale CPU resources or optimize CPU-intensive operations")
        
        if BottleneckType.MEMORY_BOUND in bottleneck_types:
            recommendations.append("Increase memory allocation or optimize memory usage")
        
        if BottleneckType.NETWORK_BOUND in bottleneck_types:
            recommendations.append("Optimize network calls and implement caching")
        
        if BottleneckType.LOCK_CONTENTION in bottleneck_types:
            recommendations.append("Reduce lock contention through better concurrency design")
        
        # Recommandations basées sur l'analyse des dépendances
        critical_services = analysis_results.get('dependency_analysis', {}).get('critical_services', [])
        if critical_services:
            top_critical = critical_services[:3]
            recommendations.append(
                f"Focus optimization on critical services: {', '.join([s['service'] for s in top_critical])}"
            )
        
        # Recommandations basées sur les requêtes
        top_queries = analysis_results.get('query_analysis_summary', {}).get('top_slow_queries', [])
        if top_queries:
            recommendations.append(f"Optimize top {len(top_queries)} slowest database queries")
        
        return list(set(recommendations))  # Élimine les doublons
    
    def _update_performance_metrics(
        self,
        start_time: float,
        bottlenecks: List[PerformanceIssue]
    ) -> None:
        """Met à jour les métriques de performance"""
        processing_time = time.time() - start_time
        
        # Enregistrement des métriques
        self.metrics.record_metric(
            'performance.analysis.processing_time_ms',
            processing_time * 1000,
            tags={'agent_id': self.agent_id}
        )
        
        self.metrics.record_metric(
            'performance.bottlenecks.detected',
            len(bottlenecks),
            tags={'agent_id': self.agent_id}
        )
        
        self.metrics.record_metric(
            'performance.bottlenecks.critical',
            len([b for b in bottlenecks if b.severity == SeverityLevel.CRITICAL]),
            tags={'agent_id': self.agent_id}
        )
        
        # Mise à jour du cache des problèmes
        for bottleneck in bottlenecks:
            if bottleneck.severity in [SeverityLevel.CRITICAL, SeverityLevel.HIGH]:
                self.performance_issues.append(bottleneck)
    
    async def generate_performance_forecast(
        self,
        historical_data: Dict[str, Any],
        forecast_horizon: int = 24  # heures
    ) -> Dict[str, Any]:
        """
        Génère une prévision de performance.
        
        Args:
            historical_data: Données historiques
            forecast_horizon: Horizon de prévision en heures
            
        Returns:
            Prévisions de performance
        """
        try:
            forecast = {
                'generated_at': datetime.utcnow().isoformat(),
                'forecast_horizon_hours': forecast_horizon,
                'predictions': {},
                'confidence_intervals': {},
                'trends': {},
                'risk_assessment': {}
            }
            
            # Prévision de charge
            if 'load_metrics' in historical_data:
                load_forecast = await self._forecast_load(
                    historical_data['load_metrics'],
                    forecast_horizon
                )
                forecast['predictions']['load'] = load_forecast
            
            # Prévision de ressources
            if 'resource_metrics' in historical_data:
                resource_forecast = await self._forecast_resources(
                    historical_data['resource_metrics'],
                    forecast_horizon
                )
                forecast['predictions']['resources'] = resource_forecast
            
            # Prévision de latence
            if 'latency_metrics' in historical_data:
                latency_forecast = await self._forecast_latency(
                    historical_data['latency_metrics'],
                    forecast_horizon
                )
                forecast['predictions']['latency'] = latency_forecast
            
            # Analyse des tendances
            forecast['trends'] = await self._analyze_performance_trends(historical_data)
            
            # Évaluation des risques
            forecast['risk_assessment'] = await self._assess_forecast_risks(
                forecast['predictions'],
                historical_data
            )
            
            return forecast
            
        except Exception as e:
            logger.error(f"Erreur génération prévision: {str(e)}")
            return {'error': str(e)}
    
    async def _forecast_load(
        self,
        load_metrics: Dict[str, Any],
        horizon_hours: int
    ) -> Dict[str, Any]:
        """Prévoit la charge future"""
        # Implémentation simplifiée - dans une vraie implémentation, utiliser un modèle de séries temporelles
        return {
            'method': 'simplified_average',
            'predicted_rps': load_metrics.get('current_rps', 0) * 1.1,  # +10%
            'peak_hour': '14:00',
            'confidence': 0.7
        }
    
    async def _forecast_resources(
        self,
        resource_metrics: Dict[str, Any],
        horizon_hours: int
    ) -> Dict[str, Any]:
        """Prévoit l'utilisation des ressources"""
        return {
            'cpu_usage_prediction': resource_metrics.get('cpu_usage', 0) * 1.05,
            'memory_usage_prediction': resource_metrics.get('memory_usage', 0) * 1.03,
            'io_usage_prediction': resource_metrics.get('io_usage', 0) * 1.02,
            'scaling_recommendations': [
                "Monitor CPU usage closely",
                "Consider auto-scaling configuration"
            ]
        }
    
    async def _forecast_latency(
        self,
        latency_metrics: Dict[str, Any],
        horizon_hours: int
    ) -> Dict[str, Any]:
        """Prévoit la latence future"""
        return {
            'p95_latency_prediction_ms': latency_metrics.get('p95_latency_ms', 0) * 1.08,
            'p99_latency_prediction_ms': latency_metrics.get('p99_latency_ms', 0) * 1.1,
            'sla_risk': 'low' if latency_metrics.get('p95_latency_ms', 0) * 1.08 < 1000 else 'medium'
        }
    
    async def _analyze_performance_trends(
        self,
        historical_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyse les tendances de performance"""
        trends = {
            'load_trend': 'stable',
            'latency_trend': 'stable',
            'resource_trend': 'stable',
            'seasonal_patterns': []
        }
        
        # Analyse simplifiée
        if 'trend_data' in historical_data:
            trend_data = historical_data['trend_data']
            
            if trend_data.get('load_slope', 0) > 0.1:
                trends['load_trend'] = 'increasing'
            elif trend_data.get('load_slope', 0) < -0.1:
                trends['load_trend'] = 'decreasing'
            
            if trend_data.get('latency_slope', 0) > 0.05:
                trends['latency_trend'] = 'increasing'
            elif trend_data.get('latency_slope', 0) < -0.05:
                trends['latency_trend'] = 'decreasing'
        
        return trends
    
    async def _assess_forecast_risks(
        self,
        predictions: Dict[str, Any],
        historical_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Évalue les risques basés sur les prévisions"""
        risks = {
            'high_risk_periods': [],
            'resource_shortages': [],
            'sla_risks': [],
            'overall_risk_level': 'low'
        }
        
        # Évaluation simplifiée
        load_prediction = predictions.get('load', {}).get('predicted_rps', 0)
        current_capacity = historical_data.get('capacity', {}).get('max_rps', 1000)
        
        if load_prediction > current_capacity * 0.8:
            risks['resource_shortages'].append({
                'type': 'capacity',
                'severity': 'medium',
                'description': f'Predicted load ({load_prediction:.0f} RPS) approaches capacity ({current_capacity} RPS)'
            })
            risks['overall_risk_level'] = 'medium'
        
        latency_prediction = predictions.get('latency', {}).get('p95_latency_prediction_ms', 0)
        if latency_prediction > 800:
            risks['sla_risks'].append({
                'type': 'latency',
                'severity': 'high',
                'description': f'Predicted p95 latency ({latency_prediction:.0f}ms) may violate SLA'
            })
            risks['overall_risk_level'] = 'high'
        
        return risks


# Exemple d'utilisation
if __name__ == "__main__":
    # Configuration de l'agent
    context = AgentContext(
        agent_id="performance_bottleneck_analyzer_001",
        environment="production",
        capabilities=["performance_analysis", "bottleneck_detection", "resource_optimization"]
    )
    
    # Création de l'analyseur
    analyzer = PerformanceBottleneckAnalyzer(
        agent_id="pba_001",
        context=context,
        tracing_endpoint="http://localhost:4318",
        metrics_endpoint="http://localhost:9090"
    )
    
    print("Performance Bottleneck Analyzer initialisé avec succès")
    print(f"Capacités: {analyzer.get_capabilities()}")
    print(f"Seuils configurés: {analyzer.performance_thresholds}")