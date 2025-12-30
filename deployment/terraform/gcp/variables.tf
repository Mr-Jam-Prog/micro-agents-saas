variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "organization_id" {
  description = "GCP organization ID"
  type        = string
}

variable "billing_account" {
  description = "GCP billing account ID"
  type        = string
}

variable "primary_region" {
  description = "Primary GCP region"
  type        = string
  default     = "europe-west1"
}

variable "secondary_region" {
  description = "Secondary GCP region for DR"
  type        = string
  default     = "europe-west4"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
  
  validation {
    condition     = contains(["production", "staging", "development"], var.environment)
    error_message = "Environment must be one of: production, staging, development."
  }
}

variable "domain_name" {
  description = "Domain name for the application"
  type        = string
  default     = "microagents.io"
}

variable "monthly_budget" {
  description = "Monthly budget in USD"
  type        = number
  default     = 5000
  
  validation {
    condition     = var.monthly_budget >= 100
    error_message = "Monthly budget must be at least $100."
  }
}

variable "alert_email" {
  description = "Email address for alerts"
  type        = string
  default     = "alerts@microagents.io"
}

variable "admin_group_email" {
  description = "Google Group email for administrators"
  type        = string
  default     = "platform-engineers@microagents.io"
}

variable "admin_ip_ranges" {
  description = "IP ranges for admin access"
  type        = list(string)
  default     = []
}

variable "blocked_ip_ranges" {
  description = "IP ranges to block"
  type        = list(string)
  default     = []
}

variable "cost_center" {
  description = "Cost center for resource labeling"
  type        = string
  default     = "devops-platform"
}

variable "slack_channel_name" {
  description = "Slack channel name for alerts"
  type        = string
  default     = "#alerts-microagents"
}

variable "slack_auth_token" {
  description = "Slack auth token for notifications"
  type        = string
  sensitive   = true
  default     = ""
}

variable "enable_autopilot" {
  description = "Enable GKE Autopilot mode"
  type        = bool
  default     = false
}

variable "enable_confidential_computing" {
  description = "Enable confidential computing for GKE nodes"
  type        = bool
  default     = false
}