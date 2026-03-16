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
