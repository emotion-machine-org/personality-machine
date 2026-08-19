"""Repository helpers for companion sharing flows."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Dict
from uuid import UUID

import asyncpg

from ..models.share import CompanionShare, CompanionShareAnalytics, ShareStatus

logger = logging.getLogger(__name__)


class ShareRateLimitExceeded(Exception):
    """Raised when a public share visitor exceeds rate limits."""

    def __init__(self, bucket: str, limit: int) -> None:
        super().__init__(f"Rate limit exceeded for {bucket} (limit={limit})")
        self.bucket = bucket
        self.limit = limit


def _share_from_row(row: asyncpg.Record | None) -> CompanionShare | None:
    if not row:
        return None
    data: Dict[str, Any] = dict(row)
    if "expose_status_events" not in data or data["expose_status_events"] is None:
        data["expose_status_events"] = False
    snapshot = data.get("config_snapshot")
    if isinstance(snapshot, str):
        try:
            data["config_snapshot"] = json.loads(snapshot)
        except Exception:
            logger.warning("Failed to parse config_snapshot for share %s", data.get("id"))
            data["config_snapshot"] = None
    try:
        return CompanionShare(**data)
    except Exception:
        logger.exception("Failed to deserialize CompanionShare row: %s", data)
        raise


class CompanionShareRepository:
    """CRUD helpers for companion_shares."""

    BASE_SELECT = """
        SELECT
            id,
            companion_id,
            owner_id,
            version_id,
            slug,
            status,
            allow_text,
            allow_voice,
            require_auth,
            expose_status_events,
            config_snapshot,
            display_name,
            description,
            created_at,
            updated_at,
            activated_at,
            disabled_at,
            total_sessions,
            total_messages,
            total_voice_sessions,
            last_activity_at
        FROM companion_shares
    """

    @staticmethod
    async def get_by_id(conn: asyncpg.Connection, share_id: UUID) -> CompanionShare | None:
        row = await conn.fetchrow(f"{CompanionShareRepository.BASE_SELECT} WHERE id = $1", share_id)
        return _share_from_row(row)

    @staticmethod
    async def get_for_companion(
        conn: asyncpg.Connection, companion_id: UUID
    ) -> CompanionShare | None:
        row = await conn.fetchrow(
            f"{CompanionShareRepository.BASE_SELECT} WHERE companion_id = $1",
            companion_id,
        )
        return _share_from_row(row)

    @staticmethod
    async def get_by_slug(conn: asyncpg.Connection, slug: str) -> CompanionShare | None:
        row = await conn.fetchrow(
            f"{CompanionShareRepository.BASE_SELECT} WHERE slug = $1",
            slug,
        )
        return _share_from_row(row)

    @staticmethod
    async def create(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        owner_id: UUID,
        version_id: UUID | None,
        slug: str,
        status: ShareStatus,
        allow_text: bool,
        allow_voice: bool,
        require_auth: bool,
        expose_status_events: bool,
        config_snapshot: Dict[str, Any] | None,
        display_name: str | None,
        description: str | None,
    ) -> CompanionShare:
        snapshot_json = json.dumps(config_snapshot) if config_snapshot is not None else None
        row = await conn.fetchrow(
            """
            INSERT INTO companion_shares (
                companion_id,
                owner_id,
                version_id,
                slug,
                status,
                allow_text,
                allow_voice,
                require_auth,
                expose_status_events,
                config_snapshot,
                display_name,
                description
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING
                id,
                companion_id,
                owner_id,
                version_id,
                slug,
                status,
                allow_text,
                allow_voice,
                require_auth,
                expose_status_events,
                config_snapshot,
                display_name,
                description,
                created_at,
                updated_at,
                activated_at,
                disabled_at,
                total_sessions,
                total_messages,
                total_voice_sessions,
                last_activity_at
            """,
            companion_id,
            owner_id,
            version_id,
            slug,
            status.value if isinstance(status, ShareStatus) else status,
            allow_text,
            allow_voice,
            require_auth,
            expose_status_events,
            snapshot_json,
            display_name,
            description,
        )
        share = _share_from_row(row)
        assert share is not None
        return share

    @staticmethod
    async def update(
        conn: asyncpg.Connection,
        share_id: UUID,
        *,
        status: ShareStatus | None = None,
        allow_text: bool | None = None,
        allow_voice: bool | None = None,
        require_auth: bool | None = None,
        expose_status_events: bool | None = None,
        config_snapshot: Dict[str, Any] | None = None,
        version_id: UUID | None = None,
        display_name: str | None = None,
        description: str | None = None,
        set_activated_at: bool = False,
        set_disabled_at: bool = False,
    ) -> CompanionShare:
        updates = []
        values: list[Any] = []

        status_value: str | None = None
        if status is not None:
            status_value = status.value if isinstance(status, ShareStatus) else status
            updates.append("status = ${}")
            values.append(status_value)
        if allow_text is not None:
            updates.append("allow_text = ${}")
            values.append(allow_text)
        if allow_voice is not None:
            updates.append("allow_voice = ${}")
            values.append(allow_voice)
        if require_auth is not None:
            updates.append("require_auth = ${}")
            values.append(require_auth)
        if expose_status_events is not None:
            updates.append("expose_status_events = ${}")
            values.append(expose_status_events)
        if config_snapshot is not None:
            updates.append("config_snapshot = ${}")
            values.append(json.dumps(config_snapshot))
        if version_id is not None:
            updates.append("version_id = ${}")
            values.append(version_id)
        if display_name is not None:
            updates.append("display_name = ${}")
            values.append(display_name)
        if description is not None:
            updates.append("description = ${}")
            values.append(description)
        if set_activated_at:
            updates.append("activated_at = NOW()")
        if set_disabled_at:
            updates.append("disabled_at = NOW()")
        elif set_disabled_at is False and status_value in (
            ShareStatus.ACTIVE.value,
            ShareStatus.DRAFT.value,
        ):
            updates.append("disabled_at = NULL")

        if not updates:
            logger.debug("No updates provided for share %s", share_id)
            row = await conn.fetchrow(
                f"{CompanionShareRepository.BASE_SELECT} WHERE id = $1",
                share_id,
            )
            share = _share_from_row(row)
            assert share is not None
            return share

        assignments = []
        param_index = 1
        for clause in updates:
            if "${}" in clause:
                assignments.append(clause.format(param_index + 1))
                param_index += 1
            else:
                assignments.append(clause)
        set_sql = ", ".join(assignments)
        values.insert(0, share_id)

        row = await conn.fetchrow(
            f"""
            UPDATE companion_shares
            SET {set_sql}
            WHERE id = $1
            RETURNING
                id,
                companion_id,
                owner_id,
                version_id,
                slug,
                status,
                allow_text,
                allow_voice,
                require_auth,
                config_snapshot,
                display_name,
                description,
                created_at,
                updated_at,
                activated_at,
                disabled_at,
                total_sessions,
                total_messages,
                total_voice_sessions,
                last_activity_at
            """,
            *values,
        )
        share = _share_from_row(row)
        assert share is not None
        return share

    @staticmethod
    async def disable(conn: asyncpg.Connection, share_id: UUID) -> CompanionShare:
        return await CompanionShareRepository.update(
            conn,
            share_id,
            status=ShareStatus.DISABLED,
            set_disabled_at=True,
        )

    @staticmethod
    async def increment_totals(
        conn: asyncpg.Connection,
        share_id: UUID,
        *,
        sessions_delta: int = 0,
        message_delta: int = 0,
        voice_sessions_delta: int = 0,
    ) -> None:
        await conn.execute(
            """
            UPDATE companion_shares
            SET
                total_sessions = total_sessions + $2,
                total_messages = total_messages + $3,
                total_voice_sessions = total_voice_sessions + $4,
                last_activity_at = CASE
                    WHEN ($2 <> 0 OR $3 <> 0 OR $4 <> 0) THEN NOW()
                    ELSE last_activity_at
                END
            WHERE id = $1
            """,
            share_id,
            sessions_delta,
            message_delta,
            voice_sessions_delta,
        )


class CompanionShareSessionRepository:
    """Helpers for companion_share_sessions analytics and per-visitor tracking."""

    @staticmethod
    async def record_activity(
        conn: asyncpg.Connection,
        *,
        share_id: UUID,
        visitor_token_hash: bytes,
        conversation_id: UUID | None = None,
        message_delta: int = 0,
        voice_sessions_delta: int = 0,
        now: datetime | None = None,
        max_per_minute: int = 8,
        max_per_hour: int = 60,
        max_per_day: int = 400,
    ) -> bool:
        """Insert or update a visitor record, enforcing simple rate limits.

        Returns ``True`` when a new visitor row is inserted.
        """
        now = now or datetime.now(UTC)
        minute_bucket = now.strftime("%Y-%m-%dT%H:%M")
        hour_bucket = now.strftime("%Y-%m-%dT%H")
        day_bucket = now.strftime("%Y-%m-%d")

        manage_tx = not getattr(conn, "is_in_transaction", lambda: False)()
        tx = conn.transaction() if manage_tx else None
        if tx is not None:
            await tx.start()
        try:
            row = await conn.fetchrow(
                """
                SELECT
                    id,
                    conversation_id,
                    message_count,
                    voice_sessions_started,
                    windowed_message_counts
                FROM companion_share_sessions
                WHERE share_id = $1 AND visitor_token_hash = $2
                FOR UPDATE
                """,
                share_id,
                visitor_token_hash,
            )

            inserted = False
            counts: Dict[str, Dict[str, int]] = {}
            if row:
                stored_counts = row["windowed_message_counts"] or {}
                if isinstance(stored_counts, str):
                    try:
                        stored_counts = json.loads(stored_counts)
                    except Exception:
                        stored_counts = {}
                counts = {
                    bucket: dict(values or {}) for bucket, values in (stored_counts or {}).items()
                }
            else:
                inserted = True

            def _bucket_count(bucket: str, key: str) -> int:
                return counts.get(bucket, {}).get(key, 0)

            if message_delta:
                if _bucket_count("minute", minute_bucket) + message_delta > max_per_minute:
                    raise ShareRateLimitExceeded("per_minute", max_per_minute)
                if _bucket_count("hour", hour_bucket) + message_delta > max_per_hour:
                    raise ShareRateLimitExceeded("per_hour", max_per_hour)
                if _bucket_count("day", day_bucket) + message_delta > max_per_day:
                    raise ShareRateLimitExceeded("per_day", max_per_day)

            def _bump(bucket: str, key: str, delta: int, max_keys: int) -> None:
                if delta == 0:
                    return
                mapping = counts.setdefault(bucket, {})
                mapping[key] = mapping.get(key, 0) + delta
                if len(mapping) > max_keys:
                    for old_key in sorted(mapping.keys())[: len(mapping) - max_keys]:
                        mapping.pop(old_key, None)

            _bump("minute", minute_bucket, message_delta, max_keys=10)
            _bump("hour", hour_bucket, message_delta, max_keys=24)
            _bump("day", day_bucket, message_delta, max_keys=7)

            if inserted:
                await conn.execute(
                    """
                    INSERT INTO companion_share_sessions (
                        share_id,
                        conversation_id,
                        visitor_token_hash,
                        first_seen_at,
                        last_seen_at,
                        updated_at,
                        message_count,
                        voice_sessions_started,
                        windowed_message_counts
                    ) VALUES ($1, $2, $3, $4, $4, $4, $5, $6, $7)
                    """,
                    share_id,
                    conversation_id,
                    visitor_token_hash,
                    now,
                    message_delta,
                    voice_sessions_delta,
                    json.dumps(counts),
                )
                return True

            await conn.execute(
                """
                UPDATE companion_share_sessions
                SET
                    conversation_id = COALESCE($3, conversation_id),
                    last_seen_at = $4,
                    updated_at = $4,
                    message_count = message_count + $5,
                    voice_sessions_started = voice_sessions_started + $6,
                    windowed_message_counts = $7
                WHERE share_id = $1 AND visitor_token_hash = $2
                """,
                share_id,
                visitor_token_hash,
                conversation_id,
                now,
                message_delta,
                voice_sessions_delta,
                json.dumps(counts),
            )
            return False
        except Exception:
            if tx is not None:
                await tx.rollback()
            raise
        finally:
            if tx is not None:
                try:
                    await tx.commit()
                except Exception:
                    pass

    @staticmethod
    async def get_for_share_and_token(
        conn: asyncpg.Connection,
        share_id: UUID,
        visitor_token_hash: bytes,
    ) -> dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT
                id,
                share_id,
                conversation_id,
                visitor_token_hash,
                first_seen_at,
                last_seen_at,
                updated_at,
                message_count,
                voice_sessions_started,
                windowed_message_counts
            FROM companion_share_sessions
            WHERE share_id = $1 AND visitor_token_hash = $2
            """,
            share_id,
            visitor_token_hash,
        )
        return dict(row) if row else None

    @staticmethod
    async def analytics_for_share(
        conn: asyncpg.Connection, share_id: UUID
    ) -> CompanionShareAnalytics:
        row = await conn.fetchrow(
            """
            SELECT
                $1::uuid AS share_id,
                COUNT(*) AS sessions,
                COALESCE(SUM(message_count), 0) AS total_messages,
                COALESCE(SUM(voice_sessions_started), 0) AS total_voice_sessions,
                MAX(last_seen_at) AS last_activity_at
            FROM companion_share_sessions
            WHERE share_id = $1
            """,
            share_id,
        )
        if not row:
            return CompanionShareAnalytics(
                share_id=share_id,
                sessions=0,
                total_messages=0,
                total_voice_sessions=0,
                last_activity_at=None,
            )
        return CompanionShareAnalytics(**dict(row))
