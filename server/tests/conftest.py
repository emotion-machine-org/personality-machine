"""Pytest configuration: tag integration tests that need external services.

Files listed in LIVE_TEST_FILES require a running API server (EM_BASE_URL,
TEST_EM_API_KEY, ...) and usually a seeded database. Files in MODAL_TEST_FILES
require deployed Modal workers and Modal credentials.

Both groups are excluded by default (see [tool.pytest.ini_options] addopts in
pyproject.toml). Run them explicitly with:

    uv run pytest -m live
    uv run pytest -m modal
"""

from __future__ import annotations

import pytest

LIVE_TEST_FILES = frozenset(
    {
        "test_api_memory_integration.py",
        "test_client_api.py",
        "test_context_engine_endpoints.py",
        "test_context_plan.py",
        "test_encryption.py",
        "test_fast_brain_hot_context_e2e.py",
        "test_layered_runtimes.py",
        "test_memory_v2.py",
        "test_state_crud.py",
        "test_stream_http.py",
        "test_system_prompt_composition.py",
        "test_v1_tools_api.py",
        "test_v2_app_state_injection.py",
        "test_v2_behavior_execution.py",
        "test_v2_behavior_webhooks.py",
        "test_v2_behaviors.py",
        "test_v2_message_history.py",
        "test_v2_messages.py",
        "test_v2_proactive.py",
        "test_v2_relationships.py",
        "test_v2_sdk.py",
        "test_v2_session_state.py",
        "test_v2_sessions.py",
        "test_v2_summaries.py",
        "test_v2_voice_refactor.py",
        "test_v2_websockets.py",
        "test_v2_ws_intro_message.py",
    }
)

MODAL_TEST_FILES = frozenset(
    {
        "test_tools_secrets.py",
        "test_v2_modal_llm_node.py",
    }
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        name = item.path.name
        if name in LIVE_TEST_FILES:
            item.add_marker(pytest.mark.live)
        if name in MODAL_TEST_FILES:
            item.add_marker(pytest.mark.modal)


@pytest.fixture
async def db_conn():
    """Direct asyncpg connection for live DB tests. Skips when DATABASE_DSN is unset."""
    import os

    import asyncpg

    dsn = os.getenv("DATABASE_DSN")
    if not dsn:
        pytest.skip("DATABASE_DSN not set")
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def conn(db_conn):
    """Alias fixture used by some live test files."""
    return db_conn
