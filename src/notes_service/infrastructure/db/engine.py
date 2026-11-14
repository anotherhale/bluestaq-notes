"""Engine and session-factory construction for SQLite and PostgreSQL.

This module owns the only database-specific knowledge in the codebase. Both
providers share the same declarative models; only connection settings differ.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from notes_service.bootstrap.config import DatabaseProvider, Settings


def create_db_engine(settings: Settings) -> Engine:
    """Build an :class:`Engine` for the configured provider."""
    url = settings.database_url
    kwargs: dict[str, object] = {"pool_pre_ping": True, "future": True}

    if settings.database_provider is DatabaseProvider.SQLITE:
        # SQLite: allow use from the threadpool FastAPI runs sync work in.
        kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, **kwargs)
        _enable_sqlite_foreign_keys(engine)
        return engine

    # PostgreSQL (psycopg 3).
    engine = create_engine(url, **kwargs)
    return engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite disables FK enforcement by default; turn it on per connection.

    Without this, ON DELETE CASCADE and FK constraints are silently ignored,
    which would let orphaned permission rows survive note deletion.
    """

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a session that commits on success and rolls back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
