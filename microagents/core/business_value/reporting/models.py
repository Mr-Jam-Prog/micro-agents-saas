from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field

class BusinessValueMetrics(BaseModel):
    tenant_id: str
    metric_type: str
    value: float
    currency: str = "USD"
    period: str
    timestamp: datetime
    attribution: Dict[str, float] = Field(default_factory=dict)
    confidence_score: float = Field(0.95, ge=0.0, le=1.0)

class ROIAnalysis(BaseModel):
    investment_amount: float
    return_amount: float
    total_roi_percentage: float
    guaranteed_roi_percentage: float = 300.0
    payback_period_months: int = 0
    net_present_value: float = 0.0
    internal_rate_of_return: float = 0.0

class CostSavings(BaseModel):
    total_savings: float
    cloud_infrastructure_savings: float = 0.0
    operational_maintenance_savings: float = 0.0
    incident_resolution_savings: float = 0.0
    security_compliance_savings: float = 0.0

class SecurityMetrics(BaseModel):
    vulnerabilities_detected: int = 0
    critical_vulnerabilities: int = 0
    mean_time_to_detect: float = 0.0
    compliance_score: float = 0.0

class PerformanceMetrics(BaseModel):
    incident_resolution_improvement: float = 0.0
    availability_percentage: float = 99.99
    average_response_time: float = 0.0

class ReportData(BaseModel):
    title: str
    report_type: str
    customer_id: Optional[UUID] = None
    period_start: datetime
    period_end: datetime
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    roi_analysis: Optional[ROIAnalysis] = None
    cost_savings: Optional[CostSavings] = None
    security_metrics: Optional[SecurityMetrics] = None
    performance_metrics: Optional[PerformanceMetrics] = None
