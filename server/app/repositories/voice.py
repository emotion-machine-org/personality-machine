import logging
from typing import List
from uuid import UUID

import asyncpg

from ..models.companion import Voice

logger = logging.getLogger(__name__)


class VoiceRepository:
    """Repository for voice catalog operations"""

    @staticmethod
    async def get_all_voices(conn: asyncpg.Connection) -> List[Voice]:
        """Get all available voices"""
        query = """
            SELECT id, name, provider, provider_key, created_at
            FROM voices
            ORDER BY provider, name
        """

        rows = await conn.fetch(query)
        return [Voice(**dict(row)) for row in rows]

    @staticmethod
    async def get_voice_by_id(conn: asyncpg.Connection, voice_id: UUID) -> Voice | None:
        """Get voice by ID"""
        query = """
            SELECT id, name, provider, provider_key, created_at
            FROM voices
            WHERE id = $1
        """
        row = await conn.fetchrow(query, voice_id)
        return Voice(**dict(row)) if row else None

    @staticmethod
    async def get_voices_by_provider(conn: asyncpg.Connection, provider: str) -> List[Voice]:
        """Get voices by provider"""
        query = """
            SELECT id, name, provider, provider_key, created_at
            FROM voices
            WHERE provider = $1
            ORDER BY name
        """

        rows = await conn.fetch(query, provider)
        return [Voice(**dict(row)) for row in rows]
