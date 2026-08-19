"""Repository for companion and conversation state management.

Provides data access for the two-table state architecture:
- relationships: Per (companion, user) pair, persists across conversations
- conversation_states: Per conversation, ephemeral
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List
from uuid import UUID

import asyncpg

from ..models.state import CompanionUserState, ConversationState
from ..services.cache_manager import cache, ttl_from_env

logger = logging.getLogger(__name__)

# Cache TTLs
_USER_STATE_CACHE_TTL_S = ttl_from_env("USER_STATE_CACHE_TTL_S", 30.0)
_CONV_STATE_CACHE_TTL_S = ttl_from_env("CONV_STATE_CACHE_TTL_S", 30.0)

# Cache namespaces
_USER_STATE_NS = "repo:user_state"
_CONV_STATE_NS = "repo:conv_state"


def _user_state_cache_key(companion_id: UUID, external_user_id: str) -> str:
    """Build cache key for user state."""
    return f"{companion_id}:{external_user_id}"


def _conv_state_cache_key(conversation_id: UUID) -> str:
    """Build cache key for conversation state."""
    return str(conversation_id)


def _normalize_jsonb(record: Dict[str, Any], *fields: str) -> Dict[str, Any]:
    """Ensure JSONB fields are dicts, not strings."""
    for fld in fields:
        val = record.get(fld)
        if isinstance(val, str):
            try:
                record[fld] = json.loads(val)
            except json.JSONDecodeError:
                record[fld] = {}
        elif val is None:
            record[fld] = {}
    return record


class StateRepository:
    """Data access helpers for state tables.

    All methods are static and require an asyncpg connection to be passed in,
    following the repository pattern used elsewhere in the codebase.
    """

    # -------------------------------------------------------------------------
    # Companion+User State (persists across conversations)
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_user_state(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        external_user_id: str,
        use_cache: bool = True,
    ) -> CompanionUserState | None:
        """Get state for a companion+user pair.

        Returns None if no state exists yet (first interaction).

        Args:
            conn: Database connection
            companion_id: The companion ID
            external_user_id: The external user ID
            use_cache: Whether to use cache (default True)
        """
        cache_key = _user_state_cache_key(companion_id, external_user_id)

        # Check cache first
        if use_cache:
            cached: CompanionUserState | None = cache.get(_USER_STATE_NS, cache_key)
            if cached is not None:
                return cached

        # Fetch from database
        row = await conn.fetchrow(
            """
            SELECT id, companion_id, external_user_id,
                   profile, version, created_at, updated_at
            FROM relationships
            WHERE companion_id = $1 AND external_user_id = $2
            """,
            companion_id,
            external_user_id,
        )
        if not row:
            return None

        data = _normalize_jsonb(dict(row), "profile")
        state = CompanionUserState(**data)

        # Cache the result
        if use_cache:
            cache.set(_USER_STATE_NS, cache_key, state, _USER_STATE_CACHE_TTL_S)

        return state

    @staticmethod
    async def create_user_state(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        external_user_id: str,
        profile: Dict[str, Any] | None = None,
    ) -> CompanionUserState:
        """Create a new state record for a companion+user pair."""
        row = await conn.fetchrow(
            """
            INSERT INTO relationships (
                companion_id, external_user_id, profile
            ) VALUES (
                $1, $2, COALESCE($3, '{}'::jsonb)
            )
            RETURNING id, companion_id, external_user_id,
                      profile, version, created_at, updated_at
            """,
            companion_id,
            external_user_id,
            json.dumps(profile) if profile else None,
        )
        data = _normalize_jsonb(dict(row), "profile")
        return CompanionUserState(**data)

    @staticmethod
    async def get_or_create_user_state(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        external_user_id: str,
    ) -> CompanionUserState:
        """Get existing state or create a new one with defaults."""
        state = await StateRepository.get_user_state(
            conn, companion_id=companion_id, external_user_id=external_user_id
        )
        if state:
            return state
        return await StateRepository.create_user_state(
            conn, companion_id=companion_id, external_user_id=external_user_id
        )

    @staticmethod
    async def update_user_state(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        external_user_id: str,
        profile: Dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> CompanionUserState | None:
        """Update state with optimistic locking.

        If expected_version is provided, the update only succeeds if the
        current version matches. Returns None if version mismatch (conflict).

        Pass None for profile if you don't want to update. Pass a dict to replace
        the entire field. For partial updates, use patch_user_state.
        """
        updates = []
        params: List[Any] = []
        idx = 1

        if profile is not None:
            params.append(json.dumps(profile))
            updates.append(f"profile = ${idx}")
            idx += 1

        if not updates:
            # Nothing to update, just return current state
            return await StateRepository.get_user_state(
                conn, companion_id=companion_id, external_user_id=external_user_id
            )

        # Always increment version
        updates.append("version = version + 1")

        # Build WHERE clause
        params.extend([companion_id, external_user_id])
        where_parts = [f"companion_id = ${idx}", f"external_user_id = ${idx + 1}"]
        idx += 2

        if expected_version is not None:
            params.append(expected_version)
            where_parts.append(f"version = ${idx}")

        sql = f"""
            UPDATE relationships
            SET {", ".join(updates)}
            WHERE {" AND ".join(where_parts)}
            RETURNING id, companion_id, external_user_id,
                      profile, version, created_at, updated_at
        """
        row = await conn.fetchrow(sql, *params)
        if not row:
            return None

        data = _normalize_jsonb(dict(row), "profile")
        # Invalidate user state cache
        cache.delete(_USER_STATE_NS, _user_state_cache_key(companion_id, external_user_id))
        return CompanionUserState(**data)

    @staticmethod
    async def patch_user_state(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        external_user_id: str,
        patches: List[Dict[str, Any]],
        expected_version: int | None = None,
    ) -> CompanionUserState | None:
        """Apply patches to profile using JSONB operations.

        Each patch is a dict with: key, value, operation (default: set)
        Example: {"key": "mood", "value": "happy"}

        For nested keys, use dot notation: "preferences.color"
        """
        if not patches:
            return await StateRepository.get_user_state(
                conn, companion_id=companion_id, external_user_id=external_user_id
            )

        # Build JSONB update expressions for profile
        set_clauses = []
        params: List[Any] = []
        idx = 1

        for patch in patches:
            key = patch.get("key", "")
            value = patch.get("value")
            operation = patch.get("operation", "set")

            if not key:
                continue

            # Convert dot notation to JSONB path array
            path_parts = key.split(".")
            path_array = "{" + ",".join(path_parts) + "}"

            if operation == "set":
                params.append(json.dumps(value))
                set_clauses.append(
                    f"profile = jsonb_set(profile, '{path_array}', ${idx}::jsonb, true)"
                )
                idx += 1
            elif operation == "delete":
                set_clauses.append(f"profile = profile #- '{path_array}'")

        if not set_clauses:
            return await StateRepository.get_user_state(
                conn, companion_id=companion_id, external_user_id=external_user_id
            )

        # Always increment version
        set_clauses.append("version = version + 1")

        # Build WHERE clause
        params.extend([companion_id, external_user_id])
        where_parts = [f"companion_id = ${idx}", f"external_user_id = ${idx + 1}"]
        idx += 2

        if expected_version is not None:
            params.append(expected_version)
            where_parts.append(f"version = ${idx}")

        sql = f"""
            UPDATE relationships
            SET {", ".join(set_clauses)}
            WHERE {" AND ".join(where_parts)}
            RETURNING id, companion_id, external_user_id,
                      profile, version, created_at, updated_at
        """
        row = await conn.fetchrow(sql, *params)
        if not row:
            return None

        data = _normalize_jsonb(dict(row), "profile")
        # Invalidate user state cache
        cache.delete(_USER_STATE_NS, _user_state_cache_key(companion_id, external_user_id))
        return CompanionUserState(**data)

    @staticmethod
    async def reset_user_state(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        external_user_id: str,
    ) -> CompanionUserState | None:
        """Reset profile to empty object."""
        row = await conn.fetchrow(
            """
            UPDATE relationships
            SET profile = '{}'::jsonb, version = version + 1
            WHERE companion_id = $1 AND external_user_id = $2
            RETURNING id, companion_id, external_user_id,
                      profile, version, created_at, updated_at
            """,
            companion_id,
            external_user_id,
        )
        if not row:
            return None

        data = _normalize_jsonb(dict(row), "profile")
        # Invalidate user state cache
        cache.delete(_USER_STATE_NS, _user_state_cache_key(companion_id, external_user_id))
        return CompanionUserState(**data)

    @staticmethod
    async def delete_user_state(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        external_user_id: str,
    ) -> bool:
        """Delete state for a companion+user pair entirely."""
        result = await conn.execute(
            """
            DELETE FROM relationships
            WHERE companion_id = $1 AND external_user_id = $2
            """,
            companion_id,
            external_user_id,
        )
        deleted = result == "DELETE 1"
        if deleted:
            # Invalidate user state cache
            cache.delete(_USER_STATE_NS, _user_state_cache_key(companion_id, external_user_id))
        return deleted

    # -------------------------------------------------------------------------
    # Conversation State (per-conversation, ephemeral)
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_conversation_state(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        *,
        use_cache: bool = True,
    ) -> ConversationState | None:
        """Get state for a conversation.

        Returns None if no state exists yet.

        Args:
            conn: Database connection
            conversation_id: The conversation ID
            use_cache: Whether to use cache (default True)
        """
        cache_key = _conv_state_cache_key(conversation_id)

        # Check cache first
        if use_cache:
            cached: ConversationState | None = cache.get(_CONV_STATE_NS, cache_key)
            if cached is not None:
                return cached

        # Fetch from database
        row = await conn.fetchrow(
            """
            SELECT conversation_id, topic_state, turn_count, metadata,
                   created_at, updated_at
            FROM conversation_states
            WHERE conversation_id = $1
            """,
            conversation_id,
        )
        if not row:
            return None

        data = _normalize_jsonb(dict(row), "topic_state", "metadata")
        state = ConversationState(**data)

        # Cache the result
        if use_cache:
            cache.set(_CONV_STATE_NS, cache_key, state, _CONV_STATE_CACHE_TTL_S)

        return state

    @staticmethod
    async def create_conversation_state(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        topic_state: Dict[str, Any] | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> ConversationState:
        """Create state for a new conversation."""
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_states (conversation_id, topic_state, metadata)
            VALUES (
                $1,
                COALESCE($2, '{"current_topic": null, "topic_stack": [], "topic_history": [], "topic_confidence": null}'::jsonb),
                COALESCE($3, '{}'::jsonb)
            )
            RETURNING *
            """,
            conversation_id,
            json.dumps(topic_state) if topic_state else None,
            json.dumps(metadata) if metadata else None,
        )
        data = _normalize_jsonb(dict(row), "topic_state", "metadata")
        return ConversationState(**data)

    @staticmethod
    async def get_or_create_conversation_state(
        conn: asyncpg.Connection,
        conversation_id: UUID,
    ) -> ConversationState:
        """Get existing conversation state or create a new one."""
        state = await StateRepository.get_conversation_state(conn, conversation_id)
        if state:
            return state
        return await StateRepository.create_conversation_state(conn, conversation_id)

    @staticmethod
    async def update_conversation_state(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        topic_state: Dict[str, Any] | None = None,
        metadata: Dict[str, Any] | None = None,
        increment_turn: bool = False,
    ) -> ConversationState | None:
        """Update conversation state fields."""
        updates = []
        params: List[Any] = []
        idx = 1

        if topic_state is not None:
            params.append(json.dumps(topic_state))
            updates.append(f"topic_state = ${idx}")
            idx += 1
        if metadata is not None:
            params.append(json.dumps(metadata))
            updates.append(f"metadata = ${idx}")
            idx += 1
        if increment_turn:
            updates.append("turn_count = turn_count + 1")

        if not updates:
            return await StateRepository.get_conversation_state(conn, conversation_id)

        params.append(conversation_id)
        sql = f"""
            UPDATE conversation_states
            SET {", ".join(updates)}
            WHERE conversation_id = ${idx}
            RETURNING *
        """
        row = await conn.fetchrow(sql, *params)
        if not row:
            return None

        data = _normalize_jsonb(dict(row), "topic_state", "metadata")
        # Invalidate conversation state cache
        cache.delete(_CONV_STATE_NS, _conv_state_cache_key(conversation_id))
        return ConversationState(**data)

    @staticmethod
    async def increment_turn_count(
        conn: asyncpg.Connection,
        conversation_id: UUID,
    ) -> int:
        """Increment turn count and return the new value.

        Creates conversation state if it doesn't exist.
        """
        # Try to increment existing
        row = await conn.fetchrow(
            """
            UPDATE conversation_states
            SET turn_count = turn_count + 1
            WHERE conversation_id = $1
            RETURNING turn_count
            """,
            conversation_id,
        )
        if row:
            # Invalidate conversation state cache
            cache.delete(_CONV_STATE_NS, _conv_state_cache_key(conversation_id))
            return row["turn_count"]

        # Create new with turn_count = 1
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_states (conversation_id, turn_count)
            VALUES ($1, 1)
            ON CONFLICT (conversation_id) DO UPDATE SET turn_count = conversation_states.turn_count + 1
            RETURNING turn_count
            """,
            conversation_id,
        )
        # Invalidate conversation state cache
        cache.delete(_CONV_STATE_NS, _conv_state_cache_key(conversation_id))
        return row["turn_count"] if row else 1

    @staticmethod
    async def patch_conversation_state(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        patches: List[Dict[str, Any]],
    ) -> ConversationState | None:
        """Apply patches to conversation state using JSONB operations.

        Each patch targets topic_state or metadata with a key and value.
        """
        if not patches:
            return await StateRepository.get_conversation_state(conn, conversation_id)

        set_clauses = []
        params: List[Any] = []
        idx = 1

        for patch in patches:
            target = patch.get("target", "metadata")
            if target not in ("topic_state", "metadata"):
                continue

            key = patch.get("key", "")
            value = patch.get("value")
            operation = patch.get("operation", "set")

            if not key:
                continue

            path_parts = key.split(".")
            path_array = "{" + ",".join(path_parts) + "}"

            if operation == "set":
                params.append(json.dumps(value))
                set_clauses.append(
                    f"{target} = jsonb_set({target}, '{path_array}', ${idx}::jsonb, true)"
                )
                idx += 1
            elif operation == "delete":
                set_clauses.append(f"{target} = {target} #- '{path_array}'")

        if not set_clauses:
            return await StateRepository.get_conversation_state(conn, conversation_id)

        params.append(conversation_id)
        sql = f"""
            UPDATE conversation_states
            SET {", ".join(set_clauses)}
            WHERE conversation_id = ${idx}
            RETURNING *
        """
        row = await conn.fetchrow(sql, *params)
        if not row:
            return None

        data = _normalize_jsonb(dict(row), "topic_state", "metadata")
        # Invalidate conversation state cache
        cache.delete(_CONV_STATE_NS, _conv_state_cache_key(conversation_id))
        return ConversationState(**data)

    # -------------------------------------------------------------------------
    # Turn Context (per-turn snapshots for debugging/analytics)
    # -------------------------------------------------------------------------

    @staticmethod
    async def save_turn_context(
        conn: asyncpg.Connection,
        *,
        conversation_id: UUID | None,  # NULL for test mode
        companion_id: UUID,
        turn_number: int,
        message_id: UUID | None = None,
        context_mode: str = "raw",
        classifier_used: bool = False,
        system_prompt: str | None = None,
        system_prompt_tokens: int | None = None,
        execution_summary: Dict[str, Any] | None = None,
        token_usage: Dict[str, Any] | None = None,
        build_ms: int | None = None,
        classifier_ms: int | None = None,
        llm_ms: int | None = None,
        layer_details: Dict[str, Any] | None = None,
    ) -> UUID:
        """Save a turn context snapshot.

        This should be called asynchronously after the LLM response
        to avoid blocking the response path.

        Returns the ID of the created turn_context record.
        """
        row = await conn.fetchrow(
            """
            INSERT INTO turn_context (
                conversation_id, message_id, companion_id, turn_number,
                context_mode, classifier_used,
                system_prompt, system_prompt_tokens,
                execution_summary, token_usage,
                build_ms, classifier_ms, llm_ms,
                layer_details
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7, $8,
                $9, $10,
                $11, $12, $13,
                $14
            )
            RETURNING id
            """,
            conversation_id,
            message_id,
            companion_id,
            turn_number,
            context_mode,
            classifier_used,
            system_prompt,
            system_prompt_tokens,
            json.dumps(execution_summary) if execution_summary else None,
            json.dumps(token_usage) if token_usage else None,
            build_ms,
            classifier_ms,
            llm_ms,
            json.dumps(layer_details) if layer_details else None,
        )
        return row["id"]

    @staticmethod
    async def get_turn_context(
        conn: asyncpg.Connection,
        *,
        conversation_id: UUID,
        turn_number: int,
    ) -> Dict[str, Any] | None:
        """Get turn context for a specific turn in a conversation."""
        row = await conn.fetchrow(
            """
            SELECT * FROM turn_context
            WHERE conversation_id = $1 AND turn_number = $2
            """,
            conversation_id,
            turn_number,
        )
        if not row:
            return None

        data = dict(row)
        # Normalize JSONB fields
        for field in ("execution_summary", "token_usage", "layer_details"):
            if isinstance(data.get(field), str):
                try:
                    data[field] = json.loads(data[field])
                except json.JSONDecodeError:
                    data[field] = {}
        return data

    @staticmethod
    async def get_conversation_turn_contexts(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get all turn contexts for a conversation, ordered by turn number."""
        rows = await conn.fetch(
            """
            SELECT * FROM turn_context
            WHERE conversation_id = $1
            ORDER BY turn_number ASC
            LIMIT $2
            """,
            conversation_id,
            limit,
        )
        results = []
        for row in rows:
            data = dict(row)
            for field in ("execution_summary", "token_usage", "layer_details"):
                if isinstance(data.get(field), str):
                    try:
                        data[field] = json.loads(data[field])
                    except json.JSONDecodeError:
                        data[field] = {}
            results.append(data)
        return results

    @staticmethod
    async def get_recent_turn_contexts_for_companion(
        conn: asyncpg.Connection,
        companion_id: UUID,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get recent turn contexts across all conversations for a companion.

        Useful for analytics and debugging.
        """
        rows = await conn.fetch(
            """
            SELECT tc.*, c.external_user_id
            FROM turn_context tc
            JOIN conversations c ON tc.conversation_id = c.id
            WHERE tc.companion_id = $1
            ORDER BY tc.created_at DESC
            LIMIT $2
            """,
            companion_id,
            limit,
        )
        results = []
        for row in rows:
            data = dict(row)
            for field in ("execution_summary", "token_usage", "layer_details"):
                if isinstance(data.get(field), str):
                    try:
                        data[field] = json.loads(data[field])
                    except json.JSONDecodeError:
                        data[field] = {}
            results.append(data)
        return results
