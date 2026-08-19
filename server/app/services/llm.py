"""Shared helpers for calling text LLM providers."""

from __future__ import annotations

import time
from typing import List

from ..constants import DEFAULT_TEXT_LLM_MAX_TOKENS, MAX_TEXT_LLM_OUTPUT_TOKENS
from .llm_resolver import resolve_llm_client

MODEL_MAX_COMPLETION_TOKENS: dict[str, int] = {
    "gpt-5.1": 10_000,
    "claude-sonnet-4": 10_000,
    "claude-sonnet-4.5": 10_000,
}
# Default ceiling for models without explicit entry (keeps 4o/4o-mini to 4096 to avoid provider 400s)
DEFAULT_MODEL_COMPLETION_CAP = 4_096


def resolve_max_tokens(model: str, requested: int | None) -> int:
    """Clamp requested tokens to model-specific ceilings and practical defaults."""
    base = requested if requested is not None else DEFAULT_TEXT_LLM_MAX_TOKENS
    cap = DEFAULT_MODEL_COMPLETION_CAP
    for prefix, limit in MODEL_MAX_COMPLETION_TOKENS.items():
        if str(model).startswith(prefix):
            cap = limit
            break
    # Also respect absolute guardrail to avoid pathological inputs
    cap = min(cap, MAX_TEXT_LLM_OUTPUT_TOKENS)
    return max(1, min(base, cap))


async def generate_llm_response_direct(
    provider_string: str,
    messages: List[dict],
    temperature: float = 0.7,
    *,
    max_tokens: int | None = None,
    timings: dict | None = None,
) -> str:
    """Generate an LLM response using the configured provider string."""

    t0 = time.perf_counter()
    api_messages = []
    for msg in messages:
        if msg.get("role") in {"system", "user", "assistant"}:
            api_messages.append(
                {
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                }
            )
    if timings is not None:
        timings["llm_convert_ms"] = (time.perf_counter() - t0) * 1000

    if not api_messages:
        return "I didn't receive any context to respond to."

    t1 = time.perf_counter()
    client, model, _ = resolve_llm_client(provider_string)
    if timings is not None:
        timings["llm_client_select_ms"] = (time.perf_counter() - t1) * 1000

    max_toks = resolve_max_tokens(model, max_tokens)
    t2 = time.perf_counter()
    completion_kwargs = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
    }
    # GPT-5.1 uses max_completion_tokens instead of max_tokens
    if str(model).startswith("gpt-5.1"):
        completion_kwargs["max_completion_tokens"] = max_toks
    else:
        completion_kwargs["max_tokens"] = max_toks

    response = await client.chat.completions.create(**completion_kwargs)
    if timings is not None:
        timings["llm_api_ms"] = (time.perf_counter() - t2) * 1000

    t3 = time.perf_counter()
    out_text = ""
    if response.choices and response.choices[0].message:
        out_text = (response.choices[0].message.content or "").strip()
    else:
        out_text = "I apologize, but I couldn't generate a response."

    if timings is not None:
        timings["llm_extract_ms"] = (time.perf_counter() - t3) * 1000
        try:
            usage = getattr(response, "usage", None) or {}
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            timings["llm_usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", usage.get("prompt_tokens")),
                "completion_tokens": getattr(
                    usage, "completion_tokens", usage.get("completion_tokens")
                ),
                "total_tokens": getattr(usage, "total_tokens", usage.get("total_tokens")),
            }
        except Exception:
            pass

    return out_text or "I apologize, but I'm having trouble generating a response right now."


async def generate_llm_response_with_tools(
    provider_string: str,
    messages: List[dict],
    tools: List[dict],
    temperature: float = 0.7,
    *,
    max_tokens: int | None = None,
    timings: dict | None = None,
) -> dict:
    """Generate an LLM response with tool support.

    Args:
        provider_string: Provider string (e.g. "claude-sonnet-4")
        messages: List of message dicts with role and content
        tools: List of tool definitions in OpenAI format
        temperature: Sampling temperature
        max_tokens: Max tokens for response
        timings: Optional dict to populate with timing info

    Returns:
        Dict with:
        - content: Text response (may be None if tool call)
        - tool_calls: List of tool call dicts (if any)
        - finish_reason: Why generation stopped ("stop", "tool_calls", etc.)
    """
    t0 = time.perf_counter()
    api_messages = []
    for msg in messages:
        if msg.get("role") in {"system", "user", "assistant"}:
            api_messages.append(
                {
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                }
            )
    if timings is not None:
        timings["llm_convert_ms"] = (time.perf_counter() - t0) * 1000

    if not api_messages:
        return {
            "content": "I didn't receive any context to respond to.",
            "tool_calls": [],
            "finish_reason": "stop",
        }

    t1 = time.perf_counter()
    client, model, _ = resolve_llm_client(provider_string)
    if timings is not None:
        timings["llm_client_select_ms"] = (time.perf_counter() - t1) * 1000

    max_toks = resolve_max_tokens(model, max_tokens)
    t2 = time.perf_counter()
    completion_kwargs = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
        "tools": tools,
    }
    # GPT-5.1 uses max_completion_tokens instead of max_tokens
    if str(model).startswith("gpt-5.1"):
        completion_kwargs["max_completion_tokens"] = max_toks
    else:
        completion_kwargs["max_tokens"] = max_toks

    response = await client.chat.completions.create(**completion_kwargs)
    if timings is not None:
        timings["llm_api_ms"] = (time.perf_counter() - t2) * 1000

    t3 = time.perf_counter()
    result = {
        "content": None,
        "tool_calls": [],
        "finish_reason": "stop",
    }

    if response.choices and response.choices[0].message:
        message = response.choices[0].message
        result["content"] = (message.content or "").strip() if message.content else None
        result["finish_reason"] = response.choices[0].finish_reason or "stop"

        # Extract tool calls if present
        if message.tool_calls:
            import json

            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                result["tool_calls"].append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": args,
                    }
                )

    if timings is not None:
        timings["llm_extract_ms"] = (time.perf_counter() - t3) * 1000
        try:
            usage = getattr(response, "usage", None) or {}
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            timings["llm_usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", usage.get("prompt_tokens")),
                "completion_tokens": getattr(
                    usage, "completion_tokens", usage.get("completion_tokens")
                ),
                "total_tokens": getattr(usage, "total_tokens", usage.get("total_tokens")),
            }
        except Exception:
            pass

    return result


async def generate_llm_response_stream_with_tools(
    provider_string: str,
    messages: List[dict],
    tools: List[dict],
    temperature: float = 0.7,
    *,
    max_tokens: int | None = None,
):
    """Stream an LLM response with tool support.

    Yields chunks of text as they arrive from the LLM.
    Final yield includes tool calls if any.

    Args:
        provider_string: Provider string (e.g. "claude-sonnet-4")
        messages: List of message dicts with role and content
        tools: List of tool definitions in OpenAI format
        temperature: Sampling temperature
        max_tokens: Max tokens for response

    Yields:
        Dict with:
        - type: "delta" | "tool_call" | "done"
        - content: Text chunk (for delta)
        - tool_calls: List of tool call dicts (for done, if any)
        - finish_reason: Why generation stopped (for done)
    """
    api_messages = []
    for msg in messages:
        if msg.get("role") in {"system", "user", "assistant"}:
            api_messages.append(
                {
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                }
            )

    if not api_messages:
        yield {
            "type": "done",
            "content": "I didn't receive any context to respond to.",
            "tool_calls": [],
            "finish_reason": "stop",
        }
        return

    client, model, _ = resolve_llm_client(provider_string)
    max_toks = resolve_max_tokens(model, max_tokens)

    completion_kwargs = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        completion_kwargs["tools"] = tools

    # GPT-5.1 uses max_completion_tokens instead of max_tokens
    if str(model).startswith("gpt-5.1"):
        completion_kwargs["max_completion_tokens"] = max_toks
    else:
        completion_kwargs["max_tokens"] = max_toks

    full_content = ""
    tool_calls = []
    finish_reason = "stop"

    # Track tool call assembly (they come in chunks)
    tool_call_chunks = {}  # id -> {name, arguments}

    async for chunk in await client.chat.completions.create(**completion_kwargs):
        if not chunk.choices:
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        # Handle content chunks
        if delta and delta.content:
            full_content += delta.content
            yield {
                "type": "delta",
                "content": delta.content,
            }

        # Handle tool call chunks
        if delta and delta.tool_calls:
            for tc in delta.tool_calls:
                # Use index as primary key — it's consistent across all chunks.
                # id is only present on the first chunk and would cause a key mismatch.
                tc_key = (
                    str(tc.index) if tc.index is not None else str(tc.id or len(tool_call_chunks))
                )
                if tc_key not in tool_call_chunks:
                    tool_call_chunks[tc_key] = {
                        "id": None,
                        "name": None,
                        "arguments": "",
                    }
                if tc.id and not tool_call_chunks[tc_key]["id"]:
                    tool_call_chunks[tc_key]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_call_chunks[tc_key]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_call_chunks[tc_key]["arguments"] += tc.function.arguments

        # Track finish reason
        if choice.finish_reason:
            finish_reason = choice.finish_reason

    # Assemble final tool calls
    if tool_call_chunks:
        import json

        for tc_data in tool_call_chunks.values():
            try:
                args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {
                    "id": tc_data["id"],
                    "name": tc_data["name"],
                    "arguments": args,
                }
            )

    yield {
        "type": "done",
        "content": full_content.strip() if full_content else None,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
    }
