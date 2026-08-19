# server/app/routers/voice/providers.py
"""Voice provider enums and voice ID mappings.

This module defines the available STT, LLM, and TTS providers,
along with voice ID mappings for each TTS provider.
"""

from __future__ import annotations

from enum import Enum

# ──────────────────────────────────────────────────────────────────────────────
# Provider Enums
# ──────────────────────────────────────────────────────────────────────────────


class STTProvider(str, Enum):
    """Speech-to-text provider options."""

    OPENAI = "openai"
    ULTRAVOX = "ultravox"
    CARTESIA = "cartesia"
    DEEPGRAM = "deepgram"


class LLMProvider(str, Enum):
    """LLM provider options for voice pipeline."""

    # OpenAI
    OPENAI_GPT4O = "openai-gpt4o"
    OPENAI_GPT4O_MINI = "openai-gpt4o-mini"
    OPENAI_GPT51 = "openai-gpt5.1"

    # Anthropic
    CLAUDE_SONNET_37 = "claude-sonnet-3.7"
    CLAUDE_SONNET_35 = "claude-sonnet-3.5"  # Deprecated alias for 3.7
    CLAUDE_HAIKU_45 = "claude-haiku-4.5"
    CLAUDE_SONNET_4 = "claude-sonnet-4"
    CLAUDE_SONNET_45 = "claude-sonnet-4.5"
    CLAUDE_SONNET_46 = "claude-sonnet-4.6"
    CLAUDE_OPUS_4 = "claude-opus-4"
    CLAUDE_OPUS_45 = "claude-opus-4.5"
    CLAUDE_OPUS_46 = "claude-opus-4.6"

    # Google Gemini (via OpenRouter or direct)
    GEMINI = "gemini"
    GEMINI_25_FLASH = "gemini-2.5-flash"  # google/gemini-2.5-flash
    GEMINI_3_FLASH = "gemini-3-flash"  # google/gemini-3-flash-preview
    GEMINI_31_FLASH_LITE = "gemini-3.1-flash-lite-preview"  # google/gemini-3.1-flash-lite-preview

    # Moonshot Kimi (via OpenRouter)
    MOONSHOT_KIMI_K2 = "moonshot-kimi-k2"  # moonshotai/kimi-k2
    MOONSHOT_KIMI_K25 = "moonshot-kimi-k2.5"  # moonshotai/kimi-k2.5

    # Local / Self-hosted
    LOCAL_VLLM_QWEN = "local-vllm-qwen"

    # External brain via OpenClaw webhook
    OPENCLAW = "openclaw"

    # Fast Brain / Slow Brain pattern (Gemini Flash + OpenClaw)
    FAST_BRAIN = "fast-brain"


class TTSProvider(str, Enum):
    """Text-to-speech provider options."""

    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    CARTESIA = "cartesia"


# ──────────────────────────────────────────────────────────────────────────────
# Voice ID Mappings
# ──────────────────────────────────────────────────────────────────────────────

# OpenAI voices (used directly as voice names)
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"]

# ElevenLabs voices with their actual voice IDs
ELEVENLABS_VOICES = {
    "Sarah": "EXAVITQu4vr4xnSDxMaL",
    "George": "JBFqnCBsd6RMkjVDRZzb",
    "Callum": "N2lVS1w4EtoT3dr4eOWO",
    "Brad": "MYiFAKeVwcvm4z9VsFAR",
    "Joseph": "QIhD5ivPGEoYZQDocuHI",
    "Charlotte": "XB0fDUnXU5powFXDhCwa",
    "Matilda": "XrExE9yKIg1WjnnlVkGX",
    "Will": "bIHbv24MWmeRgasZH58o",
    "Can": "siw1N9V8LmYeEWKyWBxv",  # OpenClaw agent voice
    "Sando": "orz8Yrvbzt78sfmUK5hU",
}

# Cartesia voices with their actual voice IDs
CARTESIA_VOICES = {
    "Sophie": "bf0a246a-8642-498a-9950-80c35e9276b5",
    "Savannah": "78ab82d5-25be-4f7d-82b3-7ad64e5b85b2",
    "Brooke": "6f84f4b8-58a2-430c-8c79-688dad597532",
    "Griffin": "c99d36f3-5ffd-4253-803a-535c1bc9c306",
    "Zia": "32b3f3c5-7171-46aa-abe7-b598964aa793",
    "Carson": "4df027cb-2920-4a1f-8c34-f21529d5c3fe",
    "Wise Lady": "c8605446-247c-4d4b-bf81-00fb84e3c7e0",
    "Ethan": "00967b2f-88a6-4a31-8153-110a92134b9f",
}

# Default voice IDs per provider
DEFAULT_VOICE_IDS = {
    "openai": "alloy",
    "elevenlabs": "EXAVITQu4vr4xnSDxMaL",  # Sarah
    "cartesia": "bf0a246a-8642-498a-9950-80c35e9276b5",  # Sophie
}


def get_voice_id(provider: str, voice_name: str) -> str:
    """Map voice name to provider-specific voice ID.

    Args:
        provider: TTS provider name (openai, elevenlabs, cartesia)
        voice_name: Human-readable voice name

    Returns:
        Provider-specific voice ID
    """
    provider = provider.lower()

    if provider == "openai":
        # OpenAI uses voice names directly
        return voice_name if voice_name in OPENAI_VOICES else DEFAULT_VOICE_IDS["openai"]

    elif provider == "elevenlabs":
        return ELEVENLABS_VOICES.get(voice_name, DEFAULT_VOICE_IDS["elevenlabs"])

    elif provider == "cartesia":
        return CARTESIA_VOICES.get(voice_name, DEFAULT_VOICE_IDS["cartesia"])

    else:
        # Unknown provider, return the voice_name as-is
        return voice_name


# ──────────────────────────────────────────────────────────────────────────────
# LLM Provider → OpenRouter Model ID Mapping
# ──────────────────────────────────────────────────────────────────────────────

OPENROUTER_MODEL_IDS = {
    # Fast Brain candidates (optimized for latency)
    LLMProvider.GEMINI_25_FLASH: "google/gemini-2.5-flash",
    LLMProvider.GEMINI_3_FLASH: "google/gemini-3-flash-preview",
    LLMProvider.GEMINI_31_FLASH_LITE: "google/gemini-3.1-flash-lite-preview",
    LLMProvider.CLAUDE_SONNET_45: "anthropic/claude-sonnet-4.5",
    LLMProvider.CLAUDE_SONNET_46: "anthropic/claude-sonnet-4.6",
    LLMProvider.MOONSHOT_KIMI_K2: "moonshotai/kimi-k2",
    LLMProvider.MOONSHOT_KIMI_K25: "moonshotai/kimi-k2.5",
    # Other models
    LLMProvider.CLAUDE_HAIKU_45: "anthropic/claude-haiku-4.5",
    LLMProvider.CLAUDE_SONNET_4: "anthropic/claude-sonnet-4",
    LLMProvider.CLAUDE_OPUS_4: "anthropic/claude-opus-4",
    LLMProvider.CLAUDE_OPUS_45: "anthropic/claude-opus-4.5",
    LLMProvider.CLAUDE_OPUS_46: "anthropic/claude-opus-4.6",
    LLMProvider.CLAUDE_SONNET_37: "anthropic/claude-3.7-sonnet",
    LLMProvider.OPENAI_GPT4O: "openai/gpt-4o",
    LLMProvider.OPENAI_GPT4O_MINI: "openai/gpt-4o-mini",
    LLMProvider.OPENAI_GPT51: "openai/gpt-5.1",
}

# Recommended fast brain models (sorted by latency, fastest first)
FAST_BRAIN_MODELS = [
    LLMProvider.GEMINI_25_FLASH,  # ~150-300ms, cheapest
    LLMProvider.GEMINI_3_FLASH,  # ~150-300ms, newest
    LLMProvider.CLAUDE_SONNET_45,  # ~300-500ms, best quality
    LLMProvider.MOONSHOT_KIMI_K2,  # ~400-600ms, good multilingual
    LLMProvider.MOONSHOT_KIMI_K25,  # ~400-600ms, multimodal
]


def get_openrouter_model_id(provider: LLMProvider) -> str:
    """Get the OpenRouter model ID for a given LLM provider."""
    return OPENROUTER_MODEL_IDS.get(provider, provider.value)


def get_all_voice_mappings() -> dict:
    """Get all available voice mappings for all providers.

    Returns:
        Dictionary with provider names as keys and voice info as values.
    """
    return {
        "openai": OPENAI_VOICES,
        "elevenlabs": [
            {"name": name, "id": voice_id} for name, voice_id in ELEVENLABS_VOICES.items()
        ],
        "cartesia": [{"name": name, "id": voice_id} for name, voice_id in CARTESIA_VOICES.items()],
    }
