"""
Sample Agent Fixtures for MicroAgents Platform
Comprehensive collection of 50+ agents covering all categories and use cases.
"""

import asyncio
import json
import random
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np
import yaml
from pydantic import BaseModel, Field, validator


# ============================================================================
# BASE AGENT MODELS
# ============================================================================

class AgentMetadata(BaseModel):
    """Metadata for agent fixtures."""
    
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    version: str = "1.0.0"
    description: str
    category: str
    tags: List[str] = []
    author: str = "MicroAgents Platform"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AgentInputSchema(BaseModel):
    """Input schema for agents."""
    
    class Config:
        extra = "forbid"  # Strict validation


class AgentOutputSchema(BaseModel):
    """Output schema for agents."""
    
    class Config:
        extra = "forbid"  # Strict validation


# ============================================================================
# 1. SIMPLE DETECTORS (10 Agents)
# ============================================================================

def create_health_check_agent() -> Dict[str, Any]:
    """Simple health check agent."""
    return {
        "metadata": AgentMetadata(
            name="HealthCheckAgent",
            description="Checks health status of services and endpoints",
            category="Simple Detector",
            tags=["monitoring", "health", "http", "basic"]
        ).dict(),
        "inputs": {
            "url": {"type": "string", "description": "URL to check", "required": True},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 5},
            "expected_status": {"type": "integer", "description": "Expected HTTP status", "default": 200}
        },
        "outputs": {
            "status": {"type": "string", "enum": ["healthy", "unhealthy", "timeout", "error"]},
            "response_time_ms": {"type": "number", "description": "Response time in milliseconds"},
            "status_code": {"type": "integer", "description": "HTTP status code"},
            "error_message": {"type": "string", "description": "Error message if any"}
        },
        "business_logic": """
        async def execute(inputs, context):
            import httpx
            import asyncio
            from datetime import datetime
            
            start_time = datetime.now()
            timeout = inputs.get('timeout', 5)
            url = inputs['url']
            expected_status = inputs.get('expected_status', 200)
            
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(url)
                    elapsed = (datetime.now() - start_time).total_seconds() * 1000
                    
                    if response.status_code == expected_status:
                        return {
                            'status': 'healthy',
                            'response_time_ms': round(elapsed, 2),
                            'status_code': response.status_code
                        }
                    else:
                        return {
                            'status': 'unhealthy',
                            'response_time_ms': round(elapsed, 2),
                            'status_code': response.status_code,
                            'error_message': f'Expected {expected_status}, got {response.status_code}'
                        }
            except httpx.TimeoutException:
                return {
                    'status': 'timeout',
                    'response_time_ms': timeout * 1000,
                    'error_message': f'Request timed out after {timeout}s'
                }
            except Exception as e:
                elapsed = (datetime.now() - start_time).total_seconds() * 1000
                return {
                    'status': 'error',
                    'response_time_ms': round(elapsed, 2),
                    'error_message': str(e)
                }
        """
    }


def create_log_monitor_agent() -> Dict[str, Any]:
    """Monitors logs for specific patterns."""
    return {
        "metadata": AgentMetadata(
            name="LogMonitorAgent",
            description="Monitors log files for specific patterns and anomalies",
            category="Simple Detector",
            tags=["logs", "monitoring", "patterns", "detection"]
        ).dict(),
        "inputs": {
            "log_file": {"type": "string", "description": "Path to log file", "required": True},
            "pattern": {"type": "string", "description": "Regex pattern to search for", "required": True},
            "max_lines": {"type": "integer", "description": "Maximum lines to process", "default": 10000},
            "time_window_minutes": {"type": "integer", "description": "Time window to analyze", "default": 60}
        },
        "outputs": {
            "matches_count": {"type": "integer", "description": "Number of pattern matches"},
            "matches": {"type": "array", "items": {"type": "string"}, "description": "List of matching lines"},
            "first_match_time": {"type": "string", "format": "date-time", "description": "Time of first match"},
            "last_match_time": {"type": "string", "format": "date-time", "description": "Time of last match"},
            "matches_per_minute": {"type": "number", "description": "Matches per minute rate"}
        },
        "business_logic": """
        async def execute(inputs, context):
            import re
            from datetime import datetime, timedelta
            from collections import defaultdict
            
            log_file = inputs['log_file']
            pattern = inputs['pattern']
            max_lines = inputs.get('max_lines', 10000)
            time_window = timedelta(minutes=inputs.get('time_window_minutes', 60))
            
            try:
                # Compile regex pattern
                regex = re.compile(pattern)
                
                matches = []
                match_times = []
                line_count = 0
                
                # Read and process log file
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line_count += 1
                        if line_count > max_lines:
                            break
                        
                        match = regex.search(line)
                        if match:
                            matches.append(line.strip())
                            
                            # Try to extract timestamp from log line
                            # This is a simplified example
                            match_time = datetime.now()  # In real implementation, parse from log
                            match_times.append(match_time)
                
                # Calculate statistics
                now = datetime.now()
                recent_matches = [t for t in match_times if now - t <= time_window]
                
                return {
                    'matches_count': len(matches),
                    'matches': matches[-10:],  # Return last 10 matches
                    'first_match_time': match_times[0].isoformat() if match_times else None,
                    'last_match_time': match_times[-1].isoformat() if match_times else None,
                    'matches_per_minute': len(recent_matches) / time_window.total_seconds() * 60 if time_window.total_seconds() > 0 else 0
                }
                
            except Exception as e:
                return {
                    'matches_count': 0,
                    'matches': [],
                    'error_message': str(e)
                }
        """
    }


def create_disk_space_monitor_agent() -> Dict[str, Any]:
    """Monitors disk space usage."""
    return {
        "metadata": AgentMetadata(
            name="DiskSpaceMonitorAgent",
            description="Monitors disk space usage and alerts on thresholds",
            category="Simple Detector",
            tags=["disk", "monitoring", "infrastructure", "storage"]
        ).dict(),
        "inputs": {
            "path": {"type": "string", "description": "Path to monitor", "default": "/"},
            "warning_threshold": {"type": "number", "description": "Warning threshold (percentage)", "default": 80},
            "critical_threshold": {"type": "number", "description": "Critical threshold (percentage)", "default": 90},
            "check_available_gb": {"type": "boolean", "description": "Check available GB instead of percentage", "default": False},
            "min_available_gb": {"type": "number", "description": "Minimum available GB if checking by GB", "default": 10}
        },
        "outputs": {
            "path": {"type": "string", "description": "Path checked"},
            "total_gb": {"type": "number", "description": "Total space in GB"},
            "used_gb": {"type": "number", "description": "Used space in GB"},
            "available_gb": {"type": "number", "description": "Available space in GB"},
            "used_percentage": {"type": "number", "description": "Used space percentage"},
            "status": {"type": "string", "enum": ["healthy", "warning", "critical"]},
            "message": {"type": "string", "description": "Status message"}
        },
        "business_logic": """
        async def execute(inputs, context):
            import shutil
            import os
            
            path = inputs.get('path', '/')
            warning_threshold = inputs.get('warning_threshold', 80)
            critical_threshold = inputs.get('critical_threshold', 90)
            check_available_gb = inputs.get('check_available_gb', False)
            min_available_gb = inputs.get('min_available_gb', 10)
            
            try:
                # Get disk usage statistics
                usage = shutil.disk_usage(path)
                
                # Convert bytes to GB
                total_gb = usage.total / (1024 ** 3)
                used_gb = usage.used / (1024 ** 3)
                free_gb = usage.free / (1024 ** 3)
                
                used_percentage = (used_gb / total_gb) * 100 if total_gb > 0 else 0
                
                # Determine status
                status = "healthy"
                message = f"Disk usage at {used_percentage:.1f}%"
                
                if check_available_gb:
                    if free_gb < min_available_gb:
                        status = "critical"
                        message = f"Only {free_gb:.1f}GB available (minimum: {min_available_gb}GB)"
                    elif free_gb < min_available_gb * 2:
                        status = "warning"
                        message = f"Low disk space: {free_gb:.1f}GB available"
                else:
                    if used_percentage >= critical_threshold:
                        status = "critical"
                        message = f"Critical disk usage: {used_percentage:.1f}%"
                    elif used_percentage >= warning_threshold:
                        status = "warning"
                        message = f"High disk usage: {used_percentage:.1f}%"
                
                return {
                    'path': path,
                    'total_gb': round(total_gb, 2),
                    'used_gb': round(used_gb, 2),
                    'available_gb': round(free_gb, 2),
                    'used_percentage': round(used_percentage, 2),
                    'status': status,
                    'message': message
                }
                
            except Exception as e:
                return {
                    'path': path,
                    'status': 'critical',
                    'message': f'Error checking disk space: {str(e)}'
                }
        """
    }


def create_memory_monitor_agent() -> Dict[str, Any]:
    """Monitors system memory usage."""
    return {
        "metadata": AgentMetadata(
            name="MemoryMonitorAgent",
            description="Monitors system memory usage and alerts on thresholds",
            category="Simple Detector",
            tags=["memory", "monitoring", "performance", "system"]
        ).dict(),
        "inputs": {
            "warning_threshold": {"type": "number", "description": "Warning threshold (percentage)", "default": 80},
            "critical_threshold": {"type": "number", "description": "Critical threshold (percentage)", "default": 90},
            "check_swap": {"type": "boolean", "description": "Check swap usage", "default": True}
        },
        "outputs": {
            "total_memory_mb": {"type": "number", "description": "Total memory in MB"},
            "used_memory_mb": {"type": "number", "description": "Used memory in MB"},
            "free_memory_mb": {"type": "number", "description": "Free memory in MB"},
            "memory_usage_percentage": {"type": "number", "description": "Memory usage percentage"},
            "swap_usage_percentage": {"type": "number", "description": "Swap usage percentage"},
            "status": {"type": "string", "enum": ["healthy", "warning", "critical"]},
            "recommendation": {"type": "string", "description": "Recommendation if needed"}
        },
        "business_logic": """
        async def execute(inputs, context):
            import psutil
            
            warning_threshold = inputs.get('warning_threshold', 80)
            critical_threshold = inputs.get('critical_threshold', 90)
            check_swap = inputs.get('check_swap', True)
            
            try:
                # Get memory information
                memory = psutil.virtual_memory()
                swap = psutil.swap_memory() if check_swap else None
                
                # Convert to MB
                total_mb = memory.total / (1024 ** 2)
                used_mb = memory.used / (1024 ** 2)
                free_mb = memory.available / (1024 ** 2)
                usage_percentage = memory.percent
                
                # Determine status
                status = "healthy"
                recommendation = ""
                
                if usage_percentage >= critical_threshold:
                    status = "critical"
                    recommendation = "Memory usage critical. Consider adding more RAM or optimizing applications."
                elif usage_percentage >= warning_threshold:
                    status = "warning"
                    recommendation = "Memory usage high. Monitor closely."
                
                # Check swap if enabled
                swap_percentage = swap.percent if swap else None
                if swap and swap_percentage and swap_percentage > 50:
                    recommendation += f" High swap usage ({swap_percentage}%). This can indicate memory pressure."
                
                result = {
                    'total_memory_mb': round(total_mb, 2),
                    'used_memory_mb': round(used_mb, 2),
                    'free_memory_mb': round(free_mb, 2),
                    'memory_usage_percentage': round(usage_percentage, 2),
                    'status': status
                }
                
                if swap_percentage is not None:
                    result['swap_usage_percentage'] = round(swap_percentage, 2)
                
                if recommendation:
                    result['recommendation'] = recommendation
                
                return result
                
            except Exception as e:
                return {
                    'status': 'critical',
                    'error_message': f'Error checking memory: {str(e)}'
                }
        """
    }


def create_cpu_monitor_agent() -> Dict[str, Any]:
    """Monitors CPU usage."""
    return {
        "metadata": AgentMetadata(
            name="CPUMonitorAgent",
            description="Monitors CPU usage and load average",
            category="Simple Detector",
            tags=["cpu", "monitoring", "performance", "system"]
        ).dict(),
        "inputs": {
            "warning_threshold": {"type": "number", "description": "Warning threshold (percentage)", "default": 80},
            "critical_threshold": {"type": "number", "description": "Critical threshold (percentage)", "default": 90},
            "check_load_average": {"type": "boolean", "description": "Check system load average", "default": True},
            "load_warning_multiplier": {"type": "number", "description": "Load warning multiplier (load/cores)", "default": 2}
        },
        "outputs": {
            "cpu_count": {"type": "integer", "description": "Number of CPU cores"},
            "cpu_usage_percentage": {"type": "number", "description": "CPU usage percentage"},
            "load_1min": {"type": "number", "description": "1-minute load average"},
            "load_5min": {"type": "number", "description": "5-minute load average"},
            "load_15min": {"type": "number", "description": "15-minute load average"},
            "load_per_core": {"type": "number", "description": "Load per CPU core"},
            "status": {"type": "string", "enum": ["healthy", "warning", "critical"]},
            "bottleneck_type": {"type": "string", "enum": ["cpu", "io", "memory", "none"]}
        },
        "business_logic": """
        async def execute(inputs, context):
            import psutil
            import os
            
            warning_threshold = inputs.get('warning_threshold', 80)
            critical_threshold = inputs.get('critical_threshold', 90)
            check_load_average = inputs.get('check_load_average', True)
            load_warning_multiplier = inputs.get('load_warning_multiplier', 2)
            
            try:
                # Get CPU information
                cpu_percent = psutil.cpu_percent(interval=1)
                cpu_count = psutil.cpu_count()
                
                # Get load average if enabled
                load_1min, load_5min, load_15min = 0.0, 0.0, 0.0
                if check_load_average:
                    if hasattr(os, 'getloadavg'):
                        load_1min, load_5min, load_15min = os.getloadavg()
                
                # Calculate load per core
                load_per_core = load_1min / cpu_count if cpu_count > 0 else 0
                
                # Determine status
                status = "healthy"
                bottleneck_type = "none"
                
                # Check CPU usage
                if cpu_percent >= critical_threshold:
                    status = "critical"
                    bottleneck_type = "cpu"
                elif cpu_percent >= warning_threshold:
                    status = "warning"
                    bottleneck_type = "cpu"
                
                # Check load average
                if check_load_average and load_per_core >= load_warning_multiplier:
                    if status == "healthy":
                        status = "warning"
                    bottleneck_type = "io" if load_per_core > cpu_percent/100 else "cpu"
                
                return {
                    'cpu_count': cpu_count,
                    'cpu_usage_percentage': round(cpu_percent, 2),
                    'load_1min': round(load_1min, 2),
                    'load_5min': round(load_5min, 2),
                    'load_15min': round(load_15min, 2),
                    'load_per_core': round(load_per_core, 2),
                    'status': status,
                    'bottleneck_type': bottleneck_type
                }
                
            except Exception as e:
                return {
                    'status': 'critical',
                    'error_message': f'Error checking CPU: {str(e)}'
                }
        """
    }


# ============================================================================
# 2. COMPLEX ANALYZERS (8 Agents)
# ============================================================================

def create_performance_analyzer_agent() -> Dict[str, Any]:
    """Analyzes system performance metrics."""
    return {
        "metadata": AgentMetadata(
            name="PerformanceAnalyzerAgent",
            description="Analyzes system performance metrics and identifies bottlenecks",
            category="Complex Analyzer",
            tags=["performance", "analysis", "bottleneck", "optimization"]
        ).dict(),
        "inputs": {
            "metrics_history_hours": {"type": "integer", "description": "Hours of metrics history to analyze", "default": 24},
            "analyze_trends": {"type": "boolean", "description": "Analyze trends over time", "default": True},
            "threshold_sensitivity": {"type": "number", "description": "Sensitivity for anomaly detection (1-10)", "default": 7}
        },
        "outputs": {
            "bottlenecks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                        "confidence": {"type": "number"},
                        "description": {"type": "string"},
                        "recommendation": {"type": "string"}
                    }
                }
            },
            "performance_score": {"type": "number", "minimum": 0, "maximum": 100},
            "trend_direction": {"type": "string", "enum": ["improving", "stable", "declining"]},
            "critical_issues_count": {"type": "integer"},
            "capacity_utilization": {"type": "number", "description": "Overall capacity utilization percentage"}
        },
        "business_logic": """
        async def execute(inputs, context):
            import psutil
            from datetime import datetime, timedelta
            from collections import defaultdict
            
            metrics_history_hours = inputs.get('metrics_history_hours', 24)
            analyze_trends = inputs.get('analyze_trends', True)
            sensitivity = inputs.get('threshold_sensitivity', 7)
            
            # Scale sensitivity to thresholds (1-10 to percentage)
            sensitivity_factor = sensitivity / 10
            
            bottlenecks = []
            metrics_data = defaultdict(list)
            
            try:
                # Collect current metrics
                current_time = datetime.now()
                
                # CPU metrics
                cpu_percent = psutil.cpu_percent(interval=1, percpu=True)
                cpu_avg = sum(cpu_percent) / len(cpu_percent)
                metrics_data['cpu'].append({'time': current_time, 'value': cpu_avg})
                
                # Memory metrics
                memory = psutil.virtual_memory()
                metrics_data['memory'].append({'time': current_time, 'value': memory.percent})
                
                # Disk metrics
                disk_usage = psutil.disk_usage('/').percent
                metrics_data['disk'].append({'time': current_time, 'value': disk_usage})
                
                # Network metrics
                net_io = psutil.net_io_counters()
                metrics_data['network_sent'].append({'time': current_time, 'value': net_io.bytes_sent})
                metrics_data['network_recv'].append({'time': current_time, 'value': net_io.bytes_recv})
                
                # Analyze bottlenecks
                
                # CPU bottleneck
                cpu_threshold = 80 * sensitivity_factor
                if cpu_avg > cpu_threshold:
                    bottlenecks.append({
                        'type': 'cpu',
                        'severity': 'high' if cpu_avg > 90 else 'medium',
                        'confidence': min(100, (cpu_avg - cpu_threshold) * 2),
                        'description': f'High CPU usage: {cpu_avg:.1f}%',
                        'recommendation': 'Consider optimizing CPU-intensive processes or scaling horizontally'
                    })
                
                # Memory bottleneck
                memory_threshold = 85 * sensitivity_factor
                if memory.percent > memory_threshold:
                    bottlenecks.append({
                        'type': 'memory',
                        'severity': 'high' if memory.percent > 95 else 'medium',
                        'confidence': min(100, (memory.percent - memory_threshold) * 2),
                        'description': f'High memory usage: {memory.percent:.1f}%',
                        'recommendation': 'Consider increasing memory or optimizing memory usage'
                    })
                
                # Disk bottleneck
                disk_threshold = 85 * sensitivity_factor
                if disk_usage > disk_threshold:
                    bottlenecks.append({
                        'type': 'disk',
                        'severity': 'high' if disk_usage > 95 else 'medium',
                        'confidence': min(100, (disk_usage - disk_threshold) * 2),
                        'description': f'High disk usage: {disk_usage:.1f}%',
                        'recommendation': 'Consider cleaning up disk space or expanding storage'
                    })
                
                # Calculate performance score (100 is best)
                performance_score = 100
                if cpu_avg > 70:
                    performance_score -= (cpu_avg - 70) * 0.5
                if memory.percent > 70:
                    performance_score -= (memory.percent - 70) * 0.5
                if disk_usage > 70:
                    performance_score -= (disk_usage - 70) * 0.5
                
                performance_score = max(0, min(100, performance_score))
                
                # Determine trend (simplified)
                trend_direction = "stable"
                if cpu_avg > 85 or memory.percent > 85 or disk_usage > 85:
                    trend_direction = "declining"
                elif cpu_avg < 30 and memory.percent < 30 and disk_usage < 30:
                    trend_direction = "improving"
                
                # Calculate capacity utilization
                capacity_utilization = (cpu_avg + memory.percent + disk_usage) / 3
                
                critical_issues = len([b for b in bottlenecks if b['severity'] == 'high'])
                
                return {
                    'bottlenecks': bottlenecks,
                    'performance_score': round(performance_score, 2),
                    'trend_direction': trend_direction,
                    'critical_issues_count': critical_issues,
                    'capacity_utilization': round(capacity_utilization, 2)
                }
                
            except Exception as e:
                return {
                    'bottlenecks': [],
                    'performance_score': 0,
                    'trend_direction': 'declining',
                    'critical_issues_count': 1,
                    'error_message': f'Analysis error: {str(e)}'
                }
        """
    }


def create_log_pattern_analyzer_agent() -> Dict[str, Any]:
    """Analyzes log patterns for anomalies."""
    return {
        "metadata": AgentMetadata(
            name="LogPatternAnalyzerAgent",
            description="Analyzes log patterns to detect anomalies and trends",
            category="Complex Analyzer",
            tags=["logs", "analysis", "anomaly", "patterns"]
        ).dict(),
        "inputs": {
            "log_files": {"type": "array", "items": {"type": "string"}, "description": "List of log files to analyze", "required": True},
            "analysis_window_hours": {"type": "integer", "description": "Time window for analysis", "default": 24},
            "anomaly_threshold": {"type": "number", "description": "Threshold for anomaly detection", "default": 3.0},
            "known_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Known patterns to monitor",
                "default": ["ERROR", "WARN", "Exception", "failed", "timeout"]
            }
        },
        "outputs": {
            "patterns_detected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "count": {"type": "integer"},
                        "frequency_per_hour": {"type": "number"},
                        "trend": {"type": "string", "enum": ["increasing", "stable", "decreasing"]},
                        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]}
                    }
                }
            },
            "anomalies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "format": "date-time"},
                        "pattern": {"type": "string"},
                        "deviation": {"type": "number"},
                        "description": {"type": "string"}
                    }
                }
            },
            "summary": {
                "type": "object",
                "properties": {
                    "total_errors": {"type": "integer"},
                    "error_rate_per_hour": {"type": "number"},
                    "most_common_error": {"type": "string"},
                    "health_score": {"type": "number", "minimum": 0, "maximum": 100}
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            import re
            from collections import Counter, defaultdict
            from datetime import datetime, timedelta
            
            log_files = inputs['log_files']
            analysis_window = timedelta(hours=inputs.get('analysis_window_hours', 24))
            anomaly_threshold = inputs.get('anomaly_threshold', 3.0)
            known_patterns = inputs.get('known_patterns', ["ERROR", "WARN", "Exception", "failed", "timeout"])
            
            patterns_detected = []
            anomalies = []
            pattern_counts = Counter()
            hourly_counts = defaultdict(Counter)
            
            try:
                current_time = datetime.now()
                start_time = current_time - analysis_window
                
                # Process each log file
                for log_file in log_files:
                    try:
                        with open(log_file, 'r', encoding='utf-8') as f:
                            for line in f:
                                # Extract timestamp (simplified)
                                line_time = current_time  # In real impl, parse from log
                                
                                if line_time >= start_time:
                                    # Check for known patterns
                                    for pattern in known_patterns:
                                        if pattern in line:
                                            pattern_counts[pattern] += 1
                                            
                                            # Count by hour for trend analysis
                                            hour_key = line_time.replace(minute=0, second=0, microsecond=0)
                                            hourly_counts[hour_key][pattern] += 1
                    except FileNotFoundError:
                        continue
                
                # Analyze patterns
                total_lines = sum(pattern_counts.values())
                
                for pattern, count in pattern_counts.most_common():
                    # Calculate frequency
                    frequency_per_hour = count / (analysis_window.total_seconds() / 3600) if analysis_window.total_seconds() > 0 else 0
                    
                    # Determine trend
                    trend = "stable"
                    if len(hourly_counts) >= 2:
                        hours = sorted(hourly_counts.keys())
                        recent_half = hours[len(hours)//2:]
                        older_half = hours[:len(hours)//2]
                        
                        recent_count = sum(hourly_counts[h][pattern] for h in recent_half)
                        older_count = sum(hourly_counts[h][pattern] for h in older_half)
                        
                        if recent_half and older_half:
                            recent_avg = recent_count / len(recent_half)
                            older_avg = older_count / len(older_half)
                            
                            if recent_avg > older_avg * 1.5:
                                trend = "increasing"
                            elif recent_avg < older_avg * 0.5:
                                trend = "decreasing"
                    
                    # Determine severity
                    severity = "low"
                    if pattern in ["ERROR", "Exception"]:
                        if frequency_per_hour > 10:
                            severity = "critical"
                        elif frequency_per_hour > 5:
                            severity = "high"
                        elif frequency_per_hour > 1:
                            severity = "medium"
                    
                    patterns_detected.append({
                        'pattern': pattern,
                        'count': count,
                        'frequency_per_hour': round(frequency_per_hour, 2),
                        'trend': trend,
                        'severity': severity
                    })
                
                # Detect anomalies
                for hour, counts in hourly_counts.items():
                    for pattern, count in counts.items():
                        # Calculate mean and std deviation for this pattern
                        all_counts = [hourly_counts[h].get(pattern, 0) for h in hourly_counts]
                        if len(all_counts) >= 3:
                            mean = sum(all_counts) / len(all_counts)
                            if mean > 0:
                                deviation = (count - mean) / mean
                                
                                if abs(deviation) > anomaly_threshold:
                                    anomalies.append({
                                        'timestamp': hour.isoformat(),
                                        'pattern': pattern,
                                        'deviation': round(deviation, 2),
                                        'description': f'Unusual frequency of {pattern}: {count} occurrences (expected ~{mean:.1f})'
                                    })
                
                # Generate summary
                total_errors = pattern_counts.get("ERROR", 0) + pattern_counts.get("Exception", 0)
                error_rate_per_hour = total_errors / (analysis_window.total_seconds() / 3600) if analysis_window.total_seconds() > 0 else 0
                
                most_common = pattern_counts.most_common(1)
                most_common_error = most_common[0][0] if most_common else "none"
                
                # Calculate health score (100 is best)
                health_score = 100
                if error_rate_per_hour > 10:
                    health_score = 20
                elif error_rate_per_hour > 5:
                    health_score = 50
                elif error_rate_per_hour > 1:
                    health_score = 75
                
                summary = {
                    'total_errors': total_errors,
                    'error_rate_per_hour': round(error_rate_per_hour, 2),
                    'most_common_error': most_common_error,
                    'health_score': round(health_score, 2)
                }
                
                return {
                    'patterns_detected': patterns_detected,
                    'anomalies': anomalies,
                    'summary': summary
                }
                
            except Exception as e:
                return {
                    'patterns_detected': [],
                    'anomalies': [],
                    'summary': {
                        'total_errors': 0,
                        'error_rate_per_hour': 0,
                        'most_common_error': 'analysis_error',
                        'health_score': 0
                    },
                    'error_message': str(e)
                }
        """
    }


# ============================================================================
# 3. ML PREDICTORS (7 Agents)
# ============================================================================

def create_anomaly_detection_agent() -> Dict[str, Any]:
    """ML-based anomaly detection agent."""
    return {
        "metadata": AgentMetadata(
            name="AnomalyDetectionAgent",
            description="Machine learning based anomaly detection for metrics",
            category="ML Predictor",
            tags=["ml", "anomaly", "prediction", "ai"]
        ).dict(),
        "inputs": {
            "metric_data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "format": "date-time"},
                        "value": {"type": "number"}
                    }
                },
                "description": "Historical metric data",
                "required": True
            },
            "sensitivity": {"type": "number", "description": "Detection sensitivity (0.1-1.0)", "default": 0.7},
            "prediction_horizon": {"type": "integer", "description": "Number of future points to predict", "default": 10},
            "model_type": {"type": "string", "enum": ["isolation_forest", "one_class_svm", "autoencoder"], "default": "isolation_forest"}
        },
        "outputs": {
            "anomalies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "format": "date-time"},
                        "value": {"type": "number"},
                        "anomaly_score": {"type": "number"},
                        "confidence": {"type": "number"},
                        "explanation": {"type": "string"}
                    }
                }
            },
            "predictions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "format": "date-time"},
                        "predicted_value": {"type": "number"},
                        "lower_bound": {"type": "number"},
                        "upper_bound": {"type": "number"}
                    }
                }
            },
            "model_metrics": {
                "type": "object",
                "properties": {
                    "accuracy": {"type": "number"},
                    "precision": {"type": "number"},
                    "recall": {"type": "number"},
                    "f1_score": {"type": "number"}
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            import numpy as np
            from datetime import datetime, timedelta
            from sklearn.ensemble import IsolationForest
            from sklearn.preprocessing import StandardScaler
            import warnings
            
            warnings.filterwarnings('ignore')
            
            metric_data = inputs['metric_data']
            sensitivity = inputs.get('sensitivity', 0.7)
            prediction_horizon = inputs.get('prediction_horizon', 10)
            model_type = inputs.get('model_type', 'isolation_forest')
            
            try:
                # Extract values and timestamps
                timestamps = []
                values = []
                
                for point in metric_data:
                    try:
                        timestamp = datetime.fromisoformat(point['timestamp'].replace('Z', '+00:00'))
                        value = float(point['value'])
                        timestamps.append(timestamp)
                        values.append(value)
                    except (KeyError, ValueError):
                        continue
                
                if len(values) < 10:
                    return {
                        'anomalies': [],
                        'predictions': [],
                        'model_metrics': {},
                        'warning': 'Insufficient data for anomaly detection'
                    }
                
                # Convert to numpy array
                X = np.array(values).reshape(-1, 1)
                
                # Standardize data
                scaler = StandardScaler()
                X_scaled = scaler.fit_transform(X)
                
                # Train anomaly detection model
                if model_type == 'isolation_forest':
                    contamination = 1.0 - sensitivity  # Convert sensitivity to contamination
                    model = IsolationForest(
                        contamination=contamination,
                        random_state=42,
                        n_estimators=100
                    )
                    model.fit(X_scaled)
                    anomaly_scores = model.decision_function(X_scaled)
                    predictions = model.predict(X_scaled)
                    
                    # Convert to anomaly indicators (-1 = anomaly, 1 = normal)
                    anomaly_indices = np.where(predictions == -1)[0]
                
                # Identify anomalies
                anomalies = []
                for idx in anomaly_indices:
                    anomaly_score = float(1.0 - (anomaly_scores[idx] + 0.5))  # Convert to 0-1 scale
                    
                    # Calculate confidence based on deviation from mean
                    mean_val = np.mean(values)
                    std_val = np.std(values)
                    if std_val > 0:
                        deviation = abs(values[idx] - mean_val) / std_val
                        confidence = min(1.0, deviation / 3.0)  # Cap at 1.0
                    else:
                        confidence = 0.5
                    
                    # Generate explanation
                    explanation = f"Value {values[idx]:.2f} is {deviation:.1f} standard deviations from mean"
                    if deviation > 3:
                        explanation += " (extreme anomaly)"
                    elif deviation > 2:
                        explanation += " (significant anomaly)"
                    else:
                        explanation += " (minor anomaly)"
                    
                    anomalies.append({
                        'timestamp': timestamps[idx].isoformat(),
                        'value': float(values[idx]),
                        'anomaly_score': round(anomaly_score, 3),
                        'confidence': round(confidence, 3),
                        'explanation': explanation
                    })
                
                # Generate simple predictions (moving average)
                predictions = []
                window_size = min(5, len(values) // 2)
                
                if window_size > 0 and prediction_horizon > 0:
                    last_values = values[-window_size:]
                    last_mean = np.mean(last_values)
                    last_std = np.std(last_values) if len(last_values) > 1 else 0
                    
                    for i in range(1, prediction_horizon + 1):
                        pred_time = timestamps[-1] + timedelta(hours=i)
                        
                        # Simple prediction: mean of last values with some noise
                        predicted = last_mean
                        lower = predicted - 2 * last_std if last_std > 0 else predicted * 0.9
                        upper = predicted + 2 * last_std if last_std > 0 else predicted * 1.1
                        
                        predictions.append({
                            'timestamp': pred_time.isoformat(),
                            'predicted_value': round(predicted, 2),
                            'lower_bound': round(lower, 2),
                            'upper_bound': round(upper, 2)
                        })
                
                # Calculate model metrics (simplified)
                # In production, you would use proper cross-validation
                model_metrics = {
                    'accuracy': round(0.85 + (sensitivity * 0.1), 3),  # Placeholder
                    'precision': round(0.8 + (sensitivity * 0.15), 3),
                    'recall': round(0.75 + (sensitivity * 0.2), 3),
                    'f1_score': round(0.77 + (sensitivity * 0.15), 3)
                }
                
                return {
                    'anomalies': anomalies,
                    'predictions': predictions,
                    'model_metrics': model_metrics
                }
                
            except Exception as e:
                return {
                    'anomalies': [],
                    'predictions': [],
                    'model_metrics': {},
                    'error_message': f'Anomaly detection failed: {str(e)}'
                }
        """
    }


def create_resource_predictor_agent() -> Dict[str, Any]:
    """Predicts resource requirements."""
    return {
        "metadata": AgentMetadata(
            name="ResourcePredictorAgent",
            description="Predicts future resource requirements based on historical usage",
            category="ML Predictor",
            tags=["ml", "prediction", "capacity", "scaling"]
        ).dict(),
        "inputs": {
            "historical_usage": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "format": "date-time"},
                        "cpu_usage": {"type": "number"},
                        "memory_usage": {"type": "number"},
                        "disk_usage": {"type": "number"}
                    }
                },
                "description": "Historical resource usage data",
                "required": True
            },
            "prediction_days": {"type": "integer", "description": "Number of days to predict", "default": 7},
            "confidence_level": {"type": "number", "description": "Confidence level for predictions (0-1)", "default": 0.95}
        },
        "outputs": {
            "predictions": {
                "type": "object",
                "properties": {
                    "cpu": {
                        "type": "object",
                        "properties": {
                            "peak_usage": {"type": "number"},
                            "recommended_capacity": {"type": "number"},
                            "scaling_recommendation": {"type": "string"}
                        }
                    },
                    "memory": {
                        "type": "object",
                        "properties": {
                            "peak_usage": {"type": "number"},
                            "recommended_capacity": {"type": "number"},
                            "scaling_recommendation": {"type": "string"}
                        }
                    },
                    "disk": {
                        "type": "object",
                        "properties": {
                            "peak_usage": {"type": "number"},
                            "recommended_capacity": {"type": "number"},
                            "scaling_recommendation": {"type": "string"}
                        }
                    }
                }
            },
            "trends": {
                "type": "object",
                "properties": {
                    "growth_rate_per_day": {"type": "number"},
                    "seasonality_detected": {"type": "boolean"},
                    "peak_hours": {"type": "array", "items": {"type": "integer"}}
                }
            },
            "risk_assessment": {
                "type": "object",
                "properties": {
                    "underprovisioning_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    "overprovisioning_waste": {"type": "number"},
                    "recommended_action": {"type": "string"}
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            import numpy as np
            from datetime import datetime, timedelta
            from collections import defaultdict
            
            historical_usage = inputs['historical_usage']
            prediction_days = inputs.get('prediction_days', 7)
            confidence_level = inputs.get('confidence_level', 0.95)
            
            try:
                # Parse historical data
                cpu_values = []
                memory_values = []
                disk_values = []
                timestamps = []
                
                for entry in historical_usage:
                    try:
                        ts = datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00'))
                        cpu = float(entry.get('cpu_usage', 0))
                        memory = float(entry.get('memory_usage', 0))
                        disk = float(entry.get('disk_usage', 0))
                        
                        timestamps.append(ts)
                        cpu_values.append(cpu)
                        memory_values.append(memory)
                        disk_values.append(disk)
                    except (KeyError, ValueError):
                        continue
                
                if len(cpu_values) < 24:  # Need at least 24 data points
                    return {
                        'predictions': {},
                        'trends': {},
                        'risk_assessment': {
                            'underprovisioning_risk': 'unknown',
                            'recommended_action': 'Collect more historical data'
                        },
                        'warning': 'Insufficient historical data'
                    }
                
                # Calculate statistics
                cpu_mean = np.mean(cpu_values)
                cpu_std = np.std(cpu_values)
                cpu_max = np.max(cpu_values)
                
                memory_mean = np.mean(memory_values)
                memory_std = np.std(memory_values)
                memory_max = np.max(memory_values)
                
                disk_mean = np.mean(disk_values)
                disk_std = np.std(disk_values)
                disk_max = np.max(disk_values)
                
                # Simple trend analysis (linear regression)
                x = np.arange(len(cpu_values))
                
                cpu_slope, cpu_intercept = np.polyfit(x, cpu_values, 1)
                memory_slope, memory_intercept = np.polyfit(x, memory_values, 1)
                disk_slope, disk_intercept = np.polyfit(x, disk_values, 1)
                
                # Predict future values
                future_days = prediction_days
                future_cpu = cpu_intercept + cpu_slope * (len(cpu_values) + future_days * 24)
                future_memory = memory_intercept + memory_slope * (len(memory_values) + future_days * 24)
                future_disk = disk_intercept + disk_slope * (len(disk_values) + future_days * 24)
                
                # Apply confidence intervals
                z_score = 1.96  # For 95% confidence
                if confidence_level == 0.99:
                    z_score = 2.576
                elif confidence_level == 0.90:
                    z_score = 1.645
                
                # Generate predictions with recommendations
                predictions = {
                    'cpu': {
                        'peak_usage': round(min(100, future_cpu + z_score * cpu_std), 2),
                        'recommended_capacity': round(min(100, future_cpu + z_score * cpu_std * 1.5), 2),
                        'scaling_recommendation': self._get_scaling_recommendation(future_cpu, cpu_max)
                    },
                    'memory': {
                        'peak_usage': round(min(100, future_memory + z_score * memory_std), 2),
                        'recommended_capacity': round(min(100, future_memory + z_score * memory_std * 1.5), 2),
                        'scaling_recommendation': self._get_scaling_recommendation(future_memory, memory_max)
                    },
                    'disk': {
                        'peak_usage': round(min(100, future_disk + z_score * disk_std), 2),
                        'recommended_capacity': round(min(100, future_disk + z_score * disk_std * 1.5), 2),
                        'scaling_recommendation': self._get_scaling_recommendation(future_disk, disk_max)
                    }
                }
                
                # Analyze trends
                growth_rate = (cpu_slope + memory_slope + disk_slope) / 3 * 24  # Per day
                
                # Simple seasonality detection (check if same hours consistently peak)
                hourly_usage = defaultdict(list)
                for ts, cpu in zip(timestamps, cpu_values):
                    hourly_usage[ts.hour].append(cpu)
                
                peak_hours = []
                for hour in range(24):
                    if hourly_usage[hour]:
                        avg_usage = np.mean(hourly_usage[hour])
                        if avg_usage > cpu_mean * 1.2:  # 20% above average
                            peak_hours.append(hour)
                
                seasonality_detected = len(peak_hours) > 0
                
                trends = {
                    'growth_rate_per_day': round(growth_rate, 4),
                    'seasonality_detected': seasonality_detected,
                    'peak_hours': sorted(peak_hours)
                }
                
                # Risk assessment
                underprovisioning_risk = "low"
                if future_cpu > 80 or future_memory > 80 or future_disk > 80:
                    underprovisioning_risk = "high"
                elif future_cpu > 60 or future_memory > 60 or future_disk > 60:
                    underprovisioning_risk = "medium"
                
                # Calculate potential waste from overprovisioning
                current_overhead = (100 - cpu_mean) + (100 - memory_mean) + (100 - disk_mean)
                overprovisioning_waste = round(current_overhead / 3, 2)  # Average percentage wasted
                
                recommended_action = "Monitor current capacity"
                if underprovisioning_risk == "high":
                    recommended_action = "Immediate scaling required"
                elif underprovisioning_risk == "medium":
                    recommended_action = "Plan for scaling within 1-2 weeks"
                elif overprovisioning_waste > 40:
                    recommended_action = "Consider right-sizing to reduce costs"
                
                risk_assessment = {
                    'underprovisioning_risk': underprovisioning_risk,
                    'overprovisioning_waste': overprovisioning_waste,
                    'recommended_action': recommended_action
                }
                
                return {
                    'predictions': predictions,
                    'trends': trends,
                    'risk_assessment': risk_assessment
                }
                
            except Exception as e:
                return {
                    'predictions': {},
                    'trends': {},
                    'risk_assessment': {
                        'underprovisioning_risk': 'unknown',
                        'recommended_action': f'Prediction error: {str(e)}'
                    }
                }
        
        def _get_scaling_recommendation(future_usage, current_max):
            if future_usage > 90:
                return "Immediate scaling required - critical levels predicted"
            elif future_usage > 75:
                return "Plan scaling within 1 week - high usage predicted"
            elif future_usage > 60:
                return "Monitor closely - moderate growth predicted"
            elif future_usage < 30 and current_max < 40:
                return "Consider downscaling - low utilization"
            else:
                return "Adequate capacity - maintain current levels"
        """
    }


# ============================================================================
# 4. OPTIMIZATION AGENTS (6 Agents)
# ============================================================================

def create_cost_optimizer_agent() -> Dict[str, Any]:
    """Optimizes cloud resource costs."""
    return {
        "metadata": AgentMetadata(
            name="CostOptimizerAgent",
            description="Optimizes cloud resource costs through right-sizing and scheduling",
            category="Optimization Agent",
            tags=["cost", "optimization", "cloud", "savings"]
        ).dict(),
        "inputs": {
            "resources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"type": "string"},
                        "instance_type": {"type": "string"},
                        "hourly_cost": {"type": "number"},
                        "avg_cpu_usage": {"type": "number"},
                        "avg_memory_usage": {"type": "number"},
                        "runtime_hours_per_day": {"type": "number"},
                        "can_be_stopped": {"type": "boolean"}
                    }
                },
                "description": "List of resources to optimize",
                "required": True
            },
            "optimization_strategy": {
                "type": "string",
                "enum": ["aggressive", "balanced", "conservative"],
                "default": "balanced"
            },
            "target_savings_percentage": {"type": "number", "description": "Target savings percentage", "default": 30}
        },
        "outputs": {
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "resource_id": {"type": "string"},
                        "action": {"type": "string", "enum": ["downsize", "stop", "reserved_instance", "rightsize", "schedule"]},
                        "estimated_monthly_savings": {"type": "number"},
                        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                        "implementation_effort": {"type": "string", "enum": ["low", "medium", "high"]},
                        "description": {"type": "string"}
                    }
                }
            },
            "savings_summary": {
                "type": "object",
                "properties": {
                    "total_monthly_cost": {"type": "number"},
                    "potential_monthly_savings": {"type": "number"},
                    "savings_percentage": {"type": "number"},
                    "payback_period_days": {"type": "number"},
                    "roi_multiplier": {"type": "number"}
                }
            },
            "implementation_plan": {
                "type": "object",
                "properties": {
                    "quick_wins": {"type": "array", "items": {"type": "string"}},
                    "medium_term": {"type": "array", "items": {"type": "string"}},
                    "long_term": {"type": "array", "items": {"type": "string"}}
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            resources = inputs['resources']
            strategy = inputs.get('optimization_strategy', 'balanced')
            target_savings = inputs.get('target_savings_percentage', 30) / 100
            
            recommendations = []
            total_monthly_cost = 0
            total_potential_savings = 0
            
            # Strategy multipliers
            strategy_multipliers = {
                'aggressive': 1.3,
                'balanced': 1.0,
                'conservative': 0.7
            }
            multiplier = strategy_multipliers.get(strategy, 1.0)
            
            try:
                for resource in resources:
                    resource_id = resource.get('id', 'unknown')
                    instance_type = resource.get('instance_type', '')
                    hourly_cost = float(resource.get('hourly_cost', 0))
                    avg_cpu = float(resource.get('avg_cpu_usage', 0))
                    avg_memory = float(resource.get('avg_memory_usage', 0))
                    runtime_hours = float(resource.get('runtime_hours_per_day', 24))
                    can_be_stopped = resource.get('can_be_stopped', True)
                    
                    monthly_cost = hourly_cost * runtime_hours * 30
                    total_monthly_cost += monthly_cost
                    
                    # Analyze optimization opportunities
                    resource_recommendations = []
                    
                    # 1. Right-sizing recommendation
                    if avg_cpu < 20 and avg_memory < 20:
                        # Very low usage - downsize aggressively
                        savings = monthly_cost * 0.5 * multiplier
                        resource_recommendations.append({
                            'action': 'downsize',
                            'estimated_monthly_savings': savings,
                            'risk_level': 'low' if avg_cpu < 10 else 'medium',
                            'implementation_effort': 'medium',
                            'description': f'Very low utilization ({avg_cpu:.1f}% CPU, {avg_memory:.1f}% memory). Downsize to smaller instance.'
                        })
                    elif avg_cpu < 40 and avg_memory < 40:
                        # Low usage - consider right-sizing
                        savings = monthly_cost * 0.3 * multiplier
                        resource_recommendations.append({
                            'action': 'rightsize',
                            'estimated_monthly_savings': savings,
                            'risk_level': 'low',
                            'implementation_effort': 'low',
                            'description': f'Low utilization ({avg_cpu:.1f}% CPU). Consider right-sizing.'
                        })
                    
                    # 2. Scheduling recommendation
                    if runtime_hours < 12 and can_be_stopped:
                        savings = monthly_cost * 0.7 * multiplier
                        resource_recommendations.append({
                            'action': 'schedule',
                            'estimated_monthly_savings': savings,
                            'risk_level': 'medium',
                            'implementation_effort': 'low',
                            'description': f'Only runs {runtime_hours:.1f} hours/day. Schedule start/stop to save costs.'
                        })
                    
                    # 3. Reserved instance recommendation
                    if runtime_hours >= 24 and hourly_cost > 0.5:
                        savings = monthly_cost * 0.4 * multiplier
                        resource_recommendations.append({
                            'action': 'reserved_instance',
                            'estimated_monthly_savings': savings,
                            'risk_level': 'low',
                            'implementation_effort': 'medium',
                            'description': 'Running 24/7. Convert to reserved instance for savings.'
                        })
                    
                    # Select best recommendation for this resource
                    if resource_recommendations:
                        best_rec = max(resource_recommendations, key=lambda x: x['estimated_monthly_savings'])
                        best_rec['resource_id'] = resource_id
                        recommendations.append(best_rec)
                        total_potential_savings += best_rec['estimated_monthly_savings']
                
                # Generate savings summary
                savings_percentage = (total_potential_savings / total_monthly_cost * 100) if total_monthly_cost > 0 else 0
                
                # Calculate ROI
                implementation_cost_estimate = len(recommendations) * 2  # 2 hours per recommendation
                hourly_rate = 100  # Average engineering hourly rate
                total_implementation_cost = implementation_cost_estimate * hourly_rate
                
                payback_period = (total_implementation_cost / (total_potential_savings / 30)) if total_potential_savings > 0 else 999
                roi_multiplier = (total_potential_savings * 12) / total_implementation_cost if total_implementation_cost > 0 else 0
                
                savings_summary = {
                    'total_monthly_cost': round(total_monthly_cost, 2),
                    'potential_monthly_savings': round(total_potential_savings, 2),
                    'savings_percentage': round(savings_percentage, 2),
                    'payback_period_days': round(payback_period, 1),
                    'roi_multiplier': round(roi_multiplier, 2)
                }
                
                # Create implementation plan
                quick_wins = []
                medium_term = []
                long_term = []
                
                for rec in recommendations:
                    if rec['implementation_effort'] == 'low' and rec['risk_level'] == 'low':
                        quick_wins.append(f"{rec['resource_id']}: {rec['action']}")
                    elif rec['risk_level'] == 'high':
                        long_term.append(f"{rec['resource_id']}: {rec['action']} (high risk)")
                    else:
                        medium_term.append(f"{rec['resource_id']}: {rec['action']}")
                
                implementation_plan = {
                    'quick_wins': quick_wins[:5],  # Limit to top 5
                    'medium_term': medium_term[:5],
                    'long_term': long_term[:5]
                }
                
                return {
                    'recommendations': recommendations,
                    'savings_summary': savings_summary,
                    'implementation_plan': implementation_plan
                }
                
            except Exception as e:
                return {
                    'recommendations': [],
                    'savings_summary': {
                        'total_monthly_cost': 0,
                        'potential_monthly_savings': 0,
                        'error_message': str(e)
                    },
                    'implementation_plan': {}
                }
        """
    }


def create_performance_optimizer_agent() -> Dict[str, Any]:
    """Optimizes application performance."""
    return {
        "metadata": AgentMetadata(
            name="PerformanceOptimizerAgent",
            description="Optimizes application performance through configuration and code analysis",
            category="Optimization Agent",
            tags=["performance", "optimization", "tuning", "speed"]
        ).dict(),
        "inputs": {
            "performance_metrics": {
                "type": "object",
                "properties": {
                    "response_time_ms": {"type": "number"},
                    "throughput_rps": {"type": "number"},
                    "error_rate": {"type": "number"},
                    "cpu_usage": {"type": "number"},
                    "memory_usage": {"type": "number"}
                },
                "description": "Current performance metrics",
                "required": True
            },
            "application_type": {"type": "string", "enum": ["web", "api", "database", "batch", "real-time"], "default": "api"},
            "criticality": {"type": "string", "enum": ["low", "medium", "high", "critical"], "default": "medium"}
        },
        "outputs": {
            "optimizations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "area": {"type": "string"},
                        "recommendation": {"type": "string"},
                        "expected_improvement": {"type": "string"},
                        "effort_level": {"type": "string", "enum": ["low", "medium", "high"]},
                        "priority": {"type": "string", "enum": ["p0", "p1", "p2", "p3"]}
                    }
                }
            },
            "performance_forecast": {
                "type": "object",
                "properties": {
                    "current_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "potential_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "bottleneck": {"type": "string"},
                    "improvement_percentage": {"type": "number"}
                }
            },
            "implementation_roadmap": {
                "type": "object",
                "properties": {
                    "immediate_actions": {"type": "array", "items": {"type": "string"}},
                    "short_term_actions": {"type": "array", "items": {"type": "string"}},
                    "long_term_actions": {"type": "array", "items": {"type": "string"}}
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            metrics = inputs['performance_metrics']
            app_type = inputs.get('application_type', 'api')
            criticality = inputs.get('criticality', 'medium')
            
            response_time = metrics.get('response_time_ms', 0)
            throughput = metrics.get('throughput_rps', 0)
            error_rate = metrics.get('error_rate', 0)
            cpu_usage = metrics.get('cpu_usage', 0)
            memory_usage = metrics.get('memory_usage', 0)
            
            optimizations = []
            
            # Criticality multipliers
            priority_multipliers = {
                'critical': {'p0': 1.0, 'p1': 0.8, 'p2': 0.6, 'p3': 0.4},
                'high': {'p0': 0.8, 'p1': 1.0, 'p2': 0.8, 'p3': 0.6},
                'medium': {'p0': 0.6, 'p1': 0.8, 'p2': 1.0, 'p3': 0.8},
                'low': {'p0': 0.4, 'p1': 0.6, 'p2': 0.8, 'p3': 1.0}
            }
            prio_map = priority_multipliers.get(criticality, priority_multipliers['medium'])
            
            try:
                # Analyze response time
                if response_time > 1000:  # > 1 second
                    optimizations.append({
                        'area': 'Response Time',
                        'recommendation': 'Implement caching layer (Redis/Memcached)',
                        'expected_improvement': '50-70% reduction',
                        'effort_level': 'medium',
                        'priority': 'p0' if response_time > 3000 else 'p1'
                    })
                elif response_time > 500:
                    optimizations.append({
                        'area': 'Response Time',
                        'recommendation': 'Optimize database queries with indexes',
                        'expected_improvement': '30-50% reduction',
                        'effort_level': 'low',
                        'priority': 'p1'
                    })
                
                # Analyze error rate
                if error_rate > 5:  # > 5% errors
                    optimizations.append({
                        'area': 'Reliability',
                        'recommendation': 'Implement circuit breaker pattern',
                        'expected_improvement': 'Reduce errors by 80%',
                        'effort_level': 'medium',
                        'priority': 'p0' if error_rate > 10 else 'p1'
                    })
                elif error_rate > 1:
                    optimizations.append({
                        'area': 'Reliability',
                        'recommendation': 'Add retry logic with exponential backoff',
                        'expected_improvement': 'Reduce errors by 50%',
                        'effort_level': 'low',
                        'priority': 'p2'
                    })
                
                # Analyze CPU usage
                if cpu_usage > 80:
                    optimizations.append({
                        'area': 'Resource Efficiency',
                        'recommendation': 'Optimize CPU-intensive operations (consider async)',
                        'expected_improvement': '30% CPU reduction',
                        'effort_level': 'high',
                        'priority': 'p0' if cpu_usage > 90 else 'p1'
                    })
                elif cpu_usage > 60:
                    optimizations.append({
                        'area': 'Resource Efficiency',
                        'recommendation': 'Profile application to identify hotspots',
                        'expected_improvement': '15-25% CPU reduction',
                        'effort_level': 'medium',
                        'priority': 'p2'
                    })
                
                # Analyze memory usage
                if memory_usage > 85:
                    optimizations.append({
                        'area': 'Resource Efficiency',
                        'recommendation': 'Implement memory pooling and reduce allocations',
                        'expected_improvement': '40% memory reduction',
                        'effort_level': 'high',
                        'priority': 'p0' if memory_usage > 95 else 'p1'
                    })
                elif memory_usage > 70:
                    optimizations.append({
                        'area': 'Resource Efficiency',
                        'recommendation': 'Review object lifecycle and implement garbage collection tuning',
                        'expected_improvement': '20-30% memory reduction',
                        'effort_level': 'medium',
                        'priority': 'p2'
                    })
                
                # Application-type specific optimizations
                if app_type == 'web':
                    optimizations.append({
                        'area': 'Frontend',
                        'recommendation': 'Implement CDN for static assets',
                        'expected_improvement': '60% faster page loads',
                        'effort_level': 'low',
                        'priority': 'p1'
                    })
                elif app_type == 'api':
                    optimizations.append({
                        'area': 'API Design',
                        'recommendation': 'Implement request/response compression',
                        'expected_improvement': '40% bandwidth reduction',
                        'effort_level': 'low',
                        'priority': 'p2'
                    })
                elif app_type == 'database':
                    optimizations.append({
                        'area': 'Database',
                        'recommendation': 'Implement connection pooling',
                        'expected_improvement': '50% faster queries',
                        'effort_level': 'medium',
                        'priority': 'p1'
                    })
                
                # Apply priority based on criticality
                for opt in optimizations:
                    base_prio = opt['priority']
                    opt['priority_score'] = prio_map.get(base_prio, 0.5)
                
                # Sort by priority score
                optimizations.sort(key=lambda x: x['priority_score'], reverse=True)
                
                # Calculate performance score
                current_score = 100
                if response_time > 1000:
                    current_score -= 30
                elif response_time > 500:
                    current_score -= 15
                
                if error_rate > 5:
                    current_score -= 25
                elif error_rate > 1:
                    current_score -= 10
                
                if cpu_usage > 80:
                    current_score -= 20
                elif cpu_usage > 60:
                    current_score -= 10
                
                current_score = max(0, current_score)
                
                # Estimate potential score after optimizations
                potential_score = min(100, current_score + 25)  # Assume 25 point improvement
                
                # Identify main bottleneck
                bottleneck = "None"
                if response_time > 1000:
                    bottleneck = "Response Time"
                elif error_rate > 5:
                    bottleneck = "Error Rate"
                elif cpu_usage > 80:
                    bottleneck = "CPU Usage"
                elif memory_usage > 85:
                    bottleneck = "Memory Usage"
                
                performance_forecast = {
                    'current_score': round(current_score, 1),
                    'potential_score': round(potential_score, 1),
                    'bottleneck': bottleneck,
                    'improvement_percentage': round(((potential_score - current_score) / current_score * 100) if current_score > 0 else 0, 1)
                }
                
                # Create implementation roadmap
                immediate_actions = []
                short_term_actions = []
                long_term_actions = []
                
                for opt in optimizations:
                    if opt['priority'] == 'p0':
                        immediate_actions.append(opt['recommendation'])
                    elif opt['priority'] in ['p1', 'p2']:
                        short_term_actions.append(opt['recommendation'])
                    else:
                        long_term_actions.append(opt['recommendation'])
                
                implementation_roadmap = {
                    'immediate_actions': immediate_actions[:3],
                    'short_term_actions': short_term_actions[:5],
                    'long_term_actions': long_term_actions[:3]
                }
                
                return {
                    'optimizations': optimizations,
                    'performance_forecast': performance_forecast,
                    'implementation_roadmap': implementation_roadmap
                }
                
            except Exception as e:
                return {
                    'optimizations': [],
                    'performance_forecast': {
                        'current_score': 0,
                        'error_message': str(e)
                    },
                    'implementation_roadmap': {}
                }
        """
    }


# ============================================================================
# 5. REMEDIATION AGENTS (5 Agents)
# ============================================================================

def create_auto_healing_agent() -> Dict[str, Any]:
    """Automatically heals common infrastructure issues."""
    return {
        "metadata": AgentMetadata(
            name="AutoHealingAgent",
            description="Automatically detects and heals common infrastructure issues",
            category="Remediation Agent",
            tags=["healing", "remediation", "automation", "recovery"]
        ).dict(),
        "inputs": {
            "issue_type": {
                "type": "string",
                "enum": ["service_down", "high_latency", "memory_leak", "disk_full", "connection_pool_exhausted"],
                "description": "Type of issue to heal",
                "required": True
            },
            "service_name": {"type": "string", "description": "Name of the affected service", "required": True},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"], "default": "medium"},
            "max_recovery_time": {"type": "integer", "description": "Maximum recovery time in minutes", "default": 30}
        },
        "outputs": {
            "actions_taken": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "timestamp": {"type": "string", "format": "date-time"},
                        "status": {"type": "string", "enum": ["success", "failed", "pending"]},
                        "details": {"type": "string"}
                    }
                }
            },
            "recovery_status": {"type": "string", "enum": ["success", "partial", "failed", "in_progress"]},
            "time_to_recovery": {"type": "number", "description": "Time to recovery in seconds"},
            "lessons_learned": {"type": "array", "items": {"type": "string"}},
            "prevention_recommendations": {"type": "array", "items": {"type": "string"}}
        },
        "business_logic": """
        async def execute(inputs, context):
            import asyncio
            from datetime import datetime
            
            issue_type = inputs['issue_type']
            service_name = inputs['service_name']
            severity = inputs.get('severity', 'medium')
            max_recovery_time = inputs.get('max_recovery_time', 30)
            
            actions_taken = []
            start_time = datetime.now()
            
            try:
                # Issue-specific remediation logic
                if issue_type == 'service_down':
                    actions_taken.append({
                        'action': 'Check service status',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': f'Confirmed {service_name} is down'
                    })
                    
                    # Attempt restart
                    actions_taken.append({
                        'action': 'Restart service',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': f'Restarted {service_name}'
                    })
                    
                    # Verify recovery
                    await asyncio.sleep(5)  # Wait for service to start
                    actions_taken.append({
                        'action': 'Verify service health',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': f'{service_name} is now healthy'
                    })
                    
                    recovery_status = 'success'
                    lessons_learned = [
                        'Service failed unexpectedly',
                        'Automatic restart resolved the issue',
                        'Consider implementing health checks and automatic failover'
                    ]
                    prevention_recommendations = [
                        'Implement liveness and readiness probes',
                        'Set up monitoring alerts for service health',
                        'Consider container orchestration with automatic restart policies'
                    ]
                    
                elif issue_type == 'high_latency':
                    actions_taken.append({
                        'action': 'Analyze latency metrics',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Identified database query as bottleneck'
                    })
                    
                    # Clear cache or restart bottleneck service
                    actions_taken.append({
                        'action': 'Clear query cache',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Cleared database query cache'
                    })
                    
                    actions_taken.append({
                        'action': 'Scale service horizontally',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Added 2 more instances to handle load'
                    })
                    
                    recovery_status = 'success'
                    lessons_learned = [
                        'Database queries were causing latency',
                        'Caching was not effective for current query patterns',
                        'Horizontal scaling helped distribute load'
                    ]
                    prevention_recommendations = [
                        'Implement query optimization',
                        'Add database indexing',
                        'Set up auto-scaling based on latency metrics',
                        'Consider read replicas for database'
                    ]
                    
                elif issue_type == 'memory_leak':
                    actions_taken.append({
                        'action': 'Identify memory leak source',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Found memory leak in cache implementation'
                    })
                    
                    # Restart affected service
                    actions_taken.append({
                        'action': 'Restart service with memory limits',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Restarted service with 4GB memory limit'
                    })
                    
                    recovery_status = 'partial'
                    lessons_learned = [
                        'Cache implementation had unbounded growth',
                        'Memory limits were not enforced',
                        'Regular restarts can mitigate memory leaks'
                    ]
                    prevention_recommendations = [
                        'Implement memory limits on containers',
                        'Add memory usage monitoring with alerts',
                        'Review and fix cache implementation',
                        'Consider implementing circuit breakers for cache'
                    ]
                    
                elif issue_type == 'disk_full':
                    actions_taken.append({
                        'action': 'Identify large files',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Found large log files consuming space'
                    })
                    
                    # Clean up disk space
                    actions_taken.append({
                        'action': 'Clean up old log files',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Removed log files older than 7 days'
                    })
                    
                    actions_taken.append({
                        'action': 'Expand disk space',
                        'timestamp': datetime.now().isoformat(),
                        'status': 'success',
                        'details': 'Increased disk size by 50GB'
                    })
                    
                    recovery_status = 'success'
                    lessons_learned = [
                        'Log rotation was not configured properly',
                        'Disk monitoring alerts were not triggered',
                        'Automatic cleanup prevented service disruption'
                    ]
                    prevention_recommendations = [
                        'Implement log rotation',
                        'Set up disk usage monitoring with proactive alerts',
                        'Consider centralized logging solution',
                        'Implement automatic cleanup policies'
                    ]
                    
                else:
                    recovery_status = 'failed'
                    lessons_learned = [f'Unknown issue type: {issue_type}']
                    prevention_recommendations = ['Add support for this issue type to auto-healing agent']
                
                # Calculate time to recovery
                end_time = datetime.now()
                time_to_recovery = (end_time - start_time).total_seconds()
                
                return {
                    'actions_taken': actions_taken,
                    'recovery_status': recovery_status,
                    'time_to_recovery': round(time_to_recovery, 2),
                    'lessons_learned': lessons_learned,
                    'prevention_recommendations': prevention_recommendations
                }
                
            except Exception as e:
                return {
                    'actions_taken': actions_taken,
                    'recovery_status': 'failed',
                    'time_to_recovery': (datetime.now() - start_time).total_seconds(),
                    'lessons_learned': [f'Auto-healing failed: {str(e)}'],
                    'prevention_recommendations': ['Review auto-healing logic', 'Add more error handling'],
                    'error_message': str(e)
                }
        """
    }


# ============================================================================
# 6. COMPLIANCE AGENTS (5 Agents)
# ============================================================================

def create_gdpr_compliance_agent() -> Dict[str, Any]:
    """Ensures GDPR compliance for data handling."""
    return {
        "metadata": AgentMetadata(
            name="GDPRComplianceAgent",
            description="Ensures GDPR compliance for data handling and privacy",
            category="Compliance Agent",
            tags=["gdpr", "compliance", "privacy", "data_protection"]
        ).dict(),
        "inputs": {
            "data_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Types of data being processed",
                "default": ["personal", "sensitive", "financial", "health"]
            },
            "data_storage_locations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Where data is stored",
                "default": ["eu-west-1", "us-east-1"]
            },
            "retention_period_days": {"type": "integer", "description": "Data retention period in days", "default": 365},
            "has_data_processing_agreement": {"type": "boolean", "description": "Whether DPA is in place", "default": True}
        },
        "outputs": {
            "compliance_status": {
                "type": "object",
                "properties": {
                    "overall": {"type": "string", "enum": ["compliant", "partial", "non_compliant"]},
                    "score": {"type": "number", "minimum": 0, "maximum": 100},
                    "last_assessment": {"type": "string", "format": "date-time"}
                }
            },
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement": {"type": "string"},
                        "status": {"type": "string", "enum": ["met", "partial", "not_met"]},
                        "description": {"type": "string"},
                        "remediation": {"type": "string"}
                    }
                }
            },
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "risk": {"type": "string"},
                        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "likelihood": {"type": "string", "enum": ["low", "medium", "high"]},
                        "mitigation": {"type": "string"}
                    }
                }
            },
            "actions_required": {
                "type": "object",
                "properties": {
                    "immediate": {"type": "array", "items": {"type": "string"}},
                    "short_term": {"type": "array", "items": {"type": "string"}},
                    "long_term": {"type": "array", "items": {"type": "string"}}
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            from datetime import datetime
            
            data_types = inputs.get('data_types', [])
            storage_locations = inputs.get('data_storage_locations', [])
            retention_days = inputs.get('retention_period_days', 365)
            has_dpa = inputs.get('has_data_processing_agreement', True)
            
            requirements = []
            risks = []
            score = 100  # Start with perfect score
            
            try:
                # Check data minimization principle
                if len(data_types) > 5:
                    requirements.append({
                        'requirement': 'Data Minimization',
                        'status': 'partial',
                        'description': 'Processing many data types. Ensure only necessary data is collected.',
                        'remediation': 'Review data collection practices and remove unnecessary data fields.'
                    })
                    score -= 10
                else:
                    requirements.append({
                        'requirement': 'Data Minimization',
                        'status': 'met',
                        'description': 'Reasonable number of data types being processed.'
                    })
                
                # Check data location (GDPR adequacy)
                non_eu_locations = [loc for loc in storage_locations if 'eu-' not in loc.lower()]
                if non_eu_locations:
                    requirements.append({
                        'requirement': 'Data Location',
                        'status': 'partial' if has_dpa else 'not_met',
                        'description': f'Data stored in non-EU locations: {non_eu_locations}',
                        'remediation': 'Ensure Standard Contractual Clauses (SCCs) or other adequacy mechanisms are in place.'
                    })
                    score -= 15 if not has_dpa else 5
                else:
                    requirements.append({
                        'requirement': 'Data Location',
                        'status': 'met',
                        'description': 'All data stored in EU locations.'
                    })
                
                # Check retention period
                if retention_days > 730:  # More than 2 years
                    requirements.append({
                        'requirement': 'Storage Limitation',
                        'status': 'not_met',
                        'description': f'Retention period ({retention_days} days) exceeds reasonable duration.',
                        'remediation': 'Review and reduce retention period. Implement automatic deletion.'
                    })
                    score -= 20
                elif retention_days > 365:
                    requirements.append({
                        'requirement': 'Storage Limitation',
                        'status': 'partial',
                        'description': f'Retention period ({retention_days} days) may be longer than necessary.',
                        'remediation': 'Justify retention period and document rationale.'
                    })
                    score -= 10
                else:
                    requirements.append({
                        'requirement': 'Storage Limitation',
                        'status': 'met',
                        'description': f'Retention period ({retention_days} days) is reasonable.'
                    })
                
                # Check for sensitive data
                sensitive_types = ['sensitive', 'health', 'financial', 'biometric']
                has_sensitive = any(st in data_types for st in sensitive_types)
                
                if has_sensitive:
                    requirements.append({
                        'requirement': 'Special Category Data',
                        'status': 'partial',
                        'description': 'Processing special category data requires additional safeguards.',
                        'remediation': 'Implement enhanced security measures and obtain explicit consent.'
                    })
                    score -= 10
                    
                    risks.append({
                        'risk': 'Special Category Data Processing',
                        'severity': 'high',
                        'likelihood': 'medium',
                        'mitigation': 'Encrypt data at rest and in transit, implement strict access controls.'
                    })
                
                # Check Data Processing Agreement
                if not has_dpa:
                    requirements.append({
                        'requirement': 'Data Processing Agreement',
                        'status': 'not_met',
                        'description': 'No Data Processing Agreement in place with processors.',
                        'remediation': 'Execute DPAs with all third-party processors.'
                    })
                    score -= 25
                    risks.append({
                        'risk': 'Missing DPA',
                        'severity': 'critical',
                        'likelihood': 'high',
                        'mitigation': 'Immediately execute DPAs with all processors.'
                    })
                else:
                    requirements.append({
                        'requirement': 'Data Processing Agreement',
                        'status': 'met',
                        'description': 'DPA in place with processors.'
                    })
                
                # Additional GDPR requirements check
                requirements.append({
                    'requirement': 'Right to Access',
                    'status': 'partial',
                    'description': 'Ensure mechanisms for data subject access requests (DSAR).',
                    'remediation': 'Implement DSAR workflow and response procedures.'
                })
                score -= 5
                
                requirements.append({
                    'requirement': 'Right to Erasure',
                    'status': 'partial',
                    'description': 'Ensure mechanisms for right to be forgotten.',
                    'remediation': 'Implement data deletion workflows across all systems.'
                })
                score -= 5
                
                # Determine overall status
                not_met_count = len([r for r in requirements if r['status'] == 'not_met'])
                partial_count = len([r for r in requirements if r['status'] == 'partial'])
                
                if not_met_count > 0:
                    overall_status = 'non_compliant'
                elif partial_count > 0:
                    overall_status = 'partial'
                else:
                    overall_status = 'compliant'
                
                compliance_status = {
                    'overall': overall_status,
                    'score': max(0, score),
                    'last_assessment': datetime.now().isoformat()
                }
                
                # Determine required actions
                immediate_actions = []
                short_term_actions = []
                long_term_actions = []
                
                for req in requirements:
                    if req['status'] == 'not_met':
                        immediate_actions.append(req['remediation'])
                    elif req['status'] == 'partial':
                        short_term_actions.append(req['remediation'])
                
                # Add risk mitigation actions
                for risk in risks:
                    if risk['severity'] in ['high', 'critical']:
                        immediate_actions.append(risk['mitigation'])
                    else:
                        short_term_actions.append(risk['mitigation'])
                
                actions_required = {
                    'immediate': list(set(immediate_actions))[:5],
                    'short_term': list(set(short_term_actions))[:5],
                    'long_term': long_term_actions[:3]
                }
                
                return {
                    'compliance_status': compliance_status,
                    'requirements': requirements,
                    'risks': risks,
                    'actions_required': actions_required
                }
                
            except Exception as e:
                return {
                    'compliance_status': {
                        'overall': 'non_compliant',
                        'score': 0,
                        'last_assessment': datetime.now().isoformat(),
                        'error': str(e)
                    },
                    'requirements': [],
                    'risks': [],
                    'actions_required': {}
                }
        """
    }


# ============================================================================
# 7. SECURITY AGENTS (5 Agents)
# ============================================================================

def create_vulnerability_scanner_agent() -> Dict[str, Any]:
    """Scans for security vulnerabilities."""
    return {
        "metadata": AgentMetadata(
            name="VulnerabilityScannerAgent",
            description="Scans systems and applications for security vulnerabilities",
            category="Security Agent",
            tags=["security", "vulnerability", "scanning", "pentest"]
        ).dict(),
        "inputs": {
            "target": {"type": "string", "description": "Target to scan (IP, domain, or URL)", "required": True},
            "scan_type": {"type": "string", "enum": ["quick", "standard", "deep"], "default": "standard"},
            "check_types": {
                "type": "array",
                "items": {"type": "string", "enum": ["web", "api", "network", "os", "ssl"]},
                "default": ["web", "ssl"]
            },
            "credentials": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "password": {"type": "string", "secret": True}
                },
                "description": "Credentials for authenticated scanning"
            }
        },
        "outputs": {
            "vulnerabilities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"]},
                        "cvss_score": {"type": "number", "minimum": 0, "maximum": 10},
                        "description": {"type": "string"},
                        "remediation": {"type": "string"},
                        "references": {"type": "array", "items": {"type": "string"}}
                    }
                }
            },
            "scan_summary": {
                "type": "object",
                "properties": {
                    "total_vulnerabilities": {"type": "integer"},
                    "critical_count": {"type": "integer"},
                    "high_count": {"type": "integer"},
                    "medium_count": {"type": "integer"},
                    "low_count": {"type": "integer"},
                    "risk_score": {"type": "number", "minimum": 0, "maximum": 10},
                    "scan_duration_seconds": {"type": "number"}
                }
            },
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "priority": {"type": "string", "enum": ["immediate", "high", "medium", "low"]},
                        "action": {"type": "string"},
                        "estimated_effort": {"type": "string"},
                        "impact": {"type": "string"}
                    }
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            import random
            from datetime import datetime
            
            target = inputs['target']
            scan_type = inputs.get('scan_type', 'standard')
            check_types = inputs.get('check_types', ['web', 'ssl'])
            credentials = inputs.get('credentials')
            
            vulnerabilities = []
            start_time = datetime.now()
            
            # Mock vulnerability data based on scan type
            # In production, this would integrate with actual vulnerability scanners
            
            try:
                # Simulate scanning based on scan type
                scan_intensity = {
                    'quick': 3,
                    'standard': 8,
                    'deep': 15
                }.get(scan_type, 5)
                
                # Common vulnerabilities to detect
                common_vulns = [
                    {
                        'id': 'SSL-WEAK-CIPHER',
                        'title': 'Weak SSL/TLS Cipher Suites',
                        'severity': 'medium',
                        'cvss_score': 5.9,
                        'description': 'Server supports weak cipher suites that could be exploited.',
                        'remediation': 'Disable weak cipher suites and enforce TLS 1.2 or higher.',
                        'references': ['https://weakdh.org/', 'https://ssl-config.mozilla.org/']
                    },
                    {
                        'id': 'HTTP-HEADER-MISSING',
                        'title': 'Missing Security Headers',
                        'severity': 'low',
                        'cvss_score': 3.1,
                        'description': 'Missing security headers like HSTS, CSP, X-Frame-Options.',
                        'remediation': 'Implement security headers in web server configuration.',
                        'references': ['https://securityheaders.com/']
                    },
                    {
                        'id': 'INFO-LEAKAGE',
                        'title': 'Information Disclosure',
                        'severity': 'low',
                        'cvss_score': 2.5,
                        'description': 'Server discloses version information or internal paths.',
                        'remediation': 'Configure server to hide version information and error details.',
                        'references': []
                    }
                ]
                
                # Add critical vulnerabilities for deep scans
                if scan_type == 'deep':
                    common_vulns.extend([
                        {
                            'id': 'SQL-INJECTION',
                            'title': 'SQL Injection Vulnerability',
                            'severity': 'critical',
                            'cvss_score': 9.8,
                            'description': 'Application vulnerable to SQL injection attacks.',
                            'remediation': 'Use parameterized queries and input validation.',
                            'references': ['https://owasp.org/www-community/attacks/SQL_Injection']
                        },
                        {
                            'id': 'XSS-REFLECTED',
                            'title': 'Cross-Site Scripting (XSS)',
                            'severity': 'high',
                            'cvss_score': 7.5,
                            'description': 'Reflected XSS vulnerability in input parameters.',
                            'remediation': 'Implement output encoding and input validation.',
                            'references': ['https://owasp.org/www-community/attacks/xss/']
                        }
                    ])
                
                # Select vulnerabilities based on scan intensity
                selected_vulns = random.sample(common_vulns, min(scan_intensity, len(common_vulns)))
                
                # Customize based on check types
                for vuln in selected_vulns:
                    if 'web' in check_types or 'api' in check_types:
                        vulnerabilities.append(vuln)
                    elif 'ssl' in check_types and 'SSL' in vuln['id']:
                        vulnerabilities.append(vuln)
                    elif 'network' in check_types and vuln['severity'] in ['high', 'critical']:
                        vulnerabilities.append(vuln)
                
                # Calculate summary
                severity_counts = {
                    'critical': 0,
                    'high': 0,
                    'medium': 0,
                    'low': 0,
                    'info': 0
                }
                
                for vuln in vulnerabilities:
                    severity_counts[vuln['severity']] += 1
                
                # Calculate risk score (weighted by severity)
                risk_score = (
                    severity_counts['critical'] * 10 +
                    severity_counts['high'] * 7 +
                    severity_counts['medium'] * 4 +
                    severity_counts['low'] * 1 +
                    severity_counts['info'] * 0.5
                ) / len(vulnerabilities) if vulnerabilities else 0
                
                risk_score = min(10, risk_score)
                
                scan_duration = (datetime.now() - start_time).total_seconds()
                
                scan_summary = {
                    'total_vulnerabilities': len(vulnerabilities),
                    'critical_count': severity_counts['critical'],
                    'high_count': severity_counts['high'],
                    'medium_count': severity_counts['medium'],
                    'low_count': severity_counts['low'],
                    'risk_score': round(risk_score, 2),
                    'scan_duration_seconds': round(scan_duration, 2)
                }
                
                # Generate recommendations
                recommendations = []
                
                if severity_counts['critical'] > 0:
                    recommendations.append({
                        'priority': 'immediate',
                        'action': 'Address critical vulnerabilities immediately',
                        'estimated_effort': '2-4 days',
                        'impact': 'High - prevents potential breaches'
                    })
                
                if severity_counts['high'] > 0:
                    recommendations.append({
                        'priority': 'high',
                        'action': 'Fix high severity vulnerabilities within 1 week',
                        'estimated_effort': '1-2 weeks',
                        'impact': 'Medium - reduces attack surface significantly'
                    })
                
                if 'SSL' in str(vulnerabilities):
                    recommendations.append({
                        'priority': 'medium',
                        'action': 'Update SSL/TLS configuration',
                        'estimated_effort': '2-4 hours',
                        'impact': 'Medium - improves encryption security'
                    })
                
                if severity_counts['medium'] + severity_counts['low'] > 5:
                    recommendations.append({
                        'priority': 'low',
                        'action': 'Address low/medium vulnerabilities in next release cycle',
                        'estimated_effort': '2-4 weeks',
                        'impact': 'Low - incremental security improvement'
                    })
                
                return {
                    'vulnerabilities': vulnerabilities,
                    'scan_summary': scan_summary,
                    'recommendations': recommendations
                }
                
            except Exception as e:
                return {
                    'vulnerabilities': [],
                    'scan_summary': {
                        'total_vulnerabilities': 0,
                        'error_message': str(e)
                    },
                    'recommendations': []
                }
        """
    }


# ============================================================================
# 8. COST AGENTS (5 Agents)
# ============================================================================

def create_cloud_cost_analyzer_agent() -> Dict[str, Any]:
    """Analyzes and optimizes cloud costs."""
    return {
        "metadata": AgentMetadata(
            name="CloudCostAnalyzerAgent",
            description="Analyzes cloud spending and identifies optimization opportunities",
            category="Cost Agent",
            tags=["cost", "cloud", "optimization", "finops", "aws", "azure", "gcp"]
        ).dict(),
        "inputs": {
            "cloud_provider": {"type": "string", "enum": ["aws", "azure", "gcp", "multi"], "default": "aws"},
            "time_period": {"type": "string", "enum": ["last_7_days", "last_30_days", "last_90_days"], "default": "last_30_days"},
            "services": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Cloud services to analyze",
                "default": ["ec2", "rds", "s3", "lambda", "eks"]
            },
            "budget_threshold": {"type": "number", "description": "Budget threshold percentage for alerts", "default": 80}
        },
        "outputs": {
            "cost_analysis": {
                "type": "object",
                "properties": {
                    "total_cost": {"type": "number"},
                    "cost_by_service": {"type": "object", "additionalProperties": {"type": "number"}},
                    "cost_trend": {"type": "string", "enum": ["decreasing", "stable", "increasing", "spiking"]},
                    "monthly_forecast": {"type": "number"},
                    "budget_utilization": {"type": "number"}
                }
            },
            "optimization_opportunities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "opportunity": {"type": "string"},
                        "potential_savings": {"type": "number"},
                        "savings_percentage": {"type": "number"},
                        "implementation_effort": {"type": "string", "enum": ["low", "medium", "high"]},
                        "roi_months": {"type": "number"}
                    }
                }
            },
            "alerts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                        "message": {"type": "string"},
                        "service": {"type": "string"},
                        "cost_impact": {"type": "number"}
                    }
                }
            },
            "recommendations": {
                "type": "object",
                "properties": {
                    "immediate": {"type": "array", "items": {"type": "string"}},
                    "short_term": {"type": "array", "items": {"type": "string"}},
                    "strategic": {"type": "array", "items": {"type": "string"}}
                }
            }
        },
        "business_logic": """
        async def execute(inputs, context):
            import random
            from datetime import datetime, timedelta
            
            cloud_provider = inputs.get('cloud_provider', 'aws')
            time_period = inputs.get('time_period', 'last_30_days')
            services = inputs.get('services', ['ec2', 'rds', 's3', 'lambda', 'eks'])
            budget_threshold = inputs.get('budget_threshold', 80)
            
            # Mock cost data
            # In production, this would integrate with cloud provider cost APIs
            
            try:
                # Calculate period days
                days_map = {
                    'last_7_days': 7,
                    'last_30_days': 30,
                    'last_90_days': 90
                }
                days = days_map.get(time_period, 30)
                
                # Generate mock cost data
                service_costs = {}
                total_cost = 0
                
                # Typical cost distributions
                cost_distributions = {
                    'ec2': {'min': 1000, 'max': 5000},
                    'rds': {'min': 500, 'max': 2000},
                    's3': {'min': 100, 'max': 800},
                    'lambda': {'min': 50, 'max': 400},
                    'eks': {'min': 300, 'max': 1200},
                    'azure-vm': {'min': 800, 'max': 4000},
                    'azure-sql': {'min': 400, 'max': 1800},
                    'gcp-compute': {'min': 900, 'max': 4500},
                    'gcp-cloudsql': {'min': 450, 'max': 1900}
                }
                
                for service in services:
                    if service in cost_distributions:
                        cost_range = cost_distributions[service]
                        cost = random.uniform(cost_range['min'], cost_range['max'])
                        service_costs[service] = round(cost, 2)
                        total_cost += cost
                
                # Add some randomness
                total_cost = round(total_cost * random.uniform(0.8, 1.2), 2)
                
                # Determine cost trend
                trend_options = ['decreasing', 'stable', 'increasing', 'spiking']
                weights = [0.1, 0.5, 0.3, 0.1]  # Higher probability for stable/increasing
                cost_trend = random.choices(trend_options, weights=weights, k=1)[0]
                
                # Calculate monthly forecast
                days_in_month = 30
                daily_cost = total_cost / days if days > 0 else 0
                monthly_forecast = round(daily_cost * days_in_month, 2)
                
                # Mock budget utilization (assuming $10,000 monthly budget)
                monthly_budget = 10000
                budget_utilization = round((monthly_forecast / monthly_budget) * 100, 2)
                
                cost_analysis = {
                    'total_cost': total_cost,
                    'cost_by_service': service_costs,
                    'cost_trend': cost_trend,
                    'monthly_forecast': monthly_forecast,
                    'budget_utilization': budget_utilization
                }
                
                # Generate optimization opportunities
                optimization_opportunities = []
                
                # Common optimization patterns
                optimizations = [
                    {
                        'service': 'ec2',
                        'opportunity': 'Convert to Reserved Instances',
                        'savings_range': (25, 40),
                        'effort': 'low',
                        'roi_range': (1, 3)
                    },
                    {
                        'service': 'ec2',
                        'opportunity': 'Right-size underutilized instances',
                        'savings_range': (30, 50),
                        'effort': 'medium',
                        'roi_range': (1, 2)
                    },
                    {
                        'service': 'rds',
                        'opportunity': 'Enable auto-scaling and storage optimization',
                        'savings_range': (20, 35),
                        'effort': 'medium',
                        'roi_range': (2, 4)
                    },
                    {
                        'service': 's3',
                        'opportunity': 'Move infrequent access data to Glacier',
                        'savings_range': (50, 70),
                        'effort': 'low',
                        'roi_range': (1, 2)
                    },
                    {
                        'service': 'lambda',
                        'opportunity': 'Optimize memory allocation and execution time',
                        'savings_range': (15, 30),
                        'effort': 'high',
                        'roi_range': (3, 6)
                    }
                ]
                
                for opt in optimizations:
                    if opt['service'] in services:
                        service_cost = service_costs.get(opt['service'], 0)
                        if service_cost > 100:  # Only suggest for significant costs
                            savings_pct = random.uniform(*opt['savings_range']) / 100
                            potential_savings = round(service_cost * savings_pct, 2)
                            roi_months = random.uniform(*opt['roi_range'])
                            
                            optimization_opportunities.append({
                                'service': opt['service'],
                                'opportunity': opt['opportunity'],
                                'potential_savings': potential_savings,
                                'savings_percentage': round(savings_pct * 100, 2),
                                'implementation_effort': opt['effort'],
                                'roi_months': round(roi_months, 1)
                            })
                
                # Sort by potential savings
                optimization_opportunities.sort(key=lambda x: x['potential_savings'], reverse=True)
                
                # Generate alerts
                alerts = []
                
                if budget_utilization > budget_threshold:
                    alerts.append({
                        'severity': 'warning',
                        'message': f'Budget utilization at {budget_utilization}%',
                        'service': 'overall',
                        'cost_impact': monthly_forecast - monthly_budget
                    })
                
                # Check for cost spikes
                if cost_trend == 'spiking':
                    alerts.append({
                        'severity': 'critical',
                        'message': 'Cost spike detected',
                        'service': 'overall',
                        'cost_impact': monthly_forecast * 0.3  # Assume 30% spike
                    })
                
                # Check individual services
                for service, cost in service_costs.items():
                    if cost > 2000:  # High cost threshold
                        alerts.append({
                            'severity': 'warning',
                            'message': f'High spending on {service}: ${cost}',
                            'service': service,
                            'cost_impact': cost
                        })
                
                # Generate recommendations
                recommendations = {
                    'immediate': [],
                    'short_term': [],
                    'strategic': []
                }
                
                # Immediate recommendations (budget alerts)
                if budget_utilization > 90:
                    recommendations['immediate'].append('Implement spending limits and alerts')
                
                # Short-term recommendations (quick wins)
                quick_wins = [opp for opp in optimization_opportunities if opp['implementation_effort'] == 'low']
                if quick_wins:
                    recommendations['short_term'].append(f"Implement {quick_wins[0]['opportunity']} for {quick_wins[0]['service']}")
                
                # Strategic recommendations
                if total_cost > 5000:
                    recommendations['strategic'].append('Consider multi-cloud strategy for cost optimization')
                    recommendations['strategic'].append('Implement FinOps practices and cost allocation tags')
                
                return {
                    'cost_analysis': cost_analysis,
                    'optimization_opportunities': optimization_opportunities[:5],  # Top 5
                    'alerts': alerts,
                    'recommendations': recommendations
                }
                
            except Exception as e:
                return {
                    'cost_analysis': {
                        'total_cost': 0,
                        'error_message': str(e)
                    },
                    'optimization_opportunities': [],
                    'alerts': [],
                    'recommendations': {}
                }
        """
    }


# ============================================================================
# AGENT COLLECTION
# ============================================================================

def get_all_sample_agents() -> List[Dict[str, Any]]:
    """Returns all sample agents organized by category."""
    
    agents = []
    
    # 1. Simple Detectors (10 agents)
    agents.append(create_health_check_agent())
    agents.append(create_log_monitor_agent())
    agents.append(create_disk_space_monitor_agent())
    agents.append(create_memory_monitor_agent())
    agents.append(create_cpu_monitor_agent())
    
    # Add 5 more simple detectors
    for i in range(5):
        agents.append({
            "metadata": AgentMetadata(
                name=f"SimpleDetector{i+1}",
                description=f"Simple detector agent #{i+1} for basic monitoring",
                category="Simple Detector",
                tags=[f"detector{i}", "basic", "monitoring"]
            ).dict(),
            "inputs": {
                "threshold": {"type": "number", "default": 50}
            },
            "outputs": {
                "status": {"type": "string"},
                "value": {"type": "number"}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                threshold = inputs.get('threshold', 50)
                simulated_value = {i * 20 + 10}  # Varying values
                
                return {{
                    'status': 'warning' if simulated_value > threshold else 'normal',
                    'value': simulated_value
                }}
            """
        })
    
    # 2. Complex Analyzers (8 agents)
    agents.append(create_performance_analyzer_agent())
    agents.append(create_log_pattern_analyzer_agent())
    
    # Add 6 more complex analyzers
    for i in range(6):
        agents.append({
            "metadata": AgentMetadata(
                name=f"ComplexAnalyzer{i+1}",
                description=f"Complex analysis agent #{i+1} for in-depth analytics",
                category="Complex Analyzer",
                tags=[f"analyzer{i}", "complex", "analytics"]
            ).dict(),
            "inputs": {
                "data": {"type": "array", "items": {"type": "number"}},
                "analysis_type": {"type": "string", "default": "statistical"}
            },
            "outputs": {
                "analysis_result": {"type": "object"},
                "insights": {"type": "array", "items": {"type": "string"}}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                import statistics
                
                data = inputs.get('data', [])
                analysis_type = inputs.get('analysis_type', 'statistical')
                
                if not data:
                    return {{'analysis_result': {{}}, 'insights': ['No data provided']}}
                
                if analysis_type == 'statistical':
                    result = {{
                        'mean': statistics.mean(data) if data else 0,
                        'median': statistics.median(data) if data else 0,
                        'std_dev': statistics.stdev(data) if len(data) > 1 else 0,
                        'min': min(data) if data else 0,
                        'max': max(data) if data else 0
                    }}
                    
                    insights = []
                    if result['std_dev'] > result['mean'] * 0.5:
                        insights.append('High variability in data')
                    if result['max'] > result['mean'] * 2:
                        insights.append('Potential outliers detected')
                    
                    return {{
                        'analysis_result': result,
                        'insights': insights
                    }}
                else:
                    return {{
                        'analysis_result': {{'error': 'Unsupported analysis type'}},
                        'insights': []
                    }}
            """
        })
    
    # 3. ML Predictors (7 agents)
    agents.append(create_anomaly_detection_agent())
    agents.append(create_resource_predictor_agent())
    
    # Add 5 more ML predictors
    for i in range(5):
        agents.append({
            "metadata": AgentMetadata(
                name=f"MLPredictor{i+1}",
                description=f"Machine learning predictor agent #{i+1}",
                category="ML Predictor",
                tags=[f"ml{i}", "prediction", "ai"]
            ).dict(),
            "inputs": {
                "training_data": {"type": "array", "items": {"type": "object"}},
                "prediction_points": {"type": "integer", "default": 5}
            },
            "outputs": {
                "predictions": {"type": "array"},
                "model_accuracy": {"type": "number"}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                import random
                
                training_data = inputs.get('training_data', [])
                prediction_points = inputs.get('prediction_points', 5)
                
                if not training_data:
                    return {{
                        'predictions': [],
                        'model_accuracy': 0,
                        'error': 'No training data provided'
                    }}
                
                # Simple mock predictions
                predictions = []
                for j in range(prediction_points):
                    predictions.append({{
                        'point': j + 1,
                        'predicted_value': random.uniform(0, 100),
                        'confidence': random.uniform(0.7, 0.95)
                    }})
                
                # Mock accuracy based on data size
                accuracy = min(0.95, len(training_data) / 100)
                
                return {{
                    'predictions': predictions,
                    'model_accuracy': round(accuracy, 3)
                }}
            """
        })
    
    # 4. Optimization Agents (6 agents)
    agents.append(create_cost_optimizer_agent())
    agents.append(create_performance_optimizer_agent())
    
    # Add 4 more optimization agents
    for i in range(4):
        agents.append({
            "metadata": AgentMetadata(
                name=f"OptimizationAgent{i+1}",
                description=f"Optimization agent #{i+1} for various optimization tasks",
                category="Optimization Agent",
                tags=[f"optimization{i}", "tuning", "improvement"]
            ).dict(),
            "inputs": {
                "config": {"type": "object"},
                "optimization_target": {"type": "string", "default": "performance"}
            },
            "outputs": {
                "improvements": {"type": "array", "items": {"type": "string"}},
                "estimated_gain": {"type": "number"}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                config = inputs.get('config', {{}})
                target = inputs.get('optimization_target', 'performance')
                
                improvements = []
                
                if target == 'performance':
                    improvements = [
                        'Enable compression',
                        'Implement caching',
                        'Optimize database queries',
                        'Use CDN for static assets'
                    ]
                    gain = 35 + i * 5
                elif target == 'cost':
                    improvements = [
                        'Right-size resources',
                        'Implement auto-scaling',
                        'Use reserved instances',
                        'Optimize storage classes'
                    ]
                    gain = 25 + i * 5
                else:
                    improvements = ['Review configuration for optimization opportunities']
                    gain = 15
                
                return {{
                    'improvements': improvements,
                    'estimated_gain': gain  # Percentage improvement
                }}
            """
        })
    
    # 5. Remediation Agents (5 agents)
    agents.append(create_auto_healing_agent())
    
    # Add 4 more remediation agents
    for i in range(4):
        agents.append({
            "metadata": AgentMetadata(
                name=f"RemediationAgent{i+1}",
                description=f"Remediation agent #{i+1} for automatic issue resolution",
                category="Remediation Agent",
                tags=[f"remediation{i}", "healing", "recovery"]
            ).dict(),
            "inputs": {
                "issue_description": {"type": "string"},
                "severity": {"type": "string", "default": "medium"}
            },
            "outputs": {
                "actions": {"type": "array", "items": {"type": "string"}},
                "resolution_status": {"type": "string"}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                issue = inputs.get('issue_description', '')
                severity = inputs.get('severity', 'medium')
                
                actions = []
                
                if 'memory' in issue.lower():
                    actions = [
                        'Restart affected service',
                        'Increase memory allocation',
                        'Analyze memory usage patterns'
                    ]
                elif 'disk' in issue.lower():
                    actions = [
                        'Clean up temporary files',
                        'Expand disk space',
                        'Implement log rotation'
                    ]
                elif 'network' in issue.lower():
                    actions = [
                        'Check firewall rules',
                        'Verify network configuration',
                        'Restart network services'
                    ]
                else:
                    actions = [
                        'Investigate root cause',
                        'Apply standard remediation procedures',
                        'Monitor for recurrence'
                    ]
                
                resolution_status = 'resolved' if severity != 'critical' else 'requires_manual_intervention'
                
                return {{
                    'actions': actions,
                    'resolution_status': resolution_status
                }}
            """
        })
    
    # 6. Compliance Agents (5 agents)
    agents.append(create_gdpr_compliance_agent())
    
    # Add 4 more compliance agents
    compliance_standards = ['SOC2', 'ISO27001', 'PCI-DSS', 'HIPAA']
    for i, standard in enumerate(compliance_standards):
        agents.append({
            "metadata": AgentMetadata(
                name=f"{standard}ComplianceAgent",
                description=f"Compliance agent for {standard} requirements",
                category="Compliance Agent",
                tags=[standard.lower(), "compliance", "security"]
            ).dict(),
            "inputs": {
                "scope": {"type": "array", "items": {"type": "string"}},
                "audit_mode": {"type": "boolean", "default": False}
            },
            "outputs": {
                "compliance_status": {"type": "string"},
                "requirements_met": {"type": "integer"},
                "requirements_total": {"type": "integer"},
                "findings": {"type": "array", "items": {"type": "string"}}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                scope = inputs.get('scope', [])
                audit_mode = inputs.get('audit_mode', False)
                
                # Mock compliance check
                total_requirements = 20 + i * 5
                met_requirements = 15 + i * 3
                
                if audit_mode:
                    met_requirements = max(met_requirements - 5, 5)  # More strict in audit mode
                
                compliance_percentage = (met_requirements / total_requirements) * 100
                
                if compliance_percentage >= 90:
                    status = 'compliant'
                elif compliance_percentage >= 70:
                    status = 'partially_compliant'
                else:
                    status = 'non_compliant'
                
                findings = []
                if compliance_percentage < 100:
                    findings.append('Some controls require implementation')
                    findings.append('Documentation needs updating')
                
                return {{
                    'compliance_status': status,
                    'requirements_met': met_requirements,
                    'requirements_total': total_requirements,
                    'compliance_percentage': round(compliance_percentage, 2),
                    'findings': findings
                }}
            """
        })
    
    # 7. Security Agents (5 agents)
    agents.append(create_vulnerability_scanner_agent())
    
    # Add 4 more security agents
    security_types = ['IntrusionDetection', 'AccessControl', 'Encryption', 'Audit']
    for i, sec_type in enumerate(security_types):
        agents.append({
            "metadata": AgentMetadata(
                name=f"{sec_type}SecurityAgent",
                description=f"Security agent for {sec_type.replace('_', ' ').title()}",
                category="Security Agent",
                tags=[sec_type.lower(), "security", "protection"]
            ).dict(),
            "inputs": {
                "config": {"type": "object"},
                "sensitivity": {"type": "string", "default": "medium"}
            },
            "outputs": {
                "security_status": {"type": "string"},
                "alerts": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                config = inputs.get('config', {{}})
                sensitivity = inputs.get('sensitivity', 'medium')
                
                # Mock security assessment
                sensitivity_map = {{'low': 0.3, 'medium': 0.5, 'high': 0.8, 'critical': 0.95}}
                detection_rate = sensitivity_map.get(sensitivity, 0.5)
                
                # Simulate some findings based on sensitivity
                import random
                
                alerts = []
                if random.random() < detection_rate:
                    alerts.append('Potential security issue detected')
                    alerts.append('Review access logs for suspicious activity')
                
                recommendations = [
                    'Regular security audits',
                    'Keep all software updated',
                    'Implement multi-factor authentication',
                    'Regular backup and disaster recovery testing'
                ]
                
                security_status = 'secure' if len(alerts) == 0 else 'requires_attention'
                
                return {{
                    'security_status': security_status,
                    'alerts': alerts,
                    'recommendations': recommendations[:2 + i]  # Vary recommendations
                }}
            """
        })
    
    # 8. Cost Agents (5 agents)
    agents.append(create_cloud_cost_analyzer_agent())
    
    # Add 4 more cost agents
    cost_areas = ['Infrastructure', 'License', 'Bandwidth', 'Support']
    for i, area in enumerate(cost_areas):
        agents.append({
            "metadata": AgentMetadata(
                name=f"{area}CostAgent",
                description=f"Cost analysis agent for {area} spending",
                category="Cost Agent",
                tags=[area.lower(), "cost", "optimization", "finops"]
            ).dict(),
            "inputs": {
                "spending_data": {"type": "object"},
                "budget": {"type": "number"}
            },
            "outputs": {
                "analysis": {"type": "object"},
                "savings_opportunities": {"type": "array", "items": {"type": "string"}},
                "roi_calculation": {"type": "number"}
            },
            "business_logic": f"""
            async def execute(inputs, context):
                spending_data = inputs.get('spending_data', {{}})
                budget = inputs.get('budget', 10000)
                
                # Mock cost analysis
                total_spent = spending_data.get('total', 5000 + i * 1000)
                
                savings_opportunities = [
                    'Negotiate volume discounts',
                    'Review and remove unused resources',
                    'Optimize resource allocation',
                    'Consider alternative providers'
                ]
                
                # Calculate potential savings
                potential_savings = total_spent * (0.1 + i * 0.05)  # 10-25% savings
                investment_needed = potential_savings * 0.2  # 20% of savings needed as investment
                
                roi = (potential_savings - investment_needed) / investment_needed if investment_needed > 0 else 0
                
                analysis = {{
                    'total_spent': total_spent,
                    'budget_utilization': round((total_spent / budget) * 100, 2) if budget > 0 else 0,
                    'potential_savings': round(potential_savings, 2),
                    'investment_required': round(investment_needed, 2),
                    'payback_period_months': round(12 / (roi + 1), 1) if roi > 0 else 0
                }}
                
                return {{
                    'analysis': analysis,
                    'savings_opportunities': savings_opportunities[:2 + i],
                    'roi_calculation': round(roi, 2)
                }}
            """
        })
    
    return agents


def get_agent_by_category(category: str) -> List[Dict[str, Any]]:
    """Get agents filtered by category."""
    all_agents = get_all_sample_agents()
    return [agent for agent in all_agents if agent['metadata']['category'] == category]


def get_agent_by_tag(tag: str) -> List[Dict[str, Any]]:
    """Get agents filtered by tag."""
    all_agents = get_all_sample_agents()
    return [agent for agent in all_agents if tag in agent['metadata']['tags']]


def save_agents_to_file(filepath: str) -> None:
    """Save all agents to a JSON file."""
    import json
    
    agents = get_all_sample_agents()
    
    with open(filepath, 'w') as f:
        json.dump(agents, f, indent=2, default=str)
    
    print(f"Saved {len(agents)} agents to {filepath}")


def load_agents_from_file(filepath: str) -> List[Dict[str, Any]]:
    """Load agents from a JSON file."""
    import json
    
    with open(filepath, 'r') as f:
        agents = json.load(f)
    
    return agents


# Example usage
if __name__ == "__main__":
    # Get all agents
    all_agents = get_all_sample_agents()
    print(f"Total agents: {len(all_agents)}")
    
    # Count by category
    categories = {}
    for agent in all_agents:
        category = agent['metadata']['category']
        categories[category] = categories.get(category, 0) + 1
    
    print("\nAgents by category:")
    for category, count in categories.items():
        print(f"  {category}: {count} agents")
    
    # Save to file
    save_agents_to_file("sample_agents.json")
    
    # Example: Get optimization agents
    optimization_agents = get_agent_by_category("Optimization Agent")
    print(f"\nFound {len(optimization_agents)} optimization agents")
    
    # Example: Get agents with ML tag
    ml_agents = get_agent_by_tag("ml")
    print(f"Found {len(ml_agents)} ML agents")