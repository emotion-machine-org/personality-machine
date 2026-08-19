from typing import Any, Dict, List
from uuid import UUID, uuid4

import asyncpg


async def create_labeling_job(
    conn: asyncpg.Connection,
    owner_id: UUID,
    companion_id: UUID,
    *,
    model: str | None = None,
    provider: str | None = None,
    labels_version: int = 1,
    skip_existing: bool = True,
    since: str | None = None,
) -> UUID:
    """Create or reuse an active labeling job using background_jobs.

    Returns existing active job id if found.
    """
    # Reuse active job for this companion
    row = await conn.fetchrow(
        """
        SELECT id FROM background_jobs
        WHERE job_type = 'label_conversations'
          AND companion_id = $1
          AND status IN ('PENDING','RUNNING')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        companion_id,
    )
    if row:
        return row["id"]

    job_id = uuid4()
    await conn.execute(
        """
        INSERT INTO background_jobs (
          id, owner_id, job_type, status, companion_id, params
        ) VALUES (
          $1, $2, 'label_conversations', 'PENDING', $3,
          jsonb_build_object(
            'model', $4::text,
            'provider', $5::text,
            'labels_version', $6::int,
            'skip_existing', $7::boolean,
            'since', $8::timestamptz
          )
        )
        """,
        job_id,
        owner_id,
        companion_id,
        model,
        provider,
        labels_version,
        skip_existing,
        since,
    )
    return job_id


async def get_job(
    conn: asyncpg.Connection,
    job_id: UUID,
) -> Dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT id, owner_id, companion_id, created_at, started_at, completed_at,
               status, total_items, processed_count, error_count, params, error
        FROM background_jobs
        WHERE id = $1
        """,
        job_id,
    )
    return dict(row) if row else None


async def get_labels_for_conversation(
    conn: asyncpg.Connection,
    conversation_id: UUID,
) -> Dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT conversation_id, engagement_label, dependency_risk_label,
               engagement_confidence, dependency_confidence, model, provider,
               labels_version, analyzed_at, status, error
        FROM conversation_labels
        WHERE conversation_id = $1
        """,
        conversation_id,
    )
    return dict(row) if row else None


async def get_labels_for_companion(
    conn: asyncpg.Connection,
    companion_id: UUID,
    *,
    engagement: List[str] | None = None,
    risk: List[str] | None = None,
    status: List[str] | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    fast: bool = False,
    min_msgs: int | None = None,
    max_msgs: int | None = None,
) -> List[Dict[str, Any]]:
    """Return labels joined with conversation summary data for a companion.

    Note: Simple filter implementation; can be optimized later.
    """
    filters = ["c.companion_id = $1"]
    params: List[Any] = [companion_id]
    idx = 2
    if engagement:
        filters.append(f"cl.engagement_label = ANY(${idx})")
        params.append(engagement)
        idx += 1
    if risk:
        filters.append(f"cl.dependency_risk_label = ANY(${idx})")
        params.append(risk)
        idx += 1
    if status:
        filters.append(f"cl.status = ANY(${idx})")
        params.append(status)
        idx += 1
    if q:
        filters.append(f"(c.external_user_id ILIKE ${idx})")
        params.append(f"%{q}%")
        idx += 1
    if min_msgs is not None:
        filters.append(f"c.message_count >= ${idx}")
        params.append(min_msgs)
        idx += 1
    if max_msgs is not None:
        filters.append(f"c.message_count <= ${idx}")
        params.append(max_msgs)
        idx += 1

    where = " AND ".join(filters)
    limit_ph = idx
    offset_ph = idx + 1
    # With persisted counters, both fast and non-fast can read from conversations
    order_expr = (
        "COALESCE(c.last_message_at, c.started_at) DESC NULLS LAST"
        if not fast
        else "c.started_at DESC"
    )
    query = f"""
        SELECT
          c.id as conversation_id,
          c.external_user_id,
          c.started_at,
          c.last_message_at,
          c.message_count,
          cl.engagement_label,
          cl.dependency_risk_label,
          cl.analyzed_at
        FROM conversations c
        LEFT JOIN conversation_labels cl ON cl.conversation_id = c.id
        WHERE {where}
        ORDER BY {order_expr}
        LIMIT ${limit_ph} OFFSET ${offset_ph}
    """
    query = query.format(where=where, order_expr=order_expr, limit_ph=limit_ph, offset_ph=offset_ph)
    params.extend([limit, offset])

    rows = await conn.fetch(query, *params)
    return [dict(row) for row in rows]
