"""Public unauthenticated endpoints for shared companions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Any, Coroutine, List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..constants import DEFAULT_TEXT_LLM_MAX_TOKENS
from ..db import get_db, get_db_connection
from ..models.share import CompanionShare, ShareStatus
from ..repositories.companion import CompanionRepository
from ..repositories.conversation import (
    add_message_returning,
    create_conversation_for_companion,
    create_conversation_for_companion_version,
    get_conversation_messages,
)
from ..repositories.share import (
    CompanionShareRepository,
    CompanionShareSessionRepository,
    ShareRateLimitExceeded,
)
from ..routers.sessions import (
    LLMProvider,
    PipelineType,
    SessionCreate,
    STTProvider,
    TTSProvider,
    VoiceConfig,
    create_share_voice_session,
)
from ..services.context_assembly import (
    build_effective_system_prompt_for_config,
    build_transient_memory_block,
)
from ..services.context_builder import (
    assemble_llm_messages,
    check_and_trigger_summary,
    get_full_history,
    update_history_cache_post_turn,
)
from ..services.llm import generate_llm_response_direct, resolve_max_tokens
from ..services.llm_resolver import resolve_llm_client
from ..services.memory_service import MemoryService
from ..services.modal_gateway import dispatch_memory_ingest_job
from ..services.share_tokens import hash_share_token, verify_share_token
from .companion_shares import DEFAULT_SHARE_CONTEXT_DESCRIPTION

router = APIRouter(prefix="/public/companions", tags=["public-companions"])

logger = logging.getLogger(__name__)


_PENDING_STREAM_TASKS: set[asyncio.Task[Any]] = set()


def _schedule_background(coro: Coroutine[Any, Any, Any]) -> None:
    """Schedule a background coroutine and track its lifecycle."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("[PUBLIC] No running loop available; dropping background task")
        return

    task = loop.create_task(coro)
    _PENDING_STREAM_TASKS.add(task)

    def _cleanup(t: asyncio.Task[Any]) -> None:
        _PENDING_STREAM_TASKS.discard(t)
        try:
            t.result()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("[PUBLIC] Background task failed")

    task.add_done_callback(_cleanup)


async def _run_in_thread(func, *args, **kwargs) -> Any:
    """Run a sync callable in the default thread pool."""
    return await asyncio.to_thread(func, *args, **kwargs)


class PublicShareMeta(BaseModel):
    slug: str
    display_name: str | None = None
    description: str | None = None
    allow_text: bool
    allow_voice: bool
    require_auth: bool
    expose_status_events: bool = False


class PublicTextSessionRequest(BaseModel):
    visitor_token: str | None = Field(default=None, max_length=255)


class PublicTextSessionResponse(BaseModel):
    share_id: UUID
    conversation_id: UUID
    visitor_token: str
    allow_text: bool
    allow_voice: bool


class PublicMessageRequest(BaseModel):
    visitor_token: str = Field(..., max_length=255)
    content: str = Field(..., min_length=1, max_length=4000)


class PublicTurnMessage(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class PublicMessageResponse(BaseModel):
    user_message: PublicTurnMessage
    assistant_message: PublicTurnMessage
    timings: dict | None = None


class PublicVoiceSessionRequest(BaseModel):
    visitor_token: str | None = Field(default=None, max_length=255)
    conversation_id: UUID | None = None


class PublicVoiceSessionResponse(BaseModel):
    share_id: UUID
    conversation_id: UUID
    visitor_token: str
    session_id: str
    ws_url: str
    pipeline_type: str


async def _get_active_share(conn: asyncpg.Connection, slug: str) -> CompanionShare:
    share = await CompanionShareRepository.get_by_slug(conn, slug)
    if not share or share.status != ShareStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Shared companion not found"
        )
    if share.require_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Authentication required for this share"
        )
    return share


@router.get("/{slug}/meta", response_model=PublicShareMeta)
async def get_public_meta(
    slug: str,
    conn: asyncpg.Connection = Depends(get_db),
):
    share = await _get_active_share(conn, slug)
    companion = await conn.fetchrow(
        "SELECT name, description FROM companions WHERE id = $1",
        share.companion_id,
    )
    description = share.description or (companion["description"] if companion else None)
    if description is None or not str(description).strip():
        description = DEFAULT_SHARE_CONTEXT_DESCRIPTION

    return PublicShareMeta(
        slug=share.slug,
        display_name=share.display_name or (companion["name"] if companion else None),
        description=description,
        allow_text=share.allow_text,
        allow_voice=share.allow_voice,
        require_auth=share.require_auth,
        expose_status_events=getattr(share, "expose_status_events", False),
    )


async def _create_share_conversation(
    conn: asyncpg.Connection,
    share: CompanionShare,
    external_user_id: str,
    share_token_hash: bytes,
) -> UUID:
    if share.version_id:
        return await create_conversation_for_companion_version(
            conn,
            share.companion_id,
            share.version_id,
            external_user_id,
            share_id=share.id,
            share_token_hash=share_token_hash,
        )
    return await create_conversation_for_companion(
        conn,
        share.companion_id,
        external_user_id,
        share_id=share.id,
        share_token_hash=share_token_hash,
    )


async def _resolve_share_snapshot(
    conn: asyncpg.Connection,
    share: CompanionShare,
) -> dict:
    detail = await CompanionRepository.get_companion_by_id_no_auth(conn, share.companion_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    actual_memory_enabled = False
    config = getattr(detail, "config", None)
    if config:
        try:
            actual_memory_enabled = bool(getattr(config.memory, "enabled", False))
        except Exception:
            actual_memory_enabled = False

    if share.config_snapshot:
        snap = dict(share.config_snapshot)
        pipeline = snap.get("voice_pipeline") or {}
        if not snap.get("llm_provider") and isinstance(pipeline, dict):
            llm_from_pipeline = pipeline.get("llm_provider")
            if llm_from_pipeline:
                snap["llm_provider"] = llm_from_pipeline
        if "temperature" not in snap or snap.get("temperature") is None:
            if isinstance(pipeline, dict) and pipeline.get("temperature") is not None:
                snap["temperature"] = pipeline.get("temperature")
        if "memory_enabled" not in snap:
            snap["memory_enabled"] = actual_memory_enabled
        return snap

    system_prompt = ""
    temperature = 0.7
    if config:
        try:
            system_prompt = config.system_prompt.get_effective_prompt()
        except Exception:
            system_prompt = getattr(config, "system_prompt", "")
        try:
            temperature = float(config.voice.temperature)
        except Exception:
            pass

    snapshot = {
        "system_prompt": system_prompt,
        "temperature": temperature,
        "llm_provider": "openai-gpt4o-mini",
        "memory_enabled": actual_memory_enabled,
    }

    version_id = getattr(getattr(detail, "current_version", None), "id", None)
    await CompanionShareRepository.update(
        conn,
        share.id,
        config_snapshot=snapshot,
        version_id=version_id or share.version_id,
    )
    return snapshot


def _coerce_enum(enum_cls, value):
    if value is None:
        return None
    try:
        return enum_cls(value)
    except Exception:
        return None


def _snapshot_voice_config(snapshot: dict) -> VoiceConfig:
    """Build VoiceConfig from snapshot, converting legacy configs to STT-LLM-TTS."""
    pipeline = snapshot.get("voice_pipeline") or {}

    # Always use STT_LLM_TTS - legacy openai-realtime configs are auto-converted
    pipeline_type = PipelineType.STT_LLM_TTS

    voice_name = pipeline.get("voice_name") or pipeline.get("openai_voice") or "alloy"
    stt_provider = _coerce_enum(STTProvider, pipeline.get("stt_provider")) or STTProvider.OPENAI
    llm_provider = (
        _coerce_enum(LLMProvider, pipeline.get("llm_provider") or snapshot.get("llm_provider"))
        or LLMProvider.OPENAI_GPT4O
    )
    tts_provider = _coerce_enum(TTSProvider, pipeline.get("tts_provider")) or TTSProvider.OPENAI

    temp_source = pipeline.get("temperature")
    if temp_source is None:
        temp_source = snapshot.get("temperature")
    try:
        temperature = float(temp_source) if temp_source is not None else 0.7
    except Exception:
        temperature = 0.7

    return VoiceConfig(
        pipeline_type=pipeline_type,
        voice_name=voice_name,
        stt_provider=stt_provider,
        llm_provider=llm_provider,
        tts_provider=tts_provider,
        temperature=temperature,
    )


async def _get_conversation_and_share(
    conn: asyncpg.Connection,
    conversation_id: UUID,
) -> tuple[asyncpg.Record, CompanionShare]:
    convo = await conn.fetchrow(
        """
        SELECT
            id,
            share_id,
            share_token_hash,
            companion_id,
            external_user_id
        FROM conversations
        WHERE id = $1
        """,
        conversation_id,
    )
    if not convo or not convo.get("share_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    share = await CompanionShareRepository.get_by_id(conn, convo["share_id"])
    if not share or share.status != ShareStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Shared companion not found"
        )

    return convo, share


@router.post("/{slug}/sessions/text", response_model=PublicTextSessionResponse)
async def create_public_text_session(
    slug: str,
    payload: PublicTextSessionRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    share = await _get_active_share(conn, slug)
    if not share.allow_text:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Text mode disabled for this companion"
        )

    visitor_token = payload.visitor_token or secrets.token_urlsafe(24)
    if not visitor_token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="visitor_token cannot be empty"
        )

    token_hash = hash_share_token(visitor_token, share.id)
    session_record = await CompanionShareSessionRepository.get_for_share_and_token(
        conn, share.id, token_hash
    )
    conversation_id: UUID | None = None
    created_new_visitor = False

    if session_record and session_record.get("conversation_id"):
        conversation_id = session_record["conversation_id"]
        now = datetime.now(UTC)
        await CompanionShareSessionRepository.record_activity(
            conn,
            share_id=share.id,
            visitor_token_hash=token_hash,
            conversation_id=conversation_id,
            message_delta=0,
            voice_sessions_delta=0,
            now=now,
        )
    else:
        external_user_id = f"share-{share.slug}-{visitor_token[:12]}"
        async with conn.transaction():
            conversation_id = await _create_share_conversation(
                conn,
                share,
                external_user_id,
                token_hash,
            )
            created_new_visitor = await CompanionShareSessionRepository.record_activity(
                conn,
                share_id=share.id,
                visitor_token_hash=token_hash,
                conversation_id=conversation_id,
                message_delta=0,
                voice_sessions_delta=0,
                now=datetime.now(UTC),
            )
            if created_new_visitor:
                await CompanionShareRepository.increment_totals(
                    conn,
                    share.id,
                    sessions_delta=1,
                )

    return PublicTextSessionResponse(
        share_id=share.id,
        conversation_id=conversation_id,
        visitor_token=visitor_token,
        allow_text=share.allow_text,
        allow_voice=share.allow_voice,
    )


@router.post("/{slug}/sessions/voice", response_model=PublicVoiceSessionResponse)
async def create_public_voice_session(
    slug: str,
    payload: PublicVoiceSessionRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    share = await _get_active_share(conn, slug)
    if not share.allow_voice:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Voice mode disabled for this companion"
        )

    visitor_token = payload.visitor_token or secrets.token_urlsafe(24)
    if not visitor_token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="visitor_token cannot be empty"
        )

    token_hash = hash_share_token(visitor_token, share.id)

    requested_conversation_id: UUID | None = payload.conversation_id
    conversation_id: UUID | None = None
    external_user_id: str | None = None

    now = datetime.now(UTC)

    async with conn.transaction():
        session_record = await CompanionShareSessionRepository.get_for_share_and_token(
            conn, share.id, token_hash
        )
        convo_row: asyncpg.Record | None = None

        if requested_conversation_id:
            convo_row = await conn.fetchrow(
                """
                SELECT id, share_id, share_token_hash, external_user_id
                FROM conversations
                WHERE id = $1
                """,
                requested_conversation_id,
            )
            if not convo_row or convo_row["share_id"] != share.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
                )
            stored_hash = convo_row.get("share_token_hash")
            if not stored_hash or not verify_share_token(visitor_token, share.id, stored_hash):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Invalid visitor token"
                )
        elif session_record and session_record.get("conversation_id"):
            convo_row = await conn.fetchrow(
                """
                SELECT id, share_id, share_token_hash, external_user_id
                FROM conversations
                WHERE id = $1
                """,
                session_record["conversation_id"],
            )
            if convo_row and convo_row["share_id"] != share.id:
                convo_row = None
            if convo_row:
                stored_hash = convo_row.get("share_token_hash")
                if not stored_hash or not verify_share_token(visitor_token, share.id, stored_hash):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN, detail="Invalid visitor token"
                    )

        if convo_row:
            conversation_id = convo_row["id"]
            external_user_id = (
                convo_row.get("external_user_id") or f"share-{share.slug}-{visitor_token[:12]}"
            )
        else:
            external_user_id = f"share-{share.slug}-{visitor_token[:12]}"
            conversation_id = await _create_share_conversation(
                conn,
                share,
                external_user_id,
                token_hash,
            )

        try:
            created_new = await CompanionShareSessionRepository.record_activity(
                conn,
                share_id=share.id,
                visitor_token_hash=token_hash,
                conversation_id=conversation_id,
                message_delta=0,
                voice_sessions_delta=1,
                now=now,
            )
        except ShareRateLimitExceeded as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({exc.bucket})",
            )

        await CompanionShareRepository.increment_totals(
            conn,
            share.id,
            sessions_delta=1 if created_new else 0,
            voice_sessions_delta=1,
        )

    if not conversation_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create conversation",
        )

    if not external_user_id:
        external_user_id = f"share-{share.slug}-{visitor_token[:12]}"

    snapshot = await _resolve_share_snapshot(conn, share)
    voice_config = _snapshot_voice_config(snapshot)
    system_prompt = snapshot.get("system_prompt") or "You are a helpful and friendly companion."

    session_payload = SessionCreate(
        systemPrompt=system_prompt,
        companionId=str(share.companion_id),
        conversationId=str(conversation_id),
        voiceConfig=voice_config,
        clientExternalUserId=external_user_id,
    )

    session_created = create_share_voice_session(
        session_payload,
        share_id=share.id,
        visitor_token_hash=token_hash,
        conversation_id=conversation_id,
    )

    return PublicVoiceSessionResponse(
        share_id=share.id,
        conversation_id=conversation_id,
        visitor_token=visitor_token,
        session_id=session_created.id,
        ws_url=session_created.ws_url,
        pipeline_type=voice_config.pipeline_type.value,
    )


@router.post("/conversations/{conversation_id}/messages", response_model=PublicMessageResponse)
async def send_public_text_message(
    conversation_id: UUID,
    payload: PublicMessageRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    if not payload.visitor_token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="visitor_token is required"
        )

    convo, share = await _get_conversation_and_share(conn, conversation_id)
    if not share.allow_text:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Text mode disabled for this companion"
        )

    token_hash = hash_share_token(payload.visitor_token, share.id)
    if not verify_share_token(payload.visitor_token, share.id, convo["share_token_hash"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid visitor token")

    snapshot = await _resolve_share_snapshot(conn, share)
    timings: dict[str, float] = {}

    try:
        user_row, assistant_row = await _public_text_turn_blocking(
            conversation=convo,
            share=share,
            snapshot=snapshot,
            token_hash=token_hash,
            user_content=payload.content,
            timings=timings,
        )
    except ShareRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({exc.bucket})",
        )

    return PublicMessageResponse(
        user_message=_serialize_message(user_row),
        assistant_message=_serialize_message(assistant_row),
        timings=timings,
    )


def _serialize_message(row: dict) -> PublicTurnMessage:
    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        dt = created_at
    else:
        try:
            dt = datetime.fromisoformat(str(created_at))
        except Exception:
            dt = datetime.now(UTC)
    return PublicTurnMessage(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        created_at=dt,
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def send_public_text_message_stream(
    conversation_id: UUID,
    payload: PublicMessageRequest,
):
    # NOTE: We explicitly use get_db_connection() instead of Depends(get_db) here.
    # Depends(get_db) would hold the connection for the ENTIRE streaming duration,
    # exhausting the pool under concurrent load.
    if not payload.visitor_token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="visitor_token is required"
        )

    async with get_db_connection() as conn:
        convo, share = await _get_conversation_and_share(conn, conversation_id)
        if not share.allow_text:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Text mode disabled for this companion",
            )

        token_hash = hash_share_token(payload.visitor_token, share.id)
        if not verify_share_token(payload.visitor_token, share.id, convo["share_token_hash"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid visitor token"
            )

        snapshot = await _resolve_share_snapshot(conn, share)
    # Connection released here, BEFORE streaming begins

    async def event_stream():
        seq = 0

        def sse(event: str, data: dict | None = None) -> str:
            nonlocal seq
            seq += 1
            body = f"event: {event}\n"
            body += f"id: {seq}\n"
            if data is None:
                data = {}
            body += f"data: {json.dumps(data)}\n\n"
            return body

        try:
            timings: dict[str, float] = {}
            memory_enabled = bool(snapshot.get("memory_enabled"))
            importance_guidance = ""
            if memory_enabled:
                mem_cfg = await _fetch_memory_config(convo["companion_id"])
                importance_guidance = (mem_cfg or {}).get("evaluation_prompt", "")

            # record activity & persist user message
            try:
                await _record_share_message_activity(
                    share, token_hash, convo["id"], message_delta=1
                )
            except ShareRateLimitExceeded as exc:
                yield sse("error", {"detail": f"Rate limit exceeded ({exc.bucket})"})
                return

            user_row = await _persist_user_message(convo["id"], payload.content, timings)
            if memory_enabled:
                # Only check importance synchronously if developer wants to show status badges
                # This avoids 200-500ms latency when badges are disabled
                check_sync = share.expose_status_events
                should_store, importance = await _schedule_memory_ingest(
                    conversation=convo,
                    message_row=user_row,
                    content=payload.content,
                    sender_type="user",
                    importance_guidance=importance_guidance,
                    check_importance_sync=check_sync,
                )
                # Emit memory_stored event only if we checked AND passed threshold
                if check_sync and should_store and share.expose_status_events:
                    yield sse(
                        "status",
                        {
                            "stage": "memory_stored",
                            "phase": "end",
                            "meta": {
                                "content": payload.content[:100],  # Trimmed content for display
                                "message_id": str(user_row["id"]),
                                "role": "user",
                                "importance": importance,
                                "stored_at": user_row.get("created_at").isoformat()
                                if user_row.get("created_at")
                                else None,
                            },
                        },
                    )
            yield sse("ack", {"user_message": _serialize_message(user_row).model_dump(mode="json")})

            history, history_timings = await _load_history(convo["id"], user_row, payload.content)
            timings.update(history_timings)

            effective_prompt = await _build_effective_prompt(convo, snapshot, timings)

            memory_block = ""
            if memory_enabled:
                if share.expose_status_events:
                    yield sse("status", {"stage": "retrieving", "phase": "start"})
                block, meta = await _maybe_build_memory_block(
                    convo, payload.content, history, memory_enabled, timings
                )
                memory_block = block
                if share.expose_status_events:
                    yield sse(
                        "status",
                        {"stage": "retrieving", "phase": "end", "meta": _compact_meta(meta)},
                    )

            await _maybe_trigger_summary(convo, history)

            llm_messages = _assemble_messages(effective_prompt, memory_block, history, timings)
            provider, temperature = _snapshot_provider(snapshot)

            if share.expose_status_events:
                yield sse(
                    "status",
                    {"stage": "thinking", "phase": "start", "meta": {"provider": provider}},
                )

            out_parts: List[str] = []
            HEARTBEAT_SEC = 15.0
            last_beat = asyncio.get_event_loop().time()

            try:
                client, model, _ = resolve_llm_client(provider)
                max_toks = resolve_max_tokens(model, DEFAULT_TEXT_LLM_MAX_TOKENS)
                t_api = time.perf_counter()
                completion_kwargs = {
                    "model": model,
                    "messages": llm_messages,
                    "temperature": temperature,
                    "stream": True,
                }
                if str(model).startswith("gpt-5.1"):
                    completion_kwargs["max_completion_tokens"] = max_toks
                else:
                    completion_kwargs["max_tokens"] = max_toks

                stream = await client.chat.completions.create(**completion_kwargs)
                async for chunk in stream:  # type: ignore[attr-defined]
                    piece = _extract_stream_delta(chunk)
                    if piece:
                        out_parts.append(piece)
                        yield sse("delta", {"content": piece})
                    now = asyncio.get_event_loop().time()
                    if (now - last_beat) >= HEARTBEAT_SEC:
                        yield ": keep-alive\n\n"
                        last_beat = now
                timings["llm_api_ms"] = (time.perf_counter() - t_api) * 1000
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.exception("Public SSE streaming failed")
                yield sse("error", {"detail": str(exc)})
                return
            finally:
                if share.expose_status_events:
                    yield sse("status", {"stage": "thinking", "phase": "end"})

            response_text = "".join(out_parts).strip()
            assistant_row = await _persist_assistant_message(convo["id"], response_text, timings)
            if memory_enabled and response_text:
                # Only check importance synchronously if developer wants to show status badges
                # This avoids 200-500ms latency when badges are disabled
                check_sync = share.expose_status_events
                should_store, importance = await _schedule_memory_ingest(
                    conversation=convo,
                    message_row=assistant_row,
                    content=response_text,
                    sender_type="assistant",
                    importance_guidance=importance_guidance,
                    check_importance_sync=check_sync,
                )
                # Emit memory_stored event only if we checked AND passed threshold
                if check_sync and should_store and share.expose_status_events:
                    yield sse(
                        "status",
                        {
                            "stage": "memory_stored",
                            "phase": "end",
                            "meta": {
                                "content": response_text[:100],  # Trimmed content for display
                                "message_id": str(assistant_row["id"]),
                                "role": "assistant",
                                "importance": importance,
                                "stored_at": assistant_row.get("created_at").isoformat()
                                if assistant_row.get("created_at")
                                else None,
                            },
                        },
                    )
            _schedule_background(_increment_share_totals(share.id, message_delta=1))

            _schedule_background(
                _run_in_thread(
                    update_history_cache_post_turn,
                    convo["id"],
                    user_message={
                        "id": user_row["id"],
                        "role": "user",
                        "content": payload.content,
                        "created_at": user_row.get("created_at"),
                    },
                    assistant_message={
                        "id": assistant_row["id"],
                        "role": "assistant",
                        "content": response_text,
                        "created_at": assistant_row.get("created_at"),
                    },
                )
            )

            yield sse(
                "message",
                {"assistant_message": _serialize_message(assistant_row).model_dump(mode="json")},
            )
            if timings:
                rounded = {
                    k: (round(v, 2) if isinstance(v, (int | float)) else v)
                    for k, v in timings.items()
                }
                yield sse("timings", rounded)
            yield sse("done", {})

        except HTTPException as http_exc:
            yield sse("error", {"detail": http_exc.detail})
        except Exception as exc:
            logger.exception("Public SSE failure")
            yield sse("error", {"detail": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/conversations/{conversation_id}/messages", response_model=List[PublicTurnMessage])
async def list_public_messages(
    conversation_id: UUID,
    visitor_token: str = Query(..., max_length=255),
    conn: asyncpg.Connection = Depends(get_db),
):
    if not visitor_token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="visitor_token is required"
        )

    convo, share = await _get_conversation_and_share(conn, conversation_id)
    if not verify_share_token(visitor_token, share.id, convo["share_token_hash"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid visitor token")

    rows = await get_conversation_messages(conn, conversation_id)
    return [_serialize_message(row) for row in rows]


async def _public_text_turn_blocking(
    *,
    conversation: asyncpg.Record,
    share: CompanionShare,
    snapshot: dict,
    token_hash: bytes,
    user_content: str,
    timings: dict[str, float],
) -> tuple[dict, dict]:
    memory_enabled = bool(snapshot.get("memory_enabled"))
    importance_guidance = ""
    if memory_enabled:
        mem_cfg = await _fetch_memory_config(conversation["companion_id"])
        importance_guidance = (mem_cfg or {}).get("evaluation_prompt", "")
    await _record_share_message_activity(share, token_hash, conversation["id"], message_delta=1)
    user_row = await _persist_user_message(conversation["id"], user_content, timings)
    if memory_enabled:
        _schedule_memory_ingest(
            conversation=conversation,
            message_row=user_row,
            content=user_content,
            sender_type="user",
            importance_guidance=importance_guidance,
        )
    history, history_timings = await _load_history(conversation["id"], user_row, user_content)
    timings.update(history_timings)
    effective_prompt = await _build_effective_prompt(conversation, snapshot, timings)
    memory_block, _ = await _maybe_build_memory_block(
        conversation,
        user_content,
        history,
        memory_enabled,
        timings,
    )
    await _maybe_trigger_summary(conversation, history)
    llm_messages = _assemble_messages(effective_prompt, memory_block, history, timings)
    provider, temperature = _snapshot_provider(snapshot)
    assistant_text = await generate_llm_response_direct(
        provider,
        llm_messages,
        temperature=temperature,
        timings=timings,
    )
    assistant_row = await _persist_assistant_message(conversation["id"], assistant_text, timings)
    if memory_enabled and assistant_text:
        _schedule_memory_ingest(
            conversation=conversation,
            message_row=assistant_row,
            content=assistant_text,
            sender_type="assistant",
            importance_guidance=importance_guidance,
        )
    await _increment_share_totals(share.id, message_delta=1)
    update_history_cache_post_turn(
        conversation["id"],
        user_message={
            "id": user_row["id"],
            "role": "user",
            "content": user_content,
            "created_at": user_row.get("created_at"),
            "input_modality": user_row.get("input_modality"),
        },
        assistant_message={
            "id": assistant_row["id"],
            "role": "assistant",
            "content": assistant_text,
            "created_at": assistant_row.get("created_at"),
            "input_modality": assistant_row.get("input_modality"),
        },
    )
    return user_row, assistant_row


async def _record_share_message_activity(
    share: CompanionShare,
    token_hash: bytes,
    conversation_id: UUID,
    message_delta: int,
) -> None:
    now = datetime.now(UTC)
    async with get_db_connection() as conn:
        await CompanionShareSessionRepository.record_activity(
            conn,
            share_id=share.id,
            visitor_token_hash=token_hash,
            conversation_id=conversation_id,
            message_delta=message_delta,
            voice_sessions_delta=0,
            now=now,
        )


async def _persist_user_message(
    conversation_id: UUID,
    content: str,
    timings: dict[str, float],
) -> dict:
    t0 = time.perf_counter()
    async with get_db_connection() as conn:
        row = await add_message_returning(
            conn,
            conversation_id,
            "user",
            content,
            input_modality="text",
        )
    timings["persist_user_ms"] = (time.perf_counter() - t0) * 1000
    return row


async def _persist_assistant_message(
    conversation_id: UUID,
    content: str,
    timings: dict[str, float],
) -> dict:
    t0 = time.perf_counter()
    async with get_db_connection() as conn:
        row = await add_message_returning(
            conn,
            conversation_id,
            "assistant",
            content,
            input_modality="text",
        )
    timings["persist_assistant_ms"] = (time.perf_counter() - t0) * 1000
    return row


async def _load_history(
    conversation_id: UUID,
    user_row: dict,
    user_content: str,
) -> tuple[List[dict], dict[str, float]]:
    t_db = time.perf_counter()
    async with get_db_connection() as conn:
        history = await get_full_history(conn, conversation_id, use_cache=True)
    timings = {
        "db_history_ms": (time.perf_counter() - t_db) * 1000,
    }
    if not history or history[-1].get("id") != user_row["id"]:
        history.append(
            {
                "id": user_row["id"],
                "role": "user",
                "content": user_content,
                "created_at": user_row.get("created_at"),
                "input_modality": user_row.get("input_modality"),
            }
        )
    timings["history_msgs"] = len(history)
    timings["history_truncated"] = False
    return history, timings


async def _build_effective_prompt(
    conversation: asyncpg.Record,
    snapshot: dict,
    timings: dict[str, float],
) -> str:
    builder_prompt = snapshot.get("system_prompt") or "You are a helpful and friendly companion."
    t_eff = time.perf_counter()
    async with get_db_connection() as conn:
        effective = await build_effective_system_prompt_for_config(
            conn,
            companion_id=conversation["companion_id"],
            builder_prompt=builder_prompt,
        )
    timings["effective_prompt_ms"] = (time.perf_counter() - t_eff) * 1000
    return effective


async def _maybe_build_memory_block(
    conversation: asyncpg.Record,
    user_content: str,
    history: List[dict],
    memory_enabled: bool,
    timings: dict[str, float],
) -> tuple[str, dict[str, float | int]]:
    if not memory_enabled:
        return "", {}
    meta: dict[str, float | int] = {}
    async with get_db_connection() as conn:
        block = await build_transient_memory_block(
            conn,
            companion_id=conversation["companion_id"],
            user_text=user_content,
            external_user_id=conversation.get("external_user_id"),
            conversation_id=None,
            memory_enabled=True,
            timings=meta,
            recent_messages=history,
            last_n=6,
        )
    for key, value in meta.items():
        timings[key] = value
    return block or "", meta


async def _maybe_trigger_summary(conversation: asyncpg.Record, history: List[dict]) -> None:
    try:
        async with get_db_connection() as conn:
            await check_and_trigger_summary(
                conn,
                companion_id=conversation["companion_id"],
                conversation_id=conversation["id"],
                messages=history,
            )
    except Exception:
        pass


def _assemble_messages(
    effective_prompt: str,
    memory_block: str,
    history: List[dict],
    timings: dict[str, float],
) -> List[dict]:
    t0 = time.perf_counter()
    llm_messages = assemble_llm_messages(
        effective_system_prompt=effective_prompt,
        memory_block=memory_block,
        prior_messages=history,
    )
    timings["llm_assemble_ms"] = (time.perf_counter() - t0) * 1000
    return llm_messages


def _snapshot_provider(snapshot: dict) -> tuple[str, float]:
    provider = str(snapshot.get("llm_provider") or "openai-gpt4o-mini")
    try:
        temperature = float(snapshot.get("temperature", 0.7) or 0.7)
    except Exception:
        temperature = 0.7
    return provider, temperature


async def _increment_share_totals(share_id: UUID, message_delta: int) -> None:
    async with get_db_connection() as conn:
        await CompanionShareRepository.increment_totals(
            conn,
            share_id,
            message_delta=message_delta,
        )


async def _fetch_memory_config(companion_id: UUID) -> dict:
    try:
        async with get_db_connection() as conn:
            return await MemoryService.get_companion_memory_config(conn, companion_id=companion_id)
    except Exception as exc:
        logger.warning(
            "[PUBLIC] Failed to load memory config for companion %s: %s", companion_id, exc
        )
        return {}


async def _schedule_memory_ingest(
    *,
    conversation: asyncpg.Record,
    message_row: dict,
    content: str,
    sender_type: str,
    importance_guidance: str,
    check_importance_sync: bool,
) -> tuple[bool, float]:
    """Schedule memory ingest, optionally checking importance synchronously.

    Args:
        check_importance_sync: If True, check importance before dispatching (adds ~200-500ms latency).
                              If False, dispatch immediately and let Modal worker decide.

    Returns (should_store, importance_score).
    When check_importance_sync=False, returns (True, 0.0) as we don't check locally.
    """
    message_id = message_row.get("id") if isinstance(message_row, dict) else None
    if not message_id:
        return False, 0.0
    try:
        companion_id = conversation["companion_id"]
        conversation_id = conversation["id"]
    except Exception:
        return False, 0.0
    external_user_id = (
        conversation.get("external_user_id") if hasattr(conversation, "get") else None
    )

    # Decide whether to check importance synchronously
    should_store = True
    importance = 0.0

    if check_importance_sync:
        # Synchronous check: adds latency but provides accurate real-time result
        from ..services.memory_service import should_store_memory

        min_importance_write = float(os.getenv("MEMORY_MIN_IMPORTANCE_WRITE", "0.55"))
        should_store, importance = await should_store_memory(
            content=content,
            importance_guidance=importance_guidance,
            min_importance_write=min_importance_write,
            is_core=False,
        )

        if not should_store:
            # Failed threshold, don't dispatch
            return False, importance

    # Dispatch to Modal
    async def _dispatch() -> None:
        try:
            await dispatch_memory_ingest_job(
                companion_id=companion_id,
                items=[
                    {
                        "message_id": str(message_id),
                        "content": content,
                        "sender_type": sender_type,
                        "conversation_id": str(conversation_id),
                        "external_user_id": external_user_id,
                        "importance_guidance": importance_guidance or "",
                        "weight_user": 1.0,
                        "modality": "text",
                        "is_core": False,
                        "pre_validated": check_importance_sync,  # True if we already validated
                        "importance": importance if check_importance_sync else None,
                    }
                ],
            )
        except Exception as exc:
            logger.warning("[PUBLIC] Modal memory dispatch failed (%s): %s", sender_type, exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("[PUBLIC] No running loop to schedule memory ingest; skipping")
        return False, importance
    loop.create_task(_dispatch())
    return should_store, importance


def _compact_meta(meta: dict[str, float | int]) -> dict[str, float | int]:
    out: dict[str, float | int] = {}
    for key in ("retrieval_items", "retrieval_ms", "retrieval_embed_ms"):
        if key in meta:
            value = meta[key]
            if isinstance(value, float):
                out[key] = round(value, 2)
            else:
                out[key] = value
    return out


def _extract_stream_delta(chunk) -> str | None:
    try:
        choice = getattr(chunk, "choices", None)
        if choice:
            choice = choice[0]
            delta = getattr(choice, "delta", None)
            if delta is not None:
                piece = getattr(delta, "content", None)
                if piece:
                    return piece
                if isinstance(delta, dict):
                    return delta.get("content")
    except Exception:
        pass
    try:
        return chunk.get("choices", [{}])[0].get("delta", {}).get("content")  # type: ignore[assignment]
    except Exception:
        return None
