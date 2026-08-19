import json
import os
from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg
import modal

# Modal image and app
image = modal.Image.debian_slim().pip_install("asyncpg")
app = modal.App("em-db")


@app.cls(
    image=image,
    timeout=60 * 60,
    scaledown_window=60 * 10,
    secrets=[modal.Secret.from_name("em-service-secrets")],
    min_containers=int(os.environ.get("DBGW_MIN_CONTAINERS", "0")),  # DBGW_MIN_CONTAINERS=1
    max_containers=int(os.environ.get("DBGW_MAX_CONTAINERS", "1")),
)
@modal.concurrent(max_inputs=int(os.environ.get("DBGW_MAX_INPUTS", "128")))
class DbGateway:
    @modal.enter()
    async def _setup(self):
        self._pool: asyncpg.Pool | None = None

    async def _setup_jsonb_codec(self, conn: asyncpg.Connection) -> None:
        """Register JSONB codec to automatically encode/decode Python dicts."""
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    async def _ensure_pool(self):
        if self._pool is None:
            dsn = os.getenv("DATABASE_DSN")
            assert dsn, "DATABASE_DSN missing"
            self._pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=int(os.environ.get("DBGW_POOL_SIZE", "16")),
                statement_cache_size=0,
                init=self._setup_jsonb_codec,
            )

    @modal.method()
    async def start_labeling_job(
        self,
        job_id: str,
        companion_id: str,
        skip_existing: bool = True,
        since: str | None = None,
    ) -> List[str]:
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                "UPDATE background_jobs SET status='RUNNING', started_at=NOW() WHERE id=$1", job_id
            )
            params: List[Any] = [companion_id]
            idx = 2
            since_clause = ""
            if since:
                since_clause = f"AND COALESCE(MAX(m.created_at), c.started_at) >= ${idx}"
                params.append(since)
                idx += 1
            if skip_existing:
                q = f"""
                SELECT c.id
                FROM conversations c
                LEFT JOIN (
                  SELECT conversation_id, analyzed_at FROM conversation_labels
                ) cl ON cl.conversation_id = c.id
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.companion_id = $1
                GROUP BY c.id, cl.analyzed_at, c.started_at
                HAVING COALESCE(MAX(m.created_at), c.started_at) > COALESCE(cl.analyzed_at, to_timestamp(0))
                {since_clause}
                ORDER BY COALESCE(MAX(m.created_at), c.started_at) DESC
                """
            else:
                q = f"""
                SELECT c.id
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.companion_id = $1
                GROUP BY c.id, c.started_at
                {since_clause}
                ORDER BY COALESCE(MAX(m.created_at), c.started_at) DESC
                """
            rows = await conn.fetch(q, *params)
            targets = [str(row["id"]) for row in rows]
            await conn.execute(
                "UPDATE background_jobs SET total_items=$2 WHERE id=$1", job_id, len(targets)
            )
            await conn.execute(
                "SELECT pg_notify('job_updates', $1)",
                json.dumps(
                    {"id": job_id, "status": "RUNNING", "total_conversations": len(targets)}
                ),
            )
            return targets

    @modal.method()
    async def get_contexts(
        self, conversation_ids: List[str], max_chars: int = 20000, max_messages: int = 300
    ) -> List[Dict[str, Any]]:
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT conversation_id::text AS conversation_id, role, content, created_at
                FROM messages
                WHERE conversation_id = ANY($1::uuid[])
                ORDER BY conversation_id, created_at
                """,
                conversation_ids,
            )
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                cid = r["conversation_id"]
                grouped.setdefault(cid, []).append(
                    {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
                )
            out: List[Dict[str, Any]] = []
            for cid, msgs in grouped.items():
                parts: List[str] = []
                for m in msgs[-max_messages:]:
                    role = m.get("role", "")
                    content = (m.get("content") or "").replace("\n\n", "\n").strip()
                    parts.append(f"{role}: {content}")
                text = "\n".join(parts)
                if len(text) > max_chars:
                    text = text[-max_chars:]
                out.append({"conversation_id": cid, "text": text})
            return out

    @modal.method()
    async def upsert_labels_batch(self, job_id: str, items: List[Dict[str, Any]]) -> None:
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                processed = len(items)
                errors = sum(1 for it in items if it.get("status") == "FAILED")
                for it in items:
                    await conn.execute(
                        """
                        INSERT INTO conversation_labels (
                          conversation_id, engagement_label, dependency_risk_label,
                          engagement_confidence, dependency_confidence, model, provider,
                          labels_version, job_id, status, error
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                        ON CONFLICT (conversation_id) DO UPDATE SET
                          engagement_label = EXCLUDED.engagement_label,
                          dependency_risk_label = EXCLUDED.dependency_risk_label,
                          engagement_confidence = EXCLUDED.engagement_confidence,
                          dependency_confidence = EXCLUDED.dependency_confidence,
                          model = EXCLUDED.model,
                          provider = EXCLUDED.provider,
                          labels_version = EXCLUDED.labels_version,
                          job_id = EXCLUDED.job_id,
                          status = EXCLUDED.status,
                          error = EXCLUDED.error,
                          analyzed_at = now()
                        """,
                        it["conversation_id"],
                        it["engagement_label"],
                        it["dependency_risk_label"],
                        it.get("engagement_confidence"),
                        it.get("dependency_confidence"),
                        it.get("model"),
                        it.get("provider"),
                        it.get("labels_version", 1),
                        job_id,
                        it.get("status", "COMPLETED"),
                        it.get("error"),
                    )

    # ── Conversation summarization support ───────────────────────────────────
    @modal.method()
    async def start_summary_job(self, job_id: str, conversation_id: str) -> Dict[str, Any]:
        """Return messages + conversation meta and set total_items for the job."""
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                SELECT c.external_user_id, c.companion_id
                FROM conversations c
                WHERE c.id = $1
                """,
                conversation_id,
            )
            if not row:
                raise RuntimeError("Conversation not found")
            msgs = await conn.fetch(
                """
                SELECT role, content, created_at
                FROM messages
                WHERE conversation_id = $1
                ORDER BY created_at
                """,
                conversation_id,
            )
            await conn.execute(
                "UPDATE background_jobs SET total_items=$2 WHERE id=$1",
                job_id,
                len(msgs),
            )
            return {
                "companion_id": str(row["companion_id"]),
                "external_user_id": row["external_user_id"],
                "messages": [
                    {
                        "role": r["role"],
                        "content": r["content"],
                        "created_at": str(r["created_at"]),
                    }
                    for r in msgs
                ],
            }

    @modal.method()
    async def mark_job_completed(
        self, job_id: str, processed: int, total: int, errors: int
    ) -> None:
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                "UPDATE background_jobs SET status='COMPLETED', completed_at=NOW(), total_items=$2, processed_count=$3, error_count=$4 WHERE id=$1",
                job_id,
                total,
                processed,
                errors,
            )
            await conn.execute(
                "SELECT pg_notify('job_updates', $1)",
                json.dumps(
                    {
                        "id": job_id,
                        "status": "COMPLETED",
                        "processed_count": processed,
                        "total_conversations": total,
                        "error_count": errors,
                    }
                ),
            )

    @modal.method()
    async def mark_job_failed(self, job_id: str, error: str) -> None:
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                "UPDATE background_jobs SET status='FAILED', completed_at=NOW(), error=$2 WHERE id=$1",
                job_id,
                error,
            )
            await conn.execute(
                "SELECT pg_notify('job_updates', $1)",
                json.dumps({"id": job_id, "status": "FAILED", "error": error}),
            )

    # ── Privacy redaction support ───────────────────────────────────────────

    @modal.method()
    async def start_privacy_job(self, job_id: str, conversation_id: str) -> List[Dict[str, Any]]:
        """Mark job RUNNING and return messages for the conversation (id, role, content). Also snapshot last_message_at."""
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                "SELECT MAX(created_at) AS last_message_at FROM messages WHERE conversation_id = $1",
                conversation_id,
            )
            await conn.execute(
                "UPDATE background_jobs SET status='RUNNING', started_at=NOW(), source_last_message_at=$2 WHERE id=$1",
                job_id,
                row["last_message_at"],
            )
            rows = await conn.fetch(
                """
                SELECT id::text AS message_id, role, content, created_at
                FROM messages
                WHERE conversation_id = $1
                ORDER BY created_at
                """,
                conversation_id,
            )
            await conn.execute(
                "UPDATE background_jobs SET total_items=$2 WHERE id=$1",
                job_id,
                len(rows),
            )
            # notify RUNNING
            await conn.execute(
                "SELECT pg_notify('job_updates', $1)",
                json.dumps({"id": job_id, "status": "RUNNING"}),
            )
            return [dict(r) for r in rows]

    @modal.method()
    async def set_message_redactions_batch(self, job_id: str, items: List[Dict[str, Any]]) -> None:
        """Batch update messages with pii_spans and set redacted_at."""
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                processed = 0
                errors = 0
                for it in items:
                    try:
                        await conn.execute(
                            "UPDATE messages SET pii_spans=$2::jsonb, redacted_at=NOW() WHERE id=$1",
                            it["message_id"],
                            json.dumps(it.get("pii_spans") or []),
                        )
                        processed += 1
                    except Exception:
                        errors += 1
                await conn.execute(
                    "UPDATE background_jobs SET processed_count = COALESCE(processed_count,0) + $2, error_count = COALESCE(error_count,0) + $3 WHERE id=$1",
                    job_id,
                    processed,
                    errors,
                )
            await conn.execute(
                "SELECT pg_notify('job_updates', $1)",
                json.dumps(
                    {
                        "id": job_id,
                        "status": "RUNNING",
                        "processed_count": processed,
                        "error_count": errors,
                    }
                ),
            )

    @modal.method()
    async def set_conversation_privacy_computed(self, conversation_id: str) -> None:
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                "UPDATE conversations SET privacy_last_computed_at = NOW() WHERE id = $1",
                conversation_id,
            )

    # ── Memory ingest support ─────────────────────────────────────────────

    @modal.method()
    async def create_memories_batch(self, items: List[Dict[str, Any]]) -> None:
        """Insert a batch of memories.

        Each item must include: companion_id, embedding (List[float]), importance, weight_user,
        modality, sender_type, is_core; and one of {content | message_id}.
        Optional: conversation_id, external_user_id, commentary.
        """
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                for it in items:
                    vec_text = "[" + ",".join(f"{float(v):.8f}" for v in it["embedding"]) + "]"
                    await conn.execute(
                        """
                        INSERT INTO memories (
                            companion_id, content, embedding, importance, weight_user, modality,
                            commentary, conversation_id, sender_type, external_user_id, message_id, is_core
                        ) VALUES (
                            $1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10, $11, $12
                        )
                        """,
                        it.get("companion_id"),
                        it.get("content"),
                        vec_text,
                        float(it.get("importance", 0.5)),
                        float(it.get("weight_user", 1.0)),
                        it.get("modality", "text"),
                        it.get("commentary"),
                        it.get("conversation_id"),
                        it.get("sender_type", "user"),
                        it.get("external_user_id"),
                        it.get("message_id"),
                        bool(it.get("is_core", False)),
                    )

                    # Bust core prompt cache if a core memory was added
                    if it.get("is_core"):
                        try:
                            from ...context.core_prompt_layer import bust_core_prompt_cache

                            bust_core_prompt_cache(UUID(str(it.get("companion_id"))))
                        except Exception:
                            pass

    @modal.method()
    async def update_memories_batch(self, items: List[Dict[str, Any]]) -> None:
        """Batch update memories. Each item must include memory_id and may
        include any of: content, embedding (List[float]), importance,
        commentary. If embedding is provided it is expected to match content.
        """
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                for it in items:
                    mem_id = it.get("memory_id")
                    if not mem_id:
                        continue
                    fields: List[str] = []
                    args: List[Any] = []
                    if it.get("importance") is not None:
                        fields.append(f"importance = ${len(args) + 1}")
                        args.append(float(it["importance"]))
                    if it.get("commentary") is not None:
                        fields.append(f"commentary = ${len(args) + 1}")
                        args.append(it.get("commentary"))
                    if it.get("content") is not None:
                        fields.append(f"content = ${len(args) + 1}")
                        args.append(it.get("content"))
                    if it.get("embedding") is not None:
                        vec_text = "[" + ",".join(f"{float(v):.8f}" for v in it["embedding"]) + "]"
                        fields.append(f"embedding = (${len(args) + 1})::vector")
                        args.append(vec_text)
                    if not fields:
                        continue
                    args.append(mem_id)
                    sql = f"UPDATE memories SET {', '.join(fields)} WHERE id = ${len(args)}"
                    await conn.execute(sql, *args)

    @modal.method()
    async def apply_memory_v2_operations(
        self, relationship_id: str, operations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Apply Memory V2 ADD/UPDATE/DELETE operations atomically.

        Operations format:
        [
            {"action": "add", "content": "...", "type": "..."},
            {"action": "update", "id": "...", "content": "...", "type": "..."},
            {"action": "delete", "id": "..."},
        ]

        Returns: {added: int, updated: int, deleted: int, errors: [...]}
        """
        await self._ensure_pool()

        added = 0
        updated = 0
        deleted = 0
        errors = []

        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                for op in operations:
                    action = op.get("action")
                    try:
                        if action == "add":
                            content = (op.get("content") or "").strip()
                            if not content:
                                errors.append("Empty content for add operation")
                                continue
                            await conn.execute(
                                """
                                INSERT INTO memory_v2_entries (relationship_id, content, type)
                                VALUES ($1, $2, $3)
                                """,
                                UUID(relationship_id),
                                content,
                                op.get("type"),
                            )
                            added += 1

                        elif action == "update":
                            entry_id = op.get("id")
                            content = (op.get("content") or "").strip()
                            if not entry_id or not content:
                                errors.append(f"Missing id or content for update: {op}")
                                continue
                            result = await conn.execute(
                                """
                                UPDATE memory_v2_entries
                                SET content = $2, type = COALESCE($3, type), updated_at = now()
                                WHERE id = $1 AND relationship_id = $4
                                """,
                                UUID(entry_id),
                                content,
                                op.get("type"),
                                UUID(relationship_id),
                            )
                            if result == "UPDATE 1":
                                updated += 1
                            else:
                                errors.append(f"Entry {entry_id} not found for update")

                        elif action == "delete":
                            entry_id = op.get("id")
                            if not entry_id:
                                errors.append("Missing id for delete operation")
                                continue
                            result = await conn.execute(
                                """
                                DELETE FROM memory_v2_entries
                                WHERE id = $1 AND relationship_id = $2
                                """,
                                UUID(entry_id),
                                UUID(relationship_id),
                            )
                            if result == "DELETE 1":
                                deleted += 1
                            else:
                                errors.append(f"Entry {entry_id} not found for delete")

                        else:
                            errors.append(f"Unknown action: {action}")

                    except Exception as e:
                        errors.append(f"Operation {action} failed: {str(e)}")

        return {
            "added": added,
            "updated": updated,
            "deleted": deleted,
            "errors": errors,
        }

    @modal.method()
    async def get_memory_v2_entries(self, relationship_id: str) -> List[Dict[str, Any]]:
        """Fetch all Memory V2 entries for a relationship.

        Returns list of {id, content, type, created_at, updated_at}.
        """
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT id::text, content, type, created_at, updated_at
                FROM memory_v2_entries
                WHERE relationship_id = $1
                ORDER BY created_at DESC
                """,
                UUID(relationship_id),
            )
            return [
                {
                    "id": r["id"],
                    "content": r["content"],
                    "type": r["type"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                }
                for r in rows
            ]

    @modal.method()
    async def get_memory_v2_config(self, relationship_id: str) -> Dict[str, Any]:
        """Fetch Memory V2 configuration for a relationship's companion.

        Returns {model, ingestion_prompt, max_entries} from the companion config.
        """
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            # Get companion_id from relationship, then get latest config
            row = await conn.fetchrow(
                """
                SELECT cv.system_prompt
                FROM relationships r
                JOIN companions c ON c.id = r.companion_id
                LEFT JOIN LATERAL (
                    SELECT system_prompt
                    FROM companion_versions
                    WHERE companion_id = c.id
                    ORDER BY version_number DESC, created_at DESC
                    LIMIT 1
                ) cv ON true
                WHERE r.id = $1
                """,
                UUID(relationship_id),
            )
            if not row or not row["system_prompt"]:
                return {"model": None, "ingestion_prompt": None, "max_entries": 100}

            # Parse config JSON
            config = row["system_prompt"]
            if isinstance(config, str):
                import json as _json

                try:
                    config = _json.loads(config)
                except Exception:
                    config = {}

            memory_config = config.get("memory", {}) if isinstance(config, dict) else {}
            return {
                "model": memory_config.get("model"),
                "ingestion_prompt": memory_config.get("ingestion_prompt"),
                "max_entries": memory_config.get("max_entries", 100),
            }

    # ── Relationship Summarization support ─────────────────────────────────────

    @modal.method()
    async def create_relationship_summary(
        self,
        relationship_id: str,
        content: str,
        version: int,
        messages_start: int,
        messages_end: int,
        message_count: int,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new relationship summary and update tracking state.

        Args:
            relationship_id: The relationship to create summary for
            content: The generated summary text
            version: Version number (1, 2, 3...)
            messages_start: First message seq included in this summary's new content
            messages_end: Last message seq included
            message_count: Total messages summarized cumulatively
            model: LLM model used for generation

        Returns:
            Dict with id, version, created_at of the new summary
        """
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                # Create the summary
                row = await conn.fetchrow(
                    """
                    INSERT INTO relationship_summaries
                        (relationship_id, content, version, messages_start,
                         messages_end, message_count, model)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id::text, version, created_at
                    """,
                    UUID(relationship_id),
                    content,
                    version,
                    messages_start,
                    messages_end,
                    message_count,
                    model,
                )

                # Update relationship tracking
                await conn.execute(
                    """
                    UPDATE relationships
                    SET last_summarized_at = now(),
                        last_summarized_message_count = $2
                    WHERE id = $1
                    """,
                    UUID(relationship_id),
                    message_count,
                )

                return {
                    "id": row["id"] if row else None,
                    "version": row["version"] if row else version,
                    "created_at": row["created_at"].isoformat()
                    if row and row["created_at"]
                    else None,
                }

    @modal.method()
    async def get_latest_relationship_summary(
        self,
        relationship_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get latest summary for a relationship.

        Returns dict with content, version, messages_end, message_count
        or None if no summary exists.
        """
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                SELECT content, version, messages_end, message_count
                FROM relationship_summaries
                WHERE relationship_id = $1
                ORDER BY version DESC
                LIMIT 1
                """,
                UUID(relationship_id),
            )
            if not row:
                return None
            return {
                "content": row["content"],
                "version": row["version"],
                "messages_end": row["messages_end"],
                "message_count": row["message_count"],
            }

    @modal.method()
    async def get_relationship_messages_for_summary(
        self,
        relationship_id: str,
        start_seq: int,
        end_seq: int,
    ) -> List[Dict[str, Any]]:
        """Get messages in a sequence range for summarization.

        Args:
            relationship_id: The relationship to get messages for
            start_seq: Starting sequence number (inclusive)
            end_seq: Ending sequence number (inclusive)

        Returns:
            List of message dicts with role, content, seq ordered by seq
        """
        await self._ensure_pool()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT role, content, seq
                FROM messages
                WHERE relationship_id = $1
                  AND seq >= $2 AND seq <= $3
                ORDER BY seq ASC
                """,
                UUID(relationship_id),
                start_seq,
                end_seq,
            )
            return [
                {
                    "role": r["role"],
                    "content": r["content"],
                    "seq": r["seq"],
                }
                for r in rows
            ]
