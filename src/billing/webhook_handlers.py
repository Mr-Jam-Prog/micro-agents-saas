"""
Stripe Webhook Handlers for MicroAgents Platform
Robust webhook processing with idempotency, error handling, and comprehensive monitoring.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable, Awaitable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
import uuid

# External dependencies
import stripe
from stripe.error import SignatureVerificationError, StripeError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import redis.asyncio as redis
from prometheus_client import Counter, Histogram, Gauge

# Internal imports
from ..database.models import (
    Tenant, Subscription, Invoice, Payment, Refund, CreditNote,
    WebhookEvent, StripeEvent, Customer, Plan, UsageRecord,
    BillingSettings, AuditLog
)
from ..config.settings import STRIPE_CONFIG, REDIS_CONFIG
from ..utils.encryption import decrypt_data
from ..utils.metrics import MetricsCollector
from ..utils.retry import async_retry
from ..exceptions import (
    WebhookError, ValidationError, ProcessingError,
    IdempotencyError, SecurityError
)

logger = logging.getLogger(__name__)

# ============================================================================
# METRICS
# ============================================================================

# Prometheus metrics
WEBHOOK_EVENTS_TOTAL = Counter(
    'billing_webhook_events_total',
    'Total number of webhook events received',
    ['event_type', 'status']
)

WEBHOOK_EVENT_DURATION = Histogram(
    'billing_webhook_event_duration_seconds',
    'Webhook event processing duration',
    ['event_type']
)

WEBHOOK_PROCESSING_ERRORS = Counter(
    'billing_webhook_processing_errors_total',
    'Total webhook processing errors',
    ['event_type', 'error_type']
)

WEBHOOK_EVENTS_IN_FLIGHT = Gauge(
    'billing_webhook_events_in_flight',
    'Number of webhook events currently being processed'
)

IDEMPOTENCY_CHECKS = Counter(
    'billing_webhook_idempotency_checks_total',
    'Total idempotency checks performed',
    ['result']
)

# ============================================================================
# ENUMS & DATA MODELS
# ============================================================================

class WebhookEventType(str, Enum):
    """Stripe webhook event types."""
    # Payment events
    PAYMENT_INTENT_SUCCEEDED = "payment_intent.succeeded"
    PAYMENT_INTENT_FAILED = "payment_intent.failed"
    PAYMENT_INTENT_PROCESSING = "payment_intent.processing"
    PAYMENT_INTENT_CANCELED = "payment_intent.canceled"
    PAYMENT_INTENT_REQUIRES_ACTION = "payment_intent.requires_action"
    
    # Charge events
    CHARGE_SUCCEEDED = "charge.succeeded"
    CHARGE_FAILED = "charge.failed"
    CHARGE_REFUNDED = "charge.refunded"
    CHARGE_DISPUTE_CREATED = "charge.dispute.created"
    CHARGE_DISPUTE_CLOSED = "charge.dispute.closed"
    
    # Subscription events
    CUSTOMER_SUBSCRIPTION_CREATED = "customer.subscription.created"
    CUSTOMER_SUBSCRIPTION_UPDATED = "customer.subscription.updated"
    CUSTOMER_SUBSCRIPTION_DELETED = "customer.subscription.deleted"
    CUSTOMER_SUBSCRIPTION_TRIAL_WILL_END = "customer.subscription.trial_will_end"
    CUSTOMER_SUBSCRIPTION_PENDING_UPDATE_APPLIED = "customer.subscription.pending_update_applied"
    CUSTOMER_SUBSCRIPTION_PENDING_UPDATE_EXPIRED = "customer.subscription.pending_update_expired"
    
    # Invoice events
    INVOICE_CREATED = "invoice.created"
    INVOICE_FINALIZED = "invoice.finalized"
    INVOICE_PAID = "invoice.paid"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    INVOICE_PAYMENT_ACTION_REQUIRED = "invoice.payment_action_required"
    INVOICE_UPCOMING = "invoice.upcoming"
    INVOICE_MARKED_UNCOLLECTIBLE = "invoice.marked_uncollectible"
    INVOICE_VOIDED = "invoice.voided"
    
    # Customer events
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMER_DELETED = "customer.deleted"
    CUSTOMER_SOURCE_CREATED = "customer.source.created"
    CUSTOMER_SOURCE_UPDATED = "customer.source.updated"
    CUSTOMER_SOURCE_DELETED = "customer.source.deleted"
    CUSTOMER_SOURCE_EXPIRING = "customer.source.expiring"
    
    # Tax events
    TAX_RATE_CREATED = "tax_rate.created"
    TAX_RATE_UPDATED = "tax_rate.updated"
    TAX_SETTINGS_UPDATED = "tax.settings.updated"
    
    # Billing portal events
    BILLING_PORTAL_SESSION_CREATED = "billing_portal.session.created"
    BILLING_PORTAL_CONFIGURATION_CREATED = "billing_portal.configuration.created"
    BILLING_PORTAL_CONFIGURATION_UPDATED = "billing_portal.configuration.updated"
    
    # Refund events
    CHARGE_REFUND_UPDATED = "charge.refund.updated"
    
    # Dispute events
    CHARGE_DISPUTE_UPDATED = "charge.dispute.updated"
    CHARGE_DISPUTE_FUNDS_REINSTATED = "charge.dispute.funds_reinstated"
    CHARGE_DISPUTE_FUNDS_WITHDRAWN = "charge.dispute.funds_withdrawn"
    
    # Custom events
    CUSTOM_EVENT = "custom.event"

class WebhookProcessingStatus(str, Enum):
    """Webhook processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    DUPLICATE = "duplicate"

class EventSeverity(str, Enum):
    """Event severity level."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class WebhookContext:
    """Context for webhook processing."""
    event_id: str
    event_type: WebhookEventType
    event_data: Dict[str, Any]
    stripe_event: stripe.Event
    db_session: Session
    redis_client: redis.Redis
    tenant_id: Optional[str] = None
    customer_id: Optional[str] = None
    subscription_id: Optional[str] = None
    invoice_id: Optional[str] = None
    payment_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    processing_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "tenant_id": self.tenant_id,
            "customer_id": self.customer_id,
            "subscription_id": self.subscription_id,
            "invoice_id": self.invoice_id,
            "payment_id": self.payment_id,
            "processing_start": self.processing_start.isoformat(),
            "metadata": self.metadata
        }

@dataclass
class ProcessingResult:
    """Result of webhook processing."""
    success: bool
    status: WebhookProcessingStatus
    message: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False
    data: Optional[Dict[str, Any]] = None
    actions_taken: List[str] = field(default_factory=list)
    duration_ms: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "message": self.message,
            "error": self.error,
            "retryable": self.retryable,
            "actions_taken": self.actions_taken,
            "duration_ms": self.duration_ms
        }

# ============================================================================
# DECORATORS & UTILITIES
# ============================================================================

def webhook_handler(event_type: WebhookEventType):
    """Decorator to register webhook handlers."""
    def decorator(func: Callable[[WebhookContext], Awaitable[ProcessingResult]]):
        @wraps(func)
        async def wrapper(context: WebhookContext) -> ProcessingResult:
            try:
                # Update metrics
                WEBHOOK_EVENTS_IN_FLIGHT.inc()
                
                # Process the event
                result = await func(context)
                
                # Record metrics
                WEBHOOK_EVENTS_TOTAL.labels(
                    event_type=event_type.value,
                    status=result.status.value
                ).inc()
                
                return result
                
            except Exception as e:
                # Record error metrics
                WEBHOOK_PROCESSING_ERRORS.labels(
                    event_type=event_type.value,
                    error_type=type(e).__name__
                ).inc()
                
                logger.error(f"Unhandled error in {event_type} handler: {e}")
                
                return ProcessingResult(
                    success=False,
                    status=WebhookProcessingStatus.FAILED,
                    error=f"Unhandled error: {str(e)}",
                    retryable=True
                )
            finally:
                WEBHOOK_EVENTS_IN_FLIGHT.dec()
        
        wrapper.event_type = event_type
        return wrapper
    return decorator

def with_event_validation(func: Callable):
    """Decorator for event validation."""
    @wraps(func)
    async def wrapper(context: WebhookContext) -> ProcessingResult:
        try:
            # Validate event structure
            if not context.event_data:
                raise ValidationError("Event data is empty")
            
            # Extract common identifiers
            await _extract_identifiers(context)
            
            # Validate required fields based on event type
            await _validate_event_data(context)
            
            # Call the original handler
            return await func(context)
            
        except ValidationError as e:
            logger.warning(f"Event validation failed: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Validation error: {str(e)}",
                retryable=False
            )
    
    return wrapper

async def _extract_identifiers(context: WebhookContext):
    """Extract identifiers from event data."""
    data = context.event_data
    
    # Extract customer ID
    if 'customer' in data:
        context.customer_id = data['customer']
    
    # Extract subscription ID
    if 'subscription' in data:
        context.subscription_id = data['subscription']
    
    # Extract invoice ID
    if 'invoice' in data:
        context.invoice_id = data['invoice']
    
    # Extract payment intent ID
    if 'payment_intent' in data:
        context.payment_id = data['payment_intent']
    
    # Extract charge ID
    if 'charge' in data:
        context.payment_id = data['charge']
    
    # Try to find tenant ID from metadata
    if data.get('metadata') and 'tenant_id' in data['metadata']:
        context.tenant_id = data['metadata']['tenant_id']
    
    # If not found, try to look up from database
    if context.customer_id and not context.tenant_id:
        tenant = context.db_session.query(Tenant).filter(
            Tenant.stripe_customer_id == context.customer_id
        ).first()
        if tenant:
            context.tenant_id = str(tenant.id)

async def _validate_event_data(context: WebhookContext):
    """Validate event data based on type."""
    event_type = context.event_type
    data = context.event_data
    
    # Define validation rules for each event type
    validation_rules = {
        WebhookEventType.PAYMENT_INTENT_SUCCEEDED: ['id', 'amount', 'customer'],
        WebhookEventType.CUSTOMER_SUBSCRIPTION_CREATED: ['id', 'customer', 'items'],
        WebhookEventType.INVOICE_PAID: ['id', 'customer', 'amount_paid'],
        # Add more rules as needed
    }
    
    if event_type in validation_rules:
        required_fields = validation_rules[event_type]
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            raise ValidationError(
                f"Missing required fields for {event_type}: {missing_fields}"
            )

# ============================================================================
# IDEMPOTENCY HANDLER
# ============================================================================

class IdempotencyHandler:
    """Handles idempotency for webhook events."""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = 86400  # 24 hours
    
    async def check_and_store(self, event_id: str, handler_key: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check if event was already processed and store idempotency key."""
        try:
            # Create unique key for this event + handler combination
            idempotency_key = f"idempotency:{event_id}:{handler_key}"
            
            # Check if already processed
            existing = await self.redis.get(idempotency_key)
            
            if existing:
                IDEMPOTENCY_CHECKS.labels(result="duplicate").inc()
                return True, json.loads(existing)
            
            # Store processing marker with short TTL for in-progress events
            processing_key = f"processing:{event_id}:{handler_key}"
            processing_lock = await self.redis.setnx(
                processing_key, 
                json.dumps({"status": "processing", "timestamp": datetime.now(timezone.utc).isoformat()}),
                ex=300  # 5 minutes TTL for processing lock
            )
            
            if not processing_lock:
                # Another process is already handling this event
                IDEMPOTENCY_CHECKS.labels(result="processing").inc()
                return True, None
            
            IDEMPOTENCY_CHECKS.labels(result="new").inc()
            return False, None
            
        except Exception as e:
            logger.error(f"Idempotency check failed: {e}")
            # On Redis failure, proceed with processing but log warning
            return False, None
    
    async def store_result(self, event_id: str, handler_key: str, result: Dict[str, Any]):
        """Store processing result for idempotency."""
        try:
            idempotency_key = f"idempotency:{event_id}:{handler_key}"
            
            # Store result with longer TTL
            await self.redis.setex(
                idempotency_key,
                self.ttl,
                json.dumps({
                    **result,
                    "stored_at": datetime.now(timezone.utc).isoformat()
                })
            )
            
            # Clean up processing lock
            processing_key = f"processing:{event_id}:{handler_key}"
            await self.redis.delete(processing_key)
            
        except Exception as e:
            logger.error(f"Failed to store idempotency result: {e}")
    
    async def cleanup_old_entries(self):
        """Clean up old idempotency entries."""
        # Redis will auto-expire based on TTL, but we can also clean up manually
        pass

# ============================================================================
# WEBHOOK EVENT STORAGE
# ============================================================================

class EventStorage:
    """Stores webhook events for audit and replay."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    async def store_event(self, context: WebhookContext, result: ProcessingResult) -> str:
        """Store webhook event in database."""
        try:
            # Create webhook event record
            webhook_event = WebhookEvent(
                id=context.event_id,
                event_type=context.event_type.value,
                event_data=context.event_data,
                processing_status=result.status.value,
                success=result.success,
                error_message=result.error,
                tenant_id=context.tenant_id,
                customer_id=context.customer_id,
                subscription_id=context.subscription_id,
                invoice_id=context.invoice_id,
                payment_id=context.payment_id,
                processed_at=datetime.now(timezone.utc),
                metadata=context.metadata,
                actions_taken=result.actions_taken
            )
            
            self.db.add(webhook_event)
            
            # Also store in StripeEvents table for Stripe-specific tracking
            stripe_event = StripeEvent(
                stripe_event_id=context.stripe_event.id,
                webhook_event_id=context.event_id,
                type=context.stripe_event.type,
                api_version=context.stripe_event.api_version,
                created=datetime.fromtimestamp(context.stripe_event.created),
                data=context.stripe_event.data,
                request_id=context.stripe_event.request,
                is_livemode=context.stripe_event.livemode
            )
            
            self.db.add(stripe_event)
            
            # Create audit log entry
            audit_log = AuditLog(
                tenant_id=context.tenant_id,
                event_type=f"webhook.{context.event_type.value}",
                event_action="RECEIVED",
                resource_type="webhook_event",
                resource_id=context.event_id,
                details_before=None,
                details_after=context.event_data,
                ip_address="stripe.com",
                user_agent="Stripe-Webhook",
                status_code=200 if result.success else 500,
                error_message=result.error
            )
            
            self.db.add(audit_log)
            
            self.db.commit()
            
            return str(webhook_event.id)
            
        except IntegrityError:
            # Event already stored (idempotency at database level)
            self.db.rollback()
            logger.info(f"Event {context.event_id} already stored in database")
            return context.event_id
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to store webhook event: {e}")
            raise
    
    async def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored webhook event."""
        event = self.db.query(WebhookEvent).filter(
            WebhookEvent.id == event_id
        ).first()
        
        if event:
            return {
                "id": event.id,
                "event_type": event.event_type,
                "processing_status": event.processing_status,
                "success": event.success,
                "error_message": event.error_message,
                "processed_at": event.processed_at,
                "actions_taken": event.actions_taken
            }
        
        return None
    
    async def get_events_for_tenant(self, tenant_id: str, 
                                   limit: int = 100) -> List[Dict[str, Any]]:
        """Get webhook events for a specific tenant."""
        events = self.db.query(WebhookEvent).filter(
            WebhookEvent.tenant_id == tenant_id
        ).order_by(WebhookEvent.processed_at.desc()).limit(limit).all()
        
        return [
            {
                "id": event.id,
                "event_type": event.event_type,
                "processing_status": event.processing_status,
                "success": event.success,
                "processed_at": event.processed_at
            }
            for event in events
        ]

# ============================================================================
# PAYMENT EVENT HANDLERS
# ============================================================================

class PaymentEventHandlers:
    """Handlers for payment-related webhook events."""
    
    @staticmethod
    @webhook_handler(WebhookEventType.PAYMENT_INTENT_SUCCEEDED)
    @with_event_validation
    @async_retry(max_retries=3, delay=1, backoff=2)
    async def handle_payment_succeeded(context: WebhookContext) -> ProcessingResult:
        """Handle successful payment intent."""
        with WEBHOOK_EVENT_DURATION.labels(
            event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED.value
        ).time():
            try:
                event_data = context.event_data
                actions = []
                
                # 1. Update payment record in database
                payment = context.db_session.query(Payment).filter(
                    Payment.transaction_id == event_data['id']
                ).first()
                
                if payment:
                    payment.status = 'completed'
                    payment.processed_at = datetime.now(timezone.utc)
                    payment.metadata = {
                        **payment.metadata,
                        'stripe_payment_intent': event_data
                    }
                    actions.append("updated_payment_record")
                else:
                    # Create new payment record
                    payment = Payment(
                        tenant_id=context.tenant_id,
                        invoice_id=context.invoice_id,
                        amount=event_data['amount'] / 100,  # Convert from cents
                        currency=event_data['currency'],
                        status='completed',
                        payment_method=event_data.get('payment_method_type', 'card'),
                        transaction_id=event_data['id'],
                        metadata={'stripe_payment_intent': event_data}
                    )
                    context.db_session.add(payment)
                    actions.append("created_payment_record")
                
                # 2. Update invoice if associated
                if context.invoice_id:
                    invoice = context.db_session.query(Invoice).filter(
                        Invoice.id == context.invoice_id
                    ).first()
                    
                    if invoice:
                        invoice.status = 'paid'
                        invoice.amount_paid = event_data['amount'] / 100
                        invoice.paid_at = datetime.now(timezone.utc)
                        actions.append("updated_invoice_status")
                
                # 3. Update subscription if payment is for subscription
                if context.subscription_id:
                    subscription = context.db_session.query(Subscription).filter(
                        Subscription.id == context.subscription_id
                    ).first()
                    
                    if subscription:
                        subscription.status = 'active'
                        subscription.updated_at = datetime.now(timezone.utc)
                        actions.append("updated_subscription_status")
                
                # 4. Send payment confirmation email
                await _send_payment_confirmation(context, event_data)
                actions.append("sent_confirmation_email")
                
                # 5. Update metrics
                MetricsCollector.increment('payments_succeeded')
                
                context.db_session.commit()
                
                return ProcessingResult(
                    success=True,
                    status=WebhookProcessingStatus.COMPLETED,
                    message="Payment successfully processed",
                    actions_taken=actions,
                    data={
                        "payment_id": str(payment.id) if payment.id else None,
                        "amount": event_data['amount'] / 100,
                        "currency": event_data['currency']
                    }
                )
                
            except Exception as e:
                context.db_session.rollback()
                logger.error(f"Failed to process payment succeeded event: {e}")
                raise ProcessingError(f"Payment processing failed: {str(e)}")
    
    @staticmethod
    @webhook_handler(WebhookEventType.PAYMENT_INTENT_FAILED)
    @with_event_validation
    async def handle_payment_failed(context: WebhookContext) -> ProcessingResult:
        """Handle failed payment intent."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Update payment record
            payment = context.db_session.query(Payment).filter(
                Payment.transaction_id == event_data['id']
            ).first()
            
            if payment:
                payment.status = 'failed'
                payment.error_message = event_data.get('last_payment_error', {}).get('message')
                payment.metadata = {
                    **payment.metadata,
                    'failure_reason': event_data.get('last_payment_error'),
                    'stripe_payment_intent': event_data
                }
                actions.append("updated_payment_record")
            
            # 2. Update invoice status
            if context.invoice_id:
                invoice = context.db_session.query(Invoice).filter(
                    Invoice.id == context.invoice_id
                ).first()
                
                if invoice:
                    invoice.status = 'payment_failed'
                    actions.append("updated_invoice_status")
            
            # 3. Send payment failure notification
            await _send_payment_failure_notification(context, event_data)
            actions.append("sent_failure_notification")
            
            # 4. Trigger dunning process
            if context.tenant_id and context.invoice_id:
                await _trigger_dunning_process(context.tenant_id, context.invoice_id)
                actions.append("triggered_dunning")
            
            # 5. Update metrics
            MetricsCollector.increment('payments_failed')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Payment failure processed",
                actions_taken=actions,
                data={
                    "failure_reason": event_data.get('last_payment_error', {}).get('message')
                }
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process payment failed event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Payment failure processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.CHARGE_SUCCEEDED)
    @with_event_validation
    async def handle_charge_succeeded(context: WebhookContext) -> ProcessingResult:
        """Handle successful charge."""
        try:
            event_data = context.event_data
            actions = ["processed_charge_success"]
            
            # Charge succeeded events are usually handled by payment_intent.succeeded
            # This handler is for additional charge-specific logic
            
            # Update metrics
            MetricsCollector.increment('charges_succeeded')
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Charge successfully processed",
                actions_taken=actions
            )
            
        except Exception as e:
            logger.error(f"Failed to process charge succeeded event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Charge processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.CHARGE_FAILED)
    @with_event_validation
    async def handle_charge_failed(context: WebhookContext) -> ProcessingResult:
        """Handle failed charge."""
        try:
            event_data = context.event_data
            actions = ["processed_charge_failure"]
            
            # Additional charge failure logic
            failure_reason = event_data.get('failure_message', 'Unknown')
            logger.warning(f"Charge failed: {failure_reason}")
            
            # Update metrics
            MetricsCollector.increment('charges_failed')
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Charge failure processed",
                actions_taken=actions,
                data={"failure_reason": failure_reason}
            )
            
        except Exception as e:
            logger.error(f"Failed to process charge failed event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Charge failure processing failed: {str(e)}",
                retryable=True
            )

# ============================================================================
# SUBSCRIPTION EVENT HANDLERS
# ============================================================================

class SubscriptionEventHandlers:
    """Handlers for subscription-related webhook events."""
    
    @staticmethod
    @webhook_handler(WebhookEventType.CUSTOMER_SUBSCRIPTION_CREATED)
    @with_event_validation
    @async_retry(max_retries=3, delay=1, backoff=2)
    async def handle_subscription_created(context: WebhookContext) -> ProcessingResult:
        """Handle new subscription creation."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Check if subscription already exists
            existing = context.db_session.query(Subscription).filter(
                Subscription.id == event_data['id']
            ).first()
            
            if existing:
                return ProcessingResult(
                    success=True,
                    status=WebhookProcessingStatus.DUPLICATE,
                    message="Subscription already exists",
                    actions_taken=["duplicate_detected"]
                )
            
            # 2. Extract plan information
            items = event_data.get('items', {}).get('data', [])
            if not items:
                raise ValidationError("No items in subscription")
            
            primary_item = items[0]
            plan_id = primary_item.get('price', {}).get('id')
            
            if not plan_id:
                raise ValidationError("No plan ID in subscription")
            
            # 3. Create subscription record
            subscription = Subscription(
                id=event_data['id'],
                tenant_id=context.tenant_id,
                plan_id=plan_id,
                status=event_data['status'],
                billing_period='monthly',  # Extract from plan
                quantity=primary_item.get('quantity', 1),
                current_period_start=datetime.fromtimestamp(event_data['current_period_start']),
                current_period_end=datetime.fromtimestamp(event_data['current_period_end']),
                cancel_at_period_end=event_data.get('cancel_at_period_end', False),
                trial_start=datetime.fromtimestamp(event_data['trial_start']) if event_data.get('trial_start') else None,
                trial_end=datetime.fromtimestamp(event_data['trial_end']) if event_data.get('trial_end') else None,
                metadata=event_data.get('metadata', {})
            )
            
            context.db_session.add(subscription)
            actions.append("created_subscription_record")
            
            # 4. Update tenant plan
            if context.tenant_id:
                tenant = context.db_session.query(Tenant).filter(
                    Tenant.id == context.tenant_id
                ).first()
                
                if tenant:
                    tenant.plan_tier = plan_id  # Or extract tier from plan metadata
                    tenant.updated_at = datetime.now(timezone.utc)
                    actions.append("updated_tenant_plan")
            
            # 5. Send welcome email for new subscription
            await _send_subscription_welcome_email(context, event_data)
            actions.append("sent_welcome_email")
            
            # 6. Update metrics
            MetricsCollector.increment('subscriptions_created')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Subscription created successfully",
                actions_taken=actions,
                data={
                    "subscription_id": subscription.id,
                    "plan_id": plan_id,
                    "status": subscription.status
                }
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process subscription created event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Subscription creation processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.CUSTOMER_SUBSCRIPTION_UPDATED)
    @with_event_validation
    async def handle_subscription_updated(context: WebhookContext) -> ProcessingResult:
        """Handle subscription updates."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Find existing subscription
            subscription = context.db_session.query(Subscription).filter(
                Subscription.id == event_data['id']
            ).first()
            
            if not subscription:
                logger.warning(f"Subscription {event_data['id']} not found, creating...")
                # Fall back to creation handler
                return await SubscriptionEventHandlers.handle_subscription_created(context)
            
            # 2. Update subscription fields
            previous_status = subscription.status
            subscription.status = event_data['status']
            subscription.cancel_at_period_end = event_data.get('cancel_at_period_end', False)
            
            # Update period dates if changed
            if 'current_period_start' in event_data:
                subscription.current_period_start = datetime.fromtimestamp(
                    event_data['current_period_start']
                )
            
            if 'current_period_end' in event_data:
                subscription.current_period_end = datetime.fromtimestamp(
                    event_data['current_period_end']
                )
            
            # Update quantity if changed
            items = event_data.get('items', {}).get('data', [])
            if items:
                primary_item = items[0]
                subscription.quantity = primary_item.get('quantity', 1)
            
            subscription.updated_at = datetime.now(timezone.utc)
            subscription.metadata = event_data.get('metadata', {})
            
            actions.append("updated_subscription_record")
            
            # 3. Handle status changes
            if previous_status != subscription.status:
                actions.append(f"status_changed_from_{previous_status}_to_{subscription.status}")
                
                # Handle specific status transitions
                if subscription.status == 'canceled':
                    await _handle_subscription_cancellation(context, subscription)
                    actions.append("processed_cancellation")
                elif subscription.status == 'past_due':
                    await _handle_subscription_past_due(context, subscription)
                    actions.append("processed_past_due")
            
            # 4. Update metrics
            MetricsCollector.increment('subscriptions_updated')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Subscription updated successfully",
                actions_taken=actions,
                data={
                    "subscription_id": subscription.id,
                    "status": subscription.status,
                    "previous_status": previous_status
                }
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process subscription updated event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Subscription update processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.CUSTOMER_SUBSCRIPTION_DELETED)
    @with_event_validation
    async def handle_subscription_deleted(context: WebhookContext) -> ProcessingResult:
        """Handle subscription deletion."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Find subscription
            subscription = context.db_session.query(Subscription).filter(
                Subscription.id == event_data['id']
            ).first()
            
            if not subscription:
                return ProcessingResult(
                    success=True,
                    status=WebhookProcessingStatus.SKIPPED,
                    message="Subscription not found, may have been already deleted",
                    actions_taken=["subscription_not_found"]
                )
            
            # 2. Update subscription status
            previous_status = subscription.status
            subscription.status = 'canceled'
            subscription.canceled_at = datetime.now(timezone.utc)
            subscription.updated_at = datetime.now(timezone.utc)
            
            actions.append("marked_subscription_canceled")
            
            # 3. Update tenant plan
            if context.tenant_id:
                tenant = context.db_session.query(Tenant).filter(
                    Tenant.id == context.tenant_id
                ).first()
                
                if tenant:
                    tenant.plan_tier = 'free'  # Or appropriate downgrade
                    tenant.updated_at = datetime.now(timezone.utc)
                    actions.append("downgraded_tenant_plan")
            
            # 4. Send cancellation confirmation
            await _send_subscription_cancellation_email(context, subscription)
            actions.append("sent_cancellation_email")
            
            # 5. Update metrics
            MetricsCollector.increment('subscriptions_canceled')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Subscription deletion processed",
                actions_taken=actions,
                data={
                    "subscription_id": subscription.id,
                    "previous_status": previous_status,
                    "canceled_at": subscription.canceled_at.isoformat()
                }
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process subscription deleted event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Subscription deletion processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.CUSTOMER_SUBSCRIPTION_TRIAL_WILL_END)
    @with_event_validation
    async def handle_trial_will_end(context: WebhookContext) -> ProcessingResult:
        """Handle trial ending notification (3 days before)."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Send trial ending reminder
            await _send_trial_ending_reminder(context, event_data)
            actions.append("sent_trial_ending_reminder")
            
            # 2. Update subscription metadata
            subscription = context.db_session.query(Subscription).filter(
                Subscription.id == event_data['id']
            ).first()
            
            if subscription:
                subscription.metadata = {
                    **subscription.metadata,
                    'trial_ending_notification_sent': datetime.now(timezone.utc).isoformat()
                }
                actions.append("updated_subscription_metadata")
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Trial ending notification processed",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process trial will end event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Trial ending notification processing failed: {str(e)}",
                retryable=False  # Non-retryable as it's time-sensitive
            )

# ============================================================================
# INVOICE EVENT HANDLERS
# ============================================================================

class InvoiceEventHandlers:
    """Handlers for invoice-related webhook events."""
    
    @staticmethod
    @webhook_handler(WebhookEventType.INVOICE_CREATED)
    @with_event_validation
    async def handle_invoice_created(context: WebhookContext) -> ProcessingResult:
        """Handle invoice creation."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Check if invoice already exists
            existing = context.db_session.query(Invoice).filter(
                Invoice.id == event_data['id']
            ).first()
            
            if existing:
                return ProcessingResult(
                    success=True,
                    status=WebhookProcessingStatus.DUPLICATE,
                    message="Invoice already exists",
                    actions_taken=["duplicate_detected"]
                )
            
            # 2. Create invoice record
            invoice = Invoice(
                id=event_data['id'],
                tenant_id=context.tenant_id,
                invoice_number=event_data.get('number'),
                currency=event_data['currency'],
                amount_due=event_data['amount_due'] / 100,
                amount_paid=event_data.get('amount_paid', 0) / 100,
                amount_remaining=event_data.get('amount_remaining', event_data['amount_due']) / 100,
                status=event_data['status'],
                billing_reason=event_data.get('billing_reason'),
                due_date=datetime.fromtimestamp(event_data['due_date']) if event_data.get('due_date') else None,
                metadata=event_data.get('metadata', {})
            )
            
            context.db_session.add(invoice)
            actions.append("created_invoice_record")
            
            # 3. Update metrics
            MetricsCollector.increment('invoices_created')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Invoice created successfully",
                actions_taken=actions,
                data={
                    "invoice_id": invoice.id,
                    "invoice_number": invoice.invoice_number,
                    "amount_due": invoice.amount_due
                }
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process invoice created event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Invoice creation processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.INVOICE_FINALIZED)
    @with_event_validation
    async def handle_invoice_finalized(context: WebhookContext) -> ProcessingResult:
        """Handle invoice finalization."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Update invoice status
            invoice = context.db_session.query(Invoice).filter(
                Invoice.id == event_data['id']
            ).first()
            
            if invoice:
                invoice.status = event_data['status']
                invoice.finalized_at = datetime.now(timezone.utc)
                invoice.hosted_invoice_url = event_data.get('hosted_invoice_url')
                invoice.invoice_pdf = event_data.get('invoice_pdf')
                actions.append("updated_invoice_status")
                
                # 2. Send invoice to customer
                await _send_invoice_to_customer(context, invoice)
                actions.append("sent_invoice_to_customer")
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Invoice finalized successfully",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process invoice finalized event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Invoice finalization processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.INVOICE_PAID)
    @with_event_validation
    async def handle_invoice_paid(context: WebhookContext) -> ProcessingResult:
        """Handle invoice payment."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Update invoice status
            invoice = context.db_session.query(Invoice).filter(
                Invoice.id == event_data['id']
            ).first()
            
            if invoice:
                invoice.status = 'paid'
                invoice.amount_paid = event_data.get('amount_paid', 0) / 100
                invoice.amount_remaining = event_data.get('amount_remaining', 0) / 100
                invoice.paid_at = datetime.now(timezone.utc)
                actions.append("updated_invoice_status")
            
            # 2. Update subscription if applicable
            if context.subscription_id and event_data.get('billing_reason') == 'subscription_create':
                subscription = context.db_session.query(Subscription).filter(
                    Subscription.id == context.subscription_id
                ).first()
                
                if subscription:
                    subscription.status = 'active'
                    subscription.updated_at = datetime.now(timezone.utc)
                    actions.append("activated_subscription")
            
            # 3. Update metrics
            MetricsCollector.increment('invoices_paid')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Invoice payment processed successfully",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process invoice paid event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Invoice payment processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.INVOICE_PAYMENT_FAILED)
    @with_event_validation
    async def handle_invoice_payment_failed(context: WebhookContext) -> ProcessingResult:
        """Handle invoice payment failure."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Update invoice status
            invoice = context.db_session.query(Invoice).filter(
                Invoice.id == event_data['id']
            ).first()
            
            if invoice:
                invoice.status = 'payment_failed'
                invoice.updated_at = datetime.now(timezone.utc)
                actions.append("updated_invoice_status")
                
                # 2. Send payment failure notification
                await _send_invoice_payment_failure_notification(context, invoice)
                actions.append("sent_payment_failure_notification")
                
                # 3. Trigger dunning process
                await _trigger_dunning_process(context.tenant_id, invoice.id)
                actions.append("triggered_dunning_process")
            
            # 4. Update metrics
            MetricsCollector.increment('invoice_payments_failed')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Invoice payment failure processed",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process invoice payment failed event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Invoice payment failure processing failed: {str(e)}",
                retryable=True
            )

# ============================================================================
# CUSTOMER EVENT HANDLERS
# ============================================================================

class CustomerEventHandlers:
    """Handlers for customer-related webhook events."""
    
    @staticmethod
    @webhook_handler(WebhookEventType.CUSTOMER_CREATED)
    @with_event_validation
    async def handle_customer_created(context: WebhookContext) -> ProcessingResult:
        """Handle new customer creation."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Check if customer already exists in our system
            customer = context.db_session.query(Customer).filter(
                Customer.stripe_customer_id == event_data['id']
            ).first()
            
            if not customer:
                # Create customer record if we have tenant context
                if context.tenant_id:
                    customer = Customer(
                        tenant_id=context.tenant_id,
                        stripe_customer_id=event_data['id'],
                        email=event_data.get('email'),
                        name=event_data.get('name'),
                        phone=event_data.get('phone'),
                        address=event_data.get('address'),
                        metadata=event_data.get('metadata', {})
                    )
                    context.db_session.add(customer)
                    actions.append("created_customer_record")
            
            # 2. Update tenant with Stripe customer ID if needed
            if context.tenant_id and not context.customer_id:
                tenant = context.db_session.query(Tenant).filter(
                    Tenant.id == context.tenant_id
                ).first()
                
                if tenant and not tenant.stripe_customer_id:
                    tenant.stripe_customer_id = event_data['id']
                    actions.append("updated_tenant_customer_id")
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Customer created successfully",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process customer created event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Customer creation processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.CUSTOMER_UPDATED)
    @with_event_validation
    async def handle_customer_updated(context: WebhookContext) -> ProcessingResult:
        """Handle customer updates."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Update customer record
            customer = context.db_session.query(Customer).filter(
                Customer.stripe_customer_id == event_data['id']
            ).first()
            
            if customer:
                customer.email = event_data.get('email', customer.email)
                customer.name = event_data.get('name', customer.name)
                customer.phone = event_data.get('phone', customer.phone)
                customer.address = event_data.get('address', customer.address)
                customer.metadata = event_data.get('metadata', customer.metadata)
                customer.updated_at = datetime.now(timezone.utc)
                actions.append("updated_customer_record")
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Customer updated successfully",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process customer updated event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Customer update processing failed: {str(e)}",
                retryable=True
            )

# ============================================================================
# REFUND & DISPUTE HANDLERS
# ============================================================================

class RefundDisputeHandlers:
    """Handlers for refund and dispute events."""
    
    @staticmethod
    @webhook_handler(WebhookEventType.CHARGE_REFUNDED)
    @with_event_validation
    async def handle_charge_refunded(context: WebhookContext) -> ProcessingResult:
        """Handle charge refund."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Find the charge/payment
            charge_id = event_data['id']
            payment = context.db_session.query(Payment).filter(
                Payment.transaction_id == charge_id
            ).first()
            
            if payment:
                # 2. Create refund record
                refund = Refund(
                    payment_id=payment.id,
                    tenant_id=context.tenant_id,
                    amount=event_data.get('amount_refunded', 0) / 100,
                    currency=event_data['currency'],
                    status='succeeded',
                    reason='requested_by_customer',  # Extract from event if available
                    transaction_id=f"ref_{charge_id}",
                    metadata={'stripe_charge': event_data}
                )
                context.db_session.add(refund)
                actions.append("created_refund_record")
                
                # 3. Update payment status
                payment.status = 'refunded'
                payment.updated_at = datetime.now(timezone.utc)
                actions.append("updated_payment_status")
            
            # 4. Update metrics
            MetricsCollector.increment('refunds_processed')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Charge refund processed successfully",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process charge refunded event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Charge refund processing failed: {str(e)}",
                retryable=True
            )
    
    @staticmethod
    @webhook_handler(WebhookEventType.CHARGE_DISPUTE_CREATED)
    @with_event_validation
    async def handle_dispute_created(context: WebhookContext) -> ProcessingResult:
        """Handle dispute creation."""
        try:
            event_data = context.event_data
            actions = []
            
            # 1. Log dispute for investigation
            dispute_id = event_data['id']
            charge_id = event_data['charge']
            
            logger.warning(f"Dispute created: {dispute_id} for charge: {charge_id}")
            actions.append("logged_dispute")
            
            # 2. Find related payment
            payment = context.db_session.query(Payment).filter(
                Payment.transaction_id == charge_id
            ).first()
            
            if payment:
                # Update payment metadata with dispute info
                payment.metadata = {
                    **payment.metadata,
                    'dispute_created': {
                        'dispute_id': dispute_id,
                        'reason': event_data.get('reason'),
                        'status': event_data.get('status'),
                        'amount': event_data.get('amount', 0) / 100,
                        'created_at': datetime.fromtimestamp(event_data['created']).isoformat()
                    }
                }
                actions.append("updated_payment_metadata")
            
            # 3. Notify internal team
            await _notify_dispute_team(context, event_data)
            actions.append("notified_dispute_team")
            
            # 4. Update metrics
            MetricsCollector.increment('disputes_created')
            
            context.db_session.commit()
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Dispute creation processed",
                actions_taken=actions
            )
            
        except Exception as e:
            context.db_session.rollback()
            logger.error(f"Failed to process dispute created event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Dispute creation processing failed: {str(e)}",
                retryable=True
            )

# ============================================================================
# TAX & COMPLIANCE HANDLERS
# ============================================================================

class TaxComplianceHandlers:
    """Handlers for tax and compliance events."""
    
    @staticmethod
    @webhook_handler(WebhookEventType.TAX_RATE_CREATED)
    @with_event_validation
    async def handle_tax_rate_created(context: WebhookContext) -> ProcessingResult:
        """Handle new tax rate creation."""
        try:
            event_data = context.event_data
            actions = []
            
            # Store tax rate for reference
            # This could be used to keep local tax rates in sync with Stripe
            
            actions.append("processed_tax_rate_creation")
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Tax rate creation processed",
                actions_taken=actions
            )
            
        except Exception as e:
            logger.error(f"Failed to process tax rate created event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Tax rate creation processing failed: {str(e)}",
                retryable=True
            )

# ============================================================================
# BILLING PORTAL HANDLERS
# ============================================================================

class BillingPortalHandlers:
    """Handlers for billing portal events."""
    
    @staticmethod
    @webhook_handler(WebhookEventType.BILLING_PORTAL_SESSION_CREATED)
    @with_event_validation
    async def handle_billing_portal_session_created(context: WebhookContext) -> ProcessingResult:
        """Handle billing portal session creation."""
        try:
            event_data = context.event_data
            actions = []
            
            # Log portal session for audit trail
            logger.info(f"Billing portal session created for customer: {event_data.get('customer')}")
            actions.append("logged_portal_session")
            
            return ProcessingResult(
                success=True,
                status=WebhookProcessingStatus.COMPLETED,
                message="Billing portal session creation processed",
                actions_taken=actions
            )
            
        except Exception as e:
            logger.error(f"Failed to process billing portal session event: {e}")
            return ProcessingResult(
                success=False,
                status=WebhookProcessingStatus.FAILED,
                error=f"Billing portal session processing failed: {str(e)}",
                retryable=False
            )

# ============================================================================
# MAIN WEBHOOK PROCESSOR
# ============================================================================

class WebhookProcessor:
    """Main webhook processor orchestrating all handlers."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.redis_client = redis.Redis.from_url(REDIS_CONFIG['url'])
        self.idempotency_handler = IdempotencyHandler(self.redis_client)
        self.event_storage = EventStorage(db_session)
        
        # Register all handlers
        self.handlers = self._register_handlers()
    
    def _register_handlers(self) -> Dict[WebhookEventType, Callable]:
        """Register all webhook handlers."""
        handlers = {}
        
        # Payment handlers
        handlers.update({
            WebhookEventType.PAYMENT_INTENT_SUCCEEDED: PaymentEventHandlers.handle_payment_succeeded,
            WebhookEventType.PAYMENT_INTENT_FAILED: PaymentEventHandlers.handle_payment_failed,
            WebhookEventType.CHARGE_SUCCEEDED: PaymentEventHandlers.handle_charge_succeeded,
            WebhookEventType.CHARGE_FAILED: PaymentEventHandlers.handle_charge_failed,
        })
        
        # Subscription handlers
        handlers.update({
            WebhookEventType.CUSTOMER_SUBSCRIPTION_CREATED: SubscriptionEventHandlers.handle_subscription_created,
            WebhookEventType.CUSTOMER_SUBSCRIPTION_UPDATED: SubscriptionEventHandlers.handle_subscription_updated,
            WebhookEventType.CUSTOMER_SUBSCRIPTION_DELETED: SubscriptionEventHandlers.handle_subscription_deleted,
            WebhookEventType.CUSTOMER_SUBSCRIPTION_TRIAL_WILL_END: SubscriptionEventHandlers.handle_trial_will_end,
        })
        
        # Invoice handlers
        handlers.update({
            WebhookEventType.INVOICE_CREATED: InvoiceEventHandlers.handle_invoice_created,
            WebhookEventType.INVOICE_FINALIZED: InvoiceEventHandlers.handle_invoice_finalized,
            WebhookEventType.INVOICE_PAID: InvoiceEventHandlers.handle_invoice_paid,
            WebhookEventType.INVOICE_PAYMENT_FAILED: InvoiceEventHandlers.handle_invoice_payment_failed,
        })
        
        # Customer handlers
        handlers.update({
            WebhookEventType.CUSTOMER_CREATED: CustomerEventHandlers.handle_customer_created,
            WebhookEventType.CUSTOMER_UPDATED: CustomerEventHandlers.handle_customer_updated,
        })
        
        # Refund & Dispute handlers
        handlers.update({
            WebhookEventType.CHARGE_REFUNDED: RefundDisputeHandlers.handle_charge_refunded,
            WebhookEventType.CHARGE_DISPUTE_CREATED: RefundDisputeHandlers.handle_dispute_created,
        })
        
        # Tax handlers
        handlers.update({
            WebhookEventType.TAX_RATE_CREATED: TaxComplianceHandlers.handle_tax_rate_created,
        })
        
        # Billing portal handlers
        handlers.update({
            WebhookEventType.BILLING_PORTAL_SESSION_CREATED: BillingPortalHandlers.handle_billing_portal_session_created,
        })
        
        return handlers
    
    async def process_webhook(self, payload: bytes, 
                            signature: str,
                            webhook_secret: str) -> ProcessingResult:
        """Process incoming webhook from Stripe."""
        try:
            # 1. Verify webhook signature
            event = self._verify_signature(payload, signature, webhook_secret)
            
            # 2. Create processing context
            context = WebhookContext(
                event_id=event.id,
                event_type=WebhookEventType(event.type),
                event_data=event.data.object.to_dict(),
                stripe_event=event,
                db_session=self.db,
                redis_client=self.redis_client,
                metadata={
                    "api_version": event.api_version,
                    "livemode": event.livemode,
                    "request_id": event.request,
                    "created": datetime.fromtimestamp(event.created).isoformat()
                }
            )
            
            # 3. Get handler for event type
            handler = self.handlers.get(context.event_type)
            
            if not handler:
                logger.info(f"No handler for event type: {context.event_type}")
                return ProcessingResult(
                    success=True,
                    status=WebhookProcessingStatus.SKIPPED,
                    message=f"No handler for event type: {context.event_type}"
                )
            
            # 4. Check idempotency
            handler_key = handler.__name__
            is_duplicate, previous_result = await self.idempotency_handler.check_and_store(
                context.event_id, handler_key
            )
            
            if is_duplicate:
                if previous_result:
                    logger.info(f"Event {context.event_id} already processed")
                    return ProcessingResult(
                        success=previous_result.get('success', True),
                        status=WebhookProcessingStatus.DUPLICATE,
                        message="Event already processed",
                        data=previous_result
                    )
                else:
                    # Another process is currently handling this event
                    await asyncio.sleep(1)  # Wait a bit and retry
                    return await self.process_webhook(payload, signature, webhook_secret)
            
            # 5. Process the event
            processing_start = datetime.now(timezone.utc)
            result = await handler(context)
            processing_end = datetime.now(timezone.utc)
            
            # Calculate duration
            result.duration_ms = (processing_end - processing_start).total_seconds() * 1000
            
            # 6. Store processing result for idempotency
            await self.idempotency_handler.store_result(
                context.event_id,
                handler_key,
                {
                    "success": result.success,
                    "status": result.status.value,
                    "data": result.data,
                    "processed_at": processing_end.isoformat()
                }
            )
            
            # 7. Store event in database
            await self.event_storage.store_event(context, result)
            
            # 8. Log processing result
            self._log_processing_result(context, result)
            
            return result
            
        except SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {e}")
            raise SecurityError("Invalid webhook signature")
            
        except StripeError as e:
            logger.error(f"Stripe error processing webhook: {e}")
            raise WebhookError(f"Stripe error: {str(e)}")
            
        except Exception as e:
            logger.error(f"Unexpected error processing webhook: {e}")
            raise WebhookError(f"Processing failed: {str(e)}")
    
    def _verify_signature(self, payload: bytes, 
                         signature: str, 
                         webhook_secret: str) -> stripe.Event:
        """Verify Stripe webhook signature."""
        try:
            return stripe.Webhook.construct_event(
                payload, signature, webhook_secret
            )
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {e}")
            raise
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            raise
    
    def _log_processing_result(self, context: WebhookContext, result: ProcessingResult):
        """Log webhook processing result."""
        log_data = {
            "event_id": context.event_id,
            "event_type": context.event_type.value,
            "tenant_id": context.tenant_id,
            "success": result.success,
            "status": result.status.value,
            "duration_ms": result.duration_ms,
            "actions_taken": result.actions_taken
        }
        
        if result.success:
            logger.info(f"Webhook processed successfully: {log_data}")
        else:
            logger.error(f"Webhook processing failed: {log_data}, error: {result.error}")
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on webhook processor."""
        try:
            # Check Redis connection
            redis_ok = await self.redis_client.ping()
            
            # Check database connection
            db_ok = self.db.execute("SELECT 1").scalar() == 1
            
            # Get processing statistics
            stats = await self._get_processing_statistics()
            
            return {
                "status": "healthy" if redis_ok and db_ok else "unhealthy",
                "redis": "connected" if redis_ok else "disconnected",
                "database": "connected" if db_ok else "disconnected",
                "statistics": stats
            }
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }
    
    async def _get_processing_statistics(self) -> Dict[str, Any]:
        """Get webhook processing statistics."""
        try:
            # Get counts by status
            status_counts = {}
            
            # Get recent processing times
            recent_events = self.db.query(WebhookEvent).order_by(
                WebhookEvent.processed_at.desc()
            ).limit(100).all()
            
            processing_times = []
            for event in recent_events:
                # Calculate processing time if available in metadata
                pass
            
            return {
                "total_events_processed": len(recent_events),
                "status_counts": status_counts,
                "recent_processing_times": processing_times
            }
            
        except Exception as e:
            logger.error(f"Failed to get processing statistics: {e}")
            return {}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _send_payment_confirmation(context: WebhookContext, payment_data: Dict[str, Any]):
    """Send payment confirmation email."""
    # Implementation depends on email service
    pass

async def _send_payment_failure_notification(context: WebhookContext, payment_data: Dict[str, Any]):
    """Send payment failure notification."""
    pass

async def _trigger_dunning_process(tenant_id: str, invoice_id: str):
    """Trigger dunning process for failed payment."""
    pass

async def _send_subscription_welcome_email(context: WebhookContext, subscription_data: Dict[str, Any]):
    """Send welcome email for new subscription."""
    pass

async def _handle_subscription_cancellation(context: WebhookContext, subscription):
    """Handle subscription cancellation logic."""
    pass

async def _handle_subscription_past_due(context: WebhookContext, subscription):
    """Handle subscription past due logic."""
    pass

async def _send_subscription_cancellation_email(context: WebhookContext, subscription):
    """Send subscription cancellation email."""
    pass

async def _send_trial_ending_reminder(context: WebhookContext, subscription_data: Dict[str, Any]):
    """Send trial ending reminder."""
    pass

async def _send_invoice_to_customer(context: WebhookContext, invoice):
    """Send invoice to customer."""
    pass

async def _send_invoice_payment_failure_notification(context: WebhookContext, invoice):
    """Send invoice payment failure notification."""
    pass

async def _notify_dispute_team(context: WebhookContext, dispute_data: Dict[str, Any]):
    """Notify internal team about dispute."""
    pass

# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Main processor
    "WebhookProcessor",
    
    # Event types
    "WebhookEventType",
    
    # Data models
    "WebhookContext",
    "ProcessingResult",
    
    # Handlers
    "PaymentEventHandlers",
    "SubscriptionEventHandlers",
    "InvoiceEventHandlers",
    "CustomerEventHandlers",
    "RefundDisputeHandlers",
    "TaxComplianceHandlers",
    "BillingPortalHandlers",
    
    # Utilities
    "IdempotencyHandler",
    "EventStorage",
    
    # Decorators
    "webhook_handler",
    "with_event_validation",
    
    # Exceptions
    "WebhookError",
    "ProcessingError",
    "IdempotencyError",
    "SecurityError"
]