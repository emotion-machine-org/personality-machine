"""Memory V2 Ingestion worker (Modal App)

Analyzes conversation turns and updates the scratchpad.

Deploy with:
  modal deploy server/app/modals/workers/memory_v2_ingest.py
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from modal import App, Cls, Image, Secret

app = App(name="em-memory-v2")

image = Image.debian_slim().pip_install(
    "httpx",
)

logger = logging.getLogger(__name__)


DEFAULT_MODEL = os.getenv("MEMORY_V2_MODEL", "google/gemini-2.0-flash-001")

# The customizable part of the ingestion prompt (shown to users in dashboard)
# Users can modify this to change what/how the AI remembers
DEFAULT_INGESTION_GUIDELINES = """You are a memory manager for an AI companion. Your job is to maintain a scratchpad of important facts about the user.

Given a conversation turn, decide what memories to ADD, UPDATE, or DELETE.

## Guidelines

**ADD** new memories when you learn:
- Identity facts (name, age, location, profession)
- Preferences and communication style
- Goals, plans, and aspirations
- Significant life events or milestones
- Important relationships (family, friends, colleagues)
- Constraints or boundaries

**UPDATE** existing memories when:
- Information needs correction
- More detail or context is available
- A fact has changed

**DELETE** memories when:
- Information is explicitly corrected/outdated
- The user asks to forget something
- A memory is redundant with another

## Important
- Be selective - only store genuinely useful information
- Avoid storing: transient details, generic statements, conversation filler
- Prefer updating over adding duplicate information
- Use concise, factual language for memory content
- Write memories from the companion's first-person perspective (use "me" not "the AI companion")"""

# The boilerplate suffix (always appended by system, hidden from users)
# Contains placeholders and response format - users should not modify this
INGESTION_PROMPT_SUFFIX = """

## Current Memories
{current_memories}

## Conversation Turn
User: {user_message}
Assistant: {assistant_response}

## Response Format
Respond with valid JSON only:
{{
  "operations": [
    {{"action": "add", "content": "User's name is Sarah", "type": "identity"}},
    {{"action": "add", "content": "User likes me", "type": "preference"}},
    {{"action": "update", "id": "mem_xxx", "content": "Updated content"}},
    {{"action": "delete", "id": "mem_yyy"}}
  ],
  "reasoning": "Brief explanation"
}}

If no changes needed: {{"operations": [], "reasoning": "No memorable information"}}"""

# Full default prompt (for backward compatibility)
DEFAULT_INGESTION_PROMPT = DEFAULT_INGESTION_GUIDELINES + INGESTION_PROMPT_SUFFIX


def _sanitize_custom_guidelines(guidelines: str) -> str:
    """Escape curly braces in custom guidelines to prevent format string errors.

    Users might include literal {something} in their guidelines which would break
    the .format() call. This escapes them to {{ and }} so they pass through safely.
    """
    # Replace { with {{ and } with }} to escape them for .format()
    return guidelines.replace("{", "{{").replace("}", "}}")


def _format_entries_for_llm(entries: List[Dict[str, Any]]) -> str:
    """Format entries for LLM analysis (includes IDs)."""
    if not entries:
        return "(No memories stored yet)"

    lines = []
    for entry in entries:
        entry_id = entry.get("id", "unknown")
        content = entry.get("content", "")
        entry_type = entry.get("type", "other")
        lines.append(f"[{entry_id}] ({entry_type}) {content}")

    return "\n".join(lines)


async def _call_llm(
    model: str,
    prompt: str,
) -> str:
    """Call LLM for memory analysis via OpenRouter."""
    import httpx

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    # Map shorthand model names to OpenRouter model IDs
    model_map = {
        "gpt-4o-mini": "openai/gpt-4o-mini",
        "gpt-4o": "openai/gpt-4o",
        "gemini-2.0-flash": "google/gemini-2.0-flash-001",
        "gemini-flash": "google/gemini-2.0-flash-001",
        "claude-3-haiku": "anthropic/claude-3-haiku",
        "claude-3-sonnet": "anthropic/claude-3-sonnet",
    }
    openrouter_model = model_map.get(model, model)

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": openrouter_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 1000,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


@app.function(
    image=image,
    secrets=[Secret.from_name("em-service-secrets")],
    timeout=60 * 5,
)
async def ingest_memory_v2(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a turn and update the scratchpad.

    Payload: {
        relationship_id: str,
        user_message: str,
        assistant_response: str,
    }

    The worker fetches current entries and config via DbGateway.

    Returns: {
        operations_applied: int,
        added: int,
        updated: int,
        deleted: int,
        warning: str | None,
        errors: [...]
    }
    """
    relationship_id = payload["relationship_id"]
    user_message = payload["user_message"]
    assistant_response = payload["assistant_response"]

    # Fetch current entries and config via DbGateway
    db_app = os.getenv("MODAL_DB_APP", "em-db")
    Gw = Cls.from_name(db_app, "DbGateway")
    gw_instance = Gw()

    # Fetch in parallel
    entries_future = gw_instance.get_memory_v2_entries.remote.aio(relationship_id)
    config_future = gw_instance.get_memory_v2_config.remote.aio(relationship_id)

    current_entries = await entries_future
    config = await config_future

    # Use defaults if config values are None
    model = config.get("model") or DEFAULT_MODEL
    max_entries = config.get("max_entries") or 100

    # Build the full ingestion prompt
    # If user provided custom guidelines, append the boilerplate suffix
    # If no custom prompt, use the full default (guidelines + suffix)
    custom_guidelines = config.get("ingestion_prompt")
    if custom_guidelines:
        # User provided custom guidelines - sanitize and append the system suffix
        sanitized = _sanitize_custom_guidelines(custom_guidelines)
        ingestion_prompt = sanitized + INGESTION_PROMPT_SUFFIX
    else:
        # Use full default prompt
        ingestion_prompt = DEFAULT_INGESTION_PROMPT

    # Format prompt with conversation data
    formatted_memories = _format_entries_for_llm(current_entries)
    prompt = ingestion_prompt.format(
        current_memories=formatted_memories,
        user_message=user_message,
        assistant_response=assistant_response,
    )

    # Call LLM
    try:
        response_text = await _call_llm(model, prompt)

        # Parse JSON response
        # Handle markdown code blocks if present
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        result = json.loads(response_text.strip())
        operations = result.get("operations", [])

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response: {e}")
        return {
            "operations_applied": 0,
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "errors": [f"JSON parse error: {e!s}"],
        }
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return {
            "operations_applied": 0,
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "errors": [f"LLM error: {e!s}"],
        }

    if not operations:
        return {
            "operations_applied": 0,
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "errors": [],
        }

    # Apply operations via DbGateway (gw_instance already defined above)
    result = await gw_instance.apply_memory_v2_operations.remote.aio(
        relationship_id=relationship_id,
        operations=operations,
    )

    # Check for soft limit warning
    warning = None
    current_count = len(current_entries) + result.get("added", 0) - result.get("deleted", 0)
    if current_count >= max_entries:
        warning = f"Memory limit reached: {current_count}/{max_entries} entries"

    return {
        "operations_applied": result.get("added", 0)
        + result.get("updated", 0)
        + result.get("deleted", 0),
        "added": result.get("added", 0),
        "updated": result.get("updated", 0),
        "deleted": result.get("deleted", 0),
        "warning": warning,
        "errors": result.get("errors", []),
    }


# ── Relationship Summarization ─────────────────────────────────────────────────

SUMMARIZATION_PROMPT = """You are summarizing a conversation between a user and an AI companion.

## Previous Summary (if any)
{previous_summary}

## New Messages to Incorporate
{new_messages}

## Instructions
Create a comprehensive summary that:
1. Incorporates all important information from the previous summary
2. Adds key points from the new messages
3. Captures: topics discussed, decisions made, user preferences revealed,
   ongoing threads/goals, emotional context, unresolved questions
4. Is written from a neutral third-person perspective
5. Is concise but complete (aim for 200-400 words)

Write the summary as flowing paragraphs, not bullet points.
Focus on information the AI companion would need to continue the relationship naturally.
"""


@app.function(
    image=image,
    secrets=[Secret.from_name("em-service-secrets")],
    timeout=60 * 5,
)
async def summarize_relationship(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize messages for a relationship.

    Payload: {
        relationship_id: str,
        previous_summary: str | None,
        messages: List[{role: str, content: str, seq: int}],
        messages_start: int,
        messages_end: int,
        version: int,
    }

    Returns: {
        success: bool,
        summary: str | None,
        version: int | None,
        error: str | None,
    }
    """
    relationship_id = payload["relationship_id"]
    previous_summary = payload.get("previous_summary") or "No previous summary."
    messages = payload["messages"]
    version = payload["version"]
    messages_start = payload["messages_start"]
    messages_end = payload["messages_end"]

    # Format messages for prompt
    formatted_messages = []
    for msg in messages:
        role = (msg.get("role") or "").capitalize()
        content = msg.get("content") or ""
        formatted_messages.append(f"{role}: {content}")
    messages_text = "\n\n".join(formatted_messages)

    # Build prompt
    prompt = SUMMARIZATION_PROMPT.format(
        previous_summary=previous_summary,
        new_messages=messages_text,
    )

    # Call LLM
    model = os.getenv("SUMMARIZATION_MODEL", "google/gemini-2.0-flash-001")
    try:
        summary = await _call_llm(model, prompt)

        # Store via DbGateway
        db_app = os.getenv("MODAL_DB_APP", "em-db")
        Gw = Cls.from_name(db_app, "DbGateway")
        gw = Gw()

        await gw.create_relationship_summary.remote.aio(
            relationship_id=relationship_id,
            content=summary,
            version=version,
            messages_start=messages_start,
            messages_end=messages_end,
            message_count=messages_end,  # Cumulative
            model=model,
        )

        logger.info(
            f"Summarization completed for relationship={relationship_id}, "
            f"version={version}, messages={messages_start}-{messages_end}"
        )

        return {
            "success": True,
            "summary": summary,
            "version": version,
        }

    except Exception as e:
        logger.error(f"Summarization failed for relationship={relationship_id}: {e}")
        return {
            "success": False,
            "summary": None,
            "version": None,
            "error": str(e),
        }
