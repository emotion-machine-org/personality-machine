# server/app/routers/voice/workspace_api.py
"""API endpoints for voice workspace - used by OpenClaw callbacks.

These endpoints allow the Slow Brain (OpenClaw) to read/write files
in the shared S3-backed workspace.
"""

from __future__ import annotations

import logging
import os
import secrets
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from .fast_brain_llm import get_active_session
from .voice_workspace import get_hot_context, get_workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice/workspace", tags=["voice-workspace"])

# Simple bearer token auth for OpenClaw callbacks
WORKSPACE_API_TOKEN = os.getenv("VOICE_WORKSPACE_API_TOKEN", "")
WORKSPACE_ALLOW_INSECURE = os.getenv("VOICE_WORKSPACE_ALLOW_INSECURE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _verify_token(authorization: str | None) -> None:
    """Verify bearer token."""
    if not WORKSPACE_API_TOKEN:
        if WORKSPACE_ALLOW_INSECURE:
            logger.warning(
                "[WORKSPACE_API] No VOICE_WORKSPACE_API_TOKEN configured and "
                "VOICE_WORKSPACE_ALLOW_INSECURE=true - allowing all requests"
            )
            return
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "VOICE_WORKSPACE_API_TOKEN is required",
        )

    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Authorization format")

    token = authorization[7:]
    if not secrets.compare_digest(token, WORKSPACE_API_TOKEN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid token")


# ─────────────────────────────────────────────────────────────────────────────
# File Operations
# ─────────────────────────────────────────────────────────────────────────────


class WriteFileRequest(BaseModel):
    content: str


class AppendFileRequest(BaseModel):
    content: str


class FileResponse(BaseModel):
    content: str | None = None
    exists: bool = True


@router.get("/{relationship_id}/files/{path:path}")
async def read_file(
    relationship_id: UUID,
    path: str,
    authorization: str | None = Header(None),
) -> FileResponse:
    """Read a file from the workspace."""
    _verify_token(authorization)

    try:
        workspace = get_workspace(relationship_id)
        content = workspace.read(path)
        if content is None:
            return FileResponse(content=None, exists=False)
        return FileResponse(content=content, exists=True)
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Read error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.put("/{relationship_id}/files/{path:path}")
async def write_file(
    relationship_id: UUID,
    path: str,
    request: WriteFileRequest,
    authorization: str | None = Header(None),
) -> dict:
    """Write/overwrite a file in the workspace."""
    _verify_token(authorization)

    try:
        workspace = get_workspace(relationship_id)
        workspace.write(path, request.content)
        return {"status": "ok", "path": path}
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Write error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.post("/{relationship_id}/files/{path:path}/append")
async def append_file(
    relationship_id: UUID,
    path: str,
    request: AppendFileRequest,
    authorization: str | None = Header(None),
) -> dict:
    """Append to a file in the workspace."""
    _verify_token(authorization)

    try:
        workspace = get_workspace(relationship_id)
        workspace.append(path, request.content)
        return {"status": "ok", "path": path}
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Append error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.delete("/{relationship_id}/files/{path:path}")
async def delete_file(
    relationship_id: UUID,
    path: str,
    authorization: str | None = Header(None),
) -> dict:
    """Delete a file from the workspace."""
    _verify_token(authorization)

    try:
        workspace = get_workspace(relationship_id)
        workspace.delete(path)
        return {"status": "ok", "path": path}
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Delete error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.get("/{relationship_id}/files")
async def list_files(
    relationship_id: UUID,
    prefix: str = "",
    authorization: str | None = Header(None),
) -> dict:
    """List files in the workspace."""
    _verify_token(authorization)

    try:
        workspace = get_workspace(relationship_id)
        files = workspace.list_files(prefix)
        return {"files": files}
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] List error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Task State Operations (Hot Context)
# ─────────────────────────────────────────────────────────────────────────────


class TaskUpdateRequest(BaseModel):
    result: str | None = None
    error: str | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str | None = None
    data: str | None = None
    found: bool = False


@router.post("/{relationship_id}/tasks/{task_id}/start")
async def task_start(
    relationship_id: UUID,
    task_id: str,
    request: TaskUpdateRequest,
    authorization: str | None = Header(None),
) -> dict:
    """Log task started."""
    _verify_token(authorization)

    try:
        ctx = get_hot_context(relationship_id)
        ctx.log_start(task_id, request.result or "")
        return {"status": "ok", "task_id": task_id, "action": "start"}
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Task start error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.post("/{relationship_id}/tasks/{task_id}/ack")
async def task_ack(
    relationship_id: UUID,
    task_id: str,
    request: TaskUpdateRequest,
    authorization: str | None = Header(None),
) -> dict:
    """Log task acknowledged."""
    _verify_token(authorization)

    try:
        ctx = get_hot_context(relationship_id)
        ctx.log_ack(task_id, request.result or "")
        return {"status": "ok", "task_id": task_id, "action": "ack"}
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Task ack error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.post("/{relationship_id}/tasks/{task_id}/done")
async def task_done(
    relationship_id: UUID,
    task_id: str,
    request: TaskUpdateRequest,
    authorization: str | None = Header(None),
) -> dict:
    """Log task completed with result."""
    _verify_token(authorization)

    if not request.result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "result is required")

    try:
        ctx = get_hot_context(relationship_id)
        ctx.log_done(task_id, request.result)
        logger.info(f"[WORKSPACE_API] Task {task_id} done for {relationship_id}")

        # Notify live Pipecat pipeline that hot_context has new data
        pushed = False
        session = get_active_session(str(relationship_id))
        if session:
            try:
                pushed = await session.notify_hot_context_updated()
                if pushed:
                    logger.info("[WORKSPACE_API] Notified live pipeline of hot_context update")
            except Exception as push_err:
                logger.warning(f"[WORKSPACE_API] Failed to notify pipeline: {push_err}")

        return {
            "status": "ok",
            "task_id": task_id,
            "action": "done",
            "pushed_to_pipeline": pushed,
        }
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Task done error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.post("/{relationship_id}/tasks/{task_id}/fail")
async def task_fail(
    relationship_id: UUID,
    task_id: str,
    request: TaskUpdateRequest,
    authorization: str | None = Header(None),
) -> dict:
    """Log task failed with error."""
    _verify_token(authorization)

    try:
        ctx = get_hot_context(relationship_id)
        ctx.log_fail(task_id, request.error or "Unknown error")
        logger.warning(f"[WORKSPACE_API] Task {task_id} failed for {relationship_id}")
        # Notify live Pipecat pipeline that hot_context has new data
        pushed = False
        session = get_active_session(str(relationship_id))
        if session:
            try:
                pushed = await session.notify_hot_context_updated()
                if pushed:
                    logger.info("[WORKSPACE_API] Notified live pipeline of failure update")
            except Exception as push_err:
                logger.warning(f"[WORKSPACE_API] Failed to notify pipeline: {push_err}")

        return {
            "status": "ok",
            "task_id": task_id,
            "action": "fail",
            "pushed_to_pipeline": pushed,
        }
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Task fail error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.get("/{relationship_id}/tasks/{task_id}")
async def task_status(
    relationship_id: UUID,
    task_id: str,
    authorization: str | None = Header(None),
) -> TaskStatusResponse:
    """Get task status."""
    _verify_token(authorization)

    try:
        ctx = get_hot_context(relationship_id)
        result = ctx.get_task_result(task_id)

        if result is None:
            return TaskStatusResponse(task_id=task_id, found=False)

        status, data = result
        return TaskStatusResponse(task_id=task_id, status=status, data=data, found=True)
    except Exception as e:
        logger.exception(f"[WORKSPACE_API] Task status error: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))
