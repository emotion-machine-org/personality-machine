"""ContextHydrator: Parallel data fetching and context assembly for orchestration.

This module provides:
- HydrationData: Raw data fetched from database (config, profile, history, etc.)
- Hydrator: Parallel data fetching with conn_factory support
- ContextAssembler: Builds final LLM messages from hydration data

Design:
    1. Hydrator.fetch() - Parallel DB queries, returns HydrationData
    2. ContextAssembler.build_messages() - Takes HydrationData, produces messages list

This separation allows:
- Independent testing of data fetching vs context assembly
- Clear single responsibility per component
- Easy optimization of either layer independently
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

import asyncpg

from ...models.companion import CompanionConfig
from ...models.state import ConversationState
from ...utils.profile import build_profile_prompt_block, deep_merge_with_schema
from .config_loader import ConfigLoader, compose_core_system_prompt
from .history_loader import HistoryLoader
from .state_loader import StateLoader

logger = logging.getLogger(__name__)


# =============================================================================
# HydrationData: Raw data container
# =============================================================================


@dataclass
class HydrationData:
    """Raw data fetched from database for context assembly.

    This is a pure data container - no logic, just holds fetched data.
    All fields are populated by Hydrator.fetch().
    """

    # Identifiers
    companion_id: UUID
    external_user_id: str | None = None
    conversation_id: UUID | None = None

    # From ConfigLoader
    companion_config: CompanionConfig | None = None
    core_system_prompt: str = ""
    core_memories: list[str] = field(default_factory=list)

    # From StateLoader (user state)
    profile: dict[str, Any] | None = None
    state_version: int | None = None

    # From StateLoader (conversation state)
    conversation_state: ConversationState | None = None

    # From HistoryLoader
    history: list[dict[str, Any]] = field(default_factory=list)

    # Cache hit indicators (for debugging/tracing)
    config_from_cache: bool = False
    user_state_from_cache: bool = False
    conv_state_from_cache: bool = False
    history_from_cache: bool = False

    # Timing info
    fetch_ms: float = 0.0


# =============================================================================
# Hydrator: Parallel data fetching
# =============================================================================

# Type alias for connection factory (returns async context manager, not coroutine)
ConnectionFactory = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]


class Hydrator:
    """Fetches all context data from database, optionally in parallel.

    Usage:
        # Sequential (single connection)
        data = await Hydrator.fetch(conn, companion_id=..., ...)

        # Parallel (multiple connections from pool)
        data = await Hydrator.fetch(
            conn=None,
            conn_factory=get_db_connection,
            companion_id=...,
            ...
        )
    """

    @staticmethod
    async def fetch(
        conn: asyncpg.Connection | None,
        *,
        companion_id: UUID,
        conversation_id: UUID | None = None,
        external_user_id: str | None = None,
        use_cache: bool = True,
        preloaded_config: CompanionConfig | None = None,
        conn_factory: ConnectionFactory | None = None,
    ) -> HydrationData:
        """Fetch all context data, optionally in parallel.

        Args:
            conn: Database connection (used for sequential fetching)
            companion_id: The companion ID
            conversation_id: Optional conversation ID (for history and conv state)
            external_user_id: Optional user ID (for user state)
            use_cache: Whether to use cached values (default True)
            preloaded_config: Optional pre-loaded config (skips config query)
            conn_factory: Optional factory for parallel connections

        Returns:
            HydrationData with all fetched data
        """
        import time

        t0 = time.perf_counter()

        # Determine if we can run in parallel
        can_parallel = conn_factory is not None

        if can_parallel:
            data = await Hydrator._fetch_parallel(
                conn_factory=conn_factory,
                companion_id=companion_id,
                conversation_id=conversation_id,
                external_user_id=external_user_id,
                use_cache=use_cache,
                preloaded_config=preloaded_config,
            )
        else:
            if conn is None:
                raise ValueError("Either conn or conn_factory must be provided")
            data = await Hydrator._fetch_sequential(
                conn=conn,
                companion_id=companion_id,
                conversation_id=conversation_id,
                external_user_id=external_user_id,
                use_cache=use_cache,
                preloaded_config=preloaded_config,
            )

        data.fetch_ms = (time.perf_counter() - t0) * 1000.0
        return data

    @staticmethod
    async def _fetch_sequential(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        conversation_id: UUID | None,
        external_user_id: str | None,
        use_cache: bool,
        preloaded_config: CompanionConfig | None,
    ) -> HydrationData:
        """Fetch all data sequentially using a single connection."""
        data = HydrationData(
            companion_id=companion_id,
            external_user_id=external_user_id,
            conversation_id=conversation_id,
        )

        # 1. Config (always needed)
        config_result = await ConfigLoader.load(
            conn,
            companion_id,
            use_cache=use_cache,
            preloaded_config=preloaded_config,
        )
        data.companion_config = config_result.config
        data.core_system_prompt = config_result.core_system_prompt
        data.core_memories = list(config_result.core_memories)
        data.config_from_cache = config_result.from_cache

        # 2. User state (if external_user_id provided)
        if external_user_id:
            user_state_result = await StateLoader.load_user_state(
                conn,
                companion_id,
                external_user_id,
                use_cache=use_cache,
            )
            data.profile = user_state_result.profile
            data.state_version = user_state_result.state_version
            data.user_state_from_cache = user_state_result.from_cache

        # 3. Conversation state (if conversation_id provided)
        if conversation_id:
            conv_state_result = await StateLoader.load_conversation_state(
                conn,
                conversation_id,
                use_cache=use_cache,
            )
            data.conversation_state = conv_state_result.state
            data.conv_state_from_cache = conv_state_result.from_cache

            # 4. History (only if conversation_id)
            history_result = await HistoryLoader.load(
                conn,
                conversation_id,
                use_cache=use_cache,
            )
            data.history = history_result.messages
            data.history_from_cache = history_result.from_cache

        # Merge profile with schema defaults (if both exist)
        if data.profile and data.companion_config:
            schema = data.companion_config.profile_schema
            if schema:
                data.profile = deep_merge_with_schema(data.profile, schema)

        return data

    @staticmethod
    async def _fetch_parallel(
        conn_factory: ConnectionFactory,
        *,
        companion_id: UUID,
        conversation_id: UUID | None,
        external_user_id: str | None,
        use_cache: bool,
        preloaded_config: CompanionConfig | None,
    ) -> HydrationData:
        """Fetch all data in parallel using multiple connections."""
        data = HydrationData(
            companion_id=companion_id,
            external_user_id=external_user_id,
            conversation_id=conversation_id,
        )

        # Build list of coroutines to run in parallel
        async def fetch_config():
            async with conn_factory() as conn:
                return await ConfigLoader.load(
                    conn,
                    companion_id,
                    use_cache=use_cache,
                    preloaded_config=preloaded_config,
                )

        async def fetch_user_state():
            if not external_user_id:
                return None
            async with conn_factory() as conn:
                return await StateLoader.load_user_state(
                    conn,
                    companion_id,
                    external_user_id,
                    use_cache=use_cache,
                )

        async def fetch_conv_state():
            if not conversation_id:
                return None
            async with conn_factory() as conn:
                return await StateLoader.load_conversation_state(
                    conn,
                    conversation_id,
                    use_cache=use_cache,
                )

        async def fetch_history():
            if not conversation_id:
                return None
            async with conn_factory() as conn:
                return await HistoryLoader.load(
                    conn,
                    conversation_id,
                    use_cache=use_cache,
                )

        # Run all fetches in parallel with exception handling
        results = await asyncio.gather(
            fetch_config(),
            fetch_user_state(),
            fetch_conv_state(),
            fetch_history(),
            return_exceptions=True,
        )
        config_result, user_state_result, conv_state_result, history_result = results

        # Config is required - re-raise if it failed
        if isinstance(config_result, BaseException):
            logger.error("Failed to fetch config for companion %s: %s", companion_id, config_result)
            raise config_result

        # Populate config data
        data.companion_config = config_result.config
        data.core_system_prompt = config_result.core_system_prompt
        data.core_memories = list(config_result.core_memories)
        data.config_from_cache = config_result.from_cache

        # Handle optional results - log errors but continue
        if isinstance(user_state_result, BaseException):
            logger.warning(
                "Failed to fetch user state for companion %s, user %s: %s",
                companion_id,
                external_user_id,
                user_state_result,
            )
        elif user_state_result:
            data.profile = user_state_result.profile
            data.state_version = user_state_result.state_version
            data.user_state_from_cache = user_state_result.from_cache

        if isinstance(conv_state_result, BaseException):
            logger.warning(
                "Failed to fetch conversation state for conversation %s: %s",
                conversation_id,
                conv_state_result,
            )
        elif conv_state_result:
            data.conversation_state = conv_state_result.state
            data.conv_state_from_cache = conv_state_result.from_cache

        if isinstance(history_result, BaseException):
            logger.warning(
                "Failed to fetch history for conversation %s: %s",
                conversation_id,
                history_result,
            )
        elif history_result:
            data.history = history_result.messages
            data.history_from_cache = history_result.from_cache

        # Merge profile with schema defaults (if both exist)
        if data.profile and data.companion_config:
            schema = data.companion_config.profile_schema
            if schema:
                data.profile = deep_merge_with_schema(data.profile, schema)

        return data


# =============================================================================
# ContextAssembler: Builds final LLM messages
# =============================================================================


class ContextAssembler:
    """Assembles HydrationData into LLM-ready messages.

    This class is responsible for:
    - Building the system prompt (core prompt + core memories)
    - Injecting profile into prompt (if enabled)
    - Formatting history messages
    - Combining all pieces into final messages list

    Usage:
        data = await Hydrator.fetch(...)
        messages = ContextAssembler.build_messages(
            data,
            user_message="Hello",
            include_profile=True,
        )
    """

    @staticmethod
    def build_system_prompt(
        data: HydrationData,
        *,
        core_prompt_override: str | None = None,
        core_memories_override: list[str] | None = None,
    ) -> str:
        """Build the system prompt from hydration data.

        Args:
            data: HydrationData with config and core memories
            core_prompt_override: Override for base system prompt
            core_memories_override: Override for core memories

        Returns:
            Composed system prompt string
        """
        if core_prompt_override is not None or core_memories_override is not None:
            # Use overrides
            base_prompt = core_prompt_override or ""
            if not base_prompt and data.companion_config and data.companion_config.system_prompt:
                base_prompt = data.companion_config.system_prompt.full_system_prompt or ""
            memories = core_memories_override or []
            return compose_core_system_prompt(base_prompt, memories)

        # Use pre-composed prompt from hydration
        return data.core_system_prompt

    @staticmethod
    def build_profile_block(
        data: HydrationData,
        *,
        profile_override: dict[str, Any] | None = None,
    ) -> str | None:
        """Build profile injection block.

        Args:
            data: HydrationData with profile
            profile_override: Override profile data

        Returns:
            Profile block string or None if no profile
        """
        profile = profile_override if profile_override is not None else data.profile

        if not profile:
            return None

        profile_schema = None
        if data.companion_config:
            profile_schema = data.companion_config.profile_schema

        return build_profile_prompt_block(
            profile,
            profile_schema=profile_schema if isinstance(profile_schema, dict) else None,
        )

    @staticmethod
    def build_history_messages(
        data: HydrationData,
        *,
        history_override: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        """Build history messages for LLM context.

        Args:
            data: HydrationData with history
            history_override: Override history

        Returns:
            List of message dicts with role and content
        """
        history = history_override if history_override is not None else data.history

        return [{"role": msg["role"], "content": msg["content"]} for msg in history]

    @staticmethod
    def build_messages(
        data: HydrationData,
        *,
        user_message: str | None = None,
        include_system_prompt: bool = True,
        include_profile: bool = False,
        include_history: bool = True,
        core_prompt_override: str | None = None,
        core_memories_override: list[str] | None = None,
        profile_override: dict[str, Any] | None = None,
        history_override: list[dict[str, Any]] | None = None,
        layer_messages: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """Build complete messages list for LLM.

        Order of messages:
        1. System prompt (core prompt + core memories)
        2. Profile block (if include_profile)
        3. Layer messages (memory, knowledge, tools blocks)
        4. History messages
        5. Current user message

        Args:
            data: HydrationData with all fetched data
            user_message: Current user message to append
            include_system_prompt: Whether to include system prompt
            include_profile: Whether to include profile block
            include_history: Whether to include history
            core_prompt_override: Override for system prompt
            core_memories_override: Override for core memories
            profile_override: Override for profile
            history_override: Override for history
            layer_messages: Messages from layers (memory, knowledge, etc.)

        Returns:
            Complete messages list ready for LLM
        """
        messages: list[dict[str, str]] = []

        # 1. System prompt
        if include_system_prompt:
            system_prompt = ContextAssembler.build_system_prompt(
                data,
                core_prompt_override=core_prompt_override,
                core_memories_override=core_memories_override,
            )
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

        # 2. Profile block
        if include_profile:
            profile_block = ContextAssembler.build_profile_block(
                data,
                profile_override=profile_override,
            )
            if profile_block:
                messages.append({"role": "system", "content": profile_block})

        # 3. Layer messages (memory, knowledge, tools blocks)
        if layer_messages:
            messages.extend(layer_messages)

        # 4. History
        if include_history:
            history_msgs = ContextAssembler.build_history_messages(
                data,
                history_override=history_override,
            )
            messages.extend(history_msgs)

        # 5. Current user message
        if user_message:
            messages.append({"role": "user", "content": user_message})

        return messages


# =============================================================================
# Backward compatibility: HydratedContext alias and ContextHydrator
# =============================================================================

# Alias for backward compatibility
HydratedContext = HydrationData


class ContextHydrator:
    """Backward-compatible interface for hydration.

    DEPRECATED: Use Hydrator.fetch() and ContextAssembler directly.

    This class maintains the old API for existing code that uses:
        ctx = await ContextHydrator.hydrate(conn, ...)
    """

    @staticmethod
    async def hydrate(
        conn: asyncpg.Connection,
        *,
        companion_id: UUID,
        conversation_id: UUID | None = None,
        external_user_id: str | None = None,
        use_cache: bool = True,
        companion_config: CompanionConfig | None = None,
    ) -> HydrationData:
        """Fetch all context data (backward-compatible method).

        DEPRECATED: Use Hydrator.fetch() instead.
        """
        return await Hydrator.fetch(
            conn,
            companion_id=companion_id,
            conversation_id=conversation_id,
            external_user_id=external_user_id,
            use_cache=use_cache,
            preloaded_config=companion_config,
        )

    @staticmethod
    def invalidate_config(companion_id: UUID) -> None:
        """Invalidate cached companion config."""
        ConfigLoader.invalidate(companion_id)


# For backward compatibility with imports
def _compose_core_system_prompt(base_prompt: str, core_memories: list[str]) -> str:
    """Backward-compatible function for compose_core_system_prompt."""
    return compose_core_system_prompt(base_prompt, core_memories)
