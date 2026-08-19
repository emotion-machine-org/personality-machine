# server/app/routers/voice/v2.py
"""v2 Voice API routes tied to relationships.

Implements WebSocket-based voice communication for the v2 API.
Voice sessions are tied to relationships, sharing state with text conversations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, List
from uuid import UUID, uuid4

import asyncpg
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, status
from pipecat.frames.frames import EndFrame, InputAudioRawFrame, TranscriptionMessage
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...context import resolve_v2_context_mode
from ...context.resolved_config import CompanionRuntimeConfig
from ...db import get_db, get_db_connection
from ...repositories.companion import CompanionRepository
from ...repositories.relationship_repository import RelationshipRepository
from ...utils.profile import resolve_profile_in_prompt_enabled
from .context_processor import VoiceContextConfig, VoiceContextProcessor
from .models import VoiceConfig, VoiceTokenRequest, VoiceTokenResponse
from .pipeline import (
    PipelineComponents,
    build_voice_pipeline,
    create_default_voice_config,
    normalize_voice_config,
)

router = APIRouter(prefix="/v2", tags=["v2-voice"])
logger = logging.getLogger(__name__)

# Configuration
WS_TOKEN_SECRET = os.environ.get("WS_TOKEN_SECRET", "dev-ws-secret-change-in-production")
VOICE_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour
HEARTBEAT_INTERVAL_SECONDS = 30
IDLE_TIMEOUT_SECONDS = 300  # 5 minutes

# WebSocket close codes
WS_CLOSE_NORMAL = 1000
WS_CLOSE_INVALID_TOKEN = 4001
WS_CLOSE_TOKEN_EXPIRED = 4002
WS_CLOSE_NOT_FOUND = 4004
WS_CLOSE_INTERNAL_ERROR = 1011


# ──────────────────────────────────────────────────────────────────────────────
# Voice Connection Manager
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class VoiceConnectionState:
    """State for an active voice WebSocket connection."""

    websocket: WebSocket
    relationship_id: UUID
    api_key_id: UUID
    connected_at: datetime
    last_activity_at: datetime
    token_expires_at: datetime
    voice_config: VoiceConfig
    pipeline_task: PipelineTask | None = None
    components: PipelineComponents | None = None
    is_bot_speaking: bool = False


class VoiceConnectionManager:
    """Manages active voice WebSocket connections.

    Separate from text ConnectionManager due to different state requirements
    (audio buffers, pipeline tasks, serializers).
    """

    def __init__(self):
        self._connections: Dict[UUID, List[VoiceConnectionState]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        relationship_id: UUID,
        api_key_id: UUID,
        token_expires_at: datetime,
        voice_config: VoiceConfig,
    ) -> VoiceConnectionState:
        """Register a new voice connection.

        Closes any existing connections for this relationship to prevent
        duplicate audio processing and responses.
        """
        now = datetime.now(UTC)
        state = VoiceConnectionState(
            websocket=websocket,
            relationship_id=relationship_id,
            api_key_id=api_key_id,
            connected_at=now,
            last_activity_at=now,
            token_expires_at=token_expires_at,
            voice_config=voice_config,
        )

        # Close any existing connections for this relationship
        existing_connections = []
        async with self._lock:
            if relationship_id in self._connections:
                existing_connections = self._connections[relationship_id][:]
                self._connections[relationship_id] = []
            self._connections[relationship_id] = [state]

        # Close existing connections outside the lock to avoid deadlock
        for old_state in existing_connections:
            logger.info(
                "Closing existing voice connection for relationship=%s (new connection)",
                relationship_id,
            )
            try:
                if old_state.pipeline_task:
                    await old_state.pipeline_task.cancel()
            except Exception:
                pass
            try:
                await old_state.websocket.close(1000, "New connection established")
            except Exception:
                pass

        logger.info(
            "Voice connection established: relationship=%s",
            relationship_id,
        )
        return state

    async def disconnect(self, state: VoiceConnectionState) -> None:
        """Unregister a voice connection and cleanup."""
        # Cancel pipeline task if running
        if state.pipeline_task:
            try:
                await state.pipeline_task.cancel()
            except Exception:
                pass

        async with self._lock:
            conns = self._connections.get(state.relationship_id, [])
            if state in conns:
                conns.remove(state)
            if not conns:
                self._connections.pop(state.relationship_id, None)

        logger.info(
            "Voice connection closed: relationship=%s, remaining=%d",
            state.relationship_id,
            len(self._connections.get(state.relationship_id, [])),
        )

    async def get_connections(self, relationship_id: UUID) -> List[VoiceConnectionState]:
        """Get all active voice connections for a relationship."""
        async with self._lock:
            return list(self._connections.get(relationship_id, []))

    async def graceful_shutdown(self) -> None:
        """Close all voice connections gracefully."""
        async with self._lock:
            all_connections = [conn for conns in self._connections.values() for conn in conns]

        for conn in all_connections:
            try:
                if conn.pipeline_task:
                    await conn.pipeline_task.cancel()
                await conn.websocket.close(WS_CLOSE_NORMAL, "Server shutting down")
            except Exception as e:
                logger.warning("Error closing voice connection during shutdown: %s", e)

        async with self._lock:
            self._connections.clear()

        logger.info("All voice connections closed for shutdown")


# Global voice connection manager
voice_connection_manager = VoiceConnectionManager()


# ──────────────────────────────────────────────────────────────────────────────
# JWT Token Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _create_voice_token(
    relationship_id: UUID,
    api_key_id: UUID,
    voice_config: VoiceConfig,
    expires_in: int = VOICE_TOKEN_EXPIRY_SECONDS,
) -> tuple[str, datetime]:
    """Create a short-lived JWT for voice WebSocket auth."""
    now = datetime.now(UTC)
    expires_at = datetime.fromtimestamp(now.timestamp() + expires_in, tz=UTC)

    payload = {
        "relationship_id": str(relationship_id),
        "api_key_id": str(api_key_id),
        "voice_config": voice_config.model_dump(),
        "type": "voice",
        "exp": int(expires_at.timestamp()),
        "iat": int(now.timestamp()),
    }

    token = jwt.encode(payload, WS_TOKEN_SECRET, algorithm="HS256")
    return token, expires_at


def _verify_voice_token(
    token: str,
    expected_relationship_id: UUID,
) -> tuple[UUID, UUID, datetime, VoiceConfig]:
    """Verify a voice WebSocket JWT and extract claims.

    Returns (relationship_id, api_key_id, expires_at, voice_config)
    """
    payload = jwt.decode(token, WS_TOKEN_SECRET, algorithms=["HS256"])

    if payload.get("type") != "voice":
        raise jwt.InvalidTokenError("Invalid token type")

    relationship_id = UUID(payload["relationship_id"])
    api_key_id = UUID(payload["api_key_id"])
    exp_timestamp = payload["exp"]
    expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
    voice_config = VoiceConfig(**payload.get("voice_config", {}))

    if relationship_id != expected_relationship_id:
        raise jwt.InvalidTokenError("Token scope mismatch")

    return relationship_id, api_key_id, expires_at, voice_config


def _make_voice_ws_url(companion_id: UUID, user_id: str) -> str:
    """Generate WebSocket URL for voice connection."""
    scheme = "wss" if os.getenv("ENV") == "prod" else "ws"
    host = os.getenv("PUBLIC_HOST", "localhost:8100")
    return f"{scheme}://{host}/v2/companions/{companion_id}/relationships/{user_id}/voice/connect"


# ──────────────────────────────────────────────────────────────────────────────
# Token Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companions/{companion_id}/relationships/{user_id}/voice/token",
    response_model=VoiceTokenResponse,
    summary="Get voice WebSocket token",
    description="Exchange API key for a short-lived JWT for voice WebSocket connection.",
)
async def create_voice_token(
    companion_id: UUID,
    user_id: str,
    request: VoiceTokenRequest | None = None,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> VoiceTokenResponse:
    """Create a voice WebSocket authentication token."""
    # Verify companion access
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Companion {companion_id} not found",
        )
    if companion.project_id != subject.project.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Companion does not belong to this project",
        )

    # Ensure relationship exists
    relationship, created = await RelationshipRepository.ensure_exists(
        conn,
        companion_id=companion_id,
        user_id=user_id,
    )

    # Clear profile and/or messages if requested (useful for onboarding flows)
    if request and not created:
        if request.clear_profile:
            await conn.execute(
                "UPDATE relationships SET profile = '{}' WHERE id = $1",
                relationship.id,
            )
        if request.clear_messages:
            await conn.execute(
                "DELETE FROM messages WHERE relationship_id = $1",
                relationship.id,
            )

    # Get voice config from request or use defaults
    voice_config = (
        request.voice_config if request and request.voice_config else create_default_voice_config()
    )
    voice_config = normalize_voice_config(voice_config)

    # Create token
    token, _expires_at = _create_voice_token(
        relationship_id=relationship.id,
        api_key_id=subject.api_key.id,
        voice_config=voice_config,
    )

    logger.info(
        "Created voice token: relationship=%s (%s), config=%s",
        relationship.id,
        "created" if created else "existing",
        voice_config.pipeline_type,
    )

    return VoiceTokenResponse(
        token=token,
        relationship_id=relationship.id,
        expires_in=VOICE_TOKEN_EXPIRY_SECONDS,
        ws_url=_make_voice_ws_url(companion_id, user_id),
    )


@router.post(
    "/relationships/{relationship_id}/voice/token",
    response_model=VoiceTokenResponse,
    summary="Get voice WebSocket token (direct)",
    description="Exchange API key for voice token using relationship ID directly.",
)
async def create_voice_token_direct(
    relationship_id: UUID,
    request: VoiceTokenRequest | None = None,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> VoiceTokenResponse:
    """Create voice token using relationship ID directly."""
    # Get relationship
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    # Verify companion access
    companion = await CompanionRepository.get_companion_by_id_no_auth(
        conn, relationship.companion_id
    )
    if not companion or companion.project_id != subject.project.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Relationship does not belong to this project",
        )

    # Get voice config
    voice_config = (
        request.voice_config if request and request.voice_config else create_default_voice_config()
    )
    voice_config = normalize_voice_config(voice_config)

    # Create token
    token, _expires_at = _create_voice_token(
        relationship_id=relationship_id,
        api_key_id=subject.api_key.id,
        voice_config=voice_config,
    )

    return VoiceTokenResponse(
        token=token,
        relationship_id=relationship_id,
        expires_in=VOICE_TOKEN_EXPIRY_SECONDS,
        ws_url=f"wss://{os.getenv('PUBLIC_HOST', 'localhost:8100')}/v2/relationships/{relationship_id}/voice/connect",
    )


# ──────────────────────────────────────────────────────────────────────────────
# WebSocket Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.websocket("/companions/{companion_id}/relationships/{user_id}/voice/connect")
async def voice_websocket_connect(
    websocket: WebSocket,
    companion_id: UUID,
    user_id: str,
    token: str = Query(...),
):
    """Voice WebSocket endpoint for real-time voice communication."""
    await websocket.accept()

    # Verify token and get relationship
    try:
        async with get_db_connection() as conn:
            relationship = await RelationshipRepository.get_by_companion_and_user(
                conn, companion_id=companion_id, user_id=user_id
            )
            if not relationship:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Relationship not found")
                return

            try:
                _rel_id, api_key_id, token_expires_at, voice_config = _verify_voice_token(
                    token, relationship.id
                )
            except jwt.ExpiredSignatureError:
                await websocket.close(WS_CLOSE_TOKEN_EXPIRED, "Token expired")
                return
            except (jwt.InvalidTokenError, ValueError) as e:
                logger.warning("Invalid voice token: %s", e)
                await websocket.close(WS_CLOSE_INVALID_TOKEN, "Invalid token")
                return

            companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
            if not companion:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Companion not found")
                return

    except Exception:
        logger.exception("Error during voice WebSocket auth")
        await websocket.close(WS_CLOSE_INTERNAL_ERROR, "Internal error")
        return

    # Run voice session
    await _run_voice_session(
        websocket=websocket,
        relationship=relationship,
        companion=companion,
        api_key_id=api_key_id,
        token_expires_at=token_expires_at,
        voice_config=voice_config,
    )


@router.websocket("/relationships/{relationship_id}/voice/connect")
async def voice_websocket_connect_direct(
    websocket: WebSocket,
    relationship_id: UUID,
    token: str = Query(...),
):
    """Voice WebSocket endpoint using relationship ID directly."""
    await websocket.accept()

    try:
        async with get_db_connection() as conn:
            relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
            if not relationship:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Relationship not found")
                return

            try:
                _rel_id, api_key_id, token_expires_at, voice_config = _verify_voice_token(
                    token, relationship_id
                )
            except jwt.ExpiredSignatureError:
                await websocket.close(WS_CLOSE_TOKEN_EXPIRED, "Token expired")
                return
            except (jwt.InvalidTokenError, ValueError) as e:
                logger.warning("Invalid voice token: %s", e)
                await websocket.close(WS_CLOSE_INVALID_TOKEN, "Invalid token")
                return

            companion = await CompanionRepository.get_companion_by_id_no_auth(
                conn, relationship.companion_id
            )
            if not companion:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Companion not found")
                return

    except Exception:
        logger.exception("Error during voice WebSocket auth")
        await websocket.close(WS_CLOSE_INTERNAL_ERROR, "Internal error")
        return

    await _run_voice_session(
        websocket=websocket,
        relationship=relationship,
        companion=companion,
        api_key_id=api_key_id,
        token_expires_at=token_expires_at,
        voice_config=voice_config,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Voice Session Handler
# ──────────────────────────────────────────────────────────────────────────────


async def _run_voice_session(
    websocket: WebSocket,
    relationship: Any,
    companion: Any,
    api_key_id: UUID,
    token_expires_at: datetime,
    voice_config: VoiceConfig,
) -> None:
    """Run a voice session for a relationship."""
    state = await voice_connection_manager.connect(
        websocket=websocket,
        relationship_id=relationship.id,
        api_key_id=api_key_id,
        token_expires_at=token_expires_at,
        voice_config=voice_config,
    )

    try:
        pending_end_call = False
        closing_for_end_call = False
        end_call_enqueued = False
        relationship_missing = False

        async def close_session_for_end_call(trigger: str) -> None:
            nonlocal closing_for_end_call, pending_end_call, end_call_enqueued
            if closing_for_end_call:
                return
            closing_for_end_call = True
            pending_end_call = False
            logger.info(
                "[V2_VOICE] Closing session after end-call request (trigger=%s) relationship=%s",
                trigger,
                relationship.id,
            )
            try:
                if not end_call_enqueued:
                    end_call_enqueued = True
                    await task.queue_frame(EndFrame())
                    return
            except Exception:
                logger.warning(
                    "[V2_VOICE] Failed to enqueue EndFrame for relationship=%s; cancelling task",
                    relationship.id,
                )
            try:
                await task.cancel()
            except Exception:
                pass

        async def request_end_call(reason: str | None = None) -> None:
            nonlocal pending_end_call
            if pending_end_call:
                return
            pending_end_call = True
            logger.info(
                "[V2_VOICE] End-call requested for relationship=%s reason=%s",
                relationship.id,
                reason or "unspecified",
            )

        # Build system prompt
        async with get_db_connection() as conn:
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
            if isinstance(dialogmachine_cfg, dict):
                prompt_override = dialogmachine_cfg.get("prompt_override")
                if isinstance(prompt_override, str) and prompt_override.strip():
                    system_prompt = prompt_override.strip()
                    logger.info(
                        "[V2_VOICE] Applied dialogmachine prompt override for relationship %s",
                        relationship.id,
                    )
                guardrails = dialogmachine_cfg.get("guardrails")
                if isinstance(guardrails, str) and guardrails.strip():
                    system_prompt = (
                        f"{system_prompt.rstrip()}\n\n"
                        f"## Relationship Guardrails\n{guardrails.strip()}"
                    )
                    logger.info(
                        "[V2_VOICE] Applied dialogmachine guardrails for relationship %s",
                        relationship.id,
                    )

            # Load conversation history from relationship messages
            history_rows = await conn.fetch(
                """
                SELECT role, content FROM messages
                WHERE relationship_id = $1
                ORDER BY created_at ASC
                LIMIT 50
                """,
                relationship.id,
            )
            conversation_history = [
                {"role": row["role"], "content": row["content"]} for row in history_rows
            ]

        # Resolve context mode BEFORE building pipeline (relationship > companion > legacy default)
        context_mode = resolve_v2_context_mode(
            relationship.context_mode,
            companion.config,
        )
        logger.info(
            f"[V2_VOICE] context_mode={context_mode}, relationship.context_mode={relationship.context_mode}"
        )

        # Get resolved config to check classifier settings
        resolved = CompanionRuntimeConfig.from_companion_config(companion.config)

        # Set up context injection processor
        # Layer configuration (memory, tools, knowledge, behaviors) comes from companion.config.layers
        # The orchestrator reads these via resolved.is_layer_enabled()
        # Classifier is enabled based on companion config (voice defaults to off, but can be overridden)
        context_config = VoiceContextConfig(
            companion_id=companion.id,
            companion_config=companion.config,
            relationship_id=relationship.id,
            external_user_id=relationship.external_user_id,
            use_layered=context_mode == "layered",
            use_classifier=resolved.classifier.enabled,
            include_profile=resolve_profile_in_prompt_enabled(
                relationship.config,
                companion.config,
            ),
            profile_data=relationship.profile,
        )

        # Callback to forward tool result events to the frontend via WebSocket
        # Used by onboarding companion to notify when a companion is created
        def on_tool_result(event):
            """Forward tool result events to WebSocket as JSON."""
            try:
                event_data = {
                    "type": "tool_result",
                    "tool": event.meta.get("tool") if event.meta else None,
                    "success": event.meta.get("success", False) if event.meta else False,
                    "result": event.meta.get("result")
                    if event.meta
                    else None,  # Contains companion_id for onboarding
                    "ts_ms": event.ts_ms,
                }
                asyncio.create_task(websocket.send_text(json.dumps(event_data)))
                logger.info(f"[V2_VOICE] Forwarded tool result event: {event_data}")
            except Exception as e:
                logger.warning(f"[V2_VOICE] Failed to forward tool result: {e}")

        # Create context processor (llm_context will be set after pipeline is built)
        context_processor = VoiceContextProcessor(
            config=context_config,
            on_tool_result=on_tool_result,
        )

        # Track audio activity
        audio_activity = {"heard": False}

        def on_audio_activity(nbytes: int):
            audio_activity["heard"] = True

        # Build pipeline with context processor
        components = build_voice_pipeline(
            websocket=websocket,
            voice_config=voice_config,
            system_prompt=system_prompt,
            conversation_history=conversation_history,
            on_audio_activity=on_audio_activity,
            context_processor=context_processor if context_mode == "layered" else None,
            companion_id=str(companion.id),
            relationship_id=str(relationship.id),
            user_id=relationship.external_user_id,
            on_end_call_requested=request_end_call,
        )
        state.components = components

        # Set LLM context on processor after pipeline is built
        if context_mode == "layered":
            context_processor.set_llm_context(components.llm_context)

        # Create pipeline task
        task = PipelineTask(components.pipeline)
        state.pipeline_task = task

        # Set up transcript handler for message persistence
        # Note: Context injection is now handled by VoiceContextProcessor in the pipeline
        @components.transcript.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            nonlocal relationship_missing, pending_end_call, closing_for_end_call

            async def _maybe_close_after_assistant_transcript() -> None:
                # Give output handlers a short window so we don't race close against
                # sentence-chunked TTS scheduling.
                await asyncio.sleep(0.6)
                if pending_end_call and not closing_for_end_call and not state.is_bot_speaking:
                    await close_session_for_end_call("assistant_transcript")

            for msg in frame.messages:
                if isinstance(msg, TranscriptionMessage):
                    timestamp = f"[{msg.timestamp}] " if msg.timestamp else ""
                    logger.info(f"[V2_VOICE] {timestamp}{msg.role}: {msg.content}")

                    # Persist message
                    if msg.content.strip():
                        try:
                            async with get_db_connection() as conn:
                                # Get next sequence number
                                seq_row = await conn.fetchrow(
                                    "SELECT next_relationship_message_seq($1) as seq",
                                    relationship.id,
                                )
                                seq = seq_row["seq"] if seq_row else None

                                await conn.execute(
                                    """
                                    INSERT INTO messages (id, relationship_id, role, content, seq, input_modality)
                                    VALUES ($1, $2, $3, $4, $5, 'voice')
                                    """,
                                    uuid4(),
                                    relationship.id,
                                    msg.role,
                                    msg.content,
                                    seq,
                                )
                        except Exception as e:
                            logger.warning(f"[V2_VOICE] Failed to persist message: {e}")
                            if not relationship_missing and "messages_relationship_id_fkey" in str(
                                e
                            ):
                                relationship_missing = True
                                logger.warning(
                                    "[V2_VOICE] Relationship missing during active session; closing connection "
                                    "for relationship=%s",
                                    relationship.id,
                                )

                                async def _close_missing_relationship() -> None:
                                    try:
                                        await task.cancel()
                                    except Exception:
                                        pass
                                    try:
                                        await websocket.close(
                                            WS_CLOSE_NOT_FOUND,
                                            "Relationship not found",
                                        )
                                    except Exception:
                                        pass

                                asyncio.create_task(_close_missing_relationship())

                    # End-call fallback trigger:
                    # Some transport builds do not expose on_bot_stopped_speaking.
                    # When Fast Brain already requested end_call and assistant transcript lands,
                    # close the session here.
                    if msg.role == "assistant" and pending_end_call and not closing_for_end_call:
                        asyncio.create_task(_maybe_close_after_assistant_transcript())

        # Set up transport event handlers
        @components.transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("[V2_VOICE] Client connected")

        @components.transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("[V2_VOICE] Client disconnected")
            await task.cancel()

        # Audio gating during bot speech
        @components.transport.event_handler("on_bot_started_speaking")
        async def on_bot_started_speaking(transport, *args, **kwargs):
            state.is_bot_speaking = True
            components.serializer.set_drop_input(True)

        @components.transport.event_handler("on_bot_stopped_speaking")
        async def on_bot_stopped_speaking(transport, *args, **kwargs):
            nonlocal closing_for_end_call, pending_end_call
            state.is_bot_speaking = False
            components.serializer.set_drop_input(False)

            # Flush any buffered barge-in audio
            buffered = components.serializer.drain_buffer()
            if buffered:
                logger.debug(f"[V2_VOICE] Flushing buffered audio: {len(buffered)} bytes")
                await task.queue_frames(
                    [
                        InputAudioRawFrame(
                            audio=buffered,
                            sample_rate=16000,
                            num_channels=1,
                        )
                    ]
                )

            if pending_end_call and not closing_for_end_call:
                asyncio.create_task(close_session_for_end_call("bot_stopped_speaking"))

        # Run pipeline
        runner = PipelineRunner(handle_sigint=False)
        logger.info("[V2_VOICE] Starting voice pipeline for relationship %s", relationship.id)
        await runner.run(task)

        if closing_for_end_call:
            try:
                await websocket.close(WS_CLOSE_NORMAL, "Assistant ended call")
            except Exception:
                pass

    except Exception as e:
        logger.exception("[V2_VOICE] Error in voice session: %s", e)
        try:
            await websocket.close(WS_CLOSE_INTERNAL_ERROR, str(e))
        except Exception:
            pass
    finally:
        await voice_connection_manager.disconnect(state)
        logger.info("[V2_VOICE] Voice session ended for relationship %s", relationship.id)
