"""CompanionRuntimeConfig: Runtime configuration for the orchestrator.

This module provides a clean, immutable configuration object that is:
1. Computed once at the start of orchestration
2. Has all values with sensible defaults applied
3. Passed to layers/runtimes that need it
4. Type-safe and easy to extend

Usage:
    config = CompanionRuntimeConfig.from_companion_config(companion_config)

    # Access values directly - no getattr() chains needed
    if config.memory.enabled:
        ...

    prompt = config.memory.evaluation_prompt
    mode = config.context_mode
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

BEHAVIOR_LAYER_ALIASES = frozenset({"actions", "behaviors"})


def _normalize_layer_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _layer_aliases(category: str) -> set[str]:
    normalized = _normalize_layer_name(category)
    if normalized in BEHAVIOR_LAYER_ALIASES:
        return set(BEHAVIOR_LAYER_ALIASES)
    return {normalized}


def _layer_entry_matches(
    layer_category: Any,
    layer_key: Any,
    category: str,
    key: str | None = None,
) -> bool:
    category_aliases = _layer_aliases(category)
    entry_category = _normalize_layer_name(layer_category)
    entry_key = _normalize_layer_name(layer_key)
    category_match = entry_category in category_aliases or entry_key in category_aliases
    if not category_match:
        return False
    if key is None:
        return True
    key_aliases = _layer_aliases(key)
    return entry_key in key_aliases or entry_category in key_aliases


@dataclass(frozen=True)
class ResolvedMemoryConfig:
    """Resolved memory configuration with defaults."""

    enabled: bool = False
    version: int = 1  # 1 = vector-based retrieval, 2 = scratchpad
    core_memories: List[str] = field(default_factory=list)
    evaluation_prompt: str = ""
    recency: float = 0.995
    top_k: int = 50
    min_saliency: float = 0.2
    # V2-specific settings
    max_entries: int = 100  # Max scratchpad entries for v2
    ingestion_model: str = "gemini-2.0-flash"  # LLM for v2 ingestion
    ingestion_prompt: str | None = None  # Custom ingestion prompt for v2


@dataclass(frozen=True)
class ResolvedKnowledgeConfig:
    """Resolved knowledge/RAG configuration."""

    enabled: bool = True  # Default enabled if assets exist
    gate_strategy: str = "keyword"  # "keyword", "always", "llm"
    top_k: int = 5
    min_confidence: float = 0.0
    classifier_summary: str | None = None  # Custom description for classifier
    # Future: source filters, reranking settings, etc.


@dataclass(frozen=True)
class ResolvedToolsConfig:
    """Resolved tools configuration."""

    enabled: bool = False
    tool_summary: str | None = None
    # Future: allowed tools, timeout, etc.


@dataclass(frozen=True)
class ResolvedActionsConfig:
    """Resolved actions configuration."""

    enabled: bool = True
    registered_actions: List[str] = field(default_factory=list)
    max_concurrent: int = 3
    use_llm_selection: bool = True
    # Future: action-specific params, webhooks config, etc.


@dataclass(frozen=True)
class ResolvedContextSettings:
    """Resolved context/token budget settings."""

    max_prompt_tokens: int | None = None
    target_prompt_fraction: float = 0.4
    reserved_completion_tokens: int | None = None
    message_limit: int = 200  # Maximum messages to load into context per turn


@dataclass(frozen=True)
class ResolvedWebhooksConfig:
    """Resolved webhooks configuration."""

    configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get(self, key: str) -> Dict[str, Any] | None:
        return self.configs.get(key)


@dataclass(frozen=True)
class ResolvedClassifierConfig:
    """Resolved classifier configuration.

    The intent classifier is an LLM-based router that decides which layers
    and actions to run based on user message and context.
    """

    enabled: bool = True
    model: str = "fast"  # "fast", "default", or "custom"
    custom_model_id: str | None = None  # For "custom" model
    timeout_ms: int = 10000
    history_limit: int = 20  # Last N messages to include for classifier
    instructions: str | None = None  # Custom instructions for when to enable layers


@dataclass(frozen=True)
class CompanionRuntimeConfig:
    """Fully resolved companion configuration.

    All values have defaults applied. Access is direct and type-safe.
    This object is immutable (frozen) to prevent accidental modification.
    """

    # Core settings
    context_mode: str = "layered"  # "layered" or "raw"

    # Intent classifier config
    classifier: ResolvedClassifierConfig = field(default_factory=ResolvedClassifierConfig)

    # Layer-specific configs
    memory: ResolvedMemoryConfig = field(default_factory=ResolvedMemoryConfig)
    knowledge: ResolvedKnowledgeConfig = field(default_factory=ResolvedKnowledgeConfig)
    tools: ResolvedToolsConfig = field(default_factory=ResolvedToolsConfig)
    actions: ResolvedActionsConfig = field(default_factory=ResolvedActionsConfig)
    context: ResolvedContextSettings = field(default_factory=ResolvedContextSettings)
    webhooks: ResolvedWebhooksConfig = field(default_factory=ResolvedWebhooksConfig)

    # Layer attachments (for custom layer configuration)
    layers: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_companion_config(cls, config: Any) -> CompanionRuntimeConfig:
        """Build a resolved config from a CompanionConfig or raw dict.

        Handles None, missing attributes, and provides sensible defaults.
        """
        if config is None:
            return cls()

        # Extract classifier config
        classifier_cfg = getattr(config, "classifier", None)
        # Handle model - could be string or enum, extract value if enum
        raw_model = (
            getattr(classifier_cfg, "model", "gemini-2.0-flash")
            if classifier_cfg
            else "gemini-2.0-flash"
        )
        model_str = (
            raw_model.value if hasattr(raw_model, "value") else str(raw_model or "gemini-2.0-flash")
        )
        classifier = ResolvedClassifierConfig(
            enabled=bool(getattr(classifier_cfg, "enabled", True)) if classifier_cfg else True,
            model=model_str,
            custom_model_id=getattr(classifier_cfg, "custom_model_id", None)
            if classifier_cfg
            else None,
            timeout_ms=int(getattr(classifier_cfg, "timeout_ms", 10000) or 10000)
            if classifier_cfg
            else 10000,
            history_limit=int(getattr(classifier_cfg, "history_limit", 20) or 20)
            if classifier_cfg
            else 20,
            instructions=getattr(classifier_cfg, "instructions", None) if classifier_cfg else None,
        )

        # Extract memory config
        mem_cfg = getattr(config, "memory", None)
        memory = ResolvedMemoryConfig(
            enabled=bool(getattr(mem_cfg, "enabled", False)) if mem_cfg else False,
            version=int(getattr(mem_cfg, "version", 1) or 1) if mem_cfg else 1,
            core_memories=list(getattr(mem_cfg, "core_memories", []) or []) if mem_cfg else [],
            evaluation_prompt=str(getattr(mem_cfg, "memory_evaluation_prompt", "") or "")
            if mem_cfg
            else "",
            recency=float(getattr(mem_cfg, "recency", 0.995) or 0.995) if mem_cfg else 0.995,
            top_k=int(getattr(mem_cfg, "top_k", 50) or 50) if mem_cfg else 50,
            min_saliency=float(getattr(mem_cfg, "min_saliency", 0.2) or 0.2) if mem_cfg else 0.2,
            # V2-specific settings
            max_entries=int(getattr(mem_cfg, "max_entries", 100) or 100) if mem_cfg else 100,
            ingestion_model=str(getattr(mem_cfg, "model", "gemini-2.0-flash") or "gemini-2.0-flash")
            if mem_cfg
            else "gemini-2.0-flash",
            ingestion_prompt=getattr(mem_cfg, "ingestion_prompt", None) if mem_cfg else None,
        )

        # Extract context settings
        ctx_cfg = getattr(config, "context", None)
        context = ResolvedContextSettings(
            max_prompt_tokens=getattr(ctx_cfg, "max_prompt_tokens", None) if ctx_cfg else None,
            target_prompt_fraction=float(getattr(ctx_cfg, "target_prompt_fraction", 0.4) or 0.4)
            if ctx_cfg
            else 0.4,
            reserved_completion_tokens=getattr(ctx_cfg, "reserved_completion_tokens", None)
            if ctx_cfg
            else None,
            message_limit=int(getattr(ctx_cfg, "message_limit", 200) or 200) if ctx_cfg else 200,
        )

        # Extract tools config
        tool_summary = getattr(ctx_cfg, "tool_summary", None) if ctx_cfg else None
        tools = ResolvedToolsConfig(
            enabled=bool(tool_summary),  # Enabled if tool_summary is set
            tool_summary=tool_summary,
        )

        # Extract layers config
        layers_cfg = list(getattr(config, "layers", []) or [])
        layers = []
        for layer in layers_cfg:
            if hasattr(layer, "model_dump"):
                layers.append(layer.model_dump())
            elif isinstance(layer, dict):
                layers.append(layer)

        # Check if specific layers are enabled via layers config
        def layer_enabled(category: str) -> bool:
            saw_match = False
            for entry in layers_cfg:
                try:
                    if _layer_entry_matches(
                        getattr(entry, "category", ""),
                        getattr(entry, "key", ""),
                        category,
                    ):
                        saw_match = True
                        if bool(getattr(entry, "enabled", True)):
                            return True
                except Exception:
                    continue
            if saw_match:
                return False
            return False

        # Extract knowledge config
        know_cfg = getattr(config, "knowledge", None)
        knowledge = ResolvedKnowledgeConfig(
            enabled=layer_enabled("knowledge_base"),
            gate_strategy=str(getattr(know_cfg, "gate_strategy", "keyword") or "keyword")
            if know_cfg
            else "keyword",
            top_k=int(getattr(know_cfg, "top_k", 5) or 5) if know_cfg else 5,
            min_confidence=float(getattr(know_cfg, "min_confidence", 0.0) or 0.0)
            if know_cfg
            else 0.0,
            classifier_summary=getattr(know_cfg, "classifier_summary", None) if know_cfg else None,
        )

        actions = ResolvedActionsConfig(
            enabled=layer_enabled("actions"),
        )

        # Extract webhooks (if stored in config - may be in companion.webhooks instead)
        webhooks_raw = getattr(config, "webhooks", {}) or {}
        webhooks = ResolvedWebhooksConfig(configs=dict(webhooks_raw))

        return cls(
            context_mode=str(getattr(config, "context_mode", "layered") or "layered").lower(),
            classifier=classifier,
            memory=memory,
            knowledge=knowledge,
            tools=tools,
            actions=actions,
            context=context,
            webhooks=webhooks,
            layers=layers,
        )

    def layer_params(self, category: str, key: str | None = None) -> Dict[str, Any]:
        """Get params for a specific layer by category and optional key."""
        fallback_params: Dict[str, Any] | None = None
        for layer in self.layers:
            layer_cat = layer.get("category", "")
            layer_key = layer.get("key", "")
            if not _layer_entry_matches(layer_cat, layer_key, category, key):
                continue
            params = layer.get("params", {})
            if bool(layer.get("enabled", True)):
                return params
            if fallback_params is None:
                fallback_params = params
        return fallback_params or {}

    def is_layer_enabled(self, category: str, key: str | None = None) -> bool:
        """Check if a layer is enabled by category and optional key."""
        saw_match = False
        for layer in self.layers:
            layer_cat = layer.get("category", "")
            layer_key = layer.get("key", "")
            if _layer_entry_matches(layer_cat, layer_key, category, key):
                saw_match = True
                if bool(layer.get("enabled", True)):
                    return True
        if saw_match:
            return False
        return False

    def is_layer_always_run(self, category: str, key: str | None = None) -> bool:
        """Check if a layer has always_run set.

        Layers with always_run=True will run regardless of classifier decision.
        """
        for layer in self.layers:
            layer_cat = layer.get("category", "")
            layer_key = layer.get("key", "")
            if _layer_entry_matches(layer_cat, layer_key, category, key):
                if bool(layer.get("always_run", False)):
                    return True
        return False

    def get_layer_info(self, category: str) -> Dict[str, Any]:
        """Get full layer info by category."""
        fallback: Dict[str, Any] | None = None
        for layer in self.layers:
            if _layer_entry_matches(layer.get("category", ""), layer.get("key", ""), category):
                if bool(layer.get("enabled", True)):
                    return layer
                if fallback is None:
                    fallback = layer
        if fallback is not None:
            return fallback
        return {}

    def should_include_behaviors(self, default_if_unconfigured: bool = False) -> bool:
        """Resolve whether behavior runtime should be included for a turn.

        If no explicit actions/behaviors layer attachment exists, uses a
        configurable fallback for backwards compatibility.
        """
        has_behavior_layer = any(
            _layer_entry_matches(layer.get("category", ""), layer.get("key", ""), "actions")
            for layer in self.layers
        )
        if not has_behavior_layer:
            return default_if_unconfigured
        return self.is_layer_enabled("actions")
