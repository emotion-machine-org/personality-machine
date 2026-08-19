"""Database configuration with environment-based settings.

Settings are designed to match existing asyncpg pool configuration
for consistency during migration.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class DatabaseSettings:
    """Database connection pool configuration.

    All settings can be overridden via environment variables.
    Defaults match the existing asyncpg pool settings in db.py.
    """

    # Connection string (required)
    dsn: str = field(default_factory=lambda: os.getenv("DATABASE_DSN", ""))

    # Pool sizing - matches existing asyncpg: min=2, max=12
    # Keeps below Supabase's 15 connection limit (leaves headroom for asyncpg pool)
    pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "10")))
    max_overflow: int = field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "2")))

    # Timeouts (in seconds)
    pool_timeout: int = field(default_factory=lambda: int(os.getenv("DB_POOL_TIMEOUT", "30")))
    pool_recycle: int = field(default_factory=lambda: int(os.getenv("DB_POOL_RECYCLE", "1800")))

    # Connection health check
    pool_pre_ping: bool = field(
        default_factory=lambda: os.getenv("DB_POOL_PRE_PING", "true").lower()
        in ("true", "1", "yes")
    )

    # SQL echo for debugging
    echo: bool = field(
        default_factory=lambda: os.getenv("DB_ECHO", "false").lower() in ("true", "1", "yes")
    )

    # Application identifier
    application_name: str = field(
        default_factory=lambda: os.getenv("DB_APPLICATION_NAME", "emotion_machine_api_sa")
    )

    # PgBouncer compatibility (critical for Supabase)
    # Must be 0 for transaction-mode pooling
    statement_cache_size: int = 0

    @property
    def async_dsn(self) -> str:
        """Convert DSN to async format for SQLAlchemy."""
        dsn = self.dsn
        if dsn.startswith("postgresql://"):
            return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif dsn.startswith("postgres://"):
            return dsn.replace("postgres://", "postgresql+asyncpg://", 1)
        return dsn

    def validate(self) -> None:
        """Validate configuration, raising ValueError if invalid."""
        if not self.dsn:
            raise ValueError("DATABASE_DSN environment variable is required")
        if self.pool_size < 1:
            raise ValueError("DB_POOL_SIZE must be at least 1")


# Singleton settings instance
_settings: DatabaseSettings | None = None


def get_database_settings() -> DatabaseSettings:
    """Get validated database settings singleton."""
    global _settings
    if _settings is None:
        _settings = DatabaseSettings()
        _settings.validate()
    return _settings
