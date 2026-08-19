"""Pure ASGI middleware for request-scoped database sessions and context.

This middleware does NOT inherit from BaseHTTPMiddleware to correctly
support SSE streaming responses. BaseHTTPMiddleware completes before
the response body is fully streamed, which closes the session too early.

Key design:
- Creates one asyncpg connection per HTTP request (for repositories)
- Optionally creates SQLAlchemy session per HTTP request (disabled by default)
- Initializes RequestContext with the connection for per-request data caching
- Attaches session to scope["state"]["db_session"] (if enabled)
- Closes connection/session only after response body is fully sent (more_body=False)
- Supports both regular and streaming responses
"""

import logging
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..db import get_pool
from ..request_context import RequestContext, clear_request_context, set_request_context

logger = logging.getLogger(__name__)


class DatabaseSessionMiddleware:
    """Pure ASGI middleware for request-scoped database sessions.

    Unlike BaseHTTPMiddleware, this correctly handles streaming responses
    by closing the session only after the response body is fully sent.

    Usage:
        app.add_middleware(DatabaseSessionMiddleware)

        # In routes, access via request.state:
        @router.get("/items")
        async def list_items(request: Request):
            session = request.state.db_session
            result = await session.execute(select(Item))
            return result.scalars().all()

        # Or use the DbSession dependency:
        @router.get("/items")
        async def list_items(db: DbSession):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """

    # Paths to skip (no database session needed)
    SKIP_PATHS = frozenset(
        {"/healthz", "/health", "/favicon.ico", "/openapi.json", "/docs", "/redoc"}
    )

    def __init__(
        self,
        app: ASGIApp,
        commit_on_success: bool = False,
        track_sessions: bool = True,
        create_session: bool = False,
    ):
        """Initialize the middleware.

        Args:
            app: The ASGI application
            commit_on_success: Whether to auto-commit on successful (2xx/3xx) responses
            track_sessions: Whether to track sessions for debugging
            create_session: Whether to create SQLAlchemy session per request (default: False)
        """
        self.app = app
        self.commit_on_success = commit_on_success
        self.track_sessions = track_sessions
        self.create_session = create_session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process ASGI request."""
        # Only handle HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip paths that don't need database
        path = scope.get("path", "")
        if path in self.SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        # Acquire asyncpg connection from pool (for repositories)
        # This connection is held for the entire request lifecycle, including streaming
        pool = get_pool()
        asyncpg_conn = await pool.acquire()

        # Initialize RequestContext with the connection
        # This allows caching of auth data (project_id, etc.) to avoid redundant queries
        request_id = str(uuid4())
        ctx = RequestContext(request_id=request_id, start_time=time.time(), _conn=asyncpg_conn)
        set_request_context(ctx)

        # Optionally create SQLAlchemy session (disabled by default to save overhead)
        session = None
        session_id = None
        if self.create_session:
            from .engine import get_session_factory
            from .tracking import SessionTracker

            session_factory = get_session_factory()
            session = session_factory()

            # Track session for debugging
            if self.track_sessions:
                method = scope.get("method", "?")
                context = f"{method} {path}"
                session_id = SessionTracker.track_session(session, context=context)
                session._tracker_id = session_id  # type: ignore[attr-defined]

            # Attach session to scope state (accessible via request.state.db_session)
            if "state" not in scope:
                scope["state"] = {}
            scope["state"]["db_session"] = session

        # Track response status and timing
        start_time = time.perf_counter()
        response_status = 200
        resources_closed = False

        async def close_resources(commit: bool = False) -> None:
            """Close the session and release asyncpg connection."""
            nonlocal resources_closed
            if resources_closed:
                return
            resources_closed = True

            # Close SQLAlchemy session if created
            if session is not None:
                from .tracking import SessionTracker

                try:
                    if commit:
                        await session.commit()
                except Exception as e:
                    logger.warning("Failed to commit session: %s", e)
                    await session.rollback()
                finally:
                    await session.close()
                    if session_id:
                        tracked = SessionTracker.untrack_session(session_id)
                        duration_ms = (time.perf_counter() - start_time) * 1000
                        if tracked and duration_ms > 5000:
                            logger.warning(
                                "Slow request: %s took %.0fms (queries=%d)",
                                tracked.context,
                                duration_ms,
                                tracked.query_count,
                            )

            # Release asyncpg connection back to pool
            try:
                await pool.release(asyncpg_conn)
            except Exception as e:
                logger.warning("Failed to release asyncpg connection: %s", e)

        async def send_wrapper(message: Message) -> None:
            """Wrap send to detect response completion."""
            nonlocal response_status

            if message["type"] == "http.response.start":
                response_status = message.get("status", 200)

            elif message["type"] == "http.response.body":
                # Check if this is the final body chunk
                more_body = message.get("more_body", False)

                if not more_body:
                    # Response complete - close resources
                    should_commit = self.commit_on_success and 200 <= response_status < 400
                    await close_resources(commit=should_commit)

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Rollback and close on unhandled exception
            if session is not None:
                try:
                    await session.rollback()
                except Exception:
                    pass
            await close_resources(commit=False)
            raise
        finally:
            # Safety net: ensure resources are closed even if send_wrapper didn't fire
            # (e.g., connection dropped before response started)
            if not resources_closed:
                await close_resources(commit=False)
            # Clear request context to avoid leaks
            clear_request_context()


def add_db_session_middleware(
    app,
    commit_on_success: bool = False,
    track_sessions: bool = True,
    create_session: bool = False,
) -> None:
    """Helper to add database session middleware to a FastAPI app.

    Args:
        app: FastAPI application instance
        commit_on_success: Whether to auto-commit on successful responses
        track_sessions: Whether to enable session tracking
        create_session: Whether to create SQLAlchemy session per request (default: False)

    Usage:
        from app.database import add_db_session_middleware

        app = FastAPI()
        add_db_session_middleware(app)
    """
    app.add_middleware(
        DatabaseSessionMiddleware,
        commit_on_success=commit_on_success,
        track_sessions=track_sessions,
        create_session=create_session,
    )
