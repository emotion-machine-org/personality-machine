from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Callable, Dict, List, Protocol

import asyncpg

from .schemas import ContextEvent, TurnEffect

# Type alias for event callback
EventCallback = Callable[[ContextEvent], None]

# Type alias for connection factory - returns async context manager that yields a connection
ConnectionFactory = Callable[[], AsyncContextManager[asyncpg.Connection]]


@dataclass
class PendingAsyncAction:
    """An async action that should be enqueued after LLM response.

    DEPRECATED: Use PendingAsyncBehavior instead.
    """

    action_key: str
    action: Dict[str, Any]  # Full action definition
    trigger_source: str
    trigger_details: str | None


@dataclass
class PendingAsyncBehavior:
    """An async behavior that should be enqueued after LLM response."""

    behavior_key: str
    behavior: Dict[str, Any]  # Full behavior definition
    trigger_source: str
    trigger_details: str | None


@dataclass
class LayerOutput:
    """Output from a layer's run() method.

    Contains messages to add to the prompt, events for observability,
    effects to apply post-turn, and pending async behaviors to enqueue.
    """

    messages: List[Dict[str, str]]
    events: List[ContextEvent]
    effects: List[TurnEffect] = field(default_factory=list)
    pending_async_actions: List[PendingAsyncAction] = field(default_factory=list)  # DEPRECATED
    pending_async_behaviors: List[PendingAsyncBehavior] = field(default_factory=list)


class LayerRuntime(Protocol):
    """Protocol for context layers.

    Each layer implements this protocol with a unique key and a run() method
    that returns messages to inject into the prompt, events for debugging,
    and optional post-turn effects.

    Layers can optionally accept an event_callback for real-time event streaming.
    """

    key: str

    async def run(self, event_callback: EventCallback | None = None) -> LayerOutput: ...


__all__ = [
    "ConnectionFactory",
    "EventCallback",
    "LayerOutput",
    "LayerRuntime",
    "PendingAsyncAction",  # DEPRECATED
    "PendingAsyncBehavior",
]
