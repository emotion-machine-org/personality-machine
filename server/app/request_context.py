"""Request context for carrying per-request data across async boundaries.

Uses contextvars for async-safe storage. Data set in the context is available
to all code running within the same request, including nested async calls.

This eliminates redundant database queries by caching commonly-needed data
that's computed during the request lifecycle (e.g., project_id from auth).

Usage:
    # In middleware or early request handling:
    ctx = RequestContext(request_id=str(uuid4()))
    set_request_context(ctx)

    # After authentication:
    ctx = get_request_context()
    ctx.project_id = project.id
    ctx.api_key_id = api_key.id

    # Anywhere in the request:
    ctx = get_request_context()
    project_id = ctx.project_id  # No DB query needed!

    # Database connection (held for entire request including streaming):
    conn = ctx.conn  # Raises if not initialized
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


@dataclass
class RequestContext:
    """Per-request context carrying authenticated and computed data.

    Attributes:
        request_id: Unique identifier for this request (for logging/tracing)
        start_time: Request start time (for duration tracking)
        project_id: Project ID from API key authentication
        api_key_id: API key ID used for this request
        user_id: Clerk user ID (for dashboard requests)
        companion_id: Companion ID if applicable
        _conn: Database connection held for entire request (including streaming)
        _cache: Generic cache for request-scoped data
    """

    # Request metadata
    request_id: str = ""
    start_time: float = field(default_factory=time.time)

    # Authentication data (populated after auth)
    project_id: UUID | None = None
    api_key_id: UUID | None = None
    user_id: str | None = None  # Clerk user ID for dashboard

    # Request-specific data
    companion_id: UUID | None = None

    # Database connection - held for entire request lifecycle (including streaming)
    # This is managed by DatabaseSessionMiddleware and should not be closed manually
    _conn: asyncpg.Connection | None = field(default=None, repr=False)

    # Generic cache for any request-scoped data
    _cache: dict[str, Any] = field(default_factory=dict)

    @property
    def conn(self) -> asyncpg.Connection:
        """Get the request-scoped database connection.

        Returns:
            The asyncpg connection for this request

        Raises:
            RuntimeError: If connection not initialized (middleware not configured)
        """
        if self._conn is None:
            raise RuntimeError(
                "Database connection not initialized in RequestContext. "
                "Ensure DatabaseSessionMiddleware is configured and you're within a request."
            )
        return self._conn

    @property
    def has_conn(self) -> bool:
        """Check if a database connection is available."""
        return self._conn is not None

    def get_cached(self, key: str) -> Any | None:
        """Get a cached value."""
        return self._cache.get(key)

    def set_cached(self, key: str, value: Any) -> None:
        """Set a cached value."""
        self._cache[key] = value

    @property
    def elapsed_ms(self) -> float:
        """Milliseconds since request started."""
        return (time.time() - self.start_time) * 1000


# ContextVar for async-safe per-request storage
_request_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def get_request_context() -> RequestContext:
    """Get the current request context.

    Returns:
        The RequestContext for the current request

    Raises:
        RuntimeError: If called outside of a request context
    """
    ctx = _request_context.get()
    if ctx is None:
        raise RuntimeError(
            "RequestContext not initialized. "
            "This usually means you're calling from outside a request context "
            "(e.g., background task, startup). Use try_get_request_context() instead."
        )
    return ctx


def try_get_request_context() -> RequestContext | None:
    """Try to get the current request context.

    Returns:
        The RequestContext if available, None otherwise.
        Use this in code that may run outside request context.
    """
    return _request_context.get()


def set_request_context(ctx: RequestContext) -> None:
    """Set the request context for the current async task.

    Args:
        ctx: The RequestContext to set
    """
    _request_context.set(ctx)


def clear_request_context() -> None:
    """Clear the request context.

    Call this at the end of request processing to avoid leaks.
    """
    _request_context.set(None)
