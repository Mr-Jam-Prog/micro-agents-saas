variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "microagents"
}

variable "environment" {
  description = "Environment name (production, staging, development)"
  type        = string
  default     = "production"
  
  validation {
    condition     = contains(["production", "staging", "development"], var.environment)
    error_message = "Environment must be one of: production, staging, development."
  }
}

variable "eks_version" {
  description = "EKS cluster version"
  type        = string
  default     = "1.27"
}

variable "domain_name" {
  description = "Domain name for the application"
  type        = string
  default     = "microagents.io"
}

variable "cost_center" {
  description = "Cost center for resource tagging"
  type        = string
  default     = "devops-platform"
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
  description = "Email address for alerts and notifications"
  type        = string
  default     = "alerts@microagents.io"
}

variable "database_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.r6g.4xlarge"
}

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.r6g.xlarge"
}