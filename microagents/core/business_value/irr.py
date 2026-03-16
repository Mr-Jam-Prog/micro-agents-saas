from typing import List
from .npv import calculate_npv

def calculate_irr(cash_flows: List[float], iterations: int = 1000, tolerance: float = 1e-7) -> float:
    if not cash_flows: return 0.0
    if cash_flows[0] >= 0:
        raise ValueError("Impossible de trouver un IRR valide. Doit avoir un investissement initial négatif")
    if all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        raise ValueError("Impossible de trouver un IRR valide")

    a, b = 0.0, 1.4
    for _ in range(100):
        mid = (a + b) / 2
        npv = calculate_npv(cash_flows, mid)
        if abs(npv) < tolerance: return mid
        if npv > 0: a = mid
        else: b = mid
    return a
