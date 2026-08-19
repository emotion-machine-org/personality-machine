"""Shared message processing logic for HTTP and WebSocket endpoints.

This module extracts common functionality from messages.py and websockets.py
to eliminate duplication and ensure consistent behavior across transports.

Architecture:
- TurnProcessor: Orchestrates the complete message turn cycle
- EventEmitter: Protocol for transport-specific event delivery (SSE/WebSocket)
- Helper functions: Shared database and context operations
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Protocol
from uuid import UUID, uuid4

import asyncpg

from ..constants import DEFAULT_TEXT_LLM_MAX_TOKENS
from ..context import (
    ContextEvent,
    ContextPlan,
    TurnContext,
    build_context_plan,
    execute_post_turn_effects,
    resolve_v2_context_mode,
)
from ..context.resolved_config import CompanionRuntimeConfig
from ..repositories.job_repository import JobRepository
from ..repositories.relationship_repository import RelationshipRepository
from ..services.context_assembly import build_effective_system_prompt, build_transient_memory_block
from ..services.intro_context import drop_intro_preamble_from_history
from ..services.llm import generate_llm_response_direct, resolve_max_tokens
from ..services.llm_resolver import resolve_llm_client
from ..services.voice_presets import resolve_llm_config
from ..utils.profile import (
    build_profile_prompt_block,
    prune_profile_contradicting_history,
    resolve_profile_in_prompt_enabled,
)

if TYPE_CHECKING:
    from ..models.v2.relationship import Relationship

logger = logging.getLogger(__name__)

DIALOGMACHINE_FAST_DEFAULT_PROVIDER = "fast-brain"
DIALOGMACHINE_FAST_HISTORY_LIMIT = 24


# -----------------------------------------------------------------------------
# Data Classes
# -----------------------------------------------------------------------------


@dataclass
class TurnConfig:
    """Configuration for a single message turn."""

    model: str | None = None
    temperature: float | None = None


@dataclass
class TurnInput:
    """Input data for processing a message turn."""

    content: str
    session_id: UUID | None = None
    session_isolated: bool = False
    config: TurnConfig | None = None
    client_message_id: str | None = None  # For WebSocket ack


@dataclass
class TurnResult:
    """Result of processing a message turn."""

    user_message_id: UUID
    user_seq: int
    assistant_message_id: UUID
    assistant_seq: int
    assistant_content: str
    build_ms: int
    trace: dict[str, Any] | None = None


@dataclass
class TurnState:
    """Mutable state during turn processing."""

    turn_id: str = field(default_factory=lambda: str(uuid4()))
    user_seq: int = 0
    assistant_seq: int = 0
    user_message_id: UUID | None = None
    context_plan: ContextPlan | None = None
    llm_messages: list[dict[str, str]] = field(default_factory=list)
    model: str = ""
    temperature: float = 0.7


# -----------------------------------------------------------------------------
# Event Emitter Protocol
# -----------------------------------------------------------------------------


class EventEmitter(Protocol):
    """Protocol for emitting events during turn processing.

    Implemented by transport-specific classes (SSE, WebSocket).
    """

    async def emit_ack(
        self,
        turn_id: str,
        message_id: UUID,
        seq: int,
        client_message_id: str | None = None,
    ) -> None:
        """Emit acknowledgment that user message was received."""
        ...

    async def emit_status(
        self,
        turn_id: str,
        stage: str,
        phase: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Emit status update (building_context, thinking, etc.)."""
        ...

    async def emit_delta(self, turn_id: str, content: str) -> None:
        """Emit streaming content delta."""
        ...

    async def emit_message(
        self,
        turn_id: str,
        message_id: UUID,
        relationship_id: UUID,
        content: str,
        seq: int,
        build_ms: int,
    ) -> None:
        """Emit final assistant message."""
        ...

    async def emit_error(self, turn_id: str, code: str, message: str) -> None:
        """Emit error event."""
        ...

    async def emit_trace(self, turn_id: str, event: ContextEvent) -> None:
        """Emit debug trace event (optional, for debug mode)."""
        ...


# -----------------------------------------------------------------------------
# Database Operations
# -----------------------------------------------------------------------------


async def get_next_seq(conn: asyncpg.Connection, relationship_id: UUID) -> int:
    """Get the next sequence number for a relationship."""
    row = await conn.fetchrow(
        "SELECT next_relationship_message_seq($1) as seq",
        relationship_id,
    )
    return row["seq"] if row else 1


async def save_message(
    conn: asyncpg.Connection,
    *,
    relationship_id: UUID,
    role: str,
    content: str,
    seq: int | None = None,
    session_id: UUID | None = None,
    is_proactive: bool = False,
    input_modality: str = "text",
    build_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save a message to the database."""
    message_id = uuid4()

    row = await conn.fetchrow(
        """
        INSERT INTO messages (
            id, relationship_id, role, content, seq, session_id,
            is_proactive, input_modality, build_ms, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id, relationship_id, role, content, seq, session_id,
                  is_proactive, input_modality, build_ms, metadata, created_at
        """,
        message_id,
        relationship_id,
        role,
        content,
        seq,
        session_id,
        is_proactive,
        input_modality,
        build_ms,
        metadata,
    )
    return dict(row)


# -----------------------------------------------------------------------------
# Context Building
# -----------------------------------------------------------------------------


def should_include_behaviors_layer(companion: Any) -> bool:
    """Resolve behavior layer inclusion for v2 turns.

    Strict layer default:
    - if no explicit actions/behaviors layer is attached, do not include behaviors
    - if a layer attachment exists, respect its enabled flag
    """
    resolved = CompanionRuntimeConfig.from_companion_config(getattr(companion, "config", None))
    return resolved.should_include_behaviors(default_if_unconfigured=False)


def get_include_profile_flag(companion: Any, relationship: Relationship) -> bool:
    """Check if profile should be included in prompt."""
    return resolve_profile_in_prompt_enabled(
        relationship.config if isinstance(relationship.config, dict) else {},
        getattr(companion, "config", None),
    )


def build_runtime_profile_block(companion: Any, relationship: Relationship) -> str | None:
    """Build the shared profile block for v2 prompt injection."""
    companion_config = getattr(companion, "config", None)
    profile_schema = getattr(companion_config, "profile_schema", None) if companion_config else None
    return build_profile_prompt_block(
        relationship.profile,
        profile_schema=profile_schema if isinstance(profile_schema, dict) else None,
        profile_version=relationship.version,
        profile_updated_at=relationship.updated_at,
    )


def build_turn_profile_metadata(companion: Any, relationship: Relationship) -> dict[str, Any]:
    """Metadata persisted with assistant turns for version-aware context invalidation."""
    include_profile = get_include_profile_flag(companion, relationship)
    health_data = (
        relationship.profile.get("health_data")
        if isinstance(relationship.profile.get("health_data"), dict)
        else {}
    )
    cycle_data = (
        health_data.get("cycle_data") if isinstance(health_data.get("cycle_data"), dict) else {}
    )
    contains_cycle_state = bool(relationship.profile.get("cycle") or cycle_data)
    return {
        "profile_version": relationship.version,
        "profile_updated_at": relationship.updated_at.isoformat(),
        "profile_in_prompt": include_profile,
        "contains_cycle_state": include_profile and contains_cycle_state,
    }


def get_profile_schema(companion: Any) -> dict[str, Any] | None:
    companion_config = getattr(companion, "config", None)
    profile_schema = getattr(companion_config, "profile_schema", None) if companion_config else None
    return profile_schema if isinstance(profile_schema, dict) else None


def compute_turn_count(relationship: Relationship) -> int:
    """Compute turn count from message_count.

    Turn 1: message_count=0, Turn 2: message_count=2, etc.
    """
    return (relationship.message_count // 2) + 1


def get_dialogmachine_config(relationship: Relationship) -> dict[str, Any] | None:
    """Return dialogmachine relationship config block when present."""
    relationship_config = relationship.config if isinstance(relationship.config, dict) else {}
    dialog_cfg = relationship_config.get("dialogmachine")
    if isinstance(dialog_cfg, dict):
        return dialog_cfg
    return None


def is_dialogmachine_fast_mode(relationship: Relationship) -> bool:
    """DialogMachine relationships use fast text mode (no layered v2 context engine)."""
    return get_dialogmachine_config(relationship) is not None


def extract_dialogmachine_text_model(relationship: Relationship) -> str:
    """Resolve DialogMachine selected LLM provider for fast text mode."""
    dialog_cfg = get_dialogmachine_config(relationship)
    if not dialog_cfg:
        return DIALOGMACHINE_FAST_DEFAULT_PROVIDER

    llm_cfg = dialog_cfg.get("llm")
    if isinstance(llm_cfg, dict):
        candidate = llm_cfg.get("provider")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    legacy_candidate = dialog_cfg.get("llm_provider")
    if isinstance(legacy_candidate, str) and legacy_candidate.strip():
        return legacy_candidate.strip()

    return DIALOGMACHINE_FAST_DEFAULT_PROVIDER


def build_dialogmachine_prompt(base_prompt: str, relationship: Relationship) -> str:
    """Apply DialogMachine prompt override and guardrails to the base system prompt."""
    dialog_cfg = get_dialogmachine_config(relationship)
    if not dialog_cfg:
        return base_prompt

    system_prompt = base_prompt
    prompt_override = dialog_cfg.get("prompt_override")
    if isinstance(prompt_override, str) and prompt_override.strip():
        system_prompt = prompt_override.strip()

    guardrails = dialog_cfg.get("guardrails")
    if isinstance(guardrails, str) and guardrails.strip():
        system_prompt = (
            f"{system_prompt.rstrip()}\n\n## Relationship Guardrails\n{guardrails.strip()}"
        )
    return system_prompt


def load_dialogmachine_hot_context(relationship_id: UUID) -> str:
    """Best-effort hot_context.md load for fast text mode."""
    try:
        from ..routers.voice.voice_workspace import HOT_CONTEXT_FILE, get_workspace

        workspace = get_workspace(relationship_id)
        raw = workspace.read(HOT_CONTEXT_FILE)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception as e:
        logger.debug(
            "DialogMachine hot_context unavailable for relationship %s: %s", relationship_id, e
        )
    return ""


def _prior_history_without_current_user(
    history_rows: list[dict[str, Any]],
    user_message: str,
) -> list[dict[str, Any]]:
    """Drop the just-saved current user message from history when present."""
    if not history_rows:
        return history_rows
    last_history = history_rows[-1]
    if (
        last_history
        and last_history.get("role") == "user"
        and str(last_history.get("content") or "") == user_message
    ):
        return history_rows[:-1]
    return history_rows


async def build_dialogmachine_fast_messages(
    conn: asyncpg.Connection,
    companion: Any,
    relationship: Relationship,
    user_message: str,
    *,
    session_id: UUID | None = None,
    session_isolated: bool = False,
) -> list[dict[str, str]]:
    """Build minimal, low-latency text context for DialogMachine fast mode."""
    try:
        effective_prompt, _ = await build_effective_system_prompt(conn, companion_id=companion.id)
    except Exception:
        effective_prompt = (
            companion.config.system_prompt.get_effective_prompt()
            if hasattr(companion.config, "system_prompt")
            else "You are a helpful companion."
        )

    system_prompt = build_dialogmachine_prompt(effective_prompt, relationship)
    hot_context = load_dialogmachine_hot_context(relationship.id)
    include_profile = get_include_profile_flag(companion, relationship)
    profile_schema = get_profile_schema(companion)
    profile_block = (
        build_runtime_profile_block(companion, relationship) if include_profile else None
    )

    history_rows = await RelationshipRepository.get_message_history(
        conn,
        relationship.id,
        session_id=session_id,
        session_isolated=session_isolated,
        limit=DIALOGMACHINE_FAST_HISTORY_LIMIT,
    )
    prior_history_rows = _prior_history_without_current_user(history_rows, user_message)
    prior_history_rows = drop_intro_preamble_from_history(
        prior_history_rows,
        companion.config,
    )
    if include_profile:
        prior_history_rows, pruned_count = prune_profile_contradicting_history(
            prior_history_rows,
            user_message,
            relationship.profile,
            profile_schema=profile_schema,
            profile_version=relationship.version,
        )
        if pruned_count:
            logger.info(
                "Pruned %d stale profile-contradicting history rows for relationship=%s",
                pruned_count,
                relationship.id,
            )

    llm_messages: list[dict[str, str]] = []
    if system_prompt:
        llm_messages.append({"role": "system", "content": system_prompt})
    if hot_context:
        llm_messages.append(
            {
                "role": "system",
                "content": (
                    "## Hot Context\n"
                    "Use this current relationship state for concise, context-aware replies.\n\n"
                    f"{hot_context}"
                ),
            }
        )

    for row in prior_history_rows:
        llm_messages.append({"role": row["role"], "content": row["content"]})

    # PROFILE must sit after prior history so stale assistant turns such as
    # "I do not know your cycle yet" cannot outrank current profile state.
    if profile_block:
        llm_messages.append({"role": "system", "content": profile_block})

    llm_messages.append({"role": "user", "content": user_message})

    return llm_messages


async def build_legacy_messages(
    conn: asyncpg.Connection,
    companion: Any,
    relationship: Relationship,
    user_message: str,
) -> list[dict[str, str]]:
    """Build LLM messages using legacy context mode.

    Includes:
    1. System prompt
    2. Memory block (if enabled)
    3. Conversation summary (if available)
    4. Conversation history from relationship
    5. Current user message
    """
    from ..repositories.summary_repository import SummaryRepository

    try:
        effective_prompt, _ = await build_effective_system_prompt(conn, companion_id=companion.id)
    except Exception:
        effective_prompt = (
            companion.config.system_prompt.get_effective_prompt()
            if hasattr(companion.config, "system_prompt")
            else "You are a helpful companion."
        )

    # Get configured message limit from companion config
    message_limit = (
        companion.config.context.message_limit
        if hasattr(companion.config, "context") and companion.config.context
        else 200
    )
    history_rows = await RelationshipRepository.get_message_history(
        conn,
        relationship.id,
        limit=message_limit,
    )
    prior_history_rows = _prior_history_without_current_user(history_rows, user_message)
    prior_history_rows = drop_intro_preamble_from_history(
        prior_history_rows,
        companion.config,
    )

    memory_enabled = bool(
        hasattr(companion.config, "memory")
        and companion.config.memory
        and companion.config.memory.enabled
    )

    recent_messages = [
        {"role": row["role"], "content": row["content"]} for row in history_rows[-6:]
    ]

    memory_block = ""
    if memory_enabled:
        memory_block = await build_transient_memory_block(
            conn,
            companion_id=companion.id,
            user_text=user_message,
            external_user_id=relationship.external_user_id,
            conversation_id=None,
            memory_enabled=True,
            timings={},
            recent_messages=recent_messages,
        )

    include_profile = get_include_profile_flag(companion, relationship)
    profile_schema = get_profile_schema(companion)
    profile_block = (
        build_runtime_profile_block(companion, relationship) if include_profile else None
    )
    if include_profile:
        prior_history_rows, pruned_count = prune_profile_contradicting_history(
            prior_history_rows,
            user_message,
            relationship.profile,
            profile_schema=profile_schema,
            profile_version=relationship.version,
        )
        if pruned_count:
            logger.info(
                "Pruned %d stale profile-contradicting history rows for relationship=%s",
                pruned_count,
                relationship.id,
            )

    # Load conversation summary if available
    summary_block = ""
    try:
        summary = await SummaryRepository.get_latest_summary(conn, relationship.id)
        if summary:
            summary_block = (
                "# CONVERSATION HISTORY SUMMARY\n"
                "This summarizes earlier conversations with this user:\n\n"
                f"{summary['content']}"
            )
    except Exception as e:
        logger.warning(f"Failed to load conversation summary: {e}")

    llm_messages: list[dict[str, str]] = []
    if effective_prompt:
        llm_messages.append({"role": "system", "content": effective_prompt})
    if memory_block:
        llm_messages.append({"role": "system", "content": memory_block})
    if summary_block:
        llm_messages.append({"role": "system", "content": summary_block})
    for row in prior_history_rows:
        llm_messages.append({"role": row["role"], "content": row["content"]})

    # PROFILE must sit after prior history so stale assistant turns such as
    # "I do not know your cycle yet" cannot outrank current profile state.
    if profile_block:
        llm_messages.append({"role": "system", "content": profile_block})

    llm_messages.append({"role": "user", "content": user_message})
    return llm_messages


async def build_context_messages(
    conn: asyncpg.Connection,
    companion: Any,
    relationship: Relationship,
    user_message: str,
    session_id: UUID | None = None,
    session_isolated: bool = False,
    event_callback: Callable[[ContextEvent], None] | None = None,
) -> tuple[list[dict[str, str]], ContextPlan | None]:
    """Build context messages using layered context mode.

    Returns (llm_messages, context_plan). Falls back to legacy on error.
    """
    include_profile = get_include_profile_flag(companion, relationship)
    turn_count = compute_turn_count(relationship)
    include_behaviors = should_include_behaviors_layer(companion)

    try:
        context_plan = await build_context_plan(
            conn=conn,
            companion_id=companion.id,
            user_message=user_message,
            external_user_id=relationship.external_user_id,
            relationship_id=relationship.id,
            session_id=session_id,
            session_isolated=session_isolated,
            turn_count_override=turn_count,
            conversation_id=None,
            companion_config=companion.config,
            include_behaviors=include_behaviors,
            include_profile_in_prompt=include_profile,
            profile_override=relationship.profile,
            profile_version=relationship.version,
            profile_updated_at=relationship.updated_at,
            event_callback=event_callback,
        )
        return context_plan.messages, context_plan
    except Exception as e:
        logger.warning(f"Context plan failed, using legacy: {e}")
        messages = await build_legacy_messages(conn, companion, relationship, user_message)
        return messages, None


# -----------------------------------------------------------------------------
# LLM Response Generation
# -----------------------------------------------------------------------------


def resolve_model_config(
    companion: Any,
    turn_config: TurnConfig | None,
) -> tuple[str, float]:
    """Resolve model and temperature from companion and turn config."""
    base_model, base_temperature = resolve_llm_config(companion.config)
    model = (turn_config.model if turn_config else None) or base_model
    temperature = (turn_config.temperature if turn_config else None) or base_temperature
    return model, temperature


def get_max_tokens(companion: Any, model: str) -> int:
    """Get max tokens for the model."""
    config_max = getattr(companion.config, "max_output_tokens", None)
    if config_max is None and hasattr(companion.config, "inference"):
        config_max = getattr(companion.config.inference, "max_output_tokens", None)
    return resolve_max_tokens(model, config_max or DEFAULT_TEXT_LLM_MAX_TOKENS)


async def generate_response_non_streaming(
    llm_messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Generate a non-streaming LLM response."""
    return await generate_llm_response_direct(
        model,
        llm_messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timings={},
    )


async def stream_llm_response(
    llm_messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
) -> AsyncIterator[str]:
    """Stream LLM response chunks.

    Yields content deltas as they arrive from the LLM.
    """
    client, resolved_model, _ = resolve_llm_client(model)

    completion_kwargs = {
        "model": resolved_model,
        "messages": llm_messages,
        "temperature": temperature,
        "stream": True,
    }
    if str(resolved_model).startswith("gpt-5.1"):
        completion_kwargs["max_completion_tokens"] = max_tokens
    else:
        completion_kwargs["max_tokens"] = max_tokens

    stream = await client.chat.completions.create(**completion_kwargs)

    async for chunk in stream:
        delta_text = None
        try:
            if chunk.choices and chunk.choices[0].delta:
                delta_text = chunk.choices[0].delta.content
        except Exception:
            pass

        if delta_text:
            yield delta_text


# -----------------------------------------------------------------------------
# Post-Turn Processing
# -----------------------------------------------------------------------------


async def execute_post_turn_processing(
    conn: asyncpg.Connection,
    context_plan: ContextPlan,
    companion: Any,
    relationship: Relationship,
    user_message: str,
    session_id: UUID | None = None,
    session_isolated: bool = False,
) -> None:
    """Execute post-turn effects and enqueue async behaviors."""
    turn_ctx = TurnContext(
        message=user_message,
        companion_id=companion.id,
        conversation_id=None,
        external_user_id=relationship.external_user_id,
        relationship_id=relationship.id,
        session_id=session_id,
        session_isolated=session_isolated,
        turn_count=context_plan.trace.get("turn_count", 1),
        keywords=context_plan.trace.get("keywords", []),
    )

    if context_plan.effects:
        try:
            await execute_post_turn_effects(
                conn, turn_context=turn_ctx, effects=context_plan.effects
            )
        except Exception as e:
            logger.warning(f"Failed to execute post-turn effects: {e}")

    if context_plan.pending_async_behaviors:
        try:
            count = await enqueue_async_behaviors(conn, context_plan, turn_ctx)
            if count > 0:
                logger.info(f"Enqueued {count} async behavior(s) for background execution")
        except Exception as e:
            logger.warning(f"Failed to enqueue async behaviors: {e}")


async def enqueue_async_behaviors(
    conn: asyncpg.Connection,
    context_plan: ContextPlan,
    turn_context: TurnContext,
) -> int:
    """Enqueue async behaviors as jobs for background execution."""
    if not context_plan.pending_async_behaviors:
        return 0

    count = 0
    for pending in context_plan.pending_async_behaviors:
        try:
            params = {
                "behavior_key": pending.behavior_key,
                "trigger_source": pending.trigger_source,
                "trigger_details": pending.trigger_details,
                "user_message": turn_context.message,
                "turn_count": turn_context.turn_count,
                "keywords": turn_context.keywords or [],
            }

            await JobRepository.enqueue(
                conn,
                job_type="behavior_execution",
                companion_id=turn_context.companion_id,
                conversation_id=turn_context.conversation_id,
                external_user_id=turn_context.external_user_id,
                behavior_key=pending.behavior_key,
                params=params,
            )
            count += 1
            logger.debug(f"Enqueued async behavior job: {pending.behavior_key}")
        except Exception as e:
            logger.warning(f"Failed to enqueue async behavior {pending.behavior_key}: {e}")

    return count


async def dispatch_memory_v2_ingestion(
    companion: Any,
    relationship: Relationship,
    user_message: str,
    assistant_response: str,
) -> None:
    """Dispatch Memory V2 ingestion if enabled."""
    mem_config = getattr(companion.config, "memory", None)
    if not mem_config or not mem_config.enabled:
        return
    if getattr(mem_config, "version", 1) != 2:
        return

    try:
        import modal

        fn = modal.Function.from_name("em-memory-v2", "ingest_memory_v2")
        fn.spawn(
            {
                "relationship_id": str(relationship.id),
                "user_message": user_message,
                "assistant_response": assistant_response,
            }
        )
        logger.info(f"Memory V2 ingestion dispatched for relationship={relationship.id}")
    except Exception as e:
        logger.warning(f"Memory V2 ingestion dispatch failed: {e}")


async def dispatch_summarization_if_needed(
    conn: asyncpg.Connection,
    companion: Any,
    relationship: Relationship,
) -> None:
    """Check if summarization should be triggered and dispatch if so.

    Summarization is triggered at multiples of message_limit (default 200, 400, 600...).
    """
    from ..repositories.summary_repository import SummaryRepository

    # Get message limit from companion config (default 200)
    message_limit = (
        companion.config.context.message_limit
        if hasattr(companion.config, "context") and companion.config.context
        else 200
    )

    # Get current message count from the relationship
    # We use the updated message_count which was just incremented by finalize_turn
    message_count = relationship.message_count + 2  # +2 because finalize_turn adds 2

    # Get last summarized count
    last_summarized = getattr(relationship, "last_summarized_message_count", 0) or 0

    # Check if we've crossed a threshold
    # Trigger at: message_limit, 2*message_limit, 3*message_limit, ...
    current_window = message_count // message_limit
    last_window = last_summarized // message_limit

    if current_window > last_window and message_count >= message_limit:
        # Time to summarize!
        await dispatch_summarization(
            conn,
            SummaryRepository,
            relationship,
            message_limit,
            last_summarized,
            message_count,
        )


async def dispatch_summarization(
    conn: asyncpg.Connection,
    SummaryRepository: Any,
    relationship: Relationship,
    message_limit: int,
    last_summarized: int,
    current_count: int,
) -> None:
    """Dispatch summarization job to Modal."""

    # Get previous summary if exists
    previous_summary = None
    version = 1

    latest = await SummaryRepository.get_latest_summary(conn, relationship.id)
    if latest:
        previous_summary = latest["content"]
        version = latest["version"] + 1

    # Calculate message range to summarize
    # For v1: messages 1 to message_limit
    # For v2+: messages (last_summarized + 1) to (current_window * message_limit)
    messages_start = last_summarized + 1 if last_summarized > 0 else 1
    messages_end = (current_count // message_limit) * message_limit

    # Fetch the messages using seq numbers
    messages = await RelationshipRepository.get_messages_for_summarization(
        conn, relationship.id, messages_start, messages_end
    )

    if not messages:
        logger.warning(
            f"No messages found for summarization: relationship={relationship.id}, "
            f"range={messages_start}-{messages_end}"
        )
        return

    # Dispatch to Modal
    try:
        import modal

        modal_env = os.getenv("MODAL_ENVIRONMENT", "staging")
        fn = modal.Function.from_name(
            "em-memory-v2", "summarize_relationship", environment_name=modal_env
        )
        fn.spawn(
            {
                "relationship_id": str(relationship.id),
                "previous_summary": previous_summary,
                "messages": messages,
                "messages_start": messages_start,
                "messages_end": messages_end,
                "version": version,
            }
        )
        logger.info(
            f"Summarization dispatched for relationship={relationship.id}, "
            f"version={version}, messages={messages_start}-{messages_end}"
        )
    except Exception as e:
        logger.warning(f"Failed to dispatch summarization: {e}")


async def finalize_turn(
    conn: asyncpg.Connection,
    relationship: Relationship,
) -> None:
    """Update relationship stats after turn completion."""
    await RelationshipRepository.record_interaction(conn, relationship.id)

    if not relationship.context_mode_locked:
        await RelationshipRepository.lock_context_mode(conn, relationship.id)


# -----------------------------------------------------------------------------
# Turn Processor
# -----------------------------------------------------------------------------


class TurnProcessor:
    """Orchestrates the complete message turn cycle.

    This class provides the core logic for processing a user message and
    generating an assistant response. Transport-specific code (SSE/WebSocket)
    uses the EventEmitter protocol to handle event delivery.

    Usage:
        processor = TurnProcessor(conn, companion, relationship, emitter)
        result = await processor.process_turn(turn_input)
    """

    def __init__(
        self,
        conn: asyncpg.Connection,
        companion: Any,
        relationship: Relationship,
        emitter: EventEmitter | None = None,
        debug_mode: bool = False,
    ):
        self.conn = conn
        self.companion = companion
        self.relationship = relationship
        self.emitter = emitter
        self.debug_mode = debug_mode
        self.state = TurnState()

    async def _refresh_relationship_for_context(self) -> None:
        """Reload relationship state immediately before prompt construction."""
        fresh_relationship = await RelationshipRepository.get_by_id(
            self.conn,
            self.relationship.id,
        )
        if fresh_relationship is not None:
            self.relationship = fresh_relationship

    async def process_turn_streaming(self, turn_input: TurnInput) -> TurnResult:
        """Process a turn with streaming response.

        Emits events via the emitter as the turn progresses.
        """
        start_time = time.perf_counter()

        # Resolve context mode and model config
        dialogmachine_fast_mode = is_dialogmachine_fast_mode(self.relationship)
        context_mode = (
            "dialogmachine-fast"
            if dialogmachine_fast_mode
            else resolve_v2_context_mode(
                self.relationship.context_mode,
                self.companion.config,
            )
        )
        self.state.model, self.state.temperature = resolve_model_config(
            self.companion, turn_input.config
        )
        if dialogmachine_fast_mode and not (turn_input.config and turn_input.config.model):
            self.state.model = extract_dialogmachine_text_model(self.relationship)

        # Get sequence numbers
        self.state.user_seq = await get_next_seq(self.conn, self.relationship.id)
        self.state.assistant_seq = self.state.user_seq + 1

        # Save user message
        user_row = await save_message(
            self.conn,
            relationship_id=self.relationship.id,
            role="user",
            content=turn_input.content,
            seq=self.state.user_seq,
            session_id=turn_input.session_id,
        )
        self.state.user_message_id = user_row["id"]

        # Emit ack
        if self.emitter:
            await self.emitter.emit_ack(
                self.state.turn_id,
                user_row["id"],
                self.state.user_seq,
                turn_input.client_message_id,
            )

        await self._refresh_relationship_for_context()
        dialogmachine_fast_mode = is_dialogmachine_fast_mode(self.relationship)
        context_mode = (
            "dialogmachine-fast"
            if dialogmachine_fast_mode
            else resolve_v2_context_mode(
                self.relationship.context_mode,
                self.companion.config,
            )
        )
        self.state.model, self.state.temperature = resolve_model_config(
            self.companion, turn_input.config
        )
        if dialogmachine_fast_mode and not (turn_input.config and turn_input.config.model):
            self.state.model = extract_dialogmachine_text_model(self.relationship)

        # Build context
        if context_mode == "dialogmachine-fast":
            self.state.llm_messages = await build_dialogmachine_fast_messages(
                self.conn,
                self.companion,
                self.relationship,
                turn_input.content,
                session_id=turn_input.session_id,
                session_isolated=turn_input.session_isolated,
            )
            self.state.context_plan = None
        elif context_mode == "layered":
            if self.emitter:
                await self.emitter.emit_status(self.state.turn_id, "building_context", "start")

            event_callback = self._make_trace_callback() if self.debug_mode else None

            self.state.llm_messages, self.state.context_plan = await build_context_messages(
                self.conn,
                self.companion,
                self.relationship,
                turn_input.content,
                turn_input.session_id,
                turn_input.session_isolated,
                event_callback,
            )

            if self.emitter:
                await self.emitter.emit_status(self.state.turn_id, "building_context", "end")
        else:
            self.state.llm_messages = await build_legacy_messages(
                self.conn, self.companion, self.relationship, turn_input.content
            )
            self.state.context_plan = None

        # Stream LLM response
        if self.emitter:
            await self.emitter.emit_status(
                self.state.turn_id,
                "thinking",
                "start",
                {"model": self.state.model},
            )

        max_tokens = get_max_tokens(self.companion, self.state.model)
        assistant_parts: list[str] = []

        async for delta in stream_llm_response(
            self.state.llm_messages,
            self.state.model,
            self.state.temperature,
            max_tokens,
        ):
            assistant_parts.append(delta)
            if self.emitter:
                await self.emitter.emit_delta(self.state.turn_id, delta)

        if self.emitter:
            await self.emitter.emit_status(self.state.turn_id, "thinking", "end")

        assistant_text = "".join(assistant_parts).strip()
        build_ms = int((time.perf_counter() - start_time) * 1000)

        # Save assistant message
        assistant_row = await save_message(
            self.conn,
            relationship_id=self.relationship.id,
            role="assistant",
            content=assistant_text,
            seq=self.state.assistant_seq,
            session_id=turn_input.session_id,
            build_ms=build_ms,
            metadata=build_turn_profile_metadata(self.companion, self.relationship),
        )

        # Post-turn processing
        await finalize_turn(self.conn, self.relationship)

        if self.state.context_plan:
            await execute_post_turn_processing(
                self.conn,
                self.state.context_plan,
                self.companion,
                self.relationship,
                turn_input.content,
                turn_input.session_id,
                turn_input.session_isolated,
            )

        # Memory V2 ingestion
        await dispatch_memory_v2_ingestion(
            self.companion,
            self.relationship,
            turn_input.content,
            assistant_text,
        )

        # Check for summarization trigger
        await dispatch_summarization_if_needed(
            self.conn,
            self.companion,
            self.relationship,
        )

        # Emit final message
        if self.emitter:
            await self.emitter.emit_message(
                self.state.turn_id,
                assistant_row["id"],
                self.relationship.id,
                assistant_text,
                self.state.assistant_seq,
                build_ms,
            )

        return TurnResult(
            user_message_id=user_row["id"],
            user_seq=self.state.user_seq,
            assistant_message_id=assistant_row["id"],
            assistant_seq=self.state.assistant_seq,
            assistant_content=assistant_text,
            build_ms=build_ms,
            trace=self.state.context_plan.trace if self.state.context_plan else None,
        )

    async def process_turn_non_streaming(self, turn_input: TurnInput) -> TurnResult:
        """Process a turn without streaming (returns complete response)."""
        start_time = time.perf_counter()

        # Resolve context mode and model config
        dialogmachine_fast_mode = is_dialogmachine_fast_mode(self.relationship)
        context_mode = (
            "dialogmachine-fast"
            if dialogmachine_fast_mode
            else resolve_v2_context_mode(
                self.relationship.context_mode,
                self.companion.config,
            )
        )
        self.state.model, self.state.temperature = resolve_model_config(
            self.companion, turn_input.config
        )
        if dialogmachine_fast_mode and not (turn_input.config and turn_input.config.model):
            self.state.model = extract_dialogmachine_text_model(self.relationship)

        # Get sequence numbers
        self.state.user_seq = await get_next_seq(self.conn, self.relationship.id)
        self.state.assistant_seq = self.state.user_seq + 1

        # Save user message
        user_row = await save_message(
            self.conn,
            relationship_id=self.relationship.id,
            role="user",
            content=turn_input.content,
            seq=self.state.user_seq,
            session_id=turn_input.session_id,
        )

        await self._refresh_relationship_for_context()
        dialogmachine_fast_mode = is_dialogmachine_fast_mode(self.relationship)
        context_mode = (
            "dialogmachine-fast"
            if dialogmachine_fast_mode
            else resolve_v2_context_mode(
                self.relationship.context_mode,
                self.companion.config,
            )
        )
        self.state.model, self.state.temperature = resolve_model_config(
            self.companion, turn_input.config
        )
        if dialogmachine_fast_mode and not (turn_input.config and turn_input.config.model):
            self.state.model = extract_dialogmachine_text_model(self.relationship)

        # Build context
        if context_mode == "dialogmachine-fast":
            self.state.llm_messages = await build_dialogmachine_fast_messages(
                self.conn,
                self.companion,
                self.relationship,
                turn_input.content,
                session_id=turn_input.session_id,
                session_isolated=turn_input.session_isolated,
            )
            self.state.context_plan = None
        elif context_mode == "layered":
            self.state.llm_messages, self.state.context_plan = await build_context_messages(
                self.conn,
                self.companion,
                self.relationship,
                turn_input.content,
                turn_input.session_id,
                turn_input.session_isolated,
            )
        else:
            self.state.llm_messages = await build_legacy_messages(
                self.conn, self.companion, self.relationship, turn_input.content
            )
            self.state.context_plan = None

        # Generate response
        max_tokens = get_max_tokens(self.companion, self.state.model)
        assistant_text = await generate_response_non_streaming(
            self.state.llm_messages,
            self.state.model,
            self.state.temperature,
            max_tokens,
        )

        build_ms = int((time.perf_counter() - start_time) * 1000)

        # Save assistant message
        assistant_row = await save_message(
            self.conn,
            relationship_id=self.relationship.id,
            role="assistant",
            content=assistant_text,
            seq=self.state.assistant_seq,
            session_id=turn_input.session_id,
            build_ms=build_ms,
            metadata=build_turn_profile_metadata(self.companion, self.relationship),
        )

        # Post-turn processing
        await finalize_turn(self.conn, self.relationship)

        if self.state.context_plan:
            await execute_post_turn_processing(
                self.conn,
                self.state.context_plan,
                self.companion,
                self.relationship,
                turn_input.content,
                turn_input.session_id,
                turn_input.session_isolated,
            )

        # Memory V2 ingestion
        await dispatch_memory_v2_ingestion(
            self.companion,
            self.relationship,
            turn_input.content,
            assistant_text,
        )

        # Check for summarization trigger
        await dispatch_summarization_if_needed(
            self.conn,
            self.companion,
            self.relationship,
        )

        return TurnResult(
            user_message_id=user_row["id"],
            user_seq=self.state.user_seq,
            assistant_message_id=assistant_row["id"],
            assistant_seq=self.state.assistant_seq,
            assistant_content=assistant_text,
            build_ms=build_ms,
            trace=self.state.context_plan.trace if self.state.context_plan else None,
        )

    def _make_trace_callback(self) -> Callable[[ContextEvent], None]:
        """Create a callback that emits trace events."""

        def trace_callback(ev: ContextEvent) -> None:
            if self.emitter:
                try:
                    asyncio.create_task(self.emitter.emit_trace(self.state.turn_id, ev))
                except Exception as e:
                    logger.warning(f"Failed to emit trace: {e}")

        return trace_callback
