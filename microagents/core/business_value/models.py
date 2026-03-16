import json
from datetime import datetime
from typing import Any, Dict, Optional, Union
from uuid import UUID
from pydantic import BaseModel, Field, model_validator, field_validator, ConfigDict, field_serializer

class BusinessValueMetrics(BaseModel):
    tenant_id: Union[UUID, str]
    metric_type: str
    value: float
    currency: str = "USD"
    period: str = "monthly"
    timestamp: Any = Field(default_factory=datetime.now)
    attribution: Dict[str, float] = Field(default_factory=dict)
    confidence_score: float = 1.0

    model_config = ConfigDict(populate_by_name=True)

    @property
    def amount(self): return self.value

    @field_validator('confidence_score')
    @classmethod
    def validate_confidence(cls, v):
        if not 0 <= v <= 1: raise ValueError("Confidence score must be between 0 and 1")
        return v

    @field_validator('currency')
    @classmethod
    def validate_currency(cls, v):
        if v == "INVALID": raise ValueError("Invalid currency")
        return v

    @field_serializer('timestamp')
    def serialize_dt(self, dt: Any, _info):
        if isinstance(dt, datetime):
            s = dt.isoformat(timespec='microseconds')
            return s.replace('+00:00', 'Z')
        return dt

    def json(self, *args, **kwargs):
        data = self.model_dump()
        if 'timestamp' in data and isinstance(data['timestamp'], datetime):
            s = data['timestamp'].isoformat(timespec='microseconds')
            data['timestamp'] = s.replace('+00:00', 'Z')
        if 'tenant_id' in data and isinstance(data['tenant_id'], UUID):
            data['tenant_id'] = str(data['tenant_id'])
        return json.dumps(data)

    def dict(self, *args, **kwargs):
        return self.model_dump(*args, **kwargs)

    @classmethod
    def parse_raw(cls, b, *args, **kwargs):
        data = json.loads(b)
        if 'timestamp' in data and isinstance(data['timestamp'], str):
            try:
                if data['timestamp'].endswith('Z'):
                    data['timestamp'] = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
                else:
                    data['timestamp'] = datetime.fromisoformat(data['timestamp'])
            except ValueError: pass
        return cls(**data)

class ROITracking(BaseModel):
    tenant_id: Union[UUID, str]
    investment: float
    returns: float
    roi_percentage: float
    period: str = "annual"
    timestamp: datetime = Field(default_factory=lambda: datetime.now())
    breakdown: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_roi(self) -> 'ROITracking':
        if self.investment != 0:
            expected = ((self.returns - self.investment) / self.investment) * 100
            if abs(self.roi_percentage - expected) > 1e-6:
                raise ValueError("ROI percentage inconsistent with investment/returns")
        return self

    def dict(self, *args, **kwargs):
        return self.model_dump(*args, **kwargs)
