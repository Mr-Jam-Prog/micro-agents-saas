output "vpc_network_name" {
  description = "VPC network name"
  value       = google_compute_network.main.name
}

output "vpc_subnet_gke" {
  description = "GKE subnet name"
  value       = google_compute_subnetwork.gke.name
}

output "gke_cluster_id" {
  description = "GKE cluster ID"
  value       = google_container_cluster.main.id
}

output "gke_autopilot_enabled" {
  description = "GKE Autopilot enabled status"
  value       = google_container_cluster.main.enable_autopilot
}

output "cloudsql_instance_ip" {
  description = "Cloud SQL private IP address"
  value       = google_sql_database_instance.main.private_ip_address
}

output "redis_instance_port" {
  description = "Memorystore Redis port"
  value       = google_redis_instance.main.port
}

output "global_load_balancer_ip" {
  description = "Global load balancer IP address"
  value       = google_compute_global_address.main.address
}

output "cdn_enabled" {
  description = "Cloud CDN enabled status"
  value       = google_compute_backend_service.main.enable_cdn
}

output "cloud_armor_policy_name" {
  description = "Cloud Armor security policy name"
  value       = google_compute_security_policy.main.name
}

output "service_accounts" {
  description = "Service accounts created"
  value = {
    gke_node = google_service_account.gke_node.email
    workload = google_service_account.workload.email
  }
}

output "kms_key_rings" {
  description = "KMS key rings"
  value = {
    main = google_kms_key_ring.main.name
  }
}

output "kms_crypto_keys" {
  description = "KMS crypto keys"
  value = {
    gke     = google_kms_crypto_key.gke.name
    storage = google_kms_crypto_key.storage.name
    secrets = google_kms_crypto_key.secrets.name
  }
}

output "monitoring_alert_policies" {
  description = "Monitoring alert policies"
  value = [
    google_monitoring_alert_policy.gke_pod_crash.name,
    google_monitoring_alert_policy.cloudsql_cpu.name,
    google_monitoring_alert_policy.redis_memory.name,
  ]
}

output "billing_budget_id" {
  description = "Billing budget ID"
  value       = google_billing_budget.monthly.name
}

output "security_command_center_notification" {
  description = "Security Command Center notification config"
  value       = google_security_center_notification_config.main.name
}

output "vpc_service_perimeter" {
  description = "VPC Service Controls perimeter"
  value       = google_access_context_manager_service_perimeter.main.title
}

output "bigquery_datasets" {
  description = "BigQuery datasets created"
  value = [
    google_bigquery_dataset.billing_export.dataset_id,
    google_bigquery_dataset.usage_export.dataset_id,
  ]
}

output "storage_buckets" {
  description = "Cloud Storage buckets created"
  value = {
    backup = google_storage_bucket.backup.name
    logs   = google_storage_bucket.logs.name
    static = google_storage_bucket.static.name
  }
}

output "gke_node_pools" {
  description = "GKE node pools"
  value = {
    for np in google_container_cluster.main.node_pool :
    np.name => {
      machine_type = np.node_config[0].machine_type
      min_count    = np.autoscaling[0].min_node_count
      max_count    = np.autoscaling[0].max_node_count
      spot         = np.node_config[0].spot
    }
  }
}