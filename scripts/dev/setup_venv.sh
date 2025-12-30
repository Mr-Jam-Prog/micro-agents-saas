```bash
#!/bin/bash
# setup_venv.sh - Complete development environment setup for MicroAgents Platform
# Version: 2.0.0
# Supports: Linux, macOS, WSL, Docker

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

# Platform detection
PLATFORM="unknown"
case "$(uname -s)" in
    Linux*)     PLATFORM="linux";;
    Darwin*)    PLATFORM="macos";;
    CYGWIN*|MINGW32*|MSYS*|MINGW*) PLATFORM="windows";;
    *)          PLATFORM="unknown";;
esac

# Check if running in WSL
if [[ "$PLATFORM" == "linux" ]] && grep -qEi "(Microsoft|WSL)" /proc/version &> /dev/null; then
    PLATFORM="wsl"
fi

# Directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
CONFIG_DIR="$PROJECT_ROOT/config"
DATA_DIR="$PROJECT_ROOT/data"
LOGS_DIR="$PROJECT_ROOT/logs"
DOCKER_DIR="$PROJECT_ROOT/docker"

# Python version
PYTHON_VERSION="3.12"
MIN_PYTHON_VERSION="3.12.0"

# Services configuration
POSTGRES_PORT=5432
REDIS_PORT=6379
API_PORT=8000
DASHBOARD_PORT=3000
DOCS_PORT=8001

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

print_header() {
    echo -e "\n${BLUE}================================================================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}================================================================================${NC}"
}

print_step() {
    echo -e "\n${GREEN}▶ $1${NC}"
}

print_info() {
    echo -e "${CYAN}ℹ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        print_error "Command '$1' not found. Please install it first."
        exit 1
    fi
}

check_python_version() {
    local current_version
    current_version=$(python3 --version | cut -d ' ' -f 2)
    
    # Compare versions
    if [ "$(printf '%s\n' "$MIN_PYTHON_VERSION" "$current_version" | sort -V | head -n1)" != "$MIN_PYTHON_VERSION" ]; then
        print_error "Python $MIN_PYTHON_VERSION or higher is required. Found $current_version"
        exit 1
    fi
    
    print_success "Python version: $current_version"
}

create_directory() {
    if [ ! -d "$1" ]; then
        mkdir -p "$1"
        print_success "Created directory: $1"
    fi
}

# ============================================================================
# 1. VIRTUAL ENVIRONMENT CREATION
# ============================================================================

setup_venv() {
    print_header "1. SETTING UP VIRTUAL ENVIRONMENT"
    
    # Check if venv already exists
    if [ -d "$VENV_DIR" ]; then
        print_warning "Virtual environment already exists at $VENV_DIR"
        read -p "Do you want to recreate it? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_info "Removing existing virtual environment..."
            rm -rf "$VENV_DIR"
        else
            print_info "Using existing virtual environment."
            return 0
        fi
    fi
    
    # Create virtual environment
    print_step "Creating virtual environment with Python $PYTHON_VERSION..."
    
    if [ "$PLATFORM" == "macos" ]; then
        # macOS specific
        python3 -m venv "$VENV_DIR"
    elif [ "$PLATFORM" == "windows" ]; then
        # Windows specific
        python -m venv "$VENV_DIR"
    else
        # Linux/WSL
        python3 -m venv "$VENV_DIR"
    fi
    
    # Activate virtual environment
    if [ "$PLATFORM" == "windows" ]; then
        source "$VENV_DIR/Scripts/activate"
    else
        source "$VENV_DIR/bin/activate"
    fi
    
    # Upgrade pip
    print_step "Upgrading pip..."
    pip install --upgrade pip
    
    print_success "Virtual environment created and activated at $VENV_DIR"
}

# ============================================================================
# 2. DEPENDENCY INSTALLATION
# ============================================================================

install_dependencies() {
    print_header "2. INSTALLING DEPENDENCIES"
    
    # Check if in virtual environment
    if [ -z "${VIRTUAL_ENV:-}" ]; then
        print_error "Not in a virtual environment. Please activate it first."
        exit 1
    fi
    
    # Install build tools first
    print_step "Installing build tools..."
    pip install hatch build twine
    
    # Install project in development mode
    print_step "Installing project in development mode..."
    pip install -e ".[dev,test,api,cli,aws,azure,gcp,kubernetes,observability,ml]"
    
    # Platform-specific dependencies
    if [ "$PLATFORM" == "macos" ]; then
        print_step "Installing macOS specific dependencies..."
        brew install postgresql redis || true
    elif [ "$PLATFORM" == "linux" ] || [ "$PLATFORM" == "wsl" ]; then
        print_step "Installing Linux specific dependencies..."
        if command -v apt-get &> /dev/null; then
            sudo apt-get update && sudo apt-get install -y \
                postgresql-client redis-tools \
                libpq-dev python3-dev build-essential
        elif command -v yum &> /dev/null; then
            sudo yum install -y \
                postgresql redis \
                postgresql-devel python3-devel gcc
        elif command -v pacman &> /dev/null; then
            sudo pacman -S --noconfirm \
                postgresql redis \
                postgresql-libs python-pip
        fi
    fi
    
    # Verify installation
    print_step "Verifying installation..."
    python -c "import microagents; print(f'Successfully imported microagents version: {microagents.__version__}')"
    
    print_success "All dependencies installed successfully"
}

# ============================================================================
# 3. PRE-COMMIT HOOKS SETUP
# ============================================================================

setup_precommit() {
    print_header "3. SETTING UP PRE-COMMIT HOOKS"
    
    # Install pre-commit
    print_step "Installing pre-commit..."
    pip install pre-commit
    
    # Install hooks
    print_step "Installing git hooks..."
    pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg
    
    # Run pre-commit on all files
    print_step "Running pre-commit on all files..."
    pre-commit run --all-files || {
        print_warning "Some pre-commit checks failed. This is normal for initial setup."
        print_info "You can run 'pre-commit run --all-files' later to fix issues."
    }
    
    # Setup commitlint if not exists
    if [ ! -f "$PROJECT_ROOT/.commitlintrc.json" ]; then
        print_step "Creating commitlint configuration..."
        cat > "$PROJECT_ROOT/.commitlintrc.json" << 'EOF'
{
  "extends": ["@commitlint/config-conventional"],
  "rules": {
    "type-enum": [
      2,
      "always",
      [
        "feat",
        "fix",
        "docs",
        "style",
        "refactor",
        "perf",
        "test",
        "build",
        "ci",
        "chore",
        "revert",
        "security",
        "hotfix"
      ]
    ],
    "scope-enum": [
      2,
      "always",
      [
        "core",
        "api",
        "cli",
        "agents",
        "monitoring",
        "security",
        "cost",
        "aws",
        "azure",
        "gcp",
        "kubernetes",
        "docs",
        "ci",
        "deps",
        "config",
        "tests",
        "benchmarks"
      ]
    ],
    "subject-case": [2, "never", ["sentence-case", "start-case", "pascal-case", "upper-case"]],
    "header-max-length": [2, "always", 100],
    "body-leading-blank": [2, "always"],
    "body-max-line-length": [2, "always", 200],
    "footer-leading-blank": [2, "always"]
  }
}
EOF
        print_success "Created .commitlintrc.json"
    fi
    
    print_success "Pre-commit hooks configured"
}

# ============================================================================
# 4. DATABASE INITIALIZATION
# ============================================================================

setup_database() {
    print_header "4. SETTING UP DATABASE"
    
    # Check if Docker is available
    if command -v docker &> /dev/null && [ "${USE_DOCKER:-true}" = "true" ]; then
        setup_database_docker
    else
        setup_database_local
    fi
}

setup_database_docker() {
    print_step "Setting up database using Docker..."
    
    # Create docker-compose file if not exists
    if [ ! -f "$DOCKER_DIR/docker-compose.dev.yml" ]; then
        create_directory "$DOCKER_DIR"
        cat > "$DOCKER_DIR/docker-compose.dev.yml" << 'EOF'
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: microagents-postgres-dev
    environment:
      POSTGRES_DB: microagents_dev
      POSTGRES_USER: microagents
      POSTGRES_PASSWORD: dev_password_123
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U microagents"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: microagents-redis-dev
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  localstack:
    image: localstack/localstack:latest
    container_name: microagents-localstack-dev
    ports:
      - "4566:4566"
    environment:
      SERVICES: s3,sqs,sns,dynamodb,cloudwatch
      DEBUG: 1
      DATA_DIR: /tmp/localstack/data
    volumes:
      - ./localstack:/docker-entrypoint-initdb.d

volumes:
  postgres_data:
  redis_data:
EOF
        print_success "Created docker-compose.dev.yml"
    fi
    
    # Start services
    print_step "Starting database services..."
    cd "$DOCKER_DIR" && docker-compose -f docker-compose.dev.yml up -d
    
    # Wait for services to be ready
    print_step "Waiting for services to be ready..."
    
    # Wait for PostgreSQL
    for i in {1..30}; do
        if docker-compose -f docker-compose.dev.yml exec postgres pg_isready -U microagents; then
            print_success "PostgreSQL is ready"
            break
        fi
        if [ $i -eq 30 ]; then
            print_error "PostgreSQL failed to start"
            exit 1
        fi
        sleep 2
    done
    
    # Wait for Redis
    for i in {1..30}; do
        if docker-compose -f docker-compose.dev.yml exec redis redis-cli ping | grep -q PONG; then
            print_success "Redis is ready"
            break
        fi
        if [ $i -eq 30 ]; then
            print_error "Redis failed to start"
            exit 1
        fi
        sleep 2
    done
    
    # Run migrations
    print_step "Running database migrations..."
    cd "$PROJECT_ROOT" && hatch run migrate upgrade head
    
    print_success "Database services started and migrated"
}

setup_database_local() {
    print_step "Setting up database locally..."
    
    # Check for PostgreSQL
    if ! command -v psql &> /dev/null; then
        print_warning "PostgreSQL not found. Skipping database setup."
        print_info "Install PostgreSQL and run: hatch run migrate upgrade head"
        return 0
    fi
    
    # Create database if not exists
    print_step "Creating database..."
    if ! psql -lqt | cut -d \| -f 1 | grep -qw microagents_dev; then
        createdb microagents_dev || {
            print_warning "Failed to create database. You may need to run:"
            print_info "  sudo -u postgres createdb microagents_dev"
        }
    fi
    
    # Run migrations
    print_step "Running migrations..."
    cd "$PROJECT_ROOT" && hatch run migrate upgrade head
    
    print_success "Local database setup complete"
}

# ============================================================================
# 5. SAMPLE DATA LOADING
# ============================================================================

load_sample_data() {
    print_header "5. LOADING SAMPLE DATA"
    
    # Check if database is accessible
    print_step "Checking database connection..."
    if ! python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from microagents.db.session import SessionLocal
try:
    db = SessionLocal()
    db.execute('SELECT 1')
    print('Database connection successful')
    db.close()
except Exception as e:
    print(f'Database connection failed: {e}')
    sys.exit(1)
"; then
        print_error "Cannot connect to database. Please set it up first."
        return 1
    fi
    
    # Load seed data
    print_step "Loading seed data..."
    cd "$PROJECT_ROOT" && hatch run seed-all
    
    # Generate demo data if requested
    read -p "Do you want to generate demo data? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_step "Generating demo data..."
        cd "$PROJECT_ROOT" && hatch run generate-demo-data
    fi
    
    # Verify data loaded
    print_step "Verifying data..."
    python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from microagents.db.session import SessionLocal
from microagents.models import AgentType

db = SessionLocal()
count = db.query(AgentType).count()
print(f'Found {count} agent types in database')
db.close()
"
    
    print_success "Sample data loaded successfully"
}

# ============================================================================
# 6. DEVELOPMENT SERVICES START
# ============================================================================

start_dev_services() {
    print_header "6. STARTING DEVELOPMENT SERVICES"
    
    # Create PM2 ecosystem file for process management
    if command -v pm2 &> /dev/null; then
        print_step "Setting up PM2 process manager..."
        cat > "$PROJECT_ROOT/ecosystem.config.js" << 'EOF'
module.exports = {
  apps: [
    {
      name: 'microagents-api',
      script: 'hatch',
      args: 'run dev',
      cwd: process.cwd(),
      env: {
        NODE_ENV: 'development',
        PYTHONPATH: '.',
        MA_ENVIRONMENT: 'development'
      },
      watch: ['microagents', 'config'],
      ignore_watch: ['node_modules', 'logs', '.git'],
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'microagents-workers',
      script: 'hatch',
      args: 'run workers',
      cwd: process.cwd(),
      env: {
        NODE_ENV: 'development',
        PYTHONPATH: '.'
      },
      instances: 2,
      exec_mode: 'cluster',
      autorestart: true
    }
  ]
};
EOF
        print_success "Created ecosystem.config.js"
    fi
    
    # Start services based on preference
    print_info "Development services can be started in different ways:"
    echo "1. Using Hatch directly (recommended for simple setup)"
    echo "2. Using PM2 (recommended for production-like setup)"
    echo "3. Using Docker Compose (full isolated environment)"
    echo "4. Manual start"
    
    read -p "Choose option (1-4, default: 1): " service_option
    service_option=${service_option:-1}
    
    case $service_option in
        1)
            start_services_hatch
            ;;
        2)
            start_services_pm2
            ;;
        3)
            start_services_docker
            ;;
        4)
            print_info "You can start services manually:"
            echo "  API: hatch run dev"
            echo "  Workers: hatch run workers"
            echo "  Dashboard: cd dashboard && npm run dev"
            ;;
    esac
}

start_services_hatch() {
    print_step "Starting services with Hatch..."
    
    # Start in background
    cd "$PROJECT_ROOT"
    
    # Start API in background
    print_info "Starting API server on port $API_PORT..."
    hatch run dev > "$LOGS_DIR/api.log" 2>&1 &
    API_PID=$!
    echo $API_PID > "$PROJECT_ROOT/.api.pid"
    
    # Start workers in background
    print_info "Starting worker processes..."
    hatch run workers > "$LOGS_DIR/workers.log" 2>&1 &
    WORKER_PID=$!
    echo $WORKER_PID > "$PROJECT_ROOT/.worker.pid"
    
    print_success "Services started in background"
    print_info "Logs:"
    echo "  API: tail -f $LOGS_DIR/api.log"
    echo "  Workers: tail -f $LOGS_DIR/workers.log"
}

start_services_pm2() {
    print_step "Starting services with PM2..."
    
    if ! command -v pm2 &> /dev/null; then
        print_error "PM2 not installed. Install with: npm install -g pm2"
        start_services_hatch
        return
    fi
    
    cd "$PROJECT_ROOT"
    pm2 start ecosystem.config.js
    
    print_success "Services started with PM2"
    print_info "Commands:"
    echo "  View logs: pm2 logs"
    echo "  Monitor: pm2 monit"
    echo "  Stop: pm2 stop all"
}

start_services_docker() {
    print_step "Starting services with Docker Compose..."
    
    if [ ! -f "$DOCKER_DIR/docker-compose.full.yml" ]; then
        cat > "$DOCKER_DIR/docker-compose.full.yml" << 'EOF'
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: microagents_dev
      POSTGRES_USER: microagents
      POSTGRES_PASSWORD: dev_password_123
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  api:
    build:
      context: ..
      dockerfile: docker/Dockerfile.api
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://microagents:dev_password_123@postgres/microagents_dev
      REDIS_URL: redis://redis:6379/0
    volumes:
      - ../:/app
    depends_on:
      - postgres
      - redis

  workers:
    build:
      context: ..
      dockerfile: docker/Dockerfile.workers
    environment:
      DATABASE_URL: postgresql://microagents:dev_password_123@postgres/microagents_dev
      REDIS_URL: redis://redis:6379/0
    volumes:
      - ../:/app
    depends_on:
      - postgres
      - redis

volumes:
  postgres_data:
  redis_data:
EOF
    fi
    
    cd "$DOCKER_DIR" && docker-compose -f docker-compose.full.yml up -d
    
    print_success "Services started with Docker Compose"
    print_info "View logs: docker-compose -f $DOCKER_DIR/docker-compose.full.yml logs -f"
}

# ============================================================================
# 7. CONFIGURATION SETUP
# ============================================================================

setup_configuration() {
    print_header "7. SETTING UP CONFIGURATION"
    
    create_directory "$CONFIG_DIR"
    create_directory "$DATA_DIR"
    create_directory "$LOGS_DIR"
    
    # Environment files
    print_step "Setting up environment configuration..."
    
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        cat > "$PROJECT_ROOT/.env" << 'EOF'
# Environment
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG

# Database
DATABASE_URL=postgresql://microagents:dev_password_123@localhost:5432/microagents_dev
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20

# Redis
REDIS_URL=redis://localhost:6379/0
REDIS_POOL_SIZE=20

# Security
SECRET_KEY=dev-secret-key-change-in-production
JWT_SECRET_KEY=dev-jwt-secret-change-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30

# API
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=4
CORS_ORIGINS=["http://localhost:3000", "http://127.0.0.1:3000"]

# Monitoring
METRICS_ENABLED=true
TRACING_ENABLED=false
LOGGING_ENABLED=true

# Agents
MAX_CONCURRENT_AGENTS=50
AGENT_HEALTH_CHECK_INTERVAL=60

# Development
RELOAD=true
TESTING=false
EOF
        print_success "Created .env file"
    else
        print_info ".env file already exists"
    fi
    
    # Local development configuration
    if [ ! -f "$CONFIG_DIR/development.yaml" ]; then
        cat > "$CONFIG_DIR/development.yaml" << 'EOF'
# Development configuration
app:
  name: "MicroAgents Platform"
  environment: "development"
  debug: true
  reload: true

database:
  url: "postgresql://microagents:dev_password_123@localhost:5432/microagents_dev"
  echo: false
  pool_size: 10
  max_overflow: 20

redis:
  url: "redis://localhost:6379/0"
  pool_size: 20

security:
  secret_key: "dev-secret-key-change-in-production"
  jwt_secret: "dev-jwt-secret-change-in-production"
  password_hashing_rounds: 4  # Lower for development

logging:
  level: "DEBUG"
  format: "json"
  file: "logs/app.log"

monitoring:
  enabled: true
  prometheus_port: 9090
  health_check_interval: 30

agents:
  max_concurrent: 50
  health_check_interval: 60
  timeout: 300

api:
  host: "0.0.0.0"
  port: 8000
  workers: 4
  cors_origins:
    - "http://localhost:3000"
    - "http://127.0.0.1:3000"
EOF
        print_success "Created development.yaml"
    fi
    
    # Git configuration for development
    print_step "Setting up Git configuration..."
    
    if [ ! -f "$PROJECT_ROOT/.gitattributes" ]; then
        cat > "$PROJECT_ROOT/.gitattributes" << 'EOF'
# Auto detect text files and perform LF normalization
* text=auto

# Source code
*.py text diff=python
*.js text diff=javascript
*.ts text diff=typescript
*.json text diff=json
*.yaml text diff=yaml
*.yml text diff=yaml
*.md text
*.txt text
*.toml text

# Binary files
*.png binary
*.jpg binary
*.jpeg binary
*.gif binary
*.ico binary
*.pdf binary
*.woff binary
*.woff2 binary
*.eot binary
*.ttf binary
*.otf binary

# Platform specific
*.sh text eol=lf
*.bat text eol=crlf
EOF
    fi
    
    # Editor configuration
    print_step "Setting up editor configuration..."
    
    if [ ! -f "$PROJECT_ROOT/.editorconfig" ]; then
        cat > "$PROJECT_ROOT/.editorconfig" << 'EOF'
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4

[*.py]
indent_size = 4
max_line_length = 88

[*.{js,ts,jsx,tsx}]
indent_size = 2

[*.{json,yaml,yml}]
indent_size = 2

[*.md]
trim_trailing_whitespace = false
max_line_length = 80

[Makefile]
indent_style = tab

[Dockerfile]
indent_size = 4
EOF
    fi
    
    print_success "Configuration files created"
}

# ============================================================================
# 8. TESTING ENVIRONMENT
# ============================================================================

setup_testing() {
    print_header "8. SETTING UP TESTING ENVIRONMENT"
    
    # Test database
    print_step "Setting up test database..."
    if command -v docker &> /dev/null; then
        docker run -d --name microagents-test-db \
            -e POSTGRES_DB=microagents_test \
            -e POSTGRES_USER=microagents \
            -e POSTGRES_PASSWORD=test_password_123 \
            -p 5433:5432 \
            postgres:15-alpine
        
        # Wait for database
        for i in {1..30}; do
            if pg_isready -h localhost -p 5433; then
                break
            fi
            sleep 2
        done
    fi
    
    # Create test configuration
    if [ ! -f "$CONFIG_DIR/testing.yaml" ]; then
        cat > "$CONFIG_DIR/testing.yaml" << 'EOF'
# Testing configuration
app:
  name: "MicroAgents Platform Test"
  environment: "testing"
  debug: false
  reload: false

database:
  url: "postgresql://microagents:test_password_123@localhost:5433/microagents_test"
  echo: false
  pool_size: 5
  max_overflow: 10

redis:
  url: "redis://localhost:6380/0"
  pool_size: 10

security:
  secret_key: "test-secret-key"
  jwt_secret: "test-jwt-secret"
  password_hashing_rounds: 4

logging:
  level: "WARNING"
  format: "simple"
  file: "logs/test.log"

testing:
  use_transaction: true
  reset_database: true
  parallel: true
EOF
    fi
    
    # Install test dependencies
    print_step "Installing test dependencies..."
    pip install pytest pytest-asyncio pytest-cov pytest-mock \
        pytest-xdist hypothesis factory-boy \
        faker freezegun respx
    
    # Create test runner script
    cat > "$PROJECT_ROOT/run_tests.sh" << 'EOF'
#!/bin/bash
set -euo pipefail

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "Running tests..."

# Run pytest with coverage
pytest tests/ \
    -v \
    --cov=microagents \
    --cov-report=html \
    --cov-report=term \
    --cov-report=xml \
    --junitxml=test-results.xml \
    --tb=short \
    --strict-markers \
    -n auto

# Check test results
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    
    # Generate coverage badge if available
    if command -v coverage-badge &> /dev/null; then
        coverage-badge -o coverage.svg
        echo "Coverage badge generated"
    fi
else
    echo -e "${RED}✗ Tests failed${NC}"
    exit 1
fi
EOF
    
    chmod +x "$PROJECT_ROOT/run_tests.sh"
    
    print_success "Testing environment configured"
    print_info "Run tests: ./run_tests.sh"
}

# ============================================================================
# 9. DOCUMENTATION GENERATION
# ============================================================================

setup_documentation() {
    print_header "9. SETTING UP DOCUMENTATION"
    
    print_step "Installing documentation dependencies..."
    pip install mkdocs mkdocs-material mkdocstrings[python] \
        mkdocs-gen-files mkdocs-literate-nav \
        mkdocs-section-index
    
    # Create docs structure
    create_directory "$PROJECT_ROOT/docs"
    create_directory "$PROJECT_ROOT/docs/api"
    create_directory "$PROJECT_ROOT/docs/guides"
    
    # Create mkdocs config if not exists
    if [ ! -f "$PROJECT_ROOT/mkdocs.yml" ]; then
        cat > "$PROJECT_ROOT/mkdocs.yml" << 'EOF'
site_name: MicroAgents Platform
site_description: Documentation for MicroAgents Platform
site_url: https://docs.microagents.io
repo_url: https://github.com/microagents/devops-platform
repo_name: microagents/devops-platform

theme:
  name: material
  palette:
    primary: indigo
    accent: blue
  features:
    - navigation.tabs
    - navigation.sections
    - toc.integrate
    - search.suggest
    - search.highlight
    - content.tabs.link
    - content.code.annotation
    - content.code.copy

plugins:
  - search
  - gen-files:
      scripts:
        - docs/gen_ref_pages.py
  - literate-nav:
      nav_file: SUMMARY.md
  - section-index
  - mkdocstrings:
      handlers:
        python:
          paths: [microagents]
          options:
            show_source: true
            show_root_heading: true

nav:
  - Home: index.md
  - Getting Started:
      - Installation: guides/installation.md
      - Quick Start: guides/quickstart.md
      - Configuration: guides/configuration.md
  - API Reference:
      - Overview: api/overview.md
      - REST API: api/rest.md
      - CLI: api/cli.md
      - Agents: api/agents.md
  - Development:
      - Setup: development/setup.md
      - Testing: development/testing.md
      - Contributing: development/contributing.md
  - Operations:
      - Deployment: operations/deployment.md
      - Monitoring: operations/monitoring.md
      - Backup & Restore: operations/backup.md

markdown_extensions:
  - admonition
  - codehilite
  - pymdownx.superfences
  - pymdownx.tabbed
  - pymdownx.details
  - pymdownx.emoji:
      emoji_index: !!python/name:material.extensions.emoji.twemoji
      emoji_generator: !!python/name:material.extensions.emoji.to_svg
  - toc:
      permalink: true
EOF
    fi
    
    # Create documentation generation script
    cat > "$PROJECT_ROOT/docs/gen_ref_pages.py" << 'EOF'
"""Generate API reference pages."""
from pathlib import Path
import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()

root = Path(__file__).parent.parent
src = root / "microagents"

for path in sorted(src.rglob("*.py")):
    module_path = path.relative_to(src).with_suffix("")
    doc_path = path.relative_to(src).with_suffix(".md")
    full_doc_path = Path("api", doc_path)

    parts = tuple(module_path.parts)

    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
        full_doc_path = full_doc_path.with_name("index.md")
    elif parts[-1] == "__main__":
        continue

    nav[parts] = doc_path.as_posix()

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        ident = ".".join(parts)
        fd.write(f"::: {ident}")

    mkdocs_gen_files.set_edit_path(full_doc_path, path.relative_to(root))

with mkdocs_gen_files.open("api/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
EOF
    
    # Create documentation preview script
    cat > "$PROJECT_ROOT/scripts/dev/preview_docs.sh" << 'EOF'
#!/bin/bash
set -euo pipefail

echo "Starting documentation preview..."
echo "Open http://localhost:8001 in your browser"

cd "$(dirname "$0")/../.."
mkdocs serve --dev-addr 127.0.0.1:8001
EOF
    
    chmod +x "$PROJECT_ROOT/scripts/dev/preview_docs.sh"
    
    print_success "Documentation setup complete"
    print_info "Preview docs: ./scripts/dev/preview_docs.sh"
}

# ============================================================================
# 10. HEALTH CHECK
# ============================================================================

run_health_check() {
    print_header "10. RUNNING HEALTH CHECK"
    
    local health_ok=true
    
    # Check Python
    print_step "Checking Python environment..."
    if [ -z "${VIRTUAL_ENV:-}" ]; then
        print_error "Not in virtual environment"
        health_ok=false
    else
        print_success "In virtual environment: $VIRTUAL_ENV"
    fi
    
    # Check dependencies
    print_step "Checking dependencies..."
    if ! python -c "import microagents" 2>/dev/null; then
        print_error "MicroAgents not importable"
        health_ok=false
    else
        print_success "MicroAgents importable"
    fi
    
    # Check database
    print_step "Checking database..."
    if ! python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from microagents.db.session import SessionLocal
try:
    db = SessionLocal()
    db.execute('SELECT 1')
    print('✓ Database connected')
    db.close()
except Exception as e:
    print(f'✗ Database error: {e}')
    sys.exit(1)
"; then
        health_ok=false
    fi
    
    # Check Redis
    print_step "Checking Redis..."
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping | grep -q PONG; then
            print_success "Redis connected"
        else
            print_warning "Redis not responding"
        fi
    fi
    
    # Check services
    print_step "Checking services..."
    if curl -s http://localhost:$API_PORT/health > /dev/null 2>&1; then
        print_success "API service running"
    else
        print_warning "API service not running (expected on port $API_PORT)"
    fi
    
    # Generate health report
    cat > "$PROJECT_ROOT/.health_report.txt" << EOF
MicroAgents Development Environment Health Report
Generated: $(date)

SUMMARY:
- Virtual Environment: ${VIRTUAL_ENV:-Not active}
- Python: $(python --version 2>&1)
- Platform: $PLATFORM
- Database: $(python -c "import sys; sys.path.insert(0, '$PROJECT_ROOT'); from microagents.db.session import SessionLocal; db = SessionLocal(); print('Connected' if db else 'Not connected'); db.close()" 2>/dev/null || echo "Unknown")
- Services: $(curl -s http://localhost:$API_PORT/health > /dev/null 2>&1 && echo "API Running" || echo "API Not Running")

NEXT STEPS:
1. Activate virtual environment: source $VENV_DIR/bin/activate
2. Start services: hatch run dev
3. Access dashboard: http://localhost:3000
4. View API docs: http://localhost:$API_PORT/docs

TROUBLESHOOTING:
- Database issues: Check docker-compose or local PostgreSQL
- Import errors: Run pip install -e .
- Port conflicts: Check ports $API_PORT, $POSTGRES_PORT, $REDIS_PORT
EOF
    
    print_step "Health report generated: .health_report.txt"
    
    if [ "$health_ok" = true ]; then
        print_success "Health check passed! Development environment is ready."
        print_info "\nQuick start:"
        echo "  1. source $VENV_DIR/bin/activate"
        echo "  2. hatch run dev"
        echo "  3. Open http://localhost:$API_PORT/docs"
    else
        print_warning "Health check found some issues. See report above."
    fi
}

# ============================================================================
# DEV SETUP FEATURES IMPLEMENTATION
# ============================================================================

setup_dev_features() {
    print_header "SETTING UP DEVELOPMENT FEATURES"
    
    # Multi-OS support already handled in platform detection
    
    # Performance optimization
    setup_performance
    
    # Security configuration
    setup_security
    
    # IDE configuration
    setup_ide
    
    # Debug tools
    setup_debug_tools
    
    # Profiling setup
    setup_profiling
    
    # Testing framework (already done)
    
    # Documentation preview (already done)
}

setup_performance() {
    print_step "Setting up performance optimizations..."
    
    # Create performance test script
    cat > "$PROJECT_ROOT/scripts/dev/performance_test.sh" << 'EOF'
#!/bin/bash
set -euo pipefail

echo "Running performance tests..."

# Run benchmarks
pytest tests/benchmarks/ -v --benchmark-only

# Memory profiling
if command -v mprof &> /dev/null; then
    echo "Running memory profiling..."
    mprof run python -c "
import microagents
# Your performance test here
"
    mprof plot
fi
EOF
    
    chmod +x "$PROJECT_ROOT/scripts/dev/performance_test.sh"
    
    # Install performance tools
    pip install py-spy memory-profiler psutil pyperf
    
    print_success "Performance tools installed"
}

setup_security() {
    print_step "Setting up security configuration..."
    
    # Create security check script
    cat > "$PROJECT_ROOT/scripts/dev/security_check.sh" << 'EOF'
#!/bin/bash
set -euo pipefail

echo "Running security checks..."

# Bandit - security linting
bandit -r microagents -x tests

# Safety - dependency checking
safety check

# Detect secrets
if command -v detect-secrets &> /dev/null; then
    detect-secrets scan --baseline .secrets.baseline
fi

# TruffleHog for secrets in git history
if command -v trufflehog &> /dev/null; then
    trufflehog git file://. --since-commit HEAD~100 --fail
fi
EOF
    
    chmod +x "$PROJECT_ROOT/scripts/dev/security_check.sh"
    
    # Install security tools
    pip install bandit safety detect-secrets trufflehog
    
    print_success "Security tools installed"
}

setup_ide() {
    print_step "Setting up IDE configurations..."
    
    # VS Code
    if [ ! -d "$PROJECT_ROOT/.vscode" ]; then
        create_directory "$PROJECT_ROOT/.vscode"
        
        cat > "$PROJECT_ROOT/.vscode/settings.json" << 'EOF'
{
    "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
    "python.terminal.activateEnvironment": true,
    "python.analysis.typeCheckingMode": "strict",
    "python.analysis.autoImportCompletions": true,
    "python.testing.pytestEnabled": true,
    "python.testing.unittestEnabled": false,
    "python.testing.pytestArgs": [
        "tests",
        "--no-cov",
        "-v"
    ],
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.organizeImports": "always"
    },
    "[python]": {
        "editor.defaultFormatter": "ms-python.black-formatter",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.organizeImports": "always"
        }
    },
    "[json]": {
        "editor.defaultFormatter": "vscode.json-language-features"
    },
    "[yaml]": {
        "editor.defaultFormatter": "redhat.vscode-yaml"
    },
    "files.exclude": {
        "**/__pycache__": true,
        "**/.pytest_cache": true,
        "**/.mypy_cache": true,
        "**/.ruff_cache": true,
        "**/.coverage": true,
        "**/htmlcov": true
    },
    "terminal.integrated.env.linux": {
        "PYTHONPATH": "${workspaceFolder}"
    },
    "terminal.integrated.env.osx": {
        "PYTHONPATH": "${workspaceFolder}"
    },
    "terminal.integrated.env.windows": {
        "PYTHONPATH": "${workspaceFolder}"
    }
}
EOF
        
        cat > "$PROJECT_ROOT/.vscode/extensions.json" << 'EOF'
{
    "recommendations": [
        "ms-python.python",
        "ms-python.vscode-pylance",
        "ms-python.black-formatter",
        "charliermarsh.ruff",
        "redhat.vscode-yaml",
        "esbenp.prettier-vscode",
        "ms-azuretools.vscode-docker",
        "github.vscode-github-actions",
        "ms-vscode.makefile-tools"
    ]
}
EOF
        
        cat > "$PROJECT_ROOT/.vscode/launch.json" << 'EOF'
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: FastAPI",
            "type": "python",
            "request": "launch",
            "module": "uvicorn",
            "args": [
                "microagents.api.server:app",
                "--reload",
                "--host",
                "0.0.0.0",
                "--port",
                "8000"
            ],
            "jinja": true,
            "justMyCode": true
        },
        {
            "name": "Python: Pytest",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "args": [
                "tests",
                "-v",
                "--no-cov"
            ],
            "console": "integratedTerminal"
        },
        {
            "name": "Python: Debug Console",
            "type": "python",
            "request": "launch",
            "program": "${file}",
            "console": "internalConsole"
        }
    ]
}
EOF
    fi
    
    # PyCharm
    if [ ! -f "$PROJECT_ROOT/.idea" ]; then
        print_info "PyCharm: Mark .venv as sources root in IDE"
    fi
    
    print_success "IDE configurations created"
}

setup_debug_tools() {
    print_step "Setting up debug tools..."
    
    # Install debug tools
    pip install ipython ipdb debugpy icecream
    
    # Create debug configuration
    cat > "$PROJECT_ROOT/scripts/dev/debug_server.sh" << 'EOF'
#!/bin/bash
set -euo pipefail

echo "Starting debug server..."
echo "Attach debugger to port 5678"

cd "$(dirname "$0")/../.."
python -m debugpy --listen 0.0.0.0:5678 -m uvicorn \
    microagents.api.server:app \
    --reload \
    --host 0.0.0.0 \
    --port 8000
EOF
    
    chmod +x "$PROJECT_ROOT/scripts/dev/debug_server.sh"
    
    print_success "Debug tools installed"
}

setup_profiling() {
    print_step "Setting up profiling tools..."
    
    # Install profiling tools
    pip install pyinstrument snakeviz memory-profiler line_profiler
    
    # Create profiling script
    cat > "$PROJECT_ROOT/scripts/dev/profile_api.sh" << 'EOF'
#!/bin/bash
set -euo pipefail

echo "Profiling API endpoint..."

# Profile with pyinstrument
python -m pyinstrument -r html -o profile.html \
    -m uvicorn microagents.api.server:app \
    --host 0.0.0.0 --port 8001 --reload &
    
PID=$!
sleep 5

# Run some requests
curl http://localhost:8001/health
curl http://localhost:8001/api/v1/agents

kill $PID
echo "Profile saved to profile.html"
open profile.html 2>/dev/null || echo "Open profile.html in browser"
EOF
    
    chmod +x "$PROJECT_ROOT/scripts/dev/profile_api.sh"
    
    print_success "Profiling tools installed"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    print_header "MICROAGENTS PLATFORM - DEVELOPMENT ENVIRONMENT SETUP"
    print_info "Platform detected: $PLATFORM"
    print_info "Project root: $PROJECT_ROOT"
    
    # Parse arguments
    SKIP_STEPS=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --skip=*)
                SKIP_STEPS="${1#*=}"
                shift
                ;;
            --help)
                print_help
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                print_help
                exit 1
                ;;
        esac
    done
    
    # Check prerequisites
    check_command python3
    check_python_version
    
    # Create necessary directories
    create_directory "$LOGS_DIR"
    create_directory "$DATA_DIR"
    create_directory "$CONFIG_DIR"
    
    # Execute steps
    execute_step "1" "setup_venv"
    execute_step "2" "install_dependencies"
    execute_step "3" "setup_precommit"
    execute_step "4" "setup_database"
    execute_step "5" "load_sample_data"
    execute_step "6" "start_dev_services"
    execute_step "7" "setup_configuration"
    execute_step "8" "setup_testing"
    execute_step "9" "setup_documentation"
    execute_step "10" "run_health_check"
    
    # Setup dev features
    setup_dev_features
    
    print_header "SETUP COMPLETE"
    print_success "MicroAgents Platform development environment is ready!"
    
    # Final instructions
    cat > "$PROJECT_ROOT/QUICKSTART.md" << 'EOF'
# Quick Start Guide

## Development Environment Ready!

### Next Steps:

1. **Activate Virtual Environment:**
   ```bash
   source .venv/bin/activate  # Linux/macOS/WSL
   # or
   .venv\Scripts\activate     # Windows
   ```

2. **Start Development Services:**
   ```bash
   # Start API server
   hatch run dev
   
   # Start workers (in another terminal)
   hatch run workers
   ```

3. **Access Services:**
   - API Documentation: http://localhost:8000/docs
   - Health Check: http://localhost:8000/health
   - Metrics: http://localhost:8000/metrics

4. **Run Tests:**
   ```bash
   ./run_tests.sh
   ```

5. **Common Commands:**
   ```bash
   # Code quality
   hatch run lint
   hatch run format
   
   # Database
   hatch run migrate upgrade head
   hatch run seed-all
   
   # Documentation
   ./scripts/dev/preview_docs.sh
   ```

### Useful Scripts:
- `scripts/dev/debug_server.sh` - Start with debugger
- `scripts/dev/performance_test.sh` - Run benchmarks
- `scripts/dev/security_check.sh` - Security audit
- `scripts/dev/profile_api.sh` - Profile API performance

### Need Help?
- Check `.health_report.txt` for environment status
- View logs in `logs/` directory
- Run `hatch --help` for available commands

Happy coding! 🚀
EOF
    
    print_info "\nQuick start guide created: QUICKSTART.md"
    print_info "Summary written to: .health_report.txt"
    
    # Display next steps
    echo -e "\n${GREEN}══════════════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}🚀 Ready to develop! Next steps:${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════${NC}"
    echo "1. source $VENV_DIR/bin/activate"
    echo "2. hatch run dev"
    echo "3. Open http://localhost:8000/docs"
    echo -e "${GREEN}══════════════════════════════════════════════════════════════════════════════${NC}"
}

execute_step() {
    local step_num=$1
    local step_func=$2
    
    # Check if step should be skipped
    if [[ "$SKIP_STEPS" == *"$step_num"* ]]; then
        print_warning "Skipping step $step_num ($step_func)"
        return 0
    fi
    
    # Execute step
    if $step_func; then
        print_success "Step $step_num completed"
    else
        print_error "Step $step_num failed"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

print_help() {
    cat << 'EOF'
MicroAgents Platform Development Setup Script

Usage: ./setup_venv.sh [OPTIONS]

Options:
  --skip=LIST     Skip specific steps (comma-separated numbers 1-10)
  --help          Show this help message

Steps:
  1. Virtual environment creation
  2. Dependency installation
  3. Pre-commit hooks setup
  4. Database initialization
  5. Sample data loading
  6. Development services start
  7. Configuration setup
  8. Testing environment
  9. Documentation generation
  10. Health check

Examples:
  ./setup_venv.sh                    # Complete setup
  ./setup_venv.sh --skip=4,5         # Skip database and data loading
  ./setup_venv.sh --skip=6           # Skip starting services

Platforms supported:
  - Linux (Ubuntu/Debian/CentOS)
  - macOS
  - Windows (WSL2 recommended)
  - Windows Native (limited support)
EOF
}

# Run main function
main "$@"
```