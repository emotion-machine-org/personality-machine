# server/app/routers/voice/filler.py
"""Filler phrase generation for voice mode tool execution.

When tools are being executed in voice mode, there's a noticeable delay (2-5+ seconds).
This module generates context-aware filler phrases like "Let me check that for you..."
to fill the gap and provide a better user experience.
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Dict, List

import httpx

logger = logging.getLogger(__name__)

# Default filler phrases used when LLM generation fails or times out
DEFAULT_FILLER_PHRASES = [
    "One moment please.",
    "Alright, checking.",
    "Just a second.",
    "Give me just a second.",
]

FILLER_SYSTEM_PROMPT = """You are generating a brief filler phrase for a voice assistant while it executes a tool call.

Generate a single short phrase (5-15 words) that:
- Acknowledges the user's request naturally
- Hints at what action is being taken based on the available tools
- Uses a warm, conversational tone

Examples:
- "Let me check that for you."
- "Looking that up now."
- "One moment while I check the calendar."
- "Let me see what I can find."

Respond with ONLY the filler phrase, no quotes or extra text."""


async def generate_filler(
    history: List[Dict[str, str]],
    tool_summaries: List[Dict[str, str]],
    timeout_ms: int = 600,
    history_limit: int = 5,
) -> str:
    """Generate a context-aware filler phrase using a fast LLM.

    Args:
        history: Recent conversation history (list of {role, content} dicts)
        tool_summaries: List of tool summaries with 'spec_name' and 'summary' keys
        timeout_ms: Maximum time to wait for LLM response (default 600ms)
        history_limit: Maximum number of recent messages to include (default 5)

    Returns:
        A filler phrase to speak while tools execute
    """
    t0 = time.perf_counter()

    try:
        # Build tool context for the LLM
        tool_context = ""
        if tool_summaries:
            tool_lines = []
            for ts in tool_summaries[:3]:  # Limit to first 3 tools
                name = ts.get("spec_name") or "Unknown"
                summary = ts.get("summary") or ""
                if summary:
                    tool_lines.append(f"- {name}: {summary}")
            if tool_lines:
                tool_context = "Available tools:\n" + "\n".join(tool_lines)

        # Build conversation context from recent history
        conversation_lines = []
        if history:
            for msg in history[-history_limit:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:150]  # Truncate long messages
                conversation_lines.append(f"{role}: {content}")

        user_prompt = f"""Tool capabilities: {tool_context if tool_context else "General tools"}

Recent conversation:
{chr(10).join(conversation_lines) if conversation_lines else "(no history)"}

Generate a brief filler phrase:"""

        filler = await _call_openrouter_filler(user_prompt, timeout_ms)

        duration_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[FILLER] Generated filler in {duration_ms:.0f}ms: {filler}")

        return filler or _get_random_fallback()

    except TimeoutError:
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.warning(f"[FILLER] LLM timed out after {duration_ms:.0f}ms, using fallback")
        return _get_random_fallback()

    except Exception as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.warning(f"[FILLER] Error generating filler after {duration_ms:.0f}ms: {e}")
        return _get_random_fallback()


async def _call_openrouter_filler(
    user_prompt: str,
    timeout_ms: int,
) -> str | None:
    """Call OpenRouter for filler generation using a fast model."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("[FILLER] OPENROUTER_API_KEY not set")
        return None

    # Use a fast, cheap model for filler generation
    model = "openai/gpt-oss-20b:nitro"

    messages = [
        {"role": "system", "content": FILLER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

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
                    "max_tokens": 100,
                    "reasoning": {
                        "effort": "minimal",  # Mandatory reasoning on Groq
                    },
                },
            )
            response.raise_for_status()
            data = response.json()

        result_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _clean_filler_response(result_text)

    except httpx.TimeoutException as e:
        raise TimeoutError(f"OpenRouter request timed out after {timeout_ms}ms") from e
    except Exception as e:
        logger.warning(f"[FILLER] OpenRouter error: {e}")
        return None


def _clean_filler_response(text: str) -> str | None:
    """Clean up the LLM response to extract just the filler phrase."""
    if not text:
        return None

    text = text.strip()

    # Remove quotes if wrapped
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        text = text[1:-1]

    return text


def _get_random_fallback() -> str:
    """Get a random fallback filler phrase."""
    return random.choice(DEFAULT_FILLER_PHRASES)
