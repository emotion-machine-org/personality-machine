"""
Repository for project secrets management.
Secrets are stored encrypted and never returned in plaintext via the API.
"""

from datetime import datetime
from typing import List
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class ProjectSecret(BaseModel):
    """Secret metadata (no value - never expose encrypted data via API)."""

    id: UUID
    project_id: UUID
    secret_name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class ProjectSecretRepository:
    @staticmethod
    async def list_secrets(conn: asyncpg.Connection, project_id: UUID) -> List[ProjectSecret]:
        """List all secrets for a project (metadata only, no values)."""
        rows = await conn.fetch(
            """
            SELECT id, project_id, secret_name, description, created_at, updated_at
            FROM project_secrets
            WHERE project_id = $1
            ORDER BY created_at DESC
            """,
            project_id,
        )
        return [
            ProjectSecret(
                id=row["id"],
                project_id=row["project_id"],
                secret_name=row["secret_name"],
                description=row["description"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    @staticmethod
    async def get_secret(
        conn: asyncpg.Connection, project_id: UUID, secret_name: str
    ) -> ProjectSecret | None:
        """Get a secret by name (metadata only)."""
        row = await conn.fetchrow(
            """
            SELECT id, project_id, secret_name, description, created_at, updated_at
            FROM project_secrets
            WHERE project_id = $1 AND secret_name = $2
            """,
            project_id,
            secret_name,
        )
        if not row:
            return None
        return ProjectSecret(
            id=row["id"],
            project_id=row["project_id"],
            secret_name=row["secret_name"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    async def create_secret(
        conn: asyncpg.Connection,
        project_id: UUID,
        secret_name: str,
        encrypted_value: bytes,
        description: str | None = None,
    ) -> ProjectSecret:
        """Create a new secret with encrypted value."""
        row = await conn.fetchrow(
            """
            INSERT INTO project_secrets (project_id, secret_name, encrypted_value, description)
            VALUES ($1, $2, $3, $4)
            RETURNING id, project_id, secret_name, description, created_at, updated_at
            """,
            project_id,
            secret_name,
            encrypted_value,
            description,
        )
        return ProjectSecret(
            id=row["id"],
            project_id=row["project_id"],
            secret_name=row["secret_name"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    async def update_secret(
        conn: asyncpg.Connection,
        project_id: UUID,
        secret_name: str,
        encrypted_value: bytes,
        description: str | None = None,
    ) -> ProjectSecret | None:
        """Update an existing secret's value (for rotation)."""
        # Build update query based on whether description is provided
        if description is not None:
            row = await conn.fetchrow(
                """
                UPDATE project_secrets
                SET encrypted_value = $3, description = $4, updated_at = now()
                WHERE project_id = $1 AND secret_name = $2
                RETURNING id, project_id, secret_name, description, created_at, updated_at
                """,
                project_id,
                secret_name,
                encrypted_value,
                description,
            )
        else:
            row = await conn.fetchrow(
                """
                UPDATE project_secrets
                SET encrypted_value = $3, updated_at = now()
                WHERE project_id = $1 AND secret_name = $2
                RETURNING id, project_id, secret_name, description, created_at, updated_at
                """,
                project_id,
                secret_name,
                encrypted_value,
            )
        if not row:
            return None
        return ProjectSecret(
            id=row["id"],
            project_id=row["project_id"],
            secret_name=row["secret_name"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    async def delete_secret(conn: asyncpg.Connection, project_id: UUID, secret_name: str) -> bool:
        """Delete a secret. Returns True if deleted, False if not found."""
        result = await conn.execute(
            """
            DELETE FROM project_secrets
            WHERE project_id = $1 AND secret_name = $2
            """,
            project_id,
            secret_name,
        )
        return result == "DELETE 1"

    @staticmethod
    async def secret_exists(conn: asyncpg.Connection, project_id: UUID, secret_name: str) -> bool:
        """Check if a secret with the given name exists."""
        row = await conn.fetchrow(
            """
            SELECT 1 FROM project_secrets
            WHERE project_id = $1 AND secret_name = $2
            """,
            project_id,
            secret_name,
        )
        return row is not None
