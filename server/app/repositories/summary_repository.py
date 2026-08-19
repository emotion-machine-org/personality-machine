"""Repository for relationship_summaries table operations.

Provides data access for incremental conversation summaries stored
per relationship. Each summary version builds on the previous,
capturing conversation history at message_limit thresholds.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class SummaryRepository:
    """Data access layer for relationship_summaries table."""

    @staticmethod
    async def get_latest_summary(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> Dict[str, Any] | None:
        """Get the most recent summary for a relationship.

        Returns dict with id, relationship_id, content, version,
        messages_start, messages_end, message_count, model, created_at
        or None if no summary exists.
        """
        row = await conn.fetchrow(
            """
            SELECT id, relationship_id, content, version,
                   messages_start, messages_end, message_count,
                   model, created_at
            FROM relationship_summaries
            WHERE relationship_id = $1
            ORDER BY version DESC
            LIMIT 1
            """,
            relationship_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def create_summary(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        content: str,
        version: int,
        messages_start: int,
        messages_end: int,
        message_count: int,
        model: str | None = None,
    ) -> Dict[str, Any]:
        """Create a new summary version.

        Args:
            conn: Database connection
            relationship_id: The relationship this summary belongs to
            content: The generated summary text
            version: Version number (1, 2, 3...)
            messages_start: First message seq included in this summary's new content
            messages_end: Last message seq included
            message_count: Total messages summarized cumulatively
            model: LLM model used for generation

        Returns:
            Dict with all summary fields including id and created_at
        """
        row = await conn.fetchrow(
            """
            INSERT INTO relationship_summaries
                (relationship_id, content, version, messages_start,
                 messages_end, message_count, model)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, relationship_id, content, version,
                      messages_start, messages_end, message_count,
                      model, created_at
            """,
            relationship_id,
            content,
            version,
            messages_start,
            messages_end,
            message_count,
            model,
        )
        return dict(row)

    @staticmethod
    async def list_summaries(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """List all summaries for a relationship (newest first).

        Args:
            conn: Database connection
            relationship_id: The relationship to list summaries for
            limit: Maximum number of summaries to return

        Returns:
            List of summary dicts ordered by version descending
        """
        rows = await conn.fetch(
            """
            SELECT id, relationship_id, content, version,
                   messages_start, messages_end, message_count,
                   model, created_at
            FROM relationship_summaries
            WHERE relationship_id = $1
            ORDER BY version DESC
            LIMIT $2
            """,
            relationship_id,
            limit,
        )
        return [dict(row) for row in rows]

    @staticmethod
    async def get_summary_by_version(
        conn: asyncpg.Connection,
        relationship_id: UUID,
        version: int,
    ) -> Dict[str, Any] | None:
        """Get a specific summary version.

        Args:
            conn: Database connection
            relationship_id: The relationship to get summary for
            version: The version number to retrieve

        Returns:
            Summary dict or None if not found
        """
        row = await conn.fetchrow(
            """
            SELECT id, relationship_id, content, version,
                   messages_start, messages_end, message_count,
                   model, created_at
            FROM relationship_summaries
            WHERE relationship_id = $1 AND version = $2
            """,
            relationship_id,
            version,
        )
        return dict(row) if row else None

    @staticmethod
    async def delete_summaries(
        conn: asyncpg.Connection,
        relationship_id: UUID,
    ) -> int:
        """Delete all summaries for a relationship.

        Returns the number of summaries deleted.
        """
        result = await conn.execute(
            "DELETE FROM relationship_summaries WHERE relationship_id = $1",
            relationship_id,
        )
        # result is like "DELETE 5"
        return int(result.split()[-1]) if result else 0
