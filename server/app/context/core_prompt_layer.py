from __future__ import annotations

"""Thin adapter around the existing core prompt assembly.

Phase 0/0.5 reuses the legacy builder prompt + core memories pipeline while we
migrate logic under `server/app/context/`.
"""

from typing import Tuple
from uuid import UUID

import asyncpg

from ..services.cache_manager import cache
from ..services.context_assembly import build_effective_system_prompt


async def load_core_prompt(conn: asyncpg.Connection, companion_id: UUID) -> Tuple[str, str]:
    """Return (effective_prompt, builder_prompt)."""

    return await build_effective_system_prompt(conn, companion_id=companion_id)


def bust_core_prompt_cache(companion_id: UUID) -> None:
    """Explicitly invalidate cached core prompts for a companion."""

    cache.delete("eff_prompt", str(companion_id))


__all__ = ["bust_core_prompt_cache", "load_core_prompt"]
