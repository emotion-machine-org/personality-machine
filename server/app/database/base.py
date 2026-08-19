"""SQLAlchemy declarative base and common model mixins.

Provides the foundation for all ORM models with:
- UUID primary keys
- Timestamp tracking (created_at, updated_at)
- Common utilities
"""

import re
from datetime import datetime
from typing import Any, ClassVar, Dict
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, func, inspect
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Naming convention for constraints (helps with migrations)
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models.

    Features:
    - Automatic table naming from class name
    - Dict conversion for serialization
    - Nice repr output
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Columns to include in repr
    __repr_attrs__: ClassVar[list[str]] = []
    __repr_max_length__: ClassVar[int] = 50

    @declared_attr.directive
    def __tablename__(cls) -> str:
        """Generate table name from class name.

        UserProfile -> user_profiles
        CompanionVersion -> companion_versions
        """
        name = cls.__name__
        # Split on capital letters
        words = re.findall(r"[A-Z][a-z0-9]*", name)
        # Join with underscores and lowercase
        table = "_".join(words).lower()
        # Pluralize (simple version)
        if not table.endswith("s"):
            table += "s"
        return table

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary.

        Includes only column values, not relationships.
        """
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def update(self, **kwargs) -> "Base":
        """Update model attributes from keyword arguments."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    @property
    def _id_str(self) -> str:
        """Get string representation of primary key."""
        mapper = inspect(self.__class__)
        pk_cols = mapper.primary_key
        values = [getattr(self, col.name, None) for col in pk_cols]
        if all(v is not None for v in values):
            return "-".join(str(v) for v in values) if len(values) > 1 else str(values[0])
        return "None"

    def __repr__(self) -> str:
        """Generate readable repr string."""
        id_str = f"#{self._id_str}" if self._id_str != "None" else ""

        # Build attribute string
        attrs = []
        for key in self.__repr_attrs__:
            if hasattr(self, key):
                value = getattr(self, key)
                if isinstance(value, str) and len(value) > self.__repr_max_length__:
                    value = value[: self.__repr_max_length__] + "..."
                attrs.append(f"{key}={value!r}")

        attr_str = f" {', '.join(attrs)}" if attrs else ""
        return f"<{self.__class__.__name__} {id_str}{attr_str}>"


class UUIDMixin:
    """Mixin that adds UUID primary key."""

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BaseModel(Base, UUIDMixin, TimestampMixin):
    """Abstract base model with UUID primary key and timestamps.

    Use this as the base for most models:

        class User(BaseModel):
            __tablename__ = "users"

            email: Mapped[str] = mapped_column(String(255), unique=True)
            name: Mapped[str] = mapped_column(String(100))
    """

    __abstract__ = True
