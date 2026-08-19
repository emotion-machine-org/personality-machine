"""API v2 Relationships Router.

Implements relationship-centric endpoints for the v2 API.

Phase 6: State & Sensing Model
- Renamed /app-state -> /profile endpoints
- Removed /state endpoints (user_state/companion_state no longer used)
- Removed legacy /app-state endpoints
"""

from __future__ import annotations

import logging
from typing import Any, Dict
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...db import get_db
from ...models.v2.relationship import (
    ProfileResponse,
    Relationship,
    RelationshipConfigResponse,
    RelationshipCreate,
    RelationshipListResponse,
    RelationshipResponse,
    ResolvedConfigResponse,
)
from ...repositories.companion import CompanionRepository
from ...repositories.relationship_repository import RelationshipRepository

router = APIRouter(prefix="/v2", tags=["v2-relationships"])
logger = logging.getLogger(__name__)


def _relationship_to_response(rel: Relationship) -> RelationshipResponse:
    """Convert internal Relationship to API response."""
    return RelationshipResponse(
        id=rel.id,
        companion_id=rel.companion_id,
        user_id=rel.user_id,
        profile=rel.profile,
        config=rel.config,
        context_mode=rel.context_mode,
        context_mode_locked=rel.context_mode_locked,
        metadata=rel.metadata,
        last_interaction_at=rel.last_interaction_at,
        message_count=rel.message_count,
        version=rel.version,
        created_at=rel.created_at,
        updated_at=rel.updated_at,
    )


async def _verify_companion_access(
    conn: asyncpg.Connection,
    companion_id: UUID,
    project_id: UUID,
) -> None:
    """Verify the companion exists and belongs to the project."""
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Companion {companion_id} not found",
        )
    if companion.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Companion does not belong to this project",
        )


# -----------------------------------------------------------------------------
# Relationship CRUD
# -----------------------------------------------------------------------------


@router.put(
    "/companions/{companion_id}/relationships/{user_id}",
    response_model=RelationshipResponse,
    status_code=status.HTTP_200_OK,
    summary="Ensure relationship exists",
    description="Idempotently create or retrieve a relationship between a user and companion.",
)
async def ensure_relationship(
    companion_id: UUID,
    user_id: str,
    body: RelationshipCreate | None = None,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> RelationshipResponse:
    """Ensure a relationship exists (idempotent PUT)."""
    await _verify_companion_access(conn, companion_id, subject.project.id)

    relationship, created = await RelationshipRepository.ensure_exists(
        conn,
        companion_id=companion_id,
        user_id=user_id,
        profile=body.profile if body else None,
        config=body.config if body else None,
        context_mode=body.context_mode if body else None,
        metadata=body.metadata if body else None,
    )

    logger.info(
        "Relationship %s %s for companion=%s user=%s",
        relationship.id,
        "created" if created else "retrieved",
        companion_id,
        user_id,
    )

    return _relationship_to_response(relationship)


@router.get(
    "/companions/{companion_id}/relationships/{user_id}",
    response_model=RelationshipResponse,
    summary="Get relationship by user",
    description="Look up a relationship by companion and user ID.",
)
async def get_relationship_by_user(
    companion_id: UUID,
    user_id: str,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> RelationshipResponse:
    """Get relationship by companion and user ID."""
    await _verify_companion_access(conn, companion_id, subject.project.id)

    relationship = await RelationshipRepository.get_by_companion_and_user(
        conn, companion_id=companion_id, user_id=user_id
    )
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship not found for user {user_id}",
        )

    return _relationship_to_response(relationship)


@router.get(
    "/companions/{companion_id}/relationships",
    response_model=RelationshipListResponse,
    summary="List relationships",
    description="List all relationships for a companion with pagination.",
)
async def list_relationships(
    companion_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> RelationshipListResponse:
    """List relationships for a companion."""
    await _verify_companion_access(conn, companion_id, subject.project.id)

    relationships, total = await RelationshipRepository.list_for_companion(
        conn, companion_id, limit=limit, offset=offset
    )

    return RelationshipListResponse(
        items=[_relationship_to_response(r) for r in relationships],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(relationships)) < total,
    )


@router.get(
    "/relationships/{relationship_id}",
    response_model=RelationshipResponse,
    summary="Get relationship by ID",
    description="Get a relationship by its unique ID.",
)
async def get_relationship(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> RelationshipResponse:
    """Get relationship by ID."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    # Verify project access via companion
    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    return _relationship_to_response(relationship)


@router.delete(
    "/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete relationship",
    description="Delete a relationship and all associated data.",
)
async def delete_relationship(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    """Delete a relationship."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    # Verify project access via companion
    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    deleted = await RelationshipRepository.delete(conn, relationship_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete relationship",
        )

    logger.info("Deleted relationship %s", relationship_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -----------------------------------------------------------------------------
# Profile Endpoints (Phase 6: renamed from app-state)
# -----------------------------------------------------------------------------


@router.get(
    "/relationships/{relationship_id}/profile",
    response_model=ProfileResponse,
    summary="Get profile",
    description="Get the developer-controlled profile for a relationship.",
)
async def get_profile(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> ProfileResponse:
    """Get profile."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    result = await RelationshipRepository.get_profile(conn, relationship_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    profile, version = result
    return ProfileResponse(profile=profile, version=version)


@router.put(
    "/relationships/{relationship_id}/profile",
    response_model=ProfileResponse,
    summary="Set profile",
    description="Replace the entire profile for a relationship.",
)
async def set_profile(
    relationship_id: UUID,
    body: Dict[str, Any],
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> ProfileResponse:
    """Replace profile entirely."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    updated = await RelationshipRepository.set_profile(conn, relationship_id, body)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile",
        )

    return ProfileResponse(profile=updated.profile, version=updated.version)


@router.patch(
    "/relationships/{relationship_id}/profile",
    response_model=ProfileResponse,
    summary="Patch profile",
    description="Merge changes into profile using JSON Merge Patch (RFC 7396).",
)
async def patch_profile(
    relationship_id: UUID,
    body: Dict[str, Any],
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> ProfileResponse:
    """Merge changes into profile."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    updated = await RelationshipRepository.patch_profile(conn, relationship_id, body)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile",
        )

    return ProfileResponse(profile=updated.profile, version=updated.version)


@router.delete(
    "/relationships/{relationship_id}/profile",
    response_model=ProfileResponse,
    summary="Clear profile",
    description="Clear profile to an empty object.",
)
async def clear_profile(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> ProfileResponse:
    """Clear profile to empty object."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    updated = await RelationshipRepository.clear_profile(conn, relationship_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear profile",
        )

    return ProfileResponse(profile=updated.profile, version=updated.version)


# -----------------------------------------------------------------------------
# Config Endpoints
# -----------------------------------------------------------------------------


@router.get(
    "/relationships/{relationship_id}/config",
    response_model=RelationshipConfigResponse,
    summary="Get relationship config",
    description="Get the configuration overrides for a relationship.",
)
async def get_config(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> RelationshipConfigResponse:
    """Get relationship config."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    result = await RelationshipRepository.get_config(conn, relationship_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    config, version = result
    return RelationshipConfigResponse(config=config, version=version)


@router.get(
    "/relationships/{relationship_id}/config/resolved",
    response_model=ResolvedConfigResponse,
    summary="Get resolved config",
    description="Get the fully resolved config (companion + relationship merged).",
)
async def get_config_resolved(
    relationship_id: UUID,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> ResolvedConfigResponse:
    """Get the merged companion + relationship config."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    # Get companion config
    companion = await CompanionRepository.get_companion_by_id_no_auth(
        conn, relationship.companion_id
    )
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Companion {relationship.companion_id} not found",
        )

    # Get relationship config
    rel_result = await RelationshipRepository.get_config(conn, relationship_id)
    relationship_overrides = rel_result[0] if rel_result else {}

    # Serialize companion config
    companion_config = (
        companion.config.model_dump()
        if hasattr(companion.config, "model_dump")
        else dict(companion.config)
    )

    # Merge: relationship overrides take precedence over companion config
    resolved = {**companion_config, **relationship_overrides}

    return ResolvedConfigResponse(
        config=resolved,
        companion_config=companion_config,
        relationship_overrides=relationship_overrides,
    )


@router.patch(
    "/relationships/{relationship_id}/config",
    response_model=RelationshipConfigResponse,
    summary="Patch relationship config",
    description="Merge changes into relationship config using JSON Merge Patch (RFC 7396).",
)
async def patch_config(
    relationship_id: UUID,
    body: Dict[str, Any],
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> RelationshipConfigResponse:
    """Merge changes into relationship config."""
    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )

    await _verify_companion_access(conn, relationship.companion_id, subject.project.id)

    updated = await RelationshipRepository.patch_config(conn, relationship_id, body)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update config",
        )

    return RelationshipConfigResponse(config=updated.config, version=updated.version)
