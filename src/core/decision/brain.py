"""
Decision Brain - Autorité décisionnelle unique
Point d'entrée central orchestrant la machine à états décisionnelle.
"""

import asyncio
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import structlog

from ..exceptions import (
    DecisionTimeoutError,
    CircuitBreakerOpenError,
    StateTransitionError,
    RiskRejectedError,
    HumanRejectedError
)
from ..monitoring.metrics import DecisionMetrics
from ..circuit_breaker import CircuitBreaker
from ..types import (
    DecisionInput,
    DecisionOutcome,
    DecisionPlan,
    DecisionState,
    Intent,
    DecisionContext,
    AgentChain,
    AutomationDecision,
    AutomationLevel,
    RiskAssessment,
    HumanGateResponse
)


@dataclass
class BrainConfig:
    """Configuration du Decision Brain."""
    
    # Timeouts par transition (secondes)
    timeouts: Dict[DecisionState, float] = field(default_factory=lambda: {
        DecisionState.INIT: 0.0,
        DecisionState.INTENT_RESOLVED: 2.0,
        DecisionState.CONTEXT_BUILT: 5.0,
        DecisionState.CHAIN_SELECTED: 3.0,
        DecisionState.CONFIDENCE_COMPUTED: 2.0,
        DecisionState.AUTOMATION_DECIDED: 1.0,
        DecisionState.RISK_ASSESSED: 3.0,
        DecisionState.WAITING_HUMAN: 3600.0,  # 1 heure
        DecisionState.EXECUTED: 10.0,
        DecisionState.RISK_REJECTED: 0.0,
        DecisionState.HUMAN_REJECTED: 0.0,
        DecisionState.FAILED: 0.0,
    })
    
    # Circuit breaker configuration
    circuit_breaker_config: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "human_gate": {"max_failures": 3, "reset_timeout": 60},
        "external_registry": {"max_failures": 5, "reset_timeout": 30},
        "decision_executor": {"max_failures": 3, "reset_timeout": 45},
    })
    
    # Human gate configuration
    human_gate_timeout_hours: float = 1.0
    human_gate_required_levels: List[str] = field(default_factory=lambda: [
        AutomationLevel.AUTO_EXECUTE_WITH_MONITORING.value,
        AutomationLevel.RECOMMEND.value
    ])
    
    # Modules enable/disable
    enabled_modules: Dict[str, bool] = field(default_factory=lambda: {
        "intent_resolver": True,
        "context_builder": True,
        "chain_selector": True,
        "confidence_engine": True,
        "automation_policy": True,
        "risk_arbitrator": True,
        "human_gate": True,
        "decision_executor": True,
    })
    
    # Retry configuration
    max_retries: int = 3
    backoff_base: float = 1.5


@dataclass
class CachedDecisionState:
    """État d'une décision en cache pour reprise."""
    input_data: DecisionInput
    current_state: DecisionState
    decision_plan: Optional[DecisionPlan]
    context: Optional[DecisionContext]
    intent: Optional[Intent]
    chain: Optional[AgentChain]
    confidence_score: float = 0.0
    automation_decision: Optional[AutomationDecision] = None
    risk_assessment: Optional[RiskAssessment] = None
    start_time: datetime = field(default_factory=datetime.utcnow)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class DecisionBrain:
    """
    Orchestrateur principal de la machine à états décisionnelle.
    
    Responsabilités:
    1. Piloter la machine à états (DecisionState)
    2. Orchestrer les modules dans l'ordre strict
    3. Produire un DecisionPlan versionné
    4. Déléguer l'exécution (jamais exécuter ici)
    5. Logger chaque transition
    6. Retourner un DecisionOutcome traçable
    """
    
    def __init__(
        self,
        # Modules injectés via DI
        intent_resolver,
        context_builder,
        chain_selector,
        confidence_engine,
        automation_policy,
        risk_arbitrator,
        human_gate,
        decision_executor,
        decision_logger,
        # Configuration
        config: BrainConfig = None,
        metrics: DecisionMetrics = None
    ):
        self.intent_resolver = intent_resolver
        self.context_builder = context_builder
        self.chain_selector = chain_selector
        self.confidence_engine = confidence_engine
        self.automation_policy = automation_policy
        self.risk_arbitrator = risk_arbitrator
        self.human_gate = human_gate
        self.decision_executor = decision_executor
        self.decision_logger = decision_logger
        
        self.config = config or BrainConfig()
        self.metrics = metrics or DecisionMetrics()
        self.logger = structlog.get_logger(__name__)
        
        # Circuit breakers par dépendance critique
        self.circuit_breakers = {
            name: CircuitBreaker(name=name, **config)
            for name, config in self.config.circuit_breaker_config.items()
        }
        
        # Cache d'état pour reprise (distribué en prod via Redis)
        self._state_cache: Dict[str, CachedDecisionState] = {}
    
    async def decide(self, input_data: DecisionInput) -> DecisionOutcome:
        """
        Point d'entrée unique pour toute décision.
        
        Args:
            input_data: DecisionInput (texte, événement ou payload)
            
        Returns:
            DecisionOutcome: Résultat final avec justification et état
        """
        trace_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        
        # Logging structuré initial
        self.logger.info(
            "decision_started",
            trace_id=trace_id,
            input_type=input_data.__class__.__name__,
            raw_input_length=len(input_data.raw_input) if isinstance(input_data.raw_input, str) else 0
        )
        
        try:
            self.metrics.decision_started.inc()
            
            # ÉTAT INITIAL
            current_state = DecisionState.INIT
            await self._log_transition(
                trace_id=trace_id,
                from_state=None,
                to_state=current_state,
                reason="Decision started",
                input_summary=str(input_data)[:100]
            )
            
            # 1. RÉSOLUTION D'INTENTION
            intent = await self._execute_step(
                trace_id=trace_id,
                from_state=current_state,
                to_state=DecisionState.INTENT_RESOLVED,
                operation_name="intent_resolver.resolve",
                operation=lambda: self.intent_resolver.resolve(input_data),
                timeout=self.config.timeouts[DecisionState.INTENT_RESOLVED],
                enabled=self.config.enabled_modules["intent_resolver"],
                fallback=Intent(type="UNKNOWN", priority=5, success_metrics={})
            )
            current_state = DecisionState.INTENT_RESOLVED
            
            # 2. CONSTRUCTION DU CONTEXTE
            context = await self._execute_step(
                trace_id=trace_id,
                from_state=current_state,
                to_state=DecisionState.CONTEXT_BUILT,
                operation_name="context_builder.build",
                operation=lambda: self.context_builder.build(intent),
                timeout=self.config.timeouts[DecisionState.CONTEXT_BUILT],
                enabled=self.config.enabled_modules["context_builder"],
                fallback=DecisionContext()
            )
            current_state = DecisionState.CONTEXT_BUILT
            
            # 3. SÉLECTION DE CHAÎNE
            chain = await self._execute_step(
                trace_id=trace_id,
                from_state=current_state,
                to_state=DecisionState.CHAIN_SELECTED,
                operation_name="chain_selector.select",
                operation=lambda: self.chain_selector.select(intent, context),
                timeout=self.config.timeouts[DecisionState.CHAIN_SELECTED],
                enabled=self.config.enabled_modules["chain_selector"],
                fallback=AgentChain(agents=[], expected_value=0.0)
            )
            current_state = DecisionState.CHAIN_SELECTED
            
            # CRÉATION DU PLAN DÉCISIONNEL (versionné)
            decision_plan = DecisionPlan(
                version="1.0",
                steps=[
                    {"module": "intent_resolver", "result": intent.dict(), "timestamp": datetime.utcnow().isoformat()},
                    {"module": "context_builder", "result": "context_built", "timestamp": datetime.utcnow().isoformat()},
                    {"module": "chain_selector", "result": chain.dict(), "timestamp": datetime.utcnow().isoformat()},
                ],
                chain=chain,
                policies={},
                created_at=datetime.utcnow(),
                metadata={
                    "trace_id": trace_id,
                    "input_type": input_data.__class__.__name__,
                    "intent_type": intent.type,
                    "intent_priority": intent.priority
                }
            )
            
            # 4. CALCUL DE CONFIANCE
            confidence_score = await self._execute_step(
                trace_id=trace_id,
                from_state=current_state,
                to_state=DecisionState.CONFIDENCE_COMPUTED,
                operation_name="confidence_engine.compute",
                operation=lambda: self.confidence_engine.compute(chain, context),
                timeout=self.config.timeouts[DecisionState.CONFIDENCE_COMPUTED],
                enabled=self.config.enabled_modules["confidence_engine"],
                fallback=1.0
            )
            current_state = DecisionState.CONFIDENCE_COMPUTED
            decision_plan.steps.append({
                "module": "confidence_engine",
                "result": confidence_score,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # 5. POLITIQUE D'AUTOMATISATION
            automation_decision = await self._execute_step(
                trace_id=trace_id,
                from_state=current_state,
                to_state=DecisionState.AUTOMATION_DECIDED,
                operation_name="automation_policy.decide",
                operation=lambda: self.automation_policy.decide(confidence_score, context),
                timeout=self.config.timeouts[DecisionState.AUTOMATION_DECIDED],
                enabled=self.config.enabled_modules["automation_policy"],
                fallback=AutomationDecision(
                    level=AutomationLevel.AUTO_EXECUTE,
                    justification="Module disabled, defaulting to auto-execute"
                )
            )
            current_state = DecisionState.AUTOMATION_DECIDED
            decision_plan.policies["automation"] = automation_decision.dict()
            decision_plan.steps.append({
                "module": "automation_policy",
                "result": automation_decision.dict(),
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # 6. ARBITRAGE DES RISQUES
            risk_assessment = await self._execute_step(
                trace_id=trace_id,
                from_state=current_state,
                to_state=DecisionState.RISK_ASSESSED,
                operation_name="risk_arbitrator.assess",
                operation=lambda: self.risk_arbitrator.assess(automation_decision, context),
                timeout=self.config.timeouts[DecisionState.RISK_ASSESSED],
                enabled=self.config.enabled_modules["risk_arbitrator"],
                fallback=RiskAssessment(
                    approved=True,
                    justification="Module disabled",
                    blast_radius="UNKNOWN",
                    rollback_possible=True
                )
            )
            decision_plan.policies["risk"] = risk_assessment.dict()
            decision_plan.steps.append({
                "module": "risk_arbitrator",
                "result": risk_assessment.dict(),
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # Vérification des risques
            if not risk_assessment.approved:
                current_state = DecisionState.RISK_REJECTED
                raise RiskRejectedError(
                    f"Risk assessment rejected: {risk_assessment.justification}",
                    blast_radius=risk_assessment.blast_radius
                )
            
            # 7. HUMAN GATE (si requis)
            human_approval_required = False
            if (self.config.enabled_modules["human_gate"] and 
                automation_decision.level.value in self.config.human_gate_required_levels):
                
                human_gate_response = await self._execute_step(
                    trace_id=trace_id,
                    from_state=current_state,
                    to_state=DecisionState.WAITING_HUMAN,
                    operation_name="human_gate.check",
                    operation=lambda: self._execute_with_circuit_breaker(
                        "human_gate",
                        lambda: self.human_gate.check(
                            automation_decision,
                            context,
                            timeout=self.config.human_gate_timeout_hours * 3600
                        )
                    ),
                    timeout=self.config.timeouts[DecisionState.WAITING_HUMAN],
                    enabled=True,  # Si human_gate est enabled dans config
                    fallback=HumanGateResponse(
                        required=False,
                        approved=True,
                        justification="Human gate bypassed due to error"
                    )
                )
                
                if human_gate_response.required:
                    human_approval_required = True
                    
                    if not human_gate_response.approved:
                        current_state = DecisionState.HUMAN_REJECTED
                        raise HumanRejectedError(
                            f"Human gate rejected: {human_gate_response.justification}"
                        )
                    
                    # Stocker l'état pour reprise
                    await self._cache_decision_state(
                        trace_id=trace_id,
                        input_data=input_data,
                        current_state=DecisionState.WAITING_HUMAN,
                        decision_plan=decision_plan,
                        context=context,
                        intent=intent,
                        chain=chain,
                        confidence_score=confidence_score,
                        automation_decision=automation_decision,
                        risk_assessment=risk_assessment,
                        start_time=start_time
                    )
                    
                    # Retourner un outcome en attente
                    return await self._finalize_decision(
                        trace_id=trace_id,
                        start_time=start_time,
                        final_state=DecisionState.WAITING_HUMAN,
                        decision_plan=decision_plan,
                        action="AWAITING_APPROVAL",
                        confidence=confidence_score,
                        justification=f"Awaiting human approval. Summary: {human_gate_response.justification}",
                        details={
                            "requires_human_approval": True,
                            "human_gate_response": human_gate_response.dict()
                        }
                    )
            
            # 8. DÉTERMINATION DE L'ACTION FINALE
            if human_approval_required:
                final_action = "RECOMMEND"
            else:
                if automation_decision.level == AutomationLevel.AUTO_EXECUTE:
                    final_action = "EXECUTE"
                elif automation_decision.level == AutomationLevel.AUTO_EXECUTE_WITH_MONITORING:
                    final_action = "EXECUTE_WITH_MONITORING"
                elif automation_decision.level == AutomationLevel.RECOMMEND:
                    final_action = "RECOMMEND"
                else:  # INVESTIGATE
                    final_action = "INVESTIGATE"
            
            # 9. EXÉCUTION FINALE
            if self.config.enabled_modules["decision_executor"] and final_action.startswith("EXECUTE"):
                execution_result = await self._execute_step(
                    trace_id=trace_id,
                    from_state=current_state,
                    to_state=DecisionState.EXECUTED,
                    operation_name="decision_executor.execute",
                    operation=lambda: self.decision_executor.execute(
                        action=final_action,
                        plan=decision_plan,
                        context=context
                    ),
                    timeout=self.config.timeouts[DecisionState.EXECUTED],
                    enabled=True,
                    fallback={"status": "SKIPPED", "reason": "Execution module error"}
                )
                
                current_state = DecisionState.EXECUTED
                decision_plan.steps.append({
                    "module": "decision_executor",
                    "result": execution_result,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                return await self._finalize_decision(
                    trace_id=trace_id,
                    start_time=start_time,
                    final_state=current_state,
                    decision_plan=decision_plan,
                    action=final_action,
                    confidence=confidence_score,
                    justification=execution_result.get("justification", f"Executed with action: {final_action}"),
                    details=execution_result
                )
            else:
                # Pas d'exécution, seulement recommandation
                current_state = DecisionState.EXECUTED
                return await self._finalize_decision(
                    trace_id=trace_id,
                    start_time=start_time,
                    final_state=current_state,
                    decision_plan=decision_plan,
                    action=final_action,
                    confidence=confidence_score,
                    justification=f"Decision finalized without execution. Action: {final_action}",
                    details={
                        "execution_skipped": True,
                        "reason": "Execution module disabled or action doesn't require execution"
                    }
                )
                
        except RiskRejectedError as e:
            self.metrics.risk_rejected.inc()
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=start_time,
                final_state=DecisionState.RISK_REJECTED,
                decision_plan=decision_plan if 'decision_plan' in locals() else None,
                action="REJECTED",
                confidence=confidence_score if 'confidence_score' in locals() else 0.0,
                justification=f"Risk rejected: {str(e)}",
                details={"blast_radius": getattr(e, 'blast_radius', 'UNKNOWN')}
            )
            
        except HumanRejectedError as e:
            self.metrics.human_rejected.inc()
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=start_time,
                final_state=DecisionState.HUMAN_REJECTED,
                decision_plan=decision_plan if 'decision_plan' in locals() else None,
                action="REJECTED",
                confidence=confidence_score if 'confidence_score' in locals() else 0.0,
                justification=f"Human rejected: {str(e)}"
            )
            
        except asyncio.TimeoutError as e:
            self.metrics.decision_timeout.inc()
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=start_time,
                final_state=DecisionState.FAILED,
                decision_plan=decision_plan if 'decision_plan' in locals() else None,
                action="FAILED",
                confidence=0.0,
                justification=f"Timeout error: {str(e)}",
                details={"error_type": "timeout"}
            )
            
        except CircuitBreakerOpenError as e:
            self.metrics.circuit_breaker_open.inc()
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=start_time,
                final_state=DecisionState.FAILED,
                decision_plan=decision_plan if 'decision_plan' in locals() else None,
                action="FAILED",
                confidence=0.0,
                justification=f"Circuit breaker open: {str(e)}",
                details={"circuit_breaker": str(e)}
            )
            
        except Exception as e:
            self.metrics.decision_failed.inc()
            self.logger.error(
                "decision_failed",
                trace_id=trace_id,
                error=str(e),
                error_type=e.__class__.__name__
            )
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=start_time,
                final_state=DecisionState.FAILED,
                decision_plan=decision_plan if 'decision_plan' in locals() else None,
                action="FAILED",
                confidence=0.0,
                justification=f"Unexpected error: {str(e)}",
                details={
                    "error_type": e.__class__.__name__,
                    "error_message": str(e)
                }
            )
    
    async def _execute_step(
        self,
        trace_id: str,
        from_state: DecisionState,
        to_state: DecisionState,
        operation_name: str,
        operation,
        timeout: float,
        enabled: bool,
        fallback
    ) -> Any:
        """
        Exécute une étape de la machine à états avec gestion d'erreurs complète.
        """
        if not enabled:
            self.logger.info(
                "module_disabled",
                trace_id=trace_id,
                module=operation_name,
                state=to_state.value
            )
            await self._log_transition(
                trace_id=trace_id,
                from_state=from_state,
                to_state=to_state,
                reason=f"Module {operation_name} disabled, using fallback"
            )
            return fallback
        
        try:
            self.logger.debug(
                "step_started",
                trace_id=trace_id,
                from_state=from_state.value,
                to_state=to_state.value,
                operation=operation_name,
                timeout=timeout
            )
            
            result = await asyncio.wait_for(operation(), timeout=timeout)
            
            await self._log_transition(
                trace_id=trace_id,
                from_state=from_state,
                to_state=to_state,
                reason=f"Step {operation_name} completed successfully"
            )
            
            self.logger.debug(
                "step_completed",
                trace_id=trace_id,
                state=to_state.value,
                operation=operation_name
            )
            
            return result
            
        except asyncio.TimeoutError:
            await self._log_transition(
                trace_id=trace_id,
                from_state=from_state,
                to_state=DecisionState.FAILED,
                reason=f"Timeout in step {operation_name} after {timeout}s"
            )
            raise DecisionTimeoutError(operation_name, timeout)
            
        except Exception as e:
            await self._log_transition(
                trace_id=trace_id,
                from_state=from_state,
                to_state=DecisionState.FAILED,
                reason=f"Error in step {operation_name}: {str(e)}"
            )
            raise
    
    async def _execute_with_circuit_breaker(
        self,
        breaker_name: str,
        operation
    ) -> Any:
        """Exécute une opération avec circuit breaker."""
        if breaker_name not in self.circuit_breakers:
            return await operation()
        
        breaker = self.circuit_breakers[breaker_name]
        
        if breaker.is_open:
            raise CircuitBreakerOpenError(
                breaker_name,
                f"Circuit breaker {breaker_name} is open"
            )
        
        try:
            result = await breaker.call(operation)
            return result
        except Exception as e:
            self.logger.warning(
                "circuit_breaker_failure",
                breaker_name=breaker_name,
                error=str(e)
            )
            raise
    
    async def _log_transition(
        self,
        trace_id: str,
        from_state: Optional[DecisionState],
        to_state: DecisionState,
        reason: str,
        **extra
    ) -> None:
        """Logger une transition d'état structurée."""
        log_data = {
            "trace_id": trace_id,
            "from_state": from_state.value if from_state else None,
            "to_state": to_state.value,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            **extra
        }
        
        # Logging structuré
        self.logger.info("state_transition", **log_data)
        
        # Logging vers le decision logger
        await self.decision_logger.log_transition(**log_data)
        
        # Métriques
        self.metrics.state_transition.labels(
            from_state=from_state.value if from_state else "INIT",
            to_state=to_state.value
        ).inc()
    
    async def _cache_decision_state(
        self,
        trace_id: str,
        input_data: DecisionInput,
        current_state: DecisionState,
        decision_plan: Optional[DecisionPlan],
        context: DecisionContext,
        intent: Intent,
        chain: AgentChain,
        confidence_score: float,
        automation_decision: AutomationDecision,
        risk_assessment: RiskAssessment,
        start_time: datetime
    ) -> None:
        """Cache l'état d'une décision pour reprise ultérieure."""
        cached_state = CachedDecisionState(
            input_data=input_data,
            current_state=current_state,
            decision_plan=decision_plan,
            context=context,
            intent=intent,
            chain=chain,
            confidence_score=confidence_score,
            automation_decision=automation_decision,
            risk_assessment=risk_assessment,
            start_time=start_time,
            trace_id=trace_id
        )
        
        self._state_cache[trace_id] = cached_state
        
        self.logger.info(
            "decision_cached",
            trace_id=trace_id,
            state=current_state.value,
            cache_size=len(self._state_cache)
        )
    
    async def _finalize_decision(
        self,
        trace_id: str,
        start_time: datetime,
        final_state: DecisionState,
        decision_plan: Optional[DecisionPlan],
        action: str,
        confidence: float,
        justification: str,
        details: Optional[Dict] = None
    ) -> DecisionOutcome:
        """Finalise la décision et retourne l'outcome."""
        duration = (datetime.utcnow() - start_time).total_seconds()
        
        outcome = DecisionOutcome(
            action=action,
            confidence=confidence,
            justification=justification,
            trace_id=trace_id,
            final_state=final_state,
            duration_seconds=duration,
            plan=decision_plan,
            details=details or {},
            timestamp=datetime.utcnow()
        )
        
        # Logger l'outcome final
        await self.decision_logger.log_outcome(outcome)
        
        # Mettre à jour les métriques
        self.metrics.decision_completed.labels(
            state=final_state.value,
            action=action
        ).inc()
        self.metrics.decision_duration.observe(duration)
        
        # Nettoyer le cache si l'état est terminal
        if final_state not in [DecisionState.WAITING_HUMAN]:
            if trace_id in self._state_cache:
                del self._state_cache[trace_id]
        
        self.logger.info(
            "decision_finalized",
            trace_id=trace_id,
            final_state=final_state.value,
            action=action,
            confidence=confidence,
            duration=duration
        )
        
        return outcome
    
    async def resume_decision(
        self,
        trace_id: str,
        human_approval: bool = True,
        approval_notes: Optional[str] = None
    ) -> DecisionOutcome:
        """
        Reprendre une décision en attente d'approbation humaine.
        
        Args:
            trace_id: ID de la décision originale
            human_approval: Résultat de l'approbation humaine
            approval_notes: Notes additionnelles de l'approbateur
            
        Returns:
            DecisionOutcome mis à jour
        """
        # Récupérer l'état depuis le cache
        if trace_id not in self._state_cache:
            raise StateTransitionError(
                f"No decision found with trace_id {trace_id}"
            )
        
        cached_state = self._state_cache[trace_id]
        
        if cached_state.current_state != DecisionState.WAITING_HUMAN:
            raise StateTransitionError(
                f"Decision {trace_id} is not in WAITING_HUMAN state (current: {cached_state.current_state.value})"
            )
        
        self.logger.info(
            "decision_resuming",
            trace_id=trace_id,
            human_approval=human_approval,
            approval_notes=approval_notes
        )
        
        if not human_approval:
            # Transition vers HUMAN_REJECTED
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=cached_state.start_time,
                final_state=DecisionState.HUMAN_REJECTED,
                decision_plan=cached_state.decision_plan,
                action="REJECTED",
                confidence=cached_state.confidence_score,
                justification=f"Human approval denied. Notes: {approval_notes or 'No notes provided'}"
            )
        
        # Human approved - continuer avec l'exécution
        try:
            # Déterminer l'action finale basée sur la décision d'automatisation
            if cached_state.automation_decision.level == AutomationLevel.AUTO_EXECUTE:
                final_action = "EXECUTE"
            elif cached_state.automation_decision.level == AutomationLevel.AUTO_EXECUTE_WITH_MONITORING:
                final_action = "EXECUTE_WITH_MONITORING"
            else:
                final_action = "RECOMMEND"
            
            # Exécuter la décision
            execution_result = await self._execute_step(
                trace_id=trace_id,
                from_state=DecisionState.WAITING_HUMAN,
                to_state=DecisionState.EXECUTED,
                operation_name="decision_executor.execute_human_approved",
                operation=lambda: self.decision_executor.execute(
                    action=final_action,
                    plan=cached_state.decision_plan,
                    context=cached_state.context,
                    human_approval_notes=approval_notes
                ),
                timeout=self.config.timeouts[DecisionState.EXECUTED],
                enabled=self.config.enabled_modules["decision_executor"],
                fallback={"status": "FAILED", "reason": "Execution failed after human approval"}
            )
            
            # Mettre à jour le plan
            if cached_state.decision_plan:
                cached_state.decision_plan.steps.append({
                    "module": "human_gate_resume",
                    "result": {
                        "human_approval": True,
                        "approval_notes": approval_notes,
                        "timestamp": datetime.utcnow().isoformat()
                    },
                    "timestamp": datetime.utcnow().isoformat()
                })
                cached_state.decision_plan.steps.append({
                    "module": "decision_executor",
                    "result": execution_result,
                    "timestamp": datetime.utcnow().isoformat()
                })
            
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=cached_state.start_time,
                final_state=DecisionState.EXECUTED,
                decision_plan=cached_state.decision_plan,
                action=final_action,
                confidence=cached_state.confidence_score,
                justification=f"Human approved and executed. Notes: {approval_notes or 'No notes provided'}",
                details=execution_result
            )
            
        except Exception as e:
            self.logger.error(
                "resume_failed",
                trace_id=trace_id,
                error=str(e)
            )
            return await self._finalize_decision(
                trace_id=trace_id,
                start_time=cached_state.start_time,
                final_state=DecisionState.FAILED,
                decision_plan=cached_state.decision_plan,
                action="FAILED",
                confidence=cached_state.confidence_score,
                justification=f"Failed after human approval: {str(e)}"
            )
    
    def get_decision_status(self, trace_id: str) -> Optional[Dict]:
        """Récupérer le statut d'une décision en cours."""
        if trace_id not in self._state_cache:
            return None
        
        cached = self._state_cache[trace_id]
        return {
            "trace_id": trace_id,
            "current_state": cached.current_state.value,
            "intent": cached.intent.dict() if cached.intent else None,
            "confidence_score": cached.confidence_score,
            "automation_decision": cached.automation_decision.dict() if cached.automation_decision else None,
            "start_time": cached.start_time.isoformat(),
            "age_seconds": (datetime.utcnow() - cached.start_time).total_seconds()
        }
    
    async def cleanup_old_decisions(self, max_age_hours: int = 24):
        """Nettoie les décisions trop anciennes du cache."""
        now = datetime.utcnow()
        to_delete = []
        
        for trace_id, cached_state in self._state_cache.items():
            age_hours = (now - cached_state.start_time).total_seconds() / 3600
            if age_hours > max_age_hours:
                to_delete.append(trace_id)
        
        for trace_id in to_delete:
            del self._state_cache[trace_id]
        
        if to_delete:
            self.logger.info(
                "cache_cleaned",
                deleted_count=len(to_delete),
                remaining_count=len(self._state_cache)
            )


class DecisionBrainFactory:
    """Factory pour créer des instances de DecisionBrain avec DI."""
    
    @staticmethod
    def create(
        config: BrainConfig = None,
        metrics: DecisionMetrics = None,
        **dependencies
    ) -> DecisionBrain:
        """
        Crée une instance de DecisionBrain.
        
        Args:
            config: Configuration du brain
            metrics: Métriques de monitoring
            **dependencies: Modules injectés
            
        Returns:
            Instance de DecisionBrain
        """
        required_deps = [
            "intent_resolver",
            "context_builder",
            "chain_selector",
            "confidence_engine",
            "automation_policy",
            "risk_arbitrator",
            "human_gate",
            "decision_executor",
            "decision_logger"
        ]
        
        missing = [dep for dep in required_deps if dep not in dependencies]
        if missing:
            raise ValueError(f"Missing dependencies: {missing}")
        
        return DecisionBrain(
            **dependencies,
            config=config,
            metrics=metrics
        )


# Singleton pour les tests et développement
_brain_instance = None

def get_decision_brain() -> DecisionBrain:
    """Obtient l'instance singleton du DecisionBrain (pour tests)."""
    global _brain_instance
    if _brain_instance is None:
        raise RuntimeError("DecisionBrain not initialized. Use DecisionBrainFactory.create()")
    return _brain_instance

def set_decision_brain(instance: DecisionBrain) -> None:
    """Définit l'instance singleton (pour tests)."""
    global _brain_instance
    _brain_instance = instance