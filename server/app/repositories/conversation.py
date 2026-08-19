"""
Repository for conversation and message operations following the CONVERSATION_PERSISTENCE_PLAN.

This module provides database operations for:
- Creating and managing conversations tied to WebSocket sessions
- Storing and retrieving conversation messages
- Supporting the session-based testing workflow where each "Press to Speak" creates a new conversation
"""

import logging
from typing import Any, Dict, List
from uuid import UUID, uuid4

import asyncpg

from ..services.cache_manager import cache, ttl_from_env

logger = logging.getLogger(__name__)

# Cache TTL for message history
_HISTORY_CACHE_TTL_S = ttl_from_env("TEXT_HISTORY_CACHE_TTL_S", 30.0)

# Cache namespace
_HISTORY_NS = "repo:history"


def _history_cache_key(conversation_id: UUID) -> str:
    """Build cache key for message history."""
    return str(conversation_id)


def invalidate_history_cache(conversation_id: UUID) -> None:
    """Invalidate cached message history for a conversation."""
    cache.delete(_HISTORY_NS, _history_cache_key(conversation_id))


def append_to_history_cache(conversation_id: UUID, message: Dict[str, Any]) -> None:
    """Append a message to cached history without full invalidation.

    This is an optimization to avoid cache miss after sending a message.
    If the conversation is not in cache, this is a no-op.
    """
    cache_key = _history_cache_key(conversation_id)
    cached = cache.get(_HISTORY_NS, cache_key)
    if cached is None:
        return  # Not in cache, nothing to append to

    # Make a copy to avoid mutating cached list
    messages = list(cached)

    # Avoid duplicates (check by id)
    if messages and messages[-1].get("id") == message.get("id"):
        return

    messages.append(message)
    cache.set(_HISTORY_NS, cache_key, messages, _HISTORY_CACHE_TTL_S)


async def create_conversation(
    conn: asyncpg.Connection,
    deployment_id: UUID,
    external_user_id: str,
    *,
    share_id: UUID | None = None,
    share_token_hash: bytes | None = None,
) -> UUID:
    """
    Create new conversation and return its ID.

    Args:
        conn: Database connection
        deployment_id: ID of the deployment (companion version being tested)
        external_user_id: Session ID or user identifier for tracking

    Returns:
        UUID of the created conversation
    """
    # Get companion_id from deployment
    companion_row = await conn.fetchrow(
        "SELECT companion_id FROM deployments WHERE id = $1", deployment_id
    )
    if not companion_row:
        raise ValueError(f"Deployment {deployment_id} not found")

    companion_id = companion_row["companion_id"]
    conversation_id = uuid4()
    await conn.execute(
        """
        INSERT INTO conversations (
            id, deployment_id, external_user_id, companion_id, share_id, share_token_hash
        ) VALUES ($1, $2, $3, $4, $5, $6)
        """,
        conversation_id,
        deployment_id,
        external_user_id,
        companion_id,
        share_id,
        share_token_hash,
    )
    logger.info(
        f"Created conversation {conversation_id} for deployment {deployment_id} (companion {companion_id})"
    )
    return conversation_id


async def create_conversation_for_companion(
    conn: asyncpg.Connection,
    companion_id: UUID,
    external_user_id: str,
    *,
    share_id: UUID | None = None,
    share_token_hash: bytes | None = None,
) -> UUID:
    """
    Create conversation directly linked to companion (for development/testing).
    No deployment required.

    Args:
        conn: Database connection
        companion_id: ID of the companion
        external_user_id: Session ID or user identifier for tracking

    Returns:
        UUID of the created conversation
    """
    conversation_id = uuid4()
    await conn.execute(
        """
        INSERT INTO conversations (
            id, external_user_id, companion_id, share_id, share_token_hash
        ) VALUES ($1, $2, $3, $4, $5)
        """,
        conversation_id,
        external_user_id,
        companion_id,
        share_id,
        share_token_hash,
    )
    logger.info(f"Created conversation {conversation_id} directly for companion {companion_id}")
    return conversation_id


async def create_conversation_for_companion_version(
    conn: asyncpg.Connection,
    companion_id: UUID,
    version_id: UUID,
    external_user_id: str,
    *,
    share_id: UUID | None = None,
    share_token_hash: bytes | None = None,
) -> UUID:
    """
    Create conversation linked to a specific companion version.
    This ensures analytics can retrieve the exact system prompt used.

    Args:
        conn: Database connection
        companion_id: ID of the companion
        version_id: ID of the specific companion version used
        external_user_id: Session ID or user identifier for tracking

    Returns:
        UUID of the created conversation
    """
    # Create a temporary deployment for this version to maintain the existing schema
    # This allows us to link the conversation to the specific version
    deployment_id = uuid4()
    conversation_id = uuid4()

    # Create temporary deployment with a slug that stays unique even when truncated
    # Keep room for a hyphen and an 8-char suffix derived from conversation_id
    suffix = str(conversation_id)[:8]
    prefix = "session-"
    max_len = 50
    room_for_user = max_len - len(prefix) - 1 - len(suffix)
    user_part = (external_user_id or "user")[: max(room_for_user, 0)]
    slug = f"{prefix}{user_part}-{suffix}"
    await conn.execute(
        """
        INSERT INTO deployments (id, companion_id, version_id, slug, is_active)
        VALUES ($1, $2, $3, $4, false)
        """,
        deployment_id,
        companion_id,
        version_id,
        slug,
    )

    # Create conversation linked to the deployment (and thus the version)
    await conn.execute(
        """
        INSERT INTO conversations (
            id, deployment_id, external_user_id, companion_id, share_id, share_token_hash
        ) VALUES ($1, $2, $3, $4, $5, $6)
        """,
        conversation_id,
        deployment_id,
        external_user_id,
        companion_id,
        share_id,
        share_token_hash,
    )

    logger.info(
        f"Created conversation {conversation_id} for companion {companion_id} version {version_id}"
    )
    return conversation_id


async def add_message(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    role: str,
    content: str,
    *,
    input_modality: str | None = None,
) -> UUID:
    """
    Add message to conversation.

    Args:
        conn: Database connection
        conversation_id: ID of the conversation
        role: Message role ('system', 'user', 'assistant')
        content: Message content

    Returns:
        UUID of the created message
    """
    message_id = uuid4()
    row = await conn.fetchrow(
        """
        INSERT INTO messages (id, conversation_id, role, content, input_modality)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, role, content, created_at, input_modality
        """,
        message_id,
        conversation_id,
        role,
        content,
        input_modality,
    )
    logger.debug(f"Added {role} message to conversation {conversation_id}")
    # Append to cache instead of invalidating
    if row:
        append_to_history_cache(conversation_id, dict(row))
    return message_id


async def add_message_returning(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    role: str,
    content: str,
    *,
    input_modality: str | None = None,
) -> Dict[str, Any]:
    """Insert a message and return its row fields (id, role, content, created_at)."""
    message_id = uuid4()
    row = await conn.fetchrow(
        """
        INSERT INTO messages (id, conversation_id, role, content, input_modality)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, role, content, created_at, input_modality
        """,
        message_id,
        conversation_id,
        role,
        content,
        input_modality,
    )
    message = dict(row)
    # Append to cache instead of invalidating
    append_to_history_cache(conversation_id, message)
    return message


async def get_conversation_messages(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    *,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """
    Get all messages for a conversation ordered by creation time.

    Args:
        conn: Database connection
        conversation_id: ID of the conversation
        use_cache: Whether to use cache (default True)

    Returns:
        List of message dictionaries with id, role, content, created_at
    """
    cache_key = _history_cache_key(conversation_id)

    # Check cache first
    if use_cache:
        cached: List[Dict[str, Any]] | None = cache.get(_HISTORY_NS, cache_key)
        if cached is not None:
            return cached

    # Fetch from database
    rows = await conn.fetch(
        """
        SELECT id, role, content, created_at, input_modality
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at
        """,
        conversation_id,
    )
    messages = [dict(row) for row in rows]

    # Cache the result
    if use_cache:
        cache.set(_HISTORY_NS, cache_key, messages, _HISTORY_CACHE_TTL_S)

    return messages


async def get_conversation_by_session(
    conn: asyncpg.Connection, session_id: str
) -> Dict[str, Any] | None:
    """
    Get the most recent conversation by session ID (external_user_id).

    Args:
        conn: Database connection
        session_id: Session identifier used as external_user_id

    Returns:
        Conversation dictionary or None if not found
    """
    row = await conn.fetchrow(
        """
        SELECT
            id,
            deployment_id,
            external_user_id,
            companion_id,
            started_at,
            share_id,
            share_token_hash
        FROM conversations
        WHERE external_user_id = $1
        ORDER BY started_at DESC
        LIMIT 1
        """,
        session_id,
    )
    return dict(row) if row else None


async def get_conversation_by_id(
    conn: asyncpg.Connection, conversation_id: UUID
) -> Dict[str, Any] | None:
    """
    Get conversation by ID.

    Args:
        conn: Database connection
        conversation_id: ID of the conversation

    Returns:
        Conversation dictionary or None if not found
    """
    row = await conn.fetchrow(
        """
        SELECT
            id,
            deployment_id,
            external_user_id,
            companion_id,
            started_at,
            share_id,
            share_token_hash
        FROM conversations
        WHERE id = $1
        """,
        conversation_id,
    )
    return dict(row) if row else None


async def add_messages_batch(conn: asyncpg.Connection, messages: List[Dict[str, Any]]) -> None:
    """
    Batch insert messages for performance optimization.

    Args:
        conn: Database connection
        messages: List of message dictionaries with id, conversation_id, role, content
    """
    if not messages:
        return

    values = [
        (
            msg["id"],
            msg["conversation_id"],
            msg["role"],
            msg["content"],
            msg.get("input_modality"),
        )
        for msg in messages
    ]
    await conn.executemany(
        """
        INSERT INTO messages (id, conversation_id, role, content, input_modality)
        VALUES ($1, $2, $3, $4, $5)
        """,
        values,
    )
    # Invalidate cache for all affected conversations
    conversation_ids = {msg["conversation_id"] for msg in messages}
    for conv_id in conversation_ids:
        invalidate_history_cache(conv_id)


async def get_latest_conversation_for_companion(
    conn: asyncpg.Connection,
    companion_id: UUID,
    owner_id: UUID,
    *,
    external_user_id: str | None = None,
    external_user_prefix: str | None = None,
) -> Dict[str, Any] | None:
    """Fetch the latest conversation for a companion scoped to the owner and optional builder id."""

    filters = ["c.companion_id = $1", "comp.owner_id = $2"]
    params: List[Any] = [companion_id, owner_id]

    if external_user_id and external_user_prefix:
        params.extend([external_user_id, f"{external_user_prefix}%"])
        filters.append(
            f"(c.external_user_id = ${len(params) - 1} OR c.external_user_id ILIKE ${len(params)})"
        )
    elif external_user_id:
        params.append(external_user_id)
        filters.append(f"c.external_user_id = ${len(params)}")
    elif external_user_prefix:
        params.append(f"{external_user_prefix}%")
        filters.append(f"c.external_user_id ILIKE ${len(params)}")

    query = f"""
        SELECT
            c.id,
            c.deployment_id,
            c.external_user_id,
            c.companion_id,
            c.started_at,
            c.share_id,
            c.share_token_hash,
            c.message_count
        FROM conversations c
        JOIN companions comp ON comp.id = c.companion_id
        WHERE {" AND ".join(filters)}
        ORDER BY c.started_at DESC
        LIMIT 1
    """

    row = await conn.fetchrow(query, *params)
    return dict(row) if row else None


async def delete_conversation(conn: asyncpg.Connection, conversation_id: UUID) -> bool:
    """
    Delete conversation and all its messages.

    Args:
        conn: Database connection
        conversation_id: ID of the conversation to delete

    Returns:
        True if conversation was deleted, False if not found
    """
    result = await conn.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
    # Extract number of deleted rows from result string like "DELETE 1"
    deleted_count = int(result.split()[-1])
    if deleted_count > 0:
        logger.info(f"Deleted conversation {conversation_id}")
        return True
    return False


async def get_user_analytics_summary(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    external_user_id: str,
) -> Dict[str, int]:
    """Return session and message aggregates for a given user within a companion."""

    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS session_count,
               COALESCE(SUM(c.message_count), 0) AS total_message_count
        FROM conversations c
        WHERE c.companion_id = $1
          AND c.external_user_id = $2
        """,
        companion_id,
        external_user_id,
    )

    if not row:
        return {"session_count": 0, "total_message_count": 0}

    return {
        "session_count": int(row["session_count"] or 0),
        "total_message_count": int(row["total_message_count"] or 0),
    }


async def set_conversation_context_engine(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    context_engine: str,
) -> None:
    """Set the context_engine for a conversation (only if not already set)."""
    await conn.execute(
        """
        UPDATE conversations
        SET context_engine = $2
        WHERE id = $1 AND context_engine IS NULL
        """,
        conversation_id,
        context_engine,
    )


async def add_message_with_build_ms(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    role: str,
    content: str,
    *,
    input_modality: str | None = None,
    build_ms: int | None = None,
) -> Dict[str, Any]:
    """Insert a message with optional build_ms timing (for assistant messages)."""
    message_id = uuid4()
    row = await conn.fetchrow(
        """
        INSERT INTO messages (id, conversation_id, role, content, input_modality, build_ms)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, role, content, created_at, input_modality, build_ms
        """,
        message_id,
        conversation_id,
        role,
        content,
        input_modality,
        build_ms,
    )
    message = dict(row)
    # Append to cache instead of invalidating
    append_to_history_cache(conversation_id, message)
    return message
