import json
from datetime import datetime
from typing import Any, Dict, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from ..constants import MAX_TEXT_LLM_OUTPUT_TOKENS


# Voice-related models
class VoiceBase(BaseModel):
    name: str = Field(..., max_length=100)
    provider: str = Field(..., max_length=50)
    provider_key: str = Field(..., max_length=255)


class Voice(VoiceBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True


# System Prompt structure matching web client expectations
class SystemPrompt(BaseModel):
    full_system_prompt: str = Field(default="", description="Complete system prompt")
    identity: str = Field(default="", description="Character identity")
    personality: str = Field(default="", description="Character personality")
    style: str = Field(default="", description="Communication style")
    backstory: str = Field(default="", description="Character backstory")
    additional_instructions: str = Field(default="", description="Additional instructions")
    self_image: str = Field(default="", description="Character self-image")

    def get_effective_prompt(self) -> str:
        """
        Returns the effective system prompt.
        If full_system_prompt is set, returns it directly.
        Otherwise, composes a prompt from the individual fields.
        """
        if self.full_system_prompt and self.full_system_prompt.strip():
            return self.full_system_prompt

        # Compose from individual fields (matching frontend behavior)
        sections = []
        if self.identity and self.identity.strip():
            sections.append(f"# IDENTITY\n\n{self.identity.strip()}")
        if self.personality and self.personality.strip():
            sections.append(f"# PERSONALITY\n\n{self.personality.strip()}")
        if self.style and self.style.strip():
            sections.append(f"# STYLE\n\n{self.style.strip()}")
        if self.backstory and self.backstory.strip():
            sections.append(f"# BACKSTORY\n\n{self.backstory.strip()}")
        if self.additional_instructions and self.additional_instructions.strip():
            sections.append(f"# ADDITIONAL INSTRUCTIONS\n\n{self.additional_instructions.strip()}")
        if self.self_image and self.self_image.strip():
            sections.append(f"# SELF IMAGE\n\n{self.self_image.strip()}")

        return "\n\n".join(sections)


# Memory configuration
class MemoryConfig(BaseModel):
    enabled: bool = Field(default=False)
    version: int = Field(
        default=1, ge=1, le=2, description="Memory version: 1=vector retrieval, 2=scratchpad"
    )
    core_memories: List[str] = Field(default_factory=list)
    memory_evaluation_prompt: str = Field(default="")
    recency: float = Field(default=0.995, ge=0.0, le=1.0)
    top_k: int = Field(default=50, ge=1, le=1000)
    min_saliency: float = Field(default=0.2, ge=0.0, le=1.0)
    # V2-specific settings
    max_entries: int = Field(default=100, ge=1, le=500, description="Max scratchpad entries for v2")
    model: str | None = Field(default=None, description="LLM model for v2 ingestion")
    ingestion_prompt: str | None = Field(default=None, description="Custom ingestion prompt for v2")


# Inference configuration - unified LLM settings for text and voice
class InferenceConfig(BaseModel):
    """Unified LLM settings - used for both text API and voice sessions."""

    model: str | None = Field(
        default=None,
        description="LLM model ID (e.g., 'claude-sonnet-4.5', 'openai-gpt4o', 'gemini-2.5-flash')",
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=16384)


# Migration map for old preset keys to new short keys
PRESET_MIGRATIONS: Dict[str, str] = {
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


def _migrate_preset_key(key: str | None) -> str | None:
    """Migrate old preset key to new short format."""
    if not key:
        return None
    # If already in new format, return as-is
    if key in (
        "sonnet-4.5-elevenlabs",
        "sonnet-4-elevenlabs",
        "sonnet-3.7-elevenlabs",
        "sonnet-4.5-cartesia",
        "sonnet-4-cartesia",
        "sonnet-3.7-cartesia",
        "gpt4o-elevenlabs",
        "gpt4o-cartesia",
        "gpt4o-mini-elevenlabs",
        "gpt5.1-elevenlabs",
        "gemini-flash-elevenlabs",
        "kimi-k2-elevenlabs",
    ):
        return key
    return PRESET_MIGRATIONS.get(key, key)


# Voice configuration - streamlined for STT/TTS providers only
class TwilioConfig(BaseModel):
    """Optional per-companion Twilio credentials (BYO Twilio)."""

    account_sid: str | None = Field(default=None, description="Twilio Account SID")
    auth_token: str | None = Field(default=None, description="Twilio Auth Token")
    phone_number: str | None = Field(default=None, description="Twilio phone number (E.164)")


class VoiceConfig(BaseModel):
    """Voice pipeline settings: STT/TTS providers and voice selection.

    The LLM used in voice sessions is determined by InferenceConfig, not here.
    """

    # Option 1: Quick setup via preset (includes recommended STT/TTS pairing)
    preset: str | None = Field(
        default=None,
        description="Voice preset key (e.g., 'sonnet-4.5-elevenlabs', 'gpt4o-cartesia')",
    )

    # Option 2: Build your own (explicit provider selection)
    stt_provider: str | None = Field(
        default=None, description="STT provider: cartesia, deepgram, openai"
    )
    tts_provider: str | None = Field(
        default=None, description="TTS provider: elevenlabs, cartesia, openai"
    )

    # Voice selection
    voice_name: str | None = Field(
        default=None, description="TTS voice name (e.g., 'Sarah', 'alloy')"
    )

    # Optional per-companion Twilio credentials (BYO Twilio)
    twilio: TwilioConfig | None = Field(
        default=None, description="Twilio credentials for this companion"
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_format(cls, data: Any) -> Any:
        """Migrate old VoiceConfig format to new."""
        if not isinstance(data, dict):
            return data

        # popular_options[0] → preset (with mapping)
        if "popular_options" in data and not data.get("preset"):
            opts = data.get("popular_options", [])
            if opts and isinstance(opts, list) and len(opts) > 0:
                data["preset"] = _migrate_preset_key(opts[0])

        # voice[0] → voice_name
        if "voice" in data and not data.get("voice_name"):
            voices = data.get("voice", [])
            if voices and isinstance(voices, list) and len(voices) > 0:
                if isinstance(voices[0], str):
                    data["voice_name"] = voices[0]

        return data


class KnowledgeConfig(BaseModel):
    """Knowledge/RAG configuration for the companion."""

    enabled: bool = Field(default=False, description="Whether knowledge retrieval is enabled")
    gate_strategy: str = Field(
        default="keyword", description="Gate strategy: 'keyword', 'always', or 'llm'"
    )
    top_k: int = Field(default=5, description="Max knowledge items to retrieve")
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Min confidence score")
    classifier_summary: str | None = Field(
        default=None,
        description="Custom description for the classifier to understand when to enable this layer. "
        "E.g., 'Health information about menstrual cycles, symptoms, and period tracking'",
    )


class LayerAttachment(BaseModel):
    key: str = Field(..., max_length=64)
    category: str = Field(..., max_length=64)
    enabled: bool = Field(default=True)
    priority: int = Field(default=50)
    params: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int | None = None
    reserved_tokens: int | None = None
    depends_on: List[str] = Field(default_factory=list)


class ContextSettings(BaseModel):
    max_prompt_tokens: int | None = None
    target_prompt_fraction: float = Field(default=0.4, ge=0.0, le=1.0)
    reserved_completion_tokens: int | None = None
    tool_summary: str | None = None
    message_limit: int = Field(
        default=200,
        ge=10,
        description="Maximum number of messages to load into context per turn",
    )


class ClassifierConfig(BaseModel):
    """Configuration for the intent classifier.

    Note: This is a local copy to avoid circular imports with context.classifier_schemas.
    """

    enabled: bool = Field(default=True)
    model: str = Field(default="gemini-2.0-flash")
    timeout_ms: int = Field(default=10000)
    history_limit: int = Field(default=20)
    instructions: str | None = Field(default=None)  # Custom instructions for when to enable layers


class IntroMessageConfig(BaseModel):
    """Optional first-message greeting sent when a websocket session is established."""

    enabled: bool = Field(default=False)
    text: str = Field(default="")
    send_once_per_relationship: bool = Field(default=True)


# Complete companion configuration
class CompanionConfig(BaseModel):
    system_prompt: SystemPrompt = Field(default_factory=SystemPrompt)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    context_mode: Literal["legacy", "layered", "raw"] = Field(default="layered")
    layers: List[LayerAttachment] = Field(default_factory=list)
    context: ContextSettings = Field(default_factory=ContextSettings)
    intro_message: IntroMessageConfig = Field(default_factory=IntroMessageConfig)
    include_profile_in_prompt: bool = Field(
        default=False,
        description="Inject relationship profile into the prompt by default for v2 turns",
    )

    # Profile schema template for user profiles
    profile_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="Schema template defining the structure for user profiles in relationships",
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_top_level(cls, data: Any) -> Any:
        """Migrate top-level LLM fields to inference block."""
        if not isinstance(data, dict):
            return data

        inference = data.get("inference", {})
        if not isinstance(inference, dict):
            inference = {}

        # Move top-level fields into inference if not already set
        if "model" in data and data["model"] is not None and not inference.get("model"):
            inference["model"] = data["model"]
        if (
            "temperature" in data
            and data["temperature"] is not None
            and inference.get("temperature") is None
        ):
            inference["temperature"] = data["temperature"]
        if (
            "max_output_tokens" in data
            and data["max_output_tokens"] is not None
            and not inference.get("max_output_tokens")
        ):
            inference["max_output_tokens"] = data["max_output_tokens"]

        # Also check for temperature in legacy voice config
        voice_data = data.get("voice", {})
        if isinstance(voice_data, dict):
            voice_temp = voice_data.get("temperature")
            if voice_temp is not None and inference.get("temperature") is None:
                inference["temperature"] = voice_temp

        if inference:
            data["inference"] = inference

        return data


# Companion base models
class CompanionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)


class CompanionCreate(CompanionBase):
    config: CompanionConfig | None = Field(default_factory=CompanionConfig)


class CompanionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    config: CompanionConfig | None = None


# Database models
class Companion(CompanionBase):
    id: UUID
    owner_id: UUID
    project_id: UUID
    created_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True


class CompanionVersion(BaseModel):
    id: UUID
    companion_id: UUID
    version_number: int
    system_prompt: str | None = None  # Deprecated: use config column instead
    voice_id: UUID | None = None
    memory_enabled: bool = False
    status: str = "DEPLOYED"  # DRAFT | DEPLOYED | ARCHIVED
    created_at: datetime

    class Config:
        from_attributes = True


class CompanionVersionSummary(BaseModel):
    id: UUID
    version_number: int
    created_at: datetime
    config: CompanionConfig

    class Config:
        from_attributes = True


# Response models for API
class CompanionSummary(BaseModel):
    """Simplified companion info for lists"""

    id: UUID
    name: str
    last_updated: str  # Formatted relative time like "2 days ago"
    project_id: UUID | None = None


class CompanionDetail(Companion):
    """Companion with full configuration"""

    config: CompanionConfig
    current_version: CompanionVersion | None = None


def parse_companion_config_payload(payload: str | Dict[str, Any] | None) -> CompanionConfig:
    """Parse stored companion configuration JSON into a CompanionConfig model.

    Handles legacy records where the JSON is double-encoded or includes out-of-range
    values for fields like `min_saliency`.
    """

    config = CompanionConfig()

    if payload is None:
        return config

    try:
        parsed: Dict[str, Any] | str | None

        if isinstance(payload, dict):
            parsed = payload
        elif isinstance(payload, str):
            parsed = json.loads(payload)
        else:
            parsed = None

        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except Exception:
                parsed = {"system_prompt": {"full_system_prompt": payload}}

        if isinstance(parsed, dict):
            try:
                memory_section = parsed.get("memory") or {}
                min_saliency = memory_section.get("min_saliency")
                if isinstance(min_saliency, (int | float)):
                    if 1 < min_saliency <= 10:
                        memory_section["min_saliency"] = float(min_saliency) / 10.0
                    elif min_saliency > 10:
                        memory_section["min_saliency"] = 0.2
                    parsed["memory"] = memory_section
            except Exception:
                # Best effort sanitization; fall back to raw values on failure
                pass

            try:
                config = CompanionConfig(**parsed)
            except Exception:
                # If validation fails, fall back to treating the payload as plain text
                if isinstance(payload, str):
                    config.system_prompt.full_system_prompt = payload
        elif isinstance(payload, str):
            config.system_prompt.full_system_prompt = payload
    except (json.JSONDecodeError, ValueError, TypeError):
        if isinstance(payload, str):
            config.system_prompt.full_system_prompt = payload

    # Clamp optional max_output_tokens to safe bounds (now in inference block)
    try:
        if config.inference.max_output_tokens is not None:
            config.inference.max_output_tokens = max(
                1, min(int(config.inference.max_output_tokens), MAX_TEXT_LLM_OUTPUT_TOKENS)
            )
    except Exception:
        config.inference.max_output_tokens = None

    return config
