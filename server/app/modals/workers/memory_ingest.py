from __future__ import annotations

"""Memory Ingest worker (Modal App)

Deploy with:
  modal deploy server/app/modals/workers/memory_ingest.py
"""

import os
from typing import Any, Dict, List, Optional

from modal import App, Cls, Image, Secret

app = App(name="em-memory")
image = Image.debian_slim().pip_install("openai")


async def _embed(text: str) -> List[float]:
    import openai

    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
    resp = await client.embeddings.create(model=model, input=text)
    return list(resp.data[0].embedding)  # type: ignore[attr-defined]


def _build_importance_system_prompt(guidance: str | None) -> str:
    base = (guidance or "").strip()
    rubric = (
        "You evaluate how important a memory is for future interactions, across any domain (e.g., health, learning, productivity, relationships, finance,  creative work).\n"
        "Consider: enduring personal facts, long-term preferences and constraints, goals/commitments/tasks, pivotal decisions, nuanced insights, safety/ethical boundaries, and changes over time.\n"
        "Scoring rubric (1-10):\n"
        "- 10 (Critical): Identity-level facts, explicit commitments, deadlines, safety constraints, or insights central to ongoing goals.\n"
        "- 9 (Very Important): Strong, stable preferences; multi-step plans; clear constraints or rules the companion must respect.\n"
        "- 8 (Important): Specific goals, detailed feedback shaping future behavior, key context to avoid repeating mistakes.\n"
        "- 7 (Moderately Important): Distinct interests, medium-term tasks, or actionable hints that improve personalization.\n"
        "- 5-6 (Somewhat Important): Useful details or mild preferences that may help but are not crucial.\n"
        "- 3-4 (Low Importance): Simple Q&A, generic chit-chat, or transient details unlikely to help later.\n"
        "- 1-2 (Minimal): Off-topic, ephemeral, or redundant content.\n"
        "Instructions: Reply ONLY with a single number 1-10. Optionally add a second line with 'Note: ...' to briefly explain the rating.\n"
    )
    if base:
        return base + "\n\n" + rubric
    return rubric


async def _importance_score(text: str, guidance: str | None) -> float:
    import re

    import openai

    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("MEMORY_IMPORTANCE_MODEL", "gpt-4o-mini")
    sys = _build_importance_system_prompt(guidance)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": text}],
            temperature=0,
            max_tokens=8,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception:
        return 0.5
    # parse 1..10 (support standalone '10')
    if raw.startswith("10") or re.search(r"\b10\b", raw):  # type: ignore[arg-type]
        return 1.0
    m = re.search(r"[1-9]", raw or "")
    if not m:
        return 0.5
    try:
        return max(min(int(m.group(0)) / 10.0, 1.0), 0.0)
    except Exception:
        return 0.5


def _heuristic_floor(content: str) -> float:
    c = (content or "").lower()
    floor = 0.0
    if any(k in c for k in ["my name is", "i'm called", "i am called", "birthday", "i was born"]):
        floor = max(floor, 0.85)
    if any(k in c for k in ["my favorite", "i prefer", "i like", "i love", "i hate"]):
        floor = max(floor, 0.6)
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
    if any(
        k in c
        for k in ["i can't", "do not", "never", "always", "i must", "i should", "i shouldn't"]
    ):
        floor = max(floor, 0.65)
    return floor


@app.function(
    image=image,
    secrets=[Secret.from_name("em-service-secrets")],
    timeout=60 * 10,
)
async def ingest_memories(payload: Dict[str, Any]) -> None:
    """Create memories for a list of items.

    Payload: { companion_id, items: [ { content?, message_id?, sender_type, conversation_id?, external_user_id?, importance_guidance?, weight_user?, modality?, is_core? } ] }
    Uses DbGateway for DB transactions.
    """
    companion_id = payload.get("companion_id")
    items: List[Dict[str, Any]] = payload.get("items", [])

    batch: List[Dict[str, Any]] = []
    for it in items:
        content = (it.get("content") or "").strip()
        # If no content passed, skip - worker avoids DB reads for simplicity
        if not content:
            continue

        # Check if importance was pre-validated by the API server
        pre_validated = bool(it.get("pre_validated", False))

        if pre_validated:
            # Use pre-computed importance from API server
            imp = float(it.get("importance", 0.5))
        else:
            # Determine write threshold (per-item override → env → default)
            try:
                thr = float(
                    it.get("min_importance_write")
                    or os.getenv("MEMORY_MIN_IMPORTANCE_WRITE", "0.55")
                )
            except Exception:
                thr = 0.55

            # Score importance first to cheaply filter trivial items
            imp = await _importance_score(content, it.get("importance_guidance"))
            imp = max(imp, _heuristic_floor(content))

            # Skip storing if below threshold and not explicitly core
            if not bool(it.get("is_core") or False) and imp < thr:
                continue

        # Only embed after passing threshold to save cost/latency
        emb = await _embed(content)

        batch.append(
            {
                "companion_id": companion_id,
                "content": None if it.get("message_id") else content,
                "embedding": emb,
                "importance": float(imp),
                "weight_user": float(it.get("weight_user") or 1.0),
                "modality": it.get("modality") or "text",
                "commentary": None,
                "conversation_id": it.get("conversation_id"),
                "sender_type": it.get("sender_type") or "user",
                "external_user_id": it.get("external_user_id"),
                "message_id": it.get("message_id"),
                "is_core": bool(it.get("is_core") or False),
            }
        )

    if not batch:
        return

    # Use DbGateway (centralized pool & transactions)
    db_app = os.getenv("MODAL_DB_APP", "em-db")
    Gw = Cls.lookup(db_app, "DbGateway")
    Gw.create_memories_batch.remote(batch)


@app.function(
    image=image,
    secrets=[Secret.from_name("em-service-secrets")],
    timeout=60 * 10,
)
async def update_memories(payload: Dict[str, Any]) -> None:
    """Update existing memories. Allows updating content (and embedding),
    importance and commentary. Expects items with at least memory_id. If
    content is provided, a new embedding is computed.

    Payload: { items: [ { memory_id, content?, importance?, commentary? } ] }
    """
    items: List[Dict[str, Any]] = payload.get("items", [])
    if not items:
        return

    batch: List[Dict[str, Any]] = []
    for it in items:
        mid = it.get("memory_id")
        if not mid:
            continue
        content = it.get("content")
        emb: List[float] | None = None
        if isinstance(content, str) and content.strip():
            emb = await _embed(content)
        batch.append(
            {
                "memory_id": mid,
                "importance": it.get("importance"),
                "commentary": it.get("commentary"),
                "content": content,
                "embedding": emb,
            }
        )

    if not batch:
        return

    db_app = os.getenv("MODAL_DB_APP", "em-db")
    Gw = Cls.lookup(db_app, "DbGateway")
    Gw.update_memories_batch.remote(batch)
