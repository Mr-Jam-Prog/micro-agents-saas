from pydantic import BaseModel
from typing import Optional, List

class PricingModel(BaseModel):
    name: str
    base_price: float
    per_unit_price: float
    unit_name: str
    features: Optional[List[str]] = None
