"""
Analytics API endpoints for conversation data visualization and labeling.
Implements analytics per ANALYTICS_IMPLEMENTATION_PLAN.md and
labeling triggers per docs/label-conversations-feature.md.
"""

import asyncio
import json
import logging
import os
from typing import List, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..auth import get_current_user
from ..db import get_db
from ..models.user import User
from ..repositories.conversation import (
    delete_conversation as repo_delete_conversation,
)
from ..repositories.conversation import (
    get_conversation_messages,
    get_user_analytics_summary,
)
from ..repositories.labeling import (
    create_labeling_job,
)
from ..repositories.labeling import (
    get_job as repo_get_job,
)
from ..repositories.labeling import (
    get_labels_for_companion as repo_get_labels_for_companion,
)
from ..repositories.labeling import (
    get_labels_for_conversation as repo_get_labels_for_conversation,
)
from ..services.modal_gateway import (
    dispatch_label_conversations_job,
    dispatch_privacy_redaction_job,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Models for Analytics API
# ──────────────────────────────────────────────────────────────────────────────


class ConversationSummary(BaseModel):
    """Summary information for a conversation in analytics view"""

    id: str
    external_user_id: str
    started_at: str
    message_count: int
    last_message_at: str | None = None


class AnalyticsMessage(BaseModel):
    """Message model for analytics display"""

    id: str
    role: str
    content: str
    created_at: str
    input_modality: Literal["voice", "text"] | None = None


class SystemPromptResponse(BaseModel):
    """System prompt for a conversation"""

    system_prompt: str


class TriggerLabelingRequest(BaseModel):
    model: str | None = None
    provider: str | None = None
    skip_existing: bool = True
    since: str | None = None  # ISO timestamp


class TriggerLabelingResponse(BaseModel):
    job_id: str


class ConversationLabelRow(BaseModel):
    conversation_id: str
    external_user_id: str | None = None
    started_at: str
    last_message_at: str | None = None
    message_count: int
    engagement_label: str | None = None
    dependency_risk_label: str | None = None
    analyzed_at: str | None = None


class LabelingJobStatus(BaseModel):
    id: str
    status: str
    total_conversations: int | None = 0
    processed_count: int | None = 0
    error_count: int | None = 0
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class UserAnalyticsSummary(BaseModel):
    external_user_id: str
    session_count: int
    total_message_count: int


# ──────────────────────────────────────────────────────────────────────────────
# Analytics Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/companions/{companion_id}/conversations", response_model=List[ConversationSummary])
async def get_conversations_for_companion_analytics(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Get all conversations for a specific companion for analytics view.
    Returns conversations with message counts and last message timestamps.
    """
    try:
        # First verify the companion belongs to the current user
        companion_check = await conn.fetchrow(
            "SELECT id FROM companions WHERE id = $1 AND owner_id = $2", companion_id, user.id
        )
        if not companion_check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Companion not found or not accessible",
            )

        # Read from persisted counters for speed
        conversations = await conn.fetch(
            """
            SELECT
                c.id,
                c.external_user_id,
                c.started_at,
                c.message_count,
                c.last_message_at
            FROM conversations c
            WHERE c.companion_id = $1
            ORDER BY c.started_at DESC
            """,
            companion_id,
        )

        return [
            ConversationSummary(
                id=str(conv["id"]),
                external_user_id=conv["external_user_id"],
                started_at=conv["started_at"].isoformat(),
                message_count=conv["message_count"] or 0,
                last_message_at=conv["last_message_at"].isoformat()
                if conv["last_message_at"]
                else None,
            )
            for conv in conversations
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch conversations for companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch conversations",
        )


@router.get("/conversations/{conversation_id}/messages", response_model=List[AnalyticsMessage])
async def get_messages_for_conversation_analytics(
    conversation_id: UUID,
    view: str | None = None,
    fallback: bool | None = False,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Get all messages for a conversation for analytics view.
    Verifies user has access to the conversation's companion.
    """
    try:
        # Verify the conversation belongs to a companion owned by the current user
        conversation_check = await conn.fetchrow(
            """
            SELECT c.id
            FROM conversations c
            JOIN companions comp ON c.companion_id = comp.id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not conversation_check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found or not accessible",
            )

        # Get messages using existing repository function
        messages = await get_conversation_messages(conn, conversation_id)

        # If redacted view requested, compute masked content using stored spans
        if (view or "").lower() == "redacted":
            # Determine staleness: any message missing spans or redacted_at older than created_at
            stale = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.conversation_id = $1
                      AND m.role IN ('user','assistant')
                      AND (m.redacted_at IS NULL OR m.redacted_at < m.created_at)
                )
                """,
                conversation_id,
            )
            if stale and not fallback:
                raise HTTPException(status_code=409, detail="Privacy view is stale or not computed")

            def mask_text(text: str, spans: List[dict] | str | None) -> str:
                # Accept JSON string or list for robustness
                if isinstance(spans, (str | bytes)):
                    try:
                        spans = json.loads(spans)  # type: ignore[assignment]
                    except Exception:
                        spans = None  # fall through to return original text
                if not spans:
                    return text
                n = len(text or "")
                parts: List[str] = []
                last = 0
                # Sort and clamp
                norm = []
                for s in spans or []:
                    try:
                        a = max(0, min(n, int(s.get("start", 0))))
                        b = max(0, min(n, int(s.get("end", 0))))
                        if b > a:
                            norm.append({"start": a, "end": b})
                    except Exception:
                        continue
                norm.sort(key=lambda x: (x["start"], x["end"]))
                # Merge overlaps
                merged: List[dict] = []
                for s in norm:
                    if merged and s["start"] <= merged[-1]["end"]:
                        merged[-1]["end"] = max(merged[-1]["end"], s["end"])
                    else:
                        merged.append(s)
                for s in merged:
                    parts.append(text[last : s["start"]])
                    parts.append("█" * (s["end"] - s["start"]))
                    last = s["end"]
                parts.append(text[last:])
                return "".join(parts)

            result: List[AnalyticsMessage] = []
            # Pre-fetch spans for all messages in one query
            rows = await conn.fetch(
                """
                SELECT id, content, pii_spans
                FROM messages
                WHERE conversation_id = $1
                ORDER BY created_at
                """,
                conversation_id,
            )
            by_id = {r["id"]: r for r in rows}
            for msg in messages:
                r = by_id.get(msg["id"])
                content = r["content"] if r else msg["content"]
                spans = r["pii_spans"] if r else None
                masked = mask_text(content, spans)
                result.append(
                    AnalyticsMessage(
                        id=str(msg["id"]),
                        role=msg["role"],
                        content=masked,
                        created_at=msg["created_at"].isoformat(),
                        input_modality=msg.get("input_modality"),
                    )
                )
            return result

        return [
            AnalyticsMessage(
                id=str(msg["id"]),
                role=msg["role"],
                content=msg["content"],
                created_at=msg["created_at"].isoformat(),
                input_modality=msg.get("input_modality"),
            )
            for msg in messages
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch messages for conversation {conversation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch messages"
        )


@router.get(
    "/companions/{companion_id}/users/{external_user_id}/summary",
    response_model=UserAnalyticsSummary,
)
async def get_user_analytics_summary_endpoint(
    companion_id: UUID,
    external_user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Return aggregate analytics for a specific external user within a companion."""

    try:
        companion_check = await conn.fetchrow(
            "SELECT id FROM companions WHERE id = $1 AND owner_id = $2",
            companion_id,
            user.id,
        )
        if not companion_check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Companion not found or not accessible",
            )

        summary = await get_user_analytics_summary(
            conn,
            companion_id=companion_id,
            external_user_id=external_user_id,
        )

        return UserAnalyticsSummary(
            external_user_id=external_user_id,
            session_count=summary.get("session_count", 0),
            total_message_count=summary.get("total_message_count", 0),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Failed to fetch analytics summary for companion %s user %s: %s",
            companion_id,
            external_user_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch user analytics summary",
        )


@router.get("/conversations/{conversation_id}/system-prompt", response_model=SystemPromptResponse)
async def get_system_prompt_for_conversation_analytics(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Get the system prompt used for a conversation.
    Handles both deployment-based and direct companion conversations.
    """
    try:
        # Verify the conversation belongs to a companion owned by the current user
        conversation_check = await conn.fetchrow(
            """
            SELECT c.id
            FROM conversations c
            JOIN companions comp ON c.companion_id = comp.id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not conversation_check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found or not accessible",
            )

        # Decide dynamically: for direct companion conversations (no deployment),
        # assemble effective prompt at read-time so core memories reflect latest.
        meta = await conn.fetchrow(
            "SELECT deployment_id, companion_id FROM conversations WHERE id = $1",
            conversation_id,
        )
        if not meta:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        if not meta["deployment_id"]:
            from ..services.context_assembly import build_effective_system_prompt

            effective, _ = await build_effective_system_prompt(
                conn, companion_id=meta["companion_id"]
            )
            return SystemPromptResponse(system_prompt=effective)

        row = await conn.fetchrow(
            """
            SELECT COALESCE(cv.effective_system_prompt, cv.system_prompt) AS system_prompt
            FROM conversations c
            JOIN deployments d ON c.deployment_id = d.id
            JOIN companion_versions cv ON d.version_id = cv.id
            WHERE c.id = $1
            """,
            conversation_id,
        )
        if not row or not row["system_prompt"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="System prompt not found for conversation",
            )
        return SystemPromptResponse(system_prompt=row["system_prompt"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch system prompt for conversation {conversation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch system prompt",
        )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation_analytics(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Delete a conversation and its messages for analytics.
    Ensures the conversation belongs to a companion owned by the current user.
    """
    try:
        # Verify access: conversation must belong to a companion owned by current user
        conversation_check = await conn.fetchrow(
            """
            SELECT c.id
            FROM conversations c
            JOIN companions comp ON c.companion_id = comp.id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not conversation_check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found or not accessible",
            )

        deleted = await repo_delete_conversation(conn, conversation_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        return {"message": "Conversation deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete conversation {conversation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete conversation",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Conversation labeling endpoints (stubs + basic DB interactions)
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companions/{companion_id}/label-conversations", response_model=TriggerLabelingResponse
)
async def trigger_conversation_labeling(
    companion_id: UUID,
    payload: TriggerLabelingRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Create (or return existing) labeling job for this companion.
    Ensures companion ownership and single active job per companion.
    """
    # Feature flag: allow gating in production rollout
    if os.getenv("ENABLE_CONVERSATION_LABELING", "true").lower() not in ("1", "true", "yes", "on"):
        raise HTTPException(status_code=404, detail="Not found")
    # Verify companion ownership
    companion_check = await conn.fetchrow(
        "SELECT id FROM companions WHERE id = $1 AND owner_id = $2", companion_id, user.id
    )
    if not companion_check:
        raise HTTPException(status_code=404, detail="Companion not found or not accessible")

    # Defaults per PRD
    model = payload.model or "gemini-2.5-flash-lite"
    provider = payload.provider or "google"

    job_id = await create_labeling_job(
        conn,
        user.id,
        companion_id,
        model=model,
        provider=provider,
        labels_version=1,
        skip_existing=payload.skip_existing,
        since=payload.since,
    )

    # NOTE: Actual Modal dispatch will be wired here later.
    # Fire-and-forget background task (Modal integration can replace this)
    try:
        # Dispatch to Modal (preferred), with local fallback
        asyncio.create_task(
            dispatch_label_conversations_job(
                job_id=job_id,
                companion_id=companion_id,
                model=model,
                provider=provider,
                labels_version=1,
                skip_existing=payload.skip_existing,
                since=payload.since,
            )
        )
    except Exception as e:
        logger.error(f"Failed to dispatch labeling job: {e}")

    return TriggerLabelingResponse(job_id=str(job_id))


@router.get("/companions/{companion_id}/labels", response_model=List[ConversationLabelRow])
async def get_companion_labels(
    companion_id: UUID,
    engagement: str | None = None,
    risk: str | None = None,
    status_filter: str | None = None,
    q: str | None = None,
    min_msgs: int | None = None,
    max_msgs: int | None = None,
    limit: int = 50,
    offset: int = 0,
    fast: bool = True,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Fetch labels joined with conversation summary rows for a companion.
    """
    # Verify companion ownership
    companion_check = await conn.fetchrow(
        "SELECT id FROM companions WHERE id = $1 AND owner_id = $2", companion_id, user.id
    )
    if not companion_check:
        raise HTTPException(status_code=404, detail="Companion not found or not accessible")

    engagement_list = [e for e in (engagement.split(",") if engagement else []) if e]
    risk_list = [r for r in (risk.split(",") if risk else []) if r]
    status_list = [s for s in (status_filter.split(",") if status_filter else []) if s]

    rows = await repo_get_labels_for_companion(
        conn,
        companion_id,
        engagement=engagement_list or None,
        risk=risk_list or None,
        status=status_list or None,
        q=q or None,
        limit=limit,
        offset=offset,
        fast=fast,
        min_msgs=min_msgs,
        max_msgs=max_msgs,
    )
    result: List[ConversationLabelRow] = []
    for r in rows:
        started = r["started_at"]
        last = r.get("last_message_at")
        analyzed = r.get("analyzed_at")
        result.append(
            ConversationLabelRow(
                conversation_id=str(r["conversation_id"]),
                external_user_id=r.get("external_user_id"),
                started_at=started.isoformat() if hasattr(started, "isoformat") else str(started),
                last_message_at=(
                    last.isoformat()
                    if hasattr(last, "isoformat")
                    else (str(last) if last else None)
                ),
                message_count=int(r.get("message_count") or 0),
                engagement_label=r.get("engagement_label"),
                dependency_risk_label=r.get("dependency_risk_label"),
                analyzed_at=(
                    analyzed.isoformat()
                    if hasattr(analyzed, "isoformat")
                    else (str(analyzed) if analyzed else None)
                ),
            )
        )
    return result


@router.get("/conversations/{conversation_id}/labels", response_model=ConversationLabelRow)
async def get_conversation_labels(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Fetch label row for a single conversation and project to row shape.
    """
    # Verify access to conversation
    row = await conn.fetchrow(
        """
        SELECT c.id, comp.owner_id
        FROM conversations c
        JOIN companions comp ON comp.id = c.companion_id
        WHERE c.id = $1
        """,
        conversation_id,
    )
    if not row or row["owner_id"] != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found or not accessible")

    label = await repo_get_labels_for_conversation(conn, conversation_id)
    if not label:
        raise HTTPException(status_code=404, detail="Labels not found for conversation")

    # Fetch summary fields
    summary = await conn.fetchrow(
        """
        SELECT c.external_user_id, c.started_at, c.last_message_at, c.message_count
        FROM conversations c
        WHERE c.id = $1
        """,
        conversation_id,
    )
    return ConversationLabelRow(
        conversation_id=str(conversation_id),
        external_user_id=summary.get("external_user_id") if summary else None,
        started_at=summary["started_at"].isoformat() if summary else "",
        last_message_at=summary["last_message_at"].isoformat()
        if summary and summary.get("last_message_at")
        else None,
        message_count=int(summary.get("message_count") or 0) if summary else 0,
        engagement_label=label["engagement_label"],
        dependency_risk_label=label["dependency_risk_label"],
        analyzed_at=label["analyzed_at"].isoformat()
        if hasattr(label["analyzed_at"], "isoformat")
        else str(label["analyzed_at"]),
    )


@router.get("/jobs/{job_id}", response_model=LabelingJobStatus)
async def get_labeling_job_status(
    job_id: UUID, user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """
    Return status for a labeling job. Validates ownership via companion.
    """
    job = await repo_get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Verify owner matches
    if job.get("owner_id") != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    return LabelingJobStatus(
        id=str(job["id"]),
        status=job["status"],
        total_conversations=int(job.get("total_conversations") or job.get("total_items") or 0),
        processed_count=int(job.get("processed_count") or 0),
        error_count=int(job.get("error_count") or 0),
        created_at=job["created_at"].isoformat() if job.get("created_at") else None,
        started_at=job["started_at"].isoformat() if job.get("started_at") else None,
        completed_at=job["completed_at"].isoformat() if job.get("completed_at") else None,
    )


@router.get("/jobs/{job_id}/events")
async def stream_labeling_job_events(
    job_id: UUID,
    user: User = Depends(get_current_user),
):
    """Server-Sent Events stream for job updates. Avoids client polling.

    Implementation uses Postgres NOTIFY/LISTEN on channel 'job_updates'.
    The Modal worker publishes job updates via pg_notify.
    """
    # Verify job ownership
    # Use a short-lived connection for the ownership check only
    from ..db import get_db_connection

    async with get_db_connection() as conn:
        job = await conn.fetchrow(
            "SELECT id, owner_id, status, total_items, processed_count, error_count, created_at, started_at, completed_at FROM background_jobs WHERE id=$1",
            job_id,
        )
    if not job or job["owner_id"] != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    async def event_generator():
        # Dedicated connection to avoid blocking the pool on long-lived SSE
        dsn = os.getenv("DATABASE_DSN")
        listener_conn = await asyncpg.connect(dsn)  # type: ignore[arg-type]
        queue: asyncio.Queue = asyncio.Queue()

        def _listener(connection, pid, channel, payload):
            try:
                data = json.loads(payload)
                if str(job_id) == data.get("id"):
                    queue.put_nowait(data)
            except Exception:
                pass

        await listener_conn.add_listener("job_updates", _listener)
        try:
            # Send initial snapshot
            init_payload = {
                "id": str(job_id),
                "status": job["status"],
                "total_conversations": int(job.get("total_items") or 0),
                "processed_count": int(job.get("processed_count") or 0),
                "error_count": int(job.get("error_count") or 0),
            }
            yield f"data: {json.dumps(init_payload)}\n\n"

            # Fallback poll if NOTIFY doesn't come through + heartbeat to keep proxies from buffering
            last = init_payload
            last_beat = asyncio.get_event_loop().time()
            HEARTBEAT_SEC = 15.0
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=1.5)
                    yield f"data: {json.dumps(data)}\n\n"
                    last = data
                    if data.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                        break
                except TimeoutError:
                    row = await listener_conn.fetchrow(
                        "SELECT status, total_items, processed_count, error_count FROM background_jobs WHERE id=$1",
                        job_id,
                    )
                    if not row:
                        continue
                    snap = {
                        "id": str(job_id),
                        "status": row["status"],
                        "total_conversations": int(row.get("total_items") or 0),
                        "processed_count": int(row.get("processed_count") or 0),
                        "error_count": int(row.get("error_count") or 0),
                    }
                    if (
                        snap["status"] != last.get("status")
                        or snap["total_conversations"] != last.get("total_conversations")
                        or snap["processed_count"] != last.get("processed_count")
                        or snap["error_count"] != last.get("error_count")
                    ):
                        yield f"data: {json.dumps(snap)}\n\n"
                        last = snap
                    if snap.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                        break
                    # Periodic heartbeat to nudge proxies to flush
                    now = asyncio.get_event_loop().time()
                    if (now - last_beat) >= HEARTBEAT_SEC:
                        # SSE comment line per spec
                        yield ": keep-alive\n\n"
                        last_beat = now
        except asyncio.CancelledError:
            # Client disconnected; just exit
            pass
        finally:
            try:
                await listener_conn.remove_listener("job_updates", _listener)
            except Exception:
                pass
            try:
                await listener_conn.close()
            except Exception:
                pass

    # Important headers for SSE over proxies (disable buffering and caching)
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


# ──────────────────────────────────────────────────────────────────────────────
# Privacy Mode Endpoints
# ──────────────────────────────────────────────────────────────────────────────


class PrivacyStatus(BaseModel):
    status: str | None = None  # active job status if any
    job_id: str | None = None
    stale: bool = False
    error: str | None = None
    enabled: bool = False
    last_computed_at: str | None = None


@router.get("/conversations/{conversation_id}/privacy/status", response_model=PrivacyStatus)
async def get_privacy_status(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    # Verify access
    row = await conn.fetchrow(
        """
        SELECT comp.owner_id, c.privacy_enabled, c.privacy_last_computed_at
        FROM conversations c
        JOIN companions comp ON c.companion_id = comp.id
        WHERE c.id = $1
        """,
        conversation_id,
    )
    if not row or row["owner_id"] != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found or not accessible")

    # Staleness
    stale = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM messages m
          WHERE m.conversation_id = $1
            AND (m.redacted_at IS NULL OR m.redacted_at < m.created_at)
        )
        """,
        conversation_id,
    )

    job = await conn.fetchrow(
        """
        SELECT id, status, error
        FROM background_jobs
        WHERE job_type='privacy_redaction' AND conversation_id=$1 AND status IN ('PENDING','RUNNING')
        ORDER BY created_at DESC LIMIT 1
        """,
        conversation_id,
    )
    return PrivacyStatus(
        status=(job["status"] if job else None),
        job_id=(str(job["id"]) if job else None),
        stale=bool(stale),
        error=(job.get("error") if job else None) if job else None,
        enabled=bool(row.get("privacy_enabled")),
        last_computed_at=(
            row["privacy_last_computed_at"].isoformat()
            if row.get("privacy_last_computed_at")
            else None
        ),
    )


class TriggerPrivacyRequest(BaseModel):
    model: str | None = None
    provider: str | None = None
    force: bool = False


class TriggerPrivacyResponse(BaseModel):
    job_id: str


@router.post(
    "/conversations/{conversation_id}/privacy/redact", response_model=TriggerPrivacyResponse
)
async def trigger_privacy_redaction(
    conversation_id: UUID,
    payload: TriggerPrivacyRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    # Verify access
    row = await conn.fetchrow(
        """
        SELECT comp.owner_id
        FROM conversations c
        JOIN companions comp ON c.companion_id = comp.id
        WHERE c.id = $1
        """,
        conversation_id,
    )
    if not row or row["owner_id"] != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found or not accessible")

    if not payload.force:
        existing = await conn.fetchrow(
            """
            SELECT id FROM background_jobs
            WHERE job_type='privacy_redaction' AND conversation_id=$1 AND status IN ('PENDING','RUNNING')
            ORDER BY created_at DESC LIMIT 1
            """,
            conversation_id,
        )
        if existing:
            return TriggerPrivacyResponse(job_id=str(existing["id"]))

    import uuid

    job_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO background_jobs (id, owner_id, job_type, status, conversation_id, params)
        VALUES (
          $1, $2, 'privacy_redaction', 'PENDING', $3,
          jsonb_build_object('model', $4::text, 'provider', $5::text)
        )
        """,
        job_id,
        user.id,
        conversation_id,
        payload.model,
        payload.provider,
    )

    await dispatch_privacy_redaction_job(
        job_id=job_id,
        conversation_id=conversation_id,
        model=payload.model,
        provider=payload.provider,
    )
    return TriggerPrivacyResponse(job_id=str(job_id))


class PrivacyToggleRequest(BaseModel):
    enabled: bool


@router.post("/conversations/{conversation_id}/privacy/toggle")
async def toggle_privacy_flag(
    conversation_id: UUID,
    payload: PrivacyToggleRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    # Verify access
    row = await conn.fetchrow(
        """
        SELECT comp.owner_id
        FROM conversations c
        JOIN companions comp ON c.companion_id = comp.id
        WHERE c.id = $1
        """,
        conversation_id,
    )
    if not row or row["owner_id"] != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found or not accessible")

    await conn.execute(
        "UPDATE conversations SET privacy_enabled = $2 WHERE id = $1",
        conversation_id,
        payload.enabled,
    )
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
# Sharing Endpoints (owner-only management)
# ──────────────────────────────────────────────────────────────────────────────


class ShareStatusResponse(BaseModel):
    status: str  # private|public
    stale: bool
    slug: str | None = None
    url_path: str | None = None


@router.get("/conversations/{conversation_id}/share/status", response_model=ShareStatusResponse)
async def get_share_status(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    row = await conn.fetchrow(
        """
        SELECT c.share_status, c.share_slug
        FROM conversations c
        JOIN companions comp ON c.companion_id = comp.id
        WHERE c.id = $1 AND comp.owner_id = $2
        """,
        conversation_id,
        user.id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found or not accessible")

    stale = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM messages m
          WHERE m.conversation_id = $1
            AND (m.redacted_at IS NULL OR m.redacted_at < m.created_at)
        )
        """,
        conversation_id,
    )
    slug = row.get("share_slug")
    return ShareStatusResponse(
        status=row["share_status"],
        stale=bool(stale),
        slug=slug,
        url_path=(f"/share/{slug}" if slug else None),
    )


@router.post("/conversations/{conversation_id}/share/enable", response_model=ShareStatusResponse)
async def enable_share(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    # Verify ownership
    conv = await conn.fetchrow(
        """
        SELECT c.id, c.share_status, c.share_slug
        FROM conversations c
        JOIN companions comp ON c.companion_id = comp.id
        WHERE c.id = $1 AND comp.owner_id = $2
        """,
        conversation_id,
        user.id,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found or not accessible")

    stale = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM messages m
          WHERE m.conversation_id = $1
            AND (m.redacted_at IS NULL OR m.redacted_at < m.created_at)
        )
        """,
        conversation_id,
    )
    if stale:
        raise HTTPException(status_code=409, detail="Privacy view is stale")

    # Generate slug if needed
    slug = conv.get("share_slug")
    if not slug:
        import secrets

        slug = secrets.token_urlsafe(10).replace("_", "").replace("-", "")[:14]
    await conn.execute(
        """
        UPDATE conversations
        SET share_status='public', share_slug=$2, share_enabled_at=NOW()
        WHERE id=$1
        """,
        conversation_id,
        slug,
    )
    return ShareStatusResponse(status="public", stale=False, slug=slug, url_path=f"/share/{slug}")


@router.post("/conversations/{conversation_id}/share/disable", response_model=ShareStatusResponse)
async def disable_share(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    # Verify ownership
    conv = await conn.fetchrow(
        """
        SELECT c.id
        FROM conversations c
        JOIN companions comp ON c.companion_id = comp.id
        WHERE c.id = $1 AND comp.owner_id = $2
        """,
        conversation_id,
        user.id,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found or not accessible")

    await conn.execute(
        "UPDATE conversations SET share_status='private', share_slug=NULL WHERE id=$1",
        conversation_id,
    )
    return ShareStatusResponse(status="private", stale=False, slug=None, url_path=None)
