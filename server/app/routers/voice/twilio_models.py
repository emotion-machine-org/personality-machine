# server/app/routers/voice/twilio_models.py
"""Pydantic models for Twilio voice integration."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class TwilioDialOutRequest(BaseModel):
    """Request to initiate an outbound Twilio call."""

    to_number: str = Field(
        ..., description="Phone number to call in E.164 format (e.g., +14155551234)"
    )
    companion_id: UUID = Field(..., description="Companion ID to use for the call")
    user_id: str = Field(..., description="External user ID for the relationship")
    ivr_goal: str | None = Field(
        default=None,
        description="IVR navigation goal (e.g., 'Navigate to billing department'). "
        "If not provided, defaults to reaching a human agent.",
    )

    @field_validator("to_number")
    @classmethod
    def validate_e164(cls, v: str) -> str:
        """Validate phone number is in E.164 format."""
        # E.164: + followed by 1-15 digits
        pattern = r"^\+[1-9]\d{1,14}$"
        if not re.match(pattern, v):
            raise ValueError(
                "Phone number must be in E.164 format (e.g., +14155551234). "
                "Include the + and country code."
            )
        return v


class TwilioDialOutResponse(BaseModel):
    """Response from initiating an outbound Twilio call."""

    call_sid: str = Field(..., description="Twilio Call SID")
    status: str = Field(..., description="Initial call status (e.g., queued, initiated)")
    call_id: str = Field(..., description="Internal call ID for tracking")


class TwilioCallStatus(BaseModel):
    """Twilio call status update."""

    call_sid: str
    status: Literal[
        "queued",
        "ringing",
        "in-progress",
        "completed",
        "busy",
        "failed",
        "no-answer",
        "canceled",
    ]
    duration: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class TwilioStreamMessage(BaseModel):
    """Parsed Twilio WebSocket stream message."""

    event: Literal["connected", "start", "media", "stop", "mark"]
    stream_sid: str | None = None
    call_sid: str | None = None
    account_sid: str | None = None
    # Start event specific
    custom_parameters: dict | None = None
    # Media event specific
    payload: str | None = None  # Base64 encoded audio
    timestamp: str | None = None
    # Mark event specific
    mark_name: str | None = None
