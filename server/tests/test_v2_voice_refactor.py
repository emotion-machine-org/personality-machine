# server/tests/test_v2_voice_refactor.py
"""Tests for the voice refactor.

Tests the modular voice pipeline components, v1 backwards compatibility,
and v2 voice API.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Providers
# ──────────────────────────────────────────────────────────────────────────────


def test_get_voice_id_openai():
    """Test OpenAI voice ID mapping."""
    from app.routers.voice.providers import OPENAI_VOICES, get_voice_id

    # Valid voice name
    assert get_voice_id("openai", "alloy") == "alloy"
    assert get_voice_id("openai", "shimmer") == "shimmer"

    # Invalid voice name falls back to default
    assert get_voice_id("openai", "invalid") == "alloy"


def test_get_voice_id_elevenlabs():
    """Test ElevenLabs voice ID mapping."""
    from app.routers.voice.providers import ELEVENLABS_VOICES, get_voice_id

    # Valid voice name
    assert get_voice_id("elevenlabs", "Sarah") == ELEVENLABS_VOICES["Sarah"]
    assert get_voice_id("elevenlabs", "George") == ELEVENLABS_VOICES["George"]

    # Invalid voice name falls back to default (Sarah)
    assert get_voice_id("elevenlabs", "invalid") == ELEVENLABS_VOICES["Sarah"]


def test_get_voice_id_cartesia():
    """Test Cartesia voice ID mapping."""
    from app.routers.voice.providers import CARTESIA_VOICES, get_voice_id

    # Valid voice name
    assert get_voice_id("cartesia", "Sophie") == CARTESIA_VOICES["Sophie"]
    assert get_voice_id("cartesia", "Griffin") == CARTESIA_VOICES["Griffin"]

    # Invalid voice name falls back to default (Sophie)
    assert get_voice_id("cartesia", "invalid") == CARTESIA_VOICES["Sophie"]


def test_get_all_voice_mappings():
    """Test voice mappings export."""
    from app.routers.voice.providers import get_all_voice_mappings

    mappings = get_all_voice_mappings()
    assert "openai" in mappings
    assert "elevenlabs" in mappings
    assert "cartesia" in mappings

    # OpenAI is a list of voice names
    assert isinstance(mappings["openai"], list)
    assert "alloy" in mappings["openai"]

    # ElevenLabs and Cartesia are lists of dicts with name/id
    assert isinstance(mappings["elevenlabs"], list)
    assert all("name" in v and "id" in v for v in mappings["elevenlabs"])


def test_provider_enums_complete():
    """Test all provider enums are defined."""
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    # STT providers
    assert STTProvider.OPENAI.value == "openai"
    assert STTProvider.DEEPGRAM.value == "deepgram"
    assert STTProvider.ULTRAVOX.value == "ultravox"
    assert STTProvider.CARTESIA.value == "cartesia"

    # LLM providers
    assert LLMProvider.OPENAI_GPT4O.value == "openai-gpt4o"
    assert LLMProvider.OPENAI_GPT4O_MINI.value == "openai-gpt4o-mini"
    assert LLMProvider.CLAUDE_SONNET_4.value == "claude-sonnet-4"
    assert LLMProvider.CLAUDE_SONNET_45.value == "claude-sonnet-4.5"
    assert LLMProvider.GEMINI_25_FLASH.value == "gemini-2.5-flash"

    # TTS providers
    assert TTSProvider.OPENAI.value == "openai"
    assert TTSProvider.ELEVENLABS.value == "elevenlabs"
    assert TTSProvider.CARTESIA.value == "cartesia"


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Models
# ──────────────────────────────────────────────────────────────────────────────


def test_voice_config_defaults():
    """Test VoiceConfig default values."""
    from app.routers.voice.models import PipelineType, VoiceConfig

    config = VoiceConfig()
    assert config.pipeline_type == PipelineType.STT_LLM_TTS
    assert config.voice_name == "alloy"
    assert config.temperature == 0.7


def test_voice_config_stt_llm_tts():
    """Test VoiceConfig with STT-LLM-TTS pipeline."""
    from app.routers.voice.models import PipelineType, VoiceConfig
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    config = VoiceConfig(
        pipeline_type=PipelineType.STT_LLM_TTS,
        stt_provider=STTProvider.DEEPGRAM,
        llm_provider=LLMProvider.CLAUDE_SONNET_4,
        tts_provider=TTSProvider.ELEVENLABS,
        voice_name="Sarah",
    )

    assert config.pipeline_type == PipelineType.STT_LLM_TTS
    assert config.stt_provider == STTProvider.DEEPGRAM
    assert config.llm_provider == LLMProvider.CLAUDE_SONNET_4
    assert config.tts_provider == TTSProvider.ELEVENLABS


def test_session_create_model():
    """Test SessionCreate model."""
    from app.routers.voice.models import SessionCreate

    session = SessionCreate(
        systemPrompt="You are helpful.",
        companionId="123e4567-e89b-12d3-a456-426614174000",
    )

    assert session.system_prompt == "You are helpful."
    assert session.companion_id == "123e4567-e89b-12d3-a456-426614174000"


def test_session_create_with_voice_config():
    """Test SessionCreate with VoiceConfig."""
    from app.routers.voice.models import PipelineType, SessionCreate, VoiceConfig

    config = VoiceConfig(
        pipeline_type=PipelineType.STT_LLM_TTS,
        voice_name="shimmer",
    )

    session = SessionCreate(
        systemPrompt="You are helpful.",
        companionId="123e4567-e89b-12d3-a456-426614174000",
        voiceConfig=config,
    )

    assert session.voice_config is not None
    assert session.voice_config.voice_name == "shimmer"


def test_voice_token_response_model():
    """Test VoiceTokenResponse model."""
    from uuid import uuid4

    from app.routers.voice.models import VoiceTokenResponse

    response = VoiceTokenResponse(
        token="test-token",
        relationship_id=uuid4(),
        expires_in=3600,
        ws_url="wss://example.com/voice",
    )

    assert response.token == "test-token"
    assert response.expires_in == 3600


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Pipeline
# ──────────────────────────────────────────────────────────────────────────────


def test_normalize_voice_config_defaults():
    """Test voice config normalization sets defaults."""
    from app.routers.voice.models import PipelineType, VoiceConfig
    from app.routers.voice.pipeline import normalize_voice_config
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    config = VoiceConfig(pipeline_type=PipelineType.STT_LLM_TTS)
    normalized = normalize_voice_config(config)

    assert normalized.stt_provider == STTProvider.OPENAI
    assert normalized.llm_provider == LLMProvider.OPENAI_GPT4O
    assert normalized.tts_provider == TTSProvider.OPENAI


def test_normalize_voice_config_auto_migrate():
    """Test auto-migration from OpenAI Realtime to STT-LLM-TTS."""
    from app.routers.voice.models import PipelineType, VoiceConfig
    from app.routers.voice.pipeline import normalize_voice_config
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    config = VoiceConfig(pipeline_type=PipelineType.OPENAI_REALTIME)
    normalized = normalize_voice_config(config)

    # Should auto-migrate to STT-LLM-TTS
    assert normalized.pipeline_type == PipelineType.STT_LLM_TTS
    assert normalized.stt_provider == STTProvider.OPENAI
    assert normalized.llm_provider == LLMProvider.OPENAI_GPT4O
    assert normalized.tts_provider == TTSProvider.OPENAI


def test_normalize_voice_config_preserves_custom():
    """Test normalization preserves custom provider settings."""
    from app.routers.voice.models import PipelineType, VoiceConfig
    from app.routers.voice.pipeline import normalize_voice_config
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    config = VoiceConfig(
        pipeline_type=PipelineType.STT_LLM_TTS,
        stt_provider=STTProvider.DEEPGRAM,
        llm_provider=LLMProvider.CLAUDE_SONNET_4,
        tts_provider=TTSProvider.ELEVENLABS,
    )
    normalized = normalize_voice_config(config)

    # Should preserve custom settings
    assert normalized.stt_provider == STTProvider.DEEPGRAM
    assert normalized.llm_provider == LLMProvider.CLAUDE_SONNET_4
    assert normalized.tts_provider == TTSProvider.ELEVENLABS


def test_create_default_voice_config():
    """Test default voice config creation."""
    from app.routers.voice.models import PipelineType
    from app.routers.voice.pipeline import create_default_voice_config
    from app.routers.voice.providers import LLMProvider, STTProvider, TTSProvider

    config = create_default_voice_config()

    assert config.pipeline_type == PipelineType.STT_LLM_TTS
    assert config.voice_name == "alloy"
    assert config.stt_provider == STTProvider.OPENAI
    assert config.llm_provider == LLMProvider.OPENAI_GPT4O
    assert config.tts_provider == TTSProvider.OPENAI


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Serializer (sync wrappers)
# ──────────────────────────────────────────────────────────────────────────────


def test_raw_audio_serializer_passthrough():
    """Test RawAudioSerializer passes audio when not dropping."""
    from pipecat.frames.frames import InputAudioRawFrame

    from app.routers.voice.services import RawAudioSerializer

    serializer = RawAudioSerializer(sample_rate=16000)
    test_audio = b"\x00\x01\x02\x03"

    # Use asyncio.run for the async method
    frame = asyncio.get_event_loop().run_until_complete(serializer.deserialize(test_audio))
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.audio == test_audio


def test_raw_audio_serializer_dropping():
    """Test RawAudioSerializer drops audio when gating enabled."""
    from app.routers.voice.services import RawAudioSerializer

    serializer = RawAudioSerializer(sample_rate=16000)
    serializer.set_drop_input(True)

    test_audio = b"\x00\x01\x02\x03"
    frame = asyncio.get_event_loop().run_until_complete(serializer.deserialize(test_audio))

    assert frame is None
    assert serializer.is_dropping()


def test_raw_audio_serializer_buffer():
    """Test RawAudioSerializer buffers audio during dropping."""
    from app.routers.voice.services import RawAudioSerializer

    serializer = RawAudioSerializer(sample_rate=16000)
    serializer.set_drop_input(True)

    test_audio = b"\x00\x01\x02\x03"
    asyncio.get_event_loop().run_until_complete(serializer.deserialize(test_audio))

    buffered = serializer.drain_buffer()
    assert buffered == test_audio


def test_raw_audio_serializer_activity_callback():
    """Test RawAudioSerializer calls activity callback."""
    from app.routers.voice.services import RawAudioSerializer

    activity_calls = []

    def on_activity(nbytes):
        activity_calls.append(nbytes)

    serializer = RawAudioSerializer(sample_rate=16000, on_audio_activity=on_activity)
    test_audio = b"\x00\x01\x02\x03"

    asyncio.get_event_loop().run_until_complete(serializer.deserialize(test_audio))

    assert len(activity_calls) == 1
    assert activity_calls[0] == 4


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Backwards Compatibility - sessions.py exports
# ──────────────────────────────────────────────────────────────────────────────


def test_sessions_module_backwards_compat():
    """Test that sessions.py exports are still available."""
    # These imports should work for backwards compatibility
    from app.routers.sessions import (
        LLMProvider,
        PipelineType,
        RawAudioSerializer,
        SessionCreate,
        SessionCreated,
        STTProvider,
        TTSProvider,
        VoiceConfig,
        cancel_active_session_tasks,
        create_share_voice_session,
        get_voice_id,
        router,
    )

    assert router is not None
    assert PipelineType.STT_LLM_TTS is not None


def test_sessions_module_voice_mappings():
    """Test voice mappings are exported from sessions.py."""
    from app.routers.sessions import (
        CARTESIA_VOICES,
        ELEVENLABS_VOICES,
        OPENAI_VOICES,
    )

    assert "alloy" in OPENAI_VOICES
    assert "Sarah" in ELEVENLABS_VOICES
    assert "Sophie" in CARTESIA_VOICES


def test_sessions_module_pipeline_type_values():
    """Test PipelineType enum values match expected v1 API contract."""
    from app.routers.sessions import PipelineType

    # These values are part of the v1 API contract
    assert PipelineType.OPENAI_REALTIME.value == "openai-realtime"
    assert PipelineType.STT_LLM_TTS.value == "stt-llm-tts"


def test_sessions_module_share_session_context():
    """Test ShareSessionContext is exported."""
    from uuid import uuid4

    from app.routers.sessions import ShareSessionContext

    ctx = ShareSessionContext(
        share_id=uuid4(),
        visitor_token_hash=b"test",
        conversation_id=uuid4(),
    )

    assert ctx.share_id is not None
    assert ctx.visitor_token_hash == b"test"


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Backwards Compatibility - v1 route structure
# ──────────────────────────────────────────────────────────────────────────────


def test_v1_router_has_expected_routes():
    """Test v1 router has all expected routes."""
    from app.routers.voice.v1 import router

    routes = [r.path for r in router.routes]

    # Routes include the prefix, so check for full paths
    # POST /sessions/
    assert any("/sessions/" in r for r in routes)
    # GET /sessions/voice-mappings
    assert any("voice-mappings" in r for r in routes)
    # PATCH /sessions/{session_id}
    assert any("{session_id}" in r for r in routes)
    # WS /sessions/ws/{session_id}
    assert any("ws/{session_id}" in r for r in routes)


def test_v1_router_prefix():
    """Test v1 router has correct prefix."""
    from app.routers.voice.v1 import router

    assert router.prefix == "/sessions"


def test_sessions_router_same_as_v1():
    """Test sessions.py router is the same as voice.v1 router."""
    from app.routers.sessions import router as sessions_router
    from app.routers.voice.v1 import router as v1_router

    # They should be the same object
    assert sessions_router is v1_router


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Backwards Compatibility - Dashboard API (create_share_voice_session)
# ──────────────────────────────────────────────────────────────────────────────


def test_create_share_voice_session_requires_voice_config():
    """Test create_share_voice_session requires voice_config."""
    from uuid import uuid4

    from app.routers.sessions import SessionCreate, create_share_voice_session

    payload = SessionCreate(
        systemPrompt="Test",
        companionId=str(uuid4()),
        voiceConfig=None,  # No voice config
    )

    with pytest.raises(ValueError, match="voice configuration"):
        create_share_voice_session(
            payload,
            share_id=uuid4(),
            visitor_token_hash=b"test",
            conversation_id=uuid4(),
        )


def test_create_share_voice_session_returns_session_created():
    """Test create_share_voice_session returns SessionCreated."""
    from uuid import uuid4

    from app.routers.sessions import (
        SessionCreate,
        SessionCreated,
        VoiceConfig,
        create_share_voice_session,
    )

    payload = SessionCreate(
        systemPrompt="Test",
        companionId=str(uuid4()),
        voiceConfig=VoiceConfig(),
    )

    result = create_share_voice_session(
        payload,
        share_id=uuid4(),
        visitor_token_hash=b"test",
        conversation_id=uuid4(),
    )

    assert isinstance(result, SessionCreated)
    assert result.id is not None
    assert "ws" in result.ws_url
    assert "?t=" in result.ws_url  # Has token


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: v2 Voice Module
# ──────────────────────────────────────────────────────────────────────────────


def test_voice_module_imports():
    """Test that voice module exports are available."""
    from app.routers.voice import (
        VoiceConfig,
        create_default_voice_config,
        v1_router,
        v2_router,
        voice_connection_manager,
    )

    assert v1_router is not None
    assert v2_router is not None


def test_v2_router_has_expected_routes():
    """Test v2 voice router has all expected routes."""
    from app.routers.voice.v2 import router

    routes = [r.path for r in router.routes]

    # Routes include the prefix, so check for presence in path strings
    # Token endpoints
    assert any("companions/{companion_id}/relationships/{user_id}/voice/token" in r for r in routes)
    assert any("relationships/{relationship_id}/voice/token" in r for r in routes)

    # WebSocket endpoints
    assert any(
        "companions/{companion_id}/relationships/{user_id}/voice/connect" in r for r in routes
    )
    assert any("relationships/{relationship_id}/voice/connect" in r for r in routes)


def test_v2_router_prefix():
    """Test v2 voice router has correct prefix."""
    from app.routers.voice.v2 import router

    assert router.prefix == "/v2"


def test_voice_connection_manager_exists():
    """Test VoiceConnectionManager is exported and initialized."""
    from app.routers.voice import voice_connection_manager

    assert voice_connection_manager is not None
    # Should be empty initially
    assert len(voice_connection_manager._connections) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests: Context Integration
# ──────────────────────────────────────────────────────────────────────────────


def test_voice_context_config():
    """Test VoiceContextConfig dataclass."""
    from uuid import uuid4

    from app.routers.voice.context import VoiceContextConfig

    config = VoiceContextConfig(
        companion_id=uuid4(),
        companion_config=None,
        relationship_id=uuid4(),
        external_user_id="test-user",
        use_layered=True,
        include_memory=True,
        include_knowledge=True,
        include_behaviors=True,
    )

    assert config.use_layered is True
    assert config.include_memory is True


def test_voice_context_config_defaults():
    """Test VoiceContextConfig has voice-optimized defaults.

    Voice should default to:
    - use_classifier=False (skip LLM classifier for low latency)
    - include_memory=True (memory layer runs)
    - include_knowledge=False (off for voice)
    - include_behaviors=False (off for voice)
    """
    from uuid import uuid4

    from app.routers.voice.context import VoiceContextConfig

    # Create config with only required fields
    config = VoiceContextConfig(
        companion_id=uuid4(),
        companion_config=None,
    )

    # Voice-optimized defaults
    assert config.use_classifier is False, "Voice should skip classifier by default"
    assert config.include_memory is True, "Voice should include memory by default"
    assert config.include_knowledge is False, "Voice should skip knowledge by default"
    assert config.include_behaviors is False, "Voice should skip behaviors by default"
    assert config.include_profile is False, "Voice should skip profile by default"
    assert config.use_layered is True, "Voice should use layered context by default"


def test_voice_session_state():
    """Test VoiceSessionState dataclass."""
    from app.routers.voice.context import VoiceSessionState

    state = VoiceSessionState()

    assert state.turn_count == 0
    assert state.context_events == []
    assert state.last_user_message is None


# ──────────────────────────────────────────────────────────────────────────────
# Integration Tests (require running server)
# ──────────────────────────────────────────────────────────────────────────────

# Test credentials (override via env vars if needed)
BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY", "")
TEST_COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID", "")
TEST_USER_ID = os.getenv("TEST_EM_USER_ID", "test-voice-user")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def test_v2_voice_token_endpoint():
    """Test v2 voice token creation."""
    url = f"{BASE_URL}/v2/companions/{TEST_COMPANION_ID}/relationships/{TEST_USER_ID}/voice/token"
    headers = _headers()

    with httpx.Client(timeout=20.0) as client:
        response = client.post(url, headers=headers, json={})
        assert response.status_code == 200

        data = response.json()
        assert "token" in data
        assert "relationship_id" in data
        assert "expires_in" in data
        assert "ws_url" in data


def test_v2_voice_token_with_config():
    """Test v2 voice token with custom voice config."""
    url = f"{BASE_URL}/v2/companions/{TEST_COMPANION_ID}/relationships/{TEST_USER_ID}/voice/token"
    headers = _headers()

    payload = {
        "voiceConfig": {
            "pipeline_type": "stt-llm-tts",
            "stt_provider": "openai",
            "llm_provider": "openai-gpt4o-mini",
            "tts_provider": "openai",
            "voice_name": "shimmer",
        }
    }

    with httpx.Client(timeout=20.0) as client:
        response = client.post(url, headers=headers, json=payload)
        assert response.status_code == 200

        data = response.json()
        assert "token" in data
        assert data["expires_in"] == 3600


def test_voice_mappings_endpoint():
    """Test voice mappings endpoint (v1 API)."""
    url = f"{BASE_URL}/sessions/voice-mappings"

    with httpx.Client(timeout=20.0) as client:
        # This endpoint doesn't require auth
        response = client.get(url)
        assert response.status_code == 200

        data = response.json()
        assert "openai" in data
        assert "elevenlabs" in data
        assert "cartesia" in data


def test_v1_session_create_still_works():
    """Test v1 session create endpoint still works (dashboard API)."""
    # Note: This test would require a valid Clerk JWT, not a project API key
    # Just verify the route exists
    from app.routers.voice.v1 import router

    routes = [r.path for r in router.routes]
    # POST /sessions/ endpoint exists
    assert any("/sessions/" in r for r in routes)
