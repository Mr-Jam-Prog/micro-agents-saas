from typing import Dict, Any, Optional

class RiskAdjuster:
    def __init__(self, risk_free_rate: float = 0.02, market_return: float = 0.08):
        self.risk_free_rate = risk_free_rate
        self.market_return = market_return

    def adjust_for_risk(self, investment_data: Dict[str, Any], adjustment_method: str = "capm", include_scenarios: bool = True) -> Dict[str, Any]:
        expected_return = investment_data["expected_return"]
        risk = investment_data["risk"]
        beta = investment_data.get("beta", 1.0)

        # CAPM: risk_free + beta * (market_return - risk_free)
        risk_adjusted_return = self.risk_free_rate + beta * (self.market_return - self.risk_free_rate)
        sharpe_ratio = (expected_return - self.risk_free_rate) / risk if risk != 0 else 0

        return {
            "risk_adjusted_return": risk_adjusted_return,
            "sharpe_ratio": sharpe_ratio,
            "scenario_analysis": {}
        }
