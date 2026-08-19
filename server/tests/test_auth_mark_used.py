import asyncio
from uuid import uuid4

import pytest

from app import auth


class DummyConnection:
    pass


class DummyConnectionContext:
    async def __aenter__(self):
        return DummyConnection()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def clear_mark_used_state():
    auth._api_key_mark_used_inflight.clear()
    auth._api_key_mark_used_last_scheduled.clear()
    yield
    auth._api_key_mark_used_inflight.clear()
    auth._api_key_mark_used_last_scheduled.clear()


async def test_schedule_mark_api_key_used_coalesces_burst(monkeypatch):
    key_id = uuid4()
    calls = []

    async def mark_used(_conn, api_key_id):
        calls.append(api_key_id)

    monkeypatch.setattr(auth, "get_db_connection", lambda: DummyConnectionContext())
    monkeypatch.setattr(auth.ProjectApiKeyRepository, "mark_used", mark_used)
    monkeypatch.setattr(auth, "_API_KEY_MARK_USED_TTL_S", 60.0)

    scheduled = await asyncio.gather(*(auth._schedule_mark_api_key_used(key_id) for _ in range(12)))

    await asyncio.sleep(0.01)

    assert scheduled.count(True) == 1
    assert scheduled.count(False) == 11
    assert calls == [key_id]


async def test_background_mark_api_key_used_timeout_releases_inflight(monkeypatch):
    key_id = uuid4()

    async def mark_used(_conn, _api_key_id):
        await asyncio.sleep(1)

    monkeypatch.setattr(auth, "get_db_connection", lambda: DummyConnectionContext())
    monkeypatch.setattr(auth.ProjectApiKeyRepository, "mark_used", mark_used)
    monkeypatch.setattr(auth, "_API_KEY_MARK_USED_TIMEOUT_S", 0.01)

    auth._api_key_mark_used_inflight.add(key_id)

    await auth._background_mark_api_key_used(key_id)

    assert key_id not in auth._api_key_mark_used_inflight
