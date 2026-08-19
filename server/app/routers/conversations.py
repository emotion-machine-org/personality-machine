"""
REST API endpoints for conversation management and text messaging.
Implements the conversation persistence plan for text mode interactions and conversation history.
"""

import logging
import time  # needed for llm sub-timings in helpers
from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..constants import DEFAULT_TEXT_LLM_MAX_TOKENS
from ..db import (
    cleanup_session_conversation,
    get_conversation_id,
    get_db,
    get_db_connection,
)
from ..models.media import ChatImageResponse
from ..models.user import User
from ..repositories.companion import CompanionRepository
from ..repositories.conversation import (
    add_message_returning,
    add_messages_batch,
    create_conversation_for_companion,
    get_conversation_by_id,
    get_conversation_by_session,
    get_conversation_messages,
    get_latest_conversation_for_companion,
)
from ..routers.sessions import LLMProvider
from ..services.context_assembly import (
    build_effective_system_prompt,
    build_transient_memory_block,
    build_transient_memory_v2_block,
)
from ..services.context_builder import (
    assemble_llm_messages,
    check_and_trigger_summary,
    get_full_history,
    update_history_cache_post_turn,
)
from ..services.image_description import build_image_context_block, extract_image_description
from ..services.llm import generate_llm_response_direct, resolve_max_tokens
from ..services.llm_resolver import resolve_llm_client
from ..services.media_assets import generate_presigned_url, persist_image_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Models for Request/Response
# ──────────────────────────────────────────────────────────────────────────────


class MessageResponse(BaseModel):
    """Response model for conversation messages"""

    id: UUID
    role: str
    content: str
    created_at: datetime


class ConversationResponse(BaseModel):
    """Response model for conversation details"""

    id: UUID
    deployment_id: UUID | None = None  # Optional for direct companion conversations
    external_user_id: str
    companion_id: UUID | None = None  # Include companion_id for direct conversations
    started_at: datetime
    message_count: int | None = None


class TextMessageRequest(BaseModel):
    """Request model for sending text messages"""

    content: str = Field(..., min_length=1, max_length=4000, description="Message content")

    llm_provider: LLMProvider = Field(
        default=LLMProvider.OPENAI_GPT4O, description="LLM provider to use"
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="LLM temperature")


class SendMessageRequest(BaseModel):
    """Combined request model for sending text messages with companion config"""

    content: str = Field(default="", max_length=4000, description="Message content")
    system_prompt: str = Field(..., description="LLM instructions/persona")
    llm_provider: LLMProvider = Field(
        default=LLMProvider.OPENAI_GPT4O, description="LLM provider to use"
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="LLM temperature")
    image_ids: List[UUID] | None = Field(
        default=None, description="IDs of uploaded images to include"
    )


class TextMessageResponse(BaseModel):
    """Response model for text message sending"""

    user_message: MessageResponse
    assistant_message: MessageResponse


class CreateConversationRequest(BaseModel):
    """Request model for creating new conversations"""

    companion_id: str = Field(..., description="ID of the companion to create conversation for")
    system_prompt: str | None = Field(None, description="Optional system prompt override")
    # Optional client-provided external user id to persist user scope across refreshes
    external_user_id: str | None = Field(
        None, description="Stable test user id for builder sessions"
    )


class SeedRequest(BaseModel):
    """Dev-only helper to seed conversation history for benchmarking."""

    pairs: int = Field(
        default=100, ge=1, le=2000, description="Number of user+assistant pairs to insert"
    )
    avg_chars: int = Field(default=120, ge=8, le=2000, description="Approx chars per message")
    user_prefix: str | None = Field(
        default="User says", description="Prefix text for user messages"
    )
    assistant_prefix: str | None = Field(
        default="Assistant replies", description="Prefix text for assistant messages"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Conversation Management Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/", response_model=ConversationResponse, status_code=201)
async def create_conversation_session(
    request: CreateConversationRequest,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a new input-agnostic conversation session.
    This creates a conversation that can be used by both text and voice modes.
    """
    try:
        # Determine external_user_id: use provided one or generate a new id
        import uuid

        external_user_id = request.external_user_id or f"text-session-{uuid.uuid4()}"

        # Verify companion belongs to current user
        owner_check = await conn.fetchrow(
            "SELECT 1 FROM companions WHERE id = $1 AND owner_id = $2",
            UUID(request.companion_id),
            user.id,
        )
        if not owner_check:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

        # Create conversation linked to companion
        conversation_id = await create_conversation_for_companion(
            conn, UUID(request.companion_id), external_user_id
        )

        # Fetch the created conversation details
        conversation = await get_conversation_by_id(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created conversation",
            )

        return ConversationResponse(**conversation)

    except ValueError as e:
        logger.error(f"Invalid companion ID {request.companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid companion ID: {request.companion_id}",
        )
    except Exception as e:
        logger.error(f"Error creating conversation for companion {request.companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create conversation",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Conversation History Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/by-session/{session_id}", response_model=Optional[ConversationResponse])
async def get_conversation_by_session_id(
    session_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get the most recent conversation for a session ID.
    Used to find the conversation ID from a WebSocket session.
    """
    try:
        conversation = None
        convo_id = get_conversation_id(session_id)
        if convo_id:
            conversation = await get_conversation_by_id(conn, convo_id)
            if not conversation:
                logger.warning(
                    f"Conversation {convo_id} not found in DB for session {session_id}, falling back to lookup"
                )

        if not conversation:
            conversation = await get_conversation_by_session(conn, session_id)

        if not conversation:
            return None
        # Enforce ownership
        check = await conn.fetchrow(
            """
            SELECT 1
            FROM conversations c
            JOIN companions comp ON comp.id = c.companion_id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation["id"],
            user.id,
        )
        if not check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        # Drop the in-memory mapping once the client has acknowledged it so we
        # don't leak entries across many test sessions.
        cleanup_session_conversation(session_id)

        return ConversationResponse(**conversation)
    except Exception as e:
        logger.error(f"Error getting conversation for session {session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversation",
        )


@router.get("/by-companion/{companion_id}/latest", response_model=Optional[ConversationResponse])
async def get_latest_conversation_for_companion_endpoint(
    companion_id: UUID,
    external_user_id: str | None = None,
    external_user_prefix: str | None = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Fetch the most recent conversation for a companion owned by the current user."""
    try:
        conversation = await get_latest_conversation_for_companion(
            conn,
            companion_id,
            user.id,
            external_user_id=external_user_id,
            external_user_prefix=external_user_prefix,
        )
        if not conversation:
            return None

        message_count = conversation.get("message_count") or 0
        conversation["message_count"] = int(message_count)
        return ConversationResponse(**conversation)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting latest conversation for companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversation",
        )


@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_conversation_messages_endpoint(
    conversation_id: UUID,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get all messages for a conversation ordered by creation time.
    Used to display conversation history in text mode.
    """
    try:
        # Verify conversation exists and ownership
        conversation = await get_conversation_by_id(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        check = await conn.fetchrow(
            """
            SELECT 1
            FROM conversations c
            JOIN companions comp ON comp.id = c.companion_id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        messages = await get_conversation_messages(conn, conversation_id)
        return [MessageResponse(**msg) for msg in messages]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting messages for conversation {conversation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve messages"
        )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation_details(
    conversation_id: UUID,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get conversation details by ID"""
    try:
        conversation = await get_conversation_by_id(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        check = await conn.fetchrow(
            """
            SELECT 1
            FROM conversations c
            JOIN companions comp ON comp.id = c.companion_id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        # Get message count
        messages = await get_conversation_messages(conn, conversation_id)
        conversation["message_count"] = len(messages)

        return ConversationResponse(**conversation)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting conversation {conversation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversation",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Image Upload Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/{conversation_id}/images", response_model=ChatImageResponse, status_code=201)
async def upload_chat_image(
    conversation_id: UUID,
    file: UploadFile = File(...),
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload an image for use in the conversation.

    The image is stored in S3 and a description is extracted using a vision model
    (Gemini 2.0 Flash). The description is used to provide image context to the
    companion, which works with any text LLM regardless of vision capability.

    Returns:
        - image_id: UUID for referencing the image in subsequent messages
        - description: AI-extracted description of the image
        - storage_url: Presigned URL for displaying the image
        - mime_type: Content type of the uploaded image
    """
    try:
        # 1. Verify conversation exists and ownership
        conversation = await get_conversation_by_id(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        check = await conn.fetchrow(
            """
            SELECT comp.id as companion_id
            FROM conversations c
            JOIN companions comp ON comp.id = c.companion_id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        companion_id = check["companion_id"]

        # 2. Generate asset ID and upload to S3
        asset_id = uuid4()
        stored = await persist_image_upload(
            file,
            asset_id=asset_id,
            companion_id=companion_id,
            conversation_id=conversation_id,
        )

        # 3. Create database record (minimal schema: conversation_id + message_id only)
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

        # 4. Generate presigned URL for the vision model
        storage_url = generate_presigned_url(stored.storage_key)

        # 5. Extract image description using vision model
        try:
            description, timings = await extract_image_description(image_url=storage_url)
            description_model = timings.get("model", "gemini-2.0-flash")

            # 6. Update asset with description
            from datetime import datetime as dt

            await conn.execute(
                """
                UPDATE media_assets
                SET description = $1, description_model = $2, description_extracted_at = $3, status = $4
                WHERE id = $5
                """,
                description,
                description_model,
                dt.utcnow(),
                "ready",
                asset_id,
            )
            logger.info(
                f"Image {asset_id} processed: {len(description)} char description in {timings.get('total_ms', 0):.0f}ms"
            )

        except Exception as e:
            # Mark as failed but still return - image is stored
            logger.error(f"Failed to extract description for image {asset_id}: {e}")
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
        logger.exception(f"Error uploading image for conversation {conversation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upload image"
        )


@router.get("/{conversation_id}/images", response_model=List[ChatImageResponse])
async def list_conversation_images(
    conversation_id: UUID,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    List all images uploaded to a conversation.
    """
    try:
        # Verify conversation exists and ownership
        check = await conn.fetchrow(
            """
            SELECT 1
            FROM conversations c
            JOIN companions comp ON comp.id = c.companion_id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

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

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error listing images for conversation {conversation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list images"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Text Messaging Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/{conversation_id}/messages", response_model=TextMessageResponse)
async def send_text_message(
    conversation_id: UUID,
    payload: SendMessageRequest,
    req: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Send a text message and get AI response.
    This enables continuing voice conversations in text mode.
    """
    try:
        # Verify conversation exists and ownership
        conversation = await get_conversation_by_id(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        check = await conn.fetchrow(
            """
            SELECT 1
            FROM conversations c
            JOIN companions comp ON comp.id = c.companion_id
            WHERE c.id = $1 AND comp.owner_id = $2
            """,
            conversation_id,
            user.id,
        )
        if not check:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        import time

        timings: dict[str, float] = {}

        # Determine memory feature flag and evaluation guidance once
        try:
            comp_row = await CompanionRepository.get_companion_by_id_no_auth(
                conn, conversation["companion_id"]
            )
            mem_config = getattr(getattr(comp_row, "config", None), "memory", None)
            mem_enabled = bool(mem_config and mem_config.enabled)
            mem_version = getattr(mem_config, "version", 1) if mem_config else 1
            comp_max_tokens = getattr(getattr(comp_row, "config", None), "max_output_tokens", None)
        except Exception:
            mem_enabled = False
            mem_version = 1
            comp_max_tokens = None

        # Determine which engine to use (hybrid selection per MERGE_STRATEGY.md)
        # Dashboard uses companion.config.context_mode (no header override)
        use_layered = (
            getattr(getattr(comp_row, "config", None), "context_mode", "legacy") == "layered"
            if comp_row
            else False
        )
        if use_layered:
            # TODO: Implement layered mode for dashboard text
            # For now, log and fall through to legacy behavior
            logger.info(
                f"[DASHBOARD_TEXT] Companion {conversation['companion_id']} has context_mode=layered, but dashboard layered mode not yet implemented. Using legacy."
            )
            use_layered = False  # Force legacy until dashboard layered is implemented

        # 1. Persist user message (returning fields to avoid a later read)
        t0 = time.perf_counter()
        user_msg_row = await add_message_returning(
            conn,
            conversation_id,
            "user",
            payload.content,
            input_modality="text",
        )
        user_message_id = user_msg_row["id"]
        timings["persist_user_ms"] = (time.perf_counter() - t0) * 1000
        # Offload memory creation to background to keep latency low (only if enabled)
        # V1: ingest each message separately; V2: skip here, ingest full turn after response
        if mem_enabled and mem_version == 1:
            try:
                from ..services.memory_service import MemoryService
                from ..services.modal_gateway import dispatch_memory_ingest_job

                mem_cfg = await MemoryService.get_companion_memory_config(
                    conn, companion_id=conversation["companion_id"]
                )
                import asyncio as _asyncio

                async def _dispatch():
                    try:
                        await dispatch_memory_ingest_job(
                            companion_id=conversation["companion_id"],
                            items=[
                                {
                                    "message_id": str(user_message_id),
                                    "content": payload.content,
                                    "sender_type": "user",
                                    "conversation_id": str(conversation_id),
                                    "external_user_id": conversation.get("external_user_id"),
                                    "importance_guidance": (mem_cfg or {}).get(
                                        "evaluation_prompt", ""
                                    ),
                                    "weight_user": 1.0,
                                    "modality": "text",
                                    "is_core": False,
                                }
                            ],
                        )
                    except Exception as _e:
                        logger.warning(f"[TEXT] Modal memory dispatch failed: {_e}")

                _asyncio.create_task(_dispatch())
            except Exception:
                pass

        # 2. Get conversation history for context - full conversation (no last-N cap)
        import time as _t

        t_dbh = _t.perf_counter()
        messages: list[dict] = await get_full_history(conn, conversation_id, use_cache=True)
        truncated = False
        db_history_ms = (_t.perf_counter() - t_dbh) * 1000
        # Ensure we include the just-persisted user message even if the DB LIMIT clipped it
        if not messages or messages[-1]["id"] != user_message_id:
            messages.append(
                {
                    "id": user_message_id,
                    "role": "user",
                    "content": payload.content,
                    "created_at": user_msg_row.get("created_at"),
                    "input_modality": user_msg_row.get("input_modality"),
                }
            )

        # 2.1 Prepare effective system prompt (core memories)
        t_eff = _t.perf_counter()
        effective_prompt, _builder_prompt = await build_effective_system_prompt(
            conn, companion_id=conversation["companion_id"]
        )
        effective_prompt_ms = (_t.perf_counter() - t_eff) * 1000
        timings["db_history_ms"] = db_history_ms
        timings["effective_prompt_ms"] = effective_prompt_ms
        timings["prompt_assemble_ms"] = db_history_ms + effective_prompt_ms
        timings["history_msgs"] = len(messages)
        timings["history_truncated"] = truncated
        await check_and_trigger_summary(
            conn,
            companion_id=conversation["companion_id"],
            conversation_id=conversation_id,
            messages=messages,
        )

        # 2.2 Conditional regular memory retrieval for this turn (shared helper)
        memory_block = ""
        if mem_enabled:
            _tim = {}
            if mem_version == 2:
                # Memory V2: Load full scratchpad
                memory_block = await build_transient_memory_v2_block(
                    conn,
                    companion_id=conversation["companion_id"],
                    external_user_id=conversation.get("external_user_id"),
                    timings=_tim,
                )
                if "memory_v2_ms" in _tim:
                    timings["memory_v2_ms"] = _tim["memory_v2_ms"]
                if "memory_v2_entries" in _tim:
                    timings["memory_v2_entries"] = _tim["memory_v2_entries"]
            else:
                # Memory V1: Vector search
                memory_block = await build_transient_memory_block(
                    conn,
                    companion_id=conversation["companion_id"],
                    user_text=payload.content,
                    external_user_id=conversation.get("external_user_id"),
                    conversation_id=None,  # allow cross-session recall for the same user
                    memory_enabled=True,
                    timings=_tim,
                    recent_messages=messages,
                    last_n=6,
                )
                if "heuristic_ms" in _tim:
                    timings["heuristic_ms"] = _tim["heuristic_ms"]
                if "retrieval_ms" in _tim:
                    timings["retrieval_ms"] = _tim["retrieval_ms"]
                if "retrieval_items" in _tim:
                    timings["retrieval_items"] = _tim["retrieval_items"]
                if "retrieval_embed_ms" in _tim:
                    timings["retrieval_embed_ms"] = _tim["retrieval_embed_ms"]
                if "retrieval_db_ms" in _tim:
                    timings["retrieval_db_ms"] = _tim["retrieval_db_ms"]

        # 2.3 Build image context block if images are attached
        image_context_block = ""
        if payload.image_ids and len(payload.image_ids) > 0:
            try:
                # Fetch image descriptions from database
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
                    timings["image_context_items"] = len(image_rows)

                    # Link images to the user message
                    for img_id in payload.image_ids:
                        await conn.execute(
                            "UPDATE media_assets SET message_id = $1 WHERE id = $2",
                            user_message_id,
                            img_id,
                        )
            except Exception as e:
                logger.warning(f"Failed to build image context: {e}")

        # 3. Generate AI response using configured LLM
        try:
            t_llm = time.perf_counter()
            # Convert to LLM format using shared helper
            _t_sub = time.perf_counter()

            # Combine effective prompt with image context if present
            final_system_prompt = effective_prompt
            if image_context_block:
                final_system_prompt = f"{effective_prompt}\n\n{image_context_block}"

            llm_messages = assemble_llm_messages(
                effective_system_prompt=final_system_prompt,
                memory_block=memory_block,
                prior_messages=messages,
            )
            timings["llm_assemble_ms"] = (time.perf_counter() - _t_sub) * 1000

            # Generate response using direct API approach
            provider_str = (
                payload.llm_provider.value
                if hasattr(payload.llm_provider, "value")
                else payload.llm_provider
            )
            response_text = await generate_llm_response_direct(
                provider_str,
                llm_messages,
                payload.temperature,
                max_tokens=comp_max_tokens,
                timings=timings,
            )
            timings["llm_ms"] = (time.perf_counter() - t_llm) * 1000

        except Exception:
            logger.exception("Error generating LLM response")
            # Provide fallback response
            response_text = "I apologize, but I'm having trouble generating a response right now. Please try again."
            try:
                timings["llm_ms"] = (time.perf_counter() - t_llm) * 1000
            except Exception:
                pass

        # 4. Persist assistant response
        t_pa = time.perf_counter()
        asst_msg_row = await add_message_returning(
            conn,
            conversation_id,
            "assistant",
            response_text,
            input_modality="text",
        )
        assistant_message_id = asst_msg_row["id"]
        timings["persist_assistant_ms"] = (time.perf_counter() - t_pa) * 1000
        # Offload memory creation as well (only if enabled)
        if mem_enabled:
            try:
                import asyncio as _asyncio

                if mem_version == 2:
                    # Memory V2: Ingest full turn (user + assistant) together
                    from ..repositories.relationship_repository import RelationshipRepository

                    relationship, _ = await RelationshipRepository.ensure_exists(
                        conn,
                        companion_id=conversation["companion_id"],
                        user_id=conversation.get("external_user_id"),
                    )

                    async def _dispatch_v2():
                        try:
                            import modal

                            fn = modal.Function.from_name("em-memory-v2", "ingest_memory_v2")
                            fn.spawn(
                                {
                                    "relationship_id": str(relationship.id),
                                    "user_message": payload.content,
                                    "assistant_response": response_text,
                                }
                            )
                        except Exception as _e:
                            logger.warning(f"[TEXT] Modal memory v2 dispatch failed: {_e}")

                    _asyncio.create_task(_dispatch_v2())
                else:
                    # Memory V1: Ingest assistant message separately
                    from ..services.memory_service import MemoryService
                    from ..services.modal_gateway import dispatch_memory_ingest_job

                    mem_cfg = await MemoryService.get_companion_memory_config(
                        conn, companion_id=conversation["companion_id"]
                    )

                    async def _dispatch():
                        try:
                            await dispatch_memory_ingest_job(
                                companion_id=conversation["companion_id"],
                                items=[
                                    {
                                        "message_id": str(assistant_message_id),
                                        "content": response_text,
                                        "sender_type": "assistant",
                                        "conversation_id": str(conversation_id),
                                        "external_user_id": conversation.get("external_user_id"),
                                        "importance_guidance": (mem_cfg or {}).get(
                                            "evaluation_prompt", ""
                                        ),
                                        "weight_user": 1.0,
                                        "modality": "text",
                                        "is_core": False,
                                    }
                                ],
                            )
                        except Exception as _e:
                            logger.warning(f"[TEXT] Modal memory dispatch failed: {_e}")

                    _asyncio.create_task(_dispatch())
            except Exception:
                pass

        # 5. Return both messages with their database details
        payload = TextMessageResponse(
            user_message=MessageResponse(**user_msg_row),
            assistant_message=MessageResponse(**asst_msg_row),
        )
        # Update the in‑process history cache with new rows
        update_history_cache_post_turn(
            conversation_id,
            user_message={
                "id": user_message_id,
                "role": "user",
                "content": payload.user_message.content,
                "created_at": payload.user_message.created_at,
                "input_modality": user_msg_row.get("input_modality"),
            },
            assistant_message={
                "id": assistant_message_id,
                "role": "assistant",
                "content": payload.assistant_message.content,
                "created_at": payload.assistant_message.created_at,
                "input_modality": asst_msg_row.get("input_modality"),
            },
        )
        # Always include timings to aid profiling
        try:
            import json as _json

            try:
                logger.info(
                    "[TEXT_TIMINGS] conv=%s timings=%s",
                    str(conversation_id),
                    _json.dumps({k: round(v, 2) for k, v in timings.items()}),
                )
            except Exception:
                pass
            d = payload.model_dump()
            d["timings"] = {k: round(v, 2) for k, v in timings.items()}
            from fastapi.responses import JSONResponse

            return JSONResponse(content=jsonable_encoder(d))
        except Exception as _e:
            logger.warning(f"[TEXT_TIMINGS] failed to attach timings: {_e}")
            return payload

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error processing text message for conversation {conversation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process message"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────────────────────────────────────


async def generate_llm_response(llm_service, messages: List[dict]) -> str:  # pragma: no cover
    """
    Generate LLM response from conversation messages using the configured LLM service.
    This function bypasses the LLM service object and directly uses the provider configuration.
    """
    try:
        # We'll get the provider from the request context instead of trying to extract from service
        # This is a temporary solution - the provider should be passed directly
        return "I apologize, but there was an issue with the LLM configuration. Please try again."

    except Exception as e:
        logger.error(f"Error in LLM response generation: {e}")
        return (
            "I apologize, but I'm having trouble generating a response right now. Please try again."
        )


@router.post("/{conversation_id}/seed")
async def seed_conversation_history(
    conversation_id: UUID,
    payload: SeedRequest,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Dev-only: quickly seed a conversation with synthetic user/assistant messages.

    Enabled only when DISABLE_AUTH_FOR_TESTING=true or SEED_ENDPOINT_ENABLED=true.
    """
    import os

    if not (
        os.getenv("DISABLE_AUTH_FOR_TESTING") == "true"
        or os.getenv("SEED_ENDPOINT_ENABLED") == "true"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # Verify conversation exists and ownership
    conversation = await get_conversation_by_id(conn, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    check = await conn.fetchrow(
        """
        SELECT 1
        FROM conversations c
        JOIN companions comp ON comp.id = c.companion_id
        WHERE c.id = $1 AND comp.owner_id = $2
        """,
        conversation_id,
        user.id,
    )
    if not check:
        raise HTTPException(status_code=404, detail="Conversation not found")

    t0 = time.perf_counter()

    def _mk_text(prefix: str, i: int, chars: int) -> str:
        base = f"{prefix} #{i}: "
        remain = max(chars - len(base), 0)
        filler = ("lorem ipsum dolor sit amet ") * ((remain // 27) + 1)
        return (base + filler)[: max(chars, len(base))]

    msgs = []
    from uuid import uuid4

    for i in range(1, payload.pairs + 1):
        u = _mk_text(payload.user_prefix or "User says", i, payload.avg_chars)
        a = _mk_text(payload.assistant_prefix or "Assistant replies", i, payload.avg_chars)
        msgs.append(
            {
                "id": uuid4(),
                "conversation_id": conversation_id,
                "role": "user",
                "content": u,
                "input_modality": "text",
            }
        )
        msgs.append(
            {
                "id": uuid4(),
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": a,
                "input_modality": "text",
            }
        )

    await add_messages_batch(conn, msgs)
    dt = (time.perf_counter() - t0) * 1000.0
    return {"created": len(msgs), "pairs": payload.pairs, "timings": {"seed_ms": round(dt, 2)}}


# ──────────────────────────────────────────────────────────────────────────────
# Streaming Text Messaging (SSE)
# ──────────────────────────────────────────────────────────────────────────────


class StreamEvent(BaseModel):
    event: str
    data: dict | None = None
    id: int | None = None


@router.post("/{conversation_id}/messages/stream")
async def send_text_message_stream(
    conversation_id: UUID,
    payload: SendMessageRequest,
    req: Request,
    user: User = Depends(get_current_user),
):
    """Stream assistant response via SSE while preserving identical context/persistence semantics.

    Events sequence (typical): ack → status(retrieving start/end) → status(thinking start) → delta* → message → timings → done
    """
    # Pre-validate conversation + ownership so we can fail with proper HTTP status
    try:
        async with get_db_connection() as conn:
            conversation = await get_conversation_by_id(conn, conversation_id)
            if not conversation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
                )
            check = await conn.fetchrow(
                """
                SELECT 1
                FROM conversations c
                JOIN companions comp ON comp.id = c.companion_id
                WHERE c.id = $1 AND comp.owner_id = $2
                """,
                conversation_id,
                user.id,
            )
            if not check:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
                )
            # Compute memory feature gate once
            try:
                comp_row = await CompanionRepository.get_companion_by_id_no_auth(
                    conn, conversation["companion_id"]
                )
                mem_config = getattr(getattr(comp_row, "config", None), "memory", None)
                mem_enabled = bool(mem_config and mem_config.enabled)
                mem_version = getattr(mem_config, "version", 1) if mem_config else 1
                comp_max_tokens = getattr(
                    getattr(comp_row, "config", None), "max_output_tokens", None
                )
            except Exception:
                mem_enabled = False
                mem_version = 1
                comp_max_tokens = None

            # Determine which engine to use (hybrid selection per MERGE_STRATEGY.md)
            # Dashboard uses companion.config.context_mode (no header override)
            use_layered = (
                getattr(getattr(comp_row, "config", None), "context_mode", "legacy") == "layered"
                if comp_row
                else False
            )
            if use_layered:
                # TODO: Implement layered mode for dashboard streaming
                # For now, log and fall through to legacy behavior
                logger.info(
                    f"[DASHBOARD_STREAM] Companion {conversation['companion_id']} has context_mode=layered, but dashboard layered mode not yet implemented. Using legacy."
                )
                use_layered = False  # Force legacy until dashboard layered is implemented
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"[SSE] Validation failed for conversation {conversation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to start stream"
        )

    async def event_generator():
        import asyncio
        import json
        import os
        import time as _t

        seq = 0
        timings: dict[str, float | int | bool] = {}
        HEARTBEAT_SEC = 15.0
        last_beat = asyncio.get_event_loop().time()

        def sse(event: str, data: dict | None = None):
            nonlocal seq
            seq += 1
            payload = ""
            payload += f"event: {event}\n"
            payload += f"id: {seq}\n"
            if data is not None:
                try:
                    payload += f"data: {json.dumps(data)}\n\n"
                except Exception:
                    payload += f"data: {json.dumps({'_text': str(data)})}\n\n"
            else:
                payload += "data: {}\n\n"
            return payload

        try:
            # 1) Persist user message
            t0 = _t.perf_counter()
            async with get_db_connection() as conn:
                user_msg_row = await add_message_returning(
                    conn,
                    conversation_id,
                    "user",
                    payload.content,
                    input_modality="text",
                )
            timings["persist_user_ms"] = (_t.perf_counter() - t0) * 1000
            yield sse("ack", {"user_message": MessageResponse(**user_msg_row).model_dump()})

            # 1.1) Best‑effort: enqueue memory creation (parity with non‑streaming)
            # V1: ingest each message separately; V2: skip here, ingest full turn after response
            if mem_enabled and mem_version == 1:
                try:
                    import asyncio as _asyncio

                    from ..services.memory_service import MemoryService, should_store_memory
                    from ..services.modal_gateway import dispatch_memory_ingest_job

                    async with get_db_connection() as _conn:
                        mem_cfg = await MemoryService.get_companion_memory_config(
                            _conn, companion_id=conversation["companion_id"]
                        )

                    # Check importance synchronously to decide if we should store
                    evaluation_prompt = (mem_cfg or {}).get("evaluation_prompt", "")
                    min_importance_write = float(os.getenv("MEMORY_MIN_IMPORTANCE_WRITE", "0.55"))
                    should_store, importance = await should_store_memory(
                        content=payload.content,
                        importance_guidance=evaluation_prompt,
                        min_importance_write=min_importance_write,
                        is_core=False,
                    )

                    # Only emit memory_stored event and dispatch if passing threshold
                    if should_store:
                        yield sse(
                            "status",
                            {
                                "stage": "memory_stored",
                                "phase": "end",
                                "meta": {
                                    "content": payload.content[:100],  # Trimmed content for display
                                    "message_id": str(user_msg_row["id"]),
                                    "role": "user",
                                    "importance": importance,
                                    "stored_at": user_msg_row.get("created_at").isoformat()
                                    if user_msg_row.get("created_at")
                                    else None,
                                },
                            },
                        )

                        async def _dispatch_user_mem():
                            try:
                                await dispatch_memory_ingest_job(
                                    companion_id=conversation["companion_id"],
                                    items=[
                                        {
                                            "message_id": str(user_msg_row["id"]),
                                            "content": payload.content,
                                            "sender_type": "user",
                                            "conversation_id": str(conversation_id),
                                            "external_user_id": conversation.get(
                                                "external_user_id"
                                            ),
                                            "importance_guidance": evaluation_prompt,
                                            "weight_user": 1.0,
                                            "modality": "text",
                                            "is_core": False,
                                            "pre_validated": True,  # Signal to Modal worker to skip threshold check
                                            "importance": importance,  # Pass pre-computed importance
                                        }
                                    ],
                                )
                            except Exception as _e:
                                logger.warning(f"[SSE] Modal memory dispatch failed (user): {_e}")

                        _asyncio.create_task(_dispatch_user_mem())
                except Exception:
                    pass

            # 2) Build context: history + effective prompt
            t_dbh = _t.perf_counter()
            async with get_db_connection() as conn:
                messages = await get_full_history(conn, conversation_id, use_cache=True)
            db_history_ms = (_t.perf_counter() - t_dbh) * 1000

            # Ensure the latest user message is present
            if not messages or messages[-1]["id"] != user_msg_row["id"]:
                messages.append(
                    {
                        "id": user_msg_row["id"],
                        "role": "user",
                        "content": payload.content,
                        "created_at": user_msg_row.get("created_at"),
                        "input_modality": user_msg_row.get("input_modality"),
                    }
                )

            t_eff = _t.perf_counter()
            async with get_db_connection() as conn:
                effective_prompt, _builder_prompt = await build_effective_system_prompt(
                    conn, companion_id=conversation["companion_id"]
                )
            effective_prompt_ms = (_t.perf_counter() - t_eff) * 1000
            timings["db_history_ms"] = db_history_ms
            timings["effective_prompt_ms"] = effective_prompt_ms
            timings["history_msgs"] = len(messages)
            timings["history_truncated"] = False

            # Schedule summary check (non-blocking if feature disabled)
            try:
                async with get_db_connection() as conn:
                    await check_and_trigger_summary(
                        conn,
                        companion_id=conversation["companion_id"],
                        conversation_id=conversation_id,
                        messages=messages,
                    )
            except Exception:
                pass

            # 2.2) Optional transient memory retrieval
            memory_block = ""
            if mem_enabled:
                yield sse("status", {"stage": "retrieving", "phase": "start"})
                _tim = {}
                async with get_db_connection() as conn:
                    if mem_version == 2:
                        # Memory V2: Load full scratchpad
                        memory_block = await build_transient_memory_v2_block(
                            conn,
                            companion_id=conversation["companion_id"],
                            external_user_id=conversation.get("external_user_id"),
                            timings=_tim,
                        )
                    else:
                        # Memory V1: Vector search
                        memory_block = await build_transient_memory_block(
                            conn,
                            companion_id=conversation["companion_id"],
                            user_text=payload.content,
                            external_user_id=conversation.get("external_user_id"),
                            conversation_id=None,  # allow cross-session recall for the same user
                            memory_enabled=True,
                            timings=_tim,
                        )
                for k in (
                    "heuristic_ms",
                    "retrieval_ms",
                    "retrieval_items",
                    "retrieval_embed_ms",
                    "retrieval_db_ms",
                    "memory_v2_ms",
                    "memory_v2_entries",
                ):
                    if k in _tim:
                        timings[k] = _tim[k]
                yield sse(
                    "status",
                    {"stage": "retrieving", "phase": "end", "meta": {k: _tim.get(k) for k in _tim}},
                )

            # 2.3) Build image context block if images are attached
            image_context_block = ""
            if payload.image_ids and len(payload.image_ids) > 0:
                try:
                    async with get_db_connection() as conn:
                        # Fetch image descriptions from database
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
                            timings["image_context_items"] = len(image_rows)

                            # Link images to the user message
                            for img_id in payload.image_ids:
                                await conn.execute(
                                    "UPDATE media_assets SET message_id = $1 WHERE id = $2",
                                    user_msg_row["id"],
                                    img_id,
                                )
                except Exception as e:
                    logger.warning(f"[SSE] Failed to build image context: {e}")

            # 3) Assemble LLM messages
            sub_t = _t.perf_counter()
            # Combine effective prompt with image context if present
            final_system_prompt = effective_prompt
            if image_context_block:
                final_system_prompt = f"{effective_prompt}\n\n{image_context_block}"

            llm_messages = assemble_llm_messages(
                effective_system_prompt=final_system_prompt,
                memory_block=memory_block,
                prior_messages=messages,
            )
            timings["llm_assemble_ms"] = (_t.perf_counter() - sub_t) * 1000

            # 4) Stream LLM response
            provider_string = (
                payload.llm_provider.value
                if hasattr(payload.llm_provider, "value")
                else str(payload.llm_provider)
            )
            yield sse(
                "status",
                {"stage": "thinking", "phase": "start", "meta": {"provider": provider_string}},
            )

            # Map provider → (client, model)
            t1 = _t.perf_counter()
            client, model, used_default = resolve_llm_client(provider_string)
            if used_default:
                logger.warning(
                    f"Unknown LLM provider '{provider_string}', defaulting to gpt-4o-mini"
                )
            timings["llm_client_select_ms"] = (_t.perf_counter() - t1) * 1000

            max_toks = resolve_max_tokens(
                model,
                comp_max_tokens if comp_max_tokens is not None else DEFAULT_TEXT_LLM_MAX_TOKENS,
            )
            t_api = _t.perf_counter()
            out_parts: list[str] = []
            try:
                # OpenAI-compatible streaming
                completion_kwargs = {
                    "model": model,
                    "messages": llm_messages,
                    "temperature": payload.temperature,
                    "stream": True,
                }
                if str(model).startswith("gpt-5.1"):
                    completion_kwargs["max_completion_tokens"] = max_toks
                else:
                    completion_kwargs["max_tokens"] = max_toks

                stream = await client.chat.completions.create(**completion_kwargs)
                # Yield deltas as they arrive
                async for chunk in stream:  # type: ignore[attr-defined]
                    try:
                        choice = (
                            getattr(chunk, "choices", None)[0]
                            if getattr(chunk, "choices", None)
                            else None
                        )
                        delta = getattr(choice, "delta", None)
                        piece = None
                        if delta is not None:
                            piece = getattr(delta, "content", None)
                            if piece is None and isinstance(delta, dict):
                                piece = delta.get("content")
                        if piece:
                            out_parts.append(piece)
                            yield sse("delta", {"content": piece})
                    except Exception:
                        try:
                            content = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                            if content:
                                out_parts.append(content)
                                yield sse("delta", {"content": content})
                        except Exception:
                            pass
                    # Heartbeat to nudge proxies if idle
                    now = asyncio.get_event_loop().time()
                    if (now - last_beat) >= HEARTBEAT_SEC:
                        yield ": keep-alive\n\n"
                        last_beat = now
                timings["llm_api_ms"] = (_t.perf_counter() - t_api) * 1000
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.exception("[SSE] LLM streaming failed")
                yield sse("error", {"message": "stream_failed", "detail": str(e)})
                return
            finally:
                yield sse("status", {"stage": "thinking", "phase": "end"})

            response_text = "".join(out_parts).strip()

            # 5) Persist assistant response
            t_pa = _t.perf_counter()
            try:
                async with get_db_connection() as conn:
                    asst_msg_row = await add_message_returning(
                        conn,
                        conversation_id,
                        "assistant",
                        response_text,
                        input_modality="text",
                    )
                timings["persist_assistant_ms"] = (_t.perf_counter() - t_pa) * 1000
            except Exception as e:
                logger.exception("[SSE] Failed to persist assistant message")
                yield sse("error", {"message": "persist_failed", "detail": str(e)})
                return

            # 5.1) Best‑effort: enqueue memory creation (parity with non‑streaming)
            if mem_enabled:
                try:
                    import asyncio as _asyncio

                    if mem_version == 2:
                        # Memory V2: Ingest full turn (user + assistant) together
                        from ..repositories.relationship_repository import RelationshipRepository

                        async with get_db_connection() as _conn:
                            relationship, _ = await RelationshipRepository.ensure_exists(
                                _conn,
                                companion_id=conversation["companion_id"],
                                user_id=conversation.get("external_user_id"),
                            )

                        async def _dispatch_v2():
                            try:
                                import modal

                                fn = modal.Function.from_name("em-memory-v2", "ingest_memory_v2")
                                fn.spawn(
                                    {
                                        "relationship_id": str(relationship.id),
                                        "user_message": payload.content,
                                        "assistant_response": response_text,
                                    }
                                )
                            except Exception as _e:
                                logger.warning(f"[SSE] Modal memory v2 dispatch failed: {_e}")

                        _asyncio.create_task(_dispatch_v2())
                    else:
                        # Memory V1: Ingest assistant message separately
                        from ..services.memory_service import MemoryService, should_store_memory
                        from ..services.modal_gateway import dispatch_memory_ingest_job

                        async with get_db_connection() as _conn:
                            mem_cfg = await MemoryService.get_companion_memory_config(
                                _conn, companion_id=conversation["companion_id"]
                            )

                        # Check importance synchronously to decide if we should store
                        evaluation_prompt = (mem_cfg or {}).get("evaluation_prompt", "")
                        min_importance_write = float(
                            os.getenv("MEMORY_MIN_IMPORTANCE_WRITE", "0.55")
                        )
                        should_store, importance = await should_store_memory(
                            content=response_text,
                            importance_guidance=evaluation_prompt,
                            min_importance_write=min_importance_write,
                            is_core=False,
                        )

                        # Only emit memory_stored event and dispatch if passing threshold
                        if should_store:
                            yield sse(
                                "status",
                                {
                                    "stage": "memory_stored",
                                    "phase": "end",
                                    "meta": {
                                        "content": response_text[
                                            :100
                                        ],  # Trimmed content for display
                                        "message_id": str(asst_msg_row["id"]),
                                        "role": "assistant",
                                        "importance": importance,
                                        "stored_at": asst_msg_row.get("created_at").isoformat()
                                        if asst_msg_row.get("created_at")
                                        else None,
                                    },
                                },
                            )

                            async def _dispatch_asst_mem():
                                try:
                                    await dispatch_memory_ingest_job(
                                        companion_id=conversation["companion_id"],
                                        items=[
                                            {
                                                "message_id": str(asst_msg_row["id"]),
                                                "content": response_text,
                                                "sender_type": "assistant",
                                                "conversation_id": str(conversation_id),
                                                "external_user_id": conversation.get(
                                                    "external_user_id"
                                                ),
                                                "importance_guidance": evaluation_prompt,
                                                "weight_user": 1.0,
                                                "modality": "text",
                                                "is_core": False,
                                                "pre_validated": True,  # Signal to Modal worker to skip threshold check
                                                "importance": importance,  # Pass pre-computed importance
                                            }
                                        ],
                                    )
                                except Exception as _e:
                                    logger.warning(
                                        f"[SSE] Modal memory dispatch failed (assistant): {_e}"
                                    )

                            _asyncio.create_task(_dispatch_asst_mem())
                except Exception:
                    pass

            # Update in-process history cache
            try:
                update_history_cache_post_turn(
                    conversation_id,
                    user_message={
                        "id": user_msg_row["id"],
                        "role": "user",
                        "content": payload.content,
                        "created_at": user_msg_row.get("created_at"),
                        "input_modality": user_msg_row.get("input_modality"),
                    },
                    assistant_message={
                        "id": asst_msg_row["id"],
                        "role": "assistant",
                        "content": response_text,
                        "created_at": asst_msg_row.get("created_at"),
                        "input_modality": asst_msg_row.get("input_modality"),
                    },
                )
            except Exception:
                pass

            # Emit final message and timings
            try:
                yield sse(
                    "message", {"assistant_message": MessageResponse(**asst_msg_row).model_dump()}
                )
                round2 = {
                    k: (round(v, 2) if isinstance(v, (int | float)) else v)
                    for k, v in timings.items()
                }
                yield sse("timings", round2)
            except Exception:
                pass

            # Done
            yield sse("done", {})

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.exception(f"[SSE] Unexpected error in event stream for {conversation_id}")
            try:
                yield sse("error", {"message": "internal_error", "detail": str(e)})
            except Exception:
                pass

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
