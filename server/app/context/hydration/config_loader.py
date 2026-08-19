"""ConfigLoader: Fetches companion config and core memories.

Responsible for:
- Loading companion config from latest version
- Fetching core memories
- Composing the core system prompt with guidance
- Caching the result
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from ...models.companion import CompanionConfig, parse_companion_config_payload
from ...services.cache_manager import cache, ttl_from_env
from .cache_keys import CacheNamespace, config_cache_key

logger = logging.getLogger(__name__)

# Cache TTL for companion config (default 30s)
_CONFIG_TTL_S = ttl_from_env("COMPANION_CONFIG_CACHE_TTL_S", 300.0)

# Default prompt when none configured
_DEFAULT_SYSTEM_PROMPT = "You are a helpful and friendly companion."

# Guidance text prepended to core memories
_CORE_MEMORY_GUIDANCE = (
    "You have access to the companion's core memories. "
    "Use them as subtle background knowledge to personalize responses.\n"
    "Do not restate, quote, or force-inject core memories. "
    "Only apply them when clearly relevant to the user's intent.\n"
    "Avoid fabricating details and prioritize the user's current request over generic facts."
)


@dataclass(frozen=True, slots=True)
class ConfigResult:
    """Result of loading companion config."""

    config: CompanionConfig
    core_system_prompt: str
    core_memories: tuple[str, ...] = ()  # Tuple for immutability (frozen dataclass)
    from_cache: bool = False


def compose_core_system_prompt(base_prompt: str, core_memories: list[str]) -> str:
    """Compose the core system prompt from base prompt and core memories.

    Args:
        base_prompt: The base system prompt from companion config
        core_memories: List of core memory strings

    Returns:
        Composed prompt with core memories section prepended (if any)
    """
    base = (base_prompt or "").strip() or _DEFAULT_SYSTEM_PROMPT

    if not core_memories:
        return base

    lines = ["# CORE MEMORIES", _CORE_MEMORY_GUIDANCE]
    for mem in core_memories:
        lines.append(f"- {mem.strip()}")
    lines.append("")
    lines.append(base)

    return "\n".join(lines).strip()


class ConfigLoader:
    """Loads and caches companion config with core memories."""

    @staticmethod
    async def load(
        conn: asyncpg.Connection,
        companion_id: UUID,
        *,
        use_cache: bool = True,
        preloaded_config: CompanionConfig | None = None,
    ) -> ConfigResult:
        """Load companion config and compose core system prompt.

        Args:
            conn: Database connection
            companion_id: The companion ID
            use_cache: Whether to use cache (default True)
            preloaded_config: Optional pre-loaded config (skips config query but still fetches core memories)

        Returns:
            ConfigResult with config and composed system prompt
        """
        cache_key = config_cache_key(companion_id)
        namespace = CacheNamespace.COMPANION_CONFIG

        # Check cache first
        if use_cache:
            cached = cache.get(namespace, cache_key)
            if cached is not None:
                config, core_prompt, core_memories = cached
                return ConfigResult(
                    config=config,
                    core_system_prompt=core_prompt,
                    core_memories=core_memories,
                    from_cache=True,
                )

        # Fetch from database
        try:
            row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(cv.config, cv.system_prompt::jsonb) as config,
                    COALESCE(cm.memories, ARRAY[]::text[]) as core_memories
                FROM companions c
                LEFT JOIN LATERAL (
                    SELECT config, system_prompt
                    FROM companion_versions
                    WHERE companion_id = c.id
                    ORDER BY version_number DESC, created_at DESC
                    LIMIT 1
                ) cv ON true
                LEFT JOIN LATERAL (
                    SELECT array_agg(content ORDER BY created_at) as memories
                    FROM memories
                    WHERE companion_id = c.id AND is_core = true
                ) cm ON true
                WHERE c.id = $1
                """,
                companion_id,
            )
        except Exception as e:
            logger.error(
                "Failed to load config for companion %s: %s",
                companion_id,
                e,
                exc_info=True,
            )
            # Re-raise - config is required, don't hide database errors
            raise

        if not row:
            logger.warning("Companion %s not found", companion_id)
            return ConfigResult(
                config=CompanionConfig(),
                core_system_prompt=_DEFAULT_SYSTEM_PROMPT,
                from_cache=False,
            )

        # Parse config (use preloaded if available)
        if preloaded_config is not None:
            config = preloaded_config
        else:
            config = parse_companion_config_payload(row["config"])

        # Extract base prompt and compose with core memories
        base_prompt = ""
        if config.system_prompt:
            base_prompt = config.system_prompt.full_system_prompt or ""

        core_memories_list = list(row["core_memories"] or [])
        core_system_prompt = compose_core_system_prompt(base_prompt, core_memories_list)
        core_memories = tuple(core_memories_list)  # Immutable for frozen dataclass

        result = ConfigResult(
            config=config,
            core_system_prompt=core_system_prompt,
            core_memories=core_memories,
            from_cache=False,
        )

        # Cache the result
        if use_cache:
            cache.set(
                namespace, cache_key, (config, core_system_prompt, core_memories), _CONFIG_TTL_S
            )

        return result

    @staticmethod
    def invalidate(companion_id: UUID) -> None:
        """Invalidate cached config for a companion."""
        cache.delete(CacheNamespace.COMPANION_CONFIG, config_cache_key(companion_id))
