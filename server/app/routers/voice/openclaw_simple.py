# server/app/routers/voice/openclaw_simple.py
"""Simplified OpenClaw integration using OpenAI-compatible chat completions.

This module provides a much simpler integration with OpenClaw by using its
built-in OpenAI-compatible /v1/chat/completions endpoint. No webhook/callback
pattern needed - just synchronous HTTP requests.

Architecture:
    Phone/Web → EM (STT) → OpenClaw /v1/chat/completions → EM (TTS) → Phone/Web
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List

import httpx
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.llm_service import LLMService
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OpenClawSimpleConfig(BaseModel):
    """Configuration for simplified OpenClaw integration."""

    enabled: bool = False
    base_url: str = Field(
        default="http://127.0.0.1:18789",
        description="OpenClaw gateway base URL",
    )
    auth_token: str = Field(
        default="",
        description="OpenClaw gateway auth token",
    )
    timeout_seconds: int = Field(
        default=60,
        description="Request timeout in seconds",
    )
    model: str = Field(
        default="claude",
        description="Model name to use (OpenClaw will route appropriately)",
    )


class OpenClawSimpleLLMService(LLMService):
    """Pipecat LLM service using OpenClaw's OpenAI-compatible endpoint.

    Much simpler than the webhook/callback approach - just makes synchronous
    HTTP calls to OpenClaw's /v1/chat/completions endpoint.
    """

    def __init__(
        self,
        config: OpenClawSimpleConfig,
        companion_id: str | None = None,
        relationship_id: str | None = None,
        user_id: str | None = None,
        **kwargs,
    ):
        super().__init__(model=config.model, **kwargs)
        self.config = config
        self.companion_id = companion_id
        self.relationship_id = relationship_id
        self.user_id = user_id
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds),
            )
        return self._http_client

    async def close(self) -> None:
        """Clean up resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _extract_user_message(self, context: OpenAILLMContext) -> str | None:
        """Extract the most recent user message from context."""
        messages = context.get_messages()
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return None

    async def _call_openclaw(self, messages: List[Dict[str, Any]]) -> str:
        """Make a request to OpenClaw's chat completions endpoint."""
        client = await self._get_client()

        headers = {
            "Content-Type": "application/json",
        }
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"

        payload = {
            "model": self.config.model,
            "messages": messages,
        }

        # Add context metadata as a header or in the payload
        if self.companion_id or self.relationship_id or self.user_id:
            payload["metadata"] = {
                "companion_id": self.companion_id,
                "relationship_id": self.relationship_id,
                "user_id": self.user_id,
                "source": "em-voice",
            }

        url = f"{self.config.base_url.rstrip('/')}/v1/chat/completions"

        logger.info(f"[OPENCLAW_SIMPLE] Calling {url} with {len(messages)} messages")

        response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            logger.error(
                f"[OPENCLAW_SIMPLE] Request failed: {response.status_code} - {response.text}"
            )
            raise Exception(f"OpenClaw request failed: {response.status_code}")

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        logger.info(f"[OPENCLAW_SIMPLE] Response: {content[:100]}...")

        return content

    async def _process_context(
        self,
        context: OpenAILLMContext,
    ) -> AsyncGenerator[Frame, None]:
        """Process the context through OpenClaw."""
        user_message = self._extract_user_message(context)

        if not user_message:
            logger.warning("[OPENCLAW_SIMPLE] No user message found in context")
            yield LLMFullResponseStartFrame()
            yield TextFrame("I didn't catch that. Could you repeat?")
            yield LLMFullResponseEndFrame()
            return

        logger.info(f"[OPENCLAW_SIMPLE] Processing: {user_message[:50]}...")

        try:
            yield LLMFullResponseStartFrame()

            # Get all messages for context
            messages = context.get_messages()

            # Call OpenClaw
            response = await self._call_openclaw(messages)

            yield TextFrame(response)
            yield LLMFullResponseEndFrame()

            # Update context with assistant response
            context.add_message({"role": "assistant", "content": response})

        except Exception as e:
            logger.exception(f"[OPENCLAW_SIMPLE] Error: {e}")
            yield LLMFullResponseStartFrame()
            yield TextFrame("I encountered an error. Please try again.")
            yield LLMFullResponseEndFrame()

    async def get_chat_completions(
        self,
        context: OpenAILLMContext,
        messages: List[Dict[str, Any]],
    ) -> AsyncGenerator[Frame, None]:
        """Get chat completions from OpenClaw."""
        async for frame in self._process_context(context):
            yield frame

    def create_context_aggregator(
        self,
        context: OpenAILLMContext,
        *,
        assistant_expect_stripped_words: bool = True,
    ):
        """Create context aggregator."""
        from pipecat.processors.aggregators.openai_llm_context import (
            OpenAIAssistantContextAggregator,
            OpenAIUserContextAggregator,
        )

        user_aggregator = OpenAIUserContextAggregator(context)
        assistant_aggregator = OpenAIAssistantContextAggregator(
            context,
            expect_stripped_words=assistant_expect_stripped_words,
        )

        return (user_aggregator, assistant_aggregator)


def build_openclaw_simple_service(
    config: OpenClawSimpleConfig,
    companion_id: str | None = None,
    relationship_id: str | None = None,
    user_id: str | None = None,
) -> OpenClawSimpleLLMService:
    """Build an OpenClaw LLM service using the simple chat completions approach."""
    return OpenClawSimpleLLMService(
        config=config,
        companion_id=companion_id,
        relationship_id=relationship_id,
        user_id=user_id,
    )
