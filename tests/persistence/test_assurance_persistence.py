"""Authentication assurance survives the database round trip (NEX-058).

The ``step-up`` obligation is only enforceable if the assurance a human proved at
login is still readable when their delegation is used, possibly in another process
and long after the login request. That makes this a *persistence* property, not a
domain one: the domain value object and the enforcement logic can both be correct
while a column that never gets written silently degrades every delegation to
single-factor.

So each test writes through one repository and reads back through a second one
built over the same session factory, mirroring the operator-grant tests. Asserting
against the same instance that wrote the row would pass even if ``add`` dropped the
field.

The absent-value case matters as much as the stored one: legacy rows predate the
column, and the only safe reading of "no recorded assurance" is the weakest level.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.agent.delegation import DelegatedAuthorization
from notes_service.domain.agent.entities import AgentIdentity
from notes_service.domain.agent.value_objects import (
    Capability,
    DelegatedAuthorizationId,
    DelegationStatus,
)
from notes_service.domain.identity.assurance import AuthenticationAssurance
from notes_service.domain.identity.session import UserSession
from notes_service.infrastructure.db.agent_repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyDelegationRepository,
)
from notes_service.infrastructure.db.repositories import SqlAlchemyUserSessionRepository
from tests.conftest import ALICE

pytestmark = pytest.mark.usefixtures("seeded")

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _register_agent(factory: sessionmaker[Session]) -> AgentIdentity:
    agent = AgentIdentity.register(
        display_name="Assurance Probe",
        owner_user_id=ALICE,
        runtime_identity="svc-assurance-probe",
        capabilities={Capability.READ_NOTE},
    )
    writer = SqlAlchemyAgentRepository(factory())
    writer.add(agent)
    writer._session.commit()
    return agent


def _restart(factory: sessionmaker[Session]) -> SqlAlchemyUserSessionRepository:
    return SqlAlchemyUserSessionRepository(factory())


def _restart_delegations(factory: sessionmaker[Session]) -> SqlAlchemyDelegationRepository:
    return SqlAlchemyDelegationRepository(factory())


def _session(assurance: AuthenticationAssurance) -> UserSession:
    return UserSession.establish(
        user_id=ALICE,
        provider="test-idp",
        subject="alice-subject",
        token="raw-cookie-secret",
        idle_ttl=timedelta(hours=1),
        absolute_ttl=timedelta(hours=8),
        now=NOW,
        assurance=assurance,
    )


@pytest.mark.parametrize(
    "assurance",
    [AuthenticationAssurance.SINGLE_FACTOR, AuthenticationAssurance.MULTI_FACTOR],
)
def test_session_assurance_round_trips(
    session_factory: sessionmaker[Session], assurance: AuthenticationAssurance
) -> None:
    writer = _restart(session_factory)
    session = _session(assurance)
    writer.add(session)
    writer._session.commit()

    reader = _restart(session_factory)
    reloaded = reader.get_by_token_hash(session.token_hash)

    assert reloaded is not None
    assert reloaded.assurance is assurance


def test_legacy_session_without_assurance_reads_as_single_factor(
    session_factory: sessionmaker[Session], engine: Engine
) -> None:
    writer = _restart(session_factory)
    session = _session(AuthenticationAssurance.MULTI_FACTOR)
    writer.add(session)
    writer._session.commit()

    # Simulate a row written before the column existed.
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE user_sessions SET assurance = NULL WHERE token_hash = :hash"),
            {"hash": session.token_hash},
        )

    reader = _restart(session_factory)
    reloaded = reader.get_by_token_hash(session.token_hash)

    assert reloaded is not None
    assert reloaded.assurance is AuthenticationAssurance.SINGLE_FACTOR


@pytest.mark.parametrize(
    "assurance",
    [AuthenticationAssurance.SINGLE_FACTOR, AuthenticationAssurance.MULTI_FACTOR],
)
def test_delegation_assurance_round_trips(
    session_factory: sessionmaker[Session], assurance: AuthenticationAssurance
) -> None:
    agent = _register_agent(session_factory)
    delegation = DelegatedAuthorization(
        id=DelegatedAuthorizationId.new(),
        agent_id=agent.id,
        delegator_user_id=ALICE,
        capabilities=frozenset({Capability.READ_NOTE}),
        created_at=NOW,
        assurance=assurance,
    )

    writer = _restart_delegations(session_factory)
    writer.add(delegation)
    writer._session.commit()

    reader = _restart_delegations(session_factory)
    reloaded = reader.get(delegation.id)

    assert reloaded is not None
    assert reloaded.assurance is assurance
    assert reloaded.status is DelegationStatus.ACTIVE
