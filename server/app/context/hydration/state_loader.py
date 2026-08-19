"""StateLoader: Thin wrapper over StateRepository for user and conversation state.

Delegates to StateRepository which handles caching and invalidation internally.
Tracks cache hits for debugging/tracing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from ...models.state import CompanionUserState, ConversationState
from ...repositories.state_repository import StateRepository
from ...services.cache_manager import cache

# Cache namespaces (must match state_repository.py)
_USER_STATE_NS = "repo:user_state"
_CONV_STATE_NS = "repo:conv_state"


def _user_state_cache_key(companion_id: UUID, external_user_id: str) -> str:
    """Generate cache key for user state."""
    return f"{companion_id}:{external_user_id}"


def _conv_state_cache_key(conversation_id: UUID) -> str:
    """Generate cache key for conversation state."""
    return str(conversation_id)


@dataclass(frozen=True, slots=True)
class UserStateResult:
    """Result of loading user state."""

    profile: dict[str, Any] | None
    state_version: int | None
    exists: bool = True  # False if no relationship exists
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class ConversationStateResult:
    """Result of loading conversation state."""

    state: ConversationState | None
    exists: bool = True  # False if no conversation state exists
    from_cache: bool = False


class StateLoader:
    """Loads user/conversation state via StateRepository (which handles caching)."""

    @staticmethod
    async def load_user_state(
        conn: asyncpg.Connection,
        companion_id: UUID,
        external_user_id: str,
        *,
        use_cache: bool = True,
    ) -> UserStateResult:
        """Load user state (profile) from relationships table.

        Args:
            conn: Database connection
            companion_id: The companion ID
            external_user_id: The external user ID
            use_cache: Whether to use cache (default True)

        Returns:
            UserStateResult with profile data (may be empty if not exists)
        """
        # Check cache first to track cache hits
        from_cache = False
        if use_cache:
            cache_key = _user_state_cache_key(companion_id, external_user_id)
            cached: CompanionUserState | None = cache.get(_USER_STATE_NS, cache_key)
            if cached is not None:
                from_cache = True

        state = await StateRepository.get_user_state(
            conn,
            companion_id=companion_id,
            external_user_id=external_user_id,
            use_cache=use_cache,
        )

        if not state:
            return UserStateResult(profile=None, state_version=None, exists=False)

        return UserStateResult(
            profile=state.profile,
            state_version=state.version,
            exists=True,
            from_cache=from_cache,
        )

    @staticmethod
    async def load_conversation_state(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        *,
        use_cache: bool = True,
    ) -> ConversationStateResult:
        """Load conversation state.

        Args:
            conn: Database connection
            conversation_id: The conversation ID
            use_cache: Whether to use cache (default True)

        Returns:
            ConversationStateResult with state data (may be None if not exists)
        """
        # Check cache first to track cache hits
        from_cache = False
        if use_cache:
            cache_key = _conv_state_cache_key(conversation_id)
            cached: ConversationState | None = cache.get(_CONV_STATE_NS, cache_key)
            if cached is not None:
                from_cache = True

        state = await StateRepository.get_conversation_state(
            conn, conversation_id, use_cache=use_cache
        )

        if not state:
            return ConversationStateResult(state=None, exists=False)

        return ConversationStateResult(state=state, exists=True, from_cache=from_cache)
