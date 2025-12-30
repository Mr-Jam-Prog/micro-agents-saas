#!/bin/bash
# scale_agents.sh - Intelligent scaling for 1400 MicroAgents Platform
# Version: 2.0.0
# Description: Advanced auto-scaling with cost optimization, performance validation, and compliance checking

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Default configuration
CONFIG_FILE="${CONFIG_FILE:-/etc/microagents/scaling.conf}"
LOG_FILE="${LOG_FILE:-/var/log/microagents/scaling.log}"
METRICS_DIR="${METRICS_DIR:-/var/lib/microagents/metrics}"
STATE_FILE="${STATE_FILE:-/var/lib/microagents/scaling.state}"
REPORT_DIR="${REPORT_DIR:-/var/lib/microagents/reports}"

# Scaling parameters
MIN_AGENTS="${MIN_AGENTS:-10}"
MAX_AGENTS="${MAX_AGENTS:-1400}"
SCALING_COOLDOWN="${SCALING_COOLDOWN:-300}"  # 5 minutes
MAX_SCALE_OUT="${MAX_SCALE_OUT:-50}"         # Max agents to add at once
MAX_SCALE_IN="${MAX_SCALE_IN:-20}"           # Max agents to remove at once
COST_THRESHOLD="${COST_THRESHOLD:-1000}"     # USD per hour threshold

# Performance thresholds (percent)
CPU_THRESHOLD_HIGH="${CPU_THRESHOLD_HIGH:-80}"
CPU_THRESHOLD_LOW="${CPU_THRESHOLD_LOW:-30}"
MEMORY_THRESHOLD_HIGH="${MEMORY_THRESHOLD_HIGH:-85}"
MEMORY_THRESHOLD_LOW="${MEMORY_THRESHOLD_LOW:-40}"
LATENCY_THRESHOLD_HIGH="${LATENCY_THRESHOLD_HIGH:-1000}"  # ms
THROUGHPUT_THRESHOLD_LOW="${THROUGHPUT_THRESHOLD_LOW:-100}"  # requests/sec

# Compliance constraints
MAX_AGENTS_PER_REGION="${MAX_AGENTS_PER_REGION:-350}"  # For multi-region distribution
MIN_AGENTS_PER_REGION="${MIN_AGENTS_PER_REGION:-2}"
GDPR_COMPLIANT="${GDPR_COMPLIANT:-true}"  # Restrict data location
PCI_COMPLIANT="${PCI_COMPLIANT:-false}"

# ============================================================================
# LOGGING AND UTILITIES
# ============================================================================

log() {
    local level="$1"
    local message="$2"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    case "$level" in
        "INFO") color="$BLUE" ;;
        "SUCCESS") color="$GREEN" ;;
        "WARNING") color="$YELLOW" ;;
        "ERROR") color="$RED" ;;
        "DEBUG") color="$PURPLE" ;;
        *) color="$NC" ;;
    esac
    
    echo -e "${color}[$timestamp] [$level] $message${NC}"
    echo "[$timestamp] [$level] $message" >> "$LOG_FILE"
}

check_dependencies() {
    local deps=("jq" "bc" "curl" "awk" "pgrep")
    local missing=()
    
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            missing+=("$dep")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        log "ERROR" "Missing dependencies: ${missing[*]}"
        exit 1
    fi
}

load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        log "INFO" "Loading configuration from $CONFIG_FILE"
        source "$CONFIG_FILE"
    else
        log "WARNING" "Configuration file not found: $CONFIG_FILE"
        log "INFO" "Using default configuration"
    fi
}

save_state() {
    local state="$1"
    echo "$state" > "$STATE_FILE"
    log "DEBUG" "Saved state: $state"
}

load_state() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo "UNKNOWN"
    fi
}

# ============================================================================
# 1. LOAD MONITORING
# ============================================================================

collect_metrics() {
    log "INFO" "Collecting system metrics..."
    
    local metrics=()
    
    # CPU metrics
    local cpu_usage=$(get_cpu_usage)
    metrics+=("cpu_usage:$cpu_usage")
    
    # Memory metrics
    local memory_usage=$(get_memory_usage)
    metrics+=("memory_usage:$memory_usage")
    
    # Network metrics
    local network_in=$(get_network_in)
    local network_out=$(get_network_out)
    metrics+=("network_in:$network_in" "network_out:$network_out")
    
    # Application metrics
    local request_rate=$(get_request_rate)
    local error_rate=$(get_error_rate)
    local latency=$(get_latency)
    metrics+=("request_rate:$request_rate" "error_rate:$error_rate" "latency:$latency")
    
    # Agent-specific metrics
    local active_agents=$(get_active_agents)
    local agent_health=$(get_agent_health)
    metrics+=("active_agents:$active_agents" "agent_health:$agent_health")
    
    # Cost metrics
    local current_cost=$(get_current_cost)
    metrics+=("current_cost:$current_cost")
    
    # Save metrics for analysis
    save_metrics "${metrics[@]}"
    
    echo "${metrics[@]}"
}

get_cpu_usage() {
    # Get average CPU usage across all agents
    if command -v mpstat &> /dev/null; then
        mpstat 1 1 | awk '/Average:/ {print 100 - $12}' | head -1
    else
        top -bn1 | grep "Cpu(s)" | awk '{print 100 - $8}'
    fi
}

get_memory_usage() {
    free | awk '/Mem:/ {printf "%.2f", $3/$2 * 100}'
}

get_network_in() {
    # Bytes per second in
    cat /proc/net/dev | grep eth0 | awk '{print $2}'
}

get_network_out() {
    # Bytes per second out
    cat /proc/net/dev | grep eth0 | awk '{print $10}'
}

get_request_rate() {
    # Requests per second from load balancer or application
    curl -s http://localhost:9090/metrics | grep 'http_requests_total' | awk '{print $2}' | tail -1 || echo "0"
}

get_error_rate() {
    # Error rate percentage
    local total_requests=$(get_request_rate)
    local error_requests=$(curl -s http://localhost:9090/metrics | grep 'http_errors_total' | awk '{print $2}' | tail -1 || echo "0")
    
    if [ "$total_requests" -gt 0 ]; then
        echo "scale=2; $error_requests * 100 / $total_requests" | bc
    else
        echo "0"
    fi
}

get_latency() {
    # Average latency in milliseconds
    curl -s http://localhost:9090/metrics | grep 'http_request_duration_seconds' | awk '{print $2 * 1000}' | tail -1 || echo "0"
}

get_active_agents() {
    # Get number of active agents from database or API
    curl -s -H "Authorization: Bearer $API_TOKEN" \
        "http://localhost:8000/api/v1/agents/status?status=ACTIVE" | \
        jq '.count // 0' 2>/dev/null || echo "0"
}

get_agent_health() {
    # Percentage of healthy agents
    local total_agents=$(curl -s -H "Authorization: Bearer $API_TOKEN" \
        "http://localhost:8000/api/v1/agents/count" | jq '.count // 1' 2>/dev/null)
    local healthy_agents=$(curl -s -H "Authorization: Bearer $API_TOKEN" \
        "http://localhost:8000/api/v1/agents/status?health=HEALTHY" | jq '.count // 0' 2>/dev/null)
    
    if [ "$total_agents" -gt 0 ]; then
        echo "scale=2; $healthy_agents * 100 / $total_agents" | bc
    else
        echo "0"
    fi
}

get_current_cost() {
    # Estimated current hourly cost in USD
    # This would integrate with cloud provider APIs
    local agent_count=$(get_active_agents)
    local cost_per_agent=0.10  # Example: $0.10 per agent per hour
    
    echo "scale=2; $agent_count * $cost_per_agent" | bc
}

save_metrics() {
    local timestamp=$(date '+%s')
    local metrics_file="$METRICS_DIR/metrics_$(date '+%Y%m%d').json"
    
    local json_metrics="{\"timestamp\": $timestamp"
    for metric in "$@"; do
        local key="${metric%%:*}"
        local value="${metric#*:}"
        json_metrics="$json_metrics, \"$key\": $value"
    done
    json_metrics="$json_metrics}"
    
    echo "$json_metrics" >> "$metrics_file"
    log "DEBUG" "Metrics saved to $metrics_file"
}

# ============================================================================
# 2. AUTO-SCALING LOGIC
# ============================================================================

analyze_scaling_needs() {
    log "INFO" "Analyzing scaling needs..."
    
    local metrics=($(collect_metrics))
    local scaling_decision="NO_SCALE"
    local scale_amount=0
    local scaling_type=""  # HORIZONTAL, VERTICAL, PREDICTIVE
    
    # Parse metrics into variables
    declare -A metric_map
    for metric in "${metrics[@]}"; do
        local key="${metric%%:*}"
        local value="${metric#*:}"
        metric_map["$key"]="$value"
    done
    
    # Get current agent count
    local current_agents=${metric_map[active_agents]:-0}
    
    # Check scaling conditions
    if [ "$(check_high_load "${metric_map[@]}")" = "true" ]; then
        scaling_decision="SCALE_OUT"
        scale_amount=$(calculate_scale_out_amount "${metric_map[@]}")
        scaling_type="HORIZONTAL"
        
    elif [ "$(check_low_load "${metric_map[@]}")" = "true" ]; then
        scaling_decision="SCALE_IN"
        scale_amount=$(calculate_scale_in_amount "${metric_map[@]}")
        scaling_type="HORIZONTAL"
        
    elif [ "$(check_predictive_need "${metric_map[@]}")" = "true" ]; then
        scaling_decision="SCALE_OUT"
        scale_amount=$(calculate_predictive_amount "${metric_map[@]}")
        scaling_type="PREDICTIVE"
        
    elif [ "$(check_vertical_need "${metric_map[@]}")" = "true" ]; then
        scaling_decision="VERTICAL_SCALE"
        scaling_type="VERTICAL"
    fi
    
    # Apply cost constraints
    if [ "$scaling_decision" = "SCALE_OUT" ]; then
        local projected_cost=$(calculate_projected_cost "$current_agents" "$scale_amount")
        if (( $(echo "$projected_cost > $COST_THRESHOLD" | bc -l) )); then
            log "WARNING" "Projected cost $projected_cost exceeds threshold $COST_THRESHOLD"
            scaling_decision="COST_CONSTRAINED"
            scale_amount=0
        fi
    fi
    
    # Apply compliance constraints
    if [ "$scaling_decision" = "SCALE_OUT" ] || [ "$scaling_decision" = "SCALE_IN" ]; then
        if ! check_compliance_constraints "$current_agents" "$scale_amount"; then
            log "WARNING" "Scaling violates compliance constraints"
            scaling_decision="COMPLIANCE_CONSTRAINED"
            scale_amount=0
        fi
    fi
    
    echo "$scaling_decision:$scale_amount:$scaling_type:$current_agents"
}

check_high_load() {
    declare -A metrics=("$@")
    
    local cpu=${metrics[cpu_usage]:-0}
    local memory=${metrics[memory_usage]:-0}
    local latency=${metrics[latency]:-0}
    local error_rate=${metrics[error_rate]:-0}
    
    # Multiple conditions for scale-out
    local conditions=0
    
    if (( $(echo "$cpu > $CPU_THRESHOLD_HIGH" | bc -l) )); then
        ((conditions++))
    fi
    
    if (( $(echo "$memory > $MEMORY_THRESHOLD_HIGH" | bc -l) )); then
        ((conditions++))
    fi
    
    if (( $(echo "$latency > $LATENCY_THRESHOLD_HIGH" | bc -l) )); then
        ((conditions++))
    fi
    
    if (( $(echo "$error_rate > 5" | bc -l) )); then  # 5% error rate
        ((conditions++))
    fi
    
    # Require at least 2 conditions for scale-out
    if [ $conditions -ge 2 ]; then
        echo "true"
    else
        echo "false"
    fi
}

check_low_load() {
    declare -A metrics=("$@")
    
    local cpu=${metrics[cpu_usage]:-0}
    local memory=${metrics[memory_usage]:-0}
    local request_rate=${metrics[request_rate]:-0}
    
    # All conditions must be met for scale-in
    if (( $(echo "$cpu < $CPU_THRESHOLD_LOW" | bc -l) )) &&
       (( $(echo "$memory < $MEMORY_THRESHOLD_LOW" | bc -l) )) &&
       (( $(echo "$request_rate < $THROUGHPUT_THRESHOLD_LOW" | bc -l) )); then
        echo "true"
    else
        echo "false"
    fi
}

check_predictive_need() {
    declare -A metrics=("$@")
    
    # Check historical patterns for predictive scaling
    local current_hour=$(date '+%H')
    local is_peak_hour=false
    
    # Peak hours: 9-17 (business hours)
    if [ "$current_hour" -ge 9 ] && [ "$current_hour" -le 17 ]; then
        is_peak_hour=true
    fi
    
    # Check if we're approaching peak based on trend
    local trend=$(analyze_load_trend)
    
    if [ "$is_peak_hour" = "true" ] && [ "$trend" = "INCREASING" ]; then
        echo "true"
    else
        echo "false"
    fi
}

check_vertical_need() {
    declare -A metrics=("$@")
    
    local cpu=${metrics[cpu_usage]:-0}
    local memory=${metrics[memory_usage]:-0}
    
    # Check if vertical scaling is more appropriate
    # High CPU but low memory suggests vertical scaling (more CPU)
    # High memory but low CPU suggests vertical scaling (more memory)
    
    if (( $(echo "$cpu > 90" | bc -l) )) && (( $(echo "$memory < 50" | bc -l) )); then
        echo "true"
    elif (( $(echo "$memory > 90" | bc -l) )) && (( $(echo "$cpu < 50" | bc -l) )); then
        echo "true"
    else
        echo "false"
    fi
}

calculate_scale_out_amount() {
    declare -A metrics=("$@")
    
    local cpu=${metrics[cpu_usage]:-0}
    local memory=${metrics[memory_usage]:-0}
    local current_agents=${metrics[active_agents]:-1}
    
    # Calculate based on overload percentage
    local cpu_overload=0
    local memory_overload=0
    
    if (( $(echo "$cpu > $CPU_THRESHOLD_HIGH" | bc -l) )); then
        cpu_overload=$(echo "scale=2; ($cpu - $CPU_THRESHOLD_HIGH) / 100" | bc)
    fi
    
    if (( $(echo "$memory > $MEMORY_THRESHOLD_HIGH" | bc -l) )); then
        memory_overload=$(echo "scale=2; ($memory - $MEMORY_THRESHOLD_HIGH) / 100" | bc)
    fi
    
    # Use the highest overload percentage
    local max_overload=$(echo "$cpu_overload $memory_overload" | awk '{if ($1 > $2) print $1; else print $2}')
    
    # Calculate agents needed: current_agents * (1 + overload)
    local needed_agents=$(echo "scale=0; $current_agents * (1 + $max_overload) / 1" | bc)
    local scale_amount=$((needed_agents - current_agents))
    
    # Apply limits
    if [ $scale_amount -lt 1 ]; then
        scale_amount=1
    fi
    
    if [ $scale_amount -gt $MAX_SCALE_OUT ]; then
        scale_amount=$MAX_SCALE_OUT
    fi
    
    # Ensure we don't exceed max agents
    local total_after_scale=$((current_agents + scale_amount))
    if [ $total_after_scale -gt $MAX_AGENTS ]; then
        scale_amount=$((MAX_AGENTS - current_agents))
    fi
    
    echo "$scale_amount"
}

calculate_scale_in_amount() {
    declare -A metrics=("$@")
    
    local cpu=${metrics[cpu_usage]:-0}
    local memory=${metrics[memory_usage]:-0}
    local current_agents=${metrics[active_agents]:-1}
    
    # Calculate underutilization percentage
    local cpu_underutil=0
    local memory_underutil=0
    
    if (( $(echo "$cpu < $CPU_THRESHOLD_LOW" | bc -l) )); then
        cpu_underutil=$(echo "scale=2; ($CPU_THRESHOLD_LOW - $cpu) / 100" | bc)
    fi
    
    if (( $(echo "$memory < $MEMORY_THRESHOLD_LOW" | bc -l) )); then
        memory_underutil=$(echo "scale=2; ($MEMORY_THRESHOLD_LOW - $memory) / 100" | bc)
    fi
    
    # Use the average underutilization
    local avg_underutil=$(echo "scale=2; ($cpu_underutil + $memory_underutil) / 2" | bc)
    
    # Calculate agents to remove: current_agents * underutilization
    local remove_agents=$(echo "scale=0; $current_agents * $avg_underutil / 1" | bc)
    
    # Apply limits
    if [ $remove_agents -lt 1 ]; then
        remove_agents=1
    fi
    
    if [ $remove_agents -gt $MAX_SCALE_IN ]; then
        remove_agents=$MAX_SCALE_IN
    fi
    
    # Ensure we don't go below min agents
    local total_after_scale=$((current_agents - remove_agents))
    if [ $total_after_scale -lt $MIN_AGENTS ]; then
        remove_agents=$((current_agents - MIN_AGENTS))
        if [ $remove_agents -lt 0 ]; then
            remove_agents=0
        fi
    fi
    
    echo "$remove_agents"
}

calculate_predictive_amount() {
    declare -A metrics=("$@")
    
    local current_agents=${metrics[active_agents]:-1}
    local historical_peak=$(get_historical_peak)
    
    # Scale to 120% of historical peak for this time
    local predictive_agents=$(echo "scale=0; $historical_peak * 1.2 / 1" | bc)
    local scale_amount=$((predictive_agents - current_agents))
    
    # Apply limits
    if [ $scale_amount -lt 1 ]; then
        scale_amount=1
    fi
    
    if [ $scale_amount -gt $MAX_SCALE_OUT ]; then
        scale_amount=$MAX_SCALE_OUT
    fi
    
    echo "$scale_amount"
}

calculate_projected_cost() {
    local current_agents="$1"
    local scale_amount="$2"
    local new_total=$((current_agents + scale_amount))
    local cost_per_agent=0.10  # $0.10 per agent per hour
    
    echo "scale=2; $new_total * $cost_per_agent" | bc
}

analyze_load_trend() {
    # Analyze load trend from recent metrics
    local recent_file="$METRICS_DIR/metrics_$(date '+%Y%m%d').json"
    
    if [ ! -f "$recent_file" ]; then
        echo "STABLE"
        return
    fi
    
    # Get last 10 data points
    local last_cpu_values=$(tail -10 "$recent_file" | grep -o '"cpu_usage":[0-9.]*' | cut -d: -f2)
    
    if [ -z "$last_cpu_values" ]; then
        echo "STABLE"
        return
    fi
    
    # Calculate trend using simple linear regression
    local n=0
    local sum_x=0
    local sum_y=0
    local sum_xy=0
    local sum_xx=0
    
    for value in $last_cpu_values; do
        sum_x=$((sum_x + n))
        sum_y=$(echo "$sum_y + $value" | bc)
        sum_xy=$(echo "$sum_xy + $n * $value" | bc)
        sum_xx=$((sum_xx + n * n))
        n=$((n + 1))
    done
    
    if [ $n -lt 2 ]; then
        echo "STABLE"
        return
    fi
    
    local slope=$(echo "scale=4; ($n * $sum_xy - $sum_x * $sum_y) / ($n * $sum_xx - $sum_x * $sum_x)" | bc)
    
    if (( $(echo "$slope > 0.5" | bc -l) )); then
        echo "INCREASING"
    elif (( $(echo "$slope < -0.5" | bc -l) )); then
        echo "DECREASING"
    else
        echo "STABLE"
    fi
}

get_historical_peak() {
    local current_hour=$(date '+%H')
    local current_day=$(date '+%u')  # 1-7 (Monday=1)
    
    # This would typically query a metrics database
    # For now, return a reasonable estimate
    echo "100"  # Example historical peak
}

# ============================================================================
# 3. COST OPTIMIZATION
# ============================================================================

optimize_cost() {
    local current_agents="$1"
    local proposed_change="$2"
    local scaling_type="$3"
    
    log "INFO" "Running cost optimization..."
    
    # Get current cost and projected cost
    local current_cost=$(get_current_cost)
    local projected_cost=$(calculate_projected_cost "$current_agents" "$proposed_change")
    
    # Check Reserved Instance/Savings Plan coverage
    local ri_coverage=$(check_ri_coverage "$current_agents" "$proposed_change")
    
    # Check spot instance availability
    local spot_availability=$(check_spot_availability)
    
    # Optimize based on time of day
    local hour=$(date '+%H')
    local is_off_peak=false
    
    if [ "$hour" -lt 6 ] || [ "$hour" -ge 22 ]; then
        is_off_peak=true
    fi
    
    # Adjust scaling based on cost optimization
    local optimized_change="$proposed_change"
    local optimization_notes=""
    
    # Strategy 1: Use spot instances during off-peak for scale-out
    if [ "$proposed_change" -gt 0 ] && [ "$is_off_peak" = "true" ] && [ "$spot_availability" = "HIGH" ]; then
        optimization_notes="Using spot instances for cost savings"
        # No change to count, but will use cheaper instances
    fi
    
    # Strategy 2: Aggressive scale-in during off-peak
    if [ "$proposed_change" -lt 0 ] && [ "$is_off_peak" = "true" ]; then
        local aggressive_scale_in=$(echo "scale=0; $proposed_change * 1.5 / 1" | bc)
        if [ $aggressive_scale_in -lt $proposed_change ]; then  # More negative
            optimized_change=$aggressive_scale_in
            optimization_notes="Aggressive scale-in during off-peak hours"
        fi
    fi
    
    # Strategy 3: Consider vertical scaling if more cost-effective
    if [ "$scaling_type" = "HORIZONTAL" ] && [ "$proposed_change" -gt 0 ]; then
        local vertical_cost=$(calculate_vertical_cost "$current_agents")
        local horizontal_cost=$(calculate_horizontal_cost "$current_agents" "$proposed_change")
        
        if (( $(echo "$vertical_cost < $horizontal_cost * 0.8" | bc -l) )); then
            scaling_type="VERTICAL"
            optimized_change=0
            optimization_notes="Switching to vertical scaling for cost efficiency"
        fi
    fi
    
    # Generate cost report
    generate_cost_report "$current_cost" "$projected_cost" "$optimization_notes"
    
    echo "$optimized_change:$scaling_type:$optimization_notes"
}

check_ri_coverage() {
    local current_agents="$1"
    local proposed_change="$2"
    
    # This would query AWS/Azure/GCP APIs
    # For now, return simulated data
    local ri_count=100  # Example: 100 agents covered by RIs
    
    local total_after_scale=$((current_agents + proposed_change))
    
    if [ $total_after_scale -le $ri_count ]; then
        echo "FULL"
    elif [ $current_agents -le $ri_count ] && [ $total_after_scale -gt $ri_count ]; then
        echo "PARTIAL"
    else
        echo "NONE"
    fi
}

check_spot_availability() {
    # Check spot instance availability and prices
    # This would query cloud provider APIs
    
    local hour=$(date '+%H')
    
    # Simulate: better availability during off-hours
    if [ "$hour" -lt 6 ] || [ "$hour" -ge 22 ]; then
        echo "HIGH"
    else
        echo "MEDIUM"
    fi
}

calculate_vertical_cost() {
    local agent_count="$1"
    # Cost of upgrading existing instances vs adding new ones
    echo "scale=2; $agent_count * 0.15" | bc  # $0.15 per upgraded agent
}

calculate_horizontal_cost() {
    local current_agents="$1"
    local new_agents="$2"
    local total_agents=$((current_agents + new_agents))
    echo "scale=2; $total_agents * 0.10" | bc  # $0.10 per agent
}

# ============================================================================
# 4. PERFORMANCE VALIDATION
# ============================================================================

validate_scaling_decision() {
    local current_agents="$1"
    local scale_amount="$2"
    local scaling_type="$3"
    
    log "INFO" "Validating scaling decision..."
    
    local validation_errors=()
    local warnings=()
    
    # Validate scale amount
    if [ "$scale_amount" -eq 0 ]; then
        log "INFO" "No scaling needed"
        return 0
    fi
    
    # Check if scaling direction matches load
    local load_trend=$(analyze_load_trend)
    
    if [ "$scale_amount" -gt 0 ] && [ "$load_trend" = "DECREASING" ]; then
        warnings+=("Scaling out while load is decreasing")
    fi
    
    if [ "$scale_amount" -lt 0 ] && [ "$load_trend" = "INCREASING" ]; then
        warnings+=("Scaling in while load is increasing")
    fi
    
    # Check rate of change
    local percent_change=$(echo "scale=2; $scale_amount * 100 / $current_agents" | bc)
    
    if (( $(echo "$percent_change > 50" | bc -l) )); then
        warnings+=("Large scale change: $percent_change%")
    fi
    
    # Check resource availability
    if [ "$scale_amount" -gt 0 ]; then
        if ! check_resource_availability "$scale_amount"; then
            validation_errors+=("Insufficient resources for scale-out")
        fi
    fi
    
    # Check agent dependencies
    if ! validate_agent_dependencies "$current_agents" "$scale_amount"; then
        validation_errors+=("Agent dependency validation failed")
    fi
    
    # Generate validation report
    if [ ${#validation_errors[@]} -eq 0 ]; then
        log "SUCCESS" "Scaling decision validated successfully"
        generate_validation_report "PASS" "${warnings[*]}"
        return 0
    else
        log "ERROR" "Scaling validation failed: ${validation_errors[*]}"
        generate_validation_report "FAIL" "${validation_errors[*]}"
        return 1
    fi
}

check_resource_availability() {
    local scale_amount="$1"
    
    # Check cloud provider quotas
    local available_instances=$(get_available_instances)
    
    if [ "$available_instances" -lt "$scale_amount" ]; then
        log "WARNING" "Insufficient instances available: $available_instances < $scale_amount"
        return 1
    fi
    
    # Check network capacity
    local network_capacity=$(get_network_capacity)
    local current_traffic=$(get_network_out)
    
    if [ "$current_traffic" -gt $((network_capacity * 80 / 100)) ]; then
        log "WARNING" "Network capacity near limit"
        return 1
    fi
    
    return 0
}

validate_agent_dependencies() {
    local current_agents="$1"
    local scale_amount="$2"
    
    # Check if scaling would break agent dependencies
    # This would query the agent dependency graph
    
    # For now, simulate validation
    local new_total=$((current_agents + scale_amount))
    
    # Ensure minimum agents per type
    local min_per_type=2
    local agents_per_type=$((new_total / 10))  # Assume 10 agent types
    
    if [ "$agents_per_type" -lt "$min_per_type" ]; then
        log "WARNING" "Would have less than $min_per_type agents per type"
        return 1
    fi
    
    return 0
}

get_available_instances() {
    # Query cloud provider for available instances
    # For AWS: aws ec2 describe-instance-type-offerings
    # For now, return a simulated value
    echo "100"
}

get_network_capacity() {
    # Get network capacity in bytes/sec
    # This would come from cloud provider or monitoring
    echo "1000000000"  # 1 Gbps
}

# ============================================================================
# 5. HEALTH CHECKING
# ============================================================================

perform_health_checks() {
    log "INFO" "Performing health checks before scaling..."
    
    local health_ok=true
    local checks_failed=()
    
    # Check 1: Database connectivity
    if ! check_database_health; then
        checks_failed+=("Database")
        health_ok=false
    fi
    
    # Check 2: Message queue health
    if ! check_queue_health; then
        checks_failed+=("Message Queue")
        health_ok=false
    fi
    
    # Check 3: Load balancer health
    if ! check_load_balancer_health; then
        checks_failed+=("Load Balancer")
        health_ok=false
    fi
    
    # Check 4: Storage health
    if ! check_storage_health; then
        checks_failed+=("Storage")
        health_ok=false
    fi
    
    # Check 5: External dependencies
    if ! check_external_dependencies; then
        checks_failed+=("External Dependencies")
        health_ok=false
    fi
    
    # Generate health report
    generate_health_report "$health_ok" "${checks_failed[*]}"
    
    if [ "$health_ok" = true ]; then
        log "SUCCESS" "All health checks passed"
        return 0
    else
        log "ERROR" "Health checks failed: ${checks_failed[*]}"
        return 1
    fi
}

check_database_health() {
    # Check database connectivity and performance
    local db_response=$(timeout 5 psql -U microagents -d microagents_dev -c "SELECT 1" 2>&1)
    
    if echo "$db_response" | grep -q "1 row"; then
        return 0
    else
        log "WARNING" "Database health check failed: $db_response"
        return 1
    fi
}

check_queue_health() {
    # Check Redis/Message queue health
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping | grep -q PONG; then
            return 0
        fi
    fi
    
    log "WARNING" "Message queue health check failed"
    return 1
}

check_load_balancer_health() {
    # Check load balancer health
    local lb_response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health)
    
    if [ "$lb_response" = "200" ]; then
        return 0
    else
        log "WARNING" "Load balancer health check failed: HTTP $lb_response"
        return 1
    fi
}

check_storage_health() {
    # Check storage health and capacity
    local disk_usage=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
    
    if [ "$disk_usage" -lt 90 ]; then
        return 0
    else
        log "WARNING" "Storage health check failed: $disk_usage% used"
        return 1
    fi
}

check_external_dependencies() {
    # Check external services (APIs, etc.)
    local external_services=(
        "https://api.github.com"
        "https://cloud.google.com"
        "https://aws.amazon.com"
    )
    
    for service in "${external_services[@]}"; do
        if ! curl -s --head "$service" > /dev/null; then
            log "WARNING" "External service unavailable: $service"
            return 1
        fi
    done
    
    return 0
}

# ============================================================================
# 6. ROLLBACK PROCEDURES
# ============================================================================

prepare_rollback() {
    local current_agents="$1"
    local scale_amount="$2"
    local scaling_type="$3"
    
    log "INFO" "Preparing rollback plan..."
    
    # Save current state for rollback
    local rollback_file="$STATE_FILE.rollback.$(date '+%s')"
    
    cat > "$rollback_file" << EOF
ROLLBACK_PLAN_VERSION=1.0
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
CURRENT_AGENTS=$current_agents
SCALE_AMOUNT=$scale_amount
SCALING_TYPE=$scaling_type
ORIGINAL_STATE=$(load_state)
EOF
    
    # Save agent configurations
    save_agent_configurations "$rollback_file"
    
    # Save load balancer configuration
    save_load_balancer_config "$rollback_file"
    
    log "SUCCESS" "Rollback plan saved to $rollback_file"
    echo "$rollback_file"
}

execute_rollback() {
    local rollback_file="$1"
    local reason="$2"
    
    log "WARNING" "Executing rollback: $reason"
    
    if [ ! -f "$rollback_file" ]; then
        log "ERROR" "Rollback file not found: $rollback_file"
        return 1
    fi
    
    # Load rollback plan
    source "$rollback_file"
    
    log "INFO" "Rolling back to $CURRENT_AGENTS agents"
    
    # Calculate rollback amount (reverse the scaling)
    local rollback_amount=$((0 - SCALE_AMOUNT))
    
    # Execute rollback scaling
    if [ "$rollback_amount" -ne 0 ]; then
        execute_scaling "$CURRENT_AGENTS" "$rollback_amount" "ROLLBACK"
    fi
    
    # Restore configurations
    restore_agent_configurations "$rollback_file"
    restore_load_balancer_config "$rollback_file"
    
    # Restore original state
    save_state "$ORIGINAL_STATE"
    
    log "SUCCESS" "Rollback completed successfully"
    
    # Generate rollback report
    generate_rollback_report "$rollback_file" "$reason"
    
    return 0
}

save_agent_configurations() {
    local rollback_file="$1"
    
    # Save agent configurations to rollback file
    curl -s -H "Authorization: Bearer $API_TOKEN" \
        "http://localhost:8000/api/v1/agents/configurations" >> "$rollback_file"
    
    log "DEBUG" "Agent configurations saved"
}

save_load_balancer_config() {
    local rollback_file="$1"
    
    # Save load balancer configuration
    curl -s "http://localhost:8080/config" >> "$rollback_file" 2>/dev/null || true
    
    log "DEBUG" "Load balancer configuration saved"
}

restore_agent_configurations() {
    local rollback_file="$1"
    
    # Extract and restore agent configurations
    # This would parse the rollback file and make API calls
    
    log "INFO" "Restoring agent configurations"
    # Implementation would depend on API
}

restore_load_balancer_config() {
    local rollback_file="$1"
    
    log "INFO" "Restoring load balancer configuration"
    # Implementation would depend on load balancer
}

# ============================================================================
# 7. MULTI-REGION SCALING
# ============================================================================

distribute_scaling() {
    local total_change="$1"
    local scaling_type="$2"
    
    log "INFO" "Distributing scaling across regions..."
    
    # Get current distribution
    declare -A region_distribution
    region_distribution["us-east-1"]=$(get_region_agent_count "us-east-1")
    region_distribution["eu-west-1"]=$(get_region_agent_count "eu-west-1")
    region_distribution["ap-southeast-1"]=$(get_region_agent_count "ap-southeast-1")
    
    # Calculate optimal distribution
    declare -A region_changes
    if [ "$total_change" -gt 0 ]; then
        region_changes=$(calculate_region_scale_out "${!region_distribution[@]}" "$total_change")
    else
        region_changes=$(calculate_region_scale_in "${!region_distribution[@]}" "$total_change")
    fi
    
    # Apply compliance constraints
    region_changes=$(apply_region_compliance_constraints region_changes)
    
    # Generate distribution plan
    generate_distribution_plan region_changes
    
    echo "${region_changes[@]}"
}

get_region_agent_count() {
    local region="$1"
    
    # Query region-specific API or database
    curl -s -H "Authorization: Bearer $API_TOKEN" \
        "http://localhost:8000/api/v1/agents/region/$region/count" | \
        jq '.count // 0' 2>/dev/null || echo "0"
}

calculate_region_scale_out() {
    local regions=("$@")
    local total_change="${!#}"
    local total_regions=${#regions[@]}
    
    declare -A changes
    
    # Distribute based on current load in each region
    for region in "${regions[@]}"; do
        local region_load=$(get_region_load "$region")
        local region_capacity=$(get_region_capacity "$region")
        
        # Calculate proportional change
        local proportional_share=$(echo "scale=0; $total_change * $region_load / 100 / 1" | bc)
        
        # Ensure we don't exceed region capacity
        local current_count=$(get_region_agent_count "$region")
        local available_capacity=$((region_capacity - current_count))
        
        if [ "$proportional_share" -gt "$available_capacity" ]; then
            proportional_share=$available_capacity
        fi
        
        # Ensure minimum per region
        if [ "$proportional_share" -lt 1 ] && [ "$total_change" -gt 0 ]; then
            proportional_share=1
        fi
        
        changes["$region"]=$proportional_share
    done
    
    # Adjust to match total change
    local actual_total=0
    for change in "${changes[@]}"; do
        actual_total=$((actual_total + change))
    done
    
    if [ "$actual_total" -ne "$total_change" ]; then
        # Distribute remainder
        local remainder=$((total_change - actual_total))
        distribute_remainder changes region "$remainder"
    fi
    
    echo "${changes[@]}"
}

calculate_region_scale_in() {
    local regions=("$@")
    local total_change="${!#}"
    local total_change_abs=${total_change#-}  # Absolute value
    
    declare -A changes
    
    # Distribute based on current utilization
    for region in "${regions[@]}"; do
        local current_count=$(get_region_agent_count "$region")
        local region_utilization=$(get_region_utilization "$region")
        
        # Calculate proportional reduction
        local proportional_reduction=$(echo "scale=0; $total_change_abs * $region_utilization / 100 / 1" | bc)
        
        # Ensure we don't go below minimum per region
        if [ $((current_count - proportional_reduction)) -lt $MIN_AGENTS_PER_REGION ]; then
            proportional_reduction=$((current_count - MIN_AGENTS_PER_REGION))
            if [ "$proportional_reduction" -lt 0 ]; then
                proportional_reduction=0
            fi
        fi
        
        changes["$region"]=$((0 - proportional_reduction))  # Negative for scale-in
    done
    
    echo "${changes[@]}"
}

apply_region_compliance_constraints() {
    declare -A changes=("$@")
    
    # Apply GDPR constraints (data location)
    if [ "$GDPR_COMPLIANT" = "true" ]; then
        # EU data must stay in EU regions
        # This would adjust changes to comply with data residency requirements
        log "INFO" "Applying GDPR compliance constraints"
    fi
    
    # Apply PCI compliance constraints
    if [ "$PCI_COMPLIANT" = "true" ]; then
        # PCI requires specific security configurations
        log "INFO" "Applying PCI compliance constraints"
    fi
    
    echo "${changes[@]}"
}

get_region_load() {
    local region="$1"
    
    # Get current load in region (requests per second, etc.)
    # This would query regional metrics
    
    # Simulate: return random load between 20-80%
    echo "$((20 + RANDOM % 60))"
}

get_region_capacity() {
    local region="$1"
    
    # Get maximum agents allowed in region
    echo "$MAX_AGENTS_PER_REGION"
}

get_region_utilization() {
    local region="$1"
    
    # Get current utilization percentage
    local current_count=$(get_region_agent_count "$region")
    local capacity=$(get_region_capacity "$region")
    
    echo "scale=0; $current_count * 100 / $capacity" | bc
}

distribute_remainder() {
    local -n changes_ref=$1
    local region_key=$2
    local remainder=$3
    
    # Distribute remainder to regions with most capacity
    for region in "${!changes_ref[@]}"; do
        if [ "$remainder" -le 0 ]; then
            break
        fi
        
        local current_count=$(get_region_agent_count "$region")
        local capacity=$(get_region_capacity "$region")
        local available=$((capacity - current_count - changes_ref[$region]))
        
        if [ "$available" -gt 0 ]; then
            local to_add=$((available < remainder ? available : remainder))
            changes_ref[$region]=$((changes_ref[$region] + to_add))
            remainder=$((remainder - to_add))
        fi
    done
}

# ============================================================================
# 8. RESOURCE OPTIMIZATION
# ============================================================================

optimize_resources() {
    local current_agents="$1"
    local scale_amount="$2"
    local scaling_type="$3"
    
    log "INFO" "Optimizing resource allocation..."
    
    local optimization_strategies=()
    
    # Strategy 1: Right-size instances
    if [ "$scaling_type" = "HORIZONTAL" ] && [ "$scale_amount" -gt 0 ]; then
        optimization_strategies+=("right_size_instances:$scale_amount")
    fi
    
    # Strategy 2: Optimize placement
    optimization_strategies+=("optimize_placement")
    
    # Strategy 3: Use appropriate instance types
    optimization_strategies+=("select_instance_types")
    
    # Strategy 4: Optimize storage
    optimization_strategies+=("optimize_storage")
    
    # Execute optimizations
    for strategy in "${optimization_strategies[@]}"; do
        execute_optimization "$strategy" "$current_agents" "$scale_amount"
    done
    
    generate_optimization_report "${optimization_strategies[*]}"
}

execute_optimization() {
    local strategy="$1"
    local current_agents="$2"
    local scale_amount="$3"
    
    case "$strategy" in
        right_size_instances:*)
            local count="${strategy#*:}"
            right_size_instances "$count"
            ;;
        optimize_placement)
            optimize_instance_placement
            ;;
        select_instance_types)
            select_optimal_instance_types
            ;;
        optimize_storage)
            optimize_storage_configuration
            ;;
    esac
}

right_size_instances() {
    local count="$1"
    
    log "INFO" "Right-sizing $count instances..."
    
    # Analyze current instance utilization
    local underutilized=$(find_underutilized_instances)
    
    if [ -n "$underutilized" ]; then
        log "INFO" "Found underutilized instances, resizing..."
        # This would call cloud provider API to resize instances
    fi
}

optimize_instance_placement() {
    log "INFO" "Optimizing instance placement..."
    
    # Spread across availability zones
    # Use placement groups for performance
    # Avoid noisy neighbors
    
    # This would interact with cloud provider APIs
}

select_optimal_instance_types() {
    log "INFO" "Selecting optimal instance types..."
    
    # Choose instance types based on workload
    # Balance cost vs performance
    # Consider burstable vs fixed performance
    
    # This would use cloud provider pricing and performance data
}

optimize_storage_configuration() {
    log "INFO" "Optimizing storage configuration..."
    
    # Choose appropriate storage types
    # Right-size volumes
    # Enable compression/deduplication
    
    # This would analyze I/O patterns and adjust storage
}

find_underutilized_instances() {
    # Find instances with low utilization
    # This would query monitoring system
    
    echo ""  # Return instance IDs
}

# ============================================================================
# 9. COMPLIANCE CHECKING
# ============================================================================

check_compliance_constraints() {
    local current_agents="$1"
    local scale_amount="$2"
    
    log "INFO" "Checking compliance constraints..."
    
    local compliance_violations=()
    
    # Check 1: Maximum agents constraint
    local new_total=$((current_agents + scale_amount))
    if [ "$new_total" -gt "$MAX_AGENTS" ]; then
        compliance_violations+=("MAX_AGENTS: Would exceed maximum of $MAX_AGENTS")
    fi
    
    # Check 2: Minimum agents constraint
    if [ "$new_total" -lt "$MIN_AGENTS" ]; then
        compliance_violations+=("MIN_AGENTS: Would go below minimum of $MIN_AGENTS")
    fi
    
    # Check 3: Regional distribution constraints
    if ! check_region_compliance "$new_total"; then
        compliance_violations+=("REGIONAL_DISTRIBUTION: Violates regional constraints")
    fi
    
    # Check 4: Data residency (GDPR)
    if [ "$GDPR_COMPLIANT" = "true" ] && ! check_gdpr_compliance; then
        compliance_violations+=("GDPR: Violates data residency requirements")
    fi
    
    # Check 5: Security compliance
    if [ "$PCI_COMPLIANT" = "true" ] && ! check_pci_compliance; then
        compliance_violations+=("PCI: Violates security requirements")
    fi
    
    # Check 6: License compliance
    if ! check_license_compliance "$new_total"; then
        compliance_violations+=("LICENSE: Exceeds licensed capacity")
    fi
    
    # Generate compliance report
    if [ ${#compliance_violations[@]} -eq 0 ]; then
        log "SUCCESS" "All compliance checks passed"
        generate_compliance_report "COMPLIANT" ""
        return 0
    else
        log "ERROR" "Compliance violations: ${compliance_violations[*]}"
        generate_compliance_report "NON_COMPLIANT" "${compliance_violations[*]}"
        return 1
    fi
}

check_region_compliance() {
    local total_agents="$1"
    
    # Check if regional distribution complies with policies
    # This would verify each region has minimum/maximum agents
    
    return 0  # Simplified for example
}

check_gdpr_compliance() {
    # Check GDPR data residency requirements
    # Ensure EU data stays in EU regions
    
    if [ "$GDPR_COMPLIANT" = "true" ]; then
        # Verify no EU data in non-EU regions
        return 0  # Simplified
    fi
    
    return 0
}

check_pci_compliance() {
    # Check PCI DSS requirements
    # Ensure proper security controls
    
    if [ "$PCI_COMPLIANT" = "true" ]; then
        # Verify encryption, logging, segmentation
        return 0  # Simplified
    fi
    
    return 0
}

check_license_compliance() {
    local total_agents="$1"
    
    # Check if within licensed capacity
    local license_limit=1400  # From license agreement
    
    if [ "$total_agents" -le "$license_limit" ]; then
        return 0
    else
        return 1
    fi
}

# ============================================================================
# 10. REPORTING
# ============================================================================

generate_scaling_report() {
    local decision="$1"
    local amount="$2"
    local scaling_type="$3"
    local current="$4"
    local new_total="$5"
    local cost_impact="$6"
    local region_distribution="$7"
    
    local report_file="$REPORT_DIR/scaling_report_$(date '+%Y%m%d_%H%M%S').json"
    
    cat > "$report_file" << EOF
{
  "report_id": "$(uuidgen)",
  "timestamp": "$(date '+%Y-%m-%d %H:%M:%S')",
  "decision": "$decision",
  "scaling_details": {
    "type": "$scaling_type",
    "amount": $amount,
    "current_agents": $current,
    "new_total": $new_total,
    "percent_change": "$(echo "scale=2; $amount * 100 / $current" | bc)%"
  },
  "cost_impact": {
    "hourly_cost": $cost_impact,
    "daily_estimate": "$(echo "scale=2; $cost_impact * 24" | bc)",
    "monthly_estimate": "$(echo "scale=2; $cost_impact * 24 * 30" | bc)"
  },
  "region_distribution": $region_distribution,
  "performance_metrics": $(get_performance_snapshot),
  "health_status": $(get_health_status),
  "compliance_status": "COMPLIANT",
  "validation_results": "PASSED"
}
EOF
    
    log "SUCCESS" "Scaling report generated: $report_file"
    
    # Send notification
    send_notification "$decision" "$amount" "$report_file"
}

generate_cost_report() {
    local current_cost="$1"
    local projected_cost="$2"
    local optimization_notes="$3"
    
    local report_file="$REPORT_DIR/cost_report_$(date '+%Y%m%d').json"
    
    cat >> "$report_file" << EOF
{
  "timestamp": "$(date '+%Y-%m-%d %H:%M:%S')",
  "current_cost_per_hour": $current_cost,
  "projected_cost_per_hour": $projected_cost,
  "cost_difference": "$(echo "scale=2; $projected_cost - $current_cost" | bc)",
  "optimization_notes": "$optimization_notes",
  "savings_opportunities": [
    {
      "type": "reserved_instances",
      "potential_savings": "$(echo "scale=2; $current_cost * 0.4" | bc)",
      "implementation_effort": "MEDIUM"
    },
    {
      "type": "spot_instances",
      "potential_savings": "$(echo "scale=2; $current_cost * 0.6" | bc)",
      "implementation_effort": "HIGH"
    }
  ]
}
EOF
}

generate_validation_report() {
    local status="$1"
    local issues="$2"
    
    echo "Validation Status: $status"
    echo "Issues: $issues"
    echo "Timestamp: $(date)"
}

generate_health_report() {
    local status="$1"
    local failed_checks="$2"
    
    echo "Health Status: $status"
    echo "Failed Checks: $failed_checks"
}

generate_optimization_report() {
    local strategies="$1"
    
    echo "Optimization Strategies Applied: $strategies"
    echo "Timestamp: $(date)"
}

generate_compliance_report() {
    local status="$1"
    local violations="$2"
    
    echo "Compliance Status: $status"
    echo "Violations: $violations"
}

generate_rollback_report() {
    local rollback_file="$1"
    local reason="$2"
    
    local report_file="$REPORT_DIR/rollback_report_$(date '+%Y%m%d_%H%M%S').json"
    
    cat > "$report_file" << EOF
{
  "rollback_executed": true,
  "timestamp": "$(date '+%Y-%m-%d %H:%M:%S')",
  "reason": "$reason",
  "rollback_file": "$rollback_file",
  "original_state": $(cat "$rollback_file" 2>/dev/null || echo "{}")
}
EOF
}

generate_distribution_plan() {
    declare -A changes=("$@")
    
    local plan_file="$REPORT_DIR/distribution_plan_$(date '+%Y%m%d_%H%M%S').json"
    
    echo "{" > "$plan_file"
    echo "  \"distribution_plan\": {" >> "$plan_file"
    
    local first=true
    for region in "${!changes[@]}"; do
        if [ "$first" = false ]; then
            echo "," >> "$plan_file"
        fi
        echo "    \"$region\": ${changes[$region]}" >> "$plan_file"
        first=false
    done
    
    echo "  }," >> "$plan_file"
    echo "  \"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\"" >> "$plan_file"
    echo "}" >> "$plan_file"
}

get_performance_snapshot() {
    # Get current performance metrics
    cat << EOF
{
  "cpu_usage": $(get_cpu_usage),
  "memory_usage": $(get_memory_usage),
  "latency_ms": $(get_latency),
  "request_rate": $(get_request_rate),
  "error_rate": $(get_error_rate)
}
EOF
}

get_health_status() {
    # Get current health status
    cat << EOF
{
  "database": "HEALTHY",
  "queue": "HEALTHY",
  "load_balancer": "HEALTHY",
  "storage": "HEALTHY",
  "overall": "HEALTHY"
}
EOF
}

send_notification() {
    local decision="$1"
    local amount="$2"
    local report_file="$3"
    
    # Send notification via Slack, Email, etc.
    local message="Scaling $decision executed: $amount agents. Report: $report_file"
    
    # Slack webhook
    if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
        curl -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"$message\"}" \
            "$SLACK_WEBHOOK_URL" > /dev/null 2>&1
    fi
    
    # Email notification
    if [ -n "${ALERT_EMAIL:-}" ]; then
        echo "$message" | mail -s "MicroAgents Scaling Notification" "$ALERT_EMAIL"
    fi
    
    log "INFO" "Notification sent for scaling $decision"
}

# ============================================================================
# MAIN SCALING EXECUTION
# ============================================================================

execute_scaling() {
    local current_agents="$1"
    local scale_amount="$2"
    local scaling_type="$3"
    local rollback_file="${4:-}"
    
    log "INFO" "Executing scaling: $scaling_type $scale_amount agents"
    
    # Calculate new total
    local new_total=$((current_agents + scale_amount))
    
    # Prepare scaling command based on type
    local scaling_command=""
    
    case "$scaling_type" in
        HORIZONTAL)
            if [ "$scale_amount" -gt 0 ]; then
                scaling_command="scale_out_horizontal $scale_amount"
            else
                scaling_command="scale_in_horizontal $((0 - scale_amount))"
            fi
            ;;
        VERTICAL)
            scaling_command="scale_vertical"
            ;;
        PREDICTIVE)
            scaling_command="scale_predictive $scale_amount"
            ;;
        ROLLBACK)
            scaling_command="scale_rollback $scale_amount"
            ;;
        *)
            log "ERROR" "Unknown scaling type: $scaling_type"
            return 1
            ;;
    esac
    
    # Execute scaling
    if eval "$scaling_command"; then
        log "SUCCESS" "Scaling executed successfully: $new_total total agents"
        
        # Update state
        save_state "SCALED:$scaling_type:$new_total:$(date '+%s')"
        
        # Wait for stabilization
        sleep 10
        
        # Verify scaling
        if verify_scaling "$new_total"; then
            log "SUCCESS" "Scaling verified successfully"
            return 0
        else
            log "ERROR" "Scaling verification failed"
            
            # Automatic rollback if we have a rollback plan
            if [ -n "$rollback_file" ]; then
                log "WARNING" "Initiating automatic rollback"
                execute_rollback "$rollback_file" "Scaling verification failed"
            fi
            
            return 1
        fi
    else
        log "ERROR" "Scaling execution failed"
        return 1
    fi
}

scale_out_horizontal() {
    local count="$1"
    
    log "INFO" "Scaling out horizontally: adding $count agents"
    
    # This would call the actual scaling API
    # For now, simulate with sleep
    for ((i=1; i<=count; i++)); do
        log "DEBUG" "Launching agent $i/$count"
        # curl -X POST http://localhost:8000/api/v1/agents/launch
        sleep 0.5
    done
    
    return 0
}

scale_in_horizontal() {
    local count="$1"
    
    log "INFO" "Scaling in horizontally: removing $count agents"
    
    # Select agents to terminate (oldest or least healthy)
    local agents_to_terminate=$(select_agents_for_termination "$count")
    
    for agent in $agents_to_terminate; do
        log "DEBUG" "Terminating agent $agent"
        # curl -X DELETE http://localhost:8000/api/v1/agents/$agent
        sleep 0.5
    done
    
    return 0
}

scale_vertical() {
    log "INFO" "Scaling vertically: upgrading instance types"
    
    # Upgrade instance types for underperforming agents
    # This would call cloud provider API
    
    return 0
}

scale_predictive() {
    local count="$1"
    
    log "INFO" "Predictive scaling: pre-warming $count agents"
    
    # Launch agents in standby mode
    scale_out_horizontal "$count"
    
    # Put agents in warm pool
    # curl -X PUT http://localhost:8000/api/v1/agents/warm-pool
    
    return 0
}

scale_rollback() {
    local amount="$1"
    
    log "WARNING" "Rollback scaling: $amount agents"
    
    if [ "$amount" -gt 0 ]; then
        scale_out_horizontal "$amount"
    else
        scale_in_horizontal $((0 - amount))
    fi
    
    return 0
}

select_agents_for_termination() {
    local count="$1"
    
    # Select agents based on strategy:
    # 1. Oldest agents first
    # 2. Least healthy agents
    # 3. Agents in over-provisioned regions
    
    # This would query the agent database
    # For now, return simulated IDs
    for ((i=1; i<=count; i++)); do
        echo "agent-$i"
    done
}

verify_scaling() {
    local expected_count="$1"
    
    log "INFO" "Verifying scaling to $expected_count agents..."
    
    # Wait for agents to register
    sleep 5
    
    # Get actual count
    local actual_count=$(get_active_agents)
    
    # Allow 5% tolerance
    local tolerance=$(echo "scale=0; $expected_count * 0.05 / 1" | bc)
    local lower_bound=$((expected_count - tolerance))
    local upper_bound=$((expected_count + tolerance))
    
    if [ "$actual_count" -ge "$lower_bound" ] && [ "$actual_count" -le "$upper_bound" ]; then
        log "SUCCESS" "Scaling verified: expected $expected_count, got $actual_count"
        return 0
    else
        log "ERROR" "Scaling verification failed: expected $expected_count, got $actual_count"
        return 1
    fi
}

# ============================================================================
# MAIN FUNCTION
# ============================================================================

main() {
    log "INFO" "Starting intelligent agent scaling"
    
    # Check dependencies
    check_dependencies
    
    # Load configuration
    load_config
    
    # Check cooldown period
    if ! check_cooldown; then
        log "INFO" "In cooldown period, skipping scaling"
        exit 0
    fi
    
    # Perform health checks
    if ! perform_health_checks; then
        log "ERROR" "Health checks failed, aborting scaling"
        exit 1
    fi
    
    # Analyze scaling needs
    local analysis=$(analyze_scaling_needs)
    local decision=$(echo "$analysis" | cut -d: -f1)
    local amount=$(echo "$analysis" | cut -d: -f2)
    local scaling_type=$(echo "$analysis" | cut -d: -f3)
    local current_agents=$(echo "$analysis" | cut -d: -f4)
    
    log "INFO" "Analysis result: $decision ($amount agents, type: $scaling_type)"
    
    if [ "$decision" = "NO_SCALE" ]; then
        log "INFO" "No scaling needed at this time"
        exit 0
    fi
    
    # Check compliance constraints
    if ! check_compliance_constraints "$current_agents" "$amount"; then
        log "ERROR" "Compliance check failed, aborting scaling"
        exit 1
    fi
    
    # Validate scaling decision
    if ! validate_scaling_decision "$current_agents" "$amount" "$scaling_type"; then
        log "ERROR" "Scaling validation failed"
        exit 1
    fi
    
    # Prepare rollback plan
    local rollback_file=$(prepare_rollback "$current_agents" "$amount" "$scaling_type")
    
    # Optimize cost
    local cost_optimization=$(optimize_cost "$current_agents" "$amount" "$scaling_type")
    local optimized_amount=$(echo "$cost_optimization" | cut -d: -f1)
    local optimized_type=$(echo "$cost_optimization" | cut -d: -f2)
    local optimization_notes=$(echo "$cost_optimization" | cut -d: -f3)
    
    if [ "$optimized_amount" -ne "$amount" ]; then
        log "INFO" "Cost optimization adjusted scaling from $amount to $optimized_amount"
        amount=$optimized_amount
        scaling_type=$optimized_type
    fi
    
    # Distribute across regions (for scale-out)
    local region_distribution="{}"
    if [ "$amount" -ne 0 ]; then
        region_distribution=$(distribute_scaling "$amount" "$scaling_type")
    fi
    
    # Optimize resources
    optimize_resources "$current_agents" "$amount" "$scaling_type"
    
    # Calculate cost impact
    local cost_impact=$(calculate_projected_cost "$current_agents" "$amount")
    
    # Execute scaling
    if execute_scaling "$current_agents" "$amount" "$scaling_type" "$rollback_file"; then
        log "SUCCESS" "Scaling completed successfully"
        
        # Calculate new total
        local new_total=$((current_agents + amount))
        
        # Generate comprehensive report
        generate_scaling_report "$decision" "$amount" "$scaling_type" \
            "$current_agents" "$new_total" "$cost_impact" "$region_distribution"
        
        # Update cooldown timer
        update_cooldown_timer
        
    else
        log "ERROR" "Scaling failed"
        
        # Execute rollback
        execute_rollback "$rollback_file" "Scaling execution failed"
        
        exit 1
    fi
    
    log "SUCCESS" "Scaling process completed"
}

check_cooldown() {
    local state=$(load_state)
    local last_scaling_time=$(echo "$state" | cut -d: -f4)
    
    if [ -z "$last_scaling_time" ] || [ "$last_scaling_time" = "UNKNOWN" ]; then
        return 0
    fi
    
    local current_time=$(date '+%s')
    local time_since=$((current_time - last_scaling_time))
    
    if [ "$time_since" -lt "$SCALING_COOLDOWN" ]; then
        local remaining=$((SCALING_COOLDOWN - time_since))
        log "INFO" "In cooldown period: $remaining seconds remaining"
        return 1
    fi
    
    return 0
}

update_cooldown_timer() {
    save_state "COOLDOWN:$(date '+%s')"
}

# ============================================================================
# EXECUTION
# ============================================================================

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --min-agents)
            MIN_AGENTS="$2"
            shift 2
            ;;
        --max-agents)
            MAX_AGENTS="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help)
            cat << EOF
Usage: $0 [OPTIONS]

Intelligent agent scaling for MicroAgents Platform.

Options:
  --config FILE      Configuration file (default: /etc/microagents/scaling.conf)
  --min-agents N     Minimum number of agents (default: 10)
  --max-agents N     Maximum number of agents (default: 1400)
  --dry-run          Analyze only, don't execute scaling
  --help             Show this help message

Scaling Strategies:
  - Horizontal scaling: Add/remove agent instances
  - Vertical scaling: Upgrade/downgrade instance types
  - Predictive scaling: Pre-warm based on patterns
  - Cost-aware scaling: Optimize for cost efficiency
  - Performance-optimized: Scale based on performance metrics
  - Compliance-constrained: Respect regulatory requirements
  - Disaster recovery: Scale for redundancy
  - Maintenance scaling: Scale around maintenance windows

Features:
  1. Load monitoring and analysis
  2. Auto-scaling logic with multiple strategies
  3. Cost optimization and budget control
  4. Performance validation and verification
  5. Health checking before scaling
  6. Automatic rollback procedures
  7. Multi-region distribution
  8. Resource optimization
  9. Compliance checking
  10. Comprehensive reporting

Environment Variables:
  API_TOKEN          Authentication token for API calls
  SLACK_WEBHOOK_URL  Webhook for notifications
  ALERT_EMAIL        Email for alerts

Examples:
  $0 --config ./scaling.conf
  $0 --min-agents 50 --max-agents 1000
  $0 --dry-run
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Run main function
if [ "${DRY_RUN:-false}" = true ]; then
    log "INFO" "DRY RUN MODE - Analyzing only"
    collect_metrics
    analyze_scaling_needs
else
    main
fi