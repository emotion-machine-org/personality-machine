# server/tests/test_openclaw_voice.py
"""Tests for OpenClaw voice integration.

Tests the webhook/callback pattern for using OpenClaw as an external brain
in the voice pipeline.

Run with: uv run pytest tests/test_openclaw_voice.py -vv
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.voice.openclaw import (
    OpenClawClient,
    OpenClawConfig,
    OpenClawTextRequest,
    OpenClawTextResponse,
    create_pending_request,
    process_with_openclaw,
    resolve_pending_request,
    router,
)

# ──────────────────────────────────────────────────────────────────────────────
# Test App Setup
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Create test FastAPI app with OpenClaw router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: OpenClawConfig
# ──────────────────────────────────────────────────────────────────────────────


def test_openclaw_config_defaults():
    """Test OpenClawConfig default values."""
    config = OpenClawConfig()
    assert config.enabled is False
    assert config.webhook_url == ""
    assert config.auth_token == ""
    assert config.session_key == ""
    assert config.timeout_seconds == 60


def test_openclaw_config_with_values():
    """Test OpenClawConfig with custom values."""
    config = OpenClawConfig(
        enabled=True,
        webhook_url="https://gateway.openclaw.ai/webhook",
        auth_token="secret-token",
        session_key="voice-tin-can",
        timeout_seconds=30,
    )
    assert config.enabled is True
    assert config.webhook_url == "https://gateway.openclaw.ai/webhook"
    assert config.auth_token == "secret-token"
    assert config.session_key == "voice-tin-can"
    assert config.timeout_seconds == 30


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: OpenClawClient
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_disabled_raises():
    """Test that disabled client raises error."""
    config = OpenClawConfig(enabled=False)
    client = OpenClawClient(config)

    with pytest.raises(Exception) as exc_info:
        await client.send_message(
            message="Hello",
            callback_url="https://example.com/callback",
        )
    assert "not configured" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_client_no_webhook_url_raises():
    """Test that missing webhook URL raises error."""
    config = OpenClawConfig(enabled=True, webhook_url="")
    client = OpenClawClient(config)

    with pytest.raises(Exception) as exc_info:
        await client.send_message(
            message="Hello",
            callback_url="https://example.com/callback",
        )
    assert "not configured" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_client_send_message_success():
    """Test successful message send."""
    config = OpenClawConfig(
        enabled=True,
        webhook_url="https://gateway.openclaw.ai/webhook",
        auth_token="secret",
        session_key="test-session",
    )
    client = OpenClawClient(config)

    # Mock the HTTP client
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.text = '{"status": "processing"}'

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        task_id = await client.send_message(
            message="What's on my calendar?",
            callback_url="https://api.em.ai/openclaw/callback",
            context={"user_id": "test-user"},
        )

        assert task_id is not None
        assert len(task_id) == 36  # UUID format

        # Verify the request was made correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://gateway.openclaw.ai/webhook"
        assert "Authorization" in call_args[1]["headers"]
        assert call_args[1]["headers"]["Authorization"] == "Bearer secret"

    await client.close()


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Callback Endpoint
# ──────────────────────────────────────────────────────────────────────────────


def test_callback_endpoint_success(client):
    """Test callback endpoint accepts response."""
    task_id = str(uuid4())

    response = client.post(
        "/openclaw/callback",
        json={
            "task_id": task_id,
            "status": "completed",
            "response": "I've checked your calendar. You have a meeting at 3pm.",
            "actions_taken": ["calendar.list"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["received"] is True


def test_callback_endpoint_failed_status(client):
    """Test callback endpoint handles failed status."""
    task_id = str(uuid4())

    response = client.post(
        "/openclaw/callback",
        json={
            "task_id": task_id,
            "status": "failed",
            "error": "Connection timeout",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["received"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Pending Request Management
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_request_lifecycle():
    """Test creating and resolving a pending request."""
    task_id = str(uuid4())

    # Create pending request
    future = create_pending_request(task_id)
    assert not future.done()

    # Resolve it
    response = OpenClawTextResponse(
        task_id=task_id,
        status="completed",
        response="Hello from OpenClaw!",
    )
    resolved = resolve_pending_request(task_id, response)

    assert resolved is True
    assert future.done()
    result = future.result()
    assert result.status == "completed"
    assert result.response == "Hello from OpenClaw!"


@pytest.mark.asyncio
async def test_pending_request_unknown_task():
    """Test resolving unknown task returns False."""
    response = OpenClawTextResponse(
        task_id=str(uuid4()),
        status="completed",
        response="Hello",
    )
    resolved = resolve_pending_request("unknown-task-id", response)
    assert resolved is False


# ──────────────────────────────────────────────────────────────────────────────
# Integration Tests: process_with_openclaw
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_with_openclaw_timeout():
    """Test that process_with_openclaw handles timeout."""
    config = OpenClawConfig(
        enabled=True,
        webhook_url="https://gateway.openclaw.ai/webhook",
        session_key="test",
    )
    client = OpenClawClient(config)

    # Mock send_message to return task_id but never call back
    with patch.object(client, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = str(uuid4())

        result = await process_with_openclaw(
            client=client,
            message="Hello",
            callback_base_url="https://api.em.ai",
            timeout_seconds=0.1,  # Very short timeout
        )

        assert "timed out" in result.lower()

    await client.close()


@pytest.mark.asyncio
async def test_process_with_openclaw_success():
    """Test successful process_with_openclaw flow."""
    config = OpenClawConfig(
        enabled=True,
        webhook_url="https://gateway.openclaw.ai/webhook",
        session_key="test",
    )
    client = OpenClawClient(config)

    task_id = str(uuid4())

    # Mock send_message
    with patch.object(client, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = task_id

        # Start processing in background
        async def process():
            return await process_with_openclaw(
                client=client,
                message="Hello",
                callback_base_url="https://api.em.ai",
                timeout_seconds=5,
            )

        # Create task
        task = asyncio.create_task(process())

        # Wait a bit for the pending request to be created
        await asyncio.sleep(0.1)

        # Simulate callback
        response = OpenClawTextResponse(
            task_id=task_id,
            status="completed",
            response="Hello from OpenClaw!",
        )
        resolve_pending_request(task_id, response)

        # Wait for result
        result = await task
        assert result == "Hello from OpenClaw!"

    await client.close()


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: OpenClawLLMService
# ──────────────────────────────────────────────────────────────────────────────


def test_openclaw_llm_service_init():
    """Test OpenClawLLMService initialization."""
    from app.routers.voice.openclaw_llm import OpenClawLLMService

    config = OpenClawConfig(
        enabled=True,
        webhook_url="https://gateway.openclaw.ai/webhook",
        session_key="test",
    )

    service = OpenClawLLMService(
        config=config,
        callback_base_url="https://api.em.ai",
        companion_id="comp-123",
        relationship_id="rel-456",
        user_id="user-789",
    )

    assert service.config == config
    assert service.callback_base_url == "https://api.em.ai"
    assert service.companion_id == "comp-123"
    assert service.relationship_id == "rel-456"
    assert service.user_id == "user-789"


def test_build_openclaw_llm_service():
    """Test build_openclaw_llm_service factory function."""
    from app.routers.voice.openclaw_llm import build_openclaw_llm_service

    config = OpenClawConfig(
        enabled=True,
        webhook_url="https://gateway.openclaw.ai/webhook",
        session_key="test",
    )

    service = build_openclaw_llm_service(
        config=config,
        callback_base_url="https://api.em.ai",
        companion_id="comp-123",
    )

    assert service is not None
    assert service.config == config


# ──────────────────────────────────────────────────────────────────────────────
# Model Tests
# ──────────────────────────────────────────────────────────────────────────────


def test_openclaw_text_request_model():
    """Test OpenClawTextRequest model."""
    request = OpenClawTextRequest(
        task_id="task-123",
        message="What's on my calendar?",
        session_key="voice-tin-can",
        callback_url="https://api.em.ai/openclaw/callback",
        context={"user_id": "test-user", "companion_id": "comp-123"},
    )

    assert request.task_id == "task-123"
    assert request.message == "What's on my calendar?"
    assert request.session_key == "voice-tin-can"
    assert request.callback_url == "https://api.em.ai/openclaw/callback"
    assert request.context == {"user_id": "test-user", "companion_id": "comp-123"}


def test_openclaw_text_response_model():
    """Test OpenClawTextResponse model."""
    response = OpenClawTextResponse(
        task_id="task-123",
        status="completed",
        response="You have a meeting at 3pm with the team.",
        actions_taken=["calendar.list", "calendar.get_event"],
    )

    assert response.task_id == "task-123"
    assert response.status == "completed"
    assert response.response == "You have a meeting at 3pm with the team."
    assert response.actions_taken == ["calendar.list", "calendar.get_event"]


def test_openclaw_text_response_failed():
    """Test OpenClawTextResponse with failed status."""
    response = OpenClawTextResponse(
        task_id="task-123",
        status="failed",
        error="Tool execution failed: rate limited",
    )

    assert response.status == "failed"
    assert response.error == "Tool execution failed: rate limited"
    assert response.response is None


# ──────────────────────────────────────────────────────────────────────────────
# Integration Tests: Pipeline
# ──────────────────────────────────────────────────────────────────────────────


def test_pipeline_openclaw_provider_requires_config():
    """Test that OpenClaw LLM provider requires config."""
    from unittest.mock import MagicMock, patch

    from app.routers.voice.models import PipelineType, VoiceConfig
    from app.routers.voice.pipeline import build_voice_pipeline
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    voice_config = VoiceConfig(
        pipeline_type=PipelineType.STT_LLM_TTS,
        stt_provider=STTProvider.OPENAI,
        llm_provider=LLMProvider.OPENCLAW,
        tts_provider=TTSProvider.OPENAI,
    )

    mock_ws = MagicMock()

    # Mock the service builders to avoid API key requirements
    with (
        patch("app.routers.voice.pipeline.build_stt_service") as mock_stt,
        patch("app.routers.voice.pipeline.build_tts_service") as mock_tts,
    ):
        mock_stt.return_value = MagicMock()
        mock_tts.return_value = MagicMock()

        # Should raise error without openclaw_config
        with pytest.raises(ValueError) as exc_info:
            build_voice_pipeline(
                websocket=mock_ws,
                voice_config=voice_config,
                system_prompt="Test",
            )

        assert "openclaw_config" in str(exc_info.value).lower()


def test_pipeline_openclaw_provider_disabled_raises():
    """Test that disabled OpenClaw config raises error."""
    from unittest.mock import MagicMock, patch

    from app.routers.voice.models import PipelineType, VoiceConfig
    from app.routers.voice.pipeline import build_voice_pipeline
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    voice_config = VoiceConfig(
        pipeline_type=PipelineType.STT_LLM_TTS,
        stt_provider=STTProvider.OPENAI,
        llm_provider=LLMProvider.OPENCLAW,
        tts_provider=TTSProvider.OPENAI,
    )

    openclaw_config = OpenClawConfig(enabled=False)
    mock_ws = MagicMock()

    # Mock the service builders to avoid API key requirements
    with (
        patch("app.routers.voice.pipeline.build_stt_service") as mock_stt,
        patch("app.routers.voice.pipeline.build_tts_service") as mock_tts,
    ):
        mock_stt.return_value = MagicMock()
        mock_tts.return_value = MagicMock()

        # Should raise error with disabled config
        with pytest.raises(ValueError) as exc_info:
            build_voice_pipeline(
                websocket=mock_ws,
                voice_config=voice_config,
                system_prompt="Test",
                openclaw_config=openclaw_config,
            )

        assert "disabled" in str(exc_info.value).lower() or "enabled" in str(exc_info.value).lower()


print("✓ All OpenClaw voice tests passed!")
