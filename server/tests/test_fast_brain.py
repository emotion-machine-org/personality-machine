# server/tests/test_fast_brain.py
"""Tests for Fast Brain voice processor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers.voice.fast_brain import (
    FastBrainResponse,
    Intent,
    classify_and_respond,
    delegate_to_openclaw,
    process_voice_input,
    wait_for_openclaw_result,
)


class TestClassifyAndRespond:
    """Tests for intent classification."""

    @pytest.mark.asyncio
    async def test_no_api_key_defaults_to_chit_chat(self):
        """Without API key, should default to chit_chat."""
        with patch("app.routers.voice.fast_brain.OPENROUTER_API_KEY", ""):
            response = await classify_and_respond("hello")
            assert response.intent == Intent.CHIT_CHAT
            assert response.immediate_response is not None

    @pytest.mark.asyncio
    async def test_classification_with_mocked_api(self):
        """Test classification with mocked OpenRouter API."""
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": '{"intent": "task", "immediate_response": "Got it!", "task_description": "check calendar"}'
                    }
                }
            ]
        }

        with patch("app.routers.voice.fast_brain.OPENROUTER_API_KEY", "test-key"):  # noqa: SIM117
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200, json=lambda: mock_response)
                mock_post.return_value.raise_for_status = MagicMock()

                response = await classify_and_respond("check my calendar")

                assert response.intent == Intent.TASK
                assert response.immediate_response == "Got it!"
                assert response.task_description == "check calendar"
                assert response.task_id is not None


class TestDelegateToOpenClaw:
    """Tests for OpenClaw delegation."""

    @pytest.mark.asyncio
    async def test_delegation_success(self):
        """Test successful delegation to OpenClaw."""
        with (
            patch("app.routers.voice.fast_brain.OPENCLAW_WEBHOOK_URL", "http://test.local/webhook"),
            patch("httpx.AsyncClient.post") as mock_post,
        ):
            mock_post.return_value = MagicMock(status_code=202)

            result = await delegate_to_openclaw(
                task_id="test-123",
                user_message="check calendar",
                task_description="check user's calendar",
            )

            assert result
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_delegation_no_webhook_url(self):
        """Test delegation fails without webhook URL."""
        with patch("app.routers.voice.fast_brain.OPENCLAW_WEBHOOK_URL", ""):
            result = await delegate_to_openclaw(
                task_id="test-123",
                user_message="check calendar",
                task_description="check calendar",
            )
            assert not result


class TestWaitForResult:
    """Tests for waiting on OpenClaw results."""

    @pytest.mark.asyncio
    async def test_wait_gets_result(self):
        """Test waiting and getting a result."""
        import os
        import tempfile

        from app.routers.voice.hot_context import HotContext

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "hot_context.md")
            ctx = HotContext(path=path)

            # Log a completed task
            ctx.log_start("task-123", "test query")
            ctx.log_done("task-123", "Here's your result!")

            # Should find it immediately
            result = await wait_for_openclaw_result(
                task_id="task-123",
                hot_context=ctx,
                timeout_seconds=1.0,
            )

            assert result == "Here's your result!"

    @pytest.mark.asyncio
    async def test_wait_timeout(self):
        """Test waiting times out for missing task."""
        import os
        import tempfile

        from app.routers.voice.hot_context import HotContext

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "hot_context.md")
            ctx = HotContext(path=path)

            # Don't log any result
            result = await wait_for_openclaw_result(
                task_id="nonexistent-task",
                hot_context=ctx,
                timeout_seconds=0.5,
                poll_interval=0.1,
            )

            assert result is None


class TestProcessVoiceInput:
    """Tests for the main voice processing function."""

    @pytest.mark.asyncio
    async def test_chit_chat_responds_immediately(self):
        """Chit-chat should return immediate response."""
        with patch("app.routers.voice.fast_brain.classify_and_respond") as mock_classify:
            mock_classify.return_value = FastBrainResponse(
                intent=Intent.CHIT_CHAT,
                immediate_response="Hello! How can I help?",
                task_id=None,
                task_description=None,
            )

            result = await process_voice_input("hi there")

            assert result == "Hello! How can I help?"

    @pytest.mark.asyncio
    async def test_task_delegates_and_waits(self):
        """Task should delegate and wait for result."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "hot_context.md")

            with patch("app.routers.voice.fast_brain.classify_and_respond") as mock_classify:
                mock_classify.return_value = FastBrainResponse(
                    intent=Intent.TASK,
                    immediate_response="Got it, checking...",
                    task_id="task-456",
                    task_description="check the weather",
                )

                with patch("app.routers.voice.fast_brain.delegate_to_openclaw") as mock_delegate:
                    mock_delegate.return_value = True

                    with patch(
                        "app.routers.voice.fast_brain.wait_for_openclaw_result"
                    ) as mock_wait:
                        mock_wait.return_value = "It's 72°F and sunny!"

                        callback_called = []

                        def on_immediate(msg):
                            callback_called.append(msg)

                        result = await process_voice_input(
                            "what's the weather",
                            on_immediate_response=on_immediate,
                            hot_context_path=path,
                        )

                        # Should have called immediate callback
                        assert "Got it" in callback_called[0]

                        # Final result should be from OpenClaw
                        assert result == "It's 72°F and sunny!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
