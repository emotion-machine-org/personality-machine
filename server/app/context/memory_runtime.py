from __future__ import annotations

import asyncio
import time
from typing import Any, Dict
from uuid import UUID

import asyncpg

from ..services.memory_runtime import (
    format_memory_block,
    retrieve_regular_memories,
    should_retrieve_memories,
)
from .layers import ConnectionFactory, EventCallback, LayerOutput, LayerRuntime
from .schemas import ContextEvent, GateResult


class MemoryRuntime(LayerRuntime):
    key = "memory"

    def __init__(
        self,
        *,
        conn: asyncpg.Connection | None = None,
        conn_factory: ConnectionFactory | None = None,
        companion_id: UUID,
        user_text: str | None,
        external_user_id: str | None,
        conversation_id: UUID | None,
        recent_messages: list | None,
        last_n: int = 6,
        min_saliency: float = 0.2,
        top_k: int = 50,
        skip_gate: bool = False,
    ) -> None:
        if conn is None and conn_factory is None:
            raise ValueError("Either conn or conn_factory must be provided")
        self.conn = conn
        self.conn_factory = conn_factory
        self.companion_id = companion_id
        self.user_text = user_text
        self.external_user_id = external_user_id
        self.conversation_id = conversation_id
        self.recent_messages = recent_messages
        self.last_n = last_n
        self.min_saliency = min_saliency
        self.top_k = top_k
        self.skip_gate = skip_gate

    async def run(self, event_callback: EventCallback | None = None) -> LayerOutput:
        # Yield to event loop to allow parallel layers to start simultaneously
        await asyncio.sleep(0)

        events: list[ContextEvent] = []
        messages: list[Dict[str, str]] = []

        def emit(ev: ContextEvent):
            """Emit event both to list and callback for real-time streaming."""
            events.append(ev)
            if event_callback:
                try:
                    event_callback(ev)
                except Exception:
                    pass  # Don't let callback errors break the layer

        if not (self.user_text or "").strip():
            return LayerOutput(messages=messages, events=events)

        t0 = time.perf_counter()
        emit(ContextEvent(name="memory:retrieving", phase="start", meta={}, ts_ms=_elapsed_ms(t0)))

        # Gate decision with structured result for debugging
        # When skip_gate is True (classifier already decided), bypass internal gate
        if self.skip_gate:
            gate_passed = True
            gate_result = GateResult(
                run=True,
                reason="classifier_decision",
                inputs={"skip_gate": True, "user_text_length": len(self.user_text or "")},
            )
        else:
            gate_passed = await should_retrieve_memories(
                self.user_text,
                recent_messages=(
                    self.recent_messages[-self.last_n :] if self.recent_messages else None
                ),
            )
            gate_result = GateResult(
                run=gate_passed,
                reason="memory_heuristic_passed" if gate_passed else "memory_heuristic_failed",
                inputs={
                    "user_text_length": len(self.user_text or ""),
                    "recent_messages_count": len(self.recent_messages or []),
                },
            )

        if not gate_passed:
            emit(
                ContextEvent(
                    name="memory:gated",
                    phase="info",
                    meta={
                        "skipped": True,
                        "reason": "heuristic_failed",
                        "gate": gate_result.model_dump(),
                    },
                    ts_ms=_elapsed_ms(t0),
                )
            )
            emit(
                ContextEvent(
                    name="memory:retrieving",
                    phase="end",
                    meta={"skipped": True},
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=messages, events=events)

        timings: Dict[str, Any] = {}

        # Use connection factory if available (for parallel execution), otherwise use provided conn
        if self.conn_factory:
            async with self.conn_factory() as conn:
                items = await retrieve_regular_memories(
                    conn,
                    companion_id=self.companion_id,
                    query_text=self.user_text,
                    external_user_id=self.external_user_id,
                    conversation_id=self.conversation_id,
                    timings=timings,
                )
        else:
            items = await retrieve_regular_memories(
                self.conn,
                companion_id=self.companion_id,
                query_text=self.user_text,
                external_user_id=self.external_user_id,
                conversation_id=self.conversation_id,
                timings=timings,
            )

        block = format_memory_block(items) or ""
        if block:
            messages.append({"role": "system", "content": block})

        meta = {"retrieval_items": len(items), "gate": gate_result.model_dump()}
        meta.update({k: v for k, v in timings.items() if k.endswith("_ms")})
        emit(
            ContextEvent(
                name="memory:retrieving",
                phase="end",
                meta=meta,
                ts_ms=_elapsed_ms(t0),
            )
        )

        return LayerOutput(messages=messages, events=events)


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


__all__ = ["MemoryRuntime"]
