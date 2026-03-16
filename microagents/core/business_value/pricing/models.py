"""
Modèles Pydantic pour la tarification et la facturation de MicroAgents Platform.

Ce module définit tous les modèles de données pour:
- Plans de tarification (Freemium, Professional, Enterprise)
- Facturation à l'usage
- Tarification basée sur le ROI
- Modèles d'abonnement
- Accords de tarification personnalisés
- Gestion des réductions
- Règles de proration
- Calcul des taxes
- Conversion de devises
- Génération de factures
"""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    Field,
    validator,
    root_validator,
    condecimal,
    conint,
    constr
)
from pydantic.generics import GenericModel
from typing import Generic, TypeVar

# ==================== ENUMS ====================

class BillingPeriod(str, Enum):
    """Période de facturation"""
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    BIANNUAL = "biannual"
    CUSTOM = "custom"


class PricingModel(str, Enum):
    """Modèle de tarification"""
    TIERED = "tiered"  # Par niveaux (Freemium, Pro, Enterprise)
    USAGE_BASED = "usage_based"  # À l'usage
    ROI_BASED = "roi_based"  # Pourcentage des économies
    SEAT_BASED = "seat_based"  # Par utilisateur
    REVENUE_SHARE = "revenue_share"  # Partage de revenus
    CUSTOM = "custom"  # Personnalisé


class PlanTier(str, Enum):
    """Niveaux de plan"""
    FREEMIUM = "freemium"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    CUSTOM = "custom"


class SubscriptionStatus(str, Enum):
    """Statut d'abonnement"""
    ACTIVE = "active"
    PENDING = "pending"
    CANCELED = "canceled"
    EXPIRED = "expired"
    TRIAL = "trial"
    PAST_DUE = "past_due"


class InvoiceStatus(str, Enum):
    """Statut de facture"""
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"
    PARTIALLY_PAID = "partially_paid"


class DiscountType(str, Enum):
    """Type de réduction"""
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"
    FREE_TRIAL = "free_trial"
    CREDIT = "credit"


class TaxType(str, Enum):
    """Type de taxe"""
    VAT = "vat"  # TVA européenne
    GST = "gst"  # Taxe sur les biens et services
    SALES_TAX = "sales_tax"  # Taxe de vente US
    SERVICE_TAX = "service_tax"  # Taxe sur les services
    CUSTOM = "custom"  # Taxe personnalisée


class Currency(str, Enum):
    """Devises supportées"""
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CAD = "CAD"
    AUD = "AUD"
    CHF = "CHF"
    CNY = "CNY"
    INR = "INR"


# ==================== MODÈLES DE BASE ====================

class PricingFeature(BaseModel):
    """Fonctionnalité incluse dans un plan"""
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    limit: Optional[int] = None  # Limite d'utilisation (None = illimité)
    unit: Optional[str] = None  # Unité de mesure (agents, GB, etc.)

    class Config:
        schema_extra = {
            "example": {
                "code": "cost_agents",
                "name": "Agents d'optimisation des coûts",
                "description": "Accès à 50 agents d'optimisation des coûts cloud",
                "limit": 50,
                "unit": "agents"
            }
        }


class PricingTierLimits(BaseModel):
    """Limites par niveau de tarification"""
    max_agents: Optional[int] = None
    max_users: Optional[int] = None
    max_projects: Optional[int] = None
    data_retention_days: Optional[int] = None
    support_level: str = "community"  # community, priority, dedicated
    sla_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    custom_domains: bool = False
    api_rate_limit: Optional[int] = None  # Requêtes par minute

    @validator('sla_percentage')
    def validate_sla(cls, v):
        if v is not None and v < 99.9:
            raise ValueError("SLA minimum: 99.9%")
        return v


class PricingPlan(BaseModel):
    """Modèle de plan de tarification"""
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., max_length=1000)
    tier: PlanTier
    pricing_model: PricingModel

    # Pricing
    base_price: Decimal = Field(..., ge=0)
    currency: Currency = Currency.USD
    billing_period: BillingPeriod = BillingPeriod.MONTHLY

    # ROI-based pricing specifics
    roi_percentage: Optional[Decimal] = Field(None, ge=0, le=100)  # Pourcentage des économies
    roi_min_amount: Optional[Decimal] = Field(None, ge=0)  # Montant minimum
    roi_max_amount: Optional[Decimal] = Field(None, ge=0)  # Montant maximum

    # Usage-based pricing specifics
    unit_price: Optional[Decimal] = Field(None, ge=0)  # Prix unitaire
    included_units: Optional[int] = Field(None, ge=0)  # Unités incluses

    # Features
    features: List[PricingFeature] = []
    limits: PricingTierLimits = Field(default_factory=PricingTierLimits)

    # Metadata
    is_active: bool = True
    is_public: bool = True  # Visible sur la page pricing
    trial_days: int = Field(0, ge=0)  # Jours d'essai gratuit
    setup_fee: Optional[Decimal] = Field(None, ge=0)  # Frais d'installation

    # Calculated fields
    estimated_roi_percentage: Optional[Decimal] = Field(None, ge=0)
    average_savings: Optional[Decimal] = Field(None, ge=0)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @root_validator
    def validate_pricing_model(cls, values):
        """Validation selon le modèle de tarification"""
        pricing_model = values.get('pricing_model')

        if pricing_model == PricingModel.ROI_BASED:
            if not values.get('roi_percentage'):
                raise ValueError("roi_percentage requis pour ROI_BASED pricing")

        elif pricing_model == PricingModel.USAGE_BASED:
            if not values.get('unit_price'):
                raise ValueError("unit_price requis pour USAGE_BASED pricing")

        return values

    @validator('estimated_roi_percentage')
    def validate_estimated_roi(cls, v):
        if v is not None and v > 1000:  # Limiter à 1000% ROI
            raise ValueError("ROI estimé ne peut dépasser 1000%")
        return v

    def calculate_monthly_price(self, estimated_usage: Optional[int] = None) -> Decimal:
        """Calcule le prix mensuel estimé"""
        if self.pricing_model == PricingModel.USAGE_BASED and estimated_usage:
            included = self.included_units or 0
            extra_units = max(0, estimated_usage - included)
            return self.base_price + (extra_units * self.unit_price)

        # Pour les autres modèles, retourner le prix de base
        return self.base_price

    class Config:
        schema_extra = {
            "example": {
                "name": "Professional Plan",
                "description": "Accès complet à 500 agents avec support prioritaire",
                "tier": "professional",
                "pricing_model": "tiered",
                "base_price": 499.00,
                "currency": "USD",
                "billing_period": "monthly",
                "trial_days": 30,
                "features": [
                    {
                        "code": "cost_agents",
                        "name": "Agents d'optimisation coûts",
                        "limit": 200,
                        "unit": "agents"
                    }
                ],
                "estimated_roi_percentage": 327
            }
        }


class UsageTier(BaseModel):
    """Niveau de tarification pour la facturation à l'usage"""
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    from_quantity: int = Field(0, ge=0)
    to_quantity: Optional[int] = None  # None = infini
    unit_price: Decimal = Field(..., ge=0)
    flat_fee: Optional[Decimal] = Field(None, ge=0)

    @validator('to_quantity')
    def validate_to_quantity(cls, v, values):
        if v is not None and v <= values.get('from_quantity', 0):
            raise ValueError("to_quantity doit être > from_quantity")
        return v

    def applies_to(self, quantity: int) -> bool:
        """Vérifie si ce niveau s'applique à la quantité donnée"""
        return (quantity >= self.from_quantity and
                (self.to_quantity is None or quantity <= self.to_quantity))


class UsageRecord(BaseModel):
    """Enregistrement d'utilisation pour facturation à l'usage"""
    id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    subscription_id: UUID
    service_code: str = Field(..., min_length=1, max_length=50)
    service_name: str = Field(..., min_length=1, max_length=100)

    # Usage metrics
    quantity: Decimal = Field(..., gt=0)
    unit: str = Field(..., min_length=1, max_length=20)  # agents, GB, requests, etc.
    unit_price: Decimal = Field(..., ge=0)

    # Pricing
    amount: Decimal = Field(..., ge=0)  # = quantity * unit_price
    currency: Currency = Currency.USD

    # Time period
    period_start: datetime
    period_end: datetime
    recorded_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator('period_end')
    def validate_period(cls, v, values):
        if 'period_start' in values and v <= values['period_start']:
            raise ValueError("period_end doit être après period_start")
        return v

    @validator('amount')
    def calculate_amount(cls, v, values):
        """Calcule automatiquement le montant si non fourni"""
        if 'quantity' in values and 'unit_price' in values:
            return values['quantity'] * values['unit_price']
        return v

    class Config:
        schema_extra = {
            "example": {
                "customer_id": "123e4567-e89b-12d3-a456-426614174000",
                "subscription_id": "123e4567-e89b-12d3-a456-426614174001",
                "service_code": "cost_optimization",
                "service_name": "Optimisation des coûts AWS",
                "quantity": 1250.50,
                "unit": "USD_saved",
                "unit_price": 0.10,
                "period_start": "2024-01-01T00:00:00Z",
                "period_end": "2024-01-31T23:59:59Z"
            }
        }


class LineItem(BaseModel):
    """Article de facture"""
    id: UUID = Field(default_factory=uuid4)
    description: str = Field(..., max_length=500)
    quantity: Decimal = Field(1, gt=0)
    unit_price: Decimal = Field(..., ge=0)
    unit: Optional[str] = Field(None, max_length=20)

    # Pricing
    amount: Decimal = Field(..., ge=0)  # quantity * unit_price
    currency: Currency = Currency.USD

    # Discount
    discount_amount: Decimal = Field(0, ge=0)
    discount_percentage: Decimal = Field(0, ge=0, le=100)

    # Tax
    tax_rate: Optional[Decimal] = Field(None, ge=0, le=100)
    tax_amount: Decimal = Field(0, ge=0)

    # Calculated
    total_amount: Decimal = Field(..., ge=0)  # amount - discount + tax

    @root_validator
    def calculate_totals(cls, values):
        """Calcule les totaux automatiquement"""
        quantity = values.get('quantity', 1)
        unit_price = values.get('unit_price', 0)

        # Calcul du montant brut
        amount = quantity * unit_price
        values['amount'] = amount

        # Application de la réduction
        discount_amount = values.get('discount_amount', 0)
        discount_percentage = values.get('discount_percentage', 0)

        if discount_percentage > 0:
            discount_amount = max(discount_amount, amount * discount_percentage / 100)

        discounted_amount = amount - discount_amount

        # Application de la taxe
        tax_rate = values.get('tax_rate', 0)
        tax_amount = discounted_amount * tax_rate / 100 if tax_rate else 0

        values['tax_amount'] = tax_amount
        values['total_amount'] = discounted_amount + tax_amount

        return values

    class Config:
        schema_extra = {
            "example": {
                "description": "MicroAgents Professional Plan - Janvier 2024",
                "quantity": 1,
                "unit_price": 499.00,
                "unit": "month",
                "tax_rate": 20.0
            }
        }


class Invoice(BaseModel):
    """Modèle de facture"""
    id: UUID = Field(default_factory=uuid4)
    invoice_number: str = Field(..., min_length=1, max_length=50)
    customer_id: UUID
    subscription_id: Optional[UUID] = None

    # Billing period
    billing_period: BillingPeriod
    period_start: datetime
    period_end: datetime
    due_date: datetime

    # Line items
    line_items: List[LineItem] = Field(default_factory=list)

    # Totals
    subtotal: Decimal = Field(0, ge=0)
    total_discount: Decimal = Field(0, ge=0)
    total_tax: Decimal = Field(0, ge=0)
    total_amount: Decimal = Field(0, ge=0)
    amount_paid: Decimal = Field(0, ge=0)
    amount_due: Decimal = Field(0, ge=0)  # total_amount - amount_paid

    currency: Currency = Currency.USD

    # Status
    status: InvoiceStatus = InvoiceStatus.DRAFT
    paid_at: Optional[datetime] = None
    voided_at: Optional[datetime] = None

    # Metadata
    notes: Optional[str] = Field(None, max_length=1000)
    terms: Optional[str] = Field(None, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @root_validator
    def calculate_totals(cls, values):
        """Calcule les totaux automatiquement"""
        line_items = values.get('line_items', [])

        if line_items:
            subtotal = sum(item.amount for item in line_items)
            total_discount = sum(item.discount_amount for item in line_items)
            total_tax = sum(item.tax_amount for item in line_items)
            total_amount = sum(item.total_amount for item in line_items)

            values['subtotal'] = subtotal
            values['total_discount'] = total_discount
            values['total_tax'] = total_tax
            values['total_amount'] = total_amount

            # Calcul du montant dû
            amount_paid = values.get('amount_paid', 0)
            values['amount_due'] = max(0, total_amount - amount_paid)

        return values

    @validator('due_date')
    def validate_due_date(cls, v, values):
        if 'period_end' in values and v < values['period_end']:
            raise ValueError("due_date doit être après period_end")
        return v

    def mark_as_paid(self, amount: Decimal, payment_date: datetime = None):
        """Marque la facture comme payée"""
        if amount <= 0:
            raise ValueError("Le montant payé doit être positif")

        self.amount_paid += amount
        self.amount_due = max(0, self.total_amount - self.amount_paid)

        if self.amount_due == 0:
            self.status = InvoiceStatus.PAID
            self.paid_at = payment_date or datetime.utcnow()
        elif self.amount_paid > 0:
            self.status = InvoiceStatus.PARTIALLY_PAID

        self.updated_at = datetime.utcnow()

    class Config:
        schema_extra = {
            "example": {
                "invoice_number": "INV-2024-001",
                "customer_id": "123e4567-e89b-12d3-a456-426614174000",
                "billing_period": "monthly",
                "period_start": "2024-01-01T00:00:00Z",
                "period_end": "2024-01-31T23:59:59Z",
                "due_date": "2024-02-15T23:59:59Z",
                "status": "open"
            }
        }


class Subscription(BaseModel):
    """Modèle d'abonnement"""
    id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    plan_id: UUID

    # Plan details
    plan_name: str
    pricing_model: PricingModel
    billing_period: BillingPeriod
    base_price: Decimal
    currency: Currency

    # Period
    start_date: datetime
    current_period_start: datetime
    current_period_end: datetime
    trial_end: Optional[datetime] = None

    # Status
    status: SubscriptionStatus = SubscriptionStatus.PENDING
    cancel_at_period_end: bool = False
    canceled_at: Optional[datetime] = None

    # Usage tracking
    current_usage: Decimal = Field(0, ge=0)
    usage_limit: Optional[Decimal] = None

    # Custom pricing
    custom_price: Optional[Decimal] = Field(None, ge=0)
    custom_terms: Optional[str] = Field(None, max_length=1000)

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @validator('current_period_end')
    def validate_period(cls, v, values):
        if 'current_period_start' in values and v <= values['current_period_start']:
            raise ValueError("current_period_end doit être après current_period_start")
        return v

    @property
    def is_trial(self) -> bool:
        """Vérifie si l'abonnement est en période d'essai"""
        if not self.trial_end:
            return False
        return datetime.utcnow() <= self.trial_end

    @property
    def is_active(self) -> bool:
        """Vérifie si l'abonnement est actif"""
        return self.status == SubscriptionStatus.ACTIVE

    def calculate_prorated_amount(self, new_plan_price: Decimal, change_date: datetime) -> Decimal:
        """Calcule le montant proratisé lors d'un changement de plan"""
        if change_date < self.current_period_start or change_date > self.current_period_end:
            raise ValueError("Date de changement hors de la période courante")

        period_days = (self.current_period_end - self.current_period_start).days
        days_used = (change_date - self.current_period_start).days
        days_remaining = period_days - days_used

        # Calcul du crédit pour les jours non utilisés sur l'ancien plan
        daily_rate_old = self.base_price / period_days
        credit = daily_rate_old * days_remaining

        # Calcul du coût pour les jours restants sur le nouveau plan
        daily_rate_new = new_plan_price / period_days
        charge = daily_rate_new * days_remaining

        return max(0, charge - credit)

    class Config:
        schema_extra = {
            "example": {
                "customer_id": "123e4567-e89b-12d3-a456-426614174000",
                "plan_id": "123e4567-e89b-12d3-a456-426614174002",
                "plan_name": "Professional Plan",
                "pricing_model": "tiered",
                "billing_period": "monthly",
                "base_price": 499.00,
                "currency": "USD",
                "start_date": "2024-01-01T00:00:00Z",
                "current_period_start": "2024-01-01T00:00:00Z",
                "current_period_end": "2024-01-31T23:59:59Z",
                "status": "active"
            }
        }


class Discount(BaseModel):
    """Modèle de réduction"""
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(..., min_length=3, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)

    # Discount type
    discount_type: DiscountType
    value: Decimal = Field(..., gt=0)  # Pourcentage ou montant fixe

    # Limits
    currency: Optional[Currency] = None  # Requis pour FIXED_AMOUNT
    maximum_amount: Optional[Decimal] = Field(None, ge=0)  # Montant maximum de réduction
    minimum_purchase: Optional[Decimal] = Field(None, ge=0)  # Achat minimum requis

    # Validity
    valid_from: datetime = Field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None
    is_active: bool = True

    # Usage limits
    max_redemptions: Optional[int] = Field(None, gt=0)
    current_redemptions: int = Field(0, ge=0)
    is_reusable: bool = False  # Peut être réutilisé par le même client

    # Applicability
    applies_to_plans: List[str] = Field(default_factory=list)  # Codes de plan
    applies_to_services: List[str] = Field(default_factory=list)  # Codes de service

    @validator('value')
    def validate_value(cls, v, values):
        discount_type = values.get('discount_type')

        if discount_type == DiscountType.PERCENTAGE and v > 100:
            raise ValueError("Le pourcentage ne peut dépasser 100%")

        return v

    @validator('currency')
    def validate_currency(cls, v, values):
        if values.get('discount_type') == DiscountType.FIXED_AMOUNT and not v:
            raise ValueError("Currency requis pour les réductions à montant fixe")
        return v

    def is_valid(self, purchase_amount: Decimal = None) -> bool:
        """Vérifie si la réduction est valide"""
        now = datetime.utcnow()

        if not self.is_active:
            return False

        if now < self.valid_from:
            return False

        if self.valid_until and now > self.valid_until:
            return False

        if self.max_redemptions and self.current_redemptions >= self.max_redemptions:
            return False

        if purchase_amount is not None and self.minimum_purchase:
            if purchase_amount < self.minimum_purchase:
                return False

        return True

    def calculate_discount_amount(self, amount: Decimal) -> Decimal:
        """Calcule le montant de la réduction"""
        if not self.is_valid(amount):
            return Decimal('0')

        if self.discount_type == DiscountType.PERCENTAGE:
            discount = amount * self.value / 100
        elif self.discount_type == DiscountType.FIXED_AMOUNT:
            discount = self.value
        else:
            discount = Decimal('0')

        # Appliquer le montant maximum
        if self.maximum_amount and discount > self.maximum_amount:
            discount = self.maximum_amount

        return min(discount, amount)  # Ne pas dépasser le montant total

    class Config:
        schema_extra = {
            "example": {
                "code": "LAUNCH2024",
                "name": "Remise de lancement 2024",
                "description": "20% de réduction pour les nouveaux clients",
                "discount_type": "percentage",
                "value": 20.0,
                "valid_until": "2024-12-31T23:59:59Z",
                "max_redemptions": 1000
            }
        }


class TaxRate(BaseModel):
    """Taux de taxe"""
    id: UUID = Field(default_factory=uuid4)
    country: str = Field(..., min_length=2, max_length=2)  # Code ISO
    region: Optional[str] = Field(None, min_length=2, max_length=3)  # État/province
    jurisdiction: str = Field(..., min_length=1, max_length=100)  # Juridiction complète
    tax_type: TaxType
    percentage: Decimal = Field(..., ge=0, le=100)
    is_inclusive: bool = False  # Taxe incluse dans le prix ou ajoutée

    # Effective dates
    effective_from: datetime = Field(default_factory=datetime.utcnow)
    effective_to: Optional[datetime] = None

    # Metadata
    description: Optional[str] = Field(None, max_length=500)
    tax_code: Optional[str] = Field(None, max_length=50)  # Code fiscal local
    is_active: bool = True

    @validator('country')
    def validate_country_code(cls, v):
        if not v.isalpha() or not v.isupper():
            raise ValueError("Le code pays doit être en majuscules (ex: FR, US)")
        return v

    @property
    def is_current(self) -> bool:
        """Vérifie si le taux de taxe est actuellement applicable"""
        now = datetime.utcnow()

        if not self.is_active:
            return False

        if now < self.effective_from:
            return False

        if self.effective_to and now > self.effective_to:
            return False

        return True

    def calculate_tax(self, amount: Decimal) -> Decimal:
        """Calcule le montant de la taxe"""
        if not self.is_current:
            return Decimal('0')

        return amount * self.percentage / 100

    class Config:
        schema_extra = {
            "example": {
                "country": "FR",
                "region": None,
                "jurisdiction": "France",
                "tax_type": "vat",
                "percentage": 20.0,
                "is_inclusive": True,
                "description": "TVA standard française"
            }
        }


class CurrencyConversion(BaseModel):
    """Conversion de devises"""
    id: UUID = Field(default_factory=uuid4)
    from_currency: Currency
    to_currency: Currency
    rate: Decimal = Field(..., gt=0)
    valid_from: datetime = Field(default_factory=datetime.utcnow)
    valid_to: datetime = Field(default_factory=lambda: datetime.utcnow() + timedelta(days=1))
    source: str = Field(..., max_length=100)  # API source

    @validator('valid_to')
    def validate_valid_to(cls, v, values):
        if 'valid_from' in values and v <= values['valid_from']:
            raise ValueError("valid_to doit être après valid_from")
        return v

    def convert(self, amount: Decimal) -> Decimal:
        """Convertit un montant"""
        if datetime.utcnow() > self.valid_to:
            raise ValueError("Le taux de conversion a expiré")

        return amount * self.rate

    class Config:
        schema_extra = {
            "example": {
                "from_currency": "EUR",
                "to_currency": "USD",
                "rate": 1.08,
                "source": "ECB"
            }
        }


# ==================== MODÈLES DE CALCUL ====================

class PricingCalculationRequest(BaseModel):
    """Requête de calcul de prix"""
    plan_id: UUID
    billing_period: BillingPeriod
    currency: Currency = Currency.USD

    # Usage-based parameters
    estimated_usage: Optional[Decimal] = None
    usage_metric: Optional[str] = None

    # ROI-based parameters
    estimated_savings: Optional[Decimal] = None  # Économies estimées

    # Discount
    discount_code: Optional[str] = None

    # Tax
    country: Optional[str] = None
    region: Optional[str] = None


class PricingCalculationResponse(BaseModel):
    """Réponse de calcul de prix"""
    plan_name: str
    billing_period: BillingPeriod

    # Pricing breakdown
    base_price: Decimal
    usage_charge: Decimal = Field(0, ge=0)
    setup_fee: Decimal = Field(0, ge=0)

    # Adjustments
    discount_amount: Decimal = Field(0, ge=0)
    discount_percentage: Decimal = Field(0, ge=0, le=100)

    # Taxes
    tax_amount: Decimal = Field(0, ge=0)
    tax_rate: Optional[Decimal] = Field(None, ge=0, le=100)

    # Totals
    subtotal: Decimal = Field(..., ge=0)
    total: Decimal = Field(..., ge=0)

    currency: Currency

    # Estimated ROI
    estimated_roi_percentage: Optional[Decimal] = None
    estimated_monthly_savings: Optional[Decimal] = None

    # Period
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None

    class Config:
        schema_extra = {
            "example": {
                "plan_name": "Professional Plan",
                "billing_period": "monthly",
                "base_price": 499.00,
                "subtotal": 499.00,
                "total": 598.80,  # Avec TVA 20%
                "currency": "USD",
                "estimated_roi_percentage": 327,
                "estimated_monthly_savings": 1630.00
            }
        }


class ROIPricingAgreement(BaseModel):
    """Accord de tarification basé sur le ROI"""
    id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    plan_id: UUID

    # ROI terms
    roi_percentage: Decimal = Field(..., gt=0, le=100)
    minimum_savings: Decimal = Field(..., gt=0)
    maximum_fee: Optional[Decimal] = Field(None, gt=0)

    # Calculation period
    calculation_period_days: int = Field(30, gt=0)  # Période de calcul des économies

    # Verification
    requires_verification: bool = True
    verification_method: str = "third_party"  # third_party, audit, automated

    # Payment terms
    payment_terms_days: int = Field(30, gt=0)
    advance_percentage: Optional[Decimal] = Field(None, gt=0, le=100)

    # Duration
    start_date: datetime
    end_date: Optional[datetime] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @validator('end_date')
    def validate_end_date(cls, v, values):
        if v and 'start_date' in values and v <= values['start_date']:
            raise ValueError("end_date doit être après start_date")
        return v

    def calculate_fee(self, actual_savings: Decimal) -> Decimal:
        """Calcule les frais basés sur les économies réelles"""
        if actual_savings < self.minimum_savings:
            return Decimal('0')

        fee = actual_savings * self.roi_percentage / 100

        if self.maximum_fee and fee > self.maximum_fee:
            return self.maximum_fee

        return fee


# ==================== MODÈLES POUR LA GÉNÉRATION DE FACTURES ====================

class InvoiceGenerationRequest(BaseModel):
    """Requête de génération de facture"""
    subscription_id: UUID
    period_start: datetime
    period_end: datetime

    # Overrides
    line_items_override: Optional[List[LineItem]] = None
    discount_code: Optional[str] = None
    notes: Optional[str] = None


class InvoiceTemplate(BaseModel):
    """Template de facture"""
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., max_length=100)
    description: Optional[str] = Field(None, max_length=500)

    # Content
    header_html: Optional[str] = None
    footer_html: Optional[str] = None
    terms_html: Optional[str] = None
    css_styles: Optional[str] = None

    # Company info
    company_name: str
    company_address: str
    company_logo_url: Optional[str] = None
    company_tax_id: Optional[str] = None

    # Defaults
    default_currency: Currency = Currency.USD
    default_payment_terms_days: int = 30
    default_notes: Optional[str] = None

    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ==================== MODÈLES DE RAPPORT ====================

class RevenueReport(BaseModel):
    """Rapport de revenus"""
    period_start: datetime
    period_end: datetime
    total_revenue: Decimal
    recurring_revenue: Decimal
    usage_revenue: Decimal
    one_time_revenue: Decimal
    currency: Currency

    # Breakdown
    revenue_by_plan: Dict[str, Decimal]
    revenue_by_currency: Dict[Currency, Decimal]
    revenue_by_region: Dict[str, Decimal]

    # Metrics
    new_mrr: Decimal  # Monthly Recurring Revenue nouveau
    churned_mrr: Decimal  # MRR perdu
    net_mrr: Decimal  # New MRR - Churned MRR
    customer_count: int
    average_revenue_per_user: Decimal


# ==================== FACTORIES ====================

class PricingModelFactory:
    """Factory pour créer des modèles de tarification"""

    @staticmethod
    def create_freemium_plan() -> PricingPlan:
        """Crée un plan Freemium"""
        return PricingPlan(
            name="Freemium",
            description="Accès gratuit aux fonctionnalités de base",
            tier=PlanTier.FREEMIUM,
            pricing_model=PricingModel.TIERED,
            base_price=Decimal('0'),
            billing_period=BillingPeriod.MONTHLY,
            features=[
                PricingFeature(
                    code="basic_monitoring",
                    name="Monitoring de base",
                    description="Monitoring de 3 services cloud",
                    limit=3,
                    unit="services"
                )
            ],
            limits=PricingTierLimits(
                max_agents=50,
                max_users=1,
                support_level="community"
            ),
            trial_days=0,
            is_public=True
        )

    @staticmethod
    def create_professional_plan() -> PricingPlan:
        """Crée un plan Professional"""
        return PricingPlan(
            name="Professional",
            description="Accès complet pour les équipes",
            tier=PlanTier.PROFESSIONAL,
            pricing_model=PricingModel.TIERED,
            base_price=Decimal('499'),
            billing_period=BillingPeriod.MONTHLY,
            features=[
                PricingFeature(
                    code="cost_agents",
                    name="Agents d'optimisation des coûts",
                    description="200 agents d'optimisation",
                    limit=200,
                    unit="agents"
                ),
                PricingFeature(
                    code="priority_support",
                    name="Support prioritaire",
                    description="Support par email et chat",
                    limit=None,
                    unit=None
                )
            ],
            limits=PricingTierLimits(
                max_agents=500,
                max_users=10,
                support_level="priority",
                sla_percentage=Decimal('99.9')
            ),
            trial_days=30,
            estimated_roi_percentage=Decimal('327'),
            average_savings=Decimal('1630')
        )

    @staticmethod
    def create_enterprise_plan() -> PricingPlan:
        """Crée un plan Enterprise"""
        return PricingPlan(
            name="Enterprise",
            description="Solution complète pour les grandes organisations",
            tier=PlanTier.ENTERPRISE,
            pricing_model=PricingModel.CUSTOM,
            base_price=Decimal('0'),  # À déterminer selon les besoins
            billing_period=BillingPeriod.CUSTOM,
            features=[
                PricingFeature(
                    code="all_agents",
                    name="Accès à tous les agents",
                    description="1400 agents complets",
                    limit=None,
                    unit="agents"
                ),
                PricingFeature(
                    code="dedicated_support",
                    name="Support dédié 24/7",
                    description="Ingénier dédié et support SLA",
                    limit=None,
                    unit=None
                )
            ],
            limits=PricingTierLimits(
                max_agents=None,  # Illimité
                max_users=None,  # Illimité
                support_level="dedicated",
                sla_percentage=Decimal('99.99'),
                custom_domains=True
            ),
            trial_days=0,
            is_public=False,  # Négociation requise
            roi_percentage=Decimal('15'),  # % des économies
            roi_min_amount=Decimal('10000')
        )


# ==================== EXPORTS ====================

__all__ = [
    # Enums
    'BillingPeriod',
    'PricingModel',
    'PlanTier',
    'SubscriptionStatus',
    'InvoiceStatus',
    'DiscountType',
    'TaxType',
    'Currency',

    # Models
    'PricingFeature',
    'PricingTierLimits',
    'PricingPlan',
    'UsageTier',
    'UsageRecord',
    'LineItem',
    'Invoice',
    'Subscription',
    'Discount',
    'TaxRate',
    'CurrencyConversion',

    # Calculation Models
    'PricingCalculationRequest',
    'PricingCalculationResponse',
    'ROIPricingAgreement',

    # Generation Models
    'InvoiceGenerationRequest',
    'InvoiceTemplate',

    # Report Models
    'RevenueReport',

    # Factories
    'PricingModelFactory'
]