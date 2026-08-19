# server/app/routers/voice/v1.py
"""v1 Voice API routes (backwards compatibility).

Maintains the original /sessions API for existing clients while using
the new modular voice pipeline internally.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Dict
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from pipecat.frames.frames import InputAudioRawFrame, TranscriptionMessage
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask

from ...auth import get_current_user
from ...context import (
    build_context_plan,
    resolve_context_mode,
)
from ...db import (
    cleanup_session_conversation,
    get_db_connection,
    persist_message,
    register_session_conversation,
)
from ...models.user import User
from ...repositories.companion import CompanionRepository
from ...repositories.conversation import (
    create_conversation_for_companion,
    create_conversation_for_companion_version,
    get_conversation_messages,
)
from ...services.context_assembly import build_effective_system_prompt
from .context import VoiceContextConfig, VoiceContextInjector
from .models import PipelineType, SessionCreate, SessionCreated, VoiceConfig
from .pipeline import build_voice_pipeline, normalize_voice_config
from .providers import get_all_voice_mappings

router = APIRouter(prefix="/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# In-memory session registries
# ──────────────────────────────────────────────────────────────────────────────

_active_tasks: Dict[str, PipelineTask] = {}
_session_cfg: Dict[str, SessionCreate] = {}
_session_owner: Dict[str, str] = {}
_session_token: Dict[str, str] = {}
_session_context_events: Dict[str, list] = {}


@dataclass
class ShareSessionContext:
    """Context for public share sessions."""

    share_id: UUID
    visitor_token_hash: bytes
    conversation_id: UUID


_session_share_ctx: Dict[str, ShareSessionContext] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────────────────────────────────────


def _make_ws_url(session_id: str) -> str:
    """Generate WebSocket URL for session."""
    scheme = "wss" if os.getenv("ENV") == "prod" else "ws"
    host = os.getenv("PUBLIC_HOST", "localhost:8100")
    return f"{scheme}://{host}/sessions/ws/{session_id}"


def _register_session(
    payload: SessionCreate,
    *,
    owner_id: str | None = None,
    share_ctx: ShareSessionContext | None = None,
    context_events: list | None = None,
) -> SessionCreated:
    """Store session config and issue a one-time WebSocket token."""
    session_id = str(uuid4())
    _session_cfg[session_id] = payload

    if owner_id:
        _session_owner[session_id] = owner_id
    if share_ctx:
        _session_share_ctx[session_id] = share_ctx
    if context_events:
        _session_context_events[session_id] = context_events

    token = secrets.token_urlsafe(32)
    _session_token[session_id] = token
    return SessionCreated(id=session_id, ws_url=f"{_make_ws_url(session_id)}?t={token}")


async def get_or_create_companion_version_for_prompt(
    companion_id: str,
    system_prompt: str,
    *,
    effective_system_prompt: str | None = None,
) -> UUID | None:
    """Get existing companion version or create new one if system prompt changed."""
    try:
        async with get_db_connection() as conn:
            owner_row = await conn.fetchrow(
                "SELECT owner_id FROM companions WHERE id = $1",
                UUID(companion_id),
            )
            if not owner_row:
                return None

            companion = await CompanionRepository.get_companion_by_id(
                conn, UUID(companion_id), owner_row["owner_id"]
            )
            if not companion:
                return None

            current_prompt = companion.config.system_prompt.get_effective_prompt()
            normalized_current = current_prompt.strip() if current_prompt else ""
            normalized_new = system_prompt.strip()

            if normalized_current == normalized_new:
                if companion.current_version:
                    return companion.current_version.id
                return None

            new_config = companion.config
            new_config.system_prompt.full_system_prompt = system_prompt

            version_id = await CompanionRepository.create_companion_version_from_config(
                conn,
                UUID(companion_id),
                new_config,
                status="SESSION",
                effective_system_prompt=effective_system_prompt,
            )
            return version_id

    except Exception as e:
        logger.error(f"Failed to get/create companion version: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Public API for share sessions
# ──────────────────────────────────────────────────────────────────────────────


def create_share_voice_session(
    payload: SessionCreate,
    *,
    share_id: UUID,
    visitor_token_hash: bytes,
    conversation_id: UUID,
) -> SessionCreated:
    """Register a voice session originating from public sharing flow."""
    if payload.voice_config is None:
        raise ValueError("Public voice sessions require a voice configuration")

    normalize_voice_config(payload.voice_config)

    ctx = ShareSessionContext(
        share_id=share_id,
        visitor_token_hash=visitor_token_hash,
        conversation_id=conversation_id,
    )
    return _register_session(payload, owner_id=None, share_ctx=ctx)


async def cancel_active_session_tasks(timeout: float = 8.0) -> None:
    """Cancel all active session tasks (for graceful shutdown)."""
    try:
        if not _active_tasks:
            return

        tasks = list(_active_tasks.values())
        _active_tasks.clear()

        async def _cancel(t: PipelineTask):
            try:
                await t.cancel()
            except Exception:
                pass

        await asyncio.wait_for(
            asyncio.gather(*(_cancel(t) for t in tasks), return_exceptions=True),
            timeout=timeout,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/voice-mappings")
async def get_voice_mappings():
    """Get available voice mappings for all providers."""
    return get_all_voice_mappings()


@router.post("/", response_model=SessionCreated, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, user: User = Depends(get_current_user)):
    """Create a new voice session (v1 API)."""
    logger.info(
        f"[V1_SESSION] Creating session: companionId={payload.companion_id}, "
        f"voiceConfig={payload.voice_config}"
    )

    # Validate companion ownership
    async with get_db_connection() as conn:
        owner_ok = await conn.fetchval(
            "SELECT COUNT(*) FROM companions WHERE id = $1 AND owner_id = $2",
            UUID(payload.companion_id),
            user.id,
        )
        if not owner_ok:
            raise HTTPException(
                status_code=404,
                detail=f"Companion {payload.companion_id} not found",
            )

    # Resolve context mode
    comp_cfg = None
    try:
        async with get_db_connection() as conn:
            comp_row = await CompanionRepository.get_companion_by_id_no_auth(
                conn, UUID(payload.companion_id)
            )
            comp_cfg = comp_row.config if comp_row else None
    except Exception:
        pass

    mode_resolution = resolve_context_mode(
        request_override=payload.context_mode,
        companion_config=comp_cfg,
    )
    use_layered = mode_resolution.use_layered

    # Build effective system prompt
    context_events: list = []
    try:
        async with get_db_connection() as conn:
            if use_layered:
                initial_plan = await build_context_plan(
                    conn=conn,
                    companion_id=UUID(payload.companion_id),
                    companion_config=comp_cfg,
                    conversation_id=None,
                    user_message=None,
                    include_memory=False,
                    include_knowledge=False,
                    include_behaviors=False,
                    append_core_prompt=True,
                    append_history=False,
                    context_mode_override="layered",
                )
                system_msgs = [
                    m["content"] for m in initial_plan.messages if m.get("role") == "system"
                ]
                final_prompt = "\n\n".join(system_msgs) if system_msgs else payload.system_prompt
                if initial_plan.events:
                    context_events.extend(initial_plan.events)
            else:
                effective_prompt, builder_prompt = await build_effective_system_prompt(
                    conn, companion_id=UUID(payload.companion_id), use_cache=False
                )
                final_prompt = effective_prompt or builder_prompt or payload.system_prompt

        if not (final_prompt or "").strip():
            final_prompt = "You are a helpful and friendly companion."
        payload.system_prompt = final_prompt
    except Exception as e:
        logger.warning(f"[V1_SESSION] Failed to build prompt, using fallback: {e}")
        if not payload.system_prompt.strip():
            payload.system_prompt = "You are a helpful and friendly companion."

    # Initialize voice config with defaults
    if not payload.voice_config and payload.voice:
        valid_voices = ["alloy", "nova", "shimmer", "echo", "fable", "onyx"]
        voice = payload.voice.lower() if payload.voice.lower() in valid_voices else "alloy"
        payload.voice_config = VoiceConfig(
            pipeline_type=PipelineType.STT_LLM_TTS,
            voice_name=voice,
        )
    elif not payload.voice_config:
        payload.voice_config = VoiceConfig(pipeline_type=PipelineType.STT_LLM_TTS)

    # Normalize config (auto-migrates OpenAI Realtime to STT-LLM-TTS)
    normalize_voice_config(payload.voice_config)

    result = _register_session(payload, owner_id=str(user.id), context_events=context_events)
    logger.info(f"[V1_SESSION] Created session {result.id}")
    return result


@router.patch("/{session_id}", status_code=status.HTTP_200_OK)
async def update_session(
    session_id: str,
    payload: SessionCreate,
    user: User = Depends(get_current_user),
):
    """Update session configuration (v1 API)."""
    if session_id in _active_tasks:
        raise HTTPException(
            status_code=409,
            detail="Cannot update an active session.",
        )

    if session_id not in _session_cfg:
        raise HTTPException(status_code=404, detail="Session not found")
    if _session_owner.get(session_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")

    _session_cfg[session_id] = payload
    logger.info(f"[V1_SESSION] Updated session {session_id}")
    return {"status": "updated"}


@router.websocket("/ws/{session_id}")
async def voice_session(websocket: WebSocket, session_id: str):
    """Voice WebSocket endpoint (v1 API)."""
    logger.info(f"[V1_WS] Connection attempt for session {session_id}")

    # Validate session and token
    token = websocket.query_params.get("t") if hasattr(websocket, "query_params") else None
    if session_id not in _session_cfg:
        logger.error(f"[V1_WS] Session {session_id} not found")
        await websocket.close(code=1008, reason="Session not found")
        return

    expected = _session_token.get(session_id)
    if not token or not expected or token != expected:
        logger.error(f"[V1_WS] Invalid token for session {session_id}")
        await websocket.close(code=1008, reason="Unauthorized")
        return

    # One-time use token
    _session_token.pop(session_id, None)

    await websocket.accept()
    logger.info(f"[V1_WS] WebSocket accepted for session {session_id}")

    cfg = _session_cfg[session_id]
    share_ctx = _session_share_ctx.get(session_id)

    if share_ctx and not cfg.conversation_id:
        try:
            cfg.conversation_id = str(share_ctx.conversation_id)
        except Exception:
            pass

    try:
        # Load conversation history and companion config
        conversation_history = None
        comp_cfg = None
        conversation_id = None
        ext_user = cfg.client_external_user_id or session_id

        async with get_db_connection() as conn:
            # Load history if continuing conversation
            if cfg.conversation_id:
                try:
                    history_rows = await get_conversation_messages(conn, UUID(cfg.conversation_id))
                    conversation_history = [
                        {"role": row["role"], "content": row["content"]} for row in history_rows
                    ]
                except Exception as e:
                    logger.warning(f"[V1_WS] Failed to load history: {e}")

            # Load companion config
            try:
                comp_row = await CompanionRepository.get_companion_by_id_no_auth(
                    conn, UUID(cfg.companion_id)
                )
                comp_cfg = comp_row.config if comp_row else None
            except Exception:
                pass

            # Resolve context mode
            use_layered = False
            if cfg.context_mode:
                use_layered = cfg.context_mode.lower() == "layered"
            elif comp_cfg:
                use_layered = getattr(comp_cfg, "context_mode", "legacy") == "layered"

            # Setup conversation
            if share_ctx:
                conversation_id = share_ctx.conversation_id
                register_session_conversation(session_id, conversation_id)
            elif cfg.conversation_id:
                conversation_id = UUID(cfg.conversation_id)
                register_session_conversation(session_id, conversation_id)
            else:
                # Create new conversation
                effective_prompt, builder_prompt = await build_effective_system_prompt(
                    conn, companion_id=UUID(cfg.companion_id)
                )
                version_id = await get_or_create_companion_version_for_prompt(
                    cfg.companion_id,
                    builder_prompt or cfg.system_prompt,
                    effective_system_prompt=effective_prompt or cfg.system_prompt,
                )

                if version_id:
                    conversation_id = await create_conversation_for_companion_version(
                        conn, UUID(cfg.companion_id), version_id, ext_user
                    )
                else:
                    conversation_id = await create_conversation_for_companion(
                        conn, UUID(cfg.companion_id), ext_user
                    )

                register_session_conversation(session_id, conversation_id)

        # Build pipeline
        audio_activity = {"heard": False}

        def on_audio_activity(nbytes: int):
            audio_activity["heard"] = True

        components = build_voice_pipeline(
            websocket=websocket,
            voice_config=cfg.voice_config,
            system_prompt=cfg.system_prompt,
            conversation_history=conversation_history,
            on_audio_activity=on_audio_activity,
        )

        task = PipelineTask(components.pipeline)
        _active_tasks[session_id] = task

        # Setup context injection
        if conversation_id:
            context_config = VoiceContextConfig(
                companion_id=UUID(cfg.companion_id),
                companion_config=comp_cfg,
                conversation_id=conversation_id,
                external_user_id=ext_user,
                use_layered=use_layered,
            )
            context_injector = VoiceContextInjector(
                config=context_config,
                llm_context=components.llm_context,
            )

            # Setup transcript handler
            @components.transcript.event_handler("on_transcript_update")
            async def on_transcript_update(processor, frame):
                for msg in frame.messages:
                    if isinstance(msg, TranscriptionMessage):
                        logger.info(f"[V1_TRANSCRIPT] {msg.role}: {msg.content}")

                        # Persist message
                        if msg.content.strip():
                            await persist_message(
                                conversation_id,
                                msg.role,
                                msg.content,
                                input_modality="voice",
                            )

                        # Context injection for user messages
                        if msg.role == "user" and msg.content.strip():
                            context_injected = await context_injector.on_user_transcription(
                                msg.content
                            )
                            if context_injected:
                                try:
                                    await task.queue_frames(
                                        [components.context_aggregator.user().get_context_frame()]
                                    )
                                except Exception as e:
                                    logger.warning(f"[V1_WS] Failed to queue context: {e}")

        # Setup transport handlers
        @components.transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("[V1_WS] Client connected")

        @components.transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("[V1_WS] Client disconnected")
            await task.cancel()

        # Audio gating
        @components.transport.event_handler("on_bot_started_speaking")
        async def on_bot_started_speaking(transport, *args, **kwargs):
            components.serializer.set_drop_input(True)

        @components.transport.event_handler("on_bot_stopped_speaking")
        async def on_bot_stopped_speaking(transport, *args, **kwargs):
            components.serializer.set_drop_input(False)
            buffered = components.serializer.drain_buffer()
            if buffered:
                await task.queue_frames(
                    [InputAudioRawFrame(audio=buffered, sample_rate=16000, num_channels=1)]
                )

        # Run pipeline
        runner = PipelineRunner(handle_sigint=False)
        logger.info("[V1_WS] Starting pipeline")
        await runner.run(task)

    except Exception as e:
        logger.error(f"[V1_WS] Error in session {session_id}: {e}", exc_info=True)
        await websocket.close(code=1011, reason=f"Pipeline error: {e!s}")
    finally:
        logger.info(f"[V1_WS] Cleaning up session {session_id}")
        _active_tasks.pop(session_id, None)
        _session_cfg.pop(session_id, None)
        _session_share_ctx.pop(session_id, None)
        _session_owner.pop(session_id, None)
        cleanup_session_conversation(session_id)
