"""
Tests de performance et scalabilité pour les agents MicroAgents

Teste:
1. Tests de charge avec intégration Locust
2. Exécution concurrente
3. Profilage d'utilisation mémoire
4. Utilisation CPU sous charge
5. Tests de réseau I/O
6. Performance base de données
7. Ratios de cache hit/miss
8. Analyse garbage collection
9. Mesure temps de démarrage
10. Performance cold/warm start
"""

import asyncio
import gc
import multiprocessing
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import psutil
import pytest
from locust import HttpUser, between, events, task
from locust.env import Environment
from locust.runners import LocalRunner, MasterRunner, WorkerRunner
from prometheus_client import Counter, Gauge, Histogram, Summary

from src.core.base.agent import BaseAgent
from src.core.agents.detectors.cost_anomaly_detector import CostAnomalyDetector
from src.monitoring.metrics.collector import MetricsCollector
from src.registry.cache.redis_handler import RedisCache
from src.utils.concurrency.manager import ConcurrencyManager


# ============================================================================
# CONSTANTES ET CONFIGURATION
# ============================================================================


class PerformanceConfig:
    """Configuration pour les tests de performance"""
    
    # Limites de performance
    MAX_ACCEPTABLE_LATENCY_P50 = 100  # ms
    MAX_ACCEPTABLE_LATENCY_P95 = 500  # ms
    MAX_ACCEPTABLE_LATENCY_P99 = 1000  # ms
    
    MIN_REQUIRED_THROUGHPUT = 100  # requêtes/seconde
    MAX_ACCEPTABLE_ERROR_RATE = 0.01  # 1%
    
    # Ressources
    MAX_MEMORY_INCREASE_MB = 50  # Maximum 50MB d'augmentation
    MAX_CPU_UTILIZATION = 80  # 80% maximum
    MAX_STARTUP_TIME_SECONDS = 5  # 5 secondes max pour démarrer
    
    # Tests de charge
    LOAD_TEST_DURATION = 60  # secondes
    LOAD_TEST_RAMP_UP = 10  # secondes
    LOAD_TEST_USERS = [10, 50, 100, 200]  # Niveaux d'utilisateurs
    
    # Cache
    MIN_CACHE_HIT_RATIO = 0.8  # 80% minimum
    MAX_CACHE_MISS_PENALTY_MS = 100  # 100ms max pour un cache miss
    
    # Base de données
    MAX_DB_QUERY_TIME_MS = 500  # 500ms max par requête
    MAX_DB_CONNECTIONS = 100  # 100 connexions max


# ============================================================================
# DÉCORATEURS ET CONTEXT MANAGERS
# ============================================================================


def performance_test(max_runtime: int = 60):
    """Décorateur pour les tests de performance avec timeout"""
    def decorator(test_func):
        @wraps(test_func)
        def wrapper(*args, **kwargs):
            # Démarre un timer
            start_time = time.time()
            
            # Exécute le test
            result = test_func(*args, **kwargs)
            
            # Vérifie le temps d'exécution
            runtime = time.time() - start_time
            if runtime > max_runtime:
                pytest.fail(f"Test trop long: {runtime:.2f}s > {max_runtime}s")
            
            return result
        return wrapper
    return decorator


@contextmanager
def performance_monitor(test_name: str, collect_metrics: bool = True):
    """Context manager pour monitorer les performances"""
    metrics = {
        "start_time": time.time(),
        "cpu_start": psutil.cpu_percent(interval=None),
        "memory_start": psutil.Process().memory_info().rss,
    }
    
    if collect_metrics:
        # Démarre le collecteur de métriques
        metrics_collector = MetricsCollector()
        metrics["collector"] = metrics_collector
    
    try:
        yield metrics
    finally:
        # Calcule les métriques finales
        metrics["end_time"] = time.time()
        metrics["duration"] = metrics["end_time"] - metrics["start_time"]
        metrics["cpu_end"] = psutil.cpu_percent(interval=0.1)
        metrics["memory_end"] = psutil.Process().memory_info().rss
        metrics["memory_increase_mb"] = (
            metrics["memory_end"] - metrics["memory_start"]
        ) / 1024 / 1024
        
        if collect_metrics:
            metrics_collector.record_agent_execution(
                agent_id="performance_monitor",
                execution_time=metrics["duration"],
                success=True,
            )


def time_execution(func):
    """Décorateur pour mesurer le temps d'exécution"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        execution_time = (end_time - start_time) * 1000  # en ms
        
        # Stocke le temps d'exécution dans le résultat
        if isinstance(result, dict):
            result["execution_time_ms"] = execution_time
        else:
            # Retourne un tuple (résultat, temps)
            return result, execution_time
        
        return result
    return wrapper


# ============================================================================
# FIXTURES DE PERFORMANCE
# ============================================================================


@pytest.fixture(scope="session")
def performance_metrics_collector():
    """Collecteur de métriques de performance global"""
    collector = MetricsCollector()
    yield collector
    
    # Nettoie à la fin de la session
    collector.cleanup()


@pytest.fixture
def sample_agents_pool():
    """Crée un pool d'agents de test pour les tests de scalabilité"""
    agents = []
    
    for i in range(100):  # 100 agents de test
        agent = Mock(spec=BaseAgent)
        agent.agent_id = f"test-agent-{i}"
        agent.agent_type = "performance_tester"
        agent.execute = AsyncMock(return_value={
            "success": True,
            "result": f"Test result {i}",
            "processing_time_ms": random.uniform(10, 100),
        })
        agent.cost_estimate = Mock(return_value=random.uniform(0.1, 1.0))
        
        agents.append(agent)
    
    return agents


@pytest.fixture
def redis_cache_client():
    """Client Redis pour les tests de cache"""
    # Utilise un mock ou un vrai Redis en fonction de la configuration
    try:
        import redis
        # Essaye de se connecter à Redis
        client = redis.Redis(host="localhost", port=6379, db=0)
        client.ping()  # Test la connexion
        yield client
        client.close()
    except:
        # Fallback sur un mock
        mock_client = Mock()
        mock_client.get = Mock(return_value=None)
        mock_client.set = Mock(return_value=True)
        mock_client.ping = Mock(return_value=True)
        yield mock_client


# ============================================================================
# TESTS DE CHARGE (LOAD TESTING)
# ============================================================================


class TestLoadTesting:
    """Tests de charge avec Locust"""
    
    class AgentLoadTestUser(HttpUser):
        """Utilisateur Locust pour tester la charge des agents"""
        wait_time = between(0.1, 0.5)
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.agent_ids = [f"agent-{i}" for i in range(100)]
        
        @task(3)
        def execute_agent(self):
            """Exécute un agent"""
            agent_id = random.choice(self.agent_ids)
            self.client.post(f"/api/v1/agents/{agent_id}/execute", json={
                "params": {"test": "load"},
                "priority": "normal",
            })
        
        @task(1)
        def get_agent_status(self):
            """Récupère le statut d'un agent"""
            agent_id = random.choice(self.agent_ids)
            self.client.get(f"/api/v1/agents/{agent_id}/status")
        
        @task(2)
        def batch_execute(self):
            """Exécute plusieurs agents en batch"""
            selected_agents = random.sample(self.agent_ids, 5)
            self.client.post("/api/v1/agents/batch-execute", json={
                "agent_ids": selected_agents,
                "params": {"test": "batch_load"},
            })
    
    @pytest.mark.loadtest
    @pytest.mark.skipif(
        os.environ.get("RUN_LOAD_TESTS") != "true",
        reason="Les tests de charge nécessitent RUN_LOAD_TESTS=true"
    )
    def test_locust_load_testing(self):
        """Test de charge avec Locust"""
        import locust.stats
        
        # Configuration Locust
        env = Environment(
            user_classes=[self.AgentLoadTestUser],
            events=events,
            host="http://localhost:8000"  # À adapter
        )
        
        runner = LocalRunner(env)
        
        # Métriques de performance
        performance_metrics = {
            "throughput": [],
            "latency_p50": [],
            "latency_p95": [],
            "latency_p99": [],
            "error_rate": [],
            "users": [],
        }
        
        def collect_metrics():
            """Collecte les métriques périodiquement"""
            stats = runner.stats
            if stats.total.num_requests > 0:
                performance_metrics["throughput"].append(
                    stats.total.current_rps
                )
                performance_metrics["latency_p50"].append(
                    stats.total.get_current_response_time_percentile(0.5)
                )
                performance_metrics["latency_p95"].append(
                    stats.total.get_current_response_time_percentile(0.95)
                )
                performance_metrics["latency_p99"].append(
                    stats.total.get_current_response_time_percentile(0.99)
                )
                performance_metrics["error_rate"].append(
                    stats.total.num_failures / stats.total.num_requests
                    if stats.total.num_requests > 0 else 0
                )
                performance_metrics["users"].append(
                    runner.user_count
                )
        
        try:
            # Phase de ramp-up
            target_users = PerformanceConfig.LOAD_TEST_USERS[-1]
            ramp_steps = len(PerformanceConfig.LOAD_TEST_USERS)
            
            for i, user_count in enumerate(PerformanceConfig.LOAD_TEST_USERS):
                print(f"\nRamp-up étape {i+1}/{ramp_steps}: {user_count} utilisateurs")
                
                runner.start(user_count, spawn_rate=user_count/PerformanceConfig.LOAD_TEST_RAMP_UP)
                
                # Exécute pendant la durée de l'étape
                step_duration = PerformanceConfig.LOAD_TEST_DURATION / ramp_steps
                time.sleep(step_duration)
                
                # Collecte les métriques
                collect_metrics()
            
            # Phase de maintien
            print(f"\nMaintien: {target_users} utilisateurs")
            time.sleep(PerformanceConfig.LOAD_TEST_DURATION)
            collect_metrics()
            
            # Arrête le test
            runner.stop()
            
            # Analyse des résultats
            self._analyze_load_test_results(performance_metrics)
            
        except Exception as e:
            pytest.fail(f"Échec du test de charge: {str(e)}")
        finally:
            runner.quit()
    
    def _analyze_load_test_results(self, metrics: Dict[str, List[float]]):
        """Analyse les résultats des tests de charge"""
        # Calcule les moyennes
        avg_throughput = statistics.mean(metrics["throughput"]) if metrics["throughput"] else 0
        avg_latency_p50 = statistics.mean(metrics["latency_p50"]) if metrics["latency_p50"] else 0
        avg_latency_p95 = statistics.mean(metrics["latency_p95"]) if metrics["latency_p95"] else 0
        avg_latency_p99 = statistics.mean(metrics["latency_p99"]) if metrics["latency_p99"] else 0
        avg_error_rate = statistics.mean(metrics["error_rate"]) if metrics["error_rate"] else 0
        
        print(f"\n{'='*50}")
        print("RÉSULTATS DES TESTS DE CHARGE")
        print(f"{'='*50}")
        print(f"Throughput moyen: {avg_throughput:.2f} req/s")
        print(f"Latence P50 moyenne: {avg_latency_p50:.2f} ms")
        print(f"Latence P95 moyenne: {avg_latency_p95:.2f} ms")
        print(f"Latence P99 moyenne: {avg_latency_p99:.2f} ms")
        print(f"Taux d'erreur moyen: {avg_error_rate:.2%}")
        print(f"{'='*50}")
        
        # Vérifie les seuils de performance
        assert avg_throughput >= PerformanceConfig.MIN_REQUIRED_THROUGHPUT, \
            f"Throughput insuffisant: {avg_throughput:.2f} < {PerformanceConfig.MIN_REQUIRED_THROUGHPUT}"
        
        assert avg_latency_p50 <= PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P50, \
            f"Latence P50 trop élevée: {avg_latency_p50:.2f} > {PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P50}"
        
        assert avg_latency_p95 <= PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P95, \
            f"Latence P95 trop élevée: {avg_latency_p95:.2f} > {PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P95}"
        
        assert avg_latency_p99 <= PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P99, \
            f"Latence P99 trop élevée: {avg_latency_p99:.2f} > {PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P99}"
        
        assert avg_error_rate <= PerformanceConfig.MAX_ACCEPTABLE_ERROR_RATE, \
            f"Taux d'erreur trop élevé: {avg_error_rate:.2%} > {PerformanceConfig.MAX_ACCEPTABLE_ERROR_RATE:.2%}"
        
        print("✅ Tous les seuils de performance sont respectés")


# ============================================================================
# TESTS D'EXÉCUTION CONCURRENTE
# ============================================================================


class TestConcurrentExecution:
    """Tests d'exécution concurrente"""
    
    @pytest.mark.performance
    @performance_test(max_runtime=30)
    def test_concurrent_agent_execution(self, sample_agents_pool):
        """Test d'exécution concurrente d'agents"""
        concurrency_levels = [1, 10, 50, 100]  # Niveaux de concurrence
        results = {}
        
        for concurrency in concurrency_levels:
            print(f"\nTest avec {concurrency} exécutions concurrentes...")
            
            with performance_monitor(f"concurrent_{concurrency}") as metrics:
                # Crée un gestionnaire de concurrence
                manager = ConcurrencyManager(max_concurrent=concurrency)
                
                # Exécute les agents de manière concurrente
                start_time = time.time()
                
                async def run_concurrent_test():
                    tasks = []
                    for i in range(concurrency):
                        agent = sample_agents_pool[i % len(sample_agents_pool)]
                        task = manager.execute(
                            agent.execute,
                            params={"test_id": i, "concurrency": concurrency}
                        )
                        tasks.append(task)
                    
                    results_list = await asyncio.gather(*tasks, return_exceptions=True)
                    return results_list
                
                # Exécute le test asynchrone
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    execution_results = loop.run_until_complete(run_concurrent_test())
                finally:
                    loop.close()
                
                end_time = time.time()
                duration = end_time - start_time
            
            # Analyse des résultats
            successful = sum(1 for r in execution_results 
                           if isinstance(r, dict) and r.get("success", False))
            failed = concurrency - successful
            
            throughput = concurrency / duration if duration > 0 else 0
            
            results[concurrency] = {
                "duration": duration,
                "successful": successful,
                "failed": failed,
                "throughput": throughput,
                "memory_increase_mb": metrics["memory_increase_mb"],
            }
            
            print(f"  Durée: {duration:.2f}s")
            print(f"  Succès: {successful}/{concurrency}")
            print(f"  Throughput: {throughput:.2f} req/s")
            print(f"  Mémoire: +{metrics['memory_increase_mb']:.2f} MB")
        
        # Analyse de scalabilité
        self._analyze_scalability(results)
    
    def _analyze_scalability(self, results: Dict[int, Dict[str, Any]]):
        """Analyse la scalabilité des résultats"""
        print(f"\n{'='*50}")
        print("ANALYSE DE SCALABILITÉ")
        print(f"{'='*50}")
        
        # Calcule l'efficacité de scaling
        base_throughput = results[1]["throughput"]
        
        for concurrency, data in results.items():
            if concurrency == 1:
                continue
            
            scaling_efficiency = (data["throughput"] / base_throughput) / concurrency * 100
            data["scaling_efficiency"] = scaling_efficiency
            
            print(f"\nConcurrence {concurrency}:")
            print(f"  Throughput: {data['throughput']:.2f} req/s")
            print(f"  Efficacité de scaling: {scaling_efficiency:.1f}%")
            print(f"  Utilisation mémoire: +{data['memory_increase_mb']:.2f} MB")
            
            # Vérifie que l'efficacité ne tombe pas trop bas
            min_efficiency = 50  # 50% d'efficacité minimum
            assert scaling_efficiency >= min_efficiency, \
                f"Efficacité de scaling trop faible: {scaling_efficiency:.1f}% < {min_efficiency}%"
            
            # Vérifie que l'augmentation mémoire est raisonnable
            max_memory_per_concurrent = 0.5  # 0.5MB par exécution concurrente
            expected_memory = concurrency * max_memory_per_concurrent
            assert data["memory_increase_mb"] <= expected_memory, \
                f"Utilisation mémoire excessive: {data['memory_increase_mb']:.2f}MB > {expected_memory:.2f}MB"
    
    @pytest.mark.performance
    def test_thread_pool_scalability(self):
        """Test de scalabilité avec ThreadPoolExecutor"""
        def cpu_intensive_task(n: int) -> float:
            """Tâche intensive en CPU"""
            result = 0
            for i in range(10000):
                result += (i * n) ** 0.5
            return result
        
        thread_counts = [1, 2, 4, 8, 16]
        task_counts = 100
        
        results = {}
        
        for thread_count in thread_counts:
            print(f"\nTest avec {thread_count} threads...")
            
            with performance_monitor(f"thread_pool_{thread_count}") as metrics:
                start_time = time.time()
                
                with ThreadPoolExecutor(max_workers=thread_count) as executor:
                    futures = [executor.submit(cpu_intensive_task, i) 
                             for i in range(task_counts)]
                    
                    # Attend la complétion
                    results_list = [f.result() for f in futures]
                
                end_time = time.time()
                duration = end_time - start_time
            
            throughput = task_counts / duration
            cpu_utilization = psutil.cpu_percent(interval=0.1)
            
            results[thread_count] = {
                "duration": duration,
                "throughput": throughput,
                "cpu_utilization": cpu_utilization,
                "memory_increase_mb": metrics["memory_increase_mb"],
            }
            
            print(f"  Durée: {duration:.2f}s")
            print(f"  Throughput: {throughput:.2f} tâches/s")
            print(f"  CPU: {cpu_utilization:.1f}%")
        
        # Vérifie que l'utilisation CPU ne dépasse pas les limites
        for thread_count, data in results.items():
            assert data["cpu_utilization"] <= PerformanceConfig.MAX_CPU_UTILIZATION, \
                f"Utilisation CPU trop élevée avec {thread_count} threads: {data['cpu_utilization']:.1f}%"
    
    @pytest.mark.performance
    def test_process_pool_scalability(self):
        """Test de scalabilité avec ProcessPoolExecutor"""
        def io_intensive_task(task_id: int) -> Dict[str, Any]:
            """Tâche intensive en I/O"""
            time.sleep(0.01)  # Simule une opération I/O
            return {
                "task_id": task_id,
                "result": task_id * 2,
                "processed_at": datetime.now().isoformat(),
            }
        
        process_counts = [1, 2, 4]
        task_counts = 100
        
        results = {}
        
        for process_count in process_counts:
            print(f"\nTest avec {process_count} processus...")
            
            start_time = time.time()
            
            with ProcessPoolExecutor(max_workers=process_count) as executor:
                futures = [executor.submit(io_intensive_task, i) 
                         for i in range(task_counts)]
                
                # Attend la complétion
                results_list = [f.result() for f in futures]
            
            end_time = time.time()
            duration = end_time - start_time
            
            throughput = task_counts / duration
            
            results[process_count] = {
                "duration": duration,
                "throughput": throughput,
                "results_count": len(results_list),
            }
            
            print(f"  Durée: {duration:.2f}s")
            print(f"  Throughput: {throughput:.2f} tâches/s")
        
        # Analyse comparative processus vs threads
        print(f"\n{'='*50}")
        print("COMPARAISON PROCESSUS VS THREADS")
        print(f"{'='*50}")
        
        for process_count in process_counts:
            if process_count in results:
                data = results[process_count]
                print(f"\n{process_count} processus: {data['throughput']:.2f} tâches/s")


# ============================================================================
# PROFILAGE UTILISATION MÉMOIRE
# ============================================================================


class TestMemoryProfiling:
    """Profilage d'utilisation mémoire"""
    
    @pytest.mark.performance
    def test_memory_usage_under_load(self, sample_agents_pool):
        """Test d'utilisation mémoire sous charge"""
        process = psutil.Process()
        
        # Mémoire initiale
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        print(f"Mémoire initiale: {initial_memory:.2f} MB")
        
        # Crée un grand nombre d'agents
        agents_count = 1000
        large_agent_pool = []
        
        for i in range(agents_count):
            agent = Mock(spec=BaseAgent)
            agent.agent_id = f"large-pool-agent-{i}"
            agent.agent_type = "memory_test"
            # Stocke des données pour utiliser de la mémoire
            agent.data = {"large_data": "x" * 1024}  # 1KB par agent
            large_agent_pool.append(agent)
        
        # Mémoire après création
        after_creation_memory = process.memory_info().rss / 1024 / 1024
        creation_increase = after_creation_memory - initial_memory
        print(f"Mémoire après création de {agents_count} agents: {after_creation_memory:.2f} MB")
        print(f"Augmentation: {creation_increase:.2f} MB")
        print(f"Mémoire par agent: {creation_increase / agents_count:.2f} KB")
        
        # Vérifie que l'augmentation est raisonnable
        max_per_agent_kb = 2  # 2KB maximum par agent
        memory_per_agent_kb = (creation_increase * 1024) / agents_count
        assert memory_per_agent_kb <= max_per_agent_kb, \
            f"Utilisation mémoire excessive par agent: {memory_per_agent_kb:.2f}KB > {max_per_agent_kb}KB"
        
        # Test sous charge
        iterations = 100
        memory_samples = []
        
        for i in range(iterations):
            # Simule une exécution
            active_agents = random.sample(large_agent_pool, 100)
            
            # Mesure la mémoire
            memory_samples.append(process.memory_info().rss)
            
            # Petite pause
            time.sleep(0.01)
        
        # Analyse des échantillons mémoire
        memory_mb_samples = [m / 1024 / 1024 for m in memory_samples]
        avg_memory = statistics.mean(memory_mb_samples)
        max_memory = max(memory_mb_samples)
        memory_variance = statistics.variance(memory_mb_samples) if len(memory_mb_samples) > 1 else 0
        
        print(f"\nAnalyse mémoire sous charge:")
        print(f"  Mémoire moyenne: {avg_memory:.2f} MB")
        print(f"  Mémoire maximale: {max_memory:.2f} MB")
        print(f"  Variance: {memory_variance:.2f} MB²")
        
        # Vérifie qu'il n'y a pas de fuite mémoire
        memory_increase = max_memory - initial_memory
        assert memory_increase <= PerformanceConfig.MAX_MEMORY_INCREASE_MB, \
            f"Augmentation mémoire excessive: {memory_increase:.2f}MB > {PerformanceConfig.MAX_MEMORY_INCREASE_MB}MB"
        
        # Vérifie la stabilité (variance faible)
        max_variance = 10  # 10MB² maximum
        assert memory_variance <= max_variance, \
            f"Variance mémoire trop élevée: {memory_variance:.2f}MB² > {max_variance}MB²"
    
    @pytest.mark.performance
    def test_memory_leak_detection(self):
        """Détection de fuites mémoire"""
        import tracemalloc
        from src.core.business_value.calculator import BusinessValueCalculator
        
        # Démarre le traçage mémoire
        tracemalloc.start()
        
        # Prend un snapshot initial
        snapshot1 = tracemalloc.take_snapshot()
        
        # Crée et utilise des objets de manière répétée
        calculators = []
        
        for i in range(100):
            calculator = BusinessValueCalculator(
                cache_size=1000,
                cache_ttl=300,
            )
            
            # Effectue des calculs
            for j in range(10):
                calculator.calculate_annualized_roi(
                    monthly_returns=10000 + j * 1000,
                    investment=100000 + j * 10000,
                )
            
            calculators.append(calculator)
        
        # Prend un snapshot final
        snapshot2 = tracemalloc.take_snapshot()
        
        # Compare les snapshots
        top_stats = snapshot2.compare_to(snapshot1, 'lineno')
        
        # Analyse les fuites
        total_leak = 0
        leaks_detected = []
        
        for stat in top_stats[:10]:  # Top 10
            if stat.size_diff > 0:  # Augmentation mémoire
                total_leak += stat.size_diff
                leaks_detected.append({
                    "file": stat.traceback[0].filename,
                    "line": stat.traceback[0].lineno,
                    "leak_size_kb": stat.size_diff / 1024,
                    "count": stat.count_diff,
                })
        
        # Arrête le traçage
        tracemalloc.stop()
        
        print(f"\nAnalyse fuites mémoire:")
        print(f"  Fuite totale: {total_leak / 1024:.2f} KB")
        
        if leaks_detected:
            print("\nFuite(s) détectée(s):")
            for leak in leaks_detected:
                print(f"  {leak['file']}:{leak['line']} - {leak['leak_size_kb']:.2f} KB ({leak['count']} objets)")
        
        # Vérifie qu'il n'y a pas de fuite significative
        max_leak_kb = 100  # 100KB maximum
        assert total_leak / 1024 <= max_leak_kb, \
            f"Fuite mémoire détectée: {total_leak / 1024:.2f}KB > {max_leak_kb}KB"
    
    @pytest.mark.performance
    def test_garbage_collection_impact(self):
        """Test l'impact du garbage collection sur les performances"""
        import gc
        
        # Désactive le GC pour le test
        gc.disable()
        
        # Crée beaucoup d'objets sans GC
        objects_created = 0
        start_time = time.time()
        
        try:
            for i in range(100000):
                # Crée un objet qui sera garbage
                data = {"id": i, "value": "x" * 100}
                objects_created += 1
                
                if i % 10000 == 0:
                    current_memory = psutil.Process().memory_info().rss / 1024 / 1024
                    print(f"  Créé {i} objets, mémoire: {current_memory:.2f} MB")
            
            duration_no_gc = time.time() - start_time
            
        finally:
            # Réactive le GC
            gc.enable()
            gc.collect()  # Force un GC
        
        # Mesure avec GC activé
        gc.enable()
        time.sleep(0.1)  # Laisse le GC se stabiliser
        
        start_time = time.time()
        
        for i in range(100000):
            data = {"id": i, "value": "x" * 100}
            
            if i % 10000 == 0:
                gc.collect()  # Force un GC périodique
        
        duration_with_gc = time.time() - start_time
        
        print(f"\nImpact du Garbage Collection:")
        print(f"  Sans GC: {duration_no_gc:.2f}s")
        print(f"  Avec GC: {duration_with_gc:.2f}s")
        print(f"  Différence: {(duration_with_gc - duration_no_gc):.2f}s ({(duration_with_gc - duration_no_gc) / duration_no_gc * 100:.1f}%)")
        
        # Vérifie que le GC n'ajoute pas trop de surcharge
        max_gc_overhead = 0.5  # 50% maximum
        gc_overhead = (duration_with_gc - duration_no_gc) / duration_no_gc
        assert gc_overhead <= max_gc_overhead, \
            f"Surcharge GC trop élevée: {gc_overhead:.1%} > {max_gc_overhead:.0%}"


# ============================================================================
# TESTS UTILISATION CPU
# ============================================================================


class TestCPUUtilization:
    """Tests d'utilisation CPU"""
    
    @pytest.mark.performance
    def test_cpu_utilization_under_load(self):
        """Test d'utilisation CPU sous charge"""
        cpu_cores = psutil.cpu_count()
        print(f"Cœurs CPU disponibles: {cpu_cores}")
        
        # Test avec différentes charges
        load_levels = [0.25, 0.5, 0.75, 1.0]  # Pourcentage de charge cible
        duration_per_level = 5  # secondes
        
        results = {}
        
        for load_level in load_levels:
            print(f"\nTest avec charge cible de {load_level*100:.0f}%...")
            
            # Mesure l'utilisation CPU initiale
            initial_cpu = psutil.cpu_percent(interval=1, percpu=True)
            initial_avg = statistics.mean(initial_cpu)
            
            # Crée une charge CPU
            target_utilization = load_level * 100
            threads_needed = int(cpu_cores * load_level)
            
            if threads_needed > 0:
                stop_event = threading.Event()
                threads = []
                
                def cpu_worker(worker_id: int, stop_event: threading.Event):
                    """Travailleur CPU intensif"""
                    while not stop_event.is_set():
                        # Calcul intensif
                        result = 0
                        for i in range(100000):
                            result += i ** 0.5
                
                # Démarre les workers
                for i in range(threads_needed):
                    t = threading.Thread(target=cpu_worker, args=(i, stop_event))
                    t.start()
                    threads.append(t)
                
                # Attend que la charge se stabilise
                time.sleep(2)
                
                # Mesure l'utilisation CPU sous charge
                cpu_samples = []
                for _ in range(duration_per_level):
                    cpu_util = psutil.cpu_percent(interval=0.5)
                    cpu_samples.append(cpu_util)
                    time.sleep(0.5)
                
                # Arrête les workers
                stop_event.set()
                for t in threads:
                    t.join()
                
                # Analyse
                avg_cpu = statistics.mean(cpu_samples)
                max_cpu = max(cpu_samples)
                
            else:
                # Pas de charge
                cpu_samples = [psutil.cpu_percent(interval=0.5) 
                             for _ in range(duration_per_level)]
                avg_cpu = statistics.mean(cpu_samples)
                max_cpu = max(cpu_samples)
            
            results[load_level] = {
                "target_utilization": target_utilization,
                "actual_avg_cpu": avg_cpu,
                "actual_max_cpu": max_cpu,
                "cpu_samples": cpu_samples,
            }
            
            print(f"  CPU cible: {target_utilization:.1f}%")
            print(f"  CPU moyen: {avg_cpu:.1f}%")
            print(f"  CPU max: {max_cpu:.1f}%")
        
        # Analyse de la linéarité
        print(f"\n{'='*50}")
        print("ANALYSE LINÉARITÉ CHARGE CPU")
        print(f"{'='*50}")
        
        for load_level, data in results.items():
            target = data["target_utilization"]
            actual = data["actual_avg_cpu"]
            deviation = abs(actual - target) / target * 100 if target > 0 else 0
            
            print(f"\nCharge {load_level*100:.0f}%:")
            print(f"  Cible: {target:.1f}%")
            print(f"  Réel: {actual:.1f}%")
            print(f"  Déviation: {deviation:.1f}%")
            
            # Vérifie que la déviation est acceptable
            max_deviation = 20  # 20% maximum
            assert deviation <= max_deviation, \
                f"Déviation CPU trop élevée: {deviation:.1f}% > {max_deviation}%"
    
    @pytest.mark.performance
    def test_cpu_scaling_efficiency(self):
        """Test l'efficacité du scaling CPU"""
        cpu_cores = psutil.cpu_count(logical=True)
        
        # Test avec différentes configurations de parallélisme
        parallelism_levels = [1, 2, 4, 8, cpu_cores]
        
        results = {}
        
        for parallelism in parallelism_levels:
            if parallelism > cpu_cores * 2:
                continue  # Évite le surbooking extrême
            
            print(f"\nTest avec parallélisme {parallelism}...")
            
            def parallel_task(task_id: int, complexity: int = 1000000) -> float:
                """Tâche parallélisable"""
                result = 0
                for i in range(complexity):
                    result += (i * task_id) ** 0.5
                return result
            
            tasks_count = 100
            
            # Exécution séquentielle (baseline)
            start_time = time.time()
            sequential_results = []
            for i in range(tasks_count):
                result = parallel_task(i, 100000)
                sequential_results.append(result)
            sequential_duration = time.time() - start_time
            
            # Exécution parallèle
            start_time = time.time()
            
            with ThreadPoolExecutor(max_workers=parallelism) as executor:
                futures = [executor.submit(parallel_task, i, 100000) 
                         for i in range(tasks_count)]
                parallel_results = [f.result() for f in futures]
            
            parallel_duration = time.time() - start_time
            
            # Calcul de l'efficacité
            speedup = sequential_duration / parallel_duration if parallel_duration > 0 else 0
            efficiency = (speedup / parallelism) * 100 if parallelism > 0 else 0
            
            results[parallelism] = {
                "sequential_duration": sequential_duration,
                "parallel_duration": parallel_duration,
                "speedup": speedup,
                "efficiency": efficiency,
                "tasks_count": tasks_count,
            }
            
            print(f"  Séquentiel: {sequential_duration:.2f}s")
            print(f"  Parallèle: {parallel_duration:.2f}s")
            print(f"  Speedup: {speedup:.2f}x")
            print(f"  Efficacité: {efficiency:.1f}%")
        
        # Analyse d'efficacité
        print(f"\n{'='*50}")
        print("ANALYSE EFFICACITÉ PARALLÉLISME")
        print(f"{'='*50}")
        
        for parallelism, data in results.items():
            if parallelism > 1:
                min_efficiency = 60  # 60% minimum
                assert data["efficiency"] >= min_efficiency, \
                    f"Efficacité trop faible avec parallélisme {parallelism}: {data['efficiency']:.1f}% < {min_efficiency}%"


# ============================================================================
# TESTS RÉSEAU I/O
# ============================================================================


class TestNetworkIO:
    """Tests de performance réseau I/O"""
    
    @pytest.mark.performance
    @pytest.mark.skipif(
        os.environ.get("TEST_NETWORK_IO") != "true",
        reason="Les tests réseau nécessitent TEST_NETWORK_IO=true"
    )
    def test_network_latency_impact(self):
        """Test l'impact de la latence réseau"""
        import socket
        import urllib.request
        import urllib.error
        
        # Test les endpoints critiques
        endpoints = [
            "http://localhost:8000/api/v1/health",
            "http://localhost:8000/api/v1/agents",
            "http://localhost:8000/api/v1/metrics",
        ]
        
        results = {}
        
        for endpoint in endpoints:
            print(f"\nTest de latence pour {endpoint}...")
            
            latencies = []
            successes = 0
            failures = 0
            
            for i in range(10):  # 10 requêtes
                try:
                    start_time = time.perf_counter()
                    
                    # Effectue la requête
                    response = urllib.request.urlopen(endpoint, timeout=5)
                    status_code = response.getcode()
                    
                    end_time = time.perf_counter()
                    latency_ms = (end_time - start_time) * 1000
                    
                    latencies.append(latency_ms)
                    successes += 1
                    
                    if status_code != 200:
                        failures += 1
                        
                except Exception as e:
                    failures += 1
                    latencies.append(None)  # Marque comme échec
            
            # Analyse des résultats
            successful_latencies = [l for l in latencies if l is not None]
            
            if successful_latencies:
                avg_latency = statistics.mean(successful_latencies)
                p95_latency = statistics.quantiles(successful_latencies, n=20)[18]  # 95ème percentile
                p99_latency = statistics.quantiles(successful_latencies, n=100)[98]  # 99ème percentile
            else:
                avg_latency = p95_latency = p99_latency = 0
            
            success_rate = successes / (successes + failures) if (successes + failures) > 0 else 0
            
            results[endpoint] = {
                "avg_latency_ms": avg_latency,
                "p95_latency_ms": p95_latency,
                "p99_latency_ms": p99_latency,
                "success_rate": success_rate,
                "request_count": successes + failures,
            }
            
            print(f"  Latence moyenne: {avg_latency:.2f} ms")
            print(f"  Latence P95: {p95_latency:.2f} ms")
            print(f"  Latence P99: {p99_latency:.2f} ms")
            print(f"  Taux de succès: {success_rate:.1%}")
        
        # Vérifie les seuils de performance
        for endpoint, data in results.items():
            assert data["avg_latency_ms"] <= PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P50, \
                f"Latence moyenne trop élevée pour {endpoint}: {data['avg_latency_ms']:.2f}ms"
            
            assert data["p95_latency_ms"] <= PerformanceConfig.MAX_ACCEPTABLE_LATENCY_P95, \
                f"Latence P95 trop élevée pour {endpoint}: {data['p95_latency_ms']:.2f}ms"
            
            assert data["success_rate"] >= (1 - PerformanceConfig.MAX_ACCEPTABLE_ERROR_RATE), \
                f"Taux de succès trop faible pour {endpoint}: {data['success_rate']:.1%}"
    
    @pytest.mark.performance
    def test_network_throughput(self):
        """Test le débit réseau"""
        import io
        import socket
        
        # Test de transfert de données
        data_sizes = [1024, 10240, 102400, 1048576]  # 1KB à 1MB
        
        results = {}
        
        for data_size in data_sizes:
            print(f"\nTest de débit avec {data_size/1024:.1f} KB...")
            
            # Génère des données
            data = b"x" * data_size
            
            throughputs = []
            
            for i in range(5):  # 5 itérations
                # Simule un transfert (mock)
                start_time = time.perf_counter()
                
                # Simule le temps de transfert basé sur la taille
                # Hypothèse: 100 MB/s
                simulated_transfer_time = data_size / (100 * 1024 * 1024)  # secondes
                time.sleep(simulated_transfer_time)
                
                # Simule le traitement
                buffer = io.BytesIO(data)
                processed = len(buffer.read())
                
                end_time = time.perf_counter()
                duration = end_time - start_time
                
                throughput = data_size / duration / 1024 / 1024  # MB/s
                throughputs.append(throughput)
            
            avg_throughput = statistics.mean(throughputs)
            min_throughput = min(throughputs)
            max_throughput = max(throughputs)
            
            results[data_size] = {
                "avg_throughput_mbps": avg_throughput,
                "min_throughput_mbps": min_throughput,
                "max_throughput_mbps": max_throughput,
                "data_size_kb": data_size / 1024,
            }
            
            print(f"  Débit moyen: {avg_throughput:.2f} MB/s")
            print(f"  Débit min: {min_throughput:.2f} MB/s")
            print(f"  Débit max: {max_throughput:.2f} MB/s")
        
        # Analyse
        print(f"\n{'='*50}")
        print("ANALYSE DÉBIT RÉSEAU")
        print(f"{'='*50}")
        
        min_acceptable_throughput = 10  # 10 MB/s minimum
        for data_size, data in results.items():
            assert data["avg_throughput_mbps"] >= min_acceptable_throughput, \
                f"Débit moyen trop faible pour {data['data_size_kb']:.1f}KB: {data['avg_throughput_mbps']:.2f} MB/s"


# ============================================================================
# TESTS PERFORMANCE BASE DE DONNÉES
# ============================================================================


class TestDatabasePerformance:
    """Tests de performance base de données"""
    
    @pytest.mark.performance
    def test_database_query_performance(self):
        """Test des performances des requêtes base de données"""
        import sqlite3
        import tempfile
        
        # Crée une base de données SQLite temporaire
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            db_path = tmp.name
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Crée une table de test
            cursor.execute('''
                CREATE TABLE performance_test (
                    id INTEGER PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    execution_time_ms REAL,
                    success INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Insère des données de test
            test_data_count = 10000
            print(f"\nInsertion de {test_data_count} lignes de test...")
            
            start_time = time.time()
            
            for i in range(test_data_count):
                cursor.execute('''
                    INSERT INTO performance_test (agent_id, execution_time_ms, success)
                    VALUES (?, ?, ?)
                ''', (f"agent-{i}", random.uniform(10, 1000), random.randint(0, 1)))
            
            conn.commit()
            insert_duration = time.time() - start_time
            insert_rate = test_data_count / insert_duration
            
            print(f"  Durée insertion: {insert_duration:.2f}s")
            print(f"  Taux insertion: {insert_rate:.2f} lignes/s")
            
            # Test des requêtes
            query_types = [
                ("SELECT simple", "SELECT * FROM performance_test WHERE id = ?"),
                ("SELECT avec filtre", "SELECT * FROM performance_test WHERE success = 1 AND execution_time_ms > ?"),
                ("SELECT avec tri", "SELECT * FROM performance_test ORDER BY execution_time_ms DESC LIMIT ?"),
                ("SELECT agrégation", "SELECT agent_id, AVG(execution_time_ms) FROM performance_test GROUP BY agent_id"),
                ("UPDATE", "UPDATE performance_test SET execution_time_ms = ? WHERE id = ?"),
                ("DELETE", "DELETE FROM performance_test WHERE id = ?"),
            ]
            
            results = {}
            
            for query_name, query_sql in query_types:
                print(f"\nTest requête: {query_name}...")
                
                execution_times = []
                
                for i in range(100):  # 100 exécutions
                    start_time = time.perf_counter()
                    
                    if "?" in query_sql:
                        if "INSERT" in query_sql or "UPDATE" in query_sql or "DELETE" in query_sql:
                            cursor.execute(query_sql, (random.uniform(10, 1000), random.randint(1, test_data_count)))
                            conn.commit()
                        else:
                            cursor.execute(query_sql, (random.randint(1, test_data_count),))
                    else:
                        cursor.execute(query_sql)
                    
                    # Récupère les résultats si nécessaire
                    if query_sql.startswith("SELECT"):
                        results_list = cursor.fetchall()
                    
                    end_time = time.perf_counter()
                    execution_time_ms = (end_time - start_time) * 1000
                    execution_times.append(execution_time_ms)
                
                avg_time = statistics.mean(execution_times)
                p95_time = statistics.quantiles(execution_times, n=20)[18]
                p99_time = statistics.quantiles(execution_times, n=100)[98]
                
                results[query_name] = {
                    "avg_time_ms": avg_time,
                    "p95_time_ms": p95_time,
                    "p99_time_ms": p99_time,
                    "query_sql": query_sql,
                }
                
                print(f"  Temps moyen: {avg_time:.2f} ms")
                print(f"  P95: {p95_time:.2f} ms")
                print(f"  P99: {p99_time:.2f} ms")
                
                # Vérifie les performances
                assert avg_time <= PerformanceConfig.MAX_DB_QUERY_TIME_MS, \
                    f"Temps d'exécution trop long pour {query_name}: {avg_time:.2f}ms"
            
            # Test des connexions concurrentes
            print(f"\nTest des connexions concurrentes...")
            
            max_connections = PerformanceConfig.MAX_DB_CONNECTIONS
            successful_connections = 0
            
            def test_connection(conn_id):
                try:
                    temp_conn = sqlite3.connect(db_path, timeout=10)
                    cursor = temp_conn.cursor()
                    cursor.execute("SELECT 1")
                    temp_conn.close()
                    return True
                except:
                    return False
            
            with ThreadPoolExecutor(max_workers=max_connections) as executor:
                futures = [executor.submit(test_connection, i) 
                         for i in range(max_connections)]
                
                successful_connections = sum(1 for f in futures if f.result())
            
            connection_success_rate = successful_connections / max_connections
            
            print(f"  Connexions réussies: {successful_connections}/{max_connections}")
            print(f"  Taux de succès: {connection_success_rate:.1%}")
            
            assert connection_success_rate >= 0.95, \
                f"Taux de succès connexions trop faible: {connection_success_rate:.1%}"
            
            return results
            
        finally:
            # Nettoie
            if 'conn' in locals():
                conn.close()
            os.unlink(db_path)


# ============================================================================
# TESTS PERFORMANCE CACHE
# ============================================================================


class TestCachePerformance:
    """Tests de performance du cache"""
    
    @pytest.mark.performance
    def test_cache_hit_ratios(self, redis_cache_client):
        """Test des ratios de cache hit/miss"""
        cache = RedisCache(
            client=redis_cache_client,
            default_ttl=300,
            namespace="performance_test",
        )
        
        # Test avec différents patterns d'accès
        test_patterns = [
            ("Uniform", 0.5),  # 50% de chance de réutilisation
            ("Temporal Locality", 0.8),  # 80% de chance de réutilisation
            ("Random", 0.2),  # 20% de chance de réutilisation
        ]
        
        results = {}
        
        for pattern_name, reuse_probability in test_patterns:
            print(f"\nTest pattern: {pattern_name} (réutilisation: {reuse_probability:.0%})...")
            
            # Pré-charge le cache
            cache_keys = [f"key-{i}" for i in range(100)]
            for key in cache_keys:
                cache.set(key, f"value-{key}")
            
            hits = 0
            misses = 0
            access_times = []
            
            for i in range(1000):  # 1000 accès
                # Choisit une clé selon le pattern
                if random.random() < reuse_probability and i > 0:
                    # Réutilise une clé récente
                    key = random.choice(cache_keys[max(0, i-100):i])
                else:
                    # Nouvelle clé
                    key = f"key-{random.randint(0, 199)}"  # 50% chance de miss
                
                start_time = time.perf_counter()
                
                value = cache.get(key)
                
                end_time = time.perf_counter()
                access_time_ms = (end_time - start_time) * 1000
                access_times.append(access_time_ms)
                
                if value is not None:
                    hits += 1
                else:
                    misses += 1
                    # Cache le miss
                    cache.set(key, f"new-value-{key}")
            
            hit_ratio = hits / (hits + misses) if (hits + misses) > 0 else 0
            avg_access_time = statistics.mean(access_times)
            p95_access_time = statistics.quantiles(access_times, n=20)[18]
            
            results[pattern_name] = {
                "hit_ratio": hit_ratio,
                "miss_ratio": 1 - hit_ratio,
                "avg_access_time_ms": avg_access_time,
                "p95_access_time_ms": p95_access_time,
                "total_accesses": hits + misses,
            }
            
            print(f"  Hit ratio: {hit_ratio:.2%}")
            print(f"  Temps accès moyen: {avg_access_time:.2f} ms")
            print(f"  Temps accès P95: {p95_access_time:.2f} ms")
            
            # Vérifie le ratio de cache hit
            assert hit_ratio >= PerformanceConfig.MIN_CACHE_HIT_RATIO, \
                f"Hit ratio trop faible pour {pattern_name}: {hit_ratio:.2%} < {PerformanceConfig.MIN_CACHE_HIT_RATIO:.0%}"
        
        # Analyse comparative
        print(f"\n{'='*50}")
        print("ANALYSE COMPARATIVE PATTERNS CACHE")
        print(f"{'='*50}")
        
        best_pattern = max(results.items(), key=lambda x: x[1]["hit_ratio"])
        worst_pattern = min(results.items(), key=lambda x: x[1]["hit_ratio"])
        
        print(f"\nMeilleur pattern: {best_pattern[0]} (hit ratio: {best_pattern[1]['hit_ratio']:.2%})")
        print(f"Pire pattern: {worst_pattern[0]} (hit ratio: {worst_pattern[1]['hit_ratio']:.2%})")
    
    @pytest.mark.performance
    def test_cache_miss_penalty(self, redis_cache_client):
        """Test de la pénalité des cache miss"""
        cache = RedisCache(
            client=redis_cache_client,
            default_ttl=300,
            namespace="miss_penalty_test",
        )
        
        # Mesure le temps d'un cache hit
        cache.set("test-hit", "hit-value")
        
        hit_times = []
        for i in range(100):
            start_time = time.perf_counter()
            value = cache.get("test-hit")
            end_time = time.perf_counter()
            hit_times.append((end_time - start_time) * 1000)
        
        avg_hit_time = statistics.mean(hit_times)
        
        # Mesure le temps d'un cache miss avec rechargement
        miss_times = []
        for i in range(100):
            key = f"miss-key-{i}"
            
            start_time = time.perf_counter()
            
            # Premier accès (miss)
            value = cache.get(key)
            if value is None:
                # Simule le rechargement
                time.sleep(0.001)  # 1ms de traitement
                cache.set(key, f"reloaded-{key}")
            
            end_time = time.perf_counter()
            miss_times.append((end_time - start_time) * 1000)
        
        avg_miss_time = statistics.mean(miss_times)
        
        # Calcule la pénalité
        miss_penalty = avg_miss_time - avg_hit_time
        
        print(f"\nAnalyse pénalité cache miss:")
        print(f"  Temps cache hit moyen: {avg_hit_time:.2f} ms")
        print(f"  Temps cache miss moyen: {avg_miss_time:.2f} ms")
        print(f"  Pénalité miss: {miss_penalty:.2f} ms")
        
        # Vérifie que la pénalité est acceptable
        assert miss_penalty <= PerformanceConfig.MAX_CACHE_MISS_PENALTY_MS, \
            f"Pénalité cache miss trop élevée: {miss_penalty:.2f}ms > {PerformanceConfig.MAX_CACHE_MISS_PENALTY_MS}ms"
    
    @pytest.mark.performance
    def test_cache_scalability(self, redis_cache_client):
        """Test de scalabilité du cache"""
        cache = RedisCache(
            client=redis_cache_client,
            default_ttl=300,
            namespace="scalability_test",
        )
        
        cache_sizes = [100, 1000, 10000, 100000]  # Nombre d'éléments
        results = {}
        
        for cache_size in cache_sizes:
            print(f"\nTest avec cache de {cache_size} éléments...")
            
            # Remplit le cache
            start_time = time.time()
            
            for i in range(cache_size):
                cache.set(f"key-{i}", f"value-{i}" * 10)  # Valeurs de 100 bytes
            
            fill_duration = time.time() - start_time
            fill_rate = cache_size / fill_duration
            
            # Test d'accès
            access_times = []
            hit_count = 0
            
            for i in range(1000):
                # Accès aléatoire
                key = f"key-{random.randint(0, cache_size-1)}"
                
                start_time = time.perf_counter()
                value = cache.get(key)
                end_time = time.perf_counter()
                
                access_time_ms = (end_time - start_time) * 1000
                access_times.append(access_time_ms)
                
                if value is not None:
                    hit_count += 1
            
            avg_access_time = statistics.mean(access_times)
            p95_access_time = statistics.quantiles(access_times, n=20)[18]
            hit_ratio = hit_count / 1000
            
            results[cache_size] = {
                "fill_duration": fill_duration,
                "fill_rate": fill_rate,
                "avg_access_time_ms": avg_access_time,
                "p95_access_time_ms": p95_access_time,
                "hit_ratio": hit_ratio,
            }
            
            print(f"  Temps remplissage: {fill_duration:.2f}s ({fill_rate:.0f} éléments/s)")
            print(f"  Temps accès moyen: {avg_access_time:.2f} ms")
            print(f"  Temps accès P95: {p95_access_time:.2f} ms")
            print(f"  Hit ratio: {hit_ratio:.2%}")
        
        # Analyse de scalabilité
        print(f"\n{'='*50}")
        print("ANALYSE SCALABILITÉ CACHE")
        print(f"{'='*50}")
        
        base_access_time = results[100]["avg_access_time_ms"]
        
        for cache_size, data in results.items():
            if cache_size > 100:
                scaling_factor = data["avg_access_time_ms"] / base_access_time
                print(f"\nCache {cache_size}:")
                print(f"  Facteur d'échelle: {scaling_factor:.2f}x")
                print(f"  Accès moyen: {data['avg_access_time_ms']:.2f} ms")
                
                # Vérifie que le temps d'accès ne dégrade pas trop
                max_scaling_factor = 2.0  # 2x maximum
                assert scaling_factor <= max_scaling_factor, \
                    f"Dégradation excessive pour cache {cache_size}: {scaling_factor:.2f}x > {max_scaling_factor}x"


# ============================================================================
# TESTS TEMPS DE DÉMARRAGE
# ============================================================================


class TestStartupPerformance:
    """Tests des temps de démarrage"""
    
    @pytest.mark.performance
    def test_cold_start_time(self):
        """Test du temps de démarrage à froid (cold start)"""
        print("\nTest démarrage à froid...")
        
        startup_times = []
        
        for i in range(10):  # 10 itérations
            # Simule un démarrage à froid
            start_time = time.perf_counter()
            
            # Importations qui se produisent au démarrage
            import importlib
            import sys
            
            # Réinitialise le module sys pour simuler un démarrage frais
            modules_to_reload = [
                'src.core.base.agent',
                'src.monitoring.metrics.collector',
                'src.registry.cache.redis_handler',
            ]
            
            for module in modules_to_reload:
                if module in sys.modules:
                    del sys.modules[module]
            
            # Recharge les modules
            for module in modules_to_reload:
                importlib.import_module(module)
            
            end_time = time.perf_counter()
            startup_time = (end_time - start_time) * 1000  # ms
            startup_times.append(startup_time)
            
            print(f"  Itération {i+1}: {startup_time:.2f} ms")
        
        avg_startup_time = statistics.mean(startup_times)
        max_startup_time = max(startup_times)
        
        print(f"\nDémarrage à froid:")
        print(f"  Temps moyen: {avg_startup_time:.2f} ms")
        print(f"  Temps max: {max_startup_time:.2f} ms")
        
        assert avg_startup_time <= PerformanceConfig.MAX_STARTUP_TIME_SECONDS * 1000, \
            f"Temps de démarrage à froid trop long: {avg_startup_time:.2f}ms"
    
    @pytest.mark.performance
    def test_warm_start_time(self):
        """Test du temps de démarrage à chaud (warm start)"""
        print("\nTest démarrage à chaud...")
        
        # Pré-charge les modules
        import src.core.base.agent
        import src.monitoring.metrics.collector
        
        startup_times = []
        
        for i in range(100):  # 100 itérations
            start_time = time.perf_counter()
            
            # Crée une instance rapide
            agent = Mock(spec=BaseAgent)
            agent.agent_id = f"warm-start-agent-{i}"
            
            end_time = time.perf_counter()
            startup_time = (end_time - start_time) * 1000  # ms
            startup_times.append(startup_time)
        
        avg_startup_time = statistics.mean(startup_times)
        p95_startup_time = statistics.quantiles(startup_times, n=20)[18]
        
        print(f"Démarrage à chaud:")
        print(f"  Temps moyen: {avg_startup_time:.2f} ms")
        print(f"  Temps P95: {p95_startup_time:.2f} ms")
        
        # Le démarrage à chaud devrait être beaucoup plus rapide
        max_warm_start_time = 100  # 100ms maximum
        assert avg_startup_time <= max_warm_start_time, \
            f"Temps de démarrage à chaud trop long: {avg_startup_time:.2f}ms"
    
    @pytest.mark.performance
    def test_cold_vs_warm_comparison(self):
        """Comparaison cold vs warm start"""
        cold_times = []
        warm_times = []
        
        for i in range(5):  # 5 itérations pour cold start
            # Cold start
            import importlib
            import sys
            
            modules_to_clear = [
                'src.core.base.agent',
                'src.monitoring.metrics.collector',
            ]
            
            start_time = time.perf_counter()
            
            for module in modules_to_clear:
                if module in sys.modules:
                    del sys.modules[module]
            
            for module in modules_to_clear:
                importlib.import_module(module)
            
            end_time = time.perf_counter()
            cold_times.append((end_time - start_time) * 1000)
            
            # Warm start (immédiatement après)
            start_time = time.perf_counter()
            
            # Réutilise les modules déjà chargés
            import src.core.base.agent
            import src.monitoring.metrics.collector
            
            # Crée une instance
            agent = Mock(spec=BaseAgent)
            
            end_time = time.perf_counter()
            warm_times.append((end_time - start_time) * 1000)
        
        avg_cold_time = statistics.mean(cold_times)
        avg_warm_time = statistics.mean(warm_times)
        speedup = avg_cold_time / avg_warm_time if avg_warm_time > 0 else 0
        
        print(f"\nComparaison Cold vs Warm Start:")
        print(f"  Cold start moyen: {avg_cold_time:.2f} ms")
        print(f"  Warm start moyen: {avg_warm_time:.2f} ms")
        print(f"  Speedup: {speedup:.1f}x")
        
        # Le warm start devrait être significativement plus rapide
        min_speedup = 10  # 10x minimum
        assert speedup >= min_speedup, \
            f"Speedup insuffisant warm vs cold: {speedup:.1f}x < {min_speedup}x"


# ============================================================================
# TESTS ROI CALCULATION SPEED
# ============================================================================


class TestROICalculationSpeed:
    """Tests de vitesse de calcul ROI"""
    
    @pytest.mark.performance
    def test_roi_calculation_throughput(self):
        """Test du débit des calculs ROI"""
        from src.core.business_value.calculator import calculate_roi
        
        calculation_counts = [100, 1000, 10000, 100000]
        results = {}
        
        for count in calculation_counts:
            print(f"\nTest avec {count} calculs ROI...")
            
            start_time = time.perf_counter()
            
            for i in range(count):
                investment = 100000 + i * 1000
                returns = 150000 + i * 1500
                roi = calculate_roi(investment, returns)
                
                # Validation basique
                assert isinstance(roi, float)
            
            end_time = time.perf_counter()
            duration = end_time - start_time
            throughput = count / duration
            
            results[count] = {
                "duration": duration,
                "throughput": throughput,
                "calculations_per_second": throughput,
            }
            
            print(f"  Durée: {duration:.2f}s")
            print(f"  Débit: {throughput:.0f} calculs/s")
            
            # Vérifie que le débit est acceptable
            min_throughput = 1000  # 1000 calculs/s minimum
            assert throughput >= min_throughput, \
                f"Débit calcul ROI trop faible: {throughput:.0f} calculs/s < {min_throughput}"
        
        # Analyse de scalabilité
        print(f"\n{'='*50}")
        print("ANALYSE SCALABILITÉ CALCUL ROI")
        print(f"{'='*50}")
        
        baseline_throughput = results[100]["throughput"]
        
        for count, data in results.items():
            if count > 100:
                efficiency = data["throughput"] / baseline_throughput * 100
                print(f"\n{count} calculs:")
                print(f"  Débit: {data['throughput']:.0f} calculs/s")
                print(f"  Efficacité: {efficiency:.1f}%")
                
                # L'efficacité ne devrait pas trop baisser
                min_efficiency = 80  # 80% minimum
                assert efficiency >= min_efficiency, \
                    f"Efficacité trop faible pour {count} calculs: {efficiency:.1f}%"
    
    @pytest.mark.performance
    def test_cost_per_request_calculation(self):
        """Test du calcul coût par requête"""
        # Hypothèses de coût
        infra_cost_per_hour = 10.0  # 10$/heure
        agent_cost_per_execution = 0.001  # 0.1 cent par exécution
        
        # Test avec différents niveaux de charge
        request_rates = [100, 1000, 10000, 100000]  # requêtes/heure
        
        results = {}
        
        for rate in request_rates:
            # Calcule le coût par requête
            infra_cost_per_request = infra_cost_per_hour / rate
            total_cost_per_request = infra_cost_per_request + agent_cost_per_execution
            
            # Calcule le ROI si chaque requête génère de la valeur
            assumed_value_per_request = 0.01  # 1 cent de valeur par requête
            roi_per_request = (assumed_value_per_request - total_cost_per_request) / total_cost_per_request * 100
            
            results[rate] = {
                "requests_per_hour": rate,
                "infra_cost_per_request": infra_cost_per_request,
                "agent_cost_per_request": agent_cost_per_execution,
                "total_cost_per_request": total_cost_per_request,
                "assumed_value_per_request": assumed_value_per_request,
                "roi_per_request": roi_per_request,
            }
            
            print(f"\n{rate} requêtes/heure:")
            print(f"  Coût infra/requête: ${infra_cost_per_request:.6f}")
            print(f"  Coût total/requête: ${total_cost_per_request:.6f}")
            print(f"  ROI/requête: {roi_per_request:.1f}%")
            
            # Vérifie que le coût par requête est raisonnable
            max_cost_per_request = 0.01  # 1 cent maximum
            assert total_cost_per_request <= max_cost_per_request, \
                f"Coût par requête trop élevé: ${total_cost_per_request:.6f} > ${max_cost_per_request}"
        
        # Analyse d'efficacité économique
        print(f"\n{'='*50}")
        print("ANALYSE EFFICACITÉ ÉCONOMIQUE")
        print(f"{'='*50}")
        
        # Trouve le point d'équilibre
        for rate, data in results.items():
            if data["roi_per_request"] >= 0:
                print(f"\nPoint d'équilibre à {rate} requêtes/heure")
                print(f"  ROI: {data['roi_per_request']:.1f}%")
                break


# ============================================================================
# RAPPORT DE PERFORMANCE
# ============================================================================


def generate_performance_report():
    """Génère un rapport de performance complet"""
    report = {
        "timestamp": datetime.now().isoformat(),
        "performance_tests": {},
        "summary": {},
        "recommendations": [],
    }
    
    # Collecte les résultats des tests
    test_results = {}
    
    # Exécute les différents tests de performance
    test_classes = [
        TestLoadTesting,
        TestConcurrentExecution,
        TestMemoryProfiling,
        TestCPUUtilization,
        TestNetworkIO,
        TestDatabasePerformance,
        TestCachePerformance,
        TestStartupPerformance,
        TestROICalculationSpeed,
    ]
    
    for test_class in test_classes:
        test_name = test_class.__name__
        print(f"\nExécution des tests: {test_name}")
        
        # Crée une instance et exécute les tests
        instance = test_class()
        
        # Exécute tous les tests de la classe
        for method_name in dir(instance):
            if method_name.startswith('test_'):
                method = getattr(instance, method_name)
                if callable(method):
                    try:
                        print(f"  - {method_name}...")
                        result = method()
                        test_results[f"{test_name}.{method_name}"] = {
                            "status": "PASSED",
                            "result": result,
                        }
                    except Exception as e:
                        test_results[f"{test_name}.{method_name}"] = {
                            "status": "FAILED",
                            "error": str(e),
                        }
    
    # Analyse des résultats
    passed = sum(1 for r in test_results.values() if r["status"] == "PASSED")
    failed = sum(1 for r in test_results.values() if r["status"] == "FAILED")
    
    report["performance_tests"] = test_results
    report["summary"] = {
        "total_tests": len(test_results),
        "passed": passed,
        "failed": failed,
        "success_rate": passed / len(test_results) if len(test_results) > 0 else 0,
    }
    
    # Génère des recommandations
    if failed > 0:
        report["recommendations"].append(
            f"{failed} tests ont échoué. Vérifiez les logs pour plus de détails."
        )
    
    # Vérifie les performances globales
    if report["summary"]["success_rate"] < 0.9:
        report["recommendations"].append(
            "Taux de succès insuffisant. Optimisez les performances critiques."
        )
    
    # Sauvegarde le rapport
    import json
    report_file = f"performance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n{'='*50}")
    print("RAPPORT DE PERFORMANCE GÉNÉRÉ")
    print(f"{'='*50}")
    print(f"Fichier: {report_file}")
    print(f"Tests exécutés: {len(test_results)}")
    print(f"Tests réussis: {passed}")
    print(f"Tests échoués: {failed}")
    print(f"Taux de succès: {report['summary']['success_rate']:.1%}")
    
    return report


# ============================================================================
# EXÉCUTION PRINCIPALE
# ============================================================================


if __name__ == "__main__":
    """Exécute les tests de performance"""
    import sys
    
    # Mode rapport complet
    if "--report" in sys.argv:
        report = generate_performance_report()
        sys.exit(0 if report["summary"]["success_rate"] >= 0.8 else 1)
    
    # Mode test spécifique
    elif "--test" in sys.argv:
        test_name = sys.argv[sys.argv.index("--test") + 1]
        
        # Exécute le test spécifique
        pytest.main([__file__, "-k", test_name, "-v", "--tb=short"])
    
    else:
        # Exécute tous les tests de performance
        pytest.main([__file__, "-m", "performance or loadtest", "-v", "--tb=short"])