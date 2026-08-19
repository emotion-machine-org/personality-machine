# server/app/routers/sessions.py
"""Voice sessions router.

DEPRECATED: This module is a thin wrapper for backwards compatibility.
Use app.routers.voice instead for new code.

The voice functionality has been refactored into:
- app/routers/voice/v1.py - v1 API (/sessions)
- app/routers/voice/v2.py - v2 API (/v2/.../voice)
- app/routers/voice/pipeline.py - Pipeline builder
- app/routers/voice/services.py - Service builders
- app/routers/voice/providers.py - Provider enums
- app/routers/voice/models.py - Pydantic models
- app/routers/voice/context.py - Context injection
"""

from __future__ import annotations

from .voice.models import (
    PipelineType,
    SessionCreate,
    SessionCreated,
    VoiceConfig,
)
from .voice.pipeline import normalize_voice_config
from .voice.providers import (
    CARTESIA_VOICES,
    ELEVENLABS_VOICES,
    OPENAI_VOICES,
    LLMProvider,
    STTProvider,
    TTSProvider,
    get_voice_id,
)
from .voice.services import RawAudioSerializer

# Re-export the v1 router as the main router for backwards compatibility
# Re-export commonly used items for backwards compatibility
from .voice.v1 import (
    ShareSessionContext,
    _active_tasks,
    # Internal items used by client_api.py
    _register_session,
    _session_cfg,
    cancel_active_session_tasks,
    create_share_voice_session,
    router,
)

__all__ = [
    "CARTESIA_VOICES",
    "ELEVENLABS_VOICES",
    "OPENAI_VOICES",
    "LLMProvider",
    "PipelineType",
    "RawAudioSerializer",
    "STTProvider",
    "SessionCreate",
    "SessionCreated",
    "ShareSessionContext",
    "TTSProvider",
    "VoiceConfig",
    "_active_tasks",
    # Internal items for client_api.py backwards compat
    "_register_session",
    "_session_cfg",
    "cancel_active_session_tasks",
    "create_share_voice_session",
    "get_voice_id",
    "normalize_voice_config",
    "router",
]
