# deployment/terraform/aws/main.tf
terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
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
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
  
  backend "s3" {
    bucket         = "microagents-terraform-state"
    key            = "production/terraform.tfstate"
    region         = "eu-west-1"
    encrypt        = true
    dynamodb_table = "terraform-state-lock"
    kms_key_id     = "alias/terraform-state-key"
  }
}

# ==============================================================================
# Providers Configuration
# ==============================================================================

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
      CostCenter  = var.cost_center
      Compliance  = "pci-dss-4.0"
    }
  }
}

provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
  token                  = data.aws_eks_cluster_auth.cluster.token
}

provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
    token                  = data.aws_eks_cluster_auth.cluster.token
  }
}

# ==============================================================================
# Local Variables
# ==============================================================================

locals {
  # Naming conventions
  name_prefix = "${var.project_name}-${var.environment}"
  
  # Availability Zones
  availability_zones = slice(data.aws_availability_zones.available.names, 0, min(3, length(data.aws_availability_zones.available.names)))
  
  # CIDR blocks
  vpc_cidr = "10.0.0.0/16"
  
  # Subnet CIDRs
  public_subnets = [
    cidrsubnet(local.vpc_cidr, 8, 1),
    cidrsubnet(local.vpc_cidr, 8, 2),
    cidrsubnet(local.vpc_cidr, 8, 3),
  ]
  
  private_subnets = [
    cidrsubnet(local.vpc_cidr, 8, 11),
    cidrsubnet(local.vpc_cidr, 8, 12),
    cidrsubnet(local.vpc_cidr, 8, 13),
  ]
  
  database_subnets = [
    cidrsubnet(local.vpc_cidr, 8, 21),
    cidrsubnet(local.vpc_cidr, 8, 22),
    cidrsubnet(local.vpc_cidr, 8, 23),
  ]
  
  # EKS node instance types (cost-optimized)
  node_instance_types = ["t3.large", "t3a.large", "m5.large", "m5a.large"]
  
  # Spot instance types for cost optimization
  spot_instance_types = ["t3.large", "t3a.large", "m5.large", "m5a.large", "r5.large", "r5a.large"]
  
  # Database instance classes based on environment
  database_instance_class = var.environment == "production" ? "db.r6g.4xlarge" : "db.t4g.large"
  
  # Redis node types
  redis_node_type = var.environment == "production" ? "cache.r6g.xlarge" : "cache.t4g.micro"
  
  # Common tags
  common_tags = {
    Project       = var.project_name
    Environment   = var.environment
    ManagedBy     = "Terraform"
    CostCenter    = var.cost_center
    Compliance    = "pci-dss-4.0"
    Backup        = "true"
    BackupRetention = "30"
  }
}

# ==============================================================================
# Data Sources
# ==============================================================================

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_eks_cluster_auth" "cluster" {
  name = module.eks.cluster_name
}

data "aws_ami" "eks_optimized" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amazon-eks-node-${var.eks_version}-*"]
  }
}

# ==============================================================================
# VPC Networking
# ==============================================================================

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${local.name_prefix}-vpc"
  cidr = local.vpc_cidr

  # Multi-AZ configuration
  azs              = local.availability_zones
  public_subnets   = local.public_subnets
  private_subnets  = local.private_subnets
  database_subnets = local.database_subnets

  # High availability configuration
  enable_nat_gateway     = true
  single_nat_gateway     = false  # One NAT per AZ for HA
  one_nat_gateway_per_az = true

  # DNS and DHCP
  enable_dns_hostnames = true
  enable_dns_support   = true

  # VPC Flow Logs for security compliance
  enable_flow_log                      = true
  create_flow_log_cloudwatch_log_group = true
  create_flow_log_cloudwatch_iam_role  = true
  flow_log_max_aggregation_interval    = 60

  # VPC Endpoints for cost optimization (reduce NAT Gateway costs)
  enable_s3_endpoint              = true
  enable_dynamodb_endpoint        = true
  enable_ecr_api_endpoint         = true
  enable_ecr_dkr_endpoint         = true
  enable_ec2_endpoint             = true
  enable_ec2messages_endpoint     = true
  enable_ssm_endpoint             = true
  enable_ssmmessages_endpoint     = true
  enable_secretsmanager_endpoint  = true

  # Tags for cost allocation and management
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
    "SubnetType"             = "Public"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
    "SubnetType"                      = "Private"
  }

  database_subnet_tags = {
    "SubnetType" = "Database"
  }

  tags = local.common_tags
}

# ==============================================================================
# EKS Cluster Configuration
# ==============================================================================

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"

  cluster_name                   = "${local.name_prefix}-cluster"
  cluster_version                = var.eks_version
  cluster_endpoint_public_access = true
  cluster_endpoint_private_access = true

  # High Availability
  cluster_enabled_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  create_cloudwatch_log_group = true
  cloudwatch_log_group_retention_in_days = 30

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # EKS Managed Node Group
  eks_managed_node_groups = {
    # On-demand nodes for system workloads
    system = {
      name           = "system"
      instance_types = ["t3.large"]
      min_size       = 2
      max_size       = 5
      desired_size   = 2
      capacity_type  = "ON_DEMAND"

      # High availability across AZs
      subnet_ids = module.vpc.private_subnets

      # Security configuration
      enable_monitoring = true
      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 50
            volume_type           = "gp3"
            iops                  = 3000
            throughput            = 125
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      # Node labels and taints
      labels = {
        "node-type" = "system"
        "os"        = "linux"
      }

      taints = [
        {
          key    = "system"
          value  = "true"
          effect = "NO_SCHEDULE"
        }
      ]

      # Update configuration
      update_config = {
        max_unavailable_percentage = 33
      }

      # Security group for nodes
      vpc_security_group_ids = [
        aws_security_group.eks_nodes.id
      ]
    }

    # Spot instances for agent workloads (cost optimization)
    agent-spot = {
      name           = "agent-spot"
      instance_types = local.spot_instance_types
      min_size       = 3
      max_size       = 50
      desired_size   = 5
      capacity_type  = "SPOT"

      # Instance distribution for better spot availability
      instance_distribution = {
        on_demand_base_capacity                  = 0
        on_demand_percentage_above_base_capacity = 0
        spot_allocation_strategy                 = "capacity-optimized"
      }

      subnet_ids = module.vpc.private_subnets

      # Larger disk for agent data
      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 100
            volume_type           = "gp3"
            iops                  = 3000
            throughput            = 125
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      # Labels for node selection
      labels = {
        "node-type" = "agent"
        "cost-type" = "spot"
      }

      # Tolerations for spot instances
      taints = [
        {
          key    = "spot"
          value  = "true"
          effect = "NO_SCHEDULE"
        }
      ]

      # Update strategy
      update_config = {
        max_unavailable_percentage = 50  # Higher for spot instances
      }

      vpc_security_group_ids = [
        aws_security_group.eks_nodes.id
      ]
    }
  }

  # Cluster security group
  cluster_security_group_additional_rules = {
    # Allow nodes to communicate with control plane
    ingress_nodes_443 = {
      description                = "Node to control plane"
      protocol                   = "tcp"
      from_port                  = 443
      to_port                    = 443
      type                       = "ingress"
      source_node_security_group = true
    }
    # Allow private access from VPC
    ingress_private_443 = {
      description = "Private access to API"
      protocol    = "tcp"
      from_port   = 443
      to_port     = 443
      type        = "ingress"
      cidr_blocks = module.vpc.private_subnets_cidr_blocks
    }
  }

  # IAM Role for service accounts (IRSA)
  enable_irsa = true

  # Addon configuration
  cluster_addons = {
    coredns = {
      most_recent = true
    }
    kube-proxy = {
      most_recent = true
    }
    vpc-cni = {
      most_recent = true
    }
    aws-ebs-csi-driver = {
      most_recent = true
      service_account_role_arn = module.ebs_csi_iam_role.iam_role_arn
    }
  }

  tags = merge(local.common_tags, {
    "k8s.io/cluster-autoscaler/enabled"             = "true"
    "k8s.io/cluster-autoscaler/${local.name_prefix}-cluster" = "owned"
  })
}

# IAM role for EBS CSI Driver
module "ebs_csi_iam_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${local.name_prefix}-ebs-csi"
  attach_ebs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

# Security group for EKS nodes
resource "aws_security_group" "eks_nodes" {
  name        = "${local.name_prefix}-eks-nodes"
  description = "Security group for EKS nodes"
  vpc_id      = module.vpc.vpc_id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-eks-nodes-sg"
  })
}

# ==============================================================================
# RDS PostgreSQL Database
# ==============================================================================

module "rds" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.0"

  identifier = "${local.name_prefix}-postgres"

  engine               = "postgres"
  engine_version       = "15.3"
  family               = "postgres15"
  major_engine_version = "15"
  instance_class       = local.database_instance_class

  # High availability configuration
  multi_az               = true
  availability_zone      = local.availability_zones[0]
  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # Storage configuration
  allocated_storage     = 100
  max_allocated_storage = 500  # Auto-scaling storage
  storage_type         = "gp3"
  storage_encrypted    = true
  kms_key_id          = aws_kms_key.rds.arn

  # Database credentials (stored in Secrets Manager)
  manage_master_user_password = true
  master_user_secret_kms_key_id = aws_kms_key.secrets.arn

  # Database configuration
  db_name  = "microagents"
  port     = 5432
  username = "microagents_admin"

  # Performance tuning
  parameters = [
    {
      name  = "shared_preload_libraries"
      value = "pg_stat_statements,auto_explain"
    },
    {
      name  = "pg_stat_statements.track"
      value = "ALL"
    },
    {
      name  = "pg_stat_statements.max"
      value = "10000"
    },
    {
      name  = "track_activity_query_size"
      value = "2048"
    },
    {
      name  = "random_page_cost"
      value = "1.1"
    },
    {
      name  = "effective_cache_size"
      value = "8GB"
    }
  ]

  # Backup and disaster recovery
  backup_retention_period = 30
  backup_window          = "03:00-04:00"
  maintenance_window     = "Sun:04:00-Sun:06:00"
  delete_automated_backups = false

  # Enhanced monitoring
  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # Performance Insights
  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  performance_insights_kms_key_id       = aws_kms_key.rds.arn

  # Read replicas for HA and read scaling
  create_db_instance_read_replica = true
  replica_count = 2
  read_replica_availability_zones = slice(local.availability_zones, 1, 3)

  # Database deletion protection
  deletion_protection = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-postgres"
  })
}

# RDS security group
resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds"
  description = "Security group for RDS PostgreSQL"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Allow PostgreSQL from EKS nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-rds-sg"
  })
}

# ==============================================================================
# ElastiCache Redis Cluster
# ==============================================================================

module "elasticache" {
  source  = "terraform-aws-modules/elasticache/aws"
  version = "~> 4.0"

  cluster_id           = "${local.name_prefix}-redis"
  engine              = "redis"
  engine_version      = "7.0"
  family              = "redis7"
  node_type           = local.redis_node_type
  num_cache_nodes     = 3  # Multi-AZ for HA
  port                = 6379

  # High availability
  multi_az_enabled      = true
  automatic_failover_enabled = true
  subnet_ids           = module.vpc.database_subnets
  security_group_ids   = [aws_security_group.elasticache.id]

  # Performance and memory
  parameter_group_name = "default.redis7"
  snapshot_retention_limit = 7
  snapshot_window     = "03:30-04:30"
  maintenance_window  = "sun:05:00-sun:06:00"

  # Security
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token = random_password.redis_auth.result

  # Monitoring
  cloudwatch_metric_alarms_enabled = true
  notification_topic_arn = aws_sns_topic.alerts.arn

  # Log delivery configuration for compliance
  log_delivery_configuration = [
    {
      destination      = aws_cloudwatch_log_group.redis_logs.name
      destination_type = "cloudwatch-logs"
      log_format       = "json"
      log_type         = "slow-log"
    },
    {
      destination      = aws_cloudwatch_log_group.redis_logs.name
      destination_type = "cloudwatch-logs"
      log_format       = "json"
      log_type         = "engine-log"
    }
  ]

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-redis"
  })
}

resource "random_password" "redis_auth" {
  length  = 32
  special = true
}

# ElastiCache security group
resource "aws_security_group" "elasticache" {
  name        = "${local.name_prefix}-elasticache"
  description = "Security group for ElastiCache Redis"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Allow Redis from EKS nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-elasticache-sg"
  })
}

# ==============================================================================
# S3 for Backups and Storage
# ==============================================================================

module "s3_backup" {
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "~> 3.0"

  bucket = "${local.name_prefix}-backups-${data.aws_caller_identity.current.account_id}"
  acl    = "private"

  # Security and compliance
  force_destroy = false
  versioning = {
    enabled = true
  }

  # Encryption
  server_side_encryption_configuration = {
    rule = {
      apply_server_side_encryption_by_default = {
        kms_master_key_id = aws_kms_key.s3.arn
        sse_algorithm     = "aws:kms"
      }
    }
  }

  # Lifecycle rules for cost optimization
  lifecycle_rule = [
    {
      id      = "backup-transition"
      enabled = true

      transition = [
        {
          days          = 30
          storage_class = "STANDARD_IA"
        },
        {
          days          = 90
          storage_class = "GLACIER"
        }
      ]

      expiration = {
        days = 365
      }
    }
  ]

  # Logging for compliance
  logging = {
    target_bucket = aws_s3_bucket.access_logs.id
    target_prefix = "backup-logs/"
  }

  # Bucket policies
  attach_policy = true
  policy = data.aws_iam_policy_document.s3_backup_policy.json

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-backups"
    BackupType = "application"
  })
}

# S3 for access logs
resource "aws_s3_bucket" "access_logs" {
  bucket = "${local.name_prefix}-access-logs-${data.aws_caller_identity.current.account_id}"

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-access-logs"
  })
}

# KMS key for S3 encryption
resource "aws_kms_key" "s3" {
  description             = "KMS key for S3 bucket encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy = data.aws_iam_policy_document.kms_s3_policy.json

  tags = local.common_tags
}

# ==============================================================================
# CloudFront CDN
# ==============================================================================

module "cloudfront" {
  source  = "terraform-aws-modules/cloudfront/aws"
  version = "~> 3.0"

  comment         = "${local.name_prefix}-cdn"
  enabled         = true
  is_ipv6_enabled = true
  price_class     = "PriceClass_100"  # US, Canada, Europe

  # Origin - ALB
  origin = {
    alb = {
      domain_name = module.alb.lb_dns_name
      origin_id   = "alb"

      custom_origin_config = {
        http_port              = 80
        https_port             = 443
        origin_protocol_policy = "http-only"
        origin_ssl_protocols   = ["TLSv1.2"]
      }
    }
  }

  # WAF integration
  web_acl_id = aws_wafv2_web_acl.cloudfront.arn

  # Default cache behavior
  default_cache_behavior = {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods  = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods   = ["GET", "HEAD"]
    compress         = true
    cache_policy_id  = "658327ea-f89d-4fab-a63d-7e88639e58f6"  # Managed-CachingOptimized
    origin_request_policy_id = "88a5eaf4-2fd4-4709-b370-b4c650ea3fcf"  # Managed-AllViewer
  }

  # Viewer certificates
  viewer_certificate = {
    acm_certificate_arn = module.acm.acm_certificate_arn
    ssl_support_method  = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  # Custom error responses
  custom_error_response = [
    {
      error_code         = 403
      response_code      = 200
      response_page_path = "/index.html"
    },
    {
      error_code         = 404
      response_code      = 200
      response_page_path = "/index.html"
    }
  ]

  # Logging
  logging_config = {
    bucket = module.s3_backup.s3_bucket_bucket_domain_name
    prefix = "cloudfront-logs/"
  }

  # Geo restrictions (optional)
  geo_restriction = {
    restriction_type = "none"
  }

  tags = local.common_tags
}

# ==============================================================================
# WAF Rules
# ==============================================================================

resource "aws_wafv2_web_acl" "cloudfront" {
  name        = "${local.name_prefix}-waf-cloudfront"
  description = "WAF for CloudFront distribution"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesCommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesKnownBadInputsRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "RateLimit"
    priority = 10

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "RateLimit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}-waf"
    sampled_requests_enabled   = true
  }

  tags = local.common_tags
}

# ==============================================================================
# Application Load Balancer
# ==============================================================================

module "alb" {
  source  = "terraform-aws-modules/alb/aws"
  version = "~> 8.0"

  name = "${local.name_prefix}-alb"

  load_balancer_type = "application"
  internal           = false

  vpc_id          = module.vpc.vpc_id
  subnets         = module.vpc.public_subnets
  security_groups = [aws_security_group.alb.id]

  # WAF integration
  web_acl_arn = aws_wafv2_web_acl.alb.arn

  # Access logs for compliance
  access_logs = {
    bucket = aws_s3_bucket.access_logs.id
    prefix = "alb-logs/"
  }

  # HTTPS listener
  https_listeners = [
    {
      port               = 443
      protocol           = "HTTPS"
      certificate_arn    = module.acm.acm_certificate_arn
      target_group_index = 0
    }
  ]

  # HTTP redirect to HTTPS
  http_tcp_listeners = [
    {
      port        = 80
      protocol    = "HTTP"
      action_type = "redirect"
      redirect = {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  ]

  # Target groups
  target_groups = [
    {
      name             = "${local.name_prefix}-api"
      backend_protocol = "HTTP"
      backend_port     = 8080
      target_type      = "ip"

      health_check = {
        enabled             = true
        interval            = 30
        path                = "/health"
        port                = "traffic-port"
        healthy_threshold   = 3
        unhealthy_threshold = 3
        timeout             = 6
        protocol            = "HTTP"
        matcher             = "200"
      }

      # Slow start for new targets
      slow_start = 30

      # Stickiness for session affinity
      stickiness = {
        type            = "lb_cookie"
        cookie_duration = 86400
        enabled         = true
      }

      # Deregistration delay for graceful shutdown
      deregistration_delay = 60
    }
  ]

  tags = local.common_tags
}

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Security group for ALB"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-alb-sg"
  })
}

resource "aws_wafv2_web_acl" "alb" {
  name        = "${local.name_prefix}-waf-alb"
  description = "WAF for ALB"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesCommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}-waf-alb"
    sampled_requests_enabled   = true
  }

  tags = local.common_tags
}

# ==============================================================================
# ACM Certificate
# ==============================================================================

module "acm" {
  source  = "terraform-aws-modules/acm/aws"
  version = "~> 4.0"

  domain_name = var.domain_name
  zone_id     = data.aws_route53_zone.selected.zone_id

  subject_alternative_names = [
    "*.${var.domain_name}",
    "api.${var.domain_name}",
    "app.${var.domain_name}"
  ]

  validation_method = "DNS"

  tags = local.common_tags
}

data "aws_route53_zone" "selected" {
  name         = var.domain_name
  private_zone = false
}

# ==============================================================================
# IAM Roles and Policies
# ==============================================================================

# IAM role for RDS Enhanced Monitoring
resource "aws_iam_role" "rds_monitoring" {
  name = "${local.name_prefix}-rds-monitoring"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "monitoring.rds.amazonaws.com"
        }
      }
    ]
  })

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
  ]

  tags = local.common_tags
}

# IAM policies for S3 backup bucket
data "aws_iam_policy_document" "s3_backup_policy" {
  statement {
    sid    = "AllowTerraformAccess"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [data.aws_caller_identity.current.arn]
    }
    actions = [
      "s3:*"
    ]
    resources = [
      "arn:aws:s3:::${local.name_prefix}-backups-${data.aws_caller_identity.current.account_id}",
      "arn:aws:s3:::${local.name_prefix}-backups-${data.aws_caller_identity.current.account_id}/*"
    ]
  }

  statement {
    sid    = "DenyUnencryptedUploads"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${local.name_prefix}-backups-${data.aws_caller_identity.current.account_id}/*"
    ]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid    = "DenyIncorrectEncryptionHeader"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${local.name_prefix}-backups-${data.aws_caller_identity.current.account_id}/*"
    ]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [aws_kms_key.s3.arn]
    }
  }
}

# KMS policy for S3
data "aws_iam_policy_document" "kms_s3_policy" {
  statement {
    sid    = "EnableIAMUserPermissions"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowS3Encryption"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey"
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${data.aws_region.current.name}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:s3:arn"
      values   = ["arn:aws:s3:::${local.name_prefix}-backups-${data.aws_caller_identity.current.account_id}/*"]
    }
  }
}

# IAM roles for EKS nodes
module "eks_node_iam" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version = "~> 5.0"

  create_role = true
  role_name   = "${local.name_prefix}-eks-node"

  trusted_role_services = [
    "ec2.amazonaws.com"
  ]

  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
  ]

  role_requires_mfa = false

  tags = local.common_tags
}

# ==============================================================================
# KMS Keys
# ==============================================================================

# KMS for RDS
resource "aws_kms_key" "rds" {
  description             = "KMS key for RDS encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableIAMUserPermissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowRDSEncryption"
        Effect = "Allow"
        Principal = {
          Service = "rds.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
      }
    ]
  })

  tags = local.common_tags
}

# KMS for Secrets Manager
resource "aws_kms_key" "secrets" {
  description             = "KMS key for Secrets Manager"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = local.common_tags
}

# ==============================================================================
# Monitoring (CloudWatch)
# ==============================================================================

# CloudWatch Log Groups
resource "aws_cloudwatch_log_group" "redis_logs" {
  name              = "/aws/elasticache/${local.name_prefix}-redis"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.s3.arn

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "eks_logs" {
  name              = "/aws/eks/${local.name_prefix}-cluster"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.s3.arn

  tags = local.common_tags
}

# CloudWatch Alarms
resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name_prefix}-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name        = "CPUUtilization"
  namespace          = "AWS/RDS"
  period             = "300"
  statistic          = "Average"
  threshold          = "80"
  alarm_description  = "RDS CPU utilization is high"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = module.rds.db_instance_id
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${local.name_prefix}-redis-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name        = "DatabaseMemoryUsagePercentage"
  namespace          = "AWS/ElastiCache"
  period             = "300"
  statistic          = "Average"
  threshold          = "85"
  alarm_description  = "Redis memory usage is high"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]

  dimensions = {
    CacheClusterId = module.elasticache.elasticache_cluster_id
  }

  tags = local.common_tags
}

# CloudWatch Dashboard
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name_prefix}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", module.rds.db_instance_id],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", module.rds.db_instance_id],
            ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", module.rds.db_instance_id]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "RDS Metrics"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ElastiCache", "CPUUtilization", "CacheClusterId", module.elasticache.elasticache_cluster_id],
            ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "CacheClusterId", module.elasticache.elasticache_cluster_id],
            ["AWS/ElastiCache", "CurrConnections", "CacheClusterId", module.elasticache.elasticache_cluster_id]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "ElastiCache Metrics"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", module.alb.lb_arn_suffix],
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", module.alb.lb_arn_suffix],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", module.alb.lb_arn_suffix]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "ALB Metrics"
        }
      }
    ]
  })
}

# SNS Topic for alerts
resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
  kms_master_key_id = aws_kms_key.secrets.arn

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ==============================================================================
# Cost Management (Budgets)
# ==============================================================================

resource "aws_budgets_budget" "monthly" {
  name              = "${local.name_prefix}-monthly-budget"
  budget_type       = "COST"
  limit_amount      = var.monthly_budget
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2024-01-01_00:00"

  cost_types {
    include_credit             = false
    include_discount           = true
    include_other_subscription = true
    include_recurring          = true
    include_refund             = false
    include_subscription       = true
    include_support            = true
    include_tax                = true
    include_upfront            = true
    use_blended                = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 150
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# ==============================================================================
# Outputs
# ==============================================================================

output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  description = "EKS cluster CA certificate"
  value       = module.eks.cluster_certificate_authority_data
  sensitive   = true
}

output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = module.rds.db_instance_address
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = module.elasticache.elasticache_cluster_configuration_endpoint
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = module.alb.lb_dns_name
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain"
  value       = module.cloudfront.cloudfront_distribution_domain_name
}

output "backup_bucket" {
  description = "S3 backup bucket name"
  value       = module.s3_backup.s3_bucket_id
}

output "kubeconfig" {
  description = "Kubectl config for the EKS cluster"
  value       = <<EOT
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: ${module.eks.cluster_certificate_authority_data}
    server: ${module.eks.cluster_endpoint}
  name: ${module.eks.cluster_name}
contexts:
- context:
    cluster: ${module.eks.cluster_name}
    user: ${module.eks.cluster_name}
  name: ${module.eks.cluster_name}
current-context: ${module.eks.cluster_name}
kind: Config
preferences: {}
users:
- name: ${module.eks.cluster_name}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: aws
      args:
        - eks
        - get-token
        - --cluster-name
        - ${module.eks.cluster_name}
        - --region
        - ${var.aws_region}
EOT
  sensitive = true
}