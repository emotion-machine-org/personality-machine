from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar, Dict, List, Sequence
from uuid import UUID

import asyncpg

from ..models.project import (
    KnowledgeAsset,
    KnowledgeIngestionJob,
    Project,
    ProjectApiKey,
    ProjectSummary,
)

logger = logging.getLogger(__name__)


def _normalize_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    metadata = record.get("metadata")
    if isinstance(metadata, str):
        try:
            record["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            record["metadata"] = {}
    elif isinstance(metadata, list):
        # Handle case where metadata is returned as a list (e.g., from JSONB array)
        if len(metadata) == 1 and isinstance(metadata[0], str):
            try:
                record["metadata"] = json.loads(metadata[0])
            except json.JSONDecodeError:
                record["metadata"] = {}
        elif len(metadata) == 1 and isinstance(metadata[0], dict):
            record["metadata"] = metadata[0]
        else:
            record["metadata"] = {}
    elif metadata is None:
        record["metadata"] = {}
    return record


def _job_from_row(row: asyncpg.Record) -> KnowledgeIngestionJob:
    return KnowledgeIngestionJob(**_normalize_metadata(dict(row)))


def _default_project_slug(owner_id: UUID, attempt: int) -> str:
    base = f"default-{str(owner_id)[:8]}"
    return base if attempt == 0 else f"{base}-{attempt + 1}"


def _default_project_name(attempt: int) -> str:
    return "Default Project" if attempt == 0 else f"Default Project {attempt + 1}"


class ProjectRepository:
    """Data access helpers for project entities."""

    @staticmethod
    async def get_project_by_id(conn: asyncpg.Connection, project_id: UUID) -> Project | None:
        row = await conn.fetchrow(
            """
            SELECT id, owner_id, name, slug, is_default, metadata, created_at, updated_at
            FROM projects
            WHERE id = $1
            """,
            project_id,
        )
        if not row:
            return None

        return Project(**_normalize_metadata(dict(row)))

    @staticmethod
    async def get_project_for_owner(
        conn: asyncpg.Connection,
        project_id: UUID,
        owner_id: UUID,
    ) -> Project | None:
        row = await conn.fetchrow(
            """
            SELECT id, owner_id, name, slug, is_default, metadata, created_at, updated_at
            FROM projects
            WHERE id = $1 AND owner_id = $2
            """,
            project_id,
            owner_id,
        )
        return Project(**_normalize_metadata(dict(row))) if row else None

    @staticmethod
    async def get_default_project_for_owner(
        conn: asyncpg.Connection, owner_id: UUID
    ) -> Project | None:
        row = await conn.fetchrow(
            """
            SELECT id, owner_id, name, slug, is_default, metadata, created_at, updated_at
            FROM projects
            WHERE owner_id = $1
            ORDER BY is_default DESC, created_at ASC
            LIMIT 1
            """,
            owner_id,
        )
        return Project(**_normalize_metadata(dict(row))) if row else None

    @staticmethod
    async def list_projects_for_owner(
        conn: asyncpg.Connection, owner_id: UUID
    ) -> List[ProjectSummary]:
        rows = await conn.fetch(
            """
            SELECT
                p.id,
                p.owner_id,
                p.name,
                p.slug,
                p.is_default,
                p.metadata,
                p.created_at,
                p.updated_at,
                COUNT(c.id)::INT AS companion_count
            FROM projects p
            LEFT JOIN companions c ON c.project_id = p.id
            WHERE p.owner_id = $1
            GROUP BY p.id
            ORDER BY p.is_default DESC, p.created_at ASC
            """,
            owner_id,
        )
        return [ProjectSummary(**_normalize_metadata(dict(row))) for row in rows]

    @staticmethod
    async def create_project(
        conn: asyncpg.Connection,
        *,
        owner_id: UUID,
        name: str,
        slug: str | None,
        is_default: bool = False,
        metadata: Dict[str, Any] | None = None,
    ) -> Project:
        row = await conn.fetchrow(
            """
            INSERT INTO projects (owner_id, name, slug, is_default, metadata)
            VALUES ($1, $2, $3, $4, COALESCE($5, '{}'::jsonb))
            RETURNING id, owner_id, name, slug, is_default, metadata, created_at, updated_at
            """,
            owner_id,
            name,
            slug,
            is_default,
            json.dumps(metadata) if isinstance(metadata, dict) else metadata,
        )
        return Project(**_normalize_metadata(dict(row)))

    @staticmethod
    async def ensure_default_project(
        conn: asyncpg.Connection,
        owner_id: UUID,
        *,
        seed_source: str = "auth-auto-default",
    ) -> Project:
        existing = await ProjectRepository.get_default_project_for_owner(conn, owner_id)
        if existing:
            return existing

        attempt = 0
        while True:
            slug = _default_project_slug(owner_id, attempt)
            name = _default_project_name(attempt)
            metadata = {
                "seeded_at": datetime.now(UTC).isoformat(),
                "seed_source": seed_source,
            }
            try:
                return await ProjectRepository.create_project(
                    conn,
                    owner_id=owner_id,
                    name=name,
                    slug=slug,
                    is_default=True,
                    metadata=metadata,
                )
            except asyncpg.UniqueViolationError:
                attempt += 1
                if attempt > 20:
                    logger.error(
                        "Failed to create default project for owner %s after %s attempts",
                        owner_id,
                        attempt,
                    )
                    raise


class KnowledgeAssetRepository:
    """Data access helpers for uploaded knowledge assets."""

    @staticmethod
    async def create_asset(
        conn: asyncpg.Connection,
        *,
        asset_id: UUID,
        project_id: UUID,
        companion_id: UUID,
        owner_user_id: UUID | None,
        filename: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        status: str = "uploaded",
        checksum: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> KnowledgeAsset:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge_assets (
                id,
                project_id,
                companion_id,
                owner_user_id,
                filename,
                mime_type,
                size_bytes,
                status,
                storage_path,
                checksum,
                metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, COALESCE($11, '{}'::jsonb))
            RETURNING *
            """,
            asset_id,
            project_id,
            companion_id,
            owner_user_id,
            filename,
            mime_type,
            size_bytes,
            status,
            storage_path,
            checksum,
            metadata,  # Pass dict directly - asyncpg's JSONB codec handles encoding
        )
        return KnowledgeAsset(**_normalize_metadata(dict(row)))

    @staticmethod
    async def get_asset_by_id(conn: asyncpg.Connection, asset_id: UUID) -> KnowledgeAsset | None:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM knowledge_assets
            WHERE id = $1
            """,
            asset_id,
        )
        return KnowledgeAsset(**_normalize_metadata(dict(row))) if row else None

    @staticmethod
    async def list_assets_for_companion(
        conn: asyncpg.Connection,
        companion_id: UUID,
        *,
        limit: int = 50,
    ) -> List[KnowledgeAsset]:
        rows = await conn.fetch(
            """
            SELECT *
            FROM knowledge_assets
            WHERE companion_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            companion_id,
            limit,
        )
        return [KnowledgeAsset(**_normalize_metadata(dict(row))) for row in rows]

    @staticmethod
    async def list_assets_by_filename(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        filename: str,
    ) -> List[KnowledgeAsset]:
        rows = await conn.fetch(
            """
            SELECT *
            FROM knowledge_assets
            WHERE companion_id = $1
              AND LOWER(filename) = LOWER($2)
            ORDER BY created_at DESC
            """,
            companion_id,
            filename,
        )
        return [KnowledgeAsset(**_normalize_metadata(dict(row))) for row in rows]

    @staticmethod
    async def update_status(
        conn: asyncpg.Connection,
        asset_id: UUID,
        *,
        status: str,
        metadata: Dict[str, Any] | None = None,
    ) -> KnowledgeAsset | None:
        # When metadata is provided, merge it with existing; otherwise keep existing
        if metadata is not None:
            row = await conn.fetchrow(
                """
                UPDATE knowledge_assets
                SET status = $2,
                    metadata = metadata || $3,
                    updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                asset_id,
                status,
                metadata,  # Pass dict directly - asyncpg's JSONB codec handles encoding
            )
        else:
            row = await conn.fetchrow(
                """
                UPDATE knowledge_assets
                SET status = $2,
                    updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                asset_id,
                status,
            )
        return KnowledgeAsset(**_normalize_metadata(dict(row))) if row else None

    @staticmethod
    async def delete_asset(
        conn: asyncpg.Connection,
        asset_id: UUID,
        companion_id: UUID,
    ) -> bool:
        """Delete a knowledge asset. Returns True if deleted, False if not found."""
        result = await conn.execute(
            """
            DELETE FROM knowledge_assets
            WHERE id = $1 AND companion_id = $2
            """,
            asset_id,
            companion_id,
        )
        return result == "DELETE 1"


class ProjectApiKeyRepository:
    """Data access helpers for project API keys."""

    @staticmethod
    async def fetch_by_prefix(conn: asyncpg.Connection, prefix: str) -> asyncpg.Record | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM project_api_keys
            WHERE prefix = $1
            """,
            prefix,
        )

    @staticmethod
    async def get_key(
        conn: asyncpg.Connection,
        key_id: UUID,
        project_id: UUID,
    ) -> ProjectApiKey | None:
        row = await conn.fetchrow(
            """
            SELECT id, project_id, created_by, name, prefix, status, scopes,
                   metadata, created_at, last_used_at, expires_at
            FROM project_api_keys
            WHERE id = $1 AND project_id = $2
            """,
            key_id,
            project_id,
        )
        return ProjectApiKey(**_normalize_metadata(dict(row))) if row else None

    @staticmethod
    async def list_keys(conn: asyncpg.Connection, project_id: UUID) -> List[ProjectApiKey]:
        rows = await conn.fetch(
            """
            SELECT id, project_id, created_by, name, prefix, status, scopes,
                   metadata, created_at, last_used_at, expires_at
            FROM project_api_keys
            WHERE project_id = $1
            ORDER BY created_at DESC
            """,
            project_id,
        )
        return [ProjectApiKey(**_normalize_metadata(dict(row))) for row in rows]

    @staticmethod
    async def create_key(
        conn: asyncpg.Connection,
        *,
        project_id: UUID,
        created_by: UUID | None,
        name: str | None,
        prefix: str,
        secret_hash: bytes,
        salt: bytes,
        scopes: Sequence[str],
        metadata: Dict | None = None,
        expires_at: datetime | None = None,
    ) -> ProjectApiKey:
        row = await conn.fetchrow(
            """
            INSERT INTO project_api_keys (
                project_id,
                created_by,
                name,
                prefix,
                secret_hash,
                salt,
                scopes,
                metadata,
                expires_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8, '{}'::jsonb), $9)
            RETURNING id, project_id, created_by, name, prefix, status, scopes,
                      metadata, created_at, last_used_at, expires_at
            """,
            project_id,
            created_by,
            name,
            prefix,
            secret_hash,
            salt,
            list(scopes),
            json.dumps(metadata) if isinstance(metadata, dict) else metadata,
            expires_at,
        )
        return ProjectApiKey(**_normalize_metadata(dict(row)))

    @staticmethod
    async def mark_used(conn: asyncpg.Connection, key_id: UUID) -> None:
        await conn.execute(
            """
            UPDATE project_api_keys
            SET last_used_at = now()
            WHERE id = $1
            """,
            key_id,
        )

    @staticmethod
    async def set_status(conn: asyncpg.Connection, key_id: UUID, status: str) -> bool:
        res = await conn.execute(
            """
            UPDATE project_api_keys
            SET status = $2, last_used_at = CASE WHEN $2 = 'revoked' THEN last_used_at ELSE last_used_at END
            WHERE id = $1
            """,
            key_id,
            status,
        )
        return res == "UPDATE 1"


class KnowledgeIngestionJobRepository:
    """Data access layer for ingestion job lifecycle.

    Uses the unified 'jobs' table for writes and 'knowledge_ingestion_jobs_v' view for reads.
    Migration 0031 consolidated all job tables into a single 'jobs' table.
    """

    # Status mapping between KnowledgeIngestionJob model and unified jobs table
    _STATUS_TO_JOBS: ClassVar[dict[str, str]] = {
        "queued": "pending",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
    }
    _STATUS_FROM_JOBS: ClassVar[dict[str, str]] = {
        "pending": "queued",
        "claimed": "running",
        "running": "running",
        "completed": "succeeded",
        "failed": "failed",
        "cancelled": "failed",
    }

    @staticmethod
    async def create_job(
        conn: asyncpg.Connection,
        *,
        project_id: UUID,
        companion_id: UUID,
        source_type: str,
        payload_ref: str | None,
        submitted_by_user: UUID | None,
        submitted_by_key: UUID | None,
        asset_id: UUID | None = None,
        metadata: Dict | None = None,
    ) -> KnowledgeIngestionJob:
        # Build params JSONB for the unified jobs table
        params = {
            "source_type": source_type,
            "payload_ref": payload_ref,
            "asset_id": str(asset_id) if asset_id else None,
            "metadata": metadata or {},
            "submitted_by_key": str(submitted_by_key) if submitted_by_key else None,
        }

        row = await conn.fetchrow(
            """
            INSERT INTO jobs (
                job_type,
                status,
                project_id,
                companion_id,
                owner_id,
                params
            )
            VALUES ('knowledge_ingestion', 'pending', $1, $2, $3, $4)
            RETURNING id, project_id, companion_id, owner_id, params, status, error,
                      created_at, updated_at, started_at, completed_at
            """,
            project_id,
            companion_id,
            submitted_by_user,
            params,  # Pass dict directly - asyncpg's JSONB codec handles encoding
        )
        return KnowledgeIngestionJobRepository._row_to_model(row)

    @staticmethod
    async def update_status(
        conn: asyncpg.Connection,
        job_id: UUID,
        status: str,
        *,
        error: str | None = None,
        mark_started: bool = False,
        mark_completed: bool = False,
    ) -> KnowledgeIngestionJob | None:
        # Map external status to jobs table status
        jobs_status = KnowledgeIngestionJobRepository._STATUS_TO_JOBS.get(status, status)

        set_started = ", started_at = COALESCE(started_at, now())" if mark_started else ""
        set_completed = ", completed_at = COALESCE(completed_at, now())" if mark_completed else ""
        row = await conn.fetchrow(
            f"""
            UPDATE jobs
            SET status = $2,
                error = $3
                {set_started}
                {set_completed}
            WHERE id = $1 AND job_type = 'knowledge_ingestion'
            RETURNING id, project_id, companion_id, owner_id, params, status, error,
                      created_at, updated_at, started_at, completed_at
            """,
            job_id,
            jobs_status,
            error,
        )
        if not row:
            return None
        return KnowledgeIngestionJobRepository._row_to_model(row)

    @staticmethod
    async def get_job_by_id(conn: asyncpg.Connection, job_id: UUID) -> KnowledgeIngestionJob | None:
        # Use the view for reads (provides backward-compatible column names)
        row = await conn.fetchrow(
            """
            SELECT *
            FROM knowledge_ingestion_jobs_v
            WHERE id = $1
            """,
            job_id,
        )
        if not row:
            return None
        return _job_from_row(row)

    @staticmethod
    async def list_jobs_for_companion(
        conn: asyncpg.Connection,
        companion_id: UUID,
        limit: int = 50,
    ) -> List[KnowledgeIngestionJob]:
        # Use the view for reads
        rows = await conn.fetch(
            """
            SELECT *
            FROM knowledge_ingestion_jobs_v
            WHERE companion_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            companion_id,
            limit,
        )
        return [_job_from_row(row) for row in rows]

    @staticmethod
    def _row_to_model(row: asyncpg.Record) -> KnowledgeIngestionJob:
        """Convert a jobs table row to KnowledgeIngestionJob model."""
        params = row["params"] or {}
        if isinstance(params, str):
            params = json.loads(params)
        # Handle corrupted params that might be a list
        if isinstance(params, list):
            params = params[0] if params and isinstance(params[0], dict) else {}

        # Extract asset_id from params, converting from string if needed
        asset_id_raw = params.get("asset_id")
        asset_id = None
        if asset_id_raw:
            try:
                asset_id = UUID(asset_id_raw) if isinstance(asset_id_raw, str) else asset_id_raw
            except (ValueError, TypeError):
                pass

        # Extract submitted_by_key from params
        submitted_by_key_raw = params.get("submitted_by_key")
        submitted_by_key = None
        if submitted_by_key_raw:
            try:
                submitted_by_key = (
                    UUID(submitted_by_key_raw)
                    if isinstance(submitted_by_key_raw, str)
                    else submitted_by_key_raw
                )
            except (ValueError, TypeError):
                pass

        # Handle metadata that might be a list (from corrupted JSONB)
        metadata = params.get("metadata", {})
        if isinstance(metadata, list):
            metadata = metadata[0] if metadata and isinstance(metadata[0], dict) else {}

        return KnowledgeIngestionJob(
            id=row["id"],
            project_id=row["project_id"],
            companion_id=row["companion_id"],
            submitted_by_user=row["owner_id"],
            submitted_by_key=submitted_by_key,
            source_type=params.get("source_type", ""),
            payload_ref=params.get("payload_ref"),
            asset_id=asset_id,
            status=KnowledgeIngestionJobRepository._STATUS_FROM_JOBS.get(
                row["status"], row["status"]
            ),
            error=row["error"],
            metadata=metadata if isinstance(metadata, dict) else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
        )
