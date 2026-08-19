# server/app/routers/voice/context_processor.py
"""Pipecat processor for injecting context before LLM.

This processor sits in the voice pipeline between the context aggregator and LLM,
allowing us to inject memory, behaviors, and profile data BEFORE the LLM generates.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List
from uuid import UUID

from pipecat.frames.frames import Frame, LLMFullResponseEndFrame, TextFrame
from pipecat.processors.aggregators.openai_llm_context import (
    OpenAILLMContext,
    OpenAILLMContextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ...context import build_context_plan, execute_post_turn_effects
from ...context.orchestrator import ClassifierOnlyResult, run_classifier_only
from ...context.schemas import ContextEvent, TurnContext
from ...db import get_db_connection
from .filler import generate_filler

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
    # Filler audio configuration (plays "Let me check that..." during tool execution)
    enable_filler: bool = True  # On by default when tools are enabled
    filler_timeout_ms: int = 300  # Max time to wait for filler LLM


@dataclass
class VoiceSessionState:
    """Mutable state for a voice session."""

    context_events: List[Any] = field(default_factory=list)
    turn_count: int = 0
    last_user_message: str | None = None
    last_assistant_message: str | None = None


class VoiceContextProcessor(FrameProcessor):
    """Pipecat processor that injects context before LLM processing.

    This processor intercepts OpenAILLMContextFrame (the frame that triggers
    the LLM to generate a response) and injects memory/context into the
    LLM context BEFORE passing it to the LLM.

    Pipeline position: context_aggregator.user() → VoiceContextProcessor → llm
    """

    def __init__(
        self,
        config: VoiceContextConfig,
        llm_context: OpenAILLMContext | None = None,
        on_context_injected: Callable[[int, int], None] | None = None,
        on_tool_result: Callable[[ContextEvent], None] | None = None,
        **kwargs,
    ):
        """Initialize the context processor.

        Args:
            config: Voice context configuration
            llm_context: Pipecat LLM context to inject into (can be set later via set_llm_context)
            on_context_injected: Optional callback(turn_count, num_messages) when context is injected
            on_tool_result: Optional callback for tool result events (for forwarding to WebSocket)
        """
        super().__init__(**kwargs)
        self.config = config
        self.llm_context = llm_context
        self.on_context_injected = on_context_injected
        self.on_tool_result = on_tool_result
        self.state = VoiceSessionState()

    def set_llm_context(self, llm_context: OpenAILLMContext) -> None:
        """Set the LLM context after pipeline creation."""
        self.llm_context = llm_context

    def _extract_last_user_message(self) -> str | None:
        """Extract the most recent user message from the LLM context.

        Since context_aggregator.user() processes TranscriptionFrame before
        this processor sees it, we get the user message from the LLM context
        that context_aggregator has already populated.
        """
        if not self.llm_context:
            return None

        messages = self.llm_context.get_messages()
        # Find the last user message (most recently added by context_aggregator)
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process frames and inject context before LLM.

        We intercept OpenAILLMContextFrame (which triggers the LLM) and inject
        memory/context BEFORE passing it to the LLM.

        Note: TranscriptionFrame goes through context_aggregator.user() first,
        which adds the user message to the LLM context. We extract it from there.
        """
        await super().process_frame(frame, direction)

        # When we see the context frame that triggers LLM, inject context first
        if isinstance(frame, OpenAILLMContextFrame):
            # Extract user message from LLM context (context_aggregator already added it)
            user_message = self._extract_last_user_message()

            logger.info(
                f"[VOICE_CTX_PROC] OpenAILLMContextFrame received, "
                f"use_layered={self.config.use_layered}, "
                f"user_msg={user_message[:50] if user_message else None}..."
            )

            if self.config.use_layered and user_message:
                await self._inject_context(user_message)

            await self.push_frame(frame, direction)
            return

        # Track assistant response end for effects
        if isinstance(frame, LLMFullResponseEndFrame):
            # Could trigger post-turn effects here if needed
            pass

        # Pass all other frames through unchanged
        await self.push_frame(frame, direction)

    async def _inject_context(self, user_message: str) -> None:
        """Build context plan and rebuild LLM context to match text mode.

        This uses a three-phase approach for voice filler support:
        1. Run classifier only (fast) to determine if tools are needed
        2. If tools needed, generate and play filler audio
        3. Execute full context plan (reuse classifier result)

        This rebuilds the full message list so voice matches text mode structure:
        [system prompt with memory/context] → [conversation history] → [current user message]
        """
        if not self.llm_context:
            logger.warning("[VOICE_CTX_PROC] LLM context not set, skipping injection")
            return

        self.state.turn_count += 1
        self.state.last_user_message = user_message

        try:
            # Get current messages from LLM context
            current_messages = self.llm_context.get_messages()

            # Separate into: system messages, history, current user message
            # Current user message is the last message (just added by context_aggregator)
            history_messages = []
            current_user_msg = None

            for msg in current_messages:
                role = msg.get("role")
                if role == "system":
                    # Skip old system messages - we'll replace with fresh ones
                    continue
                elif role in ("user", "assistant"):
                    history_messages.append(msg)

            # The last user message is the current turn's message
            if history_messages and history_messages[-1].get("role") == "user":
                current_user_msg = history_messages.pop()

            async with get_db_connection() as conn:
                classifier_result_override: ClassifierOnlyResult | None = None

                # =============================================================
                # Phase 1: Run classifier only if tools are enabled
                # =============================================================
                # Only run classifier-first if:
                # - Filler is enabled
                # - Classifier is enabled
                should_pre_classify = self.config.enable_filler and self.config.use_classifier

                if should_pre_classify:
                    logger.info("[VOICE_CTX_PROC] Phase 1: Running classifier only...")
                    classifier_result_override = await run_classifier_only(
                        conn=conn,
                        companion_id=self.config.companion_id,
                        companion_config=self.config.companion_config,
                        user_message=user_message,
                        external_user_id=self.config.external_user_id,
                        relationship_id=self.config.relationship_id,
                        conversation_id=self.config.conversation_id,
                        history_override=history_messages,
                        turn_count_override=self.state.turn_count,
                    )

                    # =============================================================
                    # Phase 2: Generate and play filler if tools are needed
                    # =============================================================
                    if (
                        classifier_result_override.success
                        and classifier_result_override.layer_decisions.get("tools", False)
                    ):
                        logger.info("[VOICE_CTX_PROC] Phase 2: Tools needed, generating filler...")
                        try:
                            # Build history for filler (include current user message)
                            filler_history = [
                                *history_messages,
                                {"role": "user", "content": user_message},
                            ]
                            filler_text = await generate_filler(
                                history=filler_history,
                                tool_summaries=classifier_result_override.tool_summaries,
                                timeout_ms=self.config.filler_timeout_ms,
                            )
                            # Push filler through TTS immediately
                            logger.info(f"[VOICE_CTX_PROC] Playing filler: {filler_text}")
                            await self.push_frame(
                                TextFrame(text=filler_text), FrameDirection.DOWNSTREAM
                            )
                        except Exception as e:
                            logger.warning(f"[VOICE_CTX_PROC] Filler generation failed: {e}")
                            # Continue without filler - don't block the pipeline

                # =============================================================
                # Phase 3: Build full context plan (reuse classifier result)
                # =============================================================
                logger.info("[VOICE_CTX_PROC] Phase 3: Building full context plan...")
                context_plan = await build_context_plan(
                    conn=conn,
                    companion_id=self.config.companion_id,
                    companion_config=self.config.companion_config,
                    conversation_id=self.config.conversation_id,
                    user_message=user_message,
                    external_user_id=self.config.external_user_id,
                    relationship_id=self.config.relationship_id,
                    turn_count_override=self.state.turn_count,
                    use_classifier=self.config.use_classifier,
                    include_memory=self.config.include_memory,
                    include_knowledge=self.config.include_knowledge,
                    include_behaviors=self.config.include_behaviors,
                    include_tools=self.config.include_tools,
                    include_profile_in_prompt=self.config.include_profile,
                    profile_override=self.config.profile_data,
                    append_core_prompt=True,  # Get full system prompt with memory
                    append_history=False,  # We'll use in-session history
                    history_override=history_messages,  # Pass in-session history for classifier
                    classifier_result_override=classifier_result_override,  # Reuse classifier result
                )

                # Rebuild message list to match text mode structure:
                # [system messages from plan] → [history] → [current user]
                new_messages = []

                # Add system messages from context plan
                for msg in context_plan.messages:
                    if msg.get("role") == "system":
                        new_messages.append(msg)

                # Add conversation history (preserves in-session messages)
                new_messages.extend(history_messages)

                # Add current user message
                if current_user_msg:
                    new_messages.append(current_user_msg)

                # Replace the entire message list
                self.llm_context.set_messages(new_messages)

                # Log events from context plan for debugging
                event_names = [e.name for e in context_plan.events] if context_plan.events else []
                logger.info(
                    f"[VOICE_CTX_PROC] Rebuilt context for turn {self.state.turn_count}: "
                    f"{len([m for m in new_messages if m.get('role') == 'system'])} system, "
                    f"{len(history_messages)} history, "
                    f"{'1 user' if current_user_msg else '0 user'}, "
                    f"events={event_names}"
                )

                # Store events for debugging
                if context_plan.events:
                    self.state.context_events.extend(context_plan.events)

                    # Forward tool result events to WebSocket (for onboarding companion creation)
                    if self.on_tool_result:
                        for event in context_plan.events:
                            if event.name == "tools:result":
                                try:
                                    self.on_tool_result(event)
                                except Exception as e:
                                    logger.warning(
                                        f"[VOICE_CTX_PROC] Failed to forward tool result: {e}"
                                    )

                # Execute post-turn effects asynchronously (don't block pipeline)
                if context_plan.effects:
                    asyncio.create_task(
                        self._execute_effects(conn, context_plan.effects, user_message)
                    )

                if self.on_context_injected:
                    self.on_context_injected(
                        self.state.turn_count,
                        len([m for m in new_messages if m.get("role") == "system"]),
                    )

        except Exception as e:
            logger.warning(f"[VOICE_CTX_PROC] Context injection failed: {e}")

    async def _execute_effects(
        self,
        conn: Any,
        effects: List[Any],
        user_message: str,
    ) -> None:
        """Execute post-turn effects asynchronously."""
        try:
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
            logger.warning(f"[VOICE_CTX_PROC] Failed to execute post-turn effects: {e}")

    def on_assistant_response(self, content: str) -> None:
        """Track assistant response for context."""
        self.state.last_assistant_message = content
