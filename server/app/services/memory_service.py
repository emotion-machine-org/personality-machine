import logging
import os
import re
from typing import Any, Dict, Sequence, Tuple
from uuid import UUID

import asyncpg

from ..models.memory import MemoryCreate, MemoryQuery
from ..repositories.companion import CompanionRepository
from ..repositories.memory import MemoryRepository
from .cache_manager import cache, ttl_from_env
from .memory_prompts import build_importance_system_prompt

logger = logging.getLogger(__name__)


EMBEDDING_MODEL = os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
IMPORTANCE_LLM_MODEL = os.getenv("MEMORY_IMPORTANCE_MODEL", "gpt-4o-mini")

# TTLs via env (seconds)
_CFG_TTL_S = ttl_from_env("MEMORY_CFG_CACHE_TTL_S", 30.0)


def _parse_importance(text: str) -> Tuple[float, str | None]:
    """Parse 1-10 (allow '10') early; map to 0-1 via score/10. Commentary optional."""
    if not text:
        return 0.5, None
    head = text[:10].strip()
    score: int = 5
    try:
        # check 10 first to avoid matching the leading '1' of '10'
        if head.startswith("10") or re.search(r"\b10\b", head):
            score = 10
        else:
            m = re.search(r"[1-9]", head)
            if m:
                score = int(m.group(0))
    except Exception:
        score = 5
    importance = max(min(score / 10.0, 1.0), 0.0)

    commentary: str | None = None
    parts = text.splitlines()
    if len(parts) > 1:
        c = parts[1].strip()
        if c.lower().startswith("note:"):
            c = c[5:].strip()
        commentary = c or None
    return importance, commentary


def _heuristic_importance_floor(content: str) -> float:
    """Light heuristic to floor importance for clearly salient statements.

    This guards against occasional under-scoring from the LLM and is intentionally conservative.
    """
    c = (content or "").lower()
    floor = 0.0
    # Identity-level facts
    if any(k in c for k in ["my name is", "i'm called", "i am called", "birthday", "i was born"]):
        floor = max(floor, 0.85)
    # Stable preferences
    if any(k in c for k in ["my favorite", "i prefer", "i like", "i love", "i hate"]):
        floor = max(floor, 0.6)
    # Goals / commitments / deadlines
    if any(
        k in c
        for k in [
            "my goal",
            "my goals",
            "i plan",
            "i'm planning",
            "deadline",
            "remind me",
            "i need to",
            "i have to",
        ]
    ):
        floor = max(floor, 0.75)
    # Constraints / rules
    if any(
        k in c
        for k in ["i can't", "do not", "never", "always", "i must", "i should", "i shouldn't"]
    ):
        floor = max(floor, 0.65)
    return floor


async def _get_embedding(content: str) -> Sequence[float]:
    """Compute embedding for text content.

    Note: Embedding cache was removed due to near 0% hit rate - user queries vary
    too much per turn, making content hashing ineffective for caching.
    """
    from .openai_clients import get_openai_async_client

    client = get_openai_async_client()
    resp = await client.embeddings.create(model=EMBEDDING_MODEL, input=content)
    return resp.data[0].embedding  # type: ignore[attr-defined]


async def _evaluate_importance(content: str, guidance: str | None) -> Tuple[float, str | None]:
    from .openai_clients import get_openai_async_client

    system_prompt = build_importance_system_prompt(guidance)

    client = get_openai_async_client()
    try:
        resp = await client.chat.completions.create(
            model=IMPORTANCE_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=0,
            max_tokens=64,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"Importance LLM call failed, defaulting to 0.5: {e}")
        return 0.5, None
    try:
        return _parse_importance(raw)
    except Exception as e:
        logger.warning(f"Failed to parse importance output '{raw}': {e}")
        return 0.5, None


async def should_store_memory(
    content: str,
    importance_guidance: str | None = None,
    min_importance_write: float = 0.55,
    is_core: bool = False,
) -> Tuple[bool, float]:
    """Evaluate whether a memory should be stored based on importance scoring.

    Returns (should_store, importance_score).
    This is meant to be called synchronously before dispatching to Modal worker.
    """
    if not content or not content.strip():
        return False, 0.0

    # Score importance (LLM call)
    importance, _ = await _evaluate_importance(content, importance_guidance)

    # Apply heuristic floor
    importance = max(importance, _heuristic_importance_floor(content))

    # Decide whether to store
    should_store = is_core or importance >= min_importance_write

    return should_store, importance


class MemoryService:
    LAMBDA_RECENCY_DEFAULT = 0.995
    MAX_ROWS_PER_COMPANION = 10_000
    PRUNE_BATCH = 500

    @staticmethod
    async def create(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        payload: MemoryCreate,
        importance_guidance: str | None = None,
    ) -> str:
        """Enqueue memory creation via Modal worker (non-blocking DB).

        Returns a queued identifier string for client acknowledgement. Actual
        persistence is handled asynchronously by the memory_ingest worker.
        """
        # Resolve content when a message_id is provided (worker avoids DB reads)
        content: str | None = (payload.content or "").strip() if payload.content else None
        conversation_id = payload.conversation_id

        if payload.message_id:
            row = await conn.fetchrow(
                """
                SELECT m.content, m.conversation_id, c.external_user_id
                FROM messages m
                JOIN conversations c ON m.conversation_id = c.id
                WHERE m.id = $1
                """,
                payload.message_id,
            )
            if not row:
                raise ValueError("message_id not found")

            db_content = row["content"] or ""
            content = db_content
            if not conversation_id:
                conversation_id = row["conversation_id"]
            if not payload.external_user_id:
                payload.external_user_id = row["external_user_id"]

        if not content:
            raise ValueError(
                "content is required when message_id is not provided or message has empty content"
            )

        # Build worker payload — worker computes embedding and importance
        item: Dict[str, Any] = {
            "content": content,
            "message_id": str(payload.message_id) if payload.message_id else None,
            "sender_type": payload.sender_type or "user",
            "conversation_id": str(conversation_id) if conversation_id else None,
            "external_user_id": payload.external_user_id,
            "importance_guidance": importance_guidance or "",
            "weight_user": float(payload.weight_user or 1.0),
            "modality": payload.modality or "text",
            "is_core": bool(payload.is_core or False),
        }
        # Clean None values to keep payload minimal
        item = {k: v for k, v in item.items() if v is not None}

        from .modal_gateway import dispatch_memory_ingest_job

        await dispatch_memory_ingest_job(
            companion_id=companion_id,
            items=[item],
        )

        # If this is a core memory, clear cached core prompt so CorePromptLayer stays fresh
        if item.get("is_core"):
            try:
                from ..context.core_prompt_layer import (
                    bust_core_prompt_cache,  # local import to avoid circular
                )

                bust_core_prompt_cache(companion_id)
            except Exception:
                pass

        # Synthetic acknowledgement id; UI reconciles on refresh
        return f"queued-{int(__import__('time').time() * 1000):d}"

    @staticmethod
    async def search(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        query: MemoryQuery,
        lambda_recency: float | None,
        top_k_default: int,
        min_saliency_default: float,
        external_user_id: str | None = None,
        is_core: bool | None = None,
        timings: Dict[str, float] | None = None,
    ) -> list[Dict[str, Any]]:
        # Embedding for the query
        import time as _t

        t0 = _t.perf_counter()
        embedding = await _get_embedding(query.query)
        t1 = _t.perf_counter()
        if timings is not None:
            timings["retrieval_embed_ms"] = (t1 - t0) * 1000

        top_k = int(query.top_k or top_k_default)
        min_saliency = float(
            query.min_saliency if query.min_saliency is not None else min_saliency_default
        )
        lam = float(lambda_recency or MemoryService.LAMBDA_RECENCY_DEFAULT)

        t2 = _t.perf_counter()
        rows = await MemoryRepository.search_memories(
            conn,
            companion_id=companion_id,
            embedding=embedding,
            lambda_recency=lam,
            top_k=top_k,
            min_saliency=min_saliency,
            conversation_id=query.conversation_id,
            external_user_id=external_user_id,
            sender_type=query.sender_type,
            modality=query.modality,
            is_core=is_core,
            timings=timings,
        )
        if timings is not None:
            timings["retrieval_db_ms"] = (_t.perf_counter() - t2) * 1000
        return rows

    @staticmethod
    async def get_companion_memory_config(
        conn: asyncpg.Connection, *, companion_id: UUID
    ) -> Dict[str, Any]:
        cached = cache.get("mem_cfg", str(companion_id))
        if cached is not None:
            return cached  # type: ignore[return-value]

        comp = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
        if not comp:
            raise ValueError("Companion not found")
        cfg = comp.config.memory if comp and comp.config and comp.config.memory else None
        # Fallback to latest companion_versions.memory_evaluation_prompt if config empty
        eval_prompt = (getattr(cfg, "memory_evaluation_prompt", "") if cfg else "") or (
            await conn.fetchval(
                "SELECT memory_evaluation_prompt FROM companion_versions WHERE companion_id = $1 ORDER BY version_number DESC, created_at DESC LIMIT 1",
                companion_id,
            )
            or ""
        )
        # Validate min_saliency as [0,1]; fallback 0.2 if out of range (guards older configs)
        ms_val = getattr(cfg, "min_saliency", 0.2) if cfg else 0.2
        try:
            ms_val = float(ms_val)
        except Exception:
            ms_val = 0.2
        if ms_val < 0.0 or ms_val > 1.0:
            ms_val = 0.2

        # Allow env default override for top_k and recency if config missing
        try:
            env_top_k = int(os.getenv("MEMORY_TOP_K_DEFAULT", "15"))
        except Exception:
            env_top_k = 15
        try:
            env_lambda = float(
                os.getenv(
                    "MEMORY_LAMBDA_RECENCY_DEFAULT", str(MemoryService.LAMBDA_RECENCY_DEFAULT)
                )
            )
        except Exception:
            env_lambda = MemoryService.LAMBDA_RECENCY_DEFAULT

        result = {
            "recency": getattr(cfg, "recency", env_lambda) if cfg else env_lambda,
            "top_k": getattr(cfg, "top_k", env_top_k) if cfg else env_top_k,
            "min_saliency": ms_val,
            "evaluation_prompt": eval_prompt,
        }
        cache.set("mem_cfg", str(companion_id), result, _CFG_TTL_S)
        return result
