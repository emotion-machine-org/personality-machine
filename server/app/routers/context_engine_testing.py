"""
Context Engine Testing API - Internal testing endpoints for the layered orchestrator.

Provides endpoints for:
- Running orchestrator with test overrides
- Streaming traces in real-time via SSE
- Comparing raw vs layered mode side-by-side
"""

import json
import logging
import time
from typing import Any, Dict, List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..constants import DEFAULT_TEXT_LLM_MAX_TOKENS
from ..context import (
    ContextEvent,
    TestOverrides,
    TurnContext,
    build_context_plan,
    persist_turn_context,
)
from ..db import get_db, get_db_connection
from ..models.companion import CompanionDetail
from ..models.user import User
from ..repositories.behavior_repository import BehaviorRepository
from ..repositories.companion import CompanionRepository
from ..repositories.job_repository import JobRepository
from ..services.llm import resolve_max_tokens

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/context-engine-testing", tags=["context-engine-testing"])


# ──────────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ──────────────────────────────────────────────────────────────────────────────


class HistoryMessage(BaseModel):
    """A single message in the conversation history."""

    role: str
    content: str


class LayerAlwaysRunConfig(BaseModel):
    """Per-layer always_run configuration."""

    memory: bool = Field(False, description="Always run memory layer regardless of classifier")
    knowledge: bool = Field(
        False, description="Always run knowledge layer regardless of classifier"
    )
    tools: bool = Field(False, description="Always run tools layer regardless of classifier")
    behaviors: bool = Field(
        False, description="Always run behaviors layer regardless of classifier"
    )


class TestContextRequest(BaseModel):
    """Request body for context engine testing."""

    companion_id: UUID
    user_message: str = Field(..., description="The user's message to process")

    # Test overrides - when provided, skip layer execution and use these values
    core_system_prompt: str | None = Field(
        None, description="Override core system prompt (skips composing from config)"
    )
    core_memories: List[str] | None = Field(
        None, description="Override core memories to compose into system prompt"
    )
    regular_memories: List[str] | None = Field(
        None, description="Override retrieved memories (skips memory runtime)"
    )
    knowledge_results: List[str] | None = Field(
        None, description="Override knowledge results (skips knowledge runtime)"
    )
    history: List[HistoryMessage] | None = Field(None, description="Override conversation history")
    profile_override: Dict[str, Any] | None = Field(
        None, description="Optional profile JSON override for layered mode"
    )

    # Mode control
    include_memory: bool = Field(True, description="Enable memory layer")
    include_knowledge: bool = Field(True, description="Enable knowledge layer")
    include_tools: bool = Field(False, description="Enable tools layer")
    include_behaviors: bool = Field(False, description="Enable behaviors layer")
    include_profile_in_prompt: bool = Field(
        False, description="Inject profile_override into layered mode prompt as # PROFILE"
    )

    # Classifier settings
    use_classifier: bool = Field(False, description="Enable intent classifier for layer selection")
    classifier_model: str = Field(
        "fast", description="Classifier model: fast (gemini-flash), default, or custom"
    )
    layer_always_run: LayerAlwaysRunConfig = Field(
        default_factory=LayerAlwaysRunConfig, description="Per-layer always_run overrides"
    )

    # LLM settings
    model: str = Field("openai-gpt4o-mini", description="LLM model to use for generation")
    temperature: float = Field(0.7, description="Temperature for LLM generation")
    max_output_tokens: int | None = Field(
        None, description="Override max output tokens (uses companion config if not set)"
    )


class ContextOutput(BaseModel):
    """Output from a single context plan build."""

    mode: str
    messages: List[Dict[str, str]]
    events: List[Dict[str, Any]]
    trace: Dict[str, Any]
    effects: List[Dict[str, Any]]
    token_usage: Dict[str, Any]
    build_ms: float
    # LLM response
    assistant_response: str | None = None
    llm_ms: float | None = None
    # Classifier results (when use_classifier is True)
    classifier_result: Dict[str, Any] | None = None
    # Classifier prompt - the full prompt sent to the classifier
    classifier_prompt: str | None = None
    # Execution summary - single source of truth for what layers ran and why
    execution_summary: Dict[str, Any] | None = None


class TestContextResponse(BaseModel):
    """Response containing both raw and layered outputs for comparison."""

    raw: ContextOutput
    layered: ContextOutput


# ──────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────────────────────────────────────


def _create_sse_helper():
    """Create a stateful SSE formatter."""
    seq = 0

    def sse(event: str, data: Dict[str, Any] | None = None) -> str:
        nonlocal seq
        seq += 1
        payload_str = json.dumps(data or {})
        return f"event: {event}\nid: {seq}\ndata: {payload_str}\n\n"

    return sse


def _event_to_dict(ev: ContextEvent) -> Dict[str, Any]:
    """Convert ContextEvent to dictionary for serialization."""
    return {
        "name": ev.name,
        "phase": ev.phase,
        "meta": ev.meta,
        "ts_ms": ev.ts_ms,
    }


async def _get_companion_with_access_check(
    conn: asyncpg.Connection,
    companion_id: UUID,
    user: User,
) -> CompanionDetail:
    """Verify user has access to companion and return full companion config."""
    companion = await CompanionRepository.get_companion_by_id(conn, companion_id, user.id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found or not accessible",
        )
    return companion


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/run", response_model=TestContextResponse)
async def run_context_test(
    request: TestContextRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Run context engine with test overrides for both raw and layered modes.

    Returns both outputs for side-by-side comparison. This endpoint does NOT
    stream events - use /run/stream for real-time event streaming.
    """
    # Verify access and get companion config
    companion = await _get_companion_with_access_check(conn, request.companion_id, user)

    # Build test overrides from request
    test_overrides = TestOverrides(
        core_system_prompt=request.core_system_prompt,
        core_memories=request.core_memories,
        regular_memories=request.regular_memories,
        knowledge_results=request.knowledge_results,
        history=[{"role": h.role, "content": h.content} for h in request.history]
        if request.history
        else None,
        skip_behaviors=not request.include_behaviors,
    )

    # Build a minimal mock config for testing
    # Important: layers config must respect the include_* flags from the request
    # Note: system_prompt is included from the real companion config for hydration
    class MockConfig:
        class Memory:
            enabled = True
            min_saliency = 0.5
            top_k = 5

        class Knowledge:
            enabled = True
            top_k = 5
            gate_strategy = "semantic"

        class Tools:
            enabled = True
            tool_summary = None

        class Classifier:
            enabled = request.use_classifier
            model = request.classifier_model
            timeout_ms = 10000
            fallback_mode = "raw"

        # Include system_prompt from real companion config for hydration
        system_prompt = companion.config.system_prompt if companion.config else None
        profile_schema = companion.config.profile_schema if companion.config else None

        memory = Memory()
        knowledge = Knowledge()
        tools = Tools()
        classifier = Classifier()
        # Layers config that the resolver actually reads - respect include_* flags
        layers = [
            {
                "category": "memory",
                "enabled": request.include_memory,
                "always_run": request.layer_always_run.memory,
                "params": {},
            },
            {
                "category": "knowledge_base",
                "enabled": request.include_knowledge,
                "always_run": request.layer_always_run.knowledge,
                "params": {},
            },
            {
                "category": "tools",
                "enabled": request.include_tools,
                "always_run": request.layer_always_run.tools,
                "params": {},
            },
            {
                "category": "actions",
                "enabled": request.include_behaviors,
                "always_run": request.layer_always_run.behaviors,
                "params": {},
            },
        ]
        context_mode = "layered"

    mock_config = MockConfig()

    # Run raw mode
    raw_plan = await build_context_plan(
        conn=conn,
        companion_id=request.companion_id,
        companion_config=mock_config,
        conversation_id=None,
        user_message=request.user_message,
        external_user_id=f"test-user-{user.id}",
        include_memory=request.include_memory,
        include_knowledge=request.include_knowledge,
        include_tools=request.include_tools,
        include_behaviors=request.include_behaviors,
        context_mode_override="raw",
        test_overrides=test_overrides,
        include_profile_in_prompt=False,
    )

    # Run layered mode
    layered_plan = await build_context_plan(
        conn=conn,
        companion_id=request.companion_id,
        companion_config=mock_config,
        conversation_id=None,
        user_message=request.user_message,
        external_user_id=f"test-user-{user.id}",
        include_memory=request.include_memory,
        include_knowledge=request.include_knowledge,
        include_tools=request.include_tools,
        include_behaviors=request.include_behaviors,
        context_mode_override="layered",
        test_overrides=test_overrides,
        use_classifier=request.use_classifier,
        include_profile_in_prompt=request.include_profile_in_prompt,
        profile_override=request.profile_override if request.include_profile_in_prompt else None,
    )

    # Extract classifier result and input from trace
    classifier_trace = layered_plan.trace.get("classifier")
    classifier_result_data = None
    if classifier_trace and classifier_trace.get("success"):
        classifier_result_data = {
            "layers": classifier_trace.get("layer_decisions", {}),
            "selected_actions": classifier_trace.get("selected_actions", []),
            "duration_ms": classifier_trace.get("duration_ms"),
        }
    classifier_prompt_text = layered_plan.trace.get("classifier_prompt")

    # Persist turn context for layered mode (for debugging/analytics)
    try:
        turn_ctx = TurnContext(
            message=request.user_message,
            companion_id=request.companion_id,
            conversation_id=None,  # Test mode has no conversation
            external_user_id=f"test-user-{user.id}",
        )
        await persist_turn_context(
            conn,
            plan=layered_plan,
            turn_context=turn_ctx,
            message_id=None,
            llm_ms=None,
        )
    except Exception as e:
        logger.warning(f"Failed to persist test turn context: {e}")

    return TestContextResponse(
        raw=ContextOutput(
            mode="raw",
            messages=raw_plan.messages,
            events=[_event_to_dict(e) for e in raw_plan.events],
            trace={k: v for k, v in raw_plan.trace.items() if k != "hydrated_context"},
            effects=[e.model_dump() for e in raw_plan.effects],
            token_usage=raw_plan.token_usage,
            build_ms=raw_plan.trace.get("build_ms", 0),
            execution_summary=raw_plan.trace.get("execution_summary"),
        ),
        layered=ContextOutput(
            mode="layered",
            messages=layered_plan.messages,
            events=[_event_to_dict(e) for e in layered_plan.events],
            trace={k: v for k, v in layered_plan.trace.items() if k != "hydrated_context"},
            effects=[e.model_dump() for e in layered_plan.effects],
            token_usage=layered_plan.token_usage,
            build_ms=layered_plan.trace.get("build_ms", 0),
            classifier_result=classifier_result_data,
            classifier_prompt=classifier_prompt_text,
            execution_summary=layered_plan.trace.get("execution_summary"),
        ),
    )


@router.post("/run/stream")
async def stream_context_test(
    request: TestContextRequest,
    user: User = Depends(get_current_user),
):
    """
    Run context engine with test overrides and stream events in real-time.

    Streams both raw and layered mode builds sequentially, then calls the LLM
    for each mode and streams the responses.

    SSE Events:
    - mode_start: {"mode": "raw"|"layered"}
    - event: Layer events as they fire
    - llm_start: {"mode": "..."} - LLM call starting
    - llm_delta: {"mode": "...", "content": "..."} - Streaming token
    - mode_complete: {"mode": "...", "output": {...}}
    - done: All modes complete
    - error: On failure
    """
    from ..services.llm_resolver import resolve_llm_client

    # NOTE: We explicitly use get_db_connection() instead of Depends(get_db) here.
    # Depends(get_db) would hold the connection for the ENTIRE streaming duration,
    # exhausting the pool under concurrent load.
    async with get_db_connection() as conn:
        # Verify access and get companion config before starting stream
        companion = await _get_companion_with_access_check(conn, request.companion_id, user)
    # Connection released here, BEFORE streaming begins

    # Resolve max_output_tokens: request override > companion config > default
    companion_max_tokens = (
        companion.config.inference.max_output_tokens if companion.config else None
    )
    requested_max_tokens = (
        request.max_output_tokens or companion_max_tokens or DEFAULT_TEXT_LLM_MAX_TOKENS
    )

    # Capture values needed in the generator (connection already released above)
    companion_id = request.companion_id
    user_message = request.user_message
    user_id = user.id
    include_memory = request.include_memory
    include_knowledge = request.include_knowledge
    include_tools = request.include_tools
    include_behaviors = request.include_behaviors
    include_profile_in_prompt = request.include_profile_in_prompt
    profile_override = request.profile_override if request.include_profile_in_prompt else None
    use_classifier = request.use_classifier
    classifier_model = request.classifier_model
    layer_always_run = request.layer_always_run
    model = request.model
    temperature = request.temperature
    max_tokens_for_llm = resolve_max_tokens(model, requested_max_tokens)
    # Capture system_prompt for MockConfig (needed by hydrator)
    companion_system_prompt = companion.config.system_prompt if companion.config else None

    # Build test overrides from request
    test_overrides = TestOverrides(
        core_system_prompt=request.core_system_prompt,
        core_memories=request.core_memories,
        regular_memories=request.regular_memories,
        knowledge_results=request.knowledge_results,
        history=[{"role": h.role, "content": h.content} for h in request.history]
        if request.history
        else None,
        skip_behaviors=not include_behaviors,
    )

    # Build a minimal mock config for testing
    # Important: layers config must respect the include_* flags from the request
    # Note: system_prompt is included from the real companion config for hydration
    class MockConfig:
        class Memory:
            enabled = True
            min_saliency = 0.5
            top_k = 5

        class Knowledge:
            enabled = True
            top_k = 5
            gate_strategy = "semantic"

        class Tools:
            enabled = True
            tool_summary = None

        class Classifier:
            enabled = use_classifier
            model = classifier_model
            timeout_ms = 10000
            fallback_mode = "raw"

        # Include system_prompt from real companion config for hydration
        system_prompt = companion_system_prompt
        profile_schema = companion.config.profile_schema if companion.config else None

        memory = Memory()
        knowledge = Knowledge()
        tools = Tools()
        classifier = Classifier()
        # Layers config that the resolver actually reads - respect include_* flags
        layers = [
            {
                "category": "memory",
                "enabled": include_memory,
                "always_run": layer_always_run.memory,
                "params": {},
            },
            {
                "category": "knowledge_base",
                "enabled": include_knowledge,
                "always_run": layer_always_run.knowledge,
                "params": {},
            },
            {
                "category": "tools",
                "enabled": include_tools,
                "always_run": layer_always_run.tools,
                "params": {},
            },
            {
                "category": "actions",
                "enabled": include_behaviors,
                "always_run": layer_always_run.behaviors,
                "params": {},
            },
        ]
        context_mode = "layered"

    mock_config = MockConfig()

    async def event_stream():
        import asyncio

        sse = _create_sse_helper()

        try:
            # Acquire a fresh connection for the streaming context
            async with get_db_connection() as stream_conn:
                # ═══════════════════════════════════════════════════════════════
                # RAW MODE
                # ═══════════════════════════════════════════════════════════════
                yield sse("mode_start", {"mode": "raw"})

                # Stream events in real-time during build
                raw_events: List[Dict[str, Any]] = []
                raw_plan = None

                # Use a helper to collect and yield
                event_queue: asyncio.Queue = asyncio.Queue()

                def raw_callback(ev: ContextEvent):
                    ev_dict = _event_to_dict(ev)
                    raw_events.append(ev_dict)
                    event_queue.put_nowait(ev_dict)

                async def run_raw_build():
                    return await build_context_plan(
                        conn=stream_conn,
                        companion_id=companion_id,
                        companion_config=mock_config,
                        conversation_id=None,
                        user_message=user_message,
                        external_user_id=f"test-user-{user_id}",
                        include_memory=include_memory,
                        include_knowledge=include_knowledge,
                        include_tools=include_tools,
                        include_behaviors=include_behaviors,
                        context_mode_override="raw",
                        test_overrides=test_overrides,
                        event_callback=raw_callback,
                        include_profile_in_prompt=False,
                    )

                # Start build task
                raw_build_task = asyncio.create_task(run_raw_build())

                # Stream events until build completes
                while not raw_build_task.done():
                    try:
                        ev = await asyncio.wait_for(event_queue.get(), timeout=0.01)
                        yield sse("event", {"mode": "raw", **ev})
                    except TimeoutError:
                        continue

                # Drain any remaining events
                while not event_queue.empty():
                    ev = event_queue.get_nowait()
                    yield sse("event", {"mode": "raw", **ev})

                raw_plan = await raw_build_task

                # Call LLM for raw mode
                raw_response = ""
                raw_llm_ms = 0.0
                try:
                    llm_client, llm_model, _ = resolve_llm_client(model)
                    yield sse("llm_start", {"mode": "raw", "model": llm_model})
                    llm_start = time.perf_counter()

                    stream = await llm_client.chat.completions.create(
                        model=llm_model,
                        messages=raw_plan.messages,
                        temperature=temperature,
                        max_tokens=max_tokens_for_llm,
                        stream=True,
                    )

                    async for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            content = chunk.choices[0].delta.content
                            raw_response += content
                            yield sse("llm_delta", {"mode": "raw", "content": content})

                    raw_llm_ms = (time.perf_counter() - llm_start) * 1000.0
                    yield sse("llm_end", {"mode": "raw", "duration_ms": raw_llm_ms})
                except Exception as e:
                    logger.exception("LLM call failed for raw mode")
                    yield sse("llm_error", {"mode": "raw", "error": str(e)})

                raw_output = ContextOutput(
                    mode="raw",
                    messages=raw_plan.messages,
                    events=[_event_to_dict(e) for e in raw_plan.events],
                    trace={k: v for k, v in raw_plan.trace.items() if k != "hydrated_context"},
                    effects=[e.model_dump() for e in raw_plan.effects],
                    token_usage=raw_plan.token_usage,
                    build_ms=raw_plan.trace.get("build_ms", 0),
                    assistant_response=raw_response,
                    llm_ms=raw_llm_ms,
                    execution_summary=raw_plan.trace.get("execution_summary"),
                )
                yield sse("mode_complete", {"mode": "raw", "output": raw_output.model_dump()})

                # ═══════════════════════════════════════════════════════════════
                # LAYERED MODE
                # ═══════════════════════════════════════════════════════════════
                yield sse("mode_start", {"mode": "layered"})

                layered_events: List[Dict[str, Any]] = []
                layered_event_queue: asyncio.Queue = asyncio.Queue()

                def layered_callback(ev: ContextEvent):
                    ev_dict = _event_to_dict(ev)
                    layered_events.append(ev_dict)
                    layered_event_queue.put_nowait(ev_dict)

                async def run_layered_build():
                    return await build_context_plan(
                        conn=stream_conn,
                        companion_id=companion_id,
                        companion_config=mock_config,
                        conversation_id=None,
                        user_message=user_message,
                        external_user_id=f"test-user-{user_id}",
                        include_memory=include_memory,
                        include_knowledge=include_knowledge,
                        include_tools=include_tools,
                        include_behaviors=include_behaviors,
                        context_mode_override="layered",
                        test_overrides=test_overrides,
                        event_callback=layered_callback,
                        use_classifier=use_classifier,
                        include_profile_in_prompt=include_profile_in_prompt,
                        profile_override=profile_override,
                    )

                # Start build task
                layered_build_task = asyncio.create_task(run_layered_build())

                # Stream events until build completes
                while not layered_build_task.done():
                    try:
                        ev = await asyncio.wait_for(layered_event_queue.get(), timeout=0.01)
                        yield sse("event", {"mode": "layered", **ev})
                    except TimeoutError:
                        continue

                # Drain any remaining events
                while not layered_event_queue.empty():
                    ev = layered_event_queue.get_nowait()
                    yield sse("event", {"mode": "layered", **ev})

                layered_plan = await layered_build_task

                # Call LLM for layered mode
                layered_response = ""
                layered_llm_ms = 0.0
                try:
                    llm_client, llm_model, _ = resolve_llm_client(model)
                    yield sse("llm_start", {"mode": "layered", "model": llm_model})
                    llm_start = time.perf_counter()

                    stream = await llm_client.chat.completions.create(
                        model=llm_model,
                        messages=layered_plan.messages,
                        temperature=temperature,
                        max_tokens=max_tokens_for_llm,
                        stream=True,
                    )

                    async for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            content = chunk.choices[0].delta.content
                            layered_response += content
                            yield sse("llm_delta", {"mode": "layered", "content": content})

                    layered_llm_ms = (time.perf_counter() - llm_start) * 1000.0
                    yield sse("llm_end", {"mode": "layered", "duration_ms": layered_llm_ms})
                except Exception as e:
                    logger.exception("LLM call failed for layered mode")
                    yield sse("llm_error", {"mode": "layered", "error": str(e)})

                # Extract classifier result and input from trace
                classifier_trace = layered_plan.trace.get("classifier")
                classifier_result_data = None
                if classifier_trace and classifier_trace.get("success"):
                    classifier_result_data = {
                        "layers": classifier_trace.get("layer_decisions", {}),
                        "selected_actions": classifier_trace.get("selected_actions", []),
                        "duration_ms": classifier_trace.get("duration_ms"),
                    }
                classifier_prompt_text = layered_plan.trace.get("classifier_prompt")

                layered_output = ContextOutput(
                    mode="layered",
                    messages=layered_plan.messages,
                    events=[_event_to_dict(e) for e in layered_plan.events],
                    trace={k: v for k, v in layered_plan.trace.items() if k != "hydrated_context"},
                    effects=[e.model_dump() for e in layered_plan.effects],
                    token_usage=layered_plan.token_usage,
                    build_ms=layered_plan.trace.get("build_ms", 0),
                    assistant_response=layered_response,
                    llm_ms=layered_llm_ms,
                    classifier_result=classifier_result_data,
                    classifier_prompt=classifier_prompt_text,
                    execution_summary=layered_plan.trace.get("execution_summary"),
                )
                yield sse(
                    "mode_complete", {"mode": "layered", "output": layered_output.model_dump()}
                )

                # Persist turn context for layered mode
                try:
                    turn_ctx = TurnContext(
                        message=user_message,
                        companion_id=companion_id,
                        conversation_id=None,
                        external_user_id=f"test-user-{user_id}",
                    )
                    await persist_turn_context(
                        stream_conn,
                        plan=layered_plan,
                        turn_context=turn_ctx,
                        message_id=None,
                        llm_ms=int(layered_llm_ms) if layered_llm_ms else None,
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist test turn context: {e}")

                # Enqueue pending async behaviors
                if layered_plan.pending_async_behaviors:
                    try:
                        for pb in layered_plan.pending_async_behaviors:
                            await JobRepository.enqueue(
                                stream_conn,
                                job_type="behavior_execution",
                                companion_id=companion_id,
                                conversation_id=None,
                                external_user_id=f"test-user-{user_id}",
                                behavior_key=pb.behavior_key,
                                params={
                                    "behavior_key": pb.behavior_key,
                                    "trigger_source": pb.trigger_source,
                                    "trigger_details": pb.trigger_details,
                                    "user_message": user_message,
                                },
                            )
                        logger.info(
                            f"Enqueued {len(layered_plan.pending_async_behaviors)} async behaviors from test"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to enqueue async behaviors: {e}")

                yield sse(
                    "done",
                    {
                        "raw_build_ms": raw_output.build_ms,
                        "raw_llm_ms": raw_llm_ms,
                        "layered_build_ms": layered_output.build_ms,
                        "layered_llm_ms": layered_llm_ms,
                    },
                )

        except Exception as exc:
            logger.exception("Context engine test stream error")
            yield sse("error", {"detail": str(exc)})

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.get("/companions")
async def list_companions_for_testing(
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    List companions available for testing.

    Returns minimal info needed for the testing UI dropdown.
    """
    rows = await conn.fetch(
        """
        SELECT c.id, c.name,
               COALESCE(MAX(cv.created_at), c.created_at) as last_updated
        FROM companions c
        LEFT JOIN companion_versions cv ON c.id = cv.companion_id
        WHERE c.owner_id = $1
        GROUP BY c.id, c.name, c.created_at
        ORDER BY last_updated DESC
        LIMIT 50
        """,
        user.id,
    )
    return [
        {
            "id": str(row["id"]),
            "name": row["name"],
        }
        for row in rows
    ]


@router.get("/companions/{companion_id}/context")
async def get_companion_context(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Get context for a companion to pre-populate the testing UI.

    Returns the companion's actual core system prompt, core memories,
    and config settings so users can see the defaults before overriding them.
    """
    # Get full companion config (single DB call)
    companion = await _get_companion_with_access_check(conn, companion_id, user)

    # Get core system prompt from companion config
    core_system_prompt = ""
    if companion.config and companion.config.system_prompt:
        core_system_prompt = companion.config.system_prompt.get_effective_prompt()

    # Fetch core memories directly
    memory_rows = await conn.fetch(
        """
        SELECT content FROM memories
        WHERE companion_id = $1 AND is_core = true
        ORDER BY created_at
        """,
        companion_id,
    )
    core_memories = [row["content"] for row in memory_rows]

    # Extract max_output_tokens from companion config
    max_output_tokens = None
    if companion.config:
        max_output_tokens = companion.config.inference.max_output_tokens

    return {
        "companion_id": str(companion_id),
        "core_system_prompt": core_system_prompt,
        "core_memories": core_memories,
        "has_knowledge_assets": await _has_knowledge_assets(conn, companion_id),
        "max_output_tokens": max_output_tokens,
    }


async def _has_knowledge_assets(conn: asyncpg.Connection, companion_id: UUID) -> bool:
    """Check if companion has knowledge assets."""
    row = await conn.fetchrow(
        "SELECT 1 FROM knowledge_assets WHERE companion_id = $1 LIMIT 1",
        companion_id,
    )
    return row is not None


# ──────────────────────────────────────────────────────────────────────────────
# Saved Tests CRUD
# ──────────────────────────────────────────────────────────────────────────────


class SavedTestConfig(BaseModel):
    """Configuration saved in a test."""

    user_message: str
    core_system_prompt: str | None = None
    core_memories: List[str] | None = None
    regular_memories: List[str] | None = None
    knowledge_results: List[str] | None = None
    history: List[HistoryMessage] | None = None
    profile_override: Dict[str, Any] | None = None

    # Layer toggles
    include_memory: bool = True
    include_knowledge: bool = True
    include_tools: bool = False
    include_actions: bool = False
    include_profile_in_prompt: bool = False

    # Classifier settings
    use_classifier: bool = False
    classifier_model: str = "fast"
    layer_always_run: LayerAlwaysRunConfig = Field(default_factory=LayerAlwaysRunConfig)

    # LLM settings
    model: str = "openai-gpt4o-mini"
    max_output_tokens: int | None = None


class CreateTestRequest(BaseModel):
    """Request to create a saved test."""

    companion_id: UUID
    name: str = Field(..., min_length=1, max_length=200)
    config: SavedTestConfig


class UpdateTestRequest(BaseModel):
    """Request to update a saved test."""

    name: str | None = Field(None, min_length=1, max_length=200)
    config: SavedTestConfig | None = None


class SavedTestResponse(BaseModel):
    """Response for a saved test."""

    id: str
    companion_id: str
    name: str
    config: SavedTestConfig
    created_at: str
    updated_at: str


@router.get("/tests/{companion_id}", response_model=List[SavedTestResponse])
async def list_saved_tests(
    companion_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """List all saved tests for a companion."""
    from ..repositories.context_engine_test_repository import ContextEngineTestRepository

    # Verify user has access to companion
    await _get_companion_with_access_check(conn, companion_id, user)

    tests = await ContextEngineTestRepository.list_tests_for_companion(conn, companion_id)

    return [
        SavedTestResponse(
            id=str(t["id"]),
            companion_id=str(t["companion_id"]),
            name=t["name"],
            config=SavedTestConfig(**t["config"]),
            created_at=t["created_at"].isoformat(),
            updated_at=t["updated_at"].isoformat(),
        )
        for t in tests
    ]


@router.post("/tests", response_model=SavedTestResponse)
async def create_saved_test(
    request: CreateTestRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Create a new saved test. Auto-renames if name already exists."""
    from ..repositories.context_engine_test_repository import ContextEngineTestRepository

    # Verify user has access to companion
    await _get_companion_with_access_check(conn, request.companion_id, user)

    test = await ContextEngineTestRepository.create_test(
        conn,
        companion_id=request.companion_id,
        name=request.name,
        config=request.config.model_dump(),
        auto_rename=True,
    )

    return SavedTestResponse(
        id=str(test["id"]),
        companion_id=str(test["companion_id"]),
        name=test["name"],
        config=SavedTestConfig(**test["config"]),
        created_at=test["created_at"].isoformat(),
        updated_at=test["updated_at"].isoformat(),
    )


@router.put("/tests/{test_id}", response_model=SavedTestResponse)
async def update_saved_test(
    test_id: UUID,
    request: UpdateTestRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Update an existing saved test."""
    from ..repositories.context_engine_test_repository import ContextEngineTestRepository

    # Get test to verify ownership
    existing = await ContextEngineTestRepository.get_test_by_id(conn, test_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test not found",
        )

    # Verify user has access to the companion
    await _get_companion_with_access_check(conn, existing["companion_id"], user)

    # Update test
    test = await ContextEngineTestRepository.update_test(
        conn,
        test_id=test_id,
        name=request.name,
        config=request.config.model_dump() if request.config else None,
    )

    return SavedTestResponse(
        id=str(test["id"]),
        companion_id=str(test["companion_id"]),
        name=test["name"],
        config=SavedTestConfig(**test["config"]),
        created_at=test["created_at"].isoformat(),
        updated_at=test["updated_at"].isoformat(),
    )


@router.delete("/tests/{test_id}")
async def delete_saved_test(
    test_id: UUID,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Delete a saved test."""
    from ..repositories.context_engine_test_repository import ContextEngineTestRepository

    # Get test to verify ownership
    existing = await ContextEngineTestRepository.get_test_by_id(conn, test_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test not found",
        )

    # Verify user has access to the companion
    await _get_companion_with_access_check(conn, existing["companion_id"], user)

    await ContextEngineTestRepository.delete_test(conn, test_id)

    return {"success": True}


@router.get("/companions/{companion_id}/behaviors")
async def get_companion_behaviors(
    companion_id: UUID,
    linked_only: bool = False,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get behaviors for the project that contains this companion.

    Args:
        linked_only: If True, only return behaviors linked to this companion.
                     If False (default), return all project behaviors with link info.

    Returns behaviors with their configurations for testing purposes.
    """
    # Get companion to verify access and get project_id
    companion = await _get_companion_with_access_check(conn, companion_id, user)

    if not companion.project_id:
        return {"behaviors": []}

    # Fetch behaviors for the project with their companion link configs
    # Using custom query to include disabled behaviors (for testing UI)
    if linked_only:
        # Only return behaviors that are linked to this companion
        rows = await conn.fetch(
            """
            SELECT
                b.id, b.key, b.name, b.description, b.source_code, b.version,
                cbl.triggers, cbl.priority, cbl.enabled, cbl.classifier_hint
            FROM behaviors b
            INNER JOIN companion_behavior_links cbl
                ON cbl.behavior_id = b.id
                AND cbl.companion_id = $2
                AND cbl.relationship_id IS NULL
            WHERE b.project_id = $1
            ORDER BY b.name
            """,
            companion.project_id,
            companion_id,
        )
    else:
        # Return all project behaviors with link info (for behavior editor/testing)
        rows = await conn.fetch(
            """
            SELECT
                b.id, b.key, b.name, b.description, b.source_code, b.version,
                cbl.triggers, cbl.priority, cbl.enabled, cbl.classifier_hint
            FROM behaviors b
            LEFT JOIN companion_behavior_links cbl
                ON cbl.behavior_id = b.id
                AND cbl.companion_id = $2
                AND cbl.relationship_id IS NULL
            WHERE b.project_id = $1
            ORDER BY b.name
            """,
            companion.project_id,
            companion_id,
        )

    behaviors_with_links = []
    for row in rows:
        # priority: True -> "sync", False -> "async"
        priority_str = "sync" if row["priority"] else "async"
        behavior_info = {
            "id": str(row["id"]),
            "key": row["key"],
            "name": row["name"],
            "description": row["description"],
            "source_code": row["source_code"],
            "version": row["version"],
            "enabled": row["enabled"] if row["enabled"] is not None else False,
            "triggers": row["triggers"] if row["triggers"] else [],
            "priority": priority_str,
            "classifier_hint": row["classifier_hint"],
        }
        behaviors_with_links.append(behavior_info)

    return {"behaviors": behaviors_with_links}


# ──────────────────────────────────────────────────────────────────────────────
# Behavior Testing & Registration Endpoints
# ──────────────────────────────────────────────────────────────────────────────


class TestBehaviorRequest(BaseModel):
    """Request to test behavior code in sandbox."""

    source_code: str = Field(
        ..., max_length=100_000, description="Python async execute(ctx) function"
    )
    timeout_seconds: int = Field(60, ge=1, le=300, description="Execution timeout")

    # Mock context for testing
    mock_message: str = Field("Hello, how are you?", description="Mock user message")
    mock_turn_count: int = Field(1, ge=0, description="Mock turn count")
    mock_profile: Dict[str, Any] = Field(default_factory=dict, description="Mock profile data")


class TestBehaviorResponse(BaseModel):
    """Response from testing behavior code."""

    success: bool
    prompt_block: str | None = None
    effects: List[Dict[str, Any]] = Field(default_factory=list)
    trace: Dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0
    context_data: Dict[str, Any] | None = Field(
        None, description="Context data passed to behavior (debug)"
    )


class RegisterBehaviorRequest(BaseModel):
    """Request to register a behavior and link to companion."""

    companion_id: UUID
    key: str = Field(..., min_length=1, max_length=100, description="Unique behavior key")
    name: str = Field(..., min_length=1, max_length=200, description="Display name")
    description: str | None = Field(None, max_length=1000, description="Behavior description")
    source_code: str = Field(
        ..., max_length=100_000, description="Python async execute(ctx) function"
    )

    # Link config
    triggers: List[str] = Field(
        default_factory=list,
        description="Trigger strings: 'always', 'every_n:5', 'keyword:help,support', 'turn_count:1'",
    )
    priority: str = Field("async", description="Priority mode: 'sync' or 'async'")
    enabled: bool = Field(True, description="Whether behavior is enabled")
    classifier_hint: str | None = Field(
        None, max_length=500, description="Hint for intent classifier to determine when to trigger"
    )


class RegisterBehaviorResponse(BaseModel):
    """Response from registering a behavior."""

    id: UUID
    key: str
    name: str
    description: str | None
    source_code: str
    version: int
    triggers: List[str]
    priority: str
    enabled: bool
    classifier_hint: str | None = None


@router.post("/behaviors/test")
async def test_behavior_code(
    request: TestBehaviorRequest,
    user: User = Depends(get_current_user),
):
    """
    Test behavior code in Modal sandbox.

    Uses the same execution path as production priority behaviors.
    """
    import json
    import time

    import modal

    t0 = time.perf_counter()

    # Build context data
    context_data = {
        "message": request.mock_message,
        "companion_id": "test-companion",
        "conversation_id": "test-conversation",
        "external_user_id": f"test-user-{user.id}",
        "turn_count": request.mock_turn_count,
        "trigger_source": "test",
        "trigger_details": "Manual test execution",
        "state": {
            "profile": request.mock_profile,
        },
    }

    context_json = json.dumps(context_data)

    try:
        # Use trusted execution path (same as priority behaviors)
        fn = modal.Function.from_name("em-context-behavior-executor", "execute_behavior_trusted")

        # Call the Modal function
        result_json = await fn.remote.aio(request.source_code, context_json)

        # Handle case where Modal might auto-deserialize
        if isinstance(result_json, dict):
            result = result_json
        else:
            result = json.loads(result_json)

        duration_ms = (time.perf_counter() - t0) * 1000

        return TestBehaviorResponse(
            success=True,
            prompt_block=result.get("prompt_block"),
            effects=result.get("effects", []),
            trace=result.get("trace", {}),
            duration_ms=duration_ms,
            context_data=context_data,
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.exception(f"Behavior test failed: {e}")
        return TestBehaviorResponse(
            success=False,
            error=str(e),
            duration_ms=duration_ms,
            context_data=context_data,
        )


@router.post("/behaviors/register", response_model=RegisterBehaviorResponse)
async def register_behavior(
    request: RegisterBehaviorRequest,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Register a behavior and link to the specified companion.

    Creates or updates the behavior in the project, then links it to the companion.
    """
    from uuid import uuid4

    # Verify user has access to companion (via project ownership)
    companion = await conn.fetchrow(
        """
        SELECT c.id, c.project_id FROM companions c
        JOIN projects p ON c.project_id = p.id
        WHERE c.id = $1 AND p.owner_id = $2
        """,
        request.companion_id,
        user.id,
    )
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found or access denied",
        )

    project_id = companion["project_id"]

    # Check if behavior already exists
    existing = await BehaviorRepository.get_behavior_by_project_key(
        conn, project_id=project_id, behavior_key=request.key
    )

    if existing:
        # Update existing behavior
        behavior_id = existing["id"]
        new_version = existing["version"] + 1
        await conn.execute(
            """
            UPDATE behaviors
            SET name = $2, description = $3, source_code = $4, version = $5, updated_at = NOW()
            WHERE id = $1
            """,
            behavior_id,
            request.name,
            request.description,
            request.source_code,
            new_version,
        )
    else:
        # Create new behavior
        behavior_id = uuid4()
        new_version = 1
        await conn.execute(
            """
            INSERT INTO behaviors (id, project_id, key, name, description, source_code, version)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            behavior_id,
            project_id,
            request.key,
            request.name,
            request.description,
            request.source_code,
            new_version,
        )

    # Upsert companion behavior link
    # priority: "sync" -> True (orchestrator waits), "async" -> False (background)
    priority_bool = request.priority == "sync"

    # Check if link exists (relationship_id is NULL for companion-level config)
    existing_link = await conn.fetchrow(
        """
        SELECT id FROM companion_behavior_links
        WHERE companion_id = $1 AND behavior_id = $2 AND relationship_id IS NULL
        """,
        request.companion_id,
        behavior_id,
    )

    if existing_link:
        await conn.execute(
            """
            UPDATE companion_behavior_links
            SET triggers = $2, priority = $3, enabled = $4, classifier_hint = $5, updated_at = NOW()
            WHERE id = $1
            """,
            existing_link["id"],
            request.triggers,
            priority_bool,
            request.enabled,
            request.classifier_hint,
        )
    else:
        await conn.execute(
            """
            INSERT INTO companion_behavior_links (companion_id, behavior_id, triggers, priority, enabled, classifier_hint)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            request.companion_id,
            behavior_id,
            request.triggers,
            priority_bool,
            request.enabled,
            request.classifier_hint,
        )

    if request.enabled:
        await CompanionRepository.ensure_actions_layer_state(
            conn,
            request.companion_id,
            enabled=True,
        )

    return RegisterBehaviorResponse(
        id=behavior_id,
        key=request.key,
        name=request.name,
        description=request.description,
        source_code=request.source_code,
        version=new_version,
        triggers=request.triggers,
        priority=request.priority,
        enabled=request.enabled,
        classifier_hint=request.classifier_hint,
    )


@router.delete("/companions/{companion_id}/behaviors/{behavior_key}")
async def unlink_behavior(
    companion_id: UUID,
    behavior_key: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Unlink a behavior from a companion.

    Removes the companion_behavior_links row but keeps the behavior in the project.
    """
    # Verify user has access to companion
    await _get_companion_with_access_check(conn, companion_id, user)

    # Delete the link using repository method (idempotent - ok if already unlinked)
    deleted = await BehaviorRepository.delete_behavior_link_by_key(
        conn,
        companion_id=companion_id,
        behavior_key=behavior_key,
    )

    if deleted:
        return {"status": "ok", "message": f"Behavior '{behavior_key}' unlinked from companion"}
    else:
        return {"status": "ok", "message": f"Behavior '{behavior_key}' was not linked to companion"}


@router.delete("/behaviors/{behavior_key}")
async def delete_behavior(
    behavior_key: str,
    companion_id: UUID,  # Query param to identify the project
    conn: asyncpg.Connection = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Delete a behavior from the project entirely.

    Removes the behavior and all its links (via FK cascade).
    """
    # Verify user has access to companion and get project_id
    companion = await _get_companion_with_access_check(conn, companion_id, user)

    if not companion.project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Companion has no project",
        )

    # Find behavior by key
    behavior = await BehaviorRepository.get_behavior_by_project_key(
        conn, project_id=companion.project_id, behavior_key=behavior_key
    )
    if not behavior:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Behavior not found",
        )

    # Delete the behavior (cascade deletes all links)
    await BehaviorRepository.delete_behavior(conn, behavior["id"])

    return {"status": "ok", "message": f"Behavior '{behavior_key}' deleted from project"}
