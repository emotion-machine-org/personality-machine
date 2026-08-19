from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.repositories.relationship_repository import RelationshipRepository


@pytest.mark.asyncio
async def test_get_message_history_uses_seq_order_for_prompt_context() -> None:
    class FakeConn:
        async def fetch(self, query, *params):
            assert "ORDER BY m.seq DESC" in query
            assert params[1] == 24
            now = datetime(2026, 6, 10, 11, 18, tzinfo=UTC)
            # DB returns newest first; repository returns oldest first.
            return [
                {
                    "role": "user",
                    "content": "why do I have blood clots ?",
                    "created_at": now,
                    "metadata": None,
                    "seq": 2,
                },
                {
                    "role": "assistant",
                    "content": "Hi, what's up?",
                    "created_at": now,
                    "metadata": None,
                    "seq": 1,
                },
            ]

    history = await RelationshipRepository.get_message_history(
        FakeConn(),
        uuid4(),
        limit=24,
    )

    assert [row["seq"] for row in history] == [1, 2]
    assert history[-1]["role"] == "user"
    assert history[-1]["content"] == "why do I have blood clots ?"
