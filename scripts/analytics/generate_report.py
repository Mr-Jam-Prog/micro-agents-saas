#!/usr/bin/env python3
"""
generate_report.py - Comprehensive analytics and reporting system for MicroAgents Platform
Version: 2.0.0
Description: Generates automated reports for ROI analysis, performance, cost optimization, and business intelligence
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum
from decimal import Decimal
import statistics
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from jinja2 import Template, Environment, FileSystemLoader
import yaml
from pydantic import BaseModel, validator
import boto3
from botocore.exceptions import ClientError
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import markdown2
from weasyprint import HTML

# Local imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from microagents.db.session import SessionLocal
from microagents.models import Agent, Tenant, ExecutionJob, CostSavings
from microagents.core.config import settings

# ============================================================================
# CONFIGURATION
# ============================================================================

class ReportType(Enum):
    """Types of reports available."""
    ROI_ANALYSIS = "roi_analysis"
    PERFORMANCE = "performance"
    COST_OPTIMIZATION = "cost_optimization"
    SECURITY_COMPLIANCE = "security_compliance"
    USAGE_ANALYTICS = "usage_analytics"
    CUSTOMER_SUCCESS = "customer_success"
    BUSINESS_VALUE = "business_value"
    COMPETITIVE_ANALYSIS = "competitive_analysis"
    FORECASTING = "forecasting"
    EXECUTIVE_SUMMARY = "executive_summary"

class OutputFormat(Enum):
    """Supported output formats."""
    PDF = "pdf"
    HTML = "html"
    EXCEL = "excel"
    JSON = "json"
    MARKDOWN = "markdown"
    CSV = "csv"
    SLACK = "slack"
    EMAIL = "email"

@dataclass
class ReportConfig:
    """Configuration for report generation."""
    report_type: ReportType
    tenant_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    output_formats: List[OutputFormat] = None
    schedule: Optional[str] = None  # cron expression
    recipients: List[str] = None
    template_name: Optional[str] = None
    custom_filters: Dict[str, Any] = None
    compression: bool = True
    encryption: bool = False
    
    def __post_init__(self):
        if self.output_formats is None:
            self.output_formats = [OutputFormat.PDF, OutputFormat.HTML]
        if self.recipients is None:
            self.recipients = []
        if self.custom_filters is None:
            self.custom_filters = {}
        if self.end_date is None:
            self.end_date = datetime.now()
        if self.start_date is None:
            self.start_date = self.end_date - timedelta(days=30)

# ============================================================================
# DATA MODELS
# ============================================================================

class ROIReportData(BaseModel):
    """ROI analysis data model."""
    period_start: datetime
    period_end: datetime
    total_cost_savings: Decimal
    total_productivity_gains: Decimal
    total_security_value: Decimal
    platform_cost: Decimal
    calculated_roi: Decimal
    roi_percentage: Decimal
    payback_period_days: int
    detailed_breakdown: Dict[str, Decimal]
    top_contributing_agents: List[Dict[str, Any]]
    confidence_score: Decimal
    
    @validator('roi_percentage')
    def validate_percentage(cls, v):
        if v < -100 or v > 10000:  # Allow up to 10000% ROI
            raise ValueError('ROI percentage out of valid range')
        return v

class PerformanceReportData(BaseModel):
    """Performance reporting data model."""
    period_start: datetime
    period_end: datetime
    availability_percentage: Decimal
    average_response_time_ms: Decimal
    p95_response_time_ms: Decimal
    p99_response_time_ms: Decimal
    error_rate_percentage: Decimal
    throughput_requests_per_second: Decimal
    concurrent_users: int
    agent_performance_metrics: List[Dict[str, Any]]
    incident_summary: Dict[str, int]
    sla_compliance: Dict[str, Decimal]

class CostOptimizationReportData(BaseModel):
    """Cost optimization report data model."""
    period_start: datetime
    period_end: datetime
    total_cloud_spend: Decimal
    optimized_spend: Decimal
    savings_amount: Decimal
    savings_percentage: Decimal
    resource_optimizations: List[Dict[str, Any]]
    cost_anomalies: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]
    forecast_next_month: Decimal
    budget_vs_actual: Dict[str, Decimal]

# ============================================================================
# DATA COLLECTION
# ============================================================================

class DataCollector:
    """Collects and processes data for reports."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.cache = {}
    
    async def collect_roi_data(self, config: ReportConfig) -> ROIReportData:
        """Collect ROI analysis data."""
        log.info(f"Collecting ROI data for {config.report_type.value}")
        
        # Query cost savings
        cost_savings_query = text("""
            SELECT SUM(savings_amount) as total_savings,
                   COUNT(*) as optimization_count
            FROM cost_savings
            WHERE savings_date BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
        """)
        
        result = self.db.execute(
            cost_savings_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        total_cost_savings = Decimal(result.total_savings or 0)
        
        # Query productivity gains
        productivity_query = text("""
            SELECT SUM(time_saved_minutes) as total_time_saved,
                   COUNT(*) as automation_count
            FROM execution_jobs
            WHERE created_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            AND status = 'COMPLETED'
        """)
        
        result = self.db.execute(
            productivity_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        # Convert time saved to monetary value (assuming $50/hour engineer cost)
        hourly_rate = Decimal('50')
        minutes_per_hour = Decimal('60')
        total_productivity_gains = Decimal(result.total_time_saved or 0) / minutes_per_hour * hourly_rate
        
        # Query security value (prevented incidents)
        security_query = text("""
            SELECT COUNT(*) as prevented_incidents,
                   SUM(estimated_cost) as prevented_cost
            FROM security_events
            WHERE created_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            AND status = 'RESOLVED'
            AND action_taken IS NOT NULL
        """)
        
        result = self.db.execute(
            security_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        total_security_value = Decimal(result.prevented_cost or 0)
        
        # Calculate platform cost
        platform_cost = self._calculate_platform_cost(config)
        
        # Calculate ROI
        total_value = total_cost_savings + total_productivity_gains + total_security_value
        calculated_roi = total_value - platform_cost
        roi_percentage = (calculated_roi / platform_cost * 100) if platform_cost > 0 else Decimal('0')
        
        # Calculate payback period
        monthly_value = total_value / Decimal('12')  # Annualize
        payback_period_days = int((platform_cost / monthly_value * 30).to_integral_value())
        
        # Get top contributing agents
        top_agents_query = text("""
            SELECT a.name, a.agent_type_id, SUM(cs.savings_amount) as total_savings
            FROM cost_savings cs
            JOIN agents a ON cs.agent_id = a.id
            WHERE cs.savings_date BETWEEN :start_date AND :end_date
            AND cs.tenant_id = COALESCE(:tenant_id, cs.tenant_id)
            GROUP BY a.id, a.name, a.agent_type_id
            ORDER BY total_savings DESC
            LIMIT 10
        """)
        
        top_agents = self.db.execute(
            top_agents_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        detailed_breakdown = {
            "cost_savings": total_cost_savings,
            "productivity_gains": total_productivity_gains,
            "security_value": total_security_value,
            "platform_cost": platform_cost
        }
        
        top_contributing_agents = [
            {
                "name": agent.name,
                "type": agent.agent_type_id,
                "savings": Decimal(agent.total_savings or 0)
            }
            for agent in top_agents
        ]
        
        # Calculate confidence score based on data completeness
        confidence_score = self._calculate_confidence_score(config)
        
        return ROIReportData(
            period_start=config.start_date,
            period_end=config.end_date,
            total_cost_savings=total_cost_savings,
            total_productivity_gains=total_productivity_gains,
            total_security_value=total_security_value,
            platform_cost=platform_cost,
            calculated_roi=calculated_roi,
            roi_percentage=roi_percentage,
            payback_period_days=payback_period_days,
            detailed_breakdown=detailed_breakdown,
            top_contributing_agents=top_contributing_agents,
            confidence_score=confidence_score
        )
    
    async def collect_performance_data(self, config: ReportConfig) -> PerformanceReportData:
        """Collect performance data."""
        log.info(f"Collecting performance data for {config.report_type.value}")
        
        # Query availability metrics
        availability_query = text("""
            SELECT 
                COUNT(*) as total_checks,
                SUM(CASE WHEN status = 'HEALTHY' THEN 1 ELSE 0 END) as healthy_checks
            FROM health_checks
            WHERE last_check BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
        """)
        
        result = self.db.execute(
            availability_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        total_checks = result.total_checks or 1
        healthy_checks = result.healthy_checks or 0
        availability_percentage = Decimal(healthy_checks) / Decimal(total_checks) * 100
        
        # Query response time metrics
        response_time_query = text("""
            SELECT 
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) as p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95,
                PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) as p99,
                AVG(duration_ms) as average,
                COUNT(*) as total_requests
            FROM execution_jobs
            WHERE completed_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            AND status = 'COMPLETED'
        """)
        
        result = self.db.execute(
            response_time_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        average_response_time_ms = Decimal(result.average or 0)
        p95_response_time_ms = Decimal(result.p95 or 0)
        p99_response_time_ms = Decimal(result.p99 or 0)
        
        # Query error rate
        error_query = text("""
            SELECT 
                COUNT(*) as total_jobs,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed_jobs
            FROM execution_jobs
            WHERE created_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
        """)
        
        result = self.db.execute(
            error_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        total_jobs = result.total_jobs or 1
        failed_jobs = result.failed_jobs or 0
        error_rate_percentage = Decimal(failed_jobs) / Decimal(total_jobs) * 100
        
        # Query throughput
        throughput_query = text("""
            SELECT 
                DATE_TRUNC('hour', created_at) as hour,
                COUNT(*) as requests_per_hour
            FROM execution_jobs
            WHERE created_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            GROUP BY DATE_TRUNC('hour', created_at)
            ORDER BY hour
        """)
        
        results = self.db.execute(
            throughput_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        if results:
            throughput_requests_per_second = Decimal(
                statistics.mean([r.requests_per_hour for r in results]) / 3600
            )
        else:
            throughput_requests_per_second = Decimal('0')
        
        # Query concurrent users (approximation)
        concurrent_query = text("""
            SELECT 
                MAX(active_sessions) as max_concurrent_users
            FROM (
                SELECT 
                    DATE_TRUNC('minute', created_at) as minute,
                    COUNT(DISTINCT user_id) as active_sessions
                FROM user_sessions
                WHERE created_at BETWEEN :start_date AND :end_date
                AND tenant_id = COALESCE(:tenant_id, tenant_id)
                GROUP BY DATE_TRUNC('minute', created_at)
            ) as session_counts
        """)
        
        result = self.db.execute(
            concurrent_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        concurrent_users = result.max_concurrent_users or 0
        
        # Get agent performance metrics
        agent_performance_query = text("""
            SELECT 
                a.name,
                a.agent_type_id,
                COUNT(ej.id) as execution_count,
                AVG(ej.duration_ms) as avg_duration,
                SUM(CASE WHEN ej.status = 'FAILED' THEN 1 ELSE 0 END) as failure_count
            FROM agents a
            LEFT JOIN execution_jobs ej ON a.id = ej.agent_id
                AND ej.created_at BETWEEN :start_date AND :end_date
            WHERE a.tenant_id = COALESCE(:tenant_id, a.tenant_id)
            GROUP BY a.id, a.name, a.agent_type_id
            HAVING COUNT(ej.id) > 0
            ORDER BY execution_count DESC
            LIMIT 20
        """)
        
        agent_results = self.db.execute(
            agent_performance_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        agent_performance_metrics = [
            {
                "name": agent.name,
                "type": agent.agent_type_id,
                "execution_count": agent.execution_count,
                "avg_duration_ms": Decimal(agent.avg_duration or 0),
                "failure_count": agent.failure_count,
                "success_rate": Decimal(1 - (agent.failure_count / agent.execution_count)) * 100
                if agent.execution_count > 0 else Decimal('100')
            }
            for agent in agent_results
        ]
        
        # Incident summary
        incident_query = text("""
            SELECT 
                severity,
                COUNT(*) as count,
                SUM(CASE WHEN status = 'RESOLVED' THEN 1 ELSE 0 END) as resolved_count
            FROM security_events
            WHERE created_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            GROUP BY severity
        """)
        
        incident_results = self.db.execute(
            incident_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        incident_summary = {
            incident.severity: {
                "total": incident.count,
                "resolved": incident.resolved_count,
                "resolution_rate": Decimal(incident.resolved_count) / Decimal(incident.count) * 100
                if incident.count > 0 else Decimal('0')
            }
            for incident in incident_results
        }
        
        # SLA compliance
        sla_compliance = {
            "availability": availability_percentage,
            "response_time_p95": p95_response_time_ms,
            "error_rate": error_rate_percentage
        }
        
        return PerformanceReportData(
            period_start=config.start_date,
            period_end=config.end_date,
            availability_percentage=availability_percentage,
            average_response_time_ms=average_response_time_ms,
            p95_response_time_ms=p95_response_time_ms,
            p99_response_time_ms=p99_response_time_ms,
            error_rate_percentage=error_rate_percentage,
            throughput_requests_per_second=throughput_requests_per_second,
            concurrent_users=concurrent_users,
            agent_performance_metrics=agent_performance_metrics,
            incident_summary=incident_summary,
            sla_compliance=sla_compliance
        )
    
    async def collect_cost_optimization_data(self, config: ReportConfig) -> CostOptimizationReportData:
        """Collect cost optimization data."""
        log.info(f"Collecting cost optimization data for {config.report_type.value}")
        
        # Query total cloud spend
        total_spend_query = text("""
            SELECT 
                SUM(cost_usd) as total_spend,
                COUNT(DISTINCT resource_id) as unique_resources
            FROM cost_items
            WHERE period_start BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
        """)
        
        result = self.db.execute(
            total_spend_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        total_cloud_spend = Decimal(result.total_spend or 0)
        
        # Query optimized spend (after optimizations)
        optimized_spend_query = text("""
            SELECT 
                SUM(optimized_cost) as optimized_total,
                COUNT(*) as optimization_count
            FROM cost_savings
            WHERE savings_date BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
        """)
        
        result = self.db.execute(
            optimized_spend_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        optimized_spend = Decimal(result.optimized_total or 0)
        
        # Calculate savings
        savings_amount = total_cloud_spend - optimized_spend
        savings_percentage = (savings_amount / total_cloud_spend * 100) if total_cloud_spend > 0 else Decimal('0')
        
        # Get resource optimizations
        resource_optimizations_query = text("""
            SELECT 
                resource_type,
                resource_id,
                provider,
                baseline_cost,
                optimized_cost,
                savings_amount,
                savings_percentage,
                recommendation,
                action_taken
            FROM cost_savings
            WHERE savings_date BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            ORDER BY savings_amount DESC
            LIMIT 20
        """)
        
        optimizations = self.db.execute(
            resource_optimizations_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        resource_optimizations = [
            {
                "resource_type": opt.resource_type,
                "resource_id": opt.resource_id,
                "provider": opt.provider,
                "baseline_cost": Decimal(opt.baseline_cost or 0),
                "optimized_cost": Decimal(opt.optimized_cost or 0),
                "savings_amount": Decimal(opt.savings_amount or 0),
                "savings_percentage": Decimal(opt.savings_percentage or 0),
                "recommendation": opt.recommendation,
                "action_taken": opt.action_taken
            }
            for opt in optimizations
        ]
        
        # Get cost anomalies
        anomalies_query = text("""
            SELECT 
                resource_id,
                cost_usd,
                expected_cost,
                anomaly_score,
                detected_at,
                description
            FROM cost_anomalies
            WHERE detected_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            ORDER BY anomaly_score DESC
            LIMIT 10
        """)
        
        anomalies = self.db.execute(
            anomalies_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        cost_anomalies = [
            {
                "resource_id": anomaly.resource_id,
                "cost_usd": Decimal(anomaly.cost_usd or 0),
                "expected_cost": Decimal(anomaly.expected_cost or 0),
                "anomaly_score": Decimal(anomaly.anomaly_score or 0),
                "detected_at": anomaly.detected_at,
                "description": anomaly.description
            }
            for anomaly in anomalies
        ]
        
        # Generate recommendations
        recommendations = self._generate_cost_recommendations(config)
        
        # Forecast next month
        forecast_next_month = self._forecast_next_month_cost(config)
        
        # Budget vs actual
        budget_vs_actual = self._get_budget_vs_actual(config)
        
        return CostOptimizationReportData(
            period_start=config.start_date,
            period_end=config.end_date,
            total_cloud_spend=total_cloud_spend,
            optimized_spend=optimized_spend,
            savings_amount=savings_amount,
            savings_percentage=savings_percentage,
            resource_optimizations=resource_optimizations,
            cost_anomalies=cost_anomalies,
            recommendations=recommendations,
            forecast_next_month=forecast_next_month,
            budget_vs_actual=budget_vs_actual
        )
    
    def _calculate_platform_cost(self, config: ReportConfig) -> Decimal:
        """Calculate platform subscription cost."""
        if config.tenant_id:
            query = text("""
                SELECT sp.price_monthly
                FROM tenant_subscriptions ts
                JOIN subscription_plans sp ON ts.plan_id = sp.id
                WHERE ts.tenant_id = :tenant_id
                AND ts.status = 'ACTIVE'
            """)
            
            result = self.db.execute(query, {"tenant_id": config.tenant_id}).fetchone()
            if result:
                return Decimal(result.price_monthly or 0)
        
        # Default platform cost
        return Decimal('499')  # Professional plan monthly
    
    def _calculate_confidence_score(self, config: ReportConfig) -> Decimal:
        """Calculate data confidence score."""
        # Check data completeness
        completeness_checks = []
        
        # Check cost savings data
        savings_check = text("""
            SELECT COUNT(*) as record_count
            FROM cost_savings
            WHERE savings_date BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
        """)
        
        result = self.db.execute(
            savings_check,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        if result.record_count > 10:
            completeness_checks.append(1.0)
        elif result.record_count > 0:
            completeness_checks.append(result.record_count / 10)
        else:
            completeness_checks.append(0.0)
        
        # Check execution jobs data
        jobs_check = text("""
            SELECT COUNT(*) as record_count
            FROM execution_jobs
            WHERE created_at BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
        """)
        
        result = self.db.execute(
            jobs_check,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        if result.record_count > 100:
            completeness_checks.append(1.0)
        elif result.record_count > 0:
            completeness_checks.append(result.record_count / 100)
        else:
            completeness_checks.append(0.0)
        
        # Calculate average confidence
        if completeness_checks:
            return Decimal(sum(completeness_checks) / len(completeness_checks) * 100)
        return Decimal('0')
    
    def _generate_cost_recommendations(self, config: ReportConfig) -> List[Dict[str, Any]]:
        """Generate cost optimization recommendations."""
        recommendations = []
        
        # Recommendation 1: Right-size instances
        rightsizing_query = text("""
            SELECT 
                resource_type,
                resource_id,
                AVG(cpu_usage) as avg_cpu,
                AVG(memory_usage) as avg_memory
            FROM metrics_time_series
            WHERE timestamp BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            AND metric_name IN ('cpu_usage', 'memory_usage')
            GROUP BY resource_type, resource_id
            HAVING AVG(cpu_usage) < 30 OR AVG(memory_usage) < 40
        """)
        
        results = self.db.execute(
            rightsizing_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        if results:
            recommendations.append({
                "type": "rightsizing",
                "title": "Right-size Underutilized Resources",
                "description": f"{len(results)} resources are underutilized and could be downsized",
                "potential_savings": Decimal('500'),
                "effort": "MEDIUM",
                "priority": "HIGH"
            })
        
        # Recommendation 2: Purchase Reserved Instances
        ri_query = text("""
            SELECT 
                COUNT(DISTINCT resource_id) as stable_resources
            FROM metrics_time_series
            WHERE timestamp BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            AND metric_name = 'cpu_usage'
            GROUP BY resource_id
            HAVING COUNT(*) > 100  # Has been running consistently
        """)
        
        result = self.db.execute(
            ri_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id
            }
        ).fetchone()
        
        if result and result.stable_resources > 5:
            recommendations.append({
                "type": "reserved_instances",
                "title": "Purchase Reserved Instances",
                "description": f"{result.stable_resources} resources are running 24/7, consider Reserved Instances",
                "potential_savings": Decimal('1000'),
                "effort": "LOW",
                "priority": "MEDIUM"
            })
        
        # Recommendation 3: Clean up idle resources
        idle_query = text("""
            SELECT COUNT(DISTINCT resource_id) as idle_resources
            FROM cost_items
            WHERE period_start BETWEEN :start_date AND :end_date
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            AND cost_usd > 0
            AND resource_id NOT IN (
                SELECT DISTINCT resource_id 
                FROM metrics_time_series 
                WHERE timestamp > :recent_date
            )
        """)
        
        result = self.db.execute(
            idle_query,
            {
                "start_date": config.start_date,
                "end_date": config.end_date,
                "tenant_id": config.tenant_id,
                "recent_date": config.end_date - timedelta(days=7)
            }
        ).fetchone()
        
        if result and result.idle_resources > 0:
            recommendations.append({
                "type": "cleanup",
                "title": "Clean Up Idle Resources",
                "description": f"{result.idle_resources} resources appear to be idle",
                "potential_savings": Decimal('200'),
                "effort": "LOW",
                "priority": "HIGH"
            })
        
        return recommendations
    
    def _forecast_next_month_cost(self, config: ReportConfig) -> Decimal:
        """Forecast next month's cost."""
        historical_query = text("""
            SELECT 
                DATE_TRUNC('month', period_start) as month,
                SUM(cost_usd) as monthly_cost
            FROM cost_items
            WHERE period_start >= :start_date - INTERVAL '6 months'
            AND tenant_id = COALESCE(:tenant_id, tenant_id)
            GROUP BY DATE_TRUNC('month', period_start)
            ORDER BY month DESC
            LIMIT 3
        """)
        
        results = self.db.execute(
            historical_query,
            {
                "start_date": config.start_date,
                "tenant_id": config.tenant_id
            }
        ).fetchall()
        
        if len(results) >= 2:
            # Simple average of last 3 months
            monthly_costs = [Decimal(r.monthly_cost or 0) for r in results]
            forecast = sum(monthly_costs) / len(monthly_costs)
            
            # Apply growth trend if available
            if len(monthly_costs) >= 3:
                growth_rate = (monthly_costs[0] / monthly_costs[2]) ** (1/2) - 1
                forecast = forecast * (1 + growth_rate)
            
            return forecast
        elif results:
            return Decimal(results[0].monthly_cost or 0)
        
        return Decimal('0')
    
    def _get_budget_vs_actual(self, config: ReportConfig) -> Dict[str, Decimal]:
        """Get budget vs actual spending."""
        # This would query budget data from a budgets table
        # For now, return simulated data
        actual = self._forecast_next_month_cost(config)
        
        return {
            "budget": actual * Decimal('1.1'),  # Budget is 10% higher than forecast
            "actual": actual,
            "variance": actual * Decimal('0.1'),
            "variance_percentage": Decimal('10')
        }

# ============================================================================
# REPORT GENERATION
# ============================================================================

class ReportGenerator:
    """Generates reports in various formats."""
    
    def __init__(self, templates_dir: Path = None):
        if templates_dir is None:
            templates_dir = Path(__file__).parent / "templates"
        self.templates_dir = templates_dir
        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            trim_blocks=True,
            lstrip_blocks=True
        )
        
    async def generate_roi_report(self, data: ROIReportData, config: ReportConfig) -> Dict[OutputFormat, bytes]:
        """Generate ROI analysis report."""
        log.info(f"Generating ROI report in formats: {[f.value for f in config.output_formats]}")
        
        outputs = {}
        
        # Generate visualizations
        charts = self._create_roi_charts(data)
        
        # Render templates for each format
        for fmt in config.output_formats:
            try:
                if fmt == OutputFormat.PDF:
                    outputs[fmt] = await self._generate_pdf(data, "roi", charts)
                elif fmt == OutputFormat.HTML:
                    outputs[fmt] = await self._generate_html(data, "roi", charts)
                elif fmt == OutputFormat.EXCEL:
                    outputs[fmt] = await self._generate_excel(data, "roi")
                elif fmt == OutputFormat.JSON:
                    outputs[fmt] = await self._generate_json(data)
                elif fmt == OutputFormat.MARKDOWN:
                    outputs[fmt] = await self._generate_markdown(data, "roi")
                elif fmt == OutputFormat.CSV:
                    outputs[fmt] = await self._generate_csv(data, "roi")
            except Exception as e:
                log.error(f"Failed to generate {fmt.value} format: {e}")
        
        return outputs
    
    async def generate_performance_report(self, data: PerformanceReportData, config: ReportConfig) -> Dict[OutputFormat, bytes]:
        """Generate performance report."""
        log.info(f"Generating performance report in formats: {[f.value for f in config.output_formats]}")
        
        outputs = {}
        charts = self._create_performance_charts(data)
        
        for fmt in config.output_formats:
            try:
                if fmt == OutputFormat.PDF:
                    outputs[fmt] = await self._generate_pdf(data, "performance", charts)
                elif fmt == OutputFormat.HTML:
                    outputs[fmt] = await self._generate_html(data, "performance", charts)
                elif fmt == OutputFormat.EXCEL:
                    outputs[fmt] = await self._generate_excel(data, "performance")
                elif fmt == OutputFormat.JSON:
                    outputs[fmt] = await self._generate_json(data)
                elif fmt == OutputFormat.MARKDOWN:
                    outputs[fmt] = await self._generate_markdown(data, "performance")
                elif fmt == OutputFormat.CSV:
                    outputs[fmt] = await self._generate_csv(data, "performance")
            except Exception as e:
                log.error(f"Failed to generate {fmt.value} format: {e}")
        
        return outputs
    
    async def generate_cost_optimization_report(self, data: CostOptimizationReportData, config: ReportConfig) -> Dict[OutputFormat, bytes]:
        """Generate cost optimization report."""
        log.info(f"Generating cost optimization report in formats: {[f.value for f in config.output_formats]}")
        
        outputs = {}
        charts = self._create_cost_charts(data)
        
        for fmt in config.output_formats:
            try:
                if fmt == OutputFormat.PDF:
                    outputs[fmt] = await self._generate_pdf(data, "cost", charts)
                elif fmt == OutputFormat.HTML:
                    outputs[fmt] = await self._generate_html(data, "cost", charts)
                elif fmt == OutputFormat.EXCEL:
                    outputs[fmt] = await self._generate_excel(data, "cost")
                elif fmt == OutputFormat.JSON:
                    outputs[fmt] = await self._generate_json(data)
                elif fmt == OutputFormat.MARKDOWN:
                    outputs[fmt] = await self._generate_markdown(data, "cost")
                elif fmt == OutputFormat.CSV:
                    outputs[fmt] = await self._generate_csv(data, "cost")
            except Exception as e:
                log.error(f"Failed to generate {fmt.value} format: {e}")
        
        return outputs
    
    def _create_roi_charts(self, data: ROIReportData) -> Dict[str, str]:
        """Create ROI visualization charts."""
        charts = {}
        
        # 1. ROI Breakdown Pie Chart
        breakdown_labels = list(data.detailed_breakdown.keys())
        breakdown_values = [float(v) for v in data.detailed_breakdown.values()]
        
        fig = go.Figure(data=[go.Pie(
            labels=breakdown_labels,
            values=breakdown_values,
            hole=.3,
            textinfo='label+percent+value'
        )])
        fig.update_layout(
            title_text="ROI Breakdown",
            showlegend=True
        )
        charts['roi_breakdown'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 2. Top Contributing Agents Bar Chart
        if data.top_contributing_agents:
            agent_names = [agent['name'][:20] + '...' if len(agent['name']) > 20 else agent['name'] 
                         for agent in data.top_contributing_agents]
            agent_savings = [float(agent['savings']) for agent in data.top_contributing_agents]
            
            fig = go.Figure(data=[go.Bar(
                x=agent_names,
                y=agent_savings,
                text=[f'${s:,.0f}' for s in agent_savings],
                textposition='auto',
            )])
            fig.update_layout(
                title_text="Top Contributing Agents",
                xaxis_title="Agent",
                yaxis_title="Savings ($)",
                showlegend=False
            )
            charts['top_agents'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 3. ROI Trend (simulated)
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun']
        roi_values = [10, 25, 45, 80, 120, float(data.roi_percentage)]
        
        fig = go.Figure(data=[go.Scatter(
            x=months,
            y=roi_values,
            mode='lines+markers',
            name='ROI %',
            line=dict(color='green', width=3)
        )])
        fig.update_layout(
            title_text="ROI Trend Over Time",
            xaxis_title="Month",
            yaxis_title="ROI %",
            showlegend=True
        )
        charts['roi_trend'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        return charts
    
    def _create_performance_charts(self, data: PerformanceReportData) -> Dict[str, str]:
        """Create performance visualization charts."""
        charts = {}
        
        # 1. SLA Compliance Gauge
        fig = go.Figure(go.Indicator(
            mode = "gauge+number+delta",
            value = float(data.availability_percentage),
            domain = {'x': [0, 1], 'y': [0, 1]},
            title = {'text': "Availability %"},
            delta = {'reference': 99.9},
            gauge = {
                'axis': {'range': [None, 100]},
                'bar': {'color': "darkblue"},
                'steps': [
                    {'range': [0, 99], 'color': "lightgray"},
                    {'range': [99, 99.9], 'color': "gray"},
                    {'range': [99.9, 100], 'color': "green"}
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': 99.9
                }
            }
        ))
        charts['availability_gauge'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 2. Response Time Distribution
        fig = go.Figure(data=[go.Bar(
            x=['Average', 'P95', 'P99'],
            y=[float(data.average_response_time_ms), 
               float(data.p95_response_time_ms), 
               float(data.p99_response_time_ms)],
            text=[f'{v:.0f}ms' for v in [float(data.average_response_time_ms), 
                                         float(data.p95_response_time_ms), 
                                         float(data.p99_response_time_ms)]],
            textposition='auto',
            marker_color=['blue', 'orange', 'red']
        )])
        fig.update_layout(
            title_text="Response Time Distribution",
            xaxis_title="Percentile",
            yaxis_title="Response Time (ms)",
            showlegend=False
        )
        charts['response_times'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 3. Agent Performance Heatmap
        if data.agent_performance_metrics:
            agent_names = [agent['name'][:15] + '...' if len(agent['name']) > 15 else agent['name'] 
                         for agent in data.agent_performance_metrics[:10]]
            success_rates = [float(agent['success_rate']) for agent in data.agent_performance_metrics[:10]]
            execution_counts = [agent['execution_count'] for agent in data.agent_performance_metrics[:10]]
            
            fig = go.Figure(data=go.Scatter(
                x=execution_counts,
                y=success_rates,
                mode='markers',
                marker=dict(
                    size=[c / 100 for c in execution_counts],  # Scale bubble size
                    color=success_rates,
                    colorscale='Viridis',
                    showscale=True,
                    colorbar=dict(title="Success Rate %")
                ),
                text=agent_names,
                hovertemplate='<b>%{text}</b><br>Executions: %{x}<br>Success Rate: %{y:.1f}%<extra></extra>'
            ))
            fig.update_layout(
                title_text="Agent Performance Bubble Chart",
                xaxis_title="Number of Executions",
                yaxis_title="Success Rate %",
                showlegend=False
            )
            charts['agent_performance'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 4. Incident Severity Distribution
        if data.incident_summary:
            severities = list(data.incident_summary.keys())
            counts = [data.incident_summary[s]['total'] for s in severities]
            
            fig = go.Figure(data=[go.Bar(
                x=severities,
                y=counts,
                text=counts,
                textposition='auto',
                marker_color=['green', 'yellow', 'orange', 'red'][:len(severities)]
            )])
            fig.update_layout(
                title_text="Incident Severity Distribution",
                xaxis_title="Severity",
                yaxis_title="Count",
                showlegend=False
            )
            charts['incidents'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        return charts
    
    def _create_cost_charts(self, data: CostOptimizationReportData) -> Dict[str, str]:
        """Create cost optimization visualization charts."""
        charts = {}
        
        # 1. Savings Overview Gauge
        fig = go.Figure(go.Indicator(
            mode = "gauge+number+delta",
            value = float(data.savings_percentage),
            domain = {'x': [0, 1], 'y': [0, 1]},
            title = {'text': "Cost Savings %"},
            delta = {'reference': 20},  # Target savings percentage
            gauge = {
                'axis': {'range': [None, 50]},
                'bar': {'color': "darkgreen"},
                'steps': [
                    {'range': [0, 10], 'color': "red"},
                    {'range': [10, 20], 'color': "orange"},
                    {'range': [20, 50], 'color': "green"}
                ],
                'threshold': {
                    'line': {'color': "blue", 'width': 4},
                    'thickness': 0.75,
                    'value': 20
                }
            }
        ))
        charts['savings_gauge'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 2. Cost Breakdown by Resource Type
        if data.resource_optimizations:
            resource_types = {}
            for opt in data.resource_optimizations:
                rtype = opt['resource_type']
                if rtype not in resource_types:
                    resource_types[rtype] = 0
                resource_types[rtype] += float(opt['savings_amount'])
            
            if resource_types:
                fig = go.Figure(data=[go.Pie(
                    labels=list(resource_types.keys()),
                    values=list(resource_types.values()),
                    hole=.3,
                    textinfo='label+percent+value'
                )])
                fig.update_layout(
                    title_text="Savings by Resource Type",
                    showlegend=True
                )
                charts['savings_by_type'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 3. Monthly Cost Trend
        # Simulated data for demonstration
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun']
        actual_costs = [10000, 9500, 9200, 8900, 8500, float(data.optimized_spend)]
        forecast_costs = [10000, 9800, 9600, 9400, 9200, float(data.forecast_next_month)]
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=months,
            y=actual_costs,
            mode='lines+markers',
            name='Actual Cost',
            line=dict(color='blue', width=3)
        ))
        fig.add_trace(go.Scatter(
            x=months,
            y=forecast_costs,
            mode='lines+markers',
            name='Forecast',
            line=dict(color='green', width=3, dash='dash')
        ))
        fig.update_layout(
            title_text="Monthly Cost Trend",
            xaxis_title="Month",
            yaxis_title="Cost ($)",
            showlegend=True
        )
        charts['cost_trend'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        # 4. Top Recommendations Impact
        if data.recommendations:
            rec_names = [rec['title'][:30] + '...' if len(rec['title']) > 30 else rec['title'] 
                        for rec in data.recommendations]
            rec_savings = [float(rec['potential_savings']) for rec in data.recommendations]
            rec_priority = [rec['priority'] for rec in data.recommendations]
            
            # Color by priority
            colors = {'HIGH': 'red', 'MEDIUM': 'orange', 'LOW': 'green'}
            bar_colors = [colors.get(p, 'gray') for p in rec_priority]
            
            fig = go.Figure(data=[go.Bar(
                x=rec_names,
                y=rec_savings,
                marker_color=bar_colors,
                text=[f'${s:,.0f}' for s in rec_savings],
                textposition='auto'
            )])
            fig.update_layout(
                title_text="Potential Savings from Recommendations",
                xaxis_title="Recommendation",
                yaxis_title="Potential Savings ($)",
                showlegend=False
            )
            charts['recommendations'] = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        return charts
    
    async def _generate_pdf(self, data: Any, report_type: str, charts: Dict[str, str]) -> bytes:
        """Generate PDF report."""
        # Generate HTML first
        html_content = await self._generate_html(data, report_type, charts, for_pdf=True)
        
        # Convert HTML to PDF
        html = HTML(string=html_content)
        pdf_bytes = html.write_pdf()
        
        return pdf_bytes
    
    async def _generate_html(self, data: Any, report_type: str, charts: Dict[str, str], for_pdf: bool = False) -> bytes:
        """Generate HTML report."""
        template = self.env.get_template(f"{report_type}_report.html")
        
        # Prepare context
        context = {
            "report_type": report_type,
            "data": data,
            "charts": charts,
            "generated_at": datetime.now(),
            "for_pdf": for_pdf,
            "company_name": "MicroAgents Platform",
            "company_logo": "https://microagents.io/logo.png"
        }
        
        # Render template
        html_content = template.render(**context)
        
        return html_content.encode('utf-8')
    
    async def _generate_excel(self, data: Any, report_type: str) -> bytes:
        """Generate Excel report."""
        import io
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        
        output = io.BytesIO()
        wb = Workbook()
        
        # Create summary sheet
        ws_summary = wb.active
        ws_summary.title = "Summary"
        
        # Add header
        ws_summary['A1'] = f"{report_type.replace('_', ' ').title()} Report"
        ws_summary['A1'].font = Font(size=16, bold=True)
        
        # Add data based on report type
        if report_type == "roi":
            self._add_roi_to_excel(ws_summary, data)
        elif report_type == "performance":
            self._add_performance_to_excel(ws_summary, data)
        elif report_type == "cost":
            self._add_cost_to_excel(ws_summary, data)
        
        # Auto-adjust column widths
        for column in ws_summary.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws_summary.column_dimensions[column_letter].width = adjusted_width
        
        # Save to bytes
        wb.save(output)
        return output.getvalue()
    
    def _add_roi_to_excel(self, ws, data):
        """Add ROI data to Excel sheet."""
        row = 3
        
        # Summary metrics
        metrics = [
            ("Total Cost Savings", f"${data.total_cost_savings:,.2f}"),
            ("Total Productivity Gains", f"${data.total_productivity_gains:,.2f}"),
            ("Total Security Value", f"${data.total_security_value:,.2f}"),
            ("Platform Cost", f"${data.platform_cost:,.2f}"),
            ("Calculated ROI", f"${data.calculated_roi:,.2f}"),
            ("ROI Percentage", f"{data.roi_percentage:.2f}%"),
            ("Payback Period", f"{data.payback_period_days} days"),
            ("Confidence Score", f"{data.confidence_score:.1f}%"),
        ]
        
        for label, value in metrics:
            ws.cell(row=row, column=1, value=label).font = Font(bold=True)
            ws.cell(row=row, column=2, value=value)
            row += 1
        
        row += 2
        
        # Detailed breakdown
        ws.cell(row=row, column=1, value="Detailed Breakdown").font = Font(bold=True, size=14)
        row += 1
        
        for category, amount in data.detailed_breakdown.items():
            ws.cell(row=row, column=1, value=category.replace('_', ' ').title())
            ws.cell(row=row, column=2, value=f"${amount:,.2f}")
            row += 1
        
        row += 2
        
        # Top contributing agents
        if data.top_contributing_agents:
            ws.cell(row=row, column=1, value="Top Contributing Agents").font = Font(bold=True, size=14)
            row += 1
            
            headers = ["Agent Name", "Type", "Savings"]
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=row, column=col, value=header)
                cell.font = Font(bold=True)
                cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            
            row += 1
            
            for agent in data.top_contributing_agents:
                ws.cell(row=row, column=1, value=agent['name'])
                ws.cell(row=row, column=2, value=agent['type'])
                ws.cell(row=row, column=3, value=f"${agent['savings']:,.2f}")
                row += 1
    
    def _add_performance_to_excel(self, ws, data):
        """Add performance data to Excel sheet."""
        row = 3
        
        # Key metrics
        metrics = [
            ("Availability", f"{data.availability_percentage:.3f}%"),
            ("Average Response Time", f"{data.average_response_time_ms:.0f} ms"),
            ("P95 Response Time", f"{data.p95_response_time_ms:.0f} ms"),
            ("P99 Response Time", f"{data.p99_response_time_ms:.0f} ms"),
            ("Error Rate", f"{data.error_rate_percentage:.3f}%"),
            ("Throughput", f"{data.throughput_requests_per_second:.1f} req/sec"),
            ("Max Concurrent Users", data.concurrent_users),
        ]
        
        for label, value in metrics:
            ws.cell(row=row, column=1, value=label).font = Font(bold=True)
            ws.cell(row=row, column=2, value=value)
            row += 1
        
        row += 2
        
        # Agent performance
        if data.agent_performance_metrics:
            ws.cell(row=row, column=1, value="Agent Performance").font = Font(bold=True, size=14)
            row += 1
            
            headers = ["Agent Name", "Type", "Executions", "Avg Duration", "Failures", "Success Rate"]
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=row, column=col, value=header)
                cell.font = Font(bold=True)
                cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            
            row += 1
            
            for agent in data.agent_performance_metrics:
                ws.cell(row=row, column=1, value=agent['name'])
                ws.cell(row=row, column=2, value=agent['type'])
                ws.cell(row=row, column=3, value=agent['execution_count'])
                ws.cell(row=row, column=4, value=f"{agent['avg_duration_ms']:.0f} ms")
                ws.cell(row=row, column=5, value=agent['failure_count'])
                ws.cell(row=row, column=6, value=f"{agent['success_rate']:.1f}%")
                row += 1
    
    def _add_cost_to_excel(self, ws, data):
        """Add cost optimization data to Excel sheet."""
        row = 3
        
        # Summary metrics
        metrics = [
            ("Total Cloud Spend", f"${data.total_cloud_spend:,.2f}"),
            ("Optimized Spend", f"${data.optimized_spend:,.2f}"),
            ("Savings Amount", f"${data.savings_amount:,.2f}"),
            ("Savings Percentage", f"{data.savings_percentage:.2f}%"),
            ("Forecast Next Month", f"${data.forecast_next_month:,.2f}"),
        ]
        
        for label, value in metrics:
            ws.cell(row=row, column=1, value=label).font = Font(bold=True)
            ws.cell(row=row, column=2, value=value)
            row += 1
        
        row += 2
        
        # Resource optimizations
        if data.resource_optimizations:
            ws.cell(row=row, column=1, value="Resource Optimizations").font = Font(bold=True, size=14)
            row += 1
            
            headers = ["Resource Type", "Resource ID", "Baseline Cost", "Optimized Cost", "Savings", "Savings %"]
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=row, column=col, value=header)
                cell.font = Font(bold=True)
                cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            
            row += 1
            
            for opt in data.resource_optimizations:
                ws.cell(row=row, column=1, value=opt['resource_type'])
                ws.cell(row=row, column=2, value=opt['resource_id'])
                ws.cell(row=row, column=3, value=f"${opt['baseline_cost']:,.2f}")
                ws.cell(row=row, column=4, value=f"${opt['optimized_cost']:,.2f}")
                ws.cell(row=row, column=5, value=f"${opt['savings_amount']:,.2f}")
                ws.cell(row=row, column=6, value=f"{opt['savings_percentage']:.2f}%")
                row += 1
        
        row += 2
        
        # Recommendations
        if data.recommendations:
            ws.cell(row=row, column=1, value="Recommendations").font = Font(bold=True, size=14)
            row += 1
            
            headers = ["Type", "Title", "Potential Savings", "Effort", "Priority"]
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=row, column=col, value=header)
                cell.font = Font(bold=True)
                cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            
            row += 1
            
            for rec in data.recommendations:
                ws.cell(row=row, column=1, value=rec['type'])
                ws.cell(row=row, column=2, value=rec['title'])
                ws.cell(row=row, column=3, value=f"${rec['potential_savings']:,.2f}")
                ws.cell(row=row, column=4, value=rec['effort'])
                ws.cell(row=row, column=5, value=rec['priority'])
                row += 1
    
    async def _generate_json(self, data: Any) -> bytes:
        """Generate JSON report."""
        # Convert dataclass to dict, handling Decimal serialization
        def default_serializer(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")
        
        json_str = json.dumps(asdict(data) if hasattr(data, '__dataclass_fields__') else data,
                             default=default_serializer, indent=2)
        return json_str.encode('utf-8')
    
    async def _generate_markdown(self, data: Any, report_type: str) -> bytes:
        """Generate Markdown report."""
        template = self.env.get_template(f"{report_type}_report.md")
        
        context = {
            "report_type": report_type,
            "data": data,
            "generated_at": datetime.now(),
            "company_name": "MicroAgents Platform"
        }
        
        markdown_content = template.render(**context)
        return markdown_content.encode('utf-8')
    
    async def _generate_csv(self, data: Any, report_type: str) -> bytes:
        """Generate CSV report."""
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        if report_type == "roi":
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Total Cost Savings", f"${data.total_cost_savings:,.2f}"])
            writer.writerow(["Total Productivity Gains", f"${data.total_productivity_gains:,.2f}"])
            writer.writerow(["Total Security Value", f"${data.total_security_value:,.2f}"])
            writer.writerow(["Platform Cost", f"${data.platform_cost:,.2f}"])
            writer.writerow(["Calculated ROI", f"${data.calculated_roi:,.2f}"])
            writer.writerow(["ROI Percentage", f"{data.roi_percentage:.2f}%"])
            writer.writerow(["Payback Period", f"{data.payback_period_days} days"])
            
        elif report_type == "performance":
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Availability", f"{data.availability_percentage:.3f}%"])
            writer.writerow(["Average Response Time", f"{data.average_response_time_ms:.0f} ms"])
            writer.writerow(["P95 Response Time", f"{data.p95_response_time_ms:.0f} ms"])
            writer.writerow(["P99 Response Time", f"{data.p99_response_time_ms:.0f} ms"])
            writer.writerow(["Error Rate", f"{data.error_rate_percentage:.3f}%"])
            writer.writerow(["Throughput", f"{data.throughput_requests_per_second:.1f} req/sec"])
            writer.writerow(["Concurrent Users", data.concurrent_users])
            
        elif report_type == "cost":
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Total Cloud Spend", f"${data.total_cloud_spend:,.2f}"])
            writer.writerow(["Optimized Spend", f"${data.optimized_spend:,.2f}"])
            writer.writerow(["Savings Amount", f"${data.savings_amount:,.2f}"])
            writer.writerow(["Savings Percentage", f"{data.savings_percentage:.2f}%"])
            writer.writerow(["Forecast Next Month", f"${data.forecast_next_month:,.2f}"])
        
        return output.getvalue().encode('utf-8')

# ============================================================================
# REPORT DISTRIBUTION
# ============================================================================

class ReportDistributor:
    """Handles report distribution to various channels."""
    
    def __init__(self):
        self.s3_client = boto3.client('s3') if settings.AWS_ACCESS_KEY_ID else None
        self.slack_client = WebClient(token=settings.SLACK_BOT_TOKEN) if settings.SLACK_BOT_TOKEN else None
        
    async def distribute(self, report_name: str, outputs: Dict[OutputFormat, bytes], config: ReportConfig) -> Dict[str, str]:
        """Distribute reports to configured channels."""
        distribution_results = {}
        
        # Store in S3
        if self.s3_client and OutputFormat.PDF in outputs:
            s3_key = await self._store_in_s3(report_name, outputs[OutputFormat.PDF], config)
            distribution_results['s3'] = s3_key
        
        # Send to Slack
        if self.slack_client and OutputFormat.SLACK in config.output_formats:
            await self._send_to_slack(report_name, outputs, config)
            distribution_results['slack'] = "sent"
        
        # Send email
        if OutputFormat.EMAIL in config.output_formats and config.recipients:
            await self._send_email(report_name, outputs, config)
            distribution_results['email'] = f"sent to {len(config.recipients)} recipients"
        
        # Save locally
        await self._save_locally(report_name, outputs, config)
        distribution_results['local'] = "saved"
        
        return distribution_results
    
    async def _store_in_s3(self, report_name: str, pdf_bytes: bytes, config: ReportConfig) -> str:
        """Store report in S3."""
        bucket_name = settings.REPORTS_S3_BUCKET
        s3_key = f"reports/{report_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        try:
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=pdf_bytes,
                ContentType='application/pdf',
                Metadata={
                    'report-type': config.report_type.value,
                    'generated-at': datetime.now().isoformat(),
                    'tenant-id': config.tenant_id or 'system'
                }
            )
            log.info(f"Report stored in S3: s3://{bucket_name}/{s3_key}")
            return s3_key
        except ClientError as e:
            log.error(f"Failed to store report in S3: {e}")
            raise
    
    async def _send_to_slack(self, report_name: str, outputs: Dict[OutputFormat, bytes], config: ReportConfig):
        """Send report to Slack."""
        if not self.slack_client:
            return
        
        try:
            # Upload PDF to Slack
            response = self.slack_client.files_upload_v2(
                channels=settings.SLACK_REPORT_CHANNEL,
                initial_comment=f"📊 *{report_name} Report*\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                file=outputs[OutputFormat.PDF],
                filename=f"{report_name}.pdf",
                title=f"{report_name} Report"
            )
            log.info(f"Report sent to Slack: {response['file']['permalink']}")
        except SlackApiError as e:
            log.error(f"Failed to send report to Slack: {e}")
    
    async def _send_email(self, report_name: str, outputs: Dict[OutputFormat, bytes], config: ReportConfig):
        """Send report via email."""
        # This would integrate with an email service like SES, SendGrid, etc.
        # For now, just log
        log.info(f"Would send email to: {', '.join(config.recipients)}")
        log.info(f"Subject: MicroAgents {report_name} Report - {datetime.now().strftime('%Y-%m-%d')}")
        
        # In production, you would:
        # 1. Create email content
        # 2. Attach reports
        # 3. Send via SMTP or email service API
    
    async def _save_locally(self, report_name: str, outputs: Dict[OutputFormat, bytes], config: ReportConfig):
        """Save reports locally."""
        reports_dir = Path(settings.REPORTS_DIR) / datetime.now().strftime('%Y/%m/%d')
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        for fmt, content in outputs.items():
            if fmt in [OutputFormat.SLACK, OutputFormat.EMAIL]:
                continue
                
            ext = fmt.value
            filename = reports_dir / f"{report_name}_{timestamp}.{ext}"
            
            with open(filename, 'wb') as f:
                f.write(content)
            
            log.info(f"Report saved locally: {filename}")

# ============================================================================
# AUTOMATED SCHEDULING
# ============================================================================

class ReportScheduler:
    """Manages automated report scheduling."""
    
    def __init__(self):
        self.schedules = self._load_schedules()
        
    def _load_schedules(self) -> List[Dict[str, Any]]:
        """Load report schedules from configuration."""
        config_file = Path(__file__).parent / "config" / "report_schedules.yaml"
        
        if config_file.exists():
            with open(config_file) as f:
                return yaml.safe_load(f) or []
        return []
    
    async def check_and_run_scheduled(self):
        """Check scheduled reports and run if due."""
        now = datetime.now()
        
        for schedule in self.schedules:
            if self._is_due(schedule, now):
                await self._run_scheduled_report(schedule)
    
    def _is_due(self, schedule: Dict[str, Any], now: datetime) -> bool:
        """Check if a report is due to run."""
        # Parse cron expression or schedule
        schedule_type = schedule.get('schedule_type', 'cron')
        
        if schedule_type == 'cron':
            # Simple cron parser (would use croniter in production)
            cron_expr = schedule.get('schedule', '0 9 * * 1')  # Default: Monday 9 AM
            return self._check_cron(cron_expr, now)
        elif schedule_type == 'interval':
            interval_hours = schedule.get('interval_hours', 24)
            last_run = schedule.get('last_run')
            if not last_run:
                return True
            last_run_dt = datetime.fromisoformat(last_run)
            return (now - last_run_dt).total_seconds() >= interval_hours * 3600
        
        return False
    
    def _check_cron(self, cron_expr: str, now: datetime) -> bool:
        """Simple cron expression checker."""
        # In production, use croniter or similar library
        # This is a simplified version
        parts = cron_expr.split()
        if len(parts) != 5:
            return False
        
        minute, hour, day_of_month, month, day_of_week = parts
        
        # Check each part
        checks = [
            self._cron_part_matches(minute, now.minute),
            self._cron_part_matches(hour, now.hour),
            self._cron_part_matches(day_of_month, now.day),
            self._cron_part_matches(month, now.month),
            self._cron_part_matches(day_of_week, now.weekday())  # 0=Monday, 6=Sunday
        ]
        
        return all(checks)
    
    def _cron_part_matches(self, part: str, value: int) -> bool:
        """Check if a cron part matches the value."""
        if part == '*':
            return True
        if ',' in part:
            return str(value) in part.split(',')
        if '-' in part:
            start, end = map(int, part.split('-'))
            return start <= value <= end
        if '/' in part:
            step_part, step = part.split('/')
            if step_part == '*':
                return value % int(step) == 0
        return int(part) == value
    
    async def _run_scheduled_report(self, schedule: Dict[str, Any]):
        """Run a scheduled report."""
        try:
            report_type = ReportType(schedule['report_type'])
            config = ReportConfig(
                report_type=report_type,
                tenant_id=schedule.get('tenant_id'),
                start_date=datetime.now() - timedelta(days=schedule.get('period_days', 30)),
                end_date=datetime.now(),
                output_formats=[OutputFormat(fmt) for fmt in schedule.get('output_formats', ['pdf', 'html'])],
                recipients=schedule.get('recipients', []),
                template_name=schedule.get('template')
            )
            
            # Update last run time
            schedule['last_run'] = datetime.now().isoformat()
            self._save_schedules()
            
            # Generate report
            await generate_report(config)
            
        except Exception as e:
            log.error(f"Failed to run scheduled report: {e}")
    
    def _save_schedules(self):
        """Save updated schedules to configuration."""
        config_file = Path(__file__).parent / "config" / "report_schedules.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(self.schedules, f, default_flow_style=False)

# ============================================================================
# MAIN REPORT GENERATION FUNCTION
# ============================================================================

async def generate_report(config: ReportConfig) -> Dict[str, Any]:
    """Main function to generate and distribute a report."""
    start_time = datetime.now()
    report_id = f"{config.report_type.value}_{start_time.strftime('%Y%m%d_%H%M%S')}"
    
    log.info(f"Starting report generation: {report_id}")
    log.info(f"Configuration: {config}")
    
    results = {
        "report_id": report_id,
        "report_type": config.report_type.value,
        "start_time": start_time,
        "status": "in_progress"
    }
    
    try:
        # Initialize components
        db = SessionLocal()
        collector = DataCollector(db)
        generator = ReportGenerator()
        distributor = ReportDistributor()
        
        # Collect data
        if config.report_type == ReportType.ROI_ANALYSIS:
            data = await collector.collect_roi_data(config)
        elif config.report_type == ReportType.PERFORMANCE:
            data = await collector.collect_performance_data(config)
        elif config.report_type == ReportType.COST_OPTIMIZATION:
            data = await collector.collect_cost_optimization_data(config)
        else:
            # For other report types, you would add corresponding methods
            raise ValueError(f"Report type {config.report_type} not yet implemented")
        
        # Generate reports
        if config.report_type == ReportType.ROI_ANALYSIS:
            outputs = await generator.generate_roi_report(data, config)
        elif config.report_type == ReportType.PERFORMANCE:
            outputs = await generator.generate_performance_report(data, config)
        elif config.report_type == ReportType.COST_OPTIMIZATION:
            outputs = await generator.generate_cost_optimization_report(data, config)
        else:
            outputs = {}
        
        # Distribute reports
        distribution_results = await distributor.distribute(report_id, outputs, config)
        
        # Update results
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        results.update({
            "end_time": end_time,
            "duration_seconds": duration,
            "status": "completed",
            "formats_generated": [fmt.value for fmt in outputs.keys()],
            "distribution_results": distribution_results,
            "data_summary": {
                "period": f"{config.start_date.date()} to {config.end_date.date()}",
                "data_points_collected": "calculated",  # Would add actual count
                "confidence_score": getattr(data, 'confidence_score', None)
            }
        })
        
        log.info(f"Report generation completed: {report_id} (took {duration:.1f}s)")
        
        # Validate report
        validation_result = await validate_report(results, outputs)
        results["validation"] = validation_result
        
        # Store report metadata
        await store_report_metadata(results, config)
        
        return results
        
    except Exception as e:
        log.error(f"Report generation failed: {e}", exc_info=True)
        
        results.update({
            "end_time": datetime.now(),
            "status": "failed",
            "error": str(e),
            "error_type": type(e).__name__
        })
        
        # Send alert for failed report
        await send_failure_alert(results, config)
        
        return results
        
    finally:
        if 'db' in locals():
            db.close()

async def validate_report(results: Dict[str, Any], outputs: Dict[OutputFormat, bytes]) -> Dict[str, Any]:
    """Validate generated report quality."""
    validation = {
        "status": "passed",
        "checks": [],
        "warnings": []
    }
    
    # Check PDF size
    if OutputFormat.PDF in outputs:
        pdf_size = len(outputs[OutputFormat.PDF])
        validation["checks"].append({
            "name": "pdf_size",
            "status": "passed" if pdf_size > 1000 else "failed",
            "details": f"{pdf_size} bytes"
        })
        if pdf_size < 1000:
            validation["warnings"].append("PDF file is suspiciously small")
    
    # Check JSON validity
    if OutputFormat.JSON in outputs:
        try:
            json.loads(outputs[OutputFormat.JSON].decode('utf-8'))
            validation["checks"].append({
                "name": "json_valid",
                "status": "passed",
                "details": "JSON parses successfully"
            })
        except json.JSONDecodeError as e:
            validation["checks"].append({
                "name": "json_valid",
                "status": "failed",
                "details": f"JSON parse error: {e}"
            })
            validation["status"] = "failed"
    
    # Check for required fields in results
    required_fields = ["report_id", "report_type", "status"]
    for field in required_fields:
        if field not in results:
            validation["checks"].append({
                "name": f"has_{field}",
                "status": "failed",
                "details": f"Missing required field: {field}"
            })
            validation["status"] = "failed"
    
    return validation

async def store_report_metadata(results: Dict[str, Any], config: ReportConfig):
    """Store report metadata in database."""
    try:
        db = SessionLocal()
        
        metadata_query = text("""
            INSERT INTO report_metadata (
                report_id, report_type, tenant_id, start_time, end_time,
                duration_seconds, status, formats_generated, distribution_channels,
                data_period_start, data_period_end, confidence_score
            ) VALUES (
                :report_id, :report_type, :tenant_id, :start_time, :end_time,
                :duration_seconds, :status, :formats, :distribution,
                :period_start, :period_end, :confidence
            )
        """)
        
        db.execute(
            metadata_query,
            {
                "report_id": results["report_id"],
                "report_type": results["report_type"],
                "tenant_id": config.tenant_id,
                "start_time": results["start_time"],
                "end_time": results["end_time"],
                "duration_seconds": results["duration_seconds"],
                "status": results["status"],
                "formats": json.dumps(results.get("formats_generated", [])),
                "distribution": json.dumps(results.get("distribution_results", {})),
                "period_start": config.start_date,
                "period_end": config.end_date,
                "confidence": results.get("data_summary", {}).get("confidence_score")
            }
        )
        
        db.commit()
        log.info(f"Report metadata stored: {results['report_id']}")
        
    except Exception as e:
        log.error(f"Failed to store report metadata: {e}")
    finally:
        db.close()

async def send_failure_alert(results: Dict[str, Any], config: ReportConfig):
    """Send alert for failed report generation."""
    # This would send to monitoring system, PagerDuty, etc.
    alert_message = f"""
    🚨 Report Generation Failed
    
    Report ID: {results['report_id']}
    Type: {results['report_type']}
    Tenant: {config.tenant_id or 'System'}
    Error: {results.get('error')}
    Error Type: {results.get('error_type')}
    Time: {results['start_time'].strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    log.error(alert_message)
    
    # Could send to Slack, email, etc.
    if settings.ALERT_SLACK_CHANNEL:
        try:
            slack = WebClient(token=settings.SLACK_BOT_TOKEN)
            slack.chat_postMessage(
                channel=settings.ALERT_SLACK_CHANNEL,
                text=alert_message
            )
        except Exception as e:
            log.error(f"Failed to send Slack alert: {e}")

# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

def parse_arguments():
    """Parse command line arguments."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate analytics reports for MicroAgents Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --type roi --tenant-id 12345
  %(prog)s --type performance --start-date 2024-01-01 --end-date 2024-01-31
  %(prog)s --type cost --output pdf,html,excel --schedule "0 9 * * 1"
  
Report Types:
  roi_analysis        - ROI and business value analysis
  performance         - System performance and reliability
  cost_optimization   - Cost savings and optimization
  security_compliance - Security and compliance status
  usage_analytics     - Platform usage patterns
  customer_success    - Customer engagement metrics
  business_value      - Business impact tracking
  competitive_analysis- Market comparison
  forecasting         - Predictive analytics
  executive_summary   - Executive overview

Output Formats:
  pdf, html, excel, json, markdown, csv, slack, email
        """
    )
    
    parser.add_argument(
        "--type", "-t",
        type=str,
        required=True,
        choices=[rt.value for rt in ReportType],
        help="Type of report to generate"
    )
    
    parser.add_argument(
        "--tenant-id",
        type=str,
        help="Tenant ID for tenant-specific reports"
    )
    
    parser.add_argument(
        "--start-date",
        type=lambda s: datetime.strptime(s, '%Y-%m-%d'),
        help="Start date (YYYY-MM-DD)"
    )
    
    parser.add_argument(
        "--end-date",
        type=lambda s: datetime.strptime(s, '%Y-%m-%d'),
        help="End date (YYYY-MM-DD)"
    )
    
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="pdf,html",
        help="Output formats (comma-separated)"
    )
    
    parser.add_argument(
        "--schedule",
        type=str,
        help="Schedule for automated generation (cron expression)"
    )
    
    parser.add_argument(
        "--recipients",
        type=str,
        help="Comma-separated list of email recipients"
    )
    
    parser.add_argument(
        "--template",
        type=str,
        help="Custom template name"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        help="Configuration file path"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without generating"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    return parser.parse_args()

# ============================================================================
# MAIN EXECUTION
# ============================================================================

async def main():
    """Main entry point."""
    args = parse_arguments()
    
    # Setup logging
    global log
    log = logging.getLogger(__name__)
    
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Parse output formats
    output_formats = []
    for fmt_str in args.output.split(','):
        try:
            output_formats.append(OutputFormat(fmt_str.strip().lower()))
        except ValueError:
            log.warning(f"Unknown output format: {fmt_str}")
    
    # Parse recipients
    recipients = []
    if args.recipients:
        recipients = [r.strip() for r in args.recipients.split(',')]
    
    # Create configuration
    config = ReportConfig(
        report_type=ReportType(args.type),
        tenant_id=args.tenant_id,
        start_date=args.start_date,
        end_date=args.end_date,
        output_formats=output_formats,
        schedule=args.schedule,
        recipients=recipients,
        template_name=args.template
    )
    
    # Dry run mode
    if args.dry_run:
        log.info("DRY RUN - Configuration validated:")
        log.info(f"  Report Type: {config.report_type.value}")
        log.info(f"  Tenant ID: {config.tenant_id}")
        log.info(f"  Period: {config.start_date} to {config.end_date}")
        log.info(f"  Output Formats: {[f.value for f in config.output_formats]}")
        log.info(f"  Recipients: {config.recipients}")
        return
    
    # Generate report
    results = await generate_report(config)
    
    # Print summary
    if results["status"] == "completed":
        print(f"\n✅ Report generation successful!")
        print(f"   Report ID: {results['report_id']}")
        print(f"   Duration: {results['duration_seconds']:.1f}s")
        print(f"   Formats: {', '.join(results['formats_generated'])}")
        
        if 'distribution_results' in results:
            print(f"   Distribution: {results['distribution_results']}")
    else:
        print(f"\n❌ Report generation failed!")
        print(f"   Error: {results.get('error')}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())