"""Timezone utilities for user-aware datetime formatting."""

from datetime import datetime
from zoneinfo import ZoneInfo


def format_user_datetime(timezone: str = "UTC") -> str:
    """Format current datetime for user's timezone.

    Args:
        timezone: IANA timezone string (e.g., "America/Los_Angeles")

    Returns:
        Formatted string like "10:44 AM, Sunday, February 2, 2026 (America/Los_Angeles)"
    """
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
        timezone = "UTC"

    now = datetime.now(tz)
    formatted = now.strftime("%I:%M %p, %A, %B %d, %Y").lstrip("0")
    return f"Current time: {formatted} ({timezone})"


def get_timezone_section(timezone: str = "UTC") -> str:
    """Build a markdown section with current datetime for prompts.

    Args:
        timezone: IANA timezone string (e.g., "America/Los_Angeles")

    Returns:
        Markdown section string like:
        ## Current Date & Time
        Current time: 10:44 AM, Sunday, February 2, 2026 (America/Los_Angeles)
    """
    return f"## Current Date & Time\n{format_user_datetime(timezone)}"
