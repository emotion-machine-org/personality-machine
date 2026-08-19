import json
import logging
import os
import time
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..auth import get_current_user
from ..db import get_db
from ..models.memory import (
    MemoryCreate,
    MemoryItem,
    MemoryListResponse,
    MemoryQuery,
    MemorySearchResponse,
    MemoryStats,
    MemoryUpdate,
)
from ..models.user import User
from ..repositories.companion import CompanionRepository
from ..repositories.memory import MemoryRepository
from ..services.memory_service import MemoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


async def _assert_companion_ownership(
    conn: asyncpg.Connection, *, companion_id: UUID, user_id: UUID
):
    # Optional explicit bypass flag (controlled benchmarks)
    if os.getenv("MEMORY_BENCHMARK_BYPASS_OWNERSHIP", "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    # Single fast EXISTS query (no extra joins/logging)
    ok = await CompanionRepository.exists_companion_owned(conn, companion_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Companion not found")
    return True


@router.get(
    "/companions/{companion_id}/memories",
    response_model=MemoryListResponse,
)
async def list_memories(
    companion_id: UUID,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conversation_id: UUID | None = None,
    external_user_id: str | None = Query(None),
    order_by: str | None = Query("created_at"),
    order_dir: str | None = Query("DESC"),
    is_core: bool | None = Query(None, description="Filter by core memory status"),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    await _assert_companion_ownership(conn, companion_id=companion_id, user_id=user.id)
    try:
        rows, total_count = await MemoryRepository.list_memories(
            conn,
            companion_id=companion_id,
            limit=limit,
            offset=offset,
            conversation_id=conversation_id,
            external_user_id=external_user_id,
            order_by=order_by or "created_at",
            order_dir=(order_dir or "DESC").upper(),
            is_core=is_core,
        )
        return MemoryListResponse(
            items=[MemoryItem(**r) for r in rows],
            total_count=total_count,
        )
    except Exception as e:
        logger.error(f"Failed to list memories: {e}")
        raise HTTPException(status_code=500, detail="Failed to list memories")


@router.post(
    "/companions/{companion_id}/memories",
    status_code=201,
)
async def create_memory(
    companion_id: UUID,
    payload: MemoryCreate,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    await _assert_companion_ownership(conn, companion_id=companion_id, user_id=user.id)
    try:
        cfg = await MemoryService.get_companion_memory_config(conn, companion_id=companion_id)
        mem_id = await MemoryService.create(
            conn,
            companion_id=companion_id,
            payload=payload,
            importance_guidance=cfg.get("evaluation_prompt", ""),
        )
        return {"id": str(mem_id)}
    except ValueError as e:
        logger.warning(f"Bad request creating memory: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create memory: {e}")
        raise HTTPException(status_code=500, detail="Failed to create memory")


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: UUID,
    updates: MemoryUpdate,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    # Verify the memory belongs to a companion owned by the user
    # We fetch the companion_id via a join to avoid leaking existence
    row = await conn.fetchrow(
        "SELECT m.companion_id FROM memories m JOIN companions c ON m.companion_id = c.id WHERE m.id = $1 AND c.owner_id = $2",
        memory_id,
        user.id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Memory not found")
    row["companion_id"]
    try:
        # Dispatch to modal worker for non-blocking update (recomputes embedding if content provided)
        from ..services.modal_gateway import dispatch_memory_update_job

        item = {"memory_id": str(memory_id)}
        if updates.importance is not None:
            item["importance"] = float(updates.importance)
        if updates.commentary is not None:
            item["commentary"] = updates.commentary
        if updates.content is not None:
            item["content"] = updates.content
        if len(item) == 1:
            raise HTTPException(status_code=400, detail="No fields to update")
        await dispatch_memory_update_job(items=[item])
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update memory {memory_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update memory")


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    row = await conn.fetchrow(
        "SELECT m.companion_id FROM memories m JOIN companions c ON m.companion_id = c.id WHERE m.id = $1 AND c.owner_id = $2",
        memory_id,
        user.id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Memory not found")
    try:
        ok = await MemoryRepository.delete_memory(
            conn, memory_id=memory_id, companion_id=row["companion_id"]
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete memory {memory_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete memory")


@router.post(
    "/companions/{companion_id}/memories/search",
    response_model=MemorySearchResponse,
)
async def search_memories(
    companion_id: UUID,
    body: MemoryQuery,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
    response: Response = None,
    request: Request = None,
):
    t_owner = time.perf_counter()
    await _assert_companion_ownership(conn, companion_id=companion_id, user_id=user.id)
    owner_ms = (time.perf_counter() - t_owner) * 1000
    try:
        t0 = time.perf_counter()
        cfg = await MemoryService.get_companion_memory_config(conn, companion_id=companion_id)
        cfg_ms = (time.perf_counter() - t0) * 1000
        timings: dict[str, float] = {}
        rows = await MemoryService.search(
            conn,
            companion_id=companion_id,
            query=body,
            lambda_recency=cfg.get("recency"),
            top_k_default=int(cfg.get("top_k", 50)),
            min_saliency_default=float(cfg.get("min_saliency", 0.2)),
            external_user_id=body.external_user_id,
            is_core=False,
            timings=timings,
        )
        t1 = time.perf_counter()
        items = [MemoryItem(**r) for r in rows]
        items_model_ms = (time.perf_counter() - t1) * 1000

        # Attach debug timing header for benchmarks
        dbg = {
            "cfg_ms": cfg_ms,
            "auth_ms": getattr(getattr(request, "state", object()), "em_auth_ms", 0.0)
            if request
            else 0.0,
            "owner_ms": owner_ms,
            "retrieval_embed_ms": timings.get("retrieval_embed_ms", 0.0),
            "retrieval_db_ms": timings.get("retrieval_db_ms", 0.0),
            "db_knn_ms": timings.get("db_knn_ms", 0.0),
            "db_rerank_ms": timings.get("db_rerank_ms", 0.0),
            "items_model_ms": items_model_ms,
        }
        if response is not None:
            try:
                response.headers["X-EM-Debug-Timing"] = json.dumps(dbg)
            except Exception:
                pass
        return MemorySearchResponse(items=items)
    except Exception as e:
        logger.error(f"Failed to search memories: {e}")
        raise HTTPException(status_code=500, detail="Failed to search memories")


@router.get(
    "/companions/{companion_id}/memories/stats",
    response_model=MemoryStats,
)
async def get_memory_stats(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    await _assert_companion_ownership(conn, companion_id=companion_id, user_id=user.id)
    try:
        raw = await MemoryRepository.get_stats(conn, companion_id=companion_id)
        return MemoryStats(**raw)
    except Exception as e:
        logger.error(f"Failed to get memory stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get memory stats")
