from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from pydantic import BaseModel, Field


class Job(BaseModel):
    """Unified job model for all async operations."""

    id: UUID
    job_type: str
    status: str  # pending, claimed, running, completed, failed, cancelled

    # Scheduling & priority
    priority: int = 0
    run_at: datetime | None = None

    # Retry logic
    attempts: int = 0
    max_attempts: int = 3

    # Scoping (all optional)
    project_id: UUID | None = None
    companion_id: UUID | None = None
    conversation_id: UUID | None = None
    owner_id: UUID | None = None
    external_user_id: str | None = None

    # Action-specific
    action_key: str | None = None

    # Payload
    params: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] | None = None

    # Error tracking
    error: str | None = None
    error_stack: str | None = None

    # Progress tracking
    total_items: int | None = None
    processed_count: int = 0

    # Timestamps
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Worker tracking
    worker_id: str | None = None

    class Config:
        from_attributes = True


class JobCreate(BaseModel):
    """Parameters for creating a new job."""

    job_type: str
    params: Dict[str, Any] = Field(default_factory=dict)

    # Optional scoping
    project_id: UUID | None = None
    companion_id: UUID | None = None
    conversation_id: UUID | None = None
    owner_id: UUID | None = None
    external_user_id: str | None = None

    # Action-specific
    action_key: str | None = None

    # Scheduling
    run_at: datetime | None = None
    priority: int = 0
    max_attempts: int = 3

    # Progress tracking (optional)
    total_items: int | None = None
