from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from ..models.companion import CompanionConfig

VOICE_PROVIDER_DEFAULTS: Dict[str, list[str]] = {
    "openai": ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"],
    "elevenlabs": [
        "Sarah",
        "George",
        "Callum",
        "Brad",
        "Joseph",
        "Charlotte",
        "Matilda",
        "Will",
        "Can",
    ],
    "cartesia": ["Sophie", "Savannah", "Brooke", "Griffin", "Zia", "Carson", "Wise Lady", "Ethan"],
}

# New short-key preset format
# Each preset defines default_model (for LLM), stt_provider, tts_provider
VOICE_PRESETS: Dict[str, Dict[str, Any]] = {
    # Anthropic + ElevenLabs (Recommended)
    "sonnet-4.5-elevenlabs": {
        "display_name": "Claude Sonnet 4.5",
        "description": "Recommended",
        "default_model": "claude-sonnet-4.5",
        "stt_provider": "cartesia",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
    "sonnet-4-elevenlabs": {
        "display_name": "Claude Sonnet 4",
        "default_model": "claude-sonnet-4",
        "stt_provider": "cartesia",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
    "sonnet-3.7-elevenlabs": {
        "display_name": "Claude Sonnet 3.7",
        "default_model": "claude-sonnet-3.7",
        "stt_provider": "cartesia",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
    # Anthropic + Cartesia
    "sonnet-4.5-cartesia": {
        "display_name": "Claude Sonnet 4.5 + Cartesia",
        "default_model": "claude-sonnet-4.5",
        "stt_provider": "cartesia",
        "tts_provider": "cartesia",
        "voice_provider": "cartesia",
    },
    "sonnet-4-cartesia": {
        "display_name": "Claude Sonnet 4 + Cartesia",
        "default_model": "claude-sonnet-4",
        "stt_provider": "cartesia",
        "tts_provider": "cartesia",
        "voice_provider": "cartesia",
    },
    "sonnet-3.7-cartesia": {
        "display_name": "Claude Sonnet 3.7 + Cartesia",
        "default_model": "claude-sonnet-3.7",
        "stt_provider": "cartesia",
        "tts_provider": "cartesia",
        "voice_provider": "cartesia",
    },
    # OpenAI
    "gpt4o-elevenlabs": {
        "display_name": "GPT-4o",
        "default_model": "openai-gpt4o",
        "stt_provider": "cartesia",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
    "gpt4o-cartesia": {
        "display_name": "GPT-4o + Cartesia",
        "default_model": "openai-gpt4o",
        "stt_provider": "cartesia",
        "tts_provider": "cartesia",
        "voice_provider": "cartesia",
    },
    "gpt5.1-elevenlabs": {
        "display_name": "GPT-5.1",
        "description": "Latest",
        "default_model": "openai-gpt5.1",
        "stt_provider": "cartesia",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
    # Gemini
    "gemini-flash-elevenlabs": {
        "display_name": "Gemini 2.5 Flash",
        "description": "Fast",
        "default_model": "gemini-2.5-flash",
        "stt_provider": "cartesia",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
    # Moonshot
    "kimi-k2-elevenlabs": {
        "display_name": "Kimi K2",
        "default_model": "moonshot-kimi-k2",
        "stt_provider": "cartesia",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
    # Budget
    "gpt4o-mini-elevenlabs": {
        "display_name": "GPT-4o Mini",
        "description": "Budget",
        "default_model": "openai-gpt4o-mini",
        "stt_provider": "deepgram",
        "tts_provider": "elevenlabs",
        "voice_provider": "elevenlabs",
    },
}

# Migration from old preset keys to new short keys
VOICE_PRESET_MIGRATIONS: Dict[str, str] = {
    # Anthropic + ElevenLabs
    "sonnet-4.5 - Anthropic (LLM) - Elevenlabs (STT - TTS)": "sonnet-4.5-elevenlabs",
    "sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)": "sonnet-4.5-elevenlabs",
    "sonnet-4 - Anthropic (LLM) - Elevenlabs (STT - TTS)": "sonnet-4-elevenlabs",
    "sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)": "sonnet-4-elevenlabs",
    "sonnet-3.7 - Anthropic (LLM) - Elevenlabs (STT - TTS)": "sonnet-3.7-elevenlabs",
    "sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)": "sonnet-3.7-elevenlabs",
    # Anthropic + Cartesia
    "sonnet-4.5 - Anthropic (LLM) - Cartesia (STT - TTS)": "sonnet-4.5-cartesia",
    "sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)": "sonnet-4.5-cartesia",
    "sonnet-4 - Anthropic (LLM) - Cartesia (STT - TTS)": "sonnet-4-cartesia",
    "sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)": "sonnet-4-cartesia",
    "sonnet-3.7 - Anthropic (LLM) - Cartesia (STT - TTS)": "sonnet-3.7-cartesia",
    "sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)": "sonnet-3.7-cartesia",
    # OpenAI
    "gpt4o - OpenAI (LLM) - Elevenlabs (STT - TTS)": "gpt4o-elevenlabs",
    "gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)": "gpt4o-elevenlabs",
    "gpt4o - OpenAI (LLM) - Cartesia (STT - TTS)": "gpt4o-cartesia",
    "gpt4o-mini - OpenAI (STT) - OpenAI (LLM) - Elevenlabs (TTS)": "gpt4o-mini-elevenlabs",
    "gpt4o-mini - Deepgram (STT) - OpenAI (LLM) - Elevenlabs (TTS)": "gpt4o-mini-elevenlabs",
    "gpt4o-mini - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)": "gpt4o-mini-elevenlabs",
    # Gemini
    "gemini-2.5-flash - Google (LLM) - Elevenlabs (STT - TTS)": "gemini-flash-elevenlabs",
    "gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)": "gemini-flash-elevenlabs",
    # Moonshot
    "kimi-k2 - Moonshot (LLM) - Elevenlabs (STT - TTS)": "kimi-k2-elevenlabs",
    "kimi-k2 - Cartesia (STT) - Moonshot (LLM) - Elevenlabs (TTS)": "kimi-k2-elevenlabs",
    # Legacy OpenAI Realtime (deprecated)
    "OpenAI - speech-to-speech": "gpt4o-elevenlabs",
    "OpenAI - speech-to-speech (Mini)": "gpt4o-mini-elevenlabs",
    # Legacy Sonnet 3.5 (deprecated)
    "sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)": "sonnet-3.7-elevenlabs",
    "sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)": "sonnet-3.7-cartesia",
}


def resolve_voice_preset_key(raw_key: str | None) -> str | None:
    """Resolve a preset key to a valid preset, handling legacy keys."""
    if not raw_key:
        return None
    # Already a valid new-format key
    if raw_key in VOICE_PRESETS:
        return raw_key
    # Try to migrate from old format
    migrated = VOICE_PRESET_MIGRATIONS.get(raw_key)
    if migrated and migrated in VOICE_PRESETS:
        return migrated
    return None


def default_voice_for_provider(provider: str) -> str:
    """Get the default voice name for a TTS provider."""
    voices = VOICE_PROVIDER_DEFAULTS.get(provider, [])
    return voices[0] if voices else "alloy"


def resolve_llm_config(config: CompanionConfig) -> Tuple[str, float]:
    """Resolve LLM model and temperature from CompanionConfig.

    Priority for model:
    1. inference.model (if set)
    2. preset's default_model (from VOICE_PRESETS)
    3. Global default ("openai-gpt4o-mini")

    Priority for temperature:
    1. inference.temperature (always from here now)

    Returns:
        Tuple of (model, temperature)
    """
    inference = config.inference

    # Resolve model
    model = inference.model
    if not model and config.voice.preset:
        preset_key = resolve_voice_preset_key(config.voice.preset)
        if preset_key:
            preset = VOICE_PRESETS.get(preset_key, {})
            model = preset.get("default_model")
    model = model or "openai-gpt4o-mini"

    # Temperature is always from inference
    temperature = inference.temperature

    return model, temperature


def resolve_voice_pipeline_config(config: CompanionConfig) -> Tuple[str, str, str]:
    """Resolve STT/TTS providers and voice name from CompanionConfig.

    Priority:
    1. Explicit stt_provider/tts_provider (if set)
    2. Preset's providers (if preset is set)
    3. Defaults (cartesia for STT, elevenlabs for TTS)

    Returns:
        Tuple of (stt_provider, tts_provider, voice_name)
    """
    voice = config.voice

    # Resolve preset
    preset_key = resolve_voice_preset_key(voice.preset) if voice.preset else None
    preset = VOICE_PRESETS.get(preset_key, {}) if preset_key else {}

    # STT provider
    stt_provider = voice.stt_provider
    if not stt_provider:
        stt_provider = preset.get("stt_provider", "cartesia")

    # TTS provider
    tts_provider = voice.tts_provider
    if not tts_provider:
        tts_provider = preset.get("tts_provider", "elevenlabs")

    # Voice name
    voice_name = voice.voice_name
    if not voice_name:
        voice_provider = preset.get("voice_provider", tts_provider)
        voice_name = default_voice_for_provider(voice_provider)

    return stt_provider, tts_provider, voice_name


def build_voice_pipeline_from_config(
    popular_options: Iterable[str] | None,
    voice_names: Iterable[str] | None,
    *,
    temperature: float | None = None,
) -> Tuple[Dict[str, Any] | None, str | None, float | None]:
    """Build voice pipeline dict from legacy format.

    DEPRECATED: Use resolve_llm_config() and resolve_voice_pipeline_config() instead.
    This function is kept for backward compatibility with old callers.
    """
    preset_key: str | None = None
    if popular_options:
        for key in popular_options:
            resolved = resolve_voice_preset_key(key)
            if resolved:
                preset_key = resolved
                break

    primary_voice_name = None
    if voice_names:
        for candidate in voice_names:
            if candidate:
                primary_voice_name = candidate
                break

    if not preset_key:
        if not primary_voice_name:
            return None, None, temperature
        default_temp = temperature if temperature is not None else 0.7
        # Default to STT-LLM-TTS pipeline with default providers
        return (
            {
                "pipeline_type": "stt-llm-tts",
                "voice_name": primary_voice_name,
                "stt_provider": "cartesia",
                "llm_provider": "openai-gpt4o-mini",
                "tts_provider": "elevenlabs",
                "temperature": default_temp,
            },
            "openai-gpt4o-mini",
            default_temp,
        )

    preset = VOICE_PRESETS[preset_key]
    voice_pipeline: Dict[str, Any] = {
        "pipeline_type": "stt-llm-tts",
    }

    if preset.get("stt_provider"):
        voice_pipeline["stt_provider"] = preset["stt_provider"]
    if preset.get("default_model"):
        voice_pipeline["llm_provider"] = preset["default_model"]
    if preset.get("tts_provider"):
        voice_pipeline["tts_provider"] = preset["tts_provider"]

    if primary_voice_name:
        voice_name = primary_voice_name
    else:
        voice_name = default_voice_for_provider(preset.get("voice_provider", "elevenlabs"))
    voice_pipeline["voice_name"] = voice_name

    effective_temperature = temperature if temperature is not None else 0.7
    voice_pipeline["temperature"] = effective_temperature

    llm_provider = preset.get("default_model")

    return voice_pipeline, llm_provider, effective_temperature
