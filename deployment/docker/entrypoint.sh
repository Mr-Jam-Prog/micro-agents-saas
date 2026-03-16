#!/bin/bash
set -euo pipefail

# Entrypoint script for MicroAgents Platform
# Provides initialization, configuration, and startup logic

# Source environment variables if .env file exists
if [[ -f "/app/.env" ]]; then
    set -o allexport
    source "/app/.env"
    set +o allexport
fi

# Configuration des variables d'environnement
export PYTHONPATH="/app:$PYTHONPATH"
export PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

# Création des répertoires nécessaires
mkdir -p "$PROMETHEUS_MULTIPROC_DIR" /app/logs /var/log/microagents
chown -R microagents:microagents "$PROMETHEUS_MULTIPROC_DIR" /app/logs /var/log/microagents
chmod 755 /var/log/microagents

# Nettoyage des anciens fichiers Prometheus
find "$PROMETHEUS_MULTIPROC_DIR" -type f -delete 2>/dev/null || true

# Function to wait for dependencies
wait_for_dependency() {
    local host=$1
    local port=$2
    local timeout=${3:-30}
    
    echo "Waiting for $host:$port (timeout: ${timeout}s)..."
    
    local start_time=$(date +%s)
    while ! nc -z "$host" "$port" 2>/dev/null; do
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [[ $elapsed -ge $timeout ]]; then
            echo "Timeout waiting for $host:$port" >&2
            return 1
        fi
        
        sleep 1
    done
    
    echo "$host:$port is available"
}

# Function to initialize database
initialize_database() {
    if [[ "${INIT_DATABASE:-false}" == "true" ]]; then
        echo "Initializing database..."
        python -m microagents.db.init
    fi
}

# Function to run migrations
run_migrations() {
    if [[ "${RUN_MIGRATIONS:-false}" == "true" ]]; then
        echo "Running database migrations..."
        python -m alembic upgrade head
    fi
}

# Function to check disk space
check_disk_space() {
    local threshold=${DISK_SPACE_THRESHOLD:-10} # Percentage
    
    local available=$(df /app --output=pcent | tail -1 | tr -d '% ')
    if [[ $available -lt $threshold ]]; then
        echo "WARNING: Low disk space on /app: ${available}% available" >&2
        return 1
    fi
}

# Function to set up logging
setup_logging() {
    # Rotate logs if they're too large
    if [[ -f /var/log/microagents/app.log ]] && \
       [[ $(stat -c%s /var/log/microagents/app.log 2>/dev/null || echo 0) -gt 104857600 ]]; then # 100MB
        mv /var/log/microagents/app.log "/var/log/microagents/app.log.$(date +%Y%m%d_%H%M%S)"
    fi
}

# Function to validate configuration
validate_config() {
    echo "Validating configuration..."
    
    # Check required environment variables
    local required_vars=("APP_ENV")
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            echo "ERROR: Required environment variable $var is not set" >&2
            exit 1
        fi
    done
    
    # Validate APP_ENV value
    case "${APP_ENV}" in
        development|staging|production)
            # Valid environment
            ;;
        *)
            echo "ERROR: Invalid APP_ENV value: ${APP_ENV}. Must be one of: development, staging, production" >&2
            exit 1
            ;;
    esac
    
    echo "Configuration validated successfully"
}

# Function to set up performance tuning
setup_performance() {
    # Configuration du nombre de workers basé sur les CPUs disponibles
    if [ -z "${UVICORN_WORKERS:-}" ]; then
        CPU_COUNT=$(nproc)
        UVICORN_WORKERS=$(( CPU_COUNT * 2 + 1 ))
        export UVICORN_WORKERS
    fi

    # Configuration de la mémoire basée sur les limites du conteneur
    if [ -z "${UVICORN_MAX_REQUESTS:-}" ]; then
        if [ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
            MEMORY_LIMIT=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
            if [ "$MEMORY_LIMIT" -lt 1073741824 ]; then  # < 1GB
                UVICORN_MAX_REQUESTS=1000
            elif [ "$MEMORY_LIMIT" -lt 2147483648 ]; then  # < 2GB
                UVICORN_MAX_REQUESTS=2000
            else
                UVICORN_MAX_REQUESTS=5000
            fi
            export UVICORN_MAX_REQUESTS
        fi
    fi
    
    # Set JVM-style memory limits if using jemalloc
    if [[ -f /usr/lib/x86_64-linux-gnu/libjemalloc.so.2 ]]; then
        export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2
        export MALLOC_CONF="dirty_decay_ms:1000,narenas:2,background_thread:true"
    fi
    
    # Set Python memory allocator
    export PYTHONMALLOC=jemalloc
    
    # Set thread pool size for async operations
    export UV_THREADPOOL_SIZE=${UV_THREADPOOL_SIZE:-32}
    
    # Set ulimits if running as root
    if [[ $EUID -eq 0 ]]; then
        ulimit -n 65536
        ulimit -u unlimited
    fi
}

# Function to preload cache
preload_cache() {
    if [ "${PRELOAD_CACHE:-false}" = "true" ]; then
        echo "Preloading cache..."
        python -c "
from microagents.core.base.registry import AgentRegistry
from microagents.registry.cache.redis_handler import RedisHandler
import asyncio

async def preload():
    registry = AgentRegistry()
    cache = RedisHandler()
    # Pré-charger les agents fréquemment utilisés
    await cache.warmup()
    
asyncio.run(preload())
"
    fi
}

# Logging de la configuration
log_configuration() {
    echo "=========================================="
    echo "MicroAgents API - ${APP_ENV:-production} Environment"
    echo "=========================================="
    echo "Version: ${APP_VERSION:-1.0.0}"
    echo "Environment: ${APP_ENV:-production}"
    echo "Workers: ${UVICORN_WORKERS:-4}"
    echo "Host: ${UVICORN_HOST:-0.0.0.0}"
    echo "Port: ${UVICORN_PORT:-8000}"
    echo "Log Level: $LOG_LEVEL"
    echo "Python Path: $PYTHONPATH"
    echo "Prometheus Dir: $PROMETHEUS_MULTIPROC_DIR"
    echo "=========================================="
}

# Function to start the application
start_application() {
    local command="${1:-}"
    
    case "${command}" in
        "worker")
            echo "Starting worker..."
            exec python -m microagents.worker
            ;;
        "api")
            echo "Starting API server..."
            # Démarrer l'application avec les paramètres optimisés
            exec uvicorn \
                "src.api.main:app" \
                --host "${UVICORN_HOST:-0.0.0.0}" \
                --port "${UVICORN_PORT:-8000}" \
                --workers "${UVICORN_WORKERS:-4}" \
                --log-level "${LOG_LEVEL:-info}" \
                --access-log \
                --proxy-headers \
                --forwarded-allow-ips "*" \
                --timeout-keep-alive 30 \
                --limit-concurrency 1000 \
                --backlog 1000 \
                --no-server-header \
                --header "server: MicroAgents/1.0" \
                --header "x-powered-by: MicroAgents Platform"
            ;;
        "scheduler")
            echo "Starting scheduler..."
            exec python -m microagents.scheduler
            ;;
        "cli")
            shift
            exec python -m microagents.cli "$@"
            ;;
        "")
            # Default: start all services in production, API in development
            if [[ "${APP_ENV}" == "production" ]]; then
                echo "Starting all services in production mode..."
                exec supervisord -c /etc/supervisor/supervisord.conf
            else
                echo "Starting API server in development mode..."
                exec uvicorn \
                    "src.api.main:app" \
                    --host "${UVICORN_HOST:-0.0.0.0}" \
                    --port "${UVICORN_PORT:-8000}" \
                    --reload \
                    --log-level "${LOG_LEVEL:-info}"
            fi
            ;;
        *)
            # Execute custom command
            exec "$@"
            ;;
    esac
}

# Wait for dependencies if necessary
wait_for_dependencies() {
    if [ "${WAIT_FOR_DEPENDENCIES:-false}" = "true" ]; then
        echo "Waiting for dependencies..."
        
        # Wait for Redis
        if [ -n "${REDIS_URL:-}" ]; then
            redis_host=$(echo "$REDIS_URL" | sed 's/.*@\([^:]*\).*/\1/')
            redis_port=$(echo "$REDIS_URL" | sed 's/.*:\([0-9]*\)$/\1/' | grep -E '^[0-9]+$' || echo "6379")
            wait_for_dependency "$redis_host" "$redis_port" 30
        fi
        
        # Wait for database
        if [ -n "${DATABASE_URL:-}" ]; then
            db_host=$(echo "$DATABASE_URL" | sed 's/.*@\([^:]*\).*/\1/')
            db_port=$(echo "$DATABASE_URL" | sed 's/.*:\([0-9]*\)\/.*/\1/' | grep -E '^[0-9]+$' || echo "5432")
            wait_for_dependency "$db_host" "$db_port" 30
        fi
        
        # Wait for additional hosts if specified
        if [[ -n "${WAIT_FOR_HOSTS:-}" ]]; then
            IFS=',' read -ra hosts <<< "$WAIT_FOR_HOSTS"
            for host_port in "${hosts[@]}"; do
                IFS=':' read -ra parts <<< "$host_port"
                wait_for_dependency "${parts[0]}" "${parts[1]:-5432}" "${WAIT_TIMEOUT:-30}"
            done
        fi
    fi
}

# Main execution
main() {
    # Set strict mode
    set -euo pipefail
    
    # Change to app directory
    cd /app
    
    # Setup
    setup_logging
    setup_performance
    check_disk_space
    validate_config
    
    # Wait for dependencies
    wait_for_dependencies
    
    # Initialize database and run migrations
    initialize_database
    run_migrations
    
    # Preload cache
    preload_cache
    
    # Log configuration
    log_configuration
    
    # Start application
    start_application "$@"
}

# Handle signals
trap 'echo "Received signal, shutting down..."; exit 0' SIGINT SIGTERM

# Run main function
main "$@"