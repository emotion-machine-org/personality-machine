# server/tests/test_openclaw_voice_e2e.py
"""End-to-end tests for OpenClaw voice integration.

Tests the complete flow:
1. OpenClaw callback endpoint
2. OpenClaw LLM service
3. Cache invalidation
4. Voice provider mappings
5. Companion API with OpenClaw config
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ──────────────────────────────────────────────────────────────────────────────
# Test 1: OpenClaw Callback Endpoint
# ──────────────────────────────────────────────────────────────────────────────


def test_openclaw_callback_endpoint():
    """Test that /openclaw/callback receives and processes responses."""
    from app.routers.voice.openclaw import OpenClawTextResponse, create_pending_request, router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Create a pending request
    task_id = str(uuid4())
    create_pending_request(task_id)

    # Send callback
    response = client.post(
        "/openclaw/callback",
        json={
            "task_id": task_id,
            "status": "completed",
            "response": "Hello from OpenClaw!",
        },
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == task_id
    assert response.json()["received"]


def test_openclaw_callback_resolves_future():
    """Test that callback resolves the pending future."""
    from app.routers.voice.openclaw import (
        OpenClawTextResponse,
        create_pending_request,
        resolve_pending_request,
        router,
    )

    task_id = str(uuid4())
    future = create_pending_request(task_id)

    # Resolve it
    response = OpenClawTextResponse(task_id=task_id, status="completed", response="Test response")
    resolved = resolve_pending_request(task_id, response)

    assert resolved
    assert future.done()
    assert future.result().response == "Test response"


# ──────────────────────────────────────────────────────────────────────────────
# Test 2: Voice Provider Mappings
# ──────────────────────────────────────────────────────────────────────────────


def test_elevenlabs_can_voice():
    """Test that Can voice is mapped correctly."""
    from app.routers.voice.providers import ELEVENLABS_VOICES, get_voice_id

    assert "Can" in ELEVENLABS_VOICES
    assert ELEVENLABS_VOICES["Can"] == "siw1N9V8LmYeEWKyWBxv"

    # Test get_voice_id function
    voice_id = get_voice_id("elevenlabs", "Can")
    assert voice_id == "siw1N9V8LmYeEWKyWBxv"


def test_openclaw_llm_provider_exists():
    """Test that OPENCLAW is a valid LLM provider."""
    from app.routers.voice.providers import LLMProvider

    assert LLMProvider.OPENCLAW.value == "openclaw"


def test_fast_brain_models_configured():
    """Test that fast brain models are configured."""
    from app.routers.voice.providers import FAST_BRAIN_MODELS, LLMProvider

    assert len(FAST_BRAIN_MODELS) > 0
    assert LLMProvider.GEMINI_25_FLASH in FAST_BRAIN_MODELS


def test_openrouter_model_ids():
    """Test OpenRouter model ID mappings."""
    from app.routers.voice.providers import (
        OPENROUTER_MODEL_IDS,
        LLMProvider,
        get_openrouter_model_id,
    )

    assert LLMProvider.GEMINI_25_FLASH in OPENROUTER_MODEL_IDS
    assert get_openrouter_model_id(LLMProvider.GEMINI_25_FLASH) == "google/gemini-2.5-flash"


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: OpenClaw Client
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openclaw_client_send_message():
    """Test OpenClaw client sends messages correctly."""
    from app.routers.voice.openclaw import OpenClawClient, OpenClawConfig

    config = OpenClawConfig(
        enabled=True,
        webhook_url="http://test-openclaw.local/webhook",
        auth_token="test-token",
        session_key="test-session",
    )
    client = OpenClawClient(config)

    # Mock the HTTP client
    with patch.object(client, "_get_client") as mock_get_client:
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_http.post.return_value = mock_response
        mock_get_client.return_value = mock_http

        task_id = await client.send_message(
            message="Hello OpenClaw",
            callback_url="http://callback.local/openclaw/callback",
            context={"user_id": "test-user"},
        )

        assert task_id is not None
        assert len(task_id) == 36  # UUID format

        # Verify the request was made
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert call_args[1]["json"]["message"] == "Hello OpenClaw"
        assert "Authorization" in call_args[1]["headers"]

    await client.close()


# ──────────────────────────────────────────────────────────────────────────────
# Test 4: Process with OpenClaw (full flow)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_with_openclaw_success():
    """Test full OpenClaw processing flow."""
    from app.routers.voice.openclaw import (
        OpenClawClient,
        OpenClawConfig,
        OpenClawTextResponse,
        process_with_openclaw,
        resolve_pending_request,
    )

    config = OpenClawConfig(
        enabled=True,
        webhook_url="http://test.local/webhook",
        auth_token="test",
        session_key="test",
    )
    client = OpenClawClient(config)

    # Mock client.send_message to return a known task_id
    test_task_id = str(uuid4())

    async def mock_send(*args, **kwargs):
        return test_task_id

    with patch.object(client, "send_message", side_effect=mock_send):
        # Start processing in background
        async def process():
            return await process_with_openclaw(
                client=client,
                message="What's the weather?",
                callback_base_url="http://callback.local",
                timeout_seconds=5,
            )

        task = asyncio.create_task(process())

        # Simulate callback arriving
        await asyncio.sleep(0.1)
        response = OpenClawTextResponse(
            task_id=test_task_id, status="completed", response="It's sunny today!"
        )
        resolve_pending_request(test_task_id, response)

        # Get result
        result = await task
        assert result == "It's sunny today!"

    await client.close()


@pytest.mark.asyncio
async def test_process_with_openclaw_timeout():
    """Test OpenClaw processing timeout."""
    from app.routers.voice.openclaw import OpenClawClient, OpenClawConfig, process_with_openclaw

    config = OpenClawConfig(
        enabled=True,
        webhook_url="http://test.local/webhook",
        auth_token="test",
        session_key="test",
    )
    client = OpenClawClient(config)

    with patch.object(client, "send_message", return_value=str(uuid4())):
        # Should timeout since no callback arrives
        result = await process_with_openclaw(
            client=client,
            message="This will timeout",
            callback_base_url="http://callback.local",
            timeout_seconds=0.1,  # Very short timeout
        )

        assert "timed out" in result.lower()

    await client.close()


# ──────────────────────────────────────────────────────────────────────────────
# Test 5: Cache Invalidation
# ──────────────────────────────────────────────────────────────────────────────


def test_cache_invalidation_import():
    """Test that cache invalidation imports are correct."""
    from app.context.hydration.cache_keys import CacheNamespace as HydrationNamespace
    from app.routers.api import CacheNamespace, config_cache_key

    # Verify they're the same
    assert CacheNamespace == HydrationNamespace


def test_config_cache_key_format():
    """Test config cache key format."""
    from uuid import UUID

    from app.context.hydration.cache_keys import config_cache_key

    companion_id = UUID("12345678-1234-5678-1234-567812345678")
    key = config_cache_key(companion_id)

    assert "companion:" in key
    assert str(companion_id) in key


# ──────────────────────────────────────────────────────────────────────────────
# Test 6: Voice Presets Include Can
# ──────────────────────────────────────────────────────────────────────────────


def test_voice_presets_can():
    """Test that Can is in voice presets."""
    from app.services.voice_presets import VOICE_PROVIDER_DEFAULTS

    assert "Can" in VOICE_PROVIDER_DEFAULTS["elevenlabs"]


# ──────────────────────────────────────────────────────────────────────────────
# Test 7: Hot Context (optional feature)
# ──────────────────────────────────────────────────────────────────────────────


def test_hot_context_log_events():
    """Test hot context event logging."""
    import os
    import tempfile

    from app.routers.voice.hot_context import HotContext

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "hot_context.md")
        ctx = HotContext(path=path)

        # Log events using the actual API
        ctx.log_start("task-123", "Hello world")
        ctx.log_ack("task-123", "Got it, processing...")
        ctx.log_done("task-123", "Done!")

        # Check task status
        task_status = ctx.get_task_status("task-123")
        assert task_status is not None
        assert task_status.task_id == "task-123"
        assert task_status.status == "done"
        assert task_status.result == "Done!"


def test_hot_context_pending_tasks():
    """Test tracking pending tasks."""
    import os
    import tempfile

    from app.routers.voice.hot_context import HotContext

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "hot_context.md")
        ctx = HotContext(path=path)

        # Start multiple tasks
        ctx.log_start("task-1", "First task")
        ctx.log_start("task-2", "Second task")
        ctx.log_done("task-1", "First done")

        # Check pending
        pending = ctx.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0].task_id == "task-2"


# ──────────────────────────────────────────────────────────────────────────────
# Test 8: OpenClaw LLM Service
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openclaw_llm_service_build():
    """Test building OpenClaw LLM service."""
    from app.routers.voice.openclaw import OpenClawConfig
    from app.routers.voice.openclaw_llm import build_openclaw_llm_service

    config = OpenClawConfig(
        enabled=True,
        webhook_url="http://test.local/webhook",
        auth_token="test",
        session_key="test",
    )

    service = build_openclaw_llm_service(
        config=config,
        callback_base_url="http://callback.local",
        companion_id="comp-123",
        user_id="user-456",
    )

    assert service is not None
    assert service.companion_id == "comp-123"
    assert service.user_id == "user-456"

    await service.close()


# ──────────────────────────────────────────────────────────────────────────────
# Run all tests
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
