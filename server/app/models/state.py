from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from pydantic import BaseModel, Field


class CompanionUserState(BaseModel):
    """State for a companion+user pair, persists across conversations.

    This model represents the evolving relationship between a specific
    companion and a specific user across all their conversations.

    Phase 6: Simplified to profile only (renamed from app_state).
    """

    id: UUID
    companion_id: UUID
    external_user_id: str

    # Developer-controlled profile state
    profile: Dict[str, Any] = Field(default_factory=dict)

    # Optimistic locking version
    version: int = 0

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationState(BaseModel):
    """State for a single conversation, resets on new conversation.

    This model tracks conversation-specific context like topics that
    don't persist across conversations.
    """

    conversation_id: UUID

    # Topic tracking
    topic_state: Dict[str, Any] = Field(
        default_factory=lambda: {
            "current_topic": None,
            "topic_stack": [],
            "topic_history": [],
            "topic_confidence": None,
        }
    )

    # Turn counter
    turn_count: int = 0

    # Extensible metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StatePatch(BaseModel):
    """A patch to apply to state fields.

    Used by TurnEffect to specify state updates.
    """

    # Which state to patch: "profile", "session", "topic_state", "metadata"
    target: str

    # Dot-notation key path (e.g., "preferences.color")
    key: str

    # New value to set
    value: Any

    # Operation type (set, delete)
    operation: str = "set"
