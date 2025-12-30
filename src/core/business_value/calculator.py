"""
Module: Business Value Calculator
Description: Calculateur de valeur business avancé avec ROI, simulations Monte Carlo et analyses comparatives
Version: 2.0.0
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import json
import math
import random
import statistics
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
    Callable,
    TypeVar,
    Generic,
    AsyncGenerator,
    cast,
)
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import structlog
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, Field, validator, model_validator, ConfigDict
from scipy import stats

from ..base.exceptions import ValidationError
from ..base.utils import Serializer, SerializationFormat


# ====== Types de base ======

T = TypeVar("T")
DecimalLike = Union[Decimal, float, int, str]


class ValueCategory(str, Enum):
    """Catégories de valeur business"""
    COST_AVOIDANCE = "cost_avoidance"  # Coûts évités
    PRODUCTIVITY_GAINS = "productivity_gains"  # Gains de productivité
    REVENUE_INCREASE = "revenue_increase"  # Augmentation de revenus
    RISK_REDUCTION = "risk_reduction"  # Réduction de risques
    COMPLIANCE_VALUE = "compliance_value"  # Valeur de compliance
    STRATEGIC_VALUE = "strategic_value"  # Valeur stratégique
    CUSTOMER_SATISFACTION = "customer_satisfaction"  # Satisfaction client
    INNOVATION_VALUE = "innovation_value"  # Valeur d'innovation


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


class TimePeriod(str, Enum):
    """Périodes temporelles"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class RiskLevel(str, Enum):
    """Niveaux de risque"""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ConfidenceLevel(str, Enum):
    """Niveaux de confiance"""
    P50 = "p50"  # 50% de confiance
    P75 = "p75"  # 75% de confiance
    P90 = "p90"  # 90% de confiance
    P95 = "p95"  # 95% de confiance
    P99 = "p99"  # 99% de confiance


# ====== Modèles de base ======

@dataclass
class MonetaryAmount:
    """Montant monétaire avec devise"""
    amount: Decimal
    currency: Currency = Currency.USD
    year: Optional[int] = None  # Année pour l'ajustement inflation
    
    def __post_init__(self):
        if isinstance(self.amount, (float, int, str)):
            self.amount = Decimal(str(self.amount))
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "amount": float(self.amount),
            "currency": self.currency.value,
            "year": self.year
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MonetaryAmount:
        return cls(
            amount=Decimal(str(data["amount"])),
            currency=Currency(data["currency"]),
            year=data.get("year")
        )
    
    def convert_to(self, target_currency: Currency, rate: float) -> MonetaryAmount:
        """Convertit vers une autre devise"""
        if self.currency == target_currency:
            return self
        
        converted = self.amount * Decimal(str(rate))
        return MonetaryAmount(
            amount=converted,
            currency=target_currency,
            year=self.year
        )
    
    def adjust_for_inflation(self, inflation_rate: float, target_year: int) -> MonetaryAmount:
        """Ajuste pour l'inflation"""
        if self.year is None or self.year == target_year:
            return self
        
        years = target_year - self.year
        adjusted = self.amount * Decimal((1 + inflation_rate) ** years)
        
        return MonetaryAmount(
            amount=adjusted,
            currency=self.currency,
            year=target_year
        )
    
    def __add__(self, other: Any) -> MonetaryAmount:
        if isinstance(other, MonetaryAmount):
            if self.currency != other.currency:
                raise ValueError(f"Devises différentes: {self.currency} != {other.currency}")
            
            return MonetaryAmount(
                amount=self.amount + other.amount,
                currency=self.currency,
                year=self.year or other.year
            )
        
        return NotImplemented
    
    def __sub__(self, other: Any) -> MonetaryAmount:
        if isinstance(other, MonetaryAmount):
            if self.currency != other.currency:
                raise ValueError(f"Devises différentes: {self.currency} != {other.currency}")
            
            return MonetaryAmount(
                amount=self.amount - other.amount,
                currency=self.currency,
                year=self.year or other.year
            )
        
        return NotImplemented
    
    def __mul__(self, scalar: Union[int, float, Decimal]) -> MonetaryAmount:
        if isinstance(scalar, (int, float, Decimal)):
            return MonetaryAmount(
                amount=self.amount * Decimal(str(scalar)),
                currency=self.currency,
                year=self.year
            )
        
        return NotImplemented
    
    def __truediv__(self, scalar: Union[int, float, Decimal]) -> MonetaryAmount:
        if isinstance(scalar, (int, float, Decimal)):
            return MonetaryAmount(
                amount=self.amount / Decimal(str(scalar)),
                currency=self.currency,
                year=self.year
            )
        
        return NotImplemented


@dataclass
class TimeSeriesPoint:
    """Point dans une série temporelle"""
    timestamp: datetime.datetime
    value: Decimal
    confidence_low: Optional[Decimal] = None
    confidence_high: Optional[Decimal] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "value": float(self.value),
            "confidence_low": float(self.confidence_low) if self.confidence_low else None,
            "confidence_high": float(self.confidence_high) if self.confidence_high else None
        }


@dataclass
class ValueMetric:
    """Métrique de valeur business"""
    category: ValueCategory
    name: str
    description: str = ""
    baseline_value: Optional[MonetaryAmount] = None
    current_value: Optional[MonetaryAmount] = None
    projected_value: Optional[MonetaryAmount] = None
    unit: str = "currency"  # currency, hours, percentage, etc.
    weight: float = 1.0  # Poids dans le calcul total
    confidence: float = 0.8  # Confiance 0-1
    risk_adjustment: float = 1.0  # Ajustement pour risque
    
    @property
    def delta(self) -> Optional[MonetaryAmount]:
        """Différence entre current et baseline"""
        if self.baseline_value and self.current_value:
            if self.baseline_value.currency != self.current_value.currency:
                raise ValueError("Devises différentes pour baseline et current")
            
            return MonetaryAmount(
                amount=self.current_value.amount - self.baseline_value.amount,
                currency=self.current_value.currency,
                year=self.current_value.year
            )
        return None
    
    @property
    def improvement_percentage(self) -> Optional[float]:
        """Pourcentage d'amélioration"""
        if self.delta and self.baseline_value and self.baseline_value.amount != Decimal(0):
            delta_amount = self.delta.amount
            baseline_amount = self.baseline_value.amount
            
            return float((delta_amount / baseline_amount) * Decimal(100))
        return None
    
    @property
    def risk_adjusted_value(self) -> Optional[MonetaryAmount]:
        """Valeur ajustée pour le risque"""
        if self.current_value:
            return MonetaryAmount(
                amount=self.current_value.amount * Decimal(str(self.risk_adjustment)),
                currency=self.current_value.currency,
                year=self.current_value.year
            )
        return None


@dataclass
class BusinessAssumption:
    """Hypothèse business pour les calculs"""
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    value: Any = None
    unit: str = ""
    confidence: float = 0.8
    source: str = ""
    valid_from: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    valid_to: Optional[datetime.datetime] = None
    tags: List[str] = field(default_factory=list)
    
    def is_valid(self, date: Optional[datetime.datetime] = None) -> bool:
        """Vérifie si l'hypothèse est valide à une date donnée"""
        check_date = date or datetime.datetime.utcnow()
        
        if check_date < self.valid_from:
            return False
        
        if self.valid_to and check_date > self.valid_to:
            return False
        
        return True


@dataclass
class Investment:
    """Investissement initial"""
    name: str = ""
    amount: MonetaryAmount = field(default_factory=lambda: MonetaryAmount(Decimal(0)))
    timing: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    category: str = "implementation"  # implementation, training, maintenance, etc.
    depreciation_period_years: Optional[int] = None  # Période d'amortissement
    residual_value: Optional[MonetaryAmount] = None  # Valeur résiduelle


@dataclass
class CashFlow:
    """Flux de trésorerie"""
    period: int  # Période (0 = initial, 1 = première période, etc.)
    timestamp: datetime.datetime
    inflow: MonetaryAmount = field(default_factory=lambda: MonetaryAmount(Decimal(0)))
    outflow: MonetaryAmount = field(default_factory=lambda: MonetaryAmount(Decimal(0)))
    description: str = ""
    
    @property
    def net_cash_flow(self) -> MonetaryAmount:
        """Flux net"""
        return MonetaryAmount(
            amount=self.inflow.amount - self.outflow.amount,
            currency=self.inflow.currency,  # Assume same currency
            year=self.timestamp.year
        )


@dataclass
class RiskFactor:
    """Facteur de risque"""
    name: str
    description: str = ""
    probability: float = 0.1  # Probabilité 0-1
    impact: float = 0.3  # Impact 0-1
    mitigation_strategy: str = ""
    mitigation_cost: Optional[MonetaryAmount] = None
    category: str = "operational"  # operational, financial, strategic, compliance
    
    @property
    def expected_value(self) -> float:
        """Valeur attendue du risque (probabilité * impact)"""
        return self.probability * self.impact
    
    @property
    def risk_score(self) -> float:
        """Score de risque (échelle 0-100)"""
        return self.expected_value * 100


@dataclass
class ComparativeAlternative:
    """Alternative comparative pour l'analyse"""
    name: str
    description: str = ""
    total_cost: Optional[MonetaryAmount] = None
    total_benefit: Optional[MonetaryAmount] = None
    implementation_time_months: Optional[float] = None
    success_probability: float = 0.8
    risks: List[RiskFactor] = field(default_factory=list)
    assumptions: List[BusinessAssumption] = field(default_factory=list)
    
    @property
    def net_present_value(self, discount_rate: float = 0.1) -> Optional[MonetaryAmount]:
        """NPV simplifié"""
        if self.total_cost and self.total_benefit:
            if self.total_cost.currency != self.total_benefit.currency:
                return None
            
            # NPV = Benefits - Costs (simplifié pour une période)
            net = self.total_benefit.amount - self.total_cost.amount
            return MonetaryAmount(
                amount=net,
                currency=self.total_benefit.currency
            )
        return None


@dataclass
class AuditEntry:
    """Entrée d'audit trail"""
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    user_id: Optional[str] = None
    action: str = ""
    entity_type: str = ""
    entity_id: str = ""
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ====== Modèle principal de calcul ======

class BusinessValueCalculation(BaseModel):
    """Calcul de valeur business complet"""
    
    # Identification
    calculation_id: str = Field(default_factory=lambda: f"calc_{uuid4().hex[:8]}")
    name: str = ""
    description: str = ""
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    created_by: Optional[str] = None
    
    # Configuration
    base_currency: Currency = Currency.USD
    time_horizon_years: int = 3
    discount_rate: float = 0.1  # Taux d'actualisation
    inflation_rate: float = 0.02  # Taux d'inflation
    tax_rate: float = 0.25  # Taux d'imposition
    
    # Données d'entrée
    investments: List[Investment] = Field(default_factory=list)
    value_metrics: List[ValueMetric] = Field(default_factory=list)
    assumptions: List[BusinessAssumption] = Field(default_factory=list)
    risk_factors: List[RiskFactor] = Field(default_factory=list)
    cash_flows: List[CashFlow] = Field(default_factory=list)
    alternatives: List[ComparativeAlternative] = Field(default_factory=list)
    
    # Résultats calculés
    _results: Optional[Dict[str, Any]] = Field(default=None, exclude=True)
    _monte_carlo_results: Optional[Dict[str, Any]] = Field(default=None, exclude=True)
    
    # Audit trail
    audit_trail: List[AuditEntry] = Field(default_factory=list)
    
    # Configuration Pydantic
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={
            datetime.datetime: lambda dt: dt.isoformat(),
            Decimal: lambda d: float(d),
            MonetaryAmount: lambda m: m.to_dict()
        }
    )
    
    # === Validators ===
    
    @validator('discount_rate', 'inflation_rate', 'tax_rate')
    @classmethod
    def validate_rates(cls, v: float) -> float:
        """Valide les taux (doivent être entre 0 et 1)"""
        if not 0 <= v <= 1:
            raise ValueError(f"Le taux doit être entre 0 et 1, reçu: {v}")
        return v
    
    @validator('time_horizon_years')
    @classmethod
    def validate_time_horizon(cls, v: int) -> int:
        """Valide l'horizon temporel"""
        if v <= 0:
            raise ValueError(f"L'horizon temporel doit être positif, reçu: {v}")
        return v
    
    @model_validator(mode='after')
    def validate_currencies(self) -> Self:
        """Valide la cohérence des devises"""
        # Convertir toutes les valeurs à la devise de base
        for metric in self.value_metrics:
            if metric.baseline_value and metric.baseline_value.currency != self.base_currency:
                self.log_warning(f"Métrique {metric.name}: devise baseline différente de la devise de base")
            
            if metric.current_value and metric.current_value.currency != self.base_currency:
                self.log_warning(f"Métrique {metric.name}: devise current différente de la devise de base")
        
        return self
    
    # === Propriétés calculées ===
    
    @property
    def total_investment(self) -> MonetaryAmount:
        """Investissement total"""
        total = Decimal(0)
        currency = self.base_currency
        
        for investment in self.investments:
            if investment.amount.currency != currency:
                # Devrait être converti
                continue
            total += investment.amount.amount
        
        return MonetaryAmount(amount=total, currency=currency)
    
    @property
    def total_annual_benefits(self) -> Optional[MonetaryAmount]:
        """Bénéfices annuels totaux"""
        if not self.value_metrics:
            return None
        
        total = Decimal(0)
        currency = self.base_currency
        
        for metric in self.value_metrics:
            if metric.current_value and metric.current_value.currency == currency:
                # Ajuster avec le poids et la confiance
                adjusted_value = metric.current_value.amount * Decimal(metric.weight) * Decimal(metric.confidence)
                total += adjusted_value
        
        return MonetaryAmount(amount=total, currency=currency)
    
    @property
    def total_risk_adjusted_benefits(self) -> Optional[MonetaryAmount]:
        """Bénéfices totaux ajustés pour le risque"""
        if not self.value_metrics:
            return None
        
        total = Decimal(0)
        currency = self.base_currency
        
        for metric in self.value_metrics:
            if metric.risk_adjusted_value and metric.risk_adjusted_value.currency == currency:
                # Ajuster avec le poids et la confiance
                adjusted_value = (
                    metric.risk_adjusted_value.amount * 
                    Decimal(metric.weight) * 
                    Decimal(metric.confidence)
                )
                total += adjusted_value
        
        return MonetaryAmount(amount=total, currency=currency)
    
    @property
    def overall_risk_score(self) -> float:
        """Score de risque global"""
        if not self.risk_factors:
            return 0.0
        
        total_score = sum(risk.risk_score for risk in self.risk_factors)
        return total_score / len(self.risk_factors)
    
    # === Méthodes de calcul principales ===
    
    def calculate_roi(self, use_risk_adjusted: bool = False) -> Dict[str, Any]:
        """
        Calcule le ROI (Return on Investment)
        
        Formule: ROI = (Net Benefits - Investment) / Investment
        
        Args:
            use_risk_adjusted: Utiliser les bénéfices ajustés pour le risque
            
        Returns:
            Résultats du calcul ROI
        """
        investment = self.total_investment.amount
        benefits = (
            self.total_risk_adjusted_benefits.amount 
            if use_risk_adjusted and self.total_risk_adjusted_benefits 
            else self.total_annual_benefits.amount 
            if self.total_annual_benefits 
            else Decimal(0)
        )
        
        # ROI annuel
        if investment != Decimal(0):
            annual_roi = (benefits - investment) / investment
        else:
            annual_roi = Decimal('Inf') if benefits > Decimal(0) else Decimal(0)
        
        # ROI sur l'horizon temporel
        total_benefits = benefits * Decimal(self.time_horizon_years)
        if investment != Decimal(0):
            total_roi = (total_benefits - investment) / investment
        else:
            total_roi = Decimal('Inf') if total_benefits > Decimal(0) else Decimal(0)
        
        return {
            "annual_roi": float(annual_roi),
            "total_roi": float(total_roi),
            "investment": float(investment),
            "annual_benefits": float(benefits),
            "total_benefits": float(total_benefits),
            "use_risk_adjusted": use_risk_adjusted,
            "time_horizon_years": self.time_horizon_years
        }
    
    def calculate_npv(self) -> Dict[str, Any]:
        """
        Calcule la NPV (Net Present Value)
        
        Formule: NPV = Σ (Cash Flow_t / (1 + r)^t)
        
        Returns:
            Résultats du calcul NPV
        """
        if not self.cash_flows:
            # Estimer les cash flows à partir des métriques
            cash_flows = self._estimate_cash_flows()
        else:
            cash_flows = self.cash_flows
        
        # Trier les cash flows par période
        cash_flows.sort(key=lambda x: x.period)
        
        npv = Decimal(0)
        cash_flow_details = []
        
        for cf in cash_flows:
            discount_factor = Decimal(1) / (Decimal(1) + Decimal(str(self.discount_rate))) ** cf.period
            discounted_cf = cf.net_cash_flow.amount * discount_factor
            npv += discounted_cf
            
            cash_flow_details.append({
                "period": cf.period,
                "timestamp": cf.timestamp.isoformat(),
                "inflow": float(cf.inflow.amount),
                "outflow": float(cf.outflow.amount),
                "net_cash_flow": float(cf.net_cash_flow.amount),
                "discount_factor": float(discount_factor),
                "discounted_cf": float(discounted_cf)
            })
        
        return {
            "npv": float(npv),
            "discount_rate": self.discount_rate,
            "currency": self.base_currency.value,
            "cash_flows": cash_flow_details,
            "total_periods": len(cash_flows)
        }
    
    def calculate_irr(self, max_iterations: int = 100, tolerance: float = 1e-6) -> Dict[str, Any]:
        """
        Calcule l'IRR (Internal Rate of Return) par méthode itérative
        
        Args:
            max_iterations: Nombre maximum d'itérations
            tolerance: Tolérance pour la convergence
            
        Returns:
            Résultats du calcul IRR
        """
        if not self.cash_flows:
            cash_flows = self._estimate_cash_flows()
        else:
            cash_flows = self.cash_flows
        
        # Préparer les cash flows
        cash_flows.sort(key=lambda x: x.period)
        periods = max(cf.period for cf in cash_flows) + 1
        
        # Créer un tableau de cash flows par période
        cf_array = [Decimal(0)] * periods
        for cf in cash_flows:
            cf_array[cf.period] = cf.net_cash_flow.amount
        
        # Méthode de Newton-Raphson pour trouver l'IRR
        irr = 0.1  # Estimation initiale
        iteration = 0
        
        for _ in range(max_iterations):
            npv = Decimal(0)
            d_npv = Decimal(0)  # Dérivée
            
            for t, cf in enumerate(cf_array):
                if cf != Decimal(0):
                    discount = (Decimal(1) + Decimal(str(irr))) ** t
                    npv += cf / discount
                    
                    if t > 0:
                        d_npv -= Decimal(t) * cf / ((Decimal(1) + Decimal(str(irr))) ** (t + 1))
            
            # Vérifier la convergence
            if abs(float(npv)) < tolerance:
                break
            
            # Mettre à jour l'IRR
            if d_npv != Decimal(0):
                irr -= float(npv / d_npv)
            else:
                irr += 0.01  # Ajustement manuel si dérivée nulle
            
            iteration += 1
        
        # Vérifier la validité
        irr_valid = -1 < irr < 1  # IRR raisonnable entre -100% et +100%
        
        return {
            "irr": irr,
            "iterations": iteration,
            "converged": iteration < max_iterations,
            "is_valid": irr_valid,
            "cash_flow_periods": periods,
            "tolerance": tolerance
        }
    
    def calculate_payback_period(self) -> Dict[str, Any]:
        """
        Calcule la période de retour sur investissement
        
        Returns:
            Résultats du calcul payback period
        """
        if not self.cash_flows:
            cash_flows = self._estimate_cash_flows()
        else:
            cash_flows = self.cash_flows
        
        cash_flows.sort(key=lambda x: x.period)
        
        cumulative_cash_flow = Decimal(0)
        payback_period = None
        investment = self.total_investment.amount
        
        for cf in cash_flows:
            cumulative_cash_flow += cf.net_cash_flow.amount
            
            if cumulative_cash_flow >= investment and payback_period is None:
                # Interpolation linéaire pour une précision exacte
                if cf.period == 0:
                    payback_period = 0
                else:
                    previous_cf = cumulative_cash_flow - cf.net_cash_flow.amount
                    fraction = float((investment - previous_cf) / cf.net_cash_flow.amount)
                    payback_period = cf.period - 1 + fraction
        
        # Si jamais atteint
        if payback_period is None:
            payback_period = float('inf')
        
        return {
            "payback_period": payback_period,
            "investment": float(investment),
            "cumulative_cf_at_payback": float(cumulative_cash_flow),
            "is_achievable": payback_period != float('inf')
        }
    
    def calculate_customer_lifetime_value(
        self,
        acquisition_cost: MonetaryAmount,
        retention_rate: float,
        profit_margin: float
    ) -> Dict[str, Any]:
        """
        Calcule la Customer Lifetime Value (CLV)
        
        Formule: CLV = (Margin * Retention Rate) / (1 + Discount Rate - Retention Rate) - Acquisition Cost
        
        Args:
            acquisition_cost: Coût d'acquisition client
            retention_rate: Taux de rétention annuel
            profit_margin: Marge bénéficiaire par client
        
        Returns:
            Résultats du calcul CLV
        """
        # Valider les inputs
        if not 0 <= retention_rate <= 1:
            raise ValueError(f"Taux de rétention invalide: {retention_rate}")
        
        if not 0 <= profit_margin <= 1:
            raise ValueError(f"Marge bénéficiaire invalide: {profit_margin}")
        
        # Convertir acquisition cost en devise de base si nécessaire
        if acquisition_cost.currency != self.base_currency:
            # Devrait utiliser un taux de change
            pass
        
        # Calculer CLV
        if self.discount_rate + 1 - retention_rate != 0:
            clv = (
                Decimal(str(profit_margin)) * Decimal(str(retention_rate)) /
                (Decimal(1) + Decimal(str(self.discount_rate)) - Decimal(str(retention_rate)))
            )
            clv_amount = MonetaryAmount(
                amount=clv - acquisition_cost.amount,
                currency=self.base_currency
            )
        else:
            clv_amount = MonetaryAmount(amount=Decimal(0), currency=self.base_currency)
        
        return {
            "clv": float(clv_amount.amount),
            "currency": clv_amount.currency.value,
            "acquisition_cost": float(acquisition_cost.amount),
            "retention_rate": retention_rate,
            "profit_margin": profit_margin,
            "discount_rate": self.discount_rate,
            "formula_used": "CLV = (Margin * Retention) / (1 + Discount - Retention) - Acquisition Cost"
        }
    
    async def run_monte_carlo_simulation(
        self,
        iterations: int = 10000,
        confidence_levels: List[ConfidenceLevel] = None
    ) -> Dict[str, Any]:
        """
        Exécute une simulation Monte Carlo pour l'incertitude
        
        Args:
            iterations: Nombre d'itérations de simulation
            confidence_levels: Niveaux de confiance à calculer
            
        Returns:
            Résultats de la simulation
        """
        if confidence_levels is None:
            confidence_levels = [ConfidenceLevel.P50, ConfidenceLevel.P75, ConfidenceLevel.P90, ConfidenceLevel.P95]
        
        self.log_info(f"Démarrage simulation Monte Carlo avec {iterations} itérations")
        
        # Préparer les distributions pour les inputs incertains
        distributions = self._prepare_monte_carlo_distributions()
        
        # Exécuter les simulations
        results = {
            "roi": [],
            "npv": [],
            "payback_period": [],
            "total_benefits": []
        }
        
        for i in range(iterations):
            # Échantillonner les distributions
            sampled_values = self._sample_distributions(distributions)
            
            # Calculer les métriques avec les valeurs échantillonnées
            roi_result = self._calculate_with_sampled_values(sampled_values, "roi")
            npv_result = self._calculate_with_sampled_values(sampled_values, "npv")
            
            results["roi"].append(roi_result)
            results["npv"].append(npv_result)
            
            # Mettre à jour périodiquement
            if i % 1000 == 0:
                await asyncio.sleep(0)  # Yield pour éviter de bloquer
        
        # Analyser les résultats
        analysis = self._analyze_monte_carlo_results(results, confidence_levels)
        
        self._monte_carlo_results = {
            "iterations": iterations,
            "results": results,
            "analysis": analysis,
            "distributions_used": list(distributions.keys()),
            "confidence_levels": [cl.value for cl in confidence_levels]
        }
        
        self.log_info(f"Simulation Monte Carlo terminée, {iterations} itérations")
        
        return self._monte_carlo_results
    
    def _prepare_monte_carlo_distributions(self) -> Dict[str, Any]:
        """Prépare les distributions pour Monte Carlo"""
        distributions = {}
        
        # Distributions pour les métriques de valeur
        for metric in self.value_metrics:
            if metric.current_value:
                # Utiliser une distribution normale centrée sur la valeur courante
                # avec un écart-type basé sur la confiance
                std_dev = float(metric.current_value.amount) * (1 - metric.confidence)
                distributions[f"metric_{metric.name}"] = {
                    "type": "normal",
                    "mean": float(metric.current_value.amount),
                    "std_dev": std_dev,
                    "min": 0  # Les valeurs ne peuvent pas être négatives
                }
        
        # Distributions pour les hypothèses
        for assumption in self.assumptions:
            if isinstance(assumption.value, (int, float)):
                distributions[f"assumption_{assumption.id}"] = {
                    "type": "uniform",
                    "min": float(assumption.value) * 0.8,  # ±20%
                    "max": float(assumption.value) * 1.2
                }
        
        # Distribution pour le taux d'actualisation
        distributions["discount_rate"] = {
            "type": "normal",
            "mean": self.discount_rate,
            "std_dev": self.discount_rate * 0.1  # 10% d'incertitude
        }
        
        return distributions
    
    def _sample_distributions(self, distributions: Dict[str, Any]) -> Dict[str, float]:
        """Échantillonne les distributions"""
        sampled = {}
        
        for name, dist in distributions.items():
            if dist["type"] == "normal":
                value = random.normalvariate(dist["mean"], dist["std_dev"])
                if "min" in dist:
                    value = max(value, dist["min"])
                sampled[name] = value
            elif dist["type"] == "uniform":
                sampled[name] = random.uniform(dist["min"], dist["max"])
            elif dist["type"] == "triangular":
                sampled[name] = random.triangular(dist["min"], dist["mode"], dist["max"])
        
        return sampled
    
    def _calculate_with_sampled_values(
        self, 
        sampled_values: Dict[str, float],
        metric: str
    ) -> float:
        """Calcule une métrique avec des valeurs échantillonnées"""
        # Cette méthode simplifiée ajuste les valeurs de base
        # Une implémentation complète reconstruirait le calcul avec les nouvelles valeurs
        
        if metric == "roi":
            # ROI simplifié basé sur les métriques ajustées
            total_investment = self.total_investment.amount
            total_benefits = Decimal(0)
            
            for metric_obj in self.value_metrics:
                if metric_obj.current_value:
                    key = f"metric_{metric_obj.name}"
                    if key in sampled_values:
                        adjusted_value = Decimal(str(sampled_values[key]))
                    else:
                        adjusted_value = metric_obj.current_value.amount
                    
                    # Ajuster avec poids et confiance
                    adjusted_value = adjusted_value * Decimal(metric_obj.weight) * Decimal(metric_obj.confidence)
                    total_benefits += adjusted_value
            
            if total_investment != Decimal(0):
                roi = float((total_benefits - total_investment) / total_investment)
            else:
                roi = float('inf')
            
            return roi
        
        elif metric == "npv":
            # NPV simplifié
            npv_value = 0.0
            
            # Ajuster le taux d'actualisation
            discount_rate = sampled_values.get("discount_rate", self.discount_rate)
            
            # Calcul NPV simple (à améliorer)
            for period in range(self.time_horizon_years + 1):
                cash_flow = 100000  # Valeur simplifiée
                discounted = cash_flow / ((1 + discount_rate) ** period)
                npv_value += discounted
            
            return npv_value
        
        return 0.0
    
    def _analyze_monte_carlo_results(
        self,
        results: Dict[str, List[float]],
        confidence_levels: List[ConfidenceLevel]
    ) -> Dict[str, Any]:
        """Analyse les résultats Monte Carlo"""
        analysis = {}
        
        for metric, values in results.items():
            if not values:
                continue
            
            # Statistiques descriptives
            mean = statistics.mean(values)
            median = statistics.median(values)
            std_dev = statistics.stdev(values) if len(values) > 1 else 0
            
            # Percentiles pour les niveaux de confiance
            percentiles = {}
            for cl in confidence_levels:
                if cl == ConfidenceLevel.P50:
                    percentile = 50
                elif cl == ConfidenceLevel.P75:
                    percentile = 75
                elif cl == ConfidenceLevel.P90:
                    percentile = 90
                elif cl == ConfidenceLevel.P95:
                    percentile = 95
                elif cl == ConfidenceLevel.P99:
                    percentile = 99
                else:
                    continue
                
                percentiles[cl.value] = np.percentile(values, percentile)
            
            # Intervalles de confiance
            confidence_interval_95 = stats.norm.interval(
                0.95, 
                loc=mean, 
                scale=std_dev / math.sqrt(len(values))
            ) if len(values) > 1 else (mean, mean)
            
            analysis[metric] = {
                "mean": mean,
                "median": median,
                "std_dev": std_dev,
                "min": min(values),
                "max": max(values),
                "percentiles": percentiles,
                "confidence_interval_95": confidence_interval_95,
                "coefficient_of_variation": std_dev / mean if mean != 0 else 0,
                "probability_positive": sum(1 for v in values if v > 0) / len(values) if values else 0
            }
        
        return analysis
    
    # === Méthodes utilitaires ===
    
    def _estimate_cash_flows(self) -> List[CashFlow]:
        """Estime les cash flows à partir des métriques"""
        cash_flows = []
        current_year = datetime.datetime.utcnow().year
        
        # Cash flow initial (investissement)
        cash_flows.append(CashFlow(
            period=0,
            timestamp=datetime.datetime(current_year, 1, 1),
            inflow=MonetaryAmount(Decimal(0), self.base_currency),
            outflow=self.total_investment,
            description="Investissement initial"
        ))
        
        # Cash flows annuels (bénéfices)
        for year in range(1, self.time_horizon_years + 1):
            timestamp = datetime.datetime(current_year + year, 6, 30)  # Milieu d'année
            
            # Bénéfices annuels
            if self.total_annual_benefits:
                benefits = self.total_annual_benefits
            else:
                benefits = MonetaryAmount(Decimal(0), self.base_currency)
            
            cash_flows.append(CashFlow(
                period=year,
                timestamp=timestamp,
                inflow=benefits,
                outflow=MonetaryAmount(Decimal(0), self.base_currency),
                description=f"Bénéfices année {year}"
            ))
        
        return cash_flows
    
    def calculate_all_metrics(self) -> Dict[str, Any]:
        """Calcule toutes les métriques de valeur"""
        results = {
            "calculation_id": self.calculation_id,
            "name": self.name,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "base_currency": self.base_currency.value,
            "summary": {},
            "detailed_results": {}
        }
        
        # ROI
        roi_results = self.calculate_roi()
        results["detailed_results"]["roi"] = roi_results
        results["summary"]["annual_roi"] = roi_results["annual_roi"]
        results["summary"]["total_roi"] = roi_results["total_roi"]
        
        # NPV
        npv_results = self.calculate_npv()
        results["detailed_results"]["npv"] = npv_results
        results["summary"]["npv"] = npv_results["npv"]
        
        # IRR
        irr_results = self.calculate_irr()
        results["detailed_results"]["irr"] = irr_results
        results["summary"]["irr"] = irr_results["irr"]
        
        # Payback Period
        payback_results = self.calculate_payback_period()
        results["detailed_results"]["payback_period"] = payback_results
        results["summary"]["payback_period"] = payback_results["payback_period"]
        
        # Totaux
        results["summary"]["total_investment"] = float(self.total_investment.amount)
        results["summary"]["total_annual_benefits"] = (
            float(self.total_annual_benefits.amount) 
            if self.total_annual_benefits else 0
        )
        results["summary"]["overall_risk_score"] = self.overall_risk_score
        
        # Analyse par catégorie
        category_analysis = {}
        for category in ValueCategory:
            category_metrics = [m for m in self.value_metrics if m.category == category]
            if category_metrics:
                total_value = sum(
                    float(m.current_value.amount) * m.weight * m.confidence 
                    for m in category_metrics 
                    if m.current_value
                )
                category_analysis[category.value] = {
                    "count": len(category_metrics),
                    "total_value": total_value,
                    "percentage_of_total": (
                        total_value / float(self.total_annual_benefits.amount) * 100
                        if self.total_annual_benefits and self.total_annual_benefits.amount != Decimal(0)
                        else 0
                    )
                }
        
        results["detailed_results"]["category_analysis"] = category_analysis
        
        self._results = results
        self.updated_at = datetime.datetime.utcnow()
        
        # Audit trail
        self.add_audit_entry(
            action="calculate_all_metrics",
            entity_type="calculation",
            entity_id=self.calculation_id,
            metadata={"results_generated": True}
        )
        
        return results
    
    def comparative_analysis(self) -> Dict[str, Any]:
        """Analyse comparative vs alternatives"""
        if not self.alternatives:
            return {"alternatives": [], "recommendation": "No alternatives provided"}
        
        analysis = {
            "current_solution": {
                "name": self.name or "Current Solution",
                "npv": self.calculate_npv()["npv"],
                "roi": self.calculate_roi()["total_roi"],
                "payback_period": self.calculate_payback_period()["payback_period"],
                "total_cost": float(self.total_investment.amount)
            },
            "alternatives": [],
            "comparison": {}
        }
        
        # Analyser chaque alternative
        for alt in self.alternatives:
            alt_analysis = {
                "name": alt.name,
                "description": alt.description,
                "total_cost": float(alt.total_cost.amount) if alt.total_cost else 0,
                "total_benefit": float(alt.total_benefit.amount) if alt.total_benefit else 0,
                "success_probability": alt.success_probability,
                "risk_score": sum(r.risk_score for r in alt.risks) / len(alt.risks) if alt.risks else 0,
                "implementation_time_months": alt.implementation_time_months
            }
            
            # Calcul NPV si possible
            if alt.total_cost and alt.total_benefit:
                net_benefit = alt.total_benefit.amount - alt.total_cost.amount
                alt_analysis["net_benefit"] = float(net_benefit)
                
                # ROI simplifié
                if alt.total_cost.amount != Decimal(0):
                    alt_analysis["roi"] = float(net_benefit / alt.total_cost.amount)
                else:
                    alt_analysis["roi"] = float('inf')
            
            analysis["alternatives"].append(alt_analysis)
        
        # Recommandation basée sur NPV
        all_options = [analysis["current_solution"]] + analysis["alternatives"]
        best_option = max(
            all_options, 
            key=lambda x: x.get("npv", x.get("net_benefit", -float('inf')))
        )
        
        analysis["comparison"]["best_option"] = best_option["name"]
        analysis["comparison"]["reason"] = (
            f"Highest NPV/net benefit: {best_option.get('npv', best_option.get('net_benefit', 0))}"
        )
        
        return analysis
    
    # === Export ===
    
    def export_to_json(self, include_raw_data: bool = False) -> Dict[str, Any]:
        """Exporte en format JSON"""
        export_data = {
            "format_version": "2.0.0",
            "export_date": datetime.datetime.utcnow().isoformat(),
            "calculation": self.model_dump(exclude={"_results", "_monte_carlo_results"}),
            "results": self._results or {},
            "monte_carlo_results": self._monte_carlo_results or {}
        }
        
        if not include_raw_data:
            # Exclure les données brutes volumineuses
            if "monte_carlo_results" in export_data and "results" in export_data["monte_carlo_results"]:
                del export_data["monte_carlo_results"]["results"]
        
        return export_data
    
    async def export_to_excel(self, filepath: Path) -> str:
        """Exporte en fichier Excel"""
        import pandas as pd
        
        # Créer un writer Excel
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            # 1. Résumé
            summary_data = {
                "Métrique": [
                    "Nom du calcul",
                    "ID du calcul",
                    "Devise de base",
                    "Horizon temporel (années)",
                    "Taux d'actualisation",
                    "Taux d'inflation",
                    "Taux d'imposition",
                    "Investissement total",
                    "Bénéfices annuels totaux",
                    "ROI annuel",
                    "ROI total",
                    "NPV",
                    "IRR",
                    "Période de retour (années)",
                    "Score de risque global"
                ],
                "Valeur": [
                    self.name,
                    self.calculation_id,
                    self.base_currency.value,
                    self.time_horizon_years,
                    f"{self.discount_rate:.2%}",
                    f"{self.inflation_rate:.2%}",
                    f"{self.tax_rate:.2%}",
                    f"{float(self.total_investment.amount):,.2f}",
                    f"{float(self.total_annual_benefits.amount):,.2f}" if self.total_annual_benefits else "N/A",
                    f"{self.calculate_roi()['annual_roi']:.2%}",
                    f"{self.calculate_roi()['total_roi']:.2%}",
                    f"{self.calculate_npv()['npv']:,.2f}",
                    f"{self.calculate_irr()['irr']:.2%}",
                    f"{self.calculate_payback_period()['payback_period']:.2f}",
                    f"{self.overall_risk_score:.1f}/100"
                ]
            }
            
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Résumé", index=False)
            
            # 2. Métriques de valeur
            metrics_data = []
            for metric in self.value_metrics:
                metrics_data.append({
                    "Catégorie": metric.category.value,
                    "Nom": metric.name,
                    "Description": metric.description,
                    "Valeur Baseline": float(metric.baseline_value.amount) if metric.baseline_value else 0,
                    "Valeur Courante": float(metric.current_value.amount) if metric.current_value else 0,
                    "Delta": float(metric.delta.amount) if metric.delta else 0,
                    "Amélioration %": f"{metric.improvement_percentage:.1%}" if metric.improvement_percentage else "N/A",
                    "Poids": metric.weight,
                    "Confiance": f"{metric.confidence:.0%}",
                    "Ajustement Risque": metric.risk_adjustment
                })
            
            if metrics_data:
                pd.DataFrame(metrics_data).to_excel(writer, sheet_name="Métriques", index=False)
            
            # 3. Cash Flows
            cash_flows = self.cash_flows or self._estimate_cash_flows()
            cash_flow_data = []
            
            for cf in cash_flows:
                cash_flow_data.append({
                    "Période": cf.period,
                    "Date": cf.timestamp.strftime("%Y-%m-%d"),
                    "Entrées": float(cf.inflow.amount),
                    "Sorties": float(cf.outflow.amount),
                    "Flux Net": float(cf.net_cash_flow.amount)
                })
            
            if cash_flow_data:
                pd.DataFrame(cash_flow_data).to_excel(writer, sheet_name="Cash Flows", index=False)
            
            # 4. Analyse comparative
            if self.alternatives:
                comparison_data = []
                current = self.calculate_roi()
                
                comparison_data.append({
                    "Option": "Solution actuelle",
                    "ROI": f"{current['total_roi']:.1%}",
                    "NPV": f"{self.calculate_npv()['npv']:,.2f}",
                    "Période Retour": f"{self.calculate_payback_period()['payback_period']:.1f} ans"
                })
                
                for alt in self.alternatives:
                    if alt.total_cost and alt.total_benefit:
                        roi = float((alt.total_benefit.amount - alt.total_cost.amount) / alt.total_cost.amount)
                        comparison_data.append({
                            "Option": alt.name,
                            "ROI": f"{roi:.1%}",
                            "NPV": "N/A",
                            "Période Retour": "N/A"
                        })
                
                pd.DataFrame(comparison_data).to_excel(writer, sheet_name="Comparaison", index=False)
        
        return str(filepath)
    
    async def export_to_pdf(self, filepath: Path, template_name: str = "default") -> str:
        """Exporte en PDF (nécessite WeasyPrint ou reportlab)"""
        try:
            # Utiliser Jinja2 pour le template HTML
            env = Environment(
                loader=FileSystemLoader("templates/business_value"),
                autoescape=True
            )
            
            template = env.get_template(f"{template_name}.html")
            
            # Données pour le template
            template_data = {
                "calculation": self,
                "results": self._results or {},
                "timestamp": datetime.datetime.utcnow(),
                "summary": self.calculate_all_metrics()["summary"]
            }
            
            html_content = template.render(**template_data)
            
            # Convertir HTML en PDF (exemple avec WeasyPrint)
            try:
                from weasyprint import HTML
                HTML(string=html_content).write_pdf(filepath)
            except ImportError:
                # Fallback vers reportlab
                self._generate_pdf_with_reportlab(filepath, template_data)
            
            return str(filepath)
            
        except Exception as e:
            self.log_error(f"Échec de l'export PDF: {e}")
            raise
    
    def _generate_pdf_with_reportlab(self, filepath: Path, data: Dict[str, Any]):
        """Génère un PDF avec reportlab (fallback)"""
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        
        c = canvas.Canvas(str(filepath), pagesize=letter)
        
        # Titre
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 750, f"Rapport de Valeur Business: {self.name}")
        
        # Résumé
        c.setFont("Helvetica", 12)
        y_position = 700
        
        c.drawString(50, y_position, f"ID Calcul: {self.calculation_id}")
        y_position -= 20
        
        c.drawString(50, y_position, f"ROI Total: {self.calculate_roi()['total_roi']:.1%}")
        y_position -= 20
        
        c.drawString(50, y_position, f"NPV: {self.calculate_npv()['npv']:,.2f} {self.base_currency.value}")
        y_position -= 20
        
        c.drawString(50, y_position, f"Période de Retour: {self.calculate_payback_period()['payback_period']:.1f} ans")
        y_position -= 40
        
        # Sauvegarder
        c.save()
    
    # === Audit Trail ===
    
    def add_audit_entry(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Ajoute une entrée d'audit trail"""
        entry = AuditEntry(
            user_id=user_id or self.created_by,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_state=before_state,
            after_state=after_state,
            metadata=metadata or {}
        )
        
        self.audit_trail.append(entry)
        
        # Limiter la taille de l'audit trail
        if len(self.audit_trail) > 1000:
            self.audit_trail = self.audit_trail[-1000:]
    
    def get_audit_log(
        self,
        start_date: Optional[datetime.datetime] = None,
        end_date: Optional[datetime.datetime] = None,
        action_filter: Optional[str] = None
    ) -> List[AuditEntry]:
        """Récupère le journal d'audit filtré"""
        filtered = self.audit_trail
        
        if start_date:
            filtered = [e for e in filtered if e.timestamp >= start_date]
        
        if end_date:
            filtered = [e for e in filtered if e.timestamp <= end_date]
        
        if action_filter:
            filtered = [e for e in filtered if action_filter in e.action]
        
        return filtered
    
    # === Logging ===
    
    def log_info(self, message: str, **kwargs):
        """Journalise un message info"""
        structlog.get_logger().info(
            "business_value_calculator",
            calculation_id=self.calculation_id,
            message=message,
            **kwargs
        )
    
    def log_warning(self, message: str, **kwargs):
        """Journalise un message warning"""
        structlog.get_logger().warning(
            "business_value_calculator",
            calculation_id=self.calculation_id,
            message=message,
            **kwargs
        )
    
    def log_error(self, message: str, **kwargs):
        """Journalise un message error"""
        structlog.get_logger().error(
            "business_value_calculator",
            calculation_id=self.calculation_id,
            message=message,
            **kwargs
        )


# ====== Calculateur principal ======

class BusinessValueCalculator:
    """
    Calculateur principal de valeur business avec support asynchrone et caching.
    """
    
    def __init__(self, cache_enabled: bool = True):
        self.cache_enabled = cache_enabled
        self._calculations_cache: Dict[str, BusinessValueCalculation] = {}
        self.logger = structlog.get_logger(
            calculator_name="BusinessValueCalculator"
        )
    
    async def create_calculation(
        self,
        name: str,
        base_currency: Currency = Currency.USD,
        created_by: Optional[str] = None,
        **kwargs
    ) -> BusinessValueCalculation:
        """Crée un nouveau calcul de valeur"""
        calculation = BusinessValueCalculation(
            name=name,
            base_currency=base_currency,
            created_by=created_by,
            **kwargs
        )
        
        if self.cache_enabled:
            self._calculations_cache[calculation.calculation_id] = calculation
        
        self.logger.info(
            "calculation_created",
            calculation_id=calculation.calculation_id,
            name=name,
            base_currency=base_currency.value
        )
        
        return calculation
    
    async def get_calculation(self, calculation_id: str) -> Optional[BusinessValueCalculation]:
        """Récupère un calcul par son ID"""
        if self.cache_enabled and calculation_id in self._calculations_cache:
            return self._calculations_cache[calculation_id]
        
        # Ici, on pourrait charger depuis une base de données
        return None
    
    async def save_calculation(self, calculation: BusinessValueCalculation) -> None:
        """Sauvegarde un calcul"""
        if self.cache_enabled:
            self._calculations_cache[calculation.calculation_id] = calculation
        
        # Ici, on pourrait sauvegarder dans une base de données
        
        self.logger.info(
            "calculation_saved",
            calculation_id=calculation.calculation_id
        )
    
    async def calculate_comprehensive_roi(
        self,
        calculation: BusinessValueCalculation,
        include_monte_carlo: bool = True,
        include_comparative: bool = True
    ) -> Dict[str, Any]:
        """Calcule un ROI complet avec analyses optionnelles"""
        self.logger.info(
            "comprehensive_roi_started",
            calculation_id=calculation.calculation_id
        )
        
        results = {
            "calculation_id": calculation.calculation_id,
            "name": calculation.name,
            "calculation_date": datetime.datetime.utcnow().isoformat(),
            "results": {}
        }
        
        # 1. Calcul des métriques de base
        base_results = calculation.calculate_all_metrics()
        results["results"]["base_calculation"] = base_results
        
        # 2. Simulation Monte Carlo (si demandée)
        if include_monte_carlo:
            try:
                monte_carlo_results = await calculation.run_monte_carlo_simulation(
                    iterations=5000
                )
                results["results"]["monte_carlo"] = monte_carlo_results["analysis"]
                
                # Ajouter des insights de risque
                risk_insights = self._generate_risk_insights(
                    monte_carlo_results["analysis"]
                )
                results["results"]["risk_insights"] = risk_insights
                
            except Exception as e:
                self.logger.error(
                    "monte_carlo_failed",
                    calculation_id=calculation.calculation_id,
                    error=str(e)
                )
                results["results"]["monte_carlo"] = {"error": str(e)}
        
        # 3. Analyse comparative (si demandée)
        if include_comparative and calculation.alternatives:
            comparative_results = calculation.comparative_analysis()
            results["results"]["comparative_analysis"] = comparative_results
            
            # Recommandation
            results["results"]["recommendation"] = (
                comparative_results["comparison"]["best_option"]
            )
        
        # 4. Score de santé business
        business_health = self._calculate_business_health_score(
            base_results,
            results.get("results", {}).get("monte_carlo", {})
        )
        results["results"]["business_health_score"] = business_health
        
        # 5. Points d'action
        results["results"]["action_items"] = self._generate_action_items(
            base_results,
            calculation.risk_factors
        )
        
        self.logger.info(
            "comprehensive_roi_completed",
            calculation_id=calculation.calculation_id,
            business_health_score=business_health.get("overall_score", 0)
        )
        
        return results
    
    def _generate_risk_insights(self, monte_carlo_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Génère des insights de risque à partir de l'analyse Monte Carlo"""
        insights = {
            "high_risk_areas": [],
            "recommendations": [],
            "confidence_levels": {}
        }
        
        for metric, analysis in monte_carlo_analysis.items():
            # Identifier les métriques à haut risque
            coefficient_of_variation = analysis.get("coefficient_of_variation", 0)
            probability_positive = analysis.get("probability_positive", 0)
            
            if coefficient_of_variation > 0.5:
                insights["high_risk_areas"].append({
                    "metric": metric,
                    "coefficient_of_variation": coefficient_of_variation,
                    "risk_level": "high"
                })
            
            if probability_positive < 0.5:
                insights["high_risk_areas"].append({
                    "metric": metric,
                    "probability_positive": probability_positive,
                    "risk_level": "high"
                })
            
            # Niveaux de confiance
            if "percentiles" in analysis:
                insights["confidence_levels"][metric] = analysis["percentiles"]
        
        # Recommandations basées sur les risques
        if insights["high_risk_areas"]:
            insights["recommendations"].append(
                "Considérer des scénarios de mitigation pour les métriques à haut risque"
            )
            insights["recommendations"].append(
                "Collecter plus de données pour réduire l'incertitude"
            )
        else:
            insights["recommendations"].append(
                "Le profil de risque est acceptable, procéder avec le projet"
            )
        
        return insights
    
    def _calculate_business_health_score(
        self,
        base_results: Dict[str, Any],
        monte_carlo_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calcule un score de santé business global"""
        score = 0
        max_score = 100
        factors = []
        
        # 1. ROI (30 points)
        roi = base_results.get("summary", {}).get("total_roi", 0)
        roi_score = min(30, roi * 10)  # 10% ROI = 100% des points
        factors.append({"factor": "roi", "score": roi_score, "weight": 30})
        score += roi_score
        
        # 2. NPV (20 points)
        npv = base_results.get("summary", {}).get("npv", 0)
        npv_score = 0
        if npv > 0:
            npv_score = min(20, (npv / 100000) * 5)  # 100k NPV = 5 points
        factors.append({"factor": "npv", "score": npv_score, "weight": 20})
        score += npv_score
        
        # 3. Payback period (20 points)
        payback = base_results.get("summary", {}).get("payback_period", float('inf'))
        payback_score = 0
        if payback != float('inf'):
            if payback <= 1:
                payback_score = 20
            elif payback <= 3:
                payback_score = 15
            elif payback <= 5:
                payback_score = 10
            else:
                payback_score = 5
        factors.append({"factor": "payback_period", "score": payback_score, "weight": 20})
        score += payback_score
        
        # 4. Risk (20 points)
        risk_score = 20
        if monte_carlo_results:
            # Ajuster basé sur la probabilité de ROI positif
            roi_analysis = monte_carlo_results.get("roi", {})
            probability_positive = roi_analysis.get("probability_positive", 1)
            risk_score = probability_positive * 20
        factors.append({"factor": "risk", "score": risk_score, "weight": 20})
        score += risk_score
        
        # 5. Confidence (10 points)
        confidence_score = 10
        factors.append({"factor": "confidence", "score": confidence_score, "weight": 10})
        score += confidence_score
        
        # Score normalisé
        normalized_score = (score / max_score) * 100
        
        return {
            "overall_score": normalized_score,
            "max_score": max_score,
            "factors": factors,
            "interpretation": self._interpret_health_score(normalized_score)
        }
    
    def _interpret_health_score(self, score: float) -> str:
        """Interprète le score de santé"""
        if score >= 80:
            return "Excellent - Projet fortement recommandé"
        elif score >= 60:
            return "Bon - Projet recommandé avec quelques réserves"
        elif score >= 40:
            return "Modéré - Nécessite des améliorations avant approbation"
        elif score >= 20:
            return "Faible - Reconsidérer le projet"
        else:
            return "Critique - Projet non recommandé"
    
    def _generate_action_items(
        self,
        base_results: Dict[str, Any],
        risk_factors: List[RiskFactor]
    ) -> List[Dict[str, Any]]:
        """Génère des points d'action basés sur les résultats"""
        actions = []
        
        # Vérifier le ROI
        roi = base_results.get("summary", {}).get("total_roi", 0)
        if roi < 0.2:  # Moins de 20% ROI
            actions.append({
                "priority": "high",
                "action": "Améliorer les bénéfices ou réduire les coûts",
                "reason": f"ROI actuel ({roi:.1%}) en dessous du seuil de 20%",
                "owner": "Business Owner"
            })
        
        # Vérifier le payback period
        payback = base_results.get("summary", {}).get("payback_period", float('inf'))
        if payback > 3:  # Plus de 3 ans
            actions.append({
                "priority": "medium",
                "action": "Accélérer la réalisation des bénéfices",
                "reason": f"Période de retour ({payback:.1f} ans) trop longue",
                "owner": "Project Manager"
            })
        
        # Traiter les risques élevés
        high_risks = [r for r in risk_factors if r.risk_score > 50]
        for risk in high_risks:
            actions.append({
                "priority": "high",
                "action": f"Implémenter la stratégie de mitigation: {risk.mitigation_strategy}",
                "reason": f"Risque élevé détecté: {risk.name} (score: {risk.risk_score:.1f})",
                "owner": "Risk Manager"
            })
        
        # Vérifier les métriques sans baseline
        # (cette logique nécessiterait accès aux métriques originales)
        
        return actions
    
    async def batch_calculate(
        self,
        calculations: List[BusinessValueCalculation],
        parallel: bool = True
    ) -> Dict[str, Any]:
        """Exécute des calculs par lots"""
        results = {}
        
        if parallel:
            # Calcul parallèle asynchrone
            tasks = []
            for calc in calculations:
                task = asyncio.create_task(
                    self.calculate_comprehensive_roi(calc, include_monte_carlo=False)
                )
                tasks.append(task)
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, (calc, result) in enumerate(zip(calculations, batch_results)):
                if isinstance(result, Exception):
                    results[calc.calculation_id] = {"error": str(result)}
                else:
                    results[calc.calculation_id] = result
        else:
            # Calcul séquentiel
            for calc in calculations:
                try:
                    result = await self.calculate_comprehensive_roi(
                        calc, 
                        include_monte_carlo=False
                    )
                    results[calc.calculation_id] = result
                except Exception as e:
                    results[calc.calculation_id] = {"error": str(e)}
        
        # Analyse comparative entre les calculs
        if len(results) > 1:
            comparative = self._compare_batch_results(results)
            results["_batch_comparison"] = comparative
        
        return results
    
    def _compare_batch_results(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Compare les résultats d'un batch de calculs"""
        comparisons = []
        
        for calc_id, result in results.items():
            if "error" in result:
                continue
            
            summary = result.get("results", {}).get("base_calculation", {}).get("summary", {})
            comparisons.append({
                "calculation_id": calc_id,
                "name": result.get("name", "Unknown"),
                "roi": summary.get("total_roi", 0),
                "npv": summary.get("npv", 0),
                "payback_period": summary.get("payback_period", float('inf')),
                "investment": summary.get("total_investment", 0)
            })
        
        # Trier par ROI décroissant
        comparisons.sort(key=lambda x: x["roi"], reverse=True)
        
        return {
            "ranked_by_roi": comparisons,
            "best_overall": comparisons[0] if comparisons else None,
            "worst_overall": comparisons[-1] if comparisons else None,
            "average_roi": (
                sum(c["roi"] for c in comparisons) / len(comparisons) 
                if comparisons else 0
            )
        }


# ====== Factory et utilitaires ======

class CalculatorFactory:
    """Factory pour créer des calculateurs de valeur business"""
    
    @staticmethod
    def create_calculator(
        cache_enabled: bool = True,
        enable_monte_carlo: bool = True,
        enable_advanced_analytics: bool = True
    ) -> BusinessValueCalculator:
        """Crée un calculateur configuré"""
        calculator = BusinessValueCalculator(cache_enabled=cache_enabled)
        
        # Configuration additionnelle pourrait être ajoutée ici
        return calculator
    
    @staticmethod
    def create_quick_calculation(
        investment_amount: float,
        annual_benefits: float,
        years: int = 3,
        currency: Currency = Currency.USD
    ) -> BusinessValueCalculation:
        """Crée un calcul rapide avec des paramètres minimaux"""
        investment = MonetaryAmount(
            amount=Decimal(str(investment_amount)),
            currency=currency
        )
        
        benefits = MonetaryAmount(
            amount=Decimal(str(annual_benefits)),
            currency=currency
        )
        
        calculation = BusinessValueCalculation(
            name=f"Quick Calculation - {currency.value}",
            base_currency=currency,
            time_horizon_years=years
        )
        
        calculation.investments = [
            Investment(
                name="Initial Investment",
                amount=investment
            )
        ]
        
        calculation.value_metrics = [
            ValueMetric(
                category=ValueCategory.COST_AVOIDANCE,
                name="Annual Benefits",
                description="Estimated annual benefits",
                current_value=benefits,
                weight=1.0,
                confidence=0.8
            )
        ]
        
        return calculation


# ====== Export ======

__all__ = [
    # Enums
    "ValueCategory",
    "Currency",
    "TimePeriod",
    "RiskLevel",
    "ConfidenceLevel",
    
    # Dataclasses
    "MonetaryAmount",
    "TimeSeriesPoint",
    "ValueMetric",
    "BusinessAssumption",
    "Investment",
    "CashFlow",
    "RiskFactor",
    "ComparativeAlternative",
    "AuditEntry",
    
    # Modèles principaux
    "BusinessValueCalculation",
    "BusinessValueCalculator",
    
    # Factory
    "CalculatorFactory",
]