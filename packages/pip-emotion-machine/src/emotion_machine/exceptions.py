"""Exceptions for the Emotion Machine SDK."""

from __future__ import annotations

from typing import Any


class APIError(RuntimeError):
    """Error from the Emotion Machine API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}

    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.status_code}] {self.message}"
        return self.message

    def __repr__(self) -> str:
        return f"APIError({self.message!r}, status_code={self.status_code})"


class KnowledgeJobFailed(RuntimeError):
    """A knowledge ingestion job failed."""

    def __init__(self, job_id: str, error: str | None = None) -> None:
        self.job_id = job_id
        self.error = error
        super().__init__(f"Knowledge job {job_id} failed: {error}")


class WebSocketError(RuntimeError):
    """WebSocket connection error."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ConnectionClosed(WebSocketError):
    """WebSocket connection was closed."""

    pass
