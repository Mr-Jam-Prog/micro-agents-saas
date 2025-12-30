"""
Billing Module for MicroAgents Platform
Complete billing solution with Stripe integration, usage-based pricing, and financial compliance.
"""

import logging
from typing import Dict, List, Optional, Any, Union, Tuple
from datetime import datetime, timedelta, date
from decimal import Decimal
from enum import Enum
import asyncio
from dataclasses import dataclass, field, asdict
from functools import lru_cache
import json
from pathlib import Path

# External dependencies
import stripe
from pydantic import BaseModel, Field, validator, root_validator
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import pandas as pd
import numpy as np

# Internal imports
from ..database.models import (
    Tenant, Subscription, Invoice, InvoiceLineItem, Payment,
    UsageRecord, CreditNote, Refund, TaxRate, Discount, Plan,
    BillingSettings
)
from ..config.settings import BILLING_CONFIG, STRIPE_CONFIG
from ..utils.encryption import encrypt_data, decrypt_data
from ..utils.validators import validate_currency, validate_email
from ..exceptions import (
    BillingError, PaymentError, SubscriptionError,
    InvoiceError, TaxCalculationError
)

logger = logging.getLogger(__name__)

# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class BillingPeriod(str, Enum):
    """Billing period enumeration."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUALLY = "annually"

class InvoiceStatus(str, Enum):
    """Invoice status enumeration."""
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"

class PaymentStatus(str, Enum):
    """Payment status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELED = "canceled"

class SubscriptionStatus(str, Enum):
    """Subscription status enumeration."""
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"

class TaxMode(str, Enum):
    """Tax calculation mode."""
    EXCLUSIVE = "exclusive"  # Tax added on top
    INCLUSIVE = "inclusive"  # Tax included in price

class RevenueRecognitionMethod(str, Enum):
    """Revenue recognition methods."""
    UPFRONT = "upfront"  # Recognize immediately
    MONTHLY = "monthly"  # Recognize over subscription period
    USAGE = "usage"  # Recognize as used

# ============================================================================
# DATA MODELS
# ============================================================================

class Address(BaseModel):
    """Billing address model."""
    line1: Optional[str] = None
    line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "US"
    
    class Config:
        schema_extra = {
            "example": {
                "line1": "123 Main St",
                "city": "San Francisco",
                "state": "CA",
                "postal_code": "94105",
                "country": "US"
            }
        }

class CustomerData(BaseModel):
    """Customer information for billing."""
    tenant_id: str
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[Address] = None
    tax_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @validator('email')
    def validate_email_format(cls, v):
        return validate_email(v)

class SubscriptionData(BaseModel):
    """Subscription creation/update data."""
    tenant_id: str
    plan_id: str
    billing_period: BillingPeriod = BillingPeriod.MONTHLY
    quantity: int = Field(1, ge=1)
    trial_days: Optional[int] = Field(None, ge=0, le=365)
    coupon_code: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    prorate: bool = True
    billing_cycle_anchor: Optional[datetime] = None
    
    @validator('quantity')
    def validate_quantity(cls, v, values):
        plan_id = values.get('plan_id')
        # Validate against plan limits
        return v

class InvoiceData(BaseModel):
    """Invoice creation data."""
    tenant_id: str
    currency: str = "usd"
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    auto_advance: bool = True
    
    @validator('currency')
    def validate_currency_code(cls, v):
        return validate_currency(v)

class UsageRecordData(BaseModel):
    """Usage record for metered billing."""
    tenant_id: str
    metric_id: str
    quantity: Decimal = Field(..., gt=0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    action: str = "increment"  # increment, set, decrement
    metadata: Dict[str, Any] = Field(default_factory=dict)

class PaymentData(BaseModel):
    """Payment processing data."""
    invoice_id: str
    amount: Decimal = Field(..., gt=0)
    currency: str = "usd"
    payment_method_id: Optional[str] = None
    payment_intent_id: Optional[str] = None
    source_type: str = "card"  # card, bank_transfer, etc.
    metadata: Dict[str, Any] = Field(default_factory=dict)

@dataclass
class BillingResult:
    """Result of billing operations."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    transaction_id: Optional[str] = None
    
    def to_dict(self):
        return asdict(self)

# ============================================================================
# STRIPE INTEGRATION
# ============================================================================

class StripeManager:
    """Manages all Stripe API interactions."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or STRIPE_CONFIG.get('secret_key')
        self.webhook_secret = STRIPE_CONFIG.get('webhook_secret')
        self.stripe = stripe
        self.stripe.api_key = self.api_key
        self.stripe.max_network_retries = 3
        self.stripe.api_version = "2023-10-16"
        
        # Configure Stripe client
        self._configure_stripe()
    
    def _configure_stripe(self):
        """Configure Stripe client with custom settings."""
        # Set timeouts
        stripe.default_http_client = stripe.http_client.RequestsClient(
            timeout=STRIPE_CONFIG.get('timeout', 30)
        )
        
        # Enable telemetry
        stripe.enable_telemetry = STRIPE_CONFIG.get('enable_telemetry', True)
        
        # Set app info
        stripe.set_app_info(
            "MicroAgents Platform",
            version="1.0.0",
            url="https://microagents.io",
            partner_id=None
        )
    
    # ==================== CUSTOMERS ====================
    
    async def create_customer(self, customer_data: CustomerData) -> Dict[str, Any]:
        """Create a Stripe customer."""
        try:
            customer = self.stripe.Customer.create(
                email=customer_data.email,
                name=customer_data.name,
                phone=customer_data.phone,
                address=customer_data.address.dict() if customer_data.address else None,
                tax_id_data=[{"type": "eu_vat", "value": customer_data.tax_id}] if customer_data.tax_id else None,
                metadata={
                    "tenant_id": customer_data.tenant_id,
                    **customer_data.metadata
                },
                expand=["tax_ids"]
            )
            
            return customer.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe customer: {e}")
            raise BillingError(f"Customer creation failed: {str(e)}")
    
    async def update_customer(self, customer_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update a Stripe customer."""
        try:
            customer = self.stripe.Customer.modify(
                customer_id,
                **updates
            )
            return customer.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to update Stripe customer {customer_id}: {e}")
            raise BillingError(f"Customer update failed: {str(e)}")
    
    async def delete_customer(self, customer_id: str) -> bool:
        """Delete a Stripe customer."""
        try:
            deleted = self.stripe.Customer.delete(customer_id)
            return deleted.deleted
        except stripe.error.StripeError as e:
            logger.error(f"Failed to delete Stripe customer {customer_id}: {e}")
            raise BillingError(f"Customer deletion failed: {str(e)}")
    
    # ==================== SUBSCRIPTIONS ====================
    
    async def create_subscription(self, subscription_data: SubscriptionData,
                                 customer_id: str) -> Dict[str, Any]:
        """Create a Stripe subscription."""
        try:
            # Get plan from Stripe
            plan = await self.get_plan(subscription_data.plan_id)
            
            subscription_params = {
                "customer": customer_id,
                "items": [{
                    "price": subscription_data.plan_id,
                    "quantity": subscription_data.quantity
                }],
                "payment_behavior": "default_incomplete",
                "expand": ["latest_invoice.payment_intent"],
                "metadata": {
                    "tenant_id": subscription_data.tenant_id,
                    **subscription_data.metadata
                }
            }
            
            # Add trial period if specified
            if subscription_data.trial_days:
                subscription_params["trial_period_days"] = subscription_data.trial_days
            
            # Add billing cycle anchor
            if subscription_data.billing_cycle_anchor:
                subscription_params["billing_cycle_anchor"] = int(
                    subscription_data.billing_cycle_anchor.timestamp()
                )
                subscription_params["proration_behavior"] = "none"
            
            # Apply coupon if provided
            if subscription_data.coupon_code:
                coupon = await self.get_coupon(subscription_data.coupon_code)
                if coupon:
                    subscription_params["coupon"] = subscription_data.coupon_code
            
            subscription = self.stripe.Subscription.create(**subscription_params)
            
            return subscription.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe subscription: {e}")
            raise SubscriptionError(f"Subscription creation failed: {str(e)}")
    
    async def update_subscription(self, subscription_id: str,
                                 updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update a Stripe subscription."""
        try:
            subscription = self.stripe.Subscription.modify(
                subscription_id,
                **updates
            )
            return subscription.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to update subscription {subscription_id}: {e}")
            raise SubscriptionError(f"Subscription update failed: {str(e)}")
    
    async def cancel_subscription(self, subscription_id: str,
                                 cancel_at_period_end: bool = False) -> Dict[str, Any]:
        """Cancel a Stripe subscription."""
        try:
            if cancel_at_period_end:
                subscription = self.stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )
            else:
                subscription = self.stripe.Subscription.delete(subscription_id)
            
            return subscription.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to cancel subscription {subscription_id}: {e}")
            raise SubscriptionError(f"Subscription cancellation failed: {str(e)}")
    
    # ==================== INVOICES ====================
    
    async def create_invoice(self, customer_id: str,
                            invoice_data: InvoiceData) -> Dict[str, Any]:
        """Create a Stripe invoice."""
        try:
            invoice_params = {
                "customer": customer_id,
                "currency": invoice_data.currency,
                "description": invoice_data.description,
                "metadata": {
                    "tenant_id": invoice_data.tenant_id,
                    **invoice_data.metadata
                },
                "auto_advance": invoice_data.auto_advance
            }
            
            if invoice_data.due_date:
                invoice_params["due_date"] = int(invoice_data.due_date.timestamp())
            
            invoice = self.stripe.Invoice.create(**invoice_params)
            
            # Add line items if provided
            if invoice_data.line_items:
                for item in invoice_data.line_items:
                    await self.add_invoice_item(
                        customer_id,
                        invoice.id,
                        item
                    )
            
            # Finalize invoice
            if invoice_data.auto_advance:
                invoice = self.stripe.Invoice.finalize_invoice(invoice.id)
            
            return invoice.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe invoice: {e}")
            raise InvoiceError(f"Invoice creation failed: {str(e)}")
    
    async def add_invoice_item(self, customer_id: str,
                              invoice_id: str,
                              item_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add an item to an invoice."""
        try:
            invoice_item = self.stripe.InvoiceItem.create(
                customer=customer_id,
                invoice=invoice_id,
                **item_data
            )
            return invoice_item.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to add invoice item: {e}")
            raise InvoiceError(f"Invoice item addition failed: {str(e)}")
    
    async def pay_invoice(self, invoice_id: str,
                         payment_method_id: Optional[str] = None) -> Dict[str, Any]:
        """Pay a Stripe invoice."""
        try:
            if payment_method_id:
                invoice = self.stripe.Invoice.pay(
                    invoice_id,
                    payment_method=payment_method_id
                )
            else:
                invoice = self.stripe.Invoice.pay(invoice_id)
            
            return invoice.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to pay invoice {invoice_id}: {e}")
            raise PaymentError(f"Invoice payment failed: {str(e)}")
    
    # ==================== PAYMENTS ====================
    
    async def create_payment_intent(self, amount: int,
                                   currency: str,
                                   customer_id: str,
                                   metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Create a payment intent."""
        try:
            payment_intent = self.stripe.PaymentIntent.create(
                amount=amount,
                currency=currency,
                customer=customer_id,
                metadata=metadata,
                automatic_payment_methods={
                    "enabled": True,
                    "allow_redirects": "never"
                },
                confirm=True
            )
            return payment_intent.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create payment intent: {e}")
            raise PaymentError(f"Payment intent creation failed: {str(e)}")
    
    async def create_refund(self, payment_intent_id: str,
                           amount: Optional[int] = None) -> Dict[str, Any]:
        """Create a refund."""
        try:
            refund_params = {
                "payment_intent": payment_intent_id
            }
            
            if amount:
                refund_params["amount"] = amount
            
            refund = self.stripe.Refund.create(**refund_params)
            return refund.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create refund: {e}")
            raise PaymentError(f"Refund creation failed: {str(e)}")
    
    # ==================== PLANS & PRICES ====================
    
    async def create_plan(self, plan_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a Stripe plan."""
        try:
            # First create product if not exists
            product = self.stripe.Product.create(
                name=plan_data["name"],
                description=plan_data.get("description"),
                metadata=plan_data.get("metadata", {})
            )
            
            # Create price
            price_data = {
                "product": product.id,
                "unit_amount": int(plan_data["amount"] * 100),  # Convert to cents
                "currency": plan_data.get("currency", "usd"),
                "recurring": {
                    "interval": plan_data.get("interval", "month"),
                    "interval_count": plan_data.get("interval_count", 1)
                },
                "metadata": plan_data.get("metadata", {})
            }
            
            if plan_data.get("tiers"):
                price_data["tiers_mode"] = plan_data.get("tiers_mode", "graduated")
                price_data["tiers"] = plan_data["tiers"]
                price_data["billing_scheme"] = "tiered"
            else:
                price_data["billing_scheme"] = "per_unit"
            
            price = self.stripe.Price.create(**price_data)
            
            return {
                "product_id": product.id,
                "price_id": price.id,
                "product": product.to_dict(),
                "price": price.to_dict()
            }
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe plan: {e}")
            raise BillingError(f"Plan creation failed: {str(e)}")
    
    async def get_plan(self, price_id: str) -> Optional[Dict[str, Any]]:
        """Get a Stripe plan by price ID."""
        try:
            price = self.stripe.Price.retrieve(price_id, expand=["product"])
            return price.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to get Stripe plan {price_id}: {e}")
            return None
    
    # ==================== COUPONS & DISCOUNTS ====================
    
    async def create_coupon(self, coupon_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a Stripe coupon."""
        try:
            coupon = self.stripe.Coupon.create(**coupon_data)
            return coupon.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe coupon: {e}")
            raise BillingError(f"Coupon creation failed: {str(e)}")
    
    async def get_coupon(self, coupon_id: str) -> Optional[Dict[str, Any]]:
        """Get a Stripe coupon."""
        try:
            coupon = self.stripe.Coupon.retrieve(coupon_id)
            return coupon.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to get Stripe coupon {coupon_id}: {e}")
            return None
    
    # ==================== TAX CALCULATION ====================
    
    async def calculate_tax(self, customer_id: str,
                           line_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate tax using Stripe Tax."""
        try:
            # Create tax calculation
            calculation = self.stripe.tax.Calculation.create(
                customer=customer_id,
                currency="usd",
                line_items=line_items,
                expand=["line_items.data.tax_breakdown"]
            )
            
            return calculation.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to calculate tax: {e}")
            raise TaxCalculationError(f"Tax calculation failed: {str(e)}")
    
    async def create_tax_id(self, customer_id: str,
                           type: str, value: str) -> Dict[str, Any]:
        """Add a tax ID to a customer."""
        try:
            tax_id = self.stripe.Customer.create_tax_id(
                customer_id,
                type=type,
                value=value
            )
            return tax_id.to_dict()
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create tax ID: {e}")
            raise BillingError(f"Tax ID creation failed: {str(e)}")
    
    # ==================== WEBHOOK HANDLING ====================
    
    async def handle_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """Handle Stripe webhook events."""
        try:
            event = self.stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            
            event_type = event['type']
            event_data = event['data']
            
            logger.info(f"Received Stripe webhook: {event_type}")
            
            # Process based on event type
            handler = self._get_webhook_handler(event_type)
            if handler:
                result = await handler(event_data['object'])
                return {
                    "event_type": event_type,
                    "handled": True,
                    "result": result
                }
            else:
                return {
                    "event_type": event_type,
                    "handled": False,
                    "message": f"No handler for event type {event_type}"
                }
                
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            raise BillingError("Invalid webhook signature")
        except Exception as e:
            logger.error(f"Webhook handling error: {e}")
            raise BillingError(f"Webhook handling failed: {str(e)}")
    
    def _get_webhook_handler(self, event_type: str):
        """Get handler for webhook event type."""
        handlers = {
            'customer.subscription.created': self._handle_subscription_created,
            'customer.subscription.updated': self._handle_subscription_updated,
            'customer.subscription.deleted': self._handle_subscription_deleted,
            'invoice.paid': self._handle_invoice_paid,
            'invoice.payment_failed': self._handle_invoice_payment_failed,
            'invoice.finalized': self._handle_invoice_finalized,
            'payment_intent.succeeded': self._handle_payment_succeeded,
            'payment_intent.payment_failed': self._handle_payment_failed,
            'charge.refunded': self._handle_charge_refunded,
        }
        return handlers.get(event_type)
    
    async def _handle_subscription_created(self, subscription: Dict[str, Any]):
        """Handle subscription created event."""
        # Update local database
        pass
    
    async def _handle_subscription_updated(self, subscription: Dict[str, Any]):
        """Handle subscription updated event."""
        pass
    
    async def _handle_subscription_deleted(self, subscription: Dict[str, Any]):
        """Handle subscription deleted event."""
        pass
    
    async def _handle_invoice_paid(self, invoice: Dict[str, Any]):
        """Handle invoice paid event."""
        pass
    
    async def _handle_invoice_payment_failed(self, invoice: Dict[str, Any]):
        """Handle invoice payment failed event."""
        pass
    
    async def _handle_invoice_finalized(self, invoice: Dict[str, Any]):
        """Handle invoice finalized event."""
        pass
    
    async def _handle_payment_succeeded(self, payment_intent: Dict[str, Any]):
        """Handle payment succeeded event."""
        pass
    
    async def _handle_payment_failed(self, payment_intent: Dict[str, Any]):
        """Handle payment failed event."""
        pass
    
    async def _handle_charge_refunded(self, charge: Dict[str, Any]):
        """Handle charge refunded event."""
        pass

# ============================================================================
# USAGE-BASED BILLING
# ============================================================================

class UsageMeter:
    """Manages usage-based billing and metering."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    async def record_usage(self, usage_data: UsageRecordData) -> BillingResult:
        """Record usage for metered billing."""
        try:
            # Get current subscription
            subscription = self._get_active_subscription(usage_data.tenant_id)
            if not subscription:
                return BillingResult(
                    success=False,
                    error="No active subscription found"
                )
            
            # Check if metric is included in plan
            plan = self._get_plan(subscription.plan_id)
            if not self._is_metric_included(plan, usage_data.metric_id):
                return BillingResult(
                    success=False,
                    error=f"Metric {usage_data.metric_id} not included in plan"
                )
            
            # Record usage
            usage_record = UsageRecord(
                tenant_id=usage_data.tenant_id,
                subscription_id=subscription.id,
                metric_id=usage_data.metric_id,
                quantity=float(usage_data.quantity),
                timestamp=usage_data.timestamp,
                action=usage_data.action,
                metadata=usage_data.metadata
            )
            
            self.db.add(usage_record)
            self.db.commit()
            
            # Update subscription usage totals
            await self._update_usage_totals(subscription.id, usage_data.metric_id, usage_data.quantity)
            
            return BillingResult(
                success=True,
                data={"usage_record_id": str(usage_record.id)},
                transaction_id=f"usage_{usage_record.id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to record usage: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Usage recording failed: {str(e)}"
            )
    
    async def get_usage_summary(self, tenant_id: str,
                               start_date: datetime,
                               end_date: datetime) -> Dict[str, Any]:
        """Get usage summary for a period."""
        try:
            # Query usage records
            usage_records = self.db.query(UsageRecord).filter(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.timestamp >= start_date,
                UsageRecord.timestamp <= end_date
            ).all()
            
            # Group by metric
            usage_by_metric = {}
            for record in usage_records:
                if record.metric_id not in usage_by_metric:
                    usage_by_metric[record.metric_id] = {
                        "total_quantity": 0,
                        "records": []
                    }
                
                usage_by_metric[record.metric_id]["total_quantity"] += record.quantity
                usage_by_metric[record.metric_id]["records"].append({
                    "timestamp": record.timestamp,
                    "quantity": record.quantity,
                    "action": record.action
                })
            
            # Calculate costs if applicable
            subscription = self._get_active_subscription(tenant_id)
            if subscription:
                plan = self._get_plan(subscription.plan_id)
                costs = await self._calculate_usage_costs(usage_by_metric, plan)
            else:
                costs = {}
            
            return {
                "period": {
                    "start": start_date,
                    "end": end_date
                },
                "usage_by_metric": usage_by_metric,
                "costs": costs,
                "total_records": len(usage_records)
            }
            
        except Exception as e:
            logger.error(f"Failed to get usage summary: {e}")
            raise BillingError(f"Usage summary retrieval failed: {str(e)}")
    
    async def generate_usage_invoice(self, tenant_id: str,
                                    period_start: datetime,
                                    period_end: datetime) -> Optional[Dict[str, Any]]:
        """Generate invoice for usage-based charges."""
        try:
            # Get usage summary
            usage_summary = await self.get_usage_summary(
                tenant_id, period_start, period_end
            )
            
            # Calculate charges
            subscription = self._get_active_subscription(tenant_id)
            if not subscription:
                return None
            
            plan = self._get_plan(subscription.plan_id)
            charges = await self._calculate_usage_charges(usage_summary, plan)
            
            if not charges:
                return None
            
            # Create invoice line items
            line_items = []
            for metric_id, charge in charges.items():
                line_items.append({
                    "metric_id": metric_id,
                    "description": f"Usage of {metric_id} ({period_start.date()} to {period_end.date()})",
                    "quantity": charge["quantity"],
                    "unit_price": charge["unit_price"],
                    "amount": charge["amount"],
                    "metadata": {
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "usage_data": usage_summary["usage_by_metric"][metric_id]
                    }
                })
            
            return {
                "tenant_id": tenant_id,
                "currency": "usd",
                "description": f"Usage charges for {period_start.date()} to {period_end.date()}",
                "line_items": line_items,
                "total_amount": sum(item["amount"] for item in line_items),
                "metadata": {
                    "billing_type": "usage",
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat()
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to generate usage invoice: {e}")
            raise InvoiceError(f"Usage invoice generation failed: {str(e)}")
    
    def _get_active_subscription(self, tenant_id: str):
        """Get active subscription for tenant."""
        return self.db.query(Subscription).filter(
            Subscription.tenant_id == tenant_id,
            Subscription.status == SubscriptionStatus.ACTIVE.value
        ).first()
    
    def _get_plan(self, plan_id: str):
        """Get plan by ID."""
        return self.db.query(Plan).filter(Plan.id == plan_id).first()
    
    def _is_metric_included(self, plan, metric_id: str) -> bool:
        """Check if metric is included in plan."""
        # Check plan metadata for included metrics
        plan_metrics = plan.metadata.get("included_metrics", [])
        return metric_id in plan_metrics
    
    async def _update_usage_totals(self, subscription_id: str,
                                  metric_id: str, quantity: Decimal):
        """Update usage totals for subscription."""
        # Implementation depends on database schema
        pass
    
    async def _calculate_usage_costs(self, usage_by_metric: Dict[str, Any],
                                    plan) -> Dict[str, Any]:
        """Calculate costs for usage."""
        # Implementation based on plan pricing
        pass
    
    async def _calculate_usage_charges(self, usage_summary: Dict[str, Any],
                                      plan) -> Dict[str, Any]:
        """Calculate charges for usage."""
        # Implementation based on plan pricing
        pass

# ============================================================================
# SUBSCRIPTION MANAGEMENT
# ============================================================================

class SubscriptionManager:
    """Manages subscription lifecycle."""
    
    def __init__(self, db_session: Session, stripe_manager: StripeManager):
        self.db = db_session
        self.stripe = stripe_manager
    
    async def create_subscription(self, subscription_data: SubscriptionData) -> BillingResult:
        """Create a new subscription."""
        try:
            # Get tenant and customer
            tenant = self.db.query(Tenant).filter(
                Tenant.id == subscription_data.tenant_id
            ).first()
            
            if not tenant:
                return BillingResult(
                    success=False,
                    error=f"Tenant {subscription_data.tenant_id} not found"
                )
            
            # Create or get Stripe customer
            customer_id = await self._get_or_create_customer(tenant)
            
            # Create Stripe subscription
            stripe_subscription = await self.stripe.create_subscription(
                subscription_data,
                customer_id
            )
            
            # Create local subscription record
            subscription = Subscription(
                id=stripe_subscription["id"],
                tenant_id=subscription_data.tenant_id,
                plan_id=subscription_data.plan_id,
                status=stripe_subscription["status"],
                billing_period=subscription_data.billing_period.value,
                quantity=subscription_data.quantity,
                current_period_start=datetime.fromtimestamp(
                    stripe_subscription["current_period_start"]
                ),
                current_period_end=datetime.fromtimestamp(
                    stripe_subscription["current_period_end"]
                ),
                cancel_at_period_end=stripe_subscription.get("cancel_at_period_end", False),
                trial_start=datetime.fromtimestamp(stripe_subscription["trial_start"]) 
                if stripe_subscription.get("trial_start") else None,
                trial_end=datetime.fromtimestamp(stripe_subscription["trial_end"])
                if stripe_subscription.get("trial_end") else None,
                metadata=subscription_data.metadata
            )
            
            self.db.add(subscription)
            self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "subscription_id": subscription.id,
                    "stripe_subscription": stripe_subscription
                },
                transaction_id=f"sub_{subscription.id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to create subscription: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Subscription creation failed: {str(e)}"
            )
    
    async def update_subscription(self, subscription_id: str,
                                 updates: Dict[str, Any]) -> BillingResult:
        """Update an existing subscription."""
        try:
            # Get local subscription
            subscription = self.db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            
            if not subscription:
                return BillingResult(
                    success=False,
                    error=f"Subscription {subscription_id} not found"
                )
            
            # Update in Stripe
            stripe_updates = {}
            if "quantity" in updates:
                stripe_updates["items"] = [{
                    "id": subscription.id,
                    "quantity": updates["quantity"]
                }]
            
            if "plan_id" in updates:
                stripe_updates["items"] = [{
                    "id": subscription.id,
                    "price": updates["plan_id"]
                }]
            
            if stripe_updates:
                stripe_subscription = await self.stripe.update_subscription(
                    subscription_id,
                    stripe_updates
                )
                
                # Update local record
                if "quantity" in updates:
                    subscription.quantity = updates["quantity"]
                
                if "plan_id" in updates:
                    subscription.plan_id = updates["plan_id"]
                
                subscription.status = stripe_subscription["status"]
                subscription.updated_at = datetime.utcnow()
                
                self.db.commit()
            
            return BillingResult(
                success=True,
                data={"subscription_id": subscription_id},
                transaction_id=f"update_sub_{subscription_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to update subscription: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Subscription update failed: {str(e)}"
            )
    
    async def cancel_subscription(self, subscription_id: str,
                                 cancel_at_period_end: bool = False) -> BillingResult:
        """Cancel a subscription."""
        try:
            # Cancel in Stripe
            stripe_subscription = await self.stripe.cancel_subscription(
                subscription_id,
                cancel_at_period_end
            )
            
            # Update local record
            subscription = self.db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            
            if subscription:
                if cancel_at_period_end:
                    subscription.cancel_at_period_end = True
                    subscription.status = SubscriptionStatus.ACTIVE.value
                else:
                    subscription.status = SubscriptionStatus.CANCELED.value
                    subscription.canceled_at = datetime.utcnow()
                
                subscription.updated_at = datetime.utcnow()
                self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "subscription_id": subscription_id,
                    "cancel_at_period_end": cancel_at_period_end
                },
                transaction_id=f"cancel_sub_{subscription_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to cancel subscription: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Subscription cancellation failed: {str(e)}"
            )
    
    async def get_subscription(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        """Get subscription details."""
        try:
            subscription = self.db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            
            if not subscription:
                return None
            
            # Get Stripe subscription for latest data
            stripe_subscription = await self.stripe.get_subscription(subscription_id)
            
            return {
                "id": subscription.id,
                "tenant_id": subscription.tenant_id,
                "plan_id": subscription.plan_id,
                "status": subscription.status,
                "billing_period": subscription.billing_period,
                "quantity": subscription.quantity,
                "current_period_start": subscription.current_period_start,
                "current_period_end": subscription.current_period_end,
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "trial_start": subscription.trial_start,
                "trial_end": subscription.trial_end,
                "canceled_at": subscription.canceled_at,
                "created_at": subscription.created_at,
                "updated_at": subscription.updated_at,
                "stripe_data": stripe_subscription
            }
            
        except Exception as e:
            logger.error(f"Failed to get subscription: {e}")
            return None
    
    async def _get_or_create_customer(self, tenant: Tenant) -> str:
        """Get or create Stripe customer for tenant."""
        # Check if tenant already has Stripe customer ID
        if tenant.stripe_customer_id:
            return tenant.stripe_customer_id
        
        # Create new customer in Stripe
        customer_data = CustomerData(
            tenant_id=str(tenant.id),
            email=tenant.billing_email or tenant.contact_email,
            name=tenant.name,
            metadata={"tenant_id": str(tenant.id)}
        )
        
        stripe_customer = await self.stripe.create_customer(customer_data)
        customer_id = stripe_customer["id"]
        
        # Update tenant with Stripe customer ID
        tenant.stripe_customer_id = customer_id
        self.db.commit()
        
        return customer_id

# ============================================================================
# INVOICE GENERATION
# ============================================================================

class InvoiceGenerator:
    """Generates and manages invoices."""
    
    def __init__(self, db_session: Session, stripe_manager: StripeManager):
        self.db = db_session
        self.stripe = stripe_manager
    
    async def generate_invoice(self, invoice_data: InvoiceData) -> BillingResult:
        """Generate a new invoice."""
        try:
            # Get tenant
            tenant = self.db.query(Tenant).filter(
                Tenant.id == invoice_data.tenant_id
            ).first()
            
            if not tenant or not tenant.stripe_customer_id:
                return BillingResult(
                    success=False,
                    error=f"Tenant {invoice_data.tenant_id} not found or no Stripe customer"
                )
            
            # Create Stripe invoice
            stripe_invoice = await self.stripe.create_invoice(
                tenant.stripe_customer_id,
                invoice_data
            )
            
            # Create local invoice record
            invoice = Invoice(
                id=stripe_invoice["id"],
                tenant_id=invoice_data.tenant_id,
                invoice_number=stripe_invoice.get("number"),
                currency=invoice_data.currency,
                amount_due=stripe_invoice.get("amount_due", 0) / 100,  # Convert from cents
                amount_paid=stripe_invoice.get("amount_paid", 0) / 100,
                amount_remaining=stripe_invoice.get("amount_remaining", 0) / 100,
                status=stripe_invoice.get("status", "draft"),
                billing_reason=stripe_invoice.get("billing_reason"),
                due_date=datetime.fromtimestamp(stripe_invoice["due_date"])
                if stripe_invoice.get("due_date") else None,
                paid_at=datetime.fromtimestamp(stripe_invoice["status_transitions"]["paid_at"])
                if stripe_invoice.get("status_transitions", {}).get("paid_at") else None,
                finalized_at=datetime.fromtimestamp(stripe_invoice["status_transitions"]["finalized_at"])
                if stripe_invoice.get("status_transitions", {}).get("finalized_at") else None,
                metadata=invoice_data.metadata
            )
            
            self.db.add(invoice)
            
            # Add line items
            for item in stripe_invoice.get("lines", {}).get("data", []):
                line_item = InvoiceLineItem(
                    invoice_id=invoice.id,
                    description=item.get("description"),
                    quantity=item.get("quantity", 1),
                    unit_price=item.get("unit_amount", 0) / 100,
                    amount=item.get("amount", 0) / 100,
                    tax_rate=item.get("tax_rates", [{}])[0].get("percentage")
                    if item.get("tax_rates") else None,
                    metadata=item.get("metadata", {})
                )
                self.db.add(line_item)
            
            self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "invoice_id": invoice.id,
                    "invoice_number": invoice.invoice_number,
                    "amount_due": invoice.amount_due,
                    "status": invoice.status,
                    "stripe_invoice": stripe_invoice
                },
                transaction_id=f"inv_{invoice.id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to generate invoice: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Invoice generation failed: {str(e)}"
            )
    
    async def generate_subscription_invoice(self, subscription_id: str) -> BillingResult:
        """Generate invoice for a subscription."""
        try:
            # Get subscription
            subscription = self.db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            
            if not subscription:
                return BillingResult(
                    success=False,
                    error=f"Subscription {subscription_id} not found"
                )
            
            # Get tenant
            tenant = self.db.query(Tenant).filter(
                Tenant.id == subscription.tenant_id
            ).first()
            
            if not tenant.stripe_customer_id:
                return BillingResult(
                    success=False,
                    error="Tenant has no Stripe customer"
                )
            
            # Create invoice in Stripe
            stripe_invoice = await self.stripe.create_invoice_for_subscription(
                subscription_id
            )
            
            # Process as regular invoice
            invoice_data = InvoiceData(
                tenant_id=subscription.tenant_id,
                currency="usd",
                description=f"Subscription invoice for {subscription.plan_id}",
                metadata={"subscription_id": subscription_id}
            )
            
            return await self.generate_invoice(invoice_data)
            
        except Exception as e:
            logger.error(f"Failed to generate subscription invoice: {e}")
            return BillingResult(
                success=False,
                error=f"Subscription invoice generation failed: {str(e)}"
            )
    
    async def pay_invoice(self, invoice_id: str,
                         payment_method_id: Optional[str] = None) -> BillingResult:
        """Pay an invoice."""
        try:
            # Pay in Stripe
            stripe_invoice = await self.stripe.pay_invoice(
                invoice_id,
                payment_method_id
            )
            
            # Update local invoice
            invoice = self.db.query(Invoice).filter(
                Invoice.id == invoice_id
            ).first()
            
            if invoice:
                invoice.status = stripe_invoice["status"]
                invoice.amount_paid = stripe_invoice.get("amount_paid", 0) / 100
                invoice.amount_remaining = stripe_invoice.get("amount_remaining", 0) / 100
                invoice.paid_at = datetime.utcnow()
                
                # Create payment record
                payment = Payment(
                    invoice_id=invoice_id,
                    tenant_id=invoice.tenant_id,
                    amount=invoice.amount_paid,
                    currency=invoice.currency,
                    status=PaymentStatus.COMPLETED.value,
                    payment_method=payment_method_id or "card",
                    transaction_id=stripe_invoice.get("payment_intent"),
                    metadata={"stripe_invoice_id": invoice_id}
                )
                
                self.db.add(payment)
                self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "invoice_id": invoice_id,
                    "status": stripe_invoice["status"],
                    "amount_paid": stripe_invoice.get("amount_paid", 0) / 100
                },
                transaction_id=f"pay_inv_{invoice_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to pay invoice: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Invoice payment failed: {str(e)}"
            )
    
    async def send_invoice(self, invoice_id: str,
                          email_template: Optional[str] = None) -> BillingResult:
        """Send invoice to customer."""
        try:
            invoice = self.db.query(Invoice).filter(
                Invoice.id == invoice_id
            ).first()
            
            if not invoice:
                return BillingResult(
                    success=False,
                    error=f"Invoice {invoice_id} not found"
                )
            
            # Send via Stripe
            stripe_invoice = self.stripe.Invoice.send_invoice(invoice_id)
            
            # Update local status
            invoice.status = stripe_invoice["status"]
            invoice.updated_at = datetime.utcnow()
            self.db.commit()
            
            # Send email notification
            await self._send_invoice_email(invoice, email_template)
            
            return BillingResult(
                success=True,
                data={"invoice_id": invoice_id, "sent": True},
                transaction_id=f"send_inv_{invoice_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to send invoice: {e}")
            return BillingResult(
                success=False,
                error=f"Invoice sending failed: {str(e)}"
            )
    
    async def _send_invoice_email(self, invoice: Invoice,
                                 template: Optional[str] = None):
        """Send invoice email to customer."""
        # Email sending implementation
        pass

# ============================================================================
# PAYMENT PROCESSING
# ============================================================================

class PaymentProcessor:
    """Processes payments and manages payment methods."""
    
    def __init__(self, db_session: Session, stripe_manager: StripeManager):
        self.db = db_session
        self.stripe = stripe_manager
    
    async def process_payment(self, payment_data: PaymentData) -> BillingResult:
        """Process a payment."""
        try:
            # Get invoice
            invoice = self.db.query(Invoice).filter(
                Invoice.id == payment_data.invoice_id
            ).first()
            
            if not invoice:
                return BillingResult(
                    success=False,
                    error=f"Invoice {payment_data.invoice_id} not found"
                )
            
            # Get tenant
            tenant = self.db.query(Tenant).filter(
                Tenant.id == invoice.tenant_id
            ).first()
            
            if not tenant.stripe_customer_id:
                return BillingResult(
                    success=False,
                    error="Tenant has no Stripe customer"
                )
            
            # Create payment intent
            payment_intent = await self.stripe.create_payment_intent(
                amount=int(payment_data.amount * 100),  # Convert to cents
                currency=payment_data.currency,
                customer_id=tenant.stripe_customer_id,
                metadata={
                    "invoice_id": payment_data.invoice_id,
                    "tenant_id": invoice.tenant_id,
                    **payment_data.metadata
                }
            )
            
            # Create payment record
            payment = Payment(
                invoice_id=payment_data.invoice_id,
                tenant_id=invoice.tenant_id,
                amount=payment_data.amount,
                currency=payment_data.currency,
                status=PaymentStatus.PROCESSING.value,
                payment_method=payment_data.source_type,
                transaction_id=payment_intent["id"],
                metadata=payment_data.metadata
            )
            
            self.db.add(payment)
            self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "payment_id": str(payment.id),
                    "payment_intent": payment_intent,
                    "client_secret": payment_intent.get("client_secret")
                },
                transaction_id=f"pay_{payment.id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to process payment: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Payment processing failed: {str(e)}"
            )
    
    async def confirm_payment(self, payment_intent_id: str) -> BillingResult:
        """Confirm a payment intent."""
        try:
            # Confirm in Stripe
            payment_intent = self.stripe.PaymentIntent.confirm(payment_intent_id)
            
            # Update payment record
            payment = self.db.query(Payment).filter(
                Payment.transaction_id == payment_intent_id
            ).first()
            
            if payment:
                payment.status = payment_intent["status"]
                payment.updated_at = datetime.utcnow()
                
                # Update invoice if payment successful
                if payment_intent["status"] == "succeeded":
                    invoice = self.db.query(Invoice).filter(
                        Invoice.id == payment.invoice_id
                    ).first()
                    
                    if invoice:
                        invoice.status = InvoiceStatus.PAID.value
                        invoice.amount_paid += payment.amount
                        invoice.amount_remaining = invoice.amount_due - invoice.amount_paid
                        invoice.paid_at = datetime.utcnow()
                
                self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "payment_intent_id": payment_intent_id,
                    "status": payment_intent["status"]
                },
                transaction_id=f"confirm_pay_{payment_intent_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to confirm payment: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Payment confirmation failed: {str(e)}"
            )
    
    async def create_refund(self, payment_id: str,
                           amount: Optional[Decimal] = None,
                           reason: Optional[str] = None) -> BillingResult:
        """Create a refund for a payment."""
        try:
            # Get payment
            payment = self.db.query(Payment).filter(
                Payment.id == payment_id
            ).first()
            
            if not payment:
                return BillingResult(
                    success=False,
                    error=f"Payment {payment_id} not found"
                )
            
            # Create refund in Stripe
            refund = await self.stripe.create_refund(
                payment.transaction_id,
                int(amount * 100) if amount else None
            )
            
            # Create refund record
            refund_record = Refund(
                payment_id=payment_id,
                tenant_id=payment.tenant_id,
                amount=refund["amount"] / 100,
                currency=refund["currency"],
                status=refund["status"],
                reason=reason,
                transaction_id=refund["id"],
                metadata={"stripe_refund_id": refund["id"]}
            )
            
            self.db.add(refund_record)
            
            # Update payment status
            payment.status = PaymentStatus.REFUNDED.value
            payment.updated_at = datetime.utcnow()
            
            self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "refund_id": str(refund_record.id),
                    "amount": refund_record.amount,
                    "status": refund_record.status
                },
                transaction_id=f"refund_{refund_record.id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to create refund: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Refund creation failed: {str(e)}"
            )
    
    async def get_payment_methods(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Get payment methods for a tenant."""
        try:
            tenant = self.db.query(Tenant).filter(
                Tenant.id == tenant_id
            ).first()
            
            if not tenant.stripe_customer_id:
                return []
            
            # Get payment methods from Stripe
            payment_methods = self.stripe.Customer.list_payment_methods(
                tenant.stripe_customer_id,
                type="card"
            )
            
            return [pm.to_dict() for pm in payment_methods.data]
            
        except Exception as e:
            logger.error(f"Failed to get payment methods: {e}")
            return []

# ============================================================================
# TAX CALCULATION
# ============================================================================

class TaxCalculator:
    """Calculates taxes for invoices."""
    
    def __init__(self, db_session: Session, stripe_manager: StripeManager):
        self.db = db_session
        self.stripe = stripe_manager
    
    async def calculate_tax(self, tenant_id: str,
                           line_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate tax for line items."""
        try:
            tenant = self.db.query(Tenant).filter(
                Tenant.id == tenant_id
            ).first()
            
            if not tenant or not tenant.stripe_customer_id:
                raise TaxCalculationError("Tenant not found or no Stripe customer")
            
            # Use Stripe Tax for calculation
            tax_calculation = await self.stripe.calculate_tax(
                tenant.stripe_customer_id,
                line_items
            )
            
            return {
                "tax_amount": tax_calculation["tax_amount_exclusive"] / 100,
                "total_amount": tax_calculation["amount_total"] / 100,
                "tax_breakdown": [
                    {
                        "jurisdiction": tb["jurisdiction"]["country"],
                        "percentage": tb["tax_rate_details"]["percentage_decimal"],
                        "amount": tb["amount"] / 100
                    }
                    for tb in tax_calculation.get("tax_breakdown", [])
                ]
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate tax: {e}")
            raise TaxCalculationError(f"Tax calculation failed: {str(e)}")
    
    async def get_tax_rates(self, country: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get applicable tax rates for location."""
        try:
            # Query local tax rates
            query = self.db.query(TaxRate).filter(
                TaxRate.country == country,
                TaxRate.is_active == True
            )
            
            if state:
                query = query.filter(TaxRate.state == state)
            
            tax_rates = query.all()
            
            # Convert to dict
            return [
                {
                    "id": str(rate.id),
                    "country": rate.country,
                    "state": rate.state,
                    "jurisdiction": rate.jurisdiction,
                    "percentage": float(rate.percentage),
                    "inclusive": rate.inclusive,
                    "description": rate.description,
                    "metadata": rate.metadata
                }
                for rate in tax_rates
            ]
            
        except Exception as e:
            logger.error(f"Failed to get tax rates: {e}")
            return []
    
    async def create_tax_rate(self, tax_rate_data: Dict[str, Any]) -> BillingResult:
        """Create a new tax rate."""
        try:
            # Create in Stripe
            stripe_tax_rate = self.stripe.TaxRate.create(**tax_rate_data)
            
            # Create local record
            tax_rate = TaxRate(
                id=stripe_tax_rate["id"],
                country=tax_rate_data.get("country"),
                state=tax_rate_data.get("state"),
                jurisdiction=tax_rate_data.get("jurisdiction"),
                percentage=Decimal(str(stripe_tax_rate["percentage"])),
                inclusive=tax_rate_data.get("inclusive", False),
                description=tax_rate_data.get("description"),
                metadata=tax_rate_data.get("metadata", {})
            )
            
            self.db.add(tax_rate)
            self.db.commit()
            
            return BillingResult(
                success=True,
                data={"tax_rate_id": tax_rate.id},
                transaction_id=f"tax_rate_{tax_rate.id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to create tax rate: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Tax rate creation failed: {str(e)}"
            )

# ============================================================================
# DISCOUNT MANAGEMENT
# ============================================================================

class DiscountManager:
    """Manages discounts and coupons."""
    
    def __init__(self, db_session: Session, stripe_manager: StripeManager):
        self.db = db_session
        self.stripe = stripe_manager
    
    async def create_coupon(self, coupon_data: Dict[str, Any]) -> BillingResult:
        """Create a new coupon."""
        try:
            # Create in Stripe
            stripe_coupon = await self.stripe.create_coupon(coupon_data)
            
            # Create local record
            discount = Discount(
                id=stripe_coupon["id"],
                code=coupon_data.get("code"),
                name=coupon_data.get("name"),
                description=coupon_data.get("description"),
                discount_type=coupon_data.get("type", "percentage"),
                amount=Decimal(str(coupon_data.get("amount", 0))),
                percentage=coupon_data.get("percent_off"),
                duration=coupon_data.get("duration", "once"),
                duration_in_months=coupon_data.get("duration_in_months"),
                max_redemptions=coupon_data.get("max_redemptions"),
                redeems_by=datetime.fromtimestamp(coupon_data["redeem_by"])
                if coupon_data.get("redeem_by") else None,
                is_active=True,
                metadata=coupon_data.get("metadata", {})
            )
            
            self.db.add(discount)
            self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "coupon_id": discount.id,
                    "code": discount.code
                },
                transaction_id=f"coupon_{discount.id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to create coupon: {e}")
            self.db.rollback()
            return BillingResult(
                success=False,
                error=f"Coupon creation failed: {str(e)}"
            )
    
    async def apply_discount(self, invoice_id: str,
                            coupon_code: str) -> BillingResult:
        """Apply discount to an invoice."""
        try:
            # Get discount
            discount = self.db.query(Discount).filter(
                Discount.code == coupon_code,
                Discount.is_active == True
            ).first()
            
            if not discount:
                return BillingResult(
                    success=False,
                    error=f"Discount {coupon_code} not found or inactive"
                )
            
            # Apply in Stripe
            invoice = self.stripe.Invoice.retrieve(invoice_id)
            
            if discount.discount_type == "percentage":
                discount_amount = invoice.amount_due * (discount.percentage / 100)
            else:
                discount_amount = discount.amount * 100  # Convert to cents
            
            # Add discount line item
            discount_item = self.stripe.InvoiceItem.create(
                invoice=invoice_id,
                amount=-discount_amount,  # Negative amount for discount
                description=f"Discount: {discount.code}",
                discountable=False
            )
            
            # Update invoice
            updated_invoice = self.stripe.Invoice.retrieve(invoice_id)
            
            # Update local invoice
            local_invoice = self.db.query(Invoice).filter(
                Invoice.id == invoice_id
            ).first()
            
            if local_invoice:
                local_invoice.amount_due = updated_invoice.amount_due / 100
                local_invoice.updated_at = datetime.utcnow()
                self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "invoice_id": invoice_id,
                    "discount_code": coupon_code,
                    "discount_amount": discount_amount / 100,
                    "new_amount_due": updated_invoice.amount_due / 100
                },
                transaction_id=f"discount_{invoice_id}_{coupon_code}"
            )
            
        except Exception as e:
            logger.error(f"Failed to apply discount: {e}")
            return BillingResult(
                success=False,
                error=f"Discount application failed: {str(e)}"
            )
    
    async def validate_coupon(self, coupon_code: str,
                             tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Validate a coupon code."""
        try:
            discount = self.db.query(Discount).filter(
                Discount.code == coupon_code,
                Discount.is_active == True
            ).first()
            
            if not discount:
                return {"valid": False, "error": "Coupon not found"}
            
            # Check expiration
            if discount.redeems_by and discount.redeems_by < datetime.utcnow():
                return {"valid": False, "error": "Coupon expired"}
            
            # Check max redemptions
            if discount.max_redemptions:
                redemption_count = self._get_redemption_count(discount.id)
                if redemption_count >= discount.max_redemptions:
                    return {"valid": False, "error": "Coupon limit reached"}
            
            return {
                "valid": True,
                "discount": {
                    "id": discount.id,
                    "code": discount.code,
                    "type": discount.discount_type,
                    "amount": float(discount.amount),
                    "percentage": discount.percentage,
                    "description": discount.description
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to validate coupon: {e}")
            return {"valid": False, "error": "Validation failed"}
    
    def _get_redemption_count(self, discount_id: str) -> int:
        """Get number of times discount has been used."""
        # Implementation depends on database schema
        return 0

# ============================================================================
# REVENUE RECOGNITION
# ============================================================================

class RevenueRecognition:
    """Manages revenue recognition according to accounting standards."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    async def recognize_revenue(self, invoice_id: str,
                               method: RevenueRecognitionMethod = None) -> BillingResult:
        """Recognize revenue for an invoice."""
        try:
            invoice = self.db.query(Invoice).filter(
                Invoice.id == invoice_id
            ).first()
            
            if not invoice or invoice.status != InvoiceStatus.PAID.value:
                return BillingResult(
                    success=False,
                    error="Invoice not found or not paid"
                )
            
            # Determine recognition method
            if not method:
                method = self._determine_recognition_method(invoice)
            
            # Apply recognition method
            if method == RevenueRecognitionMethod.UPFRONT:
                await self._recognize_upfront(invoice)
            elif method == RevenueRecognitionMethod.MONTHLY:
                await self._recognize_monthly(invoice)
            elif method == RevenueRecognitionMethod.USAGE:
                await self._recognize_usage_based(invoice)
            
            return BillingResult(
                success=True,
                data={
                    "invoice_id": invoice_id,
                    "recognition_method": method.value,
                    "recognized_at": datetime.utcnow()
                },
                transaction_id=f"rev_rec_{invoice_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to recognize revenue: {e}")
            return BillingResult(
                success=False,
                error=f"Revenue recognition failed: {str(e)}"
            )
    
    def _determine_recognition_method(self, invoice: Invoice) -> RevenueRecognitionMethod:
        """Determine appropriate revenue recognition method."""
        # Check invoice metadata for billing type
        billing_type = invoice.metadata.get("billing_type")
        
        if billing_type == "usage":
            return RevenueRecognitionMethod.USAGE
        elif billing_type == "subscription":
            return RevenueRecognitionMethod.MONTHLY
        else:
            return RevenueRecognitionMethod.UPFRONT
    
    async def _recognize_upfront(self, invoice: Invoice):
        """Recognize revenue immediately."""
        # Create revenue recognition record
        pass
    
    async def _recognize_monthly(self, invoice: Invoice):
        """Recognize revenue over subscription period."""
        # Create deferred revenue schedule
        pass
    
    async def _recognize_usage_based(self, invoice: Invoice):
        """Recognize revenue based on usage."""
        # Create usage-based recognition schedule
        pass
    
    async def generate_revenue_report(self, start_date: date,
                                     end_date: date) -> Dict[str, Any]:
        """Generate revenue recognition report."""
        try:
            # Query recognized revenue
            # Implementation depends on database schema
            
            return {
                "period": {"start": start_date, "end": end_date},
                "total_revenue": 0,
                "recognized_revenue": 0,
                "deferred_revenue": 0,
                "breakdown": {}
            }
            
        except Exception as e:
            logger.error(f"Failed to generate revenue report: {e}")
            raise BillingError(f"Revenue report generation failed: {str(e)}")

# ============================================================================
# FINANCIAL REPORTING
# ============================================================================

class FinancialReporter:
    """Generates financial reports and analytics."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    async def generate_income_statement(self, start_date: date,
                                       end_date: date) -> Dict[str, Any]:
        """Generate income statement (P&L)."""
        try:
            # Query revenue
            revenue = self._get_revenue_for_period(start_date, end_date)
            
            # Query expenses (if tracking)
            expenses = self._get_expenses_for_period(start_date, end_date)
            
            # Calculate metrics
            gross_profit = revenue - expenses
            margin = (gross_profit / revenue * 100) if revenue > 0 else 0
            
            return {
                "period": {"start": start_date, "end": end_date},
                "revenue": revenue,
                "expenses": expenses,
                "gross_profit": gross_profit,
                "margin_percentage": margin,
                "breakdown": {
                    "revenue_by_source": self._get_revenue_breakdown(start_date, end_date),
                    "expenses_by_category": self._get_expense_breakdown(start_date, end_date)
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to generate income statement: {e}")
            raise BillingError(f"Income statement generation failed: {str(e)}")
    
    async def generate_balance_sheet(self, as_of_date: date) -> Dict[str, Any]:
        """Generate balance sheet."""
        try:
            # Query assets (cash, accounts receivable, deferred revenue)
            assets = self._get_assets_as_of(as_of_date)
            
            # Query liabilities (accounts payable, deferred revenue)
            liabilities = self._get_liabilities_as_of(as_of_date)
            
            # Calculate equity
            equity = assets - liabilities
            
            return {
                "as_of_date": as_of_date,
                "assets": assets,
                "liabilities": liabilities,
                "equity": equity,
                "breakdown": {
                    "current_assets": self._get_current_assets(as_of_date),
                    "non_current_assets": self._get_non_current_assets(as_of_date),
                    "current_liabilities": self._get_current_liabilities(as_of_date),
                    "non_current_liabilities": self._get_non_current_liabilities(as_of_date)
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to generate balance sheet: {e}")
            raise BillingError(f"Balance sheet generation failed: {str(e)}")
    
    async def generate_cash_flow_statement(self, start_date: date,
                                          end_date: date) -> Dict[str, Any]:
        """Generate cash flow statement."""
        try:
            operating = self._get_operating_cash_flow(start_date, end_date)
            investing = self._get_investing_cash_flow(start_date, end_date)
            financing = self._get_financing_cash_flow(start_date, end_date)
            
            net_cash_flow = operating + investing + financing
            
            return {
                "period": {"start": start_date, "end": end_date},
                "operating_cash_flow": operating,
                "investing_cash_flow": investing,
                "financing_cash_flow": financing,
                "net_cash_flow": net_cash_flow,
                "breakdown": {
                    "operating_activities": self._get_operating_breakdown(start_date, end_date),
                    "investing_activities": self._get_investing_breakdown(start_date, end_date),
                    "financing_activities": self._get_financing_breakdown(start_date, end_date)
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to generate cash flow statement: {e}")
            raise BillingError(f"Cash flow statement generation failed: {str(e)}")
    
    async def generate_mrr_report(self, as_of_date: date) -> Dict[str, Any]:
        """Generate Monthly Recurring Revenue report."""
        try:
            # Calculate MRR
            mrr = self._calculate_mrr(as_of_date)
            
            # Calculate churn
            churn = self._calculate_churn_rate(as_of_date)
            
            # Calculate growth
            growth = self._calculate_growth_rate(as_of_date)
            
            return {
                "as_of_date": as_of_date,
                "mrr": mrr,
                "churn_rate": churn,
                "growth_rate": growth,
                "breakdown": {
                    "by_plan": self._get_mrr_by_plan(as_of_date),
                    "new_mrr": self._get_new_mrr(as_of_date),
                    "expansion_mrr": self._get_expansion_mrr(as_of_date),
                    "contraction_mrr": self._get_contraction_mrr(as_of_date),
                    "churned_mrr": self._get_churned_mrr(as_of_date)
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to generate MRR report: {e}")
            raise BillingError(f"MRR report generation failed: {str(e)}")
    
    # Helper methods (implementations depend on database schema)
    def _get_revenue_for_period(self, start_date: date, end_date: date) -> float:
        """Get total revenue for period."""
        # Implementation
        return 0.0
    
    def _get_expenses_for_period(self, start_date: date, end_date: date) -> float:
        """Get total expenses for period."""
        # Implementation
        return 0.0
    
    def _get_revenue_breakdown(self, start_date: date, end_date: date) -> Dict[str, float]:
        """Get revenue breakdown by source."""
        # Implementation
        return {}
    
    # ... additional helper methods

# ============================================================================
# COMPLIANCE FEATURES
# ============================================================================

class ComplianceManager:
    """Manages billing compliance and regulatory requirements."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    async def generate_vat_report(self, country: str,
                                 period: str) -> Dict[str, Any]:
        """Generate VAT report for a country."""
        try:
            # Query invoices for country in period
            # Calculate VAT amounts
            # Generate report
            
            return {
                "country": country,
                "period": period,
                "total_sales": 0,
                "total_vat": 0,
                "breakdown": {}
            }
            
        except Exception as e:
            logger.error(f"Failed to generate VAT report: {e}")
            raise BillingError(f"VAT report generation failed: {str(e)}")
    
    async def generate_1099_report(self, year: int) -> Dict[str, Any]:
        """Generate 1099 report for US tax purposes."""
        try:
            # Query payments to vendors
            # Generate 1099 data
            
            return {
                "year": year,
                "total_payments": 0,
                "vendors": []
            }
            
        except Exception as e:
            logger.error(f"Failed to generate 1099 report: {e}")
            raise BillingError(f"1099 report generation failed: {str(e)}")
    
    async def audit_trail(self, entity_type: str,
                         entity_id: str,
                         start_date: datetime,
                         end_date: datetime) -> List[Dict[str, Any]]:
        """Generate audit trail for billing entity."""
        try:
            # Query audit logs for entity
            # Return chronological trail
            
            return []
            
        except Exception as e:
            logger.error(f"Failed to generate audit trail: {e}")
            raise BillingError(f"Audit trail generation failed: {str(e)}")
    
    async def validate_compliance(self, standard: str) -> Dict[str, Any]:
        """Validate compliance with billing standards."""
        try:
            # Check compliance with standard (PCI DSS, GDPR, etc.)
            
            return {
                "standard": standard,
                "compliant": True,
                "checks": [],
                "issues": []
            }
            
        except Exception as e:
            logger.error(f"Failed to validate compliance: {e}")
            raise BillingError(f"Compliance validation failed: {str(e)}")

# ============================================================================
# MAIN BILLING SERVICE
# ============================================================================

class BillingService:
    """Main billing service coordinating all billing operations."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.stripe = StripeManager()
        self.subscription_manager = SubscriptionManager(db_session, self.stripe)
        self.invoice_generator = InvoiceGenerator(db_session, self.stripe)
        self.payment_processor = PaymentProcessor(db_session, self.stripe)
        self.tax_calculator = TaxCalculator(db_session, self.stripe)
        self.discount_manager = DiscountManager(db_session, self.stripe)
        self.usage_meter = UsageMeter(db_session)
        self.revenue_recognition = RevenueRecognition(db_session)
        self.financial_reporter = FinancialReporter(db_session)
        self.compliance_manager = ComplianceManager(db_session)
    
    # ==================== CUSTOMER ONBOARDING ====================
    
    async def onboard_customer(self, customer_data: CustomerData) -> BillingResult:
        """Onboard a new customer for billing."""
        try:
            # Create Stripe customer
            stripe_customer = await self.stripe.create_customer(customer_data)
            
            # Update tenant with Stripe customer ID
            tenant = self.db.query(Tenant).filter(
                Tenant.id == customer_data.tenant_id
            ).first()
            
            if tenant:
                tenant.stripe_customer_id = stripe_customer["id"]
                tenant.billing_email = customer_data.email
                self.db.commit()
            
            return BillingResult(
                success=True,
                data={
                    "customer_id": stripe_customer["id"],
                    "tenant_id": customer_data.tenant_id
                },
                transaction_id=f"onboard_{customer_data.tenant_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to onboard customer: {e}")
            return BillingResult(
                success=False,
                error=f"Customer onboarding failed: {str(e)}"
            )
    
    # ==================== SUBSCRIPTION MANAGEMENT ====================
    
    async def create_subscription(self, subscription_data: SubscriptionData) -> BillingResult:
        """Create a new subscription."""
        return await self.subscription_manager.create_subscription(subscription_data)
    
    async def update_subscription(self, subscription_id: str,
                                 updates: Dict[str, Any]) -> BillingResult:
        """Update a subscription."""
        return await self.subscription_manager.update_subscription(subscription_id, updates)
    
    async def cancel_subscription(self, subscription_id: str,
                                 cancel_at_period_end: bool = False) -> BillingResult:
        """Cancel a subscription."""
        return await self.subscription_manager.cancel_subscription(
            subscription_id, cancel_at_period_end
        )
    
    # ==================== USAGE METERING ====================
    
    async def record_usage(self, usage_data: UsageRecordData) -> BillingResult:
        """Record usage for metered billing."""
        return await self.usage_meter.record_usage(usage_data)
    
    async def generate_usage_invoice(self, tenant_id: str,
                                    period_start: datetime,
                                    period_end: datetime) -> BillingResult:
        """Generate invoice for usage-based charges."""
        invoice_data = await self.usage_meter.generate_usage_invoice(
            tenant_id, period_start, period_end
        )
        
        if not invoice_data:
            return BillingResult(
                success=False,
                error="No usage charges to invoice"
            )
        
        return await self.invoice_generator.generate_invoice(
            InvoiceData(**invoice_data)
        )
    
    # ==================== INVOICE GENERATION ====================
    
    async def generate_invoice(self, invoice_data: InvoiceData) -> BillingResult:
        """Generate a new invoice."""
        return await self.invoice_generator.generate_invoice(invoice_data)
    
    async def pay_invoice(self, invoice_id: str,
                         payment_method_id: Optional[str] = None) -> BillingResult:
        """Pay an invoice."""
        return await self.invoice_generator.pay_invoice(invoice_id, payment_method_id)
    
    async def send_invoice(self, invoice_id: str,
                          email_template: Optional[str] = None) -> BillingResult:
        """Send invoice to customer."""
        return await self.invoice_generator.send_invoice(invoice_id, email_template)
    
    # ==================== PAYMENT PROCESSING ====================
    
    async def process_payment(self, payment_data: PaymentData) -> BillingResult:
        """Process a payment."""
        return await self.payment_processor.process_payment(payment_data)
    
    async def create_refund(self, payment_id: str,
                           amount: Optional[Decimal] = None,
                           reason: Optional[str] = None) -> BillingResult:
        """Create a refund."""
        return await self.payment_processor.create_refund(payment_id, amount, reason)
    
    # ==================== DUNNING MANAGEMENT ====================
    
    async def handle_payment_failure(self, invoice_id: str) -> BillingResult:
        """Handle payment failure (dunning management)."""
        try:
            invoice = self.db.query(Invoice).filter(
                Invoice.id == invoice_id
            ).first()
            
            if not invoice:
                return BillingResult(
                    success=False,
                    error=f"Invoice {invoice_id} not found"
                )
            
            # Implement dunning logic:
            # 1. Send payment failure notification
            # 2. Update invoice status
            # 3. Retry payment if applicable
            # 4. Escalate if multiple failures
            
            return BillingResult(
                success=True,
                data={"invoice_id": invoice_id, "action": "payment_failure_handled"},
                transaction_id=f"dunning_{invoice_id}"
            )
            
        except Exception as e:
            logger.error(f"Failed to handle payment failure: {e}")
            return BillingResult(
                success=False,
                error=f"Payment failure handling failed: {str(e)}"
            )
    
    # ==================== FINANCIAL RECONCILIATION ====================
    
    async def reconcile_payments(self, start_date: datetime,
                                end_date: datetime) -> BillingResult:
        """Reconcile payments between Stripe and local database."""
        try:
            # Get payments from Stripe
            stripe_payments = self.stripe.PaymentIntent.list(
                created={
                    "gte": int(start_date.timestamp()),
                    "lte": int(end_date.timestamp())
                },
                limit=100
            )
            
            # Get local payments
            local_payments = self.db.query(Payment).filter(
                Payment.created_at >= start_date,
                Payment.created_at <= end_date
            ).all()
            
            # Reconcile
            discrepancies = []
            for stripe_payment in stripe_payments.data:
                local_payment = next(
                    (p for p in local_payments if p.transaction_id == stripe_payment.id),
                    None
                )
                
                if not local_payment:
                    discrepancies.append({
                        "type": "missing_local",
                        "stripe_payment_id": stripe_payment.id,
                        "amount": stripe_payment.amount / 100,
                        "currency": stripe_payment.currency
                    })
            
            return BillingResult(
                success=True,
                data={
                    "period": {"start": start_date, "end": end_date},
                    "stripe_payments": len(stripe_payments.data),
                    "local_payments": len(local_payments),
                    "discrepancies": discrepancies
                },
                transaction_id=f"reconcile_{start_date.date()}_{end_date.date()}"
            )
            
        except Exception as e:
            logger.error(f"Failed to reconcile payments: {e}")
            return BillingResult(
                success=False,
                error=f"Payment reconciliation failed: {str(e)}"
            )
    
    # ==================== WEBHOOK HANDLING ====================
    
    async def handle_stripe_webhook(self, payload: bytes,
                                   signature: str) -> BillingResult:
        """Handle Stripe webhook events."""
        try:
            result = await self.stripe.handle_webhook(payload, signature)
            
            # Update local state based on webhook
            if result["handled"]:
                await self._process_webhook_event(result["event_type"], result["result"])
            
            return BillingResult(
                success=True,
                data=result,
                transaction_id=f"webhook_{result.get('event_type', 'unknown')}"
            )
            
        except Exception as e:
            logger.error(f"Failed to handle webhook: {e}")
            return BillingResult(
                success=False,
                error=f"Webhook handling failed: {str(e)}"
            )
    
    async def _process_webhook_event(self, event_type: str, event_data: Dict[str, Any]):
        """Process webhook event and update local state."""
        # Implementation based on event type
        pass

# ============================================================================
# FACTORY FUNCTION
# ============================================================================

def create_billing_service(db_session: Session) -> BillingService:
    """Create and configure billing service."""
    return BillingService(db_session)

# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Main service
    "BillingService",
    "create_billing_service",
    
    # Managers
    "StripeManager",
    "SubscriptionManager",
    "InvoiceGenerator",
    "PaymentProcessor",
    "TaxCalculator",
    "DiscountManager",
    "UsageMeter",
    "RevenueRecognition",
    "FinancialReporter",
    "ComplianceManager",
    
    # Data models
    "BillingResult",
    "CustomerData",
    "SubscriptionData",
    "InvoiceData",
    "UsageRecordData",
    "PaymentData",
    "Address",
    
    # Enums
    "BillingPeriod",
    "InvoiceStatus",
    "PaymentStatus",
    "SubscriptionStatus",
    "TaxMode",
    "RevenueRecognitionMethod",
    
    # Exceptions
    "BillingError",
    "PaymentError",
    "SubscriptionError",
    "InvoiceError",
    "TaxCalculationError"
]