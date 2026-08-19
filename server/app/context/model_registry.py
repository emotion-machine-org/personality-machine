"""Model metadata registry used for token budgeting placeholders.

This keeps a minimal set of context window assumptions so Phase 0/0.5 can wire
up budgets without changing runtime behavior. Values can be refined later.
"""

from __future__ import annotations

from typing import Dict

DEFAULT_CONTEXT_WINDOW = 128_000

MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    # Anthropic
    "claude-sonnet-4": DEFAULT_CONTEXT_WINDOW,
    "claude-sonnet-4.5": 200_000,
    # OpenAI
    "gpt-4o": DEFAULT_CONTEXT_WINDOW,
    "gpt-4o-mini": DEFAULT_CONTEXT_WINDOW,
    # Google Gemini (via OpenRouter: google/gemini-*)
    "gemini-2.5-flash": 1_000_000,
    "gemini-3-flash": 1_000_000,
    # Moonshot Kimi (via OpenRouter: moonshotai/kimi-*)
    "kimi-k2": 262_144,
    "kimi-k2.5": 262_144,
}


def get_context_window(model: str | None) -> int:
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    return MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


__all__ = ["DEFAULT_CONTEXT_WINDOW", "MODEL_CONTEXT_WINDOWS", "get_context_window"]
