"""BehaviorRuntime: LayerRuntime implementation for behaviors.

This module provides the layer that evaluates and executes developer-defined
behaviors during context orchestration. It implements the LayerRuntime protocol
so it can be plugged into the orchestrator alongside memory, knowledge, etc.

The runtime:
1. Loads behaviors from DB for the companion
2. Evaluates deterministic triggers (keyword, turn_count, every_n, always)
3. Merges with classifier-selected behaviors
4. Enqueues jobs for execution:
   - Priority behaviors: executed inline, orchestrator waits
   - Async behaviors: enqueued to jobs table, background execution

Behaviors execute in Modal Sandboxes for isolation and security.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Tuple
from uuid import UUID

import asyncpg

from ..repositories.behavior_repository import BehaviorRepository
from ..repositories.job_repository import JobRepository
from .layers import ConnectionFactory, EventCallback, LayerOutput, PendingAsyncBehavior
from .schemas import (
    ContextEvent,
    GateResult,
    TriggeredBehavior,
    TriggerSource,
    TurnContext,
    TurnEffect,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Trigger Evaluation
# =============================================================================


def evaluate_trigger(
    trigger: Dict[str, Any],
    message: str,
    turn_count: int,
    keywords: List[str],
) -> Tuple[bool, str | None]:
    """Evaluate a single trigger against the current context.

    Args:
        trigger: Trigger definition dict with 'type' and type-specific params
        message: User message text
        turn_count: Current turn number
        keywords: Extracted keywords from message

    Returns:
        Tuple of (matched: bool, details: str or None)
    """
    trigger_type = trigger.get("type", "").lower()

    if trigger_type == "keyword":
        trigger_keywords = trigger.get("keywords", [])
        if not trigger_keywords:
            return False, None

        # Check if any trigger keyword matches extracted keywords or is in message
        message_lower = message.lower()
        keyword_set = {k.lower() for k in keywords}

        for kw in trigger_keywords:
            kw_lower = kw.lower()
            if kw_lower in keyword_set or kw_lower in message_lower:
                return True, f"matched keyword: {kw}"

        return False, None

    elif trigger_type == "every_n":
        n = trigger.get("n", 0)
        if n > 0 and turn_count > 0 and turn_count % n == 0:
            return True, f"turn {turn_count} (every {n})"
        return False, None

    elif trigger_type == "turn_count":
        turns = trigger.get("turns", [])
        if turn_count in turns:
            return True, f"turn {turn_count}"
        return False, None

    elif trigger_type == "always":
        return True, "always"

    return False, None


def evaluate_behavior_triggers(
    behavior: Dict[str, Any],
    message: str,
    turn_count: int,
    keywords: List[str],
) -> Tuple[bool, TriggerSource, str | None]:
    """Evaluate all triggers for a behavior.

    Args:
        behavior: Behavior definition with 'triggers' list
        message: User message
        turn_count: Current turn number
        keywords: Extracted keywords

    Returns:
        Tuple of (triggered: bool, source: TriggerSource, details: str or None)
    """
    triggers = behavior.get("triggers", [])

    for trigger in triggers:
        matched, details = evaluate_trigger(trigger, message, turn_count, keywords)
        if matched:
            trigger_type = trigger.get("type", "").lower()
            if trigger_type == "keyword":
                return True, TriggerSource.KEYWORD, details
            elif trigger_type in ("every_n", "turn_count"):
                return True, TriggerSource.TURN_COUNT, details
            elif trigger_type == "always":
                return True, TriggerSource.ALWAYS, details

    return False, TriggerSource.KEYWORD, None


# =============================================================================
# Behavior Runtime
# =============================================================================


class BehaviorRuntime:
    """LayerRuntime implementation for behavior execution.

    This layer:
    1. Loads behaviors from DB for the companion
    2. Evaluates deterministic triggers
    3. Merges with classifier-selected behaviors
    4. Enqueues jobs (priority=inline, async=background)
    5. Returns prompt blocks from priority behaviors

    Attributes:
        key: Layer identifier ("behaviors")
    """

    key = "behaviors"

    def __init__(
        self,
        turn_context: TurnContext,
        *,
        conn: asyncpg.Connection | None = None,
        conn_factory: ConnectionFactory | None = None,
        profile: Dict[str, Any] | None = None,
        max_behaviors: int = 5,
        classifier_selected_behaviors: List[str] | None = None,
        priority_timeout_ms: int = 5000,
        preloaded_behaviors: List[Dict[str, Any]] | None = None,
    ):
        """Initialize the BehaviorRuntime.

        Args:
            turn_context: Current turn context with message, keywords, etc.
            conn: Database connection (for sequential execution)
            conn_factory: Connection factory (for parallel execution)
            profile: Snapshot of profile for behavior context
            max_behaviors: Maximum behaviors to execute per turn (default: 5)
            classifier_selected_behaviors: Behavior keys selected by the intent classifier
            priority_timeout_ms: Timeout for priority behavior execution (default: 5000ms)
            preloaded_behaviors: Pre-fetched behaviors to avoid duplicate DB query.
                If provided, skips loading from DB. Used when orchestrator already
                loaded behaviors for classifier.
        """
        if conn is None and conn_factory is None:
            raise ValueError("Either conn or conn_factory must be provided")

        self.turn_context = turn_context
        self.conn = conn
        self.conn_factory = conn_factory
        self.profile = profile or {}
        self.max_behaviors = max_behaviors
        self.classifier_selected_behaviors = classifier_selected_behaviors or []
        self.priority_timeout_ms = priority_timeout_ms
        self.preloaded_behaviors = preloaded_behaviors

    async def run(self, event_callback: EventCallback | None = None) -> LayerOutput:
        """Execute the behaviors layer.

        1. Load behaviors from DB
        2. Evaluate deterministic triggers
        3. Merge with classifier-selected behaviors
        4. Enqueue jobs for all triggered behaviors
        5. Wait for priority behaviors, collect prompt blocks
        6. Return results

        Args:
            event_callback: Optional callback for real-time event streaming

        Returns:
            LayerOutput with prompt blocks from priority behaviors,
            events for debugging, and collected effects.
        """
        # Yield to event loop to allow parallel layers to start simultaneously
        await asyncio.sleep(0)

        events: List[ContextEvent] = []
        messages: List[Dict[str, str]] = []
        effects: List[TurnEffect] = []
        t0 = time.perf_counter()

        def _now_ms() -> float:
            return (time.perf_counter() - t0) * 1000.0

        def emit(ev: ContextEvent):
            events.append(ev)
            if event_callback:
                try:
                    event_callback(ev)
                except Exception:
                    pass

        companion_id = str(self.turn_context.companion_id)
        message = self.turn_context.message or ""
        keywords = self.turn_context.keywords or []
        turn_count = self.turn_context.turn_count

        # 1. Load behaviors (use preloaded if available, otherwise fetch from DB)
        emit(ContextEvent(name="behaviors:load", phase="start", meta={}, ts_ms=_now_ms()))

        if self.preloaded_behaviors is not None:
            # Use preloaded behaviors from orchestrator (avoids duplicate DB query)
            behaviors = self.preloaded_behaviors
            emit(
                ContextEvent(
                    name="behaviors:load",
                    phase="end",
                    meta={"count": len(behaviors), "source": "preloaded"},
                    ts_ms=_now_ms(),
                )
            )
        else:
            # Fallback: Load from DB (only if not preloaded)
            try:
                if self.conn_factory:
                    async with self.conn_factory() as conn:
                        behaviors = await self._load_companion_behaviors(conn, companion_id)
                else:
                    behaviors = await self._load_companion_behaviors(self.conn, companion_id)
            except Exception as e:
                logger.error(f"Failed to load behaviors: {e}")
                emit(
                    ContextEvent(
                        name="behaviors:load",
                        phase="error",
                        meta={"error": str(e)},
                        ts_ms=_now_ms(),
                    )
                )
                return LayerOutput(messages=[], events=events, effects=[])

            emit(
                ContextEvent(
                    name="behaviors:load",
                    phase="end",
                    meta={"count": len(behaviors), "source": "database"},
                    ts_ms=_now_ms(),
                )
            )

        if not behaviors:
            emit(
                ContextEvent(
                    name="behaviors:gate",
                    phase="info",
                    meta={
                        "gate_result": GateResult(
                            run=False, reason="no_behaviors_configured", inputs={}
                        ).model_dump()
                    },
                    ts_ms=_now_ms(),
                )
            )
            return LayerOutput(messages=[], events=events, effects=[])

        # 2. Evaluate deterministic triggers
        triggered_behaviors: List[TriggeredBehavior] = []
        behaviors_by_key: Dict[str, Dict[str, Any]] = {b["key"]: b for b in behaviors}

        for behavior in behaviors:
            try:
                matched, source, details = evaluate_behavior_triggers(
                    behavior, message, turn_count, keywords
                )
                if matched:
                    triggered_behaviors.append(
                        TriggeredBehavior(
                            behavior_key=behavior["key"],
                            trigger_source=source,
                            trigger_details=details,
                            priority=behavior.get("priority", False),
                        )
                    )
            except Exception as e:
                # Log the error but continue evaluating other behaviors
                # This prevents one malformed behavior from crashing the entire layer
                logger.warning(
                    f"Failed to evaluate triggers for behavior '{behavior.get('key', 'unknown')}': {e}"
                )

        # 3. Merge with classifier-selected behaviors (dedupe)
        triggered_keys = {tb.behavior_key for tb in triggered_behaviors}
        for behavior_key in self.classifier_selected_behaviors:
            if behavior_key not in triggered_keys and behavior_key in behaviors_by_key:
                behavior = behaviors_by_key[behavior_key]
                triggered_behaviors.append(
                    TriggeredBehavior(
                        behavior_key=behavior_key,
                        trigger_source=TriggerSource.CLASSIFIER,
                        trigger_details="classifier selected",
                        priority=behavior.get("priority", False),
                    )
                )

        # Gate check
        if not triggered_behaviors:
            gate_result = GateResult(
                run=False,
                reason="no_matching_triggers",
                inputs={
                    "behaviors_loaded": len(behaviors),
                    "keywords_count": len(keywords),
                    "classifier_selected_count": len(self.classifier_selected_behaviors),
                    "turn_count": turn_count,
                },
            )
            emit(
                ContextEvent(
                    name="behaviors:gate",
                    phase="info",
                    meta={"gate_result": gate_result.model_dump()},
                    ts_ms=_now_ms(),
                )
            )
            return LayerOutput(messages=[], events=events, effects=[])

        # Limit to max_behaviors
        triggered_behaviors = triggered_behaviors[: self.max_behaviors]

        gate_result = GateResult(
            run=True,
            reason="behaviors_triggered",
            inputs={
                "behaviors_loaded": len(behaviors),
                "triggered_count": len(triggered_behaviors),
                "triggered_keys": [tb.behavior_key for tb in triggered_behaviors],
                "priority_count": sum(1 for tb in triggered_behaviors if tb.priority),
                "async_count": sum(1 for tb in triggered_behaviors if not tb.priority),
            },
        )
        emit(
            ContextEvent(
                name="behaviors:gate",
                phase="info",
                meta={"gate_result": gate_result.model_dump()},
                ts_ms=_now_ms(),
            )
        )

        # 4. Separate priority and async behaviors
        # Priority behaviors execute inline
        # Async behaviors are collected and enqueued AFTER LLM response (fire-and-forget)
        priority_behaviors: List[TriggeredBehavior] = []
        pending_async: List[PendingAsyncBehavior] = []

        for triggered in triggered_behaviors:
            behavior = behaviors_by_key.get(triggered.behavior_key)
            if not behavior:
                continue

            if triggered.priority:
                # Priority behaviors: execute directly
                priority_behaviors.append(triggered)
            else:
                # Async behaviors: collect for post-LLM enqueueing
                pending_async.append(
                    PendingAsyncBehavior(
                        behavior_key=triggered.behavior_key,
                        behavior=behavior,
                        trigger_source=triggered.trigger_source.value,
                        trigger_details=triggered.trigger_details,
                    )
                )
                emit(
                    ContextEvent(
                        name=f"behaviors:async:{triggered.behavior_key}",
                        phase="info",
                        meta={
                            "trigger_source": triggered.trigger_source.value,
                            "trigger_details": triggered.trigger_details,
                            "status": "pending_enqueue",
                        },
                        ts_ms=_now_ms(),
                    )
                )

        # 5. Execute priority behaviors inline (no job queue overhead)
        # Job records are created AFTER execution for audit trail
        behavior_outputs: List[Dict[str, Any]] = []

        if priority_behaviors:
            emit(
                ContextEvent(
                    name="behaviors:priority:execute",
                    phase="start",
                    meta={"count": len(priority_behaviors)},
                    ts_ms=_now_ms(),
                )
            )

            for triggered in priority_behaviors:
                behavior = behaviors_by_key.get(triggered.behavior_key)
                if not behavior:
                    continue

                result = None
                status = "completed"
                error_msg = None

                try:
                    # Execute behavior inline (no job_id needed upfront)
                    result = await self._execute_behavior_inline(
                        behavior=behavior,
                        triggered=triggered,
                        companion_id=companion_id,
                    )

                    if result and result.get("prompt_block"):
                        behavior_outputs.append(
                            {
                                "name": behavior.get("name", triggered.behavior_key),
                                "description": behavior.get("description"),
                                "classifier_hint": behavior.get("classifier_hint"),
                                "output": result["prompt_block"],
                            }
                        )

                    # Collect effects from behavior result
                    if result and result.get("effects"):
                        for effect in result["effects"]:
                            # Inject source_behavior_key into effect payload
                            effect_payload = dict(effect)
                            effect_payload["source_behavior_key"] = triggered.behavior_key
                            effects.append(
                                TurnEffect(
                                    effect_type=effect.get("type", "unknown"),
                                    payload=effect_payload,
                                )
                            )

                except TimeoutError:
                    logger.warning(f"Priority behavior {triggered.behavior_key} timed out")
                    status = "failed"  # Use 'failed' - DB constraint doesn't allow 'timeout'
                    error_msg = f"Timed out after {self.priority_timeout_ms}ms"

                except Exception as e:
                    logger.error(f"Priority behavior {triggered.behavior_key} failed: {e}")
                    status = "failed"
                    error_msg = str(e)

                # Create job record AFTER execution (for audit trail, non-blocking)
                # Fire and forget - don't await to avoid adding latency
                job_id = None
                try:
                    if self.conn_factory:
                        async with self.conn_factory() as conn:
                            job_id = await self._create_completed_job_record(
                                conn, behavior, triggered, companion_id, result, status, error_msg
                            )
                    elif self.conn:
                        job_id = await self._create_completed_job_record(
                            self.conn, behavior, triggered, companion_id, result, status, error_msg
                        )
                except Exception as e:
                    logger.warning(f"Failed to create job record for {triggered.behavior_key}: {e}")

                emit(
                    ContextEvent(
                        name=f"behaviors:priority:{triggered.behavior_key}",
                        phase="end" if status == "completed" else status,
                        meta={
                            "job_id": job_id,
                            "has_output": bool(result and result.get("prompt_block")),
                            "status": status,
                            "effects_count": len(result.get("effects", [])) if result else 0,
                            "error": error_msg,
                        },
                        ts_ms=_now_ms(),
                    )
                )

            emit(
                ContextEvent(
                    name="behaviors:priority:execute",
                    phase="end",
                    meta={"behavior_outputs_count": len(behavior_outputs)},
                    ts_ms=_now_ms(),
                )
            )

        # Build prompt message from priority behavior outputs
        if behavior_outputs:
            # Format each behavior's output with its metadata
            formatted_blocks = []
            for bo in behavior_outputs:
                block_parts = [f"## {bo['name']}"]
                if bo.get("description"):
                    block_parts.append(bo["description"])
                if bo.get("classifier_hint"):
                    block_parts.append(f"Intent: {bo['classifier_hint']}")
                block_parts.append(f"\nOutput:\n{bo['output']}")
                formatted_blocks.append("\n".join(block_parts))

            combined_block = "\n\n---\n\n".join(formatted_blocks)
            messages.append(
                {
                    "role": "system",
                    "content": f"# BEHAVIOR CONTEXT\n\n{combined_block}",
                }
            )

        # Summary event
        emit(
            ContextEvent(
                name="behaviors:summary",
                phase="info",
                meta={
                    "triggered_count": len(triggered_behaviors),
                    "priority_executed": len(priority_behaviors),
                    "async_pending": len(pending_async),
                    "behavior_outputs_count": len(behavior_outputs),
                    "duration_ms": _now_ms(),
                },
                ts_ms=_now_ms(),
            )
        )

        return LayerOutput(
            messages=messages,
            events=events,
            effects=effects,
            pending_async_behaviors=pending_async,
        )

    async def _load_companion_behaviors(
        self,
        conn: asyncpg.Connection,
        companion_id: str,
    ) -> List[Dict[str, Any]]:
        """Load all enabled behaviors for a companion from DB.

        Uses BehaviorRepository which supports relationship-specific overrides.
        If turn_context.relationship_id is set, relationship-level configs
        take precedence over companion-level defaults.
        """
        from uuid import UUID as UUIDType

        companion_uuid = UUIDType(companion_id) if isinstance(companion_id, str) else companion_id
        relationship_id = self.turn_context.relationship_id

        behaviors = await BehaviorRepository.get_active_behaviors_for_companion(
            conn,
            companion_uuid,
            relationship_id=relationship_id,
        )
        # Convert UUID id to string for consistency with existing code
        # Use triggers_parsed for trigger evaluation (dict format), not triggers (shorthand)
        return [
            {
                **behavior,
                "id": str(behavior["id"]),
                "triggers": behavior.get("triggers_parsed", []),  # Use parsed dict format
            }
            for behavior in behaviors
        ]

    async def _enqueue_behavior_job(
        self,
        conn: asyncpg.Connection,
        behavior: Dict[str, Any],
        triggered: TriggeredBehavior,
        companion_id: str,
    ) -> str:
        """Enqueue a job for behavior execution."""
        params = {
            "behavior_key": behavior["key"],
            "trigger_source": triggered.trigger_source.value,
            "trigger_details": triggered.trigger_details,
            "user_message": self.turn_context.message,
            "turn_count": self.turn_context.turn_count,
            "keywords": self.turn_context.keywords,
        }

        job = await JobRepository.enqueue(
            conn,
            job_type="behavior_execution",
            companion_id=UUID(companion_id),
            conversation_id=self.turn_context.conversation_id,
            external_user_id=self.turn_context.external_user_id,
            behavior_key=behavior["key"],
            params=params,
        )
        return str(job.id)

    async def _execute_behavior_inline(
        self,
        behavior: Dict[str, Any],
        triggered: TriggeredBehavior,
        companion_id: str,
    ) -> Dict[str, Any] | None:
        """Execute a priority behavior inline and return its result.

        Uses Modal Functions for fast execution:
        - Trusted path (~100-300ms): Warm containers, reused across requests
        - Isolated path (~300-500ms): Fresh container per request, network blocked

        The path is determined by behavior["isolated"] flag (default: False = trusted).
        """
        import modal

        # Build context data for the behavior
        context_data = {
            "message": self.turn_context.message,
            "companion_id": companion_id,
            "conversation_id": str(self.turn_context.conversation_id)
            if self.turn_context.conversation_id
            else None,
            "relationship_id": str(self.turn_context.relationship_id)
            if self.turn_context.relationship_id
            else None,
            "external_user_id": self.turn_context.external_user_id,
            "turn_count": self.turn_context.turn_count,
            "trigger_source": triggered.trigger_source.value,
            "trigger_details": triggered.trigger_details,
            "behavior_params": behavior.get("params", {}),
            "state": {
                "profile": self.profile,
            },
        }

        source_code = behavior["source_code"]
        context_json = json.dumps(context_data)

        # Choose execution path based on isolation setting
        # Default to trusted (fast) path for backwards compatibility
        is_isolated = behavior.get("isolated", False)

        if is_isolated:
            # ISOLATED PATH: Fresh container, no network, no Modal access
            fn = modal.Function.from_name(
                "em-context-behavior-executor", "execute_behavior_isolated"
            )
        else:
            # TRUSTED PATH: Warm container, fast execution
            fn = modal.Function.from_name(
                "em-context-behavior-executor", "execute_behavior_trusted"
            )

        # Call the Modal function
        result_json = await fn.remote.aio(source_code, context_json)
        return json.loads(result_json)

    async def _create_completed_job_record(
        self,
        conn: asyncpg.Connection,
        behavior: Dict[str, Any],
        triggered: TriggeredBehavior,
        companion_id: str,
        result: Dict[str, Any] | None,
        status: str,
        error_msg: str | None,
    ) -> str:
        """Create a job record after priority behavior execution (for audit trail)."""
        params = {
            "behavior_key": behavior["key"],
            "trigger_source": triggered.trigger_source.value,
            "trigger_details": triggered.trigger_details,
            "user_message": self.turn_context.message,
            "turn_count": self.turn_context.turn_count,
            "keywords": self.turn_context.keywords,
            "priority": True,
        }

        # Create job in completed/failed state directly
        job = await JobRepository.create_completed_job(
            conn,
            job_type="behavior_execution",
            companion_id=UUID(companion_id),
            conversation_id=self.turn_context.conversation_id,
            external_user_id=self.turn_context.external_user_id,
            behavior_key=behavior["key"],
            params=params,
            status=status,
            result=result,
            error=error_msg,
        )
        return str(job.id)

    async def _wait_for_job(
        self,
        job_id: str,
        timeout_ms: int,
    ) -> Dict[str, Any] | None:
        """Wait for a job to complete and return its result.

        This polls the jobs table for completion. In production, this could
        be optimized with pg_notify or a more sophisticated mechanism.
        """
        timeout_s = timeout_ms / 1000.0
        start = time.perf_counter()
        poll_interval = 0.1  # 100ms
        job_uuid = UUID(job_id)

        while (time.perf_counter() - start) < timeout_s:
            if self.conn_factory:
                async with self.conn_factory() as conn:
                    job = await JobRepository.get_job_by_id(conn, job_uuid)
            else:
                job = await JobRepository.get_job_by_id(self.conn, job_uuid)

            if job:
                if job.status == "completed":
                    result = job.result if job.result else {}
                    return {"status": "completed", **result}
                elif job.status == "failed":
                    return {"status": "failed", "error": job.error}

            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"Job {job_id} did not complete within {timeout_ms}ms")


__all__ = ["BehaviorRuntime", "evaluate_behavior_triggers", "evaluate_trigger"]
