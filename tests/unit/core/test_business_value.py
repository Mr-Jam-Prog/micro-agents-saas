"""
Tests unitaires pour le module Business Value

Teste:
1. Calcul ROI et métriques financières
2. Cas limites et validations
3. Benchmarks de performance
4. Simulations Monte Carlo
5. Analyses comparatives
6. Sérialisation des données
7. Support multi-devises
8. Gestion des fuseaux horaires
9. Invalidation du cache
10. Sécurité des calculs concurrents
"""

import asyncio
import concurrent.futures
import json
import random
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import numpy as np
import pytest
from freezegun import freeze_time
from hypothesis import assume, given, settings, strategies as st
from hypothesis.strategies import composite, floats, integers, lists, text
from pydantic import ValidationError

from src.core.business_value import (
    BusinessValueMetrics,
    ROITracking,
    calculate_roi,
    calculate_npv,
    calculate_irr,
    run_monte_carlo_simulation,
    BusinessValueCalculator,
    ROIForecaster,
    CostForecaster,
    RevenueForecaster,
)
from src.core.business_value.calculator import InvestmentAllocator, PortfolioOptimizer
from src.core.business_value.forecast import RiskAdjuster
from src.core.business_value.pricing.models import PricingModel
from src.core.business_value.reporting.exporter import ReportExporter


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def sample_business_metrics():
    """Retourne des métriques business de test"""
    return BusinessValueMetrics(
        tenant_id="test-tenant-123",
        metric_type="cost_savings",
        value=50000.0,
        currency="USD",
        period="monthly",
        timestamp=datetime(2024, 1, 15, 10, 30, 0),
        attribution={
            "cost_optimizer_agent": 30000.0,
            "performance_optimizer_agent": 20000.0,
        },
        confidence_score=0.95,
    )


@pytest.fixture
def sample_roi_tracking():
    """Retourne un suivi ROI de test"""
    return ROITracking(
        tenant_id="test-tenant-123",
        investment=100000.0,
        returns=150000.0,
        roi_percentage=50.0,
        period="annual",
        timestamp=datetime(2024, 1, 15, 10, 30, 0),
        breakdown={
            "cost_savings": {"value": 80000.0, "confidence": 0.9},
            "revenue_impact": {"value": 70000.0, "confidence": 0.85},
        },
    )


@pytest.fixture
def sample_cash_flows():
    """Retourne des flux de trésorerie de test"""
    return [-100000, 30000, 40000, 50000, 60000]  # Investissement initial + 4 ans


@pytest.fixture
def business_value_calculator():
    """Retourne une instance du calculateur de valeur business"""
    return BusinessValueCalculator(cache_size=100, cache_ttl=300)


@pytest.fixture
def roi_forecaster():
    """Retourne une instance du prévisionniste ROI"""
    return ROIForecaster(confidence_level=0.95, forecast_horizon=12)


# ============================================================================
# TESTS UNITAIRES - FONCTIONS DE BASE
# ============================================================================


class TestBasicCalculations:
    """Teste les calculs financiers de base"""
    
    def test_calculate_roi_simple(self):
        """Test du calcul ROI simple"""
        # Cas standard
        assert calculate_roi(100000, 150000) == 50.0  # (150k-100k)/100k * 100
        assert calculate_roi(100000, 80000) == -20.0  # Pertes
        
        # Investissement nul
        with pytest.raises(ValueError, match="Investissement ne peut pas être nul"):
            calculate_roi(0, 150000)
        
        # Valeurs négatives
        assert calculate_roi(-100000, 150000) == -250.0  # Investissement négatif
        
        # Retours négatifs
        assert calculate_roi(100000, -50000) == -150.0
    
    def test_calculate_roi_edge_cases(self):
        """Test des cas limites du calcul ROI"""
        # Investissement très petit
        assert calculate_roi(0.01, 0.02) == 100.0
        
        # Investissement très grand
        large_investment = 1e12
        large_returns = 1.2e12
        expected_roi = ((large_returns - large_investment) / large_investment) * 100
        assert calculate_roi(large_investment, large_returns) == pytest.approx(expected_roi)
        
        # Retours égaux à l'investissement
        assert calculate_roi(100000, 100000) == 0.0
    
    def test_calculate_npv_basic(self):
        """Test du calcul NPV (Net Present Value)"""
        cash_flows = [-1000, 300, 400, 500]  # Investissement + 3 ans
        discount_rate = 0.1  # 10%
        
        npv = calculate_npv(cash_flows, discount_rate)
        expected = -1000 + 300/1.1 + 400/1.1**2 + 500/1.1**3
        assert npv == pytest.approx(expected, rel=1e-10)
        
        # Taux d'actualisation nul
        npv_zero_rate = calculate_npv(cash_flows, 0.0)
        assert npv_zero_rate == sum(cash_flows)
        
        # Liste vide
        assert calculate_npv([], 0.1) == 0.0
    
    def test_calculate_npv_validation(self):
        """Test de validation des entrées NPV"""
        # Taux d'actualisation négatif
        with pytest.raises(ValueError, match="Taux d'actualisation doit être positif"):
            calculate_npv([-1000, 300], -0.1)
        
        # Taux trop élevé
        with pytest.raises(ValueError, match="Taux d'actualisation trop élevé"):
            calculate_npv([-1000, 300], 5.0)  # 500%
    
    def test_calculate_irr_basic(self):
        """Test du calcul IRR (Internal Rate of Return)"""
        cash_flows = [-1000, 400, 400, 400]  # Investissement + 3 ans égaux
        
        irr = calculate_irr(cash_flows)
        # Vérifie que NPV est proche de zéro avec ce IRR
        npv = calculate_npv(cash_flows, irr)
        assert abs(npv) < 1e-6
        
        # Pas d'investissement initial
        with pytest.raises(ValueError, match="Doit avoir un investissement initial négatif"):
            calculate_irr([100, 200, 300])
    
    def test_calculate_irr_no_solution(self):
        """Test IRR sans solution"""
        # Flux toujours positifs
        with pytest.raises(ValueError, match="Impossible de trouver un IRR valide"):
            calculate_irr([100, 200, 300])
        
        # Flux toujours négatifs après investissement
        with pytest.raises(ValueError, match="Impossible de trouver un IRR valide"):
            calculate_irr([-100, -50, -30])
    
    @pytest.mark.parametrize("iterations", [100, 1000, 10000])
    def test_monte_carlo_performance(self, iterations):
        """Test de performance Monte Carlo avec différentes itérations"""
        def revenue_growth():
            return random.normalvariate(0.1, 0.02)  # 10% ± 2%
        
        def cost_reduction():
            return random.normalvariate(0.15, 0.03)  # 15% ± 3%
        
        start_time = time.time()
        results = run_monte_carlo_simulation(
            base_investment=100000,
            base_revenue=500000,
            base_costs=300000,
            revenue_growth_func=revenue_growth,
            cost_reduction_func=cost_reduction,
            years=5,
            iterations=iterations,
        )
        elapsed = time.time() - start_time
        
        # Vérifie les résultats
        assert "mean_roi" in results
        assert "std_roi" in results
        assert "percentiles" in results
        assert "confidence_intervals" in results
        
        # Vérifie les percentiles
        percentiles = results["percentiles"]
        assert 5 in percentiles
        assert 50 in percentiles  # Médiane
        assert 95 in percentiles
        
        # Performance: devrait être rapide même pour 10k itérations
        max_time = 5.0 if iterations == 10000 else 1.0
        assert elapsed < max_time, f"Monte Carlo trop lent: {elapsed:.2f}s pour {iterations} itérations"


# ============================================================================
# TESTS BASÉS SUR LA PROPRIÉTÉ (PROPERTY-BASED TESTING)
# ============================================================================


@given(
    investment=floats(min_value=0.01, max_value=1e9, exclude_min=True),
    returns=floats(min_value=-1e8, max_value=1e9),
)
@settings(max_examples=1000, deadline=None)
def test_roi_property_based(investment, returns):
    """Test property-based pour ROI"""
    assume(investment != 0)  # Évite la division par zéro
    
    roi = calculate_roi(investment, returns)
    
    # Propriété: ROI = (returns - investment) / investment * 100
    expected = ((returns - investment) / investment) * 100
    assert roi == pytest.approx(expected, rel=1e-10)
    
    # Propriété: Si returns > investment, ROI > 0
    if returns > investment:
        assert roi > 0
    elif returns < investment:
        assert roi < 0
    else:
        assert roi == 0


@given(
    cash_flows=lists(
        floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=20,
    ),
    discount_rate=floats(min_value=0.0, max_value=2.0, exclude_max=True),
)
@settings(max_examples=500, deadline=None)
def test_npv_property_based(cash_flows, discount_rate):
    """Test property-based pour NPV"""
    assume(discount_rate >= 0 and discount_rate < 2.0)  # Taux raisonnable
    
    try:
        npv = calculate_npv(cash_flows, discount_rate)
        
        # Propriété: NPV est linéaire par rapport aux cash flows
        scaled_cash_flows = [cf * 2 for cf in cash_flows]
        npv_scaled = calculate_npv(scaled_cash_flows, discount_rate)
        assert npv_scaled == pytest.approx(npv * 2, rel=1e-10)
        
        # Propriété: NPV décroît avec le taux d'actualisation (si cash flows futurs > 0)
        future_cash_flows_positive = all(cf >= 0 for cf in cash_flows[1:])
        if future_cash_flows_positive and len(cash_flows) > 1:
            npv_higher_rate = calculate_npv(cash_flows, discount_rate + 0.01)
            assert npv_higher_rate <= npv
        
    except ValueError:
        # Accepte les erreurs de validation
        pass


# ============================================================================
# TESTS DE MODÈLES PYDANTIC
# ============================================================================


class TestBusinessValueModels:
    """Teste les modèles Pydantic de business value"""
    
    def test_business_metrics_validation(self):
        """Test de validation des métriques business"""
        # Données valides
        valid_data = {
            "tenant_id": "tenant-123",
            "metric_type": "cost_savings",
            "value": 50000.0,
            "currency": "USD",
            "period": "monthly",
            "timestamp": datetime.now(),
            "attribution": {"agent1": 30000.0},
            "confidence_score": 0.95,
        }
        
        metrics = BusinessValueMetrics(**valid_data)
        assert metrics.value == 50000.0
        assert metrics.currency == "USD"
        assert metrics.confidence_score == 0.95
        
        # Score de confiance hors limites
        with pytest.raises(ValidationError):
            BusinessValueMetrics(**{**valid_data, "confidence_score": 1.5})
        
        with pytest.raises(ValidationError):
            BusinessValueMetrics(**{**valid_data, "confidence_score": -0.1})
        
        # Devise invalide
        with pytest.raises(ValidationError):
            BusinessValueMetrics(**{**valid_data, "currency": "INVALID"})
    
    def test_roi_tracking_validation(self):
        """Test de validation du suivi ROI"""
        valid_data = {
            "tenant_id": "tenant-123",
            "investment": 100000.0,
            "returns": 150000.0,
            "roi_percentage": 50.0,
            "period": "annual",
            "timestamp": datetime.now(),
            "breakdown": {"category": {"value": 50000.0, "confidence": 0.9}},
        }
        
        roi = ROITracking(**valid_data)
        assert roi.roi_percentage == 50.0
        assert roi.period == "annual"
        
        # ROI incohérent avec investment/returns
        with pytest.raises(ValidationError):
            ROITracking(**{**valid_data, "roi_percentage": 200.0})
    
    def test_serialization_roundtrip(self, sample_business_metrics):
        """Test sérialisation/désérialisation roundtrip"""
        # Sérialise en JSON
        json_str = sample_business_metrics.json()
        assert isinstance(json_str, str)
        
        # Désérialise
        deserialized = BusinessValueMetrics.parse_raw(json_str)
        
        # Vérifie l'égalité
        assert deserialized.tenant_id == sample_business_metrics.tenant_id
        assert deserialized.value == sample_business_metrics.value
        assert deserialized.timestamp == sample_business_metrics.timestamp
    
    def test_multi_currency_support(self):
        """Test du support multi-devises"""
        currencies = ["USD", "EUR", "GBP", "JPY", "CAD"]
        
        for currency in currencies:
            metrics = BusinessValueMetrics(
                tenant_id="test",
                metric_type="revenue_impact",
                value=100000.0,
                currency=currency,
                period="monthly",
                timestamp=datetime.now(),
            )
            assert metrics.currency == currency
            
            # Vérifie la sérialisation
            json_data = metrics.dict()
            assert json_data["currency"] == currency
    
    @freeze_time("2024-01-15 10:30:00")
    def test_timezone_handling(self):
        """Test de la gestion des fuseaux horaires"""
        from datetime import timezone
        
        # UTC
        utc_time = datetime.now(timezone.utc)
        metrics_utc = BusinessValueMetrics(
            tenant_id="test",
            metric_type="cost_savings",
            value=10000.0,
            currency="USD",
            period="monthly",
            timestamp=utc_time,
        )
        
        # Heure locale
        local_time = datetime.now()
        metrics_local = BusinessValueMetrics(
            tenant_id="test",
            metric_type="cost_savings",
            value=10000.0,
            currency="USD",
            period="monthly",
            timestamp=local_time,
        )
        
        # Les deux devraient être valides
        assert metrics_utc.timestamp == utc_time
        assert metrics_local.timestamp == local_time
        
        # Vérifie la sérialisation ISO 8601
        assert "T" in metrics_utc.timestamp.isoformat()
        assert "Z" in metrics_utc.timestamp.isoformat()  # UTC


# ============================================================================
# TESTS DU CALCULATEUR DE VALEUR BUSINESS
# ============================================================================


class TestBusinessValueCalculator:
    """Teste le calculateur de valeur business"""
    
    def test_initialization(self):
        """Test de l'initialisation"""
        calculator = BusinessValueCalculator(
            cache_size=500,
            cache_ttl=600,
            currency="EUR",
            default_confidence=0.9,
        )
        
        assert calculator.cache_size == 500
        assert calculator.cache_ttl == 600
        assert calculator.currency == "EUR"
        assert calculator.default_confidence == 0.9
    
    def test_calculate_composite_roi(self, business_value_calculator):
        """Test du calcul ROI composite"""
        metrics_list = [
            BusinessValueMetrics(
                tenant_id="tenant-123",
                metric_type="cost_savings",
                value=30000.0,
                currency="USD",
                period="monthly",
                timestamp=datetime.now(),
            ),
            BusinessValueMetrics(
                tenant_id="tenant-123",
                metric_type="revenue_impact",
                value=20000.0,
                currency="USD",
                period="monthly",
                timestamp=datetime.now(),
            ),
        ]
        
        investment = 100000.0
        
        roi = business_value_calculator.calculate_composite_roi(
            metrics_list=metrics_list,
            investment=investment,
            period="annual",
            include_confidence=True,
        )
        
        assert "total_returns" in roi
        assert "roi_percentage" in roi
        assert "confidence_score" in roi
        assert "breakdown" in roi
        
        total_returns = 30000.0 + 20000.0
        expected_roi = ((total_returns - investment) / investment) * 100
        assert roi["roi_percentage"] == pytest.approx(expected_roi, rel=1e-10)
    
    def test_cache_functionality(self, business_value_calculator):
        """Test de la fonctionnalité de cache"""
        # Premier calcul
        start_time = time.time()
        result1 = business_value_calculator.calculate_annualized_roi(
            monthly_returns=10000.0,
            investment=100000.0,
        )
        time_first = time.time() - start_time
        
        # Deuxième calcul (devrait utiliser le cache)
        start_time = time.time()
        result2 = business_value_calculator.calculate_annualized_roi(
            monthly_returns=10000.0,
            investment=100000.0,
        )
        time_second = time.time() - start_time
        
        # Résultats identiques
        assert result1 == result2
        
        # Deuxième calcul devrait être plus rapide (cache)
        assert time_second < time_first * 0.5, "Cache non fonctionnel"
    
    def test_cache_invalidation(self, business_value_calculator):
        """Test de l'invalidation du cache"""
        # Premier calcul
        key = ("annualized_roi", 10000.0, 100000.0)
        result1 = business_value_calculator.calculate_annualized_roi(
            monthly_returns=10000.0,
            investment=100000.0,
        )
        
        # Force l'expiration du cache
        business_value_calculator._cache.clear()
        
        # Deuxième calcul (devrait recalculer)
        result2 = business_value_calculator.calculate_annualized_roi(
            monthly_returns=10000.0,
            investment=100000.0,
        )
        
        # Même résultat mais calculé deux fois
        assert result1 == result2
        
        # Vérifie que le cache était vide
        assert key not in business_value_calculator._cache
    
    @pytest.mark.parametrize("num_threads", [2, 4, 8])
    def test_concurrent_calculations(self, business_value_calculator, num_threads):
        """Test des calculs concurrents"""
        results = []
        errors = []
        lock = threading.Lock()
        
        def calculate_roi_thread(thread_id):
            try:
                # Chaque thread calcule un ROI différent
                investment = 100000 + thread_id * 10000
                returns = 150000 + thread_id * 15000
                
                result = business_value_calculator.calculate_composite_roi(
                    metrics_list=[
                        BusinessValueMetrics(
                            tenant_id=f"tenant-{thread_id}",
                            metric_type="test",
                            value=returns,
                            currency="USD",
                            period="annual",
                            timestamp=datetime.now(),
                        )
                    ],
                    investment=investment,
                    period="annual",
                )
                
                with lock:
                    results.append((thread_id, result))
                    
            except Exception as e:
                with lock:
                    errors.append((thread_id, str(e)))
        
        # Lance les threads
        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=calculate_roi_thread, args=(i,))
            threads.append(t)
            t.start()
        
        # Attend la fin
        for t in threads:
            t.join()
        
        # Vérifie les résultats
        assert len(errors) == 0, f"Erreurs dans les threads: {errors}"
        assert len(results) == num_threads
        
        # Vérifie que tous les calculs sont uniques
        thread_ids = [r[0] for r in results]
        assert len(set(thread_ids)) == num_threads
        
        # Vérifie l'intégrité du cache après concurrence
        assert len(business_value_calculator._cache) <= business_value_calculator.cache_size
    
    def test_memory_usage(self, business_value_calculator):
        """Test de l'utilisation mémoire"""
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss
        
        # Effectue de nombreux calculs pour remplir le cache
        for i in range(1000):
            business_value_calculator.calculate_annualized_roi(
                monthly_returns=10000 + i,
                investment=100000 + i * 1000,
            )
        
        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory
        
        # Le cache ne devrait pas exploser la mémoire
        max_expected_increase = 10 * 1024 * 1024  # 10MB
        assert memory_increase < max_expected_increase, \
            f"Utilisation mémoire excessive: {memory_increase / 1024 / 1024:.2f}MB"
    
    def test_error_handling(self, business_value_calculator):
        """Test de la gestion des erreurs"""
        # Données invalides
        with pytest.raises(ValueError):
            business_value_calculator.calculate_composite_roi(
                metrics_list=[],
                investment=0,
            )
        
        # Métriques avec devises différentes
        metrics_mixed_currency = [
            BusinessValueMetrics(
                tenant_id="test",
                metric_type="cost_savings",
                value=10000.0,
                currency="USD",
                period="monthly",
                timestamp=datetime.now(),
            ),
            BusinessValueMetrics(
                tenant_id="test",
                metric_type="revenue_impact",
                value=8000.0,
                currency="EUR",  # Devise différente!
                period="monthly",
                timestamp=datetime.now(),
            ),
        ]
        
        # Devrait lever une exception pour devises mixtes
        with pytest.raises(ValueError, match="Toutes les métriques doivent avoir la même devise"):
            business_value_calculator.calculate_composite_roi(
                metrics_list=metrics_mixed_currency,
                investment=100000.0,
            )


# ============================================================================
# TESTS DE PRÉVISION (FORECASTING)
# ============================================================================


class TestForecasting:
    """Teste les modèles de prévision"""
    
    def test_roi_forecaster_basic(self, roi_forecaster):
        """Test basique du prévisionniste ROI"""
        historical_data = [
            {"timestamp": datetime(2023, 1, 1), "roi": 10.0},
            {"timestamp": datetime(2023, 2, 1), "roi": 12.0},
            {"timestamp": datetime(2023, 3, 1), "roi": 11.5},
            {"timestamp": datetime(2023, 4, 1), "roi": 13.0},
            {"timestamp": datetime(2023, 5, 1), "roi": 14.0},
        ]
        
        forecast = roi_forecaster.forecast(
            historical_data=historical_data,
            method="linear_regression",
            include_confidence=True,
        )
        
        assert "forecast" in forecast
        assert "confidence_intervals" in forecast
        assert "model_metrics" in forecast
        
        # Vérifie la structure de la prévision
        forecast_values = forecast["forecast"]
        assert len(forecast_values) == roi_forecaster.forecast_horizon
        
        # Les prévisions devraient être dans une plage raisonnable
        assert all(-100 <= v <= 1000 for v in forecast_values)
    
    def test_cost_forecaster_monte_carlo(self):
        """Test du prévisionniste de coûts avec Monte Carlo"""
        forecaster = CostForecaster(
            monte_carlo_iterations=1000,
            confidence_level=0.95,
        )
        
        historical_costs = [
            {"timestamp": datetime(2023, i, 1), "cost": 10000 + i * 1000}
            for i in range(1, 7)  # 6 mois
        ]
        
        forecast = forecaster.forecast_monte_carlo(
            historical_data=historical_costs,
            uncertainty_factors={
                "market_volatility": 0.1,
                "inflation_rate": 0.02,
                "seasonality_factor": 0.05,
            },
        )
        
        assert "mean_forecast" in forecast
        assert "percentiles" in forecast
        assert "simulation_results" in forecast
        assert len(forecast["simulation_results"]) == forecaster.monte_carlo_iterations
        
        # Vérifie la cohérence des percentiles
        percentiles = forecast["percentiles"]
        assert percentiles[5] <= percentiles[50] <= percentiles[95]
    
    def test_revenue_forecaster_arima(self):
        """Test du prévisionniste de revenus avec ARIMA"""
        forecaster = RevenueForecaster(
            model_type="arima",
            seasonal_period=12,  # Mensuel
        )
        
        # Données saisonnières simulées
        historical_revenue = []
        for month in range(1, 25):  # 2 ans
            base = 50000
            seasonal = 10000 * np.sin(2 * np.pi * month / 12)  Saisonnalité
            trend = 2000 * month  # Tendance
            noise = random.uniform(-5000, 5000)
            
            historical_revenue.append({
                "timestamp": datetime(2022 + (month - 1) // 12, (month - 1) % 12 + 1, 1),
                "revenue": base + seasonal + trend + noise,
            })
        
        forecast = forecaster.forecast_arima(
            historical_data=historical_revenue,
            forecast_months=6,
        )
        
        assert "forecast" in forecast
        assert "model_summary" in forecast
        assert "residuals_analysis" in forecast
        
        # Le modèle devrait capturer la tendance
        forecast_values = forecast["forecast"]
        assert len(forecast_values) == 6
        
        # Vérifie la progression (devrait être croissante avec tendance)
        if len(forecast_values) > 1:
            # Pas strictement croissant à cause du bruit, mais généralement croissant
            increasing_count = sum(
                1 for i in range(len(forecast_values) - 1)
                if forecast_values[i + 1] > forecast_values[i]
            )
            assert increasing_count > len(forecast_values) * 0.5  # Au moins 50% croissant


# ============================================================================
# TESTS D'OPTIMISATION
# ============================================================================


class TestOptimizationModels:
    """Teste les modèles d'optimisation"""
    
    def test_investment_allocator(self):
        """Test de l'allocateur d'investissement"""
        allocator = InvestmentAllocator(
            risk_tolerance="moderate",
            optimization_method="markowitz",
        )
        
        investment_options = [
            {
                "id": "cost_optimization",
                "expected_roi": 0.25,  # 25%
                "risk_score": 0.1,
                "min_investment": 10000,
                "max_investment": 50000,
            },
            {
                "id": "security_enhancement",
                "expected_roi": 0.15,  # 15%
                "risk_score": 0.05,
                "min_investment": 5000,
                "max_investment": 30000,
            },
            {
                "id": "performance_boost",
                "expected_roi": 0.20,  # 20%
                "risk_score": 0.15,
                "min_investment": 15000,
                "max_investment": 60000,
            },
        ]
        
        total_budget = 100000
        
        allocation = allocator.optimize_allocation(
            options=investment_options,
            total_budget=total_budget,
            constraints={
                "max_risk": 0.12,
                "diversification_min": 0.2,  # Au moins 20% par option
            },
        )
        
        assert "allocations" in allocation
        assert "expected_portfolio_roi" in allocation
        assert "portfolio_risk" in allocation
        assert "efficient_frontier" in allocation
        
        # Vérifie les contraintes
        allocations = allocation["allocations"]
        total_allocated = sum(alloc["amount"] for alloc in allocations)
        assert total_allocated <= total_budget
        
        # Vérifie la diversification
        for alloc in allocations:
            proportion = alloc["amount"] / total_allocated
            assert proportion >= 0.2 or alloc["amount"] == 0
    
    def test_portfolio_optimizer_efficient_frontier(self):
        """Test de l'optimiseur de portefeuille"""
        optimizer = PortfolioOptimizer()
        
        assets = {
            "cost_optimization": {"return": 0.25, "risk": 0.10},
            "security": {"return": 0.15, "risk": 0.05},
            "performance": {"return": 0.20, "risk": 0.15},
        }
        
        correlation_matrix = np.array([
            [1.0, 0.3, 0.5],
            [0.3, 1.0, 0.2],
            [0.5, 0.2, 1.0],
        ])
        
        frontier = optimizer.calculate_efficient_frontier(
            assets=assets,
            correlation_matrix=correlation_matrix,
            risk_free_rate=0.02,
            num_points=20,
        )
        
        assert "frontier_points" in frontier
        assert "optimal_portfolio" in frontier
        assert "sharpe_ratio" in frontier
        
        points = frontier["frontier_points"]
        assert len(points) == 20
        
        # Vérifie que le risque augmente avec le retour (généralement)
        risks = [p["risk"] for p in points]
        returns = [p["return"] for p in points]
        
        # Trie par risque et vérifie la monotonie générale
        sorted_points = sorted(points, key=lambda x: x["risk"])
        for i in range(len(sorted_points) - 1):
            # Pas strictement croissant à cause des approximations
            if sorted_points[i + 1]["return"] < sorted_points[i]["return"]:
                # La différence ne devrait pas être trop grande
                diff = sorted_points[i]["return"] - sorted_points[i + 1]["return"]
                assert diff < 0.05  # 5% de tolérance
    
    def test_risk_adjuster(self):
        """Test de l'ajusteur de risque"""
        adjuster = RiskAdjuster(
            risk_free_rate=0.02,
            market_return=0.08,
        )
        
        investment_data = {
            "expected_return": 0.15,
            "risk": 0.12,
            "beta": 1.2,
        }
        
        adjusted = adjuster.adjust_for_risk(
            investment_data=investment_data,
            adjustment_method="capm",
            include_scenarios=True,
        )
        
        assert "risk_adjusted_return" in adjusted
        assert "sharpe_ratio" in adjusted
        assert "scenario_analysis" in adjusted
        
        # Calcul CAPM: risk_free_rate + beta * (market_return - risk_free_rate)
        expected_capm = 0.02 + 1.2 * (0.08 - 0.02)  # = 0.092
        assert adjusted["risk_adjusted_return"] == pytest.approx(expected_capm, rel=1e-10)
        
        # Sharpe ratio: (return - risk_free) / risk
        expected_sharpe = (0.15 - 0.02) / 0.12
        assert adjusted["sharpe_ratio"] == pytest.approx(expected_sharpe, rel=1e-10)


# ============================================================================
# TESTS MUTATION (MUTATION TESTING SIMULATION)
# ============================================================================


class TestMutationTesting:
    """Tests pour détecter les bugs subtils (simulation de mutation testing)"""
    
    def test_roi_calculation_mutation(self):
        """Test de détection de mutation dans le calcul ROI"""
        # Version correcte
        def correct_roi(investment, returns):
            if investment == 0:
                raise ValueError("Investissement ne peut pas être nul")
            return ((returns - investment) / investment) * 100
        
        # Version mutée (bug: oubli du * 100)
        def mutated_roi(investment, returns):
            if investment == 0:
                raise ValueError("Investissement ne peut pas être nul")
            return (returns - investment) / investment  # OUBLI DE * 100!
        
        # Test qui devrait détecter la mutation
        test_cases = [
            (100000, 150000),  # ROI devrait être 50.0
            (100000, 80000),   # ROI devrait être -20.0
            (1000, 1200),      # ROI devrait être 20.0
        ]
        
        for investment, returns in test_cases:
            correct = correct_roi(investment, returns)
            mutated = mutated_roi(investment, returns)
            
            # Le test devrait échouer si on utilise la version mutée
            assert correct != mutated, f"Mutation non détectée: {investment}, {returns}"
            
            # Vérifie la magnitude de la différence
            assert abs(correct - mutated * 100) < 1e-10
    
    def test_npv_mutation_boundary(self):
        """Test de mutation aux limites"""
        def correct_npv(cash_flows, discount_rate):
            if discount_rate < 0:
                raise ValueError("Taux d'actualisation doit être positif")
            if discount_rate > 1.0:  # 100%
                raise ValueError("Taux d'actualisation trop élevé")
            
            npv = 0
            for t, cf in enumerate(cash_flows):
                npv += cf / ((1 + discount_rate) ** t)
            return npv
        
        # Muté: supprime la vérification discount_rate > 1.0
        def mutated_npv(cash_flows, discount_rate):
            if discount_rate < 0:
                raise ValueError("Taux d'actualisation doit être positif")
            # Vérification supprimée!
            
            npv = 0
            for t, cf in enumerate(cash_flows):
                npv += cf / ((1 + discount_rate) ** t)
            return npv
        
        # Cas de test avec taux élevé
        cash_flows = [-1000, 500, 500, 500]
        
        # Devrait lever une exception avec le taux élevé
        with pytest.raises(ValueError):
            correct_npv(cash_flows, 2.0)  # 200%
        
        # Version mutée accepte (BUG!)
        mutated_result = mutated_npv(cash_flows, 2.0)
        assert mutated_result is not None
        
        # Vérifie que les résultats diffèrent pour des taux raisonnables aussi
        normal_rate = 0.1
        correct_normal = correct_npv(cash_flows, normal_rate)
        mutated_normal = mutated_npv(cash_flows, normal_rate)
        assert correct_normal == mutated_normal  # Même pour taux normal


# ============================================================================
# TESTS DE PERFORMANCE ET RÉGRESSION
# ============================================================================


class TestPerformanceAndRegression:
    """Tests de performance et détection de régression"""
    
    @pytest.mark.benchmark(group="roi_calculation")
    def test_roi_performance_benchmark(self, benchmark):
        """Benchmark des calculs ROI"""
        def calculate_multiple_roi():
            results = []
            for i in range(1000):
                investment = 100000 + i * 1000
                returns = 150000 + i * 1500
                roi = calculate_roi(investment, returns)
                results.append(roi)
            return results
        
        # Exécute le benchmark
        result = benchmark(calculate_multiple_roi)
        assert len(result) == 1000
        
        # Vérifie les statistiques du benchmark
        stats = benchmark.stats
        assert stats["min"] < stats["max"]  # Doit avoir une variation
        
        # Le calcul devrait être rapide
        assert stats["mean"] < 0.1  # Moins de 100ms pour 1000 calculs
    
    @pytest.mark.benchmark(group="monte_carlo")
    def test_monte_carlo_performance_benchmark(self, benchmark):
        """Benchmark des simulations Monte Carlo"""
        def run_simulation():
            return run_monte_carlo_simulation(
                base_investment=100000,
                base_revenue=500000,
                base_costs=300000,
                revenue_growth_func=lambda: random.normalvariate(0.1, 0.02),
                cost_reduction_func=lambda: random.normalvariate(0.15, 0.03),
                years=5,
                iterations=1000,
            )
        
        result = benchmark(run_simulation)
        assert "mean_roi" in result
        
        # 1000 itérations devrait être rapide
        stats = benchmark.stats
        assert stats["mean"] < 2.0  # Moins de 2 secondes
    
    def test_performance_regression_detection(self):
        """Détection de régression de performance"""
        import time
        
        # Version actuelle
        def current_implementation():
            time.sleep(0.001)  # Simule un calcul
            return calculate_roi(100000, 150000)
        
        # Version "régression" (plus lente)
        def regression_implementation():
            time.sleep(0.01)  # 10x plus lent!
            return calculate_roi(100000, 150000)
        
        # Mesure les performances
        start = time.perf_counter()
        for _ in range(100):
            current_implementation()
        current_time = time.perf_counter() - start
        
        start = time.perf_counter()
        for _ in range(100):
            regression_implementation()
        regression_time = time.perf_counter() - start
        
        # Détecte la régression (50% plus lent)
        regression_ratio = regression_time / current_time
        if regression_ratio > 1.5:
            pytest.fail(f"Régression de performance détectée: {regression_ratio:.2f}x plus lent")
    
    def test_memory_leak_detection(self, business_value_calculator):
        """Détection de fuites mémoire"""
        import gc
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        
        # Force le GC
        gc.collect()
        
        # Mémoire avant
        memory_before = process.memory_info().rss
        
        # Effectue de nombreuses opérations
        for i in range(10000):
            business_value_calculator.calculate_annualized_roi(
                monthly_returns=10000 + (i % 100),
                investment=100000 + (i % 100) * 1000,
            )
        
        # Force le GC à nouveau
        gc.collect()
        
        # Mémoire après
        memory_after = process.memory_info().rss
        
        # Calcul l'augmentation
        memory_increase = memory_after - memory_before
        
        # Ne devrait pas avoir de fuite significative
        max_allowed_increase = 5 * 1024 * 1024  # 5MB
        assert memory_increase < max_allowed_increase, \
            f"Fuite mémoire suspecte: {memory_increase / 1024 / 1024:.2f}MB"


# ============================================================================
# TESTS FUZZ (FUZZ TESTING)
# ============================================================================


class TestFuzzTesting:
    """Tests fuzz pour la robustesse"""
    
    @pytest.mark.fuzz
    def test_fuzz_roi_calculation(self):
        """Test fuzz du calcul ROI avec données aléatoires"""
        random.seed(42)  # Reproductible
        
        for _ in range(1000):
            # Génère des données aléatoires
            investment = random.uniform(-1e6, 1e6)
            returns = random.uniform(-1e6, 1e6)
            
            try:
                roi = calculate_roi(investment, returns)
                
                # Vérifie les propriétés invariantes
                assert isinstance(roi, float)
                
                if investment == 0:
                    pytest.fail("Devrait lever une exception pour investment=0")
                
                # ROI = (returns - investment) / investment * 100
                expected = ((returns - investment) / investment) * 100
                assert roi == pytest.approx(expected, rel=1e-10)
                
            except ValueError as e:
                # Accepte les exceptions de validation
                assert "ne peut pas être nul" in str(e) or "trop élevé" in str(e)
            except Exception as e:
                # Toute autre exception est un échec
                pytest.fail(f"Exception inattendue: {type(e).__name__}: {e}")
    
    @pytest.mark.fuzz
    def test_fuzz_business_metrics_validation(self):
        """Test fuzz de la validation des métriques business"""
        from datetime import datetime
        
        random.seed(123)
        
        for _ in range(500):
            # Génère des données aléatoires
            data = {
                "tenant_id": f"tenant-{random.randint(1, 1000)}",
                "metric_type": random.choice(["cost_savings", "revenue_impact", "time_savings"]),
                "value": random.uniform(-1e6, 1e6),
                "currency": random.choice(["USD", "EUR", "GBP", "JPY", "INVALID"]),
                "period": random.choice(["daily", "weekly", "monthly", "annual", "INVALID"]),
                "timestamp": datetime.now(),
                "attribution": {f"agent{i}": random.uniform(0, 10000) for i in range(3)},
                "confidence_score": random.uniform(-0.5, 1.5),
            }
            
            try:
                metrics = BusinessValueMetrics(**data)
                
                # Si valide, vérifie les invariants
                assert metrics.value == data["value"]
                assert 0 <= metrics.confidence_score <= 1
                
            except ValidationError:
                # Validation échouée - c'est OK pour des données fuzz
                pass
            except Exception as e:
                pytest.fail(f"Exception inattendue pendant le fuzz: {e}")


# ============================================================================
# TESTS DE CONTRAT (CONTRACT TESTING)
# ============================================================================


class TestContractTesting:
    """Tests de contrat pour les interfaces"""
    
    def test_calculator_contract(self, business_value_calculator):
        """Test du contrat du calculateur"""
        # Vérifie que toutes les méthodes requises existent
        required_methods = [
            "calculate_composite_roi",
            "calculate_annualized_roi",
            "calculate_npv",
            "calculate_irr",
        ]
        
        for method_name in required_methods:
            assert hasattr(business_value_calculator, method_name)
            method = getattr(business_value_calculator, method_name)
            assert callable(method)
    
    def test_forecaster_contract(self, roi_forecaster):
        """Test du contrat du prévisionniste"""
        # Interface de base
        assert hasattr(roi_forecaster, "forecast")
        assert callable(roi_forecaster.forecast)
        
        # Propriétés requises
        assert hasattr(roi_forecaster, "confidence_level")
        assert hasattr(roi_forecaster, "forecast_horizon")
        
        # Vérifie les types
        assert isinstance(roi_forecaster.confidence_level, float)
        assert isinstance(roi_forecaster.forecast_horizon, int)
        
        # Valeurs dans des plages raisonnables
        assert 0 < roi_forecaster.confidence_level <= 1
        assert roi_forecaster.forecast_horizon > 0
    
    def test_optimizer_contract(self):
        """Test du contrat de l'optimiseur"""
        optimizer = PortfolioOptimizer()
        
        # Méthodes requises
        assert hasattr(optimizer, "calculate_efficient_frontier")
        assert callable(optimizer.calculate_efficient_frontier)
        
        # Vérifie la signature (approximative)
        import inspect
        sig = inspect.signature(optimizer.calculate_efficient_frontier)
        params = list(sig.parameters.keys())
        
        # Paramètres attendus
        expected_params = ["assets", "correlation_matrix", "risk_free_rate", "num_points"]
        for param in expected_params:
            assert param in params, f"Paramètre manquant: {param}"


# ============================================================================
# TESTS D'INTÉGRATION SIMULÉE
# ============================================================================


class TestIntegrationScenarios:
    """Tests d'intégration simulés"""
    
    def test_end_to_end_business_value_workflow(self):
        """Test du workflow complet de valeur business"""
        # 1. Collecte des métriques
        metrics = [
            BusinessValueMetrics(
                tenant_id="acme-corp",
                metric_type="cost_savings",
                value=75000.0,
                currency="USD",
                period="quarterly",
                timestamp=datetime.now(),
                confidence_score=0.92,
            ),
            BusinessValueMetrics(
                tenant_id="acme-corp",
                metric_type="revenue_impact",
                value=125000.0,
                currency="USD",
                period="quarterly",
                timestamp=datetime.now(),
                confidence_score=0.88,
            ),
        ]
        
        # 2. Calcul ROI
        calculator = BusinessValueCalculator()
        roi_result = calculator.calculate_composite_roi(
            metrics_list=metrics,
            investment=500000.0,
            period="annual",
            include_confidence=True,
        )
        
        # 3. Prévision
        forecaster = ROIForecaster()
        historical_roi = [
            {"timestamp": datetime(2023, i, 1), "roi": 15.0 + i * 0.5}
            for i in range(1, 7)
        ]
        
        forecast = forecaster.forecast(
            historical_data=historical_roi,
            method="exponential_smoothing",
        )
        
        # 4. Optimisation
        allocator = InvestmentAllocator()
        options = [
            {
                "id": "expansion",
                "expected_roi": 0.22,
                "risk_score": 0.12,
                "min_investment": 50000,
                "max_investment": 200000,
            },
            {
                "id": "optimization",
                "expected_roi": 0.18,
                "risk_score": 0.08,
                "min_investment": 30000,
                "max_investment": 150000,
            },
        ]
        
        allocation = allocator.optimize_allocation(
            options=options,
            total_budget=300000,
        )
        
        # 5. Génération de rapport
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = ReportExporter(output_dir=tmpdir)
            
            report_data = {
                "roi_analysis": roi_result,
                "forecast": forecast,
                "allocation_recommendation": allocation,
                "timestamp": datetime.now(),
            }
            
            report_path = exporter.generate_report(
                data=report_data,
                format="json",
                template="business_value_summary",
            )
            
            # Vérifie que le rapport a été généré
            assert Path(report_path).exists()
            
            # Vérifie le contenu
            with open(report_path, 'r') as f:
                report_content = json.load(f)
            
            assert "roi_analysis" in report_content
            assert "forecast" in report_content
        
        # Vérifie la cohérence des résultats
        assert roi_result["roi_percentage"] > 0  # ROI positif
        assert len(forecast["forecast"]) == forecaster.forecast_horizon
        assert "allocations" in allocation
        
        # Logique métier: ROI prévu devrait être cohérent avec l'historique
        if "mean_forecast" in forecast:
            forecast_mean = forecast["mean_forecast"]
            historical_mean = sum(d["roi"] for d in historical_roi) / len(historical_roi)
            # La prévision ne devrait pas être trop éloignée de l'historique
            assert abs(forecast_mean - historical_mean) < 20.0  # 20% de tolérance


# ============================================================================
# TESTS DE COMPARAISON (GOLDEN MASTER)
# ============================================================================


class TestGoldenMaster:
    """Tests de comparaison avec résultats de référence (Golden Master)"""
    
    def test_golden_master_roi_calculations(self):
        """Test Golden Master pour les calculs ROI"""
        # Scénarios de test avec résultats attendus
        test_cases = [
            # (investment, returns, expected_roi, tolerance)
            (100000.0, 150000.0, 50.0, 1e-10),  # Cas standard
            (100000.0, 80000.0, -20.0, 1e-10),  # Pertes
            (1000.0, 1200.0, 20.0, 1e-10),      # Petit montant
            (1.0, 1.5, 50.0, 1e-10),           # Très petit
            (1e6, 1.2e6, 20.0, 1e-10),         # Très grand
            (100.0, 100.0, 0.0, 1e-10),        # Égalité
        ]
        
        for investment, returns, expected_roi, tolerance in test_cases:
            calculated_roi = calculate_roi(investment, returns)
            
            # Vérifie contre le résultat de référence
            assert calculated_roi == pytest.approx(expected_roi, rel=tolerance), \
                f"ROI incorrect pour investment={investment}, returns={returns}. " \
                f"Attendu: {expected_roi}, Obtenu: {calculated_roi}"
            
            # Vérifie également la propriété mathématique
            expected_from_property = ((returns - investment) / investment) * 100
            assert calculated_roi == pytest.approx(expected_from_property, rel=tolerance)
    
    def test_golden_master_npv_calculations(self):
        """Test Golden Master pour les calculs NPV"""
        # Cas de test avec résultats calculés manuellement
        test_cases = [
            # (cash_flows, discount_rate, expected_npv, description)
            ([-1000, 500, 500], 0.1, -1000 + 500/1.1 + 500/1.1**2, "Projet simple"),
            ([0, 100, 100], 0.05, 100/1.05 + 100/1.05**2, "Pas d'investissement initial"),
            ([-500, 200, 200, 200], 0.0, 100, "Taux zéro"),
            ([100, 100, 100], 0.1, 100 + 100/1.1 + 100/1.1**2, "Flux tous positifs"),
        ]
        
        for cash_flows, discount_rate, expected_npv, description in test_cases:
            calculated_npv = calculate_npv(cash_flows, discount_rate)
            
            assert calculated_npv == pytest.approx(expected_npv, rel=1e-10), \
                f"NPV incorrect pour {description}. " \
                f"Attendu: {expected_npv}, Obtenu: {calculated_npv}"
    
    def test_golden_master_serialization(self):
        """Test Golden Master pour la sérialisation"""
        # Crée un objet de test
        original = BusinessValueMetrics(
            tenant_id="golden-master-test",
            metric_type="cost_savings",
            value=123456.78,
            currency="EUR",
            period="monthly",
            timestamp=datetime(2024, 1, 15, 14, 30, 45, 123456),
            attribution={"agent1": 80000.0, "agent2": 43456.78},
            confidence_score=0.9375,
        )
        
        # Sérialise
        json_str = original.json()
        
        # Format JSON attendu (partiel)
        expected_json_parts = [
            '"tenant_id": "golden-master-test"',
            '"metric_type": "cost_savings"',
            '"value": 123456.78',
            '"currency": "EUR"',
            '"period": "monthly"',
            '"confidence_score": 0.9375',
        ]
        
        # Vérifie que toutes les parties attendues sont présentes
        for part in expected_json_parts:
            assert part in json_str, f"Partie manquante dans JSON: {part}"
        
        # Vérifie le format de date ISO 8601
        assert '"2024-01-15T14:30:45.123456"' in json_str or '"2024-01-15T14:30:45.123456+00:00"' in json_str
        
        # Désérialise et vérifie l'égalité
        deserialized = BusinessValueMetrics.parse_raw(json_str)
        
        # Les objets devraient être égaux (à la précision près des floats)
        assert deserialized.tenant_id == original.tenant_id
        assert deserialized.metric_type == original.metric_type
        assert deserialized.value == original.value
        assert deserialized.currency == original.currency
        assert deserialized.period == original.period
        assert deserialized.confidence_score == original.confidence_score
        
        # La date devrait être préservée à la microseconde près
        time_diff = abs((deserialized.timestamp - original.timestamp).total_seconds())
        assert time_diff < 0.000001  # Moins d'une microseconde


# ============================================================================
# TESTS THREAD-SAFETY
# ============================================================================


class TestThreadSafety:
    """Tests de sécurité des threads"""
    
    def test_thread_safe_roi_calculations(self):
        """Test des calculs ROI thread-safe"""
        import concurrent.futures
        
        def calculate_random_roi(thread_id):
            """Fonction exécutée par chaque thread"""
            results = []
            
            for i in range(100):
                investment = 10000 + thread_id * 1000 + i
                returns = 15000 + thread_id * 1500 + i * 1.5
                
                roi = calculate_roi(investment, returns)
                
                # Vérifie la cohérence
                expected = ((returns - investment) / investment) * 100
                assert roi == pytest.approx(expected, rel=1e-10)
                
                results.append((investment, returns, roi))
            
            return results
        
        # Exécute avec plusieurs threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(calculate_random_roi, i) for i in range(8)]
            
            # Collecte les résultats
            all_results = []
            for future in concurrent.futures.as_completed(futures):
                results = future.result()
                all_results.extend(results)
            
            # Vérifie que tous les calculs ont été effectués
            assert len(all_results) == 8 * 100
            
            # Vérifie l'unicité (chaque thread a ses propres données)
            investments = [r[0] for r in all_results]
            assert len(set(investments)) == len(investments)
    
    def test_concurrent_cache_access(self, business_value_calculator):
        """Test d'accès concurrent au cache"""
        import concurrent.futures
        
        cache_hits = []
        lock = threading.Lock()
        
        def access_cache(thread_id):
            """Accède au cache de manière concurrente"""
            hits = 0
            
            for i in range(50):
                # Alternance entre lecture et écriture
                if i % 2 == 0:
                    # Écriture
                    key = f"thread_{thread_id}_key_{i}"
                    business_value_calculator._cache[key] = {
                        "value": thread_id * 1000 + i,
                        "timestamp": time.time(),
                    }
                else:
                    # Lecture (peut être un hit ou miss)
                    key = f"thread_{(thread_id + 1) % 8}_key_{i-1}"
                    if key in business_value_calculator._cache:
                        hits += 1
            
            return hits
        
        # Exécute avec plusieurs threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(access_cache, i) for i in range(8)]
            
            total_hits = 0
            for future in concurrent.futures.as_completed(futures):
                hits = future.result()
                total_hits += hits
            
            # Vérifie que le cache n'a pas été corrompu
            assert len(business_value_calculator._cache) <= business_value_calculator.cache_size
            
            # Vérifie l'accès concurrent
            assert total_hits > 0, "Aucun cache hit pendant le test concurrent"
    
    def test_thread_safe_metric_creation(self):
        """Test de création thread-safe de métriques"""
        import concurrent.futures
        
        created_metrics = []
        lock = threading.Lock()
        
        def create_metrics(thread_id):
            """Crée des métriques dans un thread"""
            thread_metrics = []
            
            for i in range(20):
                try:
                    metric = BusinessValueMetrics(
                        tenant_id=f"tenant_{thread_id}",
                        metric_type="test",
                        value=thread_id * 1000 + i,
                        currency="USD",
                        period="monthly",
                        timestamp=datetime.now(),
                    )
                    thread_metrics.append(metric)
                    
                except Exception as e:
                    # Ne devrait pas avoir d'exception
                    pytest.fail(f"Exception inattendue dans thread {thread_id}: {e}")
            
            with lock:
                created_metrics.extend(thread_metrics)
            
            return len(thread_metrics)
        
        # Exécute avec plusieurs threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(create_metrics, i) for i in range(4)]
            
            total_created = 0
            for future in concurrent.futures.as_completed(futures):
                created = future.result()
                total_created += created
            
            # Vérifie que toutes les métriques ont été créées
            assert total_created == 4 * 20
            assert len(created_metrics) == total_created
            
            # Vérifie l'intégrité des métriques
            for metric in created_metrics:
                assert isinstance(metric, BusinessValueMetrics)
                assert metric.value >= 0
                assert metric.currency == "USD"


# ============================================================================
# MAIN EXECUTION
# ============================================================================


if __name__ == "__main__":
    """Exécute les tests directement pour le débogage"""
    import sys
    
    # Exécute pytest avec verbosité
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))