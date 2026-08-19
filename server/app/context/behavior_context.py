"""BehaviorContext: Developer-facing API for behavior functions.

This module provides the context object passed to behavior functions,
giving developers a clean API for state management, scheduling, and
other side effects.

Phase 6: Namespace API
- ctx.profile.get(path, default) / ctx.profile.set(path, value) / ctx.profile.delete(path)
- ctx.session.get(key, default) / ctx.session.set(key, value) / ctx.session.delete(key)
- ctx.sense.* - placeholder (returns None)

Memory V2: Scratchpad Memory
- ctx.memory.add(content, type) - Add a memory entry
- ctx.memory.update(memory_id, content, type) - Update an entry
- ctx.memory.delete(memory_id) - Delete an entry

Example:
    @behavior(key="remember_preference", triggers=["favorite"])
    async def remember_preference(ctx: BehaviorContext) -> None:
        # Read from profile
        prefs = ctx.profile.get("preferences", default={})

        # Update profile
        ctx.profile.set("preferences.color", "blue")

        # Read from session (if in session)
        current_goal = ctx.session.get("current_goal")

        # Schedule follow-up
        await ctx.schedule_behavior("follow_up_behavior", run_at="in 5 minutes")

        # Send webhook notification
        await ctx.notify_webhook("preference_updated", {"color": "blue"})
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List
from uuid import UUID

from .schemas import TurnContext, TurnEffect

logger = logging.getLogger(__name__)


def _parse_relative_time(time_str: str) -> datetime:
    """Parse relative time strings like 'in 5 minutes' or 'in 1 hour'.

    Supports: seconds, minutes, hours, days
    """
    parts = time_str.lower().strip().split()
    if len(parts) < 3 or parts[0] != "in":
        raise ValueError(f"Invalid relative time format: {time_str}")

    try:
        amount = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time amount: {parts[1]}")

    unit = parts[2].rstrip("s")  # Handle both "minute" and "minutes"
    now = datetime.utcnow()

    if unit == "second":
        return now + timedelta(seconds=amount)
    elif unit == "minute":
        return now + timedelta(minutes=amount)
    elif unit == "hour":
        return now + timedelta(hours=amount)
    elif unit == "day":
        return now + timedelta(days=amount)
    else:
        raise ValueError(f"Unknown time unit: {unit}")


def _get_nested_value(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    """Get a value from nested dict using dot notation path."""
    if not path:
        return data if data else default

    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _build_nested_dict(path: str, value: Any) -> Dict[str, Any]:
    """Build a nested dict from a dot notation path and value.

    e.g., _build_nested_dict("user.prefs.color", "blue")
    returns {"user": {"prefs": {"color": "blue"}}}
    """
    parts = path.split(".")
    result: Dict[str, Any] = {}
    current = result
    for i, part in enumerate(parts[:-1]):
        current[part] = {}
        current = current[part]
    current[parts[-1]] = value
    return result


class ProfileNamespace:
    """Namespace for profile operations (ctx.profile.*)."""

    def __init__(self, ctx: BehaviorContext):
        self._ctx = ctx

    def get(self, path: str = "", default: Any = None) -> Any:
        """Get a value from profile using dot notation path.

        Args:
            path: Dot-notation key path (e.g., "user.preferences.color")
                  Empty string returns entire profile
            default: Default value if key not found

        Returns:
            The value at the path, or default
        """
        return _get_nested_value(self._ctx._profile, path, default)

    def set(self, path: str, value: Any) -> None:
        """Set a value in profile.

        This emits a profile patch effect that will be applied post-turn.

        Args:
            path: Dot-notation key path (e.g., "user.preferences.color")
            value: Value to store
        """
        # Build nested structure for JSON merge patch
        patch_data = _build_nested_dict(path, value)

        self._ctx._effects.append(
            TurnEffect(
                effect_type="state_patch",
                payload={
                    "target": "profile",
                    "key": path,
                    "value": value,
                    "patch_data": patch_data,
                    "operation": "set",
                },
            )
        )
        logger.debug(f"Behavior emitted profile set: {path} = {value}")

    def delete(self, path: str) -> None:
        """Delete a key from profile.

        This emits a profile patch effect with delete operation.

        Args:
            path: Dot-notation key path to delete
        """
        # For JSON merge patch, setting to null deletes the key
        patch_data = _build_nested_dict(path, None)

        self._ctx._effects.append(
            TurnEffect(
                effect_type="state_patch",
                payload={
                    "target": "profile",
                    "key": path,
                    "patch_data": patch_data,
                    "operation": "delete",
                },
            )
        )
        logger.debug(f"Behavior emitted profile delete: {path}")


class SessionNamespace:
    """Namespace for session state operations (ctx.session.*).

    Session state is temporary and only available when in an explicit session.
    If isolated=True on the session, all write operations are no-ops.
    """

    def __init__(self, ctx: BehaviorContext):
        self._ctx = ctx

    def get(self, key: str = "", default: Any = None) -> Any:
        """Get a value from session state.

        Args:
            key: Key name (simple key, not dot-notation)
                 Empty string returns entire session state
            default: Default value if key not found

        Returns:
            The value, or default if not in session or key not found
        """
        if not self._ctx._session_state:
            return default
        if not key:
            return self._ctx._session_state
        return self._ctx._session_state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in session state.

        This emits a session patch effect that will be applied post-turn.
        If not in a session or session is isolated, this is a no-op.

        Args:
            key: Key name
            value: Value to store
        """
        if not self._ctx._session_id:
            logger.debug("Session set ignored: not in a session")
            return

        if self._ctx._session_isolated:
            logger.debug("Session set ignored: session is isolated")
            return

        self._ctx._effects.append(
            TurnEffect(
                effect_type="state_patch",
                payload={
                    "target": "session",
                    "session_id": str(self._ctx._session_id),
                    "key": key,
                    "value": value,
                    "operation": "set",
                },
            )
        )
        logger.debug(f"Behavior emitted session set: {key} = {value}")

    def delete(self, key: str) -> None:
        """Delete a key from session state.

        If not in a session or session is isolated, this is a no-op.

        Args:
            key: Key name to delete
        """
        if not self._ctx._session_id:
            logger.debug("Session delete ignored: not in a session")
            return

        if self._ctx._session_isolated:
            logger.debug("Session delete ignored: session is isolated")
            return

        self._ctx._effects.append(
            TurnEffect(
                effect_type="state_patch",
                payload={
                    "target": "session",
                    "session_id": str(self._ctx._session_id),
                    "key": key,
                    "operation": "delete",
                },
            )
        )
        logger.debug(f"Behavior emitted session delete: {key}")


class SenseNamespace:
    """Namespace for sense/enrichment data (ctx.sense.*).

    Placeholder for Phase 7+ - platform-computed analysis per turn.
    Currently returns None for all properties.
    """

    def __init__(self, ctx: BehaviorContext):
        self._ctx = ctx

    @property
    def emotion(self) -> str | None:
        """Detected emotion (placeholder - returns None)."""
        return None

    @property
    def sentiment(self) -> float | None:
        """Sentiment score -1 to 1 (placeholder - returns None)."""
        return None

    @property
    def topics(self) -> List[str] | None:
        """Extracted topics (placeholder - returns None)."""
        return None

    @property
    def mental_state(self) -> Dict[str, Any] | None:
        """Mental state analysis (placeholder - returns None)."""
        return None


class MemoryNamespace:
    """Namespace for memory operations (ctx.memory.*).

    Memory V2 scratchpad - ChatGPT-style memory that persists important facts
    about the user. Entries are stored per-relationship and injected into
    the system prompt.

    Entry types:
    - identity: Personal facts (name, age, location, profession)
    - preference: User preferences and communication style
    - goal: Goals, plans, and aspirations
    - event: Significant life events or milestones
    - relationship: Important relationships (family, friends)
    - other: General information
    """

    def __init__(self, ctx: BehaviorContext):
        self._ctx = ctx

    def add(self, content: str, type: str | None = None) -> None:
        """Add a memory entry to the scratchpad.

        Args:
            content: Memory content to store (e.g., "User's name is Sarah")
            type: Optional type label: identity, preference, goal, event, relationship, other
        """
        if not content or not content.strip():
            logger.warning("Memory add ignored: empty content")
            return

        self._ctx._effects.append(
            TurnEffect(
                effect_type="memory_v2_write",
                payload={
                    "operation": "add",
                    "content": content.strip(),
                    "type": type,
                },
            )
        )
        logger.debug(f"Behavior emitted memory_v2 add: {content[:50]}...")

    def update(self, memory_id: str, content: str, type: str | None = None) -> None:
        """Update an existing memory entry.

        Args:
            memory_id: ID of memory to update (UUID string)
            content: New content
            type: Optional new type label
        """
        if not memory_id:
            logger.warning("Memory update ignored: no memory_id")
            return
        if not content or not content.strip():
            logger.warning("Memory update ignored: empty content")
            return

        self._ctx._effects.append(
            TurnEffect(
                effect_type="memory_v2_write",
                payload={
                    "operation": "update",
                    "memory_id": memory_id,
                    "content": content.strip(),
                    "type": type,
                },
            )
        )
        logger.debug(f"Behavior emitted memory_v2 update: {memory_id}")

    def delete(self, memory_id: str) -> None:
        """Delete a memory entry.

        Args:
            memory_id: ID of memory to delete (UUID string)
        """
        if not memory_id:
            logger.warning("Memory delete ignored: no memory_id")
            return

        self._ctx._effects.append(
            TurnEffect(
                effect_type="memory_v2_write",
                payload={
                    "operation": "delete",
                    "memory_id": memory_id,
                },
            )
        )
        logger.debug(f"Behavior emitted memory_v2 delete: {memory_id}")


class BehaviorContext:
    """Context passed to behavior functions providing developer verbs.

    Behaviors receive this context and use its methods to emit side effects.
    All methods are non-blocking and emit TurnEffects that are executed
    post-turn by the PostTurnExecutor.

    Attributes:
        turn_context: The current turn context (message, ids, etc.)
        profile: ProfileNamespace for ctx.profile.* operations
        session: SessionNamespace for ctx.session.* operations
        sense: SenseNamespace for ctx.sense.* (placeholder)
        memory: MemoryNamespace for ctx.memory.* (Memory V2 scratchpad)
    """

    def __init__(
        self,
        turn_context: TurnContext,
        profile: Dict[str, Any] | None = None,
        session_id: UUID | None = None,
        session_state: Dict[str, Any] | None = None,
        session_isolated: bool = False,
    ):
        self.turn_context = turn_context
        self._profile = profile or {}
        self._session_id = session_id
        self._session_state = session_state or {}
        self._session_isolated = session_isolated
        self._effects: List[TurnEffect] = []

        # Initialize namespace objects
        self.profile = ProfileNamespace(self)
        self.session = SessionNamespace(self)
        self.sense = SenseNamespace(self)
        self.memory = MemoryNamespace(self)

    @property
    def message(self) -> str:
        """The user's message text."""
        return self.turn_context.message

    @property
    def companion_id(self) -> UUID:
        """The companion ID."""
        return self.turn_context.companion_id

    @property
    def conversation_id(self) -> UUID | None:
        """The conversation ID (if in a conversation)."""
        return self.turn_context.conversation_id

    @property
    def relationship_id(self) -> UUID | None:
        """The relationship ID (v2 API)."""
        return self.turn_context.relationship_id

    @property
    def external_user_id(self) -> str | None:
        """The external user ID."""
        return self.turn_context.external_user_id

    @property
    def session_id(self) -> UUID | None:
        """The session ID (if in a session)."""
        return self._session_id

    @property
    def keywords(self) -> List[str]:
        """Keywords extracted from the user message."""
        return self.turn_context.keywords or []

    @property
    def turn_count(self) -> int:
        """Number of turns in the current conversation."""
        return self.turn_context.turn_count

    @property
    def effects(self) -> List[TurnEffect]:
        """List of effects emitted by this behavior."""
        return self._effects

    # -------------------------------------------------------------------------
    # Scheduling
    # -------------------------------------------------------------------------

    async def schedule_behavior(
        self,
        behavior_key: str,
        *,
        run_at: str | None = None,
        params: Dict[str, Any] | None = None,
        priority: int = 50,
    ) -> None:
        """Schedule a behavior for future execution.

        Args:
            behavior_key: The behavior to execute
            run_at: When to run - ISO datetime string or relative like "in 5 minutes"
            params: Optional parameters to pass to the behavior
            priority: Execution priority (higher = sooner when multiple due)
        """
        # Parse run_at
        run_at_iso: str | None = None
        if run_at:
            if run_at.lower().startswith("in "):
                run_at_iso = _parse_relative_time(run_at).isoformat()
            else:
                # Assume ISO format
                run_at_iso = run_at

        self._effects.append(
            TurnEffect(
                effect_type="schedule",
                payload={
                    "behavior_key": behavior_key,
                    "run_at": run_at_iso,
                    "params": params or {},
                    "priority": priority,
                },
            )
        )
        logger.debug(f"Behavior emitted schedule: {behavior_key} at {run_at_iso}")

    async def cancel_scheduled_behavior(self, behavior_key: str) -> None:
        """Cancel a previously scheduled behavior.

        Args:
            behavior_key: The behavior key to cancel
        """
        self._effects.append(
            TurnEffect(
                effect_type="schedule",
                payload={
                    "behavior_key": behavior_key,
                    "cancel": True,
                },
            )
        )
        logger.debug(f"Behavior emitted schedule cancel: {behavior_key}")

    # -------------------------------------------------------------------------
    # Webhooks
    # -------------------------------------------------------------------------

    async def notify_webhook(
        self,
        webhook_key: str,
        data: Dict[str, Any] | None = None,
        event_type: str = "behavior_triggered",
    ) -> None:
        """Send a notification to a developer webhook.

        Args:
            webhook_key: The configured webhook name
            data: Payload to send
            event_type: Type of event (default: "behavior_triggered")
        """
        self._effects.append(
            TurnEffect(
                effect_type="webhook",
                payload={
                    "webhook_key": webhook_key,
                    "event_type": event_type,
                    "data": data or {},
                },
            )
        )
        logger.debug(f"Behavior emitted webhook: {webhook_key} ({event_type})")

    # -------------------------------------------------------------------------
    # Proactive Messaging (Phase 7)
    # -------------------------------------------------------------------------

    def send_message(
        self,
        content: str,
        *,
        expires_in_hours: int = 24,
    ) -> None:
        """Send a proactive message to the user.

        This emits a proactive_message effect that will be processed post-turn.
        The message will be delivered via WebSocket if the user is connected,
        or stored in their inbox for later retrieval.

        Args:
            content: The message content to send
            expires_in_hours: How long the message stays in inbox if undelivered (default: 24h)
        """
        self._effects.append(
            TurnEffect(
                effect_type="proactive_message",
                payload={
                    "content": content,
                    "expires_in_hours": expires_in_hours,
                },
            )
        )
        logger.debug(f"Behavior emitted proactive message: {content[:50]}...")

    # -------------------------------------------------------------------------
    # LLM (placeholder)
    # -------------------------------------------------------------------------

    async def llm(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 100,
    ) -> str:
        """Call an LLM for dynamic content generation.

        This is a placeholder that currently returns an empty string.
        Future implementation will support actual LLM calls.

        Args:
            prompt: The prompt to send to the LLM
            model: Optional model override
            max_tokens: Maximum tokens in response

        Returns:
            Empty string (placeholder)
        """
        # TODO: Implement actual LLM call
        # This would need to be synchronous within the behavior or use
        # a different pattern (perhaps emit an effect that triggers
        # an LLM call and stores the result)
        logger.debug(f"Behavior llm() called (placeholder): {prompt[:50]}...")
        return ""


__all__ = ["BehaviorContext"]
