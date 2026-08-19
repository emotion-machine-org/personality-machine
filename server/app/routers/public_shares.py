"""
Public share endpoints for redacted, view-only conversations.
"""

from typing import List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import get_db

router = APIRouter(prefix="/api/public/shares", tags=["public-shares"])


class PublicMeta(BaseModel):
    title: str | None = None
    started_at: str
    message_count: int


class PublicMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


def _mask_text(text: str, spans: List[dict] | str | None) -> str:
    # Accept JSON string or list for robustness
    if isinstance(spans, (str | bytes)):
        try:
            import json as _json

            spans = _json.loads(spans)  # type: ignore[assignment]
        except Exception:
            spans = None
    if not spans:
        return text
    n = len(text or "")
    parts: List[str] = []
    last = 0
    norm = []
    for s in spans or []:
        try:
            a = max(0, min(n, int(s.get("start", 0))))
            b = max(0, min(n, int(s.get("end", 0))))
            if b > a:
                norm.append({"start": a, "end": b})
        except Exception:
            continue
    norm.sort(key=lambda x: (x["start"], x["end"]))
    merged: List[dict] = []
    for s in norm:
        if merged and s["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
        else:
            merged.append(s)
    for s in merged:
        parts.append(text[last : s["start"]])
        parts.append("█" * (s["end"] - s["start"]))
        last = s["end"]
    parts.append(text[last:])
    return "".join(parts)


@router.get("/{slug}/meta", response_model=PublicMeta)
async def get_public_meta(slug: str, conn: asyncpg.Connection = Depends(get_db)):
    row = await conn.fetchrow(
        """
        SELECT id, share_title, started_at
        FROM conversations
        WHERE share_slug = $1 AND share_status = 'public'
        """,
        slug,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Shared conversation not found")

    msg_count = await conn.fetchval(
        "SELECT COUNT(1) FROM messages WHERE conversation_id = $1",
        row["id"],
    )
    return PublicMeta(
        title=row.get("share_title"),
        started_at=row["started_at"].isoformat()
        if hasattr(row["started_at"], "isoformat")
        else str(row["started_at"]),
        message_count=int(msg_count or 0),
    )


@router.get("/{slug}/messages", response_model=List[PublicMessage])
async def get_public_messages(slug: str, conn: asyncpg.Connection = Depends(get_db)):
    conv = await conn.fetchrow(
        """
        SELECT id FROM conversations
        WHERE share_slug = $1 AND share_status = 'public'
        """,
        slug,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Shared conversation not found")

    # Ensure redactions are present and fresh
    stale = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM messages m
          WHERE m.conversation_id = $1
            AND (m.redacted_at IS NULL OR m.redacted_at < m.created_at)
        )
        """,
        conv["id"],
    )
    if stale:
        raise HTTPException(status_code=409, detail="Shared conversation privacy view is not ready")

    rows = await conn.fetch(
        """
        SELECT id, role, content, created_at, pii_spans
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at
        """,
        conv["id"],
    )
    out: List[PublicMessage] = []
    for r in rows:
        masked = _mask_text(r["content"], r["pii_spans"])
        out.append(
            PublicMessage(
                id=str(r["id"]),
                role=r["role"],
                content=masked,
                created_at=r["created_at"].isoformat()
                if hasattr(r["created_at"], "isoformat")
                else str(r["created_at"]),
            )
        )
    return out
