"""Database module for Emotion Machine API.

This module provides SQLAlchemy async database connectivity with:
- Connection pooling via asyncpg
- Request-scoped sessions via pure ASGI middleware
- Session tracking for debugging and leak detection
- Base ORM model with common patterns

Quick Start:
    # In your FastAPI app setup:
    from app.database import initialize_engine, close_engine, add_db_session_middleware

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await initialize_engine()
        yield
        await close_engine()

    app = FastAPI(lifespan=lifespan)
    add_db_session_middleware(app)

    # In your routes:
    from app.database import DbSession

    @router.get("/users")
    async def list_users(db: DbSession):
        result = await db.execute(select(User))
        return result.scalars().all()

    # For background tasks:
    from app.database import get_session

    async def background_job():
        async with get_session() as db:
            await db.execute(...)
            await db.commit()

Components:
    - Engine: SQLAlchemy async engine with connection pooling
    - Middleware: Pure ASGI request-scoped session middleware (streaming-safe)
    - Dependencies: FastAPI dependency injection (DbSession, DbTransaction)
    - Tracking: Session tracking for debugging
    - Base: ORM base model with UUID and timestamps
"""

# ORM base and mixins
from .base import (
    Base,
    BaseModel,
    TimestampMixin,
    UUIDMixin,
)

# Configuration
from .config import DatabaseSettings, get_database_settings

# FastAPI dependencies
from .dependencies import (
    DbSession,
    DbTransaction,
    get_db_session,
    get_db_session_dep,
    get_db_transaction,
    get_session,
)

# Engine and session factory
from .engine import (
    SessionLocal,
    close_engine,
    create_engine,
    get_engine,
    get_session_factory,
    initialize_engine,
)

# Middleware
from .middleware import (
    DatabaseSessionMiddleware,
    add_db_session_middleware,
)

# Session tracking
from .tracking import SessionTracker, TrackedSession

__all__ = [
    # Configuration
    "DatabaseSettings",
    "get_database_settings",
    # ORM Base
    "Base",
    "BaseModel",
    "TimestampMixin",
    "UUIDMixin",
    # Engine
    "create_engine",
    "get_engine",
    "get_session_factory",
    "initialize_engine",
    "close_engine",
    "SessionLocal",
    # Middleware
    "DatabaseSessionMiddleware",
    "add_db_session_middleware",
    # Dependencies
    "DbSession",
    "DbTransaction",
    "get_db_session",
    "get_db_session_dep",
    "get_db_transaction",
    "get_session",
    # Tracking
    "SessionTracker",
    "TrackedSession",
]
