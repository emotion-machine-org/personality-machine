"""Cache key builders and namespace constants for ConfigLoader.

Note: User state, conversation state, and history caching has been moved
to repositories. This file only contains config-related cache keys now.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID


class CacheNamespace(str, Enum):
    """Cache namespaces for hydration layer."""

    COMPANION_CONFIG = "hydrator:config"


def config_cache_key(companion_id: UUID) -> str:
    """Build cache key for companion config + core memories."""
    return f"companion:{companion_id}"
