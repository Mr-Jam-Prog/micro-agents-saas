"""
Cost Anomaly Detector - Example Implementation
Complete example showing how to build, deploy, and monitor a cost anomaly detection system.
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
import boto3

# Internal imports
# Note: In a real scenario, these would be imported from the installed package
# from microagents.platform.agents.base_agent import BaseAgent
# from microagents.platform.metrics import MetricsCollector

logger = logging.getLogger(__name__)

class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class CostDataPoint:
    timestamp: datetime
    service: str
    region: str
    cost: float
    currency: str = "USD"
    tags: Dict[str, str] = field(default_factory=dict)

@dataclass
class AnomalyDetectionResult:
    is_anomaly: bool
    confidence: float
    severity: Severity
    actual_value: float
    expected_value: float
    detected_at: datetime
    rule_name: str

class StatisticalDetector:
    def __init__(self, window_size: int = 30, z_score_threshold: float = 2.5):
        self.window_size = window_size
        self.z_score_threshold = z_score_threshold
    
    def detect_using_zscore(self, data: List[float]) -> AnomalyDetectionResult:
        if len(data) < self.window_size:
            return AnomalyDetectionResult(False, 0.0, Severity.LOW, data[-1] if data else 0, 0, datetime.utcnow(), "z_score")
        
        mean = np.mean(data[:-1])
        std = np.std(data[:-1])
        z_score = abs((data[-1] - mean) / std) if std > 0 else 0
        
        return AnomalyDetectionResult(
            is_anomaly=z_score > self.z_score_threshold,
            confidence=min(z_score / 5.0, 1.0),
            severity=Severity.HIGH if z_score > 3 else Severity.MEDIUM,
            actual_value=data[-1],
            expected_value=mean,
            detected_at=datetime.utcnow(),
            rule_name="z_score"
        )

async def main():
    print("Cost Anomaly Detector Example")
    detector = StatisticalDetector()
    sample_data = [100.0] * 29 + [500.0]
    result = detector.detect_using_zscore(sample_data)
    print(f"Anomaly detected: {result.is_anomaly}")
    print(f"Actual: {result.actual_value}, Expected: {result.expected_value}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
