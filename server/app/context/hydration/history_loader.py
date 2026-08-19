"""HistoryLoader: Thin wrapper over conversation repository for message history.

Delegates to conversation repository which handles caching and invalidation internally.
Tracks cache hits for debugging/tracing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from ...repositories import conversation as conversation_repo
from ...services.cache_manager import cache

# Cache namespace (must match conversation.py)
_HISTORY_NS = "repo:history"


def _history_cache_key(conversation_id: UUID) -> str:
    """Generate cache key for history."""
    return str(conversation_id)


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """Result of loading message history."""

    messages: list[dict[str, Any]]
    from_cache: bool = False


class HistoryLoader:
    """Loads message history via conversation repository (which handles caching)."""

    @staticmethod
    async def load(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        *,
        use_cache: bool = True,
    ) -> HistoryResult:
        """Load message history for a conversation.

        Args:
            conn: Database connection
            conversation_id: The conversation ID
            use_cache: Whether to use cache (default True)

        Returns:
            HistoryResult with list of message dicts
        """
        # Check cache first to track cache hits
        from_cache = False
        if use_cache:
            cache_key = _history_cache_key(conversation_id)
            cached: list[dict[str, Any]] | None = cache.get(_HISTORY_NS, cache_key)
            if cached is not None:
                from_cache = True

        messages = await conversation_repo.get_conversation_messages(
            conn, conversation_id, use_cache=use_cache
        )
        return HistoryResult(messages=messages, from_cache=from_cache)
