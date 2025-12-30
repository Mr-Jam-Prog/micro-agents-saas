#!/bin/bash
#
# Health Check Script for MicroAgents Platform
# Comprehensive monitoring with readiness, liveness, and startup probes
#
# Usage:
#   ./healthcheck.sh [--type <check_type>] [--level <severity>] [--output <format>]
#
# Check Types:
#   readiness    - Application readiness (default)
#   liveness     - Application liveness
#   startup      - Startup initialization
#   full         - All checks (including security and compliance)
#
# Severity Levels:
#   critical     - Only critical checks (default for liveness)
#   warning      - Critical + warning checks (default for readiness)
#   info         - All checks (default for full)
#
# Output Formats:
#   json         - JSON output (default)
#   human        - Human readable
#   prometheus   - Prometheus metrics
#   exitcode     - Only exit code (0=healthy, 1=unhealthy)
#

set -o pipefail
shopt -s nocasematch

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config/healthcheck.conf"
LOG_FILE="/var/log/microagents/healthcheck.log"
TIMESTAMP=$(date +%Y-%m-%dT%H:%M:%S%z)
HOSTNAME=$(hostname -f)
INSTANCE_ID=${INSTANCE_ID:-$(hostname)}

# Default values
CHECK_TYPE="readiness"
SEVERITY_LEVEL="warning"
OUTPUT_FORMAT="json"
VERBOSE=false
DRY_RUN=false
TIMEOUT=30

# Health endpoints
APP_HEALTH_PORT=${APP_HEALTH_PORT:-8080}
APP_HEALTH_ENDPOINT="http://localhost:${APP_HEALTH_PORT}/health"
API_HEALTH_ENDPOINT="http://localhost:${APP_HEALTH_PORT}/api/v1/health"
METRICS_ENDPOINT="http://localhost:${APP_HEALTH_PORT}/metrics"
READINESS_ENDPOINT="http://localhost:${APP_HEALTH_PORT}/health/ready"
LIVENESS_ENDPOINT="http://localhost:${APP_HEALTH_PORT}/health/live"
STARTUP_ENDPOINT="http://localhost:${APP_HEALTH_PORT}/health/startup"

# Database configuration
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-microagents}
DB_USER=${DB_USER:-microagents}
DB_CONNECT_TIMEOUT=5

# Cache configuration
REDIS_HOST=${REDIS_HOST:-localhost}
REDIS_PORT=${REDIS_PORT:-6379}
REDIS_PASSWORD=${REDIS_PASSWORD:-}
CACHE_KEY_PREFIX="healthcheck"

# External services
K8S_API=${K8S_API_SERVER:-https://kubernetes.default.svc}
ELASTICSEARCH_HOST=${ELASTICSEARCH_HOST:-localhost:9200}
PROMETHEUS_HOST=${PROMETHEUS_HOST:-localhost:9090}
ALERTMANAGER_HOST=${ALERTMANAGER_HOST:-localhost:9093}

# Thresholds (configurable via environment)
MAX_CPU_PERCENT=${MAX_CPU_PERCENT:-80}
MAX_MEMORY_PERCENT=${MAX_MEMORY_PERCENT:-85}
MAX_DISK_PERCENT=${MAX_DISK_PERCENT:-90}
MAX_LATENCY_MS=${MAX_LATENCY_MS:-1000}
MIN_FREE_DISK_GB=${MIN_FREE_DISK_GB:-10}
MAX_COST_DAILY=${MAX_COST_DAILY:-1000}
MIN_AVAILABLE_REPLICAS=${MIN_AVAILABLE_REPLICAS:-2}

# Security thresholds
MAX_FAILED_LOGINS=${MAX_FAILED_LOGINS:-10}
MAX_OPEN_PORTS=${MAX_OPEN_PORTS:-50}
SSL_EXPIRY_DAYS=${SSL_EXPIRY_DAYS:-30}

# Colors for human output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Global variables
declare -A CHECK_RESULTS
declare -A CHECK_MESSAGES
declare -A CHECK_DURATIONS
OVERALL_STATUS="healthy"
EXIT_CODE=0
START_TIME=$(date +%s.%N)

# Load configuration if exists
if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
fi

# Logging functions
log_message() {
    local level="$1"
    local message="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message" | tee -a "$LOG_FILE" >&2
}

log_debug() {
    if [[ "$VERBOSE" == true ]]; then
        log_message "DEBUG" "$1"
    fi
}

log_info() {
    log_message "INFO" "$1"
}

log_warning() {
    log_message "WARNING" "$1"
}

log_error() {
    log_message "ERROR" "$1"
}

# Helper functions
check_dependency() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Dependency missing: $cmd"
        return 1
    fi
    return 0
}

http_check() {
    local url="$1"
    local timeout="$2"
    local expected_status="${3:-200}"
    
    local response
    response=$(curl --max-time "$timeout" --silent --output /dev/null --write-out "%{http_code}" "$url" 2>/dev/null || echo "000")
    
    if [[ "$response" == "$expected_status" ]]; then
        return 0
    else
        log_debug "HTTP check failed for $url: status $response"
        return 1
    fi
}

json_check() {
    local url="$1"
    local timeout="$2"
    local jq_filter="$3"
    local expected_value="$4"
    
    local response
    response=$(curl --max-time "$timeout" --silent "$url" 2>/dev/null)
    
    if [[ -z "$response" ]]; then
        return 1
    fi
    
    local actual_value
    if actual_value=$(echo "$response" | jq -r "$jq_filter" 2>/dev/null); then
        if [[ "$actual_value" == "$expected_value" ]]; then
            return 0
        fi
    fi
    
    return 1
}

# Check functions
check_application_health() {
    local check_name="application_health"
    local start_time=$(date +%s.%N)
    
    if http_check "$APP_HEALTH_ENDPOINT" 5; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="Application health endpoint responding"
    else
        CHECK_RESULTS["$check_name"]="unhealthy"
        CHECK_MESSAGES["$check_name"]="Application health endpoint not responding"
        EXIT_CODE=1
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_api_health() {
    local check_name="api_health"
    local start_time=$(date +%s.%N)
    
    if json_check "$API_HEALTH_ENDPOINT" 5 ".status" "healthy"; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="API health endpoint responding with healthy status"
    else
        CHECK_RESULTS["$check_name"]="unhealthy"
        CHECK_MESSAGES["$check_name"]="API health endpoint not responding or status not healthy"
        EXIT_CODE=1
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_database_connectivity() {
    local check_name="database_connectivity"
    local start_time=$(date +%s.%N)
    
    if check_dependency "pg_isready"; then
        if PGPASSWORD="$DB_PASSWORD" pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t "$DB_CONNECT_TIMEOUT" &>/dev/null; then
            CHECK_RESULTS["$check_name"]="healthy"
            CHECK_MESSAGES["$check_name"]="Database connection successful"
        else
            CHECK_RESULTS["$check_name"]="unhealthy"
            CHECK_MESSAGES["$check_name"]="Database connection failed"
            EXIT_CODE=1
        fi
    else
        # Fallback check using psql
        if PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" -t &>/dev/null; then
            CHECK_RESULTS["$check_name"]="healthy"
            CHECK_MESSAGES["$check_name"]="Database connection successful"
        else
            CHECK_RESULTS["$check_name"]="unhealthy"
            CHECK_MESSAGES["$check_name"]="Database connection failed"
            EXIT_CODE=1
        fi
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_database_performance() {
    local check_name="database_performance"
    local start_time=$(date +%s.%N)
    
    if [[ -n "$DB_PASSWORD" ]]; then
        local query_time
        query_time=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "\timing on; SELECT 1;" 2>/dev/null | grep -oP 'Time: \K[0-9.]+' || echo "1000")
        
        if [[ $(echo "$query_time < 100" | bc -l 2>/dev/null || echo 0) -eq 1 ]]; then
            CHECK_RESULTS["$check_name"]="healthy"
            CHECK_MESSAGES["$check_name"]="Database query performance good (${query_time}ms)"
        elif [[ $(echo "$query_time < 500" | bc -l 2>/dev/null || echo 0) -eq 1 ]]; then
            CHECK_RESULTS["$check_name"]="warning"
            CHECK_MESSAGES["$check_name"]="Database query performance slow (${query_time}ms)"
            [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
        else
            CHECK_RESULTS["$check_name"]="unhealthy"
            CHECK_MESSAGES["$check_name"]="Database query performance very slow (${query_time}ms)"
            EXIT_CODE=1
        fi
    else
        CHECK_RESULTS["$check_name"]="unknown"
        CHECK_MESSAGES["$check_name"]="Database password not set, skipping performance check"
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_cache_responsiveness() {
    local check_name="cache_responsiveness"
    local start_time=$(date +%s.%N)
    
    if check_dependency "redis-cli"; then
        local redis_cmd="redis-cli -h $REDIS_HOST -p $REDIS_PORT"
        
        if [[ -n "$REDIS_PASSWORD" ]]; then
            redis_cmd="$redis_cmd -a $REDIS_PASSWORD"
        fi
        
        if $redis_cmd ping &>/dev/null; then
            # Test read/write performance
            local test_key="${CACHE_KEY_PREFIX}_test_$(date +%s)"
            local test_value="healthcheck_$(date +%s)"
            
            if $redis_cmd setex "$test_key" 10 "$test_value" &>/dev/null; then
                local retrieved_value
                retrieved_value=$($redis_cmd get "$test_key" 2>/dev/null)
                
                if [[ "$retrieved_value" == "$test_value" ]]; then
                    CHECK_RESULTS["$check_name"]="healthy"
                    CHECK_MESSAGES["$check_name"]="Cache read/write operations successful"
                    
                    # Clean up
                    $redis_cmd del "$test_key" &>/dev/null
                else
                    CHECK_RESULTS["$check_name"]="unhealthy"
                    CHECK_MESSAGES["$check_name"]="Cache read verification failed"
                    EXIT_CODE=1
                fi
            else
                CHECK_RESULTS["$check_name"]="unhealthy"
                CHECK_MESSAGES["$check_name"]="Cache write operation failed"
                EXIT_CODE=1
            fi
        else
            CHECK_RESULTS["$check_name"]="unhealthy"
            CHECK_MESSAGES["$check_name"]="Cache ping failed"
            EXIT_CODE=1
        fi
    else
        CHECK_RESULTS["$check_name"]="unknown"
        CHECK_MESSAGES["$check_name"]="redis-cli not available, skipping cache check"
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_external_services() {
    local check_name="external_services"
    local start_time=$(date +%s.%N)
    local unhealthy_count=0
    local total_count=0
    
    declare -A services=(
        ["kubernetes"]="$K8S_API/healthz"
        ["elasticsearch"]="http://$ELASTICSEARCH_HOST/_cluster/health"
        ["prometheus"]="http://$PROMETHEUS_HOST/-/healthy"
        ["alertmanager"]="http://$ALERTMANAGER_HOST/-/healthy"
    )
    
    for service in "${!services[@]}"; do
        ((total_count++))
        if http_check "${services[$service]}" 5; then
            log_debug "External service $service is healthy"
        else
            log_warning "External service $service is unhealthy"
            ((unhealthy_count++))
        fi
    done
    
    if [[ $unhealthy_count -eq 0 ]]; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="All external services responding"
    elif [[ $unhealthy_count -lt $total_count ]]; then
        CHECK_RESULTS["$check_name"]="warning"
        CHECK_MESSAGES["$check_name"]="$unhealthy_count of $total_count external services unhealthy"
        [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
    else
        CHECK_RESULTS["$check_name"]="unhealthy"
        CHECK_MESSAGES["$check_name"]="All external services unhealthy"
        EXIT_CODE=1
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_performance_metrics() {
    local check_name="performance_metrics"
    local start_time=$(date +%s.%N)
    local warning_count=0
    
    # CPU usage
    local cpu_usage
    cpu_usage=$(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print 100 - $1}')
    
    # Memory usage
    local mem_total mem_used mem_percent
    mem_total=$(free -m | awk '/^Mem:/{print $2}')
    mem_used=$(free -m | awk '/^Mem:/{print $3}')
    mem_percent=$((mem_used * 100 / mem_total))
    
    # Disk usage
    local disk_percent
    disk_percent=$(df / --output=pcent | tail -1 | tr -d '% ')
    
    # Disk free space in GB
    local disk_free_gb
    disk_free_gb=$(df -BG / | awk 'NR==2 {print $4}' | tr -d 'G')
    
    # Check thresholds
    if [[ $(echo "$cpu_usage > $MAX_CPU_PERCENT" | bc -l 2>/dev/null || echo 0) -eq 1 ]]; then
        log_warning "CPU usage high: ${cpu_usage}% > ${MAX_CPU_PERCENT}%"
        ((warning_count++))
    fi
    
    if [[ $mem_percent -gt $MAX_MEMORY_PERCENT ]]; then
        log_warning "Memory usage high: ${mem_percent}% > ${MAX_MEMORY_PERCENT}%"
        ((warning_count++))
    fi
    
    if [[ $disk_percent -gt $MAX_DISK_PERCENT ]]; then
        log_warning "Disk usage high: ${disk_percent}% > ${MAX_DISK_PERCENT}%"
        ((warning_count++))
    fi
    
    if [[ $disk_free_gb -lt $MIN_FREE_DISK_GB ]]; then
        log_warning "Disk free space low: ${disk_free_gb}GB < ${MIN_FREE_DISK_GB}GB"
        ((warning_count++))
    fi
    
    if [[ $warning_count -eq 0 ]]; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="Performance metrics within thresholds (CPU: ${cpu_usage}%, Mem: ${mem_percent}%, Disk: ${disk_percent}%)"
    else
        CHECK_RESULTS["$check_name"]="warning"
        CHECK_MESSAGES["$check_name"]="$warning_count performance metrics outside thresholds"
        [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_security_compliance() {
    local check_name="security_compliance"
    local start_time=$(date +%s.%N)
    local warning_count=0
    
    # Check for failed login attempts
    local failed_logins
    failed_logins=$(grep "Failed password" /var/log/auth.log 2>/dev/null | wc -l || echo 0)
    
    if [[ $failed_logins -gt $MAX_FAILED_LOGINS ]]; then
        log_warning "High number of failed logins: $failed_logins"
        ((warning_count++))
    fi
    
    # Check open ports
    local open_ports
    if check_dependency "ss"; then
        open_ports=$(ss -tuln | grep -c LISTEN)
    else
        open_ports=$(netstat -tuln | grep -c LISTEN)
    fi
    
    if [[ $open_ports -gt $MAX_OPEN_PORTS ]]; then
        log_warning "High number of open ports: $open_ports"
        ((warning_count++))
    fi
    
    # Check SSL certificate expiry (if applicable)
    if [[ -n "$SSL_CERT_FILE" && -f "$SSL_CERT_FILE" ]]; then
        local expiry_days
        expiry_days=$(openssl x509 -in "$SSL_CERT_FILE" -noout -enddate 2>/dev/null | cut -d= -f2 | xargs -I {} date -d {} +%s)
        local current_days=$(date +%s)
        local days_until_expiry=$(( (expiry_days - current_days) / 86400 ))
        
        if [[ $days_until_expiry -lt $SSL_EXPIRY_DAYS ]]; then
            log_warning "SSL certificate expires in $days_until_expiry days"
            ((warning_count++))
        fi
    fi
    
    if [[ $warning_count -eq 0 ]]; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="Security compliance checks passed"
    else
        CHECK_RESULTS["$check_name"]="warning"
        CHECK_MESSAGES["$check_name"]="$warning_count security compliance warnings"
        [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_cost_threshold() {
    local check_name="cost_threshold"
    local start_time=$(date +%s.%N)
    
    # This check would typically query a cost management API
    # For now, we'll simulate with environment variables or local cache
    
    local current_cost=0
    local cost_source="unknown"
    
    # Try to get cost from various sources
    if [[ -f "/var/cache/microagents/daily_cost.txt" ]]; then
        current_cost=$(cat "/var/cache/microagents/daily_cost.txt" 2>/dev/null || echo 0)
        cost_source="file"
    elif [[ -n "$DAILY_COST" ]]; then
        current_cost=$DAILY_COST
        cost_source="env"
    else
        # Fallback: estimate based on instance type
        local instance_type
        instance_type=$(curl -s --max-time 2 http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo "unknown")
        
        # Rough cost estimates for common instance types (USD per day)
        declare -A instance_costs=(
            ["t2.micro"]="0.50"
            ["t2.small"]="1.00"
            ["t2.medium"]="2.00"
            ["m5.large"]="4.00"
            ["m5.xlarge"]="8.00"
            ["unknown"]="10.00"
        )
        
        current_cost=${instance_costs[$instance_type]:-10.00}
        cost_source="estimated"
    fi
    
    if [[ $(echo "$current_cost <= $MAX_COST_DAILY" | bc -l 2>/dev/null || echo 1) -eq 1 ]]; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="Daily cost within threshold ($${current_cost} <= $${MAX_COST_DAILY}, source: ${cost_source})"
    else
        CHECK_RESULTS["$check_name"]="warning"
        CHECK_MESSAGES["$check_name"]="Daily cost exceeding threshold ($${current_cost} > $${MAX_COST_DAILY})"
        [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_sla_compliance() {
    local check_name="sla_compliance"
    local start_time=$(date +%s.%N)
    
    # Check if SLA metrics are being met
    # This would typically query a monitoring system
    local sla_violations=0
    
    # Check uptime (simplified)
    local uptime_days
    uptime_days=$(awk '{print $1/86400}' /proc/uptime 2>/dev/null || echo 0)
    
    # Check error rate from metrics endpoint
    local error_rate=0
    if http_check "$METRICS_ENDPOINT" 5; then
        local metrics_response
        metrics_response=$(curl --max-time 5 --silent "$METRICS_ENDPOINT" 2>/dev/null)
        
        # Extract error rate from Prometheus metrics (simplified)
        error_rate=$(echo "$metrics_response" | grep 'http_request_error_total' | tail -1 | awk '{print $2}' || echo 0)
    fi
    
    # Check response time
    local response_time=0
    if [[ -n "$APP_HEALTH_ENDPOINT" ]]; then
        response_time=$(curl --max-time 5 --write-out '%{time_total}' --silent --output /dev/null "$APP_HEALTH_ENDPOINT" 2>/dev/null || echo 10)
        response_time=$(echo "$response_time * 1000" | bc -l 2>/dev/null || echo 10000)
    fi
    
    # Evaluate SLA criteria
    if [[ $(echo "$response_time > $MAX_LATENCY_MS" | bc -l 2>/dev/null || echo 0) -eq 1 ]]; then
        log_warning "Response time SLA violation: ${response_time}ms > ${MAX_LATENCY_MS}ms"
        ((sla_violations++))
    fi
    
    if [[ $(echo "$error_rate > 0.01" | bc -l 2>/dev/null || echo 0) -eq 1 ]]; then  # 1% error rate
        log_warning "Error rate SLA violation: ${error_rate}"
        ((sla_violations++))
    fi
    
    if [[ $sla_violations -eq 0 ]]; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="SLA compliance metrics met (uptime: ${uptime_days} days, latency: ${response_time}ms)"
    else
        CHECK_RESULTS["$check_name"]="warning"
        CHECK_MESSAGES["$check_name"]="$sla_violations SLA compliance violations detected"
        [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_capacity_planning() {
    local check_name="capacity_planning"
    local start_time=$(date +%s.%N)
    
    # Check system capacity and projections
    local capacity_warnings=0
    
    # Check memory growth trend (simplified)
    local mem_growth_file="/var/log/microagents/memory_trend.log"
    if [[ -f "$mem_growth_file" ]]; then
        local recent_growth
        recent_growth=$(tail -10 "$mem_growth_file" | awk '{sum+=$2} END {print sum/NR}' 2>/dev/null || echo 0)
        
        if [[ $(echo "$recent_growth > 5" | bc -l 2>/dev/null || echo 0) -eq 1 ]]; then  # >5% growth per measurement
            log_warning "High memory growth trend detected"
            ((capacity_warnings++))
        fi
    fi
    
    # Check disk usage trend
    local disk_usage_trend=0
    local df_output
    df_output=$(df / --output=pcent | tail -1 | tr -d '% ')
    
    # Check if we're approaching capacity
    if [[ $df_output -gt 80 ]]; then
        log_warning "Disk usage approaching capacity: ${df_output}%"
        ((capacity_warnings++))
    fi
    
    # Check if scaling is needed based on load
    local load_average
    load_average=$(awk '{print $1}' /proc/loadavg)
    local cpu_cores
    cpu_cores=$(nproc)
    
    if [[ $(echo "$load_average > $cpu_cores * 1.5" | bc -l 2>/dev/null || echo 0) -eq 1 ]]; then
        log_warning "High load average suggests need for scaling: ${load_average} > $((cpu_cores * 3 / 2))"
        ((capacity_warnings++))
    fi
    
    if [[ $capacity_warnings -eq 0 ]]; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="Capacity planning checks passed"
    else
        CHECK_RESULTS["$check_name"]="warning"
        CHECK_MESSAGES["$check_name"]="$capacity_warnings capacity planning warnings"
        [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_disaster_recovery() {
    local check_name="disaster_recovery"
    local start_time=$(date +%s.%N)
    
    # Check disaster recovery readiness
    local dr_warnings=0
    
    # Check backup status
    local backup_status_file="/var/backups/microagents/last_backup.status"
    if [[ -f "$backup_status_file" ]]; then
        local backup_status
        backup_status=$(cat "$backup_status_file" 2>/dev/null)
        
        if [[ "$backup_status" != "success" ]]; then
            log_warning "Last backup was not successful"
            ((dr_warnings++))
        fi
        
        # Check backup age
        local backup_timestamp_file="/var/backups/microagents/last_backup.timestamp"
        if [[ -f "$backup_timestamp_file" ]]; then
            local backup_time
            backup_time=$(cat "$backup_timestamp_file" 2>/dev/null)
            local current_time
            current_time=$(date +%s)
            local backup_age_hours=$(( (current_time - backup_time) / 3600 ))
            
            if [[ $backup_age_hours -gt 24 ]]; then
                log_warning "Backup is older than 24 hours (${backup_age_hours}h)"
                ((dr_warnings++))
            fi
        fi
    else
        log_warning "No backup status file found"
        ((dr_warnings++))
    fi
    
    # Check replication status (if applicable)
    if [[ -n "$DB_HOST" && "$DB_HOST" != "localhost" ]]; then
        if check_dependency "pg_isready"; then
            # Check if we can connect to primary and replica
            local replica_host="${DB_HOST}-replica"
            if ! PGPASSWORD="$DB_PASSWORD" pg_isready -h "$replica_host" -p "$DB_PORT" -U "$DB_USER" -t 2 &>/dev/null; then
                log_warning "Database replica is not available"
                ((dr_warnings++))
            fi
        fi
    fi
    
    if [[ $dr_warnings -eq 0 ]]; then
        CHECK_RESULTS["$check_name"]="healthy"
        CHECK_MESSAGES["$check_name"]="Disaster recovery readiness checks passed"
    else
        CHECK_RESULTS["$check_name"]="warning"
        CHECK_MESSAGES["$check_name"]="$dr_warnings disaster recovery readiness warnings"
        [[ "$EXIT_CODE" -eq 0 ]] && EXIT_CODE=2
    fi
    
    CHECK_DURATIONS["$check_name"]=$(echo "$(date +%s.%N) - $start_time" | bc)
}

check_readiness() {
    log_info "Running readiness probe checks"
    
    # Basic checks for application readiness
    check_application_health
    check_api_health
    check_database_connectivity
    check_cache_responsiveness
    check_external_services
    
    if [[ "$SEVERITY_LEVEL" == "warning" ]] || [[ "$SEVERITY_LEVEL" == "info" ]]; then
        check_performance_metrics
        check_database_performance
    fi
}

check_liveness() {
    log_info "Running liveness probe checks"
    
    # Minimal checks for application liveness
    check_application_health
    check_api_health
    
    if [[ "$SEVERITY_LEVEL" == "warning" ]] || [[ "$SEVERITY_LEVEL" == "info" ]]; then
        check_database_connectivity
    fi
}

check_startup() {
    log_info "Running startup probe checks"
    
    # Comprehensive checks for startup completion
    check_application_health
    check_api_health
    check_database_connectivity
    check_cache_responsiveness
    check_database_performance
    check_performance_metrics
}

check_full() {
    log_info "Running full health checks"
    
    # All checks including security and compliance
    check_readiness
    check_startup
    
    # Additional comprehensive checks
    check_security_compliance
    check_cost_threshold
    check_sla_compliance
    check_capacity_planning
    check_disaster_recovery
}

# Output functions
output_json() {
    local end_time=$(date +%s.%N)
    local total_duration=$(echo "$end_time - $START_TIME" | bc)
    
    # Build checks array
    local checks_json=""
    for check_name in "${!CHECK_RESULTS[@]}"; do
        if [[ -n "$checks_json" ]]; then
            checks_json="${checks_json},"
        fi
        checks_json="${checks_json}{\"name\":\"$check_name\",\"status\":\"${CHECK_RESULTS[$check_name]}\",\"message\":\"${CHECK_MESSAGES[$check_name]//\"/\\\"}\",\"duration\":${CHECK_DURATIONS[$check_name]}}"
    done
    
    # Determine overall status
    local unhealthy_count=0
    local warning_count=0
    
    for status in "${CHECK_RESULTS[@]}"; do
        if [[ "$status" == "unhealthy" ]]; then
            ((unhealthy_count++))
        elif [[ "$status" == "warning" ]]; then
            ((warning_count++))
        fi
    done
    
    local overall_status="healthy"
    if [[ $unhealthy_count -gt 0 ]]; then
        overall_status="unhealthy"
    elif [[ $warning_count -gt 0 ]]; then
        overall_status="warning"
    fi
    
    cat <<EOF
{
  "timestamp": "$TIMESTAMP",
  "hostname": "$HOSTNAME",
  "instance_id": "$INSTANCE_ID",
  "check_type": "$CHECK_TYPE",
  "severity_level": "$SEVERITY_LEVEL",
  "overall_status": "$overall_status",
  "exit_code": $EXIT_CODE,
  "total_duration": $total_duration,
  "checks": [$checks_json]
}
EOF
}

output_human() {
    echo -e "${BOLD}=== MicroAgents Platform Health Check ===${NC}"
    echo -e "Timestamp:   $TIMESTAMP"
    echo -e "Hostname:    $HOSTNAME"
    echo -e "Instance ID: $INSTANCE_ID"
    echo -e "Check Type:  $CHECK_TYPE"
    echo -e "Severity:    $SEVERITY_LEVEL"
    echo
    
    echo -e "${BOLD}Check Results:${NC}"
    for check_name in "${!CHECK_RESULTS[@]}"; do
        local status="${CHECK_RESULTS[$check_name]}"
        local color="$GREEN"
        
        case "$status" in
            "healthy")
                color="$GREEN"
                ;;
            "warning")
                color="$YELLOW"
                ;;
            "unhealthy")
                color="$RED"
                ;;
            *)
                color="$BLUE"
                ;;
        esac
        
        echo -e "  ${color}●${NC} $(printf "%-25s" "$check_name") [$color$status$NC]"
        echo -e "     ${CHECK_MESSAGES[$check_name]}"
        echo -e "     Duration: ${CHECK_DURATIONS[$check_name]} seconds"
        echo
    done
    
    local end_time=$(date +%s.%N)
    local total_duration=$(echo "$end_time - $START_TIME" | bc)
    
    echo -e "${BOLD}Summary:${NC}"
    echo -e "  Total checks: ${#CHECK_RESULTS[@]}"
    echo -e "  Total duration: ${total_duration} seconds"
    
    # Determine overall status with color
    local overall_color="$GREEN"
    local overall_status="HEALTHY"
    
    if [[ $EXIT_CODE -eq 1 ]]; then
        overall_color="$RED"
        overall_status="UNHEALTHY"
    elif [[ $EXIT_CODE -eq 2 ]]; then
        overall_color="$YELLOW"
        overall_status="WARNING"
    fi
    
    echo -e "  ${BOLD}Overall Status: ${overall_color}${overall_status}${NC}"
    echo -e "  ${BOLD}Exit Code: $EXIT_CODE${NC}"
}

output_prometheus() {
    local end_time=$(date +%s.%N)
    local total_duration=$(echo "$end_time - $START_TIME" | bc)
    
    echo "# HELP healthcheck_status Overall health check status (0=healthy, 1=warning, 2=unhealthy)"
    echo "# TYPE healthcheck_status gauge"
    echo "healthcheck_status{hostname=\"$HOSTNAME\",instance_id=\"$INSTANCE_ID\",check_type=\"$CHECK_TYPE\"} $EXIT_CODE"
    
    echo "# HELP healthcheck_duration_seconds Total duration of health check"
    echo "# TYPE healthcheck_duration_seconds gauge"
    echo "healthcheck_duration_seconds{hostname=\"$HOSTNAME\",instance_id=\"$INSTANCE_ID\",check_type=\"$CHECK_TYPE\"} $total_duration"
    
    for check_name in "${!CHECK_RESULTS[@]}"; do
        local status_value=0
        case "${CHECK_RESULTS[$check_name]}" in
            "healthy") status_value=0 ;;
            "warning") status_value=1 ;;
            "unhealthy") status_value=2 ;;
            *) status_value=3 ;;
        esac
        
        echo "# HELP healthcheck_individual_status Individual check status"
        echo "# TYPE healthcheck_individual_status gauge"
        echo "healthcheck_individual_status{hostname=\"$HOSTNAME\",check=\"$check_name\"} $status_value"
        
        echo "# HELP healthcheck_individual_duration_seconds Individual check duration"
        echo "# TYPE healthcheck_individual_duration_seconds gauge"
        echo "healthcheck_individual_duration_seconds{hostname=\"$HOSTNAME\",check=\"$check_name\"} ${CHECK_DURATIONS[$check_name]}"
    done
}

output_exitcode() {
    # Only output the exit code
    echo $EXIT_CODE
}

# Parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --type|-t)
                CHECK_TYPE="$2"
                shift 2
                ;;
            --level|-l)
                SEVERITY_LEVEL="$2"
                shift 2
                ;;
            --output|-o)
                OUTPUT_FORMAT="$2"
                shift 2
                ;;
            --verbose|-v)
                VERBOSE=true
                shift
                ;;
            --dry-run|-d)
                DRY_RUN=true
                shift
                ;;
            --timeout)
                TIMEOUT="$2"
                shift 2
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                show_help
                exit 1
                ;;
        esac
    done
    
    # Set defaults based on check type
    case "$CHECK_TYPE" in
        "liveness")
            [[ "$SEVERITY_LEVEL" == "warning" ]] && SEVERITY_LEVEL="critical"
            ;;
        "readiness")
            [[ "$SEVERITY_LEVEL" == "warning" ]] && SEVERITY_LEVEL="warning"
            ;;
        "startup"|"full")
            [[ "$SEVERITY_LEVEL" == "warning" ]] && SEVERITY_LEVEL="info"
            ;;
    esac
}

show_help() {
    cat <<EOF
MicroAgents Platform Health Check Script

Usage: $0 [OPTIONS]

Options:
  -t, --type TYPE      Check type: readiness, liveness, startup, full (default: readiness)
  -l, --level LEVEL    Severity level: critical, warning, info (default: warning)
  -o, --output FORMAT  Output format: json, human, prometheus, exitcode (default: json)
  -v, --verbose        Enable verbose logging
  -d, --dry-run        Perform dry run without actual checks
  --timeout SECONDS    Timeout for checks in seconds (default: 30)
  -h, --help           Show this help message

Examples:
  $0 --type readiness --output human
  $0 --type liveness --level critical --output exitcode
  $0 --type full --level info --output prometheus

Environment Variables:
  APP_HEALTH_PORT      Application health port (default: 8080)
  DB_HOST              Database host (default: localhost)
  REDIS_HOST           Redis host (default: localhost)
  MAX_CPU_PERCENT      Maximum CPU percentage threshold (default: 80)
  MAX_MEMORY_PERCENT   Maximum memory percentage threshold (default: 85)
  MAX_DISK_PERCENT     Maximum disk percentage threshold (default: 90)
  MAX_COST_DAILY       Maximum daily cost threshold (default: 1000)
EOF
}

# Main execution
main() {
    parse_arguments "$@"
    
    log_info "Starting health check: type=$CHECK_TYPE, level=$SEVERITY_LEVEL"
    
    # Check dependencies
    local missing_deps=0
    for dep in curl bc; do
        if ! check_dependency "$dep"; then
            ((missing_deps++))
        fi
    done
    
    if [[ $missing_deps -gt 0 ]]; then
        log_error "Missing $missing_deps required dependencies"
        exit 1
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        log_info "Dry run mode enabled, no checks will be performed"
        echo "{\"dry_run\": true, \"checks\": []}"
        exit 0
    fi
    
    # Run appropriate check based on type
    case "$CHECK_TYPE" in
        "readiness")
            check_readiness
            ;;
        "liveness")
            check_liveness
            ;;
        "startup")
            check_startup
            ;;
        "full")
            check_full
            ;;
        *)
            log_error "Unknown check type: $CHECK_TYPE"
            show_help
            exit 1
            ;;
    esac
    
    # Determine overall status based on exit code
    case $EXIT_CODE in
        0)
            OVERALL_STATUS="healthy"
            ;;
        2)
            OVERALL_STATUS="warning"
            ;;
        1)
            OVERALL_STATUS="unhealthy"
            ;;
    esac
    
    # Output results
    case "$OUTPUT_FORMAT" in
        "json")
            output_json
            ;;
        "human")
            output_human
            ;;
        "prometheus")
            output_prometheus
            ;;
        "exitcode")
            output_exitcode
            ;;
        *)
            log_error "Unknown output format: $OUTPUT_FORMAT"
            show_help
            exit 1
            ;;
    esac
    
    log_info "Health check completed: status=$OVERALL_STATUS, exit_code=$EXIT_CODE"
    
    # Ensure exit code is 0 or 1 for Kubernetes/container orchestration
    if [[ $EXIT_CODE -eq 2 ]]; then
        exit 0  # Warning is still considered healthy for orchestration
    else
        exit $EXIT_CODE
    fi
}

# Run main if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi