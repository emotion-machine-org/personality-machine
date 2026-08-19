"""Testing endpoints for Memory V2 UI.

Provides endpoints that work with companion_id + user_id for easier testing,
automatically resolving/creating the relationship as needed.
"""

from __future__ import annotations

import json
import logging
from typing import List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..db import get_db
from ..models.user import User
from ..repositories.companion import CompanionRepository
from ..repositories.memory_v2_repository import MemoryV2Repository
from ..repositories.relationship_repository import RelationshipRepository
from ..services.voice_presets import resolve_llm_config

router = APIRouter(prefix="/api/memory-v2-testing", tags=["memory-v2-testing"])
logger = logging.getLogger(__name__)


# =============================================================================
# Request/Response Models
# =============================================================================


class MemoryEntryResponse(BaseModel):
    id: str
    content: str
    type: str | None = None
    created_at: str
    updated_at: str


class MemoryListResponse(BaseModel):
    relationship_id: str
    entries: List[MemoryEntryResponse]
    count: int
    total: int
    max_entries: int
    next_cursor: str | None = None
    has_more: bool = False


class UpdateEntryRequest(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=2000)
    type: str | None = Field(None, max_length=50)


class CreateEntryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    type: str | None = Field(None, max_length=50)


class ChatHistoryMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str


class TestChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    history: List[ChatHistoryMessage] = Field(default_factory=list)


class TestChatResponse(BaseModel):
    response: str
    memory_entries_count: int


class TempChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    history: List[ChatHistoryMessage] = Field(default_factory=list)
    user_id: str = Field(
        ..., min_length=1, max_length=200
    )  # Builder user ID from companion-simulator


class TempChatResponse(BaseModel):
    response: str
    memory_entries: List[MemoryEntryResponse]
    new_memories: List[MemoryEntryResponse]


class CompanionSummary(BaseModel):
    id: str
    name: str
    memory_version: int
    memory_enabled: bool


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_or_create_relationship(
    conn: asyncpg.Connection,
    companion_id: UUID,
    user_id: str,
) -> UUID:
    """Get or create a relationship for the companion + user."""
    relationship, _ = await RelationshipRepository.ensure_exists(
        conn,
        companion_id=companion_id,
        user_id=user_id,
    )
    return relationship.id


async def _verify_companion_access(
    conn: asyncpg.Connection,
    companion_id: UUID,
    user: User,
) -> dict:
    """Verify the user owns this companion and return its config."""
    companion = await CompanionRepository.get_companion_by_id(conn, companion_id, user.id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Companion {companion_id} not found",
        )
    return companion


def _entry_to_response(entry: dict) -> MemoryEntryResponse:
    return MemoryEntryResponse(
        id=str(entry["id"]),
        content=entry["content"],
        type=entry.get("type"),
        created_at=entry["created_at"].isoformat(),
        updated_at=entry["updated_at"].isoformat(),
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/companions", response_model=List[CompanionSummary])
async def list_companions_with_memory_config(
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> List[CompanionSummary]:
    """List user's companions with their memory config."""
    # Fetch companions with config from companion_versions.config (fallback to system_prompt)
    query = """
        SELECT c.id, c.name, cv.config, cv.system_prompt
        FROM companions c
        LEFT JOIN companion_versions cv ON cv.id = (
            SELECT id FROM companion_versions
            WHERE companion_id = c.id
            ORDER BY version_number DESC
            LIMIT 1
        )
        WHERE c.owner_id = $1
        ORDER BY c.created_at DESC
    """
    rows = await conn.fetch(query, user.id)

    result = []
    for row in rows:
        # Parse config - prefer config column, fallback to system_prompt for legacy data
        # asyncpg's JSONB codec returns dicts directly
        config = {}
        config_payload = row["config"] if row["config"] else row["system_prompt"]
        if config_payload:
            if isinstance(config_payload, str):
                try:
                    config = json.loads(config_payload)
                except Exception:
                    config = {}
            elif isinstance(config_payload, dict):
                config = config_payload

        memory_config = config.get("memory", {}) if isinstance(config, dict) else {}
        result.append(
            CompanionSummary(
                id=str(row["id"]),
                name=row["name"],
                memory_version=memory_config.get("version", 1),
                memory_enabled=memory_config.get("enabled", False),
            )
        )
    return result


@router.post("/companions/{companion_id}/enable-memory-v2")
async def enable_memory_v2(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    """Enable Memory V2 for a companion."""
    from ..models.companion import CompanionUpdate, MemoryConfig

    companion = await _verify_companion_access(conn, companion_id, user)

    # Build updated memory config
    existing_memory = companion.config.memory if companion.config else MemoryConfig()
    updated_memory = MemoryConfig(
        enabled=True,
        version=2,
        core_memories=existing_memory.core_memories,
        memory_evaluation_prompt=existing_memory.memory_evaluation_prompt,
        recency=existing_memory.recency,
        top_k=existing_memory.top_k,
        min_saliency=existing_memory.min_saliency,
        max_entries=existing_memory.max_entries,
        model=existing_memory.model,
        ingestion_prompt=existing_memory.ingestion_prompt,
    )

    # Use repository to update (creates new version)
    from ..models.companion import CompanionConfig

    updated_config = CompanionConfig(
        system_prompt=companion.config.system_prompt if companion.config else None,
        memory=updated_memory,
        inference=companion.config.inference if companion.config else None,
        voice=companion.config.voice if companion.config else None,
        context_mode=companion.config.context_mode if companion.config else "legacy",
        layers=companion.config.layers if companion.config else [],
        context=companion.config.context if companion.config else None,
    )

    await CompanionRepository.update_companion(
        conn, companion_id, user.id, CompanionUpdate(config=updated_config)
    )

    return {"status": "ok", "memory_version": 2}


@router.get(
    "/companions/{companion_id}/users/{user_id}/memory",
    response_model=MemoryListResponse,
)
async def get_memory_entries(
    companion_id: UUID,
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    type_filter: str | None = Query(None, max_length=50),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> MemoryListResponse:
    """
    Get memory entries for a companion + user with pagination and search.

    Pagination:
    - Returns `limit` items starting after `cursor` (ISO timestamp)
    - Response includes `next_cursor` if more items exist

    Search:
    - Uses hybrid tsvector + trigram matching
    - Exact/prefix matches ranked higher than fuzzy
    """
    await _verify_companion_access(conn, companion_id, user)
    relationship_id = await _get_or_create_relationship(conn, companion_id, user_id)

    if search:
        entries, total, next_cursor = await MemoryV2Repository.search_entries(
            conn, relationship_id, search, limit, cursor, type_filter
        )
    else:
        entries, total, next_cursor = await MemoryV2Repository.list_entries_paginated(
            conn, relationship_id, limit, cursor, type_filter
        )

    return MemoryListResponse(
        relationship_id=str(relationship_id),
        entries=[_entry_to_response(e) for e in entries],
        count=len(entries),
        total=total,
        max_entries=150,
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


@router.post(
    "/companions/{companion_id}/users/{user_id}/memory",
    response_model=MemoryEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_memory_entry(
    companion_id: UUID,
    user_id: str,
    body: CreateEntryRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> MemoryEntryResponse:
    """Add a memory entry."""
    await _verify_companion_access(conn, companion_id, user)
    relationship_id = await _get_or_create_relationship(conn, companion_id, user_id)

    entry = await MemoryV2Repository.create_entry(
        conn,
        relationship_id,
        body.content,
        body.type,
    )

    logger.info(f"Memory entry created via testing UI: {entry['id']}")
    return _entry_to_response(entry)


@router.delete(
    "/companions/{companion_id}/users/{user_id}/memory/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_memory_entry(
    companion_id: UUID,
    user_id: str,
    entry_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    """Delete a memory entry."""
    await _verify_companion_access(conn, companion_id, user)
    relationship_id = await _get_or_create_relationship(conn, companion_id, user_id)

    deleted = await MemoryV2Repository.delete_entry(conn, entry_id, relationship_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry {entry_id} not found",
        )

    logger.info(f"Memory entry deleted via testing UI: {entry_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/companions/{companion_id}/users/{user_id}/memory/{entry_id}",
    response_model=MemoryEntryResponse,
)
async def update_memory_entry(
    companion_id: UUID,
    user_id: str,
    entry_id: UUID,
    body: UpdateEntryRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> MemoryEntryResponse:
    """Update content and/or type of a memory entry."""
    await _verify_companion_access(conn, companion_id, user)
    relationship_id = await _get_or_create_relationship(conn, companion_id, user_id)

    # Get current entry to preserve fields not being updated
    current = await MemoryV2Repository.get_entry(conn, entry_id, relationship_id)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry {entry_id} not found",
        )

    # Update with new values, preserving current values if not provided
    new_content = body.content if body.content is not None else current["content"]
    new_type = body.type if body.type is not None else current.get("type")

    updated = await MemoryV2Repository.update_entry(
        conn, entry_id, relationship_id, new_content, new_type
    )

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry {entry_id} not found",
        )

    logger.info(f"Memory entry updated via testing UI: {entry_id}")
    return _entry_to_response(updated)


@router.delete(
    "/companions/{companion_id}/users/{user_id}/memory",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_all_memory(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    """Clear all memory entries."""
    await _verify_companion_access(conn, companion_id, user)
    relationship_id = await _get_or_create_relationship(conn, companion_id, user_id)

    count = await MemoryV2Repository.clear_all(conn, relationship_id)

    logger.info(f"Cleared {count} memory entries via testing UI")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/companions/{companion_id}/users/{user_id}/chat",
    response_model=TestChatResponse,
)
async def test_chat(
    companion_id: UUID,
    user_id: str,
    body: TestChatRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> TestChatResponse:
    """Send a test message and get response. Memory injection and ingestion happen automatically."""
    import asyncio

    from ..context import build_context_plan
    from ..services.llm import generate_llm_response_direct

    companion = await _verify_companion_access(conn, companion_id, user)
    relationship_id = await _get_or_create_relationship(conn, companion_id, user_id)

    # Check memory config
    mem_config = companion.config.memory if companion.config else None
    logger.info(
        f"Memory config: enabled={mem_config.enabled if mem_config else False}, "
        f"version={mem_config.version if mem_config else 'N/A'}"
    )

    # Build context plan with memory v2 layer
    plan = await build_context_plan(
        conn=conn,
        companion_id=companion_id,
        companion_config=companion.config,
        conversation_id=None,
        user_message=body.message,
        external_user_id=user_id,
        relationship_id=relationship_id,
        include_memory=True,
    )

    # Build messages list: system messages from plan + history + current user message
    messages = []

    # Add ALL system messages from plan (core prompt + memory block + other layers)
    for msg in plan.messages:
        if msg.get("role") == "system":
            messages.append(msg)
        else:
            break  # Stop at first non-system message (history/user)

    # Add conversation history
    for msg in body.history:
        messages.append({"role": msg.role, "content": msg.content})

    # Add current user message
    messages.append({"role": "user", "content": body.message})

    # Generate response
    model, temperature = (
        resolve_llm_config(companion.config) if companion.config else ("openai-gpt4o-mini", 0.7)
    )
    response = await generate_llm_response_direct(
        model,
        messages,
        temperature=temperature,
        max_tokens=1000,
    )

    # Trigger Memory V2 ingestion (async, fire-and-forget)
    if mem_config and mem_config.enabled and mem_config.version == 2:
        logger.info(
            f"Triggering Memory V2 ingestion: relationship={relationship_id}, "
            f"user_msg_len={len(body.message)}, response_len={len(response)}"
        )

        async def _dispatch_ingestion():
            try:
                import modal

                fn = modal.Function.from_name("em-memory-v2", "ingest_memory_v2")
                payload = {
                    "relationship_id": str(relationship_id),
                    "user_message": body.message,
                    "assistant_response": response,
                }
                logger.info(f"Spawning Modal function with payload: {payload}")
                fn.spawn(payload)
                logger.info(
                    f"Memory V2 ingestion dispatched successfully for relationship {relationship_id}"
                )
            except Exception as e:
                logger.error(f"Memory V2 ingestion dispatch failed: {e}", exc_info=True)

        asyncio.create_task(_dispatch_ingestion())
    else:
        logger.info(
            f"Memory V2 ingestion skipped: mem_config={mem_config is not None}, "
            f"enabled={mem_config.enabled if mem_config else 'N/A'}, "
            f"version={mem_config.version if mem_config else 'N/A'}"
        )

    # Get current entry count
    entries = await MemoryV2Repository.list_entries(conn, relationship_id)

    return TestChatResponse(
        response=response,
        memory_entries_count=len(entries),
    )


@router.post(
    "/companions/{companion_id}/temp-chat",
    response_model=TempChatResponse,
)
async def temp_chat(
    companion_id: UUID,
    body: TempChatRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> TempChatResponse:
    """
    Chat for testing memory consolidation in Memory Explorer.

    - Uses the provided builder user ID (same as companion-simulator)
    - Triggers memory ingestion synchronously (waits for result)
    - Returns updated memory list with new entries marked
    """
    import asyncio

    from ..context import build_context_plan
    from ..services.llm import generate_llm_response_direct

    companion = await _verify_companion_access(conn, companion_id, user)

    # Use the builder user ID directly (same user as companion-simulator)
    relationship_id = await _get_or_create_relationship(conn, companion_id, body.user_id)

    # Get existing memories before chat
    existing_entries = await MemoryV2Repository.list_entries(conn, relationship_id)
    existing_ids = {e["id"] for e in existing_entries}

    # Build context plan with memory v2 layer
    plan = await build_context_plan(
        conn=conn,
        companion_id=companion_id,
        companion_config=companion.config,
        conversation_id=None,
        user_message=body.message,
        external_user_id=body.user_id,
        relationship_id=relationship_id,
        include_memory=True,
    )

    # Build messages list
    messages = []
    for msg in plan.messages:
        if msg.get("role") == "system":
            messages.append(msg)
        else:
            break

    for msg in body.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": body.message})

    # Generate response
    model, temperature = (
        resolve_llm_config(companion.config) if companion.config else ("openai-gpt4o-mini", 0.7)
    )
    response_text = await generate_llm_response_direct(
        model,
        messages,
        temperature=temperature,
        max_tokens=1000,
    )

    # Trigger Memory V2 ingestion synchronously (wait for it to complete)
    mem_config = companion.config.memory if companion.config else None
    if mem_config and mem_config.enabled and mem_config.version == 2:
        logger.info(
            f"Triggering synchronous Memory V2 ingestion for temp chat: relationship={relationship_id}"
        )
        try:
            import modal

            fn = modal.Function.from_name("em-memory-v2", "ingest_memory_v2")
            payload = {
                "relationship_id": str(relationship_id),
                "user_message": body.message,
                "assistant_response": response_text,
            }
            # Use remote() for synchronous call instead of spawn()
            await asyncio.to_thread(fn.remote, payload)
            logger.info(
                f"Memory V2 ingestion completed for temp chat relationship {relationship_id}"
            )
        except Exception as e:
            logger.error(f"Memory V2 ingestion failed for temp chat: {e}", exc_info=True)

    # Get all memories after ingestion
    all_entries = await MemoryV2Repository.list_entries(conn, relationship_id, limit=150)

    # Find new memories (those not in existing_ids)
    new_entries = [e for e in all_entries if e["id"] not in existing_ids]

    return TempChatResponse(
        response=response_text,
        memory_entries=[_entry_to_response(e) for e in all_entries],
        new_memories=[_entry_to_response(e) for e in new_entries],
    )
