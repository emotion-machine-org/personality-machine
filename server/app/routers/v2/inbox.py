"""API v2 Inbox Router.

Implements inbox endpoints for retrieving and acknowledging proactive messages.
Phase 7 of the v2 API implementation.

Endpoints:
- GET  /v2/relationships/{id}/inbox - Get pending proactive messages
- POST /v2/relationships/{id}/inbox/ack - Acknowledge receipt of messages
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...db import get_db
from ...repositories.relationship_repository import RelationshipRepository

router = APIRouter(prefix="/v2", tags=["v2-inbox"])
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class InboxMessage(BaseModel):
    """A proactive message in the user's inbox."""

    id: UUID
    content: str
    seq: int | None = None
    source_behavior_key: str | None = None
    delivery_status: str
    expires_at: datetime | None = None
    created_at: datetime


class InboxResponse(BaseModel):
    """Response for inbox listing."""

    messages: List[InboxMessage]
    count: int


class AckRequest(BaseModel):
    """Request to acknowledge messages."""

    message_ids: List[UUID] = Field(..., description="List of message IDs to acknowledge")


class AckResponse(BaseModel):
    """Response for acknowledge request."""

    acknowledged: int
    message_ids: List[str]


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------


@router.get(
    "/relationships/{relationship_id}/inbox",
    response_model=InboxResponse,
    summary="Get pending proactive messages",
    description="Retrieve all pending proactive messages for a relationship.",
)
async def get_inbox(
    relationship_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    include_delivered: bool = Query(
        default=False, description="Include already-delivered messages"
    ),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> InboxResponse:
    """Get pending proactive messages from inbox."""
    # Verify relationship exists and belongs to project
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    # Verify relationship's companion belongs to this project
    companion_check = await conn.fetchval(
        """
        SELECT 1 FROM companions
        WHERE id = $1 AND project_id = $2
        """,
        relationship.companion_id,
        subject.project.id,
    )
    if not companion_check:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Relationship does not belong to this project",
        )

    # Build query based on include_delivered flag
    if include_delivered:
        # Include pending and delivered, exclude acknowledged
        status_filter = "AND delivery_status IN ('pending', 'delivered')"
    else:
        # Only pending messages
        status_filter = "AND delivery_status = 'pending'"

    # Query proactive messages
    rows = await conn.fetch(
        f"""
        SELECT id, content, seq, source_behavior_key, delivery_status,
               expires_at, created_at
        FROM messages
        WHERE relationship_id = $1
          AND is_proactive = TRUE
          {status_filter}
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY created_at ASC
        LIMIT $2
        """,
        relationship_id,
        limit,
    )

    # Mark pending messages as 'delivered' - reading from inbox = delivery
    # This prevents duplicates if user later connects via WebSocket
    pending_ids = [row["id"] for row in rows if row["delivery_status"] == "pending"]
    if pending_ids:
        await conn.execute(
            """
            UPDATE messages
            SET delivery_status = 'delivered'
            WHERE id = ANY($1::uuid[])
            """,
            pending_ids,
        )
        logger.info(
            "Marked %d inbox messages as delivered: relationship=%s",
            len(pending_ids),
            relationship_id,
        )

    messages = [
        InboxMessage(
            id=row["id"],
            content=row["content"],
            seq=row["seq"],
            source_behavior_key=row["source_behavior_key"],
            delivery_status="delivered",  # Return 'delivered' since we just marked them
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )
        for row in rows
    ]

    logger.info(
        "Inbox retrieved: relationship=%s, count=%d, include_delivered=%s",
        relationship_id,
        len(messages),
        include_delivered,
    )

    return InboxResponse(messages=messages, count=len(messages))


@router.post(
    "/relationships/{relationship_id}/inbox/ack",
    response_model=AckResponse,
    summary="Acknowledge proactive messages",
    description="Mark proactive messages as acknowledged by the client.",
)
async def acknowledge_messages(
    relationship_id: UUID,
    request: AckRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> AckResponse:
    """Acknowledge receipt of proactive messages."""
    # Verify relationship exists and belongs to project
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    # Verify relationship's companion belongs to this project
    companion_check = await conn.fetchval(
        """
        SELECT 1 FROM companions
        WHERE id = $1 AND project_id = $2
        """,
        relationship.companion_id,
        subject.project.id,
    )
    if not companion_check:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Relationship does not belong to this project",
        )

    if not request.message_ids:
        return AckResponse(acknowledged=0, message_ids=[])

    # Update delivery_status to 'acknowledged' for the given message IDs
    # Only update messages that belong to this relationship and are proactive
    result = await conn.execute(
        """
        UPDATE messages
        SET delivery_status = 'acknowledged'
        WHERE id = ANY($1::uuid[])
          AND relationship_id = $2
          AND is_proactive = TRUE
          AND delivery_status IN ('pending', 'delivered')
        """,
        request.message_ids,
        relationship_id,
    )

    # Parse the result to get count (format: "UPDATE N")
    acknowledged = int(result.split()[-1]) if result else 0

    logger.info(
        "Messages acknowledged: relationship=%s, requested=%d, acknowledged=%d",
        relationship_id,
        len(request.message_ids),
        acknowledged,
    )

    return AckResponse(
        acknowledged=acknowledged,
        message_ids=[str(mid) for mid in request.message_ids[:acknowledged]],
    )
