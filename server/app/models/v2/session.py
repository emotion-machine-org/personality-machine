"""API v2 Session models.

Sessions represent optional bounded interactions within relationships.
Use cases: billing, reporting, context isolation.

Default behavior (no sessions) = continuous chat with memory + state continuity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Session(BaseModel):
    """Internal session model matching the database schema."""

    id: UUID
    relationship_id: UUID
    type: str | None = None  # "coaching", "therapy", custom
    status: Literal["active", "ended"]
    isolated: bool = False
    state: Dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    created_at: datetime
    ended_at: datetime | None = None
    updated_at: datetime

    class Config:
        from_attributes = True


class SessionCreate(BaseModel):
    """Request body for creating a session."""

    type: str | None = Field(
        None,
        description="Session type label: 'coaching', 'therapy', or custom",
        max_length=100,
    )
    isolated: bool = Field(
        False,
        description="If true: no prior history loaded, memory read-only, no state writes",
    )


class SessionResponse(BaseModel):
    """API response for a session."""

    id: UUID
    relationship_id: UUID
    type: str | None = None
    status: Literal["active", "ended"]
    isolated: bool
    state: Dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    created_at: datetime
    ended_at: datetime | None = None

    class Config:
        from_attributes = True


class SessionListResponse(BaseModel):
    """Paginated list of sessions."""

    sessions: List[SessionResponse]
    next_cursor: str | None = None
    total: int


class SessionEndResponse(BaseModel):
    """Response for POST /sessions/{id}/end."""

    id: UUID
    status: Literal["ended"]
    summary: str | None = None
    ended_at: datetime


class SessionStateResponse(BaseModel):
    """Response for session state operations."""

    id: UUID
    state: Dict[str, Any]


class SessionStatePatch(BaseModel):
    """Request body for PATCH /sessions/{id}/state."""

    changes: Dict[str, Any] = Field(
        ...,
        description="JSON merge patch to apply to session state",
    )
