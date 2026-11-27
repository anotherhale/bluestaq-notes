"""Shared pytest fixtures.

The default test database is a single shared in-memory SQLite database per test
(``StaticPool`` keeps one connection so all sessions see the same schema). A
``--postgresql`` opt-in reuses the same fixtures against a real PostgreSQL
server when ``TEST_POSTGRESQL_URL`` is set.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from notes_service.bootstrap.config import DatabaseProvider, Environment, Settings
from notes_service.infrastructure.db.base import Base
from notes_service.infrastructure.db.seed import DEMO_USERS, seed_demo_users

# Stable demo user ids used throughout the tests and README.
ALICE = DEMO_USERS[0].id
BOB = DEMO_USERS[1].id
CAROL = DEMO_USERS[2].id
DAVE = DEMO_USERS[3].id  # suspended


def _sqlite_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment=Environment.TESTING,
        database_provider=DatabaseProvider.SQLITE,
        database_url="sqlite://",
        auth_demo_enabled=True,
        rate_limit_enabled=False,
        log_json=False,
        log_level="WARNING",
        cors_allow_origins=["http://localhost:3000"],
    )


@pytest.fixture
def engine(settings: Settings, request: pytest.FixtureRequest) -> Iterator[Engine]:
    if request.node.get_closest_marker("postgresql"):
        url = os.environ.get("TEST_POSTGRESQL_URL")
        if not url:
            pytest.skip("TEST_POSTGRESQL_URL is not set; skipping PostgreSQL integration test")
        engine = create_engine(url, future=True)
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        yield engine
        Base.metadata.drop_all(engine)
        engine.dispose()
        return

    engine = _sqlite_engine()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def seeded(engine: Engine, session_factory: sessionmaker[Session]) -> None:
    """Seed the demo users into the test database."""
    with session_factory() as session:
        seed_demo_users(session)
        session.commit()


@pytest.fixture
def app(
    settings: Settings,
    engine: Engine,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Build the app wired to the test engine, with demo users seeded."""
    from notes_service.bootstrap import app as app_module
    from notes_service.bootstrap.container import build_container as real_build

    def _builder(_settings: Settings) -> object:
        return real_build(_settings, engine=engine)

    monkeypatch.setattr(app_module, "build_container", _builder)
    application = app_module.create_app(settings)
    with session_factory() as session:
        seed_demo_users(session)
        session.commit()
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def auth_headers(user_id: str) -> dict[str, str]:
    """Build demo authentication headers for a seeded user."""
    return {"X-Demo-User-ID": user_id}


def operator_grant(user_id: str, *operations: str) -> str:
    """Build one ``<user-id>:<scope>`` entry for ``ops_operator_user_ids``.

    A helper rather than a literal so a test that widens a grant to ``"*"`` has to
    say so, instead of the widening being invisible in a comma-joined string.
    """
    if not operations:
        raise ValueError("a grant must name at least one operation, or '*'")
    return ",".join(f"{user_id}:{operation}" for operation in operations)


__all__ = ["ALICE", "BOB", "CAROL", "DAVE", "auth_headers", "operator_grant"]
