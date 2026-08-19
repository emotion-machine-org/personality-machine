"""Intent Classifier for the Context Engine.

The intent classifier is an LLM-based router that decides which layers and behaviors
to run based on user message and context. It runs in parallel with deterministic
triggers and produces a ClassifierOutput with layer decisions and behavior selections.

Key Design Decisions:
- Uses GPT via OpenRouter by default for speed (~100-200ms)
- Fails closed: on error/timeout, returns None and orchestrator uses raw mode
- Structured JSON output with Pydantic validation
- Classifier is informed about always_run layers but can't disable them
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List

import httpx

from .classifier_schemas import (
    LAYER_DESCRIPTIONS,
    ClassifierConfig,
    ClassifierInput,
    ClassifierOutput,
    ClassifierResult,
    LayerDecision,
    LayerInfo,
)
from .resolved_config import CompanionRuntimeConfig

logger = logging.getLogger(__name__)


# =============================================================================
# Classifier Prompt Template
# =============================================================================

CLASSIFIER_SYSTEM_PROMPT = """You are an intent classifier for a companion AI. Analyze the user's message and decide which information sources and behaviors are needed.

Respond with valid JSON matching this exact structure:
{
  "layers": {
    "memory": {"run": true},
    "knowledge_base": {"run": false},
    "tools": {"run": false}
  },
  "behaviors": ["behavior_key_1"]
}

Guidelines:
- memory: Enable when user references past interactions, personal details, preferences, or ongoing tasks
- knowledge_base: Enable when user asks factual questions that might be in documentation or FAQs
- tools: Enable when user needs real-time data, scheduling, or external integrations; consider available tool capabilities summary
- behaviors: ONLY select behavior keys from the "Available Behaviors" list provided. Never invent or generate behavior names. If no behaviors match, return an empty array []
- Be conservative: only enable what's clearly needed to avoid latency
- Respond ONLY with the JSON object, no markdown code blocks or extra text"""


def build_classifier_prompt(input: ClassifierInput) -> str:
    """Build the user prompt for the classifier."""
    parts = []

    # Available layers
    if input.available_layers:
        parts.append("## Available Layers")
        for layer in input.available_layers:
            always_run_note = " (ALWAYS RUNS - included for context)" if layer.always_run else ""
            parts.append(f"- {layer.name}: {layer.description}{always_run_note}")
        parts.append("")

    # Available behaviors
    if input.available_behaviors:
        parts.append("## Available Behaviors")
        for behavior in input.available_behaviors:
            parts.append(f"- {behavior.key}: {behavior.classifier_hint}")
        parts.append("")

    # Tool capabilities (if tools layer available and summaries exist)
    if input.tool_summaries:
        parts.append("## Summary of Available API Tools")
        for ts in input.tool_summaries:
            name = ts.get("spec_name") or "Unnamed API Spec"
            summary = ts.get("summary") or ""
            if summary:
                parts.append(f"- {name}: {summary}")
        parts.append("")

    # Recent history (last 10 messages)
    if input.history:
        parts.append("## Recent History")
        for msg in input.history[-10:]:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            parts.append(f"  {role}: {content}")
        parts.append("")

    # State summary
    if input.profile:
        parts.append("## Profile State")
        parts.append(f"Profile keys: {list(input.profile.keys())[:5]}")
        parts.append("")

    # User message
    parts.append("## User Message")
    parts.append(f'"{input.user_message}"')

    return "\n".join(parts)


# =============================================================================
# Classifier Execution
# =============================================================================


async def run_intent_classifier(
    input: ClassifierInput,
    config: ClassifierConfig,
) -> ClassifierResult:
    """Run the intent classifier and return structured decisions.

    Args:
        input: ClassifierInput with message, history, state, and available options
        config: ClassifierConfig with model selection and timeout

    Returns:
        ClassifierResult with output (if successful), timing info, and error details
    """
    t0 = time.perf_counter()

    if not config.enabled:
        return ClassifierResult(
            output=None,
            success=False,
            error="Classifier disabled",
            duration_ms=0,
            fallback_to_raw=True,
        )

    try:
        # Build the prompt
        user_prompt = build_classifier_prompt(input)

        # Select model and make the call via OpenRouter
        # Note: ClassifierModel is a str enum, so isinstance(model, str) is True but we need .value
        config.model.value if hasattr(config.model, "value") else str(config.model)

        # Always use gpt-oss-20b:nitro for classification (fast and reliable)
        openrouter_model = "openai/gpt-oss-20b:nitro"

        output = await _call_openrouter_classifier(
            user_prompt,
            config.timeout_ms,
            model=openrouter_model,
            custom_instructions=config.instructions,
        )

        duration_ms = (time.perf_counter() - t0) * 1000

        if output is None:
            return ClassifierResult(
                output=None,
                success=False,
                error="Failed to parse classifier response",
                duration_ms=duration_ms,
                model_used=config.model,
                fallback_to_raw=True,
            )

        return ClassifierResult(
            output=output,
            success=True,
            duration_ms=duration_ms,
            model_used=config.model,
        )

    except TimeoutError:
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.warning(f"Classifier timed out after {duration_ms:.1f}ms")
        return ClassifierResult(
            output=None,
            success=False,
            error=f"Timeout after {config.timeout_ms}ms",
            duration_ms=duration_ms,
            model_used=config.model,
            fallback_to_raw=True,
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.exception(f"Classifier error: {e}")
        return ClassifierResult(
            output=None,
            success=False,
            error=str(e),
            duration_ms=duration_ms,
            model_used=config.model,
            fallback_to_raw=True,
        )


async def _call_openrouter_classifier(
    user_prompt: str,
    timeout_ms: int,
    model: str = "openai/gpt-oss-20b:nitro",
    custom_instructions: str | None = None,
) -> ClassifierOutput | None:
    """Call OpenRouter for classification using GPT-OSS."""
    api_key = os.getenv("OPENROUTER_API_KEY")

    # Build system prompt with optional custom instructions
    system_prompt = CLASSIFIER_SYSTEM_PROMPT
    if custom_instructions:
        system_prompt += f"\n\n## Custom Instructions\n{custom_instructions}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    t0 = time.perf_counter()
    logger.info(f"OpenRouter classifier ({model}): calling API...")

    # Debug: log full prompt
    logger.info(f"[CLASSIFIER] System prompt:\n{system_prompt}")
    logger.info(f"[CLASSIFIER] User prompt:\n{user_prompt}")

    try:
        async with httpx.AsyncClient(timeout=timeout_ms / 1000.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 256,
                    "reasoning": {
                        "effort": "low",
                    },
                },
            )
            response.raise_for_status()
            data = response.json()

        t_api = (time.perf_counter() - t0) * 1000
        logger.info(f"OpenRouter classifier ({model}): done in {t_api:.0f}ms")

        result_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        logger.info(f"[CLASSIFIER] Response: {result_text}")
        return _parse_classifier_response(result_text)

    except httpx.TimeoutException:
        raise TimeoutError(f"OpenRouter request timed out after {timeout_ms}ms")
    except Exception as e:
        logger.warning(f"OpenRouter classifier error: {e}")
        return None


def _parse_classifier_response(text: str) -> ClassifierOutput | None:
    """Parse the classifier's JSON response into a ClassifierOutput.

    Handles common issues like markdown code blocks, extra whitespace, etc.
    """
    if not text:
        return None

    # Strip markdown code blocks if present
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse classifier JSON: {e}\nText: {text[:500]}")
        return None

    try:
        # Parse layers
        layers: Dict[str, LayerDecision] = {}
        raw_layers = data.get("layers", {})
        for layer_name, decision_data in raw_layers.items():
            if isinstance(decision_data, dict):
                layers[layer_name] = LayerDecision(
                    run=bool(decision_data.get("run", False)),
                )

        # Parse behaviors
        behaviors = data.get("behaviors", [])
        if not isinstance(behaviors, list):
            behaviors = []
        behaviors = [str(b) for b in behaviors if b]

        return ClassifierOutput(
            layers=layers,
            behaviors=behaviors,
        )

    except Exception as e:
        logger.warning(f"Failed to construct ClassifierOutput: {e}")
        return None


# =============================================================================
# Helper Functions
# =============================================================================


def build_layer_info_list(config: CompanionRuntimeConfig) -> List[LayerInfo]:
    """Build the list of available layers for the classifier.

    Uses the companion config to determine which layers are enabled
    and whether they have always_run set.
    """
    layer_info = []

    # Memory layer
    if config.memory.enabled or config.is_layer_enabled("memory"):
        layer_info.append(
            LayerInfo(
                name="memory",
                category="memory",
                description=LAYER_DESCRIPTIONS.get("memory", "Retrieve relevant memories"),
                always_run=config.is_layer_always_run("memory"),
            )
        )

    # Knowledge layer - use custom classifier_summary if provided, else default
    if config.knowledge.enabled or config.is_layer_enabled("knowledge_base"):
        knowledge_description = (
            config.knowledge.classifier_summary
            if config.knowledge.classifier_summary
            else LAYER_DESCRIPTIONS.get("knowledge_base", "Search knowledge base")
        )
        layer_info.append(
            LayerInfo(
                name="knowledge_base",
                category="knowledge_base",
                description=knowledge_description,
                always_run=config.is_layer_always_run("knowledge_base"),
            )
        )

    # Tools layer
    if config.tools.enabled or config.is_layer_enabled("tools"):
        layer_info.append(
            LayerInfo(
                name="tools",
                category="tools",
                description=LAYER_DESCRIPTIONS.get("tools", "Access external tools"),
                always_run=config.is_layer_always_run("tools"),
            )
        )

    return layer_info


def apply_always_run_overrides(
    classifier_output: ClassifierOutput | None,
    config: CompanionRuntimeConfig,
) -> Dict[str, bool]:
    """Apply always_run overrides to classifier decisions.

    Returns a dict of layer_name -> should_run, with always_run layers
    forced to True regardless of classifier decision.
    """
    result: Dict[str, bool] = {}

    # Layer categories to check
    layer_categories = ["memory", "knowledge_base", "tools"]

    for category in layer_categories:
        # Start with classifier decision if available
        should_run = False
        if classifier_output and category in classifier_output.layers:
            should_run = classifier_output.layers[category].run

        # Override with always_run if set
        if config.is_layer_always_run(category):
            should_run = True

        result[category] = should_run

    return result


__all__ = [
    "CLASSIFIER_SYSTEM_PROMPT",
    "apply_always_run_overrides",
    "build_classifier_prompt",
    "build_layer_info_list",
    "run_intent_classifier",
]
