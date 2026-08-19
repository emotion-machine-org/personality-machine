# server/app/routers/voice/__init__.py
"""Voice module for real-time voice conversations.

This module provides:
- v1 routes: Backwards-compatible /sessions API
- v2 routes: Relationship-based /v2/.../voice API

Usage:
    from app.routers.voice import v1_router, v2_router

    app.include_router(v1_router)
    app.include_router(v2_router)
"""

from .context import (
    VoiceContextConfig,
    VoiceContextInjector,
    VoiceSessionState,
)
from .context_processor import (
    VoiceContextConfig as VoiceContextConfigV2,
)
from .context_processor import (
    VoiceContextProcessor,
)
from .context_processor import (
    VoiceSessionState as VoiceSessionStateV2,
)
from .fast_brain_llm import (
    FastBrainConfig,
    FastBrainLLMService,
    build_fast_brain_llm_service,
)

# Re-export commonly used models and utilities
from .models import (
    PipelineType,
    SessionCreate,
    SessionCreated,
    VoiceConfig,
    VoiceTokenRequest,
    VoiceTokenResponse,
)
from .openclaw import (
    OpenClawClient,
    OpenClawConfig,
    OpenClawTextRequest,
    OpenClawTextResponse,
    process_with_openclaw,
)
from .openclaw import router as openclaw_router
from .openclaw_llm import (
    OpenClawLLMService,
    build_openclaw_llm_service,
)
from .pipeline import (
    build_voice_pipeline,
    create_default_voice_config,
    normalize_voice_config,
)
from .providers import (
    LLMProvider,
    STTProvider,
    TTSProvider,
    get_all_voice_mappings,
    get_voice_id,
)
from .twilio import router as twilio_router
from .v1 import (
    ShareSessionContext,
    cancel_active_session_tasks,
    create_share_voice_session,
)
from .v1 import router as v1_router
from .v2 import router as v2_router
from .v2 import voice_connection_manager
from .workspace_api import router as workspace_router

__all__ = [
    # Fast Brain / Slow Brain
    "FastBrainConfig",
    "FastBrainLLMService",
    # Providers
    "LLMProvider",
    # OpenClaw integration
    "OpenClawClient",
    "OpenClawConfig",
    "OpenClawLLMService",
    "OpenClawTextRequest",
    "OpenClawTextResponse",
    # Models
    "PipelineType",
    "STTProvider",
    "SessionCreate",
    "SessionCreated",
    "ShareSessionContext",
    "TTSProvider",
    "VoiceConfig",
    # Context (v1 - legacy)
    "VoiceContextConfig",
    "VoiceContextConfigV2",
    "VoiceContextInjector",
    # Context (v2 - processor-based)
    "VoiceContextProcessor",
    "VoiceSessionState",
    "VoiceSessionStateV2",
    "VoiceTokenRequest",
    "VoiceTokenResponse",
    "build_fast_brain_llm_service",
    "build_openclaw_llm_service",
    # Pipeline
    "build_voice_pipeline",
    # v1 utilities
    "cancel_active_session_tasks",
    "create_default_voice_config",
    "create_share_voice_session",
    "get_all_voice_mappings",
    "get_voice_id",
    "normalize_voice_config",
    "openclaw_router",
    "process_with_openclaw",
    "twilio_router",
    # Routers
    "v1_router",
    "v2_router",
    # v2 utilities
    "voice_connection_manager",
    "workspace_router",
]
