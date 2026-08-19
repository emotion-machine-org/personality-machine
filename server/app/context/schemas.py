from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# =============================================================================
# Trigger Types for Behaviors
# =============================================================================


class TriggerType(str, Enum):
    """Types of deterministic triggers for behaviors."""

    KEYWORD = "keyword"  # Match specific keywords
    EVERY_N_TURNS = "every_n"  # Run every N turns
    TURN_COUNT = "turn_count"  # Run at specific turn numbers
    ALWAYS = "always"  # Run every turn


class Trigger(BaseModel):
    """Definition of when a behavior should be triggered."""

    type: TriggerType
    keywords: List[str] | None = None  # For KEYWORD
    n: int | None = None  # For EVERY_N_TURNS
    turns: List[int] | None = None  # For TURN_COUNT


class TriggerSource(str, Enum):
    """How a behavior was triggered."""

    KEYWORD = "keyword"
    TURN_COUNT = "turn_count"
    ALWAYS = "always"
    CLASSIFIER = "classifier"


class TriggeredAction(BaseModel):
    """Represents an action that was triggered, with source tracking.

    Used for debugging and observability - tracks why each action was triggered.

    DEPRECATED: Use TriggeredBehavior instead.
    """

    action_key: str
    trigger_source: TriggerSource
    trigger_details: str | None = None  # e.g., "matched keyword: sad"
    priority: bool = False  # True = sync (orchestrator waits), False = async


class TriggeredBehavior(BaseModel):
    """Represents a behavior that was triggered, with source tracking.

    Used for debugging and observability - tracks why each behavior was triggered.
    """

    behavior_key: str
    trigger_source: TriggerSource
    trigger_details: str | None = None  # e.g., "matched keyword: sad"
    priority: bool = False  # True = sync (orchestrator waits), False = async


# =============================================================================
# Context Engine Schemas
# =============================================================================


class GateResult(BaseModel):
    """Result of layer gate evaluation for debugging and observability.

    Each layer decides whether to run via a gate check. This captures the
    decision and reasoning so developers can understand why a layer did
    or didn't contribute to the prompt.
    """

    run: bool
    reason: str
    inputs: Dict[str, Any] = Field(default_factory=dict)


class ContextEvent(BaseModel):
    """Lightweight event record emitted during context orchestration.

    The `name` encodes the layer plus operation (e.g., "memory:retrieving" or
    "behaviors:priority:my_behavior"), while `phase` indicates start/end or info markers.
    """

    name: str
    phase: Literal["start", "end", "info", "error"]
    meta: Dict[str, Any] = Field(default_factory=dict)
    ts_ms: float


class TurnContext(BaseModel):
    """Context for the current turn, computed once and shared across layers.

    This provides shared data that multiple layers might need, computed once
    at the start of orchestration to avoid redundant work.
    """

    message: str
    companion_id: UUID
    conversation_id: UUID | None = None
    external_user_id: str | None = None

    # v2 API: relationship_id for per-relationship behavior configs
    relationship_id: UUID | None = None

    # v2 API: session context (Phase 8)
    session_id: UUID | None = None
    session_isolated: bool = False  # If True, skip profile/memory writes

    # Enrichment (computed at turn start, optional)
    keywords: List[str] | None = None
    turn_count: int = 0

    class Config:
        arbitrary_types_allowed = True


class TurnEffect(BaseModel):
    """Side effect to apply after the LLM response is generated.

    Layers can emit effects that are collected and executed post-turn by
    the PostTurnExecutor. This keeps produce() pure while enabling state
    updates, scheduling, and other side effects.
    """

    effect_type: Literal[
        "state_patch",
        "schedule",
        "memory_write",
        "memory_v2_write",
        "job",
        "webhook",
        "proactive_message",
    ]
    payload: Dict[str, Any]


class PendingAsyncActionSchema(BaseModel):
    """Schema for pending async actions to be enqueued after LLM response.

    DEPRECATED: Use PendingAsyncBehaviorSchema instead.
    """

    action_key: str
    action: Dict[str, Any]
    trigger_source: str
    trigger_details: str | None = None


class PendingAsyncBehaviorSchema(BaseModel):
    """Schema for pending async behaviors to be enqueued after LLM response."""

    behavior_key: str
    behavior: Dict[str, Any]
    trigger_source: str
    trigger_details: str | None = None


class ContextPlan(BaseModel):
    """Structured prompt plan used by all companion modalities."""

    messages: List[Dict[str, str]]
    token_usage: Dict[str, Any] = Field(default_factory=dict)
    trace: Dict[str, Any] = Field(default_factory=dict)
    events: List[ContextEvent] = Field(default_factory=list)
    effects: List[TurnEffect] = Field(default_factory=list)
    pending_async_actions: List[PendingAsyncActionSchema] = Field(
        default_factory=list
    )  # DEPRECATED
    pending_async_behaviors: List[PendingAsyncBehaviorSchema] = Field(default_factory=list)


class TestOverrides(BaseModel):
    """Test overrides to skip layer execution and inject test data.

    Used by the context engine testing UI to provide manual inputs
    instead of running the actual layer runtimes.

    When a field is provided (not None), the corresponding layer is
    skipped and the provided data is injected directly.
    """

    # Core system prompt (skips composing from config + core memories)
    core_system_prompt: str | None = None

    # Core memories to compose into system prompt (used if core_system_prompt is None)
    core_memories: List[str] | None = None

    # Regular/retrieved memories (skips memory runtime)
    regular_memories: List[str] | None = None

    # Knowledge results (skips knowledge runtime)
    knowledge_results: List[str] | None = None

    # Message history (skips history fetch)
    history: List[Dict[str, str]] | None = None

    # Skip behaviors layer entirely
    skip_behaviors: bool = False


__all__ = [
    "ContextEvent",
    "ContextPlan",
    # Context engine
    "GateResult",
    "PendingAsyncActionSchema",  # DEPRECATED
    "PendingAsyncBehaviorSchema",
    "TestOverrides",
    "Trigger",
    "TriggerSource",
    # Trigger types
    "TriggerType",
    "TriggeredAction",  # DEPRECATED
    "TriggeredBehavior",
    "TurnContext",
    "TurnEffect",
]
