from __future__ import annotations

from datetime import datetime
from typing import List
from uuid import UUID

from pydantic import BaseModel, Field, confloat, conint


class MemoryItem(BaseModel):
    id: UUID
    companion_id: UUID
    content: str
    created_at: datetime
    importance: float = Field(ge=0.0, le=1.0)
    weight_user: float = Field(default=1.0)
    modality: str = Field(default="text")
    last_accessed_at: datetime
    commentary: str | None = None
    conversation_id: UUID | None = None
    sender_type: str = Field(default="user")
    external_user_id: str | None = None
    message_id: UUID | None = None
    is_core: bool = False

    # Optional ranking fields (populated by search)
    similarity: float | None = None
    score: float | None = None


class MemoryCreate(BaseModel):
    content: str | None = None
    message_id: UUID | None = None
    importance: confloat(ge=0.0, le=1.0) | None = None
    weight_user: float | None = 1.0
    modality: str | None = "text"
    commentary: str | None = None
    conversation_id: UUID | None = None
    sender_type: str | None = "user"
    external_user_id: str | None = None
    is_core: bool | None = False


class MemoryUpdate(BaseModel):
    importance: confloat(ge=0.0, le=1.0) | None = None
    commentary: str | None = None
    content: str | None = None


class MemoryQuery(BaseModel):
    query: str
    top_k: conint(ge=1, le=1000) | None = None
    min_saliency: float | None = None
    conversation_id: UUID | None = None
    external_user_id: str | None = None
    sender_type: str | None = None
    modality: str | None = None


class MemorySearchResponse(BaseModel):
    items: List[MemoryItem]


class MemoryListResponse(BaseModel):
    items: List[MemoryItem]
    total_count: int


class MemoryStats(BaseModel):
    total: int
    avg_importance: float | None = None
    avg_weight_user: float | None = None
    by_sender_type: dict[str, int] = Field(default_factory=dict)
    by_modality: dict[str, int] = Field(default_factory=dict)
