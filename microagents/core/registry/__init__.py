from typing import Dict, Optional, List, Type
from pydantic import BaseModel

class AgentMetadata(BaseModel):
    id: str
    name: str
    version: str
    description: Optional[str] = None
    capabilities: List[str] = []

class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, Type] = {}
        self._metadata: Dict[str, AgentMetadata] = {}

    def register(self, metadata: AgentMetadata, agent_class: Type):
        self._agents[metadata.id] = agent_class
        self._metadata[metadata.id] = metadata

    def get_agent_class(self, agent_id: str) -> Optional[Type]:
        return self._agents.get(agent_id)

    def list_agents(self) -> List[AgentMetadata]:
        return list(self._metadata.values())
