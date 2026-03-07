"""In-process event bus for SSE broadcasting."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass
class SSEEvent:
    """An event ready for SSE broadcasting."""

    event_id: int
    data: dict[str, Any]


class EventBus:
    """In-process pub/sub for broadcasting domain events to SSE listeners."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[SSEEvent]] = []

    async def publish(self, event: SSEEvent) -> None:
        """Publish an event to all active subscribers."""
        for queue in self._subscribers:
            await queue.put(event)

    async def subscribe(self) -> AsyncIterator[SSEEvent]:
        """Yield SSE events as they are published."""
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.remove(queue)
