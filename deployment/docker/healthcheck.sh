#!/bin/bash
set -euo pipefail

# Health check script for MicroAgents Platform
# Returns 0 if healthy, 1 if unhealthy

# Configuration
HEALTH_PORT="${HEALTHCHECK_PORT:-8080}"
API_PORT="${UVICORN_PORT:-8000}"
HOST="${UVICORN_HOST:-0.0.0.0}"
TIMEOUT="${HEALTH_TIMEOUT:-10}"
MAX_RETRIES="${HEALTH_MAX_RETRIES:-3}"
RETRY_DELAY="${HEALTH_RETRY_DELAY:-2}"
HEALTH_ENDPOINT="${HEALTH_ENDPOINT:-http://$HOST:$API_PORT/api/v1/health}"

# Function to check health endpoint
check_health_endpoint() {
    local endpoint=$1
    local timeout=$2
    
    # Use curl to check health endpoint
    if curl -s -f --max-time "$timeout" "$endpoint" > /dev/null 2>&1; then
        # Check JSON response
        response=$(curl -s --max-time "$timeout" "$endpoint")
        if echo "$response" | python3 -c "import json, sys; data=json.load(sys.stdin); sys.exit(0 if data.get('status') == 'healthy' else 1)"; then
            return 0
        fi
    fi
    return 1
}

# Function to check Prometheus metrics
check_metrics() {
    local endpoint="http://$HOST:$API_PORT/metrics"
    
    if curl -s -f --max-time "$TIMEOUT" "$endpoint" | grep -q 'python_info'; then
        return 0
    fi
    return 1
}

# Function to check disk space
check_disk_space() {
    local threshold=${DISK_SPACE_THRESHOLD:-5} # Percentage
    
    local available=$(df /app --output=pcent | tail -1 | tr -d '% ' 2>/dev/null || echo 100)
    if [[ $available -lt $threshold ]]; then
        echo "Low disk space: ${available}% available"
        return 1
    fi
    
    return 0
}

# Function to check memory usage
check_memory() {
    local threshold=${MEMORY_THRESHOLD:-90} # Percentage
    
    # Try cgroup memory first
    if [ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
        memory_limit=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
        # If limit is very large (> 8EB), probably unlimited
        if [ "$memory_limit" -gt 9007199254740992 ]; then
            return 0  # Unlimited memory
        fi
        
        # Get current memory usage
        if [ -f /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
            memory_usage=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)
        else
            # Fallback: process memory usage
            memory_usage=$(ps -o rss= -p 1 | awk '{print $1 * 1024}')
        fi
        
        # Calculate percentage
        memory_percent=$(( memory_usage * 100 / memory_limit ))
        
        # Fail if > 90% usage
        if [ "$memory_percent" -gt 90 ]; then
            echo "Memory usage critical: ${memory_percent}%"
            return 1
        fi
        
        # Warning if > 80%
        if [ "$memory_percent" -gt 80 ]; then
            echo "Memory usage high: ${memory_percent}%"
        fi
        
        return 0
    else
        # Fallback to system memory
        local used=$(free | awk '/Mem:/ {printf "%.0f", $3/$2 * 100}')
        
        if [[ $used -gt $threshold ]]; then
            echo "High memory usage: ${used}%"
            return 1
        fi
    fi
    
    return 0
}

# Function to check if process is running
check_process() {
    local process_name=${1:-python}
    
    if ! pgrep -f "$process_name" >/dev/null 2>&1; then
        echo "Process $process_name is not running"
        return 1
    fi
    
    return 0
}

# Function to check specific ports
check_ports() {
    local ports=${CHECK_PORTS:-"$API_PORT $HEALTH_PORT"}
    
    for port in $ports; do
        if ! ss -tuln | grep -q ":$port "; then
            echo "Port $port is not listening"
            return 1
        fi
    done
    
    return 0
}

# Function to check database connectivity
check_database() {
    if [[ "${CHECK_DATABASE:-false}" == "true" ]] || [ -n "${DATABASE_URL:-}" ]; then
        local db_host
        local db_port
        
        if [ -n "${DATABASE_URL:-}" ]; then
            db_host=$(echo "$DATABASE_URL" | sed 's/.*@\([^:]*\).*/\1/')
            db_port=$(echo "$DATABASE_URL" | sed 's/.*:\([0-9]*\)\/.*/\1/' | grep -E '^[0-9]+$' || echo "5432")
        else
            db_host=${DATABASE_HOST:-localhost}
            db_port=${DATABASE_PORT:-5432}
        fi
        
        if ! nc -z -w "$TIMEOUT" "$db_host" "$db_port" 2>/dev/null; then
            echo "Database not reachable at $db_host:$db_port"
            return 1
        fi
        
        # Optional: Test actual database connection
        if [[ -n "${DATABASE_URL:-}" ]]; then
            if ! python -c "
import sys
try:
    import psycopg2
    import os
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    conn.close()
    print('Database connection successful')
except Exception as e:
    print(f'Database connection failed: {e}')
    sys.exit(1)
" >/dev/null 2>&1; then
                echo "Database connection test failed"
                return 1
            fi
        fi
    fi
    
    return 0
}

# Function to check Redis connectivity
check_redis() {
    if [[ "${CHECK_REDIS:-false}" == "true" ]] || [ -n "${REDIS_URL:-}" ]; then
        local redis_host
        local redis_port
        
        if [ -n "${REDIS_URL:-}" ]; then
            redis_host=$(echo "$REDIS_URL" | sed 's/.*@\([^:]*\).*/\1/')
            redis_port=$(echo "$REDIS_URL" | sed 's/.*:\([0-9]*\)$/\1/' | grep -E '^[0-9]+$' || echo "6379")
        else
            redis_host=${REDIS_HOST:-localhost}
            redis_port=${REDIS_PORT:-6379}
        fi
        
        if ! nc -z -w "$TIMEOUT" "$redis_host" "$redis_port" 2>/dev/null; then
            echo "Redis not reachable at $redis_host:$redis_port"
            return 1
        fi
        
        # Optional: Test Redis PING
        if command -v redis-cli >/dev/null 2>&1; then
            if ! redis-cli -h "$redis_host" -p "$redis_port" ping | grep -q "PONG"; then
                echo "Redis PING failed"
                return 1
            fi
        fi
    fi
    
    return 0
}

# Main health check function with retry logic
main() {
    local retries=0
    
    # Retry loop
    while [[ $retries -lt $MAX_RETRIES ]]; do
        local all_checks_passed=true
        
        echo "Health check attempt $((retries + 1))/$MAX_RETRIES"
        
        # Check health endpoint
        if ! check_health_endpoint "$HEALTH_ENDPOINT" "$TIMEOUT"; then
            echo "Health endpoint check failed"
            all_checks_passed=false
        fi
        
        # Check Prometheus metrics
        if ! check_metrics; then
            echo "Metrics endpoint check failed"
            all_checks_passed=false
        fi
        
        # Check disk space
        if ! check_disk_space; then
            all_checks_passed=false
        fi
        
        # Check memory
        if ! check_memory; then
            all_checks_passed=false
        fi
        
        # Check process
        if ! check_process "python"; then
            all_checks_passed=false
        fi
        
        # Check ports
        if ! check_ports; then
            all_checks_passed=false
        fi
        
        # Check database
        if ! check_database; then
            all_checks_passed=false
        fi
        
        # Check Redis
        if ! check_redis; then
            all_checks_passed=false
        fi
        
        if [[ "$all_checks_passed" == "true" ]]; then
            echo "All health checks passed"
            exit 0
        fi
        
        retries=$((retries + 1))
        
        if [[ $retries -lt $MAX_RETRIES ]]; then
            echo "Health check failed, retrying in ${RETRY_DELAY}s"
            sleep "$RETRY_DELAY"
        fi
    done
    
    echo "Health check failed after $MAX_RETRIES attempts"
    exit 1
}

# Run main function
main "$@"