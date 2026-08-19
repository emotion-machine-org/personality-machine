# server/app/routers/voice/pipeline.py
"""Voice pipeline builder for STT→LLM→TTS.

This module builds Pipecat pipelines for real-time voice conversations.
Only the modular STT→LLM→TTS pipeline is supported; OpenAI Realtime is deprecated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .background_noise import create_background_noise_mixer
from .models import PipelineType, VoiceConfig
from .openclaw import OpenClawConfig
from .openclaw_llm import build_openclaw_llm_service
from .providers import LLMProvider, get_openrouter_model_id, get_voice_id
from .services import (
    RawAudioSerializer,
    build_llm_service,
    build_stt_service,
    build_tts_service,
)

logger = logging.getLogger(__name__)

# Voice context injection for STT-LLM-TTS pipeline
# Helps the LLM understand it's in a voice conversation
VOICE_CONTEXT_PROMPT = """
## Voice Conversation Context
You are in a real-time voice conversation. You can hear the user speaking and they can hear your responses. Respond naturally as if having a spoken conversation - be concise and conversational.
""".strip()

# Audio sample rates
SILERO_VAD_SAMPLE_RATE = 16000  # Silero VAD requires 16kHz
OUTPUT_SAMPLE_RATE = 24000  # Higher quality for output


@dataclass
class PipelineComponents:
    """Components returned from pipeline building."""

    pipeline: Pipeline
    transcript: TranscriptProcessor
    context_aggregator: Any
    llm_context: OpenAILLMContext
    serializer: RawAudioSerializer
    transport: FastAPIWebsocketTransport


def build_voice_pipeline(
    websocket: Any,
    voice_config: VoiceConfig,
    system_prompt: str,
    conversation_history: List[dict] | None = None,
    on_audio_activity: Callable[[int], None] | None = None,
    context_processor: FrameProcessor | None = None,
    openclaw_config: OpenClawConfig | None = None,
    companion_id: str | None = None,
    relationship_id: str | None = None,
    user_id: str | None = None,
    on_end_call_requested: Callable[[str | None], Awaitable[None]] | None = None,
) -> PipelineComponents:
    """Build a voice pipeline for real-time conversation.

    Args:
        websocket: FastAPI WebSocket instance
        voice_config: Voice configuration with providers and settings
        system_prompt: System prompt for the LLM
        conversation_history: Optional list of previous messages
        on_audio_activity: Optional callback for audio activity tracking
        context_processor: Optional processor to inject between context aggregator and LLM
                          Used for injecting memory, behaviors, etc. before LLM processing.
        openclaw_config: Optional OpenClaw configuration for external brain mode.
                         When provided and voice_config.llm_provider is OPENCLAW,
                         uses OpenClaw as the LLM brain instead of built-in providers.
        companion_id: Optional companion ID for OpenClaw context
        relationship_id: Optional relationship ID for OpenClaw context
        user_id: Optional external user ID for OpenClaw context

    Returns:
        PipelineComponents with all pipeline components

    Raises:
        ValueError: If required providers are not specified
    """
    # Validate providers
    if not all([voice_config.stt_provider, voice_config.llm_provider, voice_config.tts_provider]):
        raise ValueError(
            "STT, LLM, and TTS providers must be specified for voice pipeline. "
            "Set stt_provider, llm_provider, and tts_provider in voice_config."
        )

    logger.info(
        f"[PIPELINE] Building STT→LLM→TTS pipeline: "
        f"{voice_config.stt_provider} → {voice_config.llm_provider} → {voice_config.tts_provider}"
    )

    # Create serializer with audio activity callback
    serializer = RawAudioSerializer(
        sample_rate=SILERO_VAD_SAMPLE_RATE,
        on_audio_activity=on_audio_activity,
    )

    # Create transport with VAD
    vad_params = VADParams(min_volume=0.7, stop_secs=1.1)
    mixer = create_background_noise_mixer(
        enabled=voice_config.background_noise_enabled,
        noise_type=voice_config.background_noise_type,
        volume=voice_config.background_noise_volume,
    )
    if mixer:
        logger.info(
            "[PIPELINE] Background noise enabled: type=%s volume=%.2f",
            voice_config.background_noise_type,
            voice_config.background_noise_volume,
        )

    transport_params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_enabled=True,
        vad_analyzer=SileroVADAnalyzer(sample_rate=SILERO_VAD_SAMPLE_RATE, params=vad_params),
        audio_in_passthrough=True,
        audio_in_sample_rate=SILERO_VAD_SAMPLE_RATE,
        audio_out_sample_rate=OUTPUT_SAMPLE_RATE,
        audio_in_channels=1,
        audio_out_channels=1,
        add_wav_header=False,  # Raw PCM
        serializer=serializer,
        audio_out_mixer=mixer,
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=transport_params,
    )

    # Enhance system prompt with voice context
    enhanced_prompt = system_prompt.rstrip() + "\n\n" + VOICE_CONTEXT_PROMPT

    # Map voice name to provider-specific voice ID (explicit tts_voice_id takes precedence)
    voice_name = voice_config.voice_name or "alloy"
    tts_provider_str = voice_config.tts_provider.value if voice_config.tts_provider else "openai"
    voice_id = voice_config.tts_voice_id or get_voice_id(tts_provider_str, voice_name)

    if voice_config.tts_voice_id:
        logger.info(
            "[PIPELINE] Using explicit tts_voice_id '%s' (%s)",
            voice_config.tts_voice_id,
            tts_provider_str,
        )
    else:
        logger.info(
            f"[PIPELINE] Voice mapping: '{voice_name}' → '{voice_id}' for {tts_provider_str}"
        )

    # Early validation: Check OpenClaw config if that provider is selected
    if voice_config.llm_provider == LLMProvider.OPENCLAW:
        if not openclaw_config or not openclaw_config.enabled:
            raise ValueError(
                "OpenClaw LLM provider selected but openclaw_config not provided or disabled. "
                "Pass openclaw_config with enabled=True and webhook_url set."
            )

    # Build services
    stt = build_stt_service(voice_config.stt_provider)

    # Select LLM service based on provider
    if voice_config.llm_provider == LLMProvider.OPENCLAW:
        # Direct OpenClaw mode - all requests go to OpenClaw
        logger.info("[PIPELINE] Using OpenClaw as external brain")
        llm = build_openclaw_llm_service(
            config=openclaw_config,
            companion_id=companion_id,
            relationship_id=relationship_id,
            user_id=user_id,
        )
    elif voice_config.llm_provider == LLMProvider.FAST_BRAIN:
        # Fast Brain / Slow Brain pattern
        # Fast model (OpenRouter) for immediate responses, OpenClaw for tasks
        from .fast_brain_llm import FastBrainConfig, build_fast_brain_llm_service

        fast_brain_config = FastBrainConfig.from_env()
        selected_fast_provider = (
            voice_config.fast_brain_model_provider or LLMProvider.GEMINI_25_FLASH
        )
        if selected_fast_provider == LLMProvider.FAST_BRAIN:
            selected_fast_provider = LLMProvider.GEMINI_25_FLASH
        fast_brain_config.fast_model = get_openrouter_model_id(selected_fast_provider)
        fast_brain_config.allow_delegation = bool(voice_config.fast_brain_delegate_enabled)
        fast_brain_config.allow_call_termination = bool(voice_config.fast_brain_end_call_enabled)
        logger.info(
            "[PIPELINE] Using Fast Brain (model=%s delegation=%s end_call=%s)",
            fast_brain_config.fast_model,
            fast_brain_config.allow_delegation,
            fast_brain_config.allow_call_termination,
        )
        llm = build_fast_brain_llm_service(
            config=fast_brain_config,
            personality_prompt=enhanced_prompt,
            companion_id=companion_id,
            relationship_id=relationship_id,
            user_id=user_id,
            on_end_call_requested=on_end_call_requested,
        )
    else:
        llm = build_llm_service(
            voice_config.llm_provider,
            enhanced_prompt,
            voice_config.temperature,
        )

    tts = build_tts_service(
        voice_config.tts_provider,
        voice_id,
        elevenlabs_model_id=voice_config.elevenlabs_model_id,
        elevenlabs_settings={
            "stability": voice_config.elevenlabs_stability,
            "similarity_boost": voice_config.elevenlabs_similarity_boost,
            "style": voice_config.elevenlabs_style,
            "speed": voice_config.elevenlabs_speed,
            "use_speaker_boost": voice_config.elevenlabs_use_speaker_boost,
            "language_code": voice_config.elevenlabs_language_code,
        },
    )

    # Create transcript processor
    transcript = TranscriptProcessor()

    # Build initial messages with conversation history
    messages = [{"role": "system", "content": enhanced_prompt}]
    if conversation_history:
        # Limit to last 20 messages to avoid context overflow
        history_slice = conversation_history[-20:]
        for msg in history_slice:
            messages.append({"role": msg["role"], "content": msg["content"]})
        logger.info(
            f"[PIPELINE] Added {len(history_slice)} of {len(conversation_history)} "
            "messages to context"
        )

    # Create LLM context and aggregator
    tools = None
    if voice_config.llm_provider == LLMProvider.FAST_BRAIN:
        get_tools_schema = getattr(llm, "get_tools_schema", None)
        if callable(get_tools_schema):
            tools = get_tools_schema()

    llm_context = OpenAILLMContext(messages, tools=tools) if tools else OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(llm_context)

    # Build pipeline processors list
    # Pipeline: Input → STT → Transcript → Context → [ContextProcessor] → LLM → TTS → Output
    processors = [
        transport.input(),
        stt,
        transcript.user(),
        context_aggregator.user(),
    ]

    # Insert context processor if provided (for memory/behavior injection)
    if context_processor:
        processors.append(context_processor)
        logger.info("[PIPELINE] Context processor added for memory/behavior injection")

    processors.extend(
        [
            llm,
            tts,
            transport.output(),
            transcript.assistant(),
            context_aggregator.assistant(),
        ]
    )

    pipeline = Pipeline(processors)

    logger.info("[PIPELINE] Pipeline built successfully")

    return PipelineComponents(
        pipeline=pipeline,
        transcript=transcript,
        context_aggregator=context_aggregator,
        llm_context=llm_context,
        serializer=serializer,
        transport=transport,
    )


def normalize_voice_config(voice_config: VoiceConfig) -> VoiceConfig:
    """Normalize and validate voice configuration.

    Handles legacy fields and sets defaults for the STT→LLM→TTS pipeline.

    Args:
        voice_config: Voice configuration to normalize

    Returns:
        Normalized voice configuration
    """
    # Ensure we have a voice_name
    if not voice_config.voice_name:
        voice_config.voice_name = voice_config.openai_voice or "alloy"

    # Auto-migrate OpenAI Realtime to STT→LLM→TTS
    if voice_config.pipeline_type == PipelineType.OPENAI_REALTIME:
        logger.info("[PIPELINE] Auto-migrating from OpenAI Realtime to STT→LLM→TTS")
        voice_config.pipeline_type = PipelineType.STT_LLM_TTS

        # Set default providers if not specified
        if not voice_config.stt_provider:
            from .providers import STTProvider

            voice_config.stt_provider = STTProvider.OPENAI

        if not voice_config.llm_provider:
            from .providers import LLMProvider

            voice_config.llm_provider = LLMProvider.OPENAI_GPT4O

        if not voice_config.tts_provider:
            from .providers import TTSProvider

            voice_config.tts_provider = TTSProvider.OPENAI

    # Set defaults for STT→LLM→TTS if providers not specified
    if voice_config.pipeline_type == PipelineType.STT_LLM_TTS:
        from .providers import LLMProvider, STTProvider, TTSProvider

        if not voice_config.stt_provider:
            voice_config.stt_provider = STTProvider.OPENAI
        if not voice_config.llm_provider:
            voice_config.llm_provider = LLMProvider.OPENAI_GPT4O
        if not voice_config.tts_provider:
            voice_config.tts_provider = TTSProvider.OPENAI

    return voice_config


def create_default_voice_config() -> VoiceConfig:
    """Create a default voice configuration for STT→LLM→TTS pipeline.

    Returns:
        VoiceConfig with recommended providers (Cartesia STT, Claude Sonnet 4.5, ElevenLabs TTS)
    """
    from .providers import LLMProvider, STTProvider, TTSProvider

    return VoiceConfig(
        pipeline_type=PipelineType.STT_LLM_TTS,
        voice_name="Sarah",
        stt_provider=STTProvider.CARTESIA,
        llm_provider=LLMProvider.CLAUDE_HAIKU_45,
        tts_provider=TTSProvider.ELEVENLABS,
        temperature=0.7,
    )
