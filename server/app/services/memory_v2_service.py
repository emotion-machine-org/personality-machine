"""Service layer for Memory V2 scratchpad functionality.

Provides formatting utilities for Memory V2.
LLM-based ingestion is handled by the Modal worker (memory_v2_ingest.py).
"""

from __future__ import annotations

from typing import Any, Dict, List

# Guidance text injected into system prompt - tells the companion it has memory
MEMORY_V2_GUIDANCE = (
    "You have persistent memory that stores facts about this user across conversations. "
    'Never say you "can\'t remember" or "don\'t have the ability to remember" - you DO have memory. '
    "If memories are listed below, reference them naturally when relevant. "
    "If a memory contradicts the PROFILE block, the PROFILE is authoritative — treat the memory as "
    "stale and rely on the profile instead. "
    "If no memories exist yet, you can say something like: \"We're just getting to know each other, "
    "but I'll remember important things about you as we talk.\""
)


class MemoryV2Service:
    """Service for Memory V2 formatting utilities."""

    @staticmethod
    def format_for_prompt(entries: List[Dict[str, Any]]) -> str:
        """Format entries for system prompt injection.

        Returns formatted string like:
        - User's name is Sarah (identity)
        - Prefers direct feedback (preference)
        """
        if not entries:
            return ""

        lines = []
        for entry in entries:
            content = entry["content"]
            entry_type = entry.get("type")
            if entry_type:
                lines.append(f"- {content} ({entry_type})")
            else:
                lines.append(f"- {content}")

        return "\n".join(lines)

    @staticmethod
    def format_entries_for_llm(entries: List[Dict[str, Any]]) -> str:
        """Format entries for LLM analysis (includes IDs for update/delete)."""
        if not entries:
            return "(No memories stored yet)"

        lines = []
        for entry in entries:
            entry_id = str(entry["id"])
            content = entry["content"]
            entry_type = entry.get("type", "other")
            lines.append(f"[{entry_id}] ({entry_type}) {content}")

        return "\n".join(lines)
