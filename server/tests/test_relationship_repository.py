from uuid import uuid4

import asyncpg

from app.repositories.relationship_repository import RelationshipRepository


async def test_ensure_exists_handles_concurrent_create_race(monkeypatch):
    companion_id = uuid4()
    existing = object()
    calls = {"get": 0, "create": 0}

    async def get_by_companion_and_user(_conn, *, companion_id, user_id):
        calls["get"] += 1
        return None if calls["get"] == 1 else existing

    async def create(_conn, **_kwargs):
        calls["create"] += 1
        raise asyncpg.UniqueViolationError("duplicate relationship")

    monkeypatch.setattr(
        RelationshipRepository,
        "get_by_companion_and_user",
        get_by_companion_and_user,
    )
    monkeypatch.setattr(RelationshipRepository, "create", create)

    relationship, created = await RelationshipRepository.ensure_exists(
        object(),
        companion_id=companion_id,
        user_id="user-1",
        profile={},
    )

    assert relationship is existing
    assert created is False
    assert calls == {"get": 2, "create": 1}
