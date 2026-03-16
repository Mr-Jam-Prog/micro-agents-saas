import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from microagents.core.events import Event, EventBus
from microagents.core.registry import AgentRegistry

logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    def __init__(self, id: str, name: str):
        self.id = id
        self.name = name
        self.bus: Optional[EventBus] = None

    @abstractmethod
    async def execute(self, context: Dict[str, Any]) -> Any:
        pass

    async def emit(self, topic: str, payload: Dict[str, Any]):
        if self.bus:
            event = Event(topic=topic, source=self.id, payload=payload)
            await self.bus.publish(event)

class AgentRuntime:
    def __init__(self, event_bus: EventBus, registry: AgentRegistry):
        self.bus = event_bus
        self.registry = registry
        self.running = False
        self._active_agents: Dict[str, BaseAgent] = {}

    def add_agent(self, agent_id: str, name: str = None, **kwargs):
        agent_class = self.registry.get_agent_class(agent_id)
        if not agent_class:
            raise ValueError(f"Agent {agent_id} not found in registry")

        agent_name = name or agent_id
        agent = agent_class(id=agent_id, name=agent_name, **kwargs)
        agent.bus = self.bus
        self._active_agents[agent_id] = agent
        return agent

    async def run_cycle(self, context: Dict[str, Any]):
        tasks = []
        for agent in self._active_agents.values():
            tasks.append(agent.execute(context))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def start(self):
        self.running = True
        logger.info("Agent Runtime started")
        while self.running:
            await self.run_cycle({})
            await asyncio.sleep(1)

    def stop(self):
        self.running = False
        logger.info("Agent Runtime stopped")
