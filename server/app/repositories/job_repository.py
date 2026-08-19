"""Repository for unified job queue operations.

This module provides atomic queue operations using PostgreSQL's SKIP LOCKED
for efficient, distributed job processing without external dependencies.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

import asyncpg

from ..models.job import Job

logger = logging.getLogger(__name__)


def _job_from_row(row: asyncpg.Record) -> Job:
    """Convert a database row to a Job model."""
    data = dict(row)
    # Handle JSONB fields that might come as strings
    for field in ("params", "result"):
        if field in data and isinstance(data[field], str):
            try:
                data[field] = json.loads(data[field])
            except json.JSONDecodeError:
                data[field] = {}
    return Job(**data)


class JobRepository:
    """Data access helpers for the unified jobs table.

    All methods are static and require an asyncpg connection to be passed in,
    following the repository pattern used elsewhere in the codebase.
    """

    @staticmethod
    async def claim_jobs(
        conn: asyncpg.Connection,
        *,
        job_types: List[str],
        worker_id: str,
        limit: int = 1,
    ) -> List[Job]:
        """Atomically claim pending jobs using SKIP LOCKED.

        This is the primary method for workers to grab jobs. It:
        1. Finds pending jobs that are due (run_at <= now or NULL)
        2. Atomically marks them as 'claimed'
        3. Returns the claimed jobs to the worker

        Args:
            conn: Database connection
            job_types: List of job types to claim (e.g., ['action_execution', 'webhook'])
            worker_id: Identifier for this worker (for debugging/monitoring)
            limit: Maximum number of jobs to claim at once

        Returns:
            List of claimed Job objects
        """
        rows = await conn.fetch(
            """
            UPDATE jobs
            SET status = 'claimed',
                claimed_at = now(),
                worker_id = $1,
                attempts = attempts + 1
            WHERE id IN (
                SELECT id FROM jobs
                WHERE status = 'pending'
                  AND job_type = ANY($2)
                  AND (run_at IS NULL OR run_at <= now())
                ORDER BY run_at NULLS FIRST, priority DESC, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT $3
            )
            RETURNING *
            """,
            worker_id,
            job_types,
            limit,
        )
        return [_job_from_row(row) for row in rows]

    @staticmethod
    async def start_job(
        conn: asyncpg.Connection,
        job_id: UUID,
    ) -> Job | None:
        """Transition a claimed job to running state.

        Call this when actual execution begins (after any setup).

        Args:
            conn: Database connection
            job_id: ID of the job to start

        Returns:
            Updated Job if successful, None if job wasn't in 'claimed' state
        """
        row = await conn.fetchrow(
            """
            UPDATE jobs
            SET status = 'running', started_at = now()
            WHERE id = $1 AND status = 'claimed'
            RETURNING *
            """,
            job_id,
        )
        return _job_from_row(row) if row else None

    @staticmethod
    async def complete_job(
        conn: asyncpg.Connection,
        job_id: UUID,
        result: Dict[str, Any] | None = None,
    ) -> Job:
        """Mark a job as successfully completed.

        Args:
            conn: Database connection
            job_id: ID of the job to complete
            result: Optional result data to store

        Returns:
            Updated Job object
        """
        row = await conn.fetchrow(
            """
            UPDATE jobs
            SET status = 'completed',
                completed_at = now(),
                result = $2
            WHERE id = $1
            RETURNING *
            """,
            job_id,
            json.dumps(result) if result else None,
        )
        return _job_from_row(row)

    @staticmethod
    async def fail_job(
        conn: asyncpg.Connection,
        job_id: UUID,
        error: str,
        error_stack: str | None = None,
    ) -> Job:
        """Mark a job as failed with error details.

        Args:
            conn: Database connection
            job_id: ID of the job that failed
            error: Error message
            error_stack: Optional stack trace for debugging

        Returns:
            Updated Job object
        """
        row = await conn.fetchrow(
            """
            UPDATE jobs
            SET status = 'failed',
                completed_at = now(),
                error = $2,
                error_stack = $3
            WHERE id = $1
            RETURNING *
            """,
            job_id,
            error,
            error_stack,
        )
        return _job_from_row(row)

    @staticmethod
    async def requeue_job(
        conn: asyncpg.Connection,
        job_id: UUID,
        delay_seconds: int = 60,
    ) -> Job | None:
        """Requeue a failed/stuck job with exponential backoff.

        Only requeues if the job hasn't exceeded max_attempts.

        Args:
            conn: Database connection
            job_id: ID of the job to requeue
            delay_seconds: Seconds to wait before the job becomes eligible again

        Returns:
            Updated Job if requeued, None if max_attempts exceeded
        """
        row = await conn.fetchrow(
            """
            UPDATE jobs
            SET status = 'pending',
                run_at = now() + ($2 || ' seconds')::interval,
                claimed_at = NULL,
                started_at = NULL,
                worker_id = NULL
            WHERE id = $1
              AND status IN ('failed', 'running', 'claimed')
              AND attempts < max_attempts
            RETURNING *
            """,
            job_id,
            str(delay_seconds),
        )
        return _job_from_row(row) if row else None

    @staticmethod
    async def cancel_job(
        conn: asyncpg.Connection,
        job_id: UUID,
    ) -> Job | None:
        """Cancel a pending or claimed job.

        Only pending/claimed jobs can be cancelled. Running jobs should be
        allowed to complete or fail.

        Args:
            conn: Database connection
            job_id: ID of the job to cancel

        Returns:
            Updated Job if cancelled, None if job was already running/completed
        """
        row = await conn.fetchrow(
            """
            UPDATE jobs
            SET status = 'cancelled', completed_at = now()
            WHERE id = $1 AND status IN ('pending', 'claimed')
            RETURNING *
            """,
            job_id,
        )
        return _job_from_row(row) if row else None

    @staticmethod
    async def enqueue(
        conn: asyncpg.Connection,
        *,
        job_type: str,
        params: Dict[str, Any] | None = None,
        project_id: UUID | None = None,
        companion_id: UUID | None = None,
        conversation_id: UUID | None = None,
        owner_id: UUID | None = None,
        external_user_id: str | None = None,
        behavior_key: str | None = None,
        run_at: datetime | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        total_items: int | None = None,
    ) -> Job:
        """Enqueue a new job for processing.

        Args:
            conn: Database connection
            job_type: Type of job (e.g., 'behavior_execution', 'webhook_delivery')
            params: Job-specific input parameters
            project_id: Optional project scope
            companion_id: Optional companion scope
            conversation_id: Optional conversation scope
            owner_id: Optional owner (user) scope
            external_user_id: Optional end-user scope
            behavior_key: For behavior_execution jobs, the behavior identifier
            run_at: When to run (None = immediately)
            priority: Higher = processed first (default 0)
            max_attempts: Maximum retry attempts (default 3)
            total_items: For progress tracking

        Returns:
            Created Job object
        """
        row = await conn.fetchrow(
            """
            INSERT INTO jobs (
                job_type, params,
                project_id, companion_id, conversation_id, owner_id, external_user_id,
                behavior_key, run_at, priority, max_attempts, total_items
            ) VALUES (
                $1, $2,
                $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12
            )
            RETURNING *
            """,
            job_type,
            json.dumps(params or {}),
            project_id,
            companion_id,
            conversation_id,
            owner_id,
            external_user_id,
            behavior_key,
            run_at,
            priority,
            max_attempts,
            total_items,
        )
        return _job_from_row(row)

    @staticmethod
    async def create_completed_job(
        conn: asyncpg.Connection,
        *,
        job_type: str,
        companion_id: UUID,
        status: str,
        params: Dict[str, Any] | None = None,
        conversation_id: UUID | None = None,
        external_user_id: str | None = None,
        behavior_key: str | None = None,
        result: Dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job:
        """Create a job record that's already completed (for audit trail).

        Used for priority behaviors that execute inline - we create the job
        record after execution to avoid blocking the critical path.

        Args:
            conn: Database connection
            job_type: Type of job
            companion_id: Companion scope
            status: Final status ('completed', 'failed', 'timeout')
            params: Job parameters
            conversation_id: Optional conversation scope
            external_user_id: Optional end-user scope
            behavior_key: For behavior_execution jobs
            result: Result data (for completed jobs)
            error: Error message (for failed jobs)

        Returns:
            Created Job object
        """
        row = await conn.fetchrow(
            """
            INSERT INTO jobs (
                job_type, params,
                companion_id, conversation_id, external_user_id,
                behavior_key, status, result, error,
                started_at, completed_at
            ) VALUES (
                $1, $2,
                $3, $4, $5,
                $6, $7, $8, $9,
                now(), now()
            )
            RETURNING *
            """,
            job_type,
            json.dumps(params or {}),
            companion_id,
            conversation_id,
            external_user_id,
            behavior_key,
            status,
            json.dumps(result) if result else None,
            error,
        )
        return _job_from_row(row)

    @staticmethod
    async def get_job_by_id(
        conn: asyncpg.Connection,
        job_id: UUID,
    ) -> Job | None:
        """Fetch a job by its ID.

        Args:
            conn: Database connection
            job_id: ID of the job to fetch

        Returns:
            Job if found, None otherwise
        """
        row = await conn.fetchrow(
            """
            SELECT * FROM jobs WHERE id = $1
            """,
            job_id,
        )
        return _job_from_row(row) if row else None

    @staticmethod
    async def list_jobs(
        conn: asyncpg.Connection,
        *,
        job_type: str | None = None,
        status: str | None = None,
        companion_id: UUID | None = None,
        conversation_id: UUID | None = None,
        external_user_id: str | None = None,
        behavior_key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Job]:
        """List jobs with optional filters.

        Args:
            conn: Database connection
            job_type: Filter by job type
            status: Filter by status
            companion_id: Filter by companion
            conversation_id: Filter by conversation
            external_user_id: Filter by end-user
            behavior_key: Filter by behavior key
            limit: Max results
            offset: Pagination offset

        Returns:
            List of matching Job objects
        """
        filters = []
        params: List[Any] = []
        idx = 1

        if job_type:
            filters.append(f"job_type = ${idx}")
            params.append(job_type)
            idx += 1
        if status:
            filters.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if companion_id:
            filters.append(f"companion_id = ${idx}")
            params.append(companion_id)
            idx += 1
        if conversation_id:
            filters.append(f"conversation_id = ${idx}")
            params.append(conversation_id)
            idx += 1
        if external_user_id:
            filters.append(f"external_user_id = ${idx}")
            params.append(external_user_id)
            idx += 1
        if behavior_key:
            filters.append(f"behavior_key = ${idx}")
            params.append(behavior_key)
            idx += 1

        where_clause = " AND ".join(filters) if filters else "TRUE"

        rows = await conn.fetch(
            f"""
            SELECT * FROM jobs
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            limit,
            offset,
        )
        return [_job_from_row(row) for row in rows]

    @staticmethod
    async def reclaim_stuck_jobs(
        conn: asyncpg.Connection,
        timeout_minutes: int = 10,
    ) -> List[Job]:
        """Recover jobs stuck in claimed/running state.

        Jobs can get stuck if a worker crashes. This method finds jobs that
        have been claimed/running for longer than the timeout and resets them
        to pending (if under max_attempts).

        Should be called periodically by a scheduler/cron.

        Args:
            conn: Database connection
            timeout_minutes: How long before a job is considered stuck

        Returns:
            List of recovered Job objects
        """
        rows = await conn.fetch(
            """
            UPDATE jobs
            SET status = 'pending',
                claimed_at = NULL,
                started_at = NULL,
                worker_id = NULL
            WHERE status IN ('claimed', 'running')
              AND claimed_at < now() - ($1 || ' minutes')::interval
              AND attempts < max_attempts
            RETURNING *
            """,
            str(timeout_minutes),
        )
        return [_job_from_row(row) for row in rows]

    @staticmethod
    async def update_progress(
        conn: asyncpg.Connection,
        job_id: UUID,
        processed_count: int,
        total_items: int | None = None,
    ) -> Job | None:
        """Update job progress counters.

        Args:
            conn: Database connection
            job_id: ID of the job
            processed_count: Number of items processed
            total_items: Optional update to total items

        Returns:
            Updated Job if found
        """
        if total_items is not None:
            row = await conn.fetchrow(
                """
                UPDATE jobs
                SET processed_count = $2, total_items = $3
                WHERE id = $1
                RETURNING *
                """,
                job_id,
                processed_count,
                total_items,
            )
        else:
            row = await conn.fetchrow(
                """
                UPDATE jobs
                SET processed_count = $2
                WHERE id = $1
                RETURNING *
                """,
                job_id,
                processed_count,
            )
        return _job_from_row(row) if row else None

    @staticmethod
    async def find_active_job(
        conn: asyncpg.Connection,
        *,
        job_type: str,
        companion_id: UUID | None = None,
        conversation_id: UUID | None = None,
        behavior_key: str | None = None,
    ) -> Job | None:
        """Find an active (pending/claimed/running) job matching criteria.

        Useful for checking if a job is already in progress before enqueueing
        a duplicate.

        Args:
            conn: Database connection
            job_type: Type of job to find
            companion_id: Optional companion filter
            conversation_id: Optional conversation filter
            behavior_key: Optional behavior key filter

        Returns:
            Active Job if found, None otherwise
        """
        filters = ["job_type = $1", "status IN ('pending', 'claimed', 'running')"]
        params: List[Any] = [job_type]
        idx = 2

        if companion_id:
            filters.append(f"companion_id = ${idx}")
            params.append(companion_id)
            idx += 1
        if conversation_id:
            filters.append(f"conversation_id = ${idx}")
            params.append(conversation_id)
            idx += 1
        if behavior_key:
            filters.append(f"behavior_key = ${idx}")
            params.append(behavior_key)
            idx += 1

        row = await conn.fetchrow(
            f"""
            SELECT * FROM jobs
            WHERE {" AND ".join(filters)}
            ORDER BY created_at DESC
            LIMIT 1
            """,
            *params,
        )
        return _job_from_row(row) if row else None
