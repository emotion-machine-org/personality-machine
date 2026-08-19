"""API v2 Relationship models.

Relationships represent the persistent connection between a user and a companion.
They store state, configuration, and serve as the anchor for all v2 interactions.

Phase 6: Simplified state model
- Renamed app_state -> profile
- Removed user_state/companion_state (no longer used)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Relationship(BaseModel):
    """Internal relationship model matching the database schema."""

    id: UUID
    companion_id: UUID
    external_user_id: str
    user_id: str  # Generated column, same as external_user_id

    # State field (Phase 6: renamed from app_state)
    profile: Dict[str, Any] = Field(default_factory=dict)

    # Configuration
    config: Dict[str, Any] = Field(default_factory=dict)
    context_mode: str | None = None  # 'legacy', 'layered', or None (inherit)
    context_mode_locked: bool = False

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)
    last_interaction_at: datetime | None = None
    message_count: int = 0

    # Summarization tracking
    last_summarized_at: datetime | None = None
    last_summarized_message_count: int = 0

    # Optimistic locking
    version: int = 0

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RelationshipCreate(BaseModel):
    """Request body for creating/ensuring a relationship."""

    # Optional initial state (Phase 6: renamed from app_state)
    profile: Dict[str, Any] | None = None
    config: Dict[str, Any] | None = None
    context_mode: Literal["legacy", "layered"] | None = None
    metadata: Dict[str, Any] | None = None


class RelationshipResponse(BaseModel):
    """API response for a relationship."""

    id: UUID
    companion_id: UUID
    user_id: str

    # State (Phase 6: profile only, removed user_state/companion_state)
    profile: Dict[str, Any] = Field(default_factory=dict)

    # Configuration
    config: Dict[str, Any] = Field(default_factory=dict)
    context_mode: str | None = None
    context_mode_locked: bool = False

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)
    last_interaction_at: datetime | None = None
    message_count: int = 0
    version: int = 0

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RelationshipListResponse(BaseModel):
    """Paginated list of relationships."""

    items: list[RelationshipResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


# --- Profile Models (Phase 6: renamed from App State) ---


class ProfileResponse(BaseModel):
    """Response for profile endpoints (formerly app_state)."""

    profile: Dict[str, Any]
    version: int


# --- Config Models ---


class RelationshipConfig(BaseModel):
    """Relationship-level configuration overrides.

    Merged with companion config using JSON Merge Patch (RFC 7396).
    Note: model and temperature are set at companion level, not relationship level.
    """

    # Context settings
    context_mode: Literal["legacy", "layered"] | None = None
    include_profile_in_prompt: bool | None = (
        None  # Phase 6: renamed from include_app_state_in_prompt
    )

    # Memory settings
    memory: Dict[str, Any] | None = None

    # Voice settings
    voice: Dict[str, Any] | None = None

    # Layer configuration (for layered mode)
    layers: Dict[str, Any] | None = None

    class Config:
        extra = "allow"  # Allow additional fields for extensibility


class RelationshipConfigResponse(BaseModel):
    """Response for config endpoints."""

    config: Dict[str, Any]
    version: int


class ResolvedConfigResponse(BaseModel):
    """Response for GET /relationships/{id}/config/resolved."""

    # The fully resolved config (companion + relationship merged)
    config: Dict[str, Any]

    # Source information
    companion_config: Dict[str, Any]
    relationship_overrides: Dict[str, Any]
