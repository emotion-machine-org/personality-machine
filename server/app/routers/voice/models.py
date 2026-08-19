# server/app/routers/voice/models.py
"""Pydantic models for voice sessions.

This module defines the request/response models for voice API endpoints.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from .providers import LLMProvider, STTProvider, TTSProvider


class PipelineType(str, Enum):
    """Voice pipeline type.

    Only STT_LLM_TTS is supported. OPENAI_REALTIME is kept for backwards
    compatibility parsing only - it will be silently converted to STT_LLM_TTS.
    """

    OPENAI_REALTIME = "openai-realtime"  # Legacy, auto-converted to STT_LLM_TTS
    STT_LLM_TTS = "stt-llm-tts"


class VoiceConfig(BaseModel):
    """Voice pipeline configuration for real-time voice sessions.

    Uses STT→LLM→TTS pipeline with configurable providers.
    """

    pipeline_type: PipelineType = Field(
        default=PipelineType.STT_LLM_TTS,
        description="Pipeline type. Use 'stt-llm-tts'.",
    )
    voice_name: str | None = Field(
        default="alloy",
        description="Voice name. OpenAI: alloy, ash, ballad, coral, echo, sage, shimmer, verse. "
        "ElevenLabs: Sarah, George, etc. Cartesia: Sophie, Savannah, etc.",
    )
    openai_voice: str | None = Field(
        default=None,
        description="[Legacy] OpenAI voice ID. Use voice_name instead.",
    )
    stt_provider: STTProvider | None = Field(
        default=STTProvider.OPENAI,
        description="Speech-to-text provider: openai, deepgram, ultravox, cartesia",
    )
    llm_provider: LLMProvider | None = Field(
        default=LLMProvider.OPENAI_GPT4O,
        description="LLM provider: openai-gpt4o, openai-gpt4o-mini, claude-sonnet-4, etc.",
    )
    fast_brain_model_provider: LLMProvider | None = Field(
        default=None,
        description=(
            "Optional underlying model for Fast Brain routing. Used when llm_provider=fast-brain."
        ),
    )
    tts_provider: TTSProvider | None = Field(
        default=TTSProvider.OPENAI,
        description="Text-to-speech provider: openai, elevenlabs, cartesia",
    )
    tts_voice_id: str | None = Field(
        default=None,
        description="TTS voice ID. Usually auto-derived from voice_name + tts_provider.",
    )
    elevenlabs_model_id: str | None = Field(
        default=None,
        description="Optional ElevenLabs model ID override (e.g., eleven_turbo_v2_5).",
    )
    elevenlabs_stability: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional ElevenLabs stability override.",
    )
    elevenlabs_similarity_boost: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional ElevenLabs similarity boost override.",
    )
    elevenlabs_style: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional ElevenLabs style exaggeration override.",
    )
    elevenlabs_speed: float | None = Field(
        default=None,
        ge=0.7,
        le=1.2,
        description="Optional ElevenLabs speed override.",
    )
    elevenlabs_use_speaker_boost: bool | None = Field(
        default=None,
        description="Optional ElevenLabs speaker boost override.",
    )
    elevenlabs_language_code: str | None = Field(
        default=None,
        description="Optional ElevenLabs language code override (e.g., en).",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="LLM temperature (0.0-2.0). Default: 0.7",
    )
    background_noise_enabled: bool = Field(
        default=False,
        description="Enable subtle background ambience in assistant audio output.",
    )
    background_noise_type: str | None = Field(
        default=None,
        description="Background ambience preset key (for example: restaurant_chatter).",
    )
    background_noise_volume: float = Field(
        default=0.12,
        ge=0.0,
        le=1.0,
        description="Background ambience volume multiplier (0.0-1.0).",
    )
    fast_brain_delegate_enabled: bool = Field(
        default=True,
        description="Allow Fast Brain to delegate tasks to Slow Brain/OpenClaw.",
    )
    fast_brain_end_call_enabled: bool = Field(
        default=False,
        description="Allow Fast Brain to request ending the call after a final assistant response.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# v1 API Models
# ──────────────────────────────────────────────────────────────────────────────


class SessionCreate(BaseModel):
    """Request model for creating a v1 voice session."""

    system_prompt: str = Field(
        ...,
        alias="systemPrompt",
        description="LLM instructions / persona seed",
    )
    companion_id: str = Field(
        ...,
        alias="companionId",
        description="ID of the companion being tested",
    )
    conversation_id: str | None = Field(
        None,
        alias="conversationId",
        description="Optional ID of existing conversation to connect to",
    )
    voice: str | None = Field(
        None,
        description="OpenAI voice id (legacy)",
    )
    voice_config: VoiceConfig | None = Field(
        None,
        alias="voiceConfig",
    )
    client_external_user_id: str | None = Field(
        None,
        alias="clientExternalUserId",
        description="Optional client-provided stable external user id (for builder sessions)",
    )
    context_mode: str | None = Field(
        None,
        alias="contextMode",
        description="Override context engine mode: 'layered' or 'legacy'",
    )


class SessionCreated(BaseModel):
    """Response model for created v1 voice session."""

    id: str
    ws_url: str


# ──────────────────────────────────────────────────────────────────────────────
# v2 API Models
# ──────────────────────────────────────────────────────────────────────────────


class VoiceTokenRequest(BaseModel):
    """Request model for creating a v2 voice WebSocket token."""

    voice_config: VoiceConfig | None = Field(
        None,
        alias="voiceConfig",
        description="Voice configuration. Defaults to STT-LLM-TTS with OpenAI providers.",
    )
    clear_profile: bool = Field(
        False,
        alias="clearProfile",
        description="Clear the relationship profile before starting. Useful for onboarding flows.",
    )
    clear_messages: bool = Field(
        False,
        alias="clearMessages",
        description="Clear all conversation history before starting. Useful for onboarding flows.",
    )


class VoiceTokenResponse(BaseModel):
    """Response model for v2 voice WebSocket token."""

    token: str = Field(
        ...,
        description="JWT token for WebSocket authentication",
    )
    relationship_id: UUID = Field(
        ...,
        description="ID of the relationship this token is scoped to",
    )
    expires_in: int = Field(
        ...,
        description="Token expiry time in seconds",
    )
    ws_url: str = Field(
        ...,
        description="WebSocket URL to connect to",
    )


class VoiceTurnConfig(BaseModel):
    """Per-turn configuration for voice messages."""

    model: str | None = Field(
        None,
        description="Override LLM model for this turn",
    )
    temperature: float | None = Field(
        None,
        ge=0.0,
        le=2.0,
        description="Override temperature for this turn",
    )
