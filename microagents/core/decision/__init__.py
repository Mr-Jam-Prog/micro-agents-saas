from typing import Dict, Any, Optional
from microagents.core.business_value import calculate_roi

class DecisionEngine:
    def __init__(self, min_roi_threshold: float = 20.0):
        self.min_roi_threshold = min_roi_threshold

    def should_execute(self, investment: float, expected_return: float) -> bool:
        try:
            roi = calculate_roi(investment, expected_return)
            return roi >= self.min_roi_threshold
        except ValueError:
            return False

    def evaluate_action(self, action_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        # Logique simplifiée pour décider de l'exécution
        investment = context.get("investment", 0.0)
        returns = context.get("expected_return", 0.0)

        allowed = self.should_execute(investment, returns)

        return {
            "action_id": action_id,
            "allowed": allowed,
            "reason": "ROI threshold met" if allowed else "ROI too low or invalid"
        }
