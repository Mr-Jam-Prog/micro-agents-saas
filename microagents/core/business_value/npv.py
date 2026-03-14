from typing import List

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
