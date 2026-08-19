"""Common helpers for resolving LLM provider strings to clients and models."""

from __future__ import annotations

from typing import Any, Tuple

from .openai_clients import (
    get_openai_async_client,
    get_openrouter_async_client,
    get_vllm_async_client,
)

# Map provider strings to (client key, model name)
_PROVIDER_REGISTRY: dict[str, Tuple[str, str]] = {
    "fast-brain": ("openrouter", "google/gemini-2.5-flash"),
    "openai-gpt4o": ("openai", "gpt-4o"),
    "openai-gpt4o-mini": ("openai", "gpt-4o-mini"),
    "openai-gpt5.1": ("openai", "gpt-5.1"),
    "claude-haiku-4-5": ("openrouter", "anthropic/claude-haiku-4.5"),
    "claude-haiku-4.5": ("openrouter", "anthropic/claude-haiku-4.5"),
    "claude-sonnet-3.7": ("openrouter", "anthropic/claude-3.7-sonnet"),
    "claude-sonnet-3.5": ("openrouter", "anthropic/claude-3.7-sonnet"),
    "claude-sonnet-4": ("openrouter", "anthropic/claude-sonnet-4"),
    "claude-sonnet-4.5": ("openrouter", "anthropic/claude-sonnet-4.5"),
    "claude-sonnet-4.6": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "claude-opus-4": ("openrouter", "anthropic/claude-opus-4"),
    "claude-opus-4.5": ("openrouter", "anthropic/claude-opus-4.5"),
    "claude-opus-4.6": ("openrouter", "anthropic/claude-opus-4.6"),
    "gemini": ("openrouter", "google/gemini-2.5-flash"),
    "gemini-2.5-flash": ("openrouter", "google/gemini-2.5-flash"),
    "gemini-3-flash": ("openrouter", "google/gemini-3-flash-preview"),
    "gemini-3.1-flash-lite-preview": ("openrouter", "google/gemini-3.1-flash-lite-preview"),
    "moonshot-kimi-k2": ("openrouter", "moonshotai/kimi-k2-0905"),
    "moonshot-kimi-k2.5": ("openrouter", "moonshotai/kimi-k2.5"),
    "local-vllm-qwen": ("vllm", "Qwen/QwQ-32B-AWQ"),
}


def resolve_llm_client(
    provider: str | None,
    *,
    default_model: str = "gpt-4o-mini",
) -> tuple[Any, str, bool]:
    """Resolve provider string to an async client and model.

    Returns a tuple of (client, model, used_default). used_default is True when the
    provider was unknown or empty and the fallback OpenAI client/model pair was
    selected.
    """

    if not provider:
        client = get_openai_async_client()
        return client, default_model, True

    client_key, model = _PROVIDER_REGISTRY.get(provider, (None, default_model))

    if client_key == "openai":
        return get_openai_async_client(), model, False
    if client_key == "openrouter":
        return get_openrouter_async_client(), model, False
    if client_key == "vllm":
        return get_vllm_async_client(), model, False

    client = get_openai_async_client()
    return client, default_model, True
