# server/app/routers/voice/services.py
"""Voice service builders for STT, LLM, and TTS.

This module provides factory functions to create Pipecat service instances
based on provider configuration.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pipecat.frames.frames import AudioRawFrame, Frame, InputAudioRawFrame
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.elevenlabs import tts as elevenlabs_tts_module
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transcriptions.language import Language

from .providers import LLMProvider, STTProvider, TTSProvider

# Try to import optional STT services
try:
    from pipecat.services.cartesia.stt import CartesiaSTTService

    CARTESIA_STT_AVAILABLE = True
except ImportError:
    CARTESIA_STT_AVAILABLE = False

try:
    from pipecat.services.ultravox.stt import UltravoxSTTService

    ULTRAVOX_STT_AVAILABLE = True
except ImportError:
    ULTRAVOX_STT_AVAILABLE = False

try:
    from pipecat.services.deepgram.stt import DeepgramSTTService

    DEEPGRAM_STT_AVAILABLE = True
except ImportError:
    DEEPGRAM_STT_AVAILABLE = False

logger = logging.getLogger(__name__)

# Compatibility shim: newer ElevenLabs API supports language_code with
# eleven_multilingual_v2, but some Pipecat versions still exclude it.
try:
    multilingual_models = set(
        getattr(elevenlabs_tts_module, "ELEVENLABS_MULTILINGUAL_MODELS", set())
    )
    if "eleven_multilingual_v2" not in multilingual_models:
        multilingual_models.add("eleven_multilingual_v2")
        elevenlabs_tts_module.ELEVENLABS_MULTILINGUAL_MODELS = multilingual_models
        logger.info("[TTS] Enabled language_code passthrough for eleven_multilingual_v2")
except Exception as e:
    logger.warning("[TTS] Could not patch ElevenLabs multilingual model list: %s", e)

# Default max tokens for LLM completions
DEFAULT_MAX_TOKENS = 4096

# Per-voice speed overrides (ElevenLabs voice_id -> speed)
ELEVENLABS_SPEED_OVERRIDES = {
    "QIhD5ivPGEoYZQDocuHI": 1.2,  # Joseph
}

DEFAULT_ELEVENLABS_MODEL_ID = "eleven_turbo_v2_5"
SUPPORTED_ELEVENLABS_MODEL_IDS = {
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
    "eleven_multilingual_v2",
}

# Per-voice preferred ElevenLabs defaults (can be overridden by explicit workspace settings).
ELEVENLABS_VOICE_PROFILES: dict[str, dict[str, Any]] = {
    # Sando
    "orz8Yrvbzt78sfmUK5hU": {
        "model_id": "eleven_multilingual_v2",
        "speed": 1.07,
        "stability": 0.75,
        "similarity_boost": 1.0,
        "style": 0.32,
        "use_speaker_boost": True,
        "language_code": "en",
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# RawAudioSerializer
# ──────────────────────────────────────────────────────────────────────────────


class RawAudioSerializer(FrameSerializer):
    """Serializer that handles raw PCM audio and supports input gating.

    When input gating is enabled (drop_input=True), incoming client audio is
    ignored to avoid overlapping user/assistant turns that cause errors.
    Also buffers a small window of audio for barge-in recovery.
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        on_audio_activity: callable | None = None,
    ):
        super().__init__()
        self._sample_rate = sample_rate
        self._drop_input = False
        self._on_audio_activity = on_audio_activity
        self._buffer = bytearray()
        # Keep up to ~2 seconds of audio for barge-in buffering
        self._buffer_max_bytes = int(sample_rate * 2 * 2)  # 2s * 2 bytes per sample

    def set_drop_input(self, drop: bool) -> None:
        """Enable or disable input dropping."""
        self._drop_input = drop

    def is_dropping(self) -> bool:
        """Check if input is currently being dropped."""
        return self._drop_input

    def drain_buffer(self) -> bytes:
        """Drain and return buffered audio data."""
        data = bytes(self._buffer)
        self._buffer.clear()
        return data

    async def serialize(self, frame: Frame) -> bytes | None:
        """Serialize audio frame to bytes."""
        if isinstance(frame, AudioRawFrame):
            return frame.audio
        return None

    async def deserialize(self, data: bytes) -> Frame | None:
        """Deserialize bytes to audio frame, with gating support."""
        try:
            # Track audio activity as soon as bytes arrive
            if self._on_audio_activity is not None:
                try:
                    self._on_audio_activity(len(data))
                except Exception:
                    pass

            if self._drop_input:
                # During assistant speech, buffer a small window for barge-in recovery
                if len(self._buffer) < self._buffer_max_bytes:
                    space = self._buffer_max_bytes - len(self._buffer)
                    self._buffer += data[:space]
                logger.debug(
                    f"[SERIALIZER] Dropping {len(data)} bytes (assistant speaking), "
                    f"buffered={len(self._buffer)}"
                )
                return None

            return InputAudioRawFrame(
                audio=data,
                sample_rate=self._sample_rate,
                num_channels=1,
            )
        except Exception as e:
            logger.error(f"[SERIALIZER] Error deserializing audio: {e}")
            return None


# ──────────────────────────────────────────────────────────────────────────────
# STT Service Builder
# ──────────────────────────────────────────────────────────────────────────────


def build_stt_service(provider: STTProvider):
    """Build STT service based on provider.

    Args:
        provider: STT provider enum value

    Returns:
        Pipecat STT service instance

    Raises:
        ValueError: If provider is not supported
    """
    if provider == STTProvider.OPENAI:
        return OpenAISTTService(api_key=os.getenv("OPENAI_API_KEY"))

    elif provider == STTProvider.DEEPGRAM:
        if not DEEPGRAM_STT_AVAILABLE:
            logger.warning("Deepgram STT not available, falling back to OpenAI STT")
            return OpenAISTTService(api_key=os.getenv("OPENAI_API_KEY"))
        return DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))

    elif provider == STTProvider.ULTRAVOX:
        if not ULTRAVOX_STT_AVAILABLE:
            logger.warning("Ultravox STT not available, falling back to OpenAI STT")
            return OpenAISTTService(api_key=os.getenv("OPENAI_API_KEY"))
        return UltravoxSTTService(api_key=os.getenv("ULTRAVOX_API_KEY"))

    elif provider == STTProvider.CARTESIA:
        if not CARTESIA_STT_AVAILABLE:
            logger.warning("Cartesia STT not available, falling back to OpenAI STT")
            return OpenAISTTService(api_key=os.getenv("OPENAI_API_KEY"))
        return CartesiaSTTService(api_key=os.getenv("CARTESIA_API_KEY"))

    else:
        raise ValueError(f"Unsupported STT provider: {provider}")


# ──────────────────────────────────────────────────────────────────────────────
# LLM Service Builder
# ──────────────────────────────────────────────────────────────────────────────


def build_llm_service(
    provider: LLMProvider,
    system_prompt: str,
    temperature: float,
    max_tokens: int = DEFAULT_MAX_TOKENS,
):
    """Build LLM service based on provider.

    Args:
        provider: LLM provider enum value
        system_prompt: System prompt for the LLM
        temperature: Temperature for generation
        max_tokens: Maximum tokens for completion

    Returns:
        Pipecat LLM service instance

    Raises:
        ValueError: If provider is not supported
    """
    # OpenAI models
    if provider == LLMProvider.OPENAI_GPT4O:
        return OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY"),
            model="gpt-4o",
            system_prompt=system_prompt,
            params=OpenAILLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

    elif provider == LLMProvider.OPENAI_GPT4O_MINI:
        return OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY"),
            model="gpt-4o-mini",
            system_prompt=system_prompt,
            params=OpenAILLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

    elif provider == LLMProvider.OPENAI_GPT51:
        return OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY"),
            model="gpt-5.1",
            system_prompt=system_prompt,
            params=OpenAILLMService.InputParams(
                temperature=temperature,
                max_completion_tokens=max_tokens,  # gpt-5.1 requires max_completion_tokens
            ),
        )

    openrouter_model: str | None = None
    if provider in (LLMProvider.CLAUDE_SONNET_37, LLMProvider.CLAUDE_SONNET_35):
        openrouter_model = "anthropic/claude-3.7-sonnet"
    elif provider == LLMProvider.CLAUDE_HAIKU_45:
        openrouter_model = "anthropic/claude-haiku-4.5"
    elif provider == LLMProvider.CLAUDE_SONNET_4:
        openrouter_model = "anthropic/claude-sonnet-4"
    elif provider == LLMProvider.CLAUDE_SONNET_45:
        openrouter_model = "anthropic/claude-sonnet-4.5"
    elif provider == LLMProvider.CLAUDE_SONNET_46:
        openrouter_model = "anthropic/claude-sonnet-4.6"
    elif provider == LLMProvider.CLAUDE_OPUS_4:
        openrouter_model = "anthropic/claude-opus-4"
    elif provider == LLMProvider.CLAUDE_OPUS_45:
        openrouter_model = "anthropic/claude-opus-4.5"
    elif provider == LLMProvider.CLAUDE_OPUS_46:
        openrouter_model = "anthropic/claude-opus-4.6"
    elif provider in (LLMProvider.GEMINI, LLMProvider.GEMINI_25_FLASH):
        openrouter_model = "google/gemini-2.5-flash"
    elif provider == LLMProvider.GEMINI_3_FLASH:
        openrouter_model = "google/gemini-3-flash-preview"
    elif provider == LLMProvider.GEMINI_31_FLASH_LITE:
        openrouter_model = "google/gemini-3.1-flash-lite-preview"
    elif provider == LLMProvider.MOONSHOT_KIMI_K2:
        openrouter_model = "moonshotai/kimi-k2-0905"
    elif provider == LLMProvider.MOONSHOT_KIMI_K25:
        openrouter_model = "moonshotai/kimi-k2.5"

    if openrouter_model:
        return OpenAILLMService(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            model=openrouter_model,
            system_prompt=system_prompt,
            params=OpenAILLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

    # Local vLLM
    elif provider == LLMProvider.LOCAL_VLLM_QWEN:
        return OpenAILLMService(
            api_key=os.getenv("VLLM_API_KEY"),
            base_url=os.getenv("VLLM_URL"),
            model="Qwen/QwQ-32B-AWQ",
            system_prompt=system_prompt,
            params=OpenAILLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

    # OpenClaw external brain - requires special configuration
    elif provider == LLMProvider.OPENCLAW:
        raise ValueError(
            "OpenClaw LLM provider requires additional configuration. "
            "Use build_voice_pipeline() with openclaw_config parameter, or "
            "use build_openclaw_llm_service() from app.routers.voice.openclaw_llm directly."
        )

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


# ──────────────────────────────────────────────────────────────────────────────
# TTS Service Builder
# ──────────────────────────────────────────────────────────────────────────────


def _coerce_elevenlabs_language(language_code: str | None) -> Language | None:
    if not language_code:
        return None
    code = language_code.strip()
    if not code:
        return None
    # Normalize common formats like en-us -> en-US
    if "-" in code:
        parts = code.split("-", maxsplit=1)
        if len(parts) == 2 and parts[0] and parts[1]:
            code = f"{parts[0].lower()}-{parts[1].upper()}"
    else:
        code = code.lower()
    try:
        return Language(code)
    except ValueError:
        return None


def _coerce_float(value: Any, min_value: float, max_value: float) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < min_value:
        return min_value
    if result > max_value:
        return max_value
    return result


def _normalize_elevenlabs_model_id(value: Any) -> str:
    candidate = str(value).strip() if value is not None else ""
    if candidate in SUPPORTED_ELEVENLABS_MODEL_IDS:
        return candidate
    return DEFAULT_ELEVENLABS_MODEL_ID


def build_tts_service(
    provider: TTSProvider,
    voice_id: str | None = None,
    *,
    elevenlabs_model_id: str | None = None,
    elevenlabs_settings: dict[str, Any] | None = None,
):
    """Build TTS service based on provider.

    Args:
        provider: TTS provider enum value
        voice_id: Voice ID for the TTS service

    Returns:
        Pipecat TTS service instance

    Raises:
        ValueError: If provider is not supported
    """
    if provider == TTSProvider.OPENAI:
        return OpenAITTSService(
            api_key=os.getenv("OPENAI_API_KEY"),
            voice=voice_id or "alloy",
        )

    elif provider == TTSProvider.ELEVENLABS:
        profile = ELEVENLABS_VOICE_PROFILES.get(voice_id or "", {})
        model_id = (
            elevenlabs_model_id
            if elevenlabs_model_id in SUPPORTED_ELEVENLABS_MODEL_IDS
            else _normalize_elevenlabs_model_id(profile.get("model_id"))
        )
        cfg = elevenlabs_settings or {}
        speed_override = ELEVENLABS_SPEED_OVERRIDES.get(voice_id or "")
        speed = _coerce_float(cfg.get("speed"), 0.7, 1.2)
        if speed is None:
            speed = _coerce_float(profile.get("speed"), 0.7, 1.2)
        if speed is None:
            speed = speed_override
        stability = _coerce_float(cfg.get("stability"), 0.0, 1.0)
        if stability is None:
            stability = _coerce_float(profile.get("stability"), 0.0, 1.0)
        similarity_boost = _coerce_float(cfg.get("similarity_boost"), 0.0, 1.0)
        if similarity_boost is None:
            similarity_boost = _coerce_float(profile.get("similarity_boost"), 0.0, 1.0)
        style = _coerce_float(cfg.get("style"), 0.0, 1.0)
        if style is None:
            style = _coerce_float(profile.get("style"), 0.0, 1.0)
        language_code = (
            cfg.get("language_code")
            if isinstance(cfg.get("language_code"), str)
            else profile.get("language_code")
        )
        language = _coerce_elevenlabs_language(
            str(language_code) if language_code is not None else None
        )
        use_speaker_boost = (
            bool(cfg.get("use_speaker_boost"))
            if cfg.get("use_speaker_boost") is not None
            else (
                bool(profile.get("use_speaker_boost"))
                if profile.get("use_speaker_boost") is not None
                else None
            )
        )
        logger.info(
            "[TTS] Building ElevenLabs TTS service with voice_id=%s model=%s",
            voice_id,
            model_id,
        )
        return ElevenLabsTTSService(
            api_key=os.getenv("ELEVEN_API_KEY"),
            voice_id=voice_id or "pNInz6obpgDQGcFmaJgB",
            model=model_id,
            params=ElevenLabsTTSService.InputParams(
                language=language,
                stability=stability if stability is not None else 0.7,
                similarity_boost=similarity_boost if similarity_boost is not None else 0.8,
                style=style if style is not None else 0.5,
                use_speaker_boost=use_speaker_boost if use_speaker_boost is not None else True,
                speed=speed,
            ),
        )

    elif provider == TTSProvider.CARTESIA:
        return CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY"),
            voice_id=voice_id or "c8605446-247c-4d4b-bf81-00fb84e3c7e0",
            model="sonic-2",
        )

    else:
        raise ValueError(f"Unsupported TTS provider: {provider}")
