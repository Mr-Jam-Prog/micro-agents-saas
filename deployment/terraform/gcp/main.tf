# deployment/terraform/gcp/main.tf
terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.11"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
  
  backend "gcs" {
    bucket = "microagents-tf-state"
    prefix = "production"
  }
}

# ==============================================================================
# Providers Configuration
# ==============================================================================

provider "google" {
  project = var.project_id
  region  = var.primary_region
  zone    = "${var.primary_region}-a"
  
  default_labels = {
    environment = var.environment
    managed-by  = "terraform"
    cost-center = var.cost_center
    compliance  = "pci-dss-4.0"
  }
}

provider "google-beta" {
  project = var.project_id
  region  = var.primary_region
  zone    = "${var.primary_region}-a"
}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.main.endpoint}"
  token                  = data.google_client_config.current.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.main.master_auth[0].cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = "https://${google_container_cluster.main.endpoint}"
    token                  = data.google_client_config.current.access_token
    cluster_ca_certificate = base64decode(google_container_cluster.main.master_auth[0].cluster_ca_certificate)
  }
}

# ==============================================================================
# Data Sources
# ==============================================================================

data "google_client_config" "current" {}

data "google_compute_zones" "available" {
  region  = var.primary_region
  status  = "UP"
}

data "google_project" "current" {
  project_id = var.project_id
}

# ==============================================================================
# Local Variables
# ==============================================================================

locals {
  # Naming conventions
  name_prefix = "microagents-${var.environment}"
  
  # Primary and DR regions
  primary_region   = var.primary_region
  secondary_region = var.secondary_region
  
  # Zones for regional deployment
  zones = slice(data.google_compute_zones.available.names, 0, min(3, length(data.google_compute_zones.available.names)))
  
  # CIDR blocks
  vpc_cidr = "10.0.0.0/16"
  
  # Subnet CIDRs
  subnets = {
    gke = {
      cidr               = "10.0.1.0/24"
      region            = var.primary_region
      private_ip_google_access = true
    }
    private = {
      cidr               = "10.0.2.0/24"
      region            = var.primary_region
      private_ip_google_access = true
    }
    database = {
      cidr               = "10.0.3.0/24"
      region            = var.primary_region
      private_ip_google_access = true
    }
  }
  
  # GKE configuration
  gke_config = {
    # Release channel for automatic updates
    release_channel = "REGULAR"
    
    # Node pool configuration
    node_pools = {
      system = {
        name               = "system"
        machine_type       = "e2-standard-4"
        min_count          = 3
        max_count          = 10
        initial_node_count = 3
        disk_size_gb       = 100
        disk_type          = "pd-ssd"
        auto_repair        = true
        auto_upgrade       = true
        spot               = false
        labels = {
          node-type = "system"
          workload  = "system"
        }
        taints = []
      }
      
      agent = {
        name               = "agent"
        machine_type       = "e2-standard-8"
        min_count          = 5
        max_count          = 50
        initial_node_count = 5
        disk_size_gb       = 200
        disk_type          = "pd-balanced"
        auto_repair        = true
        auto_upgrade       = true
        spot               = true  # Cost optimization
        labels = {
          node-type = "agent"
          workload  = "agent"
          cost-type = "spot"
        }
        taints = [
          {
            key    = "spot"
            value  = "true"
            effect = "NO_SCHEDULE"
          }
        ]
      }
    }
  }
  
  # Cloud SQL configuration
  cloudsql_config = {
    tier              = "db-custom-4-16384"  # 4 vCPU, 16GB RAM
    disk_size_gb     = 100
    disk_type        = "PD_SSD"
    availability_type = "REGIONAL"  # High availability
    backup_configuration = {
      enabled    = true
      start_time = "03:00"
      location   = var.primary_region
      backup_retention_settings = {
        retained_backups = 30
        retention_unit   = "COUNT"
      }
    }
    database_flags = {
      max_connections           = "1000"
      shared_buffers            = "4GB"
      effective_cache_size      = "12GB"
      maintenance_work_mem      = "2GB"
      checkpoint_completion_target = "0.9"
      default_statistics_target = "100"
      random_page_cost          = "1.1"
      effective_io_concurrency  = "200"
      work_mem                  = "4MB"
      min_wal_size              = "1GB"
      max_wal_size              = "4GB"
    }
  }
  
  # Memorystore Redis configuration
  redis_config = {
    tier               = "STANDARD_HA"
    memory_size_gb     = 10
    replica_count      = 2
    read_replicas_mode = "READ_REPLICAS_ENABLED"
    persistence_config = {
      persistence_mode    = "RDB"
      rdb_snapshot_period = "TWELVE_HOURS"
    }
    maintenance_policy = {
      day                 = "SUNDAY"
      start_time = {
        hours   = 4
        minutes = 0
      }
    }
  }
  
  # Common labels
  common_labels = {
    environment = var.environment
    managed-by  = "terraform"
    project     = "microagents-platform"
    cost-center = var.cost_center
    compliance  = "pci-dss-4.0"
    backup      = "enabled"
    dr-tier     = "tier-3"
  }
}

# ==============================================================================
# VPC Network
# ==============================================================================

resource "google_compute_network" "main" {
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode           = "REGIONAL"
  
  # Enable VPC Flow Logs for security compliance
  enable_ula_internal_ipv6 = false
  mtu                     = 1460
  
  lifecycle {
    ignore_changes = [
      delete_default_routes_on_create,
    ]
  }
}

resource "google_compute_subnetwork" "gke" {
  name                     = "${local.name_prefix}-gke-subnet"
  ip_cidr_range            = local.subnets.gke.cidr
  region                   = local.subnets.gke.region
  network                  = google_compute_network.main.id
  private_ip_google_access = local.subnets.gke.private_ip_google_access
  
  # Secondary IP ranges for GKE pods and services
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.1.0.0/16"
  }
  
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.2.0.0/20"
  }
  
  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_subnetwork" "private" {
  name                     = "${local.name_prefix}-private-subnet"
  ip_cidr_range            = local.subnets.private.cidr
  region                   = local.subnets.private.region
  network                  = google_compute_network.main.id
  private_ip_google_access = local.subnets.private.private_ip_google_access
  
  purpose       = "PRIVATE"
  role          = "ACTIVE"
}

resource "google_compute_subnetwork" "database" {
  name                     = "${local.name_prefix}-database-subnet"
  ip_cidr_range            = local.subnets.database.cidr
  region                   = local.subnets.database.region
  network                  = google_compute_network.main.id
  private_ip_google_access = local.subnets.database.private_ip_google_access
  
  purpose       = "PRIVATE"
  role          = "ACTIVE"
}

# Cloud Router and NAT for private GKE nodes
resource "google_compute_router" "main" {
  name    = "${local.name_prefix}-router"
  region  = var.primary_region
  network = google_compute_network.main.id
  
  bgp {
    asn = 64514
  }
}

resource "google_compute_router_nat" "main" {
  name                               = "${local.name_prefix}-nat"
  router                             = google_compute_router.main.name
  region                             = google_compute_router.main.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
  
  # Enable dynamic port allocation
  min_ports_per_vm = 64
}

# VPC Firewall Rules
resource "google_compute_firewall" "allow_health_checks" {
  name    = "${local.name_prefix}-allow-health-checks"
  network = google_compute_network.main.name
  
  allow {
    protocol = "tcp"
    ports    = ["80", "443", "8080", "8443"]
  }
  
  source_ranges = ["130.211.0.0/22", "35.191.0.0/16", "209.85.152.0/22", "209.85.204.0/22"]
  target_tags   = ["gke-node", "load-balancer-backend"]
  
  direction = "INGRESS"
}

resource "google_compute_firewall" "allow_ssh_bastion" {
  name    = "${local.name_prefix}-allow-ssh-bastion"
  network = google_compute_network.main.name
  
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  
  source_ranges = var.admin_ip_ranges
  target_tags   = ["bastion"]
  
  direction = "INGRESS"
}

resource "google_compute_firewall" "deny_all" {
  name    = "${local.name_prefix}-deny-all"
  network = google_compute_network.main.name
  
  deny {
    protocol = "all"
  }
  
  source_ranges = ["0.0.0.0/0"]
  priority      = 65534
  
  direction = "INGRESS"
}

# ==============================================================================
# GKE Cluster
# ==============================================================================

resource "google_container_cluster" "main" {
  name     = "${local.name_prefix}-gke"
  location = var.primary_region
  
  # Enable Autopilot mode for managed operations
  enable_autopilot = var.enable_autopilot
  
  # Release channel for automatic updates
  release_channel {
    channel = local.gke_config.release_channel
  }
  
  # Network configuration
  network    = google_compute_network.main.name
  subnetwork = google_compute_subnetwork.gke.name
  
  # Private cluster configuration
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
    master_ipv4_cidr_block  = "172.16.0.0/28"
    master_global_access_config {
      enabled = true
    }
  }
  
  # IP allocation policy for pods and services
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  
  # Master authorized networks for access control
  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = google_compute_subnetwork.gke.ip_cidr_range
      display_name = "GKE Subnet"
    }
    
    dynamic "cidr_blocks" {
      for_each = var.admin_ip_ranges
      content {
        cidr_block   = cidr_blocks.value
        display_name = "Admin Access"
      }
    }
  }
  
  # Security features
  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }
  
  enable_shielded_nodes = true
  enable_confidential_nodes = var.enable_confidential_computing
  
  # Workload Identity for IAM integration
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
  
  # Monitoring and logging
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS", "APISERVER", "CONTROLLER_MANAGER", "SCHEDULER"]
    managed_prometheus {
      enabled = true
    }
  }
  
  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }
  
  # Maintenance policy
  maintenance_policy {
    recurring_window {
      start_time = "2024-01-01T02:00:00Z"
      end_time   = "2024-01-01T04:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SU"
    }
  }
  
  # Database encryption
  database_encryption {
    state    = "ENCRYPTED"
    key_name = google_kms_crypto_key.gke.self_link
  }
  
  # Addons
  addons_config {
    # HTTP Load Balancing
    http_load_balancing {
      disabled = false
    }
    
    # Horizontal Pod Autoscaling
    horizontal_pod_autoscaling {
      disabled = false
    }
    
    # Network Policy (Calico)
    network_policy_config {
      disabled = false
    }
    
    # Cloud Run for Anthos
    cloudrun_config {
      disabled = true
    }
    
    # Config Connector
    config_connector_config {
      enabled = false
    }
  }
  
  # Vertical Pod Autoscaling
  vertical_pod_autoscaling {
    enabled = true
  }
  
  # Node pools (if not using Autopilot)
  dynamic "node_pool" {
    for_each = var.enable_autopilot ? [] : [1]
    content {
      name               = local.gke_config.node_pools.system.name
      initial_node_count = local.gke_config.node_pools.system.initial_node_count
      
      management {
        auto_repair  = local.gke_config.node_pools.system.auto_repair
        auto_upgrade = local.gke_config.node_pools.system.auto_upgrade
      }
      
      node_config {
        machine_type = local.gke_config.node_pools.system.machine_type
        disk_size_gb = local.gke_config.node_pools.system.disk_size_gb
        disk_type    = local.gke_config.node_pools.system.disk_type
        spot         = local.gke_config.node_pools.system.spot
        
        # Shielded VMs for security
        shielded_instance_config {
          enable_secure_boot          = true
          enable_integrity_monitoring = true
          enable_vtpm                 = true
        }
        
        # Workload Identity
        workload_metadata_config {
          mode = "GKE_METADATA"
        }
        
        labels = local.gke_config.node_pools.system.labels
        taints = local.gke_config.node_pools.system.taints
        
        # Service account with minimal permissions
        service_account = google_service_account.gke_node.email
        oauth_scopes = [
          "https://www.googleapis.com/auth/cloud-platform"
        ]
      }
      
      autoscaling {
        min_node_count = local.gke_config.node_pools.system.min_count
        max_node_count = local.gke_config.node_pools.system.max_count
      }
    }
  }
  
  # Network policy
  network_policy {
    enabled  = true
    provider = "CALICO"
  }
  
  # Resource usage export
  resource_usage_export_config {
    enable_network_egress_metering = true
    enable_resource_consumption_metering = true
    
    bigquery_destination {
      dataset_id = google_bigquery_dataset.usage_export.dataset_id
    }
  }
  
  # Default max pods per node
  default_max_pods_per_node = 110
  
  lifecycle {
    ignore_changes = [
      node_pool,
      vertical_pod_autoscaling[0].enabled,
    ]
  }
}

# Additional node pool for agent workloads (if not using Autopilot)
resource "google_container_node_pool" "agent" {
  count = var.enable_autopilot ? 0 : 1
  
  name     = local.gke_config.node_pools.agent.name
  location = var.primary_region
  cluster  = google_container_cluster.main.name
  
  initial_node_count = local.gke_config.node_pools.agent.initial_node_count
  
  management {
    auto_repair  = local.gke_config.node_pools.agent.auto_repair
    auto_upgrade = local.gke_config.node_pools.agent.auto_upgrade
  }
  
  node_config {
    machine_type = local.gke_config.node_pools.agent.machine_type
    disk_size_gb = local.gke_config.node_pools.agent.disk_size_gb
    disk_type    = local.gke_config.node_pools.agent.disk_type
    spot         = local.gke_config.node_pools.agent.spot
    
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
      enable_vtpm                 = true
    }
    
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    
    labels = local.gke_config.node_pools.agent.labels
    taints = local.gke_config.node_pools.agent.taints
    
    service_account = google_service_account.gke_node.email
    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
  
  autoscaling {
    min_node_count = local.gke_config.node_pools.agent.min_count
    max_node_count = local.gke_config.node_pools.agent.max_count
  }
  
  lifecycle {
    ignore_changes = [
      initial_node_count,
    ]
  }
}

# ==============================================================================
# Cloud SQL (PostgreSQL)
# ==============================================================================

resource "google_sql_database_instance" "main" {
  name             = "${local.name_prefix}-postgres"
  database_version = "POSTGRES_15"
  region           = var.primary_region
  
  # High availability configuration
  settings {
    tier              = local.cloudsql_config.tier
    disk_size         = local.cloudsql_config.disk_size_gb
    disk_type         = local.cloudsql_config.disk_type
    availability_type = local.cloudsql_config.availability_type
    
    # Backup configuration
    backup_configuration {
      enabled                        = local.cloudsql_config.backup_configuration.enabled
      start_time                     = local.cloudsql_config.backup_configuration.start_time
      location                       = local.cloudsql_config.backup_configuration.location
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = local.cloudsql_config.backup_configuration.backup_retention_settings.retained_backups
        retention_unit   = local.cloudsql_config.backup_configuration.backup_retention_settings.retention_unit
      }
    }
    
    # Database flags for performance tuning
    dynamic "database_flags" {
      for_each = local.cloudsql_config.database_flags
      content {
        name  = database_flags.key
        value = database_flags.value
      }
    }
    
    # IP configuration
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.main.id
      
      # Authorized networks for Cloud SQL Auth Proxy
      authorized_networks {
        name  = "gke-subnet"
        value = google_compute_subnetwork.gke.ip_cidr_range
      }
      
      # Enable PSC for private service access
      enable_private_path_for_google_cloud_services = true
    }
    
    # Maintenance window
    maintenance_window {
      day  = 1  # Monday
      hour = 4  # 4 AM
    }
    
    # Disk autoscaling
    disk_autoresize       = true
    disk_autoresize_limit = 500  # GB
    
    # Insights
    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = false
      record_client_address   = false
    }
  }
  
  # Deletion protection
  deletion_protection = true
  
  depends_on = [
    google_service_networking_connection.private_vpc_connection
  ]
}

resource "google_sql_database" "main" {
  name     = "microagents"
  instance = google_sql_database_instance.main.name
  charset  = "UTF8"
  collation = "en_US.UTF8"
}

resource "google_sql_user" "main" {
  name     = "microagents_app"
  instance = google_sql_database_instance.main.name
  password = random_password.postgresql_app.result
  
  deletion_policy = "ABANDON"
}

# Private Service Access for Cloud SQL
resource "google_compute_global_address" "private_ip_alloc" {
  name          = "${local.name_prefix}-private-ip-alloc"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]
}

# ==============================================================================
# Memorystore Redis
# ==============================================================================

resource "google_redis_instance" "main" {
  name               = "${local.name_prefix}-redis"
  tier               = local.redis_config.tier
  memory_size_gb     = local.redis_config.memory_size_gb
  region             = var.primary_region
  replica_count      = local.redis_config.replica_count
  read_replicas_mode = local.redis_config.read_replicas_mode
  
  # High availability
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  auth_enabled            = true
  
  # Persistence
  persistence_config {
    persistence_mode    = local.redis_config.persistence_config.persistence_mode
    rdb_snapshot_period = local.redis_config.persistence_config.rdb_snapshot_period
  }
  
  # Maintenance window
  maintenance_policy {
    day                 = local.redis_config.maintenance_policy.day
    start_time {
      hours   = local.redis_config.maintenance_policy.start_time.hours
      minutes = local.redis_config.maintenance_policy.start_time.minutes
    }
  }
  
  # Network configuration
  authorized_network = google_compute_network.main.id
  
  # Connect mode
  connect_mode = "PRIVATE_SERVICE_ACCESS"
  
  # Redis configuration
  redis_configs = {
    maxmemory-policy       = "allkeys-lru"
    notify-keyspace-events = "gxE"
    timeout                = "300"
    tcp-keepalive          = "300"
  }
  
  # Labels for cost tracking
  labels = local.common_labels
  
  depends_on = [
    google_service_networking_connection.private_vpc_connection
  ]
}

# Redis auth string
resource "random_password" "redis_auth" {
  length  = 32
  special = true
}

# ==============================================================================
# Cloud Storage
# ==============================================================================

resource "google_storage_bucket" "backup" {
  name                        = "${var.project_id}-microagents-backup"
  location                    = var.primary_region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false
  
  # Versioning for backup recovery
  versioning {
    enabled = true
  }
  
  # Lifecycle rules for cost optimization
  lifecycle_rule {
    condition {
      age = 30  # days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
  
  lifecycle_rule {
    condition {
      age = 90  # days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }
  
  lifecycle_rule {
    condition {
      age = 365  # days
    }
    action {
      type = "Delete"
    }
  }
  
  # Encryption
  encryption {
    default_kms_key_name = google_kms_crypto_key.storage.self_link
  }
  
  # Logging
  logging {
    log_bucket = google_storage_bucket.logs.name
  }
  
  # Retention policy for compliance
  retention_policy {
    retention_period = 31536000  # 1 year in seconds
  }
  
  labels = local.common_labels
}

resource "google_storage_bucket" "logs" {
  name                        = "${var.project_id}-microagents-logs"
  location                    = var.primary_region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false
  
  labels = local.common_labels
}

resource "google_storage_bucket" "static" {
  name                        = "${var.project_id}-microagents-static"
  location                    = var.primary_region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false
  
  # CORS configuration for web assets
  cors {
    origin          = ["https://${var.domain_name}", "https://*.${var.domain_name}"]
    method          = ["GET", "HEAD", "OPTIONS"]
    response_header = ["*"]
    max_age_seconds = 3600
  }
  
  # Website configuration
  website {
    main_page_suffix = "index.html"
    not_found_page   = "404.html"
  }
  
  labels = local.common_labels
}

# IAM bindings for storage
resource "google_storage_bucket_iam_binding" "backup_admin" {
  bucket = google_storage_bucket.backup.name
  role   = "roles/storage.admin"
  
  members = [
    "serviceAccount:${google_service_account.gke_node.email}",
    "group:${var.admin_group_email}",
  ]
}

# ==============================================================================
# Cloud CDN and Load Balancing
# ==============================================================================

# Backend service for GKE
resource "google_compute_backend_service" "main" {
  name        = "${local.name_prefix}-backend"
  port_name   = "http"
  protocol    = "HTTP"
  timeout_sec = 30
  
  # Health checks
  health_checks = [google_compute_health_check.main.id]
  
  # Backend configuration
  backend {
    group = google_compute_region_instance_group_manager.gke_nodes.instance_group
    balancing_mode = "UTILIZATION"
    capacity_scaler = 1.0
  }
  
  # Session affinity
  session_affinity = "GENERATED_COOKIE"
  affinity_cookie_ttl_sec = 86400
  
  # CDN configuration
  enable_cdn = true
  cdn_policy {
    cache_mode = "CACHE_ALL_STATIC"
    default_ttl = 3600
    max_ttl     = 86400
    client_ttl  = 3600
    negative_caching = true
    serve_while_stale = 86400
    
    cache_key_policy {
      include_host         = true
      include_protocol     = true
      include_query_string = false
      query_string_blacklist = ["session", "token"]
    }
  }
  
  # Security policy
  security_policy = google_compute_security_policy.main.id
  
  # Logging
  log_config {
    enable = true
    sample_rate = 0.1
  }
}

# Global forwarding rule
resource "google_compute_global_forwarding_rule" "main" {
  name       = "${local.name_prefix}-https"
  target     = google_compute_target_https_proxy.main.id
  port_range = "443"
  ip_address = google_compute_global_address.main.address
  
  labels = local.common_labels
}

# Target HTTPS proxy
resource "google_compute_target_https_proxy" "main" {
  name    = "${local.name_prefix}-https-proxy"
  url_map = google_compute_url_map.main.id
  
  ssl_certificates = [google_compute_managed_ssl_certificate.main.id]
  
  # Security settings
  quic_override = "ENABLE"
}

# URL map
resource "google_compute_url_map" "main" {
  name            = "${local.name_prefix}-url-map"
  default_service = google_compute_backend_service.main.id
  
  # Host and path rules
  host_rule {
    hosts        = ["${var.domain_name}", "*.${var.domain_name}"]
    path_matcher = "allpaths"
  }
  
  path_matcher {
    name            = "allpaths"
    default_service = google_compute_backend_service.main.id
    
    path_rule {
      paths   = ["/api/*"]
      service = google_compute_backend_service.main.id
    }
    
    path_rule {
      paths   = ["/static/*"]
      service = google_compute_backend_bucket.static.id
    }
  }
}

# Managed SSL certificate
resource "google_compute_managed_ssl_certificate" "main" {
  name = "${local.name_prefix}-ssl-cert"
  
  managed {
    domains = [
      var.domain_name,
      "*.${var.domain_name}",
      "api.${var.domain_name}",
      "app.${var.domain_name}"
    ]
  }
}

# Global IP address
resource "google_compute_global_address" "main" {
  name        = "${local.name_prefix}-global-ip"
  description = "Global IP address for load balancer"
  
  labels = local.common_labels
}

# Health check
resource "google_compute_health_check" "main" {
  name = "${local.name_prefix}-health-check"
  
  timeout_sec         = 5
  check_interval_sec  = 30
  unhealthy_threshold = 2
  
  http_health_check {
    port               = 8080
    request_path       = "/health"
    proxy_header       = "NONE"
    response           = "200"
  }
}

# Backend bucket for static content
resource "google_compute_backend_bucket" "static" {
  name        = "${local.name_prefix}-static-backend"
  bucket_name = google_storage_bucket.static.name
  enable_cdn  = true
  
  cdn_policy {
    cache_mode = "CACHE_ALL_STATIC"
    default_ttl = 3600
    max_ttl     = 86400
    client_ttl  = 3600
  }
}

# Instance group for GKE nodes
resource "google_compute_region_instance_group_manager" "gke_nodes" {
  name = "${local.name_prefix}-gke-nodes"
  region = var.primary_region
  
  base_instance_name = "gke-node"
  target_size       = 3
  
  version {
    instance_template = google_compute_instance_template.gke_node.id
  }
  
  named_port {
    name = "http"
    port = 8080
  }
  
  auto_healing_policies {
    health_check      = google_compute_health_check.main.id
    initial_delay_sec = 300
  }
}

# Instance template for GKE nodes
resource "google_compute_instance_template" "gke_node" {
  name         = "${local.name_prefix}-gke-node-template"
  machine_type = "e2-medium"
  
  disk {
    source_image = "projects/cos-cloud/global/images/family/cos-stable"
    disk_type    = "pd-ssd"
    disk_size_gb = 100
  }
  
  network_interface {
    network    = google_compute_network.main.id
    subnetwork = google_compute_subnetwork.gke.name
  }
  
  metadata = {
    google-logging-enabled    = "true"
    google-monitoring-enabled = "true"
  }
  
  service_account {
    email  = google_service_account.gke_node.email
    scopes = ["cloud-platform"]
  }
  
  tags = ["gke-node", "load-balancer-backend"]
}

# ==============================================================================
# Cloud Armor (WAF)
# ==============================================================================

resource "google_compute_security_policy" "main" {
  name        = "${local.name_prefix}-security-policy"
  description = "Cloud Armor security policy for MicroAgents Platform"
  
  # Adaptive Protection for DDoS mitigation
  adaptive_protection_config {
    layer_7_ddos_defense_config {
      enable = true
      rule_visibility = "STANDARD"
    }
  }
  
  # Default rule (allow all)
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default rule, allow all traffic"
  }
  
  # Rate limiting rule
  rule {
    action   = "throttle"
    priority = 1000
    match {
      expr {
        expression = "origin.region_code == \"EU\" && request.headers[\"user-agent\"].contains(\"Bot\")"
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(403)"
      enforce_on_key   = "IP"
      rate_limit_threshold {
        count        = 1000
        interval_sec = 60
      }
    }
    description = "Rate limit for EU bots"
  }
  
  # SQL injection protection
  rule {
    action   = "deny(403)"
    priority = 900
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('sqli-stable')"
      }
    }
    description = "SQL injection protection"
  }
  
  # XSS protection
  rule {
    action   = "deny(403)"
    priority = 800
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('xss-stable')"
      }
    }
    description = "XSS protection"
  }
  
  # IP blacklist
  rule {
    action   = "deny(403)"
    priority = 100
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = var.blocked_ip_ranges
      }
    }
    description = "Block known malicious IPs"
  }
  
  # Recaptcha for sensitive endpoints
  rule {
    action   = "redirect"
    priority = 500
    match {
      expr {
        expression = "request.path.matches(\"^/admin.*\")"
      }
    }
    redirect_options {
      type = "GOOGLE_RECAPTCHA"
    }
    description = "Recaptcha for admin endpoints"
  }
}

# ==============================================================================
# IAM Configuration
# ==============================================================================

# Service accounts
resource "google_service_account" "gke_node" {
  account_id   = "${local.name_prefix}-gke-node"
  display_name = "GKE Node Service Account"
  description  = "Service account for GKE nodes with minimal permissions"
}

resource "google_service_account" "workload" {
  account_id   = "${local.name_prefix}-workload"
  display_name = "Workload Identity Service Account"
  description  = "Service account for workload identity in GKE"
}

# IAM roles for GKE node service account
resource "google_project_iam_member" "gke_node_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.gke_node.email}"
}

resource "google_project_iam_member" "gke_node_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.gke_node.email}"
}

resource "google_project_iam_member" "gke_node_monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.gke_node.email}"
}

resource "google_project_iam_member" "gke_node_resource_metadata" {
  project = var.project_id
  role    = "roles/stackdriver.resourceMetadata.writer"
  member  = "serviceAccount:${google_service_account.gke_node.email}"
}

resource "google_project_iam_member" "gke_node_gcr" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.gke_node.email}"
}

# Workload Identity IAM binding
resource "google_service_account_iam_binding" "workload_identity" {
  service_account_id = google_service_account.workload.name
  role               = "roles/iam.workloadIdentityUser"
  
  members = [
    "serviceAccount:${var.project_id}.svc.id.goog[default/microagents-sa]",
    "serviceAccount:${var.project_id}.svc.id.goog[kube-system/external-dns]",
    "serviceAccount:${var.project_id}.svc.id.goog[kube-system/cert-manager]",
  ]
}

# IAM custom role for platform engineers
resource "google_project_iam_custom_role" "platform_engineer" {
  role_id     = "platformEngineer"
  title       = "Platform Engineer"
  description = "Custom role for platform engineers with necessary permissions"
  stage       = "GA"
  
  permissions = [
    "container.clusters.get",
    "container.clusters.list",
    "container.operations.get",
    "container.operations.list",
    "container.pods.get",
    "container.pods.list",
    "container.services.get",
    "container.services.list",
    "logging.logEntries.list",
    "monitoring.timeSeries.list",
    "resourcemanager.projects.get",
    "storage.buckets.get",
    "storage.buckets.list",
    "storage.objects.get",
    "storage.objects.list",
  ]
}

# IAM binding for platform engineers
resource "google_project_iam_binding" "platform_engineers" {
  project = var.project_id
  role    = google_project_iam_custom_role.platform_engineer.id
  
  members = [
    "group:${var.admin_group_email}",
  ]
}

# ==============================================================================
# Stackdriver Monitoring (Cloud Operations)
# ==============================================================================

# Monitoring workspace
resource "google_monitoring_monitored_project" "main" {
  metrics_scope = var.project_id
}

# Alert policies
resource "google_monitoring_alert_policy" "gke_pod_crash" {
  display_name = "GKE Pod CrashLoopBackOff"
  combiner     = "OR"
  
  conditions {
    display_name = "Pod in CrashLoopBackOff"
    
    condition_monitoring_query_language {
      query = <<-EOT
        fetch k8s_container
        | metric 'kubernetes.io/container/restart_count'
        | filter (resource.cluster_name == '${google_container_cluster.main.name}')
        | align rate(1m)
        | every 1m
        | group_by [resource.pod_name],
            [value_restart_count_aggregate: aggregate(value.restart_count)]
        | condition val() > 3 '1'
      EOT
      duration = "60s"
      trigger {
        count = 1
      }
    }
  }
  
  notification_channels = [
    google_monitoring_notification_channel.email.id,
    google_monitoring_notification_channel.slack.id,
  ]
  
  documentation {
    content = "A pod in the ${google_container_cluster.main.name} cluster is restarting frequently. Check pod logs for errors."
  }
}

resource "google_monitoring_alert_policy" "cloudsql_cpu" {
  display_name = "Cloud SQL High CPU"
  combiner     = "OR"
  
  conditions {
    display_name = "Cloud SQL CPU > 80%"
    
    condition_threshold {
      filter     = "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" AND resource.type=\"cloudsql_database\" AND resource.label.\"database_id\"=\"${google_sql_database_instance.main.name}\""
      duration   = "300s"
      comparison = "COMPARISON_GT"
      threshold_value = 0.8
      
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
  
  notification_channels = [
    google_monitoring_notification_channel.email.id,
  ]
  
  documentation {
    content = "Cloud SQL instance ${google_sql_database_instance.main.name} is experiencing high CPU utilization."
  }
}

resource "google_monitoring_alert_policy" "redis_memory" {
  display_name = "Redis High Memory"
  combiner     = "OR"
  
  conditions {
    display_name = "Redis Memory > 85%"
    
    condition_threshold {
      filter     = "metric.type=\"redis.googleapis.com/stats/memory/usage_ratio\" AND resource.type=\"redis_instance\" AND resource.label.\"instance_id\"=\"${google_redis_instance.main.name}\""
      duration   = "300s"
      comparison = "COMPARISON_GT"
      threshold_value = 0.85
      
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
  
  notification_channels = [
    google_monitoring_notification_channel.email.id,
  ]
  
  documentation {
    content = "Redis instance ${google_redis_instance.main.name} memory usage is above 85%."
  }
}

# Uptime checks
resource "google_monitoring_uptime_check_config" "api" {
  display_name = "API Uptime Check"
  timeout      = "10s"
  
  http_check {
    path         = "/health"
    port         = 8080
    use_ssl      = true
    validate_ssl = true
  }
  
  monitored_resource {
    type = "uptime_url"
    labels = {
      host       = var.domain_name
      project_id = var.project_id
    }
  }
  
  content_matchers {
    content = "200"
    matcher = "MATCHES_REGEX"
  }
  
  checker_type = "STATIC_IP_CHECKERS"
}

# Notification channels
resource "google_monitoring_notification_channel" "email" {
  display_name = "Email Alerts"
  type         = "email"
  
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_notification_channel" "slack" {
  display_name = "Slack Alerts"
  type         = "slack"
  
  labels = {
    channel_name = var.slack_channel_name
  }
  
  sensitive_labels {
    auth_token = var.slack_auth_token
  }
}

# Dashboard
resource "google_monitoring_dashboard" "main" {
  dashboard_json = <<EOF
{
  "displayName": "MicroAgents Platform Dashboard",
  "gridLayout": {
    "columns": "2",
    "widgets": [
      {
        "title": "GKE Cluster Health",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "metric.type=\"kubernetes.io/container/uptime\" resource.type=\"k8s_container\"",
                  "aggregation": {
                    "perSeriesAligner": "ALIGN_RATE"
                  }
                }
              }
            }
          ]
        }
      },
      {
        "title": "Cloud SQL CPU",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" resource.type=\"cloudsql_database\"",
                  "aggregation": {
                    "perSeriesAligner": "ALIGN_MEAN"
                  }
                }
              }
            }
          ]
        }
      }
    ]
  }
}
EOF
}

# ==============================================================================
# Billing Alerts and Cost Management
# ==============================================================================

# Billing budget
resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "Monthly Budget - MicroAgents Platform"
  
  budget_filter {
    projects = ["projects/${var.project_id}"]
  }
  
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget)
    }
  }
  
  threshold_rules {
    threshold_percent = 0.5
    spend_basis      = "CURRENT_SPEND"
  }
  
  threshold_rules {
    threshold_percent = 0.8
    spend_basis      = "CURRENT_SPEND"
  }
  
  threshold_rules {
    threshold_percent = 0.95
    spend_basis      = "CURRENT_SPEND"
  }
  
  threshold_rules {
    threshold_percent = 1.0
    spend_basis      = "FORECASTED_SPEND"
  }
  
  all_updates_rule {
    monitoring_notification_channels = [
      google_monitoring_notification_channel.email.id
    ]
    disable_default_iam_recipients = false
  }
}

# Billing export to BigQuery
resource "google_bigquery_dataset" "billing_export" {
  dataset_id    = "billing_export"
  friendly_name = "Billing Export"
  description   = "Dataset for GCP billing export"
  location      = "US"
  
  default_table_expiration_ms = 2592000000  # 30 days
  
  labels = local.common_labels
}

resource "google_bigquery_table" "billing_export" {
  dataset_id = google_bigquery_dataset.billing_export.dataset_id
  table_id   = "billing_data"
  
  time_partitioning {
    type = "MONTH"
  }
  
  labels = local.common_labels
}

# Cost recommendation insights
resource "google_recommender_recommendation" "cost_optimization" {
  provider = google-beta
  
  recommender = "google.compute.instance.MachineTypeRecommender"
  location    = "global"
  parent      = "projects/${var.project_id}"
  
  description = "Cost optimization recommendations"
}

# ==============================================================================
# VPC Service Controls
# ==============================================================================

resource "google_access_context_manager_service_perimeter" "main" {
  parent = "accessPolicies/${google_access_context_manager_access_policy.main.name}"
  name   = "accessPolicies/${google_access_context_manager_access_policy.main.name}/servicePerimeters/${local.name_prefix}-perimeter"
  title  = "${local.name_prefix}-perimeter"
  
  # Perimeter type
  perimeter_type = "PERIMETER_TYPE_REGULAR"
  
  # Status configuration
  status {
    restricted_services = [
      "bigquery.googleapis.com",
      "storage.googleapis.com",
      "bigtable.googleapis.com",
      "sqladmin.googleapis.com",
    ]
    
    resources = [
      "projects/${var.project_id}"
    ]
    
    access_levels = [
      google_access_context_manager_access_level.main.name
    ]
    
    # Ingress policies
    ingress_policies {
      ingress_from {
        identity_type = "IDENTITY_TYPE_UNSPECIFIED"
        sources {
          access_level = google_access_context_manager_access_level.main.name
        }
      }
      
      ingress_to {
        resources = ["*"]
        operations {
          service_name = "*"
          method_selectors {
            method = "*"
          }
        }
      }
    }
    
    # Egress policies
    egress_policies {
      egress_from {
        identity_type = "IDENTITY_TYPE_UNSPECIFIED"
      }
      
      egress_to {
        resources = ["*"]
        operations {
          service_name = "*"
          method_selectors {
            method = "*"
          }
        }
      }
    }
  }
  
  lifecycle {
    ignore_changes = [status[0].resources]
  }
}

resource "google_access_context_manager_access_policy" "main" {
  parent = "organizations/${var.organization_id}"
  title  = "${local.name_prefix}-access-policy"
}

resource "google_access_context_manager_access_level" "main" {
  parent = "accessPolicies/${google_access_context_manager_access_policy.main.name}"
  name   = "accessPolicies/${google_access_context_manager_access_policy.main.name}/accessLevels/${local.name_prefix}-access"
  title  = "${local.name_prefix}-access"
  
  basic {
    conditions {
      device_policy {
        require_screen_lock = true
        os_constraints {
          os_type = "DESKTOP_CHROME_OS"
        }
      }
      regions = [
        var.primary_region,
        var.secondary_region,
      ]
    }
  }
}

# ==============================================================================
# Security Command Center
# ==============================================================================

resource "google_security_center_source" "main" {
  display_name = "MicroAgents Platform Security Findings"
  description  = "Security findings from MicroAgents Platform"
  organization = var.organization_id
}

resource "google_security_center_notification_config" "main" {
  config_id    = "${local.name_prefix}-security-notifications"
  organization = var.organization_id
  description  = "Security notification config for MicroAgents Platform"
  
  pubsub_topic = google_pubsub_topic.security_alerts.id
  
  streaming_config {
    filter = "state=\"ACTIVE\" AND severity=\"HIGH OR CRITICAL\""
  }
}

resource "google_pubsub_topic" "security_alerts" {
  name = "${local.name_prefix}-security-alerts"
  
  message_retention_duration = "86600s"  # 1 day
  
  labels = local.common_labels
}

# ==============================================================================
# KMS Keys
# ==============================================================================

resource "google_kms_key_ring" "main" {
  name     = "${local.name_prefix}-keyring"
  location = var.primary_region
  
  labels = local.common_labels
}

resource "google_kms_crypto_key" "gke" {
  name     = "gke-encryption-key"
  key_ring = google_kms_key_ring.main.id
  
  purpose = "ENCRYPT_DECRYPT"
  
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE"
  }
  
  rotation_period = "7776000s"  # 90 days
  
  labels = local.common_labels
}

resource "google_kms_crypto_key" "storage" {
  name     = "storage-encryption-key"
  key_ring = google_kms_key_ring.main.id
  
  purpose = "ENCRYPT_DECRYPT"
  
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "HSM"
  }
  
  rotation_period = "7776000s"  # 90 days
  
  labels = local.common_labels
}

resource "google_kms_crypto_key" "secrets" {
  name     = "secrets-encryption-key"
  key_ring = google_kms_key_ring.main.id
  
  purpose = "ENCRYPT_DECRYPT"
  
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "HSM"
  }
  
  rotation_period = "7776000s"  # 90 days
  
  labels = local.common_labels
}

# ==============================================================================
# BigQuery for Analytics and Usage Export
# ==============================================================================

resource "google_bigquery_dataset" "usage_export" {
  dataset_id    = "usage_export"
  friendly_name = "Usage Export"
  description   = "Dataset for GKE resource usage export"
  location      = "US"
  
  default_table_expiration_ms = 2592000000  # 30 days
  
  labels = local.common_labels
}

# ==============================================================================
# Random Resources
# ==============================================================================

resource "random_password" "postgresql_app" {
  length  = 32
  special = true
  upper   = true
  lower   = true
  numeric = true
}

# ==============================================================================
# Outputs
# ==============================================================================

output "gke_cluster_name" {
  description = "GKE cluster name"
  value       = google_container_cluster.main.name
}

output "gke_endpoint" {
  description = "GKE cluster endpoint"
  value       = google_container_cluster.main.endpoint
  sensitive   = true
}

output "cloudsql_instance_name" {
  description = "Cloud SQL instance name"
  value       = google_sql_database_instance.main.name
}

output "cloudsql_connection_name" {
  description = "Cloud SQL connection name"
  value       = google_sql_database_instance.main.connection_name
}

output "redis_instance_host" {
  description = "Memorystore Redis host"
  value       = google_redis_instance.main.host
}

output "load_balancer_ip" {
  description = "Global load balancer IP address"
  value       = google_compute_global_address.main.address
}

output "storage_bucket_backup" {
  description = "Backup storage bucket name"
  value       = google_storage_bucket.backup.name
}

output "kms_keyring_name" {
  description = "KMS keyring name"
  value       = google_kms_key_ring.main.name
}

output "service_account_gke_node" {
  description = "GKE node service account email"
  value       = google_service_account.gke_node.email
}

output "service_account_workload" {
  description = "Workload identity service account email"
  value       = google_service_account.workload.email
}

output "billing_budget_name" {
  description = "Billing budget name"
  value       = google_billing_budget.monthly.name
}

output "dashboard_url" {
  description = "Cloud Monitoring dashboard URL"
  value       = "https://console.cloud.google.com/monitoring/dashboards?project=${var.project_id}"
}

output "kubeconfig" {
  description = "Kubectl config for GKE cluster"
  value       = <<EOT
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: ${google_container_cluster.main.master_auth[0].cluster_ca_certificate}
    server: https://${google_container_cluster.main.endpoint}
  name: ${google_container_cluster.main.name}
contexts:
- context:
    cluster: ${google_container_cluster.main.name}
    user: ${google_container_cluster.main.name}
  name: ${google_container_cluster.main.name}
current-context: ${google_container_cluster.main.name}
kind: Config
preferences: {}
users:
- name: ${google_container_cluster.main.name}
  user:
    auth-provider:
      name: gcp
EOT
  sensitive = true
}