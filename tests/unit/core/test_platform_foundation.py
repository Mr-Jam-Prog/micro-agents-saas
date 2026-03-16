import pytest
import asyncio
from microagents.core.events import Event, EventBus
from microagents.core.registry import AgentRegistry, AgentMetadata
from microagents.core.runtime import BaseAgent, AgentRuntime
from microagents.core.decision import DecisionEngine

class MockAgent(BaseAgent):
    async def execute(self, context):
        await self.emit("test.topic", {"result": "success"})
        return "done"

@pytest.mark.asyncio
async def test_event_bus():
    bus = EventBus()
    received = []

    async def callback(event):
        received.append(event)

    bus.subscribe("test.topic", callback)
    event = Event(topic="test.topic", source="test", payload={"data": 1})
    await bus.publish(event)

    assert len(received) == 1
    assert received[0].payload["data"] == 1

def test_agent_registry():
    registry = AgentRegistry()
    metadata = AgentMetadata(id="mock", name="Mock Agent", version="1.0")
    registry.register(metadata, MockAgent)

    assert registry.get_agent_class("mock") == MockAgent
    assert len(registry.list_agents()) == 1

@pytest.mark.asyncio
async def test_agent_runtime():
    bus = EventBus()
    registry = AgentRegistry()
    metadata = AgentMetadata(id="mock", name="Mock Agent", version="1.0")
    registry.register(metadata, MockAgent)

    runtime = AgentRuntime(bus, registry)
    agent = runtime.add_agent("mock")

    results = await runtime.run_cycle({})
    assert results == ["done"]

def test_decision_engine():
    engine = DecisionEngine(min_roi_threshold=20.0)

    # ROI = (150-100)/100 * 100 = 50%
    assert engine.should_execute(100, 150) is True

    # ROI = (110-100)/100 * 100 = 10%
    assert engine.should_execute(100, 110) is False

    eval_result = engine.evaluate_action("test", {"investment": 100, "expected_return": 150})
    assert eval_result["allowed"] is True
