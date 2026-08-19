"""API v2 Messages Router.

Implements the unified event protocol for sending messages in relationships.
Supports both REST (non-streaming) and SSE (streaming) via Accept header.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...context import ContextEvent
from ...db import get_db
from ...models.v2.message import (
    CompositeSendResponse,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    SendMessageResponse,
)
from ...models.v2.relationship import Relationship
from ...repositories.companion import CompanionRepository
from ...repositories.relationship_repository import RelationshipRepository
from ...repositories.session_repository import SessionRepository
from ...request_context import get_request_context, try_get_request_context
from ...services.message_processor import (
    EventEmitter,
    TurnConfig,
    TurnInput,
    TurnProcessor,
    TurnResult,
)

router = APIRouter(prefix="/v2", tags=["v2-messages"])
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# SSE Event Helpers
# -----------------------------------------------------------------------------


def _create_sse_formatter():
    """Create a stateful SSE formatter with unified event protocol.

    Events have: seq (null for ephemeral), timestamp, turn_id, type, data
    """
    event_id = 0

    def format_event(
        event_type: str,
        data: dict[str, Any],
        *,
        seq: int | None = None,
        turn_id: str | None = None,
    ) -> str:
        """Format an SSE event with unified protocol."""
        nonlocal event_id
        event_id += 1

        event_payload = {
            "seq": seq,
            "timestamp": datetime.now(UTC).isoformat(),
            "turn_id": turn_id,
            "type": event_type,
            "data": data,
        }
        payload_str = json.dumps(event_payload)
        return f"event: {event_type}\nid: {event_id}\ndata: {payload_str}\n\n"

    return format_event


# -----------------------------------------------------------------------------
# SSE Event Emitter
# -----------------------------------------------------------------------------


class SSEEventCollector(EventEmitter):
    """EventEmitter that uses asyncio.Queue for true async event delivery.

    Events are pushed to a queue and can be consumed via async iteration,
    eliminating polling overhead.
    """

    def __init__(self, sse_formatter):
        self.sse = sse_formatter
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def events(self) -> AsyncIterator[str]:
        """Async iterator that yields events as they arrive.

        Terminates when done() is called (None sentinel received).
        """
        while True:
            event = await self.queue.get()
            if event is None:  # Sentinel for completion
                break
            yield event

    def done(self) -> None:
        """Signal that no more events will be emitted."""
        self.queue.put_nowait(None)

    async def emit_ack(
        self,
        turn_id: str,
        message_id: UUID,
        seq: int,
        client_message_id: str | None = None,
    ) -> None:
        self.queue.put_nowait(
            self.sse(
                "ack",
                {"message_id": str(message_id), "turn_id": turn_id},
                seq=seq,
                turn_id=turn_id,
            )
        )

    async def emit_status(
        self,
        turn_id: str,
        stage: str,
        phase: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        data: dict[str, Any] = {"stage": stage, "phase": phase}
        if meta:
            data["meta"] = meta
        self.queue.put_nowait(self.sse("status", data, turn_id=turn_id))

    async def emit_delta(self, turn_id: str, content: str) -> None:
        self.queue.put_nowait(
            self.sse(
                "delta",
                {"content": content, "role": "assistant"},
                turn_id=turn_id,
            )
        )

    async def emit_message(
        self,
        turn_id: str,
        message_id: UUID,
        relationship_id: UUID,
        content: str,
        seq: int,
        build_ms: int,
    ) -> None:
        self.queue.put_nowait(
            self.sse(
                "message",
                {
                    "id": str(message_id),
                    "relationship_id": str(relationship_id),
                    "role": "assistant",
                    "content": content,
                    "seq": seq,
                    "build_ms": build_ms,
                },
                seq=seq,
                turn_id=turn_id,
            )
        )

    async def emit_error(self, turn_id: str, code: str, message: str) -> None:
        self.queue.put_nowait(
            self.sse(
                "error",
                {"code": code, "message": message},
                turn_id=turn_id,
            )
        )

    async def emit_trace(self, turn_id: str, event: ContextEvent) -> None:
        self.queue.put_nowait(
            self.sse(
                "trace",
                {
                    "name": event.name,
                    "phase": event.phase,
                    "ts_ms": event.ts_ms,
                    "meta": event.meta,
                },
                turn_id=turn_id,
            )
        )


async def _verify_companion_access(
    conn: asyncpg.Connection,
    companion_id: UUID,
    project_id: UUID,
) -> Any:
    """Verify the companion exists and belongs to the project.

    Also caches companion_id and companion data in RequestContext for downstream use,
    eliminating redundant queries in context layers (e.g., tools_runtime).
    """
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Companion {companion_id} not found",
        )
    if companion.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Companion does not belong to this project",
        )

    # Cache companion_id and companion data in RequestContext
    # This eliminates redundant queries in downstream layers
    ctx = try_get_request_context()
    if ctx:
        ctx.companion_id = companion_id
        ctx.set_cached("companion", companion)

    return companion


async def _validate_session(
    conn: asyncpg.Connection,
    session_id: UUID | None,
    relationship_id: UUID,
) -> None:
    """Validate session if provided.

    Checks:
    - Session exists
    - Session belongs to the relationship
    - Session is active (not ended)
    """
    if session_id is None:
        return

    session = await SessionRepository.get_by_id(conn, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    if session.relationship_id != relationship_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session does not belong to this relationship",
        )

    if session.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is not active",
        )


def _row_to_message_response(row: dict[str, Any]) -> MessageResponse:
    """Convert a database row to MessageResponse."""
    return MessageResponse(
        id=row["id"],
        relationship_id=row["relationship_id"],
        session_id=row.get("session_id"),
        seq=row.get("seq"),
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        build_ms=row.get("build_ms"),
        input_modality=row.get("input_modality"),
        is_proactive=row.get("is_proactive", False),
        delivery_status=row.get("delivery_status"),
    )


# -----------------------------------------------------------------------------
# Core Message Processing
# -----------------------------------------------------------------------------


async def _process_message_non_streaming(
    conn: asyncpg.Connection,
    *,
    relationship: Relationship,
    companion: Any,
    message: MessageCreate,
    project_id: UUID,
) -> tuple[MessageResponse, dict[str, Any] | None]:
    """Process a message and generate a response (non-streaming).

    Uses the shared TurnProcessor for core message processing logic.
    """
    # Look up session isolation status if session provided
    session_isolated = False
    if message.session_id:
        is_isolated = await SessionRepository.is_session_isolated(conn, message.session_id)
        if is_isolated is not None:
            session_isolated = is_isolated

    # Build turn config
    turn_config = None
    if message.config:
        turn_config = TurnConfig(
            model=message.config.model,
            temperature=message.config.temperature,
        )

    processor = TurnProcessor(
        conn=conn,
        companion=companion,
        relationship=relationship,
        emitter=None,  # No events for non-streaming
    )

    turn_input = TurnInput(
        content=message.content,
        session_id=message.session_id,
        session_isolated=session_isolated,
        config=turn_config,
    )

    result = await processor.process_turn_non_streaming(turn_input)

    return _turn_result_to_message_response(result, relationship.id), result.trace


def _turn_result_to_message_response(result: TurnResult, relationship_id: UUID) -> MessageResponse:
    """Convert TurnResult to MessageResponse."""
    return MessageResponse(
        id=result.assistant_message_id,
        relationship_id=relationship_id,
        session_id=None,
        seq=result.assistant_seq,
        role="assistant",
        content=result.assistant_content,
        created_at=datetime.now(UTC),
        build_ms=result.build_ms,
        input_modality="text",
        is_proactive=False,
        delivery_status=None,
    )


# -----------------------------------------------------------------------------
# Streaming Implementation
# -----------------------------------------------------------------------------


async def _stream_message(
    relationship: Relationship,
    companion: Any,
    message: MessageCreate,
    project_id: UUID,
) -> AsyncIterator[str]:
    """Stream a message response using SSE with unified event protocol.

    Uses the request-scoped database connection from middleware, which stays
    open throughout the entire streaming response.
    """
    sse = _create_sse_formatter()

    # Get connection from request context (managed by middleware)
    ctx = get_request_context()
    conn = ctx.conn

    # Create SSE event collector for the emitter
    emitter = SSEEventCollector(sse)

    try:
        # Look up session isolation status if session provided
        session_isolated = False
        if message.session_id:
            is_isolated = await SessionRepository.is_session_isolated(conn, message.session_id)
            if is_isolated is not None:
                session_isolated = is_isolated

        # Build turn config
        turn_config = None
        if message.config:
            turn_config = TurnConfig(
                model=message.config.model,
                temperature=message.config.temperature,
            )

        processor = TurnProcessor(
            conn=conn,
            companion=companion,
            relationship=relationship,
            emitter=emitter,
        )

        turn_input = TurnInput(
            content=message.content,
            session_id=message.session_id,
            session_isolated=session_isolated,
            config=turn_config,
        )

        # Run processor in background task, stream events as they arrive
        async def run_processor():
            """Run processor and signal completion via emitter.done()."""
            try:
                await processor.process_turn_streaming(turn_input)
            finally:
                emitter.done()

        processor_task = asyncio.create_task(run_processor())

        # Heartbeat configuration
        HEARTBEAT_SEC = 15.0
        last_beat = asyncio.get_event_loop().time()

        # Stream events as they arrive via async queue (no polling)
        async for event in emitter.events():
            yield event

            # Check for heartbeat
            now = asyncio.get_event_loop().time()
            if (now - last_beat) >= HEARTBEAT_SEC:
                yield ": keep-alive\n\n"
                last_beat = now

        # Ensure processor completed and propagate any exceptions
        await processor_task

    except HTTPException as exc:
        yield sse("error", {"code": "http_error", "message": exc.detail}, turn_id="")
    except Exception as exc:
        logger.exception("Streaming error")
        yield sse("error", {"code": "internal_error", "message": str(exc)}, turn_id="")


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------


@router.get(
    "/relationships/{relationship_id}/messages",
    response_model=MessageListResponse,
    summary="List relationship messages",
    description="List sequenced messages for a relationship. Includes proactive messages by default.",
)
async def list_relationship_messages(
    relationship_id: UUID,
    limit: int = Query(default=50, ge=1, le=500),
    before_seq: int | None = Query(default=None, ge=1),
    after_seq: int | None = Query(default=None, ge=0),
    session_id: UUID | None = Query(default=None),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """List messages for a relationship with cursor-style pagination."""
    if before_seq is not None and after_seq is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="before_seq and after_seq cannot be used together",
        )

    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    if session_id is not None:
        session = await SessionRepository.get_by_id(conn, session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found",
            )
        if session.relationship_id != relationship_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session does not belong to this relationship",
            )

    rows, has_more = await RelationshipRepository.list_messages(
        conn,
        relationship_id,
        limit=limit,
        before_seq=before_seq,
        after_seq=after_seq,
        session_id=session_id,
    )
    messages = [_row_to_message_response(row) for row in rows]
    seqs = [msg.seq for msg in messages if msg.seq is not None]

    return MessageListResponse(
        relationship_id=relationship_id,
        messages=messages,
        limit=limit,
        has_more=has_more,
        oldest_seq=min(seqs) if seqs else None,
        newest_seq=max(seqs) if seqs else None,
    )


@router.get(
    "/companions/{companion_id}/relationships/{user_id}/messages",
    response_model=MessageListResponse,
    summary="List messages by companion and user",
    description="Resolve relationship by companion+user and list sequenced messages (including proactive messages).",
)
async def list_messages_by_companion_user(
    companion_id: UUID,
    user_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    before_seq: int | None = Query(default=None, ge=1),
    after_seq: int | None = Query(default=None, ge=0),
    session_id: UUID | None = Query(default=None),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """List messages by resolving the relationship from companion and external user id."""
    if before_seq is not None and after_seq is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="before_seq and after_seq cannot be used together",
        )

    await _verify_companion_access(conn, companion_id, subject.project.id)
    relationship = await RelationshipRepository.get_by_companion_and_user(
        conn,
        companion_id=companion_id,
        user_id=user_id,
    )
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship not found for user {user_id}",
        )

    if session_id is not None:
        session = await SessionRepository.get_by_id(conn, session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found",
            )
        if session.relationship_id != relationship.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session does not belong to this relationship",
            )

    rows, has_more = await RelationshipRepository.list_messages(
        conn,
        relationship.id,
        limit=limit,
        before_seq=before_seq,
        after_seq=after_seq,
        session_id=session_id,
    )
    messages = [_row_to_message_response(row) for row in rows]
    seqs = [msg.seq for msg in messages if msg.seq is not None]

    return MessageListResponse(
        relationship_id=relationship.id,
        messages=messages,
        limit=limit,
        has_more=has_more,
        oldest_seq=min(seqs) if seqs else None,
        newest_seq=max(seqs) if seqs else None,
    )


@router.post(
    "/companions/{companion_id}/relationships/{user_id}/messages",
    response_model=CompositeSendResponse,
    summary="Send message (composite)",
    description="Ensure relationship exists and send a message in one call. "
    "Use Accept: text/event-stream header for SSE streaming.",
)
async def send_message_composite(
    companion_id: UUID,
    user_id: str,
    body: MessageCreate,
    accept: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
):
    """Composite endpoint: ensures relationship exists + sends message in one call.

    Uses the request-scoped database connection from middleware, which stays open
    throughout the entire request lifecycle including SSE streaming.
    """
    # Get connection from request context (managed by middleware)
    ctx = get_request_context()
    conn = ctx.conn

    companion = await _verify_companion_access(conn, companion_id, subject.project.id)
    relationship, created = await RelationshipRepository.ensure_exists(
        conn,
        companion_id=companion_id,
        user_id=user_id,
    )

    logger.info(
        "Composite send: relationship=%s (%s) for companion=%s user=%s",
        relationship.id,
        "created" if created else "existing",
        companion_id,
        user_id,
    )

    # Validate session if provided
    await _validate_session(conn, body.session_id, relationship.id)

    # Check for SSE streaming
    if accept and "text/event-stream" in accept:
        return StreamingResponse(
            _stream_message(relationship, companion, body, subject.project.id),
            media_type="text/event-stream",
        )

    # Non-streaming response
    message_response, trace = await _process_message_non_streaming(
        conn,
        relationship=relationship,
        companion=companion,
        message=body,
        project_id=subject.project.id,
    )

    return CompositeSendResponse(
        relationship_id=relationship.id,
        relationship_created=created,
        message=message_response,
        trace=trace,
    )


@router.post(
    "/relationships/{relationship_id}/messages",
    response_model=SendMessageResponse,
    summary="Send message (direct)",
    description="Send a message to an existing relationship. "
    "Use Accept: text/event-stream header for SSE streaming.",
)
async def send_message_direct(
    relationship_id: UUID,
    body: MessageCreate,
    accept: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
):
    """Direct endpoint: send message to existing relationship.

    Uses the request-scoped database connection from middleware, which stays open
    throughout the entire request lifecycle including SSE streaming.
    """
    # Get connection from request context (managed by middleware)
    ctx = get_request_context()
    conn = ctx.conn

    # Get relationship
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    # Verify companion access
    companion = await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    logger.info(
        "Direct send: relationship=%s companion=%s user=%s",
        relationship.id,
        relationship.companion_id,
        relationship.user_id,
    )

    # Validate session if provided
    await _validate_session(conn, body.session_id, relationship.id)

    # Check for SSE streaming
    if accept and "text/event-stream" in accept:
        return StreamingResponse(
            _stream_message(relationship, companion, body, subject.project.id),
            media_type="text/event-stream",
        )

    # Non-streaming response
    message_response, trace = await _process_message_non_streaming(
        conn,
        relationship=relationship,
        companion=companion,
        message=body,
        project_id=subject.project.id,
    )

    return SendMessageResponse(
        relationship_id=relationship.id,
        message=message_response,
        trace=trace,
    )
