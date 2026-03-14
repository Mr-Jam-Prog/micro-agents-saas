def calculate_roi(investment: float, returns: float) -> float:
    if investment == 0:
        raise ValueError("Investissement ne peut pas être nul")
    return ((returns - investment) / investment) * 100
