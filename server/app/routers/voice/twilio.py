# server/app/routers/voice/twilio.py
"""Twilio voice integration for outbound calls.

This module provides endpoints for initiating outbound Twilio calls and handling
the Twilio media stream WebSocket connection for voice conversations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import Response
from pipecat.frames.frames import TranscriptionMessage
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pydantic import BaseModel
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...db import get_db, get_db_connection
from ...repositories.companion import CompanionRepository
from ...repositories.relationship_repository import RelationshipRepository
from .pipeline import create_default_voice_config, normalize_voice_config
from .providers import get_voice_id
from .services import build_llm_service, build_stt_service, build_tts_service
from .twilio_models import TwilioDialOutRequest, TwilioDialOutResponse

router = APIRouter(prefix="/twilio", tags=["twilio-voice"])
logger = logging.getLogger(__name__)

# Twilio configuration
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
TWILIO_VALIDATE_SIGNATURE = os.environ.get("TWILIO_VALIDATE_SIGNATURE", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Audio configuration for Twilio (8kHz mulaw)
TWILIO_SAMPLE_RATE = 8000

# In-memory store for pending calls (maps call_id to call metadata)
# In production, consider using Redis for multi-instance deployments
_pending_calls: Dict[str, dict] = {}

# In-memory store for call status (maps call_sid to status info)
# Twilio status values: queued, ringing, in-progress, completed, busy, no-answer, canceled, failed
_call_status: Dict[str, dict] = {}
_call_auth_tokens: Dict[str, str] = {}


async def _persist_twilio_call_event(
    *,
    call_sid: str,
    event_type: str,
    status: str | None = None,
    call_id: str | None = None,
    companion_id: str | UUID | None = None,
    relationship_id: str | UUID | None = None,
    user_id: str | None = None,
    direction: str | None = None,
    from_number: str | None = None,
    to_number: str | None = None,
    duration: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist Twilio call state and event to Postgres."""
    if not call_sid:
        return

    companion_uuid: UUID | None = None
    relationship_uuid: UUID | None = None
    try:
        if companion_id:
            companion_uuid = UUID(str(companion_id))
    except Exception:
        companion_uuid = None
    try:
        if relationship_id:
            relationship_uuid = UUID(str(relationship_id))
    except Exception:
        relationship_uuid = None

    async with get_db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO twilio_calls (
                call_sid, call_id, companion_id, relationship_id, external_user_id,
                direction, from_number, to_number, status, duration_seconds, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            ON CONFLICT (call_sid) DO UPDATE
            SET
                call_id = COALESCE(EXCLUDED.call_id, twilio_calls.call_id),
                companion_id = COALESCE(EXCLUDED.companion_id, twilio_calls.companion_id),
                relationship_id = COALESCE(EXCLUDED.relationship_id, twilio_calls.relationship_id),
                external_user_id = COALESCE(EXCLUDED.external_user_id, twilio_calls.external_user_id),
                direction = COALESCE(EXCLUDED.direction, twilio_calls.direction),
                from_number = COALESCE(EXCLUDED.from_number, twilio_calls.from_number),
                to_number = COALESCE(EXCLUDED.to_number, twilio_calls.to_number),
                status = COALESCE(EXCLUDED.status, twilio_calls.status),
                duration_seconds = COALESCE(EXCLUDED.duration_seconds, twilio_calls.duration_seconds),
                metadata = twilio_calls.metadata || EXCLUDED.metadata,
                updated_at = NOW()
            """,
            call_sid,
            call_id,
            companion_uuid,
            relationship_uuid,
            user_id,
            direction,
            from_number,
            to_number,
            status,
            duration,
            json.dumps(payload or {}),
        )

        await conn.execute(
            """
            INSERT INTO twilio_call_events (call_sid, event_type, status, payload)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            call_sid,
            event_type,
            status,
            json.dumps(payload or {}),
        )


def _schedule_twilio_call_event(**kwargs) -> None:
    """Schedule best-effort async persistence of Twilio call events."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    task = loop.create_task(_persist_twilio_call_event(**kwargs))

    def _done(t: asyncio.Task) -> None:
        try:
            exc = t.exception()
            if exc:
                logger.warning(f"[TWILIO_STATE] Async persistence failed: {exc}")
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.warning(f"[TWILIO_STATE] Async persistence callback error: {err}")

    task.add_done_callback(_done)


class TwilioStatusResponse(BaseModel):
    """Response for call status check."""

    call_sid: str
    status: str
    duration: int | None = None
    timestamp: str


def _get_twilio_client() -> TwilioClient:
    """Get configured Twilio client."""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio credentials not configured",
        )
    return TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def _get_twilio_client_for(account_sid: str, auth_token: str) -> TwilioClient:
    """Get Twilio client for provided credentials."""
    if not account_sid or not auth_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio credentials not configured",
        )
    return TwilioClient(account_sid, auth_token)


def _resolve_twilio_credentials(companion=None) -> tuple[str, str, str] | None:
    """Resolve Twilio credentials, preferring per-companion config."""
    try:
        twilio_cfg = None
        if companion and companion.config and companion.config.voice:
            twilio_cfg = companion.config.voice.twilio
        if (
            twilio_cfg
            and twilio_cfg.account_sid
            and twilio_cfg.auth_token
            and twilio_cfg.phone_number
        ):
            return (
                twilio_cfg.account_sid,
                twilio_cfg.auth_token,
                twilio_cfg.phone_number,
            )
    except Exception:
        pass

    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER:
        return (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER)

    return None


def _get_public_base_url() -> str:
    """Get the public base URL for Twilio callbacks.

    In development, this should be an ngrok URL.
    In production, this should be the public API URL.
    """
    # Check for explicit ngrok/public URL override
    public_url = os.environ.get("TWILIO_PUBLIC_URL")
    if public_url:
        return public_url.rstrip("/")

    # Fall back to PUBLIC_HOST
    host = os.environ.get("PUBLIC_HOST", "localhost:8100")
    scheme = "https" if os.environ.get("ENV") == "prod" else "http"
    return f"{scheme}://{host}"


def _request_url_for_signature(request: Request) -> str:
    """Build request URL in a proxy-safe way for Twilio signature validation."""
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()

    scheme = forwarded_proto or request.url.scheme
    host = forwarded_host or request.headers.get("host") or request.url.netloc
    path = request.url.path
    query = request.url.query
    return f"{scheme}://{host}{path}" + (f"?{query}" if query else "")


def _validate_twilio_signature(
    request: Request,
    params: dict[str, str],
    auth_tokens: list[str],
) -> None:
    """Validate Twilio webhook signature against one or more auth tokens."""
    if not TWILIO_VALIDATE_SIGNATURE:
        return

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Twilio signature")

    url = _request_url_for_signature(request)
    unique_tokens = [tok for tok in dict.fromkeys(auth_tokens) if tok]

    if not unique_tokens:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No Twilio auth token available to validate signature",
        )

    for token in unique_tokens:
        validator = RequestValidator(token)
        if validator.validate(url, params, signature):
            return

    raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid Twilio signature")


# ──────────────────────────────────────────────────────────────────────────────
# Inbound Call Endpoint
# ──────────────────────────────────────────────────────────────────────────────

# Default companion for inbound calls (Can)
INBOUND_COMPANION_ID = os.environ.get("TWILIO_INBOUND_COMPANION_ID", "")
INBOUND_API_KEY_ID = os.environ.get("TWILIO_INBOUND_API_KEY_ID", "")


@router.post(
    "/inbound",
    summary="Handle inbound Twilio call",
    description="Webhook for incoming calls to the Twilio number. Returns TwiML to connect to voice pipeline.",
)
async def handle_inbound_call(
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    CallSid: str = Form(...),
    companion_id: str | None = Query(None),
    api_key_id: str | None = Query(None),
    conn=Depends(get_db),
):
    """Handle incoming Twilio call.

    Configure this endpoint as the Voice webhook URL for your Twilio number.
    It creates a call session and returns TwiML to connect to the voice WebSocket.
    """
    logger.info(f"[TWILIO_INBOUND] Incoming call: from={From}, to={To}, call_sid={CallSid}")

    resolved_companion_id = companion_id or INBOUND_COMPANION_ID
    resolved_api_key_id = api_key_id or INBOUND_API_KEY_ID or ""

    signature_tokens = [TWILIO_AUTH_TOKEN]
    if resolved_companion_id:
        try:
            companion_uuid = UUID(resolved_companion_id)
            companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_uuid)
            companion_token = None
            if companion and companion.config and companion.config.voice:
                twilio_cfg = companion.config.voice.twilio
                if twilio_cfg and twilio_cfg.auth_token:
                    companion_token = twilio_cfg.auth_token
            if companion_token:
                signature_tokens.append(companion_token)
        except Exception as e:
            logger.warning(
                f"[TWILIO_INBOUND] Failed to resolve companion token for signature check: {e}"
            )

    _validate_twilio_signature(
        request=request,
        params={"From": From, "To": To, "CallSid": CallSid},
        auth_tokens=signature_tokens,
    )

    if not resolved_companion_id:
        logger.error("[TWILIO_INBOUND] TWILIO_INBOUND_COMPANION_ID not configured")
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, this number is not configured. Goodbye.</Say>
    <Hangup/>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Create call ID for tracking
    call_id = str(uuid4())

    # Extract caller phone as user_id (normalize format)
    user_id = From.replace("+", "").replace("-", "").replace(" ", "")

    # Store pending call data (relationship will be created/fetched in WebSocket handler)
    _pending_calls[call_id] = {
        "companion_id": resolved_companion_id,
        "user_id": f"phone:{user_id}",
        "relationship_id": "",  # Will be resolved in WebSocket handler
        "api_key_id": resolved_api_key_id,
        "call_sid": CallSid,
        "from": From,
        "to": To,
        "inbound": True,
        "created_at": datetime.now(UTC).isoformat(),
    }

    _schedule_twilio_call_event(
        call_sid=CallSid,
        event_type="inbound_webhook_received",
        status="incoming",
        call_id=call_id,
        companion_id=resolved_companion_id,
        user_id=f"phone:{user_id}",
        direction="inbound",
        from_number=From,
        to_number=To,
        payload={"source": "twilio_inbound_webhook"},
    )

    # Build WebSocket URL
    base_url = _get_public_base_url()
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/twilio/ws"

    # Return TwiML that connects to our WebSocket
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Connecting you now.</Say>
    <Connect>
        <Stream url="{ws_url}">
            <Parameter name="call_id" value="{call_id}"/>
            <Parameter name="companion_id" value="{resolved_companion_id}"/>
            <Parameter name="user_id" value="phone:{user_id}"/>
            <Parameter name="relationship_id" value=""/>
            <Parameter name="api_key_id" value="{resolved_api_key_id}"/>
            <Parameter name="inbound" value="true"/>
        </Stream>
    </Connect>
</Response>"""

    logger.info(
        f"[TWILIO_INBOUND] Created call session: call_id={call_id}, user_id=phone:{user_id}"
    )
    return Response(content=twiml, media_type="application/xml")


# ──────────────────────────────────────────────────────────────────────────────
# Dial-Out Endpoint
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/dial-out",
    response_model=TwilioDialOutResponse,
    summary="Initiate outbound Twilio call",
    description="Start an outbound call to a phone number via Twilio.",
)
async def dial_out(
    request: TwilioDialOutRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn=Depends(get_db),
) -> TwilioDialOutResponse:
    """Initiate an outbound call via Twilio.

    This endpoint:
    1. Validates the companion and relationship
    2. Creates a Twilio call with TwiML that connects to our WebSocket
    3. Returns the call SID for tracking
    """
    # Verify companion access
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, request.companion_id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Companion {request.companion_id} not found",
        )
    if companion.project_id != subject.project.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Companion does not belong to this project",
        )

    # Ensure relationship exists
    relationship, _ = await RelationshipRepository.ensure_exists(
        conn,
        companion_id=request.companion_id,
        user_id=request.user_id,
    )

    # Generate unique call ID for TwiML callback
    call_id = str(uuid4())

    # Store call metadata for WebSocket handler
    _pending_calls[call_id] = {
        "companion_id": str(request.companion_id),
        "user_id": request.user_id,
        "relationship_id": str(relationship.id),
        "api_key_id": str(subject.api_key.id),
        "ivr_goal": request.ivr_goal,
        "created_at": datetime.now(UTC).isoformat(),
    }

    # Build TwiML and status callback URLs
    base_url = _get_public_base_url()
    twiml_url = f"{base_url}/twilio/twiml/{call_id}"
    status_callback_url = f"{base_url}/twilio/status-callback"

    logger.info(
        f"[TWILIO] Initiating dial-out: to={request.to_number}, "
        f"companion={request.companion_id}, twiml_url={twiml_url}"
    )

    try:
        creds = _resolve_twilio_credentials(companion)
        if not creds:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Twilio credentials not configured",
            )
        account_sid, auth_token, from_number = creds
        _pending_calls[call_id]["twilio_auth_token"] = auth_token
        client = _get_twilio_client_for(account_sid, auth_token)
        call = client.calls.create(
            to=request.to_number,
            from_=from_number,
            url=twiml_url,
            method="POST",
            status_callback=status_callback_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
        )

        logger.info(f"[TWILIO] Call initiated: sid={call.sid}, status={call.status}")
        _call_auth_tokens[call.sid] = auth_token

        _schedule_twilio_call_event(
            call_sid=call.sid,
            event_type="dial_out_initiated",
            status=call.status,
            call_id=call_id,
            companion_id=request.companion_id,
            relationship_id=relationship.id,
            user_id=request.user_id,
            direction="outbound",
            from_number=from_number,
            to_number=request.to_number,
            payload={"source": "twilio_dial_out_api"},
        )

        return TwilioDialOutResponse(
            call_sid=call.sid,
            status=call.status,
            call_id=call_id,
        )

    except Exception as e:
        # Clean up pending call on failure
        _pending_calls.pop(call_id, None)
        logger.exception(f"[TWILIO] Failed to initiate call: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate call: {e!s}",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Status Callback Endpoint
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/status-callback",
    summary="Twilio status callback webhook",
    description="Receives call status updates from Twilio.",
    include_in_schema=False,  # Internal webhook, not for public docs
)
async def status_callback(
    request: Request,
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    CallDuration: int | None = Form(None),
):
    """Receive status updates from Twilio.

    Twilio sends POST requests with form data when call status changes.
    Status values: queued, ringing, in-progress, completed, busy, no-answer, canceled, failed
    """
    params = {
        "CallSid": CallSid,
        "CallStatus": CallStatus,
    }
    if CallDuration is not None:
        params["CallDuration"] = str(CallDuration)
    _validate_twilio_signature(
        request=request,
        params=params,
        auth_tokens=[_call_auth_tokens.get(CallSid, ""), TWILIO_AUTH_TOKEN],
    )

    logger.info(f"[TWILIO_STATUS] call_sid={CallSid}, status={CallStatus}, duration={CallDuration}")

    # Store/update status
    _call_status[CallSid] = {
        "status": CallStatus,
        "duration": CallDuration,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    if CallStatus in {"completed", "busy", "no-answer", "canceled", "failed"}:
        _call_auth_tokens.pop(CallSid, None)

    _schedule_twilio_call_event(
        call_sid=CallSid,
        event_type="status_callback",
        status=CallStatus,
        duration=CallDuration,
        payload={"call_duration": CallDuration},
    )

    # Return empty response (Twilio expects 200)
    return Response(status_code=200)


@router.get(
    "/status/{call_sid}",
    response_model=TwilioStatusResponse,
    summary="Get call status",
    description="Check the current status of a Twilio call.",
)
async def get_call_status(call_sid: str) -> TwilioStatusResponse:
    """Get the current status of a call.

    Returns the last known status from the callback webhook.
    If no status is recorded, returns 'unknown'.
    """
    status_info = _call_status.get(call_sid)

    if status_info:
        return TwilioStatusResponse(
            call_sid=call_sid,
            status=status_info["status"],
            duration=status_info.get("duration"),
            timestamp=status_info["timestamp"],
        )

    # No status recorded yet - call may still be initializing
    return TwilioStatusResponse(
        call_sid=call_sid,
        status="unknown",
        duration=None,
        timestamp=datetime.now(UTC).isoformat(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# TwiML Endpoint
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/twiml/{call_id}",
    summary="TwiML response for Twilio call",
    description="Returns TwiML that connects the call to our WebSocket.",
)
async def get_twiml(call_id: str, request: Request) -> Response:
    """Return TwiML that connects Twilio to our WebSocket.

    This endpoint is called by Twilio when the call is answered.
    It returns TwiML that establishes a bidirectional WebSocket stream.
    """
    call_data = _pending_calls.get(call_id)
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    _validate_twilio_signature(
        request=request,
        params=params,
        auth_tokens=[
            call_data.get("twilio_auth_token", "") if call_data else "",
            TWILIO_AUTH_TOKEN,
        ],
    )

    # Verify call exists
    if not call_data:
        logger.warning(f"[TWILIO] TwiML requested for unknown call_id: {call_id}")
        # Return TwiML that says "call not found" and hangs up
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, this call session has expired.</Say>
    <Hangup/>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Build WebSocket URL with call metadata
    base_url = _get_public_base_url()
    # Convert http(s) to ws(s)
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/twilio/ws"

    # TwiML with Stream that connects to our WebSocket
    # Pass call metadata as parameters
    ivr_goal = call_data.get("ivr_goal") or ""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}">
            <Parameter name="call_id" value="{call_id}"/>
            <Parameter name="companion_id" value="{call_data["companion_id"]}"/>
            <Parameter name="user_id" value="{call_data["user_id"]}"/>
            <Parameter name="relationship_id" value="{call_data["relationship_id"]}"/>
            <Parameter name="api_key_id" value="{call_data["api_key_id"]}"/>
            <Parameter name="ivr_goal" value="{ivr_goal}"/>
        </Stream>
    </Connect>
</Response>"""

    logger.info(f"[TWILIO] Returning TwiML for call_id={call_id}")
    return Response(content=twiml, media_type="application/xml")


# ──────────────────────────────────────────────────────────────────────────────
# WebSocket Endpoint
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class TwilioCallState:
    """State for an active Twilio voice call."""

    call_id: str
    stream_sid: str
    call_sid: str
    companion_id: UUID
    user_id: str
    relationship_id: UUID
    api_key_id: UUID | None  # Optional for inbound calls
    connected_at: datetime
    ivr_goal: str | None = None
    is_inbound: bool = False  # True for calls TO the companion
    source: str | None = None
    dialogmachine_elevenlabs: dict[str, Any] | None = None
    dialogmachine_llm_provider: str | None = None
    pipeline_task: PipelineTask | None = None


def _build_call_message_metadata(state: TwilioCallState, mode: str) -> dict:
    """Build metadata payload for persisted Twilio transcript messages."""
    return {
        "channel": "twilio",
        "call_sid": state.call_sid,
        "call_id": state.call_id,
        "stream_sid": state.stream_sid,
        "call_direction": "inbound" if state.is_inbound else "outbound",
        "call_mode": mode,
    }


@router.websocket("/ws")
async def twilio_websocket(websocket: WebSocket):
    """Handle Twilio media stream WebSocket connection.

    This endpoint receives audio from Twilio and sends audio back.
    Twilio uses 8kHz mulaw audio format.
    """
    await websocket.accept()

    state: TwilioCallState | None = None
    stream_sid: str | None = None
    call_sid: str | None = None
    call_params: dict = {}

    try:
        # Wait for the 'start' message from Twilio which contains stream metadata
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")

            if event == "connected":
                logger.info("[TWILIO_WS] Connected event received")
                continue

            elif event == "start":
                # Extract stream metadata
                start_data = data.get("start", {})
                stream_sid = data.get("streamSid")
                call_sid = start_data.get("callSid")
                call_params = start_data.get("customParameters", {})

                logger.info(
                    f"[TWILIO_WS] Stream started: stream_sid={stream_sid}, "
                    f"call_sid={call_sid}, params={call_params}"
                )

                # Extract call metadata from parameters
                call_id = call_params.get("call_id")
                if not call_id:
                    logger.error("[TWILIO_WS] No call_id in stream parameters")
                    await websocket.close(1008, "Missing call_id")
                    return

                # Clean up pending call entry
                pending = _pending_calls.pop(call_id, None)
                if not pending:
                    logger.warning(f"[TWILIO_WS] Call metadata not found for call_id={call_id}")
                    await websocket.close(1008, "Unknown call_id")
                    return

                expected_call_sid = pending.get("call_sid")
                if expected_call_sid and call_sid and expected_call_sid != call_sid:
                    logger.error(
                        "[TWILIO_WS] call_sid mismatch for call_id=%s: expected=%s actual=%s",
                        call_id,
                        expected_call_sid,
                        call_sid,
                    )
                    await websocket.close(1008, "call_sid mismatch")
                    return

                if not pending.get("companion_id") or not pending.get("user_id"):
                    logger.error(
                        "[TWILIO_WS] Missing required metadata for call_id=%s (companion_id/user_id)",
                        call_id,
                    )
                    await websocket.close(1008, "Missing call metadata")
                    return

                # Extract IVR goal (empty string from TwiML means None)
                ivr_goal = pending.get("ivr_goal")
                if ivr_goal == "":
                    ivr_goal = None

                # Handle relationship_id - may be empty for inbound calls
                relationship_id_str = pending.get("relationship_id") or ""
                is_inbound = bool(pending.get("inbound", False))

                if relationship_id_str:
                    relationship_id = UUID(relationship_id_str)
                elif is_inbound:
                    # For inbound calls, create/get relationship dynamically
                    async with get_db_connection() as conn:
                        companion_id = UUID(pending["companion_id"])
                        user_id = pending["user_id"]
                        relationship, created = await RelationshipRepository.ensure_exists(
                            conn, companion_id=companion_id, user_id=user_id
                        )
                        relationship_id = relationship.id
                        logger.info(
                            f"[TWILIO_WS] {'Created' if created else 'Fetched'} relationship: {relationship_id} for inbound call"
                        )
                else:
                    logger.error("[TWILIO_WS] No relationship_id for non-inbound call")
                    await websocket.close(1008, "Missing relationship_id")
                    return

                # Handle api_key_id - may be empty for inbound calls
                api_key_id_str = pending.get("api_key_id") or ""
                api_key_id = UUID(api_key_id_str) if api_key_id_str else None

                # Create call state
                state = TwilioCallState(
                    call_id=call_id,
                    stream_sid=stream_sid or "",
                    call_sid=call_sid or "",
                    companion_id=UUID(pending["companion_id"]),
                    user_id=pending["user_id"],
                    relationship_id=relationship_id,
                    api_key_id=api_key_id,
                    connected_at=datetime.now(UTC),
                    ivr_goal=ivr_goal,
                    is_inbound=is_inbound,
                    source=str(pending.get("source")) if pending.get("source") else None,
                    dialogmachine_elevenlabs=(
                        pending.get("dialogmachine_elevenlabs")
                        if isinstance(pending.get("dialogmachine_elevenlabs"), dict)
                        else None
                    ),
                    dialogmachine_llm_provider=(
                        str(pending.get("dialogmachine_llm_provider")).strip()
                        if isinstance(pending.get("dialogmachine_llm_provider"), str)
                        else None
                    ),
                )

                _schedule_twilio_call_event(
                    call_sid=state.call_sid,
                    event_type="media_stream_started",
                    status="in-progress",
                    call_id=state.call_id,
                    companion_id=state.companion_id,
                    relationship_id=state.relationship_id,
                    user_id=state.user_id,
                    direction="inbound" if state.is_inbound else "outbound",
                    payload={"stream_sid": state.stream_sid},
                )

                # Start the voice pipeline
                break

            elif event == "stop":
                logger.info("[TWILIO_WS] Stream stopped before start")
                return

        # Run voice session with Twilio transport
        if state:
            await _run_twilio_voice_session(websocket, state, stream_sid, call_sid)

    except Exception as e:
        logger.exception(f"[TWILIO_WS] Error in WebSocket handler: {e}")
    finally:
        if state and state.pipeline_task:
            try:
                await state.pipeline_task.cancel()
            except Exception:
                pass
        logger.info(f"[TWILIO_WS] Connection closed: stream_sid={stream_sid}")


async def _run_inbound_conversation(
    websocket: WebSocket,
    state: TwilioCallState,
    stream_sid: str,
    transport,
    stt,
    tts,
    voice_config,
    enhanced_prompt: str,
    conversation_history: list,
    task_delegation_enabled: bool = True,
    call_termination_enabled: bool = False,
) -> None:
    """Run a direct conversation pipeline (no IVR navigation).

    For companion calls, we skip IVR detection and go straight to conversation mode.
    The companion greets the caller first.
    """
    from uuid import uuid4

    from pipecat.frames.frames import EndFrame, LLMMessagesUpdateFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
    from pipecat.processors.transcript_processor import TranscriptProcessor

    from .fast_brain_llm import FastBrainConfig, build_fast_brain_llm_service
    from .providers import LLMProvider

    try:
        # NOTE: VAD params are set by the transport already, skip modification
        # to avoid timing issues with uninitialized sample_rate
        pending_end_call = False
        end_call_enqueued = False

        async def request_end_call(reason: str | None = None) -> None:
            nonlocal pending_end_call
            if pending_end_call:
                return
            pending_end_call = True
            logger.info(
                "[TWILIO_WS] End-call requested by fast brain for call_id=%s reason=%s",
                state.call_id,
                reason or "unspecified",
            )

        selected_llm_provider = voice_config.llm_provider or LLMProvider.GEMINI_25_FLASH
        if selected_llm_provider == LLMProvider.FAST_BRAIN:
            fast_brain_config = FastBrainConfig.from_env()
            fast_brain_config.allow_delegation = bool(task_delegation_enabled)
            fast_brain_config.allow_call_termination = bool(call_termination_enabled)
            logger.info(
                "[TWILIO_WS] Fast Brain delegation enabled=%s call_termination_enabled=%s for call_id=%s source=%s",
                fast_brain_config.allow_delegation,
                fast_brain_config.allow_call_termination,
                state.call_id,
                state.source or "unknown",
            )
            conversation_llm = build_fast_brain_llm_service(
                config=fast_brain_config,
                personality_prompt=enhanced_prompt,
                companion_id=str(state.companion_id),
                relationship_id=str(state.relationship_id),
                user_id=state.user_id,
                on_end_call_requested=request_end_call,
            )
        else:
            logger.info(
                "[TWILIO_WS] Using direct LLM provider=%s for call_id=%s",
                selected_llm_provider.value,
                state.call_id,
            )
            conversation_llm = build_llm_service(
                selected_llm_provider,
                enhanced_prompt,
                voice_config.temperature,
            )

        # Create transcript processor
        transcript = TranscriptProcessor()

        # Build initial messages (system prompt + history, but NO user message yet)
        conversation_messages = [{"role": "system", "content": enhanced_prompt}]
        if conversation_history:
            history_slice = conversation_history[-20:]
            for msg in history_slice:
                conversation_messages.append({"role": msg["role"], "content": msg["content"]})

        # Create LLM context and aggregator
        tools = None
        if selected_llm_provider == LLMProvider.FAST_BRAIN:
            get_tools_schema = getattr(conversation_llm, "get_tools_schema", None)
            if callable(get_tools_schema):
                tools = get_tools_schema()

        llm_context = (
            OpenAILLMContext(conversation_messages, tools=tools)
            if tools
            else OpenAILLMContext(conversation_messages)
        )
        context_aggregator = conversation_llm.create_context_aggregator(llm_context)

        # Build conversation pipeline: STT → User Aggregator → LLM → TTS
        pipeline_processors = [
            transport.input(),
            stt,
            transcript.user(),
            context_aggregator.user(),
            conversation_llm,
            tts,
            transport.output(),
            transcript.assistant(),
            context_aggregator.assistant(),
        ]

        pipeline = Pipeline(pipeline_processors)
        task = PipelineTask(pipeline)
        state.pipeline_task = task

        logger.info(f"[TWILIO_WS] Starting conversation for relationship {state.relationship_id}")

        # Set up transcript handler for message persistence
        @transcript.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            nonlocal pending_end_call, end_call_enqueued
            for msg in frame.messages:
                if isinstance(msg, TranscriptionMessage):
                    logger.info(f"[TWILIO_WS] [CONV] {msg.role}: {msg.content}")

                    # Persist message
                    if msg.content.strip():
                        try:
                            async with get_db_connection() as conn:
                                seq_row = await conn.fetchrow(
                                    "SELECT next_relationship_message_seq($1) as seq",
                                    state.relationship_id,
                                )
                                seq = seq_row["seq"] if seq_row else None
                                metadata = _build_call_message_metadata(state, mode="conversation")

                                await conn.execute(
                                    """
                                    INSERT INTO messages (id, relationship_id, role, content, seq, input_modality, metadata)
                                    VALUES ($1, $2, $3, $4, $5, 'voice', $6)
                                    """,
                                    uuid4(),
                                    state.relationship_id,
                                    msg.role,
                                    msg.content,
                                    seq,
                                    metadata,
                                )
                        except Exception as e:
                            logger.warning(f"[TWILIO_WS] Failed to persist message: {e}")

                    if msg.role == "assistant" and pending_end_call and not end_call_enqueued:
                        logger.info(
                            "[TWILIO_WS] Ending call after assistant final response: call_id=%s",
                            state.call_id,
                        )
                        pending_end_call = False
                        end_call_enqueued = True
                        await task.queue_frame(EndFrame())

        # Set up transport event handlers
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("[TWILIO_WS] Twilio client connected - triggering greeting")

            # Trigger initial greeting now that client is connected
            greeting_messages = list(conversation_messages)  # Copy
            greeting_messages.append(
                {
                    "role": "user",
                    "content": "[Phone call connected - caller is waiting. Greet them warmly and briefly.]",
                }
            )

            # Queue frame to update context and trigger LLM response
            await task.queue_frame(LLMMessagesUpdateFrame(messages=greeting_messages, run_llm=True))

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("[TWILIO_WS] Twilio client disconnected")
            await task.cancel()

        # Run pipeline - the LLM will immediately generate a greeting
        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)

    except Exception as e:
        logger.exception(f"[TWILIO_WS] Error in inbound conversation: {e}")
    finally:
        logger.info(f"[TWILIO_WS] Conversation ended for relationship {state.relationship_id}")


async def _run_twilio_voice_session(
    websocket: WebSocket,
    state: TwilioCallState,
    stream_sid: str,
    call_sid: str,
) -> None:
    """Run the voice pipeline for a Twilio call with IVR navigation.

    The call flow:
    1. Start with IVRNavigator to detect and navigate through IVR menus
    2. When human is detected or IVR navigation completes, switch to conversation mode
    3. If IVR navigation gets stuck, hang up the call
    """
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import EndFrame, LLMMessagesUpdateFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
    from pipecat.processors.transcript_processor import TranscriptProcessor

    from .ivr_pipeline import (
        CONVERSATION_VAD_PARAMS,
        IVR_VAD_PARAMS,
        IVRStatus,
        build_ivr_navigator,
    )

    try:
        # Load companion and relationship data
        async with get_db_connection() as conn:
            companion = await CompanionRepository.get_companion_by_id_no_auth(
                conn, state.companion_id
            )
            if not companion:
                logger.error(f"[TWILIO_WS] Companion not found: {state.companion_id}")
                return

            relationship = await RelationshipRepository.get_by_id(conn, state.relationship_id)
            if not relationship:
                logger.error(f"[TWILIO_WS] Relationship not found: {state.relationship_id}")
                return
            if relationship.companion_id != companion.id:
                logger.error(
                    "[TWILIO_WS] Relationship %s does not belong to companion %s",
                    state.relationship_id,
                    state.companion_id,
                )
                return

            # Build system prompt
            from ...services.context_assembly import build_effective_system_prompt

            effective_prompt, _ = await build_effective_system_prompt(
                conn, companion_id=companion.id
            )
            system_prompt = (
                effective_prompt or companion.config.system_prompt.get_effective_prompt()
                if hasattr(companion.config, "system_prompt")
                else "You are a helpful companion."
            )

            # DialogMachine per-relationship prompt override (if configured)
            relationship_config = (
                relationship.config if isinstance(relationship.config, dict) else {}
            )
            dialogmachine_cfg = relationship_config.get("dialogmachine")
            dialogmachine_llm_provider = (
                state.dialogmachine_llm_provider.strip()
                if isinstance(state.dialogmachine_llm_provider, str)
                and state.dialogmachine_llm_provider.strip()
                else None
            )
            task_delegation_enabled: bool = True
            call_termination_enabled: bool = False
            if isinstance(dialogmachine_cfg, dict):
                if not dialogmachine_llm_provider:
                    llm_cfg = dialogmachine_cfg.get("llm")
                    if isinstance(llm_cfg, dict) and isinstance(llm_cfg.get("provider"), str):
                        candidate = llm_cfg.get("provider", "").strip()
                        dialogmachine_llm_provider = candidate or None
                    elif isinstance(dialogmachine_cfg.get("llm_provider"), str):
                        candidate = dialogmachine_cfg.get("llm_provider", "").strip()
                        dialogmachine_llm_provider = candidate or None
                prompt_override = dialogmachine_cfg.get("prompt_override")
                if isinstance(prompt_override, str) and prompt_override.strip():
                    system_prompt = prompt_override.strip()
                    logger.info(
                        "[TWILIO_WS] Applied dialogmachine prompt override for relationship %s",
                        state.relationship_id,
                    )
                guardrails = dialogmachine_cfg.get("guardrails")
                if isinstance(guardrails, str) and guardrails.strip():
                    system_prompt = (
                        f"{system_prompt.rstrip()}\n\n"
                        f"## Relationship Guardrails\n{guardrails.strip()}"
                    )
                    logger.info(
                        "[TWILIO_WS] Applied dialogmachine guardrails for relationship %s",
                        state.relationship_id,
                    )
                tools_cfg = dialogmachine_cfg.get("tools")
                selected_tools: set[str] | None = None
                if isinstance(tools_cfg, dict) and isinstance(tools_cfg.get("selected"), list):
                    selected_tools = {
                        str(item).strip()
                        for item in tools_cfg.get("selected")
                        if isinstance(item, str) and str(item).strip()
                    }

                if selected_tools is not None:
                    task_delegation_enabled = "task_delegation" in selected_tools
                    call_termination_enabled = "end_call" in selected_tools
                else:
                    # For DialogMachine flows we default delegation OFF unless explicitly enabled.
                    task_delegation_cfg = dialogmachine_cfg.get("enable_task_delegation")
                    if isinstance(task_delegation_cfg, bool):
                        task_delegation_enabled = task_delegation_cfg
                    elif state.source == "dialogmachine_dial":
                        task_delegation_enabled = False

                    # Default end-call behavior ON for DialogMachine phone flow.
                    if state.source == "dialogmachine_dial":
                        call_termination_enabled = True
            elif state.source == "dialogmachine_dial":
                task_delegation_enabled = False
                call_termination_enabled = True

            # Load conversation history
            history_rows = await conn.fetch(
                """
                SELECT role, content FROM messages
                WHERE relationship_id = $1
                ORDER BY created_at ASC
                LIMIT 50
                """,
                state.relationship_id,
            )
            conversation_history = [
                {"role": row["role"], "content": row["content"]} for row in history_rows
            ]

        # Get voice config from companion, fallback to defaults
        from .providers import LLMProvider, STTProvider, TTSProvider

        voice_config = create_default_voice_config()
        selected_fast_model_provider = LLMProvider.GEMINI_25_FLASH
        if state.source == "dialogmachine_dial" and dialogmachine_llm_provider:
            try:
                selected_fast_model_provider = LLMProvider(dialogmachine_llm_provider)
            except ValueError:
                logger.warning(
                    "[TWILIO_WS] Ignoring unsupported DialogMachine llm provider: %s",
                    dialogmachine_llm_provider,
                )
        if selected_fast_model_provider == LLMProvider.FAST_BRAIN:
            selected_fast_model_provider = LLMProvider.GEMINI_25_FLASH
        if state.source == "dialogmachine_dial":
            voice_config.fast_brain_model_provider = selected_fast_model_provider
            voice_config.llm_provider = LLMProvider.FAST_BRAIN
        else:
            voice_config.llm_provider = selected_fast_model_provider

        # Load TTS/STT settings from companion config
        if companion and companion.config and companion.config.voice:
            comp_voice = companion.config.voice
            # If preset is set but providers are missing, resolve to match dashboard behavior
            if getattr(comp_voice, "preset", None) and (
                not comp_voice.stt_provider or not comp_voice.tts_provider
            ):
                try:
                    from ...services.voice_presets import resolve_voice_pipeline_config

                    resolved_stt, resolved_tts, resolved_voice = resolve_voice_pipeline_config(
                        companion.config
                    )
                    if not comp_voice.stt_provider:
                        comp_voice.stt_provider = resolved_stt
                    if not comp_voice.tts_provider:
                        comp_voice.tts_provider = resolved_tts
                    if not comp_voice.voice_name:
                        comp_voice.voice_name = resolved_voice
                except Exception as e:
                    logger.warning(f"[TWILIO_WS] Failed to resolve voice preset: {e}")
            # TTS provider (e.g., "elevenlabs")
            if comp_voice.tts_provider:
                try:
                    voice_config.tts_provider = TTSProvider(comp_voice.tts_provider)
                    logger.info(f"[TWILIO_WS] Using companion TTS: {comp_voice.tts_provider}")
                except ValueError:
                    logger.warning(f"[TWILIO_WS] Unknown TTS provider: {comp_voice.tts_provider}")
            # STT provider (e.g., "deepgram")
            if comp_voice.stt_provider:
                try:
                    voice_config.stt_provider = STTProvider(comp_voice.stt_provider)
                    logger.info(f"[TWILIO_WS] Using companion STT: {comp_voice.stt_provider}")
                except ValueError:
                    logger.warning(f"[TWILIO_WS] Unknown STT provider: {comp_voice.stt_provider}")
            # Voice name
            if comp_voice.voice_name:
                voice_config.voice_name = comp_voice.voice_name
                logger.info(f"[TWILIO_WS] Using companion voice: {comp_voice.voice_name}")

        # DialogMachine workspace-level ElevenLabs overrides (if present)
        dialogmachine_elevenlabs = (
            state.dialogmachine_elevenlabs
            if isinstance(state.dialogmachine_elevenlabs, dict)
            else {}
        )
        dm_voice_id = (
            str(dialogmachine_elevenlabs.get("voice_id")).strip()
            if dialogmachine_elevenlabs.get("voice_id")
            else ""
        )
        dm_voice_name = (
            str(dialogmachine_elevenlabs.get("voice_name")).strip()
            if dialogmachine_elevenlabs.get("voice_name")
            else ""
        )
        dm_model_id = (
            str(dialogmachine_elevenlabs.get("model_id")).strip()
            if dialogmachine_elevenlabs.get("model_id")
            else ""
        )
        dm_language_enabled = bool(dialogmachine_elevenlabs.get("language_override_enabled"))
        dm_language_code = (
            str(dialogmachine_elevenlabs.get("language_code")).strip()
            if dialogmachine_elevenlabs.get("language_code")
            else ""
        )
        has_dm_tts_override = any(
            key in dialogmachine_elevenlabs
            for key in (
                "voice_id",
                "voice_name",
                "model_id",
                "stability",
                "similarity_boost",
                "style",
                "speed",
                "use_speaker_boost",
                "language_override_enabled",
                "language_code",
            )
        )
        if state.source == "dialogmachine_dial" and has_dm_tts_override:
            voice_config.tts_provider = TTSProvider.ELEVENLABS
            if dm_voice_name:
                voice_config.voice_name = dm_voice_name
            if dm_voice_id:
                voice_config.tts_voice_id = dm_voice_id
            if dm_model_id:
                voice_config.elevenlabs_model_id = dm_model_id
            voice_config.elevenlabs_stability = dialogmachine_elevenlabs.get("stability")
            voice_config.elevenlabs_similarity_boost = dialogmachine_elevenlabs.get(
                "similarity_boost"
            )
            voice_config.elevenlabs_style = dialogmachine_elevenlabs.get("style")
            voice_config.elevenlabs_speed = dialogmachine_elevenlabs.get("speed")
            voice_config.elevenlabs_use_speaker_boost = dialogmachine_elevenlabs.get(
                "use_speaker_boost"
            )
            voice_config.elevenlabs_language_code = (
                dm_language_code if dm_language_enabled and dm_language_code else None
            )
            logger.info(
                "[TWILIO_WS] Applied DialogMachine ElevenLabs overrides: voice_id=%s model=%s",
                voice_config.tts_voice_id or "mapped",
                voice_config.elevenlabs_model_id or "default",
            )

        # Look up companion's voice from the voices table (voice_id is a UUID reference)
        companion_voice_id = None
        if companion and companion.current_version and companion.current_version.voice_id:
            from ...repositories.voice import VoiceRepository

            async with get_db_connection() as voice_conn:
                voice = await VoiceRepository.get_voice_by_id(
                    voice_conn, companion.current_version.voice_id
                )
                if voice:
                    companion_voice_id = voice.provider_key  # The actual ElevenLabs ID
                    logger.info(
                        f"[TWILIO_WS] Loaded voice: {voice.name} ({voice.provider}) → {companion_voice_id}"
                    )

        voice_config = normalize_voice_config(voice_config)

        # Create Twilio serializer (handles mulaw encoding/decoding)
        from .pipeline import SILERO_VAD_SAMPLE_RATE

        serializer = TwilioFrameSerializer(
            stream_sid=stream_sid,
            params=TwilioFrameSerializer.InputParams(
                twilio_sample_rate=TWILIO_SAMPLE_RATE,
                sample_rate=SILERO_VAD_SAMPLE_RATE,
                auto_hang_up=False,  # We handle hang-up ourselves via EndFrame
            ),
        )

        # Choose VAD params: conversation mode for companion calls, IVR only when needed
        vad_params = (
            CONVERSATION_VAD_PARAMS if (state.is_inbound or not state.ivr_goal) else IVR_VAD_PARAMS
        )

        # Create transport with chosen VAD parameters
        transport_params = FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(sample_rate=SILERO_VAD_SAMPLE_RATE, params=vad_params),
            audio_in_sample_rate=SILERO_VAD_SAMPLE_RATE,
            audio_out_sample_rate=TWILIO_SAMPLE_RATE,
            audio_in_channels=1,
            audio_out_channels=1,
            serializer=serializer,
        )

        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=transport_params,
        )

        # Voice context prompt for conversation mode
        VOICE_CONTEXT_PROMPT = """
## Voice Conversation Context
You are in a real-time voice conversation over the phone. Respond naturally as if having a spoken conversation - be concise and conversational. The caller can hear your responses immediately.
""".strip()

        enhanced_prompt = system_prompt.rstrip() + "\n\n" + VOICE_CONTEXT_PROMPT

        # Inject hot_context for all voice sessions (session start only)
        if state.relationship_id:
            try:
                from .voice_workspace import HotContextS3

                hot_context = HotContextS3(state.relationship_id)
                hot_context_block = hot_context.render()
                if hot_context_block:
                    hot_context_instruction = (
                        "If the caller asks about updates or status, answer directly from Hot Context. "
                        "Only delegate new actions or work requests."
                        if task_delegation_enabled
                        else "If the caller asks about updates or status, answer directly from Hot Context. "
                        "Task delegation is disabled for this call."
                    )
                    enhanced_prompt = (
                        enhanced_prompt
                        + "\n\n## Hot Context\n"
                        + hot_context_block
                        + "\n\n"
                        + hot_context_instruction
                    )
            except Exception as e:
                logger.warning(f"[TWILIO_WS] Failed to load hot_context: {e}")

        # Map voice name to provider-specific voice ID
        # Prefer companion's explicit voice_id (e.g., ElevenLabs ID) over name mapping
        voice_name = voice_config.voice_name or "alloy"
        tts_provider_str = (
            voice_config.tts_provider.value if voice_config.tts_provider else "openai"
        )
        if voice_config.tts_voice_id:
            voice_id = voice_config.tts_voice_id
            logger.info(f"[TWILIO_WS] Using explicit voice_config.tts_voice_id: {voice_id}")
        elif companion_voice_id:
            # Use the companion's explicit voice ID (e.g., ElevenLabs voice ID)
            voice_id = companion_voice_id
            logger.info(f"[TWILIO_WS] Using explicit voice_id: {voice_id}")
        else:
            # Fall back to name-based lookup
            voice_id = get_voice_id(tts_provider_str, voice_name)
            logger.info(f"[TWILIO_WS] Mapped voice '{voice_name}' → '{voice_id}'")

        # Build services
        stt = build_stt_service(voice_config.stt_provider)
        tts = build_tts_service(
            voice_config.tts_provider,
            voice_id,
            elevenlabs_model_id=voice_config.elevenlabs_model_id,
            elevenlabs_settings={
                "stability": voice_config.elevenlabs_stability,
                "similarity_boost": voice_config.elevenlabs_similarity_boost,
                "style": voice_config.elevenlabs_style,
                "speed": voice_config.elevenlabs_speed,
                "use_speaker_boost": voice_config.elevenlabs_use_speaker_boost,
                "language_code": voice_config.elevenlabs_language_code,
            },
        )
        logger.info(f"[TWILIO_WS] Built TTS: provider={tts_provider_str}, voice_id={voice_id}")

        # ========== DIRECT CONVERSATION (skip IVR when no goal) ==========
        if state.is_inbound or not state.ivr_goal:
            await _run_inbound_conversation(
                websocket=websocket,
                state=state,
                stream_sid=stream_sid,
                transport=transport,
                stt=stt,
                tts=tts,
                voice_config=voice_config,
                enhanced_prompt=enhanced_prompt,
                conversation_history=conversation_history,
                task_delegation_enabled=task_delegation_enabled,
                call_termination_enabled=call_termination_enabled,
            )
            return
        # ========== END DIRECT CONVERSATION ==========

        llm_provider_for_ivr = voice_config.llm_provider
        if llm_provider_for_ivr == LLMProvider.FAST_BRAIN:
            # IVRNavigator expects a direct model provider.
            llm_provider_for_ivr = LLMProvider.GEMINI_25_FLASH

        # Build IVR Navigator - this handles both IVR detection and navigation
        ivr_navigator = build_ivr_navigator(
            llm_provider=llm_provider_for_ivr,
            ivr_goal=state.ivr_goal,
            temperature=0.3,  # Lower temperature for more deterministic navigation
        )

        # Build conversation LLM for when human is detected
        conversation_llm = build_llm_service(
            llm_provider_for_ivr,
            enhanced_prompt,
            voice_config.temperature,
        )

        # Create transcript processor
        transcript = TranscriptProcessor()

        # Build initial messages for conversation mode
        conversation_messages = [{"role": "system", "content": enhanced_prompt}]
        if conversation_history:
            history_slice = conversation_history[-20:]
            for msg in history_slice:
                conversation_messages.append({"role": msg["role"], "content": msg["content"]})

        # Create LLM context and aggregator for conversation mode
        llm_context = OpenAILLMContext(conversation_messages)
        context_aggregator = conversation_llm.create_context_aggregator(llm_context)

        # Track conversation mode state
        in_conversation_mode = False

        # Build IVR pipeline: STT → IVRNavigator → TTS
        # IVRNavigator replaces the LLM in the pipeline during IVR navigation
        ivr_pipeline_processors = [
            transport.input(),
            stt,
            transcript.user(),
            context_aggregator.user(),
            ivr_navigator,
            tts,
            transport.output(),
            transcript.assistant(),
            context_aggregator.assistant(),
        ]

        pipeline = Pipeline(ivr_pipeline_processors)
        task = PipelineTask(pipeline)
        state.pipeline_task = task

        logger.info(
            f"[TWILIO_WS] Starting IVR navigation for relationship {state.relationship_id}, "
            f"goal={state.ivr_goal or 'default'}"
        )

        # IVR Navigator event handlers
        @ivr_navigator.event_handler("on_conversation_detected")
        async def on_conversation_detected(processor, ivr_conversation_history):
            """Human detected - switch to conversational mode."""
            nonlocal in_conversation_mode
            in_conversation_mode = True

            logger.info("[TWILIO_WS] Human detected - switching to conversation mode")

            # Build messages for the conversation LLM, including any IVR conversation context
            messages = [{"role": "system", "content": enhanced_prompt}]

            # Add prior conversation history
            if conversation_history:
                history_slice = conversation_history[-20:]
                for msg in history_slice:
                    messages.append({"role": msg["role"], "content": msg["content"]})

            # Add IVR conversation history (what was said during IVR navigation)
            if ivr_conversation_history:
                for msg in ivr_conversation_history:
                    messages.append(msg)

            # Update LLM context with full history and trigger response
            await task.queue_frame(LLMMessagesUpdateFrame(messages=messages, run_llm=True))

            # Update VAD for natural conversation flow
            if transport.params.vad_analyzer:
                transport.params.vad_analyzer.params = CONVERSATION_VAD_PARAMS
                logger.info("[TWILIO_WS] VAD params updated for conversation mode")

        @ivr_navigator.event_handler("on_ivr_status_changed")
        async def on_ivr_status_changed(processor, ivr_status):
            """Handle IVR navigation status changes."""
            nonlocal in_conversation_mode

            logger.info(f"[TWILIO_WS] IVR status changed: {ivr_status}")

            if ivr_status == IVRStatus.COMPLETED:
                # IVR navigation completed successfully - ready for conversation
                if not in_conversation_mode:
                    logger.info("[TWILIO_WS] IVR navigation completed - ready for conversation")
                    # The on_conversation_detected handler will be called next

            elif ivr_status == IVRStatus.STUCK:
                # Cannot proceed - hang up the call
                logger.warning("[TWILIO_WS] IVR navigation stuck - ending call")
                await task.queue_frame(EndFrame())

            elif ivr_status == IVRStatus.DETECTED:
                logger.info("[TWILIO_WS] IVR system detected - navigating...")

            elif ivr_status == IVRStatus.WAIT:
                logger.info("[TWILIO_WS] Waiting for more IVR information...")

        # Set up transcript handler for message persistence
        @transcript.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            for msg in frame.messages:
                if isinstance(msg, TranscriptionMessage):
                    mode = "CONV" if in_conversation_mode else "IVR"
                    logger.info(f"[TWILIO_WS] [{mode}] {msg.role}: {msg.content}")

                    # Persist message
                    if msg.content.strip():
                        try:
                            async with get_db_connection() as conn:
                                seq_row = await conn.fetchrow(
                                    "SELECT next_relationship_message_seq($1) as seq",
                                    state.relationship_id,
                                )
                                seq = seq_row["seq"] if seq_row else None
                                mode = "conversation" if in_conversation_mode else "ivr"
                                metadata = _build_call_message_metadata(state, mode=mode)

                                await conn.execute(
                                    """
                                    INSERT INTO messages (id, relationship_id, role, content, seq, input_modality, metadata)
                                    VALUES ($1, $2, $3, $4, $5, 'voice', $6)
                                    """,
                                    uuid4(),
                                    state.relationship_id,
                                    msg.role,
                                    msg.content,
                                    seq,
                                    metadata,
                                )
                        except Exception as e:
                            logger.warning(f"[TWILIO_WS] Failed to persist message: {e}")

        # Set up transport event handlers
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("[TWILIO_WS] Twilio client connected")

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("[TWILIO_WS] Twilio client disconnected")
            await task.cancel()

        # Run pipeline
        runner = PipelineRunner(handle_sigint=False)
        logger.info(f"[TWILIO_WS] Starting voice pipeline for relationship {state.relationship_id}")
        await runner.run(task)

    except Exception as e:
        logger.exception(f"[TWILIO_WS] Error in voice session: {e}")
    finally:
        logger.info(f"[TWILIO_WS] Voice session ended for relationship {state.relationship_id}")
