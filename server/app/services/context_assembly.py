from __future__ import annotations

from typing import Dict, List, Tuple
from uuid import UUID

import asyncpg

from ..repositories.companion import CompanionRepository
from ..repositories.memory import MemoryRepository
from ..repositories.memory_v2_repository import MemoryV2Repository
from .cache_manager import cache, ttl_from_env
from .memory_runtime import (
    format_memory_block,
    retrieve_regular_memories,
    should_retrieve_memories,
)
from .memory_v2_service import MEMORY_V2_GUIDANCE, MemoryV2Service

CORE_GUIDANCE = (
    "You have access to the companion's core memories. Use them as subtle background knowledge to personalize responses.\n"
    "Do not restate, quote, or force-inject core memories. Only apply them when clearly relevant to the user's intent.\n"
    "Avoid fabricating details and prioritize the user's current request over generic facts."
)


# TTL for effective prompt cache (seconds)
_PROMPT_TTL_S = ttl_from_env("MEMORY_PROMPT_CACHE_TTL_S", 30.0)


async def build_effective_system_prompt(
    conn: asyncpg.Connection, *, companion_id: UUID, use_cache: bool = True
) -> Tuple[str, str]:
    """
    Compose the effective system prompt used at runtime.
    Returns (effective_prompt, builder_prompt).
    - builder_prompt is the raw prompt from the latest config JSON.
    - effective_prompt prepends a '# CORE MEMORIES' section when memory is enabled.

    Args:
        conn: Database connection
        companion_id: The companion UUID
        use_cache: If False, bypass cache and fetch fresh from DB (useful for new sessions)
    """
    # Fast path: return cached prompt if present and caching enabled
    if use_cache:
        cached = cache.get("eff_prompt", str(companion_id))
        if cached is not None:
            eff, builder = cached  # type: ignore[assignment]
            return eff, builder

    comp = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not comp:
        raise ValueError("Companion not found")

    builder_prompt = comp.config.system_prompt.get_effective_prompt() or ""
    mem_cfg = comp.config.memory
    if not mem_cfg or not getattr(mem_cfg, "enabled", False):
        return builder_prompt or "You are a helpful and friendly companion.", builder_prompt

    # Fetch core memories from DB (authoritative list)
    core_rows = await MemoryRepository.list_core_memories(
        conn, companion_id=companion_id, limit=100
    )
    core_texts = [r.get("content") for r in core_rows if (r.get("content") or "").strip()]

    if not core_texts:
        eff = builder_prompt or "You are a helpful and friendly companion."
        cache.set("eff_prompt", str(companion_id), (eff, builder_prompt), _PROMPT_TTL_S)
        return eff, builder_prompt

    # Compose effective prompt
    lines = ["# CORE MEMORIES", CORE_GUIDANCE]
    for t in core_texts:
        lines.append(f"- {t.strip()}")
    lines.append("")
    if builder_prompt:
        lines.append(builder_prompt)

    effective = "\n".join(lines).strip()
    cache.set("eff_prompt", str(companion_id), (effective, builder_prompt), _PROMPT_TTL_S)
    return effective, builder_prompt


async def build_effective_system_prompt_for_config(
    conn: asyncpg.Connection, *, companion_id: UUID, builder_prompt: str
) -> str:
    """Compose an effective prompt from a provided builder_prompt and DB core memories."""
    if not (builder_prompt or "").strip():
        builder_prompt = "You are a helpful and friendly companion."
    core_rows = await MemoryRepository.list_core_memories(
        conn, companion_id=companion_id, limit=100
    )
    core_texts = [r.get("content") for r in core_rows if (r.get("content") or "").strip()]
    if not core_texts:
        return builder_prompt
    lines = ["# CORE MEMORIES", CORE_GUIDANCE]
    for t in core_texts:
        lines.append(f"- {t.strip()}")
    lines.append("")
    lines.append(builder_prompt)
    return "\n".join(lines).strip()


async def build_transient_memory_block(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    user_text: str | None,
    external_user_id: str | None,
    conversation_id: UUID | None,
    memory_enabled: bool = True,
    timings: dict | None = None,
    recent_messages: List[Dict] | None = None,
    last_n: int = 6,
) -> str:
    """
    Compute the per-turn transient memory block for personalization.
    This applies identical heuristics for both text and voice routes.

    Returns a formatted system message block or an empty string if none.
    """
    try:
        import os as _os

        if not memory_enabled:
            return ""
        if _os.getenv("MEMORY_RETRIEVAL_ENABLED", "true").lower() not in ("1", "true", "yes", "on"):
            return ""
        if not (user_text or "").strip():
            return ""
        import time as _t

        t0 = _t.perf_counter()
        # Gate based on configured strategy; include short context window if available
        gate = await should_retrieve_memories(
            user_text, recent_messages=(recent_messages[-last_n:] if recent_messages else None)
        )
        if timings is not None:
            timings["heuristic_ms"] = (_t.perf_counter() - t0) * 1000
        if not gate:
            return ""
        t1 = _t.perf_counter()
        import asyncio as _aio

        try:
            timeout_ms = float(_os.getenv("MEMORY_RETRIEVAL_TIMEOUT_MS", "2500"))
        except Exception:
            timeout_ms = 2500.0
        try:
            items = await _aio.wait_for(
                retrieve_regular_memories(
                    conn,
                    companion_id=companion_id,
                    query_text=user_text,
                    external_user_id=external_user_id,
                    conversation_id=conversation_id,
                    timings=timings,
                ),
                timeout=timeout_ms / 1000.0,
            )
        except Exception:
            if timings is not None:
                timings["retrieval_timeout_ms"] = (_t.perf_counter() - t1) * 1000
            items = []
        block = format_memory_block(items) or ""
        if timings is not None:
            timings["retrieval_ms"] = (_t.perf_counter() - t1) * 1000
            # If underlying service filled detailed timings via global state in future, add; otherwise, count items
            timings["retrieval_items"] = len(items)
        return block
    except Exception:
        # Non-fatal; simply return empty when retrieval fails
        return ""


# Cache TTL for memory V2 entries in legacy mode (seconds)
_MEMORY_V2_TTL_S = ttl_from_env("MEMORY_V2_CACHE_TTL_S", 30.0)


async def build_transient_memory_v2_block(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    external_user_id: str | None,
    max_entries: int = 100,
    timings: dict | None = None,
) -> str:
    """
    Build the Memory V2 scratchpad block for legacy context mode.

    Unlike V1 which does vector search, V2 loads the full scratchpad
    and injects it as a formatted list. No gating needed since the
    scratchpad is already curated by the ingestion process.

    Args:
        conn: Database connection
        companion_id: The companion UUID
        external_user_id: The external user ID (to look up relationship)
        max_entries: Maximum entries to include (default 100)
        timings: Optional dict to record timing metrics

    Returns:
        Formatted memory block or empty string if no entries.
    """
    import time as _t

    t0 = _t.perf_counter()

    # Need external_user_id to look up relationship
    if not external_user_id:
        if timings is not None:
            timings["memory_v2_skip"] = "no_external_user_id"
            timings["memory_v2_ms"] = (_t.perf_counter() - t0) * 1000
        return ""

    try:
        # Look up or create relationship
        from ..repositories.relationship_repository import RelationshipRepository

        relationship, _ = await RelationshipRepository.ensure_exists(
            conn, companion_id=companion_id, user_id=external_user_id
        )
        relationship_id = relationship.id

        # Check cache first (cache stores just the formatted entries, not the full block)
        cache_key = f"memory_v2_legacy:{relationship_id}"
        cached_block = cache.get("memory_v2", cache_key)
        if cached_block is not None:
            if timings is not None:
                timings["memory_v2_cache_hit"] = True
                timings["memory_v2_ms"] = (_t.perf_counter() - t0) * 1000
            return f"# MEMORY\n\n{MEMORY_V2_GUIDANCE}\n\n{cached_block}"

        entries = await MemoryV2Repository.list_entries(conn, relationship_id, limit=max_entries)

        if timings is not None:
            timings["memory_v2_entries"] = len(entries)
            timings["memory_v2_ms"] = (_t.perf_counter() - t0) * 1000

        if not entries:
            # Still inject guidance so companion knows it has memory capability
            return f"# MEMORY\n\n{MEMORY_V2_GUIDANCE}\n\nStored memories: None yet."

        block = MemoryV2Service.format_for_prompt(entries)

        # Cache result (only cache non-empty blocks)
        cache.set("memory_v2", cache_key, block, _MEMORY_V2_TTL_S)

        return f"# MEMORY\n\n{MEMORY_V2_GUIDANCE}\n\n{block}"

    except Exception:
        # Non-fatal; return empty when retrieval fails
        if timings is not None:
            timings["memory_v2_error"] = True
            timings["memory_v2_ms"] = (_t.perf_counter() - t0) * 1000
        return ""
