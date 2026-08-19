"""FastAPI dependencies for database session injection.

Provides multiple ways to access database sessions in route handlers:
1. DbSession - Request-scoped session from middleware (recommended)
2. get_db_session_dep - Generator dependency for explicit control
3. get_session - Context manager for background tasks
"""

from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import get_session_factory
from .tracking import SessionTracker


def get_db_session(request: Request) -> AsyncSession:
    """Get database session from middleware-provided request state.

    This is the recommended way to access the database in route handlers.
    The session is created by DatabaseSessionMiddleware and attached to
    request.state.db_session.

    Usage:
        @router.get("/users")
        async def list_users(db: DbSession):
            result = await db.execute(select(User))
            return result.scalars().all()

    Returns:
        AsyncSession from the middleware

    Raises:
        HTTPException 503: If database session not available (middleware not configured)
    """
    session = getattr(request.state, "db_session", None)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database session not available. Ensure DatabaseSessionMiddleware is configured.",
        )

    # Record activity if session is tracked
    tracker_id = getattr(session, "_tracker_id", None)
    if tracker_id:
        SessionTracker.record_activity(tracker_id)

    return session


# Type alias for clean dependency injection
# Usage: async def my_route(db: DbSession): ...
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_db_session_dep() -> AsyncGenerator[AsyncSession, None]:
    """Generator dependency that creates a new session per call.

    Use this when you need a session outside of the middleware's scope,
    or when you need explicit control over the session lifecycle.

    Note: Prefer DbSession for normal route handlers as it reuses the
    middleware-provided session.

    Usage:
        @router.get("/items")
        async def list_items(
            db: Annotated[AsyncSession, Depends(get_db_session_dep)]
        ):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    session_factory = get_session_factory()
    session = session_factory()

    session_id = SessionTracker.track_session(session, context="dependency")

    try:
        yield session
    finally:
        SessionTracker.untrack_session(session_id)
        await session.close()


async def get_db_transaction() -> AsyncGenerator[AsyncSession, None]:
    """Generator dependency with automatic transaction management.

    The transaction is automatically committed on success or rolled back
    on exception.

    Usage:
        @router.post("/transfer")
        async def transfer_funds(
            db: Annotated[AsyncSession, Depends(get_db_transaction)]
        ):
            await db.execute(update(Account).where(...))
            await db.execute(update(Account).where(...))
            # Auto-commits if no exception raised
    """
    session_factory = get_session_factory()
    session = session_factory()

    session_id = SessionTracker.track_session(session, context="transaction")

    try:
        async with session.begin():
            yield session
        # Transaction committed automatically by context manager
    except Exception:
        # Transaction rolled back automatically by context manager
        raise
    finally:
        SessionTracker.untrack_session(session_id)
        await session.close()


# Type alias for transaction dependency
DbTransaction = Annotated[AsyncSession, Depends(get_db_transaction)]


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for getting a database session.

    Use this for background tasks, Celery workers, or anywhere outside
    the HTTP request context where you can't use FastAPI dependencies.

    Usage:
        async def background_task():
            async with get_session() as session:
                result = await session.execute(select(User))
                await session.commit()
    """
    session_factory = get_session_factory()
    session = session_factory()

    session_id = SessionTracker.track_session(session, context="context_manager")

    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        SessionTracker.untrack_session(session_id)
        await session.close()
