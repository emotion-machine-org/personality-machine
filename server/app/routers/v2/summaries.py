"""API v2 Summaries Router.

Provides endpoints for managing relationship conversation summaries.
Summaries are incremental and versioned, built automatically when
message counts cross configured thresholds.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...db import get_db
from ...repositories.companion import CompanionRepository
from ...repositories.relationship_repository import RelationshipRepository
from ...repositories.summary_repository import SummaryRepository

router = APIRouter(prefix="/v2", tags=["v2-summaries"])
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Response Models
# -----------------------------------------------------------------------------


class SummaryResponse(BaseModel):
    """A single conversation summary."""

    id: UUID
    relationship_id: UUID
    content: str
    version: int
    messages_start: int
    messages_end: int
    message_count: int
    model: str | None = None
    created_at: datetime


class SummaryListResponse(BaseModel):
    """List of summaries for a relationship."""

    items: List[SummaryResponse]
    total: int


class TriggerSummarizationResponse(BaseModel):
    """Response for manual summarization trigger."""

    triggered: bool
    message: str
    version: int | None = None
    messages_start: int | None = None
    messages_end: int | None = None


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


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


async def _get_relationship(
    conn: asyncpg.Connection,
    companion_id: UUID,
    user_id: str,
) -> Any:
    """Get relationship, raising 404 if not found."""
    relationship = await RelationshipRepository.get_by_companion_and_user(
        conn, companion_id=companion_id, user_id=user_id
    )
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship not found for user {user_id}",
        )
    return relationship


def _summary_to_response(summary: Dict[str, Any]) -> SummaryResponse:
    """Convert summary dict to API response."""
    return SummaryResponse(
        id=summary["id"],
        relationship_id=summary["relationship_id"],
        content=summary["content"],
        version=summary["version"],
        messages_start=summary["messages_start"],
        messages_end=summary["messages_end"],
        message_count=summary["message_count"],
        model=summary.get("model"),
        created_at=summary["created_at"],
    )


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------


@router.get(
    "/companions/{companion_id}/relationships/{user_id}/summaries",
    response_model=SummaryListResponse,
    summary="List conversation summaries",
    description="List all summaries for a relationship, newest first.",
)
async def list_summaries(
    companion_id: UUID,
    user_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SummaryListResponse:
    """List all summaries for a relationship."""
    await _verify_companion_access(conn, companion_id, subject.project.id)
    relationship = await _get_relationship(conn, companion_id, user_id)

    summaries = await SummaryRepository.list_summaries(conn, relationship.id, limit=limit)

    return SummaryListResponse(
        items=[_summary_to_response(s) for s in summaries],
        total=len(summaries),
    )


@router.get(
    "/companions/{companion_id}/relationships/{user_id}/summaries/latest",
    response_model=SummaryResponse,
    summary="Get latest summary",
    description="Get the most recent conversation summary for a relationship.",
)
async def get_latest_summary(
    companion_id: UUID,
    user_id: str,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> SummaryResponse:
    """Get the latest summary for a relationship."""
    await _verify_companion_access(conn, companion_id, subject.project.id)
    relationship = await _get_relationship(conn, companion_id, user_id)

    summary = await SummaryRepository.get_latest_summary(conn, relationship.id)
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No summary available for this relationship",
        )

    return _summary_to_response(summary)


@router.post(
    "/companions/{companion_id}/relationships/{user_id}/summaries/trigger",
    response_model=TriggerSummarizationResponse,
    summary="Trigger summarization",
    description="Manually trigger summarization for a relationship if eligible.",
)
async def trigger_summarization(
    companion_id: UUID,
    user_id: str,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> TriggerSummarizationResponse:
    """Manually trigger summarization for a relationship.

    Triggers summarization if the relationship has enough messages
    that haven't been summarized yet.
    """
    await _verify_companion_access(conn, companion_id, subject.project.id)
    relationship = await _get_relationship(conn, companion_id, user_id)

    # Get companion config for message_limit
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    message_limit = (
        companion.config.context.message_limit
        if hasattr(companion.config, "context") and companion.config.context
        else 200
    )

    # Check if we have enough messages to summarize
    message_count = relationship.message_count
    last_summarized = relationship.last_summarized_message_count or 0

    # Calculate if a new summary is needed
    current_window = message_count // message_limit
    last_window = last_summarized // message_limit

    if current_window <= last_window or message_count < message_limit:
        return TriggerSummarizationResponse(
            triggered=False,
            message=f"Not enough new messages to summarize. "
            f"Current: {message_count}, last summarized: {last_summarized}, "
            f"threshold: {message_limit}",
        )

    # Get previous summary info
    previous_summary = None
    version = 1
    latest = await SummaryRepository.get_latest_summary(conn, relationship.id)
    if latest:
        previous_summary = latest["content"]
        version = latest["version"] + 1

    # Calculate message range
    messages_start = last_summarized + 1 if last_summarized > 0 else 1
    messages_end = (message_count // message_limit) * message_limit

    # Fetch messages
    messages = await RelationshipRepository.get_messages_for_summarization(
        conn, relationship.id, messages_start, messages_end
    )

    if not messages:
        return TriggerSummarizationResponse(
            triggered=False,
            message=f"No messages found in range {messages_start}-{messages_end}",
        )

    # Dispatch to Modal
    try:
        import os

        import modal

        modal_env = os.getenv("MODAL_ENVIRONMENT", "staging")
        fn = modal.Function.from_name(
            "em-memory-v2", "summarize_relationship", environment_name=modal_env
        )
        fn.spawn(
            {
                "relationship_id": str(relationship.id),
                "previous_summary": previous_summary,
                "messages": messages,
                "messages_start": messages_start,
                "messages_end": messages_end,
                "version": version,
            }
        )

        logger.info(
            f"Manual summarization triggered for relationship={relationship.id}, "
            f"version={version}, messages={messages_start}-{messages_end}"
        )

        return TriggerSummarizationResponse(
            triggered=True,
            message=f"Summarization job dispatched for version {version}",
            version=version,
            messages_start=messages_start,
            messages_end=messages_end,
        )

    except Exception as e:
        logger.error(f"Failed to trigger summarization: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger summarization: {e!s}",
        ) from e
