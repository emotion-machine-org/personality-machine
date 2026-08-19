# server/app/routers/voice/openclaw.py
"""OpenClaw integration for external brain voice processing.

This module provides integration between Emotion Machine voice and OpenClaw,
allowing OpenClaw to serve as the "brain" for voice companions. Instead of
processing messages through EM's built-in LLM, transcribed text is sent to
OpenClaw for processing, and the response is spoken back via TTS.

Architecture:
    Phone/Web → EM (STT) → OpenClaw Webhook → LLM + Tools → Callback → EM (TTS) → Phone/Web

This enables voice companions that can:
- Access OpenClaw's full tool suite (calendar, email, web search, etc.)
- Maintain persistent memory across text and voice sessions
- Execute complex multi-step tasks via voice
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/openclaw", tags=["openclaw-voice"])
logger = logging.getLogger(__name__)

# Configuration
OPENCLAW_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("OPENCLAW_REQUEST_TIMEOUT", "60"))
OPENCLAW_CALLBACK_SECRET = os.environ.get("OPENCLAW_CALLBACK_SECRET", "")
OPENCLAW_CALLBACK_MAX_AGE_SECONDS = int(os.environ.get("OPENCLAW_CALLBACK_MAX_AGE_SECONDS", "600"))

# In-memory store for pending requests (maps task_id to callback info)
# In production, consider using Redis for multi-instance deployments
_pending_requests: Dict[str, dict] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────


class OpenClawConfig(BaseModel):
    """Configuration for OpenClaw integration in a companion."""

    enabled: bool = False
    webhook_url: str = Field(
        default="",
        description="OpenClaw webhook URL (e.g., https://gateway.openclaw.ai/em-voice/webhook)",
    )
    auth_token: str = Field(
        default="",
        description="Shared secret for authenticating requests to OpenClaw",
    )
    session_key: str = Field(
        default="",
        description="OpenClaw session key for maintaining conversation context",
    )
    timeout_seconds: int = Field(
        default=60,
        description="Timeout for OpenClaw requests",
    )


class OpenClawTextRequest(BaseModel):
    """Request to process text through OpenClaw.

    Sent from EM voice pipeline to OpenClaw after STT transcription.
    """

    task_id: str = Field(description="Unique identifier for this request")
    message: str = Field(description="Transcribed user message")
    session_key: str = Field(description="OpenClaw session key for context")
    callback_url: str = Field(description="URL to POST response to")
    context: Dict[str, Any] | None = Field(
        default=None,
        description="Additional context (user_id, companion_id, etc.)",
    )


class OpenClawTextResponse(BaseModel):
    """Response from OpenClaw after processing.

    Posted back to EM's callback endpoint.
    """

    task_id: str = Field(description="Task ID from the original request")
    status: str = Field(description="'completed' or 'failed'")
    response: str | None = Field(default=None, description="Response text to speak")
    error: str | None = Field(default=None, description="Error message if failed")
    actions_taken: list[str] | None = Field(
        default=None,
        description="List of actions performed (for logging/display)",
    )


class OpenClawCallbackAck(BaseModel):
    """Acknowledgment for callback receipt."""

    task_id: str
    received: bool = True


# ──────────────────────────────────────────────────────────────────────────────
# OpenClaw Client
# ──────────────────────────────────────────────────────────────────────────────


class OpenClawClient:
    """Client for communicating with OpenClaw webhook."""

    def __init__(self, config: OpenClawConfig):
        self.config = config
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds),
            )
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def send_message(
        self,
        message: str,
        callback_url: str,
        context: Dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> str:
        """Send a message to OpenClaw for processing.

        Args:
            message: The transcribed user message
            callback_url: URL for OpenClaw to POST the response to
            context: Optional additional context

        Returns:
            task_id for tracking the request

        Raises:
            HTTPException: If the request fails
        """
        if not self.config.enabled or not self.config.webhook_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenClaw integration not configured",
            )

        resolved_task_id = task_id or str(uuid4())
        request = OpenClawTextRequest(
            task_id=resolved_task_id,
            message=message,
            session_key=self.config.session_key,
            callback_url=callback_url,
            context=context,
        )

        client = await self._get_client()
        headers = {
            "Content-Type": "application/json",
        }
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"

        try:
            logger.info(
                f"[OPENCLAW] Sending message to OpenClaw: task_id={resolved_task_id}, "
                f"message={message[:50]}..."
            )

            response = await client.post(
                self.config.webhook_url,
                json=request.model_dump(),
                headers=headers,
            )

            if response.status_code not in (200, 202):
                logger.error(
                    f"[OPENCLAW] Request failed: status={response.status_code}, "
                    f"body={response.text}"
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"OpenClaw request failed: {response.status_code}",
                )

            logger.info(f"[OPENCLAW] Request accepted: task_id={resolved_task_id}")
            return resolved_task_id

        except httpx.TimeoutException:
            logger.error(f"[OPENCLAW] Request timed out: task_id={resolved_task_id}")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="OpenClaw request timed out",
            )
        except httpx.RequestError as e:
            logger.error(f"[OPENCLAW] Request error: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenClaw request error: {e!s}",
            )


# ──────────────────────────────────────────────────────────────────────────────
# Callback Endpoint
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class PendingRequest:
    """State for a pending OpenClaw request."""

    task_id: str
    created_at: datetime
    response_future: asyncio.Future
    companion_id: UUID | None = None
    relationship_id: UUID | None = None


# Store pending requests with their futures for async/await pattern
_pending_futures: Dict[str, PendingRequest] = {}
_warned_missing_callback_secret = False


def create_pending_request(
    task_id: str,
    companion_id: UUID | None = None,
    relationship_id: UUID | None = None,
) -> asyncio.Future:
    """Create a pending request and return a future to await."""
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    _pending_futures[task_id] = PendingRequest(
        task_id=task_id,
        created_at=datetime.now(UTC),
        response_future=future,
        companion_id=companion_id,
        relationship_id=relationship_id,
    )

    return future


def resolve_pending_request(task_id: str, response: OpenClawTextResponse) -> bool:
    """Resolve a pending request with the response."""
    pending = _pending_futures.pop(task_id, None)
    if pending and not pending.response_future.done():
        pending.response_future.set_result(response)
        return True
    return False


def _build_callback_signature(task_id: str, timestamp: int) -> str:
    payload = f"{task_id}.{timestamp}".encode()
    secret = OPENCLAW_CALLBACK_SECRET.encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _build_signed_callback_url(callback_base_url: str, task_id: str) -> str:
    callback_url = f"{callback_base_url}/openclaw/callback"
    if not OPENCLAW_CALLBACK_SECRET:
        return callback_url
    ts = int(datetime.now(UTC).timestamp())
    sig = _build_callback_signature(task_id, ts)
    return f"{callback_url}?ts={ts}&sig={sig}"


def _verify_callback_signature(task_id: str, ts: int | None, sig: str | None) -> None:
    global _warned_missing_callback_secret

    if not OPENCLAW_CALLBACK_SECRET:
        if not _warned_missing_callback_secret:
            logger.warning(
                "[OPENCLAW] OPENCLAW_CALLBACK_SECRET is not configured; "
                "callback signature verification is disabled"
            )
            _warned_missing_callback_secret = True
        return

    if ts is None or not sig:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing callback signature",
        )

    now_ts = int(datetime.now(UTC).timestamp())
    if abs(now_ts - ts) > OPENCLAW_CALLBACK_MAX_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Callback signature expired",
        )

    expected = _build_callback_signature(task_id, ts)
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid callback signature",
        )


@router.post(
    "/callback",
    response_model=OpenClawCallbackAck,
    summary="Callback endpoint for OpenClaw responses",
    description="Receives processed responses from OpenClaw.",
)
async def openclaw_callback(
    response: OpenClawTextResponse,
    ts: int | None = Query(None),
    sig: str | None = Query(None),
) -> OpenClawCallbackAck:
    """Receive a response from OpenClaw.

    This endpoint is called by OpenClaw after processing a message.
    It resolves the pending future AND writes to hot_context for Fast Brain polling.
    """
    from .voice_workspace import HotContextS3

    _verify_callback_signature(response.task_id, ts, sig)

    # Write to hot_context (S3) for Fast Brain polling
    pending = _pending_futures.get(response.task_id)
    relationship_id = pending.relationship_id if pending else None
    if relationship_id:
        try:
            hot_context = HotContextS3(relationship_id)
            if response.status == "completed" and response.response:
                hot_context.log_done(response.task_id, response.response)
            elif response.status == "failed":
                hot_context.log_fail(response.task_id, response.error or "Unknown error")
        except Exception as e:
            logger.warning(f"[OPENCLAW] Failed to write to hot_context (S3): {e}")
    else:
        logger.warning(
            f"[OPENCLAW] No relationship_id for task_id={response.task_id}; "
            "skipping hot_context write"
        )
    logger.info(
        f"[OPENCLAW] Received callback: task_id={response.task_id}, "
        f"status={response.status}, response_len={len(response.response or '')}"
    )

    resolved = resolve_pending_request(response.task_id, response)

    if not resolved:
        logger.warning(
            f"[OPENCLAW] No pending request for task_id={response.task_id} (may have timed out)"
        )

    return OpenClawCallbackAck(task_id=response.task_id, received=True)


# ──────────────────────────────────────────────────────────────────────────────
# Voice Pipeline Integration
# ──────────────────────────────────────────────────────────────────────────────


async def process_with_openclaw(
    client: OpenClawClient,
    message: str,
    callback_base_url: str,
    context: Dict[str, Any] | None = None,
    timeout_seconds: int = 60,
) -> str:
    """Process a message through OpenClaw and wait for the response.

    This is the main integration point for the voice pipeline. Call this
    instead of the built-in LLM when OpenClaw mode is enabled.

    Args:
        client: OpenClaw client instance
        message: User's transcribed message
        callback_base_url: Base URL for the callback (e.g., https://api.emotionmachine.ai)
        context: Additional context to send
        timeout_seconds: How long to wait for response

    Returns:
        Response text to speak

    Raises:
        HTTPException: If processing fails or times out
    """
    task_id = str(uuid4())
    callback_url = _build_signed_callback_url(callback_base_url, task_id)

    # Send request and create pending future
    task_id = await client.send_message(
        task_id=task_id,
        message=message,
        callback_url=callback_url,
        context=context,
    )

    future = create_pending_request(
        task_id=task_id,
        companion_id=context.get("companion_id") if context else None,
        relationship_id=context.get("relationship_id") if context else None,
    )

    try:
        # Wait for callback with timeout
        response: OpenClawTextResponse = await asyncio.wait_for(
            future,
            timeout=timeout_seconds,
        )

        if response.status == "failed":
            logger.error(f"[OPENCLAW] Processing failed: {response.error}")
            return "I'm sorry, I encountered an error processing your request."

        return response.response or "I processed your request."

    except TimeoutError:
        # Clean up pending request
        _pending_futures.pop(task_id, None)
        logger.error(f"[OPENCLAW] Request timed out: task_id={task_id}")
        return "I'm sorry, the request timed out. Please try again."


# ──────────────────────────────────────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────────────────────────────────────


async def cleanup_stale_requests(max_age_seconds: int = 300) -> int:
    """Clean up stale pending requests.

    Call periodically to prevent memory leaks from abandoned requests.

    Returns:
        Number of requests cleaned up
    """
    now = datetime.now(UTC)
    stale_ids = []

    for task_id, pending in _pending_futures.items():
        age = (now - pending.created_at).total_seconds()
        if age > max_age_seconds:
            stale_ids.append(task_id)

    for task_id in stale_ids:
        pending = _pending_futures.pop(task_id, None)
        if pending and not pending.response_future.done():
            pending.response_future.cancel()

    if stale_ids:
        logger.info(f"[OPENCLAW] Cleaned up {len(stale_ids)} stale requests")

    return len(stale_ids)
