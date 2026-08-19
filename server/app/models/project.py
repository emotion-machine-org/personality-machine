from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

from pydantic import BaseModel, Field


class Project(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    slug: str | None = None
    is_default: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectSummary(Project):
    companion_count: int = 0


class ProjectApiKey(BaseModel):
    id: UUID
    project_id: UUID
    created_by: UUID | None = None
    name: str | None = None
    prefix: str
    status: str
    scopes: List[str] = Field(default_factory=lambda: ["read", "write"])
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None

    class Config:
        from_attributes = True


class ProjectApiKeyWithSecret(ProjectApiKey):
    """Response payload when creating a new API key."""

    secret: str


class ApiKeyCreateRequest(BaseModel):
    """Request payload for creating a new API key."""

    name: str | None = None


class KnowledgeIngestionJob(BaseModel):
    id: UUID
    project_id: UUID
    companion_id: UUID
    submitted_by_user: UUID | None = None
    submitted_by_key: UUID | None = None
    source_type: str
    payload_ref: str | None = None
    asset_id: UUID | None = None
    status: str
    error: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    class Config:
        from_attributes = True


class KnowledgeAsset(BaseModel):
    id: UUID
    project_id: UUID
    companion_id: UUID
    owner_user_id: UUID | None = None
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    storage_path: str
    checksum: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
