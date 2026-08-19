"""Builder-facing API for managing companion share settings."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..db import get_db
from ..models.share import CompanionShare, CompanionShareAnalytics, ShareStatus
from ..models.user import User
from ..repositories.companion import CompanionRepository
from ..repositories.share import (
    CompanionShareRepository,
    CompanionShareSessionRepository,
)
from ..services.voice_presets import build_voice_pipeline_from_config

CORE_SNAPSHOT_KEYS = ("system_prompt", "memory_enabled", "llm_provider", "temperature")

router = APIRouter(prefix="/api/companions", tags=["companion-shares"])

DEFAULT_SHARE_CONTEXT_DESCRIPTION = (
    "Have a casual conversation with the companion and see how it makes you feel."
)


class CompanionShareOut(BaseModel):
    id: UUID
    companion_id: UUID
    slug: str
    status: ShareStatus
    allow_text: bool
    allow_voice: bool
    require_auth: bool
    expose_status_events: bool
    display_name: str | None = None
    description: str | None = None
    version_id: UUID | None = None
    config_snapshot: Dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None = None
    disabled_at: datetime | None = None
    total_sessions: int
    total_messages: int
    total_voice_sessions: int
    last_activity_at: datetime | None = None
    has_pending_changes: bool = False


class ShareSettingsRequest(BaseModel):
    status: ShareStatus | None = Field(default=None)
    allow_text: bool | None = None
    allow_voice: bool | None = None
    require_auth: bool | None = None
    expose_status_events: bool | None = None
    display_name: str | None = None
    description: str | None = None
    version_id: UUID | None = None
    config_snapshot: Dict[str, Any] | None = None


class ShareAnalyticsResponse(BaseModel):
    share_id: UUID
    sessions: int
    total_messages: int
    total_voice_sessions: int
    last_activity_at: datetime | None


def _to_share_out(share: CompanionShare, *, has_pending_changes: bool = False) -> CompanionShareOut:
    return CompanionShareOut(
        id=share.id,
        companion_id=share.companion_id,
        slug=share.slug,
        status=share.status,
        allow_text=share.allow_text,
        allow_voice=share.allow_voice,
        require_auth=share.require_auth,
        expose_status_events=share.expose_status_events,
        display_name=share.display_name,
        description=share.description,
        version_id=share.version_id,
        config_snapshot=share.config_snapshot,
        created_at=share.created_at,
        updated_at=share.updated_at,
        activated_at=share.activated_at,
        disabled_at=share.disabled_at,
        total_sessions=share.total_sessions,
        total_messages=share.total_messages,
        total_voice_sessions=share.total_voice_sessions,
        last_activity_at=share.last_activity_at,
        has_pending_changes=has_pending_changes,
    )


async def _get_owned_companion(
    conn: asyncpg.Connection,
    companion_id: UUID,
    owner_id: UUID,
) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, owner_id, name, description FROM companions WHERE id = $1",
        companion_id,
    )
    if not row or row["owner_id"] != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
    return row


def _generate_slug() -> str:
    return uuid4().hex


async def _ensure_share(
    conn: asyncpg.Connection,
    companion: asyncpg.Record,
    owner_id: UUID,
) -> CompanionShare:
    existing = await CompanionShareRepository.get_for_companion(conn, companion_id=companion["id"])
    if existing:
        return existing
    slug = _generate_slug()
    display_name = companion.get("name") if hasattr(companion, "get") else companion["name"]
    description = (
        companion.get("description") if hasattr(companion, "get") else companion["description"]
    )
    if not description or not str(description).strip():
        description = DEFAULT_SHARE_CONTEXT_DESCRIPTION
    return await CompanionShareRepository.create(
        conn,
        companion_id=companion["id"],
        owner_id=owner_id,
        version_id=None,
        slug=slug,
        status=ShareStatus.DRAFT,
        allow_text=True,
        allow_voice=False,
        require_auth=False,
        expose_status_events=False,
        config_snapshot=None,
        display_name=display_name,
        description=description,
    )


async def _build_config_snapshot(
    conn: asyncpg.Connection, companion_id: UUID
) -> tuple[dict, UUID | None]:
    detail = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    config = getattr(detail, "config", None)
    system_prompt = ""
    temperature = 0.7
    memory_enabled = False
    popular_options: list[str] = []
    voice_names: list[str] = []
    if config:
        try:
            system_prompt = config.system_prompt.get_effective_prompt()
        except Exception:
            system_prompt = getattr(config, "system_prompt", "")
        try:
            temperature = float(config.voice.temperature)
        except Exception:
            pass
        try:
            memory_enabled = bool(getattr(config.memory, "enabled", False))
        except Exception:
            pass
        try:
            popular_options = list(getattr(config.voice, "popular_options", []) or [])
        except Exception:
            popular_options = []
        try:
            voice_names = list(getattr(config.voice, "voice", []) or [])
        except Exception:
            voice_names = []

    try:
        voice_pipeline, pipeline_llm_provider, pipeline_temperature = (
            build_voice_pipeline_from_config(
                popular_options,
                voice_names,
                temperature=temperature,
            )
        )
    except Exception:
        voice_pipeline = None
        pipeline_llm_provider = None
        pipeline_temperature = None

    snapshot = {
        "system_prompt": system_prompt,
        "temperature": temperature,
        "llm_provider": "openai-gpt4o-mini",
        "memory_enabled": memory_enabled,
    }
    if voice_pipeline:
        snapshot["voice_pipeline"] = voice_pipeline
    if pipeline_llm_provider:
        snapshot["llm_provider"] = pipeline_llm_provider
    if pipeline_temperature is not None:
        snapshot["temperature"] = pipeline_temperature

    version_id = getattr(getattr(detail, "current_version", None), "id", None)
    return snapshot, version_id


def _normalize_snapshot(snapshot: Dict[str, Any]) -> str:
    if not snapshot:
        return "{}"
    try:
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        # Fallback: coerce to string representation per field
        coerced = {k: (str(v) if isinstance(v, UUID) else v) for k, v in snapshot.items()}
        return json.dumps(coerced, sort_keys=True, separators=(",", ":"), default=str)


def _core_snapshot_view(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    if not snapshot:
        return {}
    return {k: snapshot.get(k) for k in CORE_SNAPSHOT_KEYS}


def _merge_publish_snapshot(
    base_snapshot: Dict[str, Any],
    provided_snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if provided_snapshot:
        # Copy to avoid mutating caller state
        merged.update(provided_snapshot)
    # Ensure canonical fields always match the authoritative companion config
    for key in CORE_SNAPSHOT_KEYS:
        merged[key] = base_snapshot.get(key)
    # Preserve voice pipeline or other extensions if supplied by the caller
    if "voice_pipeline" not in merged and "voice_pipeline" in base_snapshot:
        merged["voice_pipeline"] = base_snapshot["voice_pipeline"]
    return merged


async def _has_pending_changes(
    conn: asyncpg.Connection,
    share: CompanionShare,
) -> bool:
    if share.status != ShareStatus.ACTIVE:
        return False
    if not share.config_snapshot:
        return True
    try:
        current_snapshot, current_version_id = await _build_config_snapshot(
            conn, share.companion_id
        )
    except HTTPException:
        # If the companion is missing, treat as no pending changes to avoid masking 404s elsewhere
        return False

    if _normalize_snapshot(_core_snapshot_view(share.config_snapshot)) != _normalize_snapshot(
        _core_snapshot_view(current_snapshot)
    ):
        return True

    if current_version_id and share.version_id != current_version_id:
        return True
    return bool(current_version_id is None and share.version_id is not None)


@router.get("/{companion_id}/share", response_model=CompanionShareOut)
async def get_companion_share(
    companion_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        companion_uuid = UUID(companion_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid companion id")

    companion = await _get_owned_companion(conn, companion_uuid, user.id)
    share = await _ensure_share(conn, companion, user.id)
    pending = await _has_pending_changes(conn, share)
    return _to_share_out(share, has_pending_changes=pending)


@router.post("/{companion_id}/share", response_model=CompanionShareOut)
async def update_companion_share(
    companion_id: str,
    payload: ShareSettingsRequest,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        companion_uuid = UUID(companion_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid companion id")

    companion = await _get_owned_companion(conn, companion_uuid, user.id)
    share = await _ensure_share(conn, companion, user.id)

    body = payload.model_dump(exclude_unset=True)
    status_raw = body.get("status")
    status_value: ShareStatus | None
    if status_raw is None:
        status_value = None
    elif isinstance(status_raw, ShareStatus):
        status_value = status_raw
    else:
        status_value = ShareStatus(status_raw)

    kwargs: Dict[str, Any] = {}
    for field in (
        "allow_text",
        "allow_voice",
        "require_auth",
        "expose_status_events",
        "config_snapshot",
        "version_id",
        "display_name",
        "description",
    ):
        if field in body:
            kwargs[field] = body[field]

    set_activated = (
        status_value is not None
        and share.status != ShareStatus.ACTIVE
        and status_value == ShareStatus.ACTIVE
    )
    set_disabled = status_value == ShareStatus.DISABLED if status_value is not None else False

    if status_value == ShareStatus.ACTIVE:
        base_snapshot, version_id = await _build_config_snapshot(conn, companion_uuid)
        provided_snapshot = kwargs.get("config_snapshot") if "config_snapshot" in kwargs else None
        kwargs["config_snapshot"] = _merge_publish_snapshot(base_snapshot, provided_snapshot)
        if version_id is not None:
            kwargs["version_id"] = version_id

    updated = await CompanionShareRepository.update(
        conn,
        share.id,
        status=status_value,
        set_activated_at=set_activated,
        set_disabled_at=set_disabled,
        **kwargs,
    )
    pending = await _has_pending_changes(conn, updated)
    return _to_share_out(updated, has_pending_changes=pending)


@router.post("/{companion_id}/share/disable", response_model=CompanionShareOut)
async def disable_companion_share(
    companion_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        companion_uuid = UUID(companion_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid companion id")

    companion = await _get_owned_companion(conn, companion_uuid, user.id)
    share = await CompanionShareRepository.get_for_companion(conn, companion_id=companion["id"])
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

    updated = await CompanionShareRepository.disable(conn, share.id)
    pending = await _has_pending_changes(conn, updated)
    return _to_share_out(updated, has_pending_changes=pending)


@router.get("/{companion_id}/share/analytics", response_model=ShareAnalyticsResponse)
async def get_share_analytics(
    companion_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        companion_uuid = UUID(companion_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid companion id")

    companion = await _get_owned_companion(conn, companion_uuid, user.id)
    share = await CompanionShareRepository.get_for_companion(conn, companion_id=companion["id"])
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

    analytics: CompanionShareAnalytics = await CompanionShareSessionRepository.analytics_for_share(
        conn, share.id
    )
    return ShareAnalyticsResponse(**analytics.model_dump())
