# server/app/routers/voice/openclaw_llm.py
"""OpenClaw LLM service for Pipecat voice pipeline.

This module provides a Pipecat-compatible LLM service that forwards
requests to OpenClaw instead of processing them locally. This allows
OpenClaw to serve as the "brain" for voice companions while EM handles
the voice I/O (STT/TTS).

Usage in pipeline:
    Instead of:
        llm = OpenAILLMService(...)
    Use:
        llm = OpenClawLLMService(config=openclaw_config, callback_base_url=...)
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncGenerator, Dict, List

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.llm_service import LLMService

from .openclaw import (
    OpenClawClient,
    OpenClawConfig,
    process_with_openclaw,
)

logger = logging.getLogger(__name__)


class OpenClawLLMService(LLMService):
    """Pipecat LLM service that uses OpenClaw as the brain.

    This service intercepts LLM requests and forwards them to OpenClaw,
    which processes them with its full context (memory, tools, etc.)
    and returns a response.

    Unlike traditional LLM services, this doesn't do local inference -
    it delegates to the OpenClaw agent running elsewhere.
    """

    def __init__(
        self,
        config: OpenClawConfig,
        callback_base_url: str,
        companion_id: str | None = None,
        relationship_id: str | None = None,
        user_id: str | None = None,
        **kwargs,
    ):
        """Initialize the OpenClaw LLM service.

        Args:
            config: OpenClaw configuration with webhook URL and auth
            callback_base_url: Base URL for the callback endpoint
            companion_id: EM companion ID for context
            relationship_id: EM relationship ID for context
            user_id: External user ID for context
        """
        # Initialize parent with dummy model name
        super().__init__(model="openclaw-agent", **kwargs)

        self.config = config
        self.callback_base_url = callback_base_url
        self.companion_id = companion_id
        self.relationship_id = relationship_id
        self.user_id = user_id
        self._client = OpenClawClient(config)

    async def close(self) -> None:
        """Clean up resources."""
        await self._client.close()

    def _build_context(self) -> Dict[str, Any]:
        """Build context dict for OpenClaw request."""
        ctx = {}
        if self.companion_id:
            ctx["companion_id"] = self.companion_id
        if self.relationship_id:
            ctx["relationship_id"] = self.relationship_id
        if self.user_id:
            ctx["user_id"] = self.user_id
        return ctx

    def _extract_user_message(self, context: OpenAILLMContext) -> str | None:
        """Extract the most recent user message from context."""
        messages = context.get_messages()
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return None

    async def _process_context(
        self,
        context: OpenAILLMContext,
    ) -> AsyncGenerator[Frame, None]:
        """Process the context through OpenClaw.

        This is the main LLM processing method. Instead of running inference
        locally, we send the user's message to OpenClaw and yield the response.
        """
        user_message = self._extract_user_message(context)

        if not user_message:
            logger.warning("[OPENCLAW_LLM] No user message found in context")
            yield LLMFullResponseStartFrame()
            yield TextFrame("I didn't catch that. Could you repeat?")
            yield LLMFullResponseEndFrame()
            return

        logger.info(f"[OPENCLAW_LLM] Processing message: {user_message[:50]}...")

        try:
            # Signal start of response
            yield LLMFullResponseStartFrame()

            # Get response from OpenClaw
            response = await process_with_openclaw(
                client=self._client,
                message=user_message,
                callback_base_url=self.callback_base_url,
                context=self._build_context(),
                timeout_seconds=self.config.timeout_seconds,
            )

            # Yield response text
            yield TextFrame(response)

            # Signal end of response
            yield LLMFullResponseEndFrame()

            # Add assistant message to context for conversation history
            context.add_message({"role": "assistant", "content": response})

            logger.info(f"[OPENCLAW_LLM] Response: {response[:50]}...")

        except Exception as e:
            logger.exception(f"[OPENCLAW_LLM] Error processing message: {e}")
            yield LLMFullResponseStartFrame()
            yield TextFrame("I encountered an error. Please try again.")
            yield LLMFullResponseEndFrame()

    # Required LLMService methods

    async def get_chat_completions(
        self,
        context: OpenAILLMContext,
        messages: List[Dict[str, Any]],
    ) -> AsyncGenerator[Frame, None]:
        """Get chat completions from OpenClaw.

        This is called by the Pipecat pipeline when it needs LLM output.
        """
        async for frame in self._process_context(context):
            yield frame

    def create_context_aggregator(
        self,
        context: OpenAILLMContext,
        *,
        assistant_expect_stripped_words: bool = True,
    ):
        """Create context aggregator.

        Required by LLMService. We use the standard OpenAI context aggregator
        since we're still using OpenAI-format messages.
        """
        from .fast_brain_llm import ContextAggregatorPair

        # Try new Pipecat API first, fall back to deprecated one
        try:
            from pipecat.processors.aggregators.llm_response import (
                LLMAssistantContextAggregator,
                LLMUserContextAggregator,
            )

            user_aggregator = LLMUserContextAggregator(context)
            assistant_aggregator = LLMAssistantContextAggregator(context)
            return ContextAggregatorPair(_user=user_aggregator, _assistant=assistant_aggregator)
        except ImportError:
            pass

        # Fall back to OpenAI-specific aggregators (older Pipecat)
        from pipecat.processors.aggregators.openai_llm_context import (
            OpenAIAssistantContextAggregator,
            OpenAIUserContextAggregator,
        )

        user_aggregator = OpenAIUserContextAggregator(context)
        assistant_aggregator = OpenAIAssistantContextAggregator(
            context,
            expect_stripped_words=assistant_expect_stripped_words,
        )
        return ContextAggregatorPair(_user=user_aggregator, _assistant=assistant_aggregator)


def build_openclaw_llm_service(
    config: OpenClawConfig,
    callback_base_url: str | None = None,
    companion_id: str | None = None,
    relationship_id: str | None = None,
    user_id: str | None = None,
) -> OpenClawLLMService:
    """Build an OpenClaw LLM service for use in voice pipeline.

    Args:
        config: OpenClaw configuration
        callback_base_url: Base URL for callback. Defaults to PUBLIC_HOST env var.
        companion_id: EM companion ID
        relationship_id: EM relationship ID
        user_id: External user ID

    Returns:
        Configured OpenClawLLMService instance
    """
    if not callback_base_url:
        host = os.environ.get("PUBLIC_HOST", "localhost:8100")
        scheme = "https" if os.environ.get("ENV") == "prod" else "http"
        callback_base_url = f"{scheme}://{host}"

    return OpenClawLLMService(
        config=config,
        callback_base_url=callback_base_url,
        companion_id=companion_id,
        relationship_id=relationship_id,
        user_id=user_id,
    )
