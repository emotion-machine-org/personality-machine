"""ContextHydrator: Unified DB query for orchestrator context.

This module re-exports from the refactored hydration package for backward
compatibility. New code should import from app.context.hydration directly.

The hydration package provides focused components:
- ConfigLoader: Companion config + core memories
- StateLoader: User/conversation state (READ-ONLY, no side effects)
- HistoryLoader: Message history
- ContextHydrator: Unified interface

Migration guide:
    # Old import
    from app.context.context_hydrator import ContextHydrator, HydratedContext

    # New import (preferred)
    from app.context.hydration import ContextHydrator, HydratedContext

    # Or use individual loaders for more control
    from app.context.hydration import ConfigLoader, StateLoader, HistoryLoader
"""

# Re-export everything for backward compatibility
from .hydration import ContextHydrator, HydratedContext
from .hydration.config_loader import compose_core_system_prompt

# Backward-compatible alias
_compose_core_system_prompt = compose_core_system_prompt

__all__ = [
    "ContextHydrator",
    "HydratedContext",
    "_compose_core_system_prompt",
    "compose_core_system_prompt",
]
