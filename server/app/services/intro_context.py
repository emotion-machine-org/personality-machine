from __future__ import annotations

from typing import Any

DEFAULT_INTRO_MESSAGE_TEXT = "Hi, how are you?"


def resolve_intro_message_text(companion_config: Any) -> str | None:
    """Return the configured text WebSocket intro, if enabled."""
    intro_cfg = getattr(companion_config, "intro_message", None) if companion_config else None
    if not intro_cfg or not bool(getattr(intro_cfg, "enabled", False)):
        return None

    text = str(getattr(intro_cfg, "text", "") or "").strip()
    return text or DEFAULT_INTRO_MESSAGE_TEXT


def drop_intro_preamble_from_history(
    history_rows: list[dict[str, Any]],
    companion_config: Any,
) -> list[dict[str, Any]]:
    """Omit persisted WebSocket intro turns from LLM context.

    The intro is a UI/session opener, not useful model context. Keeping it as a
    leading assistant message before the user's first real turn can make some
    providers answer the intro instead of the current user message.
    """
    intro_text = resolve_intro_message_text(companion_config)
    if not intro_text or not history_rows:
        return history_rows

    drop_count = 0
    for row in history_rows:
        if row.get("role") != "assistant":
            break
        if str(row.get("content") or "").strip() != intro_text:
            break
        drop_count += 1

    if not drop_count:
        return history_rows
    return history_rows[drop_count:]
