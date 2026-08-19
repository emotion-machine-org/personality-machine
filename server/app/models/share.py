from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict
from uuid import UUID

from pydantic import BaseModel


class ShareStatus(str, Enum):
    """Lifecycle states for a companion share."""

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"


class CompanionShare(BaseModel):
    """Complete share record returned from the database."""

    id: UUID
    companion_id: UUID
    owner_id: UUID
    version_id: UUID | None
    slug: str
    status: ShareStatus
    allow_text: bool
    allow_voice: bool
    require_auth: bool
    expose_status_events: bool
    config_snapshot: Dict[str, Any] | None
    display_name: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    disabled_at: datetime | None
    total_sessions: int
    total_messages: int
    total_voice_sessions: int
    last_activity_at: datetime | None

    class Config:
        from_attributes = True


class CompanionShareAnalytics(BaseModel):
    """Aggregated usage metrics for a share."""

    share_id: UUID
    sessions: int
    total_messages: int
    total_voice_sessions: int
    last_activity_at: datetime | None
