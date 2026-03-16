class ROIForecaster:
    def __init__(self, confidence_level: float = 0.95, forecast_horizon: int = 12):
        self.confidence_level = confidence_level
        self.forecast_horizon = forecast_horizon
    def forecast(self, historical_data=None, *args, **kwargs):
        return {
            "forecast": [0.0] * self.forecast_horizon, "confidence_intervals": [],
            "model_metrics": {"mae": 0.1}, "steps": self.forecast_horizon, "mean_forecast": 0.0
        }
