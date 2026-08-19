from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID, uuid4

import asyncpg
import modal
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator

from ..auth import ProjectApiKeySubject, get_project_api_subject
from ..constants import DEFAULT_TEXT_LLM_MAX_TOKENS
from ..context import (
    ContextEvent,
    TurnContext,
    build_context_plan,
    execute_post_turn_effects,
    persist_turn_context,
)
from ..context.core_prompt_layer import bust_core_prompt_cache
from ..context.resolved_config import CompanionRuntimeConfig
from ..db import get_db, get_db_connection
from ..models.companion import CompanionConfig, CompanionCreate, CompanionDetail, CompanionUpdate
from ..models.media import ChatImageResponse
from ..repositories.companion import CompanionRepository
from ..repositories.conversation import (
    add_message_returning,
    add_message_with_build_ms,
    create_conversation_for_companion,
    get_conversation_by_id,
    get_conversation_messages,
    set_conversation_context_engine,
)
from ..repositories.job_repository import JobRepository
from ..repositories.project import KnowledgeIngestionJobRepository
from ..repositories.project_secrets import ProjectSecretRepository
from ..repositories.tool_index_repository import ToolIndexRepository
from ..schemas.knowledge import (
    KnowledgeIngestionRequest,
    KnowledgeJobResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from ..services.cache_manager import cache
from ..services.context_assembly import build_effective_system_prompt, build_transient_memory_block
from ..services.encryption import encrypt_secret
from ..services.image_description import build_image_context_block, extract_image_description
from ..services.knowledge_service import create_asset_from_upload, ingest_knowledge_payload
from ..services.llm import generate_llm_response_direct, resolve_max_tokens
from ..services.llm_resolver import resolve_llm_client
from ..services.media_assets import generate_presigned_url, persist_image_upload
from ..services.openai_clients import get_openrouter_async_client
from ..services.openai_vector_store import search_vector_store

# Voice session imports
from .sessions import (
    PipelineType,
    SessionCreate,
    VoiceConfig,
    _register_session,
    _session_cfg,
    normalize_voice_config,
)

# Voice imports
from .voice.providers import ELEVENLABS_VOICES, get_all_voice_mappings

router = APIRouter(prefix="/v1", tags=["client-api"])
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────────────────────────────────────


class CompanionListItem(BaseModel):
    id: UUID
    name: str
    last_updated: str
    project_id: UUID


class CompanionResource(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    config: CompanionConfig
    created_at: datetime
    project_id: UUID


class CompanionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    config: CompanionConfig | None = None

    def to_model(self) -> CompanionCreate:
        return CompanionCreate(
            name=self.name,
            description=self.description,
            config=self.config,
        )


class CoreMemoriesRequest(BaseModel):
    """Add or refresh core memories for a companion."""

    memories: List[str] = Field(
        ..., min_items=1, max_items=200, description="List of core memory strings"
    )
    max_total: int = Field(
        default=50, ge=1, le=500, description="Keep only the most recent N core memories"
    )


class CompanionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    config: CompanionConfig | None = None

    def to_model(self) -> CompanionUpdate:
        return CompanionUpdate(
            name=self.name,
            description=self.description,
            config=self.config,
        )


class ProfileSchemaRequest(BaseModel):
    schema: Dict[str, Any]

    @validator("schema")
    def validate_object(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("schema must be a JSON object")
        return value


class ProfileSchemaResponse(BaseModel):
    companion_id: UUID
    project_id: UUID
    schema: Dict[str, Any]
    updated_at: datetime
    updated_by: UUID | None = None

    @classmethod
    def from_companion(cls, companion: CompanionDetail) -> ProfileSchemaResponse:
        """Create from companion config (new storage location)."""
        if companion.current_version:
            updated_at = companion.current_version.created_at
        else:
            updated_at = companion.created_at
        return cls(
            companion_id=companion.id,
            project_id=companion.project_id,
            schema=companion.config.profile_schema if companion.config else {},
            updated_at=updated_at,
            updated_by=None,  # Not tracked in new storage
        )


class ChatRequest(BaseModel):
    external_user_id: str = Field(..., max_length=255)
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: UUID | None = None
    profile: Dict[str, Any] | None = None
    model: str | None = Field(default="openai-gpt4o-mini")
    temperature: float | None = Field(default=0.7, ge=0.0, le=2.0)
    image_ids: List[UUID] | None = Field(
        default=None, description="IDs of uploaded images to include"
    )


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"
    emotion_machine: Dict[str, Any] = Field(default_factory=dict)


class ChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatChoice]
    usage: ChatUsage


class ConversationMessage(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class ConversationResponse(BaseModel):
    id: UUID
    companion_id: UUID
    external_user_id: str | None
    started_at: datetime
    messages: List[ConversationMessage]


class ConversationListItem(BaseModel):
    id: UUID
    companion_id: UUID
    external_user_id: str | None
    started_at: datetime
    last_message_at: datetime | None = None
    message_count: int = 0


class CreateConversationRequest(BaseModel):
    external_user_id: str = Field(..., max_length=255)


class CreateConversationResponse(BaseModel):
    conversation_id: UUID


class ClientSessionCreate(BaseModel):
    """Request model for creating a voice session via API key authentication."""

    companion_id: UUID = Field(
        ...,
        alias="companionId",
        description="ID of the companion to use",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    conversation_id: UUID | None = Field(
        default=None,
        alias="conversationId",
        description="(Optional) Existing conversation ID to continue",
    )
    external_user_id: str | None = Field(
        default=None,
        alias="externalUserId",
        max_length=255,
        description="(Optional) Stable end-user identifier for tracking",
    )
    voice_config: VoiceConfig | None = Field(
        default=None,
        alias="voiceConfig",
        description="(Optional) Voice pipeline configuration. Defaults to OpenAI Realtime with 'alloy' voice.",
    )

    model_config = {"populate_by_name": True}


class ClientSessionResponse(BaseModel):
    """Response model for voice session creation."""

    id: str = Field(..., description="Session ID")
    ws_url: str = Field(..., description="WebSocket URL with one-time token")
    conversation_id: str = Field(..., description="Conversation ID (new or continued)")


class ToolIndexRequest(BaseModel):
    """Request to index an OpenAPI spec for tool integration."""

    spec_name: str | None = Field(None, description="Optional display name for the spec")
    openapi_spec: Dict[str, Any] = Field(..., description="OpenAPI 3.x specification as JSON")
    secrets_config: Dict[str, str] | None = Field(
        None,
        description="Map of HTTP header names to project secret names, e.g. {'Authorization': 'my_api_key'}",
    )


class ToolIndexResponse(BaseModel):
    """Response after indexing an OpenAPI spec."""

    spec_id: UUID
    dispatched: bool = Field(..., description="Whether the indexing job was dispatched to Modal")
    request_id: UUID


class ToolSpecItem(BaseModel):
    """Tool spec summary for listing."""

    id: UUID
    spec_name: str | None = None
    secrets_config: Dict[str, str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ToolSpecDetail(BaseModel):
    """Detailed tool spec information."""

    id: UUID
    project_id: UUID
    companion_id: UUID
    spec_name: str | None = None
    secrets_config: Dict[str, str] | None = None
    json_content: Dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UpdateToolSecretsConfigRequest(BaseModel):
    """Request to update secrets_config for a tool spec."""

    secrets_config: Dict[str, str] = Field(
        ..., description="Map of HTTP header names to project secret names"
    )


class CreateSecretRequest(BaseModel):
    """Request to create or update a project secret."""

    secret_name: str = Field(
        ..., min_length=1, max_length=100, description="Unique name for the secret"
    )
    secret_value: str = Field(..., min_length=1, description="The secret value (will be encrypted)")
    description: str | None = Field(None, max_length=500, description="Optional description")


class SecretMetadata(BaseModel):
    """Secret metadata (no value exposed)."""

    id: UUID
    secret_name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────────────


async def _ensure_companion_in_project(
    conn: asyncpg.Connection,
    companion_id: UUID,
    project_id: UUID,
    project_owner_id: UUID,
) -> CompanionDetail:
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not companion or companion.owner_id != project_owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    if companion.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    return companion


def _companion_to_resource(companion, project_id: UUID) -> CompanionResource:
    return CompanionResource(
        id=companion.id,
        name=companion.name,
        description=companion.description,
        config=companion.config or CompanionConfig(),
        created_at=companion.created_at,
        project_id=project_id,
    )


def _chat_response_payload(
    *,
    model: str,
    content: str,
    conversation_id: UUID,
    project_id: UUID,
    context_engine: str | None = None,
    build_ms: int | None = None,
    events: List[Dict[str, Any]] | None = None,
) -> ChatCompletionResponse:
    response_id = f"chatcmpl-{uuid4()}"
    created_ts = int(time.time())
    meta: Dict[str, Any] = {
        "conversation_id": str(conversation_id),
        "project_id": str(project_id),
    }
    if context_engine:
        meta["context_engine"] = context_engine
    if build_ms is not None:
        meta["build_ms"] = build_ms
    if events:
        meta["context_events"] = events
    return ChatCompletionResponse(
        id=response_id,
        created=created_ts,
        model=model,
        choices=[
            ChatChoice(
                message=ChatMessage(role="assistant", content=content),
                emotion_machine={
                    "metadata": meta,
                },
            )
        ],
        usage=ChatUsage(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Companion endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/companions", response_model=List[CompanionListItem])
async def list_companions(
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    summaries = await CompanionRepository.list_companions_for_project(conn, subject.project.id)
    return summaries


@router.post("/companions", response_model=CompanionResource, status_code=status.HTTP_201_CREATED)
async def create_companion(
    payload: CompanionCreateRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    companion = await CompanionRepository.create_companion(
        conn,
        subject.project.owner_id,
        payload.to_model(),
        project_id=subject.project.id,
    )
    return _companion_to_resource(companion, subject.project.id)


@router.get("/companions/{companion_id}", response_model=CompanionResource)
async def get_companion(
    companion_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    companion = await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )
    return _companion_to_resource(companion, subject.project.id)


@router.patch("/companions/{companion_id}", response_model=CompanionResource)
async def update_companion(
    companion_id: UUID,
    payload: CompanionUpdateRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )
    updated = await CompanionRepository.update_companion(
        conn,
        companion_id,
        subject.project.owner_id,
        payload.to_model(),
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
    # Invalidate cached effective prompt so new sessions use updated config
    cache.delete("eff_prompt", str(companion_id))
    return _companion_to_resource(updated, subject.project.id)


@router.delete("/companions/{companion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_companion(
    companion_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete a companion."""
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )
    deleted = await CompanionRepository.delete_companion(
        conn,
        companion_id,
        subject.project.owner_id,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
    # Invalidate cached effective prompt
    cache.delete("eff_prompt", str(companion_id))


# ──────────────────────────────────────────────────────────────────────────────
# Profile schema endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.put("/companions/{companion_id}/profile-schema", response_model=ProfileSchemaResponse)
async def upsert_profile_schema(
    companion_id: UUID,
    payload: ProfileSchemaRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Update profile schema in companion config (creates new version)."""
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )
    # Update companion config with new profile_schema (creates a new version)
    updates = CompanionUpdate(config=CompanionConfig(profile_schema=payload.schema))
    updated = await CompanionRepository.update_companion(
        conn,
        companion_id=companion_id,
        user_id=subject.project.owner_id,
        updates=updates,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
    # Invalidate cache for core prompt since config changed
    bust_core_prompt_cache(companion_id)
    return ProfileSchemaResponse.from_companion(updated)


@router.get("/companions/{companion_id}/profile-schema", response_model=ProfileSchemaResponse)
async def get_profile_schema(
    companion_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get profile schema from companion config."""
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not companion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
    return ProfileSchemaResponse.from_companion(companion)


# ──────────────────────────────────────────────────────────────────────────────
# Knowledge ingestion
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companions/{companion_id}/knowledge",
    response_model=KnowledgeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_knowledge(
    companion_id: UUID,
    request: Request,
    payload: KnowledgeIngestionRequest | None = Body(None),
    file: UploadFile | None = File(None),
    payload_type: str | None = Form(None),
    content: str | None = Form(None),
    payload_key: str | None = Form(None),
    asset_id: UUID | None = Form(None),
    # Allow JSON bodies without nesting under "payload"
    body_type: str | None = Body(None, alias="type"),
    body_content: str | None = Body(None, alias="content"),
    body_key: str | None = Body(None, alias="key"),
    body_asset_id: UUID | None = Body(None, alias="asset_id"),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Unified knowledge ingest endpoint.
    - If `file` is provided (multipart), an asset is created then ingested.
    - Otherwise, inline payload is ingested (text/markdown/json).
    """
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    # Resolve effective payload fields (favor multipart form when provided)
    effective_type = payload_type or body_type or (payload.type if payload else None) or "markdown"
    inline_content = (
        content
        if content is not None
        else body_content
        if body_content is not None
        else (payload.content if payload else None)
    )
    inline_key = (
        payload_key
        if payload_key is not None
        else body_key
        if body_key is not None
        else (payload.key if payload else None)
    )
    inline_asset_id = (
        asset_id
        if asset_id is not None
        else body_asset_id
        if body_asset_id is not None
        else (payload.asset_id if payload else None)
    )

    # Fallback: if nothing parsed (e.g., simple JSON body), read raw body once more
    if (
        file is None
        and inline_content is None
        and inline_key is None
        and inline_asset_id is None
        and payload is None
    ):
        try:
            raw = await request.json()
            inline_content = raw.get("content", inline_content)
            effective_type = raw.get("type", effective_type)
            inline_key = raw.get("key", inline_key)
            inline_asset_id = raw.get("asset_id", inline_asset_id)
        except Exception:
            pass

    # If a file is provided, create asset and set asset_id
    created_asset_id: UUID | None = None
    if file is not None:
        asset = await create_asset_from_upload(
            conn,
            project_id=subject.project.id,
            companion_id=companion_id,
            owner_user_id=subject.project.owner_id,
            upload=file,
        )
        created_asset_id = asset.id

    job = await ingest_knowledge_payload(
        conn,
        project_id=subject.project.id,
        companion_id=companion_id,
        payload_type=effective_type,
        inline_content=inline_content,
        payload_key=inline_key,
        asset_id=created_asset_id or inline_asset_id,
        submitted_by_user=None,
        submitted_by_key=subject.api_key.id,
        source_label="api",
        missing_key_error="Unknown ingestion key",
    )
    return KnowledgeJobResponse.from_model(job)


@router.post(
    "/companions/{companion_id}/knowledge/search",
    response_model=KnowledgeSearchResponse,
)
async def search_knowledge(
    companion_id: UUID,
    payload: KnowledgeSearchRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )
    results = await search_vector_store(
        conn,
        companion_id=companion_id,
        query=payload.query,
        max_results=payload.max_results or 5,
        filters=payload.filters,
        mode=payload.mode,
    )
    return KnowledgeSearchResponse(results=results)


@router.post(
    "/companions/{companion_id}/core-memories",
    status_code=status.HTTP_200_OK,
)
async def add_core_memories(
    companion_id: UUID,
    payload: CoreMemoriesRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Add core memories for a companion. Trims to `max_total` most recent entries."""
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )
    mems = [m.strip() for m in payload.memories if m.strip()]
    if not mems:
        raise HTTPException(status_code=400, detail="No memories provided")

    for m in mems:
        await conn.execute(
            """
            INSERT INTO memories (id, companion_id, content, is_core, created_at)
            VALUES ($1, $2, $3, TRUE, now())
            """,
            uuid4(),
            companion_id,
            m,
        )

    await conn.execute(
        """
        DELETE FROM memories
        WHERE id IN (
            SELECT id FROM memories
            WHERE companion_id = $1 AND is_core = TRUE
            ORDER BY created_at DESC
            OFFSET $2
        )
        """,
        companion_id,
        payload.max_total,
    )

    try:
        bust_core_prompt_cache(companion_id)
    except Exception:
        pass

    return {"status": "ok", "added": len(mems)}


@router.get("/knowledge-jobs/{job_id}", response_model=KnowledgeJobResponse)
async def get_knowledge_job(
    job_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    job = await KnowledgeIngestionJobRepository.get_job_by_id(conn, job_id)
    if not job or job.project_id != subject.project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return KnowledgeJobResponse.from_model(job)


# ──────────────────────────────────────────────────────────────────────────────
# Chat endpoints
# ──────────────────────────────────────────────────────────────────────────────


async def _chat_legacy(
    companion: CompanionDetail,
    companion_id: UUID,
    conversation_id: UUID,
    payload: ChatRequest,
    project_id: UUID,
    conn: asyncpg.Connection,
    user_message_id: UUID,
) -> ChatCompletionResponse:
    """Legacy chat flow - uses build_effective_system_prompt (NOT context engine).

    User message is already saved by caller. This function builds the LLM context,
    calls the LLM, and saves the assistant response.
    """
    # Check if memory is enabled for this companion
    mem_enabled = bool(companion.config.memory and companion.config.memory.enabled)
    mem_cfg: Dict[str, Any] | None = None

    # Get conversation history (includes the user message just added)
    history_rows = await get_conversation_messages(conn, conversation_id)

    # Build effective system prompt using legacy method (NOT context engine)
    try:
        effective_prompt, _ = await build_effective_system_prompt(conn, companion_id=companion_id)
    except Exception:
        effective_prompt = (
            companion.config.system_prompt.get_effective_prompt()
            or "You are a helpful and friendly companion."
        )

    # Build image context block if images are attached
    image_context_block = ""
    if payload.image_ids and len(payload.image_ids) > 0:
        try:
            image_rows = await conn.fetch(
                """
                SELECT id, description, created_at
                FROM media_assets
                WHERE id = ANY($1) AND conversation_id = $2
                ORDER BY created_at
                """,
                payload.image_ids,
                conversation_id,
            )
            if image_rows:
                image_descriptions = [
                    {"description": row["description"] or "(No description available)"}
                    for row in image_rows
                ]
                image_context_block = build_image_context_block(image_descriptions)

                # Link images to the user message
                for img_id in payload.image_ids:
                    await conn.execute(
                        "UPDATE media_assets SET message_id = $1 WHERE id = $2",
                        user_message_id,
                        img_id,
                    )
        except Exception as e:
            logging.warning(f"[API_CHAT] Failed to build image context: {e}")

    # Combine effective prompt with image context if present
    final_system_prompt = effective_prompt
    if image_context_block:
        final_system_prompt = f"{effective_prompt}\n\n{image_context_block}"

    # Build LLM messages manually (legacy approach - no context engine)
    llm_messages: List[Dict[str, str]] = []
    if final_system_prompt:
        llm_messages.append({"role": "system", "content": final_system_prompt})
    for row in history_rows:
        llm_messages.append({"role": row["role"], "content": row["content"]})

    timings: Dict[str, Any] = {}
    max_tokens = resolve_max_tokens(
        payload.model or "openai-gpt4o-mini",
        companion.config.inference.max_output_tokens or DEFAULT_TEXT_LLM_MAX_TOKENS,
    )

    assistant_text = await generate_llm_response_direct(
        payload.model or "openai-gpt4o-mini",
        llm_messages,
        temperature=payload.temperature or 0.7,
        max_tokens=max_tokens,
        timings=timings,
    )

    assistant_row = await add_message_returning(
        conn,
        conversation_id,
        "assistant",
        assistant_text,
        input_modality="text",
    )
    assistant_message_id = assistant_row["id"]

    # Dispatch memory ingestion for assistant message (background task)
    if mem_enabled:
        try:
            from ..services.memory_service import MemoryService
            from ..services.modal_gateway import dispatch_memory_ingest_job

            mem_cfg = await MemoryService.get_companion_memory_config(
                conn, companion_id=companion_id
            )

            async def _dispatch_assistant():
                try:
                    await dispatch_memory_ingest_job(
                        companion_id=companion_id,
                        items=[
                            {
                                "message_id": str(assistant_message_id),
                                "content": assistant_text,
                                "sender_type": "assistant",
                                "conversation_id": str(conversation_id),
                                "external_user_id": payload.external_user_id,
                                "importance_guidance": (mem_cfg or {}).get("evaluation_prompt", ""),
                                "weight_user": 1.0,
                                "modality": "text",
                                "is_core": False,
                            }
                        ],
                    )
                except Exception as e:
                    logging.warning(f"[API_CHAT] Modal memory dispatch (assistant) failed: {e}")

            asyncio.create_task(_dispatch_assistant())
        except Exception:
            pass

    return _chat_response_payload(
        model=payload.model or "openai-gpt4o-mini",
        content=assistant_text,
        conversation_id=conversation_id,
        project_id=project_id,
        context_engine="legacy",
    )


async def _chat_layered(
    companion: CompanionDetail,
    companion_id: UUID,
    conversation_id: UUID,
    payload: ChatRequest,
    project_id: UUID,
    conn: asyncpg.Connection,
) -> ChatCompletionResponse:
    """Layered chat flow - new context engine with state, effects, and actions."""
    build_start = time.perf_counter()
    resolved_config = CompanionRuntimeConfig.from_companion_config(companion.config)
    include_behaviors = resolved_config.should_include_behaviors(default_if_unconfigured=False)

    plan = await build_context_plan(
        conn=conn,
        companion_id=companion_id,
        companion_config=companion.config,
        conversation_id=conversation_id,
        user_message=payload.message,
        external_user_id=payload.external_user_id,
        include_memory=True,
        include_knowledge=True,
        include_behaviors=include_behaviors,
        hydrate_state=True,
    )

    build_ms = int((time.perf_counter() - build_start) * 1000)
    llm_messages = plan.messages

    timings: Dict[str, Any] = {}
    llm_start = time.perf_counter()
    assistant_text = await generate_llm_response_direct(
        payload.model or "openai-gpt4o-mini",
        llm_messages,
        temperature=payload.temperature or 0.7,
        timings=timings,
    )
    llm_ms = int((time.perf_counter() - llm_start) * 1000)

    # Store assistant message with build timing
    asst_msg = await add_message_with_build_ms(
        conn,
        conversation_id,
        "assistant",
        assistant_text,
        input_modality="text",
        build_ms=build_ms,
    )
    message_id = asst_msg.get("id")

    # Build turn context for post-turn operations
    turn_ctx = TurnContext(
        message=payload.message,
        companion_id=companion_id,
        conversation_id=conversation_id,
        external_user_id=payload.external_user_id,
    )

    # Execute post-turn effects (state updates, webhooks, etc.)
    if plan.effects:
        # Get memory evaluation prompt from companion config for importance scoring
        mem_cfg = getattr(companion.config, "memory", None)
        memory_eval_prompt = getattr(mem_cfg, "memory_evaluation_prompt", "") if mem_cfg else ""
        # Extract hydrated context from trace for optimistic locking
        hydrated_ctx = plan.trace.get("hydrated_context")
        try:
            await execute_post_turn_effects(
                conn=conn,
                turn_context=turn_ctx,
                effects=plan.effects,
                hydrated_context=hydrated_ctx,
                memory_evaluation_prompt=memory_eval_prompt,
            )
        except Exception as e:
            logger.warning(f"Post-turn effects failed: {e}")

    # Fire-and-forget: enqueue async actions and persist turn context
    async def _post_response_work():
        from ..db import get_db_connection

        # Enqueue pending async behaviors (legacy actions use same path)
        if plan.pending_async_actions:
            try:
                async with get_db_connection() as job_conn:
                    for pa in plan.pending_async_actions:
                        await JobRepository.enqueue(
                            job_conn,
                            job_type="behavior_execution",
                            companion_id=companion_id,
                            conversation_id=conversation_id,
                            external_user_id=payload.external_user_id,
                            behavior_key=pa.action_key,
                            params={
                                "behavior_key": pa.action_key,
                                "trigger_source": pa.trigger_source,
                                "trigger_details": pa.trigger_details,
                                "user_message": payload.message,
                            },
                        )
                logger.debug(f"Enqueued {len(plan.pending_async_actions)} async behaviors")
            except Exception as e:
                logger.warning(f"Failed to enqueue async behaviors: {e}")

        # Persist turn context
        try:
            async with get_db_connection() as ctx_conn:
                await persist_turn_context(
                    ctx_conn,
                    plan=plan,
                    turn_context=turn_ctx,
                    message_id=message_id,
                    llm_ms=llm_ms,
                )
        except Exception as e:
            logger.warning(f"Turn context persistence failed: {e}")

    asyncio.create_task(_post_response_work())

    debug_events_enabled = os.getenv("CONTEXT_EVENTS_DEBUG", "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    events_payload = [e.model_dump() for e in plan.events] if debug_events_enabled else None

    return _chat_response_payload(
        model=payload.model or "openai-gpt4o-mini",
        content=assistant_text,
        conversation_id=conversation_id,
        project_id=project_id,
        context_engine="layered",
        build_ms=build_ms,
        events=events_payload,
    )


@router.post(
    "/companions/{companion_id}/chat",
    response_model=ChatCompletionResponse,
)
async def chat_with_companion(
    companion_id: UUID,
    payload: ChatRequest,
    x_context_engine: str | None = Header(None, alias="X-Context-Engine"),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    companion = await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    conversation_id = payload.conversation_id
    if conversation_id:
        conversation = await get_conversation_by_id(conn, conversation_id)
        if (
            not conversation
            or conversation["companion_id"] != companion_id
            or companion.project_id != subject.project.id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
    else:
        conversation_id = await create_conversation_for_companion(
            conn,
            companion_id,
            payload.external_user_id,
        )

    # Add user message
    user_row = await add_message_returning(
        conn,
        conversation_id,
        "user",
        payload.message,
        input_modality="text",
    )
    user_message_id = user_row["id"]

    # Check if memory is enabled for this companion
    memory_enabled = bool(companion.config.memory and companion.config.memory.enabled)

    # Dispatch memory ingestion for user message (background task)
    mem_cfg: Dict[str, Any] | None = None
    if memory_enabled:
        try:
            from ..services.memory_service import MemoryService
            from ..services.modal_gateway import dispatch_memory_ingest_job

            mem_cfg = await MemoryService.get_companion_memory_config(
                conn, companion_id=companion_id
            )

            async def _dispatch_user():
                try:
                    await dispatch_memory_ingest_job(
                        companion_id=companion_id,
                        items=[
                            {
                                "message_id": str(user_message_id),
                                "content": payload.message,
                                "sender_type": "user",
                                "conversation_id": str(conversation_id),
                                "external_user_id": payload.external_user_id,
                                "importance_guidance": (mem_cfg or {}).get("evaluation_prompt", ""),
                                "weight_user": 1.0,
                                "modality": "text",
                                "is_core": False,
                            }
                        ],
                    )
                except Exception as e:
                    logging.warning(f"[API_CHAT_STREAM] Modal memory dispatch (user) failed: {e}")

            asyncio.create_task(_dispatch_user())
        except Exception:
            pass

    # Determine which engine to use (hybrid selection per MERGE_STRATEGY.md)
    # Priority: 1) Header override, 2) companion.config.context_mode, 3) default "legacy"
    if x_context_engine:
        use_layered = x_context_engine.lower() in ("v2", "layered")
    else:
        use_layered = getattr(companion.config, "context_mode", "legacy") == "layered"
    context_engine = "layered" if use_layered else "legacy"

    # Set context_engine on conversation (first message sets it)
    await set_conversation_context_engine(conn, conversation_id, context_engine)

    if use_layered:
        return await _chat_layered(
            companion, companion_id, conversation_id, payload, subject.project.id, conn
        )
    else:
        return await _chat_legacy(
            companion,
            companion_id,
            conversation_id,
            payload,
            subject.project.id,
            conn,
            user_message_id,
        )


def _create_sse_helper():
    """Create a stateful SSE formatter."""
    seq = 0

    def sse(event: str, data: Dict[str, Any] | None = None) -> str:
        nonlocal seq
        seq += 1
        payload_str = json.dumps(data or {})
        return f"event: {event}\nid: {seq}\ndata: {payload_str}\n\n"

    return sse


async def _stream_legacy(
    companion: CompanionDetail,
    companion_id: UUID,
    conversation_id: UUID,
    payload: ChatRequest,
    project_id: UUID,
    user_row: Dict[str, Any],
) -> StreamingResponse:
    """Legacy streaming chat - uses build_effective_system_prompt (NOT context engine)."""
    provider = payload.model or "openai-gpt4o-mini"
    client, resolved_model, _ = resolve_llm_client(provider)
    temperature = payload.temperature or 0.7
    max_tokens = resolve_max_tokens(
        resolved_model, companion.config.inference.max_output_tokens or DEFAULT_TEXT_LLM_MAX_TOKENS
    )
    response_id = f"chatcmpl-{uuid4()}"
    created_ts = int(time.time())

    async def event_stream():
        sse = _create_sse_helper()

        try:
            yield sse(
                "ack",
                {
                    "conversation_id": str(conversation_id),
                    "message": {
                        "id": str(user_row["id"]),
                        "role": user_row["role"],
                        "content": user_row["content"],
                        "created_at": user_row["created_at"].isoformat(),
                    },
                },
            )

            # Legacy streaming: use build_effective_system_prompt (NOT context engine)
            async with get_db_connection() as conn:
                # Get conversation history
                history_rows = await get_conversation_messages(conn, conversation_id)
                recent_messages = [
                    {"role": row["role"], "content": row["content"]} for row in history_rows
                ]

                # Build effective system prompt
                try:
                    effective_prompt, _ = await build_effective_system_prompt(
                        conn, companion_id=companion_id
                    )
                except Exception:
                    effective_prompt = (
                        companion.config.system_prompt.get_effective_prompt()
                        or "You are a helpful and friendly companion."
                    )

                # Check if memory is enabled
                memory_enabled = bool(companion.config.memory and companion.config.memory.enabled)

                # Build transient memory block if memory is enabled
                memory_block = ""
                timings: Dict[str, Any] = {}
                if memory_enabled:
                    yield sse("status", {"stage": "retrieving", "phase": "start"})
                    memory_block = await build_transient_memory_block(
                        conn,
                        companion_id=companion_id,
                        user_text=payload.message,
                        external_user_id=payload.external_user_id,
                        conversation_id=conversation_id,
                        memory_enabled=True,
                        timings=timings,
                        recent_messages=recent_messages,
                    )
                    retrieval_meta = {}
                    if "retrieval_items" in timings:
                        retrieval_meta["retrieval_items"] = int(timings["retrieval_items"])
                    if "retrieval_ms" in timings:
                        retrieval_meta["retrieval_ms"] = float(timings["retrieval_ms"])
                    yield sse(
                        "status",
                        {"stage": "retrieving", "phase": "end", "meta": retrieval_meta},
                    )

                # Build image context block if images are attached
                image_context_block = ""
                user_message_id = user_row["id"]
                if payload.image_ids and len(payload.image_ids) > 0:
                    try:
                        image_rows = await conn.fetch(
                            """
                            SELECT id, description, created_at
                            FROM media_assets
                            WHERE id = ANY($1) AND conversation_id = $2
                            ORDER BY created_at
                            """,
                            payload.image_ids,
                            conversation_id,
                        )
                        if image_rows:
                            image_descriptions = [
                                {"description": row["description"] or "(No description available)"}
                                for row in image_rows
                            ]
                            image_context_block = build_image_context_block(image_descriptions)

                            # Link images to the user message
                            for img_id in payload.image_ids:
                                await conn.execute(
                                    "UPDATE media_assets SET message_id = $1 WHERE id = $2",
                                    user_message_id,
                                    img_id,
                                )
                    except Exception as e:
                        logging.warning(f"[API_CHAT_STREAM] Failed to build image context: {e}")

            # Combine effective prompt with image context if present
            final_system_prompt = effective_prompt
            if image_context_block:
                final_system_prompt = f"{effective_prompt}\n\n{image_context_block}"

            # Build LLM messages (legacy approach)
            llm_messages: List[Dict[str, str]] = []
            if final_system_prompt:
                llm_messages.append({"role": "system", "content": final_system_prompt})
            if payload.profile:
                llm_messages.append(
                    {
                        "role": "system",
                        "content": f"# USER PROFILE\n{json.dumps(payload.profile, ensure_ascii=False)}",
                    }
                )
            if memory_block:
                llm_messages.append({"role": "system", "content": memory_block})
            for row in history_rows:
                llm_messages.append({"role": row["role"], "content": row["content"]})

            yield sse(
                "status",
                {"stage": "thinking", "phase": "start", "meta": {"model": resolved_model}},
            )

            assistant_parts: List[str] = []
            HEARTBEAT_SEC = 15.0
            last_beat = asyncio.get_event_loop().time()

            try:
                completion_kwargs = {
                    "model": resolved_model,
                    "messages": llm_messages,
                    "temperature": temperature,
                    "stream": True,
                }
                if str(resolved_model).startswith("gpt-5.1"):
                    completion_kwargs["max_completion_tokens"] = max_tokens
                else:
                    completion_kwargs["max_tokens"] = max_tokens

                stream = await client.chat.completions.create(**completion_kwargs)
                async for chunk in stream:  # type: ignore[attr-defined]
                    delta_text = _extract_delta_content(chunk)
                    if delta_text:
                        assistant_parts.append(delta_text)
                        yield sse(
                            "delta",
                            _format_delta_chunk(
                                response_id=response_id,
                                created_ts=created_ts,
                                model=resolved_model,
                                content=delta_text,
                            ),
                        )
                    finish_reason = _extract_finish_reason(chunk)
                    if finish_reason and finish_reason != "stop":
                        yield sse("status", {"stage": "thinking", "phase": "end"})
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Streaming interrupted ({finish_reason})",
                        )
                    now = asyncio.get_event_loop().time()
                    if (now - last_beat) >= HEARTBEAT_SEC:
                        yield ": keep-alive\n\n"
                        last_beat = now
            finally:
                yield sse("status", {"stage": "thinking", "phase": "end"})

            assistant_text = "".join(assistant_parts).strip()

            # Acquire fresh connection for saving assistant message
            async with get_db_connection() as save_conn:
                assistant_row = await add_message_returning(
                    save_conn,
                    conversation_id,
                    "assistant",
                    assistant_text,
                    input_modality="text",
                )
            assistant_message_id = assistant_row["id"]

            # Dispatch memory ingestion for assistant message (background task)
            if memory_enabled:
                try:
                    from ..services.memory_service import MemoryService
                    from ..services.modal_gateway import dispatch_memory_ingest_job

                    async with get_db_connection() as mem_conn:
                        mem_cfg = await MemoryService.get_companion_memory_config(
                            mem_conn, companion_id=companion_id
                        )

                    async def _dispatch_assistant():
                        try:
                            await dispatch_memory_ingest_job(
                                companion_id=companion_id,
                                items=[
                                    {
                                        "message_id": str(assistant_message_id),
                                        "content": assistant_text,
                                        "sender_type": "assistant",
                                        "conversation_id": str(conversation_id),
                                        "external_user_id": payload.external_user_id,
                                        "importance_guidance": (mem_cfg or {}).get(
                                            "evaluation_prompt", ""
                                        ),
                                        "weight_user": 1.0,
                                        "modality": "text",
                                        "is_core": False,
                                    }
                                ],
                            )
                        except Exception as e:
                            logging.warning(
                                f"[API_CHAT_STREAM] Modal memory dispatch (assistant) failed: {e}"
                            )

                    asyncio.create_task(_dispatch_assistant())
                except Exception:
                    pass

            final_payload = _format_final_completion(
                response_id=response_id,
                created_ts=created_ts,
                model=resolved_model,
                content=assistant_text,
                conversation_id=conversation_id,
                project_id=project_id,
                context_engine="legacy",
            )
            yield sse("message", final_payload)
            yield sse(
                "done",
                {
                    "conversation_id": str(conversation_id),
                    "assistant_message_id": str(assistant_row["id"]),
                },
            )
        except HTTPException as exc:
            yield sse("error", {"detail": exc.detail})
        except Exception as exc:  # pragma: no cover - defensive
            yield sse("error", {"detail": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _stream_layered(
    companion: CompanionDetail,
    companion_id: UUID,
    conversation_id: UUID,
    payload: ChatRequest,
    project_id: UUID,
    user_row: Dict[str, Any],
) -> StreamingResponse:
    """Layered streaming chat - full context engine with layer events and effects."""
    provider = payload.model or "openai-gpt4o-mini"
    client, resolved_model, _ = resolve_llm_client(provider)
    temperature = payload.temperature or 0.7
    response_id = f"chatcmpl-{uuid4()}"
    created_ts = int(time.time())

    async def event_stream():
        sse = _create_sse_helper()

        try:
            yield sse(
                "ack",
                {
                    "conversation_id": str(conversation_id),
                    "message": {
                        "id": str(user_row["id"]),
                        "role": user_row["role"],
                        "content": user_row["content"],
                        "created_at": user_row["created_at"].isoformat(),
                    },
                },
            )

            event_queue: asyncio.Queue[ContextEvent] = asyncio.Queue()
            build_start = time.perf_counter()

            def on_context_event(ev: ContextEvent) -> None:
                try:
                    event_queue.put_nowait(ev)
                except Exception:
                    pass

            # Build context plan with all layered features
            # Acquire fresh connection for context building
            async with get_db_connection() as conn:
                resolved_config = CompanionRuntimeConfig.from_companion_config(companion.config)
                include_behaviors = resolved_config.should_include_behaviors(
                    default_if_unconfigured=False
                )
                plan_task = asyncio.create_task(
                    build_context_plan(
                        conn=conn,
                        companion_id=companion_id,
                        companion_config=companion.config,
                        conversation_id=conversation_id,
                        user_message=payload.message,
                        external_user_id=payload.external_user_id,
                        include_memory=True,
                        include_knowledge=True,
                        include_behaviors=include_behaviors,
                        hydrate_state=True,
                        event_callback=on_context_event,
                    )
                )

                # Stream layer events as they occur during orchestration
                while True:
                    try:
                        ev = await asyncio.wait_for(event_queue.get(), timeout=0.05)
                    except TimeoutError:
                        if plan_task.done():
                            break
                        continue

                    layer, _sep, action = ev.name.partition(":")
                    stage = action or layer
                    meta = {**(ev.meta or {}), "layer": layer}
                    yield sse(
                        "status",
                        {
                            "stage": stage,
                            "phase": ev.phase,
                            "meta": meta,
                        },
                    )

                # Drain any late events queued right before completion
                while not event_queue.empty():
                    ev = event_queue.get_nowait()
                    layer, _sep, action = ev.name.partition(":")
                    stage = action or layer
                    meta = {**(ev.meta or {}), "layer": layer}
                    yield sse(
                        "status",
                        {
                            "stage": stage,
                            "phase": ev.phase,
                            "meta": meta,
                        },
                    )

                plan = await plan_task

            build_ms = int((time.perf_counter() - build_start) * 1000)

            llm_messages: List[Dict[str, str]] = plan.messages

            yield sse(
                "status",
                {"stage": "thinking", "phase": "start", "meta": {"model": resolved_model}},
            )

            assistant_parts: List[str] = []
            env_max = int(os.getenv("TEXT_LLM_MAX_TOKENS", "180") or 180)
            max_tokens = max(1, min(env_max, 180))
            HEARTBEAT_SEC = 15.0
            last_beat = asyncio.get_event_loop().time()
            llm_start = time.perf_counter()

            try:
                stream = await client.chat.completions.create(
                    model=resolved_model,
                    messages=llm_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                async for chunk in stream:  # type: ignore[attr-defined]
                    delta_text = _extract_delta_content(chunk)
                    if delta_text:
                        assistant_parts.append(delta_text)
                        yield sse(
                            "delta",
                            _format_delta_chunk(
                                response_id=response_id,
                                created_ts=created_ts,
                                model=resolved_model,
                                content=delta_text,
                            ),
                        )
                    finish_reason = _extract_finish_reason(chunk)
                    if finish_reason and finish_reason != "stop":
                        yield sse("status", {"stage": "thinking", "phase": "end"})
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Streaming interrupted ({finish_reason})",
                        )
                    now = asyncio.get_event_loop().time()
                    if (now - last_beat) >= HEARTBEAT_SEC:
                        yield ": keep-alive\n\n"
                        last_beat = now
            finally:
                yield sse("status", {"stage": "thinking", "phase": "end"})

            assistant_text = "".join(assistant_parts).strip()
            llm_ms = int((time.perf_counter() - llm_start) * 1000) if llm_start else None

            # Acquire fresh connection for saving and effects
            async with get_db_connection() as conn:
                # Store assistant message with build_ms
                assistant_row = await add_message_with_build_ms(
                    conn,
                    conversation_id,
                    "assistant",
                    assistant_text,
                    input_modality="text",
                    build_ms=build_ms,
                )
                message_id = assistant_row.get("id")

                # Build turn context for post-turn operations
                turn_ctx = TurnContext(
                    message=payload.message,
                    companion_id=companion_id,
                    conversation_id=conversation_id,
                    external_user_id=payload.external_user_id,
                )

                # Execute post-turn effects (state updates, webhooks, etc.)
                if plan.effects:
                    # Get memory evaluation prompt from companion config for importance scoring
                    mem_cfg = getattr(companion.config, "memory", None)
                    memory_eval_prompt = (
                        getattr(mem_cfg, "memory_evaluation_prompt", "") if mem_cfg else ""
                    )
                    # Extract hydrated context from trace for optimistic locking
                    hydrated_ctx = plan.trace.get("hydrated_context")
                    try:
                        await execute_post_turn_effects(
                            conn=conn,
                            turn_context=turn_ctx,
                            effects=plan.effects,
                            hydrated_context=hydrated_ctx,
                            memory_evaluation_prompt=memory_eval_prompt,
                        )
                    except Exception as e:
                        logger.warning(f"Post-turn effects failed: {e}")

                # Enqueue pending async behaviors (legacy actions use same path)
                if plan.pending_async_actions:
                    try:
                        for pa in plan.pending_async_actions:
                            await JobRepository.enqueue(
                                conn,
                                job_type="behavior_execution",
                                companion_id=companion_id,
                                conversation_id=conversation_id,
                                external_user_id=payload.external_user_id,
                                behavior_key=pa.action_key,
                                params={
                                    "behavior_key": pa.action_key,
                                    "trigger_source": pa.trigger_source,
                                    "trigger_details": pa.trigger_details,
                                    "user_message": payload.message,
                                },
                            )
                        logger.debug(f"Enqueued {len(plan.pending_async_actions)} async behaviors")
                    except Exception as e:
                        logger.warning(f"Failed to enqueue async behaviors: {e}")

                # Persist turn context snapshot (non-blocking)
                try:
                    await persist_turn_context(
                        conn,
                        plan=plan,
                        turn_context=turn_ctx,
                        message_id=message_id,
                        llm_ms=llm_ms,
                    )
                except Exception as e:
                    logger.warning(f"Turn context persistence failed: {e}")

            final_payload = _format_final_completion(
                response_id=response_id,
                created_ts=created_ts,
                model=resolved_model,
                content=assistant_text,
                conversation_id=conversation_id,
                project_id=project_id,
                context_engine="layered",
                build_ms=build_ms,
            )
            yield sse("message", final_payload)
            yield sse(
                "done",
                {
                    "conversation_id": str(conversation_id),
                    "assistant_message_id": str(assistant_row["id"]),
                },
            )
        except HTTPException as exc:
            yield sse("error", {"detail": exc.detail})
        except Exception as exc:  # pragma: no cover - defensive
            yield sse("error", {"detail": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/companions/{companion_id}/chat/stream")
async def stream_chat_with_companion(
    companion_id: UUID,
    payload: ChatRequest,
    x_context_engine: str | None = Header(None, alias="X-Context-Engine"),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
):
    # NOTE: We explicitly use get_db_connection() instead of Depends(get_db) here.
    # Depends(get_db) would hold the connection for the ENTIRE streaming duration,
    # exhausting the pool under concurrent load. This way, we release the connection
    # before the StreamingResponse begins.
    async with get_db_connection() as conn:
        companion = await _ensure_companion_in_project(
            conn,
            companion_id,
            subject.project.id,
            subject.project.owner_id,
        )

        conversation_id = payload.conversation_id
        if conversation_id:
            conversation = await get_conversation_by_id(conn, conversation_id)
            if (
                not conversation
                or conversation["companion_id"] != companion_id
                or not await CompanionRepository.is_companion_in_project(
                    conn,
                    companion_id,
                    subject.project.id,
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
                )
        else:
            conversation_id = await create_conversation_for_companion(
                conn,
                companion_id,
                payload.external_user_id,
            )

        # Determine which engine to use (hybrid selection per MERGE_STRATEGY.md)
        # Priority: 1) Header override, 2) companion.config.context_mode, 3) default "legacy"
        if x_context_engine:
            use_layered = x_context_engine.lower() in ("v2", "layered")
        else:
            use_layered = getattr(companion.config, "context_mode", "legacy") == "layered"
        context_engine = "layered" if use_layered else "legacy"

        # Set context_engine on conversation (first message sets it)
        await set_conversation_context_engine(conn, conversation_id, context_engine)

        user_row = await add_message_returning(
            conn,
            conversation_id,
            "user",
            payload.message,
            input_modality="text",
        )
    # Connection released here, BEFORE streaming begins

    if use_layered:
        return await _stream_layered(
            companion, companion_id, conversation_id, payload, subject.project.id, user_row
        )
    else:
        return await _stream_legacy(
            companion, companion_id, conversation_id, payload, subject.project.id, user_row
        )


# ──────────────────────────────────────────────────────────────────────────────
# Conversation management endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companions/{companion_id}/conversations",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation_api(
    companion_id: UUID,
    request: CreateConversationRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Create a new conversation for a companion.

    This is useful when you need to upload images before sending any messages.
    The returned conversation_id can be used to upload images and then send
    messages that reference those images.
    """
    # Verify companion belongs to project
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    # Create the conversation
    conversation_id = await create_conversation_for_companion(
        conn,
        companion_id,
        request.external_user_id,
    )

    return CreateConversationResponse(conversation_id=conversation_id)


# ──────────────────────────────────────────────────────────────────────────────
# Image upload endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companions/{companion_id}/conversations/{conversation_id}/images",
    response_model=ChatImageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_chat_image_api(
    companion_id: UUID,
    conversation_id: UUID,
    file: UploadFile = File(...),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Upload an image for use in a conversation.

    The image is stored in S3 and a description is extracted using a vision model
    (Gemini 2.0 Flash). The description is used to provide image context to the
    companion, which works with any text LLM regardless of vision capability.

    Returns:
        - image_id: UUID for referencing the image in subsequent messages
        - description: AI-extracted description of the image
        - storage_url: Presigned URL for displaying the image
        - mime_type: Content type of the uploaded image
    """
    # Verify companion belongs to project
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    # Verify conversation belongs to this companion
    conversation = await get_conversation_by_id(conn, conversation_id)
    if not conversation or conversation["companion_id"] != companion_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    try:
        # Generate asset ID and upload to S3
        asset_id = uuid4()
        stored = await persist_image_upload(
            file,
            asset_id=asset_id,
            companion_id=companion_id,
            conversation_id=conversation_id,
        )

        # Create database record (minimal schema: conversation_id + message_id only)
        await conn.execute(
            """
            INSERT INTO media_assets (
                id, conversation_id, asset_type, storage_path, filename,
                mime_type, size_bytes, checksum, width, height, status
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
            )
            """,
            asset_id,
            conversation_id,
            "image",
            stored.storage_key,
            stored.filename,
            stored.mime_type,
            stored.size_bytes,
            stored.checksum,
            stored.width,
            stored.height,
            "processing",
        )

        # Generate presigned URL for the vision model
        storage_url = generate_presigned_url(stored.storage_key)

        # Extract image description using vision model
        try:
            description, timings = await extract_image_description(image_url=storage_url)
            description_model = timings.get("model", "gemini-2.0-flash")

            # Update asset with description
            await conn.execute(
                """
                UPDATE media_assets
                SET description = $1, description_model = $2, description_extracted_at = now(), status = $3
                WHERE id = $4
                """,
                description,
                description_model,
                "ready",
                asset_id,
            )
            logger.info(
                f"[API] Image {asset_id} processed: {len(description)} char description "
                f"in {timings.get('total_ms', 0):.0f}ms"
            )

        except Exception as e:
            # Mark as failed but still return - image is stored
            logger.error(f"[API] Failed to extract description for image {asset_id}: {e}")
            await conn.execute(
                """
                UPDATE media_assets
                SET status = $1, error_message = $2
                WHERE id = $3
                """,
                "failed",
                str(e)[:500],
                asset_id,
            )
            description = "(Image description could not be extracted)"

        return ChatImageResponse(
            image_id=asset_id,
            description=description,
            storage_url=storage_url,
            mime_type=stored.mime_type,
            width=stored.width,
            height=stored.height,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"[API] Error uploading image for conversation {conversation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upload image"
        )


@router.get(
    "/companions/{companion_id}/conversations/{conversation_id}/images",
    response_model=List[ChatImageResponse],
)
async def list_conversation_images_api(
    companion_id: UUID,
    conversation_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    List all images uploaded to a conversation.
    """
    # Verify companion belongs to project
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    # Verify conversation belongs to this companion
    conversation = await get_conversation_by_id(conn, conversation_id)
    if not conversation or conversation["companion_id"] != companion_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # Fetch all images for this conversation
    rows = await conn.fetch(
        """
        SELECT id, storage_path, description, mime_type, width, height
        FROM media_assets
        WHERE conversation_id = $1 AND asset_type = 'image'
        ORDER BY created_at DESC
        """,
        conversation_id,
    )

    results = []
    for row in rows:
        storage_url = generate_presigned_url(row["storage_path"])
        results.append(
            ChatImageResponse(
                image_id=row["id"],
                description=row["description"] or "",
                storage_url=storage_url,
                mime_type=row["mime_type"],
                width=row["width"],
                height=row["height"],
            )
        )

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Conversation retrieval
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    conversation = await get_conversation_by_id(conn, conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    companion_id = conversation["companion_id"]
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    messages = await get_conversation_messages(conn, conversation_id)
    return ConversationResponse(
        id=conversation_id,
        companion_id=companion_id,
        external_user_id=conversation.get("external_user_id"),
        started_at=conversation["started_at"],
        messages=[
            ConversationMessage(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in messages
        ],
    )


@router.get(
    "/companions/{companion_id}/conversations",
    response_model=List[ConversationListItem],
)
async def list_conversations_for_companion(
    companion_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    external_user_id: str | None = Query(None, max_length=255),
    external_user_prefix: str | None = Query(None, max_length=255),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    filters = ["c.companion_id = $1"]
    params: List[Any] = [companion_id]

    if external_user_id:
        params.append(external_user_id)
        filters.append(f"c.external_user_id = ${len(params)}")

    if external_user_prefix:
        params.append(f"{external_user_prefix}%")
        filters.append(f"c.external_user_id ILIKE ${len(params)}")

    where_clause = " AND ".join(filters)
    limit_param = len(params) + 1
    offset_param = len(params) + 2

    query = f"""
        SELECT c.id,
               c.external_user_id,
               c.started_at,
               c.last_message_at,
               c.message_count
        FROM conversations c
        WHERE {where_clause}
        ORDER BY c.started_at DESC
        LIMIT ${limit_param} OFFSET ${offset_param}
        """

    rows = await conn.fetch(
        query,
        *params,
        limit,
        offset,
    )

    return [
        ConversationListItem(
            id=row["id"],
            companion_id=companion_id,
            external_user_id=row["external_user_id"],
            started_at=row["started_at"],
            last_message_at=row["last_message_at"],
            message_count=int(row["message_count"] or 0),
        )
        for row in rows
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Voice session endpoints
# ──────────────────────────────────────────────────────────────────────────────

# Track which sessions belong to which project (for API key auth)
_session_project: Dict[str, UUID] = {}


@router.post(
    "/sessions",
    response_model=ClientSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a voice session",
    description="Create a new voice session for a companion. Returns a WebSocket URL with a one-time token.",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "minimal": {
                            "summary": "Minimal (OpenAI Realtime)",
                            "description": "Only companionId is required. Uses OpenAI Realtime with 'alloy' voice.",
                            "value": {"companionId": "550e8400-e29b-41d4-a716-446655440000"},
                        },
                        "with_user": {
                            "summary": "With external user tracking",
                            "description": "Track conversations per end-user.",
                            "value": {
                                "companionId": "550e8400-e29b-41d4-a716-446655440000",
                                "externalUserId": "user-123",
                            },
                        },
                        "custom_voice": {
                            "summary": "Custom voice",
                            "description": "Use a different voice with OpenAI providers.",
                            "value": {
                                "companionId": "550e8400-e29b-41d4-a716-446655440000",
                                "voiceConfig": {
                                    "pipeline_type": "stt-llm-tts",
                                    "stt_provider": "openai",
                                    "llm_provider": "openai-gpt4o",
                                    "tts_provider": "openai",
                                    "voice_name": "sage",
                                },
                            },
                        },
                        "stt_llm_tts": {
                            "summary": "STT-LLM-TTS pipeline",
                            "description": "Use modular pipeline with Deepgram STT, Claude LLM, and ElevenLabs TTS.",
                            "value": {
                                "companionId": "550e8400-e29b-41d4-a716-446655440000",
                                "externalUserId": "user-123",
                                "voiceConfig": {
                                    "pipeline_type": "stt-llm-tts",
                                    "stt_provider": "deepgram",
                                    "llm_provider": "claude-sonnet-4",
                                    "tts_provider": "elevenlabs",
                                    "voice_name": "Sarah",
                                    "temperature": 0.8,
                                },
                            },
                        },
                        "continue_conversation": {
                            "summary": "Continue existing conversation",
                            "description": "Resume a previous conversation in voice mode. Only companionId and conversationId are needed.",
                            "value": {
                                "companionId": "550e8400-e29b-41d4-a716-446655440000",
                                "conversationId": "660e8400-e29b-41d4-a716-446655440000",
                            },
                        },
                    }
                }
            }
        }
    },
)
async def create_voice_session(
    payload: ClientSessionCreate,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Create a voice session authenticated via API key.

    The system prompt is automatically retrieved from the companion's configuration.

    - If conversationId is provided: continues existing conversation
    - If conversationId is omitted: creates a new conversation

    Returns session ID, WebSocket URL, and conversation ID for client tracking.
    """
    # Validate companion belongs to the project
    companion = await _ensure_companion_in_project(
        conn,
        payload.companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    # Build effective system prompt from companion config
    try:
        effective_prompt, builder_prompt = await build_effective_system_prompt(
            conn, companion_id=payload.companion_id
        )
        system_prompt = effective_prompt or builder_prompt
    except Exception as e:
        logger.warning(
            f"Failed to build effective prompt for companion {payload.companion_id}: {e}"
        )
        system_prompt = None

    # Fallback to companion config or default
    if not system_prompt:
        if companion.config and companion.config.system_prompt:
            system_prompt = companion.config.system_prompt.get_effective_prompt()
        if not system_prompt:
            system_prompt = "You are a helpful and friendly companion. Keep your responses conversational and engaging."

    # Build voice config with defaults
    voice_config = payload.voice_config or VoiceConfig(pipeline_type=PipelineType.OPENAI_REALTIME)

    # Validate STT-LLM-TTS pipeline has required providers
    if voice_config.pipeline_type == PipelineType.STT_LLM_TTS:
        if not all(
            [voice_config.stt_provider, voice_config.llm_provider, voice_config.tts_provider]
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="STT, LLM, and TTS providers must be specified for stt-llm-tts pipeline",
            )

    normalize_voice_config(voice_config)

    # Handle conversation: reuse existing or create new
    if payload.conversation_id:
        # Validate existing conversation belongs to this companion
        conversation = await get_conversation_by_id(conn, payload.conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        if conversation["companion_id"] != payload.companion_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        conversation_id = payload.conversation_id
        logger.info(f"[V1_SESSION] Continuing existing conversation {conversation_id}")
    else:
        # Create new conversation upfront
        conversation_id = await create_conversation_for_companion(
            conn,
            payload.companion_id,
            payload.external_user_id or "api-user",
        )
        logger.info(f"[V1_SESSION] Created new conversation {conversation_id}")

    # Create internal SessionCreate for the session registry
    # Pass the conversation_id so WebSocket handler doesn't create another
    internal_payload = SessionCreate(
        systemPrompt=system_prompt,
        companionId=str(payload.companion_id),
        conversationId=str(conversation_id),  # Always set now
        voiceConfig=voice_config,
        clientExternalUserId=payload.external_user_id,
    )

    # Register session (reuses sessions.py infrastructure)
    # Pass project owner as the "owner" for session tracking
    result = _register_session(internal_payload, owner_id=str(subject.project.owner_id))

    # Also track which project owns this session (for PATCH authorization)
    _session_project[result.id] = subject.project.id

    logger.info(
        f"[V1_SESSION] Created session {result.id} for companion {payload.companion_id} "
        f"(project={subject.project.id}, conversation={conversation_id}, pipeline={voice_config.pipeline_type})"
    )

    return ClientSessionResponse(
        id=result.id,
        ws_url=result.ws_url,
        conversation_id=str(conversation_id),
    )


@router.patch(
    "/sessions/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Update a voice session",
    description="Update voice configuration for an inactive session. Cannot update sessions with active WebSocket connections.",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "change_voice": {
                            "summary": "Change voice",
                            "description": "Update the voice before connecting.",
                            "value": {
                                "companionId": "550e8400-e29b-41d4-a716-446655440000",
                                "voiceConfig": {"voice_name": "coral"},
                            },
                        },
                        "switch_pipeline": {
                            "summary": "Switch to STT-LLM-TTS",
                            "description": "Change from OpenAI Realtime to modular pipeline.",
                            "value": {
                                "companionId": "550e8400-e29b-41d4-a716-446655440000",
                                "voiceConfig": {
                                    "pipeline_type": "stt-llm-tts",
                                    "stt_provider": "openai",
                                    "llm_provider": "openai-gpt4o",
                                    "tts_provider": "openai",
                                    "voice_name": "alloy",
                                },
                            },
                        },
                    }
                }
            }
        }
    },
)
async def update_voice_session(
    session_id: str,
    payload: ClientSessionCreate,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Update a voice session's configuration before it connects.

    Can only update sessions that haven't started yet (no active WebSocket).
    """
    from .sessions import _active_tasks

    # Check session exists
    if session_id not in _session_cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Check session belongs to this project
    if _session_project.get(session_id) != subject.project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Cannot update active sessions
    if session_id in _active_tasks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot update an active session. Please stop the session first.",
        )

    # Get existing session config to preserve conversation_id
    existing_cfg = _session_cfg[session_id]
    existing_conversation_id = existing_cfg.conversation_id

    # Validate companion belongs to the project
    companion = await _ensure_companion_in_project(
        conn,
        payload.companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    # Build effective system prompt
    try:
        effective_prompt, builder_prompt = await build_effective_system_prompt(
            conn, companion_id=payload.companion_id
        )
        system_prompt = effective_prompt or builder_prompt
    except Exception:
        system_prompt = None

    if not system_prompt:
        if companion.config and companion.config.system_prompt:
            system_prompt = companion.config.system_prompt.get_effective_prompt()
        if not system_prompt:
            system_prompt = "You are a helpful and friendly companion. Keep your responses conversational and engaging."

    # Build voice config
    voice_config = payload.voice_config or VoiceConfig(pipeline_type=PipelineType.OPENAI_REALTIME)

    if voice_config.pipeline_type == PipelineType.STT_LLM_TTS:
        if not all(
            [voice_config.stt_provider, voice_config.llm_provider, voice_config.tts_provider]
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="STT, LLM, and TTS providers must be specified for stt-llm-tts pipeline",
            )

    normalize_voice_config(voice_config)

    # Update session config - preserve the original conversation_id
    internal_payload = SessionCreate(
        systemPrompt=system_prompt,
        companionId=str(payload.companion_id),
        conversationId=existing_conversation_id,  # Preserve original conversation
        voiceConfig=voice_config,
        clientExternalUserId=payload.external_user_id or existing_cfg.client_external_user_id,
    )

    _session_cfg[session_id] = internal_payload

    logger.info(
        f"[V1_SESSION] Updated session {session_id} (conversation={existing_conversation_id})"
    )

    return {"status": "updated"}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _extract_delta_content(chunk: Any) -> str | None:
    try:
        choices = getattr(chunk, "choices", None)
        if not choices:
            return None
        delta = getattr(choices[0], "delta", None)
        if not delta:
            return None
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(part for part in content if isinstance(part, str))
    except Exception:
        return None
    return None


def _extract_finish_reason(chunk: Any) -> str | None:
    try:
        choices = getattr(chunk, "choices", None)
        if not choices:
            return None
        return getattr(choices[0], "finish_reason", None)
    except Exception:
        return None


def _format_delta_chunk(
    *,
    response_id: str,
    created_ts: int,
    model: str,
    content: str,
) -> Dict[str, Any]:
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": None,
            }
        ],
    }


def _format_final_completion(
    *,
    response_id: str,
    created_ts: int,
    model: str,
    content: str,
    conversation_id: UUID,
    project_id: UUID,
    context_engine: str | None = None,
    build_ms: int | None = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "conversation_id": str(conversation_id),
        "project_id": str(project_id),
    }
    if context_engine:
        meta["context_engine"] = context_engine
    if build_ms is not None:
        meta["build_ms"] = build_ms
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "emotion_machine": {
                    "metadata": meta,
                },
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tools endpoints
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_MODAL_ENV = "main"


async def _dispatch_modal_index_job(
    *,
    project_id: UUID,
    companion_id: UUID,
    spec_id: UUID,
    spec_name: str | None,
    openapi_spec: Dict[str, Any],
    request_id: UUID,
) -> bool:
    """Dispatch tool indexing job to Modal worker."""
    worker = modal.Cls.from_name(
        "em-tools",
        "ToolsWorker",
        environment_name=os.getenv("MODAL_ENVIRONMENT", DEFAULT_MODAL_ENV),
    )

    payload: Dict[str, Any] = {
        "request_id": str(request_id),
        "project_id": str(project_id),
        "companion_id": str(companion_id),
        "spec_id": str(spec_id),
        "openapi_spec": openapi_spec,
    }
    if spec_name:
        payload["spec_name"] = spec_name

    try:
        resp = await worker().index_tools.remote.aio(payload)
        if resp.get("status") == "error":
            logger.warning(
                "Modal index request failed: %s %s", resp.get("request_id"), resp.get("message")
            )
            return False
        return True
    except Exception as exc:
        logger.warning("Failed to execute Modal index endpoint: %s", exc)
        return False


async def _verify_tool_spec_access(
    conn: asyncpg.Connection,
    *,
    spec_id: UUID,
    companion_id: UUID,
    project_id: UUID,
) -> Dict[str, Any] | None:
    """Verify tool spec exists and belongs to the companion/project."""
    row = await conn.fetchrow(
        """
        SELECT ts.id, ts.project_id, ts.companion_id, ts.spec_name,
               ts.secrets_config, ts.json_content, ts.created_at, ts.updated_at
        FROM tool_specs ts
        JOIN companions c ON ts.companion_id = c.id
        WHERE ts.id = $1 AND ts.companion_id = $2 AND c.project_id = $3
        """,
        spec_id,
        companion_id,
        project_id,
    )
    return dict(row) if row else None


@router.post(
    "/companions/{companion_id}/tools",
    response_model=ToolIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Index an OpenAPI spec",
    description="Upload and index an OpenAPI spec for tool integration. Tools will be available in the context engine.",
)
async def index_tool_spec(
    companion_id: UUID,
    payload: ToolIndexRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Index an OpenAPI specification for a companion.

    The OpenAPI spec defines the available tools/endpoints that the context engine
    can use during conversations. Each operation in the spec becomes a callable tool.

    Use `secrets_config` to map HTTP headers to project secrets for authentication.
    For example: `{"Authorization": "my_api_key"}` will inject the decrypted value
    of the secret named "my_api_key" into the Authorization header.
    """
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    # Create the spec record
    spec_id = await ToolIndexRepository.create_spec(
        conn,
        project_id=subject.project.id,
        companion_id=companion_id,
        spec_name=payload.spec_name,
        json_content=payload.openapi_spec,
        secrets_config=payload.secrets_config,
    )

    # Dispatch Modal job to index operations
    request_id = uuid4()
    dispatched = await _dispatch_modal_index_job(
        project_id=subject.project.id,
        companion_id=companion_id,
        spec_id=spec_id,
        spec_name=payload.spec_name,
        openapi_spec=payload.openapi_spec,
        request_id=request_id,
    )

    return ToolIndexResponse(
        spec_id=spec_id,
        dispatched=dispatched,
        request_id=request_id,
    )


@router.get(
    "/companions/{companion_id}/tools",
    response_model=List[ToolSpecItem],
    summary="List tool specs",
    description="List all indexed OpenAPI specs for a companion.",
)
async def list_tool_specs(
    companion_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """List all tool specs indexed for a companion."""
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    rows = await ToolIndexRepository.list_specs_for_companion(conn, companion_id=companion_id)
    result = []
    for r in rows:
        secrets_cfg = r.get("secrets_config")
        if isinstance(secrets_cfg, str):
            try:
                secrets_cfg = json.loads(secrets_cfg)
            except (json.JSONDecodeError, TypeError):
                secrets_cfg = None
        result.append(
            ToolSpecItem(
                id=r["id"],
                spec_name=r.get("spec_name"),
                secrets_config=secrets_cfg if secrets_cfg else None,
                created_at=r.get("created_at"),
                updated_at=r.get("updated_at"),
            )
        )
    return result


@router.get(
    "/companions/{companion_id}/tools/{spec_id}",
    response_model=ToolSpecDetail,
    summary="Get tool spec details",
    description="Get detailed information about a specific tool spec.",
)
async def get_tool_spec(
    companion_id: UUID,
    spec_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get detailed information about a tool spec including the full OpenAPI content."""
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    spec_info = await _verify_tool_spec_access(
        conn,
        spec_id=spec_id,
        companion_id=companion_id,
        project_id=subject.project.id,
    )
    if not spec_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool spec not found")

    secrets_cfg = spec_info.get("secrets_config")
    if isinstance(secrets_cfg, str):
        try:
            secrets_cfg = json.loads(secrets_cfg)
        except (json.JSONDecodeError, TypeError):
            secrets_cfg = None

    json_content = spec_info.get("json_content")
    if isinstance(json_content, str):
        try:
            json_content = json.loads(json_content)
        except (json.JSONDecodeError, TypeError):
            json_content = None

    return ToolSpecDetail(
        id=spec_info["id"],
        project_id=spec_info["project_id"],
        companion_id=spec_info["companion_id"],
        spec_name=spec_info.get("spec_name"),
        secrets_config=secrets_cfg,
        json_content=json_content,
        created_at=spec_info.get("created_at"),
        updated_at=spec_info.get("updated_at"),
    )


@router.patch(
    "/companions/{companion_id}/tools/{spec_id}",
    summary="Update tool spec secrets config",
    description="Update the secrets_config mapping for a tool spec.",
)
async def update_tool_spec_secrets_config(
    companion_id: UUID,
    spec_id: UUID,
    payload: UpdateToolSecretsConfigRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Update the secrets_config for a tool spec.

    This allows you to change which project secrets are used for authentication
    headers without re-indexing the entire spec.
    """
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    spec_info = await _verify_tool_spec_access(
        conn,
        spec_id=spec_id,
        companion_id=companion_id,
        project_id=subject.project.id,
    )
    if not spec_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool spec not found")

    await ToolIndexRepository.update_secrets_config(
        conn,
        spec_id=spec_id,
        secrets_config=payload.secrets_config,
    )

    return {
        "status": "updated",
        "id": str(spec_id),
        "secrets_config": payload.secrets_config,
    }


@router.delete(
    "/companions/{companion_id}/tools/{spec_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete tool spec",
    description="Delete a tool spec and all its indexed operations.",
)
async def delete_tool_spec(
    companion_id: UUID,
    spec_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete a tool spec and all associated indexed operations."""
    await _ensure_companion_in_project(
        conn,
        companion_id,
        subject.project.id,
        subject.project.owner_id,
    )

    spec_info = await _verify_tool_spec_access(
        conn,
        spec_id=spec_id,
        companion_id=companion_id,
        project_id=subject.project.id,
    )
    if not spec_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool spec not found")

    ok = await ToolIndexRepository.delete_spec(conn, spec_id=spec_id, companion_id=companion_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool spec not found")


# ──────────────────────────────────────────────────────────────────────────────
# Secrets endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/secrets",
    response_model=SecretMetadata,
    status_code=status.HTTP_201_CREATED,
    summary="Create or update a secret",
    description="Create a new project secret or update an existing one. Secrets are encrypted at rest.",
)
async def create_or_update_secret(
    payload: CreateSecretRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Create or update a project secret.

    Secrets are encrypted using AES-256-GCM and can be referenced in tool specs
    via `secrets_config` to inject authentication headers.

    If a secret with the same name already exists, it will be updated (rotated).
    """
    encrypted_value = encrypt_secret(payload.secret_value)

    # Check if secret exists
    existing = await ProjectSecretRepository.secret_exists(
        conn,
        project_id=subject.project.id,
        secret_name=payload.secret_name,
    )

    if existing:
        # Update existing secret
        secret = await ProjectSecretRepository.update_secret(
            conn,
            project_id=subject.project.id,
            secret_name=payload.secret_name,
            encrypted_value=encrypted_value,
            description=payload.description,
        )
    else:
        # Create new secret
        secret = await ProjectSecretRepository.create_secret(
            conn,
            project_id=subject.project.id,
            secret_name=payload.secret_name,
            encrypted_value=encrypted_value,
            description=payload.description,
        )

    return SecretMetadata(
        id=secret.id,
        secret_name=secret.secret_name,
        description=secret.description,
        created_at=secret.created_at,
        updated_at=secret.updated_at,
    )


@router.get(
    "/secrets",
    response_model=List[SecretMetadata],
    summary="List secrets",
    description="List all secrets for the project. Only metadata is returned, not the actual values.",
)
async def list_secrets(
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    List all secrets for the project.

    Returns metadata only - secret values are never exposed via the API.
    """
    secrets = await ProjectSecretRepository.list_secrets(conn, project_id=subject.project.id)
    return [
        SecretMetadata(
            id=s.id,
            secret_name=s.secret_name,
            description=s.description,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in secrets
    ]


@router.delete(
    "/secrets/{secret_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a secret",
    description="Delete a secret. This will also remove references to it from tool specs.",
)
async def delete_secret(
    secret_name: str,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Delete a project secret.

    Warning: This will also remove any references to this secret from tool specs'
    secrets_config. Make sure to update any affected tool specs with new secrets.
    """
    deleted = await ProjectSecretRepository.delete_secret(
        conn,
        project_id=subject.project.id,
        secret_name=secret_name,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found")

    # Clean up references in tool specs
    await ToolIndexRepository.remove_secret_from_specs(
        conn,
        project_id=subject.project.id,
        secret_name=secret_name,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Voice Mappings
# ──────────────────────────────────────────────────────────────────────────────


class FastBrainDeriveRequest(BaseModel):
    soul: str | None = None
    identity: str | None = None


class FastBrainDeriveResponse(BaseModel):
    prompt: str
    voice_name: str | None = None
    companion_name: str | None = None
    model: str


def _extract_json_block(content: str) -> dict | None:
    cleaned = content.strip()
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S)
        if match:
            cleaned = match.group(1).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Fallback: grab first JSON-looking object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            return None
    return None


def _strip_slow_brain_sections(text: str) -> str:
    markers = [
        "openclaw operating instructions",
        "agents.md",
        "tooling notes",
        "tools",
        "memory",
        "safety",
        "external vs internal",
        "projects & repos",
        "heartbeat",
    ]
    lower = text.lower()
    cut_at = None
    for marker in markers:
        idx = lower.find(marker)
        if idx != -1:
            cut_at = idx if cut_at is None else min(cut_at, idx)
    if cut_at is None:
        return text.strip()
    return text[:cut_at].strip()


def _match_voice_name(text: str, voice_names: list[str]) -> str | None:
    for name in voice_names:
        if re.search(rf"\\b{re.escape(name)}\\b", text, re.IGNORECASE):
            return name
    return None


def _sanitize_companion_name(candidate: str | None) -> str | None:
    if not candidate:
        return None

    cleaned = str(candidate).strip()
    cleaned = re.sub(r"^#+\s*", "", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", cleaned)
    cleaned = re.sub(r"[`*_~>\"]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;!?-_'")
    if not cleaned:
        return None

    lower = cleaned.lower()
    generic_names = {
        "identity",
        "soul",
        "who am i",
        "who am i?",
        "profile",
        "persona",
        "assistant",
    }
    if lower in generic_names:
        return None
    if ".md" in lower:
        return None
    if "who am i" in lower and len(cleaned.split()) <= 5:
        return None

    if not re.search(r"[a-zA-Z]", cleaned):
        return None

    words = cleaned.split()
    if len(words) > 4:
        return None

    return cleaned


def _extract_name_from_markdown(text: str | None) -> str | None:
    if not text:
        return None

    working = text.strip()
    if not working:
        return None

    frontmatter = re.match(r"^\s*---\s*\n(.*?)\n---\s*", working, re.S)
    if frontmatter:
        frontmatter_text = frontmatter.group(1)
        for key in ("name", "companion_name", "assistant_name", "persona_name", "character_name"):
            match = re.search(rf"(?im)^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", frontmatter_text)
            if match:
                parsed = _sanitize_companion_name(match.group(1))
                if parsed:
                    return parsed

    key_value_patterns = (
        r"(?im)^\s*(?:name|companion(?:\s+name)?|assistant(?:\s+name)?|persona(?:\s+name)?)\s*[:\-]\s*(.+?)\s*$",
        r"(?im)\bmy\s+name\s+is\s+([A-Z][A-Za-z' -]{0,60})\b",
    )
    for pattern in key_value_patterns:
        for match in re.finditer(pattern, working):
            parsed = _sanitize_companion_name(match.group(1))
            if parsed:
                return parsed

    for line in working.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            parsed = _sanitize_companion_name(re.sub(r"^#+\s*", "", stripped))
            if parsed:
                return parsed
            continue
        parsed = _sanitize_companion_name(stripped)
        if parsed:
            return parsed
        break

    return None


def _derive_companion_name(soul: str | None, identity: str | None) -> str | None:
    return _extract_name_from_markdown(soul) or _extract_name_from_markdown(identity)


@router.post(
    "/voice/derive-fast-brain",
    response_model=FastBrainDeriveResponse,
    summary="Derive a fast-brain prompt and voice selection",
    description="Uses Gemini Flash via OpenRouter to extract a concise fast-brain prompt and select an ElevenLabs voice.",
)
async def derive_fast_brain_prompt(
    payload: FastBrainDeriveRequest,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
):
    source_sections: list[str] = []
    if payload.soul:
        source_sections.append(f"## SOUL\n{payload.soul}")
    if payload.identity:
        source_sections.append(f"## IDENTITY\n{payload.identity}")
    source = "\n\n".join(source_sections).strip()
    if not source:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="soul or identity required"
        )

    if len(source) > 12000:
        source = source[:12000] + "\n\n[TRUNCATED]"

    voice_names = sorted(ELEVENLABS_VOICES.keys())

    try:
        client = get_openrouter_async_client()
    except RuntimeError as e:
        logger.warning(f"[FAST_BRAIN_DERIVE] OpenRouter unavailable: {e}")
        fallback_prompt = _strip_slow_brain_sections(source)
        return FastBrainDeriveResponse(
            prompt=fallback_prompt or source,
            voice_name=_match_voice_name(source, voice_names),
            companion_name=_derive_companion_name(payload.soul, payload.identity),
            model="fallback",
        )

    system = (
        "You are refining a FAST-BRAIN system prompt for a voice companion. "
        "Input is a raw SOUL/IDENTITY document that may include slow-brain instructions. "
        "Output a concise persona-only prompt (no tools, no file paths, no operational policies). "
        "Also choose the best ElevenLabs voice name and a concise companion name.\n\n"
        "Return STRICT JSON with:\n"
        '{ "prompt": "...", "voice_name": "...", "companion_name": "..." }\n\n'
        "Rules:\n"
        "- Keep prompt under ~800 tokens.\n"
        "- Exclude sections about tools, workflows, memory management, repos, file paths, safety policies.\n"
        "- Preserve personality, tone, identity, and speaking style.\n"
        "- voice_name must be one of the provided names.\n"
        "- companion_name should be short (1-4 words) and derived from the persona.\n"
        "- companion_name must not be a filename, markdown heading label, or phrases like 'Who am I'."
    )

    user = f"SOUL/IDENTITY:\n{source}\n\nElevenLabs voices:\n{', '.join(voice_names)}"

    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        content = response.choices[0].message.content or ""
        data = _extract_json_block(content) or {}
        prompt = str(data.get("prompt", "")).strip()
        voice_name = data.get("voice_name")
        companion_name = _sanitize_companion_name(data.get("companion_name"))
        fallback_name = _derive_companion_name(payload.soul, payload.identity)

        if voice_name not in voice_names:
            voice_name = _match_voice_name(source, voice_names)

        if not prompt:
            prompt = _strip_slow_brain_sections(source) or source
        if not companion_name:
            companion_name = fallback_name

        return FastBrainDeriveResponse(
            prompt=prompt,
            voice_name=voice_name,
            companion_name=companion_name,
            model="google/gemini-2.5-flash",
        )
    except Exception as e:
        logger.warning(f"[FAST_BRAIN_DERIVE] LLM failed: {e}")
        fallback_prompt = _strip_slow_brain_sections(source)
        return FastBrainDeriveResponse(
            prompt=fallback_prompt or source,
            voice_name=_match_voice_name(source, voice_names),
            companion_name=_derive_companion_name(payload.soul, payload.identity),
            model="fallback",
        )


@router.get(
    "/voice-mappings",
    summary="Get voice mappings",
    description="Get all available voice mappings for TTS providers (OpenAI, ElevenLabs, Cartesia).",
)
async def get_voice_mappings():
    """Get all available voice mappings for TTS providers."""
    return get_all_voice_mappings()
