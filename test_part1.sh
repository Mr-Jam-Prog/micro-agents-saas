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
