from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import Dict, List
from uuid import UUID

import asyncpg

from ..models.memory import MemoryItem, MemoryQuery
from ..services.memory_service import MemoryService
from .openai_clients import get_openai_async_client

logger = logging.getLogger(__name__)


class RetrievalGateStrategy(str, Enum):
    KEYWORD_HEURISTIC = "KEYWORD_HEURISTIC"
    LLM_GATE = "LLM_GATE"
    NO_GATE = "NO_GATE"


# Select one strategy by setting this constant.
# Note: We intentionally avoid a new env var per requestor’s preference.
GATE_STRATEGY: RetrievalGateStrategy = RetrievalGateStrategy.KEYWORD_HEURISTIC  # NO_GATE

# For NO_GATE, use a conservative saliency threshold so weak hits drop out early.
NO_GATE_MIN_SALIENCY: float = 0.35


def _should_retrieve_keyword_heuristic(
    user_text: str, last_seconds: int | None = None, turn_index: int | None = None
) -> bool:
    txt = (user_text or "").lower()
    triggers = [
        "remember",
        "in the future",
        "usually",
        "i like",
        "i love",
        "i hate",
        "my name is",
        "i am planning",
        "goal",
        "preference",
        "i prefer",
        # Expanded to catch common recall queries
        "favorite",
        "favourite",
        "do you remember",
        "do you know",
        "what is my",
    ]
    if any(k in txt for k in triggers):
        return True
    # Occasionally fetch every ~2 turns or >30s gap
    return bool(
        (turn_index is not None and turn_index % 2 == 0)
        or (last_seconds is not None and last_seconds >= 30)
    )


async def _llm_gate(user_text: str, recent_messages: List[Dict] | None = None) -> bool:
    """Ask a tiny LLM if retrieval would improve the next reply.

    Returns True/False. On failure or timeout, falls back to keyword heuristic.
    """
    # Quick positive: if keywords strongly suggest retrieval, short-circuit
    if _should_retrieve_keyword_heuristic(user_text):
        return True

    model = os.getenv("MEMORY_RETRIEVAL_MODEL", "gpt-4o-mini")
    timeout_ms = float(os.getenv("MEMORY_CLASSIFIER_TIMEOUT_MS", "200"))  # keep it tiny

    # Prepare compact context of last N turns (role: content)
    context_lines: List[str] = []
    if recent_messages:
        for m in recent_messages[-6:]:
            role = (m.get("role") or "").strip()[:16]
            content = (m.get("content") or "").strip().replace("\n", " ")[:180]
            if role and content:
                context_lines.append(f"{role}: {content}")
    ctx = "\n".join(context_lines)

    # Try OpenAI first; if model indicates Gemini, attempt import dynamically and fallback gracefully
    async def _openai_call():
        client = get_openai_async_client()
        sys = "Reply strictly with 'yes' or 'no'."
        usr = (
            "Decide if retrieving the user's stored memories would help answer the next assistant reply.\n"
            "Say 'yes' if the user is asking about past preferences, identity, ongoing tasks/goals, constraints, or anything likely stored as memory; otherwise 'no'.\n"
            f"Recent: {ctx}\n\nNow the user's message: {user_text[:1000]}"
        )
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            temperature=0,
            max_tokens=2,
        )
        txt = (resp.choices[0].message.content or "no").strip().lower()
        return txt.startswith("y")

    async def _gemini_call():  # pragma: no cover (optional path)
        try:
            import google.generativeai as genai  # type: ignore

            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
            model_name = model or "gemini-1.5-flash"
            prompt = (
                "Reply 'yes' or 'no' only: should we retrieve user memories for the next reply?\n"
                f"Recent: {ctx}\nUser: {user_text[:1000]}"
            )
            # Gemini Python SDK is sync; run in thread to keep async interface
            loop = asyncio.get_event_loop()

            def _sync():
                m = genai.GenerativeModel(model_name)
                r = m.generate_content(prompt)
                return (r.text or "no").strip().lower()

            txt = await loop.run_in_executor(None, _sync)
            return txt.startswith("y")
        except Exception:
            return _should_retrieve_keyword_heuristic(user_text)

    try:
        if model.lower().startswith("gemini") or model.lower().startswith("google"):
            return await asyncio.wait_for(_gemini_call(), timeout=timeout_ms / 1000.0)
        return await asyncio.wait_for(_openai_call(), timeout=timeout_ms / 1000.0)
    except Exception:
        return _should_retrieve_keyword_heuristic(user_text)


async def should_retrieve_memories(
    user_text: str,
    *,
    recent_messages: List[Dict] | None = None,
) -> bool:
    """Strategy-dispatched retrieval gating.

    - KEYWORD_HEURISTIC: lexical triggers and cadence
    - LLM_GATE: asks a tiny model using recent messages for context
    - NO_GATE: always retrieve
    """
    if GATE_STRATEGY == RetrievalGateStrategy.NO_GATE:
        return True
    if GATE_STRATEGY == RetrievalGateStrategy.KEYWORD_HEURISTIC:
        return _should_retrieve_keyword_heuristic(user_text)
    if GATE_STRATEGY == RetrievalGateStrategy.LLM_GATE:
        return await _llm_gate(user_text, recent_messages)
    # Fallback: be safe and use heuristic
    return _should_retrieve_keyword_heuristic(user_text)


async def retrieve_regular_memories(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    query_text: str,
    external_user_id: str | None,
    conversation_id: UUID | None,
    timings: dict | None = None,
) -> List[MemoryItem]:
    cfg = await MemoryService.get_companion_memory_config(conn, companion_id=companion_id)
    # Conservative threshold for NO_GATE to fail fast on weak matches
    min_saliency_default = float(cfg.get("min_saliency", 0.2))
    if GATE_STRATEGY == RetrievalGateStrategy.NO_GATE:
        try:
            min_saliency_default = max(min_saliency_default, float(NO_GATE_MIN_SALIENCY))
        except Exception:
            min_saliency_default = max(min_saliency_default, 0.35)

    rows = await MemoryService.search(
        conn,
        companion_id=companion_id,
        query=MemoryQuery(query=query_text),
        lambda_recency=cfg.get("recency"),
        top_k_default=int(cfg.get("top_k", 30)),
        min_saliency_default=min_saliency_default,
        external_user_id=external_user_id,
        is_core=False,
        timings=timings,
    )
    # log retrieved memories
    logger.info(f"Retrieved memories: {rows}")
    return [MemoryItem(**r) for r in rows]


def format_memory_block(items: List[MemoryItem]) -> str:
    if not items:
        return ""
    sel = items[:5]
    lines = [
        "# MEMORY CONTEXT",
        "Use these to personalize; do not restate verbatim unless explicitly relevant.",
    ]
    for m in sel:
        lines.append(f"- {m.content.strip()}")
    return "\n".join(lines)
