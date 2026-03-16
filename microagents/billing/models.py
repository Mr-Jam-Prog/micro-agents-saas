"""
Modèles de facturation pour MicroAgents Platform

Ce module définit tous les modèles de données pour le système de facturation,
incluant les abonnements, l'utilisation, les factures, les paiements,
les taxes, les réductions, et la gestion des revenus.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, Column, DateTime, Enum as SQLEnum, ForeignKey,
    Integer, Numeric, String, Text, JSON, Index, CheckConstraint,
    UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import expression

from pydantic import BaseModel, Field, validator, root_validator

Base = declarative_base()


# ==================== ENUMS ====================

class BillingCycle(str, Enum):
    """Cycle de facturation"""
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    BIANNUAL = "biannual"
    USAGE = "usage"
    ONE_TIME = "one_time"


class SubscriptionStatus(str, Enum):
    """Statut d'un abonnement"""
    ACTIVE = "active"
    PENDING = "pending"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"


class InvoiceStatus(str, Enum):
    """Statut d'une facture"""
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


class PaymentStatus(str, Enum):
    """Statut d'un paiement"""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethodType(str, Enum):
    """Type de méthode de paiement"""
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    SEPA_DEBIT = "sepa_debit"
    BACS_DEBIT = "bacs_debit"
    PAYPAL = "paypal"
    APPLE_PAY = "apple_pay"
    GOOGLE_PAY = "google_pay"
    CRYPTO = "crypto"
    CASH = "cash"


class TaxType(str, Enum):
    """Type de taxe"""
    VAT = "vat"
    GST = "gst"
    PST = "pst"
    HST = "hst"
    SALES_TAX = "sales_tax"
    WITHHOLDING = "withholding"
    OTHER = "other"


class DiscountType(str, Enum):
    """Type de réduction"""
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"
    FREE_TRIAL = "free_trial"
    USAGE_CREDIT = "usage_credit"


class RefundStatus(str, Enum):
    """Statut d'un remboursement"""
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class RevenueRecognitionMethod(str, Enum):
    """Méthode de reconnaissance de revenus"""
    PROPORTIONAL = "proportional"  # Au prorata temporel
    IMMEDIATE = "immediate"        # Immédiate
    DEFERRED = "deferred"          # Différée
    MILESTONE = "milestone"        # Par étape


# ==================== MODÈLES DE BASE ====================

class Customer(Base):
    """
    Client ou organisation qui souscrit aux services.
    
    Relations:
        - Un client a plusieurs abonnements
        - Un client a plusieurs factures
        - Un client a plusieurs méthodes de paiement
        - Un client a plusieurs crédits
    """
    __tablename__ = "customers"
    
    # Identifiants
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    external_id = Column(String(100), unique=True, index=True)  # ID dans système externe
    
    # Informations de base
    email = Column(String(255), nullable=False, index=True)
    name = Column(String(255))
    phone = Column(String(50))
    company = Column(String(255))
    vat_number = Column(String(50), index=True)  # Numéro de TVA
    tax_id = Column(String(100))  # Autre identification fiscale
    
    # Adresse de facturation
    address_line1 = Column(String(255))
    address_line2 = Column(String(255))
    address_city = Column(String(100))
    address_state = Column(String(100))
    address_postal_code = Column(String(20))
    address_country = Column(String(2))  # Code ISO 3166-1 alpha-2
    
    # Préférences et métadonnées
    currency = Column(String(3), default="USD")  # Devise par défaut
    timezone = Column(String(50), default="UTC")
    locale = Column(String(10), default="en")
    metadata = Column(JSON, default=dict)
    
    # Statut et conformité
    is_tax_exempt = Column(Boolean, default=False)
    tax_exempt_reason = Column(String(100))
    gdpr_consent = Column(Boolean, default=False)
    gdpr_consent_date = Column(DateTime)
    
    # Relations
    subscriptions = relationship("Subscription", back_populates="customer", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="customer", cascade="all, delete-orphan")
    payment_methods = relationship("PaymentMethod", back_populates="customer", cascade="all, delete-orphan")
    credits = relationship("CustomerCredit", back_populates="customer", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    
    # Index
    __table_args__ = (
        Index('idx_customers_email_deleted', 'email', 'deleted_at'),
        Index('idx_customers_stripe_id', 'stripe_id'),
        Index('idx_customers_company', 'company'),
    )
    
    @validates('email')
    def validate_email(self, key, email):
        """Valide le format de l'email."""
        if email and '@' not in email:
            raise ValueError("Email invalide")
        return email.lower()
    
    @validates('address_country')
    def validate_country(self, key, country):
        """Valide le code pays."""
        if country and len(country) != 2:
            raise ValueError("Le code pays doit être sur 2 caractères")
        return country.upper() if country else None


class Product(Base):
    """
    Produit ou service vendu.
    
    Relations:
        - Un produit a plusieurs plans de prix
        - Un produit a plusieurs fonctionnalités
    """
    __tablename__ = "products"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Informations du produit
    name = Column(String(255), nullable=False)
    description = Column(Text)
    statement_descriptor = Column(String(22))  # Description sur relevé bancaire
    
    # Catégorisation
    category = Column(String(100))
    subcategory = Column(String(100))
    product_type = Column(String(50))  # service, physical, digital
    
    # Métadonnées et configuration
    metadata = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    tax_code = Column(String(100))  # Code fiscal Stripe
    
    # Relations
    plans = relationship("PricingPlan", back_populates="product", cascade="all, delete-orphan")
    features = relationship("ProductFeature", back_populates="product", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_products_name_active', 'name', 'is_active'),
        UniqueConstraint('stripe_id', name='uq_products_stripe_id'),
        CheckConstraint('LENGTH(statement_descriptor) <= 22', name='chk_statement_desc_length'),
    )


class PricingPlan(Base):
    """
    Plan de tarification pour un produit.
    
    Relations:
        - Un plan appartient à un produit
        - Un plan a plusieurs niveaux de tarification
        - Un plan a plusieurs fonctionnalités incluses
    """
    __tablename__ = "pricing_plans"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    product_id = Column(PG_UUID(as_uuid=True), ForeignKey('products.id', ondelete='CASCADE'), nullable=False)
    
    # Informations du plan
    name = Column(String(255), nullable=False)
    description = Column(Text)
    
    # Configuration de tarification
    billing_scheme = Column(String(50), nullable=False)  # per_unit, tiered, graduated
    usage_type = Column(String(50), default="licensed")  # licensed, metered
    billing_cycle = Column(SQLEnum(BillingCycle), nullable=False)
    
    # Montants et devises
    amount = Column(Integer)  # En centimes, null pour les plans à niveaux
    currency = Column(String(3), default="USD")
    
    # Période d'engagement
    interval = Column(String(20))  # day, week, month, year
    interval_count = Column(Integer, default=1)
    
    # Essai gratuit
    trial_period_days = Column(Integer, default=0)
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    
    # Relations
    product = relationship("Product", back_populates="plans")
    tiers = relationship("PricingTier", back_populates="plan", cascade="all, delete-orphan")
    included_features = relationship("PlanFeature", back_populates="plan", cascade="all, delete-orphan")
    subscriptions = relationship("Subscription", back_populates="plan", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_plans_product_active', 'product_id', 'is_active'),
        UniqueConstraint('stripe_id', name='uq_plans_stripe_id'),
        CheckConstraint('amount >= 0 OR amount IS NULL', name='chk_plan_amount_non_negative'),
        CheckConstraint('trial_period_days >= 0', name='chk_trial_non_negative'),
    )
    
    @validates('billing_scheme')
    def validate_billing_scheme(self, key, scheme):
        """Valide le schéma de facturation."""
        if scheme not in ['per_unit', 'tiered', 'graduated']:
            raise ValueError("Schéma de facturation invalide")
        return scheme


class PricingTier(Base):
    """
    Niveau de tarification pour les plans à paliers.
    
    Relations:
        - Un niveau appartient à un plan
    """
    __tablename__ = "pricing_tiers"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id = Column(PG_UUID(as_uuid=True), ForeignKey('pricing_plans.id', ondelete='CASCADE'), nullable=False)
    
    # Définition du niveau
    up_to = Column(Integer, nullable=True)  # Null pour infini
    unit_amount = Column(Integer, nullable=False)  # En centimes par unité
    flat_amount = Column(Integer)  # Montant fixe en centimes
    
    # Configuration
    tier_mode = Column(String(20), default="graduated")  # graduated, volume
    
    # Relations
    plan = relationship("PricingPlan", back_populates="tiers")
    
    __table_args__ = (
        Index('idx_tiers_plan_up_to', 'plan_id', 'up_to'),
        CheckConstraint('up_to IS NULL OR up_to > 0', name='chk_up_to_positive'),
        CheckConstraint('unit_amount >= 0', name='chk_unit_amount_non_negative'),
        CheckConstraint('flat_amount IS NULL OR flat_amount >= 0', name='chk_flat_amount_non_negative'),
    )


class ProductFeature(Base):
    """
    Fonctionnalité d'un produit.
    
    Relations:
        - Une fonctionnalité appartient à un produit
        - Une fonctionnalité peut être incluse dans plusieurs plans
    """
    __tablename__ = "product_features"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    product_id = Column(PG_UUID(as_uuid=True), ForeignKey('products.id', ondelete='CASCADE'), nullable=False)
    
    # Description de la fonctionnalité
    name = Column(String(255), nullable=False)
    description = Column(Text)
    key = Column(String(100), nullable=False)  # Identifiant technique
    
    # Type de fonctionnalité
    feature_type = Column(String(50), default="boolean")  # boolean, numeric, enum
    unit = Column(String(50))  # unité de mesure
    
    # Valeurs par défaut
    default_value = Column(JSON)
    min_value = Column(Numeric(20, 6))
    max_value = Column(Numeric(20, 6))
    
    # Métadonnées
    is_required = Column(Boolean, default=False)
    display_order = Column(Integer, default=0)
    
    # Relations
    product = relationship("Product", back_populates="features")
    plan_inclusions = relationship("PlanFeature", back_populates="feature", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_features_product_key', 'product_id', 'key', unique=True),
        CheckConstraint('display_order >= 0', name='chk_display_order_non_negative'),
    )


class PlanFeature(Base):
    """
    Fonctionnalité incluse dans un plan avec ses limites.
    
    Relations:
        - Un PlanFeature lie un plan à une fonctionnalité
    """
    __tablename__ = "plan_features"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id = Column(PG_UUID(as_uuid=True), ForeignKey('pricing_plans.id', ondelete='CASCADE'), nullable=False)
    feature_id = Column(PG_UUID(as_uuid=True), ForeignKey('product_features.id', ondelete='CASCADE'), nullable=False)
    
    # Valeurs spécifiques au plan
    included_value = Column(JSON)  # Valeur incluse (bool, nombre, etc.)
    limit_type = Column(String(50))  # soft, hard, none
    limit_value = Column(Numeric(20, 6))  # Valeur limite
    
    # Surcoût en cas de dépassement
    overage_price = Column(Integer)  # En centimes par unité
    overage_currency = Column(String(3), default="USD")
    
    # Relations
    plan = relationship("PricingPlan", back_populates="included_features")
    feature = relationship("ProductFeature", back_populates="plan_inclusions")
    
    __table_args__ = (
        UniqueConstraint('plan_id', 'feature_id', name='uq_plan_feature'),
        Index('idx_plan_features_plan', 'plan_id'),
        Index('idx_plan_features_feature', 'feature_id'),
    )


class Subscription(Base):
    """
    Abonnement d'un client à un plan.
    
    Relations:
        - Un abonnement appartient à un client
        - Un abonnement référence un plan
        - Un abonnement a plusieurs enregistrements d'usage
        - Un abonnement a plusieurs items de facture
    """
    __tablename__ = "subscriptions"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    plan_id = Column(PG_UUID(as_uuid=True), ForeignKey('pricing_plans.id', ondelete='SET NULL'), nullable=True)
    
    # Statut et période
    status = Column(SQLEnum(SubscriptionStatus), nullable=False, index=True)
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=False)
    
    # Annulation
    cancel_at_period_end = Column(Boolean, default=False)
    canceled_at = Column(DateTime)
    
    # Essai
    trial_start = Column(DateTime)
    trial_end = Column(DateTime)
    
    # Quantité et métadonnées
    quantity = Column(Integer, default=1)
    metadata = Column(JSON, default=dict)
    
    # Données de facturation
    billing_cycle_anchor = Column(DateTime)
    days_until_due = Column(Integer, default=30)
    
    # Relations
    customer = relationship("Customer", back_populates="subscriptions")
    plan = relationship("PricingPlan", back_populates="subscriptions")
    usage_records = relationship("UsageRecord", back_populates="subscription", cascade="all, delete-orphan")
    invoice_items = relationship("InvoiceItem", back_populates="subscription", cascade="all, delete-orphan")
    prorations = relationship("Proration", back_populates="subscription", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_subscriptions_customer_status', 'customer_id', 'status'),
        Index('idx_subscriptions_period_end', 'current_period_end'),
        Index('idx_subscriptions_trial_end', 'trial_end'),
        CheckConstraint('quantity > 0', name='chk_subscription_quantity_positive'),
        CheckConstraint('current_period_end > current_period_start', name='chk_period_valid'),
    )
    
    @property
    def is_trialing(self) -> bool:
        """Vérifie si l'abonnement est en période d'essai."""
        if not self.trial_end:
            return False
        now = datetime.utcnow()
        return self.trial_start <= now <= self.trial_end


class UsageRecord(Base):
    """
    Enregistrement d'utilisation pour la facturation à l'usage.
    
    Relations:
        - Un usage appartient à un abonnement
        - Un usage est lié à une fonctionnalité
    """
    __tablename__ = "usage_records"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    subscription_id = Column(PG_UUID(as_uuid=True), ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False)
    feature_id = Column(PG_UUID(as_uuid=True), ForeignKey('product_features.id', ondelete='SET NULL'), nullable=True)
    
    # Métriques d'utilisation
    quantity = Column(Numeric(20, 6), nullable=False)
    action = Column(String(20), default="increment")  # increment, set
    
    # Période de mesure
    timestamp = Column(DateTime, nullable=False, index=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    
    # Relations
    subscription = relationship("Subscription", back_populates="usage_records")
    feature = relationship("ProductFeature")
    
    __table_args__ = (
        Index('idx_usage_subscription_timestamp', 'subscription_id', 'timestamp'),
        Index('idx_usage_feature_timestamp', 'feature_id', 'timestamp'),
        CheckConstraint('quantity >= 0', name='chk_usage_quantity_non_negative'),
    )


class Invoice(Base):
    """
    Facture émise à un client.
    
    Relations:
        - Une facture appartient à un client
        - Une facture a plusieurs items
        - Une facture peut avoir plusieurs paiements
        - Une facture peut générer une note de crédit
    """
    __tablename__ = "invoices"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    subscription_id = Column(PG_UUID(as_uuid=True), ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True)
    
    # Informations de base
    number = Column(String(50), unique=True, index=True)  # Numéro de facture
    status = Column(SQLEnum(InvoiceStatus), nullable=False, index=True)
    
    # Montants
    amount_due = Column(Integer, nullable=False)  # En centimes
    amount_paid = Column(Integer, default=0)
    amount_remaining = Column(Integer, default=0)
    amount_discount = Column(Integer, default=0)
    amount_tax = Column(Integer, default=0)
    amount_shipping = Column(Integer, default=0)
    
    # Devise et taxes
    currency = Column(String(3), nullable=False, default="USD")
    tax_percent = Column(Numeric(5, 2))  # Pourcentage de taxe
    
    # Dates
    date = Column(DateTime, default=datetime.utcnow, nullable=False)
    due_date = Column(DateTime, index=True)
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    
    # Paiement
    paid_at = Column(DateTime)
    payment_intent_id = Column(String(100))
    
    # Documents
    invoice_pdf = Column(String(500))  # URL du PDF
    hosted_invoice_url = Column(String(500))
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    description = Column(Text)
    
    # Relations
    customer = relationship("Customer", back_populates="invoices")
    subscription = relationship("Subscription")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="invoice", cascade="all, delete-orphan")
    credit_notes = relationship("CreditNote", back_populates="invoice", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_invoices_customer_status', 'customer_id', 'status'),
        Index('idx_invoices_due_date', 'due_date'),
        Index('idx_invoices_number', 'number'),
        CheckConstraint('amount_due >= 0', name='chk_invoice_amount_due_non_negative'),
        CheckConstraint('amount_paid >= 0', name='chk_invoice_amount_paid_non_negative'),
        CheckConstraint('amount_remaining >= 0', name='chk_invoice_amount_remaining_non_negative'),
        CheckConstraint('amount_due = amount_paid + amount_remaining', name='chk_invoice_amounts_consistent'),
    )
    
    @property
    def is_overdue(self) -> bool:
        """Vérifie si la facture est en retard."""
        if not self.due_date or self.status != InvoiceStatus.OPEN:
            return False
        return datetime.utcnow() > self.due_date
    
    @property
    def overdue_days(self) -> Optional[int]:
        """Retourne le nombre de jours de retard."""
        if not self.is_overdue:
            return None
        return (datetime.utcnow() - self.due_date).days


class InvoiceItem(Base):
    """
    Item d'une facture.
    
    Relations:
        - Un item appartient à une facture
        - Un item peut être lié à un abonnement
        - Un item peut être lié à une période
    """
    __tablename__ = "invoice_items"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    invoice_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoices.id', ondelete='CASCADE'), nullable=False)
    subscription_id = Column(PG_UUID(as_uuid=True), ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True)
    
    # Description
    description = Column(Text, nullable=False)
    type = Column(String(50), default="subscription")  # subscription, usage, one_time, etc.
    
    # Montants
    amount = Column(Integer, nullable=False)  # En centimes
    quantity = Column(Numeric(10, 3), default=1.0)
    unit_amount = Column(Integer)  # En centimes par unité
    
    # Période
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    
    # Taxes
    tax_amount = Column(Integer, default=0)
    tax_rates = Column(JSON)  # Liste des taux de taxes appliqués
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    
    # Relations
    invoice = relationship("Invoice", back_populates="items")
    subscription = relationship("Subscription", back_populates="invoice_items")
    
    __table_args__ = (
        Index('idx_invoice_items_invoice', 'invoice_id'),
        Index('idx_invoice_items_subscription', 'subscription_id'),
        CheckConstraint('amount >= 0', name='chk_invoice_item_amount_non_negative'),
        CheckConstraint('quantity >= 0', name='chk_invoice_item_quantity_non_negative'),
    )


class Payment(Base):
    """
    Paiement effectué pour une facture.
    
    Relations:
        - Un paiement appartient à une facture
        - Un paiement est effectué par une méthode de paiement
        - Un paiement peut générer un remboursement
    """
    __tablename__ = "payments"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    invoice_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoices.id', ondelete='CASCADE'), nullable=False)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    
    # Informations de base
    amount = Column(Integer, nullable=False)  # En centimes
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(SQLEnum(PaymentStatus), nullable=False, index=True)
    
    # Méthode de paiement
    payment_method_id = Column(String(100), index=True)
    payment_method_type = Column(SQLEnum(PaymentMethodType))
    
    # Frais
    application_fee_amount = Column(Integer, default=0)
    processing_fee = Column(Integer, default=0)
    
    # Dates
    authorized_at = Column(DateTime)
    captured_at = Column(DateTime)
    processed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Données bancaires/carte (chiffrées)
    last4 = Column(String(4))
    brand = Column(String(50))
    country = Column(String(2))
    
    # Erreurs
    error_code = Column(String(50))
    error_message = Column(Text)
    decline_code = Column(String(50))
    
    # Relations
    invoice = relationship("Invoice", back_populates="payments")
    customer = relationship("Customer")
    refunds = relationship("Refund", back_populates="payment", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_payments_customer_processed', 'customer_id', 'processed_at'),
        Index('idx_payments_invoice_status', 'invoice_id', 'status'),
        CheckConstraint('amount > 0', name='chk_payment_amount_positive'),
        CheckConstraint('application_fee_amount >= 0', name='chk_fee_non_negative'),
    )


class PaymentMethod(Base):
    """
    Méthode de paiement d'un client.
    
    Relations:
        - Une méthode de paiement appartient à un client
    """
    __tablename__ = "payment_methods"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    
    # Type de méthode
    type = Column(SQLEnum(PaymentMethodType), nullable=False)
    is_default = Column(Boolean, default=False)
    
    # Données spécifiques (chiffrées)
    card_brand = Column(String(50))
    card_last4 = Column(String(4))
    card_exp_month = Column(Integer)
    card_exp_year = Column(Integer)
    card_fingerprint = Column(String(100))
    
    bank_name = Column(String(100))
    bank_last4 = Column(String(4))
    bank_routing_number = Column(String(9))
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    
    # Relations
    customer = relationship("Customer", back_populates="payment_methods")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_payment_methods_customer_default', 'customer_id', 'is_default'),
        UniqueConstraint('stripe_id', name='uq_payment_methods_stripe_id'),
        CheckConstraint('card_exp_month IS NULL OR (card_exp_month >= 1 AND card_exp_month <= 12)', 
                       name='chk_card_exp_month'),
        CheckConstraint('card_exp_year IS NULL OR card_exp_year >= 2000', 
                       name='chk_card_exp_year'),
    )


class TaxRate(Base):
    """
    Taux de taxe applicable.
    
    Relations:
        - Un taux de taxe peut être appliqué à plusieurs items
    """
    __tablename__ = "tax_rates"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Informations de base
    display_name = Column(String(100), nullable=False)
    description = Column(Text)
    jurisdiction = Column(String(100), nullable=False)
    tax_type = Column(SQLEnum(TaxType), nullable=False)
    
    # Taux
    percentage = Column(Numeric(5, 2), nullable=False)
    is_inclusive = Column(Boolean, default=False)  # TTC ou HT
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    
    # Relations
    invoice_items = relationship("InvoiceItemTax", back_populates="tax_rate", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint('jurisdiction', 'tax_type', 'is_inclusive', 'percentage', 
                        name='uq_tax_rate_unique'),
        CheckConstraint('percentage >= 0 AND percentage <= 100', 
                       name='chk_tax_percentage_range'),
    )


class InvoiceItemTax(Base):
    """
    Lien entre un item de facture et un taux de taxe.
    """
    __tablename__ = "invoice_item_taxes"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_item_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoice_items.id', ondelete='CASCADE'), nullable=False)
    tax_rate_id = Column(PG_UUID(as_uuid=True), ForeignKey('tax_rates.id', ondelete='CASCADE'), nullable=False)
    
    # Montant de taxe pour cet item
    tax_amount = Column(Integer, nullable=False)  # En centimes
    
    # Relations
    invoice_item = relationship("InvoiceItem")
    tax_rate = relationship("TaxRate", back_populates="invoice_items")
    
    __table_args__ = (
        UniqueConstraint('invoice_item_id', 'tax_rate_id', name='uq_invoice_item_tax'),
        CheckConstraint('tax_amount >= 0', name='chk_tax_amount_non_negative'),
    )


class DiscountCode(Base):
    """
    Code de réduction.
    
    Relations:
        - Un code de réduction peut être appliqué à plusieurs factures
    """
    __tablename__ = "discount_codes"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Information du code
    code = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255))
    description = Column(Text)
    
    # Type de réduction
    discount_type = Column(SQLEnum(DiscountType), nullable=False)
    amount = Column(Integer)  # En centimes ou pourcentage
    currency = Column(String(3))  # Pour les réductions fixes
    
    # Durée
    duration = Column(String(20), default="once")  # once, forever, repeating
    duration_in_months = Column(Integer)
    
    # Limitations
    max_redemptions = Column(Integer)
    times_redeemed = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    
    # Dates
    valid_from = Column(DateTime)
    valid_until = Column(DateTime)
    
    # Restrictions
    applies_to_products = Column(JSON)  # Liste d'IDs de produits
    applies_to_plans = Column(JSON)     # Liste d'IDs de plans
    minimum_amount = Column(Integer)    # Montant minimum d'achat
    
    # Relations
    invoice_discounts = relationship("InvoiceDiscount", back_populates="discount_code", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_discount_codes_code_active', 'code', 'is_active'),
        CheckConstraint('amount > 0', name='chk_discount_amount_positive'),
        CheckConstraint('times_redeemed >= 0', name='chk_times_redeemed_non_negative'),
        CheckConstraint('max_redemptions IS NULL OR max_redemptions > 0', 
                       name='chk_max_redemptions_positive'),
        CheckConstraint('valid_until IS NULL OR valid_until > valid_from', 
                       name='chk_discount_valid_dates'),
    )
    
    @property
    def is_valid(self) -> bool:
        """Vérifie si le code de réduction est valide."""
        if not self.is_active:
            return False
        
        now = datetime.utcnow()
        
        if self.valid_from and now < self.valid_from:
            return False
        
        if self.valid_until and now > self.valid_until:
            return False
        
        if self.max_redemptions and self.times_redeemed >= self.max_redemptions:
            return False
        
        return True


class InvoiceDiscount(Base):
    """
    Application d'un code de réduction à une facture.
    """
    __tablename__ = "invoice_discounts"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoices.id', ondelete='CASCADE'), nullable=False)
    discount_code_id = Column(PG_UUID(as_uuid=True), ForeignKey('discount_codes.id', ondelete='CASCADE'), nullable=False)
    
    # Montant de la réduction appliquée
    amount = Column(Integer, nullable=False)  # En centimes
    
    # Relations
    invoice = relationship("Invoice")
    discount_code = relationship("DiscountCode", back_populates="invoice_discounts")
    
    __table_args__ = (
        UniqueConstraint('invoice_id', 'discount_code_id', name='uq_invoice_discount'),
        CheckConstraint('amount > 0', name='chk_invoice_discount_amount_positive'),
    )


class CreditNote(Base):
    """
    Note de crédit émise pour une facture.
    
    Relations:
        - Une note de crédit appartient à une facture
        - Une note de crédit a plusieurs items
    """
    __tablename__ = "credit_notes"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    invoice_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoices.id', ondelete='CASCADE'), nullable=False)
    
    # Informations de base
    number = Column(String(50), unique=True, index=True)
    reason = Column(String(50))  # duplicate, fraudulent, order_change, product_unsatisfactory
    
    # Montants
    amount = Column(Integer, nullable=False)  # En centimes
    amount_refunded = Column(Integer, default=0)
    amount_available = Column(Integer, default=0)
    
    # Devise et taxes
    currency = Column(String(3), nullable=False, default="USD")
    tax_amount = Column(Integer, default=0)
    
    # Statut
    status = Column(String(20), default="issued")  # issued, void, applied
    
    # Dates
    date = Column(DateTime, default=datetime.utcnow, nullable=False)
    voided_at = Column(DateTime)
    
    # Relations
    invoice = relationship("Invoice", back_populates="credit_notes")
    items = relationship("CreditNoteItem", back_populates="credit_note", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_credit_notes_invoice', 'invoice_id'),
        CheckConstraint('amount > 0', name='chk_credit_note_amount_positive'),
        CheckConstraint('amount_refunded >= 0', name='chk_credit_note_refunded_non_negative'),
        CheckConstraint('amount_available >= 0', name='chk_credit_note_available_non_negative'),
        CheckConstraint('amount = amount_refunded + amount_available', 
                       name='chk_credit_note_amounts_consistent'),
    )


class CreditNoteItem(Base):
    """
    Item d'une note de crédit.
    """
    __tablename__ = "credit_note_items"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    credit_note_id = Column(PG_UUID(as_uuid=True), ForeignKey('credit_notes.id', ondelete='CASCADE'), nullable=False)
    invoice_item_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoice_items.id', ondelete='SET NULL'), nullable=True)
    
    # Description
    description = Column(Text, nullable=False)
    type = Column(String(50))  # refund, adjustment
    
    # Montants
    amount = Column(Integer, nullable=False)  # En centimes
    quantity = Column(Numeric(10, 3), default=1.0)
    unit_amount = Column(Integer)  # En centimes par unité
    
    # Taxes
    tax_amount = Column(Integer, default=0)
    
    # Relations
    credit_note = relationship("CreditNote", back_populates="items")
    invoice_item = relationship("InvoiceItem")
    
    __table_args__ = (
        CheckConstraint('amount > 0', name='chk_credit_note_item_amount_positive'),
        CheckConstraint('quantity >= 0', name='chk_credit_note_item_quantity_non_negative'),
    )


class Refund(Base):
    """
    Remboursement d'un paiement.
    
    Relations:
        - Un remboursement appartient à un paiement
        - Un remboursement peut être lié à une note de crédit
    """
    __tablename__ = "refunds"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    stripe_id = Column(String(100), unique=True, nullable=False, index=True)
    payment_id = Column(PG_UUID(as_uuid=True), ForeignKey('payments.id', ondelete='CASCADE'), nullable=False)
    credit_note_id = Column(PG_UUID(as_uuid=True), ForeignKey('credit_notes.id', ondelete='SET NULL'), nullable=True)
    
    # Informations de base
    amount = Column(Integer, nullable=False)  # En centimes
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(SQLEnum(RefundStatus), nullable=False)
    
    # Raison
    reason = Column(String(50))  # duplicate, fraudulent, requested_by_customer
    
    # Dates
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime)
    failed_at = Column(DateTime)
    
    # Méthode de remboursement
    refund_method = Column(String(50))  # original_payment_method, bank_transfer
    
    # Données bancaires pour virement
    bank_account_last4 = Column(String(4))
    bank_name = Column(String(100))
    
    # Relations
    payment = relationship("Payment", back_populates="refunds")
    credit_note = relationship("CreditNote")
    
    __table_args__ = (
        Index('idx_refunds_payment_status', 'payment_id', 'status'),
        CheckConstraint('amount > 0', name='chk_refund_amount_positive'),
        CheckConstraint('processed_at IS NULL OR processed_at >= requested_at', 
                       name='chk_refund_dates'),
    )


class Proration(Base):
    """
    Proration pour les changements d'abonnement.
    
    Relations:
        - Une proration appartient à un abonnement
        - Une proration génère un item de facture
    """
    __tablename__ = "prorations"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    subscription_id = Column(PG_UUID(as_uuid=True), ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False)
    invoice_item_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoice_items.id', ondelete='SET NULL'), nullable=True)
    
    # Informations de proration
    proration_type = Column(String(50), nullable=False)  # upgrade, downgrade, cancellation
    proration_date = Column(DateTime, nullable=False)
    
    # Calculs
    unused_amount = Column(Integer, nullable=False)  # En centimes
    prorated_amount = Column(Integer, nullable=False)  # En centimes
    days_remaining = Column(Integer, nullable=False)
    days_in_period = Column(Integer, nullable=False)
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    
    # Relations
    subscription = relationship("Subscription", back_populates="prorations")
    invoice_item = relationship("InvoiceItem")
    
    __table_args__ = (
        Index('idx_prorations_subscription_date', 'subscription_id', 'proration_date'),
        CheckConstraint('days_remaining >= 0', name='chk_proration_days_remaining_non_negative'),
        CheckConstraint('days_in_period > 0', name='chk_proration_days_in_period_positive'),
    )


class CustomerCredit(Base):
    """
    Crédit disponible pour un client.
    
    Relations:
        - Un crédit appartient à un client
    """
    __tablename__ = "customer_credits"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    
    # Informations de crédit
    amount = Column(Integer, nullable=False)  # En centimes
    currency = Column(String(3), nullable=False, default="USD")
    
    # Origine
    source = Column(String(50))  # promotion, refund, manual_adjustment
    reason = Column(Text)
    
    # Dates
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime)
    
    # Utilisation
    used_amount = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    
    # Relations
    customer = relationship("Customer", back_populates="credits")
    
    __table_args__ = (
        Index('idx_customer_credits_customer_active', 'customer_id', 'is_active'),
        Index('idx_customer_credits_expires', 'expires_at'),
        CheckConstraint('amount > 0', name='chk_customer_credit_amount_positive'),
        CheckConstraint('used_amount >= 0', name='chk_customer_credit_used_non_negative'),
        CheckConstraint('used_amount <= amount', name='chk_customer_credit_used_leq_amount'),
    )
    
    @property
    def available_amount(self) -> int:
        """Montant de crédit disponible."""
        return self.amount - self.used_amount
    
    @property
    def is_expired(self) -> bool:
        """Vérifie si le crédit a expiré."""
        if not self.expires_at:
            return False
        return datetime.utcnow() > self.expires_at


class RevenueSchedule(Base):
    """
    Calendrier de reconnaissance de revenus.
    
    Relations:
        - Un calendrier est lié à une facture
    """
    __tablename__ = "revenue_schedules"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id = Column(PG_UUID(as_uuid=True), ForeignKey('invoices.id', ondelete='CASCADE'), nullable=False)
    
    # Méthode de reconnaissance
    recognition_method = Column(SQLEnum(RevenueRecognitionMethod), nullable=False)
    
    # Montants
    total_amount = Column(Integer, nullable=False)  # En centimes
    recognized_amount = Column(Integer, default=0)
    deferred_amount = Column(Integer, default=0)
    
    # Période
    recognition_start = Column(DateTime, nullable=False)
    recognition_end = Column(DateTime, nullable=False)
    
    # Métadonnées
    metadata = Column(JSON, default=dict)
    
    # Relations
    invoice = relationship("Invoice")
    entries = relationship("RevenueScheduleEntry", back_populates="schedule", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_revenue_schedules_invoice', 'invoice_id'),
        Index('idx_revenue_schedules_recognition_end', 'recognition_end'),
        CheckConstraint('total_amount > 0', name='chk_revenue_schedule_total_positive'),
        CheckConstraint('recognized_amount >= 0', name='chk_revenue_recognized_non_negative'),
        CheckConstraint('deferred_amount >= 0', name='chk_revenue_deferred_non_negative'),
        CheckConstraint('total_amount = recognized_amount + deferred_amount', 
                       name='chk_revenue_amounts_consistent'),
        CheckConstraint('recognition_end > recognition_start', 
                       name='chk_revenue_period_valid'),
    )


class RevenueScheduleEntry(Base):
    """
    Entrée dans le calendrier de reconnaissance de revenus.
    """
    __tablename__ = "revenue_schedule_entries"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    schedule_id = Column(PG_UUID(as_uuid=True), ForeignKey('revenue_schedules.id', ondelete='CASCADE'), nullable=False)
    
    # Période
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Montant à reconnaître
    amount_to_recognize = Column(Integer, nullable=False)  # En centimes
    amount_recognized = Column(Integer, default=0)
    
    # Dates de reconnaissance
    scheduled_recognition_date = Column(DateTime, nullable=False)
    actual_recognition_date = Column(DateTime)
    
    # Statut
    status = Column(String(20), default="scheduled")  # scheduled, recognized, skipped
    
    # Relations
    schedule = relationship("RevenueSchedule", back_populates="entries")
    
    __table_args__ = (
        Index('idx_revenue_entries_schedule_period', 'schedule_id', 'period_start'),
        Index('idx_revenue_entries_recognition_date', 'scheduled_recognition_date'),
        CheckConstraint('amount_to_recognize > 0', name='chk_revenue_entry_amount_positive'),
        CheckConstraint('amount_recognized >= 0', name='chk_revenue_recognized_non_negative'),
        CheckConstraint('amount_recognized <= amount_to_recognize', 
                       name='chk_revenue_recognized_leq_amount'),
        CheckConstraint('period_end > period_start', name='chk_revenue_entry_period_valid'),
    )


# ==================== MODÈLES PYDANTIC POUR VALIDATION ====================

class CustomerCreate(BaseModel):
    """Modèle pour la création d'un client."""
    email: str = Field(..., min_length=3, max_length=255)
    name: Optional[str] = Field(None, max_length=255)
    company: Optional[str] = Field(None, max_length=255)
    vat_number: Optional[str] = Field(None, max_length=50)
    address_country: Optional[str] = Field(None, min_length=2, max_length=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @validator('email')
    def validate_email_format(cls, v):
        if '@' not in v:
            raise ValueError('Email invalide')
        return v.lower()
    
    @validator('address_country')
    def validate_country_code(cls, v):
        if v and len(v) != 2:
            raise ValueError('Le code pays doit contenir exactement 2 caractères')
        return v.upper() if v else None


class SubscriptionCreate(BaseModel):
    """Modèle pour la création d'un abonnement."""
    customer_id: UUID
    plan_id: UUID
    quantity: int = Field(default=1, gt=0)
    trial_days: int = Field(default=0, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InvoiceCreate(BaseModel):
    """Modèle pour la création d'une facture."""
    customer_id: UUID
    amount_due: int = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    due_date: Optional[datetime] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @validator('due_date')
    def validate_due_date(cls, v):
        if v and v < datetime.utcnow():
            raise ValueError('La date d\'échéance ne peut pas être dans le passé')
        return v


class PaymentCreate(BaseModel):
    """Modèle pour la création d'un paiement."""
    invoice_id: UUID
    amount: int = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    payment_method_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ==================== INDEX ADDITIONNELS POUR PERFORMANCE ====================

# Ces index sont déjà définis dans les contraintes __table_args__
# Voici quelques index supplémentaires suggérés pour les requêtes courantes

# Index pour les recherches de clients par nom et email
Index('idx_customers_name_email', Customer.name, Customer.email)

# Index pour les factures en retard
Index('idx_invoices_overdue', Invoice.due_date, Invoice.status)

# Index pour les abonnements actifs
Index('idx_subscriptions_active', Subscription.status, Subscription.current_period_end)

# Index pour l'agrégation d'usage
Index('idx_usage_aggregation', UsageRecord.feature_id, UsageRecord.timestamp)

# Index pour les paiements récents
Index('idx_payments_recent', Payment.processed_at.desc())

# Index pour les crédits expirant bientôt
Index('idx_credits_expiring', CustomerCredit.expires_at, CustomerCredit.is_active)