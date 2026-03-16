from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from uuid import UUID, uuid4

class Event(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    topic: str
    source: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, list] = {}

    def subscribe(self, topic: str, callback):
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(callback)

    async def publish(self, event: Event):
        if event.topic in self._subscribers:
            for callback in self._subscribers[event.topic]:
                await callback(event)

        # Topic wildcard support could be added here
        if "*" in self._subscribers:
            for callback in self._subscribers["*"]:
                await callback(event)
