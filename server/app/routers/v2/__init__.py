"""API v2 Routers."""

from .behaviors import router as behaviors_router
from .inbox import router as inbox_router
from .memory import router as memory_router
from .messages import router as messages_router
from .relationships import router as relationships_router
from .sessions import router as sessions_router
from .summaries import router as summaries_router
from .websockets import router as websockets_router

__all__ = [
    "behaviors_router",
    "inbox_router",
    "memory_router",
    "messages_router",
    "relationships_router",
    "sessions_router",
    "summaries_router",
    "websockets_router",
]
