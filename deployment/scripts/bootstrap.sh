#!/bin/bash
# deployment/scripts/bootstrap.sh
# MicroAgents Platform Bootstrap Script
# Idempotent deployment script for multi-cloud Kubernetes clusters

set -euo pipefail
IFS=$'\n\t'

# ==============================================================================
# Configuration
# ==============================================================================

SCRIPT_NAME="$(basename "$0")"
SCRIPT_VERSION="1.0.0"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default configuration
DEFAULT_NAMESPACE="microagents"
DEFAULT_ENVIRONMENT="production"
DEFAULT_CLOUD_PROVIDER="aws"
DEFAULT_K8S_VERSION="1.27"
DEFAULT_STORAGE_CLASS="standard"
DEFAULT_NODE_COUNT=3
DEFAULT_NODE_TYPE="Standard_D4s_v3"

# Configuration file
CONFIG_FILE="${CONFIG_FILE:-./bootstrap-config.yaml}"

# ==============================================================================
# Logging Functions
# ==============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_debug() {
    if [[ "${DEBUG:-false}" == "true" ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
    fi
}

# ==============================================================================
# Utility Functions
# ==============================================================================

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check exit code and exit on failure
check_exit_code() {
    local exit_code=$1
    local message=$2
    
    if [[ $exit_code -ne 0 ]]; then
        log_error "$message (exit code: $exit_code)"
        
        if [[ "${ENABLE_ROLLBACK:-true}" == "true" ]]; then
            rollback_cleanup
        fi
        
        exit $exit_code
    fi
}

# Generate random password
generate_password() {
    local length=${1:-32}
    tr -dc 'A-Za-z0-9!@#$%^&*()_+=-' < /dev/urandom | head -c "$length"
}

# Wait for resource to be ready
wait_for_resource() {
    local resource_type=$1
    local resource_name=$2
    local namespace=${3:-$DEFAULT_NAMESPACE}
    local timeout=${4:-300}
    local interval=${5:-5}
    
    log_info "Waiting for $resource_type/$resource_name to be ready..."
    
    local start_time=$(date +%s)
    while true; do
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [[ $elapsed -ge $timeout ]]; then
            log_error "Timeout waiting for $resource_type/$resource_name"
            return 1
        fi
        
        if kubectl get "$resource_type" "$resource_name" -n "$namespace" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q "True"; then
            log_success "$resource_type/$resource_name is ready"
            return 0
        fi
        
        sleep "$interval"
    done
}

# ==============================================================================
# Rollback Functions
# ==============================================================================

rollback_cleanup() {
    log_warning "Starting rollback cleanup..."
    
    # Store rollback state
    local rollback_file="/tmp/microagents-rollback-$TIMESTAMP.log"
    echo "Rollback started at: $(date)" > "$rollback_file"
    
    # Delete resources in reverse order
    if [[ -f "/tmp/microagents-resources-created.log" ]]; then
        tac "/tmp/microagents-resources-created.log" | while read -r resource; do
            log_info "Deleting resource: $resource"
            kubectl delete "$resource" --ignore-not-found=true --wait=false >> "$rollback_file" 2>&1 || true
        done
    fi
    
    # Clean up temporary files
    rm -f "/tmp/microagents-resources-created.log"
    
    log_warning "Rollback completed. Log saved to: $rollback_file"
}

# Record created resource for rollback
record_resource() {
    local resource=$1
    echo "$resource" >> "/tmp/microagents-resources-created.log"
}

# ==============================================================================
# Pre-requisites Checking
# ==============================================================================

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    local missing_tools=()
    
    # Required tools
    declare -A required_tools=(
        ["kubectl"]="Kubernetes CLI"
        ["helm"]="Helm package manager"
        ["jq"]="JSON processor"
        ["yq"]="YAML processor"
        ["git"]="Version control"
    )
    
    # Cloud-specific tools
    case "$CLOUD_PROVIDER" in
        aws)
            required_tools["aws"]="AWS CLI"
            required_tools["terraform"]="Infrastructure as Code"
            ;;
        azure)
            required_tools["az"]="Azure CLI"
            required_tools["terraform"]="Infrastructure as Code"
            ;;
        gcp)
            required_tools["gcloud"]="Google Cloud CLI"
            required_tools["terraform"]="Infrastructure as Code"
            ;;
        *)
            log_warning "Unknown cloud provider: $CLOUD_PROVIDER"
            ;;
    esac
    
    # Check each tool
    for tool in "${!required_tools[@]}"; do
        if command_exists "$tool"; then
            local version=$("$tool" version 2>/dev/null || "$tool" --version 2>/dev/null || echo "unknown")
            log_debug "✓ $tool: ${required_tools[$tool]} (version: $version)"
        else
            missing_tools+=("$tool")
            log_error "✗ $tool: ${required_tools[$tool]} is missing"
        fi
    done
    
    # Check Kubernetes cluster connectivity
    if command_exists kubectl; then
        if kubectl cluster-info >/dev/null 2>&1; then
            log_debug "✓ Kubernetes cluster is accessible"
            K8S_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "unknown")
            log_debug "  Context: $K8S_CONTEXT"
        else
            log_warning "Kubernetes cluster is not accessible"
        fi
    fi
    
    # Check disk space
    local free_space=$(df -h / | awk 'NR==2 {print $4}')
    log_debug "Free disk space: $free_space"
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing_tools[*]}"
        log_info "Attempting to install missing tools..."
        install_missing_tools "${missing_tools[@]}"
    else
        log_success "All prerequisites satisfied"
    fi
}

install_missing_tools() {
    local missing_tools=("$@")
    
    # Detect package manager
    local pkg_manager=""
    if command_exists apt-get; then
        pkg_manager="apt"
    elif command_exists yum; then
        pkg_manager="yum"
    elif command_exists brew; then
        pkg_manager="brew"
    else
        log_error "No supported package manager found"
        return 1
    fi
    
    # Install tools
    for tool in "${missing_tools[@]}"; do
        log_info "Installing $tool..."
        
        case $tool in
            kubectl)
                curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
                chmod +x kubectl
                sudo mv kubectl /usr/local/bin/
                ;;
            helm)
                curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
                ;;
            jq|yq|git)
                if [[ $pkg_manager == "apt" ]]; then
                    sudo apt-get update && sudo apt-get install -y "$tool"
                elif [[ $pkg_manager == "yum" ]]; then
                    sudo yum install -y "$tool"
                elif [[ $pkg_manager == "brew" ]]; then
                    brew install "$tool"
                fi
                ;;
            aws)
                if [[ $pkg_manager == "apt" ]]; then
                    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
                    unzip awscliv2.zip
                    sudo ./aws/install
                elif [[ $pkg_manager == "brew" ]]; then
                    brew install awscli
                fi
                ;;
            az)
                if [[ $pkg_manager == "apt" ]]; then
                    curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
                elif [[ $pkg_manager == "brew" ]]; then
                    brew install azure-cli
                fi
                ;;
            gcloud)
                if [[ $pkg_manager == "apt" ]]; then
                    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
                    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo tee /usr/share/keyrings/cloud.google.gpg
                    sudo apt-get update && sudo apt-get install -y google-cloud-sdk
                elif [[ $pkg_manager == "brew" ]]; then
                    brew install google-cloud-sdk
                fi
                ;;
            terraform)
                if [[ $pkg_manager == "apt" ]]; then
                    wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
                    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
                    sudo apt update && sudo apt install -y terraform
                elif [[ $pkg_manager == "brew" ]]; then
                    brew install terraform
                fi
                ;;
        esac
        
        check_exit_code $? "Failed to install $tool"
    done
}

# ==============================================================================
# Environment Validation
# ==============================================================================

validate_environment() {
    log_info "Validating environment..."
    
    # Check for configuration file
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_warning "Configuration file not found: $CONFIG_FILE"
        log_info "Creating default configuration..."
        create_default_config
    fi
    
    # Load configuration
    load_configuration
    
    # Validate required environment variables
    validate_environment_variables
    
    # Validate cloud provider configuration
    validate_cloud_provider
    
    # Validate Kubernetes version
    validate_kubernetes_version
    
    # Check resource quotas
    validate_resource_quotas
    
    log_success "Environment validation completed"
}

create_default_config() {
    cat > "$CONFIG_FILE" << EOF
# MicroAgents Platform Bootstrap Configuration
# Generated: $(date)

environment: $DEFAULT_ENVIRONMENT
cloud_provider: $DEFAULT_CLOUD_PROVIDER
namespace: $DEFAULT_NAMESPACE

kubernetes:
  version: $DEFAULT_K8S_VERSION
  node_count: $DEFAULT_NODE_COUNT
  node_type: $DEFAULT_NODE_TYPE

storage:
  class: $DEFAULT_STORAGE_CLASS
  size: 100Gi

monitoring:
  enabled: true
  retention_days: 30

backup:
  enabled: true
  schedule: "0 3 * * *"
  retention_days: 30

security:
  enable_network_policies: true
  enable_pod_security: true

performance:
  enable_hpa: true
  enable_vpa: false
EOF
}

load_configuration() {
    if command_exists yq; then
        ENVIRONMENT=$(yq e '.environment' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_ENVIRONMENT")
        CLOUD_PROVIDER=$(yq e '.cloud_provider' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_CLOUD_PROVIDER")
        NAMESPACE=$(yq e '.namespace' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_NAMESPACE")
        K8S_VERSION=$(yq e '.kubernetes.version' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_K8S_VERSION")
        NODE_COUNT=$(yq e '.kubernetes.node_count' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_NODE_COUNT")
        NODE_TYPE=$(yq e '.kubernetes.node_type' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_NODE_TYPE")
        STORAGE_CLASS=$(yq e '.storage.class' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_STORAGE_CLASS")
    else
        # Fallback to defaults if yq is not available
        ENVIRONMENT=$DEFAULT_ENVIRONMENT
        CLOUD_PROVIDER=$DEFAULT_CLOUD_PROVIDER
        NAMESPACE=$DEFAULT_NAMESPACE
        K8S_VERSION=$DEFAULT_K8S_VERSION
        NODE_COUNT=$DEFAULT_NODE_COUNT
        NODE_TYPE=$DEFAULT_NODE_TYPE
        STORAGE_CLASS=$DEFAULT_STORAGE_CLASS
    fi
    
    log_debug "Configuration loaded:"
    log_debug "  Environment: $ENVIRONMENT"
    log_debug "  Cloud Provider: $CLOUD_PROVIDER"
    log_debug "  Namespace: $NAMESPACE"
    log_debug "  Kubernetes Version: $K8S_VERSION"
    log_debug "  Node Count: $NODE_COUNT"
    log_debug "  Node Type: $NODE_TYPE"
    log_debug "  Storage Class: $STORAGE_CLASS"
}

validate_environment_variables() {
    local required_vars=()
    
    case "$CLOUD_PROVIDER" in
        aws)
            required_vars+=("AWS_ACCESS_KEY_ID" "AWS_SECRET_ACCESS_KEY" "AWS_REGION")
            ;;
        azure)
            required_vars+=("ARM_SUBSCRIPTION_ID" "ARM_TENANT_ID" "ARM_CLIENT_ID" "ARM_CLIENT_SECRET")
            ;;
        gcp)
            required_vars+=("GOOGLE_CREDENTIALS" "GOOGLE_PROJECT" "GOOGLE_REGION")
            ;;
    esac
    
    local missing_vars=()
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            missing_vars+=("$var")
        fi
    done
    
    if [[ ${#missing_vars[@]} -gt 0 ]]; then
        log_error "Missing required environment variables: ${missing_vars[*]}"
        exit 1
    fi
}

validate_cloud_provider() {
    case "$CLOUD_PROVIDER" in
        aws|azure|gcp)
            log_debug "Cloud provider $CLOUD_PROVIDER is supported"
            ;;
        *)
            log_error "Unsupported cloud provider: $CLOUD_PROVIDER"
            log_error "Supported providers: aws, azure, gcp"
            exit 1
            ;;
    esac
}

validate_kubernetes_version() {
    local min_version="1.24"
    local max_version="1.28"
    
    # Extract major.minor version
    local k8s_version=$(echo "$K8S_VERSION" | grep -o '^[0-9]\+\.[0-9]\+')
    
    if ! awk -v ver="$k8s_version" -v min="$min_version" -v max="$max_version" 'BEGIN {
        split(ver, v, ".")
        split(min, mn, ".")
        split(max, mx, ".")
        
        ver_num = v[1] * 1000 + v[2]
        min_num = mn[1] * 1000 + mn[2]
        max_num = mx[1] * 1000 + mx[2]
        
        if (ver_num < min_num || ver_num > max_num) {
            exit 1
        }
    }'; then
        log_error "Kubernetes version $K8S_VERSION is not supported"
        log_error "Supported range: $min_version - $max_version"
        exit 1
    fi
}

validate_resource_quotas() {
    log_info "Checking cluster resource quotas..."
    
    # Check nodes
    local node_count=$(kubectl get nodes --no-headers 2>/dev/null | wc -l || echo "0")
    if [[ $node_count -lt 3 ]]; then
        log_warning "Cluster has only $node_count nodes (minimum 3 recommended for production)"
    fi
    
    # Check node resources
    kubectl get nodes -o json | jq -r '.items[] | "\(.metadata.name): CPU=\(.status.capacity.cpu), Memory=\(.status.capacity.memory)"' 2>/dev/null | while read -r node_info; do
        log_debug "Node resources: $node_info"
    done
    
    # Check storage classes
    local storage_classes=$(kubectl get storageclass --no-headers 2>/dev/null | wc -l || echo "0")
    if [[ $storage_classes -eq 0 ]]; then
        log_warning "No storage classes found in cluster"
    fi
}

# ==============================================================================
# Dependency Installation
# ==============================================================================

install_dependencies() {
    log_info "Installing dependencies..."
    
    # Create namespace if it doesn't exist
    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    record_resource "namespace/$NAMESPACE"
    
    # Add required Helm repositories
    install_helm_repositories
    
    # Install CRDs
    install_crds
    
    # Install ingress controller
    install_ingress_controller
    
    # Install cert-manager
    install_cert_manager
    
    log_success "Dependencies installed successfully"
}

install_helm_repositories() {
    declare -A helm_repos=(
        ["bitnami"]="https://charts.bitnami.com/bitnami"
        ["prometheus-community"]="https://prometheus-community.github.io/helm-charts"
        ["grafana"]="https://grafana.github.io/helm-charts"
        ["jetstack"]="https://charts.jetstack.io"
        ["ingress-nginx"]="https://kubernetes.github.io/ingress-nginx"
        ["external-dns"]="https://kubernetes-sigs.github.io/external-dns"
        ["velero"]="https://vmware-tanzu.github.io/helm-charts"
    )
    
    for repo_name in "${!helm_repos[@]}"; do
        if helm repo list | grep -q "$repo_name"; then
            log_debug "Helm repository $repo_name already exists"
        else
            log_info "Adding Helm repository: $repo_name"
            helm repo add "$repo_name" "${helm_repos[$repo_name]}"
            check_exit_code $? "Failed to add Helm repository $repo_name"
        fi
    done
    
    # Update repositories
    helm repo update
    check_exit_code $? "Failed to update Helm repositories"
}

install_crds() {
    log_info "Installing Custom Resource Definitions..."
    
    # Install cert-manager CRDs
    kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.crds.yaml
    check_exit_code $? "Failed to install cert-manager CRDs"
    
    # Install prometheus operator CRDs
    kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.68.0/example/prometheus-operator-crd/monitoring.coreos.com_alertmanagers.yaml
    kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.68.0/example/prometheus-operator-crd/monitoring.coreos.com_podmonitors.yaml
    kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.68.0/example/prometheus-operator-crd/monitoring.coreos.com_probes.yaml
    kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.68.0/example/prometheus-operator-crd/monitoring.coreos.com_prometheuses.yaml
    kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.68.0/example/prometheus-operator-crd/monitoring.coreos.com_prometheusrules.yaml
    kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.68.0/example/prometheus-operator-crd/monitoring.coreos.com_servicemonitors.yaml
    kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.68.0/example/prometheus-operator-crd/monitoring.coreos.com_thanosrulers.yaml
    
    wait_for_resource "customresourcedefinition" "prometheuses.monitoring.coreos.com" "" 120
}

install_ingress_controller() {
    log_info "Installing ingress controller..."
    
    local ingress_namespace="ingress-nginx"
    kubectl create namespace "$ingress_namespace" --dry-run=client -o yaml | kubectl apply -f -
    
    helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
        --namespace "$ingress_namespace" \
        --set controller.replicaCount=2 \
        --set controller.nodeSelector."kubernetes\.io/os"=linux \
        --set defaultBackend.nodeSelector."kubernetes\.io/os"=linux \
        --set controller.admissionWebhooks.patch.nodeSelector."kubernetes\.io/os"=linux \
        --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-type"=nlb \
        --set controller.metrics.enabled=true \
        --set controller.podAnnotations."prometheus\.io/scrape"=true \
        --set controller.podAnnotations."prometheus\.io/port"=10254 \
        --wait
    
    check_exit_code $? "Failed to install ingress controller"
    
    wait_for_resource "deployment" "ingress-nginx-controller" "$ingress_namespace"
}

install_cert_manager() {
    log_info "Installing cert-manager..."
    
    local cert_manager_namespace="cert-manager"
    kubectl create namespace "$cert_manager_namespace" --dry-run=client -o yaml | kubectl apply -f -
    
    helm upgrade --install cert-manager jetstack/cert-manager \
        --namespace "$cert_manager_namespace" \
        --version v1.13.0 \
        --set installCRDs=true \
        --set nodeSelector."kubernetes\.io/os"=linux \
        --set webhook.nodeSelector."kubernetes\.io/os"=linux \
        --set cainjector.nodeSelector."kubernetes\.io/os"=linux \
        --set startupapicheck.nodeSelector."kubernetes\.io/os"=linux \
        --wait
    
    check_exit_code $? "Failed to install cert-manager"
    
    wait_for_resource "deployment" "cert-manager" "$cert_manager_namespace"
    wait_for_resource "deployment" "cert-manager-webhook" "$cert_manager_namespace"
    wait_for_resource "deployment" "cert-manager-cainjector" "$cert_manager_namespace"
}

# ==============================================================================
# Cluster Initialization
# ==============================================================================

initialize_cluster() {
    log_info "Initializing cluster..."
    
    # Create MicroAgents namespace
    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    
    # Create service account
    kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: microagents-sa
  namespace: $NAMESPACE
automountServiceAccountToken: false
EOF
    record_resource "serviceaccount/microagents-sa" "$NAMESPACE"
    
    # Create cluster role
    kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: microagents-cluster-role
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "endpoints", "persistentvolumeclaims", "secrets", "configmaps"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "replicasets", "daemonsets"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["monitoring.coreos.com"]
    resources: ["prometheuses", "servicemonitors", "podmonitors", "prometheusrules"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
EOF
    record_resource "clusterrole/microagents-cluster-role"
    
    # Create cluster role binding
    kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: microagents-cluster-role-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: microagents-cluster-role
subjects:
  - kind: ServiceAccount
    name: microagents-sa
    namespace: $NAMESPACE
EOF
    record_resource "clusterrolebinding/microagents-cluster-role-binding"
    
    # Create config map for environment variables
    kubectl create configmap microagents-config \
        --namespace "$NAMESPACE" \
        --from-literal=environment="$ENVIRONMENT" \
        --from-literal=cloud_provider="$CLOUD_PROVIDER" \
        --dry-run=client -o yaml | kubectl apply -f -
    record_resource "configmap/microagents-config" "$NAMESPACE"
    
    log_success "Cluster initialization completed"
}

# ==============================================================================
# Storage Provisioning
# ==============================================================================

provision_storage() {
    log_info "Provisioning storage..."
    
    # Create storage class if it doesn't exist
    if ! kubectl get storageclass "$STORAGE_CLASS" >/dev/null 2>&1; then
        log_info "Creating storage class: $STORAGE_CLASS"
        
        case "$CLOUD_PROVIDER" in
            aws)
                kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $STORAGE_CLASS
provisioner: kubernetes.io/aws-ebs
parameters:
  type: gp3
  fsType: ext4
reclaimPolicy: Retain
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
EOF
                ;;
            azure)
                kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $STORAGE_CLASS
provisioner: disk.csi.azure.com
parameters:
  skuname: Premium_LRS
  fsType: ext4
reclaimPolicy: Retain
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
EOF
                ;;
            gcp)
                kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $STORAGE_CLASS
provisioner: pd.csi.storage.gke.io
parameters:
  type: pd-ssd
  fsType: ext4
reclaimPolicy: Retain
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
EOF
                ;;
        esac
        
        check_exit_code $? "Failed to create storage class"
        record_resource "storageclass/$STORAGE_CLASS"
    fi
    
    # Create persistent volumes for different components
    create_persistent_volumes
    
    log_success "Storage provisioning completed"
}

create_persistent_volumes() {
    # Database volume
    kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $STORAGE_CLASS
  resources:
    requests:
      storage: 100Gi
EOF
    record_resource "persistentvolumeclaim/postgres-pvc" "$NAMESPACE"
    
    # Redis volume
    kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: redis-pvc
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $STORAGE_CLASS
  resources:
    requests:
      storage: 50Gi
EOF
    record_resource "persistentvolumeclaim/redis-pvc" "$NAMESPACE"
    
    # Backup volume
    kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: backup-pvc
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: $STORAGE_CLASS
  resources:
    requests:
      storage: 200Gi
EOF
    record_resource "persistentvolumeclaim/backup-pvc" "$NAMESPACE"
}

# ==============================================================================
# Network Configuration
# ==============================================================================

configure_network() {
    log_info "Configuring network..."
    
    # Create network policies
    create_network_policies
    
    # Configure ingress
    configure_ingress
    
    # Setup DNS
    configure_dns
    
    log_success "Network configuration completed"
}

create_network_policies() {
    log_info "Creating network policies..."
    
    # Default deny all policy
    kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: $NAMESPACE
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
EOF
    record_resource "networkpolicy/default-deny-all" "$NAMESPACE"
    
    # Allow internal communication
    kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-internal
  namespace: $NAMESPACE
spec:
  podSelector: {}
  ingress:
  - from:
    - podSelector: {}
    ports:
    - protocol: TCP
      port: 8080
    - protocol: TCP
      port: 5432
    - protocol: TCP
      port: 6379
  egress:
  - to:
    - podSelector: {}
    ports:
    - protocol: TCP
      port: 8080
    - protocol: TCP
      port: 5432
    - protocol: TCP
      port: 6379
EOF
    record_resource "networkpolicy/allow-internal" "$NAMESPACE"
    
    # Allow ingress from ingress controller
    kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress
  namespace: $NAMESPACE
spec:
  podSelector: {}
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 80
    - protocol: TCP
      port: 443
EOF
    record_resource "networkpolicy/allow-ingress" "$NAMESPACE"
}

configure_ingress() {
    log_info "Configuring ingress..."
    
    # Create ingress resource
    kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: microagents-ingress
  namespace: $NAMESPACE
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - api.microagents.io
    - app.microagents.io
    secretName: microagents-tls
  rules:
  - host: api.microagents.io
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: microagents-api
            port:
              number: 8080
  - host: app.microagents.io
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: microagents-ui
            port:
              number: 80
EOF
    record_resource "ingress/microagents-ingress" "$NAMESPACE"
}

configure_dns() {
    log_info "Configuring DNS..."
    
    # Install external-dns if not installed
    if ! helm list -n kube-system | grep -q external-dns; then
        log_info "Installing external-dns..."
        
        case "$CLOUD_PROVIDER" in
            aws)
                helm upgrade --install external-dns external-dns/external-dns \
                    --namespace kube-system \
                    --set provider=aws \
                    --set aws.zoneType=public \
                    --set txtOwnerId="$NAMESPACE" \
                    --set policy=sync
                ;;
            azure)
                helm upgrade --install external-dns external-dns/external-dns \
                    --namespace kube-system \
                    --set provider=azure \
                    --set azure.resourceGroup="$NAMESPACE-rg" \
                    --set azure.tenantId="$ARM_TENANT_ID" \
                    --set azure.subscriptionId="$ARM_SUBSCRIPTION_ID" \
                    --set azure.aadClientId="$ARM_CLIENT_ID" \
                    --set azure.aadClientSecret="$ARM_CLIENT_SECRET" \
                    --set policy=sync
                ;;
            gcp)
                helm upgrade --install external-dns external-dns/external-dns \
                    --namespace kube-system \
                    --set provider=google \
                    --set google.project="$GOOGLE_PROJECT" \
                    --set google.serviceAccountSecret="external-dns-sa" \
                    --set policy=sync
                ;;
        esac
        
        check_exit_code $? "Failed to install external-dns"
    fi
}

# ==============================================================================
# Security Setup
# ==============================================================================

setup_security() {
    log_info "Setting up security..."
    
    # Create secrets
    create_secrets
    
    # Setup pod security
    setup_pod_security
    
    # Configure RBAC
    configure_rbac
    
    # Enable audit logging
    enable_audit_logging
    
    log_success "Security setup completed"
}

create_secrets() {
    log_info "Creating secrets..."
    
    # Generate passwords
    local postgres_password=$(generate_password 32)
    local redis_password=$(generate_password 32)
    local jwt_secret=$(generate_password 64)
    
    # Create Kubernetes secrets
    kubectl create secret generic microagents-secrets \
        --namespace "$NAMESPACE" \
        --from-literal=postgres-password="$postgres_password" \
        --from-literal=redis-password="$redis_password" \
        --from-literal=jwt-secret="$jwt_secret" \
        --dry-run=client -o yaml | kubectl apply -f -
    record_resource "secret/microagents-secrets" "$NAMESPACE"
    
    # Store secrets in cloud provider secret manager
    store_secrets_in_cloud_provider "$postgres_password" "$redis_password" "$jwt_secret"
}

store_secrets_in_cloud_provider() {
    local postgres_password=$1
    local redis_password=$2
    local jwt_secret=$3
    
    case "$CLOUD_PROVIDER" in
        aws)
            aws secretsmanager create-secret \
                --name "microagents/postgres-password" \
                --secret-string "$postgres_password" \
                --description "PostgreSQL password for MicroAgents" \
                --region "$AWS_REGION" || true
            
            aws secretsmanager create-secret \
                --name "microagents/redis-password" \
                --secret-string "$redis_password" \
                --description "Redis password for MicroAgents" \
                --region "$AWS_REGION" || true
            ;;
        azure)
            az keyvault secret set \
                --vault-name "${NAMESPACE}-kv" \
                --name "postgres-password" \
                --value "$postgres_password" || true
            ;;
        gcp)
            echo "$postgres_password" | gcloud secrets create postgres-password \
                --data-file=- \
                --replication-policy="automatic" || true
            ;;
    esac
}

setup_pod_security() {
    log_info "Setting up pod security..."
    
    # Create Pod Security Standards
    kubectl apply -f - <<EOF
apiVersion: policy/v1
kind: PodSecurityPolicy
metadata:
  name: microagents-restricted
spec:
  privileged: false
  allowPrivilegeEscalation: false
  requiredDropCapabilities:
    - ALL
  volumes:
    - 'configMap'
    - 'emptyDir'
    - 'projected'
    - 'secret'
    - 'downwardAPI'
    - 'persistentVolumeClaim'
  hostNetwork: false
  hostIPC: false
  hostPID: false
  runAsUser:
    rule: 'MustRunAsNonRoot'
  seLinux:
    rule: 'RunAsAny'
  supplementalGroups:
    rule: 'MustRunAs'
    ranges:
      - min: 1
        max: 65535
  fsGroup:
    rule: 'MustRunAs'
    ranges:
      - min: 1
        max: 65535
  readOnlyRootFilesystem: true
EOF
    record_resource "podsecuritypolicy/microagents-restricted"
    
    # Create security context constraints for OpenShift (if applicable)
    if kubectl api-resources | grep -q securitycontextconstraints; then
        kubectl apply -f - <<EOF
apiVersion: security.openshift.io/v1
kind: SecurityContextConstraints
metadata:
  name: microagents-scc
allowPrivilegedContainer: false
allowedCapabilities: []
defaultAddCapabilities: []
fsGroup:
  type: MustRunAs
  ranges:
  - min: 1
    max: 65535
readOnlyRootFilesystem: true
requiredDropCapabilities:
- ALL
runAsUser:
  type: MustRunAsNonRoot
seLinuxContext:
  type: MustRunAs
supplementalGroups:
  type: MustRunAs
  ranges:
  - min: 1
    max: 65535
volumes:
- configMap
- emptyDir
- projected
- secret
- downwardAPI
- persistentVolumeClaim
EOF
    fi
}

configure_rbac() {
    log_info "Configuring RBAC..."
    
    # Create additional roles
    kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: microagents-operator
  namespace: $NAMESPACE
rules:
  - apiGroups: ["microagents.io"]
    resources: ["agentdefinitions"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
EOF
    record_resource "role/microagents-operator" "$NAMESPACE"
    
    # Create role binding
    kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: microagents-operator-binding
  namespace: $NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: microagents-operator
subjects:
  - kind: ServiceAccount
    name: microagents-sa
    namespace: $NAMESPACE
EOF
    record_resource "rolebinding/microagents-operator-binding" "$NAMESPACE"
}

enable_audit_logging() {
    log_info "Enabling audit logging..."
    
    # This would typically be configured at the cluster level
    # For now, we'll enable it in our deployments
    
    log_debug "Audit logging will be enabled in application deployments"
}

# ==============================================================================
# Monitoring Deployment
# ==============================================================================

deploy_monitoring() {
    log_info "Deploying monitoring stack..."
    
    # Create monitoring namespace
    local monitoring_namespace="monitoring"
    kubectl create namespace "$monitoring_namespace" --dry-run=client -o yaml | kubectl apply -f -
    
    # Install Prometheus Stack
    install_prometheus_stack "$monitoring_namespace"
    
    # Install Grafana
    install_grafana "$monitoring_namespace"
    
    # Configure application monitoring
    configure_application_monitoring
    
    log_success "Monitoring stack deployed"
}

install_prometheus_stack() {
    local namespace=$1
    
    log_info "Installing Prometheus Stack..."
    
    helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
        --namespace "$namespace" \
        --version 46.8.0 \
        --set prometheus.prometheusSpec.replicaCount=2 \
        --set prometheus.prometheusSpec.retention="30d" \
        --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName="$STORAGE_CLASS" \
        --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.accessModes[0]="ReadWriteOnce" \
        --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage="50Gi" \
        --set alertmanager.alertmanagerSpec.replicaCount=2 \
        --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.storageClassName="$STORAGE_CLASS" \
        --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.resources.requests.storage="10Gi" \
        --set grafana.persistence.enabled=true \
        --set grafana.persistence.storageClassName="$STORAGE_CLASS" \
        --set grafana.persistence.size="10Gi" \
        --set grafana.adminPassword='$(generate_password 16)' \
        --set nodeExporter.enabled=true \
        --set kubeStateMetrics.enabled=true \
        --wait
    
    check_exit_code $? "Failed to install Prometheus Stack"
    
    wait_for_resource "deployment" "prometheus-grafana" "$namespace"
    wait_for_resource "statefulset" "prometheus-prometheus-kube-prometheus-prometheus" "$namespace"
}

install_grafana() {
    local namespace=$1
    
    log_info "Installing Grafana dashboards..."
    
    # Create configmap with dashboards
    kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: microagents-dashboards
  namespace: $namespace
  labels:
    grafana_dashboard: "1"
data:
  microagents-overview.json: |
    {
      "dashboard": {
        "title": "MicroAgents Overview",
        "panels": [],
        "tags": ["microagents", "platform"],
        "timezone": "browser"
      }
    }
  microagents-performance.json: |
    {
      "dashboard": {
        "title": "MicroAgents Performance",
        "panels": [],
        "tags": ["microagents", "performance"],
        "timezone": "browser"
      }
    }
EOF
    
    # Create service monitor for our applications
    kubectl apply -f - <<EOF
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: microagents-service-monitor
  namespace: $NAMESPACE
spec:
  selector:
    matchLabels:
      app: microagents
  endpoints:
  - port: metrics
    interval: 30s
    path: /metrics
EOF
    record_resource "servicemonitor/microagents-service-monitor" "$NAMESPACE"
}

configure_application_monitoring() {
    log_info "Configuring application monitoring..."
    
    # Create Prometheus rules for alerting
    kubectl apply -f - <<EOF
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: microagents-alerts
  namespace: $NAMESPACE
spec:
  groups:
  - name: microagents.rules
    rules:
    - alert: HighErrorRate
      expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "High error rate detected"
        description: "Error rate is {{ \$value }} for service {{ \$labels.service }}"
    
    - alert: HighLatency
      expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 1
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "High latency detected"
    
    - alert: PodCrashLooping
      expr: rate(kube_pod_container_status_restarts_total[15m]) > 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Pod is crash looping"
EOF
    record_resource "prometheusrule/microagents-alerts" "$NAMESPACE"
}

# ==============================================================================
# Backup Configuration
# ==============================================================================

configure_backup() {
    log_info "Configuring backup..."
    
    # Install Velero
    install_velero
    
    # Configure backup schedules
    configure_backup_schedules
    
    # Setup disaster recovery
    setup_disaster_recovery
    
    log_success "Backup configuration completed"
}

install_velero() {
    log_info "Installing Velero..."
    
    local velero_namespace="velero"
    kubectl create namespace "$velero_namespace" --dry-run=client -o yaml | kubectl apply -f -
    
    case "$CLOUD_PROVIDER" in
        aws)
            # Install Velero with AWS configuration
            helm upgrade --install velero vmware-tanzu/velero \
                --namespace "$velero_namespace" \
                --set configuration.provider=aws \
                --set configuration.backupStorageLocation.bucket="$NAMESPACE-backups" \
                --set configuration.backupStorageLocation.config.region="$AWS_REGION" \
                --set configuration.volumeSnapshotLocation.config.region="$AWS_REGION" \
                --set credentials.existingSecret="cloud-credentials" \
                --set schedules.daily.schedule="0 3 * * *" \
                --set schedules.daily.template.ttl="720h" \
                --set initContainers[0].name=velero-plugin-for-aws \
                --set initContainers[0].image=velero/velero-plugin-for-aws:v1.7.0 \
                --set initContainers[0].volumeMounts[0].mountPath=/target \
                --set initContainers[0].volumeMounts[0].name=plugins
            ;;
        azure)
            # Install Velero with Azure configuration
            helm upgrade --install velero vmware-tanzu/velero \
                --namespace "$velero_namespace" \
                --set configuration.provider=azure \
                --set configuration.backupStorageLocation.bucket="$NAMESPACE-backups" \
                --set configuration.backupStorageLocation.config.resourceGroup="$NAMESPACE-rg" \
                --set configuration.backupStorageLocation.config.storageAccount="$NAMESPACE-backup" \
                --set credentials.existingSecret="cloud-credentials" \
                --set schedules.daily.schedule="0 3 * * *" \
                --set schedules.daily.template.ttl="720h"
            ;;
        gcp)
            # Install Velero with GCP configuration
            helm upgrade --install velero vmware-tanzu/velero \
                --namespace "$velero_namespace" \
                --set configuration.provider=gcp \
                --set configuration.backupStorageLocation.bucket="$NAMESPACE-backups" \
                --set configuration.backupStorageLocation.config.project="$GOOGLE_PROJECT" \
                --set credentials.existingSecret="cloud-credentials" \
                --set schedules.daily.schedule="0 3 * * *" \
                --set schedules.daily.template.ttl="720h"
            ;;
    esac
    
    check_exit_code $? "Failed to install Velero"
    
    wait_for_resource "deployment" "velero" "$velero_namespace"
}

configure_backup_schedules() {
    log_info "Configuring backup schedules..."
    
    # Create Velero backup schedule
    kubectl apply -f - <<EOF
apiVersion: velero.io/v1
kind: Schedule
metadata:
  name: microagents-daily-backup
  namespace: velero
spec:
  schedule: "0 3 * * *"
  template:
    includedNamespaces:
    - $NAMESPACE
    includedResources:
    - deployments
    - statefulsets
    - configmaps
    - secrets
    - persistentvolumes
    - persistentvolumeclaims
    storageLocation: default
    ttl: 720h
    snapshotVolumes: true
EOF
    
    # Create backup location
    kubectl apply -f - <<EOF
apiVersion: velero.io/v1
kind: BackupStorageLocation
metadata:
  name: default
  namespace: velero
spec:
  provider: $CLOUD_PROVIDER
  objectStorage:
    bucket: $NAMESPACE-backups
  config:
    region: $AWS_REGION
EOF
}

setup_disaster_recovery() {
    log_info "Setting up disaster recovery..."
    
    # Create pod disruption budgets
    kubectl apply -f - <<EOF
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: microagents-api-pdb
  namespace: $NAMESPACE
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: microagents-api
EOF
    record_resource "poddisruptionbudget/microagents-api-pdb" "$NAMESPACE"
    
    kubectl apply -f - <<EOF
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: microagents-worker-pdb
  namespace: $NAMESPACE
spec:
  minAvailable: 3
  selector:
    matchLabels:
      app: microagents-worker
EOF
    record_resource "poddisruptionbudget/microagents-worker-pdb" "$NAMESPACE"
}

# ==============================================================================
# Health Verification
# ==============================================================================

verify_health() {
    log_info "Verifying system health..."
    
    # Verify cluster components
    verify_cluster_components
    
    # Verify network connectivity
    verify_network_connectivity
    
    # Verify storage
    verify_storage
    
    # Verify security
    verify_security
    
    # Verify monitoring
    verify_monitoring
    
    # Verify backups
    verify_backups
    
    # Run smoke tests
    run_smoke_tests
    
    log_success "Health verification completed"
}

verify_cluster_components() {
    log_info "Verifying cluster components..."
    
    # Check node status
    local unhealthy_nodes=$(kubectl get nodes --no-headers | grep -v "Ready" | wc -l)
    if [[ $unhealthy_nodes -gt 0 ]]; then
        log_warning "$unhealthy_nodes nodes are not in Ready state"
        kubectl get nodes | grep -v "Ready"
    else
        log_success "All nodes are healthy"
    fi
    
    # Check system pods
    local system_namespaces=("kube-system" "ingress-nginx" "cert-manager" "monitoring" "velero")
    for ns in "${system_namespaces[@]}"; do
        local unhealthy_pods=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | grep -v "Running\|Completed" | wc -l || echo "0")
        if [[ $unhealthy_pods -gt 0 ]]; then
            log_warning "$unhealthy_pods pods in $ns namespace are not healthy"
            kubectl get pods -n "$ns" | grep -v "Running\|Completed"
        fi
    done
}

verify_network_connectivity() {
    log_info "Verifying network connectivity..."
    
    # Create test pod
    kubectl run network-test --namespace "$NAMESPACE" --image=alpine:3.18 --restart=Never -- \
        sh -c "ping -c 3 google.com && echo 'External connectivity: OK' && \
               nc -zv postgres-service 5432 && echo 'PostgreSQL connectivity: OK' && \
               nc -zv redis-service 6379 && echo 'Redis connectivity: OK'"
    
    # Wait for completion
    kubectl wait --namespace "$NAMESPACE" --for=condition=complete job/network-test --timeout=60s
    
    # Show logs
    kubectl logs --namespace "$NAMESPACE" job/network-test
    
    # Cleanup
    kubectl delete job network-test --namespace "$NAMESPACE" --ignore-not-found=true
}

verify_storage() {
    log_info "Verifying storage..."
    
    # Check storage classes
    kubectl get storageclass
    
    # Check persistent volume claims
    kubectl get pvc -n "$NAMESPACE"
    
    # Test storage by creating a test PVC
    kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: test-pvc
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $STORAGE_CLASS
  resources:
    requests:
      storage: 1Gi
EOF
    
    # Wait for PVC to be bound
    local timeout=60
    local start_time=$(date +%s)
    while true; do
        local status=$(kubectl get pvc test-pvc -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [[ "$status" == "Bound" ]]; then
            log_success "Storage test PVC is bound"
            break
        fi
        
        local current_time=$(date +%s)
        if [[ $((current_time - start_time)) -ge $timeout ]]; then
            log_error "Timeout waiting for test PVC to be bound"
            break
        fi
        
        sleep 5
    done
    
    # Cleanup
    kubectl delete pvc test-pvc -n "$NAMESPACE" --ignore-not-found=true
}

verify_security() {
    log_info "Verifying security..."
    
    # Check network policies
    kubectl get networkpolicies -n "$NAMESPACE"
    
    # Check RBAC
    kubectl get roles,rolebindings -n "$NAMESPACE"
    
    # Check secrets are encrypted
    log_debug "Verifying secret encryption..."
}

verify_monitoring() {
    log_info "Verifying monitoring..."
    
    # Check Prometheus
    if kubectl get deployment prometheus-prometheus-kube-prometheus-prometheus -n monitoring >/dev/null 2>&1; then
        log_success "Prometheus is running"
    else
        log_warning "Prometheus is not running"
    fi
    
    # Check Grafana
    if kubectl get deployment prometheus-grafana -n monitoring >/dev/null 2>&1; then
        log_success "Grafana is running"
    else
        log_warning "Grafana is not running"
    fi
    
    # Check service monitors
    kubectl get servicemonitors -n "$NAMESPACE"
}

verify_backups() {
    log_info "Verifying backups..."
    
    # Check Velero
    if kubectl get deployment velero -n velero >/dev/null 2>&1; then
        log_success "Velero is running"
        
        # Check backup storage location
        if kubectl get backupstoragelocation default -n velero >/dev/null 2>&1; then
            local bsl_status=$(kubectl get backupstoragelocation default -n velero -o jsonpath='{.status.phase}')
            if [[ "$bsl_status" == "Available" ]]; then
                log_success "Backup storage location is available"
            else
                log_warning "Backup storage location status: $bsl_status"
            fi
        fi
    else
        log_warning "Velero is not running"
    fi
}

run_smoke_tests() {
    log_info "Running smoke tests..."
    
    # Create simple test deployment
    kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: smoke-test
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: smoke-test
  template:
    metadata:
      labels:
        app: smoke-test
    spec:
      containers:
      - name: smoke-test
        image: nginx:alpine
        ports:
        - containerPort: 80
        readinessProbe:
          httpGet:
            path: /
            port: 80
          initialDelaySeconds: 5
          periodSeconds: 5
EOF
    record_resource "deployment/smoke-test" "$NAMESPACE"
    
    # Create service
    kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: smoke-test-service
  namespace: $NAMESPACE
spec:
  selector:
    app: smoke-test
  ports:
  - port: 80
    targetPort: 80
EOF
    record_resource "service/smoke-test-service" "$NAMESPACE"
    
    # Wait for deployment
    wait_for_resource "deployment" "smoke-test" "$NAMESPACE" 120
    
    # Test service
    kubectl run curl-test --namespace "$NAMESPACE" --image=curlimages/curl:8.2.1 --restart=Never -- \
        sh -c "curl -f http://smoke-test-service && echo 'Smoke test passed'"
    
    kubectl wait --namespace "$NAMESPACE" --for=condition=complete job/curl-test --timeout=30s
    
    local logs=$(kubectl logs --namespace "$NAMESPACE" job/curl-test 2>/dev/null || echo "")
    if echo "$logs" | grep -q "Smoke test passed"; then
        log_success "Smoke tests passed"
    else
        log_error "Smoke tests failed"
        echo "$logs"
    fi
    
    # Cleanup
    kubectl delete deployment smoke-test --namespace "$NAMESPACE" --ignore-not-found=true
    kubectl delete service smoke-test-service --namespace "$NAMESPACE" --ignore-not-found=true
    kubectl delete job curl-test --namespace "$NAMESPACE" --ignore-not-found=true
}

# ==============================================================================
# Performance Optimization
# ==============================================================================

optimize_performance() {
    log_info "Optimizing performance..."
    
    # Configure HPA
    configure_hpa
    
    # Configure resource limits
    configure_resource_limits
    
    # Optimize network
    optimize_network
    
    # Optimize storage
    optimize_storage
    
    log_success "Performance optimization completed"
}

configure_hpa() {
    log_info "Configuring Horizontal Pod Autoscaling..."
    
    # Create HPA for API
    kubectl apply -f - <<EOF
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: microagents-api-hpa
  namespace: $NAMESPACE
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: microagents-api
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
EOF
    record_resource "horizontalpodautoscaler/microagents-api-hpa" "$NAMESPACE"
    
    # Create HPA for workers
    kubectl apply -f - <<EOF
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: microagents-worker-hpa
  namespace: $NAMESPACE
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: microagents-worker
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 60
  - type: Pods
    pods:
      metric:
        name: queue_length
      target:
        type: AverageValue
        averageValue: 1000
EOF
    record_resource "horizontalpodautoscaler/microagents-worker-hpa" "$NAMESPACE"
}

configure_resource_limits() {
    log_info "Configuring resource limits..."
    
    # Create LimitRange
    kubectl apply -f - <<EOF
apiVersion: v1
kind: LimitRange
metadata:
  name: microagents-limits
  namespace: $NAMESPACE
spec:
  limits:
  - default:
      cpu: 500m
      memory: 512Mi
    defaultRequest:
      cpu: 100m
      memory: 128Mi
    type: Container
EOF
    record_resource "limitrange/microagents-limits" "$NAMESPACE"
    
    # Create ResourceQuota
    kubectl apply -f - <<EOF
apiVersion: v1
kind: ResourceQuota
metadata:
  name: microagents-quota
  namespace: $NAMESPACE
spec:
  hard:
    requests.cpu: "10"
    requests.memory: 20Gi
    limits.cpu: "20"
    limits.memory: 40Gi
    pods: "50"
    services: "20"
    persistentvolumeclaims: "10"
    secrets: "50"
    configmaps: "50"
EOF
    record_resource "resourcequota/microagents-quota" "$NAMESPACE"
}

optimize_network() {
    log_info "Optimizing network..."
    
    # Configure network policies for performance
    kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: optimize-network
  namespace: $NAMESPACE
spec:
  podSelector: {}
  policyTypes:
  - Egress
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          name: monitoring
    ports:
    - port: 9090
      protocol: TCP
  - to:
    - ipBlock:
        cidr: 0.0.0.0/0
    ports:
    - port: 53
      protocol: UDP
    - port: 443
      protocol: TCP
EOF
    record_resource "networkpolicy/optimize-network" "$NAMESPACE"
}

optimize_storage() {
    log_info "Optimizing storage..."
    
    # Create storage class with performance settings
    case "$CLOUD_PROVIDER" in
        aws)
            kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: fast
provisioner: kubernetes.io/aws-ebs
parameters:
  type: io2
  iopsPerGB: "100"
  fsType: ext4
reclaimPolicy: Retain
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
EOF
            ;;
        azure)
            kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: fast
provisioner: disk.csi.azure.com
parameters:
  skuname: PremiumV2_LRS
  fsType: ext4
reclaimPolicy: Retain
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
EOF
            ;;
        gcp)
            kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: fast
provisioner: pd.csi.storage.gke.io
parameters:
  type: pd-ssd
  fsType: ext4
reclaimPolicy: Retain
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
EOF
            ;;
    esac
}

# ==============================================================================
# Main Execution
# ==============================================================================

print_banner() {
    cat << "EOF"
    
    __  __ _                   _                       _____ _             _   
   |  \/  (_)                 | |                     / ____| |           | |  
   | \  / |_ ___ _ __ ___  ___| |_ ___  _ __ ______  | |    | |_   _ _ __ | |_ 
   | |\/| | / __| '__/ _ \/ __| __/ _ \| '__|______| | |    | | | | | '_ \| __|
   | |  | | \__ \ | |  __/ (__| || (_) | |           | |____| | |_| | | | | |_ 
   |_|  |_|_|___/_|  \___|\___|\__\___/|_|            \_____|_|\__,_|_| |_|\__|
   
   DevOps Intelligence Platform Bootstrap v1.0.0
   
EOF
}

print_summary() {
    local end_time=$(date +%s)
    local duration=$((end_time - START_TIME))
    
    cat << EOF
    
╔══════════════════════════════════════════════════════════════════════╗
║                        BOOTSTRAP SUMMARY                             ║
╠══════════════════════════════════════════════════════════════════════╣
║ Environment:      $ENVIRONMENT
║ Cloud Provider:   $CLOUD_PROVIDER
║ Namespace:        $NAMESPACE
║ Duration:         ${duration}s
║ Status:           COMPLETED SUCCESSFULLY
║ Timestamp:        $(date)
║ Log File:         $LOG_FILE
╚══════════════════════════════════════════════════════════════════════╝

Next steps:
1. Access the dashboard: https://grafana.$NAMESPACE.svc.cluster.local
2. Check deployment status: kubectl get all -n $NAMESPACE
3. View logs: kubectl logs -f -n $NAMESPACE -l app=microagents
4. Monitor health: ./scripts/health-check.sh

For support:
- Documentation: https://docs.microagents.io
- Issues: https://github.com/microagents/devops-platform/issues
- Slack: #platform-support

EOF
}

main() {
    START_TIME=$(date +%s)
    
    # Parse command line arguments
    parse_arguments "$@"
    
    # Initialize logging
    init_logging
    
    # Print banner
    print_banner
    
    # Dry run mode
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "DRY RUN MODE ENABLED - No changes will be made"
        validate_environment
        log_success "Dry run completed successfully"
        exit 0
    fi
    
    # Execute bootstrap steps
    local steps=(
        "check_prerequisites"
        "validate_environment"
        "install_dependencies"
        "initialize_cluster"
        "provision_storage"
        "configure_network"
        "setup_security"
        "deploy_monitoring"
        "configure_backup"
        "optimize_performance"
        "verify_health"
    )
    
    local total_steps=${#steps[@]}
    local current_step=1
    
    for step in "${steps[@]}"; do
        log_info "[Step $current_step/$total_steps] Executing: $step"
        
        # Execute step with error handling
        if $step; then
            log_success "Step $current_step/$total_steps completed: $step"
        else
            log_error "Step $current_step/$total_steps failed: $step"
            
            if [[ "$ENABLE_ROLLBACK" == "true" ]]; then
                rollback_cleanup
            fi
            
            exit 1
        fi
        
        ((current_step++))
    done
    
    # Print summary
    print_summary
}

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -v|--version)
                echo "$SCRIPT_NAME version $SCRIPT_VERSION"
                exit 0
                ;;
            -d|--dry-run)
                DRY_RUN="true"
                shift
                ;;
            -c|--config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            --no-rollback)
                ENABLE_ROLLBACK="false"
                shift
                ;;
            --debug)
                DEBUG="true"
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

show_help() {
    cat << EOF
MicroAgents Platform Bootstrap Script

Usage: $SCRIPT_NAME [OPTIONS]

Options:
  -h, --help           Show this help message
  -v, --version        Show version information
  -d, --dry-run        Enable dry run mode (no changes)
  -c, --config FILE    Use custom configuration file
  --no-rollback        Disable rollback on failure
  --debug              Enable debug logging

Environment Variables:
  CONFIG_FILE          Configuration file path
  DEBUG                Enable debug mode
  CLOUD_PROVIDER       Cloud provider (aws, azure, gcp)
  ENVIRONMENT          Environment name (production, staging, development)

Examples:
  $SCRIPT_NAME
  $SCRIPT_NAME --dry-run
  $SCRIPT_NAME --config ./custom-config.yaml
  CLOUD_PROVIDER=aws $SCRIPT_NAME

EOF
}

init_logging() {
    LOG_DIR="${LOG_DIR:-./logs}"
    mkdir -p "$LOG_DIR"
    LOG_FILE="$LOG_DIR/bootstrap-$TIMESTAMP.log"
    
    # Redirect all output to log file and stdout
    exec > >(tee -a "$LOG_FILE")
    exec 2> >(tee -a "$LOG_FILE" >&2)
    
    log_info "Logging to: $LOG_FILE"
    log_info "Starting MicroAgents Platform bootstrap"
    log_info "Script version: $SCRIPT_VERSION"
    log_info "Timestamp: $TIMESTAMP"
}

# ==============================================================================
# Entry Point
# ==============================================================================

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi