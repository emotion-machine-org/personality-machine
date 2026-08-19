import logging
import os
from typing import Any, Dict
from uuid import UUID

from ..db import get_db_connection

logger = logging.getLogger(__name__)


async def dispatch_label_conversations_job(
    *,
    job_id: UUID,
    companion_id: UUID,
    model: str,
    provider: str,
    labels_version: int,
    skip_existing: bool,
    since: str | None,
) -> None:
    """Dispatch the labeling job to Modal (strict, explicit status updates).

    Sets job to RUNNING immediately. If dispatch fails, marks FAILED and re-raises.
    """
    # Mark RUNNING
    async with get_db_connection() as conn:
        await conn.execute(
            "UPDATE background_jobs SET status='RUNNING', started_at=NOW() WHERE id = $1",
            job_id,
        )

    try:
        # Import inside try to catch missing dependency and fail the job cleanly
        import modal  # type: ignore

        app_name = os.getenv("MODAL_APP_NAME", "em-analytics")
        fn_name = os.getenv("MODAL_LABEL_FN", "label_conversations")
        fn = modal.Function.from_name(app_name, fn_name)

        payload: Dict[str, Any] = {
            "job_id": str(job_id),
            "companion_id": str(companion_id),
            "model": model,
            "provider": provider,
            "labels_version": labels_version,
            "skip_existing": skip_existing,
            "since": since,
        }
        if hasattr(fn, "spawn"):
            fn.spawn(payload)
            logger.info(f"Modal job spawned for labeling: job={job_id}")
        else:
            fn.call(payload)
            logger.info(f"Modal job invoked synchronously for labeling: job={job_id}")
    except Exception as e:
        # Mark FAILED and propagate error
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE background_jobs SET status='FAILED', completed_at=NOW(), error=$2 WHERE id = $1",
                job_id,
                str(e),
            )
        raise


async def dispatch_privacy_redaction_job(
    *,
    job_id: UUID,
    conversation_id: UUID,
    model: str | None,
    provider: str | None,
) -> None:
    """Dispatch the privacy redaction job to Modal and mark job RUNNING."""
    async with get_db_connection() as conn:
        await conn.execute(
            "UPDATE background_jobs SET status='RUNNING', started_at=NOW() WHERE id = $1",
            job_id,
        )

    try:
        import modal  # type: ignore

        app_name = os.getenv("MODAL_PRIVACY_APP_NAME", os.getenv("MODAL_APP_NAME", "em-privacy"))
        fn_name = os.getenv("MODAL_PRIVACY_FN", "redact_conversation")
        fn = modal.Function.from_name(app_name, fn_name)
        payload: Dict[str, Any] = {
            "job_id": str(job_id),
            "conversation_id": str(conversation_id),
        }
        if model:
            payload["model"] = model
        if provider:
            payload["provider"] = provider
        if hasattr(fn, "spawn"):
            fn.spawn(payload)
            logger.info(f"Modal job spawned for privacy redaction: job={job_id}")
        else:
            fn.call(payload)
            logger.info(f"Modal job invoked synchronously for privacy redaction: job={job_id}")
    except Exception as e:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE background_jobs SET status='FAILED', completed_at=NOW(), error=$2 WHERE id = $1",
                job_id,
                str(e),
            )
        raise


async def dispatch_memory_ingest_job(
    *,
    companion_id: UUID,
    items: list[dict],
) -> None:
    """Dispatch a lightweight memory-ingest job to Modal.

    This uses a single function call with an inlined payload; for P0 we do not
    create a row in background_jobs to keep overhead minimal. The worker itself
    will handle DB writes via DbGateway.
    """
    try:
        import modal  # type: ignore

        app_name = os.getenv("MODAL_MEMORY_APP", "em-memory")
        fn_name = os.getenv("MODAL_MEMORY_FN", "ingest_memories")
        fn = modal.Function.from_name(app_name, fn_name)
        payload = {
            "companion_id": str(companion_id),
            "items": items,
        }
        if hasattr(fn, "spawn"):
            fn.spawn(payload)
        else:
            fn.call(payload)
    except Exception as e:
        logger.warning(f"Failed to dispatch memory ingest to Modal: {e}")
        raise


async def dispatch_memory_update_job(*, items: list[dict]) -> None:
    """Dispatch a memory update job to Modal.

    The worker recomputes embeddings if content is provided and updates the
    corresponding rows via DbGateway.
    """
    try:
        import modal  # type: ignore

        app_name = os.getenv("MODAL_MEMORY_APP", "em-memory")
        fn_name = os.getenv("MODAL_MEMORY_UPDATE_FN", "update_memories")
        fn = modal.Function.from_name(app_name, fn_name)
        payload = {"items": items}
        if hasattr(fn, "spawn"):
            fn.spawn(payload)
        else:
            fn.call(payload)
    except Exception as e:
        logger.warning(f"Failed to dispatch memory update to Modal: {e}")
        raise


async def dispatch_conversation_summary_job(
    *,
    job_id: UUID,
    conversation_id: UUID,
) -> None:
    """Dispatch a conversation summarization job to Modal and mark RUNNING."""
    async with get_db_connection() as conn:
        await conn.execute(
            "UPDATE background_jobs SET status='RUNNING', started_at=NOW() WHERE id = $1",
            job_id,
        )

    try:
        import modal  # type: ignore

        app_name = os.getenv("MODAL_SUMMARY_APP", "em-summary")
        fn_name = os.getenv("MODAL_SUMMARY_FN", "summarize_conversation")
        fn = modal.Function.from_name(app_name, fn_name)
        payload = {"job_id": str(job_id), "conversation_id": str(conversation_id)}
        if hasattr(fn, "spawn"):
            fn.spawn(payload)
        else:
            fn.call(payload)
    except Exception as e:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE background_jobs SET status='FAILED', completed_at=NOW(), error=$2 WHERE id = $1",
                job_id,
                str(e),
            )
        raise
