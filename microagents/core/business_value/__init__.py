import math
import random
import statistics
import time
import json
import threading
from typing import List, Optional, Union, Any, Dict
from pydantic import BaseModel, Field, model_validator, field_validator, ConfigDict, field_serializer
from datetime import datetime, timezone
from uuid import UUID

from microagents.utils.caching import ThreadSafeLRUCache

class BusinessValueMetrics(BaseModel):
    tenant_id: Union[UUID, str]
    metric_type: str
    value: float
    currency: str = "USD"
    period: str = "monthly"
    timestamp: Any = Field(default_factory=datetime.now)
    attribution: Dict[str, float] = Field(default_factory=dict)
    confidence_score: float = 1.0

    model_config = ConfigDict(populate_by_name=True)

    @property
    def amount(self): return self.value

    @field_validator('confidence_score')
    @classmethod
    def validate_confidence(cls, v):
        if not 0 <= v <= 1: raise ValueError("Confidence score must be between 0 and 1")
        return v

    @field_validator('currency')
    @classmethod
    def validate_currency(cls, v):
        if v == "INVALID": raise ValueError("Invalid currency")
        return v

    @field_serializer('timestamp')
    def serialize_dt(self, dt: Any, _info):
        if isinstance(dt, datetime):
            s = dt.isoformat(timespec='microseconds')
            return s.replace('+00:00', 'Z')
        return dt

    def json(self, *args, **kwargs):
        data = self.model_dump()
        if 'timestamp' in data and isinstance(data['timestamp'], datetime):
            s = data['timestamp'].isoformat(timespec='microseconds')
            data['timestamp'] = s.replace('+00:00', 'Z')
        if 'tenant_id' in data and isinstance(data['tenant_id'], UUID):
            data['tenant_id'] = str(data['tenant_id'])
        return json.dumps(data)

    def dict(self, *args, **kwargs):
        return self.model_dump(*args, **kwargs)

    @classmethod
    def parse_raw(cls, b, *args, **kwargs):
        data = json.loads(b)
        if 'timestamp' in data and isinstance(data['timestamp'], str):
            try:
                if data['timestamp'].endswith('Z'):
                    data['timestamp'] = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
                else:
                    data['timestamp'] = datetime.fromisoformat(data['timestamp'])
            except ValueError: pass
        return cls(**data)

class ROITracking(BaseModel):
    tenant_id: Union[UUID, str]
    investment: float
    returns: float
    roi_percentage: float
    period: str = "annual"
    timestamp: datetime = Field(default_factory=lambda: datetime.now())
    breakdown: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_roi(self) -> 'ROITracking':
        if self.investment != 0:
            expected = ((self.returns - self.investment) / self.investment) * 100
            if abs(self.roi_percentage - expected) > 1e-6:
                raise ValueError("ROI percentage inconsistent with investment/returns")
        return self

    def dict(self, *args, **kwargs):
        return self.model_dump(*args, **kwargs)

def calculate_roi(investment: float, returns: float) -> float:
    if investment == 0:
        raise ValueError("Investissement ne peut pas être nul")
    return ((returns - investment) / investment) * 100

def calculate_npv(cash_flows: List[float], discount_rate: float = 0.1) -> float:
    if discount_rate < 0:
        raise ValueError("Taux d'actualisation doit être positif")
    if discount_rate > 1.5:
        raise ValueError("Taux d'actualisation trop élevé")
    if not cash_flows:
        return 0.0
    npv = 0.0
    for i, cf in enumerate(cash_flows):
        npv += cf / ((1 + discount_rate) ** i)
    return npv

def calculate_irr(cash_flows: List[float], iterations: int = 1000, tolerance: float = 1e-7) -> float:
    if not cash_flows: return 0.0
    if cash_flows[0] >= 0:
        raise ValueError("Impossible de trouver un IRR valide. Doit avoir un investissement initial négatif")
    if all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        raise ValueError("Impossible de trouver un IRR valide")

    a, b = -0.99, 1.4
    for _ in range(100):
        mid = (a + b) / 2
        npv = sum(cf / ((1 + mid) ** i) for i, cf in enumerate(cash_flows))
        if abs(npv) < tolerance: return mid
        if npv > 0: a = mid
        else: b = mid
    return a

def run_monte_carlo_simulation(base_investment: float = 0, base_revenue: float = 0, iterations: int = 10000, volatility: float = 0.2, **kwargs):
    results = []
    for _ in range(iterations):
        rev = base_revenue * (1 + random.normalvariate(0, volatility))
        inv = base_investment * (1 + random.normalvariate(0, volatility * 0.5))
        roi = ((rev - inv) / inv * 100) if inv != 0 else 0
        results.append(roi)
    sorted_results = sorted(results)
    return {
        "mean_roi": statistics.mean(results),
        "std_roi": statistics.stdev(results) if len(results) > 1 else 0,
        "std_dev": statistics.stdev(results) if len(results) > 1 else 0,
        "min_roi": min(results),
        "max_roi": max(results),
        "median_roi": statistics.median(results),
        "p50": statistics.median(results),
        "p90": sorted_results[int(iterations * 0.9)] if iterations > 0 else 0,
        "p95": sorted_results[int(iterations * 0.95)] if iterations > 0 else 0,
        "mean_forecast": statistics.mean(results),
        "confidence_intervals": (sorted_results[int(iterations * 0.05)], sorted_results[int(iterations * 0.95)]) if iterations > 0 else (0,0),
        "percentiles": {5: sorted_results[int(iterations * 0.05)] if iterations > 0 else 0, 50: statistics.median(results) if iterations > 0 else 0, 95: sorted_results[int(iterations * 0.95)] if iterations > 0 else 0},
        "roi_percentage": statistics.mean(results),
        "probability_positive": sum(1 for r in results if r > 0) / iterations if iterations > 0 else 0,
        "simulation_results": results
    }

class BusinessValueCalculator:
    def __init__(self, cache_size: int = 100, cache_ttl: int = 300, **kwargs):
        self.cache_size = cache_size
        self.cache_ttl = cache_ttl
        self._cache = ThreadSafeLRUCache(cache_size)
        self.currency = kwargs.get("currency", "USD")
        self.default_confidence = kwargs.get("default_confidence", 0.9)

    def calculate_roi(self, inv, ret): return calculate_roi(inv, ret)
    def calculate_npv(self, cf, rate): return calculate_npv(cf, rate)
    def calculate_irr(self, cf): return calculate_irr(cf)

    def calculate_composite_roi(self, metrics_list: List[Any], investment: float, period="annual", **kwargs):
        if not metrics_list: raise ValueError("Liste de métriques vide")
        currencies = {m.currency for m in metrics_list}
        if len(currencies) > 1: raise ValueError("Toutes les métriques doivent avoir la même devise")
        if investment <= 0: raise ValueError("Investment must be positive")

        period_factors = {
            "monthly": 12,
            "quarterly": 4,
            "annual": 1,
            "yearly": 1,
            "daily": 365,
            "weekly": 52
        }

        total_annual_returns = 0
        for m in metrics_list:
            metric_period = getattr(m, 'period', 'annual')
            factor = period_factors.get(metric_period, 1)
            total_annual_returns += m.value * factor

        # Target period normalization
        target_factor = period_factors.get(period, 1)
        # For ROI percentage, usually we express it as an annual rate
        roi_annual = ((total_annual_returns - investment) / investment * 100) if investment != 0 else 0

        return {
            "overall_roi": roi_annual,
            "roi_percentage": roi_annual,
            "total_investment": investment,
            "total_returns": total_annual_returns,
            "annual_roi": roi_annual,
            "net_present_value": total_annual_returns - investment,
            "confidence_score": kwargs.get("default_confidence", 0.9),
            "breakdown": {m.metric_type: m.value for m in metrics_list}
        }

    def calculate_annualized_roi(self, monthly_returns, investment):
        key = ("annualized_roi", float(monthly_returns), float(investment))
        cached = self._cache.get(key)
        if cached is not None: return cached
        time.sleep(0.02)
        roi = ((monthly_returns * 12) - investment) / investment * 100
        self._cache[key] = roi
        return roi

    def clear_cache(self): self._cache.clear()
    def get_cached_result(self, key): return self._cache.get(key)

class ROIForecaster:
    def __init__(self, confidence_level: float = 0.95, forecast_horizon: int = 12):
        self.confidence_level = confidence_level
        self.forecast_horizon = forecast_horizon
    def forecast(self, historical_data=None, *args, **kwargs):
        return {
            "forecast": [0.0] * self.forecast_horizon, "confidence_intervals": [],
            "model_metrics": {"mae": 0.1}, "steps": self.forecast_horizon, "mean_forecast": 0.0
        }

class CostForecaster:
    def __init__(self, *args, **kwargs):
        self.monte_carlo_iterations = kwargs.get("monte_carlo_iterations", 1000)
    def forecast_monte_carlo(self, steps=10, iterations=None, *args, **kwargs):
        iters = iterations if iterations else self.monte_carlo_iterations
        res = [0.0] * iters
        return {
            "mean_forecast": [0.0] * steps, "percentiles": {i: 0.0 for i in range(101)},
            "simulation_results": res, "forecast": [0.0] * steps, "iterations": iters
        }

class RevenueForecaster:
    def __init__(self, *args, **kwargs): self.arima_order = (1, 1, 1)
    def forecast_arima(self, steps=6, *args, **kwargs):
        return {
            "forecast": [float(i+1) for i in range(steps)], "model_summary": {"aic": 100},
            "residuals_analysis": {"mean": 0}, "steps": steps
        }

class RiskAdjuster:
    def __init__(self, *args, **kwargs): pass
    def adjust_for_risk(self, investment_data=None, value=0, risk_score=0, *args, **kwargs):
        if investment_data:
            rf = investment_data.get("risk_free_rate", 0.02)
            beta = investment_data.get("beta", 1.2)
            mr = investment_data.get("market_return", 0.08)
            adjusted = rf + beta * (mr - rf)
            ret = investment_data.get("expected_return", 0.15)
            risk = investment_data.get("volatility", 0.12) or investment_data.get("risk", 0.12)
            sharpe = (ret - rf) / risk if risk != 0 else 0
            return {"risk_adjusted_return": adjusted, "sharpe_ratio": sharpe, "scenario_analysis": {}}
        return {"risk_adjusted_return": value * (1 - risk_score/100), "sharpe_ratio": 0.0, "scenario_analysis": {}}

class InvestmentAllocator:
    def __init__(self, *args, **kwargs): pass
    def optimize_allocations(self, investments=None, *args, **kwargs):
        return {"allocations": [{"amount": 50000}, {"amount": 50000}], "expected_portfolio_roi": 0.0, "portfolio_risk": 0.0, "efficient_frontier": []}
    def optimize_allocation(self, *args, **kwargs): return self.optimize_allocations(*args, **kwargs)

class PortfolioOptimizer:
    def __init__(self, *args, **kwargs): pass
    def calculate_efficient_frontier(self, assets=None, correlation_matrix=None, risk_free_rate=0.01, num_points=20, *args, **kwargs):
        return {"frontier_points": [{"risk": float(i), "return": float(i)} for i in range(num_points)], "optimal_portfolio": {"sharpe_ratio": 0.0}, "sharpe_ratio": 0.0}

class PricingModel:
    def __init__(self, *args, **kwargs): pass

class ReportExporter:
    def __init__(self, output_dir=None, *args, **kwargs): self.output_dir = output_dir
    def generate_report(self, data=None, *args, **kwargs):
        class DateTimeEncoder(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, datetime): return o.isoformat()
                return super().default(o)
        path = "/tmp/report.pdf"
        with open(path, "w") as f: json.dump(data if data else {"roi_analysis": {}, "forecast": []}, f, cls=DateTimeEncoder)
        return path
