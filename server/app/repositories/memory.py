import logging
import os
from typing import Any, Dict, List, Sequence
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


VALID_ORDER_BY = {"created_at", "last_accessed_at", "importance"}
VALID_ORDER_DIR = {"ASC", "DESC"}


def _vec_text(values: Sequence[float]) -> str:
    """Render a python sequence as a Postgres vector literal via text cast.

    We depend on the pgvector implicit text→vector cast: '[0.1, 0.2, ...]'.
    """
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


class MemoryRepository:
    @staticmethod
    async def list_memories(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        limit: int = 50,
        offset: int = 0,
        conversation_id: UUID | None = None,
        external_user_id: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "DESC",
        is_core: bool | None = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        if order_by not in VALID_ORDER_BY:
            order_by = "created_at"
        order_dir = order_dir.upper()
        if order_dir not in VALID_ORDER_DIR:
            order_dir = "DESC"

        filters = ["m.companion_id = $1"]
        params: List[Any] = [companion_id]

        if conversation_id:
            params.append(conversation_id)
            filters.append(f"m.conversation_id = ${len(params)}")
        if external_user_id:
            params.append(external_user_id)
            filters.append(f"m.external_user_id = ${len(params)}")
        if is_core is not None:
            params.append(is_core)
            filters.append(f"m.is_core = ${len(params)}")

        where_clause = " AND ".join(filters)

        base_select = "\n".join(
            [
                "SELECT m.id, m.companion_id,",
                "       COALESCE(m.content, msg.content) AS content,",
                "       m.created_at, m.importance, m.weight_user, m.modality,",
                "       m.last_accessed_at, m.commentary, m.conversation_id, m.sender_type,",
                "       m.external_user_id, m.message_id, m.is_core",
                "FROM memories m",
                "LEFT JOIN messages msg ON m.message_id = msg.id",
                f"WHERE {where_clause}",
                f"ORDER BY m.{order_by} {order_dir}",
                f"LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}",
            ]
        )

        rows = await conn.fetch(base_select, *params, limit, offset)

        count_sql = "\n".join(
            [
                "SELECT COUNT(*)",
                "FROM memories m",
                "LEFT JOIN messages msg ON m.message_id = msg.id",
                f"WHERE {where_clause}",
            ]
        )
        total_count = await conn.fetchval(count_sql, *params)

        return [dict(r) for r in rows], int(total_count or 0)

    @staticmethod
    async def create_memory(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        content: str | None,
        embedding: Sequence[float],
        importance: float,
        weight_user: float = 1.0,
        modality: str = "text",
        commentary: str | None = None,
        conversation_id: UUID | None = None,
        sender_type: str = "user",
        external_user_id: str | None = None,
        message_id: UUID | None = None,
        is_core: bool = False,
    ) -> UUID:
        vec = _vec_text(embedding)
        row = await conn.fetchrow(
            """
            INSERT INTO memories (
                companion_id, content, embedding, importance, weight_user, modality,
                commentary, conversation_id, sender_type, external_user_id, message_id, is_core
            ) VALUES (
                $1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10, $11, $12
            ) RETURNING id
            """,
            companion_id,
            content,
            vec,
            float(importance),
            float(weight_user),
            modality,
            commentary,
            conversation_id,
            sender_type,
            external_user_id,
            message_id,
            bool(is_core),
        )
        return row["id"]

    @staticmethod
    async def update_memory(
        conn: asyncpg.Connection,
        *,
        memory_id: UUID,
        companion_id: UUID,
        importance: float | None = None,
        commentary: str | None = None,
        content: str | None = None,
    ) -> bool:
        fields = []
        args: List[Any] = []
        if importance is not None:
            fields.append(f"importance = ${len(args) + 1}")
            args.append(float(importance))
        if commentary is not None:
            fields.append(f"commentary = ${len(args) + 1}")
            args.append(commentary)
        if content is not None:
            fields.append(f"content = ${len(args) + 1}")
            args.append(content)
        if not fields:
            return False
        args.extend([memory_id, companion_id])
        sql = f"""
            UPDATE memories SET {", ".join(fields)}
            WHERE id = ${len(args) - 1} AND companion_id = ${len(args)}
        """
        res = await conn.execute(sql, *args)
        return res.startswith("UPDATE ") and not res.endswith("0")

    @staticmethod
    async def delete_memory(
        conn: asyncpg.Connection, *, memory_id: UUID, companion_id: UUID
    ) -> bool:
        res = await conn.execute(
            "DELETE FROM memories WHERE id = $1 AND companion_id = $2",
            memory_id,
            companion_id,
        )
        return res == "DELETE 1"

    @staticmethod
    async def search_memories(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        embedding: Sequence[float],
        lambda_recency: float,
        top_k: int = 50,
        min_saliency: float = 2.0,
        conversation_id: UUID | None = None,
        external_user_id: str | None = None,
        sender_type: str | None = None,
        modality: str | None = None,
        is_core: bool | None = None,
        candidate_count: int | None = None,
        timings: Dict[str, float] | None = None,
    ) -> List[Dict[str, Any]]:
        import time as _t

        vec = _vec_text(embedding)
        filters = ["m.companion_id = $1"]
        params: List[Any] = [companion_id]
        if conversation_id:
            params.append(conversation_id)
            filters.append(f"m.conversation_id = ${len(params)}")
        if external_user_id:
            params.append(external_user_id)
            filters.append(f"m.external_user_id = ${len(params)}")
        if sender_type:
            params.append(sender_type)
            filters.append(f"m.sender_type = ${len(params)}")
        if modality:
            params.append(modality)
            filters.append(f"m.modality = ${len(params)}")
        if is_core is not None:
            params.append(bool(is_core))
            filters.append(f"m.is_core = ${len(params)}")

        # Candidate list size for re-ranking
        if candidate_count is None:
            try:
                # Default: min(300, top_k*10)
                candidate_count = min(
                    300, int(top_k) * int(os.getenv("MEMORY_CANDIDATE_MULTIPLIER", "10"))
                )
            except Exception:
                candidate_count = min(300, top_k * 10)
        candidate_count = max(int(top_k), int(candidate_count))

        # Optional: widen HNSW search breadth for this statement
        try:
            ef = os.getenv("MEMORY_HNSW_EF_SEARCH")
            if ef:
                ef_int = int(ef)
                await conn.execute(f"SET LOCAL hnsw.ef_search = {ef_int}")
        except Exception:
            pass

        # Parameter indices after dynamic filters
        v_idx = len(params) + 1
        cand_idx = v_idx + 1

        # Stage 1: ANN KNN to get candidate ids and distances
        sql_ann = f"""
          SELECT m.id, (m.embedding <=> ${v_idx}::vector) AS dist
          FROM memories m
          WHERE {" AND ".join(filters)}
          ORDER BY m.embedding <=> ${v_idx}::vector
          LIMIT ${cand_idx}
        """
        t_knn = _t.perf_counter()
        ann_rows = await conn.fetch(sql_ann, [*params, vec, int(candidate_count)])
        if timings is not None:
            timings["db_knn_ms"] = (_t.perf_counter() - t_knn) * 1000

        if not ann_rows:
            return []

        ids = [r["id"] for r in ann_rows]
        dists = [float(r["dist"]) for r in ann_rows]

        # Stage 2: Re-rank and filter on only candidates, joining message content now
        sql_rerank = """
        WITH ann(id, dist) AS (
            SELECT * FROM UNNEST($1::uuid[], $2::float4[])
        )
        SELECT m.id, m.companion_id,
               COALESCE(m.content, msg.content) AS content,
               m.created_at, m.importance, m.weight_user, m.modality,
               m.last_accessed_at, m.commentary, m.conversation_id, m.sender_type,
               m.external_user_id, m.message_id, m.is_core,
               (1 - ann.dist) AS similarity,
               ((1 - ann.dist) * (m.importance * m.weight_user)
                 * POWER($3::float, EXTRACT(EPOCH FROM (now() - m.last_accessed_at))/3600)) AS score
        FROM ann
        JOIN memories m ON m.id = ann.id
        LEFT JOIN messages msg ON m.message_id = msg.id
        WHERE ((1 - ann.dist) * (m.importance * m.weight_user)
                 * POWER($3::float, EXTRACT(EPOCH FROM (now() - m.last_accessed_at))/3600)) >= $4
        ORDER BY score DESC
        LIMIT $5
        """
        t_rank = _t.perf_counter()
        rows = await conn.fetch(
            sql_rerank, ids, dists, float(lambda_recency), float(min_saliency), int(top_k)
        )
        if timings is not None:
            timings["db_rerank_ms"] = (_t.perf_counter() - t_rank) * 1000
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        # Bump recency for fetched rows, unless disabled via env
        try:
            if os.getenv("MEMORY_UPDATE_RECENCY_ENABLED", "true").lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                await conn.execute(
                    "UPDATE memories SET last_accessed_at = now() WHERE id = ANY($1::uuid[])",
                    ids,
                )
        except Exception:
            # Non-fatal
            pass
        return [dict(r) for r in rows]

    @staticmethod
    async def list_core_memories(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT m.id, m.companion_id, COALESCE(m.content, msg.content) AS content,
                   m.created_at, m.importance, m.weight_user, m.modality,
                   m.last_accessed_at, m.commentary, m.conversation_id, m.sender_type,
                   m.external_user_id, m.message_id, m.is_core
            FROM memories m
            LEFT JOIN messages msg ON m.message_id = msg.id
            WHERE m.companion_id = $1 AND m.is_core = true
            ORDER BY m.created_at DESC
            LIMIT $2 OFFSET $3
            """,
            companion_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]

    @staticmethod
    async def get_stats(conn: asyncpg.Connection, *, companion_id: UUID) -> Dict[str, Any]:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE companion_id = $1", companion_id
        )
        avg_row = await conn.fetchrow(
            "SELECT AVG(importance) AS avg_imp, AVG(weight_user) AS avg_wu FROM memories WHERE companion_id = $1",
            companion_id,
        )
        by_sender_rows = await conn.fetch(
            "SELECT sender_type, COUNT(*) AS c FROM memories WHERE companion_id = $1 GROUP BY sender_type",
            companion_id,
        )
        by_modality_rows = await conn.fetch(
            "SELECT modality, COUNT(*) AS c FROM memories WHERE companion_id = $1 GROUP BY modality",
            companion_id,
        )
        return {
            "total": int(total or 0),
            "avg_importance": float(avg_row["avg_imp"])
            if avg_row and avg_row["avg_imp"] is not None
            else None,
            "avg_weight_user": float(avg_row["avg_wu"])
            if avg_row and avg_row["avg_wu"] is not None
            else None,
            "by_sender_type": {r["sender_type"]: int(r["c"]) for r in by_sender_rows},
            "by_modality": {r["modality"]: int(r["c"]) for r in by_modality_rows},
        }
