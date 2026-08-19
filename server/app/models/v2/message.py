"""API v2 Message models.

Messages in v2 are associated with relationships, not conversations.
Session_id is optional for bounded interactions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TurnConfig(BaseModel):
    """Per-turn configuration overrides.

    Highest priority in the config hierarchy:
    Turn Config > Relationship Config > Companion Config
    """

    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(None, gt=0)
    model: str | None = None

    # Tool/behavior control
    tools: List[str] | None = None
    disable_behaviors: bool | None = None

    # Debug options
    trace: bool | None = None

    class Config:
        extra = "allow"


class MessageCreate(BaseModel):
    """Request body for sending a message."""

    content: str = Field(..., min_length=1, max_length=32000)

    # Optional session context
    session_id: UUID | None = None

    # Per-turn config overrides
    config: TurnConfig | None = None

    # Image context (Phase 3+)
    image_ids: List[UUID] | None = None


class MessageResponse(BaseModel):
    """Response for a completed message turn."""

    # Message identity
    id: UUID
    relationship_id: UUID
    session_id: UUID | None = None
    seq: int | None = None

    # Content
    role: Literal["user", "assistant"]
    content: str

    # Metadata
    created_at: datetime
    build_ms: int | None = None
    input_modality: str | None = None

    # Proactive message fields
    is_proactive: bool = False
    delivery_status: str | None = None

    class Config:
        from_attributes = True


class SendMessageResponse(BaseModel):
    """Response for POST /relationships/{id}/messages (non-streaming)."""

    # The relationship context
    relationship_id: UUID

    # The assistant's response message
    message: MessageResponse

    # Optional trace info (if config.trace=True)
    trace: Dict[str, Any] | None = None


class MessageListResponse(BaseModel):
    """Response for history endpoints returning sequenced relationship messages."""

    relationship_id: UUID
    messages: List[MessageResponse]
    limit: int
    has_more: bool
    oldest_seq: int | None = None
    newest_seq: int | None = None


class CompositeSendResponse(BaseModel):
    """Response for composite endpoint POST /companions/{cid}/relationships/{uid}/messages.

    This endpoint ensures the relationship exists and sends the message in one call.
    """

    # Relationship info (created or existing)
    relationship_id: UUID
    relationship_created: bool = False

    # The assistant's response
    message: MessageResponse

    # Optional trace
    trace: Dict[str, Any] | None = None


# --- Streaming Event Models ---


class StreamEvent(BaseModel):
    """Base model for SSE stream events."""

    seq: int | None = None  # Per-relationship sequence (null for ephemeral)
    timestamp: datetime
    turn_id: str | None = None
    type: str
    data: Dict[str, Any]


class AckEvent(BaseModel):
    """Acknowledgment event for received message."""

    client_message_id: str | None = None
    message_id: UUID
    turn_id: str


class DeltaEvent(BaseModel):
    """Streaming token delta."""

    content: str
    role: str = "assistant"


class StatusEvent(BaseModel):
    """Status update during processing."""

    stage: str  # 'retrieving', 'thinking', etc.
    phase: Literal["start", "end"]
    meta: Dict[str, Any] | None = None


# --- Inbox Models ---


class InboxMessage(BaseModel):
    """A proactive message in the inbox."""

    id: UUID
    content: str
    source_behavior_key: str | None = None
    created_at: datetime
    expires_at: datetime | None = None


class InboxResponse(BaseModel):
    """Response for GET /relationships/{id}/inbox."""

    messages: List[InboxMessage]
    count: int


class InboxAckRequest(BaseModel):
    """Request body for POST /relationships/{id}/inbox/ack."""

    message_ids: List[UUID]


# --- WebSocket Models ---


class WsTokenResponse(BaseModel):
    """Response for POST /companions/{cid}/relationships/{uid}/ws-token."""

    token: str
    relationship_id: UUID
    expires_in: int = 3600  # seconds


class WsUserMessage(BaseModel):
    """Client-to-server message via WebSocket."""

    type: Literal["user_message"] = "user_message"
    client_message_id: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1, max_length=32000)
    session_id: UUID | None = None
    config: TurnConfig | None = None


class WsPing(BaseModel):
    """Client heartbeat ping."""

    type: Literal["ping"] = "ping"


class WsCancel(BaseModel):
    """Client cancellation request."""

    type: Literal["cancel"] = "cancel"


class WsRefreshToken(BaseModel):
    """Client token refresh request."""

    type: Literal["refresh_token"] = "refresh_token"
    token: str


# WebSocket event types that can be sent to client
WsEventType = Literal[
    "connected",
    "ack",
    "status",
    "delta",
    "message",
    "proactive",
    "state_updated",
    "cancelled",
    "error",
    "heartbeat",
    "pong",
    "token_expiring",
]
