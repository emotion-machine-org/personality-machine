from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List
from uuid import UUID

import asyncpg

from ..services.openai_vector_store import search_vector_store
from .layers import ConnectionFactory, EventCallback, LayerOutput, LayerRuntime
from .schemas import ContextEvent, GateResult

logger = logging.getLogger(__name__)


class KnowledgeRuntime(LayerRuntime):
    key = "knowledge"

    def __init__(
        self,
        *,
        conn: asyncpg.Connection | None = None,
        conn_factory: ConnectionFactory | None = None,
        companion_id: UUID,
        user_text: str,
        top_k: int = 5,
        filters: Dict[str, Any] | None = None,
        mode: str | None = None,
        gate_strategy: str = "keyword",  # none | keyword | llm
        keywords: List[str] | None = None,
        skip_gate: bool = False,
    ) -> None:
        if conn is None and conn_factory is None:
            raise ValueError("Either conn or conn_factory must be provided")
        self.conn = conn
        self.conn_factory = conn_factory
        self.companion_id = companion_id
        self.user_text = user_text
        self.top_k = top_k
        self.filters = filters
        self.mode = mode
        self.gate_strategy = gate_strategy.lower()
        self.keywords = keywords or []
        self.skip_gate = skip_gate

    async def run(self, event_callback: EventCallback | None = None) -> LayerOutput:
        # Yield to event loop to allow parallel layers to start simultaneously
        await asyncio.sleep(0)

        events: List[ContextEvent] = []
        messages: List[Dict[str, str]] = []

        def emit(ev: ContextEvent):
            """Emit event both to list and callback for real-time streaming."""
            events.append(ev)
            if event_callback:
                try:
                    event_callback(ev)
                except Exception:
                    pass

        if not (self.user_text or "").strip():
            logger.info(
                "[knowledge_runtime] Skipping - empty user text | companion_id=%s",
                self.companion_id,
            )
            return LayerOutput(messages=messages, events=events)

        t0 = time.perf_counter()
        logger.info(
            "[knowledge_runtime] Starting retrieval | companion_id=%s query=%s top_k=%d gate_strategy=%s",
            self.companion_id,
            self.user_text[:100] if self.user_text else "",
            self.top_k,
            self.gate_strategy,
        )
        emit(
            ContextEvent(name="knowledge:retrieving", phase="start", meta={}, ts_ms=_elapsed_ms(t0))
        )

        # Gate decision with structured result for debugging
        gate_result = self._should_run_gate()
        if not gate_result.run:
            logger.info(
                "[knowledge_runtime] Gate blocked execution | companion_id=%s reason=%s elapsed_ms=%.1f",
                self.companion_id,
                gate_result.reason,
                _elapsed_ms(t0),
            )
            emit(
                ContextEvent(
                    name="knowledge:gated",
                    phase="info",
                    meta={
                        "skipped": True,
                        "reason": gate_result.reason,
                        "gate": gate_result.model_dump(),
                    },
                    ts_ms=_elapsed_ms(t0),
                )
            )
            emit(
                ContextEvent(
                    name="knowledge:retrieving",
                    phase="end",
                    meta={"skipped": True},
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=messages, events=events)

        logger.info(
            "[knowledge_runtime] Gate passed, starting vector search | companion_id=%s reason=%s",
            self.companion_id,
            gate_result.reason,
        )
        search_t0 = time.perf_counter()
        try:
            # Use connection factory if available (for parallel execution), otherwise use provided conn
            if self.conn_factory:
                async with self.conn_factory() as conn:
                    results = await search_vector_store(
                        conn,
                        companion_id=self.companion_id,
                        query=self.user_text,
                        max_results=self.top_k,
                        filters=self.filters,
                        mode=self.mode,
                    )
            else:
                results = await search_vector_store(
                    self.conn,
                    companion_id=self.companion_id,
                    query=self.user_text,
                    max_results=self.top_k,
                    filters=self.filters,
                    mode=self.mode,
                )
            search_elapsed = _elapsed_ms(search_t0)
            logger.info(
                "[knowledge_runtime] Vector search completed | companion_id=%s results=%d search_ms=%.1f",
                self.companion_id,
                len(results) if results else 0,
                search_elapsed,
            )
        except Exception as exc:
            logger.error(
                "[knowledge_runtime] Vector search FAILED | companion_id=%s error=%s elapsed_ms=%.1f",
                self.companion_id,
                str(exc),
                _elapsed_ms(t0),
            )
            emit(
                ContextEvent(
                    name="knowledge:retrieving",
                    phase="error",
                    meta={"error": str(exc)},
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=messages, events=events)

        if results:
            lines = ["# KNOWLEDGE BASE"]
            scores = []
            for r in results:
                score = r.get("score")
                fn = r.get("filename") or r.get("file_id") or "source"
                text = (r.get("text") or "").strip()
                prefix = (
                    f"- [{score:.2f}] {fn}: " if isinstance(score, (int, float)) else f"- {fn}: "
                )
                lines.append(prefix + text)
                if isinstance(score, (int, float)):
                    scores.append(score)
            messages.append({"role": "system", "content": "\n".join(lines)})
            logger.info(
                "[knowledge_runtime] Retrieved documents | companion_id=%s count=%d scores=%s",
                self.companion_id,
                len(results),
                [f"{s:.3f}" for s in scores] if scores else "n/a",
            )
        else:
            logger.info(
                "[knowledge_runtime] No documents retrieved | companion_id=%s",
                self.companion_id,
            )

        elapsed_total = _elapsed_ms(t0)
        logger.info(
            "[knowledge_runtime] run() completed | companion_id=%s results=%d elapsed_ms=%.1f",
            self.companion_id,
            len(results) if results else 0,
            elapsed_total,
        )
        emit(
            ContextEvent(
                name="knowledge:retrieving",
                phase="end",
                meta={"results": len(results), "gate": gate_result.model_dump()},
                ts_ms=_elapsed_ms(t0),
            )
        )
        return LayerOutput(messages=messages, events=events)

    def _should_run_gate(self) -> GateResult:
        """Evaluate gate and return structured result for debugging."""
        text = (self.user_text or "").lower()
        inputs = {
            "strategy": self.gate_strategy,
            "text_length": len(text),
            "has_question_mark": "?" in text,
            "keywords_configured": len(self.keywords),
            "skip_gate": self.skip_gate,
        }

        # When skip_gate is True (classifier already decided), bypass internal gate
        if self.skip_gate:
            return GateResult(run=True, reason="classifier_decision", inputs=inputs)

        if self.gate_strategy in ("none", "no_gate", "always"):
            return GateResult(run=True, reason="gate_disabled", inputs=inputs)

        if self.gate_strategy in ("keyword", "keywords"):
            passed, reason = self._keyword_gate()
            return GateResult(run=passed, reason=reason, inputs=inputs)

        if self.gate_strategy in ("llm", "gemini"):
            ok = self._llm_gate()
            if ok is not None:
                return GateResult(
                    run=ok, reason="llm_gate_" + ("passed" if ok else "failed"), inputs=inputs
                )
            # Fallback to keyword if LLM gate unavailable
            passed, reason = self._keyword_gate()
            return GateResult(run=passed, reason=f"llm_fallback:{reason}", inputs=inputs)

        # Default: run
        return GateResult(run=True, reason="default_run", inputs=inputs)

    def _keyword_gate(self) -> tuple[bool, str]:
        """Returns (passed, reason) tuple."""
        text = (self.user_text or "").lower()
        tokens = {t.strip(".,!?") for t in text.split() if len(t) >= 4}
        if self.keywords:
            kws = {k.lower() for k in self.keywords}
            matched = tokens & kws
            if matched:
                return True, f"keyword_match:{list(matched)[:3]}"
            return False, "no_keyword_match"
        # Heuristic: run if user query is long enough or contains question marks
        if len(text) > 24:
            return True, "text_length_heuristic"
        if "?" in text:
            return True, "question_mark_heuristic"
        return False, "heuristic_failed"

    def _llm_gate(self) -> bool | None:
        try:
            import os

            import google.generativeai as genai

            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return None
            genai.configure(api_key=api_key)
            prompt = (
                "Decide if knowledge base retrieval is needed for the user's message.\n"
                "Respond with YES or NO only.\n\n"
                f"User: {self.user_text}\n"
            )
            resp = genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt)
            text = (resp.text or "").strip().lower()
            if "yes" in text and "no" not in text:
                return True
            if "no" in text and "yes" not in text:
                return False
        except Exception:
            return None
        return None


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


__all__ = ["KnowledgeRuntime"]
