"""
Base suite module for MicroAgents Platform.
Defines the foundational Suite abstraction for orchestrating agent workflows.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, StrEnum
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
)

from pydantic import BaseModel, Field, validator

from ..agents.base.agent import BaseAgent, AgentResult, AgentContext
from ..agents.base.registry import AgentRegistry
from .pricing_strategy import PricingStrategy, StandardPricing, EnterprisePricing
from ..business_value.calculator import BusinessValueCalculator

logger = logging.getLogger(__name__)


class SuiteTier(StrEnum):
    """Suite pricing tiers."""
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    CUSTOM = "custom"


class ExecutionMode(StrEnum):
    """Agent execution modes."""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    BATCH = "batch"


class WorkflowState(StrEnum):
    """Workflow state machine states."""
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DependencyType(StrEnum):
    """Types of dependencies between agents."""
    REQUIRES = "requires"  # Agent A requires Agent B to complete
    TRIGGERS = "triggers"  # Agent A triggers Agent B
    ENHANCES = "enhances"  # Agent A enhances Agent B's capabilities
    CONFLICTS = "conflicts"  # Agent A conflicts with Agent B


@dataclass
class AgentDependency:
    """Represents a dependency between agents."""
    source_agent_id: str
    target_agent_id: str
    dependency_type: DependencyType
    condition: Optional[str] = None  # Optional condition for dependency
    timeout: Optional[timedelta] = None  # Optional timeout for dependency resolution


@dataclass
class SuiteMetrics:
    """Metrics tracking for suite execution."""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_agents_executed: int = 0
    successful_agents: int = 0
    failed_agents: int = 0
    average_execution_time: float = 0.0
    resource_usage: Dict[str, float] = field(default_factory=dict)
    sla_violations: int = 0
    cost_incurred: float = 0.0
    business_value_generated: float = 0.0
    
    @property
    def duration(self) -> Optional[timedelta]:
        """Calculate total duration if available."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate of agents."""
        if self.total_agents_executed == 0:
            return 0.0
        return self.successful_agents / self.total_agents_executed
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "duration": self.duration.total_seconds() if self.duration else None,
            "total_agents_executed": self.total_agents_executed,
            "success_rate": self.success_rate,
            "cost_incurred": self.cost_incurred,
            "business_value_generated": self.business_value_generated,
            "sla_violations": self.sla_violations,
        }


class SuiteConfiguration(BaseModel):
    """Configuration model for a suite."""
    
    class Config:
        arbitrary_types_allowed = True
    
    name: str = Field(..., description="Name of the suite")
    tier: SuiteTier = Field(SuiteTier.STARTER, description="Pricing tier")
    description: Optional[str] = None
    version: str = "1.0.0"
    
    # Agent configuration
    agent_ids: List[str] = Field(default_factory=list, description="List of agent IDs in suite")
    execution_mode: ExecutionMode = Field(ExecutionMode.SEQUENTIAL, description="Execution mode")
    max_concurrent_agents: int = Field(5, ge=1, le=100, description="Maximum concurrent agents")
    
    # Dependency configuration
    dependencies: List[AgentDependency] = Field(default_factory=list, description="Agent dependencies")
    
    # SLA configuration
    sla_target: float = Field(0.99, ge=0.0, le=1.0, description="SLA target (0-1)")
    max_execution_time: timedelta = Field(
        default_factory=lambda: timedelta(minutes=30),
        description="Maximum execution time for suite"
    )
    
    # Feature flags per tier
    feature_flags: Dict[str, bool] = Field(
        default_factory=lambda: {
            "auto_scaling": False,
            "advanced_analytics": False,
            "custom_workflows": False,
            "priority_support": False,
            "multi_region": False,
        },
        description="Feature flags enabled for this tier"
    )
    
    # Compliance requirements
    compliance_requirements: Set[str] = Field(
        default_factory=lambda: {"gdpr", "soc2"},
        description="Compliance requirements"
    )
    
    # Usage limits
    monthly_agent_executions: Optional[int] = Field(
        None, ge=0, description="Monthly agent execution limit"
    )
    data_processing_limit_gb: Optional[float] = Field(
        None, ge=0.0, description="Monthly data processing limit in GB"
    )
    
    @validator("feature_flags")
    def validate_feature_flags(cls, v: Dict[str, bool], values: Dict[str, Any]) -> Dict[str, bool]:
        """Validate feature flags based on tier."""
        tier = values.get("tier", SuiteTier.STARTER)
        
        # Tier-specific feature availability
        tier_features = {
            SuiteTier.STARTER: {"auto_scaling": False, "advanced_analytics": False},
            SuiteTier.PROFESSIONAL: {"multi_region": False},
            SuiteTier.ENTERPRISE: {},  # All features available
        }
        
        # Disable features not available for tier
        for feature, enabled in tier_features.get(tier, {}).items():
            if not enabled and v.get(feature, False):
                v[feature] = False
                logger.warning(f"Feature '{feature}' not available for tier {tier}, disabling")
        
        return v


class WorkflowStep(BaseModel):
    """Represents a step in the workflow."""
    
    class Config:
        arbitrary_types_allowed = True
    
    agent_id: str
    depends_on: List[str] = Field(default_factory=list)
    timeout: Optional[timedelta] = None
    retry_count: int = 0
    retry_delay: timedelta = Field(default_factory=lambda: timedelta(seconds=5))
    condition: Optional[str] = None  # Python expression for conditional execution
    priority: int = 0  # Higher priority executes first


class SuiteEvent(BaseModel):
    """Event emitted by suite during execution."""
    
    class Config:
        arbitrary_types_allowed = True
    
    event_type: str
    suite_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    workflow_state: Optional[WorkflowState] = None
    
    def to_json(self) -> str:
        """Convert event to JSON string."""
        return self.json()


class SuiteObserver(ABC):
    """Observer interface for suite events."""
    
    @abstractmethod
    async def on_suite_event(self, event: SuiteEvent) -> None:
        """Handle suite event."""
        pass


class AgentFactory(ABC):
    """Factory pattern for creating agents."""
    
    @abstractmethod
    def create_agent(self, agent_id: str, context: AgentContext) -> BaseAgent:
        """Create an agent instance."""
        pass
    
    @abstractmethod
    def get_agent_metadata(self, agent_id: str) -> Dict[str, Any]:
        """Get metadata for an agent."""
        pass


class DefaultAgentFactory(AgentFactory):
    """Default agent factory using AgentRegistry."""
    
    def __init__(self, registry: Optional[AgentRegistry] = None):
        self.registry = registry or AgentRegistry()
    
    def create_agent(self, agent_id: str, context: AgentContext) -> BaseAgent:
        """Create agent from registry."""
        agent_cls = self.registry.get_agent_class(agent_id)
        if not agent_cls:
            raise ValueError(f"Agent {agent_id} not found in registry")
        return agent_cls(context=context)
    
    def get_agent_metadata(self, agent_id: str) -> Dict[str, Any]:
        """Get metadata from registry."""
        return self.registry.get_agent_metadata(agent_id)


class SuiteBuilder:
    """Builder pattern for constructing suites."""
    
    def __init__(self, name: str):
        self.config = SuiteConfiguration(name=name)
        self._workflow_steps: List[WorkflowStep] = []
        self._feature_flags: Dict[str, bool] = {}
    
    def with_tier(self, tier: SuiteTier) -> SuiteBuilder:
        """Set suite tier."""
        self.config.tier = tier
        return self
    
    def with_agent(self, agent_id: str, priority: int = 0) -> SuiteBuilder:
        """Add an agent to the suite."""
        self.config.agent_ids.append(agent_id)
        step = WorkflowStep(agent_id=agent_id, priority=priority)
        self._workflow_steps.append(step)
        return self
    
    def with_dependency(self, source: str, target: str, 
                       dep_type: DependencyType = DependencyType.REQUIRES) -> SuiteBuilder:
        """Add a dependency between agents."""
        dependency = AgentDependency(
            source_agent_id=source,
            target_agent_id=target,
            dependency_type=dep_type
        )
        self.config.dependencies.append(dependency)
        return self
    
    def with_sla(self, sla_target: float, max_time: timedelta) -> SuiteBuilder:
        """Set SLA parameters."""
        self.config.sla_target = sla_target
        self.config.max_execution_time = max_time
        return self
    
    def with_feature_flag(self, feature: str, enabled: bool = True) -> SuiteBuilder:
        """Set a feature flag."""
        self._feature_flags[feature] = enabled
        return self
    
    def with_compliance(self, *requirements: str) -> SuiteBuilder:
        """Add compliance requirements."""
        self.config.compliance_requirements.update(requirements)
        return self
    
    def build(self) -> SuiteConfiguration:
        """Build the suite configuration."""
        # Apply feature flags
        self.config.feature_flags.update(self._feature_flags)
        
        # Build dependency graph for workflow steps
        self._build_workflow_dependencies()
        
        return self.config
    
    def _build_workflow_dependencies(self) -> None:
        """Build dependency relationships between workflow steps."""
        step_map = {step.agent_id: step for step in self._workflow_steps}
        
        for dep in self.config.dependencies:
            if dep.dependency_type == DependencyType.REQUIRES:
                # Target requires source to complete
                if dep.target_agent_id in step_map and dep.source_agent_id in step_map:
                    step_map[dep.target_agent_id].depends_on.append(dep.source_agent_id)
        
        # Sort steps by priority (higher first)
        self._workflow_steps.sort(key=lambda x: x.priority, reverse=True)


class BaseSuite(ABC):
    """
    Base suite class implementing the composite pattern.
    Orchestrates multiple agents into cohesive workflows.
    """
    
    # Strategy pattern for pricing
    _pricing_strategies: ClassVar[Dict[SuiteTier, Type[PricingStrategy]]] = {
        SuiteTier.STARTER: StandardPricing,
        SuiteTier.PROFESSIONAL: StandardPricing,
        SuiteTier.ENTERPRISE: EnterprisePricing,
    }
    
    def __init__(
        self,
        config: SuiteConfiguration,
        agent_factory: Optional[AgentFactory] = None,
        pricing_strategy: Optional[PricingStrategy] = None,
    ):
        self.config = config
        self.agent_factory = agent_factory or DefaultAgentFactory()
        self.pricing_strategy = pricing_strategy or self._create_pricing_strategy()
        
        # Workflow state
        self.state: WorkflowState = WorkflowState.INITIALIZING
        self.current_step: int = 0
        self.workflow_steps: List[WorkflowStep] = []
        
        # Execution tracking
        self.metrics = SuiteMetrics()
        self.agent_results: Dict[str, AgentResult] = {}
        self.execution_context: Dict[str, Any] = {}
        
        # Observer pattern for events
        self.observers: List[SuiteObserver] = []
        
        # Business value calculator
        self.business_value_calculator = BusinessValueCalculator()
        
        # Dependency resolution
        self.dependency_graph: Dict[str, List[str]] = defaultdict(list)
        self._build_dependency_graph()
        
        # Usage metering
        self.usage_meter: Dict[str, float] = defaultdict(float)
        
        # Initialize workflow
        self._initialize_workflow()
    
    def _create_pricing_strategy(self) -> PricingStrategy:
        """Factory method for pricing strategy based on tier."""
        strategy_cls = self._pricing_strategies.get(
            self.config.tier,
            StandardPricing
        )
        return strategy_cls()
    
    def _build_dependency_graph(self) -> None:
        """Build dependency graph from configuration."""
        for dep in self.config.dependencies:
            if dep.dependency_type == DependencyType.REQUIRES:
                self.dependency_graph[dep.target_agent_id].append(dep.source_agent_id)
    
    def _initialize_workflow(self) -> None:
        """Initialize workflow steps from configuration."""
        # Create workflow steps from agent IDs
        for agent_id in self.config.agent_ids:
            step = WorkflowStep(
                agent_id=agent_id,
                depends_on=self.dependency_graph.get(agent_id, []),
            )
            self.workflow_steps.append(step)
        
        # Sort by dependencies (topological sort)
        self._sort_workflow_steps()
        self.state = WorkflowState.READY
        
        # Emit initialization event
        self._emit_event("suite_initialized", {
            "agent_count": len(self.workflow_steps),
            "tier": self.config.tier,
        })
    
    def _sort_workflow_steps(self) -> None:
        """Sort workflow steps topologically based on dependencies."""
        visited = set()
        temp_visited = set()
        sorted_steps = []
        
        def visit(agent_id: str) -> None:
            if agent_id in temp_visited:
                raise ValueError(f"Circular dependency detected involving {agent_id}")
            if agent_id not in visited:
                temp_visited.add(agent_id)
                
                # Visit dependencies first
                step = next((s for s in self.workflow_steps if s.agent_id == agent_id), None)
                if step:
                    for dep in step.depends_on:
                        visit(dep)
                
                temp_visited.remove(agent_id)
                visited.add(agent_id)
                sorted_steps.append(step)
        
        # Visit all agents
        for step in self.workflow_steps:
            if step.agent_id not in visited:
                visit(step.agent_id)
        
        self.workflow_steps = [s for s in sorted_steps if s is not None]
    
    def add_observer(self, observer: SuiteObserver) -> None:
        """Add an observer for suite events."""
        self.observers.append(observer)
    
    def remove_observer(self, observer: SuiteObserver) -> None:
        """Remove an observer."""
        if observer in self.observers:
            self.observers.remove(observer)
    
    def _emit_event(self, event_type: str, data: Dict[str, Any], 
                   agent_id: Optional[str] = None) -> None:
        """Emit an event to all observers."""
        event = SuiteEvent(
            event_type=event_type,
            suite_id=self.config.name,
            data=data,
            agent_id=agent_id,
            workflow_state=self.state,
        )
        
        # Notify observers asynchronously
        async def notify_observers():
            for observer in self.observers:
                try:
                    await observer.on_suite_event(event)
                except Exception as e:
                    logger.error(f"Observer error: {e}")
        
        # Fire and forget
        asyncio.create_task(notify_observers())
    
    async def execute(self, initial_context: Optional[Dict[str, Any]] = None) -> SuiteMetrics:
        """
        Execute the suite workflow.
        
        Args:
            initial_context: Initial context data for agents
            
        Returns:
            Suite metrics from execution
        """
        if self.state != WorkflowState.READY:
            raise RuntimeError(f"Suite not ready for execution. Current state: {self.state}")
        
        self.state = WorkflowState.RUNNING
        self.metrics.start_time = datetime.utcnow()
        self.execution_context = initial_context or {}
        
        self._emit_event("suite_started", {
            "workflow_steps": len(self.workflow_steps),
            "execution_mode": self.config.execution_mode,
        })
        
        try:
            # Execute based on mode
            if self.config.execution_mode == ExecutionMode.SEQUENTIAL:
                await self._execute_sequential()
            elif self.config.execution_mode == ExecutionMode.PARALLEL:
                await self._execute_parallel()
            elif self.config.execution_mode == ExecutionMode.CONDITIONAL:
                await self._execute_conditional()
            elif self.config.execution_mode == ExecutionMode.BATCH:
                await self._execute_batch()
            
            self.state = WorkflowState.COMPLETED
            self._calculate_business_value()
            
        except asyncio.CancelledError:
            self.state = WorkflowState.CANCELLED
            self._emit_event("suite_cancelled", {"reason": "user_cancelled"})
            raise
            
        except Exception as e:
            self.state = WorkflowState.FAILED
            self._emit_event("suite_failed", {"error": str(e)})
            logger.error(f"Suite execution failed: {e}")
            raise
            
        finally:
            self.metrics.end_time = datetime.utcnow()
            self._update_usage_meter()
            self._check_sla_compliance()
            
            self._emit_event("suite_completed", {
                "metrics": self.metrics.to_dict(),
                "state": self.state,
            })
        
        return self.metrics
    
    async def _execute_sequential(self) -> None:
        """Execute agents sequentially."""
        for step in self.workflow_steps:
            await self._execute_agent_step(step)
    
    async def _execute_parallel(self) -> None:
        """Execute agents in parallel with concurrency limit."""
        semaphore = asyncio.Semaphore(self.config.max_concurrent_agents)
        
        async def execute_with_semaphore(step: WorkflowStep) -> None:
            async with semaphore:
                await self._execute_agent_step(step)
        
        tasks = [execute_with_semaphore(step) for step in self.workflow_steps]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _execute_conditional(self) -> None:
        """Execute agents based on conditions."""
        for step in self.workflow_steps:
            if step.condition:
                # Evaluate condition
                try:
                    condition_met = eval(
                        step.condition,
                        {"context": self.execution_context, "results": self.agent_results}
                    )
                    if not condition_met:
                        logger.info(f"Skipping agent {step.agent_id} due to condition")
                        continue
                except Exception as e:
                    logger.error(f"Condition evaluation failed for {step.agent_id}: {e}")
                    continue
            
            await self._execute_agent_step(step)
    
    async def _execute_batch(self) -> None:
        """Execute agents in batches."""
        batch_size = self.config.max_concurrent_agents
        for i in range(0, len(self.workflow_steps), batch_size):
            batch = self.workflow_steps[i:i + batch_size]
            tasks = [self._execute_agent_step(step) for step in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _execute_agent_step(self, step: WorkflowStep) -> None:
        """Execute a single agent step."""
        agent_id = step.agent_id
        
        # Check dependencies
        for dep_id in step.depends_on:
            if dep_id not in self.agent_results:
                raise ValueError(f"Dependency {dep_id} not satisfied for {agent_id}")
            if not self.agent_results[dep_id].success:
                logger.warning(f"Dependency {dep_id} failed, skipping {agent_id}")
                return
        
        self._emit_event("agent_started", {
            "step": self.current_step,
            "total_steps": len(self.workflow_steps),
        }, agent_id=agent_id)
        
        try:
            # Create agent context
            context = AgentContext(
                suite_id=self.config.name,
                execution_id=str(id(self)),
                data=self.execution_context.copy(),
                tier=self.config.tier,
                feature_flags=self.config.feature_flags,
            )
            
            # Create and execute agent
            agent = self.agent_factory.create_agent(agent_id, context)
            result = await agent.execute()
            
            # Store result
            self.agent_results[agent_id] = result
            
            # Update execution context with agent output
            if result.output:
                self.execution_context.update(result.output)
            
            # Update metrics
            self.metrics.total_agents_executed += 1
            if result.success:
                self.metrics.successful_agents += 1
            else:
                self.metrics.failed_agents += 1
            
            # Update usage
            self.usage_meter["agent_executions"] += 1
            if result.metadata and "execution_time" in result.metadata:
                self.usage_meter["execution_time_seconds"] += result.metadata["execution_time"]
            
            self._emit_event("agent_completed", {
                "success": result.success,
                "execution_time": result.metadata.get("execution_time", 0),
            }, agent_id=agent_id)
            
        except Exception as e:
            logger.error(f"Agent {agent_id} execution failed: {e}")
            self.agent_results[agent_id] = AgentResult(
                success=False,
                error=str(e),
                metadata={"exception": str(e)}
            )
            self.metrics.total_agents_executed += 1
            self.metrics.failed_agents += 1
            
            self._emit_event("agent_failed", {
                "error": str(e),
                "step": self.current_step,
            }, agent_id=agent_id)
        
        finally:
            self.current_step += 1
    
    def _calculate_business_value(self) -> None:
        """Calculate business value generated by suite execution."""
        try:
            value = self.business_value_calculator.calculate_suite_value(
                suite_config=self.config,
                agent_results=self.agent_results,
                metrics=self.metrics,
            )
            self.metrics.business_value_generated = value
        except Exception as e:
            logger.error(f"Failed to calculate business value: {e}")
            self.metrics.business_value_generated = 0.0
    
    def _update_usage_meter(self) -> None:
        """Update usage metering for billing."""
        # Calculate cost based on pricing strategy
        cost = self.pricing_strategy.calculate_cost(
            tier=self.config.tier,
            usage=self.usage_meter,
            feature_flags=self.config.feature_flags,
        )
        self.metrics.cost_incurred = cost
        
        # Check usage limits
        if self.config.monthly_agent_executions:
            remaining = self.config.monthly_agent_executions - self.usage_meter.get("agent_executions", 0)
            if remaining < 0:
                logger.warning(f"Monthly agent execution limit exceeded")
    
    def _check_sla_compliance(self) -> None:
        """Check SLA compliance based on metrics."""
        success_rate = self.metrics.success_rate
        
        if success_rate < self.config.sla_target:
            self.metrics.sla_violations += 1
            logger.warning(
                f"SLA violation detected: "
                f"success_rate={success_rate:.3f}, target={self.config.sla_target}"
            )
        
        # Check execution time SLA
        if self.metrics.duration and self.metrics.duration > self.config.max_execution_time:
            self.metrics.sla_violations += 1
            logger.warning(
                f"Execution time SLA violation: "
                f"duration={self.metrics.duration}, max={self.config.max_execution_time}"
            )
    
    def check_compliance(self) -> Dict[str, bool]:
        """Check compliance with configured requirements."""
        compliance_results = {}
        
        for requirement in self.config.compliance_requirements:
            try:
                # This would integrate with compliance checking agents
                # For now, return placeholder values
                compliance_results[requirement] = True
            except Exception as e:
                logger.error(f"Compliance check failed for {requirement}: {e}")
                compliance_results[requirement] = False
        
        return compliance_results
    
    def get_available_features(self) -> Dict[str, bool]:
        """Get available features for current tier."""
        return self.config.feature_flags.copy()
    
    def can_upgrade_tier(self, target_tier: SuiteTier) -> Tuple[bool, List[str]]:
        """
        Check if suite can be upgraded to target tier.
        
        Returns:
            Tuple of (can_upgrade, reasons)
        """
        reasons = []
        
        # Check if target tier is higher than current
        tier_order = list(SuiteTier)
        current_index = tier_order.index(self.config.tier)
        target_index = tier_order.index(target_tier)
        
        if target_index <= current_index:
            reasons.append(f"Target tier {target_tier} is not higher than current tier {self.config.tier}")
            return False, reasons
        
        # Check if any conflicting configurations
        # (This would be more comprehensive in a real implementation)
        
        return True, reasons
    
    def upgrade_tier(self, target_tier: SuiteTier) -> None:
        """Upgrade suite to target tier."""
        can_upgrade, reasons = self.can_upgrade_tier(target_tier)
        if not can_upgrade:
            raise ValueError(f"Cannot upgrade to {target_tier}: {', '.join(reasons)}")
        
        # Update configuration
        old_tier = self.config.tier
        self.config.tier = target_tier
        
        # Update pricing strategy
        self.pricing_strategy = self._create_pricing_strategy()
        
        # Enable additional features for new tier
        self._update_feature_flags_for_tier(target_tier)
        
        self._emit_event("tier_upgraded", {
            "from_tier": old_tier,
            "to_tier": target_tier,
            "new_features": list(self.config.feature_flags.keys()),
        })
    
    def _update_feature_flags_for_tier(self, tier: SuiteTier) -> None:
        """Update feature flags based on tier."""
        # Enable tier-specific features
        tier_features = {
            SuiteTier.PROFESSIONAL: ["advanced_analytics", "custom_workflows"],
            SuiteTier.ENTERPRISE: ["auto_scaling", "priority_support", "multi_region"],
        }
        
        for feature in tier_features.get(tier, []):
            self.config.feature_flags[feature] = True
    
    def pause(self) -> None:
        """Pause suite execution."""
        if self.state == WorkflowState.RUNNING:
            self.state = WorkflowState.PAUSED
            self._emit_event("suite_paused", {"current_step": self.current_step})
    
    def resume(self) -> None:
        """Resume suite execution."""
        if self.state == WorkflowState.PAUSED:
            self.state = WorkflowState.RUNNING
            self._emit_event("suite_resumed", {"current_step": self.current_step})
    
    def cancel(self) -> None:
        """Cancel suite execution."""
        if self.state in [WorkflowState.RUNNING, WorkflowState.PAUSED]:
            self.state = WorkflowState.CANCELLED
            self._emit_event("suite_cancelled", {"reason": "user_requested"})
    
    def get_status_report(self) -> Dict[str, Any]:
        """Generate comprehensive status report."""
        return {
            "suite": {
                "name": self.config.name,
                "tier": self.config.tier,
                "state": self.state,
                "current_step": self.current_step,
                "total_steps": len(self.workflow_steps),
            },
            "metrics": self.metrics.to_dict(),
            "usage": dict(self.usage_meter),
            "agent_results": {
                agent_id: {
                    "success": result.success,
                    "error": result.error,
                    "execution_time": result.metadata.get("execution_time", 0),
                }
                for agent_id, result in self.agent_results.items()
            },
            "compliance": self.check_compliance(),
            "available_features": self.get_available_features(),
        }


class SuiteComposite(BaseSuite):
    """
    Composite pattern implementation for nested suites.
    Allows suites to contain other suites.
    """
    
    def __init__(self, config: SuiteConfiguration, **kwargs):
        super().__init__(config, **kwargs)
        self.child_suites: List[BaseSuite] = []
    
    def add_child_suite(self, suite: BaseSuite) -> None:
        """Add a child suite to the composite."""
        self.child_suites.append(suite)
        # Add child suite's agents to workflow
        for agent_id in suite.config.agent_ids:
            if agent_id not in self.config.agent_ids:
                self.config.agent_ids.append(agent_id)
        
        # Rebuild workflow
        self._initialize_workflow()
    
    def remove_child_suite(self, suite: BaseSuite) -> None:
        """Remove a child suite from the composite."""
        if suite in self.child_suites:
            self.child_suites.remove(suite)
            # Rebuild workflow without child suite's agents
            self.config.agent_ids = [
                aid for aid in self.config.agent_ids 
                if aid in self._get_all_agent_ids()
            ]
            self._initialize_workflow()
    
    def _get_all_agent_ids(self) -> Set[str]:
        """Get all agent IDs from this suite and all child suites."""
        agent_ids = set(self.config.agent_ids)
        for child in self.child_suites:
            if isinstance(child, SuiteComposite):
                agent_ids.update(child._get_all_agent_ids())
            else:
                agent_ids.update(child.config.agent_ids)
        return agent_ids
    
    async def execute(self, initial_context: Optional[Dict[str, Any]] = None) -> SuiteMetrics:
        """Execute composite suite including all child suites."""
        # Execute child suites first (if any)
        child_metrics = []
        for child in self.child_suites:
            try:
                child_result = await child.execute(initial_context)
                child_metrics.append(child_result)
            except Exception as e:
                logger.error(f"Child suite execution failed: {e}")
        
        # Execute parent suite
        parent_metrics = await super().execute(initial_context)
        
        # Aggregate metrics
        for child_metric in child_metrics:
            parent_metrics.total_agents_executed += child_metric.total_agents_executed
            parent_metrics.successful_agents += child_metric.successful_agents
            parent_metrics.failed_agents += child_metric.failed_agents
            parent_metrics.cost_incurred += child_metric.cost_incurred
            parent_metrics.business_value_generated += child_metric.business_value_generated
        
        return parent_metrics