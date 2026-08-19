from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class ToolIndexRepository:
    """Repository for tool spec storage and indexed operations."""

    @staticmethod
    async def get_companion_project(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        user_id: UUID,
    ) -> UUID | None:
        """Get the project_id for a companion, verifying ownership.

        Returns None if companion not found or not owned by user.
        """
        row = await conn.fetchrow(
            """
            SELECT project_id
            FROM companions
            WHERE id = $1 AND owner_id = $2
            """,
            companion_id,
            user_id,
        )
        return row["project_id"] if row else None

    @staticmethod
    async def verify_spec_access(
        conn: asyncpg.Connection,
        *,
        spec_id: UUID,
        companion_id: UUID,
        user_id: UUID,
    ) -> Dict[str, Any] | None:
        """Verify user has access to a tool spec via companion ownership.

        Returns spec info dict if accessible, None otherwise.
        """
        row = await conn.fetchrow(
            """
            SELECT ts.id, ts.project_id, ts.companion_id
            FROM tool_specs ts
            JOIN companions c ON ts.companion_id = c.id
            WHERE ts.id = $1 AND ts.companion_id = $2 AND c.owner_id = $3
            """,
            spec_id,
            companion_id,
            user_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def create_spec(
        conn: asyncpg.Connection,
        *,
        project_id: UUID,
        companion_id: UUID | None,
        spec_name: str | None,
        json_content: Dict[str, Any],
        secrets_config: Dict[str, str] | None = None,
        base_url: str | None = None,
    ) -> UUID:
        row = await conn.fetchrow(
            """
            INSERT INTO tool_specs (project_id, companion_id, spec_name, json_content, secrets_config, base_url)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            project_id,
            companion_id,
            spec_name,
            json_content,
            secrets_config,
            base_url,
        )
        return row["id"]

    @staticmethod
    async def delete_operations_for_spec(conn: asyncpg.Connection, *, spec_id: UUID) -> None:
        await conn.execute("DELETE FROM tool_operations WHERE spec_id = $1", spec_id)

    @staticmethod
    async def upsert_operations(
        conn: asyncpg.Connection,
        *,
        project_id: UUID,
        spec_id: UUID,
        operations: Iterable[Dict[str, Any]],
    ) -> int:
        """Insert a batch of tool operations for a spec, replacing prior rows."""
        await ToolIndexRepository.delete_operations_for_spec(conn, spec_id=spec_id)
        inserted = 0
        for op in operations:
            await conn.execute(
                """
                INSERT INTO tool_operations (
                    project_id,
                    spec_id,
                    name,
                    description,
                    path,
                    method,
                    input_parameters,
                    output_schema,
                    embedding,
                    embedding_model,
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    CASE WHEN $9 IS NULL THEN NULL ELSE $9::vector END,
                    $10, $11
                )
                """,
                project_id,
                spec_id,
                op.get("name"),
                op.get("description"),
                op.get("path"),
                op.get("method"),
                op.get("input_parameters"),
                op.get("output_schema"),
                op.get("embedding"),
                op.get("embedding_model"),
            )
            inserted += 1
        return inserted

    @staticmethod
    async def get_latest_spec_for_companion(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
    ) -> Dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT id, project_id, spec_name, json_content, base_url, updated_at
            FROM tool_specs
            WHERE companion_id = $1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            companion_id,
        )
        if not row:
            return None
        return dict(row)

    @staticmethod
    async def get_operation_by_name(
        conn: asyncpg.Connection,
        *,
        project_id: UUID,
        spec_id: UUID,
        name: str,
    ) -> Dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT id, name, method, path, description, input_parameters, output_schema
            FROM tool_operations
            WHERE project_id = $1 AND spec_id = $2 AND name = $3
            """,
            project_id,
            spec_id,
            name,
        )
        return dict(row) if row else None

    @staticmethod
    async def list_specs_for_companion(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
    ) -> List[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT id, project_id, companion_id, spec_name, secrets_config, base_url, created_at, updated_at
            FROM tool_specs
            WHERE companion_id = $1
            ORDER BY updated_at DESC
            """,
            companion_id,
        )
        return [dict(r) for r in rows]

    @staticmethod
    async def get_spec_for_companion(
        conn: asyncpg.Connection,
        *,
        spec_id: UUID,
        companion_id: UUID,
    ) -> Dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT id, project_id, companion_id, spec_name, base_url, created_at, updated_at
            FROM tool_specs
            WHERE id = $1 AND companion_id = $2
            """,
            spec_id,
            companion_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def delete_spec(
        conn: asyncpg.Connection,
        *,
        spec_id: UUID,
        companion_id: UUID,
    ) -> bool:
        res = await conn.execute(
            "DELETE FROM tool_specs WHERE id = $1 AND companion_id = $2",
            spec_id,
            companion_id,
        )
        return res.startswith("DELETE") and not res.endswith("0")

    @staticmethod
    async def update_secrets_config(
        conn: asyncpg.Connection,
        *,
        spec_id: UUID,
        secrets_config: Dict[str, str] | None,
    ) -> None:
        """Update secrets_config for a tool spec."""
        await conn.execute(
            "UPDATE tool_specs SET secrets_config = $1, updated_at = now() WHERE id = $2",
            secrets_config,
            spec_id,
        )

    @staticmethod
    async def update_base_url(
        conn: asyncpg.Connection,
        *,
        spec_id: UUID,
        base_url: str | None,
    ) -> None:
        """Update base_url for a tool spec."""
        await conn.execute(
            "UPDATE tool_specs SET base_url = $1, updated_at = now() WHERE id = $2",
            base_url,
            spec_id,
        )

    @staticmethod
    async def remove_secret_from_specs(
        conn: asyncpg.Connection,
        *,
        project_id: UUID,
        secret_name: str,
    ) -> int:
        """Remove a secret reference from all tool specs in a project.

        Returns the number of specs that were updated.
        """
        # Find specs where secrets_config contains this secret name as a value
        specs_with_secret = await conn.fetch(
            """
            SELECT id, secrets_config
            FROM tool_specs
            WHERE project_id = $1
              AND secrets_config IS NOT NULL
              AND secrets_config::text LIKE $2
            """,
            project_id,
            f'%"{secret_name}"%',
        )

        updated = 0
        for spec in specs_with_secret:
            secrets_cfg = spec["secrets_config"]
            if isinstance(secrets_cfg, dict):
                # Remove entries where value matches the secret name
                new_cfg = {k: v for k, v in secrets_cfg.items() if v != secret_name}
                await conn.execute(
                    "UPDATE tool_specs SET secrets_config = $1, updated_at = now() WHERE id = $2",
                    new_cfg if new_cfg else None,
                    spec["id"],
                )
                updated += 1

        return updated

    @staticmethod
    async def load_tool_summaries(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
    ) -> List[Dict[str, str]]:
        """Load classifier summaries for all tool specs linked to companion."""
        rows = await conn.fetch(
            """
            SELECT spec_name, classifier_summary
            FROM tool_specs
            WHERE companion_id = $1 AND classifier_summary IS NOT NULL
            ORDER BY updated_at DESC
            """,
            companion_id,
        )
        return [{"spec_name": r["spec_name"], "summary": r["classifier_summary"]} for r in rows]
