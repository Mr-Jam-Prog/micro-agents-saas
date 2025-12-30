"""
Modèles SQLAlchemy pour le registre MicroAgents

Ce module définit tous les modèles de données pour le registre d'agents,
incluant le versioning, les métriques de performance, les relations de dépendance,
les évaluations utilisateurs, et le suivi d'audit.
"""

import enum
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, DateTime, Enum, Float, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, func, text, Index
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import expression

Base = declarative_base()


# ============================================================================
# ENUMS
# ============================================================================

class AgentStatus(str, enum.Enum):
    """Statut de publication de l'agent."""
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class ExecutionStatus(str, enum.Enum):
    """Statut d'exécution."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class MetricType(str, enum.Enum):
    """Type de métrique."""
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    ACCURACY = "accuracy"
    COST = "cost"
    RELIABILITY = "reliability"
    AVAILABILITY = "availability"
    CUSTOM = "custom"


class RatingValue(int, enum.Enum):
    """Valeurs d'évaluation."""
    ONE_STAR = 1
    TWO_STARS = 2
    THREE_STARS = 3
    FOUR_STARS = 4
    FIVE_STARS = 5


class DependencyType(str, enum.Enum):
    """Type de dépendance."""
    REQUIRES = "requires"
    RECOMMENDS = "recommends"
    CONFLICTS = "conflicts"
    PROVIDES = "provides"
    ENHANCES = "enhances"


class BillingPeriod(str, enum.Enum):
    """Période de facturation."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    USAGE_BASED = "usage_based"


class AuditAction(str, enum.Enum):
    """Actions d'audit."""
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    APPROVE = "approve"
    REJECT = "reject"
    DEPRECATE = "deprecate"
    ARCHIVE = "archive"
    RATE = "rate"
    REVIEW = "review"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"


# ============================================================================
# MODÈLES PRINCIPAUX
# ============================================================================

class Organization(Base):
    """Organisation ou entreprise utilisant la plateforme."""
    
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint('slug', name='uq_organizations_slug'),
        Index('idx_organizations_created_at', 'created_at'),
        Index('idx_organizations_tier', 'tier'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    logo_url = Column(String(500))
    website_url = Column(String(500))
    
    # Contact information
    contact_email = Column(String(255))
    billing_email = Column(String(255))
    support_email = Column(String(255))
    
    # Configuration
    tier = Column(String(50), default="free", nullable=False)  # free, pro, enterprise
    max_agents = Column(Integer, default=10)
    max_users = Column(Integer, default=5)
    custom_domains = Column(ARRAY(String(255)))
    
    # Billing
    stripe_customer_id = Column(String(255))
    stripe_subscription_id = Column(String(255))
    billing_period = Column(Enum(BillingPeriod), default=BillingPeriod.MONTHLY)
    next_billing_date = Column(DateTime)
    
    # Compliance
    gdpr_compliant = Column(Boolean, default=False)
    data_retention_days = Column(Integer, default=30)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime)
    
    # Relationships
    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    agents = relationship("Agent", back_populates="organization")
    subscriptions = relationship("Subscription", back_populates="organization")
    
    @hybrid_property
    def is_active(self) -> bool:
        """Vérifie si l'organisation est active."""
        return self.deleted_at is None and self.tier != "suspended"
    
    @is_active.expression
    def is_active(cls):
        return expression.and_(
            cls.deleted_at.is_(None),
            cls.tier != "suspended"
        )
    
    @validates('slug')
    def validate_slug(self, key, slug):
        """Valide le format du slug."""
        if not slug.replace('-', '').replace('_', '').isalnum():
            raise ValueError("Le slug ne peut contenir que des lettres, chiffres, tirets et underscores")
        return slug.lower()


class User(Base):
    """Utilisateur de la plateforme."""
    
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint('email', name='uq_users_email'),
        UniqueConstraint('username', name='uq_users_username'),
        Index('idx_users_organization_id', 'organization_id'),
        Index('idx_users_created_at', 'created_at'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'), nullable=False)
    
    # Identity
    email = Column(String(255), nullable=False, unique=True)
    username = Column(String(100), nullable=False, unique=True)
    full_name = Column(String(255))
    avatar_url = Column(String(500))
    
    # Authentication
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    
    # Preferences
    language = Column(String(10), default="en")
    timezone = Column(String(50), default="UTC")
    
    # Security
    last_login_at = Column(DateTime)
    failed_login_attempts = Column(Integer, default=0)
    password_changed_at = Column(DateTime, default=func.now())
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime)
    
    # Relationships
    organization = relationship("Organization", back_populates="users")
    agent_creations = relationship("Agent", back_populates="creator")
    executions = relationship("Execution", back_populates="user")
    ratings = relationship("Rating", back_populates="user")
    reviews = relationship("Review", back_populates="user")
    subscriptions = relationship("Subscription", back_populates="user")
    
    @hybrid_property
    def is_deleted(self) -> bool:
        """Vérifie si l'utilisateur est supprimé."""
        return self.deleted_at is not None
    
    @validates('email')
    def validate_email(self, key, email):
        """Valide le format de l'email."""
        if '@' not in email:
            raise ValueError("Email invalide")
        return email.lower()


class Agent(Base):
    """Modèle d'agent avec versioning."""
    
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint('name', 'organization_id', name='uq_agents_name_org'),
        Index('idx_agents_organization_id', 'organization_id'),
        Index('idx_agents_creator_id', 'creator_id'),
        Index('idx_agents_status', 'status'),
        Index('idx_agents_category', 'category'),
        Index('idx_agents_roi_score', 'roi_score'),
        Index('idx_agents_created_at', 'created_at'),
        Index('idx_agents_popularity', 'download_count', 'rating_average'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'), nullable=False)
    creator_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Basic information
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    description = Column(Text)
    readme = Column(Text)  # Documentation Markdown complète
    
    # Categorization
    category = Column(String(100), nullable=False, index=True)
    subcategory = Column(String(100))
    tags = Column(ARRAY(String(50)))  # Tags pour la recherche
    
    # Configuration
    status = Column(Enum(AgentStatus), default=AgentStatus.DRAFT, nullable=False)
    visibility = Column(String(20), default="private")  # private, shared, public
    license = Column(String(100), default="Apache-2.0")
    
    # Business value
    roi_score = Column(Float, default=0.0)  # Score ROI de 0 à 10
    complexity = Column(String(20))  # simple, medium, complex, advanced
    criticality = Column(String(20))  # low, medium, high, critical
    
    # Usage statistics
    download_count = Column(BigInteger, default=0)
    execution_count = Column(BigInteger, default=0)
    rating_count = Column(Integer, default=0)
    rating_average = Column(Float, default=0.0)
    review_count = Column(Integer, default=0)
    
    # Performance metrics (aggregated from versions)
    avg_latency_ms = Column(Float)
    avg_accuracy = Column(Float)
    avg_cost_usd = Column(Float)
    success_rate = Column(Float)
    
    # Pricing
    pricing_model = Column(String(50), default="free")  # free, freemium, subscription, pay_per_use
    price_usd = Column(Numeric(10, 2))
    price_interval = Column(String(20))
    
    # Compliance
    compliance_standards = Column(ARRAY(String(50)))  # SOC2, ISO27001, GDPR, etc.
    data_classification = Column(String(50))
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    published_at = Column(DateTime)
    deprecated_at = Column(DateTime)
    
    # Relationships
    organization = relationship("Organization", back_populates="agents")
    creator = relationship("User", back_populates="agent_creations")
    versions = relationship("AgentVersion", back_populates="agent", cascade="all, delete-orphan")
    dependencies = relationship("Dependency", foreign_keys="[Dependency.agent_id]", back_populates="agent")
    executions = relationship("Execution", back_populates="agent")
    ratings = relationship("Rating", back_populates="agent")
    reviews = relationship("Review", back_populates="agent")
    subscriptions = relationship("Subscription", back_populates="agent")
    compatibility_rules = relationship("CompatibilityRule", back_populates="agent")
    
    @hybrid_property
    def latest_version(self):
        """Retourne la dernière version publiée."""
        # Cette propriété est typiquement chargée via une query séparée
        pass
    
    @hybrid_property
    def is_public(self) -> bool:
        """Vérifie si l'agent est public."""
        return self.visibility == "public"
    
    @validates('roi_score')
    def validate_roi_score(self, key, score):
        """Valide le score ROI."""
        if not 0 <= score <= 10:
            raise ValueError("Le score ROI doit être entre 0 et 10")
        return score


class AgentVersion(Base):
    """Version spécifique d'un agent."""
    
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint('agent_id', 'version', name='uq_agent_versions_agent_version'),
        Index('idx_agent_versions_agent_id', 'agent_id'),
        Index('idx_agent_versions_version', 'version'),
        Index('idx_agent_versions_status', 'status'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id', ondelete='CASCADE'), nullable=False)
    
    # Version information
    version = Column(String(50), nullable=False)  # Format semver: 1.0.0
    changelog = Column(Text)
    release_notes = Column(Text)
    
    # Source code
    source_code_url = Column(String(500))
    source_hash = Column(String(64))  # SHA256 du code source
    docker_image = Column(String(500))
    docker_tag = Column(String(100))
    
    # Configuration
    config_schema = Column(JSON)  # JSON Schema pour la configuration
    default_config = Column(JSON)  # Configuration par défaut
    environment_variables = Column(JSON)  # Variables d'environnement requises
    
    # Requirements
    python_version = Column(String(20))
    requirements = Column(ARRAY(String(255)))  # Dépendances Python
    system_dependencies = Column(ARRAY(String(255)))  # Dépendances système
    
    # Performance specifications
    min_cpu = Column(String(20))  # 0.5, 1, 2, etc.
    min_memory = Column(String(20))  # 128Mi, 256Mi, 512Mi, etc.
    min_storage = Column(String(20))
    
    # Status
    status = Column(Enum(AgentStatus), default=AgentStatus.DRAFT, nullable=False)
    is_stable = Column(Boolean, default=False)
    is_latest = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    published_at = Column(DateTime)
    
    # Relationships
    agent = relationship("Agent", back_populates="versions")
    executions = relationship("Execution", back_populates="agent_version")
    dependencies = relationship("Dependency", foreign_keys="[Dependency.agent_version_id]", back_populates="agent_version")
    metrics = relationship("PerformanceMetric", back_populates="agent_version")
    
    @hybrid_property
    def major_version(self) -> int:
        """Extrait la version majeure."""
        if self.version:
            parts = self.version.split('.')
            return int(parts[0]) if parts[0].isdigit() else 0
        return 0
    
    @hybrid_property
    def minor_version(self) -> int:
        """Extrait la version mineure."""
        if self.version and len(self.version.split('.')) > 1:
            parts = self.version.split('.')
            return int(parts[1]) if parts[1].isdigit() else 0
        return 0
    
    @hybrid_property
    def patch_version(self) -> int:
        """Extrait la version de patch."""
        if self.version and len(self.version.split('.')) > 2:
            parts = self.version.split('.')
            patch = parts[2].split('-')[0].split('+')[0]  # Ignore pre-release/build metadata
            return int(patch) if patch.isdigit() else 0
        return 0


class Dependency(Base):
    """Relations de dépendance entre agents."""
    
    __tablename__ = "dependencies"
    __table_args__ = (
        UniqueConstraint('agent_id', 'depends_on_agent_id', 'dependency_type', 
                        name='uq_dependencies_agent_depends_type'),
        Index('idx_dependencies_agent_id', 'agent_id'),
        Index('idx_dependencies_depends_on_agent_id', 'depends_on_agent_id'),
        Index('idx_dependencies_type', 'dependency_type'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id', ondelete='CASCADE'), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), ForeignKey('agent_versions.id', ondelete='CASCADE'))
    depends_on_agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id', ondelete='CASCADE'), nullable=False)
    depends_on_version_id = Column(UUID(as_uuid=True), ForeignKey('agent_versions.id', ondelete='CASCADE'))
    
    # Dependency details
    dependency_type = Column(Enum(DependencyType), nullable=False)
    version_constraint = Column(String(100))  # Format semver: ^1.0.0, ~2.3.0, >=3.0.0 <4.0.0
    is_optional = Column(Boolean, default=False)
    
    # Metadata
    description = Column(Text)
    auto_resolve = Column(Boolean, default=True)  # Résolution automatique des dépendances
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    agent = relationship("Agent", foreign_keys=[agent_id], back_populates="dependencies")
    agent_version = relationship("AgentVersion", foreign_keys=[agent_version_id], back_populates="dependencies")
    depends_on_agent = relationship("Agent", foreign_keys=[depends_on_agent_id])
    depends_on_version = relationship("AgentVersion", foreign_keys=[depends_on_version_id])


class Execution(Base):
    """Historique d'exécution des agents."""
    
    __tablename__ = "executions"
    __table_args__ = (
        Index('idx_executions_agent_id', 'agent_id'),
        Index('idx_executions_user_id', 'user_id'),
        Index('idx_executions_status', 'status'),
        Index('idx_executions_started_at', 'started_at'),
        Index('idx_executions_organization_id', 'organization_id'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    agent_version_id = Column(UUID(as_uuid=True), ForeignKey('agent_versions.id'))
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'), nullable=False)
    
    # Execution details
    execution_id = Column(String(100), unique=True, nullable=False)  # ID externe/métier
    status = Column(Enum(ExecutionStatus), default=ExecutionStatus.PENDING, nullable=False)
    
    # Input/Output
    input_data = Column(JSON)  # Données d'entrée
    output_data = Column(JSON)  # Données de sortie
    error_message = Column(Text)
    error_stack_trace = Column(Text)
    
    # Resource usage
    cpu_usage_seconds = Column(Float)
    memory_usage_mb = Column(Float)
    network_usage_mb = Column(Float)
    storage_usage_mb = Column(Float)
    
    # Cost tracking
    cost_usd = Column(Numeric(10, 4))
    cost_currency = Column(String(3), default="USD")
    cost_breakdown = Column(JSON)  # Détail des coûts
    
    # Timing
    queued_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    timeout_seconds = Column(Integer, default=300)
    
    # Environment
    environment = Column(String(50))  # dev, staging, prod, etc.
    region = Column(String(50))  # cloud region
    node_id = Column(String(100))  # Worker node
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    agent = relationship("Agent", back_populates="executions")
    agent_version = relationship("AgentVersion", back_populates="executions")
    user = relationship("User", back_populates="executions")
    organization = relationship("Organization")
    metrics = relationship("PerformanceMetric", back_populates="execution")
    billing_record = relationship("BillingRecord", back_populates="execution", uselist=False)
    
    @hybrid_property
    def duration_seconds(self) -> Optional[float]:
        """Calcule la durée d'exécution en secondes."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    @hybrid_property
    def is_successful(self) -> bool:
        """Vérifie si l'exécution a réussi."""
        return self.status == ExecutionStatus.SUCCESS
    
    @validates('cost_usd')
    def validate_cost(self, key, cost):
        """Valide que le coût n'est pas négatif."""
        if cost is not None and cost < 0:
            raise ValueError("Le coût ne peut pas être négatif")
        return cost


class PerformanceMetric(Base):
    """Métriques de performance détaillées."""
    
    __tablename__ = "performance_metrics"
    __table_args__ = (
        Index('idx_performance_metrics_agent_version_id', 'agent_version_id'),
        Index('idx_performance_metrics_execution_id', 'execution_id'),
        Index('idx_performance_metrics_metric_type', 'metric_type'),
        Index('idx_performance_metrics_timestamp', 'timestamp'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_version_id = Column(UUID(as_uuid=True), ForeignKey('agent_versions.id'))
    execution_id = Column(UUID(as_uuid=True), ForeignKey('executions.id'))
    
    # Metric details
    metric_type = Column(Enum(MetricType), nullable=False)
    metric_name = Column(String(100), nullable=False)  # e.g., "p95_latency", "accuracy_score"
    value = Column(Numeric(15, 6), nullable=False)
    unit = Column(String(50))  # ms, req/s, %, USD, etc.
    
    # Context
    timestamp = Column(DateTime, default=func.now(), nullable=False)
    window_seconds = Column(Integer)  # Fenêtre temporelle pour les métriques agrégées
    sample_size = Column(Integer)  # Taille de l'échantillon
    
    # Dimensions
    environment = Column(String(50))
    region = Column(String(50))
    instance_type = Column(String(100))
    
    # Metadata
    tags = Column(JSON)  # Tags supplémentaires pour le filtrage
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    # Relationships
    agent_version = relationship("AgentVersion", back_populates="metrics")
    execution = relationship("Execution", back_populates="metrics")
    
    @validates('value')
    def validate_value(self, key, value):
        """Valide la valeur de la métrique."""
        # Validation spécifique par type
        if self.metric_type == MetricType.ACCURACY and not (0 <= value <= 1):
            raise ValueError("La précision doit être entre 0 et 1")
        elif self.metric_type == MetricType.COST and value < 0:
            raise ValueError("Le coût ne peut pas être négatif")
        return value


class Rating(Base):
    """Évaluations des agents par les utilisateurs."""
    
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint('user_id', 'agent_id', name='uq_ratings_user_agent'),
        Index('idx_ratings_agent_id', 'agent_id'),
        Index('idx_ratings_user_id', 'user_id'),
        Index('idx_ratings_created_at', 'created_at'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Rating details
    value = Column(Integer, nullable=False)  # 1-5 étoiles
    criteria_ratings = Column(JSON)  # {"ease_of_use": 4, "performance": 5, "documentation": 3}
    
    # Context
    agent_version = Column(String(50))
    environment = Column(String(50))
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    agent = relationship("Agent", back_populates="ratings")
    user = relationship("User", back_populates="ratings")
    
    @validates('value')
    def validate_value(self, key, value):
        """Valide la valeur de l'évaluation."""
        if not 1 <= value <= 5:
            raise ValueError("La valeur d'évaluation doit être entre 1 et 5")
        return value


class Review(Base):
    """Avis détaillés sur les agents."""
    
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint('user_id', 'agent_id', name='uq_reviews_user_agent'),
        Index('idx_reviews_agent_id', 'agent_id'),
        Index('idx_reviews_user_id', 'user_id'),
        Index('idx_reviews_helpful_count', 'helpful_count'),
        Index('idx_reviews_created_at', 'created_at'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Review content
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    
    # Metrics
    helpful_count = Column(Integer, default=0)
    report_count = Column(Integer, default=0)
    is_featured = Column(Boolean, default=False)
    
    # Moderation
    is_approved = Column(Boolean, default=False)
    moderated_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    moderation_notes = Column(Text)
    
    # Context
    agent_version = Column(String(50))
    use_case = Column(String(100))
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    moderated_at = Column(DateTime)
    
    # Relationships
    agent = relationship("Agent", back_populates="reviews")
    user = relationship("User", back_populates="reviews")
    moderator = relationship("User", foreign_keys=[moderated_by])
    helpful_votes = relationship("ReviewHelpfulVote", back_populates="review", cascade="all, delete-orphan")
    
    @hybrid_property
    def is_helpful(self) -> bool:
        """Vérifie si l'avis est considéré comme utile."""
        return self.helpful_count > 5 and self.helpful_count > (self.report_count * 2)


class ReviewHelpfulVote(Base):
    """Votes d'utilité pour les avis."""
    
    __tablename__ = "review_helpful_votes"
    __table_args__ = (
        UniqueConstraint('user_id', 'review_id', name='uq_review_helpful_votes_user_review'),
        Index('idx_review_helpful_votes_review_id', 'review_id'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id = Column(UUID(as_uuid=True), ForeignKey('reviews.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Vote details
    is_helpful = Column(Boolean, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    # Relationships
    review = relationship("Review", back_populates="helpful_votes")
    user = relationship("User")


class Subscription(Base):
    """Abonnements des utilisateurs/organisations aux agents."""
    
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint('organization_id', 'agent_id', name='uq_subscriptions_org_agent'),
        UniqueConstraint('user_id', 'agent_id', name='uq_subscriptions_user_agent'),
        Index('idx_subscriptions_agent_id', 'agent_id'),
        Index('idx_subscriptions_organization_id', 'organization_id'),
        Index('idx_subscriptions_user_id', 'user_id'),
        Index('idx_subscriptions_status', 'status'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'))
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    
    # Subscription details
    status = Column(String(50), default="active")  # active, cancelled, expired, suspended
    pricing_tier = Column(String(50))
    
    # Limits
    max_executions_per_period = Column(Integer)
    max_concurrent_executions = Column(Integer)
    
    # Billing
    price_usd = Column(Numeric(10, 2))
    billing_period = Column(Enum(BillingPeriod))
    stripe_subscription_item_id = Column(String(255))
    
    # Usage tracking
    executions_this_period = Column(Integer, default=0)
    last_reset_at = Column(DateTime)
    
    # Dates
    starts_at = Column(DateTime, default=func.now())
    ends_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    trial_ends_at = Column(DateTime)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    agent = relationship("Agent", back_populates="subscriptions")
    organization = relationship("Organization", back_populates="subscriptions")
    user = relationship("User", back_populates="subscriptions")
    billing_records = relationship("BillingRecord", back_populates="subscription")
    
    @hybrid_property
    def is_active(self) -> bool:
        """Vérifie si l'abonnement est actif."""
        now = datetime.utcnow()
        return (
            self.status == "active" and
            (self.starts_at is None or self.starts_at <= now) and
            (self.ends_at is None or self.ends_at > now)
        )
    
    @hybrid_property
    def is_trial(self) -> bool:
        """Vérifie si c'est une période d'essai."""
        now = datetime.utcnow()
        return self.trial_ends_at and self.trial_ends_at > now


class BillingRecord(Base):
    """Enregistrements de facturation détaillés."""
    
    __tablename__ = "billing_records"
    __table_args__ = (
        Index('idx_billing_records_subscription_id', 'subscription_id'),
        Index('idx_billing_records_organization_id', 'organization_id'),
        Index('idx_billing_records_execution_id', 'execution_id'),
        Index('idx_billing_records_period_start', 'period_start'),
        Index('idx_billing_records_status', 'status'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey('subscriptions.id'))
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'), nullable=False)
    execution_id = Column(UUID(as_uuid=True), ForeignKey('executions.id'))
    
    # Billing details
    amount_usd = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), default="USD")
    description = Column(String(500))
    
    # Period
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Usage breakdown
    usage_quantity = Column(Integer)
    usage_unit = Column(String(50))  # executions, hours, GB, etc.
    unit_price_usd = Column(Numeric(10, 4))
    
    # Status
    status = Column(String(50), default="pending")  # pending, billed, paid, failed, refunded
    stripe_invoice_id = Column(String(255))
    stripe_payment_intent_id = Column(String(255))
    
    # Taxes
    tax_amount_usd = Column(Numeric(10, 2))
    tax_rate = Column(Numeric(5, 3))
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    billed_at = Column(DateTime)
    paid_at = Column(DateTime)
    
    # Relationships
    subscription = relationship("Subscription", back_populates="billing_records")
    organization = relationship("Organization")
    execution = relationship("Execution", back_populates="billing_record")
    
    @hybrid_property
    def total_amount_usd(self) -> Decimal:
        """Calcule le montant total avec taxes."""
        total = self.amount_usd or Decimal('0')
        if self.tax_amount_usd:
            total += self.tax_amount_usd
        return total
    
    @validates('period_start', 'period_end')
    def validate_period(self, key, value):
        """Valide que la période est logique."""
        if key == 'period_end' and hasattr(self, 'period_start'):
            if value <= self.period_start:
                raise ValueError("La date de fin doit être après la date de début")
        return value


class CompatibilityRule(Base):
    """Règles de compatibilité entre agents."""
    
    __tablename__ = "compatibility_rules"
    __table_args__ = (
        UniqueConstraint('agent_id', 'compatible_with_agent_id', 
                        name='uq_compatibility_rules_agent_compatible'),
        Index('idx_compatibility_rules_agent_id', 'agent_id'),
        Index('idx_compatibility_rules_compatible_with_agent_id', 'compatible_with_agent_id'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    compatible_with_agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    
    # Compatibility details
    compatibility_score = Column(Float)  # 0-1, 1 étant parfaitement compatible
    tested_versions = Column(ARRAY(String(50)))  # Versions testées
    known_issues = Column(JSON)  # Problèmes de compatibilité connus
    
    # Test results
    last_tested_at = Column(DateTime)
    test_results = Column(JSON)  # Résultats détaillés des tests
    test_coverage = Column(Float)  # Couverture des tests de compatibilité
    
    # Recommendations
    recommendation = Column(String(50))  # recommended, supported, discouraged, incompatible
    recommendation_reason = Column(Text)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    agent = relationship("Agent", foreign_keys=[agent_id], back_populates="compatibility_rules")
    compatible_with_agent = relationship("Agent", foreign_keys=[compatible_with_agent_id])
    
    @validates('compatibility_score')
    def validate_score(self, key, score):
        """Valide le score de compatibilité."""
        if score is not None and not (0 <= score <= 1):
            raise ValueError("Le score de compatibilité doit être entre 0 et 1")
        return score


class UsageStatistic(Base):
    """Statistiques d'utilisation agrégées."""
    
    __tablename__ = "usage_statistics"
    __table_args__ = (
        UniqueConstraint('agent_id', 'date', 'granularity', name='uq_usage_statistics_agent_date_granularity'),
        Index('idx_usage_statistics_agent_id', 'agent_id'),
        Index('idx_usage_statistics_date', 'date'),
        Index('idx_usage_statistics_granularity', 'granularity'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    
    # Time period
    date = Column(DateTime, nullable=False)  # Début de la période
    granularity = Column(String(20), nullable=False)  # hourly, daily, weekly, monthly
    
    # Usage counts
    execution_count = Column(Integer, default=0)
    unique_user_count = Column(Integer, default=0)
    unique_organization_count = Column(Integer, default=0)
    
    # Performance aggregates
    avg_latency_ms = Column(Float)
    p95_latency_ms = Column(Float)
    p99_latency_ms = Column(Float)
    success_rate = Column(Float)
    error_count = Column(Integer, default=0)
    
    # Cost aggregates
    total_cost_usd = Column(Numeric(12, 4))
    avg_cost_per_execution = Column(Numeric(10, 4))
    
    # Geography
    regions = Column(JSON)  # Distribution par région
    environments = Column(JSON)  # Distribution par environnement
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    agent = relationship("Agent")
    
    @hybrid_property
    def error_rate(self) -> Optional[float]:
        """Calcule le taux d'erreur."""
        if self.execution_count > 0:
            return self.error_count / self.execution_count
        return None


class AuditLog(Base):
    """Journaux d'audit pour le suivi des activités."""
    
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index('idx_audit_logs_user_id', 'user_id'),
        Index('idx_audit_logs_organization_id', 'organization_id'),
        Index('idx_audit_logs_action', 'action'),
        Index('idx_audit_logs_resource_type', 'resource_type'),
        Index('idx_audit_logs_timestamp', 'timestamp'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'))
    
    # Audit details
    action = Column(Enum(AuditAction), nullable=False)
    resource_type = Column(String(100), nullable=False)  # agent, execution, subscription, etc.
    resource_id = Column(UUID(as_uuid=True), nullable=False)
    
    # Changes
    old_values = Column(JSON)
    new_values = Column(JSON)
    
    # Context
    ip_address = Column(String(45))  # Support IPv6
    user_agent = Column(String(500))
    request_id = Column(String(100))
    
    # Metadata
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    
    # Timestamps
    timestamp = Column(DateTime, default=func.now(), nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User")
    organization = relationship("Organization")


class CacheInvalidation(Base):
    """Suivi de l'invalidation du cache."""
    
    __tablename__ = "cache_invalidations"
    __table_args__ = (
        Index('idx_cache_invalidations_cache_key', 'cache_key'),
        Index('idx_cache_invalidations_invalidated_at', 'invalidated_at'),
        Index('idx_cache_invalidations_entity_type', 'entity_type'),
        Index('idx_cache_invalidations_entity_id', 'entity_id'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Cache details
    cache_key = Column(String(500), nullable=False)
    cache_type = Column(String(50), nullable=False)  # redis, memcached, local
    
    # Entity reference
    entity_type = Column(String(100))  # agent, user, organization, etc.
    entity_id = Column(UUID(as_uuid=True))
    
    # Invalidation reason
    reason = Column(String(200))  # update, delete, ttl_expired, manual
    triggered_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    
    # Performance
    ttl_seconds = Column(Integer)
    size_bytes = Column(BigInteger)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    invalidated_at = Column(DateTime, default=func.now(), nullable=False)
    accessed_at = Column(DateTime)  # Dernier accès
    
    # Relationships
    trigger_user = relationship("User")
    
    @hybrid_property
    def age_seconds(self) -> float:
        """Âge de l'entrée d'invalidation en secondes."""
        return (datetime.utcnow() - self.invalidated_at).total_seconds()


class AgentMarketStats(Base):
    """Statistiques de marché pour les agents publics."""
    
    __tablename__ = "agent_market_stats"
    __table_args__ = (
        UniqueConstraint('agent_id', 'calculated_at', name='uq_agent_market_stats_agent_calculated'),
        Index('idx_agent_market_stats_agent_id', 'agent_id'),
        Index('idx_agent_market_stats_calculated_at', 'calculated_at'),
        Index('idx_agent_market_stats_trend_score', 'trend_score'),
    )
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.id'), nullable=False)
    
    # Market metrics
    popularity_rank = Column(Integer)
    trend_score = Column(Float)  # Score de tendance (positif = en croissance)
    market_share = Column(Float)  # Part de marché dans la catégorie
    
    # Adoption metrics
    weekly_downloads = Column(Integer)
    weekly_new_subscriptions = Column(Integer)
    weekly_active_users = Column(Integer)
    
    # Performance benchmarks
    benchmark_score = Column(Float)  # Score de performance comparatif
    reliability_score = Column(Float)
    cost_efficiency_score = Column(Float)
    
    # Competitive analysis
    similar_agents = Column(ARRAY(UUID(as_uuid=True)))  # IDs d'agents similaires
    competitive_advantages = Column(ARRAY(String(200)))  # Avantages compétitifs
    
    # Timestamps
    calculated_at = Column(DateTime, nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Relationships
    agent = relationship("Agent")
    
    @hybrid_property
    def growth_rate(self) -> Optional[float]:
        """Calcule le taux de croissance hebdomadaire."""
        # À calculer via des requêtes sur les statistiques historiques
        pass


# ============================================================================
# VIEWS MATERIALIZED (pour les performances)
# ============================================================================

class AgentSummaryView(Base):
    """Vue matérialisée pour les résumés d'agents."""
    
    __tablename__ = "agent_summaries"
    __table_args__ = {'info': {'is_materialized_view': True}}
    
    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String(255))
    slug = Column(String(255))
    category = Column(String(100))
    status = Column(Enum(AgentStatus))
    visibility = Column(String(20))
    
    # Aggregated metrics
    latest_version = Column(String(50))
    download_count = Column(BigInteger)
    execution_count = Column(BigInteger)
    rating_average = Column(Float)
    rating_count = Column(Integer)
    review_count = Column(Integer)
    
    # Performance aggregates
    avg_latency_ms = Column(Float)
    avg_accuracy = Column(Float)
    success_rate = Column(Float)
    
    # Business metrics
    roi_score = Column(Float)
    pricing_model = Column(String(50))
    price_usd = Column(Numeric(10, 2))
    
    # Organization
    organization_name = Column(String(255))
    organization_slug = Column(String(100))
    
    # Timestamps
    published_at = Column(DateTime)
    updated_at = Column(DateTime)
    refreshed_at = Column(DateTime, default=func.now())


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def create_tables(engine):
    """Crée toutes les tables dans la base de données."""
    Base.metadata.create_all(engine)


def drop_tables(engine):
    """Supprime toutes les tables de la base de données."""
    Base.metadata.drop_all(engine)


def get_model_by_name(model_name: str):
    """Retourne la classe de modèle par son nom."""
    models = {
        'Organization': Organization,
        'User': User,
        'Agent': Agent,
        'AgentVersion': AgentVersion,
        'Dependency': Dependency,
        'Execution': Execution,
        'PerformanceMetric': PerformanceMetric,
        'Rating': Rating,
        'Review': Review,
        'ReviewHelpfulVote': ReviewHelpfulVote,
        'Subscription': Subscription,
        'BillingRecord': BillingRecord,
        'CompatibilityRule': CompatibilityRule,
        'UsageStatistic': UsageStatistic,
        'AuditLog': AuditLog,
        'CacheInvalidation': CacheInvalidation,
        'AgentMarketStats': AgentMarketStats,
    }
    return models.get(model_name)


# Exemple d'utilisation
if __name__ == "__main__":
    from sqlalchemy import create_engine
    
    # Création de la base de données
    engine = create_engine('postgresql://user:password@localhost/microagents_registry')
    
    # Création des tables
    create_tables(engine)
    
    print("Tables créées avec succès!")
    print(f"Nombre de tables: {len(Base.metadata.tables)}")
    
    # Liste des tables
    for table_name in Base.metadata.tables.keys():
        print(f"  - {table_name}")