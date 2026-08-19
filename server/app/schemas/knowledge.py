from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ..models.project import KnowledgeAsset, KnowledgeIngestionJob


class KnowledgeAssetResponse(BaseModel):
    id: UUID
    project_id: UUID
    companion_id: UUID
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, asset: KnowledgeAsset) -> KnowledgeAssetResponse:
        return cls(
            id=asset.id,
            project_id=asset.project_id,
            companion_id=asset.companion_id,
            filename=asset.filename,
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
            status=asset.status,
            metadata=asset.metadata,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )


class KnowledgeIngestionRequest(BaseModel):
    type: str = Field(..., description="Payload type: text | markdown | json")
    content: str | None = Field(default=None, description="Inline content for ingestion")
    key: str | None = Field(default=None, description="Reference key for pre-uploaded asset")
    asset_id: UUID | None = Field(default=None, description="Previously uploaded asset identifier")


class KnowledgeJobResponse(BaseModel):
    id: UUID
    status: str
    error: str | None = None
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_model(cls, job: KnowledgeIngestionJob) -> KnowledgeJobResponse:
        return cls(
            id=job.id,
            status=job.status,
            error=job.error,
            metadata=job.metadata,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    max_results: int | None = Field(default=5, ge=1, le=50)
    filters: Dict[str, Any] | None = None
    mode: Literal["semantic", "keyword", "hybrid"] = Field(
        default="hybrid",
        description="Search strategy: semantic, keyword, or hybrid (default).",
    )


class KnowledgeSearchResult(BaseModel):
    file_id: str | None = None
    filename: str | None = None
    score: float | None = None
    text: str
    attributes: Dict[str, Any] | None = None


class KnowledgeSearchResponse(BaseModel):
    results: List[KnowledgeSearchResult]
