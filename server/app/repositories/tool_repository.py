"""Repository for tool management.

Provides data access for:
- tools table: Tool definitions (name, spec, summary)
- companion_tool_links table: Per-companion tool configuration

All methods are static and require an asyncpg connection to be passed in.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class ToolRepository:
    """Data access helpers for tools and companion_tool_links tables."""

    @staticmethod
    async def search_tools(
        conn: asyncpg.Connection,
        companion_id: UUID,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for tools linked to a companion using full-text search.

        Falls back to returning top tools by priority if no matches found.
        """
        rows = await conn.fetch(
            """
            WITH ranked AS (
                SELECT t.id, t.file_name, t.name, t.summary, t.spec,
                       ts_rank(
                         to_tsvector('english', t.file_name || ' ' || t.name || ' ' || t.summary || ' ' || coalesce(t.spec::text,'')),
                         plainto_tsquery('english', $2)
                       ) AS rank
                FROM companion_tool_links l
                JOIN tools t ON t.id = l.tool_id
                WHERE l.companion_id = $1 AND l.enabled = TRUE
            )
            SELECT * FROM ranked
            WHERE rank IS NOT NULL
            ORDER BY rank DESC, name ASC
            LIMIT $3
            """,
            companion_id,
            query,
            limit,
        )
        if rows:
            return [dict(r) for r in rows]

        # Fallback: return top enabled by priority/name
        rows = await conn.fetch(
            """
            SELECT t.id, t.file_name, t.name, t.summary, t.spec
            FROM companion_tool_links l
            JOIN tools t ON t.id = l.tool_id
            WHERE l.companion_id = $1 AND l.enabled = TRUE
            ORDER BY l.priority DESC, t.name ASC
            LIMIT $2
            """,
            companion_id,
            limit,
        )
        return [dict(r) for r in rows]

    @staticmethod
    async def get_enabled_tools_for_companion(
        conn: asyncpg.Connection,
        companion_id: UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get all enabled tools for a companion, ordered by priority."""
        rows = await conn.fetch(
            """
            SELECT t.id, t.file_name, t.name, t.summary, t.spec, l.priority
            FROM companion_tool_links l
            JOIN tools t ON t.id = l.tool_id
            WHERE l.companion_id = $1 AND l.enabled = TRUE
            ORDER BY l.priority DESC, t.name ASC
            LIMIT $2
            """,
            companion_id,
            limit,
        )
        return [dict(r) for r in rows]
