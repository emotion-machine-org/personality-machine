# server/app/routers/voice/fast_brain.py
"""Fast Brain processor for voice pipeline.

The Fast Brain (Gemini Flash) handles immediate voice responses and decides
when to delegate complex tasks to the Slow Brain (OpenClaw).

Architecture:
    User speaks → STT → Fast Brain
                          ↓
                    Chit-chat? → Respond immediately
                          ↓
                    Task? → Delegate to OpenClaw
                          ↓
                    "Got it, checking..."
                          ↓
                    Wait for OpenClaw result → Speak it
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

import httpx

from .hot_context import HotContext, get_context

logger = logging.getLogger(__name__)

# Configuration
FAST_BRAIN_MODEL = os.environ.get("FAST_BRAIN_MODEL", "google/gemini-2.5-flash")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENCLAW_WEBHOOK_URL = os.environ.get(
    "OPENCLAW_WEBHOOK_URL", "http://127.0.0.1:18789/em-voice/webhook"
)
OPENCLAW_AUTH_TOKEN = os.environ.get("OPENCLAW_AUTH_TOKEN", "")


class Intent(str, Enum):
    """Classification of user intent."""

    CHIT_CHAT = "chit_chat"  # Simple conversation, fast brain handles
    TASK = "task"  # Complex task, delegate to OpenClaw
    QUESTION = "question"  # Question that might need tools/search
    COMMAND = "command"  # Direct command to execute


@dataclass
class FastBrainResponse:
    """Response from the Fast Brain."""

    intent: Intent
    immediate_response: str | None  # Response to speak immediately
    task_id: str | None  # If delegating, the task ID
    task_description: str | None  # What we're asking OpenClaw to do


# Classification prompt for Fast Brain
CLASSIFICATION_PROMPT = """You are the Fast Brain - a quick-response voice assistant.
Your job is to classify user messages and either respond immediately OR delegate to the Slow Brain.

RESPOND IMMEDIATELY (chit_chat) for:
- Greetings: "hi", "hello", "hey"
- Simple acknowledgments: "ok", "thanks", "sure"
- Small talk: "how are you", "what's up"
- Simple questions you can answer from context

DELEGATE TO SLOW BRAIN (task) for:
- Requests needing tools: "check my calendar", "send an email", "search for..."
- Complex questions: "what happened in the meeting yesterday"
- Multi-step tasks: "book a flight", "schedule a meeting"
- Anything requiring memory, files, or external actions

Respond with JSON:
{
  "intent": "chit_chat" or "task",
  "immediate_response": "Your response if chit_chat, or acknowledgment if task",
  "task_description": "Description of task for Slow Brain (only if task)"
}

Be concise and natural - this is voice, not text.
"""


async def classify_and_respond(
    user_message: str,
    conversation_history: list[dict] | None = None,
    personality_prompt: str | None = None,
) -> FastBrainResponse:
    """Classify user intent and generate appropriate response.

    Args:
        user_message: The transcribed user message
        conversation_history: Recent conversation for context
        personality_prompt: System prompt with personality (e.g., Can's SOUL)

    Returns:
        FastBrainResponse with intent and response/task info
    """
    if not OPENROUTER_API_KEY:
        logger.warning("[FAST_BRAIN] No OPENROUTER_API_KEY, defaulting to chit_chat")
        return FastBrainResponse(
            intent=Intent.CHIT_CHAT,
            immediate_response="I'm here to help! What would you like?",
            task_id=None,
            task_description=None,
        )

    # Build messages for classification
    system_prompt = CLASSIFICATION_PROMPT
    if personality_prompt:
        system_prompt = f"{personality_prompt}\n\n{CLASSIFICATION_PROMPT}"

    messages = [{"role": "system", "content": system_prompt}]

    # Add recent history for context (last 5 messages)
    if conversation_history:
        for msg in conversation_history[-5:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": FAST_BRAIN_MODEL,
                    "messages": messages,
                    "max_tokens": 200,
                    "temperature": 0.7,
                },
            )
            response.raise_for_status()
            result = response.json()

            content = result["choices"][0]["message"]["content"]

            # Parse JSON response
            # Handle markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())

            intent = Intent(data.get("intent", "chit_chat"))
            task_id = str(uuid4()) if intent == Intent.TASK else None

            return FastBrainResponse(
                intent=intent,
                immediate_response=data.get("immediate_response"),
                task_id=task_id,
                task_description=data.get("task_description"),
            )

    except Exception as e:
        logger.exception(f"[FAST_BRAIN] Classification error: {e}")
        # Fallback to simple response
        return FastBrainResponse(
            intent=Intent.CHIT_CHAT,
            immediate_response="I'm here! How can I help you?",
            task_id=None,
            task_description=None,
        )


async def delegate_to_openclaw(
    task_id: str,
    user_message: str,
    task_description: str,
    context: dict[str, Any] | None = None,
    hot_context: HotContext | None = None,
) -> bool:
    """Delegate a task to OpenClaw (Slow Brain).

    Args:
        task_id: Unique task identifier
        user_message: Original user message
        task_description: What we want OpenClaw to do
        context: Additional context (companion_id, user_id, etc.)
        hot_context: HotContext instance for logging

    Returns:
        True if delegation was successful, False otherwise
    """
    if hot_context:
        hot_context.log_start(task_id, user_message)

    webhook_url = OPENCLAW_WEBHOOK_URL
    if not webhook_url:
        logger.error("[FAST_BRAIN] No OPENCLAW_WEBHOOK_URL configured")
        if hot_context:
            hot_context.log_fail(task_id, "OpenClaw webhook not configured")
        return False

    payload = {
        "task_id": task_id,
        "message": user_message,
        "task_description": task_description,
        "context": context or {},
    }

    headers = {"Content-Type": "application/json"}
    if OPENCLAW_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_AUTH_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers=headers,
            )

            if response.status_code in (200, 202):
                logger.info(f"[FAST_BRAIN] Delegated task {task_id} to OpenClaw")
                if hot_context:
                    hot_context.log_ack(task_id, "OpenClaw accepted task")
                return True
            else:
                logger.error(f"[FAST_BRAIN] OpenClaw returned {response.status_code}")
                if hot_context:
                    hot_context.log_fail(task_id, f"OpenClaw returned {response.status_code}")
                return False

    except Exception as e:
        logger.exception(f"[FAST_BRAIN] Failed to delegate to OpenClaw: {e}")
        if hot_context:
            hot_context.log_fail(task_id, str(e))
        return False


async def wait_for_openclaw_result(
    task_id: str,
    hot_context: HotContext,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.5,
) -> str | None:
    """Wait for OpenClaw to complete a task.

    Polls hot_context for the task result.

    Args:
        task_id: Task to wait for
        hot_context: HotContext instance
        timeout_seconds: Maximum time to wait
        poll_interval: How often to check

    Returns:
        Result string if completed, None if timeout/failed
    """
    elapsed = 0.0

    while elapsed < timeout_seconds:
        status = hot_context.get_task_status(task_id)

        if status:
            if status.status == "done" and status.result:
                logger.info(f"[FAST_BRAIN] Task {task_id} completed")
                return status.result
            elif status.status == "failed":
                logger.warning(f"[FAST_BRAIN] Task {task_id} failed: {status.error}")
                return None

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning(f"[FAST_BRAIN] Task {task_id} timed out after {timeout_seconds}s")
    return None


async def process_voice_input(
    user_message: str,
    conversation_history: list[dict] | None = None,
    personality_prompt: str | None = None,
    context: dict[str, Any] | None = None,
    on_immediate_response: Callable[[str], None] | None = None,
    hot_context_path: str = "hot_context.md",
) -> str:
    """Main entry point for Fast Brain voice processing.

    Args:
        user_message: Transcribed user message
        conversation_history: Recent conversation
        personality_prompt: Personality/SOUL prompt
        context: Additional context
        on_immediate_response: Callback for immediate responses (for TTS)
        hot_context_path: Path to hot_context file

    Returns:
        Final response to speak
    """
    hot_context = get_context(hot_context_path)

    # Step 1: Classify intent
    response = await classify_and_respond(
        user_message=user_message,
        conversation_history=conversation_history,
        personality_prompt=personality_prompt,
    )

    logger.info(f"[FAST_BRAIN] Intent: {response.intent}, Task ID: {response.task_id}")

    # Step 2: Handle based on intent
    if response.intent == Intent.CHIT_CHAT:
        # Simple response - return immediately
        return response.immediate_response or "I'm here!"

    elif response.intent == Intent.TASK:
        # Speak acknowledgment immediately
        if on_immediate_response and response.immediate_response:
            on_immediate_response(response.immediate_response)

        # Delegate to OpenClaw
        success = await delegate_to_openclaw(
            task_id=response.task_id,
            user_message=user_message,
            task_description=response.task_description or user_message,
            context=context,
            hot_context=hot_context,
        )

        if not success:
            return "I'm sorry, I couldn't process that request right now."

        # Wait for result
        result = await wait_for_openclaw_result(
            task_id=response.task_id,
            hot_context=hot_context,
            timeout_seconds=30.0,
        )

        if result:
            return result
        else:
            return "I'm still working on that. I'll let you know when it's ready."

    # Default fallback
    return response.immediate_response or "I'm here to help!"
