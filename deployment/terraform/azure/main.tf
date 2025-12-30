# deployment/terraform/azure/main.tf
terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.80"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.45"
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
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
  
  backend "azurerm" {
    resource_group_name  = "tfstate-microagents"
    storage_account_name = "microagentstfstate"
    container_name       = "terraform-state"
    key                  = "production.terraform.tfstate"
    use_azuread_auth     = true
    subscription_id      = var.subscription_id
    tenant_id           = var.tenant_id
  }
}

# ==============================================================================
# Providers Configuration
# ==============================================================================

provider "azurerm" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
  features {
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
    key_vault {
      purge_soft_delete_on_destroy = true
    }
    virtual_machine {
      delete_os_disk_on_deletion = true
    }
  }
}

provider "azuread" {
  tenant_id = var.tenant_id
}

provider "kubernetes" {
  host                   = azurerm_kubernetes_cluster.main.kube_config.0.host
  client_certificate     = base64decode(azurerm_kubernetes_cluster.main.kube_config.0.client_certificate)
  client_key             = base64decode(azurerm_kubernetes_cluster.main.kube_config.0.client_key)
  cluster_ca_certificate = base64decode(azurerm_kubernetes_cluster.main.kube_config.0.cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = azurerm_kubernetes_cluster.main.kube_config.0.host
    client_certificate     = base64decode(azurerm_kubernetes_cluster.main.kube_config.0.client_certificate)
    client_key             = base64decode(azurerm_kubernetes_cluster.main.kube_config.0.client_key)
    cluster_ca_certificate = base64decode(azurerm_kubernetes_cluster.main.kube_config.0.cluster_ca_certificate)
  }
}

# ==============================================================================
# Local Variables
# ==============================================================================

locals {
  # Naming conventions
  name_prefix = "microagents-${var.environment}"
  
  # Primary region and paired region for DR
  primary_region   = var.primary_location
  secondary_region = var.secondary_location
  
  # Availability zones
  zones = ["1", "2", "3"]
  
  # CIDR blocks
  vnet_cidr = "10.0.0.0/16"
  
  # Subnet CIDRs
  subnets = {
    aks_system = {
      cidr        = cidrsubnet(local.vnet_cidr, 8, 1)
      service_endpoints = ["Microsoft.Sql", "Microsoft.Storage", "Microsoft.KeyVault"]
    }
    aks_user = {
      cidr        = cidrsubnet(local.vnet_cidr, 8, 2)
      service_endpoints = ["Microsoft.Sql", "Microsoft.Storage", "Microsoft.KeyVault"]
    }
    postgresql = {
      cidr        = cidrsubnet(local.vnet_cidr, 8, 3)
      service_endpoints = ["Microsoft.Sql"]
      delegation = "Microsoft.DBforPostgreSQL/flexibleServers"
    }
    redis = {
      cidr        = cidrsubnet(local.vnet_cidr, 8, 4)
      service_endpoints = ["Microsoft.Cache"]
    }
    bastion = {
      cidr        = cidrsubnet(local.vnet_cidr, 8, 5)
      service_endpoints = []
    }
    private_endpoints = {
      cidr        = cidrsubnet(local.vnet_cidr, 8, 6)
      service_endpoints = ["Microsoft.KeyVault", "Microsoft.ContainerRegistry"]
    }
  }
  
  # Node pools configuration
  system_node_pool = {
    name                = "system"
    vm_size            = "Standard_D4s_v3"
    node_count         = 3
    min_count          = 3
    max_count          = 5
    os_disk_size_gb    = 128
    os_disk_type       = "Ephemeral"
    availability_zones = local.zones
    max_pods          = 50
    node_labels = {
      "node-type" = "system"
      "os"        = "linux"
    }
    node_taints = [
      "system:NoSchedule"
    ]
  }
  
  agent_node_pool = {
    name                = "agent"
    vm_size            = "Standard_D8s_v3"
    node_count         = 5
    min_count          = 3
    max_count          = 50
    os_disk_size_gb    = 256
    os_disk_type       = "Managed"
    availability_zones = local.zones
    max_pods          = 100
    priority          = "Spot"
    spot_max_price    = -1  # On-demand price
    eviction_policy   = "Delete"
    node_labels = {
      "node-type" = "agent"
      "priority"  = "spot"
    }
    node_taints = [
      "spot:NoSchedule"
    ]
  }
  
  # PostgreSQL configuration
  postgresql_config = {
    version         = "15"
    sku_name        = var.environment == "production" ? "GP_Standard_D4s_v3" : "GP_Standard_D2s_v3"
    storage_mb      = 32768  # 32GB
    backup_retention_days = 35
    geo_redundant_backup_enabled = true
    auto_grow_enabled = true
  }
  
  # Redis configuration
  redis_config = {
    family          = "C"
    capacity        = 2
    sku_name        = "Standard"
    zones           = local.zones
    minimum_tls_version = "1.2"
  }
  
  # Common tags
  common_tags = {
    environment   = var.environment
    project       = "microagents-platform"
    managed-by    = "terraform"
    cost-center   = var.cost_center
    compliance    = "pci-dss-4.0"
    backup        = "enabled"
    dr-tier       = "tier-3"
  }
}

# ==============================================================================
# Resource Groups
# ==============================================================================

resource "azurerm_resource_group" "primary" {
  name     = "${local.name_prefix}-rg-${local.primary_region}"
  location = local.primary_region
  
  tags = local.common_tags
}

resource "azurerm_resource_group" "secondary" {
  name     = "${local.name_prefix}-rg-${local.secondary_region}"
  location = local.secondary_region
  
  tags = local.common_tags
}

# ==============================================================================
# Virtual Network and Subnets
# ==============================================================================

resource "azurerm_virtual_network" "main" {
  name                = "${local.name_prefix}-vnet"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  address_space       = [local.vnet_cidr]
  
  tags = local.common_tags
}

resource "azurerm_subnet" "aks_system" {
  name                 = "${local.name_prefix}-aks-system"
  resource_group_name  = azurerm_resource_group.primary.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [local.subnets.aks_system.cidr]
  
  service_endpoints = local.subnets.aks_system.service_endpoints
}

resource "azurerm_subnet" "aks_user" {
  name                 = "${local.name_prefix}-aks-user"
  resource_group_name  = azurerm_resource_group.primary.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [local.subnets.aks_user.cidr]
  
  service_endpoints = local.subnets.aks_user.service_endpoints
}

resource "azurerm_subnet" "postgresql" {
  name                 = "${local.name_prefix}-postgresql"
  resource_group_name  = azurerm_resource_group.primary.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [local.subnets.postgresql.cidr]
  
  service_endpoints = local.subnets.postgresql.service_endpoints
  delegation {
    name = "postgresql"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "redis" {
  name                 = "${local.name_prefix}-redis"
  resource_group_name  = azurerm_resource_group.primary.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [local.subnets.redis.cidr]
  
  service_endpoints = local.subnets.redis.service_endpoints
}

resource "azurerm_subnet" "bastion" {
  name                 = "${local.name_prefix}-bastion"
  resource_group_name  = azurerm_resource_group.primary.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [local.subnets.bastion.cidr]
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "${local.name_prefix}-private-endpoints"
  resource_group_name  = azurerm_resource_group.primary.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [local.subnets.private_endpoints.cidr]
  
  service_endpoints = local.subnets.private_endpoints.service_endpoints
  
  # Private endpoint network policies
  private_endpoint_network_policies_enabled     = true
  private_link_service_network_policies_enabled = true
}

# Network Security Groups
resource "azurerm_network_security_group" "aks" {
  name                = "${local.name_prefix}-aks-nsg"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  
  security_rule {
    name                       = "AllowControlPlane"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "AzureCloud"
    destination_address_prefix = "*"
  }
  
  security_rule {
    name                       = "AllowNodePorts"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "30000-32767"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
  
  tags = local.common_tags
}

resource "azurerm_subnet_network_security_group_association" "aks_system" {
  subnet_id                 = azurerm_subnet.aks_system.id
  network_security_group_id = azurerm_network_security_group.aks.id
}

resource "azurerm_subnet_network_security_group_association" "aks_user" {
  subnet_id                 = azurerm_subnet.aks_user.id
  network_security_group_id = azurerm_network_security_group.aks.id
}

# ==============================================================================
# AKS Cluster
# ==============================================================================

resource "azurerm_kubernetes_cluster" "main" {
  name                = "${local.name_prefix}-aks"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  dns_prefix          = "${local.name_prefix}-aks"
  kubernetes_version  = var.kubernetes_version
  
  # Network configuration
  network_profile {
    network_plugin    = "azure"
    network_policy    = "azure"
    load_balancer_sku = "standard"
    service_cidr      = "10.0.128.0/17"
    dns_service_ip    = "10.0.128.10"
    docker_bridge_cidr = "172.17.0.1/16"
  }
  
  # Identity configuration (managed identity)
  identity {
    type = "SystemAssigned"
  }
  
  # System node pool
  default_node_pool {
    name                 = local.system_node_pool.name
    vm_size              = local.system_node_pool.vm_size
    node_count           = local.system_node_pool.node_count
    min_count            = local.system_node_pool.min_count
    max_count            = local.system_node_pool.max_count
    os_disk_size_gb      = local.system_node_pool.os_disk_size_gb
    os_disk_type         = local.system_node_pool.os_disk_type
    availability_zones   = local.system_node_pool.availability_zones
    max_pods             = local.system_node_pool.max_pods
    vnet_subnet_id       = azurerm_subnet.aks_system.id
    enable_auto_scaling  = true
    node_labels          = local.system_node_pool.node_labels
    node_taints          = local.system_node_pool.node_taints
  }
  
  # Auto-scaler profile
  auto_scaler_profile {
    balance_similar_node_groups      = true
    expander                         = "least-waste"
    max_graceful_termination_sec     = 600
    scale_down_delay_after_add       = "10m"
    scale_down_unneeded              = "10m"
    scale_down_unready               = "20m"
    scale_down_utilization_threshold = "0.5"
  }
  
  # Add-ons
  azure_active_directory_role_based_access_control {
    managed                = true
    admin_group_object_ids = [azuread_group.aks_admins.object_id]
    azure_rbac_enabled     = true
  }
  
  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  }
  
  azure_policy_enabled = true
  
  # Maintenance configuration
  maintenance_window {
    allowed {
      day   = "Sunday"
      hours = [2, 3, 4]
    }
    not_allowed {
      start = "2024-12-25T00:00:00Z"
      end   = "2024-12-26T23:59:59Z"
    }
  }
  
  # Security features
  local_account_disabled = true
  run_command_enabled    = false
  http_application_routing_enabled = false
  
  # Upgrade channel
  automatic_channel_upgrade = "stable"
  
  tags = local.common_tags
  
  lifecycle {
    ignore_changes = [
      default_node_pool[0].node_count,
    ]
  }
}

# Additional node pool for agent workloads (spot instances)
resource "azurerm_kubernetes_cluster_node_pool" "agent" {
  name                  = local.agent_node_pool.name
  kubernetes_cluster_id = azurerm_kubernetes_cluster.main.id
  vm_size               = local.agent_node_pool.vm_size
  node_count            = local.agent_node_pool.node_count
  min_count             = local.agent_node_pool.min_count
  max_count             = local.agent_node_pool.max_count
  os_disk_size_gb       = local.agent_node_pool.os_disk_size_gb
  os_disk_type          = local.agent_node_pool.os_disk_type
  availability_zones    = local.agent_node_pool.availability_zones
  max_pods              = local.agent_node_pool.max_pods
  vnet_subnet_id        = azurerm_subnet.aks_user.id
  enable_auto_scaling   = true
  priority              = local.agent_node_pool.priority
  spot_max_price        = local.agent_node_pool.spot_max_price
  eviction_policy       = local.agent_node_pool.eviction_policy
  node_labels           = local.agent_node_pool.node_labels
  node_taints           = local.agent_node_pool.node_taints
  
  tags = local.common_tags
  
  lifecycle {
    ignore_changes = [
      node_count,
    ]
  }
}

# ==============================================================================
# Azure Database for PostgreSQL (Flexible Server)
# ==============================================================================

resource "azurerm_postgresql_flexible_server" "main" {
  name                   = "${local.name_prefix}-postgres"
  resource_group_name    = azurerm_resource_group.primary.name
  location               = azurerm_resource_group.primary.location
  version                = local.postgresql_config.version
  administrator_login    = "microagentsadmin"
  administrator_password = random_password.postgresql_admin.result
  zone                   = local.zones[0]
  
  # High availability configuration
  high_availability {
    mode                      = "ZoneRedundant"
    standby_availability_zone = local.zones[1]
  }
  
  # Performance configuration
  sku_name                     = local.postgresql_config.sku_name
  storage_mb                   = local.postgresql_config.storage_mb
  backup_retention_days        = local.postgresql_config.backup_retention_days
  geo_redundant_backup_enabled = local.postgresql_config.geo_redundant_backup_enabled
  auto_grow_enabled            = local.postgresql_config.auto_grow_enabled
  
  # Network configuration
  delegated_subnet_id = azurerm_subnet.postgresql.id
  private_dns_zone_id = azurerm_private_dns_zone.postgresql.id
  
  # Maintenance
  maintenance_window {
    day_of_week  = 0
    start_hour   = 2
    start_minute = 0
  }
  
  # Monitoring
  monitoring_enabled = true
  
  tags = local.common_tags
  
  depends_on = [
    azurerm_private_dns_zone_virtual_network_link.postgresql
  ]
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "microagents"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_configuration" "performance" {
  for_each = {
    "shared_preload_libraries" : "pg_stat_statements,auto_explain",
    "pg_stat_statements.track" : "all",
    "effective_cache_size" : "8GB",
    "random_page_cost" : "1.1",
    "log_min_duration_statement" : "1000"
  }
  
  name      = each.key
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = each.value
}

# Private DNS zone for PostgreSQL
resource "azurerm_private_dns_zone" "postgresql" {
  name                = "${local.name_prefix}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.primary.name
  
  tags = local.common_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgresql" {
  name                  = "${local.name_prefix}-postgresql-link"
  resource_group_name   = azurerm_resource_group.primary.name
  private_dns_zone_name = azurerm_private_dns_zone.postgresql.name
  virtual_network_id    = azurerm_virtual_network.main.id
  
  registration_enabled = false
}

# ==============================================================================
# Azure Cache for Redis
# ==============================================================================

resource "azurerm_redis_cache" "main" {
  name                = "${local.name_prefix}-redis"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  capacity            = local.redis_config.capacity
  family              = local.redis_config.family
  sku_name            = local.redis_config.sku_name
  enable_non_ssl_port = false
  minimum_tls_version = local.redis_config.minimum_tls_version
  zones               = local.redis_config.zones
  
  # High availability
  replicas_per_master = 2
  replicas_per_primary = 2
  
  # Patch schedule
  patch_schedule {
    day_of_week    = "Sunday"
    start_hour_utc = 4
  }
  
  # Redis configuration
  redis_configuration {
    maxmemory_reserved              = 2
    maxmemory_delta                 = 2
    maxmemory_policy                = "allkeys-lru"
    notify_keyspace_events          = "gxE"
    aof_backup_enabled              = true
    aof_storage_connection_string_0 = azurerm_storage_account.backup.primary_blob_connection_string
  }
  
  # Network configuration
  subnet_id = azurerm_subnet.redis.id
  
  tags = local.common_tags
}

# Redis firewall rule for AKS nodes
resource "azurerm_redis_firewall_rule" "aks" {
  name                = "aks-nodes"
  redis_cache_name    = azurerm_redis_cache.main.name
  resource_group_name = azurerm_resource_group.primary.name
  start_ip            = cidrhost(local.subnets.aks_user.cidr, 0)
  end_ip              = cidrhost(local.subnets.aks_user.cidr, 255)
}

# ==============================================================================
# Blob Storage for Backups and Static Content
# ==============================================================================

resource "azurerm_storage_account" "backup" {
  name                     = "${replace(local.name_prefix, "-", "")}backup"
  resource_group_name      = azurerm_resource_group.primary.name
  location                 = azurerm_resource_group.primary.location
  account_tier             = "Standard"
  account_replication_type = "GRS"  # Geo-redundant storage
  account_kind            = "StorageV2"
  
  # Security
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  
  # Network
  network_rules {
    default_action             = "Deny"
    ip_rules                   = []
    virtual_network_subnet_ids = [
      azurerm_subnet.aks_system.id,
      azurerm_subnet.aks_user.id
    ]
    bypass = ["AzureServices"]
  }
  
  # Lifecycle management
  lifecycle {
    ignore_changes = [
      network_rules[0].ip_rules,
    ]
  }
  
  tags = local.common_tags
}

resource "azurerm_storage_container" "backups" {
  name                  = "backups"
  storage_account_name  = azurerm_storage_account.backup.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "logs" {
  name                  = "logs"
  storage_account_name  = azurerm_storage_account.backup.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "static" {
  name                  = "static"
  storage_account_name  = azurerm_storage_account.backup.name
  container_access_type = "private"
}

# Storage lifecycle management policy
resource "azurerm_storage_management_policy" "backup" {
  storage_account_id = azurerm_storage_account.backup.id
  
  rule {
    name    = "backup-lifecycle"
    enabled = true
    
    filters {
      prefix_match = ["backups/"]
      blob_types   = ["blockBlob"]
    }
    
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than    = 30
        tier_to_archive_after_days_since_modification_greater_than = 90
        delete_after_days_since_modification_greater_than          = 365
      }
      
      snapshot {
        delete_after_days_since_creation_greater_than = 90
      }
      
      version {
        delete_after_days_since_creation = 90
      }
    }
  }
}

# ==============================================================================
# Azure Front Door with WAF
# ==============================================================================

resource "azurerm_frontdoor" "main" {
  name                                         = "${local.name_prefix}-frontdoor"
  resource_group_name                          = azurerm_resource_group.primary.name
  enforce_backend_pools_certificate_name_check = false
  
  # Routing configuration
  routing_rule {
    name               = "httpsRedirect"
    accepted_protocols = ["Http"]
    patterns_to_match  = ["/*"]
    frontend_endpoints = ["httpsRedirect"]
    
    redirect_configuration {
      redirect_protocol = "HttpsOnly"
      redirect_type     = "Moved"
    }
  }
  
  routing_rule {
    name               = "mainRouting"
    accepted_protocols = ["Https"]
    patterns_to_match  = ["/*"]
    frontend_endpoints = ["mainFrontend"]
    
    forwarding_configuration {
      forwarding_protocol = "HttpsOnly"
      backend_pool_name   = "aksBackend"
    }
  }
  
  # Backend pool for AKS
  backend_pool {
    name = "aksBackend"
    backend {
      host_header = azurerm_public_ip.aks.ip_address
      address     = azurerm_public_ip.aks.ip_address
      http_port   = 80
      https_port  = 443
      priority    = 1
      weight      = 50
    }
    
    load_balancing_name = "loadBalancingSettings1"
    health_probe_name   = "healthProbeSettings1"
  }
  
  # Frontend endpoints
  frontend_endpoint {
    name      = "httpsRedirect"
    host_name = "${local.name_prefix}-frontdoor.azurefd.net"
  }
  
  frontend_endpoint {
    name                              = "mainFrontend"
    host_name                         = "${local.name_prefix}-frontdoor.azurefd.net"
    session_affinity_enabled          = false
    session_affinity_ttl_seconds      = 0
    web_application_firewall_policy_link_id = azurerm_frontdoor_firewall_policy.main.id
  }
  
  # Load balancing settings
  backend_pool_load_balancing {
    name = "loadBalancingSettings1"
  }
  
  # Health probe settings
  backend_pool_health_probe {
    name                = "healthProbeSettings1"
    path                = "/health"
    protocol            = "Https"
    interval_in_seconds = 30
  }
  
  tags = local.common_tags
}

# WAF policy for Front Door
resource "azurerm_frontdoor_firewall_policy" "main" {
  name                              = "${local.name_prefix}-waf"
  resource_group_name               = azurerm_resource_group.primary.name
  enabled                           = true
  mode                              = "Prevention"
  redirect_url                      = "https://${local.name_prefix}-frontdoor.azurefd.net/blocked"
  custom_block_response_status_code = 403
  custom_block_response_body        = base64encode("<html><body><h1>Access Denied</h1><p>Request blocked by WAF policy.</p></body></html>")
  
  # Managed rules
  managed_rule {
    type    = "DefaultRuleSet"
    version = "1.0"
    
    exclusion {
      match_variable = "QueryStringArgNames"
      operator       = "Equals"
      selector       = "debug"
    }
  }
  
  managed_rule {
    type    = "Microsoft_BotManagerRuleSet"
    version = "1.0"
  }
  
  # Custom rules
  custom_rule {
    name                           = "RateLimit"
    enabled                        = true
    priority                       = 1
    rate_limit_duration_in_minutes = 1
    rate_limit_threshold           = 100
    type                           = "RateLimitRule"
    action                         = "Block"
    
    match_condition {
      match_variable = "RemoteAddr"
      operator       = "IPMatch"
      negation_condition = false
      match_values = [
        "192.168.1.0/24",
        "10.0.0.0/8"
      ]
    }
  }
  
  tags = local.common_tags
}

# Public IP for AKS ingress
resource "azurerm_public_ip" "aks" {
  name                = "${local.name_prefix}-aks-pip"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = local.zones
  
  tags = local.common_tags
}

# ==============================================================================
# Azure Security Center / Defender for Cloud
# ==============================================================================

# Enable Defender plans
resource "azurerm_security_center_subscription_pricing" "defender" {
  for_each = toset([
    "Kubernetes",
    "ContainerRegistry",
    "KeyVaults",
    "SqlServers",
    "Storage",
    "Dns",
    "AppServices"
  ])
  
  tier          = "Standard"
  resource_type = each.key
  subplan       = each.key == "Kubernetes" ? "P2" : null
}

# Security Center automation
resource "azurerm_security_center_automation" "main" {
  name                = "${local.name_prefix}-automation"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  
  action {
    type        = "logicapp"
    resource_id = azurerm_logic_app_workflow.security_automation.id
    trigger_url = azurerm_logic_app_workflow.security_automation.access_endpoint
  }
  
  source {
    event_source = "Alerts"
    rule_set {
      rule {
        property_path  = "Severity"
        operator       = "Equals"
        expected_value = "High"
        property_type  = "String"
      }
    }
  }
  
  scopes = [
    "/subscriptions/${var.subscription_id}"
  ]
}

# Security policies
resource "azurerm_policy_assignment" "security" {
  for_each = {
    "Enable-MFA-For-Owners" : "/providers/Microsoft.Authorization/policyDefinitions/aa633080-8b72-40c4-a2d7-d00c03e80bed",
    "Require-Tags-On-Resources" : "/providers/Microsoft.Authorization/policyDefinitions/871b6d14-10aa-478d-b590-94f262ecfa99",
    "Enforce-SSL-Connection" : "/providers/Microsoft.Authorization/policyDefinitions/e802a67a-daf5-4436-9ea6-f6d821dd0c5d",
    "Enable-Audit-On-SQL" : "/providers/Microsoft.Authorization/policyDefinitions/a6fb4358-5bf4-4ad7-ba82-2cd2f41ce5e9"
  }
  
  name                 = each.key
  scope                = azurerm_resource_group.primary.id
  policy_definition_id = each.value
  description          = "Security compliance policy assignment"
  display_name         = each.key
  
  parameters = <<PARAMS
    {
      "tagName": {
        "value": "environment"
      }
    }
  PARAMS
  
  identity {
    type = "SystemAssigned"
  }
}

# ==============================================================================
# Azure Monitor Configuration
# ==============================================================================

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.name_prefix}-logs"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  
  daily_quota_gb = 1
  
  tags = local.common_tags
}

resource "azurerm_application_insights" "main" {
  name                = "${local.name_prefix}-appinsights"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  
  sampling_percentage = 10
  
  tags = local.common_tags
}

# Monitor action group for alerts
resource "azurerm_monitor_action_group" "critical" {
  name                = "${local.name_prefix}-critical-alerts"
  resource_group_name = azurerm_resource_group.primary.name
  short_name          = "critical"
  
  email_receiver {
    name          = "sendtoadmin"
    email_address = var.alert_email
  }
  
  azure_app_push_receiver {
    name          = "apppushadmin"
    email_address = var.alert_email
  }
  
  tags = local.common_tags
}

# Alert rules
resource "azurerm_monitor_metric_alert" "postgresql_cpu" {
  name                = "${local.name_prefix}-postgresql-cpu-high"
  resource_group_name = azurerm_resource_group.primary.name
  scopes              = [azurerm_postgresql_flexible_server.main.id]
  description         = "PostgreSQL CPU utilization is high"
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  
  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "cpu_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 80
    
    dimension {
      name     = "ResourceId"
      operator = "Include"
      values   = [azurerm_postgresql_flexible_server.main.id]
    }
  }
  
  action {
    action_group_id = azurerm_monitor_action_group.critical.id
  }
  
  tags = local.common_tags
}

resource "azurerm_monitor_metric_alert" "redis_memory" {
  name                = "${local.name_prefix}-redis-memory-high"
  resource_group_name = azurerm_resource_group.primary.name
  scopes              = [azurerm_redis_cache.main.id]
  description         = "Redis memory usage is high"
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  
  criteria {
    metric_namespace = "Microsoft.Cache/redis"
    metric_name      = "usedmemorypercentage"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 85
    
    dimension {
      name     = "Cache"
      operator = "Include"
      values   = [azurerm_redis_cache.main.name]
    }
  }
  
  action {
    action_group_id = azurerm_monitor_action_group.critical.id
  }
  
  tags = local.common_tags
}

# Dashboard
resource "azurerm_portal_dashboard" "main" {
  name                = "${local.name_prefix}-dashboard"
  resource_group_name = azurerm_resource_group.primary.name
  location            = azurerm_resource_group.primary.location
  
  dashboard_properties = templatefile("${path.module}/dashboard.tpl", {
    subscription_id    = var.subscription_id
    resource_group     = azurerm_resource_group.primary.name
    aks_cluster       = azurerm_kubernetes_cluster.main.name
    postgres_server   = azurerm_postgresql_flexible_server.main.name
    redis_cache       = azurerm_redis_cache.main.name
    storage_account   = azurerm_storage_account.backup.name
    location          = azurerm_resource_group.primary.location
  })
  
  tags = local.common_tags
}

# ==============================================================================
# Azure Cost Management
# ==============================================================================

resource "azurerm_consumption_budget_resource_group" "monthly" {
  name              = "${local.name_prefix}-monthly-budget"
  resource_group_id = azurerm_resource_group.primary.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"
  
  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  }
  
  notification {
    enabled        = true
    threshold      = 80.0
    operator       = "GreaterThan"
    threshold_type = "Actual"
    
    contact_emails = [
      var.alert_email,
      var.finance_email
    ]
    
    contact_groups = [
      azurerm_monitor_action_group.critical.id
    ]
  }
  
  notification {
    enabled        = true
    threshold      = 100.0
    operator       = "GreaterThan"
    threshold_type = "Actual"
    
    contact_emails = [
      var.alert_email,
      var.finance_email
    ]
  }
  
  notification {
    enabled        = true
    threshold      = 120.0
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    
    contact_emails = [
      var.alert_email,
      var.finance_email
    ]
  }
}

# Cost management export
resource "azurerm_cost_management_export_resource_group" "monthly" {
  name                         = "${local.name_prefix}-cost-export"
  resource_group_id            = azurerm_resource_group.primary.id
  recurrence_type              = "Monthly"
  recurrence_period_start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  
  export_data_storage_location {
    container_id     = "${azurerm_storage_account.backup.id}/blobServices/default/containers/logs"
    root_folder_path = "/costs"
  }
  
  export_data_options {
    type       = "Usage"
    time_frame = "MonthToDate"
  }
}

# ==============================================================================
# Azure AD Groups and Role Assignments
# ==============================================================================

resource "azuread_group" "aks_admins" {
  display_name     = "${local.name_prefix}-aks-admins"
  security_enabled = true
  owners           = [data.azuread_client_config.current.object_id]
  
  members = [
    data.azuread_client_config.current.object_id,
    azurerm_user_assigned_identity.aks.principal_id
  ]
}

resource "azuread_group" "platform_engineers" {
  display_name     = "${local.name_prefix}-platform-engineers"
  security_enabled = true
  owners           = [data.azuread_client_config.current.object_id]
}

# Role assignments
resource "azurerm_role_assignment" "aks_contributor" {
  scope                = azurerm_kubernetes_cluster.main.id
  role_definition_name = "Contributor"
  principal_id         = azuread_group.platform_engineers.object_id
}

resource "azurerm_role_assignment" "storage_contributor" {
  scope                = azurerm_storage_account.backup.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}

# ==============================================================================
# Azure Container Registry
# ==============================================================================

resource "azurerm_container_registry" "main" {
  name                = "${replace(local.name_prefix, "-", "")}acr"
  resource_group_name = azurerm_resource_group.primary.name
  location            = azurerm_resource_group.primary.location
  sku                 = "Premium"
  admin_enabled       = false
  
  # Geo-replication for DR
  georeplications {
    location = local.secondary_region
    zone_redundancy_enabled = true
  }
  
  network_rule_set {
    default_action = "Deny"
    
    ip_rule {
      action   = "Allow"
      ip_range = cidrhost(local.subnets.aks_system.cidr, 0)
    }
    
    virtual_network {
      action    = "Allow"
      subnet_id = azurerm_subnet.aks_system.id
    }
  }
  
  # Security
  anonymous_pull_enabled = false
  data_endpoint_enabled  = true
  
  retention_policy {
    days    = 30
    enabled = true
  }
  
  tags = local.common_tags
}

# ==============================================================================
# Azure Key Vault for Secrets Management
# ==============================================================================

resource "azurerm_key_vault" "main" {
  name                       = "${replace(local.name_prefix, "-", "")}kv"
  location                   = azurerm_resource_group.primary.location
  resource_group_name        = azurerm_resource_group.primary.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 90
  purge_protection_enabled   = true
  
  # Network ACLs
  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
    ip_rules       = []
    
    virtual_network_subnet_ids = [
      azurerm_subnet.aks_system.id,
      azurerm_subnet.aks_user.id
    ]
  }
  
  # Access policies
  access_policy {
    tenant_id = var.tenant_id
    object_id = data.azuread_client_config.current.object_id
    
    secret_permissions = [
      "Get", "List", "Set", "Delete", "Backup", "Restore", "Recover", "Purge"
    ]
    
    key_permissions = [
      "Get", "List", "Create", "Delete", "Recover", "Backup", "Restore", "Purge"
    ]
    
    certificate_permissions = [
      "Get", "List", "Create", "Delete", "Recover", "Backup", "Restore", "Purge"
    ]
  }
  
  access_policy {
    tenant_id = var.tenant_id
    object_id = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
    
    secret_permissions = [
      "Get", "List"
    ]
  }
  
  tags = local.common_tags
}

# Key Vault secrets
resource "azurerm_key_vault_secret" "postgresql_password" {
  name         = "postgresql-admin-password"
  value        = random_password.postgresql_admin.result
  key_vault_id = azurerm_key_vault.main.id
  
  content_type = "password"
  
  lifecycle {
    ignore_changes = [
      value,
    ]
  }
}

resource "azurerm_key_vault_secret" "redis_password" {
  name         = "redis-access-key"
  value        = azurerm_redis_cache.main.primary_access_key
  key_vault_id = azurerm_key_vault.main.id
  
  content_type = "access-key"
}

# ==============================================================================
# Resource Locks
# ==============================================================================

resource "azurerm_management_lock" "resource_group" {
  name       = "${local.name_prefix}-rg-lock"
  scope      = azurerm_resource_group.primary.id
  lock_level = "CanNotDelete"
  
  notes = "Prevent accidental deletion of production resource group"
}

resource "azurerm_management_lock" "aks" {
  name       = "${local.name_prefix}-aks-lock"
  scope      = azurerm_kubernetes_cluster.main.id
  lock_level = "CanNotDelete"
  
  notes = "Prevent accidental deletion of AKS cluster"
}

resource "azurerm_management_lock" "postgresql" {
  name       = "${local.name_prefix}-postgresql-lock"
  scope      = azurerm_postgresql_flexible_server.main.id
  lock_level = "CanNotDelete"
  
  notes = "Prevent accidental deletion of PostgreSQL database"
}

# ==============================================================================
# Disaster Recovery Resources (Secondary Region)
# ==============================================================================

resource "azurerm_storage_account" "secondary_backup" {
  name                     = "${replace(local.name_prefix, "-", "")}backupsec"
  resource_group_name      = azurerm_resource_group.secondary.name
  location                 = local.secondary_region
  account_tier             = "Standard"
  account_replication_type = "LRS"  # Locally redundant in secondary region
  account_kind            = "StorageV2"
  
  allow_nested_items_to_be_public = false
  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }
  
  tags = merge(local.common_tags, {
    dr-region = "secondary"
  })
}

resource "azurerm_container_registry" "secondary" {
  name                = "${replace(local.name_prefix, "-", "")}acrsec"
  resource_group_name = azurerm_resource_group.secondary.name
  location            = local.secondary_region
  sku                 = "Premium"
  admin_enabled       = false
  
  network_rule_set {
    default_action = "Deny"
  }
  
  tags = merge(local.common_tags, {
    dr-region = "secondary"
  })
}

# ==============================================================================
# Logic App for Security Automation
# ==============================================================================

resource "azurerm_logic_app_workflow" "security_automation" {
  name                = "${local.name_prefix}-security-automation"
  location            = azurerm_resource_group.primary.location
  resource_group_name = azurerm_resource_group.primary.name
  
  tags = local.common_tags
}

# ==============================================================================
# Managed Identities
# ==============================================================================

resource "azurerm_user_assigned_identity" "aks" {
  name                = "${local.name_prefix}-aks-identity"
  resource_group_name = azurerm_resource_group.primary.name
  location            = azurerm_resource_group.primary.location
  
  tags = local.common_tags
}

# ==============================================================================
# Data Sources
# ==============================================================================

data "azuread_client_config" "current" {}

data "azurerm_client_config" "current" {}

# ==============================================================================
# Random Resources
# ==============================================================================

resource "random_password" "postgresql_admin" {
  length  = 32
  special = true
  upper   = true
  lower   = true
  numeric = true
}

# ==============================================================================
# Outputs
# ==============================================================================

output "aks_cluster_name" {
  description = "AKS cluster name"
  value       = azurerm_kubernetes_cluster.main.name
}

output "aks_cluster_id" {
  description = "AKS cluster ID"
  value       = azurerm_kubernetes_cluster.main.id
}

output "aks_kube_config" {
  description = "Kubeconfig for AKS cluster"
  value       = azurerm_kubernetes_cluster.main.kube_config_raw
  sensitive   = true
}

output "postgresql_hostname" {
  description = "PostgreSQL flexible server hostname"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "redis_hostname" {
  description = "Redis cache hostname"
  value       = azurerm_redis_cache.main.hostname
}

output "frontdoor_hostname" {
  description = "Front Door hostname"
  value       = azurerm_frontdoor.main.frontend_endpoints[0].host_name
}

output "storage_account_name" {
  description = "Primary storage account name"
  value       = azurerm_storage_account.backup.name
}

output "key_vault_name" {
  description = "Key Vault name"
  value       = azurerm_key_vault.main.name
}

output "container_registry_name" {
  description = "Container registry name"
  value       = azurerm_container_registry.main.name
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace ID"
  value       = azurerm_log_analytics_workspace.main.id
}

output "application_insights_instrumentation_key" {
  description = "Application Insights instrumentation key"
  value       = azurerm_application_insights.main.instrumentation_key
  sensitive   = true
}

output "public_ip_address" {
  description = "Public IP address for AKS ingress"
  value       = azurerm_public_ip.aks.ip_address
}

output "dashboard_url" {
  description = "Azure Portal dashboard URL"
  value       = "https://portal.azure.com/#blade/Microsoft_Azure_Monitoring/LogsDashboardBlade/id/%2Fsubscriptions%2F${var.subscription_id}%2Fresourcegroups%2F${azurerm_resource_group.primary.name}%2Fproviders%2Fmicrosoft.portal%2Fdashboards%2F${azurerm_portal_dashboard.main.name}"
}