from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator, field_serializer

class BusinessValueMetrics(BaseModel):
    tenant_id: str
    metric_type: str
    value: float
    currency: str = "USD"
    period: str
    timestamp: datetime
    attribution: Dict[str, float] = Field(default_factory=dict)
    confidence_score: float = Field(0.95, ge=0.0, le=1.0)

    @field_validator('currency')
    @classmethod
    def validate_currency(cls, v):
        allowed = ["USD", "EUR", "GBP", "JPY", "CAD"]
        if v not in allowed:
            raise ValueError(f"Invalid currency: {v}")
        return v

    @field_serializer('timestamp')
    def serialize_timestamp(self, timestamp: datetime, _info):
        return timestamp.isoformat().replace('+00:00', 'Z')

class ROITracking(BaseModel):
    tenant_id: str
    investment: float
    returns: float
    roi_percentage: float
    period: str
    timestamp: datetime
    breakdown: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_roi_consistency(self) -> 'ROITracking':
        if self.investment != 0:
            expected_roi = ((self.returns - self.investment) / self.investment) * 100
            if abs(self.roi_percentage - expected_roi) > 0.1:
                 raise ValueError(f"ROI percentage {self.roi_percentage} is inconsistent with investment {self.investment} and returns {self.returns}")
        return self

    @field_serializer('timestamp')
    def serialize_timestamp(self, timestamp: datetime, _info):
        return timestamp.isoformat().replace('+00:00', 'Z')
