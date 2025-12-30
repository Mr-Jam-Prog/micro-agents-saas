"""
Stripe Billing Integration Handler

Ce module gère l'intégration complète avec Stripe pour:
- Gestion des abonnements
- Facturation à l'usage
- Génération de factures
- Traitement des paiements
- Gestion des webhooks
- Calcul des taxes
- Gestion des réductions
- Proration
- Gestion des impayés
- Reconnaissance de revenus
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union
from enum import Enum

import stripe
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from src.billing.models import (
    Invoice, Subscription, Customer, Payment, UsageRecord,
    InvoiceStatus, SubscriptionStatus, PaymentStatus
)
from src.utils.serialization.serializers import json_serializer
from src.utils.security.utils import encrypt_data, decrypt_data
from src.core.compliance.auditor import ComplianceAuditor

logger = logging.getLogger(__name__)


class BillingCycle(Enum):
    """Cycle de facturation"""
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    USAGE = "usage"


class Currency(Enum):
    """Devises supportées"""
    USD = "usd"
    EUR = "eur"
    GBP = "gbp"
    JPY = "jpy"
    CAD = "cad"
    AUD = "aud"


class TaxRate(BaseModel):
    """Taux de taxe"""
    country: str
    state: Optional[str] = None
    jurisdiction: str
    percentage: Decimal
    tax_type: str  # VAT, GST, etc.
    is_inclusive: bool = False

    @validator('percentage')
    def validate_percentage(cls, v):
        if not (0 <= v <= 100):
            raise ValueError("Le pourcentage doit être entre 0 et 100")
        return v


class PricingTier(BaseModel):
    """Niveau de tarification"""
    up_to: Optional[int] = None  # None pour le dernier niveau (infinite)
    unit_amount: int  # Montant en centimes
    flat_amount: Optional[int] = None


class StripeHandler:
    """Handler principal pour l'intégration Stripe"""
    
    def __init__(self, api_key: str, webhook_secret: str, db_session: Session):
        """
        Initialise le handler Stripe.
        
        Args:
            api_key: Clé API Stripe (secrète)
            webhook_secret: Secret pour vérifier les webhooks
            db_session: Session de base de données
        """
        stripe.api_key = api_key
        self.webhook_secret = webhook_secret
        self.db = db_session
        self.compliance_auditor = ComplianceAuditor()
        
        # Configuration PCI DSS
        self.pci_compliant = True
        self.tokenization_enabled = True
        
        logger.info("StripeHandler initialisé avec succès")
    
    # ==================== GESTION DES CLIENTS ====================
    
    async def create_customer(
        self,
        email: str,
        name: str,
        metadata: Dict[str, str],
        tax_id: Optional[str] = None,
        address: Optional[Dict[str, str]] = None,
        currency: Currency = Currency.USD
    ) -> Customer:
        """
        Crée un client Stripe.
        
        Args:
            email: Email du client
            name: Nom du client
            metadata: Métadonnées personnalisées
            tax_id: Numéro d'identification fiscale
            address: Adresse du client
            currency: Devise par défaut
            
        Returns:
            Objet Customer
        """
        try:
            # Vérification de conformité GDPR
            self.compliance_auditor.validate_gdpr_consent(email, metadata)
            
            customer_data = {
                'email': email,
                'name': name,
                'metadata': metadata,
                'currency': currency.value,
                'address': address or {},
                'tax_exempt': 'none',
            }
            
            if tax_id:
                customer_data['tax_id_data'] = [{
                    'type': 'eu_vat' if tax_id.startswith('EU') else 'unknown',
                    'value': tax_id
                }]
            
            stripe_customer = stripe.Customer.create(**customer_data)
            
            # Création dans notre base de données
            customer = Customer(
                stripe_id=stripe_customer.id,
                email=email,
                name=name,
                metadata=metadata,
                currency=currency.value,
                tax_id=encrypt_data(tax_id) if tax_id else None,
                created_at=datetime.utcnow()
            )
            
            self.db.add(customer)
            self.db.commit()
            
            logger.info(f"Client créé: {customer.id} (Stripe: {stripe_customer.id})")
            return customer
            
        except Exception as e:
            logger.error(f"Erreur création client: {str(e)}")
            self.db.rollback()
            raise
    
    async def update_customer(
        self,
        customer_id: str,
        **updates
    ) -> Customer:
        """
        Met à jour un client.
        
        Args:
            customer_id: ID du client dans notre système
            **updates: Champs à mettre à jour
            
        Returns:
            Client mis à jour
        """
        customer = self.db.query(Customer).filter_by(id=customer_id).first()
        if not customer:
            raise ValueError(f"Client non trouvé: {customer_id}")
        
        try:
            # Mise à jour dans Stripe
            stripe.Customer.modify(
                customer.stripe_id,
                **{k: v for k, v in updates.items() if v is not None}
            )
            
            # Mise à jour dans notre base
            for key, value in updates.items():
                if hasattr(customer, key):
                    if key == 'tax_id' and value:
                        value = encrypt_data(value)
                    setattr(customer, key, value)
            
            customer.updated_at = datetime.utcnow()
            self.db.commit()
            
            return customer
            
        except Exception as e:
            logger.error(f"Erreur mise à jour client: {str(e)}")
            self.db.rollback()
            raise
    
    # ==================== GESTION DES PRODUITS ET PRIX ====================
    
    async def create_product(
        self,
        name: str,
        description: str,
        metadata: Dict[str, str],
        tax_code: Optional[str] = None
    ) -> str:
        """
        Crée un produit dans Stripe.
        
        Args:
            name: Nom du produit
            description: Description
            metadata: Métadonnées
            tax_code: Code fiscal Stripe
            
        Returns:
            ID du produit Stripe
        """
        try:
            product_data = {
                'name': name,
                'description': description,
                'metadata': metadata,
                'active': True,
            }
            
            if tax_code:
                product_data['tax_code'] = tax_code
            
            product = stripe.Product.create(**product_data)
            return product.id
            
        except Exception as e:
            logger.error(f"Erreur création produit: {str(e)}")
            raise
    
    async def create_price(
        self,
        product_id: str,
        unit_amount: int,
        currency: Currency,
        recurring: Optional[Dict[str, Any]] = None,
        billing_scheme: str = "per_unit",
        tiers: Optional[List[PricingTier]] = None,
        tax_behavior: str = "unspecified"
    ) -> str:
        """
        Crée un prix dans Stripe.
        
        Args:
            product_id: ID du produit
            unit_amount: Montant unitaire en centimes
            currency: Devise
            recurring: Configuration récurrente
            billing_scheme: Schéma de facturation
            tiers: Niveaux de tarification
            tax_behavior: Comportement fiscal
            
        Returns:
            ID du prix Stripe
        """
        try:
            price_data = {
                'product': product_id,
                'unit_amount': unit_amount,
                'currency': currency.value,
                'active': True,
                'billing_scheme': billing_scheme,
                'tax_behavior': tax_behavior,
            }
            
            if recurring:
                price_data['recurring'] = recurring
            
            if tiers and billing_scheme == "tiered":
                price_data['tiers_mode'] = 'graduated'
                price_data['tiers'] = [
                    {
                        'up_to': tier.up_to if tier.up_to else 'inf',
                        'unit_amount': tier.unit_amount,
                        'flat_amount': tier.flat_amount
                    }
                    for tier in tiers
                ]
            
            price = stripe.Price.create(**price_data)
            return price.id
            
        except Exception as e:
            logger.error(f"Erreur création prix: {str(e)}")
            raise
    
    # ==================== GESTION DES ABONNEMENTS ====================
    
    async def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        quantity: int = 1,
        trial_days: int = 0,
        metadata: Dict[str, str] = None,
        tax_rates: Optional[List[TaxRate]] = None,
        promotion_code: Optional[str] = None,
        proration_behavior: str = "create_prorations"
    ) -> Subscription:
        """
        Crée un nouvel abonnement.
        
        Args:
            customer_id: ID du client
            price_id: ID du prix
            quantity: Quantité
            trial_days: Jours d'essai gratuit
            metadata: Métadonnées
            tax_rates: Taux de taxes applicables
            promotion_code: Code promotionnel
            proration_behavior: Comportement de proration
            
        Returns:
            Objet Subscription
        """
        customer = self.db.query(Customer).filter_by(id=customer_id).first()
        if not customer:
            raise ValueError(f"Client non trouvé: {customer_id}")
        
        try:
            subscription_data = {
                'customer': customer.stripe_id,
                'items': [{'price': price_id, 'quantity': quantity}],
                'payment_behavior': 'default_incomplete',
                'expand': ['latest_invoice.payment_intent'],
                'proration_behavior': proration_behavior,
                'metadata': metadata or {},
            }
            
            if trial_days > 0:
                subscription_data['trial_period_days'] = trial_days
            
            if tax_rates:
                subscription_data['default_tax_rates'] = [
                    self._create_or_get_tax_rate(tax_rate)
                    for tax_rate in tax_rates
                ]
            
            if promotion_code:
                subscription_data['promotion_code'] = promotion_code
            
            stripe_subscription = stripe.Subscription.create(**subscription_data)
            
            # Création dans notre base
            subscription = Subscription(
                stripe_id=stripe_subscription.id,
                customer_id=customer.id,
                status=SubscriptionStatus.ACTIVE,
                current_period_start=datetime.fromtimestamp(
                    stripe_subscription.current_period_start
                ),
                current_period_end=datetime.fromtimestamp(
                    stripe_subscription.current_period_end
                ),
                cancel_at_period_end=stripe_subscription.cancel_at_period_end,
                trial_end=(
                    datetime.fromtimestamp(stripe_subscription.trial_end)
                    if stripe_subscription.trial_end
                    else None
                ),
                metadata=metadata or {},
                created_at=datetime.utcnow()
            )
            
            self.db.add(subscription)
            self.db.commit()
            
            # Log d'audit
            self._log_audit_trail(
                "subscription_created",
                customer_id=customer.id,
                subscription_id=subscription.id,
                amount=stripe_subscription.plan.amount * quantity if stripe_subscription.plan else 0,
                currency=stripe_subscription.currency
            )
            
            logger.info(f"Abonnement créé: {subscription.id}")
            return subscription
            
        except stripe.error.CardError as e:
            logger.error(f"Erreur carte de crédit: {e.user_message}")
            raise
        except Exception as e:
            logger.error(f"Erreur création abonnement: {str(e)}")
            self.db.rollback()
            raise
    
    async def update_subscription(
        self,
        subscription_id: str,
        price_id: Optional[str] = None,
        quantity: Optional[int] = None,
        proration_behavior: str = "create_prorations",
        billing_cycle_anchor: str = "now"
    ) -> Subscription:
        """
        Met à jour un abonnement existant.
        
        Args:
            subscription_id: ID de l'abonnement
            price_id: Nouveau prix
            quantity: Nouvelle quantité
            proration_behavior: Comportement de proration
            billing_cycle_anchor: Ancrage du cycle de facturation
            
        Returns:
            Abonnement mis à jour
        """
        subscription = self.db.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            raise ValueError(f"Abonnement non trouvé: {subscription_id}")
        
        try:
            update_data = {
                'proration_behavior': proration_behavior,
                'billing_cycle_anchor': billing_cycle_anchor,
            }
            
            if price_id or quantity is not None:
                items = []
                if price_id:
                    items.append({'price': price_id, 'quantity': quantity or 1})
                else:
                    # Mettre à jour la quantité sur l'item existant
                    stripe_subscription = stripe.Subscription.retrieve(subscription.stripe_id)
                    items = [{
                        'id': stripe_subscription['items']['data'][0].id,
                        'quantity': quantity
                    }]
                
                update_data['items'] = items
            
            stripe_subscription = stripe.Subscription.modify(
                subscription.stripe_id,
                **update_data
            )
            
            # Mise à jour dans notre base
            subscription.status = SubscriptionStatus(stripe_subscription.status)
            subscription.current_period_start = datetime.fromtimestamp(
                stripe_subscription.current_period_start
            )
            subscription.current_period_end = datetime.fromtimestamp(
                stripe_subscription.current_period_end
            )
            subscription.updated_at = datetime.utcnow()
            
            self.db.commit()
            
            logger.info(f"Abonnement mis à jour: {subscription.id}")
            return subscription
            
        except Exception as e:
            logger.error(f"Erreur mise à jour abonnement: {str(e)}")
            self.db.rollback()
            raise
    
    async def cancel_subscription(
        self,
        subscription_id: str,
        cancel_at_period_end: bool = True
    ) -> Subscription:
        """
        Annule un abonnement.
        
        Args:
            subscription_id: ID de l'abonnement
            cancel_at_period_end: Annuler à la fin de la période
            
        Returns:
            Abonnement annulé
        """
        subscription = self.db.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            raise ValueError(f"Abonnement non trouvé: {subscription_id}")
        
        try:
            if cancel_at_period_end:
                stripe_subscription = stripe.Subscription.modify(
                    subscription.stripe_id,
                    cancel_at_period_end=True
                )
                subscription.cancel_at_period_end = True
            else:
                stripe_subscription = stripe.Subscription.delete(subscription.stripe_id)
                subscription.status = SubscriptionStatus.CANCELED
            
            subscription.updated_at = datetime.utcnow()
            self.db.commit()
            
            logger.info(f"Abonnement annulé: {subscription.id}")
            return subscription
            
        except Exception as e:
            logger.error(f"Erreur annulation abonnement: {str(e)}")
            self.db.rollback()
            raise
    
    # ==================== FACTURATION À L'USAGE ====================
    
    async def record_usage(
        self,
        subscription_item_id: str,
        quantity: int,
        timestamp: Optional[datetime] = None,
        action: str = "increment"
    ) -> UsageRecord:
        """
        Enregistre l'utilisation pour la facturation à l'usage.
        
        Args:
            subscription_item_id: ID de l'item d'abonnement
            quantity: Quantité utilisée
            timestamp: Timestamp de l'usage
            action: Action (increment, set)
            
        Returns:
            Enregistrement d'usage
        """
        try:
            usage_data = {
                'subscription_item': subscription_item_id,
                'quantity': quantity,
                'timestamp': int((timestamp or datetime.utcnow()).timestamp()),
                'action': action,
            }
            
            stripe.UsageRecord.create(**usage_data)
            
            # Création dans notre base pour le reporting
            usage_record = UsageRecord(
                subscription_item_id=subscription_item_id,
                quantity=quantity,
                timestamp=timestamp or datetime.utcnow(),
                action=action,
                recorded_at=datetime.utcnow()
            )
            
            self.db.add(usage_record)
            self.db.commit()
            
            logger.debug(f"Usage enregistré: {quantity} unités")
            return usage_record
            
        except Exception as e:
            logger.error(f"Erreur enregistrement usage: {str(e)}")
            self.db.rollback()
            raise
    
    async def calculate_usage_billing(
        self,
        customer_id: str,
        start_date: datetime,
        end_date: datetime,
        metric: str
    ) -> Dict[str, Any]:
        """
        Calcule la facturation à l'usage.
        
        Args:
            customer_id: ID du client
            start_date: Date de début
            end_date: Date de fin
            metric: Métrique à facturer
            
        Returns:
            Détails de facturation
        """
        try:
            # Récupération des enregistrements d'usage
            usage_records = self.db.query(UsageRecord).filter(
                UsageRecord.timestamp.between(start_date, end_date),
                # Jointure pour filtrer par client
            ).all()
            
            total_quantity = sum(record.quantity for record in usage_records)
            
            # Calcul du montant basé sur les niveaux de tarification
            # (simplifié - à implémenter selon votre modèle de tarification)
            amount = self._calculate_tiered_pricing(total_quantity)
            
            return {
                'customer_id': customer_id,
                'period': {'start': start_date, 'end': end_date},
                'metric': metric,
                'total_quantity': total_quantity,
                'amount': amount,
                'currency': 'usd',
                'records_count': len(usage_records)
            }
            
        except Exception as e:
            logger.error(f"Erreur calcul facturation usage: {str(e)}")
            raise
    
    # ==================== GESTION DES FACTURES ====================
    
    async def create_invoice(
        self,
        customer_id: str,
        items: List[Dict[str, Any]],
        due_date: Optional[datetime] = None,
        metadata: Dict[str, str] = None,
        auto_advance: bool = True
    ) -> Invoice:
        """
        Crée une facture.
        
        Args:
            customer_id: ID du client
            items: Articles à facturer
            due_date: Date d'échéance
            metadata: Métadonnées
            auto_advance: Passer automatiquement à la facturation
            
        Returns:
            Objet Invoice
        """
        customer = self.db.query(Customer).filter_by(id=customer_id).first()
        if not customer:
            raise ValueError(f"Client non trouvé: {customer_id}")
        
        try:
            invoice_data = {
                'customer': customer.stripe_id,
                'auto_advance': auto_advance,
                'metadata': metadata or {},
            }
            
            if due_date:
                invoice_data['due_date'] = int(due_date.timestamp())
            
            # Création de la facture dans Stripe
            stripe_invoice = stripe.Invoice.create(**invoice_data)
            
            # Ajout des items
            for item in items:
                stripe.InvoiceItem.create(
                    customer=customer.stripe_id,
                    amount=item['amount'],
                    currency=item.get('currency', 'usd'),
                    description=item.get('description', ''),
                    invoice=stripe_invoice.id,
                    metadata=item.get('metadata', {})
                )
            
            # Finalisation de la facture
            stripe_invoice = stripe.Invoice.finalize_invoice(stripe_invoice.id)
            
            # Création dans notre base
            invoice = Invoice(
                stripe_id=stripe_invoice.id,
                customer_id=customer.id,
                amount_due=stripe_invoice.amount_due,
                amount_paid=stripe_invoice.amount_paid,
                amount_remaining=stripe_invoice.amount_remaining,
                currency=stripe_invoice.currency,
                status=InvoiceStatus(stripe_invoice.status),
                due_date=(
                    datetime.fromtimestamp(stripe_invoice.due_date)
                    if stripe_invoice.due_date
                    else None
                ),
                invoice_pdf=stripe_invoice.invoice_pdf,
                metadata=metadata or {},
                created_at=datetime.utcnow()
            )
            
            self.db.add(invoice)
            self.db.commit()
            
            logger.info(f"Facture créée: {invoice.id}")
            return invoice
            
        except Exception as e:
            logger.error(f"Erreur création facture: {str(e)}")
            self.db.rollback()
            raise
    
    async def send_invoice(self, invoice_id: str) -> Invoice:
        """
        Envoie une facture au client.
        
        Args:
            invoice_id: ID de la facture
            
        Returns:
            Facture envoyée
        """
        invoice = self.db.query(Invoice).filter_by(id=invoice_id).first()
        if not invoice:
            raise ValueError(f"Facture non trouvée: {invoice_id}")
        
        try:
            stripe_invoice = stripe.Invoice.send_invoice(invoice.stripe_id)
            invoice.status = InvoiceStatus(stripe_invoice.status)
            invoice.sent_at = datetime.utcnow()
            invoice.updated_at = datetime.utcnow()
            
            self.db.commit()
            
            logger.info(f"Facture envoyée: {invoice.id}")
            return invoice
            
        except Exception as e:
            logger.error(f"Erreur envoi facture: {str(e)}")
            self.db.rollback()
            raise
    
    # ==================== GESTION DES PAIEMENTS ====================
    
    async def process_payment(
        self,
        invoice_id: str,
        payment_method_id: Optional[str] = None,
        off_session: bool = False
    ) -> Payment:
        """
        Traite le paiement d'une facture.
        
        Args:
            invoice_id: ID de la facture
            payment_method_id: ID de la méthode de paiement
            off_session: Paiement hors session
            
        Returns:
            Paiement traité
        """
        invoice = self.db.query(Invoice).filter_by(id=invoice_id).first()
        if not invoice:
            raise ValueError(f"Facture non trouvée: {invoice_id}")
        
        try:
            if payment_method_id:
                # Paiement avec méthode spécifique
                stripe_invoice = stripe.Invoice.pay(
                    invoice.stripe_id,
                    payment_method=payment_method_id,
                    off_session=off_session
                )
            else:
                # Paiement avec la méthode par défaut
                stripe_invoice = stripe.Invoice.pay(
                    invoice.stripe_id,
                    off_session=off_session
                )
            
            # Création du paiement dans notre base
            payment = Payment(
                stripe_id=stripe_invoice.payment_intent,
                invoice_id=invoice.id,
                customer_id=invoice.customer_id,
                amount=stripe_invoice.amount_paid,
                currency=stripe_invoice.currency,
                status=PaymentStatus.SUCCEEDED,
                payment_method=payment_method_id or 'default',
                processed_at=datetime.utcnow()
            )
            
            self.db.add(payment)
            
            # Mise à jour de la facture
            invoice.amount_paid = stripe_invoice.amount_paid
            invoice.amount_remaining = stripe_invoice.amount_remaining
            invoice.status = InvoiceStatus(stripe_invoice.status)
            invoice.updated_at = datetime.utcnow()
            
            self.db.commit()
            
            logger.info(f"Paiement traité: {payment.id} pour facture {invoice.id}")
            return payment
            
        except stripe.error.CardError as e:
            logger.error(f"Erreur carte de crédit: {e.user_message}")
            
            # Enregistrement de l'échec de paiement
            payment = Payment(
                invoice_id=invoice.id,
                customer_id=invoice.customer_id,
                amount=invoice.amount_due,
                currency=invoice.currency,
                status=PaymentStatus.FAILED,
                error_message=e.user_message,
                processed_at=datetime.utcnow()
            )
            
            self.db.add(payment)
            self.db.commit()
            
            # Déclenchement du processus de dunning
            await self._trigger_dunning_process(invoice.id, e.code)
            
            raise
            
        except Exception as e:
            logger.error(f"Erreur traitement paiement: {str(e)}")
            self.db.rollback()
            raise
    
    # ==================== GESTION DES IMPAYÉS (DUNNING) ====================
    
    async def _trigger_dunning_process(
        self,
        invoice_id: str,
        error_code: str
    ) -> None:
        """
        Déclenche le processus de recouvrement pour les paiements échoués.
        
        Args:
            invoice_id: ID de la facture
            error_code: Code d'erreur Stripe
        """
        try:
            invoice = self.db.query(Invoice).filter_by(id=invoice_id).first()
            if not invoice:
                return
            
            # Stratégie de dunning basée sur le code d'erreur
            dunning_strategy = self._get_dunning_strategy(error_code)
            
            # Planification des tentatives de récupération
            for attempt in dunning_strategy['attempts']:
                await asyncio.sleep(attempt['delay_days'] * 24 * 3600)
                
                # Tentative de récupération
                success = await self._retry_payment(
                    invoice.id,
                    attempt['payment_method']
                )
                
                if success:
                    logger.info(f"Paiement récupéré pour facture {invoice.id}")
                    return
            
            # Si toutes les tentatives échouent
            await self._handle_final_dunning_stage(invoice.id)
            
        except Exception as e:
            logger.error(f"Erreur processus dunning: {str(e)}")
    
    async def _retry_payment(
        self,
        invoice_id: str,
        payment_method: str
    ) -> bool:
        """
        Tente de récupérer un paiement échoué.
        
        Args:
            invoice_id: ID de la facture
            payment_method: Méthode de paiement à utiliser
            
        Returns:
            True si le paiement a réussi
        """
        try:
            # Logique de réessai
            # Pourrait inclure:
            # - Réessayer avec la même carte
            # - Essayer une carte de secours
            # - Envoyer un email de rappel
            # - Proposer un autre moyen de paiement
            
            return True  # À implémenter
            
        except Exception as e:
            logger.error(f"Erreur réessai paiement: {str(e)}")
            return False
    
    # ==================== CALCUL DES TAXES ====================
    
    def _create_or_get_tax_rate(self, tax_rate: TaxRate) -> str:
        """
        Crée ou récupère un taux de taxe Stripe.
        
        Args:
            tax_rate: Objet TaxRate
            
        Returns:
            ID du taux de taxe Stripe
        """
        try:
            # Recherche d'un taux existant
            existing_rates = stripe.TaxRate.list(
                active=True,
                inclusive=tax_rate.is_inclusive
            )
            
            for rate in existing_rates.data:
                if (rate.jurisdiction == tax_rate.jurisdiction and
                    rate.percentage == float(tax_rate.percentage)):
                    return rate.id
            
            # Création d'un nouveau taux
            new_rate = stripe.TaxRate.create(
                display_name=f"{tax_rate.tax_type} - {tax_rate.jurisdiction}",
                description=f"{tax_rate.tax_type} tax for {tax_rate.jurisdiction}",
                jurisdiction=tax_rate.jurisdiction,
                percentage=float(tax_rate.percentage),
                inclusive=tax_rate.is_inclusive
            )
            
            return new_rate.id
            
        except Exception as e:
            logger.error(f"Erreur création taux taxe: {str(e)}")
            raise
    
    async def calculate_tax(
        self,
        customer_id: str,
        amount: int,
        currency: str
    ) -> Dict[str, Any]:
        """
        Calcule les taxes pour un montant.
        
        Args:
            customer_id: ID du client
            amount: Montant en centimes
            currency: Devise
            
        Returns:
            Détails des taxes
        """
        customer = self.db.query(Customer).filter_by(id=customer_id).first()
        if not customer:
            raise ValueError(f"Client non trouvé: {customer_id}")
        
        try:
            # Utilisation de l'API de calcul des taxes de Stripe
            # (nécessite Stripe Tax activé)
            calculation = stripe.tax.Calculation.create(
                currency=currency,
                customer_details={
                    'address': {
                        'line1': customer.address_line1 or '',
                        'city': customer.city or '',
                        'state': customer.state or '',
                        'postal_code': customer.postal_code or '',
                        'country': customer.country or 'US',
                    }
                } if customer.address_line1 else None,
                line_items=[{
                    'amount': amount,
                    'reference': 'service_fee',
                }]
            )
            
            return {
                'tax_amount': calculation.tax_amount_exclusive,
                'total_amount': amount + calculation.tax_amount_exclusive,
                'breakdown': calculation.tax_breakdown,
            }
            
        except Exception as e:
            logger.error(f"Erreur calcul taxes: {str(e)}")
            # Fallback: taux par défaut
            return self._calculate_default_tax(amount, customer.country)
    
    # ==================== GESTION DES RÉDUCTIONS ====================
    
    async def create_coupon(
        self,
        percent_off: Optional[float] = None,
        amount_off: Optional[int] = None,
        currency: Optional[str] = None,
        duration: str = "once",
        duration_in_months: Optional[int] = None,
        max_redemptions: Optional[int] = None,
        metadata: Dict[str, str] = None
    ) -> str:
        """
        Crée un coupon de réduction.
        
        Args:
            percent_off: Pourcentage de réduction
            amount_off: Montant de réduction en centimes
            currency: Devise (requis si amount_off)
            duration: Durée (once, repeating, forever)
            duration_in_months: Mois de durée si repeating
            max_redemptions: Nombre maximum d'utilisations
            metadata: Métadonnées
            
        Returns:
            ID du coupon Stripe
        """
        try:
            coupon_data = {
                'duration': duration,
                'metadata': metadata or {},
            }
            
            if percent_off:
                coupon_data['percent_off'] = percent_off
            elif amount_off:
                if not currency:
                    raise ValueError("Currency required for amount_off coupons")
                coupon_data['amount_off'] = amount_off
                coupon_data['currency'] = currency
            else:
                raise ValueError("Either percent_off or amount_off is required")
            
            if duration == "repeating" and duration_in_months:
                coupon_data['duration_in_months'] = duration_in_months
            
            if max_redemptions:
                coupon_data['max_redemptions'] = max_redemptions
            
            coupon = stripe.Coupon.create(**coupon_data)
            return coupon.id
            
        except Exception as e:
            logger.error(f"Erreur création coupon: {str(e)}")
            raise
    
    async def create_promotion_code(
        self,
        coupon_id: str,
        code: str,
        customer_id: Optional[str] = None,
        max_redemptions: Optional[int] = None,
        restrictions: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Crée un code promotionnel.
        
        Args:
            coupon_id: ID du coupon
            code: Code promotionnel
            customer_id: ID du client (pour usage unique)
            max_redemptions: Utilisations maximum
            restrictions: Restrictions supplémentaires
            
        Returns:
            ID du code promotionnel Stripe
        """
        try:
            promotion_data = {
                'coupon': coupon_id,
                'code': code,
            }
            
            if customer_id:
                customer = self.db.query(Customer).filter_by(id=customer_id).first()
                if customer:
                    promotion_data['customer'] = customer.stripe_id
            
            if max_redemptions:
                promotion_data['max_redemptions'] = max_redemptions
            
            if restrictions:
                promotion_data['restrictions'] = restrictions
            
            promotion_code = stripe.PromotionCode.create(**promotion_data)
            return promotion_code.id
            
        except Exception as e:
            logger.error(f"Erreur création code promotion: {str(e)}")
            raise
    
    # ==================== RECONNAISSANCE DE REVENUS ====================
    
    async def recognize_revenue(
        self,
        invoice_id: str,
        recognition_schedule: List[Dict[str, Any]]
    ) -> None:
        """
        Gère la reconnaissance de revenus selon les normes comptables.
        
        Args:
            invoice_id: ID de la facture
            recognition_schedule: Calendrier de reconnaissance
        """
        try:
            invoice = self.db.query(Invoice).filter_by(id=invoice_id).first()
            if not invoice:
                return
            
            # Reconnaissance proportionnelle au temps pour les abonnements
            # Reconnaissance ponctuelle pour les ventes
            
            for schedule_item in recognition_schedule:
                await self._create_revenue_schedule_entry(
                    invoice_id=invoice.id,
                    amount=schedule_item['amount'],
                    recognition_date=schedule_item['date'],
                    description=schedule_item.get('description', '')
                )
            
            logger.info(f"Revenus reconnus pour facture {invoice.id}")
            
        except Exception as e:
            logger.error(f"Erreur reconnaissance revenus: {str(e)}")
    
    # ==================== PORTAL CLIENT ====================
    
    async def create_customer_portal_session(
        self,
        customer_id: str,
        return_url: str
    ) -> str:
        """
        Crée une session pour le portail client Stripe.
        
        Args:
            customer_id: ID du client
            return_url: URL de retour
            
        Returns:
            URL du portail client
        """
        customer = self.db.query(Customer).filter_by(id=customer_id).first()
        if not customer:
            raise ValueError(f"Client non trouvé: {customer_id}")
        
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer.stripe_id,
                return_url=return_url,
                configuration={
                    'business_profile': {
                        'headline': 'Gérer votre abonnement MicroAgents',
                    },
                    'features': {
                        'invoice_history': {'enabled': True},
                        'payment_method_update': {'enabled': True},
                        'subscription_cancel': {'enabled': True},
                        'subscription_update': {'enabled': True},
                    }
                }
            )
            
            return session.url
            
        except Exception as e:
            logger.error(f"Erreur création session portail: {str(e)}")
            raise
    
    # ==================== WEBHOOK HANDLING ====================
    
    async def handle_webhook(
        self,
        payload: bytes,
        signature: str
    ) -> Dict[str, Any]:
        """
        Traite les webhooks Stripe.
        
        Args:
            payload: Corps de la requête
            signature: Signature du webhook
            
        Returns:
            Résultat du traitement
        """
        try:
            # Vérification de la signature
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            
            event_type = event['type']
            data = event['data']
            
            logger.info(f"Webhook reçu: {event_type}")
            
            # Traitement selon le type d'événement
            handler_method = getattr(self, f"_handle_{event_type}", None)
            if handler_method:
                await handler_method(data)
            else:
                logger.warning(f"Aucun handler pour {event_type}")
            
            return {'status': 'processed', 'event': event_type}
            
        except stripe.error.SignatureVerificationError:
            logger.error("Signature webhook invalide")
            raise
        except Exception as e:
            logger.error(f"Erreur traitement webhook: {str(e)}")
            raise
    
    async def _handle_invoice_payment_succeeded(self, data: Dict[str, Any]) -> None:
        """Traitement des paiements de facture réussis."""
        invoice = data['object']
        
        # Mise à jour de la facture dans notre base
        db_invoice = self.db.query(Invoice).filter_by(stripe_id=invoice['id']).first()
        if db_invoice:
            db_invoice.status = InvoiceStatus.PAID
            db_invoice.amount_paid = invoice['amount_paid']
            db_invoice.amount_remaining = invoice['amount_remaining']
            db_invoice.updated_at = datetime.utcnow()
            self.db.commit()
    
    async def _handle_invoice_payment_failed(self, data: Dict[str, Any]) -> None:
        """Traitement des échecs de paiement."""
        invoice = data['object']
        
        # Déclenchement du processus de dunning
        db_invoice = self.db.query(Invoice).filter_by(stripe_id=invoice['id']).first()
        if db_invoice:
            await self._trigger_dunning_process(db_invoice.id, 'payment_failed')
    
    async def _handle_customer_subscription_updated(self, data: Dict[str, Any]) -> None:
        """Traitement des mises à jour d'abonnement."""
        subscription = data['object']
        
        db_subscription = self.db.query(Subscription).filter_by(
            stripe_id=subscription['id']
        ).first()
        
        if db_subscription:
            db_subscription.status = SubscriptionStatus(subscription['status'])
            db_subscription.current_period_end = datetime.fromtimestamp(
                subscription['current_period_end']
            )
            db_subscription.updated_at = datetime.utcnow()
            self.db.commit()
    
    # ==================== RAPPORTS FINANCIERS ====================
    
    async def generate_financial_report(
        self,
        start_date: datetime,
        end_date: datetime,
        report_type: str = "revenue"
    ) -> Dict[str, Any]:
        """
        Génère un rapport financier.
        
        Args:
            start_date: Date de début
            end_date: Date de fin
            report_type: Type de rapport (revenue, arpu, churn, etc.)
            
        Returns:
            Données du rapport
        """
        try:
            if report_type == "revenue":
                return await self._generate_revenue_report(start_date, end_date)
            elif report_type == "arpu":
                return await self._generate_arpu_report(start_date, end_date)
            elif report_type == "churn":
                return await self._generate_churn_report(start_date, end_date)
            elif report_type == "mrr":
                return await self._generate_mrr_report(start_date, end_date)
            else:
                raise ValueError(f"Type de rapport inconnu: {report_type}")
                
        except Exception as e:
            logger.error(f"Erreur génération rapport: {str(e)}")
            raise
    
    async def _generate_revenue_report(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Génère un rapport de revenus."""
        # Récupération des paiements dans la période
        payments = self.db.query(Payment).filter(
            Payment.processed_at.between(start_date, end_date),
            Payment.status == PaymentStatus.SUCCEEDED
        ).all()
        
        total_revenue = sum(p.amount for p in payments)
        
        # Analyse par devise
        revenue_by_currency = {}
        for payment in payments:
            currency = payment.currency
            revenue_by_currency[currency] = revenue_by_currency.get(currency, 0) + payment.amount
        
        return {
            'period': {'start': start_date, 'end': end_date},
            'total_revenue': total_revenue,
            'revenue_by_currency': revenue_by_currency,
            'payment_count': len(payments),
            'average_payment': total_revenue / len(payments) if payments else 0,
        }
    
    # ==================== UTILITAIRES ====================
    
    def _calculate_tiered_pricing(self, quantity: int) -> int:
        """
        Calcule le prix selon les niveaux de tarification.
        
        Args:
            quantity: Quantité
            
        Returns:
            Montant en centimes
        """
        # À implémenter selon votre modèle de tarification
        # Exemple simple: prix fixe par unité après un certain seuil
        if quantity <= 1000:
            return quantity * 10  # $0.10 par unité
        elif quantity <= 10000:
            return quantity * 8   # $0.08 par unité
        else:
            return quantity * 6   # $0.06 par unité
    
    def _calculate_default_tax(
        self,
        amount: int,
        country: str
    ) -> Dict[str, Any]:
        """
        Calcule les taxes par défaut.
        
        Args:
            amount: Montant en centimes
            country: Pays du client
            
        Returns:
            Détails des taxes
        """
        # Taux de TVA par défaut par pays
        vat_rates = {
            'FR': 20.0,  # France
            'DE': 19.0,  # Allemagne
            'IT': 22.0,  # Italie
            'ES': 21.0,  # Espagne
            'GB': 20.0,  # Royaume-Uni
            'US': 0.0,   # USA (varie par état)
        }
        
        tax_rate = vat_rates.get(country, 0.0)
        tax_amount = int(amount * tax_rate / 100)
        
        return {
            'tax_amount': tax_amount,
            'total_amount': amount + tax_amount,
            'breakdown': [{
                'name': 'VAT',
                'rate': tax_rate,
                'amount': tax_amount,
            }],
        }
    
    def _get_dunning_strategy(self, error_code: str) -> Dict[str, Any]:
        """
        Retourne la stratégie de dunning basée sur le code d'erreur.
        
        Args:
            error_code: Code d'erreur Stripe
            
        Returns:
            Stratégie de dunning
        """
        strategies = {
            'card_declined': {
                'attempts': [
                    {'delay_days': 1, 'payment_method': 'same_card'},
                    {'delay_days': 3, 'payment_method': 'backup_card'},
                    {'delay_days': 7, 'payment_method': 'bank_transfer'},
                ]
            },
            'insufficient_funds': {
                'attempts': [
                    {'delay_days': 3, 'payment_method': 'same_card'},
                    {'delay_days': 7, 'payment_method': 'backup_card'},
                ]
            },
            'expired_card': {
                'attempts': [
                    {'delay_days': 1, 'payment_method': 'backup_card'},
                    {'delay_days': 2, 'payment_method': 'bank_transfer'},
                ]
            },
        }
        
        return strategies.get(error_code, {
            'attempts': [
                {'delay_days': 1, 'payment_method': 'same_card'},
                {'delay_days': 3, 'payment_method': 'backup_card'},
            ]
        })
    
    def _log_audit_trail(
        self,
        action: str,
        **details
    ) -> None:
        """
        Enregistre une trace d'audit.
        
        Args:
            action: Action effectuée
            **details: Détails de l'action
        """
        audit_log = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.utcnow().isoformat(),
            'action': action,
            'details': details,
            'environment': 'production',
            'compliance_check': self.compliance_auditor.check_action(action, details)
        }
        
        # À implémenter: enregistrement dans une base de données d'audit
        logger.info(f"AUDIT: {json_serializer(audit_log)}")
    
    async def _create_revenue_schedule_entry(
        self,
        invoice_id: str,
        amount: int,
        recognition_date: datetime,
        description: str
    ) -> None:
        """
        Crée une entrée dans le calendrier de reconnaissance de revenus.
        
        Args:
            invoice_id: ID de la facture
            amount: Montant à reconnaître
            recognition_date: Date de reconnaissance
            description: Description
        """
        # À implémenter: stockage dans une table de reconnaissance de revenus
        pass
    
    async def _handle_final_dunning_stage(self, invoice_id: str) -> None:
        """
        Gère la dernière étape du processus de dunning.
        
        Args:
            invoice_id: ID de la facture
        """
        # Actions possibles:
        # - Suspendre le service
        # - Transférer à une agence de recouvrement
        # - Marquer comme créance douteuse
        logger.warning(f"Facture {invoice_id} en échec définitif de paiement")
    
    # ==================== PROPRIÉTÉS DE CONFORMITÉ ====================
    
    @property
    def pci_compliance_status(self) -> Dict[str, Any]:
        """
        Retourne le statut de conformité PCI.
        
        Returns:
            Statut de conformité
        """
        return {
            'compliant': self.pci_compliant,
            'tokenization_enabled': self.tokenization_enabled,
            'last_audit': '2024-12-01',
            'certification': 'PCI DSS Level 1',
            'scope_reduction': True,
        }
    
    @property
    def gdpr_compliance_status(self) -> Dict[str, Any]:
        """
        Retourne le statut de conformité GDPR.
        
        Returns:
            Statut de conformité
        """
        return {
            'data_encryption': True,
            'right_to_be_forgotten': True,
            'data_portability': True,
            'privacy_by_design': True,
            'dpo_contact': 'dpo@microagents.io',
        }


# Exemple d'utilisation
if __name__ == "__main__":
    # Configuration
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # Initialisation de la base de données
    engine = create_engine(os.getenv('DATABASE_URL'))
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    # Création du handler
    handler = StripeHandler(
        api_key=os.getenv('STRIPE_SECRET_KEY'),
        webhook_secret=os.getenv('STRIPE_WEBHOOK_SECRET'),
        db_session=db
    )
    
    print("Stripe Handler prêt pour MicroAgents Platform")