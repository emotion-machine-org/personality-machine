"""Context orchestrator package.

This module encapsulates plan building, layer runtimes, and related schemas so
all modalities (text, stream, voice) share a single prompt assembly surface.
"""

from .behavior_context import BehaviorContext
from .behavior_runtime import BehaviorRuntime
from .chat_helpers import (
    ModeResolution,
    TurnContextResult,
    build_turn_context,
    resolve_context_mode,
    resolve_v2_context_mode,
)
from .context_hydrator import ContextHydrator, HydratedContext
from .core_prompt_layer import bust_core_prompt_cache
from .orchestrator import build_context_plan
from .post_turn_executor import execute_post_turn_effects, persist_turn_context
from .resolved_config import CompanionRuntimeConfig
from .schemas import (
    ContextEvent,
    ContextPlan,
    GateResult,
    PendingAsyncBehaviorSchema,
    TestOverrides,
    TriggeredBehavior,
    TurnContext,
    TurnEffect,
)

__all__ = [
    # Behaviors
    "BehaviorContext",
    "BehaviorRuntime",
    # Configuration
    "CompanionRuntimeConfig",
    "ContextEvent",
    # Hydration
    "ContextHydrator",
    # Schemas
    "ContextPlan",
    "GateResult",
    "HydratedContext",
    "ModeResolution",
    "PendingAsyncBehaviorSchema",
    "TestOverrides",
    "TriggeredBehavior",
    "TurnContext",
    "TurnContextResult",
    "TurnEffect",
    # Orchestration
    "build_context_plan",
    "build_turn_context",
    "bust_core_prompt_cache",
    "execute_post_turn_effects",
    "persist_turn_context",
    # Chat helpers
    "resolve_context_mode",
    "resolve_v2_context_mode",
]
