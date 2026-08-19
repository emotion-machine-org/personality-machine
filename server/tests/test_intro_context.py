from __future__ import annotations

from app.models.companion import CompanionConfig
from app.services.intro_context import (
    DEFAULT_INTRO_MESSAGE_TEXT,
    drop_intro_preamble_from_history,
    resolve_intro_message_text,
)


def test_resolve_intro_message_text_uses_default_when_config_text_empty() -> None:
    config = CompanionConfig(intro_message={"enabled": True, "text": ""})

    assert resolve_intro_message_text(config) == DEFAULT_INTRO_MESSAGE_TEXT


def test_drop_intro_preamble_from_history_removes_configured_intro() -> None:
    config = CompanionConfig(intro_message={"enabled": True, "text": "Hi, what's up?"})

    history = drop_intro_preamble_from_history(
        [
            {"role": "assistant", "content": "Hi, what's up?"},
            {"role": "user", "content": "why do I have blood clots ?"},
        ],
        config,
    )

    assert history == [{"role": "user", "content": "why do I have blood clots ?"}]


def test_drop_intro_preamble_from_history_keeps_non_intro_assistant_context() -> None:
    config = CompanionConfig(intro_message={"enabled": True, "text": "Hi, what's up?"})

    history = [
        {"role": "assistant", "content": "Your appointment reminder is ready."},
        {"role": "user", "content": "yes"},
    ]

    assert drop_intro_preamble_from_history(history, config) == history
