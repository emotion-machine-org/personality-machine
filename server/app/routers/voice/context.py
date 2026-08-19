# server/app/routers/voice/context.py
"""Voice context injection for real-time conversations.

This module handles injecting context (memory, behaviors, profile) into the
voice pipeline between user speech (STT) and LLM response generation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List
from uuid import UUID

import asyncpg
from pipecat.frames.frames import TranscriptionMessage
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.transcript_processor import TranscriptProcessor

from ...context import build_context_plan, execute_post_turn_effects
from ...db import get_db_connection

logger = logging.getLogger(__name__)


@dataclass
class VoiceContextConfig:
    """Configuration for voice context injection.

    Voice defaults to a simpler context setup than text:
    - No classifier (saves ~100-200ms latency per turn)
    - Memory only (no knowledge, behaviors, or tools)

    This can be overridden per-session if needed.
    """

    companion_id: UUID
    companion_config: Any
    relationship_id: UUID | None = None
    conversation_id: UUID | None = None
    external_user_id: str | None = None
    use_layered: bool = True
    # Voice-optimized defaults: classifier off, memory only
    use_classifier: bool = False  # Skip LLM classifier for lower latency
    include_memory: bool = True
    include_knowledge: bool = False  # Off by default for voice
    include_behaviors: bool = False  # Off by default for voice
    include_tools: bool = False  # Off by default for voice
    include_profile: bool = False
    profile_data: Dict[str, Any] | None = None


@dataclass
class VoiceSessionState:
    """Mutable state for a voice session."""

    context_events: List[Any] = field(default_factory=list)
    turn_count: int = 0
    last_user_message: str | None = None
    last_assistant_message: str | None = None


class VoiceContextInjector:
    """Injects context into voice pipeline after user speech.

    This class handles the integration between the voice pipeline and the
    context engine, ensuring that memory, behaviors, and profile data are
    available to the LLM for each response.
    """

    def __init__(
        self,
        config: VoiceContextConfig,
        llm_context: OpenAILLMContext,
        on_message_persisted: Callable[[str, str, UUID], None] | None = None,
    ):
        """Initialize the context injector.

        Args:
            config: Voice context configuration
            llm_context: Pipecat LLM context to inject into
            on_message_persisted: Optional callback when message is persisted
        """
        self.config = config
        self.llm_context = llm_context
        self.on_message_persisted = on_message_persisted
        self.state = VoiceSessionState()

    async def on_user_transcription(self, content: str) -> bool:
        """Handle user transcription and inject context.

        Called after STT transcribes user speech. Builds context plan
        and injects relevant context into the LLM.

        Args:
            content: Transcribed user speech

        Returns:
            True if context was injected, False otherwise
        """
        if not content.strip():
            return False

        self.state.last_user_message = content
        self.state.turn_count += 1
        context_injected = False

        if not self.config.use_layered:
            # Legacy mode - no context injection needed
            return False

        try:
            async with get_db_connection() as conn:
                context_plan = await build_context_plan(
                    conn=conn,
                    companion_id=self.config.companion_id,
                    companion_config=self.config.companion_config,
                    conversation_id=self.config.conversation_id,
                    user_message=content,
                    external_user_id=self.config.external_user_id,
                    relationship_id=self.config.relationship_id,
                    turn_count_override=self.state.turn_count,
                    use_classifier=self.config.use_classifier,  # Voice default: False
                    include_memory=self.config.include_memory,
                    include_knowledge=self.config.include_knowledge,
                    include_behaviors=self.config.include_behaviors,
                    include_tools=self.config.include_tools,  # Voice default: False
                    include_profile_in_prompt=self.config.include_profile,
                    profile_override=self.config.profile_data,
                    append_core_prompt=False,  # Already in system prompt
                    append_history=False,  # Already in LLM context
                )

                # Inject system messages into LLM context
                for msg in context_plan.messages:
                    if msg.get("role") == "system":
                        block = msg.get("content")
                        if block:
                            try:
                                if hasattr(self.llm_context, "add_system_message"):
                                    self.llm_context.add_system_message(block)
                                elif hasattr(self.llm_context, "add_message"):
                                    self.llm_context.add_message(
                                        {"role": "system", "content": block}
                                    )
                                context_injected = True
                            except Exception as e:
                                logger.warning(f"[VOICE_CTX] Failed to inject context: {e}")

                # Store events
                if context_plan.events:
                    self.state.context_events.extend(context_plan.events)

                # Execute post-turn effects asynchronously
                if context_plan.effects:
                    asyncio.create_task(self._execute_effects(conn, context_plan.effects, content))

                logger.debug(
                    f"[VOICE_CTX] Injected context for turn {self.state.turn_count}: "
                    f"{len(context_plan.messages)} messages, {len(context_plan.effects)} effects"
                )

        except Exception as e:
            logger.warning(f"[VOICE_CTX] Context injection failed: {e}")

        return context_injected

    async def _execute_effects(
        self,
        conn: asyncpg.Connection,
        effects: List[Any],
        user_message: str,
    ) -> None:
        """Execute post-turn effects asynchronously."""
        try:
            from ...context.schemas import TurnContext

            turn_ctx = TurnContext(
                message=user_message,
                companion_id=self.config.companion_id,
                conversation_id=self.config.conversation_id,
                external_user_id=self.config.external_user_id,
                relationship_id=self.config.relationship_id,
                turn_count=self.state.turn_count,
            )
            await execute_post_turn_effects(conn, turn_ctx, effects)
        except Exception as e:
            logger.warning(f"[VOICE_CTX] Failed to execute post-turn effects: {e}")

    def on_assistant_response(self, content: str) -> None:
        """Track assistant response for context."""
        self.state.last_assistant_message = content


def setup_transcript_handler(
    transcript: TranscriptProcessor,
    context_injector: VoiceContextInjector,
    task: Any,
    context_aggregator: Any,
    on_message: Callable[[str, str], None] | None = None,
) -> None:
    """Set up transcript event handler with context injection.

    Args:
        transcript: Pipecat transcript processor
        context_injector: Context injector instance
        task: Pipecat pipeline task for queueing frames
        context_aggregator: LLM context aggregator
        on_message: Optional callback for each message (role, content)
    """

    @transcript.event_handler("on_transcript_update")
    async def on_transcript_update(processor, frame):
        for msg in frame.messages:
            if isinstance(msg, TranscriptionMessage):
                # Log transcript
                timestamp = f"[{msg.timestamp}] " if msg.timestamp else ""
                logger.info(f"[TRANSCRIPT] {timestamp}{msg.role}: {msg.content}")

                # Notify callback
                if on_message and msg.content.strip():
                    on_message(msg.role, msg.content)

                # Handle user transcription - inject context for NEXT turn
                # Note: We don't queue a new context frame here because the LLM
                # has already started generating. The injected context (memory, etc.)
                # will be used for subsequent turns.
                if msg.role == "user" and msg.content.strip():
                    await context_injector.on_user_transcription(msg.content)

                # Track assistant response
                elif msg.role == "assistant" and msg.content.strip():
                    context_injector.on_assistant_response(msg.content)
