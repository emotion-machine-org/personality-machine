"""Repository for v2 Relationship operations.

Provides data access for the `relationships` table (renamed from companion_user_states).
Supports the v2 API with relationship-centric operations.

Phase 6: Simplified state model
- Renamed app_state -> profile
- Removed user_state/companion_state columns (no longer used)
- Added session state methods
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple
from uuid import UUID

import asyncpg

from ..models.v2.relationship import Relationship
from ..services.cache_manager import cache
from ..utils.profile import deep_copy_dict, deep_merge_with_schema

logger = logging.getLogger(__name__)

# Backward-compatible alias used by older tests.
_deep_merge_with_schema = deep_merge_with_schema

# Cache namespace (must match StateRepository)
_USER_STATE_NS = "repo:user_state"


def _user_state_cache_key(companion_id: UUID, external_user_id: str) -> str:
    """Build cache key for user state (must match StateRepository)."""
    return f"{companion_id}:{external_user_id}"


def _normalize_jsonb(record: Dict[str, Any], *fields: str) -> Dict[str, Any]:
    """Ensure JSONB fields are dicts, not strings or lists."""
    for fld in fields:
        val = record.get(fld)
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                # Ensure parsed value is a dict
                record[fld] = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                record[fld] = {}
        elif isinstance(val, list):
            # Handle corrupted data that became a list - try to merge dicts from list
            merged = {}
            for item in val:
                if isinstance(item, dict):
                    merged.update(item)
                elif isinstance(item, str):
                    try:
                        parsed = json.loads(item)
                        if isinstance(parsed, dict):
                            merged.update(parsed)
                    except json.JSONDecodeError:
                        pass
            record[fld] = merged
        elif val is None or not isinstance(val, dict):
            record[fld] = {}
    return record


def _row_to_relationship(row: asyncpg.Record) -> Relationship:
    """Convert a database row to a Relationship model."""
    data = _normalize_jsonb(
        dict(row),
        "profile",
        "config",
        "metadata",
    )
    # Handle user_id generated column
    if "user_id" not in data or data["user_id"] is None:
        data["user_id"] = data.get("external_user_id", "")
    return Relationship(**data)


class RelationshipRepository:
    """Data access for the relationships table (v2 API)."""

    # -------------------------------------------------------------------------
    # Core CRUD
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_by_id(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> Relationship | None:
        """Get a relationship by its ID."""
        row = await conn.fetchrow(
            """
            SELECT id, companion_id, external_user_id, user_id,
                   profile, config, context_mode, context_mode_locked,
                   metadata, last_interaction_at, message_count,
                   last_summarized_at, last_summarized_message_count,
                   version, created_at, updated_at
            FROM relationships
            WHERE id = $1
            """,
            relationship_id,
        )
        if not row:
            return None
        return _row_to_relationship(row)

    @staticmethod
    async def get_by_companion_and_user(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        user_id: str,
    ) -> Relationship | None:
        """Get a relationship by companion_id and user_id."""
        row = await conn.fetchrow(
            """
            SELECT id, companion_id, external_user_id, user_id,
                   profile, config, context_mode, context_mode_locked,
                   metadata, last_interaction_at, message_count,
                   last_summarized_at, last_summarized_message_count,
                   version, created_at, updated_at
            FROM relationships
            WHERE companion_id = $1 AND external_user_id = $2
            """,
            companion_id,
            user_id,
        )
        if not row:
            return None
        return _row_to_relationship(row)

    @staticmethod
    async def create(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        user_id: str,
        profile: Dict[str, Any] | None = None,
        config: Dict[str, Any] | None = None,
        context_mode: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> Relationship:
        """Create a new relationship."""
        row = await conn.fetchrow(
            """
            INSERT INTO relationships (
                companion_id, external_user_id,
                profile, config, context_mode, metadata
            ) VALUES (
                $1, $2,
                COALESCE($3, '{}'::jsonb),
                COALESCE($4, '{}'::jsonb),
                $5,
                COALESCE($6, '{}'::jsonb)
            )
            RETURNING id, companion_id, external_user_id, user_id,
                      profile, config, context_mode, context_mode_locked,
                      metadata, last_interaction_at, message_count,
                      last_summarized_at, last_summarized_message_count,
                      version, created_at, updated_at
            """,
            companion_id,
            user_id,
            profile,
            config,
            context_mode,
            metadata,
        )
        return _row_to_relationship(row)

    @staticmethod
    async def ensure_exists(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        user_id: str,
        profile: Dict[str, Any] | None = None,
        config: Dict[str, Any] | None = None,
        context_mode: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> Tuple[Relationship, bool]:
        """Get existing relationship or create a new one.

        If creating a new relationship and no profile is provided,
        initializes the profile from the companion's profile_schema.

        Returns (relationship, created) tuple.
        """
        existing = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if existing:
            return existing, False

        # If no profile provided, initialize from companion's profile_schema
        initial_profile = profile
        if initial_profile is None:
            schema_row = await conn.fetchrow(
                """
                SELECT config->'profile_schema' as profile_schema
                FROM companion_versions
                WHERE companion_id = $1 AND status = 'DEPLOYED'
                ORDER BY version_number DESC
                LIMIT 1
                """,
                companion_id,
            )
            if schema_row and schema_row["profile_schema"]:
                schema = schema_row["profile_schema"]
                if isinstance(schema, str):
                    schema = json.loads(schema)
                if schema:
                    initial_profile = deep_copy_dict(schema)

        try:
            new_rel = await RelationshipRepository.create(
                conn,
                companion_id=companion_id,
                user_id=user_id,
                profile=initial_profile,
                config=config,
                context_mode=context_mode,
                metadata=metadata,
            )
            return new_rel, True
        except asyncpg.UniqueViolationError:
            # Concurrent ensure requests can both observe no row, then race the
            # unique (companion_id, external_user_id) constraint. Treat the
            # loser as an idempotent read.
            existing = await RelationshipRepository.get_by_companion_and_user(
                conn, companion_id=companion_id, user_id=user_id
            )
            if existing:
                return existing, False
            raise

    @staticmethod
    async def delete(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> bool:
        """Delete a relationship by ID."""
        result = await conn.execute(
            "DELETE FROM relationships WHERE id = $1",
            relationship_id,
        )
        return result == "DELETE 1"

    @staticmethod
    async def list_for_companion(
        conn: asyncpg.Connection,
        companion_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Relationship], int]:
        """List relationships for a companion with pagination.

        Returns (relationships, total_count) tuple.
        """
        # Get total count
        count_row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM relationships WHERE companion_id = $1",
            companion_id,
        )
        total = count_row["cnt"] if count_row else 0

        # Get paginated results
        rows = await conn.fetch(
            """
            SELECT id, companion_id, external_user_id, user_id,
                   profile, config, context_mode, context_mode_locked,
                   metadata, last_interaction_at, message_count,
                   last_summarized_at, last_summarized_message_count,
                   version, created_at, updated_at
            FROM relationships
            WHERE companion_id = $1
            ORDER BY last_interaction_at DESC NULLS LAST, created_at DESC
            LIMIT $2 OFFSET $3
            """,
            companion_id,
            limit,
            offset,
        )

        relationships = [_row_to_relationship(row) for row in rows]
        return relationships, total

    # -------------------------------------------------------------------------
    # Profile Operations (renamed from app_state)
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_profile(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> Tuple[Dict[str, Any], int] | None:
        """Get profile and version for a relationship.

        Returns (profile, version) or None if not found.
        Note: This returns the raw profile without schema merging.
        Use get_profile_merged() for schema-aware profile retrieval.
        """
        row = await conn.fetchrow(
            "SELECT profile, version FROM relationships WHERE id = $1",
            relationship_id,
        )
        if not row:
            return None
        profile = row["profile"]
        if isinstance(profile, str):
            profile = json.loads(profile)
        return profile or {}, row["version"]

    @staticmethod
    async def get_profile_merged(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> Tuple[Dict[str, Any], int] | None:
        """Get profile merged with companion's profile_schema.

        This implements lazy migration: missing fields from the schema are
        automatically filled with their default values, while existing profile
        data is preserved.

        Returns (merged_profile, version) or None if relationship not found.
        """
        # Fetch profile, companion_id, and schema in one query
        row = await conn.fetchrow(
            """
            SELECT r.profile, r.version, r.companion_id,
                   cv.config->'profile_schema' as profile_schema
            FROM relationships r
            LEFT JOIN companion_versions cv ON cv.companion_id = r.companion_id
                AND cv.status = 'DEPLOYED'
            WHERE r.id = $1
            ORDER BY cv.version_number DESC
            LIMIT 1
            """,
            relationship_id,
        )
        if not row:
            return None

        profile = row["profile"]
        if isinstance(profile, str):
            profile = json.loads(profile)
        profile = profile or {}

        schema = row["profile_schema"]
        if isinstance(schema, str):
            schema = json.loads(schema)
        schema = schema or {}

        # Merge profile with schema (schema provides defaults for missing fields)
        merged = deep_merge_with_schema(profile, schema)

        return merged, row["version"]

    @staticmethod
    async def set_profile(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        profile: Dict[str, Any],
    ) -> Relationship | None:
        """Replace profile entirely."""
        row = await conn.fetchrow(
            """
            UPDATE relationships
            SET profile = $1, version = version + 1
            WHERE id = $2
            RETURNING id, companion_id, external_user_id, user_id,
                      profile, config, context_mode, context_mode_locked,
                      metadata, last_interaction_at, message_count,
                      last_summarized_at, last_summarized_message_count,
                      version, created_at, updated_at
            """,
            profile,
            relationship_id,
        )
        if not row:
            return None
        rel = _row_to_relationship(row)
        # Invalidate user state cache
        cache.delete(_USER_STATE_NS, _user_state_cache_key(rel.companion_id, rel.external_user_id))
        return rel

    @staticmethod
    async def patch_profile(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        changes: Dict[str, Any],
    ) -> Relationship | None:
        """Merge changes into profile (JSON Merge Patch)."""
        row = await conn.fetchrow(
            """
            UPDATE relationships
            SET profile = profile || $1, version = version + 1
            WHERE id = $2
            RETURNING id, companion_id, external_user_id, user_id,
                      profile, config, context_mode, context_mode_locked,
                      metadata, last_interaction_at, message_count,
                      last_summarized_at, last_summarized_message_count,
                      version, created_at, updated_at
            """,
            changes,
            relationship_id,
        )
        if not row:
            return None
        rel = _row_to_relationship(row)
        # Invalidate user state cache
        cache.delete(_USER_STATE_NS, _user_state_cache_key(rel.companion_id, rel.external_user_id))
        return rel

    @staticmethod
    async def clear_profile(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> Relationship | None:
        """Clear profile to empty object."""
        row = await conn.fetchrow(
            """
            UPDATE relationships
            SET profile = '{}'::jsonb, version = version + 1
            WHERE id = $1
            RETURNING id, companion_id, external_user_id, user_id,
                      profile, config, context_mode, context_mode_locked,
                      metadata, last_interaction_at, message_count,
                      last_summarized_at, last_summarized_message_count,
                      version, created_at, updated_at
            """,
            relationship_id,
        )
        if not row:
            return None
        rel = _row_to_relationship(row)
        # Invalidate user state cache
        cache.delete(_USER_STATE_NS, _user_state_cache_key(rel.companion_id, rel.external_user_id))
        return rel

    # -------------------------------------------------------------------------
    # Config Operations
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_config(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> Tuple[Dict[str, Any], int] | None:
        """Get config and version for a relationship.

        Returns (config, version) or None if not found.
        """
        row = await conn.fetchrow(
            "SELECT config, version FROM relationships WHERE id = $1",
            relationship_id,
        )
        if not row:
            return None
        config = row["config"]
        if isinstance(config, str):
            config = json.loads(config)
        return config or {}, row["version"]

    @staticmethod
    async def patch_config(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        changes: Dict[str, Any],
    ) -> Relationship | None:
        """Merge changes into config (JSON Merge Patch)."""
        row = await conn.fetchrow(
            """
            UPDATE relationships
            SET config = config || $1, version = version + 1
            WHERE id = $2
            RETURNING id, companion_id, external_user_id, user_id,
                      profile, config, context_mode, context_mode_locked,
                      metadata, last_interaction_at, message_count,
                      last_summarized_at, last_summarized_message_count,
                      version, created_at, updated_at
            """,
            changes,
            relationship_id,
        )
        if not row:
            return None
        return _row_to_relationship(row)

    # -------------------------------------------------------------------------
    # Interaction Tracking
    # -------------------------------------------------------------------------

    @staticmethod
    async def record_interaction(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> None:
        """Update last_interaction_at and increment message_count.

        Increments message_count by 2 (one for user message, one for assistant response).
        This ensures turn_count = message_count // 2 + 1 works correctly.
        """
        await conn.execute(
            """
            UPDATE relationships
            SET last_interaction_at = NOW(),
                message_count = message_count + 2
            WHERE id = $1
            """,
            relationship_id,
        )

    @staticmethod
    async def lock_context_mode(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> None:
        """Lock context_mode after first message."""
        await conn.execute(
            """
            UPDATE relationships
            SET context_mode_locked = TRUE
            WHERE id = $1 AND context_mode_locked = FALSE
            """,
            relationship_id,
        )

    # -------------------------------------------------------------------------
    # Session State Operations (Phase 6)
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_session_state(
        conn: asyncpg.Connection,
        session_id: UUID,
    ) -> Tuple[Dict[str, Any], bool] | None:
        """Get session state and isolated flag.

        Returns (state, isolated) or None if not found.
        """
        row = await conn.fetchrow(
            "SELECT state, isolated FROM v2_sessions WHERE id = $1",
            session_id,
        )
        if not row:
            return None
        state = row["state"]
        if isinstance(state, str):
            state = json.loads(state)
        return state or {}, row["isolated"]

    @staticmethod
    async def patch_session_state(
        conn: asyncpg.Connection,
        session_id: UUID,
        changes: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Merge changes into session state (JSON Merge Patch).

        Returns updated state or None if session not found or is isolated.
        """
        # First check if session exists and is not isolated
        check = await conn.fetchrow(
            "SELECT isolated FROM v2_sessions WHERE id = $1",
            session_id,
        )
        if not check:
            return None
        if check["isolated"]:
            logger.warning(f"Cannot write to isolated session {session_id}")
            return None

        # Apply changes
        row = await conn.fetchrow(
            """
            UPDATE v2_sessions
            SET state = state || $1
            WHERE id = $2 AND isolated = FALSE
            RETURNING state
            """,
            changes,
            session_id,
        )
        if not row:
            return None
        state = row["state"]
        if isinstance(state, str):
            state = json.loads(state)
        return state or {}

    @staticmethod
    async def set_session_state(
        conn: asyncpg.Connection,
        session_id: UUID,
        state: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Replace session state entirely.

        Returns updated state or None if session not found or is isolated.
        """
        # First check if session exists and is not isolated
        check = await conn.fetchrow(
            "SELECT isolated FROM v2_sessions WHERE id = $1",
            session_id,
        )
        if not check:
            return None
        if check["isolated"]:
            logger.warning(f"Cannot write to isolated session {session_id}")
            return None

        # Replace state
        row = await conn.fetchrow(
            """
            UPDATE v2_sessions
            SET state = $1
            WHERE id = $2 AND isolated = FALSE
            RETURNING state
            """,
            state,
            session_id,
        )
        if not row:
            return None
        result = row["state"]
        if isinstance(result, str):
            result = json.loads(result)
        return result or {}

    @staticmethod
    async def delete_session_state_key(
        conn: asyncpg.Connection,
        session_id: UUID,
        key: str,
    ) -> Dict[str, Any] | None:
        """Delete a key from session state.

        Returns updated state or None if session not found or is isolated.
        """
        # First check if session exists and is not isolated
        check = await conn.fetchrow(
            "SELECT isolated FROM v2_sessions WHERE id = $1",
            session_id,
        )
        if not check:
            return None
        if check["isolated"]:
            logger.warning(f"Cannot write to isolated session {session_id}")
            return None

        # Remove key from state
        row = await conn.fetchrow(
            """
            UPDATE v2_sessions
            SET state = state - $1
            WHERE id = $2 AND isolated = FALSE
            RETURNING state
            """,
            key,
            session_id,
        )
        if not row:
            return None
        state = row["state"]
        if isinstance(state, str):
            state = json.loads(state)
        return state or {}

    # -------------------------------------------------------------------------
    # Message History (Phase 8: v2 context loading)
    # -------------------------------------------------------------------------

    @staticmethod
    async def list_messages(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
        session_id: UUID | None = None,
    ) -> tuple[List[Dict[str, Any]], bool]:
        """List sequenced messages for a relationship with cursor-style pagination.

        Query modes:
        - Latest window (default): newest `limit` messages
        - Backward pagination: `before_seq` (messages with seq < before_seq)
        - Forward pagination: `after_seq` (messages with seq > after_seq)

        Returns:
            (messages, has_more)
            - messages are ordered oldest -> newest for client rendering
            - has_more indicates additional rows exist in the queried direction
        """
        if before_seq is not None and after_seq is not None:
            raise ValueError("before_seq and after_seq are mutually exclusive")

        params: List[Any] = [relationship_id]
        filters = [
            "m.relationship_id = $1",
            "m.seq IS NOT NULL",
        ]

        if session_id is not None:
            params.append(session_id)
            filters.append(f"m.session_id = ${len(params)}")

        forward = after_seq is not None
        if after_seq is not None:
            params.append(after_seq)
            filters.append(f"m.seq > ${len(params)}")
            order_clause = "m.seq ASC"
        else:
            if before_seq is not None:
                params.append(before_seq)
                filters.append(f"m.seq < ${len(params)}")
            order_clause = "m.seq DESC"

        params.append(limit + 1)
        query = f"""
            SELECT
                m.id,
                m.relationship_id,
                m.session_id,
                m.seq,
                m.role,
                m.content,
                m.created_at,
                m.build_ms,
                m.input_modality,
                m.is_proactive,
                m.delivery_status,
                m.metadata
            FROM messages m
            WHERE {" AND ".join(filters)}
            ORDER BY {order_clause}
            LIMIT ${len(params)}
        """

        rows = await conn.fetch(query, *params)
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        messages = [dict(row) for row in rows]
        if not forward:
            messages.reverse()

        return messages, has_more

    @staticmethod
    async def get_message_history(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        *,
        session_id: UUID | None = None,
        session_isolated: bool | None = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Load message history for context building.

        Behavior based on session state:
        - No session: Load recent messages from relationship
        - Session (non-isolated): Load recent messages from relationship
        - Session (isolated): Load only messages from this session

        Args:
            conn: Database connection
            relationship_id: The relationship to load messages for
            session_id: Optional active session ID
            session_isolated: Optional pre-fetched isolation status (avoids DB query)
            limit: Maximum number of messages to load

        Returns:
            List of message dicts with role, content, created_at (oldest first)
        """
        # Use pre-fetched isolation status if provided, otherwise query
        is_isolated = False
        if session_id:
            if session_isolated is not None:
                is_isolated = session_isolated
            else:
                row = await conn.fetchrow(
                    "SELECT isolated FROM v2_sessions WHERE id = $1",
                    session_id,
                )
                if row:
                    is_isolated = row["isolated"]

        if is_isolated and session_id:
            # Isolated session: only load messages from THIS session
            rows = await conn.fetch(
                """
                SELECT role, content, created_at, metadata, seq
                FROM messages
                WHERE session_id = $1
                ORDER BY seq DESC NULLS LAST, created_at DESC, id DESC
                LIMIT $2
                """,
                session_id,
                limit,
            )
        else:
            # Non-isolated: load recent relationship messages
            # Exclude messages from OTHER isolated sessions
            rows = await conn.fetch(
                """
                SELECT m.role, m.content, m.created_at, m.metadata, m.seq
                FROM messages m
                LEFT JOIN v2_sessions s ON m.session_id = s.id
                WHERE m.relationship_id = $1
                  AND (s.id IS NULL OR s.isolated = FALSE)
                ORDER BY m.seq DESC NULLS LAST, m.created_at DESC, m.id DESC
                LIMIT $2
                """,
                relationship_id,
                limit,
            )

        # Reverse to get oldest-first order for prompt assembly. V2 messages
        # have an explicit per-relationship seq, so use it ahead of timestamps.
        rows = list(reversed(rows))

        return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Summarization Support
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_message_count(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> int:
        """Get total message count for a relationship.

        This queries the actual messages table to get the precise count,
        which may differ from the cached message_count on the relationship
        during active conversations.
        """
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM messages WHERE relationship_id = $1",
            relationship_id,
        )
        return count or 0

    @staticmethod
    async def update_summarization_state(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        message_count: int,
    ) -> None:
        """Update tracking columns after summarization completes.

        Args:
            conn: Database connection
            relationship_id: The relationship that was summarized
            message_count: The cumulative message count that was summarized
        """
        await conn.execute(
            """
            UPDATE relationships
            SET last_summarized_at = now(),
                last_summarized_message_count = $2
            WHERE id = $1
            """,
            relationship_id,
            message_count,
        )

    @staticmethod
    async def get_summarization_state(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> tuple[int, int] | None:
        """Get summarization tracking state for a relationship.

        Returns (last_summarized_message_count, message_count) or None if not found.
        """
        row = await conn.fetchrow(
            """
            SELECT COALESCE(last_summarized_message_count, 0) as last_summarized,
                   message_count
            FROM relationships
            WHERE id = $1
            """,
            relationship_id,
        )
        if not row:
            return None
        return row["last_summarized"], row["message_count"]

    @staticmethod
    async def get_messages_for_summarization(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        start_seq: int,
        end_seq: int,
    ) -> List[Dict[str, Any]]:
        """Get messages in a sequence range for summarization.

        Args:
            conn: Database connection
            relationship_id: The relationship to get messages for
            start_seq: Starting sequence number (inclusive)
            end_seq: Ending sequence number (inclusive)

        Returns:
            List of message dicts with role, content, seq ordered by seq
        """
        rows = await conn.fetch(
            """
            SELECT role, content, seq
            FROM messages
            WHERE relationship_id = $1
              AND seq >= $2 AND seq <= $3
            ORDER BY seq ASC
            """,
            relationship_id,
            start_seq,
            end_seq,
        )
        return [dict(row) for row in rows]
