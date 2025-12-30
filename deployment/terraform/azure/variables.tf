variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
  sensitive   = true
}

variable "tenant_id" {
  description = "Azure AD tenant ID"
  type        = string
  sensitive   = true
}

variable "primary_location" {
  description = "Primary Azure region"
  type        = string
  default     = "westeurope"
}

variable "secondary_location" {
  description = "Secondary Azure region for DR"
  type        = string
  default     = "northeurope"
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

variable "kubernetes_version" {
  description = "Kubernetes version for AKS"
  type        = string
  default     = "1.27"
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

variable "finance_email" {
  description = "Email address for finance alerts"
  type        = string
  default     = "finance@microagents.io"
}

variable "cost_center" {
  description = "Cost center for tagging"
  type        = string
  default     = "devops-platform"
}

variable "admin_object_ids" {
  description = "Azure AD object IDs for admin access"
  type        = list(string)
  default     = []
}