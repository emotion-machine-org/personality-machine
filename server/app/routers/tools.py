from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List
from uuid import UUID, uuid4

import modal
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..db import get_db
from ..models.user import User
from ..repositories.tool_index_repository import ToolIndexRepository

logger = logging.getLogger(__name__)

DEFAULT_MODAL_ENV = "main"

router = APIRouter(prefix="/api/tools", tags=["api"])


class ToolIndexRequest(BaseModel):
    companion_id: UUID
    spec_name: str | None = Field(None, description="Optional display name for the uploaded spec")
    openapi_spec: Dict[str, Any]
    secrets_config: Dict[str, str] | None = Field(
        None,
        description="Map of HTTP header names to project secret names, e.g. {'Authorization': 'my_api_key'}",
    )


class ToolIndexResponse(BaseModel):
    spec_id: UUID
    dispatched: bool
    request_id: UUID


class ToolSpecItem(BaseModel):
    id: UUID
    spec_name: str | None = None
    secrets_config: Dict[str, str] | None = None
    created_at: str | None = None
    updated_at: str | None = None


async def _get_companion_project(conn, companion_id: UUID, user_id: UUID) -> UUID:
    """Get project_id for a companion, verifying ownership."""
    project_id = await ToolIndexRepository.get_companion_project(
        conn, companion_id=companion_id, user_id=user_id
    )
    if project_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found or inaccessible"
        )
    return project_id


async def _verify_spec_access(
    conn, *, spec_id: UUID, companion_id: UUID, user_id: UUID
) -> Dict[str, Any]:
    """Verify user has access to a tool spec."""
    spec_info = await ToolIndexRepository.verify_spec_access(
        conn, spec_id=spec_id, companion_id=companion_id, user_id=user_id
    )
    if spec_info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool spec not found")
    return spec_info


async def _dispatch_modal_index_job(
    *,
    project_id: UUID,
    companion_id: UUID,
    spec_id: UUID,
    spec_name: str | None,
    openapi_spec: Dict[str, Any],
    request_id: UUID,
) -> bool:
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
        if resp["status"] == "error":
            logger.warning("Modal index request failed: %s %s", resp["request_id"], resp["message"])
            return False
        return True
    except Exception as exc:
        logger.warning("Failed to execute Modal index endpoint: %s", exc)
        return False


@router.post("/index", response_model=ToolIndexResponse)
async def index_tool_spec(
    request: ToolIndexRequest,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
):
    """Store an OpenAPI spec and dispatch Modal indexing."""
    project_id = await _get_companion_project(conn, request.companion_id, user.id)
    spec_id = await ToolIndexRepository.create_spec(
        conn,
        project_id=project_id,
        companion_id=request.companion_id,
        spec_name=request.spec_name,
        json_content=request.openapi_spec,
        secrets_config=request.secrets_config,
    )

    request_id = uuid4()
    dispatched = await _dispatch_modal_index_job(
        project_id=project_id,
        companion_id=request.companion_id,
        spec_id=spec_id,
        spec_name=request.spec_name,
        openapi_spec=request.openapi_spec,
        request_id=request_id,
    )

    return ToolIndexResponse(spec_id=spec_id, dispatched=dispatched, request_id=request_id)


@router.get("", response_model=List[ToolSpecItem])
async def list_tool_specs(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
):
    """List tool specs for a companion."""
    await _get_companion_project(conn, companion_id, user.id)
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
                created_at=r.get("created_at").isoformat() if r.get("created_at") else None,
                updated_at=r.get("updated_at").isoformat() if r.get("updated_at") else None,
            )
        )
    return result


class UpdateSecretsConfigRequest(BaseModel):
    secrets_config: Dict[str, str] = Field(
        ..., description="Map of HTTP header names to project secret names"
    )


@router.patch("/{spec_id}/secrets-config")
async def update_secrets_config(
    spec_id: UUID,
    companion_id: UUID,
    request: UpdateSecretsConfigRequest,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
):
    """Update secrets_config for a tool spec without re-indexing."""
    await _verify_spec_access(conn, spec_id=spec_id, companion_id=companion_id, user_id=user.id)
    await ToolIndexRepository.update_secrets_config(
        conn, spec_id=spec_id, secrets_config=request.secrets_config
    )
    return {"status": "updated", "id": str(spec_id), "secrets_config": request.secrets_config}


@router.delete("/{spec_id}")
async def delete_tool_spec(
    spec_id: UUID,
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
):
    """Delete a tool spec (and its operations) for a companion."""
    await _verify_spec_access(conn, spec_id=spec_id, companion_id=companion_id, user_id=user.id)
    ok = await ToolIndexRepository.delete_spec(conn, spec_id=spec_id, companion_id=companion_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool spec not found")
    return {"status": "deleted", "id": str(spec_id)}
