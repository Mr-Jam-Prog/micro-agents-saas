import random
import statistics

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
