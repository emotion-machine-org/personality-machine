"""Pydantic models for media assets (images, etc.)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class ChatImageResponse(BaseModel):
    """Response model for image upload endpoint."""

    image_id: UUID
    description: str
    storage_url: str
    mime_type: str
    width: int | None = None
    height: int | None = None
