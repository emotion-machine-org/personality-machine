"""Repository for Memory V2 scratchpad entries.

Provides data access for the `memory_v2_entries` table.
Each relationship has its own scratchpad of semantic entries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class MemoryV2Repository:
    """Data access layer for memory_v2_entries table."""

    @staticmethod
    async def list_entries(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get all entries for a relationship, newest first (by updated_at)."""
        rows = await conn.fetch(
            """
            SELECT id, relationship_id, content, type, created_at, updated_at
            FROM memory_v2_entries
            WHERE relationship_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            relationship_id,
            limit,
        )
        return [dict(row) for row in rows]

    @staticmethod
    async def get_entry(
        conn: asyncpg.Connection,
        entry_id: UUID,
        relationship_id: UUID,
    ) -> Dict[str, Any] | None:
        """Get a single entry (with relationship check for auth)."""
        row = await conn.fetchrow(
            """
            SELECT id, relationship_id, content, type, created_at, updated_at
            FROM memory_v2_entries
            WHERE id = $1 AND relationship_id = $2
            """,
            entry_id,
            relationship_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def create_entry(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        content: str,
        entry_type: str | None = None,
    ) -> Dict[str, Any]:
        """Create a new entry. Returns the created entry."""
        row = await conn.fetchrow(
            """
            INSERT INTO memory_v2_entries (relationship_id, content, type)
            VALUES ($1, $2, $3)
            RETURNING id, relationship_id, content, type, created_at, updated_at
            """,
            relationship_id,
            content.strip(),
            entry_type,
        )
        return dict(row)

    @staticmethod
    async def update_entry(
        conn: asyncpg.Connection,
        entry_id: UUID,
        relationship_id: UUID,
        content: str,
        entry_type: str | None = None,
    ) -> Dict[str, Any] | None:
        """Update an existing entry. Returns updated entry or None if not found."""
        row = await conn.fetchrow(
            """
            UPDATE memory_v2_entries
            SET content = $3, type = COALESCE($4, type), updated_at = now()
            WHERE id = $1 AND relationship_id = $2
            RETURNING id, relationship_id, content, type, created_at, updated_at
            """,
            entry_id,
            relationship_id,
            content.strip(),
            entry_type,
        )
        return dict(row) if row else None

    @staticmethod
    async def delete_entry(
        conn: asyncpg.Connection,
        entry_id: UUID,
        relationship_id: UUID,
    ) -> bool:
        """Delete an entry. Returns True if deleted, False if not found."""
        result = await conn.execute(
            """
            DELETE FROM memory_v2_entries
            WHERE id = $1 AND relationship_id = $2
            """,
            entry_id,
            relationship_id,
        )
        return result == "DELETE 1"

    @staticmethod
    async def clear_all(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> int:
        """Delete all entries for a relationship. Returns count deleted."""
        result = await conn.execute(
            """
            DELETE FROM memory_v2_entries
            WHERE relationship_id = $1
            """,
            relationship_id,
        )
        # Parse "DELETE N" result
        try:
            return int(result.split()[-1])
        except (IndexError, ValueError):
            return 0

    @staticmethod
    async def get_entry_count(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> int:
        """Get count of entries for limit checking."""
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM memory_v2_entries
            WHERE relationship_id = $1
            """,
            relationship_id,
        )
        return count or 0

    @staticmethod
    async def bulk_apply_operations(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        operations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply multiple ADD/UPDATE/DELETE operations atomically.

        Operations format:
        [
            {"action": "add", "content": "...", "type": "..."},
            {"action": "update", "id": "...", "content": "...", "type": "..."},
            {"action": "delete", "id": "..."},
        ]

        Returns summary: {added: int, updated: int, deleted: int, errors: [...]}
        """
        added = 0
        updated = 0
        deleted = 0
        errors = []

        for op in operations:
            action = op.get("action")
            try:
                if action == "add":
                    await MemoryV2Repository.create_entry(
                        conn,
                        relationship_id,
                        op["content"],
                        op.get("type"),
                    )
                    added += 1
                elif action == "update":
                    entry_id = UUID(op["id"])
                    result = await MemoryV2Repository.update_entry(
                        conn,
                        entry_id,
                        relationship_id,
                        op["content"],
                        op.get("type"),
                    )
                    if result:
                        updated += 1
                    else:
                        errors.append(f"Entry {op['id']} not found for update")
                elif action == "delete":
                    entry_id = UUID(op["id"])
                    result = await MemoryV2Repository.delete_entry(
                        conn,
                        entry_id,
                        relationship_id,
                    )
                    if result:
                        deleted += 1
                    else:
                        errors.append(f"Entry {op['id']} not found for delete")
                else:
                    errors.append(f"Unknown action: {action}")
            except Exception as e:
                errors.append(f"Operation {action} failed: {e!s}")

        return {
            "added": added,
            "updated": updated,
            "deleted": deleted,
            "errors": errors,
        }

    @staticmethod
    async def list_entries_paginated(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        limit: int = 20,
        cursor: str | None = None,
        type_filter: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], int, str | None]:
        """
        Cursor-based pagination for memory entries.

        Args:
            conn: Database connection
            relationship_id: Relationship UUID
            limit: Max entries to return (will fetch limit+1 to check for more)
            cursor: ISO timestamp cursor (entries with updated_at < cursor)
            type_filter: Optional type to filter by

        Returns:
            Tuple of (entries, total_count, next_cursor)
            next_cursor is None if no more entries exist
        """
        # Build query dynamically based on filters
        params: List[Any] = [relationship_id, limit + 1]
        where_clauses = ["relationship_id = $1"]

        if cursor:
            cursor_dt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            params.append(cursor_dt)
            where_clauses.append(f"updated_at < ${len(params)}")

        if type_filter:
            params.append(type_filter)
            where_clauses.append(f"type = ${len(params)}")

        where_sql = " AND ".join(where_clauses)

        query = f"""
        SELECT id, relationship_id, content, type, created_at, updated_at
        FROM memory_v2_entries
        WHERE {where_sql}
        ORDER BY updated_at DESC
        LIMIT $2
        """

        rows = await conn.fetch(query, *params)
        entries = [dict(row) for row in rows[:limit]]

        # Check if more exist
        has_more = len(rows) > limit
        next_cursor = entries[-1]["updated_at"].isoformat() if has_more and entries else None

        # Get total count (separate query for accuracy with filters)
        count_params: List[Any] = [relationship_id]
        count_where = ["relationship_id = $1"]
        if type_filter:
            count_params.append(type_filter)
            count_where.append(f"type = ${len(count_params)}")

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM memory_v2_entries WHERE {' AND '.join(count_where)}",
            *count_params,
        )

        return entries, total or 0, next_cursor

    @staticmethod
    async def search_entries(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        query: str,
        limit: int = 20,
        cursor: str | None = None,
        type_filter: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], int, str | None]:
        """
        Hybrid search using tsvector + trigram.

        Combines PostgreSQL's native FTS with trigram similarity for:
        - Exact/prefix matches via tsvector (ts_rank)
        - Fuzzy matches via pg_trgm (similarity)

        Args:
            conn: Database connection
            relationship_id: Relationship UUID
            query: Search query string
            limit: Max entries to return
            cursor: ISO timestamp cursor for pagination
            type_filter: Optional type to filter by

        Returns:
            Tuple of (entries, total_count, next_cursor)
            Results sorted by combined relevance score
        """
        # Build dynamic WHERE clauses
        params: List[Any] = [relationship_id, query, limit + 1]
        where_clauses = ["relationship_id = $1"]

        # Search condition: match via tsvector OR trigram similarity > 0.1
        search_condition = """(
            search_vector @@ plainto_tsquery('english', $2)
            OR similarity(content, $2) > 0.1
        )"""
        where_clauses.append(search_condition)

        if cursor:
            cursor_dt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            params.append(cursor_dt)
            where_clauses.append(f"updated_at < ${len(params)}")

        if type_filter:
            params.append(type_filter)
            where_clauses.append(f"type = ${len(params)}")

        where_sql = " AND ".join(where_clauses)

        # Combined ranking: FTS weighted 2x higher than trigram
        query_sql = f"""
        SELECT
            id, relationship_id, content, type, created_at, updated_at,
            (ts_rank(search_vector, plainto_tsquery('english', $2)) * 2 +
             similarity(content, $2)) as combined_rank
        FROM memory_v2_entries
        WHERE {where_sql}
        ORDER BY combined_rank DESC, updated_at DESC
        LIMIT $3
        """

        rows = await conn.fetch(query_sql, *params)
        entries = [
            {k: v for k, v in dict(row).items() if k != "combined_rank"} for row in rows[:limit]
        ]

        # Check if more exist
        has_more = len(rows) > limit
        next_cursor = entries[-1]["updated_at"].isoformat() if has_more and entries else None

        # Get total count for search results
        count_params: List[Any] = [relationship_id, query]
        count_where = ["relationship_id = $1", search_condition]
        if type_filter:
            count_params.append(type_filter)
            count_where.append(f"type = ${len(count_params)}")

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM memory_v2_entries WHERE {' AND '.join(count_where)}",
            *count_params,
        )

        return entries, total or 0, next_cursor

    @staticmethod
    async def delete_by_user_id_prefix(
        conn: asyncpg.Connection,
        companion_id: UUID,
        user_id_prefix: str,
    ) -> int:
        """
        Delete all memory entries for relationships matching a user_id prefix.
        Used for cleaning up temp sessions.

        Args:
            conn: Database connection
            companion_id: Companion UUID
            user_id_prefix: User ID prefix to match (e.g., "temp_abc123")

        Returns:
            Number of entries deleted
        """
        result = await conn.execute(
            """
            DELETE FROM memory_v2_entries
            WHERE relationship_id IN (
                SELECT id FROM relationships
                WHERE companion_id = $1 AND user_id LIKE $2 || '%'
            )
            """,
            companion_id,
            user_id_prefix,
        )
        try:
            return int(result.split()[-1])
        except (IndexError, ValueError):
            return 0
