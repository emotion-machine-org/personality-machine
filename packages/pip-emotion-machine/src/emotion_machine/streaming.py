"""SSE (Server-Sent Events) streaming parser."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator


def parse_sse_line(line: str) -> tuple[str | None, str | None]:
    """Parse a single SSE line into (field, value) or (None, None) for empty/comment."""
    if not line or line.startswith(":"):
        return None, None
    if ":" in line:
        field, _, value = line.partition(":")
        return field.strip(), value.lstrip()
    return line, ""


def parse_sse_sync(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Parse SSE events from a synchronous line iterator.

    Yields dicts with keys: event, id, data
    """
    event_type: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []

    for line in lines:
        line = line.rstrip("\r\n")

        if not line:
            # Empty line = dispatch event
            if data_lines:
                data_str = "\n".join(data_lines)
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = data_str

                yield {
                    "event": event_type or "message",
                    "id": event_id,
                    "data": data,
                }

            # Reset for next event
            event_type = None
            event_id = None
            data_lines = []
            continue

        field, value = parse_sse_line(line)
        if field is None:
            continue

        if field == "event":
            event_type = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value or "")

    # Handle any remaining data without trailing newline
    if data_lines:
        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = data_str

        yield {
            "event": event_type or "message",
            "id": event_id,
            "data": data,
        }


async def parse_sse_async(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Parse SSE events from an async line iterator.

    Yields dicts with keys: event, id, data
    """
    event_type: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []

    async for line in lines:
        line = line.rstrip("\r\n")

        if not line:
            # Empty line = dispatch event
            if data_lines:
                data_str = "\n".join(data_lines)
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = data_str

                yield {
                    "event": event_type or "message",
                    "id": event_id,
                    "data": data,
                }

            # Reset for next event
            event_type = None
            event_id = None
            data_lines = []
            continue

        field, value = parse_sse_line(line)
        if field is None:
            continue

        if field == "event":
            event_type = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value or "")

    # Handle any remaining data without trailing newline
    if data_lines:
        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = data_str

        yield {
            "event": event_type or "message",
            "id": event_id,
            "data": data,
        }
