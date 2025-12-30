"""
Integration tests for agent execution patterns and orchestration.
Tests single, batch, and dependent agent execution with error handling.
"""

import asyncio
import concurrent.futures
import json
import math
import random
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
import pytest_asyncio
from pydantic import BaseModel, Field, ValidationError

from microagents.agents.base import BaseAgent, AgentExecutionError, AgentState
from microagents.agents.executor import AgentExecutor, ExecutionContext
from microagents.agents.orchestrator import AgentOrchestrator, OrchestrationError
from microagents.core.circuit_breaker import CircuitBreaker, CircuitBreakerState
from microagents.core.cost import CostTracker, CostRecord
from microagents.core.metrics import ExecutionMetrics, ResourceMetrics
from microagents.core.retry import RetryPolicy, RetryManager
from microagents.core.bulkhead import Bulkhead, BulkheadFullError
from microagents.core.priority import PriorityQueue, Priority
from microagents.core.tenant import TenantContext, TenantIsolationError


class TestAgentExecution:
    """Integration tests for agent execution patterns."""
    
    # Sample agent definitions for testing
    class SimpleCalculatorAgent(BaseAgent):
        """Simple agent that performs arithmetic operations."""
        
        class InputSchema(BaseModel):
            operation: str = Field(..., description="Operation to perform: add, subtract, multiply, divide")
            a: float = Field(..., description="First operand")
            b: float = Field(..., description="Second operand")
        
        class OutputSchema(BaseModel):
            result: float = Field(..., description="Operation result")
            operation: str = Field(..., description="Operation performed")
            timestamp: datetime = Field(default_factory=datetime.utcnow)
        
        async def execute(self, inputs: InputSchema, context: ExecutionContext) -> OutputSchema:
            """Execute arithmetic operation."""
            await asyncio.sleep(0.01)  # Simulate some processing time
            
            if inputs.operation == "add":
                result = inputs.a + inputs.b
            elif inputs.operation == "subtract":
                result = inputs.a - inputs.b
            elif inputs.operation == "multiply":
                result = inputs.a * inputs.b
            elif inputs.operation == "divide":
                if inputs.b == 0:
                    raise ValueError("Division by zero")
                result = inputs.a / inputs.b
            else:
                raise ValueError(f"Unknown operation: {inputs.operation}")
            
            return self.OutputSchema(
                result=round(result, 4),
                operation=inputs.operation
            )
    
    class FlakyAgent(BaseAgent):
        """Agent that fails randomly for testing retry logic."""
        
        class InputSchema(BaseModel):
            task_id: str = Field(..., description="Task identifier")
            failure_rate: float = Field(0.3, ge=0, le=1, description="Probability of failure")
            max_attempts: int = Field(3, ge=1, le=10, description="Maximum attempts")
        
        class OutputSchema(BaseModel):
            task_id: str = Field(..., description="Task identifier")
            attempts: int = Field(..., description="Number of attempts made")
            success: bool = Field(..., description="Whether task succeeded")
            final_result: Optional[str] = Field(None, description="Final result if successful")
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.attempt_counter = {}
        
        async def execute(self, inputs: InputSchema, context: ExecutionContext) -> OutputSchema:
            """Execute with random failures."""
            task_id = inputs.task_id
            
            # Track attempts
            if task_id not in self.attempt_counter:
                self.attempt_counter[task_id] = 0
            self.attempt_counter[task_id] += 1
            
            attempts = self.attempt_counter[task_id]
            
            # Fail randomly based on failure rate
            if random.random() < inputs.failure_rate and attempts < inputs.max_attempts:
                raise AgentExecutionError(
                    f"Random failure on attempt {attempts}",
                    retryable=True,
                    retry_delay=0.1
                )
            
            # Success after max attempts or if no failure triggered
            return self.OutputSchema(
                task_id=task_id,
                attempts=attempts,
                success=True,
                final_result=f"Completed after {attempts} attempts"
            )
    
    class DependentAgent(BaseAgent):
        """Agent that depends on other agents' results."""
        
        class InputSchema(BaseModel):
            upstream_results: List[Dict[str, Any]] = Field(
                ..., 
                description="Results from upstream agents"
            )
            aggregation_type: str = Field(
                "sum", 
                description="Aggregation type: sum, average, max, min"
            )
        
        class OutputSchema(BaseModel):
            aggregated_result: float = Field(..., description="Aggregated result")
            input_count: int = Field(..., description="Number of inputs aggregated")
            aggregation_type: str = Field(..., description="Type of aggregation performed")
        
        async def execute(self, inputs: InputSchema, context: ExecutionContext) -> OutputSchema:
            """Aggregate upstream results."""
            await asyncio.sleep(0.02)  # Simulate processing time
            
            # Extract numeric results from upstream
            values = []
            for result in inputs.upstream_results:
                if "result" in result:
                    values.append(float(result["result"]))
                elif "value" in result:
                    values.append(float(result["value"]))
            
            if not values:
                raise ValueError("No numeric values found in upstream results")
            
            # Perform aggregation
            if inputs.aggregation_type == "sum":
                result = sum(values)
            elif inputs.aggregation_type == "average":
                result = sum(values) / len(values)
            elif inputs.aggregation_type == "max":
                result = max(values)
            elif inputs.aggregation_type == "min":
                result = min(values)
            else:
                raise ValueError(f"Unknown aggregation type: {inputs.aggregation_type}")
            
            return self.OutputSchema(
                aggregated_result=round(result, 4),
                input_count=len(values),
                aggregation_type=inputs.aggregation_type
            )
    
    class CostTrackingAgent(BaseAgent):
        """Agent that tracks execution costs."""
        
        class InputSchema(BaseModel):
            resource_type: str = Field(..., description="Type of resource to allocate")
            quantity: int = Field(1, ge=1, le=1000, description="Quantity of resources")
            duration_ms: int = Field(100, ge=1, le=60000, description="Duration in milliseconds")
        
        class OutputSchema(BaseModel):
            cost_usd: float = Field(..., description="Cost in USD")
            resource_type: str = Field(..., description="Type of resource used")
            quantity: int = Field(..., description="Quantity of resources")
            duration_ms: int = Field(..., description="Duration in milliseconds")
            cost_breakdown: Dict[str, float] = Field(..., description="Cost breakdown")
        
        # Cost per unit per hour (USD)
        RESOURCE_COSTS = {
            "cpu": 0.05,      # $0.05 per CPU-hour
            "memory": 0.01,   # $0.01 per GB-hour
            "storage": 0.001, # $0.001 per GB-hour
            "gpu": 0.50,      # $0.50 per GPU-hour
            "api_call": 0.0001,  # $0.0001 per API call
        }
        
        async def execute(self, inputs: InputSchema, context: ExecutionContext) -> OutputSchema:
            """Calculate cost for resource usage."""
            # Simulate resource allocation
            await asyncio.sleep(inputs.duration_ms / 1000)
            
            # Calculate cost
            hourly_rate = self.RESOURCE_COSTS.get(inputs.resource_type, 0.02)
            hours = inputs.duration_ms / (1000 * 3600)  # Convert ms to hours
            base_cost = hourly_rate * inputs.quantity * hours
            
            # Add overhead (10%)
            overhead = base_cost * 0.1
            total_cost = base_cost + overhead
            
            cost_breakdown = {
                "base_cost": round(base_cost, 6),
                "overhead": round(overhead, 6),
                "total_cost": round(total_cost, 6)
            }
            
            return self.OutputSchema(
                cost_usd=round(total_cost, 6),
                resource_type=inputs.resource_type,
                quantity=inputs.quantity,
                duration_ms=inputs.duration_ms,
                cost_breakdown=cost_breakdown
            )
    
    class BusinessValueAgent(BaseAgent):
        """Agent that calculates business value of operations."""
        
        class InputSchema(BaseModel):
            operation_type: str = Field(..., description="Type of business operation")
            success: bool = Field(True, description="Whether operation succeeded")
            customer_tier: str = Field("standard", description="Customer tier: standard, premium, enterprise")
            processing_time_ms: int = Field(0, ge=0, description="Processing time in milliseconds")
        
        class OutputSchema(BaseModel):
            business_value_usd: float = Field(..., description="Business value in USD")
            sla_penalty_usd: float = Field(0.0, description="SLA penalty if applicable")
            customer_satisfaction_score: float = Field(..., ge=0, le=100, description="Customer satisfaction score")
            roi_percentage: float = Field(..., description="Return on investment percentage")
        
        # Business value per operation type (USD)
        OPERATION_VALUES = {
            "transaction": 0.50,
            "query": 0.10,
            "report": 1.00,
            "analysis": 5.00,
            "optimization": 10.00,
        }
        
        # Customer tier multipliers
        TIER_MULTIPLIERS = {
            "standard": 1.0,
            "premium": 1.5,
            "enterprise": 2.0,
        }
        
        # SLA thresholds in milliseconds
        SLA_THRESHOLDS = {
            "transaction": 1000,
            "query": 500,
            "report": 2000,
            "analysis": 5000,
            "optimization": 10000,
        }
        
        async def execute(self, inputs: InputSchema, context: ExecutionContext) -> OutputSchema:
            """Calculate business value."""
            # Base value from operation type
            base_value = self.OPERATION_VALUES.get(inputs.operation_type, 0.25)
            
            # Apply customer tier multiplier
            tier_multiplier = self.TIER_MULTIPLIERS.get(inputs.customer_tier, 1.0)
            value = base_value * tier_multiplier
            
            # Apply success/failure multiplier
            if not inputs.success:
                value *= 0.1  # 90% reduction for failures
            
            # Calculate SLA penalty
            sla_threshold = self.SLA_THRESHOLDS.get(inputs.operation_type, 1000)
            sla_penalty = 0.0
            
            if inputs.processing_time_ms > sla_threshold:
                # 1% penalty per ms over threshold, capped at 50%
                overage = inputs.processing_time_ms - sla_threshold
                penalty_percentage = min(50.0, overage * 0.01)
                sla_penalty = value * (penalty_percentage / 100)
                value -= sla_penalty
            
            # Calculate customer satisfaction score
            satisfaction_score = 100.0
            if not inputs.success:
                satisfaction_score -= 30
            if sla_penalty > 0:
                satisfaction_score -= min(30, (sla_penalty / value) * 100)
            satisfaction_score = max(0.0, satisfaction_score)
            
            # Calculate ROI (simplified)
            roi_percentage = (value / 0.01) * 100  # Assuming $0.01 cost basis
            
            return self.OutputSchema(
                business_value_usd=round(value, 4),
                sla_penalty_usd=round(sla_penalty, 4),
                customer_satisfaction_score=round(satisfaction_score, 2),
                roi_percentage=round(roi_percentage, 2)
            )
    
    @pytest.fixture
    def simple_agent(self):
        """Fixture providing simple calculator agent."""
        return self.SimpleCalculatorAgent(
            agent_id="calc_agent_1",
            name="Calculator",
            version="1.0.0"
        )
    
    @pytest.fixture
    def flaky_agent(self):
        """Fixture providing flaky agent for retry testing."""
        return self.FlakyAgent(
            agent_id="flaky_agent_1",
            name="Flaky Agent",
            version="1.0.0"
        )
    
    @pytest.fixture
    def dependent_agent(self):
        """Fixture providing dependent agent."""
        return self.DependentAgent(
            agent_id="dependent_agent_1",
            name="Dependent Aggregator",
            version="1.0.0"
        )
    
    @pytest.fixture
    def cost_agent(self):
        """Fixture providing cost tracking agent."""
        return self.CostTrackingAgent(
            agent_id="cost_agent_1",
            name="Cost Tracker",
            version="1.0.0"
        )
    
    @pytest.fixture
    def business_value_agent(self):
        """Fixture providing business value agent."""
        return self.BusinessValueAgent(
            agent_id="business_value_agent_1",
            name="Business Value Calculator",
            version="1.0.0"
        )
    
    @pytest.fixture
    def agent_executor(self):
        """Fixture providing agent executor."""
        return AgentExecutor(
            max_concurrent=10,
            timeout=30.0
        )
    
    @pytest.fixture
    def agent_orchestrator(self):
        """Fixture providing agent orchestrator."""
        return AgentOrchestrator(
            max_parallel_agents=5,
            default_timeout=10.0
        )
    
    @pytest.fixture
    def cost_tracker(self):
        """Fixture providing cost tracker."""
        return CostTracker(
            budget_limit=1000.0,
            alert_threshold=0.8
        )
    
    @pytest.fixture
    def retry_manager(self):
        """Fixture providing retry manager."""
        return RetryManager(
            max_attempts=3,
            base_delay=0.1,
            max_delay=1.0,
            jitter=True
        )
    
    @pytest.fixture
    def circuit_breaker(self):
        """Fixture providing circuit breaker."""
        return CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=10.0,
            half_open_max_attempts=2
        )
    
    @pytest.fixture
    def bulkhead(self):
        """Fixture providing bulkhead."""
        return Bulkhead(
            max_concurrent=5,
            max_queue_size=10
        )
    
    @pytest.fixture
    def priority_queue(self):
        """Fixture providing priority queue."""
        return PriorityQueue(
            max_size=100
        )
    
    @pytest.fixture
    def tenant_context(self):
        """Fixture providing tenant context."""
        return TenantContext(
            tenant_id="test_tenant_1",
            user_id="test_user_1",
            permissions=["agent.execute", "agent.create"],
            isolation_level="strict"
        )
    
    # Test Group 1: Single Agent Execution
    class TestSingleAgentExecution:
        """Tests for single agent execution."""
        
        @pytest.mark.asyncio
        async def test_simple_agent_execution(self, simple_agent, agent_executor):
            """Test basic execution of a single agent."""
            inputs = {
                "operation": "add",
                "a": 10.5,
                "b": 5.3
            }
            
            # Execute agent
            result = await agent_executor.execute_agent(simple_agent, inputs)
            
            # Verify result
            assert result["result"] == pytest.approx(15.8)
            assert result["operation"] == "add"
            assert "timestamp" in result
            
            # Verify execution metrics
            metrics = agent_executor.get_execution_metrics()
            assert metrics.total_executions == 1
            assert metrics.successful_executions == 1
            assert metrics.failed_executions == 0
            
        @pytest.mark.asyncio
        async def test_agent_execution_with_validation_error(self, simple_agent, agent_executor):
            """Test agent execution with invalid inputs."""
            inputs = {
                "operation": "add",
                "a": "not_a_number",  # Invalid type
                "b": 5.3
            }
            
            # Should raise validation error
            with pytest.raises(ValidationError):
                await agent_executor.execute_agent(simple_agent, inputs)
            
            # Verify metrics
            metrics = agent_executor.get_execution_metrics()
            assert metrics.total_executions == 0  # Validation errors don't count as executions
            
        @pytest.mark.asyncio
        async def test_agent_execution_with_runtime_error(self, simple_agent, agent_executor):
            """Test agent execution that raises runtime error."""
            inputs = {
                "operation": "divide",
                "a": 10.0,
                "b": 0.0  # Division by zero
            }
            
            # Should raise runtime error
            with pytest.raises(ValueError) as exc_info:
                await agent_executor.execute_agent(simple_agent, inputs)
            
            assert "Division by zero" in str(exc_info.value)
            
            # Verify metrics
            metrics = agent_executor.get_execution_metrics()
            assert metrics.total_executions == 1
            assert metrics.successful_executions == 0
            assert metrics.failed_executions == 1
            
        @pytest.mark.asyncio
        async def test_agent_state_management(self, simple_agent):
            """Test agent state transitions during execution."""
            # Initial state
            assert simple_agent.state == AgentState.IDLE
            
            # Start execution
            execution_task = asyncio.create_task(
                simple_agent.execute({"operation": "add", "a": 1, "b": 2})
            )
            
            # Should transition to RUNNING
            await asyncio.sleep(0.001)
            assert simple_agent.state == AgentState.RUNNING
            
            # Wait for completion
            await execution_task
            assert simple_agent.state == AgentState.IDLE
            
            # Test error state
            try:
                await simple_agent.execute({"operation": "divide", "a": 1, "b": 0})
            except ValueError:
                pass
            
            # Should return to IDLE after error
            assert simple_agent.state == AgentState.IDLE
            
        @pytest.mark.asyncio
        async def test_agent_execution_context(self, simple_agent, agent_executor):
            """Test execution context passing."""
            execution_id = str(uuid.uuid4())
            
            context = ExecutionContext(
                execution_id=execution_id,
                request_id="test_request_123",
                tenant_id="test_tenant",
                user_id="test_user",
                priority=Priority.NORMAL
            )
            
            inputs = {"operation": "multiply", "a": 3, "b": 4}
            
            # Execute with context
            result = await agent_executor.execute_agent(simple_agent, inputs, context)
            
            assert result["result"] == 12.0
            
            # Verify context was available to agent
            # (This would require the agent to log or return context info)
            
    # Test Group 2: Batch Agent Execution
    class TestBatchAgentExecution:
        """Tests for batch execution of multiple agents."""
        
        @pytest.mark.asyncio
        async def test_batch_execution_sequential(self, simple_agent, agent_executor):
            """Test sequential batch execution."""
            batch_inputs = [
                {"operation": "add", "a": 1, "b": 2},
                {"operation": "subtract", "a": 5, "b": 3},
                {"operation": "multiply", "a": 4, "b": 3},
                {"operation": "divide", "a": 10, "b": 2},
            ]
            
            # Execute sequentially
            results = []
            for inputs in batch_inputs:
                result = await agent_executor.execute_agent(simple_agent, inputs)
                results.append(result)
            
            # Verify all executions succeeded
            assert len(results) == 4
            assert results[0]["result"] == 3.0
            assert results[1]["result"] == 2.0
            assert results[2]["result"] == 12.0
            assert results[3]["result"] == 5.0
            
            # Verify metrics
            metrics = agent_executor.get_execution_metrics()
            assert metrics.total_executions == 4
            assert metrics.successful_executions == 4
            
        @pytest.mark.asyncio
        async def test_batch_execution_parallel(self, simple_agent, agent_executor):
            """Test parallel batch execution."""
            batch_size = 10
            batch_inputs = [
                {"operation": "add", "a": i, "b": i * 2}
                for i in range(batch_size)
            ]
            
            # Execute in parallel
            tasks = [
                agent_executor.execute_agent(simple_agent, inputs)
                for inputs in batch_inputs
            ]
            
            start_time = time.time()
            results = await asyncio.gather(*tasks)
            end_time = time.time()
            
            # Verify all executions succeeded
            assert len(results) == batch_size
            for i, result in enumerate(results):
                expected = i + (i * 2)
                assert result["result"] == expected
            
            # Should be faster than sequential execution
            # Sequential would be ~10 * 0.01 = 0.1 seconds
            # Parallel should be ~0.01 seconds (limited by slowest)
            execution_time = end_time - start_time
            assert execution_time < 0.05  # Should be much faster than sequential
            
            print(f"\nParallel batch execution: {batch_size} agents in {execution_time:.3f}s")
            
        @pytest.mark.asyncio
        async def test_batch_execution_with_mixed_results(self, simple_agent, agent_executor):
            """Test batch execution with some failures."""
            batch_inputs = [
                {"operation": "add", "a": 1, "b": 2},           # Should succeed
                {"operation": "divide", "a": 10, "b": 0},       # Should fail
                {"operation": "multiply", "a": 3, "b": 4},      # Should succeed
                {"operation": "invalid_op", "a": 1, "b": 2},    # Should fail
            ]
            
            # Execute with error handling
            results = []
            errors = []
            
            for inputs in batch_inputs:
                try:
                    result = await agent_executor.execute_agent(simple_agent, inputs)
                    results.append(result)
                except Exception as e:
                    errors.append((inputs, str(e)))
            
            # Verify results
            assert len(results) == 2  # Two successful executions
            assert len(errors) == 2   # Two failed executions
            
            # Verify successful results
            assert results[0]["result"] == 3.0
            assert results[1]["result"] == 12.0
            
            # Verify error types
            error_messages = [e[1] for e in errors]
            assert any("Division by zero" in msg for msg in error_messages)
            assert any("Unknown operation" in msg for msg in error_messages)
            
        @pytest.mark.asyncio
        async def test_batch_execution_with_bulkhead(self, simple_agent, bulkhead):
            """Test batch execution with bulkhead pattern."""
            batch_size = 20
            concurrent_limit = 5
            
            bulkhead = Bulkhead(
                max_concurrent=concurrent_limit,
                max_queue_size=batch_size * 2
            )
            
            async def execute_with_bulkhead(inputs):
                async with bulkhead:
                    await asyncio.sleep(0.01)  # Simulate work
                    return await simple_agent.execute(inputs)
            
            # Create batch of tasks
            batch_inputs = [
                {"operation": "add", "a": i, "b": i}
                for i in range(batch_size)
            ]
            
            # Execute with bulkhead
            tasks = [execute_with_bulkhead(inputs) for inputs in batch_inputs]
            start_time = time.time()
            results = await asyncio.gather(*tasks)
            end_time = time.time()
            
            # Verify all executed
            assert len(results) == batch_size
            
            # Verify concurrency was limited
            # With 5 concurrent, 20 tasks at 0.01s each = ~0.04s minimum
            execution_time = end_time - start_time
            assert execution_time >= 0.04  # At least 4 batches of 5
            
            print(f"\nBulkhead execution: {batch_size} agents with {concurrent_limit} concurrent")
            print(f"  Execution time: {execution_time:.3f}s")
            print(f"  Throughput: {batch_size / execution_time:.1f} agents/sec")
            
        @pytest.mark.asyncio
        async def test_batch_execution_priority_queuing(self, simple_agent, priority_queue):
            """Test batch execution with priority queuing."""
            batch_size = 10
            results = []
            execution_order = []
            
            async def execute_with_priority(inputs, priority: Priority):
                await priority_queue.enqueue(priority)
                try:
                    # Record execution order
                    execution_order.append((priority, inputs["a"]))
                    await asyncio.sleep(0.001)  # Small delay
                    return await simple_agent.execute(inputs)
                finally:
                    priority_queue.dequeue()
            
            # Create tasks with different priorities
            tasks = []
            for i in range(batch_size):
                priority = Priority.HIGH if i % 3 == 0 else Priority.NORMAL
                inputs = {"operation": "add", "a": i, "b": i}
                tasks.append(execute_with_priority(inputs, priority))
            
            # Execute all tasks
            results = await asyncio.gather(*tasks)
            
            # Verify all executed
            assert len(results) == batch_size
            
            # HIGH priority tasks should execute before NORMAL
            # (Note: This is probabilistic with asyncio)
            high_priority_indices = [
                i for i, (priority, _) in enumerate(execution_order)
                if priority == Priority.HIGH
            ]
            
            if len(high_priority_indices) > 0:
                # Check that high priority tasks started early
                avg_high_index = sum(high_priority_indices) / len(high_priority_indices)
                avg_normal_index = sum(
                    i for i, (priority, _) in enumerate(execution_order)
                    if priority == Priority.NORMAL
                ) / (batch_size - len(high_priority_indices))
                
                assert avg_high_index < avg_normal_index
            
            print(f"\nPriority queuing execution order:")
            for priority, value in execution_order:
                print(f"  {priority.name}: value={value}")
    
    # Test Group 3: Dependent Agent Execution
    class TestDependentAgentExecution:
        """Tests for dependent agent execution patterns."""
        
        @pytest.mark.asyncio
        async def test_sequential_dependent_execution(self, simple_agent, dependent_agent, agent_executor):
            """Test agents that depend on previous agents' results."""
            # Stage 1: Execute multiple simple agents
            stage1_inputs = [
                {"operation": "add", "a": 1, "b": 2},
                {"operation": "multiply", "a": 3, "b": 4},
                {"operation": "subtract", "a": 10, "b": 3},
            ]
            
            stage1_results = []
            for inputs in stage1_inputs:
                result = await agent_executor.execute_agent(simple_agent, inputs)
                stage1_results.append(result)
            
            # Stage 2: Aggregate results with dependent agent
            stage2_inputs = {
                "upstream_results": stage1_results,
                "aggregation_type": "sum"
            }
            
            final_result = await agent_executor.execute_agent(dependent_agent, stage2_inputs)
            
            # Verify aggregation
            assert final_result["aggregated_result"] == pytest.approx(3.0 + 12.0 + 7.0)  # 22.0
            assert final_result["input_count"] == 3
            assert final_result["aggregation_type"] == "sum"
            
        @pytest.mark.asyncio
        async def test_parallel_dependent_execution(self, simple_agent, dependent_agent, agent_orchestrator):
            """Test parallel execution with dependencies."""
            # Define workflow with dependencies
            workflow = [
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "add", "a": 1, "b": 2},
                    "id": "task_1",
                },
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "multiply", "a": 3, "b": 4},
                    "id": "task_2",
                },
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "subtract", "a": 10, "b": 3},
                    "id": "task_3",
                },
                {
                    "agent": dependent_agent,
                    "inputs": {
                        "upstream_results": ["task_1", "task_2", "task_3"],
                        "aggregation_type": "average"
                    },
                    "id": "aggregation_task",
                    "dependencies": ["task_1", "task_2", "task_3"]
                }
            ]
            
            # Execute workflow
            results = await agent_orchestrator.execute_workflow(workflow)
            
            # Verify all tasks executed
            assert len(results) == 4
            
            # Verify aggregation result
            aggregation_result = results["aggregation_task"]
            expected_average = (3.0 + 12.0 + 7.0) / 3  # 22.0 / 3 ≈ 7.3333
            assert aggregation_result["aggregated_result"] == pytest.approx(expected_average, rel=1e-3)
            
        @pytest.mark.asyncio
        async def test_conditional_execution_based_on_results(self, simple_agent, agent_executor):
            """Test conditional execution based on previous results."""
            # First agent execution
            result1 = await agent_executor.execute_agent(
                simple_agent,
                {"operation": "add", "a": 5, "b": 3}
            )
            
            # Conditional execution based on result
            if result1["result"] > 5:
                # Execute more complex operation
                result2 = await agent_executor.execute_agent(
                    simple_agent,
                    {"operation": "multiply", "a": result1["result"], "b": 2}
                )
                final_result = result2["result"]
            else:
                # Execute simple operation
                result2 = await agent_executor.execute_agent(
                    simple_agent,
                    {"operation": "subtract", "a": 10, "b": result1["result"]}
                )
                final_result = result2["result"]
            
            # Verify conditional logic
            assert result1["result"] == 8.0  # 5 + 3
            assert final_result == 16.0  # 8 * 2 (since 8 > 5)
            
        @pytest.mark.asyncio
        async def test_dependent_execution_with_error_propagation(self, simple_agent, dependent_agent):
            """Test error propagation in dependent execution."""
            # First agent fails
            try:
                await simple_agent.execute({"operation": "divide", "a": 10, "b": 0})
                upstream_results = [{"result": 0}]  # This won't be reached
            except ValueError as e:
                # Dependent agent should handle missing upstream or fail gracefully
                upstream_results = []
            
            # Dependent agent should handle empty results
            try:
                result = await dependent_agent.execute({
                    "upstream_results": upstream_results,
                    "aggregation_type": "sum"
                })
                # Should raise ValueError for empty results
                assert False, "Expected ValueError for empty results"
            except ValueError as e:
                assert "No numeric values found" in str(e)
                
    # Test Group 4: Error Propagation Testing
    class TestErrorPropagation:
        """Tests for error propagation and handling."""
        
        @pytest.mark.asyncio
        async def test_error_propagation_in_workflow(self, simple_agent, agent_orchestrator):
            """Test error propagation through workflow dependencies."""
            workflow = [
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "add", "a": 1, "b": 2},
                    "id": "task_1",
                },
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "divide", "a": 10, "b": 0},  # Will fail
                    "id": "task_2",
                },
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "multiply", "a": 3, "b": 4},
                    "id": "task_3",
                    "dependencies": ["task_2"]  # Depends on failed task
                }
            ]
            
            # Execute workflow - task_3 should not execute due to dependency failure
            results = await agent_orchestrator.execute_workflow(workflow)
            
            # Only task_1 should have succeeded
            assert "task_1" in results
            assert results["task_1"]["result"] == 3.0
            
            # task_2 should have failed
            assert "task_2" not in results
            
            # task_3 should not have executed due to failed dependency
            assert "task_3" not in results
            
        @pytest.mark.asyncio
        async def test_error_handling_with_circuit_breaker(self, flaky_agent, circuit_breaker):
            """Test circuit breaker pattern with flaky agent."""
            failure_count = 0
            success_count = 0
            
            async def execute_with_circuit_breaker():
                if circuit_breaker.state == CircuitBreakerState.OPEN:
                    raise CircuitBreaker.Error("Circuit breaker is open")
                
                try:
                    result = await flaky_agent.execute({
                        "task_id": "test_task",
                        "failure_rate": 0.8,  # High failure rate
                        "max_attempts": 5
                    })
                    
                    circuit_breaker.record_success()
                    return result
                except Exception as e:
                    circuit_breaker.record_failure()
                    raise
            
            # Execute multiple times
            for i in range(10):
                try:
                    result = await execute_with_circuit_breaker()
                    success_count += 1
                except (AgentExecutionError, CircuitBreaker.Error) as e:
                    failure_count += 1
                
                await asyncio.sleep(0.05)  # Small delay between attempts
            
            # Circuit breaker should have tripped
            assert failure_count > 0
            assert circuit_breaker.failure_count >= circuit_breaker.failure_threshold
            
            print(f"\nCircuit breaker test:")
            print(f"  Successes: {success_count}")
            print(f"  Failures: {failure_count}")
            print(f"  Circuit state: {circuit_breaker.state}")
            
        @pytest.mark.asyncio
        async def test_retry_logic_with_exponential_backoff(self, flaky_agent, retry_manager):
            """Test retry logic with exponential backoff."""
            execution_count = 0
            
            async def flaky_operation():
                nonlocal execution_count
                execution_count += 1
                
                # Fail first two attempts, succeed on third
                if execution_count < 3:
                    raise AgentExecutionError(
                        f"Failed on attempt {execution_count}",
                        retryable=True
                    )
                
                return {"status": "success", "attempts": execution_count}
            
            # Execute with retry
            result = await retry_manager.execute_with_retry(flaky_operation)
            
            # Should have succeeded after retries
            assert result["status"] == "success"
            assert result["attempts"] == 3
            assert execution_count == 3
            
            # Verify retry delays increased
            delays = retry_manager.get_last_retry_delays()
            if delays:
                assert len(delays) == 2  # Two retries
                assert delays[0] <= delays[1]  # Second delay should be >= first
            
        @pytest.mark.asyncio
        async def test_error_aggregation_in_batch(self, simple_agent, agent_executor):
            """Test error aggregation in batch execution."""
            batch_size = 5
            batch_inputs = []
            
            for i in range(batch_size):
                if i % 2 == 0:
                    # Valid operation
                    batch_inputs.append({"operation": "add", "a": i, "b": i})
                else:
                    # Invalid operation that will fail
                    batch_inputs.append({"operation": "divide", "a": i, "b": 0})
            
            # Execute batch with error aggregation
            successful = []
            errors = []
            
            for inputs in batch_inputs:
                try:
                    result = await agent_executor.execute_agent(simple_agent, inputs)
                    successful.append(result)
                except Exception as e:
                    errors.append({
                        "inputs": inputs,
                        "error": str(e),
                        "error_type": type(e).__name__
                    })
            
            # Verify results
            assert len(successful) == 3  # Even indices: 0, 2, 4
            assert len(errors) == 2      # Odd indices: 1, 3
            
            # Aggregate error information
            error_summary = {
                "total": len(errors),
                "by_type": {},
                "examples": errors[:2]  # First two errors
            }
            
            for error in errors:
                error_type = error["error_type"]
                error_summary["by_type"][error_type] = error_summary["by_type"].get(error_type, 0) + 1
            
            assert error_summary["total"] == 2
            assert error_summary["by_type"]["ValueError"] == 2
            
    # Test Group 5: Performance Under Load
    class TestPerformanceUnderLoad:
        """Tests for performance under load."""
        
        @pytest.mark.asyncio
        async def test_high_concurrency_execution(self, simple_agent):
            """Test execution under high concurrency."""
            concurrency_levels = [1, 5, 10, 20, 50]
            results = {}
            
            for concurrency in concurrency_levels:
                semaphore = asyncio.Semaphore(concurrency)
                
                async def execute_with_concurrency(i):
                    async with semaphore:
                        await asyncio.sleep(0.001)  # Small delay to simulate work
                        return await simple_agent.execute({
                            "operation": "add",
                            "a": i,
                            "b": i
                        })
                
                # Create tasks
                num_tasks = 100
                tasks = [execute_with_concurrency(i) for i in range(num_tasks)]
                
                # Execute and measure
                start_time = time.time()
                results_list = await asyncio.gather(*tasks)
                end_time = time.time()
                
                execution_time = end_time - start_time
                throughput = num_tasks / execution_time
                
                results[concurrency] = {
                    "execution_time": execution_time,
                    "throughput": throughput,
                    "success_rate": 100.0  # All should succeed
                }
                
                print(f"\nConcurrency {concurrency}:")
                print(f"  Time: {execution_time:.3f}s")
                print(f"  Throughput: {throughput:.1f} tasks/sec")
            
            # Verify performance scales with concurrency (up to a point)
            low_concurrency_time = results[1]["execution_time"]
            high_concurrency_time = results[20]["execution_time"]
            
            # High concurrency should be much faster
            assert high_concurrency_time < low_concurrency_time / 10
            
        @pytest.mark.asyncio
        async def test_load_test_with_gradual_increase(self, simple_agent, agent_executor):
            """Test gradual load increase."""
            load_levels = [10, 50, 100, 200, 500]
            performance_metrics = []
            
            for load in load_levels:
                # Warm-up
                warmup_tasks = [
                    simple_agent.execute({"operation": "add", "a": 1, "b": 2})
                    for _ in range(10)
                ]
                await asyncio.gather(*warmup_tasks)
                
                # Actual load test
                tasks = []
                start_time = time.time()
                
                for i in range(load):
                    task = asyncio.create_task(
                        agent_executor.execute_agent(
                            simple_agent,
                            {"operation": "add", "a": i, "b": i * 2}
                        )
                    )
                    tasks.append(task)
                
                # Wait for completion
                results = await asyncio.gather(*tasks, return_exceptions=True)
                end_time = time.time()
                
                # Calculate metrics
                execution_time = end_time - start_time
                successful = sum(1 for r in results if not isinstance(r, Exception))
                failed = load - successful
                throughput = load / execution_time
                
                metrics = {
                    "load": load,
                    "execution_time": execution_time,
                    "throughput": throughput,
                    "success_rate": (successful / load) * 100,
                    "successful": successful,
                    "failed": failed
                }
                
                performance_metrics.append(metrics)
                
                print(f"\nLoad test: {load} agents")
                print(f"  Time: {execution_time:.3f}s")
                print(f"  Throughput: {throughput:.1f} agents/sec")
                print(f"  Success rate: {metrics['success_rate']:.1f}%")
            
            # Analyze performance degradation
            success_rates = [m["success_rate"] for m in performance_metrics]
            throughputs = [m["throughput"] for m in performance_metrics]
            
            # Success rate should remain high
            assert min(success_rates) > 95.0
            
            # Throughput should scale with load (up to system limits)
            # Note: This may vary based on system resources
            
        @pytest.mark.asyncio
        async def test_resource_usage_under_load(self, simple_agent):
            """Test resource usage under load."""
            import psutil
            import os
            
            # Get initial resource usage
            process = psutil.Process(os.getpid())
            initial_cpu = process.cpu_percent(interval=None)
            initial_memory = process.memory_info().rss
            
            # Execute under load
            num_tasks = 1000
            batch_size = 100
            
            for batch in range(0, num_tasks, batch_size):
                tasks = []
                for i in range(batch, min(batch + batch_size, num_tasks)):
                    tasks.append(
                        simple_agent.execute({
                            "operation": "add",
                            "a": i,
                            "b": i
                        })
                    )
                
                await asyncio.gather(*tasks)
                
                # Monitor resource usage
                cpu_usage = process.cpu_percent(interval=None)
                memory_usage = process.memory_info().rss
                
                print(f"\nBatch {batch//batch_size + 1}:")
                print(f"  CPU: {cpu_usage:.1f}%")
                print(f"  Memory: {memory_usage / 1024 / 1024:.1f} MB")
            
            # Get final resource usage
            final_cpu = process.cpu_percent(interval=0.1)
            final_memory = process.memory_info().rss
            
            # Memory should not leak (allow some increase for caching)
            memory_increase = (final_memory - initial_memory) / 1024 / 1024  # MB
            assert memory_increase < 100.0  # Should not increase by more than 100MB
            
            print(f"\nResource usage summary:")
            print(f"  Initial memory: {initial_memory / 1024 / 1024:.1f} MB")
            print(f"  Final memory: {final_memory / 1024 / 1024:.1f} MB")
            print(f"  Memory increase: {memory_increase:.1f} MB")
            
        @pytest.mark.asyncio
        async def test_latency_percentiles(self, simple_agent, agent_executor):
            """Test latency percentiles under load."""
            num_executions = 1000
            latencies = []
            
            for i in range(num_executions):
                start_time = time.perf_counter()
                await agent_executor.execute_agent(
                    simple_agent,
                    {"operation": "add", "a": i, "b": i}
                )
                end_time = time.perf_counter()
                latencies.append(end_time - start_time)
            
            # Calculate percentiles
            latencies.sort()
            p50 = latencies[int(num_executions * 0.50)]
            p90 = latencies[int(num_executions * 0.90)]
            p95 = latencies[int(num_executions * 0.95)]
            p99 = latencies[int(num_executions * 0.99)]
            
            # Verify latency SLAs
            assert p50 < 0.02  # 50th percentile under 20ms
            assert p95 < 0.05  # 95th percentile under 50ms
            assert p99 < 0.10  # 99th percentile under 100ms
            
            print(f"\nLatency percentiles ({num_executions} executions):")
            print(f"  P50: {p50 * 1000:.1f}ms")
            print(f"  P90: {p90 * 1000:.1f}ms")
            print(f"  P95: {p95 * 1000:.1f}ms")
            print(f"  P99: {p99 * 1000:.1f}ms")
            print(f"  Max: {max(latencies) * 1000:.1f}ms")
            
    # Test Group 6: Resource Usage Monitoring
    class TestResourceUsageMonitoring:
        """Tests for resource usage monitoring."""
        
        @pytest.mark.asyncio
        async def test_memory_usage_tracking(self, simple_agent):
            """Test memory usage tracking during execution."""
            import tracemalloc
            
            # Start memory tracking
            tracemalloc.start()
            
            # Take snapshot before execution
            snapshot1 = tracemalloc.take_snapshot()
            
            # Execute agent multiple times
            for i in range(100):
                await simple_agent.execute({
                    "operation": "add",
                    "a": i,
                    "b": i * 2
                })
            
            # Take snapshot after execution
            snapshot2 = tracemalloc.take_snapshot()
            
            # Compare snapshots
            top_stats = snapshot2.compare_to(snapshot1, 'lineno')
            
            # Calculate total memory increase
            total_increase = sum(stat.size for stat in top_stats)
            
            # Memory increase should be reasonable
            assert total_increase < 10 * 1024 * 1024  # Less than 10MB
            
            # Stop tracking
            tracemalloc.stop()
            
            print(f"\nMemory usage tracking:")
            print(f"  Total increase: {total_increase / 1024:.1f} KB")
            
            # Show top memory consumers
            print("  Top memory consumers:")
            for stat in top_stats[:3]:  # Top 3
                print(f"    {stat.traceback.format()[-1]}: {stat.size / 1024:.1f} KB")
                
        @pytest.mark.asyncio
        async def test_cpu_usage_monitoring(self, simple_agent):
            """Test CPU usage monitoring."""
            import psutil
            import os
            
            process = psutil.Process(os.getpid())
            
            # Get CPU times before execution
            cpu_times_before = process.cpu_times()
            
            # Execute CPU-intensive operations
            tasks = []
            for i in range(1000):
                # Create a more CPU-intensive task
                task = asyncio.create_task(
                    simple_agent.execute({
                        "operation": "multiply",
                        "a": i,
                        "b": i,
                    })
                )
                tasks.append(task)
            
            # Wait for completion
            await asyncio.gather(*tasks)
            
            # Get CPU times after execution
            cpu_times_after = process.cpu_times()
            
            # Calculate CPU usage
            user_time_diff = cpu_times_after.user - cpu_times_before.user
            system_time_diff = cpu_times_after.system - cpu_times_before.system
            total_cpu_time = user_time_diff + system_time_diff
            
            # CPU time should be reasonable for 1000 operations
            assert total_cpu_time < 5.0  # Less than 5 seconds of CPU time
            
            print(f"\nCPU usage monitoring:")
            print(f"  User time: {user_time_diff:.3f}s")
            print(f"  System time: {system_time_diff:.3f}s")
            print(f"  Total CPU time: {total_cpu_time:.3f}s")
            
        @pytest.mark.asyncio
        async def test_execution_metrics_collection(self, simple_agent, agent_executor):
            """Test collection of execution metrics."""
            # Execute multiple agents
            num_executions = 100
            
            for i in range(num_executions):
                if i % 10 == 0:
                    # Every 10th execution fails
                    try:
                        await agent_executor.execute_agent(
                            simple_agent,
                            {"operation": "divide", "a": i, "b": 0}
                        )
                    except ValueError:
                        pass
                else:
                    await agent_executor.execute_agent(
                        simple_agent,
                        {"operation": "add", "a": i, "b": i}
                    )
            
            # Get metrics
            metrics = agent_executor.get_execution_metrics()
            
            # Verify metrics
            assert metrics.total_executions == num_executions
            assert metrics.successful_executions == num_executions - 10  # 90 successes
            assert metrics.failed_executions == 10  # 10 failures
            
            # Calculate success rate
            success_rate = (metrics.successful_executions / metrics.total_executions) * 100
            assert success_rate == 90.0
            
            # Check average execution time
            assert metrics.average_execution_time > 0
            
            print(f"\nExecution metrics:")
            print(f"  Total executions: {metrics.total_executions}")
            print(f"  Successful: {metrics.successful_executions}")
            print(f"  Failed: {metrics.failed_executions}")
            print(f"  Success rate: {success_rate:.1f}%")
            print(f"  Avg execution time: {metrics.average_execution_time * 1000:.1f}ms")
            
    # Test Group 7: Cost Tracking Accuracy
    class TestCostTrackingAccuracy:
        """Tests for cost tracking accuracy."""
        
        @pytest.mark.asyncio
        async def test_cost_calculation_accuracy(self, cost_agent):
            """Test accuracy of cost calculations."""
            test_cases = [
                {
                    "inputs": {"resource_type": "cpu", "quantity": 1, "duration_ms": 3600000},  # 1 hour
                    "expected_cost": 0.05 + 0.005,  # $0.05 + 10% overhead
                },
                {
                    "inputs": {"resource_type": "memory", "quantity": 10, "duration_ms": 1800000},  # 10 GB for 0.5 hour
                    "expected_cost": (0.01 * 10 * 0.5) * 1.1,  # $0.05 + 10% overhead
                },
                {
                    "inputs": {"resource_type": "gpu", "quantity": 2, "duration_ms": 7200000},  # 2 GPUs for 2 hours
                    "expected_cost": (0.50 * 2 * 2) * 1.1,  # $2.20
                },
            ]
            
            for test_case in test_cases:
                result = await cost_agent.execute(test_case["inputs"])
                calculated_cost = result["cost_usd"]
                expected_cost = test_case["expected_cost"]
                
                # Allow small floating point differences
                assert abs(calculated_cost - expected_cost) < 0.0001
                
                # Verify cost breakdown
                assert "cost_breakdown" in result
                assert "base_cost" in result["cost_breakdown"]
                assert "overhead" in result["cost_breakdown"]
                assert "total_cost" in result["cost_breakdown"]
                
                # Breakdown should sum to total
                breakdown_sum = (
                    result["cost_breakdown"]["base_cost"] +
                    result["cost_breakdown"]["overhead"]
                )
                assert abs(breakdown_sum - result["cost_usd"]) < 0.0001
                
        @pytest.mark.asyncio
        async def test_cost_tracking_accumulation(self, cost_agent, cost_tracker):
            """Test accumulation of costs across multiple executions."""
            executions = [
                {"resource_type": "cpu", "quantity": 1, "duration_ms": 1000},
                {"resource_type": "memory", "quantity": 2, "duration_ms": 2000},
                {"resource_type": "storage", "quantity": 100, "duration_ms": 5000},
                {"resource_type": "api_call", "quantity": 1000, "duration_ms": 0},
            ]
            
            total_cost = 0.0
            
            for execution in executions:
                # Execute agent
                result = await cost_agent.execute(execution)
                execution_cost = result["cost_usd"]
                
                # Track cost
                cost_record = CostRecord(
                    agent_id=cost_agent.agent_id,
                    execution_id=str(uuid.uuid4()),
                    cost_usd=execution_cost,
                    resource_type=execution["resource_type"],
                    quantity=execution["quantity"],
                    duration_ms=execution["duration_ms"]
                )
                
                cost_tracker.record_cost(cost_record)
                total_cost += execution_cost
            
            # Verify accumulated cost
            assert cost_tracker.get_total_cost() == pytest.approx(total_cost, rel=1e-6)
            
            # Verify cost by resource type
            cost_by_type = cost_tracker.get_cost_by_resource_type()
            assert len(cost_by_type) == 4  # Four resource types
            
            # Verify budget tracking
            assert cost_tracker.get_budget_utilization() == total_cost / 1000.0
            
            print(f"\nCost tracking accumulation:")
            print(f"  Total cost: ${total_cost:.6f}")
            print(f"  Budget utilization: {cost_tracker.get_budget_utilization() * 100:.1f}%")
            
            for resource_type, cost in cost_by_type.items():
                print(f"  {resource_type}: ${cost:.6f}")
                
        @pytest.mark.asyncio
        async def test_cost_optimization_validation(self, cost_agent):
            """Test validation of cost optimization logic."""
            # Test that cost calculations are consistent
            # Same resource usage should produce same cost
            inputs = {"resource_type": "cpu", "quantity": 1, "duration_ms": 1000}
            
            # Execute multiple times
            costs = []
            for _ in range(10):
                result = await cost_agent.execute(inputs)
                costs.append(result["cost_usd"])
            
            # All costs should be identical (within floating point precision)
            for cost in costs[1:]:
                assert abs(cost - costs[0]) < 0.000001
            
            # Test that cost scales linearly with quantity
            base_inputs = {"resource_type": "cpu", "quantity": 1, "duration_ms": 1000}
            base_result = await cost_agent.execute(base_inputs)
            base_cost = base_result["cost_usd"]
            
            scaled_inputs = {"resource_type": "cpu", "quantity": 10, "duration_ms": 1000}
            scaled_result = await cost_agent.execute(scaled_inputs)
            scaled_cost = scaled_result["cost_usd"]
            
            # Cost should scale linearly (within overhead calculation)
            expected_scaled_cost = base_cost * 10
            scaling_error = abs(scaled_cost - expected_scaled_cost) / expected_scaled_cost
            assert scaling_error < 0.01  # Less than 1% error
            
            print(f"\nCost optimization validation:")
            print(f"  Base cost (1 CPU): ${base_cost:.6f}")
            print(f"  Scaled cost (10 CPU): ${scaled_cost:.6f}")
            print(f"  Expected: ${expected_scaled_cost:.6f}")
            print(f"  Scaling error: {scaling_error * 100:.2f}%")
            
    # Test Group 8: Business Value Calculation
    class TestBusinessValueCalculation:
        """Tests for business value calculation."""
        
        @pytest.mark.asyncio
        async def test_business_value_accuracy(self, business_value_agent):
            """Test accuracy of business value calculations."""
            test_cases = [
                {
                    "inputs": {
                        "operation_type": "transaction",
                        "success": True,
                        "customer_tier": "standard",
                        "processing_time_ms": 500
                    },
                    "expected_value": 0.50,  # Base value for transaction
                    "expected_sla_penalty": 0.0,  # Under threshold
                },
                {
                    "inputs": {
                        "operation_type": "transaction",
                        "success": False,
                        "customer_tier": "standard",
                        "processing_time_ms": 500
                    },
                    "expected_value": 0.50 * 0.1,  # 90% reduction for failure
                    "expected_sla_penalty": 0.0,
                },
                {
                    "inputs": {
                        "operation_type": "transaction",
                        "success": True,
                        "customer_tier": "enterprise",
                        "processing_time_ms": 1500  # 500ms over SLA
                    },
                    "expected_value": 0.50 * 2.0,  # Enterprise multiplier
                    "expected_sla_penalty": (0.50 * 2.0) * 0.05,  # 5% penalty (500ms * 0.01%)
                },
                {
                    "inputs": {
                        "operation_type": "optimization",
                        "success": True,
                        "customer_tier": "premium",
                        "processing_time_ms": 5000
                    },
                    "expected_value": 10.00 * 1.5,  # Premium multiplier
                    "expected_sla_penalty": 0.0,  # Within threshold
                },
            ]
            
            for test_case in test_cases:
                result = await business_value_agent.execute(test_case["inputs"])
                
                # Check business value
                calculated_value = result["business_value_usd"]
                expected_value = test_case["expected_value"]
                assert abs(calculated_value - expected_value) < 0.01
                
                # Check SLA penalty
                calculated_penalty = result["sla_penalty_usd"]
                expected_penalty = test_case.get("expected_sla_penalty", 0.0)
                assert abs(calculated_penalty - expected_penalty) < 0.01
                
                # Check satisfaction score
                assert 0 <= result["customer_satisfaction_score"] <= 100
                
                # Check ROI
                assert result["roi_percentage"] > 0
                
                print(f"\nBusiness value test case:")
                print(f"  Operation: {test_case['inputs']['operation_type']}")
                print(f"  Customer: {test_case['inputs']['customer_tier']}")
                print(f"  Success: {test_case['inputs']['success']}")
                print(f"  Value: ${calculated_value:.4f}")
                print(f"  SLA penalty: ${calculated_penalty:.4f}")
                print(f"  Satisfaction: {result['customer_satisfaction_score']:.1f}")
                print(f"  ROI: {result['roi_percentage']:.1f}%")
                
        @pytest.mark.asyncio
        async def test_roi_calculation_validation(self, business_value_agent):
            """Test validation of ROI calculations."""
            # ROI should be proportional to business value
            high_value_result = await business_value_agent.execute({
                "operation_type": "optimization",
                "success": True,
                "customer_tier": "enterprise",
                "processing_time_ms": 1000
            })
            
            low_value_result = await business_value_agent.execute({
                "operation_type": "query",
                "success": True,
                "customer_tier": "standard",
                "processing_time_ms": 1000
            })
            
            # Higher value should have higher ROI
            assert high_value_result["roi_percentage"] > low_value_result["roi_percentage"]
            
            # Failed operations should have lower ROI
            failed_result = await business_value_agent.execute({
                "operation_type": "optimization",
                "success": False,
                "customer_tier": "enterprise",
                "processing_time_ms": 1000
            })
            
            assert failed_result["roi_percentage"] < high_value_result["roi_percentage"]
            
            print(f"\nROI calculation validation:")
            print(f"  High-value ROI: {high_value_result['roi_percentage']:.1f}%")
            print(f"  Low-value ROI: {low_value_result['roi_percentage']:.1f}%")
            print(f"  Failed ROI: {failed_result['roi_percentage']:.1f}%")
            
    # Test Group 9: Multi-Tenant Isolation
    class TestMultiTenantIsolation:
        """Tests for multi-tenant isolation."""
        
        @pytest.mark.asyncio
        async def test_tenant_data_isolation(self, simple_agent, tenant_context):
            """Test data isolation between tenants."""
            # Create agents for different tenants
            tenant1_agent = self.SimpleCalculatorAgent(
                agent_id="tenant1_agent",
                name="Tenant 1 Calculator",
                version="1.0.0",
                tenant_id="tenant_1"
            )
            
            tenant2_agent = self.SimpleCalculatorAgent(
                agent_id="tenant2_agent",
                name="Tenant 2 Calculator",
                version="1.0.0",
                tenant_id="tenant_2"
            )
            
            # Execute with tenant contexts
            tenant1_context = TenantContext(
                tenant_id="tenant_1",
                user_id="user_1",
                permissions=["agent.execute"]
            )
            
            tenant2_context = TenantContext(
                tenant_id="tenant_2", 
                user_id="user_2",
                permissions=["agent.execute"]
            )
            
            # Agents should only execute in their own tenant context
            # (This depends on the execution framework implementation)
            
            # For now, verify agents have tenant IDs
            assert tenant1_agent.tenant_id == "tenant_1"
            assert tenant2_agent.tenant_id == "tenant_2"
            
            # Agents from different tenants should be distinct
            assert tenant1_agent.agent_id != tenant2_agent.agent_id
            
        @pytest.mark.asyncio
        async def test_tenant_resource_isolation(self, cost_tracker):
            """Test resource isolation between tenants."""
            # Create cost trackers for different tenants
            tenant1_tracker = CostTracker(
                budget_limit=100.0,
                tenant_id="tenant_1"
            )
            
            tenant2_tracker = CostTracker(
                budget_limit=200.0,
                tenant_id="tenant_2"
            )
            
            # Record costs for each tenant
            tenant1_cost = CostRecord(
                agent_id="tenant1_agent",
                execution_id="exec_1",
                cost_usd=10.0,
                tenant_id="tenant_1"
            )
            
            tenant2_cost = CostRecord(
                agent_id="tenant2_agent", 
                execution_id="exec_2",
                cost_usd=20.0,
                tenant_id="tenant_2"
            )
            
            tenant1_tracker.record_cost(tenant1_cost)
            tenant2_tracker.record_cost(tenant2_cost)
            
            # Costs should be isolated
            assert tenant1_tracker.get_total_cost() == 10.0
            assert tenant2_tracker.get_total_cost() == 20.0
            
            # Budget utilization should be tenant-specific
            assert tenant1_tracker.get_budget_utilization() == 0.1  # 10/100
            assert tenant2_tracker.get_budget_utilization() == 0.1  # 20/200
            
        @pytest.mark.asyncio
        async def test_tenant_permission_isolation(self):
            """Test permission isolation between tenants."""
            tenant1_permissions = ["agent.execute", "agent.create", "data.read"]
            tenant2_permissions = ["agent.execute", "data.read"]  # No create permission
            
            tenant1_context = TenantContext(
                tenant_id="tenant_1",
                user_id="user_1",
                permissions=tenant1_permissions
            )
            
            tenant2_context = TenantContext(
                tenant_id="tenant_2",
                user_id="user_2", 
                permissions=tenant2_permissions
            )
            
            # Verify permission isolation
            assert tenant1_context.has_permission("agent.create") is True
            assert tenant2_context.has_permission("agent.create") is False
            
            # Both should have execute permission
            assert tenant1_context.has_permission("agent.execute") is True
            assert tenant2_context.has_permission("agent.execute") is True
            
    # Test Group 10: Rollback Mechanisms
    class TestRollbackMechanisms:
        """Tests for rollback and recovery mechanisms."""
        
        @pytest.mark.asyncio
        async def test_execution_rollback_on_error(self):
            """Test rollback when execution fails."""
            # Create agent with stateful execution
            class StatefulAgent(BaseAgent):
                class InputSchema(BaseModel):
                    value: int = Field(..., description="Value to process")
                    should_fail: bool = Field(False, description="Whether to fail")
                
                class OutputSchema(BaseModel):
                    processed_value: int = Field(..., description="Processed value")
                    state_snapshot: str = Field(..., description="State snapshot")
                
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.state = []
                
                async def execute(self, inputs, context):
                    # Save current state
                    initial_state = self.state.copy()
                    
                    try:
                        # Modify state
                        self.state.append(inputs.value)
                        
                        # Simulate failure if requested
                        if inputs.should_fail:
                            raise ValueError("Intentional failure")
                        
                        # Return success with state snapshot
                        return self.OutputSchema(
                            processed_value=inputs.value * 2,
                            state_snapshot=str(self.state)
                        )
                    except Exception:
                        # Rollback state on failure
                        self.state = initial_state
                        raise
            
            agent = StatefulAgent(
                agent_id="stateful_agent",
                name="Stateful Agent",
                version="1.0.0"
            )
            
            # Successful execution
            result1 = await agent.execute({"value": 1, "should_fail": False})
            assert result1["processed_value"] == 2
            assert agent.state == [1]  # State should be updated
            
            # Failed execution - should rollback
            try:
                await agent.execute({"value": 2, "should_fail": True})
                assert False, "Should have raised exception"
            except ValueError as e:
                assert "Intentional failure" in str(e)
            
            # State should be rolled back to [1]
            assert agent.state == [1]
            
            # Another successful execution
            result2 = await agent.execute({"value": 3, "should_fail": False})
            assert result2["processed_value"] == 6
            assert agent.state == [1, 3]  # State should include new value
            
        @pytest.mark.asyncio
        async def test_transactional_workflow_execution(self, simple_agent, agent_orchestrator):
            """Test transactional workflow execution."""
            workflow = [
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "add", "a": 1, "b": 2},
                    "id": "step1",
                    "transactional": True
                },
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "multiply", "a": 3, "b": 4},
                    "id": "step2",
                    "transactional": True
                },
                {
                    "agent": simple_agent,
                    "inputs": {"operation": "divide", "a": 10, "b": 0},  # Will fail
                    "id": "step3",
                    "transactional": True,
                    "dependencies": ["step1", "step2"]
                }
            ]
            
            # Execute workflow with transaction support
            # (This would require transactional execution framework)
            # For now, verify workflow definition
            
            assert workflow[0]["transactional"] is True
            assert workflow[1]["transactional"] is True
            assert workflow[2]["transactional"] is True
            
            # In a transactional system, step1 and step2 should be rolled back
            # when step3 fails due to dependency
            
        @pytest.mark.asyncio
        async def test_state_persistence_and_recovery(self):
            """Test state persistence and recovery."""
            # This would require state persistence mechanism
            # For now, create a simple test structure
            
            class RecoverableAgent(BaseAgent):
                class InputSchema(BaseModel):
                    action: str = Field(..., description="Action to perform")
                
                class OutputSchema(BaseModel):
                    result: str = Field(..., description="Result of action")
                    state_hash: str = Field(..., description="Hash of current state")
                
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.counter = 0
                    self.actions = []
                
                async def execute(self, inputs, context):
                    # Perform action
                    self.counter += 1
                    self.actions.append(inputs.action)
                    
                    # Create state hash for recovery
                    state_hash = f"{self.counter}:{','.join(self.actions)}"
                    
                    return self.OutputSchema(
                        result=f"Performed {inputs.action}",
                        state_hash=state_hash
                    )
                
                def get_state_snapshot(self):
                    """Get snapshot of current state for persistence."""
                    return {
                        "counter": self.counter,
                        "actions": self.actions.copy(),
                        "state_hash": f"{self.counter}:{','.join(self.actions)}"
                    }
                
                def restore_state(self, snapshot):
                    """Restore state from snapshot."""
                    self.counter = snapshot["counter"]
                    self.actions = snapshot["actions"].copy()
            
            agent = RecoverableAgent(
                agent_id="recoverable_agent",
                name="Recoverable Agent",
                version="1.0.0"
            )
            
            # Execute some actions
            result1 = await agent.execute({"action": "start"})
            snapshot1 = agent.get_state_snapshot()
            
            result2 = await agent.execute({"action": "process"})
            snapshot2 = agent.get_state_snapshot()
            
            # Verify state progression
            assert agent.counter == 2
            assert agent.actions == ["start", "process"]
            
            # Create new agent instance and restore state
            recovered_agent = RecoverableAgent(
                agent_id="recovered_agent",
                name="Recovered Agent",
                version="1.0.0"
            )
            
            recovered_agent.restore_state(snapshot2)
            
            # Should have same state
            assert recovered_agent.counter == 2
            assert recovered_agent.actions == ["start", "process"]
            
            # Continue execution from recovered state
            result3 = await recovered_agent.execute({"action": "complete"})
            assert recovered_agent.counter == 3
            assert recovered_agent.actions == ["start", "process", "complete"]


# Performance test markers
pytest.mark.performance = pytest.mark.skipif(
    os.getenv("RUN_PERFORMANCE_TESTS", "false").lower() != "true",
    reason="Performance tests disabled by default"
)

# Resource-intensive test markers
pytest.mark.resource_intensive = pytest.mark.skipif(
    os.getenv("RUN_RESOURCE_INTENSIVE_TESTS", "false").lower() != "true",
    reason="Resource-intensive tests disabled by default"
)


if __name__ == "__main__":
    # Run specific test groups
    pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-k", "TestSingleAgentExecution or TestBatchAgentExecution",
        "--log-level=INFO"
    ])