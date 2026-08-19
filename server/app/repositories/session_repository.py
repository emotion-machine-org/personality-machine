"""Repository for v2 Session operations.

Provides data access for the `v2_sessions` table.
Sessions are optional bounded interactions within relationships.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple
from uuid import UUID

import asyncpg

from ..models.v2.session import Session

logger = logging.getLogger(__name__)


def _normalize_jsonb(val: Any) -> Dict[str, Any]:
    """Ensure JSONB field is a dict, not a string or None."""
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    elif val is None:
        return {}
    elif isinstance(val, dict):
        return val
    return {}


def _row_to_session(row: asyncpg.Record) -> Session:
    """Convert a database row to a Session model."""
    data = dict(row)
    data["state"] = _normalize_jsonb(data.get("state"))
    return Session(**data)


class SessionRepository:
    """Data access for the v2_sessions table."""

    # -------------------------------------------------------------------------
    # Core CRUD
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_by_id(
        conn: asyncpg.Connection,
        session_id: UUID,
    ) -> Session | None:
        """Get a session by its ID."""
        row = await conn.fetchrow(
            """
            SELECT id, relationship_id, type, status, isolated,
                   state, summary, created_at, ended_at, updated_at
            FROM v2_sessions
            WHERE id = $1
            """,
            session_id,
        )
        if not row:
            return None
        return _row_to_session(row)

    @staticmethod
    async def create(
        conn: asyncpg.Connection,
        *,
        relationship_id: UUID,
        type: str | None = None,
        isolated: bool = False,
    ) -> Session:
        """Create a new session.

        Note: The database has a trigger that prevents multiple active sessions
        per relationship. This will raise an exception if one already exists.
        """
        row = await conn.fetchrow(
            """
            INSERT INTO v2_sessions (relationship_id, type, isolated)
            VALUES ($1, $2, $3)
            RETURNING id, relationship_id, type, status, isolated,
                      state, summary, created_at, ended_at, updated_at
            """,
            relationship_id,
            type,
            isolated,
        )
        return _row_to_session(row)

    @staticmethod
    async def get_active_for_relationship(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> Session | None:
        """Get the active session for a relationship (if any)."""
        row = await conn.fetchrow(
            """
            SELECT id, relationship_id, type, status, isolated,
                   state, summary, created_at, ended_at, updated_at
            FROM v2_sessions
            WHERE relationship_id = $1 AND status = 'active'
            """,
            relationship_id,
        )
        if not row:
            return None
        return _row_to_session(row)

    @staticmethod
    async def list_for_relationship(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Tuple[List[Session], str | None, int]:
        """List sessions for a relationship with cursor-based pagination.

        Returns (sessions, next_cursor, total_count).
        Cursor is the session ID to start after.
        """
        # Get total count
        count_row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM v2_sessions WHERE relationship_id = $1",
            relationship_id,
        )
        total = count_row["cnt"] if count_row else 0

        # Build query with optional cursor
        if cursor:
            try:
                cursor_id = UUID(cursor)
                # Get the created_at of the cursor session for pagination
                cursor_row = await conn.fetchrow(
                    "SELECT created_at FROM v2_sessions WHERE id = $1",
                    cursor_id,
                )
                if cursor_row:
                    rows = await conn.fetch(
                        """
                        SELECT id, relationship_id, type, status, isolated,
                               state, summary, created_at, ended_at, updated_at
                        FROM v2_sessions
                        WHERE relationship_id = $1
                          AND (created_at, id) < ($2, $3)
                        ORDER BY created_at DESC, id DESC
                        LIMIT $4
                        """,
                        relationship_id,
                        cursor_row["created_at"],
                        cursor_id,
                        limit,
                    )
                else:
                    rows = []
            except (ValueError, TypeError):
                rows = []
        else:
            rows = await conn.fetch(
                """
                SELECT id, relationship_id, type, status, isolated,
                       state, summary, created_at, ended_at, updated_at
                FROM v2_sessions
                WHERE relationship_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                relationship_id,
                limit,
            )

        sessions = [_row_to_session(row) for row in rows]

        # Determine next cursor
        next_cursor = None
        if sessions and len(sessions) == limit:
            # Check if there are more
            last_session = sessions[-1]
            more_row = await conn.fetchrow(
                """
                SELECT 1 FROM v2_sessions
                WHERE relationship_id = $1
                  AND (created_at, id) < ($2, $3)
                LIMIT 1
                """,
                relationship_id,
                last_session.created_at,
                last_session.id,
            )
            if more_row:
                next_cursor = str(last_session.id)

        return sessions, next_cursor, total

    @staticmethod
    async def end_session(
        conn: asyncpg.Connection,
        session_id: UUID,
        summary: str | None = None,
    ) -> Session | None:
        """End a session, setting status to 'ended' and storing summary."""
        row = await conn.fetchrow(
            """
            UPDATE v2_sessions
            SET status = 'ended',
                ended_at = NOW(),
                summary = $2
            WHERE id = $1 AND status = 'active'
            RETURNING id, relationship_id, type, status, isolated,
                      state, summary, created_at, ended_at, updated_at
            """,
            session_id,
            summary,
        )
        if not row:
            return None
        return _row_to_session(row)

    # -------------------------------------------------------------------------
    # State Operations
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_state(
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
        state = _normalize_jsonb(row["state"])
        return state, row["isolated"]

    @staticmethod
    async def patch_state(
        conn: asyncpg.Connection,
        session_id: UUID,
        changes: Dict[str, Any],
    ) -> Session | None:
        """Merge changes into session state (JSON Merge Patch).

        Only works on active, non-isolated sessions.
        """
        row = await conn.fetchrow(
            """
            UPDATE v2_sessions
            SET state = state || $1
            WHERE id = $2
              AND status = 'active'
              AND isolated = FALSE
            RETURNING id, relationship_id, type, status, isolated,
                      state, summary, created_at, ended_at, updated_at
            """,
            changes,
            session_id,
        )
        if not row:
            return None
        return _row_to_session(row)

    @staticmethod
    async def is_session_active(
        conn: asyncpg.Connection,
        session_id: UUID,
    ) -> bool | None:
        """Check if a session exists and is active.

        Returns True if active, False if ended, None if not found.
        """
        row = await conn.fetchrow(
            "SELECT status FROM v2_sessions WHERE id = $1",
            session_id,
        )
        if not row:
            return None
        return row["status"] == "active"

    @staticmethod
    async def is_session_isolated(
        conn: asyncpg.Connection,
        session_id: UUID,
    ) -> bool | None:
        """Check if a session is isolated.

        Returns True if isolated, False if not, None if not found.
        """
        row = await conn.fetchrow(
            "SELECT isolated FROM v2_sessions WHERE id = $1",
            session_id,
        )
        if not row:
            return None
        return row["isolated"]

    # -------------------------------------------------------------------------
    # Message Queries (for summary generation)
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_session_messages(
        conn: asyncpg.Connection,
        session_id: UUID,
        *,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get messages for a session, ordered by creation time.

        Used for generating session summaries.
        """
        rows = await conn.fetch(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE session_id = $1
            ORDER BY created_at ASC
            LIMIT $2
            """,
            session_id,
            limit,
        )
        return [dict(row) for row in rows]
