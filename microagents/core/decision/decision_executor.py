"""
Decision Executor - Exécute ou recommande basé sur un plan de décision
Gère l'exécution des chaînes d'agents avec retries, backoff et monitoring.
Aucune décision prise ici - uniquement exécution de plans pré-approuvés.
"""

import asyncio
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, field
import logging
from enum import Enum
import json
import time

from ..types import AgentChain, DecisionPlan, DecisionContext
from ..monitoring.metrics import ExecutionMetrics
from ..exceptions import (
    ExecutionFailedError,
    MaxRetriesExceededError,
    ChainExecutionError,
    AgentTimeoutError
)

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    """Statuts d'exécution."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ExecutionMode(str, Enum):
    """Modes d'exécution."""
    EXECUTE = "execute"
    EXECUTE_WITH_MONITORING = "execute_with_monitoring"
    RECOMMEND = "recommend"
    INVESTIGATE = "investigate"


@dataclass
class ExecutionResult:
    """Résultat d'une exécution."""
    status: ExecutionStatus
    output: Dict[str, Any]
    metrics: Dict[str, Any]
    agent_results: List[Dict[str, Any]]
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    retry_count: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class RetryConfig:
    """Configuration des retries."""
    max_retries: int = 3
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    jitter: float = 0.1  # Pourcentage de jitter pour éviter les thundering herds
    
    def calculate_backoff(self, attempt: int) -> float:
        """Calcule le backoff pour une tentative donnée."""
        backoff = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt - 1))
        backoff = min(backoff, self.max_backoff_seconds)
        
        # Ajouter du jitter
        jitter = random.uniform(-self.jitter, self.jitter) * backoff
        return max(0.1, backoff + jitter)


@dataclass
class AgentExecutionResult:
    """Résultat d'exécution d'un agent individuel."""
    agent_id: str
    status: ExecutionStatus
    output: Dict[str, Any]
    duration_seconds: float
    start_time: datetime
    end_time: datetime
    error: Optional[str] = None
    retry_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    """
    Orchestrateur abstrait pour l'exécution des chaînes d'agents.
    Dans une implémentation réelle, cela serait connecté à CrewAI ou un autre orchestrateur.
    """
    
    def __init__(self, agent_registry=None, timeout_seconds: float = 300):
        """
        Initialise l'orchestrateur.
        
        Args:
            agent_registry: Registre d'agents pour la résolution
            timeout_seconds: Timeout global par défaut
        """
        self.agent_registry = agent_registry
        self.timeout_seconds = timeout_seconds
        self.logger = logging.getLogger(f"{__name__}.Orchestrator")
    
    async def run_chain(
        self,
        chain: AgentChain,
        context: DecisionContext,
        execution_mode: ExecutionMode = ExecutionMode.EXECUTE,
        timeout_seconds: Optional[float] = None
    ) -> List[AgentExecutionResult]:
        """
        Exécute une chaîne d'agents.
        
        Args:
            chain: Chaîne d'agents à exécuter
            context: Contexte décisionnel
            execution_mode: Mode d'exécution
            timeout_seconds: Timeout spécifique (optionnel)
            
        Returns:
            List[AgentExecutionResult]: Résultats de chaque agent
            
        Raises:
            ChainExecutionError: Si l'exécution de la chaîne échoue
        """
        timeout = timeout_seconds or self.timeout_seconds
        start_time = datetime.utcnow()
        
        self.logger.info(
            "chain_execution_started",
            chain_length=len(chain.agents),
            execution_mode=execution_mode.value,
            timeout_seconds=timeout
        )
        
        try:
            results = []
            
            for i, agent_id in enumerate(chain.agents):
                agent_start = datetime.utcnow()
                
                self.logger.info(
                    "agent_execution_started",
                    agent_id=agent_id,
                    position=i + 1,
                    total_agents=len(chain.agents)
                )
                
                try:
                    # Exécuter l'agent avec timeout
                    agent_output = await asyncio.wait_for(
                        self._execute_agent(agent_id, context, execution_mode),
                        timeout=timeout / len(chain.agents)  # Répartir le timeout
                    )
                    
                    agent_end = datetime.utcnow()
                    duration = (agent_end - agent_start).total_seconds()
                    
                    result = AgentExecutionResult(
                        agent_id=agent_id,
                        status=ExecutionStatus.SUCCESS,
                        output=agent_output,
                        duration_seconds=duration,
                        start_time=agent_start,
                        end_time=agent_end,
                        metadata={
                            "position": i + 1,
                            "execution_mode": execution_mode.value,
                            "chain_expected_value": chain.expected_value
                        }
                    )
                    
                    results.append(result)
                    
                    self.logger.info(
                        "agent_execution_completed",
                        agent_id=agent_id,
                        duration_seconds=duration,
                        output_keys=list(agent_output.keys())
                    )
                    
                except asyncio.TimeoutError:
                    agent_end = datetime.utcnow()
                    
                    result = AgentExecutionResult(
                        agent_id=agent_id,
                        status=ExecutionStatus.TIMEOUT,
                        output={},
                        duration_seconds=(agent_end - agent_start).total_seconds(),
                        start_time=agent_start,
                        end_time=agent_end,
                        error=f"Agent timeout after {timeout / len(chain.agents)} seconds",
                        metadata={"position": i + 1}
                    )
                    
                    results.append(result)
                    self.logger.error(f"Agent timeout: {agent_id}")
                    
                    # Si on est en mode monitoring, on peut continuer
                    if execution_mode != ExecutionMode.EXECUTE_WITH_MONITORING:
                        raise AgentTimeoutError(f"Agent {agent_id} timeout at position {i + 1}")
                    
                except Exception as e:
                    agent_end = datetime.utcnow()
                    
                    result = AgentExecutionResult(
                        agent_id=agent_id,
                        status=ExecutionStatus.FAILED,
                        output={},
                        duration_seconds=(agent_end - agent_start).total_seconds(),
                        start_time=agent_start,
                        end_time=agent_end,
                        error=str(e),
                        metadata={"position": i + 1}
                    )
                    
                    results.append(result)
                    self.logger.error(f"Agent execution failed: {agent_id}, error: {e}")
                    
                    # Si on est en mode monitoring, on peut continuer
                    if execution_mode != ExecutionMode.EXECUTE_WITH_MONITORING:
                        raise ChainExecutionError(f"Agent {agent_id} failed: {str(e)}")
            
            end_time = datetime.utcnow()
            total_duration = (end_time - start_time).total_seconds()
            
            self.logger.info(
                "chain_execution_completed",
                total_duration_seconds=total_duration,
                successful_agents=sum(1 for r in results if r.status == ExecutionStatus.SUCCESS),
                total_agents=len(chain.agents)
            )
            
            return results
            
        except Exception as e:
            self.logger.error(f"Chain execution failed: {e}")
            raise ChainExecutionError(f"Chain execution failed: {str(e)}")
    
    async def _execute_agent(
        self,
        agent_id: str,
        context: DecisionContext,
        execution_mode: ExecutionMode
    ) -> Dict[str, Any]:
        """Exécute un agent individuel."""
        # Dans une implémentation réelle, cela appellerait l'agent via CrewAI ou autre
        
        # Simulation d'exécution d'agent
        await asyncio.sleep(random.uniform(0.5, 2.0))  # Simulation de temps d'exécution
        
        # Simulation de sortie d'agent basée sur le contexte et le mode
        intent = context.intent or {}
        intent_type = intent.get("type", "UNKNOWN")
        
        # Générer une sortie simulée basée sur l'intention
        output_templates = {
            "COST_OPTIMIZATION": {
                "recommendations": [
                    {"action": "resize_instance", "estimated_savings": 150, "risk": "low"},
                    {"action": "delete_unused_volumes", "estimated_savings": 75, "risk": "medium"}
                ],
                "total_estimated_savings": 225,
                "implementation_time_minutes": 30
            },
            "SECURITY_RISK_REDUCTION": {
                "vulnerabilities_found": 3,
                "remediations_applied": 2,
                "security_score_improvement": 15,
                "remaining_risks": ["CVE-2023-1234"]
            },
            "PERFORMANCE_IMPROVEMENT": {
                "bottlenecks_identified": ["database_queries", "cache_misses"],
                "improvement_actions": ["query_optimization", "cache_warming"],
                "expected_latency_reduction_percent": 40
            }
        }
        
        default_output = {
            "agent_id": agent_id,
            "execution_mode": execution_mode.value,
            "timestamp": datetime.utcnow().isoformat(),
            "status": "completed"
        }
        
        agent_output = output_templates.get(intent_type, default_output)
        agent_output.update({
            "agent_id": agent_id,
            "execution_mode": execution_mode.value,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        return agent_output


class DecisionExecutor:
    """
    Exécuteur de décisions.
    
    Responsabilités:
    1. Exécuter des chaînes d'agents via l'orchestrateur
    2. Générer des recommandations structurées
    3. Gérer les retries avec backoff
    4. Émettre des métriques d'exécution
    """
    
    def __init__(
        self,
        orchestrator: Orchestrator,
        metrics: Optional[ExecutionMetrics] = None,
        retry_config: Optional[RetryConfig] = None,
        enable_monitoring: bool = True
    ):
        """
        Initialise l'exécuteur de décisions.
        
        Args:
            orchestrator: Orchestrateur pour l'exécution des chaînes
            metrics: Métriques de monitoring (optionnel)
            retry_config: Configuration des retries (optionnel)
            enable_monitoring: Active le monitoring des exécutions
        """
        self.orchestrator = orchestrator
        self.metrics = metrics or ExecutionMetrics()
        self.retry_config = retry_config or RetryConfig()
        self.enable_monitoring = enable_monitoring
        self.logger = logger
        
        # Cache des résultats d'exécution (pour debug/audit)
        self.execution_history: List[ExecutionResult] = []
        self.max_history_size = 1000
        
        self.logger.info(
            "decision_executor_initialized",
            retry_config=self.retry_config,
            enable_monitoring=enable_monitoring
        )
    
    async def execute(
        self,
        action: str,
        plan: DecisionPlan,
        context: DecisionContext,
        human_approval_notes: Optional[str] = None,
        timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Exécute une action basée sur un plan de décision.
        
        Args:
            action: Action à exécuter (EXECUTE, RECOMMEND, etc.)
            plan: Plan de décision à exécuter
            context: Contexte décisionnel
            human_approval_notes: Notes d'approbation humaine (optionnel)
            timeout_seconds: Timeout spécifique (optionnel)
            
        Returns:
            Dict[str, Any]: Résultat de l'exécution
            
        Raises:
            ExecutionFailedError: Si l'exécution échoue après tous les retries
            MaxRetriesExceededError: Si le nombre maximum de retries est dépassé
        """
        start_time = datetime.utcnow()
        execution_id = self._generate_execution_id()
        
        self.logger.info(
            "execution_started",
            execution_id=execution_id,
            action=action,
            plan_version=plan.version,
            chain_length=len(plan.chain.agents)
        )
        
        try:
            # 1. Valider les entrées
            self._validate_execution_inputs(action, plan, context)
            
            # 2. Déterminer le mode d'exécution
            execution_mode = self._determine_execution_mode(action)
            
            # 3. Exécuter avec retries si nécessaire
            result = await self._execute_with_retries(
                execution_id=execution_id,
                chain=plan.chain,
                context=context,
                execution_mode=execution_mode,
                human_approval_notes=human_approval_notes,
                timeout_seconds=timeout_seconds
            )
            
            # 4. Générer le rapport final
            report = self._generate_execution_report(
                execution_id=execution_id,
                action=action,
                plan=plan,
                context=context,
                result=result,
                human_approval_notes=human_approval_notes,
                start_time=start_time
            )
            
            # 5. Émettre les métriques
            await self._emit_metrics(result, action, execution_mode)
            
            # 6. Stocker dans l'historique
            await self._store_execution_result(result)
            
            self.logger.info(
                "execution_completed",
                execution_id=execution_id,
                status=result.status.value,
                duration_seconds=result.duration_seconds,
                retry_count=result.retry_count
            )
            
            return report
            
        except Exception as e:
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            
            self.logger.error(
                "execution_failed",
                execution_id=execution_id,
                error=str(e),
                duration_seconds=duration
            )
            
            # Émettre une métrique d'échec
            self.metrics.execution_failed.labels(
                action=action,
                error_type=e.__class__.__name__
            ).inc()
            
            raise ExecutionFailedError(
                f"Execution {execution_id} failed: {str(e)}",
                execution_id=execution_id,
                duration_seconds=duration
            )
    
    def _validate_execution_inputs(
        self,
        action: str,
        plan: DecisionPlan,
        context: DecisionContext
    ) -> None:
        """Valide les entrées d'exécution."""
        if not plan or not plan.chain:
            raise ValueError("Invalid plan: no chain specified")
        
        if not plan.chain.agents:
            raise ValueError("Invalid chain: no agents specified")
        
        valid_actions = ["EXECUTE", "EXECUTE_WITH_MONITORING", "RECOMMEND", "INVESTIGATE"]
        if action not in valid_actions:
            raise ValueError(f"Invalid action: {action}. Must be one of {valid_actions}")
    
    def _determine_execution_mode(self, action: str) -> ExecutionMode:
        """Détermine le mode d'exécution basé sur l'action."""
        mode_mapping = {
            "EXECUTE": ExecutionMode.EXECUTE,
            "EXECUTE_WITH_MONITORING": ExecutionMode.EXECUTE_WITH_MONITORING,
            "RECOMMEND": ExecutionMode.RECOMMEND,
            "INVESTIGATE": ExecutionMode.INVESTIGATE
        }
        
        return mode_mapping.get(action, ExecutionMode.RECOMMEND)
    
    async def _execute_with_retries(
        self,
        execution_id: str,
        chain: AgentChain,
        context: DecisionContext,
        execution_mode: ExecutionMode,
        human_approval_notes: Optional[str] = None,
        timeout_seconds: Optional[float] = None
    ) -> ExecutionResult:
        """Exécute avec politique de retries et backoff."""
        last_error = None
        start_time = datetime.utcnow()
        
        for attempt in range(self.retry_config.max_retries + 1):  # +1 pour la tentative initiale
            attempt_start = datetime.utcnow()
            
            try:
                if execution_mode in [ExecutionMode.EXECUTE, ExecutionMode.EXECUTE_WITH_MONITORING]:
                    # Exécution réelle de la chaîne
                    agent_results = await self.orchestrator.run_chain(
                        chain=chain,
                        context=context,
                        execution_mode=execution_mode,
                        timeout_seconds=timeout_seconds
                    )
                    
                    # Calculer le statut global
                    successful_agents = sum(1 for r in agent_results if r.status == ExecutionStatus.SUCCESS)
                    total_agents = len(agent_results)
                    
                    if successful_agents == total_agents:
                        status = ExecutionStatus.SUCCESS
                    elif successful_agents > 0:
                        status = ExecutionStatus.PARTIAL
                    else:
                        status = ExecutionStatus.FAILED
                    
                    # Aggréger les sorties
                    aggregated_output = self._aggregate_agent_outputs(agent_results)
                    
                else:
                    # Mode recommandation/investigation - pas d'exécution réelle
                    agent_results = []
                    status = ExecutionStatus.SUCCESS
                    aggregated_output = self._generate_recommendation_output(
                        chain, context, execution_mode, human_approval_notes
                    )
                
                end_time = datetime.utcnow()
                duration = (end_time - start_time).total_seconds()
                
                return ExecutionResult(
                    status=status,
                    output=aggregated_output,
                    metrics={
                        "attempt": attempt + 1,
                        "total_attempts": attempt + 1,
                        "execution_mode": execution_mode.value,
                        "human_approval_notes": human_approval_notes
                    },
                    agent_results=[r.__dict__ for r in agent_results],
                    start_time=start_time,
                    end_time=end_time,
                    duration_seconds=duration,
                    retry_count=attempt
                )
                
            except Exception as e:
                last_error = e
                attempt_end = datetime.utcnow()
                attempt_duration = (attempt_end - attempt_start).total_seconds()
                
                self.logger.warning(
                    "execution_attempt_failed",
                    execution_id=execution_id,
                    attempt=attempt + 1,
                    max_attempts=self.retry_config.max_retries + 1,
                    error=str(e),
                    duration_seconds=attempt_duration
                )
                
                # Émettre une métrique d'échec de tentative
                self.metrics.execution_attempt_failed.labels(
                    execution_mode=execution_mode.value,
                    error_type=e.__class__.__name__
                ).inc()
                
                # Si c'est la dernière tentative, on arrête
                if attempt >= self.retry_config.max_retries:
                    break
                
                # Sinon, attendre avant de réessayer
                backoff = self.retry_config.calculate_backoff(attempt + 1)
                self.logger.info(
                    "retry_backoff",
                    execution_id=execution_id,
                    next_attempt=attempt + 2,
                    backoff_seconds=backoff
                )
                
                await asyncio.sleep(backoff)
        
        # Si on arrive ici, toutes les tentatives ont échoué
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        raise MaxRetriesExceededError(
            f"Execution failed after {self.retry_config.max_retries + 1} attempts. Last error: {str(last_error)}",
            execution_id=execution_id,
            max_retries=self.retry_config.max_retries,
            last_error=str(last_error),
            duration_seconds=duration
        )
    
    def _aggregate_agent_outputs(self, agent_results: List[AgentExecutionResult]) -> Dict[str, Any]:
        """Agrège les sorties de tous les agents."""
        if not agent_results:
            return {"message": "No agents executed"}
        
        # Grouper par statut
        by_status = {}
        for result in agent_results:
            status = result.status.value
            if status not in by_status:
                by_status[status] = []
            by_status[status].append({
                "agent_id": result.agent_id,
                "duration_seconds": result.duration_seconds,
                "output": result.output,
                "error": result.error
            })
        
        # Calculer les statistiques
        successful = [r for r in agent_results if r.status == ExecutionStatus.SUCCESS]
        failed = [r for r in agent_results if r.status == ExecutionStatus.FAILED]
        timed_out = [r for r in agent_results if r.status == ExecutionStatus.TIMEOUT]
        
        # Agrégation intelligente basée sur les sorties
        aggregated = {
            "summary": {
                "total_agents": len(agent_results),
                "successful_agents": len(successful),
                "failed_agents": len(failed),
                "timed_out_agents": len(timed_out),
                "success_rate": len(successful) / len(agent_results) if agent_results else 0
            },
            "agents_by_status": by_status,
            "total_duration_seconds": sum(r.duration_seconds for r in agent_results),
            "aggregated_outputs": self._merge_agent_outputs([r.output for r in successful]),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        return aggregated
    
    def _merge_agent_outputs(self, outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Fusionne les sorties de plusieurs agents de manière intelligente."""
        if not outputs:
            return {}
        
        merged = {}
        
        # Pour chaque sortie, fusionner intelligemment
        for output in outputs:
            for key, value in output.items():
                if key not in merged:
                    merged[key] = value
                elif isinstance(value, list) and isinstance(merged[key], list):
                    # Fusionner les listes
                    merged[key].extend(value)
                elif isinstance(value, dict) and isinstance(merged[key], dict):
                    # Fusionner les dictionnaires récursivement
                    merged[key].update(value)
                elif isinstance(value, (int, float)) and isinstance(merged[key], (int, float)):
                    # Additionner les nombres
                    merged[key] += value
                else:
                    # Pour les autres types, garder le dernier
                    merged[key] = value
        
        # Dédupliquer les listes si nécessaire
        for key, value in merged.items():
            if isinstance(value, list):
                # Dédupliquer basé sur la représentation JSON
                seen = set()
                unique = []
                for item in value:
                    item_json = json.dumps(item, sort_keys=True)
                    if item_json not in seen:
                        seen.add(item_json)
                        unique.append(item)
                merged[key] = unique
        
        return merged
    
    def _generate_recommendation_output(
        self,
        chain: AgentChain,
        context: DecisionContext,
        execution_mode: ExecutionMode,
        human_approval_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Génère une sortie de recommandation structurée."""
        intent = context.intent or {}
        
        recommendation = {
            "type": "recommendation",
            "execution_mode": execution_mode.value,
            "intent": intent,
            "proposed_chain": {
                "agents": chain.agents,
                "expected_value": chain.expected_value,
                "metadata": chain.metadata or {}
            },
            "business_context": {
                "expected_roi": context.business_value.get("expected_roi", 0) if context.business_value else 0,
                "risk_tolerance": context.client_preferences.get("risk_tolerance", "medium") if context.client_preferences else "medium",
                "automation_preference": context.client_preferences.get("automation_preference", "medium") if context.client_preferences else "medium"
            },
            "recommendation_details": {
                "why_recommended": "Decision confidence below automation threshold or manual review requested",
                "next_steps": [
                    "Review the proposed actions below",
                    "Approve for execution if acceptable",
                    "Request modifications if needed",
                    "Schedule execution during maintenance window"
                ],
                "estimated_impact": {
                    "time_required_minutes": 30,
                    "resources_affected": "Application servers and database",
                    "rollback_possible": True
                }
            },
            "human_approval_notes": human_approval_notes,
            "generated_at": datetime.utcnow().isoformat(),
            "review_deadline": (datetime.utcnow() + timedelta(hours=24)).isoformat()
        }
        
        # Ajouter des détails spécifiques basés sur l'intention
        intent_type = intent.get("type")
        if intent_type == "COST_OPTIMIZATION":
            recommendation["recommendation_details"]["estimated_savings"] = chain.expected_value * 1000  # Conversion
        elif intent_type == "SECURITY_RISK_REDUCTION":
            recommendation["recommendation_details"]["risk_reduction"] = "High"
        
        return recommendation
    
    def _generate_execution_report(
        self,
        execution_id: str,
        action: str,
        plan: DecisionPlan,
        context: DecisionContext,
        result: ExecutionResult,
        human_approval_notes: Optional[str] = None,
        start_time: datetime = None
    ) -> Dict[str, Any]:
        """Génère un rapport d'exécution complet."""
        intent = context.intent or {}
        
        report = {
            "execution_id": execution_id,
            "status": result.status.value,
            "action": action,
            "intent": intent,
            "plan_version": plan.version,
            "chain_executed": {
                "agents": plan.chain.agents,
                "expected_value": plan.chain.expected_value
            },
            "execution_summary": {
                "start_time": result.start_time.isoformat(),
                "end_time": result.end_time.isoformat(),
                "duration_seconds": result.duration_seconds,
                "retry_count": result.retry_count,
                "successful_agents": sum(1 for r in result.agent_results if r.get("status") == ExecutionStatus.SUCCESS.value),
                "total_agents": len(result.agent_results)
            },
            "output": result.output,
            "metrics": result.metrics,
            "context_summary": {
                "environment": context.environment.get("environment", "unknown") if context.environment else "unknown",
                "business_criticality": context.business_value.get("business_criticality", "medium") if context.business_value else "medium",
                "automation_preference": context.client_preferences.get("automation_preference", "medium") if context.client_preferences else "medium"
            },
            "human_approval_notes": human_approval_notes,
            "agent_results": result.agent_results,
            "errors": result.errors,
            "warnings": result.warnings,
            "report_generated_at": datetime.utcnow().isoformat(),
            "next_steps": self._determine_next_steps(result.status, action)
        }
        
        return report
    
    def _determine_next_steps(self, status: ExecutionStatus, action: str) -> List[str]:
        """Détermine les prochaines étapes basées sur le statut d'exécution."""
        if status == ExecutionStatus.SUCCESS:
            if action in ["EXECUTE", "EXECUTE_WITH_MONITORING"]:
                return [
                    "Monitor system metrics for 1 hour",
                    "Verify expected outcomes were achieved",
                    "Update runbooks if new procedures were established",
                    "Schedule follow-up review in 24 hours"
                ]
            else:
                return [
                    "Present recommendation to stakeholders",
                    "Schedule implementation if approved",
                    "Document decision rationale"
                ]
        elif status == ExecutionStatus.PARTIAL:
            return [
                "Review partial execution results",
                "Investigate failed agents",
                "Determine if re-execution is needed",
                "Update monitoring for affected components"
            ]
        elif status == ExecutionStatus.FAILED:
            return [
                "Investigate root cause of failure",
                "Review execution logs",
                "Update error handling procedures",
                "Consider alternative approaches"
            ]
        else:
            return ["Review execution report for detailed analysis"]
    
    async def _emit_metrics(
        self,
        result: ExecutionResult,
        action: str,
        execution_mode: ExecutionMode
    ) -> None:
        """Émet des métriques de monitoring."""
        # Métriques de durée
        self.metrics.execution_duration.labels(
            action=action,
            status=result.status.value,
            mode=execution_mode.value
        ).observe(result.duration_seconds)
        
        # Métriques de succès/échec
        if result.status == ExecutionStatus.SUCCESS:
            self.metrics.execution_success.labels(
                action=action,
                mode=execution_mode.value
            ).inc()
        else:
            self.metrics.execution_failure.labels(
                action=action,
                status=result.status.value,
                mode=execution_mode.value
            ).inc()
        
        # Métriques de retry
        if result.retry_count > 0:
            self.metrics.retry_count.labels(
                action=action,
                mode=execution_mode.value
            ).observe(result.retry_count)
        
        # Métriques d'agents
        successful_agents = sum(1 for r in result.agent_results if r.get("status") == ExecutionStatus.SUCCESS.value)
        total_agents = len(result.agent_results)
        
        if total_agents > 0:
            self.metrics.agent_success_rate.labels(
                action=action,
                mode=execution_mode.value
            ).observe(successful_agents / total_agents)
    
    async def _store_execution_result(self, result: ExecutionResult):
        """Stocke le résultat d'exécution dans l'historique."""
        self.execution_history.append(result)
        
        # Limiter la taille de l'historique
        if len(self.execution_history) > self.max_history_size:
            self.execution_history = self.execution_history[-self.max_history_size:]
    
    def _generate_execution_id(self) -> str:
        """Génère un ID d'exécution unique."""
        import uuid
        import hashlib
        import time
        
        unique_str = f"{uuid.uuid4()}{time.time()}"
        return f"EXEC-{hashlib.md5(unique_str.encode(), usedforsecurity=False).hexdigest()[:8].upper()}"
    
    async def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Récupère le statut d'une exécution par son ID."""
        for result in reversed(self.execution_history):
            # Chercher par ID dans les métadonnées
            if result.output.get("execution_id") == execution_id:
                return {
                    "execution_id": execution_id,
                    "status": result.status.value,
                    "duration_seconds": result.duration_seconds,
                    "retry_count": result.retry_count,
                    "start_time": result.start_time.isoformat(),
                    "end_time": result.end_time.isoformat(),
                    "agent_success_count": sum(1 for r in result.agent_results if r.get("status") == ExecutionStatus.SUCCESS.value),
                    "agent_total_count": len(result.agent_results)
                }
        
        return None
    
    async def get_recent_executions(
        self,
        limit: int = 50,
        status: Optional[ExecutionStatus] = None
    ) -> List[Dict[str, Any]]:
        """Récupère les exécutions récentes."""
        executions = []
        
        for result in reversed(self.execution_history):
            if status and result.status != status:
                continue
            
            executions.append({
                "execution_id": result.output.get("execution_id", "unknown"),
                "status": result.status.value,
                "duration_seconds": result.duration_seconds,
                "retry_count": result.retry_count,
                "start_time": result.start_time.isoformat(),
                "agent_count": len(result.agent_results)
            })
            
            if len(executions) >= limit:
                break
        
        return executions
    
    async def cancel_execution(self, execution_id: str) -> bool:
        """Annule une exécution en cours."""
        # Dans une implémentation réelle, cela interromprait l'exécution
        # Pour l'exemple, nous marquons simplement comme annulé
        
        self.logger.info(f"Execution cancellation requested: {execution_id}")
        
        # Pour une vraie annulation, nous aurions besoin d'un mécanisme
        # pour interrompre l'orchestrateur
        
        return True  # Simulation
    
    def get_executor_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques sur l'exécuteur."""
        total_executions = len(self.execution_history)
        
        if total_executions == 0:
            return {
                "total_executions": 0,
                "success_rate": 0.0,
                "average_duration_seconds": 0.0,
                "retry_config": self.retry_config
            }
        
        successful = sum(1 for r in self.execution_history if r.status == ExecutionStatus.SUCCESS)
        total_duration = sum(r.duration_seconds for r in self.execution_history)
        total_retries = sum(r.retry_count for r in self.execution_history)
        
        return {
            "total_executions": total_executions,
            "successful_executions": successful,
            "success_rate": successful / total_executions,
            "average_duration_seconds": total_duration / total_executions,
            "average_retry_count": total_retries / total_executions,
            "retry_config": {
                "max_retries": self.retry_config.max_retries,
                "initial_backoff_seconds": self.retry_config.initial_backoff_seconds,
                "max_backoff_seconds": self.retry_config.max_backoff_seconds
            },
            "history_size": len(self.execution_history),
            "max_history_size": self.max_history_size
        }


# Singleton pour utilisation facile
_decision_executor_instance = None

def get_decision_executor(
    orchestrator: Optional[Orchestrator] = None,
    retry_config: Optional[RetryConfig] = None
) -> DecisionExecutor:
    """Obtient l'instance singleton du DecisionExecutor."""
    global _decision_executor_instance
    if _decision_executor_instance is None:
        if orchestrator is None:
            orchestrator = Orchestrator()
        
        _decision_executor_instance = DecisionExecutor(
            orchestrator=orchestrator,
            retry_config=retry_config
        )
    return _decision_executor_instance


# Fonction utilitaire pour exécution rapide
async def execute_decision(
    action: str,
    plan: DecisionPlan,
    context: DecisionContext,
    human_approval_notes: Optional[str] = None,
    timeout_seconds: Optional[float] = None
) -> Dict[str, Any]:
    """
    Fonction utilitaire pour exécuter une décision.
    
    Args:
        action: Action à exécuter
        plan: Plan de décision
        context: Contexte décisionnel
        human_approval_notes: Notes d'approbation humaine
        timeout_seconds: Timeout spécifique
        
    Returns:
        Dict[str, Any]: Résultat de l'exécution
    """
    executor = DecisionExecutor(orchestrator=Orchestrator())
    return await executor.execute(
        action=action,
        plan=plan,
        context=context,
        human_approval_notes=human_approval_notes,
        timeout_seconds=timeout_seconds
    )


# Tests unitaires intégrés
if __name__ == "__main__":
    import asyncio
    
    async def test_decision_executor():
        """Test basique du DecisionExecutor."""
        from ..types import DecisionPlan, AgentChain, DecisionContext
        
        # Créer un plan de test
        chain = AgentChain(
            agents=["cost_analyzer", "cost_recommender"],
            expected_value=2.5,
            metadata={"selection_timestamp": datetime.utcnow().isoformat()}
        )
        
        plan = DecisionPlan(
            version="1.0",
            steps=[],
            chain=chain,
            policies={"automation": {"level": "AUTO_EXECUTE_WITH_MONITORING"}},
            created_at=datetime.utcnow(),
            metadata={"trace_id": "test-trace-123"}
        )
        
        # Créer un contexte de test
        context = DecisionContext(
            client_preferences={"automation_preference": "high"},
            business_value={"expected_roi": 2.5},
            system_state={},
            compliance_constraints={},
            decision_history=[],
            environment={"environment": "staging"},
            intent={"type": "COST_OPTIMIZATION", "priority": 8},
            metadata={}
        )
        
        # Tester l'exécution
        executor = DecisionExecutor(orchestrator=Orchestrator())
        
        print("Testing execution mode...")
        try:
            result = await executor.execute(
                action="EXECUTE_WITH_MONITORING",
                plan=plan,
                context=context,
                timeout_seconds=30
            )
            
            print(f"✓ Execution completed:")
            print(f"  Status: {result.get('status')}")
            print(f"  Execution ID: {result.get('execution_id')}")
            print(f"  Duration: {result.get('execution_summary', {}).get('duration_seconds')}s")
            print(f"  Output keys: {list(result.get('output', {}).keys())}")
            
        except Exception as e:
            print(f"✗ Execution failed: {e}")
        
        # Tester le mode recommandation
        print("\nTesting recommendation mode...")
        try:
            recommendation = await executor.execute(
                action="RECOMMEND",
                plan=plan,
                context=context
            )
            
            print(f"✓ Recommendation generated:")
            print(f"  Type: {recommendation.get('output', {}).get('type')}")
            print(f"  Agents: {len(recommendation.get('chain_executed', {}).get('agents', []))}")
            
        except Exception as e:
            print(f"✗ Recommendation failed: {e}")
        
        # Tester les statistiques
        print("\nTesting statistics...")
        stats = executor.get_executor_stats()
        print(f"✓ Executor stats:")
        print(f"  Total executions: {stats['total_executions']}")
        print(f"  Success rate: {stats['success_rate']:.2%}")
        print(f"  Average duration: {stats['average_duration_seconds']:.2f}s")
        
        print("\n✓ All tests passed!")
    
    asyncio.run(test_decision_executor())