"""
Mock data fixtures for MicroAgents Platform testing.
Contains realistic data samples for comprehensive testing.
"""

import json
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
from faker import Faker

fake = Faker()
Faker.seed(42)  # For reproducible data

# ============================================================================
# 1. METRICS DATA
# ============================================================================

class MetricType(Enum):
    CPU_USAGE = "cpu_usage"
    MEMORY_USAGE = "memory_usage"
    DISK_USAGE = "disk_usage"
    NETWORK_IN = "network_in"
    NETWORK_OUT = "network_out"
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    THROUGHPUT = "throughput"
    CONCURRENT_USERS = "concurrent_users"
    RESPONSE_TIME = "response_time"

@dataclass
class MetricDataPoint:
    timestamp: datetime
    value: float
    metric_type: MetricType
    tags: Dict[str, str]
    unit: str = "percent"

def generate_metrics_data(
    start_time: datetime,
    end_time: datetime,
    interval_minutes: int = 5
) -> List[MetricDataPoint]:
    """Generate realistic time-series metrics data."""
    data_points = []
    current = start_time
    
    while current <= end_time:
        # Simulate daily patterns
        hour = current.hour
        is_business_hours = 9 <= hour <= 17
        is_night = hour < 6 or hour > 22
        
        for metric_type in MetricType:
            base_value = random.uniform(10, 90)
            
            # Apply patterns
            if metric_type == MetricType.CPU_USAGE:
                if is_business_hours:
                    value = base_value * random.uniform(1.2, 1.8)
                elif is_night:
                    value = base_value * random.uniform(0.3, 0.7)
                else:
                    value = base_value
                unit = "percent"
                
            elif metric_type == MetricType.LATENCY:
                value = random.uniform(50, 500)  # ms
                if hour in [14, 15]:  # Peak hours
                    value *= random.uniform(1.5, 3)
                unit = "milliseconds"
                
            elif metric_type == MetricType.ERROR_RATE:
                value = random.uniform(0.01, 5.0)  # percentage
                # Simulate error spikes
                if random.random() < 0.01:  # 1% chance of error spike
                    value = random.uniform(10, 50)
                unit = "percent"
                
            elif metric_type == MetricType.THROUGHPUT:
                value = random.uniform(100, 10000)  # requests/sec
                if is_business_hours:
                    value *= random.uniform(1.5, 2.5)
                unit = "requests_per_second"
                
            else:
                value = base_value
                unit = "percent"
            
            # Add some randomness
            value *= random.uniform(0.95, 1.05)
            
            data_points.append(MetricDataPoint(
                timestamp=current,
                value=round(value, 2),
                metric_type=metric_type,
                tags={
                    "service": random.choice(["api-gateway", "user-service", "payment-service", "auth-service"]),
                    "environment": random.choice(["production", "staging", "development"]),
                    "region": random.choice(["us-east-1", "eu-west-1", "ap-southeast-1"]),
                    "instance_id": f"i-{fake.uuid4()[:8]}"
                },
                unit=unit
            ))
        
        current += timedelta(minutes=interval_minutes)
    
    return data_points

# ============================================================================
# 2. LOG SAMPLES
# ============================================================================

class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

@dataclass
class LogEntry:
    timestamp: datetime
    level: LogLevel
    service: str
    message: str
    trace_id: str
    span_id: str
    user_id: Optional[str] = None
    duration_ms: Optional[float] = None
    http_method: Optional[str] = None
    http_status: Optional[int] = None
    path: Optional[str] = None
    extra: Dict[str, Any] = None

def generate_log_samples(count: int = 1000) -> List[LogEntry]:
    """Generate realistic structured log entries."""
    logs = []
    
    for _ in range(count):
        timestamp = fake.date_time_between(start_date="-30d", end_date="now")
        level = random.choices(
            [LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL],
            weights=[10, 60, 15, 10, 5]
        )[0]
        
        service = random.choice([
            "api-gateway", "user-service", "payment-service", "auth-service",
            "database", "cache", "message-queue", "file-storage"
        ])
        
        trace_id = fake.uuid4()
        span_id = fake.uuid4()[:16]
        
        # Generate realistic messages based on service and level
        if service == "api-gateway":
            http_method = random.choice(["GET", "POST", "PUT", "DELETE", "PATCH"])
            http_status = random.choice([200, 201, 400, 401, 403, 404, 500, 502, 503])
            path = random.choice([
                "/api/v1/users",
                "/api/v1/payments",
                "/api/v1/auth/login",
                "/api/v1/products",
                "/health"
            ])
            duration = random.uniform(10, 5000)
            
            if level == LogLevel.INFO:
                message = f"{http_method} {path} {http_status} {duration:.0f}ms"
            elif level == LogLevel.ERROR:
                message = f"Failed to proxy request to {path}: Connection timeout"
            else:
                message = f"Request processed: {http_method} {path}"
                
            logs.append(LogEntry(
                timestamp=timestamp,
                level=level,
                service=service,
                message=message,
                trace_id=trace_id,
                span_id=span_id,
                duration_ms=duration,
                http_method=http_method,
                http_status=http_status,
                path=path
            ))
            
        elif service == "database":
            if level == LogLevel.WARNING:
                message = "Slow query detected: SELECT * FROM users WHERE last_login < '2024-01-01'"
            elif level == LogLevel.ERROR:
                message = "Connection pool exhausted, waiting for available connections"
            else:
                message = "Query executed successfully"
                
            logs.append(LogEntry(
                timestamp=timestamp,
                level=level,
                service=service,
                message=message,
                trace_id=trace_id,
                span_id=span_id,
                extra={"query_time": random.uniform(100, 5000)}
            ))
            
        else:
            # Generic log entry
            messages = {
                LogLevel.INFO: [
                    "Service started successfully",
                    "Configuration reloaded",
                    "Cache warmed up",
                    "Health check passed",
                    "Connected to external service"
                ],
                LogLevel.WARNING: [
                    "High memory usage detected",
                    "Connection retry attempt",
                    "Deprecated API called",
                    "Rate limit approaching"
                ],
                LogLevel.ERROR: [
                    "Failed to connect to database",
                    "External API returned error",
                    "Disk space running low",
                    "Authentication failed"
                ],
                LogLevel.CRITICAL: [
                    "Service unavailable",
                    "Data corruption detected",
                    "Security breach attempt",
                    "Payment processing failed"
                ]
            }
            
            message = random.choice(messages.get(level, ["Unknown event"]))
            logs.append(LogEntry(
                timestamp=timestamp,
                level=level,
                service=service,
                message=message,
                trace_id=trace_id,
                span_id=span_id
            ))
    
    return sorted(logs, key=lambda x: x.timestamp)

# ============================================================================
# 3. ERROR SCENARIOS
# ============================================================================

@dataclass
class ErrorScenario:
    error_code: str
    description: str
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL
    service: str
    timestamp: datetime
    root_cause: str
    impact: str
    resolution: str
    affected_users: int
    duration_minutes: int

ERROR_SCENARIOS = [
    ErrorScenario(
        error_code="DB-001",
        description="Database connection pool exhaustion",
        severity="HIGH",
        service="payment-service",
        timestamp=fake.date_time_between(start_date="-7d", end_date="-6d"),
        root_cause="Connection leak in payment processing module",
        impact="Payment processing delayed by 30%, 15% failure rate",
        resolution="Restarted service, fixed connection handling",
        affected_users=1500,
        duration_minutes=45
    ),
    ErrorScenario(
        error_code="API-002",
        description="Third-party API rate limit exceeded",
        severity="MEDIUM",
        service="notification-service",
        timestamp=fake.date_time_between(start_date="-3d", end_date="-2d"),
        root_cause="Burst of notification requests during marketing campaign",
        impact="Email notifications delayed by 2 hours",
        resolution="Implemented exponential backoff, increased rate limits",
        affected_users=5000,
        duration_minutes=120
    ),
    ErrorScenario(
        error_code="SEC-003",
        description="Suspicious login attempts detected",
        severity="CRITICAL",
        service="auth-service",
        timestamp=fake.date_time_between(start_date="-1d", end_date="now"),
        root_cause="Credential stuffing attack from IP range 192.168.1.0/24",
        impact="1000+ failed login attempts, 3 accounts compromised",
        resolution="Blocked IP range, forced password reset, enabled MFA",
        affected_users=1000,
        duration_minutes=15
    ),
    ErrorScenario(
        error_code="INFRA-004",
        description="AWS region partial outage",
        severity="HIGH",
        service="all-services",
        timestamp=fake.date_time_between(start_date="-10d", end_date="-9d"),
        root_cause="AWS us-east-1 network connectivity issues",
        impact="40% increased latency, 10% error rate for 2 hours",
        resolution="Failed over to eu-west-1 region",
        affected_users=25000,
        duration_minutes=120
    ),
    ErrorScenario(
        error_code="MEM-005",
        description="Memory leak in user session cache",
        severity="MEDIUM",
        service="user-service",
        timestamp=fake.date_time_between(start_date="-5d", end_date="-4d"),
        root_cause="Session objects not being garbage collected",
        impact="Gradual performance degradation over 24 hours",
        resolution="Fixed memory leak, implemented cache eviction policy",
        affected_users=8000,
        duration_minutes=1440  # 24 hours
    ),
]

# ============================================================================
# 4. PERFORMANCE DATA
# ============================================================================

@dataclass
class PerformanceBenchmark:
    name: str
    p50: float  # 50th percentile
    p90: float  # 90th percentile
    p95: float  # 95th percentile
    p99: float  # 99th percentile
    max: float
    min: float
    requests_per_second: float
    concurrent_users: int
    test_duration_seconds: int
    success_rate: float

PERFORMANCE_BENCHMARKS = [
    PerformanceBenchmark(
        name="API Gateway - User Lookup",
        p50=45.2,
        p90=89.7,
        p95=125.4,
        p99=250.8,
        max=512.3,
        min=12.1,
        requests_per_second=1250,
        concurrent_users=5000,
        test_duration_seconds=300,
        success_rate=99.98
    ),
    PerformanceBenchmark(
        name="Payment Processing",
        p50=120.5,
        p90=245.8,
        p95=320.1,
        p99=589.4,
        max=1024.7,
        min=45.3,
        requests_per_second=450,
        concurrent_users=1000,
        test_duration_seconds=600,
        success_rate=99.95
    ),
    PerformanceBenchmark(
        name="Authentication Service",
        p50=25.8,
        p90=58.3,
        p95=89.6,
        p99=145.2,
        max=256.9,
        min=8.7,
        requests_per_second=3200,
        concurrent_users=10000,
        test_duration_seconds=900,
        success_rate=99.99
    ),
    PerformanceBenchmark(
        name="Database Query - Complex Report",
        p50=1250.4,
        p90=2450.8,
        p95=3210.5,
        p99=5120.9,
        max=10240.3,
        min=450.2,
        requests_per_second=25,
        concurrent_users=100,
        test_duration_seconds=1200,
        success_rate=99.90
    ),
]

# ============================================================================
# 5. COST DATA
# ============================================================================

@dataclass
class CostItem:
    service: str
    resource_type: str
    resource_id: str
    cost_usd: float
    period_start: datetime
    period_end: datetime
    region: str
    tags: Dict[str, str]
    usage_hours: float
    usage_units: float

def generate_cost_data(days: int = 30) -> List[CostItem]:
    """Generate realistic cloud cost data."""
    costs = []
    base_date = datetime.now() - timedelta(days=days)
    
    # EC2 instances
    for i in range(50):
        instance_type = random.choice(["m5.large", "m5.xlarge", "c5.2xlarge", "r5.4xlarge"])
        hourly_rate = {
            "m5.large": 0.096,
            "m5.xlarge": 0.192,
            "c5.2xlarge": 0.34,
            "r5.4xlarge": 1.008
        }[instance_type]
        
        for day in range(days):
            date = base_date + timedelta(days=day)
            # Simulate weekend vs weekday usage
            is_weekend = date.weekday() >= 5
            usage_hours = 24 if random.random() > 0.3 else random.uniform(8, 16)  # 30% chance of partial usage
            
            if is_weekend:
                usage_hours *= random.uniform(0.3, 0.7)  # Reduced usage on weekends
            
            cost = CostItem(
                service="AWS EC2",
                resource_type="Compute",
                resource_id=f"i-{fake.uuid4()[:8]}",
                cost_usd=round(hourly_rate * usage_hours, 4),
                period_start=date,
                period_end=date + timedelta(days=1),
                region=random.choice(["us-east-1", "eu-west-1", "ap-southeast-1"]),
                tags={
                    "Environment": random.choice(["production", "staging", "development"]),
                    "Owner": random.choice(["team-platform", "team-data", "team-frontend"]),
                    "Application": random.choice(["api-gateway", "user-service", "payment-service"])
                },
                usage_hours=usage_hours,
                usage_units=1
            )
            costs.append(cost)
    
    # RDS Databases
    for i in range(10):
        db_type = random.choice(["db.t3.medium", "db.m5.large", "db.r5.xlarge"])
        hourly_rate = {
            "db.t3.medium": 0.072,
            "db.m5.large": 0.171,
            "db.r5.xlarge": 0.48
        }[db_type]
        
        for day in range(days):
            date = base_date + timedelta(days=day)
            usage_hours = 24  # DBs run 24/7
            storage_gb = random.uniform(100, 1000)
            storage_cost = storage_gb * 0.115 / 30 / 24 * usage_hours  # GP3 storage cost
            
            cost = CostItem(
                service="AWS RDS",
                resource_type="Database",
                resource_id=f"db-{fake.uuid4()[:8]}",
                cost_usd=round(hourly_rate * usage_hours + storage_cost, 4),
                period_start=date,
                period_end=date + timedelta(days=1),
                region=random.choice(["us-east-1", "eu-west-1"]),
                tags={
                    "Environment": "production",
                    "Database": random.choice(["users", "payments", "analytics"]),
                    "Backup": "enabled"
                },
                usage_hours=usage_hours,
                usage_units=storage_gb
            )
            costs.append(cost)
    
    # S3 Storage
    for i in range(5):
        for day in range(days):
            date = base_date + timedelta(days=day)
            storage_gb = random.uniform(5000, 50000)
            requests = random.randint(1000000, 5000000)
            
            # S3 pricing: $0.023 per GB, $0.0004 per 1000 requests
            storage_cost = storage_gb * 0.023 / 30 / 24 * 24
            request_cost = requests * 0.0004 / 1000
            
            cost = CostItem(
                service="AWS S3",
                resource_type="Storage",
                resource_id=f"bucket-{fake.uuid4()[:8]}",
                cost_usd=round(storage_cost + request_cost, 4),
                period_start=date,
                period_end=date + timedelta(days=1),
                region="us-east-1",
                tags={
                    "StorageClass": random.choice(["STANDARD", "INTELLIGENT_TIERING", "GLACIER"]),
                    "Lifecycle": random.choice(["enabled", "disabled"]),
                    "Encryption": "AES-256"
                },
                usage_hours=24,
                usage_units=storage_gb
            )
            costs.append(cost)
    
    # Data Transfer
    for day in range(days):
        date = base_date + timedelta(days=day)
        transfer_gb = random.uniform(100, 1000)
        transfer_cost = transfer_gb * 0.09  # $0.09 per GB for inter-region
        
        cost = CostItem(
            service="AWS Data Transfer",
            resource_type="Network",
            resource_id="INTERNET-OUT",
            cost_usd=round(transfer_cost, 4),
            period_start=date,
            period_end=date + timedelta(days=1),
            region="global",
            tags={
                "Direction": "OUT",
                "Source": random.choice(["us-east-1", "eu-west-1"]),
                "Destination": "INTERNET"
            },
            usage_hours=24,
            usage_units=transfer_gb
        )
        costs.append(cost)
    
    return costs

# ============================================================================
# 6. SECURITY EVENT DATA
# ============================================================================

class SecuritySeverity(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

@dataclass
class SecurityEvent:
    event_id: str
    timestamp: datetime
    severity: SecuritySeverity
    event_type: str
    source_ip: str
    destination_ip: str
    user_agent: str
    user_id: Optional[str]
    description: str
    action_taken: str
    status: str  # OPEN, INVESTIGATING, RESOLVED, FALSE_POSITIVE

SECURITY_EVENTS = [
    SecurityEvent(
        event_id="SEC-2024-001",
        timestamp=fake.date_time_between(start_date="-2d", end_date="-1d"),
        severity=SecuritySeverity.HIGH,
        event_type="BRUTE_FORCE_ATTEMPT",
        source_ip=fake.ipv4(),
        destination_ip="10.0.1.15",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        user_id=None,
        description="Multiple failed login attempts from same IP (150 attempts in 5 minutes)",
        action_taken="IP temporarily blocked, alert sent to SOC",
        status="RESOLVED"
    ),
    SecurityEvent(
        event_id="SEC-2024-002",
        timestamp=fake.date_time_between(start_date="-5d", end_date="-4d"),
        severity=SecuritySeverity.CRITICAL,
        event_type="UNAUTHORIZED_ACCESS",
        source_ip="192.168.1.100",
        destination_ip="10.0.2.45",
        user_agent="curl/7.68.0",
        user_id="admin",
        description="Privileged account accessed from unrecognized IP without MFA",
        action_taken="Session terminated, password reset forced, investigation started",
        status="INVESTIGATING"
    ),
    SecurityEvent(
        event_id="SEC-2024-003",
        timestamp=fake.date_time_between(start_date="-7d", end_date="-6d"),
        severity=SecuritySeverity.MEDIUM,
        event_type="MALWARE_DETECTED",
        source_ip="10.0.3.12",
        destination_ip="malicious-domain.com",
        user_agent="Python/3.9 requests/2.28.1",
        user_id="service-account-01",
        description="Service account attempted to connect to known malicious domain",
        action_taken="Service account suspended, endpoint scanned",
        status="RESOLVED"
    ),
    SecurityEvent(
        event_id="SEC-2024-004",
        timestamp=fake.date_time_between(start_date="-1d", end_date="now"),
        severity=SecuritySeverity.LOW,
        event_type="VULNERABILITY_SCAN",
        source_ip="scanner.internal",
        destination_ip="10.0.0.0/16",
        user_agent="Nessus/10.5.0",
        user_id=None,
        description="Routine vulnerability scan completed, 3 medium severity findings",
        action_taken="Findings assigned to respective teams",
        status="OPEN"
    ),
    SecurityEvent(
        event_id="SEC-2024-005",
        timestamp=fake.date_time_between(start_date="-3d", end_date="-2d"),
        severity=SecuritySeverity.HIGH,
        event_type="DATA_EXFILTRATION_ATTEMPT",
        source_ip="10.0.5.67",
        destination_ip="external-storage.com",
        user_agent="rclone/1.60.1",
        user_id="developer-03",
        description="Large volume of sensitive data attempted to be copied to external service",
        action_taken="Transfer blocked, user account disabled, legal notified",
        status="INVESTIGATING"
    ),
]

# ============================================================================
# 7. COMPLIANCE AUDIT DATA
# ============================================================================

@dataclass
class ComplianceControl:
    control_id: str
    standard: str  # SOC2, ISO27001, GDPR, HIPAA, PCI-DSS
    requirement: str
    description: str
    implementation_status: str  # IMPLEMENTED, PARTIAL, NOT_IMPLEMENTED
    last_audit_date: datetime
    next_audit_date: datetime
    evidence: List[str]
    owner: str

@dataclass
class AuditFinding:
    finding_id: str
    control_id: str
    severity: str
    description: str
    recommendation: str
    due_date: datetime
    status: str  # OPEN, IN_PROGRESS, RESOLVED
    assigned_to: str

COMPLIANCE_CONTROLS = [
    ComplianceControl(
        control_id="SOC2-CC1.1",
        standard="SOC2",
        requirement="The entity demonstrates commitment to integrity and ethical values.",
        description="Code of conduct and ethics training implemented",
        implementation_status="IMPLEMENTED",
        last_audit_date=fake.date_time_between(start_date="-90d", end_date="-60d"),
        next_audit_date=datetime.now() + timedelta(days=30),
        evidence=["Policy_Document_v2.1.pdf", "Training_Records_2024.xlsx", "Employee_Signatures.zip"],
        owner="HR Department"
    ),
    ComplianceControl(
        control_id="ISO27001-A.12.4",
        standard="ISO27001",
        requirement="Logging and monitoring",
        description="Security events are logged, monitored, and analyzed",
        implementation_status="IMPLEMENTED",
        last_audit_date=fake.date_time_between(start_date="-60d", end_date="-30d"),
        next_audit_date=datetime.now() + timedelta(days=45),
        evidence=["SIEM_Configuration.json", "Alert_Dashboard.png", "Incident_Response_Logs.csv"],
        owner="Security Team"
    ),
    ComplianceControl(
        control_id="GDPR-Art.25",
        standard="GDPR",
        requirement="Data protection by design and by default",
        description="Privacy considerations integrated into system design",
        implementation_status="PARTIAL",
        last_audit_date=fake.date_time_between(start_date="-45d", end_date="-15d"),
        next_audit_date=datetime.now() + timedelta(days=60),
        evidence=["Privacy_Impact_Assessment.pdf", "Data_Flow_Diagrams.vsdx", "DPO_Review_Notes.docx"],
        owner="Privacy Office"
    ),
    ComplianceControl(
        control_id="PCI-DSS-3.2",
        standard="PCI-DSS",
        requirement="Protect stored cardholder data",
        description="Encryption of cardholder data at rest",
        implementation_status="IMPLEMENTED",
        last_audit_date=fake.date_time_between(start_date="-30d", end_date="-10d"),
        next_audit_date=datetime.now() + timedelta(days=90),
        evidence=["Encryption_Certificates.pem", "Key_Rotation_Schedule.xlsx", "HSM_Configuration.backup"],
        owner="Payment Security Team"
    ),
]

AUDIT_FINDINGS = [
    AuditFinding(
        finding_id="AUD-2024-001",
        control_id="SOC2-CC1.1",
        severity="MEDIUM",
        description="Annual ethics training completion rate below target (85% vs 95% target)",
        recommendation="Implement automated reminders and escalation process",
        due_date=datetime.now() + timedelta(days=45),
        status="IN_PROGRESS",
        assigned_to="hr-manager@company.com"
    ),
    AuditFinding(
        finding_id="AUD-2024-002",
        control_id="ISO27001-A.12.4",
        severity="HIGH",
        description="Log retention period (30 days) does not meet requirement (90 days)",
        recommendation="Extend log retention to 90 days and implement archival process",
        due_date=datetime.now() + timedelta(days=30),
        status="OPEN",
        assigned_to="security-lead@company.com"
    ),
    AuditFinding(
        finding_id="AUD-2024-003",
        control_id="GDPR-Art.25",
        severity="LOW",
        description="Data minimization not fully implemented in new user registration flow",
        recommendation="Review and reduce data collection to minimum necessary",
        due_date=datetime.now() + timedelta(days=60),
        status="RESOLVED",
        assigned_to="product-manager@company.com"
    ),
]

# ============================================================================
# 8. USER BEHAVIOR DATA
# ============================================================================

@dataclass
class UserSession:
    user_id: str
    session_id: str
    start_time: datetime
    end_time: datetime
    ip_address: str
    user_agent: str
    pages_visited: List[str]
    actions: List[Dict[str, Any]]
    device_type: str
    country: str

def generate_user_sessions(count: int = 500) -> List[UserSession]:
    """Generate realistic user behavior data."""
    sessions = []
    
    for _ in range(count):
        user_id = f"user_{fake.uuid4()[:8]}"
        session_id = f"session_{fake.uuid4()}"
        start_time = fake.date_time_between(start_date="-7d", end_date="now")
        duration = random.randint(60, 3600)  # 1 minute to 1 hour
        end_time = start_time + timedelta(seconds=duration)
        
        # Generate page visits
        pages = []
        actions = []
        
        current_time = start_time
        page_count = random.randint(1, 20)
        
        for i in range(page_count):
            page_duration = random.randint(5, 300)
            page = random.choice([
                "/dashboard",
                "/users/list",
                "/reports/performance",
                "/alerts",
                "/settings/profile",
                "/billing",
                "/help",
                "/api-docs"
            ])
            pages.append(page)
            
            # Generate actions on page
            page_actions = random.randint(1, 5)
            for j in range(page_actions):
                action_time = current_time + timedelta(seconds=random.randint(0, page_duration))
                actions.append({
                    "timestamp": action_time,
                    "action_type": random.choice(["click", "hover", "scroll", "input", "submit"]),
                    "element": random.choice(["button.save", "link.documentation", "input.search", "form.login"]),
                    "value": fake.word() if random.random() > 0.5 else None
                })
            
            current_time += timedelta(seconds=page_duration)
            if current_time >= end_time:
                break
        
        sessions.append(UserSession(
            user_id=user_id,
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            ip_address=fake.ipv4(),
            user_agent=random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
                "curl/7.68.0",
                "PostmanRuntime/7.32.3"
            ]),
            pages_visited=pages,
            actions=actions,
            device_type=random.choice(["desktop", "mobile", "tablet", "api-client"]),
            country=fake.country_code()
        ))
    
    return sessions

# ============================================================================
# 9. BUSINESS METRICS
# ============================================================================

@dataclass
class BusinessMetric:
    metric_name: str
    timestamp: datetime
    value: float
    unit: str
    segment: Optional[str] = None

def generate_business_metrics(days: int = 90) -> List[BusinessMetric]:
    """Generate business performance metrics."""
    metrics = []
    base_date = datetime.now() - timedelta(days=days)
    
    for day in range(days):
        date = base_date + timedelta(days=day)
        
        # Daily Active Users (DAU)
        is_weekend = date.weekday() >= 5
        dau_base = 10000
        dau = dau_base * (0.7 if is_weekend else 1.0) * random.uniform(0.95, 1.05)
        
        # Monthly Recurring Revenue (MRR)
        mrr_growth = 1.0 + (day * 0.005)  # 0.5% daily growth
        mrr = 50000 * mrr_growth * random.uniform(0.98, 1.02)
        
        # Customer Acquisition Cost (CAC)
        cac = 120 * random.uniform(0.9, 1.1)
        
        # Customer Lifetime Value (LTV)
        ltv = 1500 * random.uniform(0.95, 1.05)
        
        # Churn Rate
        churn_base = 0.025  # 2.5%
        churn = churn_base * random.uniform(0.8, 1.2)
        
        metrics.extend([
            BusinessMetric("DAU", date, round(dau), "users"),
            BusinessMetric("MRR", date, round(mrr, 2), "USD"),
            BusinessMetric("CAC", date, round(cac, 2), "USD"),
            BusinessMetric("LTV", date, round(ltv, 2), "USD"),
            BusinessMetric("Churn Rate", date, round(churn, 4), "percent"),
        ])
        
        # Add segmented metrics
        for segment in ["enterprise", "smb", "startup"]:
            segment_multiplier = {
                "enterprise": 1.5,
                "smb": 1.0,
                "startup": 0.7
            }[segment]
            
            metrics.append(BusinessMetric(
                "MRR",
                date,
                round(mrr * segment_multiplier * random.uniform(0.9, 1.1), 2),
                "USD",
                segment
            ))
    
    return metrics

# ============================================================================
# 10. EXTERNAL API RESPONSES
# ============================================================================

EXTERNAL_API_RESPONSES = {
    "aws_cloudwatch": {
        "GetMetricStatistics": {
            "Label": "CPUUtilization",
            "Datapoints": [
                {
                    "Timestamp": (datetime.now() - timedelta(hours=i)).isoformat(),
                    "Average": random.uniform(10, 90),
                    "Unit": "Percent"
                }
                for i in range(24, 0, -1)
            ]
        }
    },
    "slack_webhook": {
        "ok": True,
        "channel": "C1234567890",
        "ts": "1503435956.000247",
        "message": {
            "text": "Alert: High CPU usage detected",
            "username": "MicroAgents Bot",
            "bot_id": "B12345678",
            "type": "message"
        }
    },
    "pagerduty": {
        "incident": {
            "id": "P1234567",
            "type": "incident",
            "summary": "High CPU usage on production servers",
            "status": "triggered",
            "urgency": "high",
            "created_at": datetime.now().isoformat(),
            "assignments": [
                {
                    "at": datetime.now().isoformat(),
                    "assignee": {
                        "id": "P123456",
                        "type": "user_reference",
                        "summary": "John Doe"
                    }
                }
            ]
        }
    },
    "github": {
        "workflow_run": {
            "id": 30433642,
            "name": "CI/CD Pipeline",
            "head_branch": "main",
            "head_sha": "acb5823e8d7c7e5d6a8b9c0d1e2f3a4b5c6d7e8f",
            "run_number": 123,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
            "updated_at": datetime.now().isoformat()
        }
    },
    "datadog": {
        "status": "ok",
        "event": {
            "id": 1234567890123456789,
            "title": "High CPU usage on api-server-1",
            "text": "CPU usage is above 90% for 5 minutes",
            "date_happened": int((datetime.now() - timedelta(minutes=10)).timestamp()),
            "priority": "normal",
            "source": "MicroAgents",
            "alert_type": "warning"
        }
    },
    "sentry": {
        "id": "1234567890abcdef",
        "project": "microagents-api",
        "release": "v1.2.3",
        "platform": "python",
        "culprit": "app/services/payment.py in process_payment",
        "title": "PaymentProcessingError: Invalid card number",
        "timestamp": datetime.now().isoformat(),
        "level": "error",
        "metadata": {
            "type": "PaymentProcessingError",
            "value": "Invalid card number"
        }
    },
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def anonymize_data(data: Any, salt: str = "microagents") -> Any:
    """Anonymize sensitive data for testing."""
    if isinstance(data, str) and "@" in data:
        # Anonymize emails
        username, domain = data.split("@")
        hashed = hashlib.sha256((username + salt).encode()).hexdigest()[:8]
        return f"{hashed}@{domain}"
    
    elif isinstance(data, str) and data.replace(".", "").isdigit():
        # Anonymize IP addresses
        parts = data.split(".")
        if len(parts) == 4:
            parts[-1] = "xxx"
            parts[-2] = "xxx"
            return ".".join(parts)
    
    elif isinstance(data, dict):
        return {k: anonymize_data(v, salt) for k, v in data.items()}
    
    elif isinstance(data, list):
        return [anonymize_data(item, salt) for item in data]
    
    return data

def export_mock_data() -> Dict[str, Any]:
    """Export all mock data as a dictionary."""
    end_time = datetime.now()
    start_time = end_time - timedelta(days=7)
    
    return {
        "metrics": [asdict(m) for m in generate_metrics_data(start_time, end_time, 15)],
        "logs": [asdict(l) for l in generate_log_samples(100)],
        "error_scenarios": [asdict(e) for e in ERROR_SCENARIOS],
        "performance_benchmarks": [asdict(p) for p in PERFORMANCE_BENCHMARKS],
        "cost_data": [asdict(c) for c in generate_cost_data(7)],
        "security_events": [asdict(s) for s in SECURITY_EVENTS],
        "compliance_controls": [asdict(c) for c in COMPLIANCE_CONTROLS],
        "audit_findings": [asdict(a) for a in AUDIT_FINDINGS],
        "user_sessions": [asdict(u) for u in generate_user_sessions(50)],
        "business_metrics": [asdict(b) for b in generate_business_metrics(30)],
        "external_api_responses": EXTERNAL_API_RESPONSES
    }

# ============================================================================
# MAIN DATA EXPORT
# ============================================================================

if __name__ == "__main__":
    # Export all mock data
    all_data = export_mock_data()
    
    # Save to JSON file
    with open("mock_data_export.json", "w") as f:
        json.dump(all_data, f, default=str, indent=2)
    
    print(f"Generated {len(all_data['metrics'])} metric data points")
    print(f"Generated {len(all_data['logs'])} log entries")
    print(f"Generated {len(all_data['cost_data'])} cost items")
    print(f"Generated {len(all_data['user_sessions'])} user sessions")
    print(f"Generated {len(all_data['business_metrics'])} business metrics")
    print("\nMock data exported to mock_data_export.json")