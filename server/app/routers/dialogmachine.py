"""DialogMachine dashboard APIs for phone-call workflow testing."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..db import get_db
from ..models.user import User
from ..repositories.companion import CompanionRepository
from ..repositories.relationship_repository import RelationshipRepository
from .voice.background_noise import (
    AVAILABLE_BACKGROUND_NOISE_TYPES,
    DEFAULT_BACKGROUND_NOISE_TYPE,
    clamp_background_noise_volume,
    is_valid_background_noise_type,
    normalize_background_noise_type,
)
from .voice.models import VoiceConfig
from .voice.providers import ELEVENLABS_VOICES, LLMProvider
from .voice.twilio import (
    _call_auth_tokens,
    _get_public_base_url,
    _get_twilio_client_for,
    _pending_calls,
    _resolve_twilio_credentials,
    _schedule_twilio_call_event,
)
from .voice.twilio_models import TwilioDialOutResponse
from .voice.v2 import _create_voice_token, create_default_voice_config, normalize_voice_config
from .voice.voice_workspace import HOT_CONTEXT_FILE, get_workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dialogmachine", tags=["dialogmachine"])

# Fixed UUID for the onboarding companion (must match seed script)
ONBOARDING_COMPANION_ID = UUID("00000000-0000-0000-0000-000000000001")

DIALOGMACHINE_TOOL_TASK_DELEGATION = "task_delegation"
DIALOGMACHINE_TOOL_END_CALL = "end_call"
AVAILABLE_DIALOGMACHINE_TOOLS = (
    DIALOGMACHINE_TOOL_END_CALL,
    DIALOGMACHINE_TOOL_TASK_DELEGATION,
)
DEFAULT_DIALOGMACHINE_TOOLS = (DIALOGMACHINE_TOOL_END_CALL,)

DEFAULT_DIALOGMACHINE_LLM_PROVIDER = LLMProvider.FAST_BRAIN.value
DIALOGMACHINE_LLM_MODEL_OPTIONS = (
    {
        "id": LLMProvider.FAST_BRAIN.value,
        "label": "Gemini 2.5 Flash (Default)",
        "description": "OpenRouter: google/gemini-2.5-flash",
    },
    {
        "id": LLMProvider.OPENAI_GPT4O.value,
        "label": "OpenAI GPT-4o",
        "description": "OpenAI: gpt-4o",
    },
    {
        "id": LLMProvider.OPENAI_GPT4O_MINI.value,
        "label": "OpenAI GPT-4o mini",
        "description": "OpenAI: gpt-4o-mini",
    },
    {
        "id": LLMProvider.OPENAI_GPT51.value,
        "label": "OpenAI GPT-5.1",
        "description": "OpenAI: gpt-5.1",
    },
    {
        "id": LLMProvider.CLAUDE_HAIKU_45.value,
        "label": "Claude Haiku 4.5",
        "description": "OpenRouter: anthropic/claude-haiku-4.5",
    },
    {
        "id": LLMProvider.CLAUDE_SONNET_4.value,
        "label": "Claude Sonnet 4",
        "description": "OpenRouter: anthropic/claude-sonnet-4",
    },
    {
        "id": LLMProvider.CLAUDE_SONNET_45.value,
        "label": "Claude Sonnet 4.5",
        "description": "OpenRouter: anthropic/claude-sonnet-4.5",
    },
    {
        "id": LLMProvider.CLAUDE_SONNET_46.value,
        "label": "Claude Sonnet 4.6",
        "description": "OpenRouter: anthropic/claude-sonnet-4.6",
    },
    {
        "id": LLMProvider.CLAUDE_OPUS_4.value,
        "label": "Claude Opus 4",
        "description": "OpenRouter: anthropic/claude-opus-4",
    },
    {
        "id": LLMProvider.CLAUDE_OPUS_45.value,
        "label": "Claude Opus 4.5",
        "description": "OpenRouter: anthropic/claude-opus-4.5",
    },
    {
        "id": LLMProvider.CLAUDE_OPUS_46.value,
        "label": "Claude Opus 4.6",
        "description": "OpenRouter: anthropic/claude-opus-4.6",
    },
    {
        "id": LLMProvider.GEMINI_25_FLASH.value,
        "label": "Gemini 2.5 Flash",
        "description": "OpenRouter: google/gemini-2.5-flash",
    },
    {
        "id": LLMProvider.GEMINI_31_FLASH_LITE.value,
        "label": "Gemini 3.1 Flash Lite Preview",
        "description": "OpenRouter: google/gemini-3.1-flash-lite-preview",
    },
)
SUPPORTED_DIALOGMACHINE_LLM_PROVIDERS = tuple(
    option["id"] for option in DIALOGMACHINE_LLM_MODEL_OPTIONS
)

DEFAULT_DIALOGMACHINE_ELEVENLABS_MODEL_ID = "eleven_turbo_v2_5"
SUPPORTED_DIALOGMACHINE_ELEVENLABS_MODELS = (
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
    "eleven_multilingual_v2",
)
DIALOGMACHINE_ELEVENLABS_MODEL_LABELS = {
    "eleven_flash_v2_5": "Eleven Flash v2.5",
    "eleven_turbo_v2_5": "Eleven Turbo v2.5",
    "eleven_multilingual_v2": "Eleven Multilingual v2",
}
DEFAULT_DIALOGMACHINE_ELEVENLABS_STABILITY = 0.7
DEFAULT_DIALOGMACHINE_ELEVENLABS_SIMILARITY_BOOST = 0.8
DEFAULT_DIALOGMACHINE_ELEVENLABS_STYLE = 0.5
DEFAULT_DIALOGMACHINE_ELEVENLABS_SPEED = 1.0
DEFAULT_DIALOGMACHINE_ELEVENLABS_USE_SPEAKER_BOOST = True
DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_OVERRIDE_ENABLED = False
DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE = "en"
ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io/v1"


def _get_elevenlabs_api_key() -> str:
    """Resolve ElevenLabs API key from common env var names."""
    return os.getenv("ELEVEN_API_KEY", "").strip() or os.getenv("ELEVENLABS_API_KEY", "").strip()


def _extract_upstream_error_detail(response: httpx.Response) -> str:
    """Extract concise upstream error detail without leaking excessive payload."""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            if isinstance(detail, dict):
                message = detail.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    except Exception:
        pass
    return (response.text or "").strip()[:240] or "unknown upstream error"


def _local_elevenlabs_voices_fallback() -> list[DialogmachineElevenlabsVoice]:
    rows = [
        DialogmachineElevenlabsVoice(
            voice_id=voice_id,
            name=name,
            category="local-default",
        )
        for name, voice_id in ELEVENLABS_VOICES.items()
    ]
    rows.sort(key=lambda voice: voice.name.lower())
    return rows


async def _get_owned_companion(
    conn: asyncpg.Connection,
    companion_id: UUID,
    owner_user_id: UUID,
):
    """Get companion by owner with onboarding companion exception."""
    if companion_id == ONBOARDING_COMPANION_ID:
        companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    else:
        companion = await CompanionRepository.get_companion_by_id(conn, companion_id, owner_user_id)
    if not companion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")
    return companion


async def _get_or_create_relationship(
    conn: asyncpg.Connection,
    companion_id: UUID,
    test_user_id: str,
):
    relationship, _ = await RelationshipRepository.ensure_exists(
        conn, companion_id=companion_id, user_id=test_user_id
    )
    return relationship


def _extract_prompt_override(config: dict | None) -> str | None:
    if not isinstance(config, dict):
        return None
    dialog_cfg = config.get("dialogmachine")
    if not isinstance(dialog_cfg, dict):
        return None
    value = dialog_cfg.get("prompt_override")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_guardrails(config: dict | None) -> str | None:
    if not isinstance(config, dict):
        return None
    dialog_cfg = config.get("dialogmachine")
    if not isinstance(dialog_cfg, dict):
        return None
    value = dialog_cfg.get("guardrails")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_background_noise(config: dict | None) -> tuple[bool, str, float]:
    if not isinstance(config, dict):
        return False, DEFAULT_BACKGROUND_NOISE_TYPE, 0.12
    dialog_cfg = config.get("dialogmachine")
    if not isinstance(dialog_cfg, dict):
        return False, DEFAULT_BACKGROUND_NOISE_TYPE, 0.12
    noise_cfg = dialog_cfg.get("background_noise")
    if not isinstance(noise_cfg, dict):
        return False, DEFAULT_BACKGROUND_NOISE_TYPE, 0.12

    enabled = bool(noise_cfg.get("enabled", False))
    noise_type = normalize_background_noise_type(
        str(noise_cfg.get("type")) if noise_cfg.get("type") is not None else None
    )
    volume = clamp_background_noise_volume(noise_cfg.get("volume", 0.12))
    return enabled, noise_type, volume


def _extract_task_delegation_enabled(config: dict | None) -> bool:
    """Read DialogMachine task-delegation toggle.

    Defaults to False for DialogMachine so direct conversation mode is the baseline.
    """
    tools = _extract_dialogmachine_tools(config)
    return DIALOGMACHINE_TOOL_TASK_DELEGATION in tools


def _extract_end_call_enabled(config: dict | None) -> bool:
    tools = _extract_dialogmachine_tools(config)
    return DIALOGMACHINE_TOOL_END_CALL in tools


def _normalize_dialogmachine_tools(raw: list[object] | None) -> list[str]:
    if not raw:
        return []
    requested = {str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()}
    return [tool for tool in AVAILABLE_DIALOGMACHINE_TOOLS if tool in requested]


def _extract_dialogmachine_tools(config: dict | None) -> list[str]:
    if not isinstance(config, dict):
        return list(DEFAULT_DIALOGMACHINE_TOOLS)

    dialog_cfg = config.get("dialogmachine")
    if not isinstance(dialog_cfg, dict):
        return list(DEFAULT_DIALOGMACHINE_TOOLS)

    tools_cfg = dialog_cfg.get("tools")
    if isinstance(tools_cfg, dict) and isinstance(tools_cfg.get("selected"), list):
        return _normalize_dialogmachine_tools(tools_cfg.get("selected"))

    # Legacy fallback path (single delegation toggle).
    selected = list(DEFAULT_DIALOGMACHINE_TOOLS)
    legacy_delegation = dialog_cfg.get("enable_task_delegation")
    if isinstance(legacy_delegation, bool) and legacy_delegation:
        selected.append(DIALOGMACHINE_TOOL_TASK_DELEGATION)
    return _normalize_dialogmachine_tools(selected)


def _normalize_dialogmachine_llm_provider(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_DIALOGMACHINE_LLM_PROVIDER
    candidate = value.strip()
    if candidate in SUPPORTED_DIALOGMACHINE_LLM_PROVIDERS:
        return candidate
    return DEFAULT_DIALOGMACHINE_LLM_PROVIDER


def _extract_dialogmachine_llm_provider(config: dict | None) -> str:
    if not isinstance(config, dict):
        return DEFAULT_DIALOGMACHINE_LLM_PROVIDER

    dialog_cfg = config.get("dialogmachine")
    if not isinstance(dialog_cfg, dict):
        return DEFAULT_DIALOGMACHINE_LLM_PROVIDER

    llm_cfg = dialog_cfg.get("llm")
    if isinstance(llm_cfg, dict):
        return _normalize_dialogmachine_llm_provider(llm_cfg.get("provider"))

    # Legacy fallback path.
    return _normalize_dialogmachine_llm_provider(dialog_cfg.get("llm_provider"))


def _clamp_float(
    value: Any,
    *,
    min_value: float,
    max_value: float,
    default: float,
) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric < min_value:
        return min_value
    if numeric > max_value:
        return max_value
    return numeric


def _normalize_language_code(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE
    candidate = value.strip()
    if not candidate:
        return DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE
    if "-" in candidate:
        parts = candidate.split("-", maxsplit=1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[0].lower()}-{parts[1].upper()}"
    return candidate.lower()


def _normalize_elevenlabs_model_id(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_DIALOGMACHINE_ELEVENLABS_MODEL_ID
    candidate = value.strip()
    if candidate in SUPPORTED_DIALOGMACHINE_ELEVENLABS_MODELS:
        return candidate
    return DEFAULT_DIALOGMACHINE_ELEVENLABS_MODEL_ID


def _normalize_dialogmachine_elevenlabs(raw: dict | None) -> dict[str, Any]:
    cfg = raw if isinstance(raw, dict) else {}
    voice_id = str(cfg.get("voice_id")).strip() if cfg.get("voice_id") else None
    voice_name = str(cfg.get("voice_name")).strip() if cfg.get("voice_name") else None
    language_override_enabled = bool(
        cfg.get(
            "language_override_enabled",
            DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_OVERRIDE_ENABLED,
        )
    )
    language_code = _normalize_language_code(
        cfg.get("language_code", DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE)
    )
    return {
        "voice_id": voice_id or None,
        "voice_name": voice_name or None,
        "model_id": _normalize_elevenlabs_model_id(cfg.get("model_id")),
        "stability": _clamp_float(
            cfg.get("stability"),
            min_value=0.0,
            max_value=1.0,
            default=DEFAULT_DIALOGMACHINE_ELEVENLABS_STABILITY,
        ),
        "similarity_boost": _clamp_float(
            cfg.get("similarity_boost"),
            min_value=0.0,
            max_value=1.0,
            default=DEFAULT_DIALOGMACHINE_ELEVENLABS_SIMILARITY_BOOST,
        ),
        "style": _clamp_float(
            cfg.get("style"),
            min_value=0.0,
            max_value=1.0,
            default=DEFAULT_DIALOGMACHINE_ELEVENLABS_STYLE,
        ),
        "speed": _clamp_float(
            cfg.get("speed"),
            min_value=0.7,
            max_value=1.2,
            default=DEFAULT_DIALOGMACHINE_ELEVENLABS_SPEED,
        ),
        "use_speaker_boost": bool(
            cfg.get(
                "use_speaker_boost",
                DEFAULT_DIALOGMACHINE_ELEVENLABS_USE_SPEAKER_BOOST,
            )
        ),
        "language_override_enabled": language_override_enabled,
        "language_code": (
            language_code
            if language_override_enabled
            else DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE
        ),
    }


def _extract_dialogmachine_elevenlabs(config: dict | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return _normalize_dialogmachine_elevenlabs(None)

    dialog_cfg = config.get("dialogmachine")
    if not isinstance(dialog_cfg, dict):
        return _normalize_dialogmachine_elevenlabs(None)

    return _normalize_dialogmachine_elevenlabs(dialog_cfg.get("elevenlabs"))


class DialogmachineHotContextResponse(BaseModel):
    relationship_id: str
    content: str
    exists: bool


class DialogmachineHotContextUpdateRequest(BaseModel):
    content: str = Field(default="", description="New hot_context.md content")


class DialogmachinePromptOverrideResponse(BaseModel):
    relationship_id: str
    prompt_override: str | None = None


class DialogmachinePromptOverrideUpdateRequest(BaseModel):
    prompt_override: str | None = Field(
        default=None,
        description="Per-relationship prompt override. Use null or empty to clear.",
    )


class DialogmachineGuardrailsResponse(BaseModel):
    relationship_id: str
    guardrails: str | None = None


class DialogmachineGuardrailsUpdateRequest(BaseModel):
    guardrails: str | None = Field(
        default=None,
        description="Per-relationship guardrails appended to runtime prompt. Use null or empty to clear.",
    )


class DialogmachineBackgroundNoiseResponse(BaseModel):
    relationship_id: str
    enabled: bool = False
    noise_type: str = DEFAULT_BACKGROUND_NOISE_TYPE
    volume: float = 0.12
    available_noise_types: list[str] = Field(
        default_factory=lambda: list(AVAILABLE_BACKGROUND_NOISE_TYPES)
    )


class DialogmachineBackgroundNoiseUpdateRequest(BaseModel):
    enabled: bool = False
    noise_type: str = Field(default=DEFAULT_BACKGROUND_NOISE_TYPE)
    volume: float = Field(default=0.12, ge=0.0, le=1.0)


class DialogmachineToolCallsResponse(BaseModel):
    relationship_id: str
    enabled: bool = False
    selected_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_DIALOGMACHINE_TOOLS))
    available_tools: list[str] = Field(default_factory=lambda: list(AVAILABLE_DIALOGMACHINE_TOOLS))


class DialogmachineToolCallsUpdateRequest(BaseModel):
    enabled: bool | None = Field(
        default=None,
        description="[Legacy] Toggle task delegation only. selected_tools is preferred.",
    )
    selected_tools: list[str] | None = Field(
        default=None,
        description="Tool keys to enable for DialogMachine runtime behavior.",
    )


class DialogmachineLlmModelOption(BaseModel):
    id: str
    label: str
    description: str | None = None


class DialogmachineLlmSettingsResponse(BaseModel):
    relationship_id: str
    provider: str = DEFAULT_DIALOGMACHINE_LLM_PROVIDER
    available_models: list[DialogmachineLlmModelOption] = Field(
        default_factory=lambda: [
            DialogmachineLlmModelOption(
                id=option["id"],
                label=option["label"],
                description=option.get("description"),
            )
            for option in DIALOGMACHINE_LLM_MODEL_OPTIONS
        ]
    )


class DialogmachineLlmSettingsUpdateRequest(BaseModel):
    provider: str | None = Field(
        default=None,
        description="Voice pipeline llm_provider value to use for DialogMachine simulate/dial.",
    )


class DialogmachineElevenlabsModelOption(BaseModel):
    id: str
    label: str


class DialogmachineElevenlabsVoice(BaseModel):
    voice_id: str
    name: str
    category: str | None = None


class DialogmachineElevenlabsSettingsResponse(BaseModel):
    relationship_id: str
    voice_id: str | None = None
    voice_name: str | None = None
    model_id: str = DEFAULT_DIALOGMACHINE_ELEVENLABS_MODEL_ID
    stability: float = DEFAULT_DIALOGMACHINE_ELEVENLABS_STABILITY
    similarity_boost: float = DEFAULT_DIALOGMACHINE_ELEVENLABS_SIMILARITY_BOOST
    style: float = DEFAULT_DIALOGMACHINE_ELEVENLABS_STYLE
    speed: float = DEFAULT_DIALOGMACHINE_ELEVENLABS_SPEED
    use_speaker_boost: bool = DEFAULT_DIALOGMACHINE_ELEVENLABS_USE_SPEAKER_BOOST
    language_override_enabled: bool = DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_OVERRIDE_ENABLED
    language_code: str = DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE
    available_models: list[DialogmachineElevenlabsModelOption] = Field(
        default_factory=lambda: [
            DialogmachineElevenlabsModelOption(
                id=model_id, label=DIALOGMACHINE_ELEVENLABS_MODEL_LABELS[model_id]
            )
            for model_id in SUPPORTED_DIALOGMACHINE_ELEVENLABS_MODELS
        ]
    )


class DialogmachineElevenlabsSettingsUpdateRequest(BaseModel):
    voice_id: str | None = None
    voice_name: str | None = None
    model_id: str | None = None
    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    similarity_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
    use_speaker_boost: bool | None = None
    language_override_enabled: bool | None = None
    language_code: str | None = None


class DialogmachineVoiceTokenRequest(BaseModel):
    voice_config: dict[str, object] | None = Field(default=None)


class DialogmachineVoiceTokenResponse(BaseModel):
    token: str
    relationship_id: str
    expires_in: int
    ws_url: str


class DialogmachineDialRequest(BaseModel):
    to_number: str = Field(..., description="E.164 phone number (e.g., +14155551234)")
    ivr_goal: str | None = Field(default=None, description="Optional IVR goal")


class DialogmachineTwilioCallTranscriptMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    call_sid: str | None = None
    call_id: str | None = None
    call_mode: str | None = None


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/hot-context",
    response_model=DialogmachineHotContextResponse,
)
async def get_hot_context(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Read hot_context.md for a companion/test-user relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        workspace = get_workspace(relationship.id)
        content = workspace.read(HOT_CONTEXT_FILE)
        return DialogmachineHotContextResponse(
            relationship_id=str(relationship.id),
            content=content or "",
            exists=content is not None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to load hot context for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load hot context",
        )


@router.put(
    "/companions/{companion_id}/test-users/{user_id}/hot-context",
    response_model=DialogmachineHotContextResponse,
)
async def put_hot_context(
    companion_id: UUID,
    user_id: str,
    body: DialogmachineHotContextUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Overwrite hot_context.md for a companion/test-user relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        workspace = get_workspace(relationship.id)
        workspace.write(HOT_CONTEXT_FILE, body.content)
        return DialogmachineHotContextResponse(
            relationship_id=str(relationship.id),
            content=body.content,
            exists=True,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to write hot context for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write hot context",
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/prompt-override",
    response_model=DialogmachinePromptOverrideResponse,
)
async def get_prompt_override(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get dialogmachine prompt override for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        return DialogmachinePromptOverrideResponse(
            relationship_id=str(relationship.id),
            prompt_override=_extract_prompt_override(relationship.config or {}),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get prompt override for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get prompt override",
        )


@router.put(
    "/companions/{companion_id}/test-users/{user_id}/prompt-override",
    response_model=DialogmachinePromptOverrideResponse,
)
async def put_prompt_override(
    companion_id: UUID,
    user_id: str,
    body: DialogmachinePromptOverrideUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Set/clear dialogmachine prompt override for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        current_config = relationship.config if isinstance(relationship.config, dict) else {}
        dialog_cfg = current_config.get("dialogmachine")
        if not isinstance(dialog_cfg, dict):
            dialog_cfg = {}

        prompt_override = None
        if isinstance(body.prompt_override, str) and body.prompt_override.strip():
            prompt_override = body.prompt_override.strip()

        dialog_cfg["prompt_override"] = prompt_override
        updated_config = dict(current_config)
        updated_config["dialogmachine"] = dialog_cfg

        await conn.execute(
            """
            UPDATE relationships
            SET config = $2::jsonb, version = version + 1
            WHERE id = $1
            """,
            relationship.id,
            json.dumps(updated_config),
        )

        return DialogmachinePromptOverrideResponse(
            relationship_id=str(relationship.id),
            prompt_override=prompt_override,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to write prompt override for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write prompt override",
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/guardrails",
    response_model=DialogmachineGuardrailsResponse,
)
async def get_guardrails(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get dialogmachine guardrails for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        return DialogmachineGuardrailsResponse(
            relationship_id=str(relationship.id),
            guardrails=_extract_guardrails(relationship.config or {}),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get guardrails for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get guardrails",
        )


@router.put(
    "/companions/{companion_id}/test-users/{user_id}/guardrails",
    response_model=DialogmachineGuardrailsResponse,
)
async def put_guardrails(
    companion_id: UUID,
    user_id: str,
    body: DialogmachineGuardrailsUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Set/clear dialogmachine guardrails for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        current_config = relationship.config if isinstance(relationship.config, dict) else {}
        dialog_cfg = current_config.get("dialogmachine")
        if not isinstance(dialog_cfg, dict):
            dialog_cfg = {}

        guardrails = None
        if isinstance(body.guardrails, str) and body.guardrails.strip():
            guardrails = body.guardrails.strip()

        dialog_cfg["guardrails"] = guardrails
        updated_config = dict(current_config)
        updated_config["dialogmachine"] = dialog_cfg

        await conn.execute(
            """
            UPDATE relationships
            SET config = $2::jsonb, version = version + 1
            WHERE id = $1
            """,
            relationship.id,
            json.dumps(updated_config),
        )

        return DialogmachineGuardrailsResponse(
            relationship_id=str(relationship.id),
            guardrails=guardrails,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to write guardrails for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write guardrails",
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/background-noise",
    response_model=DialogmachineBackgroundNoiseResponse,
)
async def get_background_noise(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get dialogmachine simulate background-noise settings for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        enabled, noise_type, volume = _extract_background_noise(relationship.config or {})
        return DialogmachineBackgroundNoiseResponse(
            relationship_id=str(relationship.id),
            enabled=enabled,
            noise_type=noise_type,
            volume=volume,
            available_noise_types=list(AVAILABLE_BACKGROUND_NOISE_TYPES),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to load background noise settings for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load background noise settings",
        )


@router.put(
    "/companions/{companion_id}/test-users/{user_id}/background-noise",
    response_model=DialogmachineBackgroundNoiseResponse,
)
async def put_background_noise(
    companion_id: UUID,
    user_id: str,
    body: DialogmachineBackgroundNoiseUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Set dialogmachine simulate background-noise settings for a relationship."""
    if not is_valid_background_noise_type(body.noise_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported background noise type: {body.noise_type}",
        )
    noise_type = normalize_background_noise_type(body.noise_type)

    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        current_config = relationship.config if isinstance(relationship.config, dict) else {}
        dialog_cfg = current_config.get("dialogmachine")
        if not isinstance(dialog_cfg, dict):
            dialog_cfg = {}

        dialog_cfg["background_noise"] = {
            "enabled": bool(body.enabled),
            "type": noise_type,
            "volume": clamp_background_noise_volume(body.volume),
        }

        updated_config = dict(current_config)
        updated_config["dialogmachine"] = dialog_cfg

        await conn.execute(
            """
            UPDATE relationships
            SET config = $2::jsonb, version = version + 1
            WHERE id = $1
            """,
            relationship.id,
            json.dumps(updated_config),
        )

        return DialogmachineBackgroundNoiseResponse(
            relationship_id=str(relationship.id),
            enabled=bool(body.enabled),
            noise_type=noise_type,
            volume=clamp_background_noise_volume(body.volume),
            available_noise_types=list(AVAILABLE_BACKGROUND_NOISE_TYPES),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to write background noise settings for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write background noise settings",
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/tool-calls",
    response_model=DialogmachineToolCallsResponse,
)
async def get_tool_calls(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get DialogMachine task delegation (tool calls) setting for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        selected_tools = _extract_dialogmachine_tools(relationship.config or {})
        return DialogmachineToolCallsResponse(
            relationship_id=str(relationship.id),
            enabled=DIALOGMACHINE_TOOL_TASK_DELEGATION in selected_tools,
            selected_tools=selected_tools,
            available_tools=list(AVAILABLE_DIALOGMACHINE_TOOLS),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to load tool-calls setting for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load tool-calls setting",
        )


@router.put(
    "/companions/{companion_id}/test-users/{user_id}/tool-calls",
    response_model=DialogmachineToolCallsResponse,
)
async def put_tool_calls(
    companion_id: UUID,
    user_id: str,
    body: DialogmachineToolCallsUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Set DialogMachine task delegation (tool calls) for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        current_config = relationship.config if isinstance(relationship.config, dict) else {}
        dialog_cfg = current_config.get("dialogmachine")
        if not isinstance(dialog_cfg, dict):
            dialog_cfg = {}
        selected_tools = _extract_dialogmachine_tools(current_config)

        if body.selected_tools is not None:
            selected_tools = _normalize_dialogmachine_tools([*body.selected_tools])
        elif body.enabled is not None:
            if body.enabled and DIALOGMACHINE_TOOL_TASK_DELEGATION not in selected_tools:
                selected_tools.append(DIALOGMACHINE_TOOL_TASK_DELEGATION)
            if not body.enabled and DIALOGMACHINE_TOOL_TASK_DELEGATION in selected_tools:
                selected_tools = [
                    tool for tool in selected_tools if tool != DIALOGMACHINE_TOOL_TASK_DELEGATION
                ]
            selected_tools = _normalize_dialogmachine_tools(selected_tools)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide selected_tools or enabled",
            )

        dialog_cfg["enable_task_delegation"] = DIALOGMACHINE_TOOL_TASK_DELEGATION in selected_tools
        dialog_cfg["tools"] = {"selected": selected_tools}
        updated_config = dict(current_config)
        updated_config["dialogmachine"] = dialog_cfg

        await conn.execute(
            """
            UPDATE relationships
            SET config = $2::jsonb, version = version + 1
            WHERE id = $1
            """,
            relationship.id,
            json.dumps(updated_config),
        )

        return DialogmachineToolCallsResponse(
            relationship_id=str(relationship.id),
            enabled=DIALOGMACHINE_TOOL_TASK_DELEGATION in selected_tools,
            selected_tools=selected_tools,
            available_tools=list(AVAILABLE_DIALOGMACHINE_TOOLS),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to write tool-calls setting for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write tool-calls setting",
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/llm",
    response_model=DialogmachineLlmSettingsResponse,
)
async def get_dialogmachine_llm_settings(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get DialogMachine workspace LLM settings for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        provider = _extract_dialogmachine_llm_provider(relationship.config or {})
        return DialogmachineLlmSettingsResponse(
            relationship_id=str(relationship.id),
            provider=provider,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to load LLM settings for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load LLM settings",
        )


@router.put(
    "/companions/{companion_id}/test-users/{user_id}/llm",
    response_model=DialogmachineLlmSettingsResponse,
)
async def put_dialogmachine_llm_settings(
    companion_id: UUID,
    user_id: str,
    body: DialogmachineLlmSettingsUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Persist DialogMachine workspace LLM settings for a relationship."""
    raw_provider = str(body.provider or "").strip()
    if not raw_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provider is required",
        )
    if raw_provider not in SUPPORTED_DIALOGMACHINE_LLM_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported llm provider: {raw_provider}",
        )

    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)

        async with conn.transaction():
            locked_row = await conn.fetchrow(
                """
                SELECT config
                FROM relationships
                WHERE id = $1
                FOR UPDATE
                """,
                relationship.id,
            )
            locked_config = locked_row["config"] if locked_row else {}
            if isinstance(locked_config, str):
                try:
                    locked_config = json.loads(locked_config)
                except json.JSONDecodeError:
                    locked_config = {}
            current_config = locked_config if isinstance(locked_config, dict) else {}

            dialog_cfg = current_config.get("dialogmachine")
            if not isinstance(dialog_cfg, dict):
                dialog_cfg = {}

            dialog_cfg["llm"] = {"provider": raw_provider}
            # Legacy compatibility for older read paths.
            dialog_cfg["llm_provider"] = raw_provider
            updated_config = dict(current_config)
            updated_config["dialogmachine"] = dialog_cfg

            await conn.execute(
                """
                UPDATE relationships
                SET config = $2::jsonb, version = version + 1
                WHERE id = $1
                """,
                relationship.id,
                json.dumps(updated_config),
            )

        return DialogmachineLlmSettingsResponse(
            relationship_id=str(relationship.id),
            provider=raw_provider,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to write LLM settings for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write LLM settings",
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/elevenlabs",
    response_model=DialogmachineElevenlabsSettingsResponse,
)
async def get_dialogmachine_elevenlabs_settings(
    companion_id: UUID,
    user_id: str,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get DialogMachine workspace ElevenLabs settings for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        elevenlabs_cfg = _extract_dialogmachine_elevenlabs(relationship.config or {})
        return DialogmachineElevenlabsSettingsResponse(
            relationship_id=str(relationship.id),
            voice_id=elevenlabs_cfg["voice_id"],
            voice_name=elevenlabs_cfg["voice_name"],
            model_id=elevenlabs_cfg["model_id"],
            stability=elevenlabs_cfg["stability"],
            similarity_boost=elevenlabs_cfg["similarity_boost"],
            style=elevenlabs_cfg["style"],
            speed=elevenlabs_cfg["speed"],
            use_speaker_boost=elevenlabs_cfg["use_speaker_boost"],
            language_override_enabled=elevenlabs_cfg["language_override_enabled"],
            language_code=elevenlabs_cfg["language_code"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to load ElevenLabs settings for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load ElevenLabs settings",
        )


@router.put(
    "/companions/{companion_id}/test-users/{user_id}/elevenlabs",
    response_model=DialogmachineElevenlabsSettingsResponse,
)
async def put_dialogmachine_elevenlabs_settings(
    companion_id: UUID,
    user_id: str,
    body: DialogmachineElevenlabsSettingsUpdateRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Persist DialogMachine workspace ElevenLabs settings for a relationship."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        updates = body.model_dump(exclude_unset=True)

        async with conn.transaction():
            locked_row = await conn.fetchrow(
                """
                SELECT config
                FROM relationships
                WHERE id = $1
                FOR UPDATE
                """,
                relationship.id,
            )
            locked_config = locked_row["config"] if locked_row else {}
            if isinstance(locked_config, str):
                try:
                    locked_config = json.loads(locked_config)
                except json.JSONDecodeError:
                    locked_config = {}
            current_config = locked_config if isinstance(locked_config, dict) else {}

            dialog_cfg = current_config.get("dialogmachine")
            if not isinstance(dialog_cfg, dict):
                dialog_cfg = {}

            merged = _extract_dialogmachine_elevenlabs(current_config)

            if "voice_id" in updates:
                voice_id = str(updates.get("voice_id") or "").strip()
                merged["voice_id"] = voice_id or None
            if "voice_name" in updates:
                voice_name = str(updates.get("voice_name") or "").strip()
                merged["voice_name"] = voice_name or None
            if "model_id" in updates:
                merged["model_id"] = _normalize_elevenlabs_model_id(updates.get("model_id"))
            if "stability" in updates:
                merged["stability"] = _clamp_float(
                    updates.get("stability"),
                    min_value=0.0,
                    max_value=1.0,
                    default=DEFAULT_DIALOGMACHINE_ELEVENLABS_STABILITY,
                )
            if "similarity_boost" in updates:
                merged["similarity_boost"] = _clamp_float(
                    updates.get("similarity_boost"),
                    min_value=0.0,
                    max_value=1.0,
                    default=DEFAULT_DIALOGMACHINE_ELEVENLABS_SIMILARITY_BOOST,
                )
            if "style" in updates:
                merged["style"] = _clamp_float(
                    updates.get("style"),
                    min_value=0.0,
                    max_value=1.0,
                    default=DEFAULT_DIALOGMACHINE_ELEVENLABS_STYLE,
                )
            if "speed" in updates:
                merged["speed"] = _clamp_float(
                    updates.get("speed"),
                    min_value=0.7,
                    max_value=1.2,
                    default=DEFAULT_DIALOGMACHINE_ELEVENLABS_SPEED,
                )
            if "use_speaker_boost" in updates:
                merged["use_speaker_boost"] = bool(updates.get("use_speaker_boost"))
            if "language_override_enabled" in updates:
                merged["language_override_enabled"] = bool(updates.get("language_override_enabled"))
            if "language_code" in updates:
                merged["language_code"] = _normalize_language_code(updates.get("language_code"))

            merged["language_code"] = _normalize_language_code(
                merged.get("language_code", DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE)
            )
            if not merged["language_override_enabled"]:
                merged["language_code"] = DEFAULT_DIALOGMACHINE_ELEVENLABS_LANGUAGE_CODE

            dialog_cfg["elevenlabs"] = merged
            updated_config = dict(current_config)
            updated_config["dialogmachine"] = dialog_cfg

            await conn.execute(
                """
                UPDATE relationships
                SET config = $2::jsonb, version = version + 1
                WHERE id = $1
                """,
                relationship.id,
                json.dumps(updated_config),
            )

        return DialogmachineElevenlabsSettingsResponse(
            relationship_id=str(relationship.id),
            voice_id=merged["voice_id"],
            voice_name=merged["voice_name"],
            model_id=merged["model_id"],
            stability=merged["stability"],
            similarity_boost=merged["similarity_boost"],
            style=merged["style"],
            speed=merged["speed"],
            use_speaker_boost=merged["use_speaker_boost"],
            language_override_enabled=merged["language_override_enabled"],
            language_code=merged["language_code"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to write ElevenLabs settings for companion %s user %s: %s",
            companion_id,
            user_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write ElevenLabs settings",
        )


@router.get("/elevenlabs/voices", response_model=list[DialogmachineElevenlabsVoice])
async def list_elevenlabs_voices(_user: User = Depends(get_current_user)):
    """List all voices from the configured ElevenLabs account."""
    api_key = _get_elevenlabs_api_key()
    if not api_key:
        logger.warning(
            "[DIALOGMACHINE] ElevenLabs API key missing. Using local ElevenLabs voice fallback list."
        )
        return _local_elevenlabs_voices_fallback()

    try:
        # ElevenLabs docs currently use /v2/voices. Keep /v1 fallback for compatibility.
        endpoints = [
            "https://api.elevenlabs.io/v2/voices",
            f"{ELEVENLABS_API_BASE_URL}/voices",
        ]
        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=20.0) as client:
            for idx, url in enumerate(endpoints):
                candidate = await client.get(
                    url,
                    headers={"xi-api-key": api_key, "accept": "application/json"},
                )
                if candidate.status_code == 404 and idx < len(endpoints) - 1:
                    logger.info(
                        "[DIALOGMACHINE] ElevenLabs endpoint not found, trying fallback: %s", url
                    )
                    continue
                response = candidate
                break

        if response is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to fetch ElevenLabs voices (no response)",
            )

        if response.status_code >= 400:
            upstream_detail = _extract_upstream_error_detail(response)
            logger.warning(
                "ElevenLabs voices request failed: status=%s detail=%s",
                response.status_code,
                upstream_detail,
            )
            if response.status_code in (401, 403):
                logger.warning(
                    "[DIALOGMACHINE] Falling back to local ElevenLabs voices due to permission/auth issue."
                )
                return _local_elevenlabs_voices_fallback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch ElevenLabs voices ({response.status_code}): {upstream_detail}",
            )
        payload = response.json()
        rows: list[DialogmachineElevenlabsVoice] = []
        for item in payload.get("voices", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("voice_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not voice_id or not name:
                continue
            rows.append(
                DialogmachineElevenlabsVoice(
                    voice_id=voice_id,
                    name=name,
                    category=str(item.get("category")).strip() if item.get("category") else None,
                )
            )
        rows.sort(key=lambda voice: voice.name.lower())
        return rows
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch ElevenLabs voices: %s", e)
        logger.warning(
            "[DIALOGMACHINE] Falling back to local ElevenLabs voices after fetch exception."
        )
        return _local_elevenlabs_voices_fallback()


@router.post("/elevenlabs/voices/clone", response_model=DialogmachineElevenlabsVoice)
async def clone_elevenlabs_voice(
    name: str = Form(...),
    audio: list[UploadFile] = File(...),
    _user: User = Depends(get_current_user),
):
    """Create a new cloned voice in ElevenLabs from a recorded/uploaded audio sample."""
    api_key = _get_elevenlabs_api_key()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ElevenLabs API key not configured (ELEVEN_API_KEY)",
        )

    voice_name = name.strip()
    if not voice_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Voice name is required"
        )

    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="At least one audio file is required"
        )

    try:
        multipart_files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, clip in enumerate(audio):
            filename = clip.filename or f"clone-audio-{index + 1}.wav"
            content = await clip.read()
            if not content:
                continue
            multipart_files.append(
                (
                    "files",
                    (
                        filename,
                        content,
                        clip.content_type or "audio/wav",
                    ),
                )
            )

        if not multipart_files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is empty"
            )

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{ELEVENLABS_API_BASE_URL}/voices/add",
                headers={"xi-api-key": api_key},
                data={"name": voice_name},
                files=multipart_files,
            )

        if response.status_code >= 400:
            upstream_detail = _extract_upstream_error_detail(response)
            logger.warning(
                "ElevenLabs voice clone failed: status=%s detail=%s",
                response.status_code,
                upstream_detail,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to clone voice in ElevenLabs ({response.status_code}): {upstream_detail}",
            )

        payload = response.json() if response.content else {}
        voice_id = str(payload.get("voice_id") or "").strip()
        voice_label = str(payload.get("name") or voice_name).strip()
        if not voice_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ElevenLabs clone response missing voice_id",
            )
        return DialogmachineElevenlabsVoice(
            voice_id=voice_id,
            name=voice_label,
            category=str(payload.get("category")).strip() if payload.get("category") else "cloned",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to clone ElevenLabs voice: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clone ElevenLabs voice",
        )


@router.post(
    "/companions/{companion_id}/test-users/{user_id}/simulate-token",
    response_model=DialogmachineVoiceTokenResponse,
)
async def create_simulate_token(
    companion_id: UUID,
    user_id: str,
    request: DialogmachineVoiceTokenRequest | None = None,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a voice websocket token for simulated phone-call testing."""
    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await _get_or_create_relationship(conn, companion_id, user_id)

        # Simulate mode starts with direct Fast Brain conversation path.
        # Force legacy mode (unless relationship is already locked to another mode).
        if relationship.context_mode != "legacy":
            if relationship.context_mode_locked:
                logger.warning(
                    "[DIALOGMACHINE] Simulate requested legacy context mode but relationship %s "
                    "is locked to %s",
                    relationship.id,
                    relationship.context_mode,
                )
            else:
                await conn.execute(
                    """
                    UPDATE relationships
                    SET context_mode = 'legacy',
                        version = version + 1
                    WHERE id = $1
                    """,
                    relationship.id,
                )
                refreshed = await RelationshipRepository.get_by_id(conn, relationship.id)
                if refreshed:
                    relationship = refreshed
                logger.info(
                    "[DIALOGMACHINE] Simulate forced context_mode=legacy for relationship %s",
                    relationship.id,
                )

        request_fields: set[str] = set()
        if request and request.voice_config:
            request_fields = set(request.voice_config.keys())
            voice_config = VoiceConfig(**request.voice_config)
        else:
            voice_config = create_default_voice_config()
        voice_config = normalize_voice_config(voice_config)

        # Workspace LLM override for simulate sessions.
        # DialogMachine simulate always runs through Fast Brain pipeline.
        selected_fast_model_provider = voice_config.llm_provider or LLMProvider.GEMINI_25_FLASH
        if "llm_provider" not in request_fields:
            llm_provider = _extract_dialogmachine_llm_provider(relationship.config or {})
            try:
                selected_fast_model_provider = LLMProvider(llm_provider)
            except ValueError:
                selected_fast_model_provider = LLMProvider.GEMINI_25_FLASH
        if selected_fast_model_provider == LLMProvider.FAST_BRAIN:
            selected_fast_model_provider = LLMProvider.GEMINI_25_FLASH
        voice_config.fast_brain_model_provider = selected_fast_model_provider
        voice_config.llm_provider = LLMProvider.FAST_BRAIN

        # Use relationship workspace defaults unless explicitly overridden in request.
        rel_noise_enabled, rel_noise_type, rel_noise_volume = _extract_background_noise(
            relationship.config or {}
        )
        if "background_noise_enabled" not in request_fields:
            voice_config.background_noise_enabled = rel_noise_enabled
        if "background_noise_type" not in request_fields:
            voice_config.background_noise_type = rel_noise_type
        if "background_noise_volume" not in request_fields:
            voice_config.background_noise_volume = rel_noise_volume

        # Normalize values to avoid invalid or empty noise keys in runtime pipeline.
        voice_config.background_noise_type = normalize_background_noise_type(
            voice_config.background_noise_type
        )
        voice_config.background_noise_volume = clamp_background_noise_volume(
            voice_config.background_noise_volume
        )

        # DialogMachine defaults to direct Fast Brain responses (no Slow Brain delegation).
        if "fast_brain_delegate_enabled" not in request_fields:
            voice_config.fast_brain_delegate_enabled = _extract_task_delegation_enabled(
                relationship.config or {}
            )
        if "fast_brain_end_call_enabled" not in request_fields:
            voice_config.fast_brain_end_call_enabled = _extract_end_call_enabled(
                relationship.config or {}
            )

        # Workspace ElevenLabs overrides (voice/model/params) for simulate sessions.
        relationship_elevenlabs = _extract_dialogmachine_elevenlabs(relationship.config or {})
        if (
            voice_config.tts_provider
            and str(voice_config.tts_provider.value).lower() == "elevenlabs"
        ):
            if "tts_voice_id" not in request_fields and relationship_elevenlabs["voice_id"]:
                voice_config.tts_voice_id = relationship_elevenlabs["voice_id"]
            if "voice_name" not in request_fields and relationship_elevenlabs["voice_name"]:
                voice_config.voice_name = relationship_elevenlabs["voice_name"]
            if "elevenlabs_model_id" not in request_fields:
                voice_config.elevenlabs_model_id = relationship_elevenlabs["model_id"]
            if "elevenlabs_stability" not in request_fields:
                voice_config.elevenlabs_stability = relationship_elevenlabs["stability"]
            if "elevenlabs_similarity_boost" not in request_fields:
                voice_config.elevenlabs_similarity_boost = relationship_elevenlabs[
                    "similarity_boost"
                ]
            if "elevenlabs_style" not in request_fields:
                voice_config.elevenlabs_style = relationship_elevenlabs["style"]
            if "elevenlabs_speed" not in request_fields:
                voice_config.elevenlabs_speed = relationship_elevenlabs["speed"]
            if "elevenlabs_use_speaker_boost" not in request_fields:
                voice_config.elevenlabs_use_speaker_boost = relationship_elevenlabs[
                    "use_speaker_boost"
                ]
            if "elevenlabs_language_code" not in request_fields:
                voice_config.elevenlabs_language_code = (
                    relationship_elevenlabs["language_code"]
                    if relationship_elevenlabs["language_override_enabled"]
                    else None
                )

        marker_id = uuid4()
        token, _expires_at = _create_voice_token(
            relationship_id=relationship.id,
            api_key_id=marker_id,
            voice_config=voice_config,
        )

        return DialogmachineVoiceTokenResponse(
            token=token,
            relationship_id=str(relationship.id),
            expires_in=3600,
            ws_url=f"/v2/relationships/{relationship.id}/voice/connect",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to create simulate token for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create simulate token",
        )


@router.post(
    "/companions/{companion_id}/test-users/{user_id}/dial",
    response_model=TwilioDialOutResponse,
)
async def dial(
    companion_id: UUID,
    user_id: str,
    request: DialogmachineDialRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Initiate a real Twilio dial-out call for dialogmachine testing."""
    if not re.match(r"^\+[1-9]\d{1,14}$", request.to_number):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number must be in E.164 format (e.g., +14155551234)",
        )

    call_id: str | None = None
    try:
        companion = await _get_owned_companion(conn, companion_id, user.id)
        creds = _resolve_twilio_credentials(companion)
        if not creds:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Twilio credentials not configured",
            )
        account_sid, auth_token, from_number = creds

        relationship = await _get_or_create_relationship(conn, companion_id, user_id)
        dialogmachine_elevenlabs = _extract_dialogmachine_elevenlabs(relationship.config or {})
        dialogmachine_llm_provider = _extract_dialogmachine_llm_provider(relationship.config or {})

        call_id = str(uuid4())
        _pending_calls[call_id] = {
            "companion_id": str(companion_id),
            "user_id": user_id,
            "relationship_id": str(relationship.id),
            "api_key_id": str(uuid4()),  # dashboard marker
            "ivr_goal": request.ivr_goal,
            "source": "dialogmachine_dial",
            "dialogmachine_elevenlabs": dialogmachine_elevenlabs,
            "dialogmachine_llm_provider": dialogmachine_llm_provider,
            "created_at": datetime.now(UTC).isoformat(),
        }

        base_url = _get_public_base_url()
        twiml_url = f"{base_url}/twilio/twiml/{call_id}"
        status_callback_url = f"{base_url}/twilio/status-callback"

        client = _get_twilio_client_for(account_sid, auth_token)
        call = client.calls.create(
            to=request.to_number,
            from_=from_number,
            url=twiml_url,
            method="POST",
            status_callback=status_callback_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
        )
        _call_auth_tokens[call.sid] = auth_token
        _schedule_twilio_call_event(
            call_sid=call.sid,
            event_type="dial_out_initiated",
            status=call.status,
            call_id=call_id,
            companion_id=companion_id,
            relationship_id=relationship.id,
            user_id=user_id,
            direction="outbound",
            from_number=from_number,
            to_number=request.to_number,
            payload={"source": "dialogmachine_dial"},
        )

        return TwilioDialOutResponse(
            call_sid=call.sid,
            status=call.status,
            call_id=call_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        if call_id:
            _pending_calls.pop(call_id, None)
        logger.error("Failed to dial for companion %s user %s: %s", companion_id, user_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate call: {e!s}",
        )


@router.get(
    "/companions/{companion_id}/test-users/{user_id}/twilio-calls/{call_sid}/messages",
    response_model=list[DialogmachineTwilioCallTranscriptMessage],
)
async def get_twilio_call_messages(
    companion_id: UUID,
    user_id: str,
    call_sid: str,
    limit: int = Query(default=500, ge=1, le=2000),
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Get transcript messages for a specific Twilio call."""
    if not re.match(r"^CA[0-9a-fA-F]{32}$", call_sid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Twilio Call SID format",
        )

    try:
        await _get_owned_companion(conn, companion_id, user.id)
        relationship = await RelationshipRepository.get_by_companion_and_user(
            conn, companion_id=companion_id, user_id=user_id
        )
        if not relationship:
            return []

        rows = await conn.fetch(
            """
            SELECT id::text AS id, role, content, created_at, metadata
            FROM messages
            WHERE relationship_id = $1
              AND input_modality = 'voice'
              AND metadata->>'channel' = 'twilio'
              AND metadata->>'call_sid' = $2
            ORDER BY created_at ASC
            LIMIT $3
            """,
            relationship.id,
            call_sid,
            limit,
        )

        return [
            DialogmachineTwilioCallTranscriptMessage(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"].isoformat() if row.get("created_at") else "",
                call_sid=(row.get("metadata") or {}).get("call_sid"),
                call_id=(row.get("metadata") or {}).get("call_id"),
                call_mode=(row.get("metadata") or {}).get("call_mode"),
            )
            for row in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to load call transcript for companion %s user %s: %s", companion_id, user_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load call transcript",
        )
