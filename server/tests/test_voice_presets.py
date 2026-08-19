from app.services.voice_presets import build_voice_pipeline_from_config, resolve_voice_preset_key


def test_build_voice_pipeline_from_config_legacy_realtime_migrates():
    """Test that legacy OpenAI Realtime preset is migrated to STT-LLM-TTS."""
    pipeline, llm_provider, temperature = build_voice_pipeline_from_config(
        ["OpenAI - speech-to-speech"],  # Legacy preset, should be migrated
        ["shimmer"],
        temperature=0.5,
    )

    assert pipeline is not None
    # Legacy preset should be migrated to STT-LLM-TTS
    assert pipeline["pipeline_type"] == "stt-llm-tts"
    assert pipeline["voice_name"] == "shimmer"
    assert pipeline["stt_provider"] == "cartesia"
    assert pipeline["llm_provider"] == "openai-gpt4o"
    assert pipeline["tts_provider"] == "elevenlabs"
    assert pipeline["temperature"] == 0.5
    assert llm_provider == "openai-gpt4o"
    assert temperature == 0.5


def test_build_voice_pipeline_from_config_stt_llm_tts():
    pipeline, llm_provider, temperature = build_voice_pipeline_from_config(
        ["sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)"],
        ["Charlotte"],
        temperature=None,
    )

    assert pipeline is not None
    assert pipeline["pipeline_type"] == "stt-llm-tts"
    assert pipeline["stt_provider"] == "cartesia"
    assert pipeline["tts_provider"] == "elevenlabs"
    assert pipeline["llm_provider"] == "claude-sonnet-4.5"
    assert pipeline["voice_name"] == "Charlotte"
    assert temperature == pipeline["temperature"] == 0.7
    assert llm_provider == "claude-sonnet-4.5"


def test_build_voice_pipeline_from_config_handles_migration():
    pipeline, llm_provider, _ = build_voice_pipeline_from_config(
        ["sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)"],
        [],
        temperature=None,
    )

    resolved = resolve_voice_preset_key(
        "sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)"
    )
    # Now returns short-form key after migration
    assert resolved == "sonnet-3.7-elevenlabs"
    assert pipeline is not None
    assert pipeline["voice_name"] == "Sarah"  # default voice for ElevenLabs
    assert llm_provider == "claude-sonnet-3.7"


def test_build_voice_pipeline_from_config_falls_back_to_stt_llm_tts():
    """Test fallback uses STT-LLM-TTS with default preset providers."""
    pipeline, llm_provider, temperature = build_voice_pipeline_from_config(
        [],
        ["shimmer"],
        temperature=0.65,
    )

    assert pipeline is not None
    # Fallback uses gpt4o-mini-elevenlabs preset (Cartesia STT, ElevenLabs TTS)
    assert pipeline["pipeline_type"] == "stt-llm-tts"
    assert pipeline["voice_name"] == "shimmer"
    assert pipeline["stt_provider"] == "cartesia"
    assert pipeline["llm_provider"] == "openai-gpt4o-mini"
    assert pipeline["tts_provider"] == "elevenlabs"
    assert temperature == 0.65
    assert llm_provider == "openai-gpt4o-mini"
