"""Memory V2 API endpoints for scratchpad management.

Provides CRUD operations for memory v2 entries (scratchpad) per relationship.
"""

from __future__ import annotations

import logging
from typing import List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...db import get_db
from ...repositories.memory_v2_repository import MemoryV2Repository

router = APIRouter(prefix="/v2", tags=["v2-memory"])
logger = logging.getLogger(__name__)


# =============================================================================
# Request/Response Models
# =============================================================================


class MemoryEntryResponse(BaseModel):
    """Memory entry response."""

    id: UUID
    content: str
    type: str | None = None
    created_at: str
    updated_at: str


class MemoryEntriesResponse(BaseModel):
    """List of memory entries response."""

    entries: List[MemoryEntryResponse]
    count: int
    max_entries: int = 100


class CreateMemoryEntryRequest(BaseModel):
    """Request to create a memory entry."""

    content: str = Field(..., min_length=1, max_length=2000)
    type: str | None = Field(
        None,
        max_length=50,
        description="Entry type: identity, preference, goal, event, relationship, other",
    )


class UpdateMemoryEntryRequest(BaseModel):
    """Request to update a memory entry."""

    content: str = Field(..., min_length=1, max_length=2000)
    type: str | None = Field(None, max_length=50)


# =============================================================================
# Helper Functions
# =============================================================================


async def _verify_relationship_access(
    conn: asyncpg.Connection,
    relationship_id: UUID,
    project_id: UUID,
) -> dict:
    """Verify the relationship exists and belongs to a companion in the project.

    Returns the relationship row with config.
    """
    row = await conn.fetchrow(
        """
        SELECT r.id, r.config
        FROM relationships r
        JOIN companions c ON r.companion_id = c.id
        WHERE r.id = $1 AND c.project_id = $2
        """,
        relationship_id,
        project_id,
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )
    return dict(row)


def _entry_to_response(entry: dict) -> MemoryEntryResponse:
    """Convert entry dict to response model."""
    return MemoryEntryResponse(
        id=entry["id"],
        content=entry["content"],
        type=entry.get("type"),
        created_at=entry["created_at"].isoformat(),
        updated_at=entry["updated_at"].isoformat(),
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "/relationships/{relationship_id}/memory",
    response_model=MemoryEntriesResponse,
    summary="List memory entries",
    description="Get all memory entries for a relationship (newest first).",
)
async def list_memory_entries(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> MemoryEntriesResponse:
    """Get all memory entries for a relationship (newest first)."""
    rel = await _verify_relationship_access(conn, relationship_id, subject.project.id)

    # Get max_entries from config
    config = rel.get("config") or {}
    max_entries = config.get("memory", {}).get("max_entries", 100)

    entries = await MemoryV2Repository.list_entries(conn, relationship_id, limit=max_entries)

    return MemoryEntriesResponse(
        entries=[_entry_to_response(e) for e in entries],
        count=len(entries),
        max_entries=max_entries,
    )


@router.get(
    "/relationships/{relationship_id}/memory/{entry_id}",
    response_model=MemoryEntryResponse,
    summary="Get memory entry",
    description="Get a specific memory entry.",
)
async def get_memory_entry(
    relationship_id: UUID,
    entry_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> MemoryEntryResponse:
    """Get a specific memory entry."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    entry = await MemoryV2Repository.get_entry(conn, entry_id, relationship_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory entry {entry_id} not found",
        )

    return _entry_to_response(entry)


@router.post(
    "/relationships/{relationship_id}/memory",
    response_model=MemoryEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create memory entry",
    description="Manually add a memory entry.",
)
async def create_memory_entry(
    relationship_id: UUID,
    body: CreateMemoryEntryRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> MemoryEntryResponse:
    """Manually add a memory entry."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    entry = await MemoryV2Repository.create_entry(
        conn,
        relationship_id,
        body.content,
        body.type,
    )

    logger.info(f"Memory entry created for relationship {relationship_id}: {entry['id']}")

    return _entry_to_response(entry)


@router.patch(
    "/relationships/{relationship_id}/memory/{entry_id}",
    response_model=MemoryEntryResponse,
    summary="Update memory entry",
    description="Update an existing memory entry.",
)
async def update_memory_entry(
    relationship_id: UUID,
    entry_id: UUID,
    body: UpdateMemoryEntryRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> MemoryEntryResponse:
    """Update an existing memory entry."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    entry = await MemoryV2Repository.update_entry(
        conn,
        entry_id,
        relationship_id,
        body.content,
        body.type,
    )

    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory entry {entry_id} not found",
        )

    logger.info(f"Memory entry updated: {entry_id}")

    return _entry_to_response(entry)


@router.delete(
    "/relationships/{relationship_id}/memory/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete memory entry",
    description="Delete a memory entry.",
)
async def delete_memory_entry(
    relationship_id: UUID,
    entry_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    """Delete a memory entry."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    deleted = await MemoryV2Repository.delete_entry(conn, entry_id, relationship_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory entry {entry_id} not found",
        )

    logger.info(f"Memory entry deleted: {entry_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/relationships/{relationship_id}/memory",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear all memory",
    description="Clear all memory entries for a relationship.",
)
async def clear_memory(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    """Clear all memory entries for a relationship."""
    await _verify_relationship_access(conn, relationship_id, subject.project.id)

    count = await MemoryV2Repository.clear_all(conn, relationship_id)

    logger.info(f"Cleared {count} memory entries for relationship {relationship_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
