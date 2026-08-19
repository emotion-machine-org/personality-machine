from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Tuple
from uuid import UUID

import asyncpg

from ..db import get_db_connection
from ..repositories.behavior_repository import BehaviorRepository
from ..repositories.companion import CompanionRepository
from ..repositories.relationship_repository import RelationshipRepository
from ..repositories.tool_index_repository import ToolIndexRepository
from ..services.context_builder import get_full_history
from ..services.intro_context import drop_intro_preamble_from_history
from ..utils.profile import (
    build_profile_prompt_block,
    normalize_profile_for_runtime,
    prune_profile_contradicting_history,
)
from .behavior_runtime import BehaviorRuntime

# Intent classifier imports
from .classifier_schemas import (
    BehaviorInfo,
    ClassifierConfig,
    ClassifierInput,
    ClassifierResult,
)
from .hydration import ContextHydrator, HydrationData, Hydrator

# Backward compatibility alias
HydratedContext = HydrationData
from .intent_classifier import (
    apply_always_run_overrides,
    build_layer_info_list,
    run_intent_classifier,
)
from .knowledge_runtime import KnowledgeRuntime
from .layers import LayerOutput, LayerRuntime
from .memory_runtime import MemoryRuntime
from .resolved_config import CompanionRuntimeConfig
from .schemas import (
    ContextEvent,
    ContextPlan,
    PendingAsyncActionSchema,
    PendingAsyncBehaviorSchema,
    TestOverrides,
    TurnContext,
    TurnEffect,
)
from .tools_runtime import ToolsRuntime

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_LAST_N = 6


# =============================================================================
# Classifier-Only Result for Voice Filler
# =============================================================================
from dataclasses import dataclass, field


@dataclass
class ClassifierOnlyResult:
    """Result from run_classifier_only() for voice filler generation.

    Contains the classifier decision and tool summaries needed to generate
    context-aware filler phrases before tool execution.
    """

    success: bool
    layer_decisions: Dict[str, bool] = field(default_factory=dict)
    tool_summaries: List[Dict[str, str]] = field(default_factory=list)
    classifier_result: ClassifierResult | None = None
    error: str | None = None


# Enable parallel layer execution
ENABLE_PARALLEL_LAYERS = True
# Enable intent classifier (can be toggled for gradual rollout)
ENABLE_INTENT_CLASSIFIER = True


def _now_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


async def run_classifier_only(
    *,
    conn: asyncpg.Connection,
    companion_id: UUID,
    companion_config: Any,
    user_message: str,
    external_user_id: str | None = None,
    relationship_id: UUID | None = None,
    conversation_id: UUID | None = None,
    history_override: List[Dict[str, str]] | None = None,
    turn_count_override: int | None = None,
) -> ClassifierOnlyResult:
    """Run only the intent classifier without executing any layers.

    This is used by voice mode to quickly determine if tools are needed,
    so we can play a filler phrase while tools execute.

    Args:
        conn: Database connection
        companion_id: Companion UUID
        companion_config: Companion configuration dict
        user_message: The user's message to classify
        external_user_id: Optional external user ID
        relationship_id: Optional relationship ID (for v2 API)
        conversation_id: Optional conversation ID (for v1 API)
        history_override: Optional in-session history (for voice)
        turn_count_override: Optional explicit turn count

    Returns:
        ClassifierOnlyResult with layer_decisions, tool_summaries, and classifier_result
    """
    # Resolve config once at the start
    resolved = CompanionRuntimeConfig.from_companion_config(companion_config)

    # Check if classifier is enabled
    if not ENABLE_INTENT_CLASSIFIER or not resolved.classifier.enabled:
        return ClassifierOnlyResult(
            success=False,
            error="Classifier disabled",
        )

    try:
        # Hydrate context for classifier
        hydrated: HydratedContext | None = None
        try:
            hydrated = await ContextHydrator.hydrate(
                conn,
                companion_id=companion_id,
                conversation_id=conversation_id,
                external_user_id=external_user_id,
                use_cache=True,
            )
        except Exception as e:
            logger.warning(f"[CLASSIFIER_ONLY] Hydration failed: {e}")

        # Compute turn count
        computed_turn_count = 1
        if turn_count_override is not None:
            computed_turn_count = turn_count_override
        elif hydrated and hydrated.conversation_state:
            computed_turn_count = hydrated.conversation_state.turn_count or 1

        # Build classifier config
        classifier_config = ClassifierConfig(
            enabled=True,
            model=resolved.classifier.model,  # type: ignore
            timeout_ms=resolved.classifier.timeout_ms,
            history_limit=resolved.classifier.history_limit,
            instructions=resolved.classifier.instructions,
        )

        # Build available layers info
        available_layers = build_layer_info_list(resolved)

        # Load tool summaries if tools layer is enabled
        tool_summaries: List[Dict[str, str]] = []
        tools_layer_available = any(layer.name == "tools" for layer in available_layers)
        if tools_layer_available:
            tool_summaries = await ToolIndexRepository.load_tool_summaries(
                conn, companion_id=companion_id
            )

        # Build available behaviors info (classifier-eligible only)
        all_behaviors: List[Dict[str, Any]] = []
        available_behaviors: List[BehaviorInfo] = []

        try:
            all_behaviors = await BehaviorRepository.get_active_behaviors_for_companion(
                conn, companion_id, relationship_id=relationship_id
            )
            logger.info(f"[ORCHESTRATOR] Loaded {len(all_behaviors)} behaviors (single query)")
        except Exception as e:
            logger.warning(f"Failed to load behaviors: {e}")
            all_behaviors = []

        # Filter for classifier-eligible behaviors in memory
        available_behaviors = [
            BehaviorInfo(
                key=b["key"],
                name=b["name"],
                classifier_hint=b.get("classifier_hint") or "",
            )
            for b in all_behaviors
            if b.get("classifier_eligible") and b.get("classifier_hint")
        ]
        # Note: We do NOT skip the classifier even if there are no classifier-eligible
        # behaviors. The classifier also decides which layers to run (memory, knowledge,
        # tools), not just behaviors. Skipping it would break layer decisions.

        # Build history for classifier
        classifier_history: List[Dict[str, str]] = []
        source_history = (
            history_override if history_override else (hydrated.history if hydrated else None)
        )
        if source_history:
            for msg in source_history[-resolved.classifier.history_limit :]:
                classifier_history.append(
                    {
                        "role": msg.get("role", ""),
                        "content": msg.get("content", "")[:500],  # Truncate for classifier
                    }
                )

        # Helper to ensure state is a dict
        def _ensure_dict(val: Any) -> Dict[str, Any]:
            if val is None:
                return {}
            if isinstance(val, str):
                try:
                    import json

                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    return {}
            if isinstance(val, dict):
                return val
            return {}

        classifier_input = ClassifierInput(
            user_message=user_message,
            history=classifier_history,
            turn_count=computed_turn_count,
            profile=_ensure_dict(hydrated.profile) if hydrated else {},
            available_layers=available_layers,
            available_behaviors=available_behaviors,
            tool_summaries=tool_summaries,
        )

        # Run classifier
        classifier_result = await run_intent_classifier(classifier_input, classifier_config)

        if classifier_result.success and classifier_result.output:
            # Apply always_run overrides
            layer_decisions = apply_always_run_overrides(classifier_result.output, resolved)

            return ClassifierOnlyResult(
                success=True,
                layer_decisions=layer_decisions,
                tool_summaries=tool_summaries,
                classifier_result=classifier_result,
            )
        else:
            return ClassifierOnlyResult(
                success=False,
                error=classifier_result.error or "Classifier failed",
                classifier_result=classifier_result,
            )

    except Exception as e:
        logger.exception(f"[CLASSIFIER_ONLY] Exception: {e}")
        return ClassifierOnlyResult(
            success=False,
            error=str(e),
        )


async def build_context_plan(
    *,
    conn: asyncpg.Connection,
    companion_id: UUID,
    companion_config: Any,
    conversation_id: UUID | None,
    user_message: str | None,
    external_user_id: str | None = None,
    relationship_id: UUID | None = None,  # Phase 5: for per-relationship behavior configs
    session_id: UUID | None = None,  # Phase 8: active session for context loading
    session_isolated: bool | None = None,  # Phase 8: pre-fetched isolation status (avoids DB query)
    turn_count_override: int | None = None,  # Phase 5: explicit turn count (for v2 relationships)
    include_memory: bool = False,
    include_knowledge: bool = False,
    include_tools: bool = False,
    include_behaviors: bool = False,
    append_core_prompt: bool = True,
    append_history: bool = True,
    layer_keys: List[str] | None = None,
    context_mode_override: str | None = None,
    history_cache: bool = True,
    event_callback: Callable[[ContextEvent], None] | None = None,
    hydrate_state: bool = True,
    test_overrides: TestOverrides | None = None,
    use_classifier: bool | None = None,  # None = use config default
    include_profile_in_prompt: bool = False,  # Inject profile into system prompt
    profile_override: Dict[str, Any] | None = None,  # Profile override for v2 API
    profile_version: int | None = None,
    profile_updated_at: Any | None = None,
    history_override: List[Dict[str, str]]
    | None = None,  # In-session history for classifier (voice)
    classifier_result_override: ClassifierOnlyResult
    | None = None,  # Skip classifier if provided (voice filler)
) -> ContextPlan:
    """Build a ContextPlan with state hydration and TurnContext.

    Phase 2: Adds state hydration, TurnContext computation, and effects collection.
    Parallel execution is implemented but disabled by default (ENABLE_PARALLEL_LAYERS).
    """

    t0 = time.perf_counter()
    events: List[ContextEvent] = []
    trace: Dict[str, Any] = {}

    def emit(name: str, phase: str, meta: Dict[str, Any] | None = None) -> None:
        ev = ContextEvent(
            name=name,
            phase=phase,  # type: ignore[arg-type]
            meta=meta or {},
            ts_ms=_now_ms(t0),
        )
        events.append(ev)
        if event_callback:
            try:
                event_callback(ev)
            except Exception:
                # non-fatal; instrumentation should never break flow
                pass

    # Resolve config once at the start - single source of truth
    resolved = CompanionRuntimeConfig.from_companion_config(companion_config)
    context_mode = context_mode_override or resolved.context_mode
    raw_mode = context_mode == "raw"
    trace["resolved_config"] = {
        "context_mode": context_mode,
        "memory_enabled": resolved.memory.enabled,
        "knowledge_enabled": resolved.knowledge.enabled,
    }

    messages: List[Dict[str, str]] = []
    all_effects: List[TurnEffect] = []
    all_pending_async: List[PendingAsyncActionSchema] = []
    all_pending_async_behaviors: List[PendingAsyncBehaviorSchema] = []

    # Hydrate all context (parallel when ENABLE_PARALLEL_LAYERS, sequential otherwise)
    # Always hydrate regardless of mode - raw mode just skips running layers
    hydrated: HydrationData | None = None

    if hydrate_state:
        hydrate_start = time.perf_counter()
        try:
            # Use parallel hydration when parallel layers are enabled
            if ENABLE_PARALLEL_LAYERS:
                hydrated = await Hydrator.fetch(
                    conn=None,
                    conn_factory=get_db_connection,
                    companion_id=companion_id,
                    conversation_id=conversation_id,
                    external_user_id=external_user_id,
                    use_cache=history_cache,
                    preloaded_config=companion_config,
                )
                trace["hydration_mode"] = "parallel"
            else:
                hydrated = await Hydrator.fetch(
                    conn,
                    companion_id=companion_id,
                    conversation_id=conversation_id,
                    external_user_id=external_user_id,
                    use_cache=history_cache,
                    preloaded_config=companion_config,
                )
                trace["hydration_mode"] = "sequential"

            trace["hydrated"] = True
            trace["state_version"] = hydrated.state_version
            trace["turn_count"] = (
                hydrated.conversation_state.turn_count if hydrated.conversation_state else 0
            )
            trace["hydrate_fetch_ms"] = hydrated.fetch_ms
        except Exception as e:
            logger.error(
                "Hydration failed for companion %s, conversation %s: %s",
                companion_id,
                conversation_id,
                e,
                exc_info=True,
            )
            emit("state:hydrate", "error", {"error": str(e)})
            trace["hydrate_error"] = str(e)
        trace["hydrate_ms"] = _now_ms(hydrate_start)

    # Compute turn count - prefer explicit override, then conversation_state, then default to 1
    computed_turn_count = 1
    if turn_count_override is not None:
        computed_turn_count = turn_count_override
    elif hydrated and hydrated.conversation_state:
        computed_turn_count = hydrated.conversation_state.turn_count or 1

    # Build TurnContext for sharing across layers
    turn_context = TurnContext(
        message=user_message or "",
        companion_id=companion_id,
        conversation_id=conversation_id,
        external_user_id=external_user_id,
        relationship_id=relationship_id,  # Phase 5: for per-relationship behavior configs
        keywords=_extract_keywords(user_message) if user_message else None,
        turn_count=computed_turn_count,
    )
    trace["turn_context_keywords"] = turn_context.keywords

    # ==========================================================================
    # Intent Classifier: Decide which layers to run
    # ==========================================================================
    classifier_result: ClassifierResult | None = None
    layer_decisions: Dict[str, bool] = {}  # layer_category -> should_run
    should_use_classifier = False

    prefetched_history: List[Dict[str, Any]] | None = None

    # Check if we have a classifier result override (from voice filler flow)
    if classifier_result_override and classifier_result_override.success:
        # Use the pre-computed classifier result to avoid running classifier twice
        classifier_result = classifier_result_override.classifier_result
        layer_decisions = classifier_result_override.layer_decisions
        trace["classifier"] = {
            "success": True,
            "from_override": True,
            "layer_decisions": layer_decisions,
        }
        logger.info(
            f"[ORCHESTRATOR] Using classifier_result_override: layer_decisions={layer_decisions}"
        )
    else:
        # Determine if we should use the classifier
        should_use_classifier = (
            ENABLE_INTENT_CLASSIFIER
            and resolved.classifier.enabled
            and not raw_mode
            and user_message
        )
        if use_classifier is not None:
            should_use_classifier = use_classifier and not raw_mode and user_message

        if should_use_classifier and relationship_id and (not hydrated or not hydrated.history):
            # v2 API uses relationship_id - fetch history now for classifier
            message_limit = resolved.context.message_limit if resolved.context else 200
            prefetched_history = await RelationshipRepository.get_message_history(
                conn,
                relationship_id,
                session_id=session_id,
                limit=message_limit,
            )
            trace["prefetched_history_source"] = "relationship"
            trace["prefetched_history_count"] = len(prefetched_history)

        # Debug logging for classifier decision
        logger.info(
            f"[ORCHESTRATOR] Classifier decision: should_use={should_use_classifier}, "
            f"ENABLE_INTENT_CLASSIFIER={ENABLE_INTENT_CLASSIFIER}, "
            f"resolved.classifier.enabled={resolved.classifier.enabled}, "
            f"use_classifier={use_classifier}, raw_mode={raw_mode}, "
            f"has_user_message={bool(user_message)}"
        )

    # Early load of ALL behaviors (single query, filter in memory for classifier)
    # This avoids duplicate queries: one for classifier, another for behavior layer
    all_behaviors: List[Dict[str, Any]] = []
    available_behaviors: List[BehaviorInfo] = []

    if include_behaviors:
        try:
            all_behaviors = await BehaviorRepository.get_active_behaviors_for_companion(
                conn, companion_id, relationship_id=relationship_id
            )
            logger.info(f"[ORCHESTRATOR] Loaded {len(all_behaviors)} behaviors (single query)")
        except Exception as e:
            logger.warning(
                "Failed to load behaviors for companion %s: %s",
                companion_id,
                e,
                exc_info=True,
            )
            all_behaviors = []

        # Filter for classifier-eligible behaviors in memory
        if should_use_classifier:
            available_behaviors = [
                BehaviorInfo(
                    key=b["key"],
                    name=b["name"],
                    classifier_hint=b.get("classifier_hint") or "",
                )
                for b in all_behaviors
                if b.get("classifier_eligible") and b.get("classifier_hint")
            ]
            # Note: We do NOT skip the classifier even if there are no classifier-eligible
            # behaviors. The classifier also decides which layers to run (memory, knowledge,
            # tools), not just behaviors. Skipping it would break layer decisions.

    if should_use_classifier:
        emit("classifier:start", "start", {"model": resolved.classifier.model})
        classifier_start = time.perf_counter()

        try:
            # Build classifier input
            classifier_config = ClassifierConfig(
                enabled=True,
                model=resolved.classifier.model,  # type: ignore
                timeout_ms=resolved.classifier.timeout_ms,
                history_limit=resolved.classifier.history_limit,
                instructions=resolved.classifier.instructions,
            )

            # Build available layers info
            available_layers = build_layer_info_list(resolved)
            logger.info(
                f"[ORCHESTRATOR] Classifier available_layers: {[layer.name for layer in available_layers]}"
            )

            # Load tool summaries for classifier (only if tools layer exists AND is not always_run)
            # If always_run=True, classifier's decision is overridden anyway - skip the query
            tool_summaries: List[Dict[str, str]] = []
            tools_layer_available = any(layer.name == "tools" for layer in available_layers)
            tools_always_run = resolved.is_layer_always_run("tools")

            if tools_layer_available and not tools_always_run:
                tool_summaries = await ToolIndexRepository.load_tool_summaries(
                    conn, companion_id=companion_id
                )
                logger.info(f"[ORCHESTRATOR] Tool summaries loaded: {len(tool_summaries)}")
            elif tools_layer_available and tools_always_run:
                logger.info("[ORCHESTRATOR] Tools layer is always_run, skipping summary load")

            # available_behaviors was already loaded earlier (before classifier decision)

            # Build history for classifier (limited to history_limit)
            # Uses history_override first, then prefetched_history, then hydrated.history
            classifier_history: List[Dict[str, str]] = []
            source_history = (
                history_override
                if history_override
                else (
                    prefetched_history
                    if prefetched_history
                    else (hydrated.history if hydrated else None)
                )
            )
            if source_history:
                for msg in source_history[-resolved.classifier.history_limit :]:
                    classifier_history.append(
                        {
                            "role": msg.get("role", ""),
                            "content": msg.get("content", "")[:500],  # Truncate for classifier
                        }
                    )

            # Helper to ensure state is a dict (DB may return as JSON string)
            def _ensure_dict(val: Any) -> Dict[str, Any]:
                if val is None:
                    return {}
                if isinstance(val, str):
                    try:
                        return json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        return {}
                if isinstance(val, dict):
                    return val
                return {}

            classifier_input = ClassifierInput(
                user_message=user_message,
                history=classifier_history,
                turn_count=turn_context.turn_count,
                profile=_ensure_dict(hydrated.profile) if hydrated else {},
                available_layers=available_layers,
                available_behaviors=available_behaviors,
                tool_summaries=tool_summaries,
            )

            # Run classifier
            classifier_result = await run_intent_classifier(classifier_input, classifier_config)

            classifier_ms = _now_ms(classifier_start)
            if classifier_result.success and classifier_result.output:
                # Apply always_run overrides
                layer_decisions = apply_always_run_overrides(classifier_result.output, resolved)
                emit(
                    "classifier:end",
                    "end",
                    {
                        "duration_ms": classifier_ms,
                        "layers": {
                            k: v.model_dump() for k, v in classifier_result.output.layers.items()
                        },
                        "behaviors": classifier_result.output.behaviors,
                        "layer_decisions": layer_decisions,
                    },
                )
                trace["classifier"] = {
                    "success": True,
                    "duration_ms": classifier_ms,
                    "model": classifier_result.model_used,
                    "layer_decisions": layer_decisions,
                    "selected_behaviors": classifier_result.output.behaviors,
                }
                # Store the actual classifier prompt for debugging/transparency
                from .intent_classifier import CLASSIFIER_SYSTEM_PROMPT, build_classifier_prompt

                classifier_user_prompt = build_classifier_prompt(classifier_input)
                trace["classifier_prompt"] = (
                    f"{CLASSIFIER_SYSTEM_PROMPT}\n\n{classifier_user_prompt}"
                )
            else:
                # Classifier failed - fall back to raw mode behavior
                emit(
                    "classifier:error",
                    "error",
                    {
                        "duration_ms": classifier_ms,
                        "error": classifier_result.error,
                        "fallback": "raw_mode",
                    },
                )
                trace["classifier"] = {
                    "success": False,
                    "duration_ms": classifier_ms,
                    "model": classifier_result.model_used,
                    "error": classifier_result.error,
                    "fallback": "raw_mode",
                }
                # On classifier failure, don't run any optional layers (fail closed)
                raw_mode = True
                logger.warning(
                    f"Classifier failed, falling back to raw mode: {classifier_result.error}"
                )

        except Exception as e:
            classifier_ms = _now_ms(classifier_start)
            emit(
                "classifier:error",
                "error",
                {
                    "duration_ms": classifier_ms,
                    "error": str(e),
                    "fallback": "raw_mode",
                },
            )
            trace["classifier"] = {
                "success": False,
                "duration_ms": classifier_ms,
                "error": str(e),
                "fallback": "raw_mode",
            }
            raw_mode = True
            logger.exception(f"Classifier exception, falling back to raw mode: {e}")
    else:
        trace["classifier"] = {"skipped": True, "reason": "disabled or raw_mode"}

    # Core prompt (system) block - from test overrides or hydrated context
    if append_core_prompt:
        core_prompt = None
        if test_overrides and (
            test_overrides.core_system_prompt is not None
            or test_overrides.core_memories is not None
        ):
            # Test override: compose core_system_prompt + core_memories
            from .context_hydrator import _compose_core_system_prompt

            base_prompt = test_overrides.core_system_prompt or ""
            # If no base prompt override, try to get from hydrated config
            if (
                not base_prompt
                and hydrated
                and hydrated.companion_config
                and hydrated.companion_config.system_prompt
            ):
                base_prompt = hydrated.companion_config.system_prompt.full_system_prompt or ""
            core_memories = test_overrides.core_memories or []
            if base_prompt or core_memories:
                core_prompt = _compose_core_system_prompt(base_prompt, core_memories)
                trace["core_prompt_source"] = "test_override"
        elif hydrated and hydrated.core_system_prompt:
            core_prompt = hydrated.core_system_prompt
            trace["core_prompt_source"] = "hydrated"

        if core_prompt:
            messages.append({"role": "system", "content": core_prompt})

    # Phase 6: Build profile block if enabled. Injection is deferred until
    # AFTER layer outputs so profile sits closest to the user message and
    # wins over stale memory entries (recency bias in LLM attention).
    deferred_profile_block: str | None = None
    deferred_profile_keys: list[str] = []
    deferred_profile_data: Dict[str, Any] | None = None
    deferred_profile_schema: Dict[str, Any] | None = None
    if include_profile_in_prompt:
        # Use override if provided (v2 API), otherwise use hydrated profile
        profile_data = profile_override
        if profile_data is None and hydrated:
            profile_data = hydrated.profile
        profile_schema = getattr(companion_config, "profile_schema", None)
        deferred_profile_data = profile_data
        deferred_profile_schema = profile_schema if isinstance(profile_schema, dict) else None

        if profile_data:
            deferred_profile_block = build_profile_prompt_block(
                profile_data,
                profile_schema=deferred_profile_schema,
                profile_version=profile_version,
                profile_updated_at=profile_updated_at,
            )

        if deferred_profile_block and profile_data:
            deferred_profile_keys = list(profile_data.keys())
            trace["profile_injected"] = True
            trace["profile_keys"] = deferred_profile_keys
            trace["profile_version"] = profile_version
            trace["profile_updated_at"] = (
                profile_updated_at.isoformat()
                if hasattr(profile_updated_at, "isoformat")
                else profile_updated_at
            )
        else:
            trace["profile_injected"] = False
            trace["profile_reason"] = "empty_or_missing"

    # Optional layers - use resolved config for clean access
    layer_runtimes: List[LayerRuntime] = []

    # Use test override history, hydrated history, prefetched history, or fetch separately
    history_rows: List[Dict[str, Any]] = []
    if test_overrides and test_overrides.history is not None:
        history_rows = test_overrides.history
        trace["history_source"] = "test_override"
    elif hydrated and hydrated.history:
        history_rows = hydrated.history
        trace["history_source"] = "hydrated"
    elif prefetched_history is not None:
        # Reuse history we already fetched for the classifier
        history_rows = prefetched_history
        trace["history_source"] = "prefetched"
        trace["session_id"] = str(session_id) if session_id else None
    elif relationship_id:
        # Phase 8: v2 API uses relationship_id + optional session
        message_limit = resolved.context.message_limit if resolved.context else 200
        history_rows = await RelationshipRepository.get_message_history(
            conn,
            relationship_id,
            session_id=session_id,
            session_isolated=session_isolated,  # Pass pre-fetched value to avoid DB query
            limit=message_limit,
        )
        trace["history_source"] = "relationship"
        trace["session_id"] = str(session_id) if session_id else None
    elif conversation_id:
        # v1 API uses conversation_id
        history_rows = await get_full_history(conn, conversation_id, use_cache=history_cache)
        trace["history_source"] = "fetched"
    trace["history_count"] = len(history_rows)
    prior_history_rows = history_rows
    if user_message and history_rows:
        last_history = history_rows[-1]
        if (
            last_history
            and last_history.get("role") == "user"
            and str(last_history.get("content") or "") == user_message
        ):
            prior_history_rows = history_rows[:-1]
    prior_history_rows = drop_intro_preamble_from_history(
        prior_history_rows,
        companion_config,
    )
    if include_profile_in_prompt and deferred_profile_data:
        prior_history_rows, pruned_count = prune_profile_contradicting_history(
            prior_history_rows,
            user_message or "",
            deferred_profile_data,
            profile_schema=deferred_profile_schema,
            profile_version=profile_version,
        )
        if pruned_count:
            trace["profile_contradicting_history_pruned"] = pruned_count

    # Memory layer
    # When classifier is used, layer_decisions takes precedence
    # Otherwise, fall back to legacy include_* flags and config
    memory_should_run = (
        layer_decisions.get("memory", False)
        if layer_decisions
        else (
            include_memory
            or resolved.is_layer_enabled("memory")
            or (layer_keys and "memory" in layer_keys)
        )
    )
    # Check for test override - inject memories directly instead of running MemoryRuntime
    if test_overrides and test_overrides.regular_memories is not None:
        if test_overrides.regular_memories:
            # Format injected memories as a system message block
            memory_block = "# RETRIEVED MEMORIES\n" + "\n".join(
                f"- {mem}" for mem in test_overrides.regular_memories
            )
            messages.append({"role": "system", "content": memory_block})
            emit("memory:start", "start")
            emit(
                "memory:retrieving",
                "info",
                {"items": len(test_overrides.regular_memories), "source": "test_override"},
            )
            emit("memory:end", "end", {"duration_ms": 0})
        trace["memory_source"] = "test_override"
    elif memory_should_run and resolved.memory.enabled and not raw_mode:
        # Check memory version to decide which layer to use
        memory_version = resolved.memory.version if hasattr(resolved.memory, "version") else 1

        if memory_version == 2 and relationship_id:
            # Memory V2: scratchpad-based memory (requires relationship_id)
            from .memory_v2_layer import MemoryV2Layer

            layer_runtimes.append(
                MemoryV2Layer(
                    turn_context,
                    conn=None if ENABLE_PARALLEL_LAYERS else conn,
                    conn_factory=get_db_connection if ENABLE_PARALLEL_LAYERS else None,
                    max_entries=resolved.memory.max_entries
                    if hasattr(resolved.memory, "max_entries")
                    else 100,
                )
            )
            trace["memory_source"] = "memory_v2_layer"
            trace["memory_version"] = 2
        else:
            # Memory V1: vector-based retrieval (default)
            recent_messages = (
                [
                    {"role": r["role"], "content": r["content"]}
                    for r in prior_history_rows[-DEFAULT_MEMORY_LAST_N:]
                ]
                if prior_history_rows
                else None
            )
            # Use conn_factory for parallel execution, conn for sequential
            # Skip internal gate if classifier made the decision
            layer_runtimes.append(
                MemoryRuntime(
                    conn=None if ENABLE_PARALLEL_LAYERS else conn,
                    conn_factory=get_db_connection if ENABLE_PARALLEL_LAYERS else None,
                    companion_id=companion_id,
                    user_text=user_message,
                    external_user_id=external_user_id,
                    conversation_id=conversation_id,
                    recent_messages=recent_messages,
                    last_n=DEFAULT_MEMORY_LAST_N,
                    # Pass resolved memory config for future use
                    min_saliency=resolved.memory.min_saliency,
                    top_k=resolved.memory.top_k,
                    skip_gate=bool(layer_decisions),  # Skip gate if classifier decided
                )
            )
            trace["memory_source"] = "runtime"
            trace["memory_version"] = 1

    # Knowledge layer
    # When classifier is used, layer_decisions takes precedence
    knowledge_should_run = (
        layer_decisions.get("knowledge_base", False)
        if layer_decisions
        else (
            include_knowledge
            or resolved.is_layer_enabled("knowledge_base")
            or (layer_keys and "knowledge" in layer_keys)
        )
    )
    # Check for test override - inject knowledge directly instead of running KnowledgeRuntime
    if test_overrides and test_overrides.knowledge_results is not None:
        if test_overrides.knowledge_results:
            # Format injected knowledge as a system message block
            knowledge_block = "# RETRIEVED KNOWLEDGE\n" + "\n".join(
                f"- {kb}" for kb in test_overrides.knowledge_results
            )
            messages.append({"role": "system", "content": knowledge_block})
            emit("knowledge:start", "start")
            emit(
                "knowledge:retrieving",
                "info",
                {"items": len(test_overrides.knowledge_results), "source": "test_override"},
            )
            emit("knowledge:end", "end", {"duration_ms": 0})
        trace["knowledge_source"] = "test_override"
    elif knowledge_should_run and not raw_mode and user_message:
        assets_present = await _has_knowledge_assets(conn, companion_id)
        if assets_present:
            kb_params = resolved.layer_params("knowledge_base")
            # Use conn_factory for parallel execution, conn for sequential
            # Skip internal gate if classifier made the decision
            layer_runtimes.append(
                KnowledgeRuntime(
                    conn=None if ENABLE_PARALLEL_LAYERS else conn,
                    conn_factory=get_db_connection if ENABLE_PARALLEL_LAYERS else None,
                    companion_id=companion_id,
                    user_text=user_message,
                    top_k=kb_params.get("top_k", resolved.knowledge.top_k),
                    filters=kb_params.get("filters"),
                    mode=kb_params.get("gate_strategy", resolved.knowledge.gate_strategy),
                    skip_gate=bool(layer_decisions),  # Skip gate if classifier decided
                )
            )
            trace["knowledge_source"] = "runtime"

    # Tools layer
    # When classifier is used, layer_decisions takes precedence
    tools_should_run = (
        layer_decisions.get("tools", False)
        if layer_decisions
        else (
            include_tools
            or resolved.is_layer_enabled("tools")
            or (layer_keys and "tools" in layer_keys)
        )
    )
    logger.info(
        f"[ORCHESTRATOR] Tools decision: should_run={tools_should_run}, "
        f"layer_decisions={layer_decisions}, include_tools={include_tools}, "
        f"is_layer_enabled={resolved.is_layer_enabled('tools')}"
    )
    if tools_should_run and not raw_mode and user_message:
        tools_params = resolved.layer_params("tools")
        # Use conn_factory for parallel execution, conn for sequential
        layer_runtimes.append(
            ToolsRuntime(
                conn=None if ENABLE_PARALLEL_LAYERS else conn,
                conn_factory=get_db_connection if ENABLE_PARALLEL_LAYERS else None,
                companion_id=companion_id,
                user_text=user_message or "",
                tool_summary=resolved.tools.tool_summary,
                params=tools_params,
                relationship_id=relationship_id,
            )
        )

    # Actions layer (developer-defined behaviors triggered by keywords or classifier)
    actions_requested = (
        include_behaviors
        or resolved.is_layer_enabled("actions")
        or (layer_keys and "actions" in layer_keys)
    )

    # Check for test override to skip behaviors
    skip_behaviors = test_overrides.skip_behaviors if test_overrides else False
    if skip_behaviors:
        trace["behaviors_source"] = "skipped_by_test_override"

    # Get classifier-selected behaviors (if any)
    classifier_selected_behaviors: List[str] = []
    if classifier_result and classifier_result.success and classifier_result.output:
        classifier_selected_behaviors = classifier_result.output.behaviors or []
        trace["classifier_selected_behaviors"] = classifier_selected_behaviors

    if actions_requested and not raw_mode and user_message and not skip_behaviors:
        # Use profile_override if provided (v2 relationships), otherwise use hydrated
        effective_profile = (
            normalize_profile_for_runtime(
                profile_override,
                profile_schema=getattr(companion_config, "profile_schema", None),
            )
            if profile_override is not None
            else (hydrated.profile if hydrated else None)
        )

        # Transform all_behaviors to the format expected by BehaviorRuntime
        # (id as string, triggers from triggers_parsed)
        preloaded_behaviors = (
            [
                {
                    **b,
                    "id": str(b["id"]),
                    "triggers": b.get("triggers_parsed", []),
                }
                for b in all_behaviors
            ]
            if all_behaviors
            else None
        )

        layer_runtimes.append(
            BehaviorRuntime(
                turn_context=turn_context,
                conn=None if ENABLE_PARALLEL_LAYERS else conn,
                conn_factory=get_db_connection if ENABLE_PARALLEL_LAYERS else None,
                profile=effective_profile,
                classifier_selected_behaviors=classifier_selected_behaviors,
                preloaded_behaviors=preloaded_behaviors,
            )
        )

    # Create a layer event callback that forwards to the main emit
    def layer_event_callback(ev: ContextEvent) -> None:
        """Forward layer events to main callback for real-time streaming."""
        emit(ev.name, ev.phase, ev.meta)

    # Run layers (sequential by default, parallel when ENABLE_PARALLEL_LAYERS is True)
    if ENABLE_PARALLEL_LAYERS and len(layer_runtimes) > 1:
        # Parallel execution using asyncio.gather
        layer_outputs = await _run_layers_parallel(layer_runtimes, emit, t0, layer_event_callback)
        for layer, out in layer_outputs:
            if out:
                messages.extend(out.messages)
                all_effects.extend(out.effects)
                # Collect pending async actions (DEPRECATED)
                for pa in out.pending_async_actions:
                    all_pending_async.append(
                        PendingAsyncActionSchema(
                            action_key=pa.action_key,
                            action=pa.action,
                            trigger_source=pa.trigger_source,
                            trigger_details=pa.trigger_details,
                        )
                    )
                # Collect pending async behaviors (Phase 5)
                for pb in out.pending_async_behaviors:
                    all_pending_async_behaviors.append(
                        PendingAsyncBehaviorSchema(
                            behavior_key=pb.behavior_key,
                            behavior=pb.behavior,
                            trigger_source=pb.trigger_source,
                            trigger_details=pb.trigger_details,
                        )
                    )
                # Events already emitted via callback, no need to re-emit
    else:
        # Sequential execution (default, simpler debugging)
        for layer in layer_runtimes:
            emit(f"{layer.key}:start", "start")
            layer_start = time.perf_counter()
            try:
                out: LayerOutput = await layer.run(event_callback=layer_event_callback)
                messages.extend(out.messages)
                all_effects.extend(out.effects)
                # Collect pending async actions (DEPRECATED)
                for pa in out.pending_async_actions:
                    all_pending_async.append(
                        PendingAsyncActionSchema(
                            action_key=pa.action_key,
                            action=pa.action,
                            trigger_source=pa.trigger_source,
                            trigger_details=pa.trigger_details,
                        )
                    )
                # Collect pending async behaviors (Phase 5)
                for pb in out.pending_async_behaviors:
                    all_pending_async_behaviors.append(
                        PendingAsyncBehaviorSchema(
                            behavior_key=pb.behavior_key,
                            behavior=pb.behavior,
                            trigger_source=pb.trigger_source,
                            trigger_details=pb.trigger_details,
                        )
                    )
                # Events already emitted via callback, no need to re-emit
            except Exception as e:
                import traceback

                logger.error(
                    f"[ORCHESTRATOR] Layer {layer.key} failed: {e}\n{traceback.format_exc()}"
                )
                emit(
                    f"{layer.key}:error",
                    "error",
                    {"error": str(e), "traceback": traceback.format_exc()},
                )
            finally:
                emit(f"{layer.key}:end", "end", {"duration_ms": _now_ms(layer_start)})

    # Inject conversation summary if available (between memory/layers and history)
    # Order is: Memory V2 -> Summary -> Recent Messages
    if relationship_id and not raw_mode:
        try:
            from ..repositories.summary_repository import SummaryRepository

            summary = await SummaryRepository.get_latest_summary(conn, relationship_id)
            if summary:
                summary_block = (
                    "# CONVERSATION HISTORY SUMMARY\n"
                    "This summarizes earlier conversations with this user:\n\n"
                    f"{summary['content']}"
                )
                messages.append({"role": "system", "content": summary_block})
                trace["summary_version"] = summary["version"]
                trace["summary_message_count"] = summary["message_count"]
                emit(
                    "summary:injected",
                    "info",
                    {"version": summary["version"], "message_count": summary["message_count"]},
                )
        except Exception as e:
            logger.warning(f"Failed to load conversation summary: {e}")
            trace["summary_error"] = str(e)

    # Append prior history in order (optional)
    if append_history:
        messages.extend(
            {"role": row["role"], "content": row["content"]} for row in prior_history_rows
        )

    # Inject profile block after history so stale assistant/user turns cannot
    # outrank the current authoritative profile state.
    if deferred_profile_block:
        messages.append({"role": "system", "content": deferred_profile_block})
        emit("profile:inject", "info", {"keys": deferred_profile_keys})

    # Append current user message
    if user_message:
        messages.append({"role": "user", "content": user_message})

    build_ms = _now_ms(t0)
    trace["build_ms"] = build_ms

    token_usage = {
        "approx_prompt_characters": sum(len(m.get("content", "")) for m in messages),
        "approx_message_count": len(messages),
    }

    # Store hydrated context reference in trace for post-turn executor
    trace["hydrated_context"] = hydrated
    trace["effects_count"] = len(all_effects)
    trace["pending_async_count"] = len(all_pending_async)
    trace["pending_async_behaviors_count"] = len(all_pending_async_behaviors)

    # Build execution_summary - single source of truth for what ran
    execution_summary = _build_execution_summary(
        events=events,
        trace=trace,
        layer_decisions=layer_decisions,
        classifier_selected_behaviors=classifier_selected_behaviors,
        test_overrides=test_overrides,
        raw_mode=raw_mode,
    )
    trace["execution_summary"] = execution_summary

    # Base thinking marker for downstream event streams
    emit("thinking", "start")

    return ContextPlan(
        messages=messages,
        token_usage=token_usage,
        trace=trace,
        events=events,
        effects=all_effects,
        pending_async_actions=all_pending_async,
        pending_async_behaviors=all_pending_async_behaviors,
    )


__all__ = ["ClassifierOnlyResult", "build_context_plan", "run_classifier_only"]


def _build_execution_summary(
    events: List[ContextEvent],
    trace: Dict[str, Any],
    layer_decisions: Dict[str, bool],
    classifier_selected_behaviors: List[str],
    test_overrides: TestOverrides | None,
    raw_mode: bool,
) -> Dict[str, Any]:
    """Build a structured summary of what layers executed and why.

    This is the single source of truth for layer execution status.
    """
    event_names = [e.name for e in events]

    def has_event(prefix: str) -> bool:
        return any(n.startswith(prefix) for n in event_names)

    def get_event_meta(prefix: str, phase: str = None) -> Dict[str, Any] | None:
        """Get meta from a specific event."""
        for e in events:
            if e.name.startswith(prefix):
                if phase is None or e.phase == phase:
                    return e.meta
        return None

    # Determine what actually ran and why
    layers: Dict[str, Dict[str, Any]] = {}

    # Memory layer
    memory_ran = has_event("memory:retrieving") or has_event("memory:end")
    memory_source = trace.get("memory_source", "not_run")
    if memory_source == "test_override":
        memory_meta = get_event_meta("memory:retrieving", "info") or {}
        layers["memory"] = {
            "ran": True,
            "source": "test_override",
            "classifier_decision": layer_decisions.get("memory", False),
            "items": memory_meta.get("items", 0),
        }
    elif memory_ran:
        memory_meta = get_event_meta("memory:retrieving", "info") or {}
        layers["memory"] = {
            "ran": True,
            "source": "classifier" if layer_decisions.get("memory") else "always_run",
            "classifier_decision": layer_decisions.get("memory", False),
            "items": memory_meta.get("items", 0),
        }
    else:
        layers["memory"] = {
            "ran": False,
            "source": "not_requested",
            "classifier_decision": layer_decisions.get("memory", False),
            "reason": "raw_mode" if raw_mode else "classifier_skipped",
        }

    # Knowledge layer
    knowledge_ran = has_event("knowledge:retrieving") or has_event("knowledge:end")
    knowledge_source = trace.get("knowledge_source", "not_run")
    if knowledge_source == "test_override":
        knowledge_meta = get_event_meta("knowledge:retrieving", "info") or {}
        layers["knowledge_base"] = {
            "ran": True,
            "source": "test_override",
            "classifier_decision": layer_decisions.get("knowledge_base", False),
            "items": knowledge_meta.get("items", 0),
        }
    elif knowledge_ran:
        knowledge_meta = get_event_meta("knowledge:retrieving", "info") or {}
        layers["knowledge_base"] = {
            "ran": True,
            "source": "classifier" if layer_decisions.get("knowledge_base") else "always_run",
            "classifier_decision": layer_decisions.get("knowledge_base", False),
            "items": knowledge_meta.get("items", 0),
        }
    else:
        layers["knowledge_base"] = {
            "ran": False,
            "source": "not_requested",
            "classifier_decision": layer_decisions.get("knowledge_base", False),
            "reason": "raw_mode" if raw_mode else "classifier_skipped",
        }

    # Tools layer
    tools_ran = has_event("tools:") and not any(n == "tools:gated" for n in event_names)
    if tools_ran:
        layers["tools"] = {
            "ran": True,
            "source": "classifier" if layer_decisions.get("tools") else "always_run",
            "classifier_decision": layer_decisions.get("tools", False),
        }
    else:
        layers["tools"] = {
            "ran": False,
            "source": "not_requested",
            "classifier_decision": layer_decisions.get("tools", False),
            "reason": "raw_mode" if raw_mode else "classifier_skipped",
        }

    # Behaviors layer
    behaviors_ran = has_event("behaviors:async:") or has_event("behaviors:priority:")
    behaviors_source = trace.get("behaviors_source", "not_run")

    if behaviors_source == "skipped_by_test_override":
        layers["behaviors"] = {
            "ran": False,
            "source": "skipped_by_test_override",
            "classifier_decision": classifier_selected_behaviors,
        }
    elif behaviors_ran:
        # Find which behaviors were triggered
        triggered_behaviors = []
        for e in events:
            if e.name.startswith("behaviors:async:"):
                behavior_key = e.name.split(":")[-1]
                triggered_behaviors.append(behavior_key)

        # Get summary info
        summary_meta = get_event_meta("behaviors:summary", "info") or {}

        layers["behaviors"] = {
            "ran": True,
            "source": "triggered",
            "classifier_decision": classifier_selected_behaviors,
            "triggered_behaviors": triggered_behaviors,
            "priority_count": summary_meta.get("priority_executed", 0),
            "async_count": summary_meta.get("async_enqueued", 0),
        }
    else:
        layers["behaviors"] = {
            "ran": False,
            "source": "not_requested",
            "classifier_decision": classifier_selected_behaviors,
            "reason": "raw_mode" if raw_mode else "no_triggers_matched",
        }

    return {
        "layers": layers,
        "classifier_used": bool(layer_decisions),
        "raw_mode": raw_mode,
    }


def _extract_keywords(text: str) -> List[str]:
    """Extract keywords from user message for gate decisions.

    Simple tokenization - splits on whitespace, filters short words,
    lowercases, and removes common punctuation.
    """
    if not text:
        return []
    # Simple keyword extraction - can be enhanced later
    tokens = text.lower().split()
    keywords = []
    for t in tokens:
        cleaned = t.strip(".,!?;:\"'()[]{}").strip()
        if len(cleaned) >= 3 and cleaned.isalnum():
            keywords.append(cleaned)
    return keywords[:20]  # Cap at 20 keywords


async def _run_layers_parallel(
    layers: List[LayerRuntime],
    emit: Callable,
    t0: float,
    event_callback: Callable | None = None,
) -> List[Tuple[LayerRuntime, LayerOutput | None]]:
    """Run layers in parallel using asyncio.gather.

    Each layer is wrapped to emit start/end events and handle errors.
    Returns list of (layer, output) tuples in original order.
    """

    async def run_one(layer: LayerRuntime) -> Tuple[LayerRuntime, LayerOutput | None]:
        emit(f"{layer.key}:start", "start")
        layer_start = time.perf_counter()
        try:
            out = await layer.run(event_callback=event_callback)
            emit(f"{layer.key}:end", "end", {"duration_ms": _now_ms(layer_start)})
            return (layer, out)
        except Exception as e:
            emit(f"{layer.key}:error", "error", {"error": str(e)})
            emit(f"{layer.key}:end", "end", {"duration_ms": _now_ms(layer_start)})
            return (layer, None)

    results = await asyncio.gather(*[run_one(layer) for layer in layers])
    return list(results)


async def _has_knowledge_assets(conn: asyncpg.Connection, companion_id: UUID) -> bool:
    """Check if companion has knowledge assets or successful ingestion jobs."""
    try:
        return await CompanionRepository.has_knowledge_assets(conn, companion_id)
    except Exception:
        return False
