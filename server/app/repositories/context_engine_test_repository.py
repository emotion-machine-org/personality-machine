"""Repository for context engine test management.

Provides data access for:
- context_engine_tests table: Saved test configurations for the context engine testing page

All methods are static and require an asyncpg connection to be passed in.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class ContextEngineTestRepository:
    """Data access helpers for context_engine_tests table."""

    @staticmethod
    async def list_tests_for_companion(
        conn: asyncpg.Connection,
        companion_id: UUID,
    ) -> List[Dict[str, Any]]:
        """Get all saved tests for a companion, ordered by creation time."""
        rows = await conn.fetch(
            """
            SELECT id, companion_id, name, config, created_at, updated_at
            FROM context_engine_tests
            WHERE companion_id = $1
            ORDER BY created_at DESC
            """,
            companion_id,
        )
        return [dict(r) for r in rows]

    @staticmethod
    async def get_test_by_id(
        conn: asyncpg.Connection,
        test_id: UUID,
    ) -> Dict[str, Any] | None:
        """Get a single test by ID."""
        row = await conn.fetchrow(
            """
            SELECT id, companion_id, name, config, created_at, updated_at
            FROM context_engine_tests
            WHERE id = $1
            """,
            test_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def get_unique_name(
        conn: asyncpg.Connection,
        companion_id: UUID,
        desired_name: str,
    ) -> str:
        """Generate a unique name for a test, auto-appending number if needed.

        If 'My Test' exists, returns 'My Test (2)', etc.
        """
        # Check if the desired name exists
        existing = await conn.fetchval(
            """
            SELECT COUNT(*) FROM context_engine_tests
            WHERE companion_id = $1 AND name = $2
            """,
            companion_id,
            desired_name,
        )

        if existing == 0:
            return desired_name

        # Find all names matching the pattern "desired_name" or "desired_name (N)"
        pattern = re.escape(desired_name)
        rows = await conn.fetch(
            """
            SELECT name FROM context_engine_tests
            WHERE companion_id = $1
              AND (name = $2 OR name ~ $3)
            """,
            companion_id,
            desired_name,
            f"^{pattern} \\(\\d+\\)$",
        )

        existing_names = {r["name"] for r in rows}

        # Find the next available number
        counter = 2
        while True:
            candidate = f"{desired_name} ({counter})"
            if candidate not in existing_names:
                return candidate
            counter += 1

    @staticmethod
    async def create_test(
        conn: asyncpg.Connection,
        companion_id: UUID,
        name: str,
        config: Dict[str, Any],
        auto_rename: bool = True,
    ) -> Dict[str, Any]:
        """Create a new test configuration.

        Args:
            conn: Database connection
            companion_id: The companion this test belongs to
            name: Test name (will be made unique if auto_rename=True)
            config: Test configuration as JSON-serializable dict
            auto_rename: If True, auto-append number for duplicate names

        Returns:
            The created test record
        """
        if auto_rename:
            name = await ContextEngineTestRepository.get_unique_name(conn, companion_id, name)

        row = await conn.fetchrow(
            """
            INSERT INTO context_engine_tests (companion_id, name, config)
            VALUES ($1, $2, $3)
            RETURNING id, companion_id, name, config, created_at, updated_at
            """,
            companion_id,
            name,
            config,
        )
        return dict(row)

    @staticmethod
    async def update_test(
        conn: asyncpg.Connection,
        test_id: UUID,
        name: str | None = None,
        config: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        """Update an existing test configuration.

        Args:
            conn: Database connection
            test_id: The test to update
            name: New name (optional)
            config: New config (optional)

        Returns:
            The updated test record, or None if not found
        """
        # Build dynamic update
        updates = []
        params = [test_id]
        param_idx = 2

        if name is not None:
            updates.append(f"name = ${param_idx}")
            params.append(name)
            param_idx += 1

        if config is not None:
            updates.append(f"config = ${param_idx}")
            params.append(config)
            param_idx += 1

        if not updates:
            # Nothing to update, just return current
            return await ContextEngineTestRepository.get_test_by_id(conn, test_id)

        query = f"""
            UPDATE context_engine_tests
            SET {", ".join(updates)}
            WHERE id = $1
            RETURNING id, companion_id, name, config, created_at, updated_at
        """

        row = await conn.fetchrow(query, *params)
        return dict(row) if row else None

    @staticmethod
    async def delete_test(
        conn: asyncpg.Connection,
        test_id: UUID,
    ) -> bool:
        """Delete a test configuration.

        Returns:
            True if deleted, False if not found
        """
        result = await conn.execute(
            """
            DELETE FROM context_engine_tests
            WHERE id = $1
            """,
            test_id,
        )
        return result == "DELETE 1"

    @staticmethod
    async def get_companion_id_for_test(
        conn: asyncpg.Connection,
        test_id: UUID,
    ) -> UUID | None:
        """Get the companion_id for a test (for authorization checks)."""
        return await conn.fetchval(
            """
            SELECT companion_id FROM context_engine_tests
            WHERE id = $1
            """,
            test_id,
        )
