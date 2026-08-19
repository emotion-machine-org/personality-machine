"""Context hydration package.

Provides focused components for fetching orchestration context:
- Hydrator: Parallel data fetching with conn_factory support
- ContextAssembler: Builds final LLM messages from hydration data
- HydrationData: Raw data container for fetched context

Loaders (used internally by Hydrator):
- ConfigLoader: Companion config + core memories
- StateLoader: User/conversation state
- HistoryLoader: Message history

Backward compatibility:
- ContextHydrator: Deprecated, use Hydrator.fetch() instead
- HydratedContext: Alias for HydrationData
"""

from .config_loader import ConfigLoader
from .context_hydrator import (
    ContextAssembler,
    ContextHydrator,
    HydratedContext,
    HydrationData,
    Hydrator,
)
from .history_loader import HistoryLoader
from .state_loader import StateLoader

__all__ = [
    "ConfigLoader",
    "ContextAssembler",
    "ContextHydrator",
    "HistoryLoader",
    "HydratedContext",
    "HydrationData",
    "Hydrator",
    "StateLoader",
]
