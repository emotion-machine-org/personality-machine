"""SQLAlchemy async engine and session factory.

This module provides the core database connectivity using SQLAlchemy
with asyncpg as the async driver. Configuration is designed to be
compatible with Supabase's PgBouncer in transaction mode.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import DatabaseSettings, get_database_settings

logger = logging.getLogger(__name__)

# Global engine instance
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: DatabaseSettings | None = None) -> AsyncEngine:
    """Create a new SQLAlchemy async engine.

    Args:
        settings: Optional database settings. Uses environment config if not provided.

    Returns:
        Configured AsyncEngine instance
    """
    if settings is None:
        settings = get_database_settings()

    engine = create_async_engine(
        settings.async_dsn,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_recycle=settings.pool_recycle,
        pool_pre_ping=settings.pool_pre_ping,
        echo=settings.echo,
        # PgBouncer compatibility - critical for Supabase
        connect_args={
            # Disable prepared statements for pgbouncer transaction mode
            "statement_cache_size": settings.statement_cache_size,
            "server_settings": {
                "application_name": settings.application_name,
                "jit": "off",  # Disable JIT for faster connection startup
            },
        },
    )

    logger.info(
        "Created SQLAlchemy async engine: pool_size=%d, max_overflow=%d, app=%s",
        settings.pool_size,
        settings.max_overflow,
        settings.application_name,
    )

    return engine


def get_engine() -> AsyncEngine:
    """Get the global SQLAlchemy engine.

    Creates the engine on first call (lazy initialization).

    Returns:
        The global AsyncEngine instance

    Raises:
        RuntimeError: If engine not initialized
    """
    global _engine
    if _engine is None:
        raise RuntimeError("Database engine not initialized. Call initialize_engine() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the global session factory.

    Returns:
        Configured async_sessionmaker

    Raises:
        RuntimeError: If engine not initialized
    """
    global _session_factory
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized. Call initialize_engine() first.")
    return _session_factory


async def initialize_engine(
    settings: DatabaseSettings | None = None,
) -> AsyncEngine:
    """Initialize the database engine.

    Call this during application startup.

    Args:
        settings: Optional database settings

    Returns:
        The initialized engine
    """
    global _engine, _session_factory

    if _engine is not None:
        logger.warning("Engine already initialized, returning existing")
        return _engine

    _engine = create_engine(settings)
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # Test connectivity
    try:
        async with _engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.scalar()
        logger.info("Database connection verified")
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        await _engine.dispose()
        _engine = None
        _session_factory = None
        raise

    return _engine


async def close_engine() -> None:
    """Close the database engine and dispose of connection pool.

    Call this during application shutdown.
    """
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None
        _session_factory = None


class SessionLocal:
    """Context manager for creating database sessions.

    Usage:
        async with SessionLocal() as session:
            result = await session.execute(...)
            await session.commit()
    """

    def __init__(self):
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        factory = get_session_factory()
        self._session = factory()
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            if exc_type is not None:
                await self._session.rollback()
            await self._session.close()
            self._session = None
