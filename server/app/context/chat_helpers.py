"""Chat helpers for hybrid context mode selection.

This module provides shared utilities for mode resolution and context injection
used by both text (client_api, conversations) and voice (sessions) endpoints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List
from uuid import UUID

import asyncpg

from .orchestrator import build_context_plan
from .schemas import ContextEvent, ContextPlan

logger = logging.getLogger(__name__)


@dataclass
class ModeResolution:
    """Result of resolving which context mode to use."""

    use_layered: bool
    source: str  # "request_override", "companion_config", "default"


def resolve_context_mode(
    *,
    request_override: str | None,
    companion_config: Any,
) -> ModeResolution:
    """Resolve which context mode to use (hybrid selection per MERGE_STRATEGY.md).

    Priority:
    1. Request override (header/field value)
    2. companion.config.context_mode
    3. Default to "legacy"

    Args:
        request_override: Optional mode from request header/field ("layered", "legacy", "v2")
        companion_config: Companion config object (may have context_mode attribute)

    Returns:
        ModeResolution with use_layered flag and source for debugging
    """
    if request_override:
        use_layered = request_override.lower() in ("v2", "layered")
        return ModeResolution(use_layered=use_layered, source="request_override")

    if companion_config:
        mode = getattr(companion_config, "context_mode", None)
        if mode:
            use_layered = mode == "layered"
            return ModeResolution(use_layered=use_layered, source="companion_config")

    return ModeResolution(use_layered=False, source="default")


def resolve_v2_context_mode(
    relationship_context_mode: str | None,
    companion_config: Any,
) -> str:
    """Resolve context_mode for V2 APIs (relationships).

    Priority:
    1. relationship.context_mode (if set)
    2. companion.config.context_mode (if set)
    3. "legacy" (default fallback - matches V1 behavior)

    Args:
        relationship_context_mode: Optional mode from relationship (may be None)
        companion_config: Companion config object (may have context_mode attribute)

    Returns:
        "layered" or "legacy"
    """
    # 1. Check relationship-level override
    if relationship_context_mode:
        return relationship_context_mode

    # 2. Check companion config
    if companion_config:
        mode = getattr(companion_config, "context_mode", None)
        if mode:
            return mode

    # 3. Default to legacy (conservative)
    return "legacy"


@dataclass
class TurnContextResult:
    """Result of per-turn context injection."""

    messages: List[Dict[str, str]]  # System messages to inject
    events: List[ContextEvent]  # Context events (for text mode streaming)
    effects: List[Any]  # Post-turn effects to execute
    plan: ContextPlan | None  # Full plan if layered mode was used


async def build_turn_context(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    companion_config: Any,
    conversation_id: UUID | None,
    user_message: str,
    external_user_id: str | None = None,
    use_layered: bool,
    include_knowledge: bool = True,
    include_behaviors: bool = True,
    emit_events: bool = True,
    event_callback: Callable[[ContextEvent], None] | None = None,
) -> TurnContextResult:
    """Build context for a turn (layered or legacy mode).

    This is the shared helper for per-turn context injection used by both
    text and voice endpoints.

    Args:
        conn: Database connection
        companion_id: Companion UUID
        companion_config: Companion config object
        conversation_id: Optional conversation UUID
        user_message: The user's message text
        external_user_id: Optional external user identifier
        use_layered: Whether to use layered mode (True) or legacy (False)
        include_knowledge: Include knowledge retrieval in layered mode
        include_behaviors: Include actions in layered mode
        emit_events: Whether to emit context events (True for text, False for voice)
        event_callback: Optional callback for streaming events

    Returns:
        TurnContextResult with messages, events, and effects
    """
    if use_layered:
        return await _build_layered_turn_context(
            conn,
            companion_id=companion_id,
            companion_config=companion_config,
            conversation_id=conversation_id,
            user_message=user_message,
            external_user_id=external_user_id,
            include_knowledge=include_knowledge,
            include_behaviors=include_behaviors,
            event_callback=event_callback if emit_events else None,
        )
    else:
        return await _build_legacy_turn_context(
            conn,
            companion_id=companion_id,
            user_message=user_message,
            external_user_id=external_user_id,
            conversation_id=conversation_id,
        )


async def _build_layered_turn_context(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    companion_config: Any,
    conversation_id: UUID | None,
    user_message: str,
    external_user_id: str | None,
    include_knowledge: bool,
    include_behaviors: bool,
    event_callback: Callable[[ContextEvent], None] | None,
) -> TurnContextResult:
    """Build context using the layered context engine."""
    plan = await build_context_plan(
        conn=conn,
        companion_id=companion_id,
        companion_config=companion_config,
        conversation_id=conversation_id,
        user_message=user_message,
        external_user_id=external_user_id,
        include_memory=True,
        include_knowledge=include_knowledge,
        include_behaviors=include_behaviors,
        append_core_prompt=False,  # Core prompt already in session/conversation
        append_history=False,  # History managed separately
        hydrate_state=True,
        context_mode_override="layered",
        event_callback=event_callback,
    )

    # Extract system messages from plan
    system_messages = [m for m in plan.messages if m.get("role") == "system" and m.get("content")]

    return TurnContextResult(
        messages=system_messages,
        events=plan.events or [],
        effects=plan.effects or [],
        plan=plan,
    )


async def _build_legacy_turn_context(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    user_message: str,
    external_user_id: str | None,
    conversation_id: UUID | None,
) -> TurnContextResult:
    """Build context using legacy memory retrieval.

    Note: conversation_id is intentionally passed as None to allow cross-session
    recall for the same user (matching original voice session behavior).
    """
    from ..services.context_assembly import build_transient_memory_block

    memory_block = await build_transient_memory_block(
        conn,
        companion_id=companion_id,
        user_text=user_message,
        external_user_id=external_user_id,
        conversation_id=None,  # Allow cross-session recall for the same user
        memory_enabled=True,
        timings={},
        recent_messages=None,
    )

    messages = []
    if memory_block:
        messages.append({"role": "system", "content": memory_block})

    return TurnContextResult(
        messages=messages,
        events=[],
        effects=[],
        plan=None,
    )
