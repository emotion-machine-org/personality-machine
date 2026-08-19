import logging
import os
from typing import List, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from ..auth import get_current_user, invalidate_api_key_cache
from ..context.hydration.cache_keys import CacheNamespace, config_cache_key
from ..db import get_db
from ..models.companion import (
    CompanionConfig,
    CompanionCreate,
    CompanionDetail,
    CompanionSummary,
    CompanionUpdate,
    CompanionVersionSummary,
    Voice,
)
from ..models.project import ApiKeyCreateRequest, ProjectApiKey, ProjectApiKeyWithSecret
from ..models.user import User
from ..models.v2.message import MessageCreate
from ..repositories.companion import CompanionRepository
from ..repositories.project import (
    KnowledgeAssetRepository,
    ProjectApiKeyRepository,
    ProjectRepository,
)
from ..repositories.project_secrets import ProjectSecret, ProjectSecretRepository
from ..repositories.relationship_repository import RelationshipRepository
from ..repositories.tool_index_repository import ToolIndexRepository
from ..repositories.voice import VoiceRepository
from ..schemas.knowledge import (
    KnowledgeAssetResponse,
    KnowledgeIngestionRequest,
    KnowledgeJobResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from ..services.api_keys import generate_project_api_key
from ..services.cache_manager import cache
from ..services.encryption import encrypt_secret
from ..services.knowledge_service import (
    create_asset_from_upload,
    delete_knowledge_asset,
    ingest_knowledge_payload,
)
from ..services.onboarding_service import create_companion_from_answers as create_companion_service
from ..services.openai_vector_store import search_vector_store
from .v2.messages import _process_message_non_streaming

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])
INTRO_MESSAGE_SENT_AT_KEY = "intro_message_sent_at"

# ──────────────────────────────────────────────────────────────────────────────
# User endpoints
# ──────────────────────────────────────────────────────────────────────────────


async def _reset_relationship_runtime_state(
    conn: asyncpg.Connection,
    *,
    relationship_id: UUID,
    companion_id: UUID,
    user_id: str,
    clear_profile: bool,
) -> None:
    """Clear conversation/runtime artifacts for a relationship to prevent stale leakage."""
    # Clear runtime/session/message artifacts tied to the relationship.
    await conn.execute(
        "DELETE FROM v2_sessions WHERE relationship_id = $1",
        relationship_id,
    )
    await conn.execute(
        "DELETE FROM messages WHERE relationship_id = $1",
        relationship_id,
    )
    await conn.execute(
        "DELETE FROM relationship_summaries WHERE relationship_id = $1",
        relationship_id,
    )
    await conn.execute(
        "DELETE FROM memory_v2_entries WHERE relationship_id = $1",
        relationship_id,
    )

    # Legacy memories table is keyed by companion + external_user_id (not relationship_id).
    await conn.execute(
        """
        DELETE FROM memories
        WHERE companion_id = $1
          AND external_user_id = $2
        """,
        companion_id,
        user_id,
    )

    if clear_profile:
        await conn.execute(
            """
            UPDATE relationships
            SET profile = '{}'::jsonb,
                message_count = 0,
                last_interaction_at = NULL,
                last_summarized_at = NULL,
                last_summarized_message_count = 0,
                metadata = COALESCE(metadata, '{}'::jsonb) - $2,
                version = version + 1
            WHERE id = $1
            """,
            relationship_id,
            INTRO_MESSAGE_SENT_AT_KEY,
        )
        return

    await conn.execute(
        """
        UPDATE relationships
        SET message_count = 0,
            last_interaction_at = NULL,
            last_summarized_at = NULL,
            last_summarized_message_count = 0,
            metadata = COALESCE(metadata, '{}'::jsonb) - $2,
            version = version + 1
        WHERE id = $1
        """,
        relationship_id,
        INTRO_MESSAGE_SENT_AT_KEY,
    )


@router.get("/me")
async def get_current_user_profile(user: User = Depends(get_current_user)):
    """Get current authenticated user's profile"""
    return {
        "id": str(user.id),
        "clerk_user_id": user.clerk_user_id,
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "auth_provider": user.auth_provider,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "onboarding_completed": user.onboarding_completed,
        "onboarding_completed_at": user.onboarding_completed_at.isoformat()
        if user.onboarding_completed_at
        else None,
    }


@router.get("/auth/dev-token")
async def get_dev_token():
    """Return a development token when auth bypass is enabled.

    - Requires DISABLE_AUTH_FOR_TESTING=true in the server environment.
    - Intended for local notebooks and manual testing only.
    """
    if os.getenv("DISABLE_AUTH_FOR_TESTING") == "true":
        return {"token": "mock-dev-token"}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/me/complete-onboarding")
async def complete_onboarding(
    user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """Mark onboarding as completed for the current user"""
    try:
        await conn.execute(
            """
            UPDATE users
            SET onboarding_completed = true,
                onboarding_completed_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
        """,
            user.id,
        )

        return {"message": "Onboarding completed successfully"}
    except Exception as e:
        logger.error(f"Failed to complete onboarding for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete onboarding",
        )


class OnboardingAnswersRequest(BaseModel):
    """Request body for creating a companion from onboarding answers."""

    purpose: Literal["friend", "coach", "teacher", "custom"]
    approach: Literal["playful", "supportive", "challenging"]
    tone: Literal["casual", "direct", "formal"]
    custom_purpose: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=100)


class OnboardingCompanionResponse(BaseModel):
    """Response after creating a companion from onboarding answers."""

    companion_id: UUID
    companion_name: str
    message: str


@router.post(
    "/onboarding/create-companion-from-answers",
    response_model=OnboardingCompanionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_companion_from_onboarding_answers(
    body: OnboardingAnswersRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a new companion from onboarding answers and mark onboarding as complete."""
    try:
        companion, companion_name = await create_companion_service(
            conn,
            user_id=user.id,
            purpose=body.purpose,
            approach=body.approach,
            tone=body.tone,
            custom_purpose=body.custom_purpose,
            name=body.name,
            mark_onboarding_complete=True,
        )

        return OnboardingCompanionResponse(
            companion_id=companion.id,
            companion_name=companion_name,
            message=f"Your companion '{companion_name}' is ready!",
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create companion from onboarding for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create companion",
        ) from None


# Fixed UUID for the onboarding companion (must match seed script)
ONBOARDING_COMPANION_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_companion_for_test_user_endpoint(
    conn: asyncpg.Connection, companion_id: UUID, user_id: UUID
) -> CompanionDetail:
    """Get companion for test-user endpoints, allowing access to onboarding companion.

    The onboarding companion is a shared system companion that any authenticated user
    can interact with during onboarding. Other companions require ownership.
    """
    if companion_id == ONBOARDING_COMPANION_ID:
        companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    else:
        companion = await CompanionRepository.get_companion_by_id(conn, companion_id, user_id)

    if not companion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    return companion


# Internal API key for tool-to-tool calls (from Modal workers)
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "internal-dev-key")


class InternalOnboardingRequest(BaseModel):
    """Request body for internal onboarding endpoint (tool calls)."""

    description: str = Field(..., max_length=1000, description="Rich description of the companion")
    name: str | None = Field(default=None, max_length=100)
    vibe: Literal["chill", "energetic", "warm", "witty", "intense"] | None = Field(default=None)
    relationship_id: UUID = Field(..., description="Relationship ID from the voice session")


@router.post(
    "/internal/onboarding/create-companion-from-answers",
    response_model=OnboardingCompanionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,  # Hide from public docs
)
async def create_companion_from_onboarding_internal(
    body: InternalOnboardingRequest,
    x_internal_key: str | None = Header(default=None, alias="X-Internal-Key"),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a new companion from onboarding answers (internal tool call).

    Used by voice session tools. Authenticates via X-Internal-Key header
    and uses relationship_id to look up the user.
    """
    # Validate internal key
    if x_internal_key != INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal key",
        )

    # Look up relationship to get user info
    relationship = await RelationshipRepository.get_by_id(conn, body.relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found",
        )

    # Get user_id from external_user_id (must be internal user UUID)
    # Web app flows should pass internal UUID, not Clerk ID
    try:
        user_id = UUID(relationship.external_user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID in relationship - expected UUID",
        )

    try:
        # Import the new function
        from ..services.onboarding_service import create_companion_from_description

        companion, companion_name = await create_companion_from_description(
            conn,
            user_id=user_id,
            description=body.description,
            name=body.name,
            vibe=body.vibe,
            mark_onboarding_complete=True,
        )

        logger.info(
            f"Created companion from onboarding (internal) for user {user_id}: "
            f"{companion.id} ({companion_name}) via relationship {body.relationship_id}"
        )

        return OnboardingCompanionResponse(
            companion_id=companion.id,
            companion_name=companion_name,
            message=f"Your companion '{companion_name}' is ready!",
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            f"Failed to create companion from onboarding (internal) for relationship {body.relationship_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create companion",
        ) from None


class OnboardingChatRequest(BaseModel):
    """Request body for onboarding chat."""

    message: str = Field(..., min_length=1, max_length=2000)


class OnboardingChatResponse(BaseModel):
    """Response from onboarding chat."""

    response: str
    message_id: UUID


@router.post("/onboarding/chat", response_model=OnboardingChatResponse)
async def onboarding_chat(
    body: OnboardingChatRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Send a message to the onboarding companion and get a response.

    Uses JWT auth and reuses the v2 message processing logic internally.
    This endpoint is designed for the text-based onboarding flow.
    """
    try:
        # Get or create relationship with onboarding companion
        relationship, _ = await RelationshipRepository.ensure_exists(
            conn,
            companion_id=ONBOARDING_COMPANION_ID,
            user_id=str(user.id),  # Use internal UUID as user_id
        )

        # Get companion (no auth check - onboarding companion is public)
        companion = await CompanionRepository.get_companion_by_id_no_auth(
            conn, ONBOARDING_COMPANION_ID
        )
        if not companion:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Onboarding companion not found",
            )

        # Use the v2 message processing logic
        message = MessageCreate(content=body.message)
        response, _ = await _process_message_non_streaming(
            conn,
            relationship=relationship,
            companion=companion,
            message=message,
            project_id=companion.project_id,
        )

        return OnboardingChatResponse(
            response=response.content,
            message_id=response.id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to process onboarding chat for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process message",
        ) from None


# ──────────────────────────────────────────────────────────────────────────────
# Companion endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/companions", response_model=List[CompanionSummary])
async def get_companions(
    user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """Get all companions belonging to the current user"""
    try:
        companions = await CompanionRepository.get_companions_by_user(conn, user.id)
        return companions
    except Exception as e:
        logger.error(f"Failed to fetch companions for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch companions"
        )


@router.get("/companions/{companion_id}", response_model=CompanionConfig)
async def get_companion(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get companion configuration by ID"""
    try:
        companion = await CompanionRepository.get_companion_by_id(conn, companion_id, user.id)
        if not companion:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
        # Workaround: some older rows store the entire config JSON string in full_system_prompt.
        # Ensure we return clean text for full_system_prompt.
        cfg = companion.config
        try:
            fsp = cfg.system_prompt.full_system_prompt or ""
            if isinstance(fsp, str) and fsp.strip().startswith("{"):
                import json as _json

                parsed = _json.loads(fsp)
                if isinstance(parsed, dict):
                    inner = parsed.get("system_prompt") or {}
                    inner_fsp = inner.get("full_system_prompt")
                    if isinstance(inner_fsp, str) and inner_fsp:
                        cfg.system_prompt.full_system_prompt = inner_fsp
        except Exception:
            # If anything goes wrong, fall back to existing value
            pass
        return cfg
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch companion {companion_id} for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch companion"
        )


@router.get("/companions/{companion_id}/versions", response_model=List[CompanionVersionSummary])
async def get_companion_versions(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """List available versions for a companion owned by the current user."""
    try:
        versions = await CompanionRepository.list_companion_versions(conn, companion_id, user.id)
        if versions is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
        return versions
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to fetch companion versions for {companion_id} and user {user.id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch companion versions",
        )


@router.get("/companions/{companion_id}/versions/{version_id}", response_model=CompanionConfig)
async def get_companion_version_config(
    companion_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Retrieve a sanitized configuration snapshot for a specific version."""
    try:
        config = await CompanionRepository.get_companion_version_config(
            conn, companion_id, version_id, user.id
        )
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Companion version not found"
            )
        return config
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to fetch companion version {version_id} for companion {companion_id} and user {user.id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch companion version",
        )


@router.get("/companions/{companion_id}/classifier-inputs")
async def get_classifier_inputs(
    companion_id: UUID,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get all classifier inputs for a companion.

    Returns the classifier system prompt, tool summaries, knowledge summary,
    and behavior hints that are provided to the classifier.
    """
    from ..context.intent_classifier import CLASSIFIER_SYSTEM_PROMPT
    from ..repositories.tool_index_repository import ToolIndexRepository

    # Get companion to verify access and get config
    companion = await CompanionRepository.get_companion_by_id(conn, companion_id, user.id)
    if not companion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    # 1. Get tool specs with classifier summaries
    tool_summaries = await ToolIndexRepository.load_tool_summaries(conn, companion_id=companion_id)

    # 2. Get knowledge classifier summary from companion config
    # Check if knowledge layer is enabled (via layers array or knowledge.enabled)
    knowledge_enabled = False
    if companion.config:
        # Check layers array for knowledge_base
        if companion.config.layers:
            for layer in companion.config.layers:
                if layer.category == "knowledge_base" and layer.enabled:
                    knowledge_enabled = True
                    break
        # Also check knowledge.enabled
        if companion.config.knowledge and companion.config.knowledge.enabled:
            knowledge_enabled = True

    knowledge_summary = None
    if knowledge_enabled and companion.config and companion.config.knowledge:
        # Use custom summary if set, otherwise use default
        knowledge_summary = (
            companion.config.knowledge.classifier_summary
            or "Documentation, FAQs, knowledge sources"
        )

    # 3. Get behaviors with classifier hints (only enabled, classifier-eligible ones)
    behavior_hints = []
    if companion.project_id:
        rows = await conn.fetch(
            """
            SELECT b.key, b.name, cbl.classifier_hint
            FROM behaviors b
            JOIN companion_behavior_links cbl ON cbl.behavior_id = b.id
            WHERE cbl.companion_id = $1
              AND cbl.relationship_id IS NULL
              AND cbl.enabled = TRUE
              AND cbl.classifier_eligible = TRUE
              AND cbl.classifier_hint IS NOT NULL
              AND cbl.classifier_hint != ''
            ORDER BY b.name
            """,
            companion_id,
        )
        behavior_hints = [
            {"key": row["key"], "name": row["name"], "hint": row["classifier_hint"]} for row in rows
        ]

    return {
        "system_prompt": CLASSIFIER_SYSTEM_PROMPT,
        "tool_summaries": tool_summaries,
        "knowledge_summary": knowledge_summary,
        "behavior_hints": behavior_hints,
    }


@router.post("/companions", response_model=CompanionDetail, status_code=status.HTTP_201_CREATED)
async def create_companion(
    companion_data: CompanionCreate,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a new companion"""
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)
        companion = await CompanionRepository.create_companion(
            conn,
            user.id,
            companion_data,
            project_id=project.id,
        )
        return CompanionDetail(
            id=companion.id,
            name=companion.name,
            description=companion.description,
            owner_id=companion.owner_id,
            project_id=companion.project_id,
            created_at=companion.created_at,
            metadata=companion.metadata,
            config=companion.config,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create companion for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create companion"
        )


@router.put("/companions/{companion_id}", response_model=CompanionConfig)
async def update_companion(
    companion_id: UUID,
    updates: CompanionUpdate,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Update companion configuration (creates new version)"""
    try:
        companion = await CompanionRepository.update_companion(conn, companion_id, user.id, updates)
        if not companion:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
        # Invalidate cached effective prompt so new sessions use updated config
        cache.delete("eff_prompt", str(companion_id))
        # Also invalidate the hydration config loader cache
        cache.delete(CacheNamespace.COMPANION_CONFIG, config_cache_key(companion_id))
        return companion.config
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update companion {companion_id} for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update companion"
        )


@router.patch("/companions/{companion_id}/config", response_model=CompanionConfig)
async def update_companion_config(
    companion_id: UUID,
    config: CompanionConfig,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Update companion configuration without creating a new deployment (for testing/development)"""
    try:
        # Get existing companion to verify ownership
        companion = await CompanionRepository.get_companion_by_id(conn, companion_id, user.id)
        if not companion:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

        # Create new version with the updated config
        updates = CompanionUpdate(config=config)
        updated_companion = await CompanionRepository.update_companion(
            conn, companion_id, user.id, updates
        )

        # Invalidate cached effective prompt so new sessions use updated config
        cache.delete("eff_prompt", str(companion_id))
        # Also invalidate the hydration config loader cache
        cache.delete(CacheNamespace.COMPANION_CONFIG, config_cache_key(companion_id))
        return updated_companion.config
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update companion config {companion_id} for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update companion configuration",
        )


@router.delete("/companions/{companion_id}")
async def delete_companion(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete companion"""
    try:
        deleted = await CompanionRepository.delete_companion(conn, companion_id, user.id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
        return {"message": "Companion deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete companion {companion_id} for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete companion"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Knowledge assets & ingestion
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companions/{companion_id}/knowledge-assets",
    response_model=KnowledgeAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_knowledge_asset(
    companion_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    _, project = await _ensure_user_companion_context(conn, companion_id=companion_id, user=user)
    asset = await create_asset_from_upload(
        conn,
        project_id=project.id,
        companion_id=companion_id,
        owner_user_id=user.id,
        upload=file,
    )
    return KnowledgeAssetResponse.from_model(asset)


@router.get(
    "/companions/{companion_id}/knowledge-assets",
    response_model=List[KnowledgeAssetResponse],
)
async def list_knowledge_assets(
    companion_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    await _ensure_user_companion_context(conn, companion_id=companion_id, user=user)
    assets = await KnowledgeAssetRepository.list_assets_for_companion(
        conn,
        companion_id,
        limit=limit,
    )
    return [KnowledgeAssetResponse.from_model(asset) for asset in assets]


@router.delete("/companions/{companion_id}/knowledge-assets/{asset_id}")
async def delete_knowledge_asset_endpoint(
    companion_id: UUID,
    asset_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete a knowledge asset and its vector store file."""
    await _ensure_user_companion_context(conn, companion_id=companion_id, user=user)
    deleted = await delete_knowledge_asset(conn, asset_id=asset_id, companion_id=companion_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge asset not found",
        )
    return {"message": "Knowledge asset deleted successfully"}


@router.post(
    "/companions/{companion_id}/knowledge",
    response_model=KnowledgeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_knowledge(
    companion_id: UUID,
    payload: KnowledgeIngestionRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    _, project = await _ensure_user_companion_context(conn, companion_id=companion_id, user=user)
    job = await ingest_knowledge_payload(
        conn,
        project_id=project.id,
        companion_id=companion_id,
        payload_type=payload.type,
        inline_content=payload.content,
        payload_key=payload.key,
        asset_id=payload.asset_id,
        submitted_by_user=user.id,
        submitted_by_key=None,
        source_label="dashboard",
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
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    _, project = await _ensure_user_companion_context(conn, companion_id=companion_id, user=user)
    results = await search_vector_store(
        conn,
        companion_id=companion_id,
        query=payload.query,
        max_results=payload.max_results or 5,
        filters=payload.filters,
        mode=payload.mode,
    )
    return KnowledgeSearchResponse(results=results)


@router.post("/users/me/default-companion", response_model=CompanionConfig)
async def create_default_companion(
    user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """Create default companion for new users"""
    try:
        # Check if user already has companions
        existing_companions = await CompanionRepository.get_companions_by_user(conn, user.id)
        if existing_companions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="User already has companions"
            )

        # Create default companion with sensible defaults
        default_companion_data = CompanionCreate(
            name="My AI Companion",
            description="A friendly AI companion ready to chat",
            config=CompanionConfig(
                system_prompt={
                    "identity": "You are a helpful and friendly AI companion",
                    "personality": "Warm, empathetic, and supportive",
                    "style": "Conversational and approachable",
                    "backstory": "You're designed to be a thoughtful companion for meaningful conversations",
                    "additional_instructions": "Be encouraging and help users explore their thoughts and ideas",
                    "self_image": "A caring companion who genuinely enjoys helping people",
                    "full_system_prompt": "You are a helpful and friendly AI companion. You are warm, empathetic, and supportive. Respond in a conversational and approachable way. You're designed to be a thoughtful companion for meaningful conversations. Be encouraging and help users explore their thoughts and ideas. You see yourself as a caring companion who genuinely enjoys helping people.",
                },
                voice={
                    "popular_options": ["OpenAI - speech-to-speech"],
                    "voice": ["alloy"],
                    "temperature": 0.25,
                },
                memory={
                    "enabled": False,
                    "core_memories": [],
                    "memory_evaluation_prompt": "",
                    "recency": 0.995,
                    "top_k": 50,
                    "min_saliency": 0.2,
                },
            ),
        )
        project = await ProjectRepository.ensure_default_project(conn, user.id)
        companion = await CompanionRepository.create_companion(
            conn,
            user.id,
            default_companion_data,
            project_id=project.id,
        )
        return companion.config
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create default companion for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create default companion",
        )


async def _ensure_user_companion_context(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    user: User,
):
    companion = await CompanionRepository.get_companion_by_id(conn, companion_id, user.id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )
    project = await ProjectRepository.ensure_default_project(conn, user.id)
    if companion.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found in project",
        )
    return companion, project


# ──────────────────────────────────────────────────────────────────────────────
# Minimal name-only endpoint (get/update current user's companion name)
# ──────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel  # local import to avoid touching top imports


class NamePayload(BaseModel):
    name: str


@router.get("/companion-name")
async def get_companion_name(
    user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """Return the name and id of the user's primary companion.

    If the user has none, create a default and return it.
    """
    try:
        companions = await CompanionRepository.get_companions_by_user(conn, user.id)
        if companions:
            primary = companions[0]
            return {"id": str(primary.id), "name": primary.name}

        project = await ProjectRepository.ensure_default_project(conn, user.id)
        created = await CompanionRepository.create_companion(
            conn,
            user.id,
            CompanionCreate(name="My AI Companion", description="", config=CompanionConfig()),
            project_id=project.id,
        )
        return {"id": str(created.id), "name": created.name}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get companion name for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get companion name"
        )


@router.put("/companion-name")
async def set_companion_name(
    payload: NamePayload,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Set the name of the user's primary companion.

    - Picks the most recently updated companion if multiple exist
    - Creates one if none exists, then sets its name
    Returns { id, name } for immediate UI usage.
    """
    try:
        companions = await CompanionRepository.get_companions_by_user(conn, user.id)
        if companions:
            target_id = companions[0].id
        else:
            project = await ProjectRepository.ensure_default_project(conn, user.id)
            created = await CompanionRepository.create_companion(
                conn,
                user.id,
                CompanionCreate(name="My AI Companion", description="", config=CompanionConfig()),
                project_id=project.id,
            )
            target_id = created.id

        updated = await CompanionRepository.update_companion(
            conn, target_id, user.id, updates=CompanionUpdate(name=payload.name)
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

        return {"id": str(updated.id), "name": payload.name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set companion name for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to set companion name"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Voice endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/voices", response_model=List[Voice])
async def get_voices(
    provider: str | None = None,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get available voices, optionally filtered by provider"""
    try:
        if provider:
            voices = await VoiceRepository.get_voices_by_provider(conn, provider)
        else:
            voices = await VoiceRepository.get_all_voices(conn)
        return voices
    except Exception as e:
        logger.error(f"Failed to fetch voices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch voices"
        )


@router.get("/voices/{voice_id}", response_model=Voice)
async def get_voice(
    voice_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get voice by ID"""
    try:
        voice = await VoiceRepository.get_voice_by_id(conn, voice_id)
        if not voice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice not found")
        return voice
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch voice {voice_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch voice"
        )


# ──────────────────────────────────────────────────────────────────────────────
# API Key Management endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/projects/default/keys", response_model=List[ProjectApiKey])
async def list_api_keys(
    user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """List all API keys for the user's default project"""
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)
        keys = await ProjectApiKeyRepository.list_keys(conn, project.id)
        return keys
    except Exception as e:
        logger.error(f"Failed to list API keys for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list API keys"
        )


@router.post(
    "/projects/default/keys",
    response_model=ProjectApiKeyWithSecret,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a new API key for the user's default project.

    The full key is returned only once in this response.
    Store it securely - it cannot be retrieved again.
    """
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)

        # Generate the key components
        full_key, prefix, salt, secret_hash = generate_project_api_key()

        # Store the key in the database
        api_key = await ProjectApiKeyRepository.create_key(
            conn,
            project_id=project.id,
            created_by=user.id,
            name=payload.name,
            prefix=prefix,
            secret_hash=secret_hash,
            salt=salt,
            scopes=["read", "write"],
            metadata={},
            expires_at=None,
        )

        # Return the key with the secret (only time it's shown)
        return ProjectApiKeyWithSecret(
            id=api_key.id,
            project_id=api_key.project_id,
            created_by=api_key.created_by,
            name=api_key.name,
            prefix=api_key.prefix,
            status=api_key.status,
            scopes=api_key.scopes,
            metadata=api_key.metadata,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            expires_at=api_key.expires_at,
            secret=full_key,
        )
    except Exception as e:
        logger.error(f"Failed to create API key for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create API key"
        )


@router.delete("/projects/default/keys/{key_id}")
async def revoke_api_key(
    key_id: UUID, user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """Revoke an API key. This action cannot be undone."""
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)

        # Verify the key belongs to the user's project
        existing_key = await ProjectApiKeyRepository.get_key(conn, key_id, project.id)
        if not existing_key:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

        if existing_key.status == "revoked":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="API key is already revoked"
            )

        # Revoke the key
        success = await ProjectApiKeyRepository.set_status(conn, key_id, "revoked")
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke API key"
            )

        # Invalidate the cache for this API key
        invalidate_api_key_cache(existing_key.prefix)

        return {"message": "API key revoked successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to revoke API key {key_id} for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke API key"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Project Secrets endpoints
# ──────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel, Field


class SecretCreateRequest(BaseModel):
    """Request to create a new project secret."""

    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")
    value: str = Field(..., min_length=1)
    description: str | None = Field(None, max_length=500)


class SecretUpdateRequest(BaseModel):
    """Request to update a project secret's value."""

    value: str = Field(..., min_length=1)
    description: str | None = Field(None, max_length=500)


@router.get("/projects/default/secrets", response_model=List[ProjectSecret])
async def list_project_secrets(
    user: User = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db)
):
    """List all secrets for the user's default project.

    Returns metadata only - secret values are never exposed via API.
    """
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)
        secrets = await ProjectSecretRepository.list_secrets(conn, project.id)
        return secrets
    except Exception as e:
        logger.error(f"Failed to list secrets for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list secrets"
        )


@router.post(
    "/projects/default/secrets", response_model=ProjectSecret, status_code=status.HTTP_201_CREATED
)
async def create_project_secret(
    payload: SecretCreateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a new project secret.

    The secret value is encrypted before storage and can never be retrieved.
    To update a secret, use the PUT endpoint.
    """
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)

        # Check if secret already exists
        if await ProjectSecretRepository.secret_exists(conn, project.id, payload.name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Secret '{payload.name}' already exists",
            )

        # Encrypt the value
        encrypted_value = encrypt_secret(payload.value)

        # Create the secret
        secret = await ProjectSecretRepository.create_secret(
            conn,
            project_id=project.id,
            secret_name=payload.name,
            encrypted_value=encrypted_value,
            description=payload.description,
        )

        return secret
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create secret for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create secret"
        )


@router.put("/projects/default/secrets/{secret_name}", response_model=ProjectSecret)
async def update_project_secret(
    secret_name: str,
    payload: SecretUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Update a project secret's value (for rotation).

    The new value is encrypted before storage.
    """
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)

        # Check if secret exists
        if not await ProjectSecretRepository.secret_exists(conn, project.id, secret_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Secret '{secret_name}' not found"
            )

        # Encrypt the new value
        encrypted_value = encrypt_secret(payload.value)

        # Update the secret
        secret = await ProjectSecretRepository.update_secret(
            conn,
            project_id=project.id,
            secret_name=secret_name,
            encrypted_value=encrypted_value,
            description=payload.description,
        )

        if not secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update secret"
            )

        return secret
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update secret {secret_name} for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update secret"
        )


@router.delete("/projects/default/secrets/{secret_name}")
async def delete_project_secret(
    secret_name: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete a project secret.

    Also removes references to this secret from any tool specs.
    """
    try:
        project = await ProjectRepository.ensure_default_project(conn, user.id)

        # Check if secret exists
        if not await ProjectSecretRepository.secret_exists(conn, project.id, secret_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Secret '{secret_name}' not found"
            )

        # Remove secret references from all tool_specs in this project
        specs_updated = await ToolIndexRepository.remove_secret_from_specs(
            conn, project_id=project.id, secret_name=secret_name
        )

        # Delete the secret
        success = await ProjectSecretRepository.delete_secret(conn, project.id, secret_name)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete secret"
            )

        msg = f"Secret '{secret_name}' deleted successfully"
        if specs_updated > 0:
            msg += f" (removed from {specs_updated} tool spec{'s' if specs_updated > 1 else ''})"

        return {"message": msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete secret {secret_name} for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete secret"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test Relationships (Dashboard Simulator)
# ──────────────────────────────────────────────────────────────────────────────


class TestUserSummary(BaseModel):
    """Summary of a test relationship for the dashboard."""

    id: str
    user_id: str
    message_count: int
    last_interaction_at: str | None
    profile_preview: dict | None = None


class TestUserDetail(BaseModel):
    """Detailed test relationship info."""

    id: str
    user_id: str
    message_count: int
    last_interaction_at: str | None
    profile: dict
    config: dict
    created_at: str


class TestUserConfigResponse(BaseModel):
    """Config response for a test relationship."""

    config: dict
    version: int


class DashboardRelationshipSummary(BaseModel):
    """Summary item for relationships page in dashboard."""

    id: str
    user_id: str
    message_count: int
    last_interaction_at: str | None
    created_at: str
    profile: dict


class DashboardRelationshipListResponse(BaseModel):
    """Paginated relationship list response for dashboard."""

    items: List[DashboardRelationshipSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


@router.get(
    "/companions/{companion_id}/relationships",
    response_model=DashboardRelationshipListResponse,
)
async def list_dashboard_relationships(
    companion_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(
        default=None, description="Filter by user_id (contains, case-insensitive)"
    ),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """List relationships for dashboard management UI (Clerk-auth)."""
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        search_term = search.strip() if search else None
        if search_term:
            like = f"%{search_term}%"
            count_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt
                FROM relationships
                WHERE companion_id = $1
                  AND external_user_id ILIKE $2
                """,
                companion_id,
                like,
            )
            rows = await conn.fetch(
                """
                SELECT id, external_user_id, message_count, last_interaction_at, created_at, profile
                FROM relationships
                WHERE companion_id = $1
                  AND external_user_id ILIKE $2
                ORDER BY last_interaction_at DESC NULLS LAST, created_at DESC
                LIMIT $3 OFFSET $4
                """,
                companion_id,
                like,
                limit,
                offset,
            )
        else:
            count_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt
                FROM relationships
                WHERE companion_id = $1
                """,
                companion_id,
            )
            rows = await conn.fetch(
                """
                SELECT id, external_user_id, message_count, last_interaction_at, created_at, profile
                FROM relationships
                WHERE companion_id = $1
                ORDER BY last_interaction_at DESC NULLS LAST, created_at DESC
                LIMIT $2 OFFSET $3
                """,
                companion_id,
                limit,
                offset,
            )

        total = count_row["cnt"] if count_row else 0
        items = [
            DashboardRelationshipSummary(
                id=str(row["id"]),
                user_id=row["external_user_id"],
                message_count=row["message_count"],
                last_interaction_at=row["last_interaction_at"].isoformat()
                if row["last_interaction_at"]
                else None,
                created_at=row["created_at"].isoformat(),
                profile=row["profile"] if isinstance(row["profile"], dict) else {},
            )
            for row in rows
        ]

        return DashboardRelationshipListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list relationships for companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list relationships",
        )


@router.delete("/companions/{companion_id}/relationships/{relationship_id}")
async def delete_dashboard_relationship(
    companion_id: UUID,
    relationship_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete a relationship from dashboard management UI (Clerk-auth)."""
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
        if not relationship or relationship.companion_id != companion_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Relationship not found",
            )

        deleted = await RelationshipRepository.delete(conn, relationship_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete relationship",
            )

        return {"message": "Relationship deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to delete relationship {relationship_id} for companion {companion_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete relationship",
        )


@router.get("/companions/{companion_id}/test-users", response_model=List[TestUserSummary])
async def list_test_users(
    companion_id: UUID,
    prefix: str = Query(default="builder-", description="Filter by user_id prefix"),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """List test relationships for the dashboard simulator.

    Returns relationships where user_id starts with the given prefix (default: builder-).
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        # List relationships and filter by prefix
        relationships, _ = await RelationshipRepository.list_for_companion(
            conn, companion_id, limit=limit, offset=0
        )

        return [
            TestUserSummary(
                id=str(r.id),
                user_id=r.user_id,
                message_count=r.message_count,
                last_interaction_at=r.last_interaction_at.isoformat()
                if r.last_interaction_at
                else None,
                profile_preview={"name": r.profile.get("name")} if r.profile.get("name") else None,
            )
            for r in relationships
            if r.user_id.startswith(prefix)
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list test users for companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list test users"
        )


@router.put("/companions/{companion_id}/test-users/{user_id}", response_model=TestUserDetail)
async def ensure_test_user(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Ensure a test relationship exists (idempotent).

    Creates the relationship if it doesn't exist, returns existing if it does.
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        # Ensure relationship exists
        relationship, _ = await RelationshipRepository.ensure_exists(
            conn, companion_id=companion_id, user_id=user_id
        )

        return TestUserDetail(
            id=str(relationship.id),
            user_id=relationship.user_id,
            message_count=relationship.message_count,
            last_interaction_at=relationship.last_interaction_at.isoformat()
            if relationship.last_interaction_at
            else None,
            profile=relationship.profile or {},
            config=relationship.config or {},
            created_at=relationship.created_at.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to ensure test user {user_id} for companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to ensure test user"
        )


@router.get("/companions/{companion_id}/test-users/{user_id}", response_model=TestUserDetail)
async def get_test_user(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get details of a test relationship."""
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        relationship = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if not relationship:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test user not found")

        return TestUserDetail(
            id=str(relationship.id),
            user_id=relationship.user_id,
            message_count=relationship.message_count,
            last_interaction_at=relationship.last_interaction_at.isoformat()
            if relationship.last_interaction_at
            else None,
            profile=relationship.profile or {},
            config=relationship.config or {},
            created_at=relationship.created_at.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get test user {user_id} for companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get test user"
        )


@router.patch(
    "/companions/{companion_id}/test-users/{user_id}/config",
    response_model=TestUserConfigResponse,
)
async def patch_test_user_config(
    companion_id: UUID,
    user_id: str,
    body: dict[str, object],
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Patch dashboard test-user relationship config (JSON Merge Patch)."""
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        # Ensure relationship exists for dashboard convenience
        relationship, _ = await RelationshipRepository.ensure_exists(
            conn, companion_id=companion_id, user_id=user_id
        )

        updated = await RelationshipRepository.patch_config(conn, relationship.id, body)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update test user config",
            )

        return TestUserConfigResponse(config=updated.config or {}, version=updated.version)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to patch config for test user {user_id} / companion {companion_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update test user config",
        )


@router.delete("/companions/{companion_id}/test-users/{user_id}")
async def delete_test_user(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete a test relationship (reset).

    This removes all messages, profile, and state for the relationship.
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        relationship = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if not relationship:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test user not found")

        deleted = await RelationshipRepository.delete(conn, relationship.id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete test user",
            )

        return {"message": "Test user deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete test user {user_id} for companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete test user"
        )


@router.post("/companions/{companion_id}/test-users/{user_id}/reset-conversation")
async def reset_test_user_conversation(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Reset conversation runtime state for a test relationship.

    Preserves relationship identity/config and clears:
    - messages/sessions/async operations
    - memories/context cache/summaries
    - relationship profile
    - counters/timestamps derived from message history
    - intro-message sent marker (so intro can trigger on next empty reconnect)
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        relationship = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if not relationship:
            return {"message": "Nothing to reset"}

        async with conn.transaction():
            await _reset_relationship_runtime_state(
                conn,
                relationship_id=relationship.id,
                companion_id=companion_id,
                user_id=user_id,
                clear_profile=True,
            )

        logger.info("Reset conversation for relationship %s", relationship.id)
        return {"message": "Conversation reset successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to reset conversation for test user %s / companion %s: %s",
            user_id,
            companion_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset conversation",
        )


@router.post("/companions/{companion_id}/test-users/{user_id}/reset-profile")
async def reset_test_user_profile(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Reset a test user's profile and conversation state.

    Useful for onboarding flows where you want a completely fresh start.
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        relationship = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if not relationship:
            return {"message": "Nothing to reset"}

        # Clear profile and all conversation/runtime artifacts.
        async with conn.transaction():
            await _reset_relationship_runtime_state(
                conn,
                relationship_id=relationship.id,
                companion_id=companion_id,
                user_id=user_id,
                clear_profile=True,
            )
        logger.info("Reset profile and messages for relationship %s", relationship.id)

        return {"message": "Reset successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reset for test user {user_id} / companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset"
        )


class TestUserMessage(BaseModel):
    """Message in a test relationship."""

    id: str
    role: str
    content: str
    created_at: str


class TwilioCallTranscriptMessage(BaseModel):
    """Message in a Twilio call transcript."""

    id: str
    role: str
    content: str
    created_at: str
    call_sid: str | None = None
    call_id: str | None = None
    call_mode: str | None = None


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/messages",
    response_model=List[TestUserMessage],
)
async def get_test_user_messages(
    companion_id: UUID,
    user_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get message history for a test relationship.

    Returns recent messages in chronological order (oldest first).
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        # Get relationship
        relationship = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if not relationship:
            return []  # No relationship = no messages

        # Get message history
        messages = await RelationshipRepository.get_message_history(
            conn, relationship_id=relationship.id, limit=limit
        )

        return [
            TestUserMessage(
                id=str(msg.get("id", "")),
                role=msg["role"],
                content=msg["content"],
                created_at=msg["created_at"].isoformat() if msg.get("created_at") else "",
            )
            for msg in messages
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get messages for test user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get messages"
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/twilio-calls/{call_sid}/messages",
    response_model=List[TwilioCallTranscriptMessage],
)
async def get_twilio_call_transcript(
    companion_id: UUID,
    user_id: str,
    call_sid: str,
    limit: int = Query(default=500, ge=1, le=2000),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get transcript messages for a specific Twilio call (oldest first)."""
    import re

    if not re.match(r"^CA[0-9a-fA-F]{32}$", call_sid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Twilio Call SID format",
        )

    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        relationship = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if not relationship:
            return []

        rows = await conn.fetch(
            """
            SELECT id::text AS id, role, content, created_at, metadata
            FROM messages
            WHERE relationship_id = $1
              AND input_modality = 'voice'
              AND metadata->>'channel' = 'twilio'
              AND metadata->>'call_sid' = $2
            ORDER BY created_at ASC
            LIMIT $3
            """,
            relationship.id,
            call_sid,
            limit,
        )

        return [
            TwilioCallTranscriptMessage(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"].isoformat() if row.get("created_at") else "",
                call_sid=(row.get("metadata") or {}).get("call_sid"),
                call_id=(row.get("metadata") or {}).get("call_id"),
                call_mode=(row.get("metadata") or {}).get("call_mode"),
            )
            for row in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get Twilio transcript for user %s / companion %s / call %s: %s",
            user_id,
            companion_id,
            call_sid,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get Twilio call transcript",
        )


# Dashboard WebSocket token (uses Clerk auth, not API key auth)
from uuid import uuid4 as generate_uuid

from .v2.websockets import _create_ws_token


class DashboardWsTokenResponse(BaseModel):
    """WebSocket token response for dashboard."""

    token: str
    relationship_id: str
    expires_in: int
    ws_url: str


@router.post(
    "/companions/{companion_id}/test-users/{user_id}/ws-token",
    response_model=DashboardWsTokenResponse,
)
async def create_dashboard_ws_token(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a WebSocket token for the dashboard simulator.

    Uses Clerk auth (not API key auth) for dashboard use.
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        # Ensure relationship exists
        relationship, _ = await RelationshipRepository.ensure_exists(
            conn, companion_id=companion_id, user_id=user_id
        )

        # Create token with a dashboard-specific marker as api_key_id
        # The websocket just stores this, doesn't validate it
        dashboard_marker_id = generate_uuid()
        token, expires_at = _create_ws_token(
            relationship_id=relationship.id,
            api_key_id=dashboard_marker_id,
        )

        return DashboardWsTokenResponse(
            token=token,
            relationship_id=str(relationship.id),
            expires_in=3600,
            ws_url=f"/v2/relationships/{relationship.id}/connect",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create ws token for {user_id} / companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create WebSocket token",
        )


# Dashboard Voice WebSocket token
from .voice.models import VoiceConfig
from .voice.v2 import _create_voice_token, create_default_voice_config, normalize_voice_config


class DashboardVoiceTokenRequest(BaseModel):
    """Request body for dashboard voice token."""

    voice_config: dict | None = None
    clear_profile: bool = Field(False, alias="clearProfile")


class DashboardVoiceTokenResponse(BaseModel):
    """Voice WebSocket token response for dashboard."""

    token: str
    relationship_id: str
    expires_in: int
    ws_url: str


@router.post(
    "/companions/{companion_id}/test-users/{user_id}/voice-token",
    response_model=DashboardVoiceTokenResponse,
)
async def create_dashboard_voice_token(
    companion_id: UUID,
    user_id: str,
    request: DashboardVoiceTokenRequest | None = None,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a voice WebSocket token for the dashboard simulator.

    Uses Clerk auth (not API key auth) for dashboard use.
    """
    try:
        # Verify ownership (allows onboarding companion for any user)
        await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        # Ensure relationship exists
        relationship, created = await RelationshipRepository.ensure_exists(
            conn, companion_id=companion_id, user_id=user_id
        )

        # Clear profile if requested (useful for onboarding flows)
        if request and request.clear_profile and not created:
            await conn.execute(
                "UPDATE relationships SET profile = '{}' WHERE id = $1",
                relationship.id,
            )
            logger.info(f"Cleared profile for relationship {relationship.id}")

        # Get voice config from request or use defaults
        if request and request.voice_config:
            voice_config = VoiceConfig(**request.voice_config)
        else:
            voice_config = create_default_voice_config()
        voice_config = normalize_voice_config(voice_config)

        # Create token with a dashboard-specific marker as api_key_id
        dashboard_marker_id = generate_uuid()
        token, expires_at = _create_voice_token(
            relationship_id=relationship.id,
            api_key_id=dashboard_marker_id,
            voice_config=voice_config,
        )

        return DashboardVoiceTokenResponse(
            token=token,
            relationship_id=str(relationship.id),
            expires_in=3600,
            ws_url=f"/v2/relationships/{relationship.id}/voice/connect",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create voice token for {user_id} / companion {companion_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create voice WebSocket token",
        )


# Dashboard Twilio Dial-Out
from .voice.twilio import (
    _call_auth_tokens,
    _get_public_base_url,
    _get_twilio_client_for,
    _pending_calls,
    _resolve_twilio_credentials,
    _schedule_twilio_call_event,
)
from .voice.twilio_models import TwilioDialOutResponse


class DashboardTwilioDialOutRequest(BaseModel):
    """Request body for dashboard Twilio dial-out."""

    to_number: str = Field(..., description="Phone number to call in E.164 format")


@router.post(
    "/companions/{companion_id}/test-users/{user_id}/twilio-dial-out",
    response_model=TwilioDialOutResponse,
)
async def dashboard_twilio_dial_out(
    companion_id: UUID,
    user_id: str,
    request: DashboardTwilioDialOutRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Initiate a Twilio outbound call from the dashboard.

    Uses Clerk auth (not API key auth) for dashboard use.
    """
    import re
    from datetime import UTC, datetime

    # Validate phone number format
    if not re.match(r"^\+[1-9]\d{1,14}$", request.to_number):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number must be in E.164 format (e.g., +14155551234)",
        )

    try:
        # Verify ownership
        companion = await get_companion_for_test_user_endpoint(conn, companion_id, user.id)

        creds = _resolve_twilio_credentials(companion)
        if not creds:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Twilio credentials not configured",
            )
        account_sid, auth_token, from_number = creds

        # Ensure relationship exists
        relationship, _ = await RelationshipRepository.ensure_exists(
            conn, companion_id=companion_id, user_id=user_id
        )

        # Generate unique call ID for TwiML callback
        call_id = str(generate_uuid())

        # Store call metadata for WebSocket handler
        _pending_calls[call_id] = {
            "companion_id": str(companion_id),
            "user_id": user_id,
            "relationship_id": str(relationship.id),
            "api_key_id": str(generate_uuid()),  # Dashboard marker
            "created_at": datetime.now(UTC).isoformat(),
        }

        # Build TwiML and status callback URLs
        base_url = _get_public_base_url()
        twiml_url = f"{base_url}/twilio/twiml/{call_id}"
        status_callback_url = f"{base_url}/twilio/status-callback"

        logger.info(
            f"[TWILIO_DASHBOARD] Initiating dial-out: to={request.to_number}, "
            f"companion={companion_id}, twiml_url={twiml_url}"
        )

        # Initiate the call
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
        _call_auth_tokens[call.sid] = auth_token
        _schedule_twilio_call_event(
            call_sid=call.sid,
            event_type="dial_out_initiated",
            status=call.status,
            call_id=call_id,
            companion_id=companion_id,
            relationship_id=relationship.id,
            user_id=user_id,
            direction="outbound",
            from_number=from_number,
            to_number=request.to_number,
            payload={"source": "dashboard_twilio_dial_out"},
        )

        logger.info(f"[TWILIO_DASHBOARD] Call initiated: sid={call.sid}, status={call.status}")

        return TwilioDialOutResponse(
            call_sid=call.sid,
            status=call.status,
            call_id=call_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        # Clean up pending call on failure
        _pending_calls.pop(call_id, None)
        logger.exception(f"[TWILIO_DASHBOARD] Failed to initiate call: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate call: {e!s}",
        )
