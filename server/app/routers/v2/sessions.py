"""API v2 Sessions Router.

Implements session-centric endpoints for bounded interactions within relationships.
Sessions are optional - default behavior is continuous chat.

Use cases:
- Billing ("this coaching session = $50")
- Reporting ("show me all therapy sessions")
- Context isolation ("don't carry over from last session")
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...db import get_db
from ...models.v2.session import (
    Session,
    SessionCreate,
    SessionEndResponse,
    SessionListResponse,
    SessionResponse,
    SessionStatePatch,
)
from ...repositories.companion import CompanionRepository
from ...repositories.relationship_repository import RelationshipRepository
from ...repositories.session_repository import SessionRepository
from ...services.llm import generate_llm_response_direct

router = APIRouter(prefix="/v2", tags=["v2-sessions"])
logger = logging.getLogger(__name__)


def _session_to_response(session: Session) -> SessionResponse:
    """Convert internal Session to API response."""
    return SessionResponse(
        id=session.id,
        relationship_id=session.relationship_id,
        type=session.type,
        status=session.status,
        isolated=session.isolated,
        state=session.state,
        summary=session.summary,
        created_at=session.created_at,
        ended_at=session.ended_at,
    )


async def _verify_companion_access(
    conn: asyncpg.Connection,
    companion_id: UUID,
    project_id: UUID,
) -> None:
    """Verify the companion exists and belongs to the project."""
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


async def _verify_relationship_access(
    conn: asyncpg.Connection,
    relationship_id: UUID,
    project_id: UUID,
) -> None:
    """Verify the relationship exists and belongs to a companion in the project."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )
    await _verify_companion_access(conn, relationship.companion_id, project_id)


async def _generate_session_summary(
    messages: list[dict],
) -> str:
    """Generate an AI summary of session messages.

    Args:
        messages: List of message dicts with role and content

    Returns:
        A 2-3 sentence summary of the conversation
    """
    if not messages:
        return ""

    # Build conversation text for summarization
    conversation_parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role == "user":
            conversation_parts.append(f"User: {content}")
        elif role == "assistant":
            conversation_parts.append(f"Assistant: {content}")

    conversation_text = "\n".join(conversation_parts)

    # Truncate if too long (keep under ~2000 chars for efficient summarization)
    if len(conversation_text) > 2000:
        conversation_text = conversation_text[:2000] + "..."

    # Generate summary using LLM
    llm_messages = [
        {
            "role": "system",
            "content": (
                "You are a conversation summarizer. Summarize the following conversation "
                "in 2-3 concise sentences. Focus on the main topics discussed and any "
                "key outcomes or decisions. Be objective and factual."
            ),
        },
        {
            "role": "user",
            "content": f"Summarize this conversation:\n\n{conversation_text}",
        },
    ]

    try:
        summary = await generate_llm_response_direct(
            "openai-gpt4o-mini",
            llm_messages,
            temperature=0.3,
            max_tokens=150,
        )
        return summary.strip()
    except Exception as e:
        logger.warning(f"Failed to generate session summary: {e}")
        return ""


# -----------------------------------------------------------------------------
# Session CRUD
# -----------------------------------------------------------------------------


@router.post(
    "/relationships/{relationship_id}/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
    description="Start a new bounded session within a relationship. "
    "Only one active session per relationship is allowed.",
)
async def create_session(
    relationship_id: UUID,
    body: SessionCreate | None = None,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SessionResponse:
    """Create a new session for a relationship."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    # Check if there's already an active session
    existing = await SessionRepository.get_active_for_relationship(conn, relationship_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Relationship already has an active session: {existing.id}",
        )

    try:
        session = await SessionRepository.create(
            conn,
            relationship_id=relationship_id,
            type=body.type if body else None,
            isolated=body.isolated if body else False,
        )
    except asyncpg.RaiseError as e:
        # Database trigger raised an error about multiple active sessions
        if "already has an active session" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Relationship already has an active session",
            )
        raise

    logger.info(
        "Session created: id=%s, relationship=%s, type=%s, isolated=%s",
        session.id,
        relationship_id,
        session.type,
        session.isolated,
    )

    return _session_to_response(session)


@router.get(
    "/relationships/{relationship_id}/sessions",
    response_model=SessionListResponse,
    summary="List sessions for a relationship",
    description="Get a paginated list of sessions for a relationship, ordered by creation time descending.",
)
async def list_sessions(
    relationship_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SessionListResponse:
    """List sessions for a relationship."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    sessions, next_cursor, total = await SessionRepository.list_for_relationship(
        conn, relationship_id, limit=limit, cursor=cursor
    )

    return SessionListResponse(
        sessions=[_session_to_response(s) for s in sessions],
        next_cursor=next_cursor,
        total=total,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get a session by ID",
    description="Retrieve a specific session by its unique ID.",
)
async def get_session(
    session_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SessionResponse:
    """Get a session by its ID."""
    session = await SessionRepository.get_by_id(conn, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    # Verify access via relationship
    await _verify_relationship_access(conn, session.relationship_id, subject.project.id)

    return _session_to_response(session)


@router.post(
    "/sessions/{session_id}/end",
    response_model=SessionEndResponse,
    summary="End a session",
    description="End an active session. Generates an AI summary of the conversation.",
)
async def end_session(
    session_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SessionEndResponse:
    """End an active session and generate summary."""
    session = await SessionRepository.get_by_id(conn, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    # Verify access via relationship
    await _verify_relationship_access(conn, session.relationship_id, subject.project.id)

    if session.status == "ended":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is already ended",
        )

    # Get session messages for summary generation
    messages = await SessionRepository.get_session_messages(conn, session_id, limit=100)

    # Generate summary
    summary = await _generate_session_summary(messages)

    # End the session
    ended_session = await SessionRepository.end_session(conn, session_id, summary=summary)
    if not ended_session:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to end session",
        )

    logger.info(
        "Session ended: id=%s, summary_length=%d",
        session_id,
        len(summary) if summary else 0,
    )

    return SessionEndResponse(
        id=ended_session.id,
        status="ended",
        summary=ended_session.summary,
        ended_at=ended_session.ended_at,
    )


@router.patch(
    "/sessions/{session_id}/state",
    response_model=SessionResponse,
    summary="Patch session state",
    description="Merge changes into session state using JSON Merge Patch. "
    "Only works on active, non-isolated sessions.",
)
async def patch_session_state(
    session_id: UUID,
    body: SessionStatePatch,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SessionResponse:
    """Patch session state with JSON merge patch."""
    session = await SessionRepository.get_by_id(conn, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    # Verify access via relationship
    await _verify_relationship_access(conn, session.relationship_id, subject.project.id)

    if session.status == "ended":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update state of ended session",
        )

    if session.isolated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update state of isolated session",
        )

    updated = await SessionRepository.patch_state(conn, session_id, body.changes)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update session state",
        )

    return _session_to_response(updated)


# -----------------------------------------------------------------------------
# Session Retrieval Helpers
# -----------------------------------------------------------------------------


@router.get(
    "/relationships/{relationship_id}/sessions/active",
    response_model=Optional[SessionResponse],
    summary="Get active session for a relationship",
    description="Get the currently active session for a relationship, if any.",
)
async def get_active_session(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SessionResponse | None:
    """Get the active session for a relationship."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    session = await SessionRepository.get_active_for_relationship(conn, relationship_id)
    if not session:
        return None

    return _session_to_response(session)
