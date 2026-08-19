from __future__ import annotations

_IMPORTANCE_RUBRIC = (
    "You evaluate how important a memory is for future interactions, across any domain (e.g., health, learning, productivity, relationships, finance,  creative work).\n"
    "Consider: enduring personal facts, long-term preferences and constraints, goals/commitments/tasks, pivotal decisions, nuanced insights, safety/ethical boundaries, and changes over time.\n"
    "Scoring rubric (1-10):\n"
    "- 10 (Critical): Identity-level facts, explicit commitments, deadlines, safety constraints, or insights central to ongoing goals.\n"
    "- 9 (Very Important): Strong, stable preferences; multi-step plans; clear constraints or rules the companion must respect.\n"
    "- 8 (Important): Specific goals, detailed feedback shaping future behavior, key context to avoid repeating mistakes.\n"
    "- 7 (Moderately Important): Distinct interests, medium-term tasks, or actionable hints that improve personalization.\n"
    "- 5-6 (Somewhat Important): Useful details or mild preferences that may help but are not crucial.\n"
    "- 3-4 (Low Importance): Simple Q&A, generic chit-chat, or transient details unlikely to help later.\n"
    "- 1-2 (Minimal): Off-topic, ephemeral, or redundant content.\n"
    "Instructions: Reply ONLY with a single number 1-10. Optionally add a second line with 'Note: ...' to briefly explain the rating.\n"
)


def build_importance_system_prompt(guidance: str | None) -> str:
    base = (guidance or "").strip()
    if base:
        return base + "\n\n" + _IMPORTANCE_RUBRIC
    return _IMPORTANCE_RUBRIC
