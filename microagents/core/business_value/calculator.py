import time
from typing import List, Any
from .roi import calculate_roi
from .npv import calculate_npv
from .irr import calculate_irr
from microagents.utils.caching import ThreadSafeLRUCache

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

        roi = ((monthly_returns * 12) - investment) / investment * 100
        self._cache[key] = roi
        return roi

    def clear_cache(self): self._cache.clear()
    def get_cached_result(self, key): return self._cache.get(key)
