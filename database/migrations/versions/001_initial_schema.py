"""
Database migration: Initial schema for MicroAgents Platform.
Version: 001_initial_schema.py
Created: 2024-01-15
Description: Initial database schema with comprehensive tables for 1400 micro-agents platform.
"""

from datetime import datetime
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import (
    UUID, JSONB, ARRAY, ENUM, TSVECTOR, INET, MACADDR
)
from sqlalchemy.sql import text

# ============================================================================
# ENUM TYPES
# ============================================================================

def create_enums():
    """Create custom ENUM types for the database."""
    
    # Agent status enumeration
    op.execute("""
    CREATE TYPE agent_status AS ENUM (
        'REGISTERED',
        'ACTIVE',
        'IDLE',
        'ERROR',
        'STOPPED',
        'MAINTENANCE',
        'DEPRECATED'
    )
    """)
    
    # Execution status enumeration
    op.execute("""
    CREATE TYPE execution_status AS ENUM (
        'PENDING',
        'RUNNING',
        'COMPLETED',
        'FAILED',
        'TIMEOUT',
        'CANCELLED',
        'RETRYING'
    )
    """)
    
    # Severity levels for alerts and events
    op.execute("""
    CREATE TYPE severity_level AS ENUM (
        'LOW',
        'MEDIUM',
        'HIGH',
        'CRITICAL'
    )
    """)
    
    # Billing periods
    op.execute("""
    CREATE TYPE billing_period AS ENUM (
        'HOURLY',
        'DAILY',
        'WEEKLY',
        'MONTHLY',
        'QUARTERLY',
        'ANNUAL'
    )
    """)
    
    # User roles
    op.execute("""
    CREATE TYPE user_role AS ENUM (
        'SUPER_ADMIN',
        'TENANT_ADMIN',
        'DEVELOPER',
        'OPERATOR',
        'VIEWER',
        'BILLING',
        'AUDITOR'
    )
    """)
    
    # Compliance standards
    op.execute("""
    CREATE TYPE compliance_standard AS ENUM (
        'SOC2',
        'ISO27001',
        'GDPR',
        'HIPAA',
        'PCI_DSS',
        'NIST',
        'FEDRAMP'
    )
    """)
    
    # Cloud providers
    op.execute("""
    CREATE TYPE cloud_provider AS ENUM (
        'AWS',
        'AZURE',
        'GCP',
        'ORACLE',
        'ALIBABA',
        'IBM',
        'DIGITAL_OCEAN'
    )
    """)

def drop_enums():
    """Drop custom ENUM types."""
    op.execute("DROP TYPE IF EXISTS agent_status CASCADE")
    op.execute("DROP TYPE IF EXISTS execution_status CASCADE")
    op.execute("DROP TYPE IF EXISTS severity_level CASCADE")
    op.execute("DROP TYPE IF EXISTS billing_period CASCADE")
    op.execute("DROP TYPE IF EXISTS user_role CASCADE")
    op.execute("DROP TYPE IF EXISTS compliance_standard CASCADE")
    op.execute("DROP TYPE IF EXISTS cloud_provider CASCADE")

# ============================================================================
# 1. AGENT REGISTRY TABLES
# ============================================================================

def create_agent_tables():
    """Create tables for agent registry and management."""
    
    # Agent types catalog
    op.create_table(
        'agent_types',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('description', sa.Text),
        sa.Column('category', sa.String(50), nullable=False,
                 comment='e.g., monitoring, security, cost, performance'),
        sa.Column('version', sa.String(20), nullable=False),
        sa.Column('docker_image', sa.String(255)),
        sa.Column('resource_requirements', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('config_schema', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('is_public', sa.Boolean, nullable=False, default=True),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('deprecated_at', sa.DateTime),
        # Indexes
        sa.Index('idx_agent_types_category', 'category'),
        sa.Index('idx_agent_types_version', 'version'),
        sa.Index('idx_agent_types_public', 'is_public'),
        comment='Catalog of available agent types'
    )
    
    # Registered agents
    op.create_table(
        'agents',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False,
                 comment='Multi-tenant isolation'),
        sa.Column('agent_type_id', UUID(), sa.ForeignKey('agent_types.id',
                 ondelete='RESTRICT'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('status', sa.Enum('agent_status', name='agent_status'),
                 nullable=False, server_default='REGISTERED'),
        sa.Column('config', JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column('last_heartbeat', sa.DateTime),
        sa.Column('health_score', sa.Integer,
                 server_default=sa.text('100')),
        sa.Column('deployment_info', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('resource_usage', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('tags', ARRAY(sa.String(50)),
                 server_default=sa.text("'{}'::text[]")),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('deleted_at', sa.DateTime),
        # Indexes
        sa.Index('idx_agents_tenant', 'tenant_id'),
        sa.Index('idx_agents_status', 'status'),
        sa.Index('idx_agents_heartbeat', 'last_heartbeat'),
        sa.Index('idx_agents_type', 'agent_type_id'),
        sa.Index('idx_agents_tags', 'tags', postgresql_using='gin'),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_agent_tenant_name'),
        comment='Registered agents with their current state'
    )
    
    # Agent dependencies
    op.create_table(
        'agent_dependencies',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('agent_id', UUID(), sa.ForeignKey('agents.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('depends_on_agent_id', UUID(), sa.ForeignKey('agents.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('dependency_type', sa.String(50), nullable=False,
                 comment='e.g., REQUIRES, OPTIONAL, CONFLICTS'),
        sa.Column('config_mapping', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_agent_deps_agent', 'agent_id'),
        sa.Index('idx_agent_deps_depends', 'depends_on_agent_id'),
        sa.UniqueConstraint('agent_id', 'depends_on_agent_id',
                          name='uq_agent_dependency'),
        comment='Dependencies between agents'
    )

# ============================================================================
# 2. EXECUTION HISTORY
# ============================================================================

def create_execution_tables():
    """Create tables for execution history and job tracking."""
    
    # Execution jobs
    op.create_table(
        'execution_jobs',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('agent_id', UUID(), sa.ForeignKey('agents.id',
                 ondelete='SET NULL')),
        sa.Column('trigger_type', sa.String(50), nullable=False,
                 comment='e.g., SCHEDULED, MANUAL, EVENT, API'),
        sa.Column('trigger_config', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('input_data', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('status', sa.Enum('execution_status', name='execution_status'),
                 nullable=False, server_default='PENDING'),
        sa.Column('result_data', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('error_message', sa.Text),
        sa.Column('error_stack', sa.Text),
        sa.Column('started_at', sa.DateTime),
        sa.Column('completed_at', sa.DateTime),
        sa.Column('duration_ms', sa.BigInteger),
        sa.Column('retry_count', sa.Integer, server_default=sa.text('0')),
        sa.Column('max_retries', sa.Integer, server_default=sa.text('3')),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by', UUID()),
        # Partitioning by month
        sa.PrimaryKeyConstraint('id', 'created_at'),
        # Indexes
        sa.Index('idx_exec_jobs_tenant', 'tenant_id'),
        sa.Index('idx_exec_jobs_status', 'status'),
        sa.Index('idx_exec_jobs_agent', 'agent_id'),
        sa.Index('idx_exec_jobs_created', 'created_at'),
        sa.Index('idx_exec_jobs_completed', 'completed_at'),
        postgresql_partition_by='RANGE (created_at)',
        comment='Execution jobs with partitioning for performance'
    )
    
    # Create partitions for execution_jobs
    op.execute("""
    CREATE TABLE execution_jobs_2024_01 PARTITION OF execution_jobs
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
    
    CREATE TABLE execution_jobs_2024_02 PARTITION OF execution_jobs
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
    """)
    
    # Execution steps
    op.create_table(
        'execution_steps',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('job_id', UUID(), sa.ForeignKey('execution_jobs.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('step_number', sa.Integer, nullable=False),
        sa.Column('step_name', sa.String(100), nullable=False),
        sa.Column('agent_type_id', UUID(), sa.ForeignKey('agent_types.id')),
        sa.Column('input_data', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('output_data', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('status', sa.Enum('execution_status', name='execution_status'),
                 nullable=False, server_default='PENDING'),
        sa.Column('error_message', sa.Text),
        sa.Column('started_at', sa.DateTime),
        sa.Column('completed_at', sa.DateTime),
        sa.Column('duration_ms', sa.BigInteger),
        # Indexes
        sa.Index('idx_exec_steps_job', 'job_id'),
        sa.Index('idx_exec_steps_status', 'status'),
        sa.Index('idx_exec_steps_number', 'step_number'),
        sa.UniqueConstraint('job_id', 'step_number',
                          name='uq_step_job_number'),
        comment='Detailed steps within execution jobs'
    )
    
    # Job schedules
    op.create_table(
        'job_schedules',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('job_name', sa.String(200), nullable=False),
        sa.Column('cron_expression', sa.String(50), nullable=False),
        sa.Column('timezone', sa.String(50), server_default='UTC'),
        sa.Column('agent_id', UUID(), sa.ForeignKey('agents.id')),
        sa.Column('config', JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('last_execution', sa.DateTime),
        sa.Column('next_execution', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_job_schedules_tenant', 'tenant_id'),
        sa.Index('idx_job_schedules_active', 'is_active'),
        sa.Index('idx_job_schedules_next', 'next_execution'),
        sa.UniqueConstraint('tenant_id', 'job_name',
                          name='uq_schedule_tenant_name'),
        comment='Scheduled job configurations'
    )

# ============================================================================
# 3. BUSINESS VALUE CALCULATIONS
# ============================================================================

def create_business_value_tables():
    """Create tables for business value calculations and ROI tracking."""
    
    # ROI calculations
    op.create_table(
        'roi_calculations',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('calculation_date', sa.Date, nullable=False,
                 server_default=sa.text('CURRENT_DATE')),
        sa.Column('period_start', sa.Date, nullable=False),
        sa.Column('period_end', sa.Date, nullable=False),
        sa.Column('category', sa.String(50), nullable=False,
                 comment='e.g., cost_savings, productivity, security'),
        sa.Column('metric_name', sa.String(100), nullable=False),
        sa.Column('baseline_value', sa.Numeric(15, 2)),
        sa.Column('current_value', sa.Numeric(15, 2)),
        sa.Column('improvement_value', sa.Numeric(15, 2)),
        sa.Column('improvement_percentage', sa.Numeric(5, 2)),
        sa.Column('monetary_value', sa.Numeric(15, 2),
                 comment='Value in USD'),
        sa.Column('confidence_score', sa.Numeric(3, 2),
                 server_default=sa.text('0.95')),
        sa.Column('data_sources', ARRAY(sa.String(200))),
        sa.Column('assumptions', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Partitioning by month
        sa.PrimaryKeyConstraint('id', 'calculation_date'),
        # Indexes
        sa.Index('idx_roi_tenant', 'tenant_id'),
        sa.Index('idx_roi_category', 'category'),
        sa.Index('idx_roi_date', 'calculation_date'),
        sa.Index('idx_roi_period', 'period_start', 'period_end'),
        postgresql_partition_by='RANGE (calculation_date)',
        comment='ROI calculations with partitioning'
    )
    
    # Create partitions for roi_calculations
    op.execute("""
    CREATE TABLE roi_calculations_2024_01 PARTITION OF roi_calculations
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
    
    CREATE TABLE roi_calculations_2024_02 PARTITION OF roi_calculations
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
    """)
    
    # Cost savings
    op.create_table(
        'cost_savings',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('agent_id', UUID(), sa.ForeignKey('agents.id')),
        sa.Column('savings_date', sa.Date, nullable=False,
                 server_default=sa.text('CURRENT_DATE')),
        sa.Column('resource_type', sa.String(50), nullable=False),
        sa.Column('resource_id', sa.String(100)),
        sa.Column('provider', sa.Enum('cloud_provider', name='cloud_provider')),
        sa.Column('region', sa.String(50)),
        sa.Column('baseline_cost', sa.Numeric(15, 2), nullable=False),
        sa.Column('optimized_cost', sa.Numeric(15, 2), nullable=False),
        sa.Column('savings_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('savings_percentage', sa.Numeric(5, 2)),
        sa.Column('recommendation', sa.Text),
        sa.Column('action_taken', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_cost_savings_tenant', 'tenant_id'),
        sa.Index('idx_cost_savings_date', 'savings_date'),
        sa.Index('idx_cost_savings_agent', 'agent_id'),
        sa.Index('idx_cost_savings_provider', 'provider'),
        comment='Detailed cost savings tracking'
    )
    
    # Business KPIs
    op.create_table(
        'business_kpis',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('kpi_name', sa.String(100), nullable=False),
        sa.Column('kpi_category', sa.String(50), nullable=False,
                 comment='e.g., financial, operational, customer'),
        sa.Column('target_value', sa.Numeric(15, 4)),
        sa.Column('current_value', sa.Numeric(15, 4)),
        sa.Column('unit', sa.String(20)),
        sa.Column('calculation_logic', sa.Text),
        sa.Column('data_source', sa.String(200)),
        sa.Column('refresh_frequency', sa.String(20),
                 server_default='DAILY'),
        sa.Column('last_calculated', sa.DateTime),
        sa.Column('trend', sa.String(20),
                 comment='e.g., UP, DOWN, STABLE'),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_kpis_tenant', 'tenant_id'),
        sa.Index('idx_kpis_category', 'kpi_category'),
        sa.Index('idx_kpis_name', 'kpi_name'),
        sa.UniqueConstraint('tenant_id', 'kpi_name',
                          name='uq_kpi_tenant_name'),
        comment='Business KPI definitions and current values'
    )

# ============================================================================
# 4. USER MANAGEMENT
# ============================================================================

def create_user_tables():
    """Create tables for user management and authentication."""
    
    # Tenants/Organizations
    op.create_table(
        'tenants',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('slug', sa.String(50), nullable=False, unique=True),
        sa.Column('description', sa.Text),
        sa.Column('contact_email', sa.String(255)),
        sa.Column('billing_email', sa.String(255)),
        sa.Column('plan_tier', sa.String(50), nullable=False,
                 server_default='PROFESSIONAL'),
        sa.Column('max_agents', sa.Integer, server_default=sa.text('500')),
        sa.Column('max_users', sa.Integer, server_default=sa.text('50')),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('trial_ends_at', sa.DateTime),
        sa.Column('billing_starts_at', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_tenants_slug', 'slug'),
        sa.Index('idx_tenants_active', 'is_active'),
        sa.Index('idx_tenants_plan', 'plan_tier'),
        comment='Tenant/organization information'
    )
    
    # Users
    op.create_table(
        'users',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), sa.ForeignKey('tenants.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('hashed_password', sa.String(255)),
        sa.Column('full_name', sa.String(150)),
        sa.Column('avatar_url', sa.String(500)),
        sa.Column('role', sa.Enum('user_role', name='user_role'),
                 nullable=False, server_default='VIEWER'),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('is_email_verified', sa.Boolean, nullable=False, default=False),
        sa.Column('last_login_at', sa.DateTime),
        sa.Column('last_login_ip', INET()),
        sa.Column('mfa_enabled', sa.Boolean, nullable=False, default=False),
        sa.Column('mfa_secret', sa.String(32)),
        sa.Column('timezone', sa.String(50), server_default='UTC'),
        sa.Column('preferences', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('deleted_at', sa.DateTime),
        # Indexes
        sa.Index('idx_users_tenant', 'tenant_id'),
        sa.Index('idx_users_email', 'email'),
        sa.Index('idx_users_role', 'role'),
        sa.Index('idx_users_active', 'is_active'),
        sa.UniqueConstraint('tenant_id', 'email',
                          name='uq_user_tenant_email'),
        comment='User accounts with multi-tenant support'
    )
    
    # API Keys
    op.create_table(
        'api_keys',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), sa.ForeignKey('tenants.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(), sa.ForeignKey('users.id',
                 ondelete='CASCADE')),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('key_prefix', sa.String(8), nullable=False),
        sa.Column('hashed_key', sa.String(255), nullable=False),
        sa.Column('scopes', ARRAY(sa.String(50)),
                 server_default=sa.text("'{}'::text[]")),
        sa.Column('last_used_at', sa.DateTime),
        sa.Column('last_used_ip', INET()),
        sa.Column('expires_at', sa.DateTime),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by', UUID()),
        # Indexes
        sa.Index('idx_api_keys_tenant', 'tenant_id'),
        sa.Index('idx_api_keys_user', 'user_id'),
        sa.Index('idx_api_keys_prefix', 'key_prefix'),
        sa.Index('idx_api_keys_active', 'is_active'),
        sa.Index('idx_api_keys_expires', 'expires_at'),
        sa.UniqueConstraint('tenant_id', 'name',
                          name='uq_api_key_tenant_name'),
        comment='API key management'
    )
    
    # User sessions
    op.create_table(
        'user_sessions',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(), sa.ForeignKey('users.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('session_token', sa.String(255), nullable=False, unique=True),
        sa.Column('refresh_token', sa.String(255)),
        sa.Column('user_agent', sa.Text),
        sa.Column('ip_address', INET()),
        sa.Column('device_info', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('revoked_at', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_user_sessions_user', 'user_id'),
        sa.Index('idx_user_sessions_token', 'session_token'),
        sa.Index('idx_user_sessions_expires', 'expires_at'),
        comment='User session management'
    )

# ============================================================================
# 5. BILLING TABLES
# ============================================================================

def create_billing_tables():
    """Create tables for billing and subscription management."""
    
    # Subscription plans
    op.create_table(
        'subscription_plans',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('slug', sa.String(50), nullable=False, unique=True),
        sa.Column('description', sa.Text),
        sa.Column('price_monthly', sa.Numeric(10, 2), nullable=False),
        sa.Column('price_annual', sa.Numeric(10, 2)),
        sa.Column('currency', sa.String(3), nullable=False,
                 server_default='USD'),
        sa.Column('max_agents', sa.Integer, nullable=False),
        sa.Column('max_users', sa.Integer, nullable=False),
        sa.Column('features', JSONB(), nullable=False,
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('is_public', sa.Boolean, nullable=False, default=True),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_plans_slug', 'slug'),
        sa.Index('idx_plans_active', 'is_active'),
        sa.Index('idx_plans_public', 'is_public'),
        comment='Subscription plan definitions'
    )
    
    # Tenant subscriptions
    op.create_table(
        'tenant_subscriptions',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), sa.ForeignKey('tenants.id',
                 ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('plan_id', UUID(), sa.ForeignKey('subscription_plans.id'),
                 nullable=False),
        sa.Column('status', sa.String(20), nullable=False,
                 server_default='ACTIVE',
                 comment='ACTIVE, PAST_DUE, CANCELLED, EXPIRED'),
        sa.Column('billing_period', sa.Enum('billing_period',
                 name='billing_period'), nullable=False,
                 server_default='MONTHLY'),
        sa.Column('current_period_start', sa.DateTime, nullable=False),
        sa.Column('current_period_end', sa.DateTime, nullable=False),
        sa.Column('cancel_at_period_end', sa.Boolean, nullable=False,
                 default=False),
        sa.Column('cancelled_at', sa.DateTime),
        sa.Column('trial_ends_at', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_subscriptions_tenant', 'tenant_id'),
        sa.Index('idx_subscriptions_status', 'status'),
        sa.Index('idx_subscriptions_period', 'current_period_end'),
        comment='Tenant subscription information'
    )
    
    # Invoices
    op.create_table(
        'invoices',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), sa.ForeignKey('tenants.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('invoice_number', sa.String(50), nullable=False, unique=True),
        sa.Column('period_start', sa.Date, nullable=False),
        sa.Column('period_end', sa.Date, nullable=False),
        sa.Column('issue_date', sa.Date, nullable=False),
        sa.Column('due_date', sa.Date, nullable=False),
        sa.Column('subtotal', sa.Numeric(15, 2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(15, 2), server_default=sa.text('0')),
        sa.Column('total_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False,
                 server_default='USD'),
        sa.Column('status', sa.String(20), nullable=False,
                 server_default='DRAFT',
                 comment='DRAFT, ISSUED, PAID, OVERDUE, VOID'),
        sa.Column('paid_at', sa.DateTime),
        sa.Column('payment_method', sa.String(50)),
        sa.Column('pdf_url', sa.String(500)),
        sa.Column('notes', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_invoices_tenant', 'tenant_id'),
        sa.Index('idx_invoices_number', 'invoice_number'),
        sa.Index('idx_invoices_status', 'status'),
        sa.Index('idx_invoices_due', 'due_date'),
        comment='Billing invoices'
    )
    
    # Invoice line items
    op.create_table(
        'invoice_line_items',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('invoice_id', UUID(), sa.ForeignKey('invoices.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('description', sa.String(255), nullable=False),
        sa.Column('quantity', sa.Numeric(10, 4), nullable=False),
        sa.Column('unit_price', sa.Numeric(15, 4), nullable=False),
        sa.Column('amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('tax_rate', sa.Numeric(5, 4), server_default=sa.text('0')),
        sa.Column('tax_amount', sa.Numeric(15, 2), server_default=sa.text('0')),
        sa.Column('metadata', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_line_items_invoice', 'invoice_id'),
        comment='Detailed invoice line items'
    )

# ============================================================================
# 6. AUDIT LOGS
# ============================================================================

def create_audit_tables():
    """Create tables for audit logging and security events."""
    
    # Audit logs
    op.create_table(
        'audit_logs',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID()),
        sa.Column('user_id', UUID(), sa.ForeignKey('users.id')),
        sa.Column('api_key_id', UUID(), sa.ForeignKey('api_keys.id')),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('event_action', sa.String(50), nullable=False,
                 comment='e.g., CREATE, READ, UPDATE, DELETE'),
        sa.Column('resource_type', sa.String(100)),
        sa.Column('resource_id', UUID()),
        sa.Column('details_before', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('details_after', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('ip_address', INET()),
        sa.Column('user_agent', sa.Text),
        sa.Column('status_code', sa.Integer),
        sa.Column('error_message', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Partitioning by month
        sa.PrimaryKeyConstraint('id', 'created_at'),
        # Indexes
        sa.Index('idx_audit_logs_tenant', 'tenant_id'),
        sa.Index('idx_audit_logs_user', 'user_id'),
        sa.Index('idx_audit_logs_event', 'event_type'),
        sa.Index('idx_audit_logs_resource', 'resource_type', 'resource_id'),
        sa.Index('idx_audit_logs_created', 'created_at'),
        postgresql_partition_by='RANGE (created_at)',
        comment='Comprehensive audit logging with partitioning'
    )
    
    # Create partitions for audit_logs
    op.execute("""
    CREATE TABLE audit_logs_2024_01 PARTITION OF audit_logs
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
    
    CREATE TABLE audit_logs_2024_02 PARTITION OF audit_logs
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
    """)
    
    # Security events
    op.create_table(
        'security_events',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID()),
        sa.Column('severity', sa.Enum('severity_level', name='severity_level'),
                 nullable=False, server_default='LOW'),
        sa.Column('event_type', sa.String(100), nullable=False,
                 comment='e.g., LOGIN_FAILURE, API_ABUSE, MALWARE_DETECTED'),
        sa.Column('source_ip', INET()),
        sa.Column('user_id', UUID(), sa.ForeignKey('users.id')),
        sa.Column('resource', sa.String(255)),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('details', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('action_taken', sa.String(100)),
        sa.Column('status', sa.String(20), nullable=False,
                 server_default='OPEN',
                 comment='OPEN, INVESTIGATING, RESOLVED, FALSE_POSITIVE'),
        sa.Column('assigned_to', UUID(), sa.ForeignKey('users.id')),
        sa.Column('resolved_at', sa.DateTime),
        sa.Column('resolved_by', UUID(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_security_events_tenant', 'tenant_id'),
        sa.Index('idx_security_events_severity', 'severity'),
        sa.Index('idx_security_events_type', 'event_type'),
        sa.Index('idx_security_events_source_ip', 'source_ip'),
        sa.Index('idx_security_events_status', 'status'),
        sa.Index('idx_security_events_created', 'created_at'),
        comment='Security event tracking and management'
    )

# ============================================================================
# 7. CONFIGURATION STORAGE
# ============================================================================

def create_configuration_tables():
    """Create tables for configuration storage and management."""
    
    # Configuration templates
    op.create_table(
        'configuration_templates',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('description', sa.Text),
        sa.Column('config_type', sa.String(50), nullable=False,
                 comment='e.g., AGENT, TENANT, SYSTEM'),
        sa.Column('schema', JSONB(), nullable=False,
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('default_values', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('is_public', sa.Boolean, nullable=False, default=True),
        sa.Column('version', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_config_templates_type', 'config_type'),
        sa.Index('idx_config_templates_public', 'is_public'),
        comment='Configuration template definitions'
    )
    
    # Tenant configurations
    op.create_table(
        'tenant_configurations',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), sa.ForeignKey('tenants.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('config_key', sa.String(100), nullable=False),
        sa.Column('config_value', JSONB(), nullable=False,
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('config_type', sa.String(50),
                 server_default='CUSTOM'),
        sa.Column('description', sa.Text),
        sa.Column('is_encrypted', sa.Boolean, nullable=False, default=False),
        sa.Column('is_secret', sa.Boolean, nullable=False, default=False),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_by', UUID()),
        # Indexes
        sa.Index('idx_tenant_configs_tenant', 'tenant_id'),
        sa.Index('idx_tenant_configs_key', 'config_key'),
        sa.Index('idx_tenant_configs_type', 'config_type'),
        sa.UniqueConstraint('tenant_id', 'config_key',
                          name='uq_tenant_config_key'),
        comment='Tenant-specific configuration storage'
    )
    
    # System configurations
    op.create_table(
        'system_configurations',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('config_key', sa.String(100), nullable=False, unique=True),
        sa.Column('config_value', JSONB(), nullable=False,
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('data_type', sa.String(20), nullable=False,
                 server_default='STRING',
                 comment='STRING, NUMBER, BOOLEAN, ARRAY, OBJECT'),
        sa.Column('description', sa.Text),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('is_public', sa.Boolean, nullable=False, default=True),
        sa.Column('is_encrypted', sa.Boolean, nullable=False, default=False),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_by', UUID()),
        # Indexes
        sa.Index('idx_system_configs_key', 'config_key'),
        sa.Index('idx_system_configs_category', 'category'),
        comment='System-wide configuration storage'
    )

# ============================================================================
# 8. CACHE TABLES
# ============================================================================

def create_cache_tables():
    """Create tables for caching and temporary data storage."""
    
    # Distributed cache
    op.create_table(
        'distributed_cache',
        sa.Column('cache_key', sa.String(500), primary_key=True),
        sa.Column('cache_value', sa.LargeBinary, nullable=False),
        sa.Column('expires_at', sa.DateTime, nullable=False,
                 index=True),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('tags', ARRAY(sa.String(100)),
                 server_default=sa.text("'{}'::text[]")),
        # Indexes
        sa.Index('idx_cache_expires', 'expires_at'),
        sa.Index('idx_cache_tags', 'tags', postgresql_using='gin'),
        comment='Distributed cache storage'
    )
    
    # Rate limiting
    op.create_table(
        'rate_limits',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID()),
        sa.Column('user_id', UUID(), sa.ForeignKey('users.id')),
        sa.Column('api_key_id', UUID(), sa.ForeignKey('api_keys.id')),
        sa.Column('resource', sa.String(255), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('limit_count', sa.Integer, nullable=False),
        sa.Column('current_count', sa.Integer, nullable=False),
        sa.Column('window_start', sa.DateTime, nullable=False),
        sa.Column('window_end', sa.DateTime, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_rate_limits_tenant', 'tenant_id'),
        sa.Index('idx_rate_limits_user', 'user_id'),
        sa.Index('idx_rate_limits_resource', 'resource'),
        sa.Index('idx_rate_limits_window', 'window_start', 'window_end'),
        comment='Rate limiting tracking'
    )
    
    # Background job queue
    op.create_table(
        'job_queue',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID()),
        sa.Column('job_type', sa.String(100), nullable=False),
        sa.Column('priority', sa.Integer, nullable=False,
                 server_default=sa.text('5')),
        sa.Column('status', sa.String(20), nullable=False,
                 server_default='PENDING',
                 comment='PENDING, PROCESSING, COMPLETED, FAILED'),
        sa.Column('payload', JSONB(), nullable=False,
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('result', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('error_message', sa.Text),
        sa.Column('retry_count', sa.Integer, server_default=sa.text('0')),
        sa.Column('max_retries', sa.Integer, server_default=sa.text('3')),
        sa.Column('scheduled_at', sa.DateTime,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('started_at', sa.DateTime),
        sa.Column('completed_at', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_job_queue_tenant', 'tenant_id'),
        sa.Index('idx_job_queue_status', 'status'),
        sa.Index('idx_job_queue_priority', 'priority'),
        sa.Index('idx_job_queue_scheduled', 'scheduled_at'),
        sa.Index('idx_job_queue_type', 'job_type'),
        comment='Background job queue'
    )

# ============================================================================
# 9. MONITORING DATA
# ============================================================================

def create_monitoring_tables():
    """Create tables for monitoring data storage."""
    
    # Metrics storage (time-series)
    op.create_table(
        'metrics_time_series',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('metric_name', sa.String(150), nullable=False),
        sa.Column('metric_value', sa.Numeric(15, 4), nullable=False),
        sa.Column('timestamp', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('tags', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('unit', sa.String(20)),
        # Partitioning by day for time-series data
        sa.PrimaryKeyConstraint('id', 'timestamp'),
        # Indexes
        sa.Index('idx_metrics_tenant', 'tenant_id'),
        sa.Index('idx_metrics_name', 'metric_name'),
        sa.Index('idx_metrics_timestamp', 'timestamp'),
        sa.Index('idx_metrics_tags', 'tags', postgresql_using='gin'),
        postgresql_partition_by='RANGE (timestamp)',
        comment='Time-series metrics storage with partitioning'
    )
    
    # Create partitions for metrics_time_series
    op.execute("""
    CREATE TABLE metrics_time_series_2024_01_15 PARTITION OF metrics_time_series
    FOR VALUES FROM ('2024-01-15') TO ('2024-01-16');
    
    CREATE TABLE metrics_time_series_2024_01_16 PARTITION OF metrics_time_series
    FOR VALUES FROM ('2024-01-16') TO ('2024-01-17');
    """)
    
    # Alerts
    op.create_table(
        'alerts',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('severity', sa.Enum('severity_level', name='severity_level'),
                 nullable=False, server_default='MEDIUM'),
        sa.Column('status', sa.String(20), nullable=False,
                 server_default='ACTIVE',
                 comment='ACTIVE, ACKNOWLEDGED, RESOLVED, SUPPRESSED'),
        sa.Column('source', sa.String(100),
                 comment='e.g., AGENT, SYSTEM, EXTERNAL'),
        sa.Column('metric_name', sa.String(150)),
        sa.Column('condition', JSONB(),
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column('triggered_value', sa.Numeric(15, 4)),
        sa.Column('threshold', sa.Numeric(15, 4)),
        sa.Column('first_triggered_at', sa.DateTime, nullable=False),
        sa.Column('last_triggered_at', sa.DateTime),
        sa.Column('resolved_at', sa.DateTime),
        sa.Column('acknowledged_at', sa.DateTime),
        sa.Column('acknowledged_by', UUID(), sa.ForeignKey('users.id')),
        sa.Column('notification_channels', ARRAY(sa.String(50))),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_alerts_tenant', 'tenant_id'),
        sa.Index('idx_alerts_severity', 'severity'),
        sa.Index('idx_alerts_status', 'status'),
        sa.Index('idx_alerts_triggered', 'first_triggered_at'),
        sa.Index('idx_alerts_metric', 'metric_name'),
        comment='Alert definitions and state'
    )
    
    # Health checks
    op.create_table(
        'health_checks',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('agent_id', UUID(), sa.ForeignKey('agents.id')),
        sa.Column('check_name', sa.String(100), nullable=False),
        sa.Column('check_type', sa.String(50), nullable=False,
                 comment='e.g., HTTP, TCP, SSL, CUSTOM'),
        sa.Column('endpoint', sa.String(500)),
        sa.Column('interval_seconds', sa.Integer, nullable=False),
        sa.Column('timeout_seconds', sa.Integer, nullable=False),
        sa.Column('status', sa.String(20), nullable=False,
                 server_default='HEALTHY',
                 comment='HEALTHY, UNHEALTHY, DEGRADED, UNKNOWN'),
        sa.Column('last_check', sa.DateTime),
        sa.Column('last_success', sa.DateTime),
        sa.Column('response_time_ms', sa.Integer),
        sa.Column('failure_count', sa.Integer, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_health_checks_tenant', 'tenant_id'),
        sa.Index('idx_health_checks_agent', 'agent_id'),
        sa.Index('idx_health_checks_status', 'status'),
        sa.Index('idx_health_checks_last', 'last_check'),
        comment='Health check configurations and results'
    )

# ============================================================================
# 10. COMPLIANCE RECORDS
# ============================================================================

def create_compliance_tables():
    """Create tables for compliance and regulatory records."""
    
    # Compliance controls
    op.create_table(
        'compliance_controls',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('control_id', sa.String(100), nullable=False),
        sa.Column('standard', sa.Enum('compliance_standard',
                 name='compliance_standard'), nullable=False),
        sa.Column('requirement', sa.Text, nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('implementation_status', sa.String(20), nullable=False,
                 server_default='NOT_IMPLEMENTED',
                 comment='NOT_IMPLEMENTED, PARTIAL, IMPLEMENTED'),
        sa.Column('owner', sa.String(150)),
        sa.Column('evidence_required', sa.Boolean, nullable=False, default=True),
        sa.Column('last_audit_date', sa.DateTime),
        sa.Column('next_audit_date', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_compliance_controls_tenant', 'tenant_id'),
        sa.Index('idx_compliance_controls_standard', 'standard'),
        sa.Index('idx_compliance_controls_status', 'implementation_status'),
        sa.UniqueConstraint('tenant_id', 'control_id', 'standard',
                          name='uq_compliance_control'),
        comment='Compliance control definitions'
    )
    
    # Compliance evidence
    op.create_table(
        'compliance_evidence',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('control_id', UUID(), sa.ForeignKey('compliance_controls.id',
                 ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_type', sa.String(50), nullable=False,
                 comment='e.g., DOCUMENT, SCREENSHOT, LOG, REPORT'),
        sa.Column('description', sa.Text),
        sa.Column('file_url', sa.String(500)),
        sa.Column('file_hash', sa.String(64)),
        sa.Column('collected_at', sa.DateTime, nullable=False),
        sa.Column('collected_by', UUID(), sa.ForeignKey('users.id')),
        sa.Column('verified_at', sa.DateTime),
        sa.Column('verified_by', UUID(), sa.ForeignKey('users.id')),
        sa.Column('notes', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_compliance_evidence_control', 'control_id'),
        sa.Index('idx_compliance_evidence_collected', 'collected_at'),
        comment='Compliance evidence storage'
    )
    
    # Audit findings
    op.create_table(
        'audit_findings',
        sa.Column('id', UUID(), primary_key=True,
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(), nullable=False),
        sa.Column('control_id', UUID(), sa.ForeignKey('compliance_controls.id')),
        sa.Column('finding_type', sa.String(50), nullable=False,
                 comment='e.g., OBSERVATION, DEFICIENCY, VIOLATION'),
        sa.Column('severity', sa.Enum('severity_level', name='severity_level'),
                 nullable=False, server_default='LOW'),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('recommendation', sa.Text),
        sa.Column('due_date', sa.Date),
        sa.Column('status', sa.String(20), nullable=False,
                 server_default='OPEN',
                 comment='OPEN, IN_PROGRESS, RESOLVED, CLOSED'),
        sa.Column('assigned_to', UUID(), sa.ForeignKey('users.id')),
        sa.Column('resolved_at', sa.DateTime),
        sa.Column('resolved_by', UUID(), sa.ForeignKey('users.id')),
        sa.Column('resolution_notes', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                 server_default=sa.text('CURRENT_TIMESTAMP')),
        # Indexes
        sa.Index('idx_audit_findings_tenant', 'tenant_id'),
        sa.Index('idx_audit_findings_control', 'control_id'),
        sa.Index('idx_audit_findings_severity', 'severity'),
        sa.Index('idx_audit_findings_status', 'status'),
        sa.Index('idx_audit_findings_due', 'due_date'),
        comment='Audit findings and remediation tracking'
    )

# ============================================================================
# ADDITIONAL PERFORMANCE OPTIMIZATIONS
# ============================================================================

def create_performance_optimizations():
    """Create additional indexes, constraints, and optimizations."""
    
    # Create BRIN indexes for time-series data
    op.execute("""
    CREATE INDEX idx_metrics_time_series_brin 
    ON metrics_time_series 
    USING BRIN (timestamp) 
    WITH (pages_per_range = 128);
    """)
    
    # Create partial indexes for common queries
    op.execute("""
    CREATE INDEX idx_agents_active 
    ON agents (tenant_id, status) 
    WHERE status IN ('ACTIVE', 'IDLE') AND deleted_at IS NULL;
    """)
    
    op.execute("""
    CREATE INDEX idx_users_active 
    ON users (tenant_id, is_active) 
    WHERE is_active = true AND deleted_at IS NULL;
    """)
    
    op.execute("""
    CREATE INDEX idx_exec_jobs_recent 
    ON execution_jobs (tenant_id, created_at DESC) 
    WHERE created_at > CURRENT_DATE - INTERVAL '30 days';
    """)
    
    # Create GIN indexes for JSONB columns that are frequently queried
    op.execute("""
    CREATE INDEX idx_agents_config 
    ON agents 
    USING GIN (config jsonb_path_ops);
    """)
    
    op.execute("""
    CREATE INDEX idx_agents_deployment 
    ON agents 
    USING GIN (deployment_info jsonb_path_ops);
    """)
    
    # Create functional indexes
    op.execute("""
    CREATE INDEX idx_tenants_lower_slug 
    ON tenants (LOWER(slug));
    """)
    
    op.execute("""
    CREATE INDEX idx_users_lower_email 
    ON users (tenant_id, LOWER(email));
    """)

def create_data_retention_policies():
    """Create data retention policies and cleanup procedures."""
    
    # Create retention policy functions
    op.execute("""
    CREATE OR REPLACE FUNCTION cleanup_old_audit_logs()
    RETURNS void AS $$
    BEGIN
        DELETE FROM audit_logs 
        WHERE created_at < CURRENT_DATE - INTERVAL '2 years';
    END;
    $$ LANGUAGE plpgsql;
    """)
    
    op.execute("""
    CREATE OR REPLACE FUNCTION cleanup_old_metrics()
    RETURNS void AS $$
    BEGIN
        DELETE FROM metrics_time_series 
        WHERE timestamp < CURRENT_DATE - INTERVAL '1 year';
    END;
    $$ LANGUAGE plpgsql;
    """)
    
    op.execute("""
    CREATE OR REPLACE FUNCTION cleanup_soft_deleted()
    RETURNS void AS $$
    BEGIN
        DELETE FROM users 
        WHERE deleted_at < CURRENT_DATE - INTERVAL '90 days';
        
        DELETE FROM agents 
        WHERE deleted_at < CURRENT_DATE - INTERVAL '90 days';
    END;
    $$ LANGUAGE plpgsql;
    """)
    
    # Create scheduled cleanup job comments
    op.execute("""
    COMMENT ON FUNCTION cleanup_old_audit_logs() IS 
    'Cleans up audit logs older than 2 years';
    
    COMMENT ON FUNCTION cleanup_old_metrics() IS 
    'Cleans up metrics older than 1 year';
    
    COMMENT ON FUNCTION cleanup_soft_deleted() IS 
    'Permanently removes soft-deleted records after 90 days';
    """)

# ============================================================================
# MIGRATION FUNCTIONS
# ============================================================================

def upgrade():
    """Upgrade database schema."""
    
    # Create ENUM types first
    create_enums()
    
    # Create all tables in dependency order
    create_user_tables()          # Tenants needed for FK constraints
    create_agent_tables()         # Agents depend on agent_types
    create_execution_tables()     # Jobs depend on agents
    create_business_value_tables()
    create_billing_tables()       # Billing depends on tenants
    create_audit_tables()         # Audit depends on users
    create_configuration_tables()
    create_cache_tables()
    create_monitoring_tables()
    create_compliance_tables()
    
    # Create performance optimizations
    create_performance_optimizations()
    
    # Create data retention policies
    create_data_retention_policies()
    
    # Create initial admin user and default tenant
    op.execute("""
    INSERT INTO tenants (id, name, slug, description, plan_tier, max_agents, max_users)
    VALUES (
        '00000000-0000-0000-0000-000000000000',
        'System',
        'system',
        'System tenant for platform operations',
        'ENTERPRISE',
        10000,
        100
    );
    """)

def downgrade():
    """Downgrade database schema."""
    
    # Drop tables in reverse dependency order
    op.drop_table('audit_findings')
    op.drop_table('compliance_evidence')
    op.drop_table('compliance_controls')
    op.drop_table('health_checks')
    op.drop_table('alerts')
    op.drop_table('metrics_time_series')
    op.drop_table('job_queue')
    op.drop_table('rate_limits')
    op.drop_table('distributed_cache')
    op.drop_table('system_configurations')
    op.drop_table('tenant_configurations')
    op.drop_table('configuration_templates')
    op.drop_table('security_events')
    op.drop_table('audit_logs')
    op.drop_table('invoice_line_items')
    op.drop_table('invoices')
    op.drop_table('tenant_subscriptions')
    op.drop_table('subscription_plans')
    op.drop_table('business_kpis')
    op.drop_table('cost_savings')
    op.drop_table('roi_calculations')
    op.drop_table('job_schedules')
    op.drop_table('execution_steps')
    
    # Drop partitions before parent tables
    op.execute("DROP TABLE IF EXISTS execution_jobs_2024_02 CASCADE")
    op.execute("DROP TABLE IF EXISTS execution_jobs_2024_01 CASCADE")
    op.drop_table('execution_jobs')
    
    op.execute("DROP TABLE IF EXISTS roi_calculations_2024_02 CASCADE")
    op.execute("DROP TABLE IF EXISTS roi_calculations_2024_01 CASCADE")
    op.drop_table('roi_calculations')
    
    op.execute("DROP TABLE IF EXISTS audit_logs_2024_02 CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_logs_2024_01 CASCADE")
    op.drop_table('audit_logs')
    
    op.execute("DROP TABLE IF EXISTS metrics_time_series_2024_01_16 CASCADE")
    op.execute("DROP TABLE IF EXISTS metrics_time_series_2024_01_15 CASCADE")
    op.drop_table('metrics_time_series')
    
    op.drop_table('agent_dependencies')
    op.drop_table('agents')
    op.drop_table('agent_types')
    op.drop_table('user_sessions')
    op.drop_table('api_keys')
    op.drop_table('users')
    op.drop_table('tenants')
    
    # Drop cleanup functions
    op.execute("DROP FUNCTION IF EXISTS cleanup_soft_deleted() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS cleanup_old_metrics() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS cleanup_old_audit_logs() CASCADE")
    
    # Drop ENUM types last
    drop_enums()