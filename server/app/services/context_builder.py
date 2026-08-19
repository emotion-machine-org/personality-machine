from __future__ import annotations

"""Shared helpers for building and managing LLM context across text and voice.

Back-compatible with current sessions.py aggregator logic:
- Does NOT assume any specific pipeline; returns plain system/user/assistant dicts.
- Provides best-effort in-process history cache for DB-backed text route.
- Provides a summary trigger that both text and voice can call.
"""

import os
import time
from typing import Dict, List
from uuid import UUID

import asyncpg

from ..repositories.conversation import get_conversation_messages
from .cache_manager import cache, ttl_from_env

# ── Small in‑process caches (per worker) ─────────────────────────────────────
_HISTORY_TTL_S = ttl_from_env("TEXT_HISTORY_CACHE_TTL_S", 30.0)

_SUMMARY_LAST_TRIGGER: Dict[UUID, float] = {}
_SUMMARY_MIN_INTERVAL_S = 300.0  # 5 minutes


async def get_full_history(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    *,
    use_cache: bool = True,
) -> List[Dict]:
    """Return full conversation history (id, role, content, created_at)."""
    if use_cache:
        cached = cache.get("history", str(conversation_id))
        if cached:
            return list(cached)  # type: ignore[return-value]
    rows = await get_conversation_messages(conn, conversation_id)
    if use_cache:
        cache.set("history", str(conversation_id), list(rows), _HISTORY_TTL_S)
    return rows


def update_history_cache_post_turn(
    conversation_id: UUID,
    *,
    user_message: Dict | None = None,
    assistant_message: Dict | None = None,
) -> None:
    """Append newly created user/assistant messages into the cache if present."""
    try:
        cur = cache.get("history", str(conversation_id)) or []
        cur = list(cur)
        if user_message is not None:
            if not cur or cur[-1].get("id") != user_message.get("id"):
                cur.append(user_message)
        if assistant_message is not None:
            cur.append(assistant_message)
        cache.set("history", str(conversation_id), cur, _HISTORY_TTL_S)
    except Exception:
        pass


def assemble_llm_messages(
    *,
    effective_system_prompt: str,
    memory_block: str | None,
    prior_messages: List[Dict],
) -> List[Dict]:
    """Build OpenAI‑compatible messages list for text route."""
    out: List[Dict] = []
    if effective_system_prompt:
        out.append({"role": "system", "content": effective_system_prompt})
    if memory_block:
        out.append({"role": "system", "content": memory_block})
    out.extend({"role": m["role"], "content": m["content"]} for m in prior_messages)
    return out


async def check_and_trigger_summary(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    conversation_id: UUID,
    messages: List[Dict] | None = None,
    total_chars_hint: int | None = None,
) -> None:
    """Schedule a background summary job if context exceeds the budget.

    - Does not block the request.
    - Safe to call from text or voice code paths.
    - Voice code may pass only total_chars_hint if message list is not available.
    """
    try:
        # Fast opt-out gate (default off to avoid overhead when not desired)
        if os.getenv("TEXT_CONTEXT_SUMMARY_ENABLED", "false").lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return

        # 1 token = 4 chars
        # So for 8k budget, we need 8000 * 4 = 32000 chars
        budget_chars = int(os.getenv("TEXT_CONTEXT_CHAR_BUDGET", "32000") or 32000)
        thresh = float(os.getenv("TEXT_CONTEXT_SUMMARIZE_THRESHOLD", "0.8") or 0.8)
        if budget_chars <= 0:
            return
        if messages is not None:
            total_chars = sum(len(m.get("content") or "") for m in messages)
        else:
            total_chars = int(total_chars_hint or 0)
        if total_chars <= 0:
            return
        ratio = total_chars / budget_chars
        if ratio < thresh:
            return
        now = time.perf_counter()
        last = _SUMMARY_LAST_TRIGGER.get(conversation_id, 0.0)
        if (now - last) < _SUMMARY_MIN_INTERVAL_S:
            return

        import uuid

        job_id = uuid.uuid4()
        owner_id = await conn.fetchval(
            "SELECT comp.owner_id FROM companions comp WHERE comp.id = $1",
            companion_id,
        )
        import json as _json

        params_json = _json.dumps(
            {
                "budget_chars": int(budget_chars),
                "total_chars": int(total_chars),
                "threshold": float(thresh),
            }
        )
        await conn.execute(
            """
            INSERT INTO background_jobs (id, owner_id, job_type, status, conversation_id, params)
            VALUES ($1, $2, 'conversation_summary', 'PENDING', $3, $4::jsonb)
            """,
            job_id,
            owner_id,
            conversation_id,
            params_json,
        )
        from ..services.modal_gateway import dispatch_conversation_summary_job

        await dispatch_conversation_summary_job(job_id=job_id, conversation_id=conversation_id)
        _SUMMARY_LAST_TRIGGER[conversation_id] = now
    except Exception as e:
        # Non‑fatal; log server‑side only
        import logging

        logging.getLogger(__name__).warning(f"[SUMMARY] trigger failed: {e}")
