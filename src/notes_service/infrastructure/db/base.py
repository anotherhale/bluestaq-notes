"""Declarative base and shared column helpers for ORM models.

The ORM layer is deliberately kept separate from the domain model. Domain
objects are mapped to/from these rows by the repository adapters; the domain
never sees a ``DeclarativeBase``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# A stable naming convention makes Alembic autogenerate deterministic and gives
# constraints predictable names for migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp for column defaults."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp column stored as naive UTC and always read back tz-aware UTC.

    The underlying column is a plain ``TIMESTAMP`` (no timezone), which both
    SQLite and PostgreSQL support identically. Values are normalised to naive
    UTC on write and re-labelled as UTC on read. Without this, PostgreSQL would
    silently convert an aware datetime into the session's local wall-clock time
    on a timezone-less column, shifting stored instants by the server's UTC
    offset; SQLite would return naive datetimes that serialise differently from
    freshly created (aware) ones.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return aware.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Native ``Uuid`` works on both SQLite (stored as CHAR(32)) and PostgreSQL
# (native UUID), so the same models serve both providers unchanged.
UUIDType = Uuid(as_uuid=True)


__all__ = ["Base", "Mapped", "UTCDateTime", "UUIDType", "mapped_column", "utcnow"]
