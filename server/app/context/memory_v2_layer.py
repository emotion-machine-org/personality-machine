"""MemoryV2Layer: Context layer for scratchpad injection.

Unlike MemoryRuntime (v1) which does vector search, this layer
loads the entire scratchpad and injects it as a formatted list.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List
from uuid import UUID

import asyncpg

from ..repositories.memory_v2_repository import MemoryV2Repository
from ..services.cache_manager import cache, ttl_from_env
from ..services.memory_v2_service import MEMORY_V2_GUIDANCE, MemoryV2Service
from .layers import ConnectionFactory, EventCallback, LayerOutput
from .schemas import ContextEvent, GateResult, TurnContext

logger = logging.getLogger(__name__)

# Cache TTL for memory v2 entries (default 30s)
_MEMORY_V2_TTL_S = ttl_from_env("MEMORY_V2_CACHE_TTL_S", 30.0)
_CACHE_NAMESPACE = "memory_v2"


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def _cache_key(relationship_id: UUID) -> str:
    """Generate cache key for memory v2 entries."""
    return str(relationship_id)


class MemoryV2Layer:
    """Context layer that injects Memory V2 scratchpad into prompt.

    Unlike MemoryRuntime (v1) which does vector search, this layer
    loads the entire scratchpad and injects it as a formatted list.
    """

    key = "memory_v2"

    def __init__(
        self,
        turn_context: TurnContext,
        *,
        conn: asyncpg.Connection | None = None,
        conn_factory: ConnectionFactory | None = None,
        max_entries: int = 100,
    ):
        if conn is None and conn_factory is None:
            raise ValueError("Either conn or conn_factory must be provided")

        self.turn_context = turn_context
        self.conn = conn
        self.conn_factory = conn_factory
        self.max_entries = max_entries

    async def run(self, event_callback: EventCallback | None = None) -> LayerOutput:
        """Load scratchpad and inject into system prompt."""
        # Yield to event loop to allow parallel layers to start simultaneously
        await asyncio.sleep(0)

        events: List[ContextEvent] = []
        messages: List[Dict[str, str]] = []
        t0 = time.perf_counter()

        def emit(ev: ContextEvent):
            """Emit event both to list and callback for real-time streaming."""
            events.append(ev)
            if event_callback:
                try:
                    event_callback(ev)
                except Exception:
                    pass  # Don't let callback errors break the layer

        relationship_id = self.turn_context.relationship_id
        if not relationship_id:
            emit(
                ContextEvent(
                    name="memory_v2:gate",
                    phase="info",
                    meta={
                        "gate_result": GateResult(
                            run=False,
                            reason="no_relationship_id",
                            inputs={},
                        ).model_dump()
                    },
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=[], events=events, effects=[])

        # Check cache first
        cache_key = _cache_key(relationship_id)
        cached_entries = cache.get(_CACHE_NAMESPACE, cache_key)

        if cached_entries is not None:
            entries = cached_entries
            emit(
                ContextEvent(
                    name="memory_v2:load",
                    phase="info",
                    meta={"count": len(entries), "cache_hit": True},
                    ts_ms=_elapsed_ms(t0),
                )
            )
        else:
            # Load entries from DB
            emit(ContextEvent(name="memory_v2:load", phase="start", meta={}, ts_ms=_elapsed_ms(t0)))

            try:
                if self.conn_factory:
                    async with self.conn_factory() as conn:
                        entries = await MemoryV2Repository.list_entries(
                            conn, relationship_id, limit=self.max_entries
                        )
                else:
                    entries = await MemoryV2Repository.list_entries(
                        self.conn, relationship_id, limit=self.max_entries
                    )

                # Cache the result
                cache.set(_CACHE_NAMESPACE, cache_key, entries, _MEMORY_V2_TTL_S)

                emit(
                    ContextEvent(
                        name="memory_v2:load",
                        phase="end",
                        meta={"count": len(entries)},
                        ts_ms=_elapsed_ms(t0),
                    )
                )

            except Exception as e:
                logger.error(f"Failed to load memory v2 entries: {e}")
                emit(
                    ContextEvent(
                        name="memory_v2:load",
                        phase="error",
                        meta={"error": str(e)},
                        ts_ms=_elapsed_ms(t0),
                    )
                )
                return LayerOutput(messages=[], events=events, effects=[])

        # Format for prompt (or note that no memories exist yet)
        if entries:
            formatted = MemoryV2Service.format_for_prompt(entries)
            emit(
                ContextEvent(
                    name="memory_v2:gate",
                    phase="info",
                    meta={
                        "gate_result": GateResult(
                            run=True,
                            reason="entries_found",
                            inputs={
                                "relationship_id": str(relationship_id),
                                "entry_count": len(entries),
                            },
                        ).model_dump()
                    },
                    ts_ms=_elapsed_ms(t0),
                )
            )
            memory_block = f"# MEMORY\n\n{MEMORY_V2_GUIDANCE}\n\n{formatted}"
        else:
            emit(
                ContextEvent(
                    name="memory_v2:gate",
                    phase="info",
                    meta={
                        "gate_result": GateResult(
                            run=True,
                            reason="no_entries_yet",
                            inputs={"relationship_id": str(relationship_id)},
                        ).model_dump()
                    },
                    ts_ms=_elapsed_ms(t0),
                )
            )
            # Still inject guidance so companion knows it has memory capability
            memory_block = f"# MEMORY\n\n{MEMORY_V2_GUIDANCE}\n\nStored memories: None yet."
        messages.append(
            {
                "role": "system",
                "content": memory_block,
            }
        )

        emit(
            ContextEvent(
                name="memory_v2:summary",
                phase="info",
                meta={
                    "entries_injected": len(entries),
                    "prompt_length": len(memory_block),
                    "duration_ms": _elapsed_ms(t0),
                },
                ts_ms=_elapsed_ms(t0),
            )
        )

        return LayerOutput(messages=messages, events=events, effects=[])

    @staticmethod
    def invalidate_cache(relationship_id: UUID) -> None:
        """Invalidate cached memory v2 entries for a relationship.

        Call this after mutations (manual add/delete via test UI).
        Note: Async ingestion from Modal cannot call this directly,
        so we rely on TTL expiration for those updates.
        """
        cache.delete(_CACHE_NAMESPACE, _cache_key(relationship_id))


__all__ = ["MemoryV2Layer"]
