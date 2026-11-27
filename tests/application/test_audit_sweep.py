"""The platform sweep finds breaks the owner-facing check cannot (NEX-042).

``GET /agents/{id}/.../verification`` verifies one agent, for its owner. That is
the wrong shape for the platform question — *is any chain in this deployment
broken* — because it can only answer for an agent someone already suspects, and
only for a caller who owns it. A tampered chain would stay invisible until its
owner happened to look, which is the opposite of how an integrity control should
behave.

The properties pinned here are the ones that make the sweep trustworthy:

* it reports a break it was not told about, for an agent the operator does not
  own (the whole point — the sweep is not owner-scoped);
* ``agents_total`` against ``agents_checked`` makes coverage falsifiable, so a
  capped run cannot masquerade as a clean bill of health;
* the gate refuses a non-operator before any repository is touched.

Tampering is applied through the database directly: that is the threat model, an
operator or attacker editing history behind the API's back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import ResourceNotFound
from notes_service.application.use_cases.audit_sweep import SweepAuditIntegrity
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.models import ToolInvocationRow
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock
from notes_service.infrastructure.security.operator_access_log import (
    InMemoryOperatorAccessLog,
)
from tests.application.grant_helpers import grants_from_scopes
from tests.conftest import ALICE, BOB, auth_headers

READ = "read_note"
CREATE = "create_note"
TOOL_GATEWAY = "tool-gateway"


def _principal(user_id: str) -> Principal:
    uid = UserId.parse(user_id)
    return Principal(
        user_id=uid,
        email=Email("user@example.com"),
        display_name="User",
        provider="demo",
        subject=str(uid),
    )


def _use_case(
    session_factory: sessionmaker[Session],
    *,
    operators: tuple[str, ...] = (ALICE,),
    max_agents: int = 500,
) -> SweepAuditIntegrity:
    return SweepAuditIntegrity(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        operator_gate=OperatorGate(
            grants=grants_from_scopes({UserId.parse(raw): frozenset({"*"}) for raw in operators}),
            access_log=InMemoryOperatorAccessLog(),
            clock=SystemClock(),
        ),
        max_agents=max_agents,
    )


def _register_agent(client: TestClient, *, owner: str = ALICE, name: str = "Auditor") -> str:
    response = client.post(
        "/api/v1/agents",
        headers=auth_headers(owner),
        json={
            "display_name": name,
            "runtime_identity": f"svc-{name.lower()}",
            "capabilities": [READ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _secret(client: TestClient, agent_id: str, *, owner: str = ALICE) -> str:
    delegation = client.post(
        f"/api/v1/agents/{agent_id}/delegations",
        headers=auth_headers(owner),
        json={"capabilities": [READ]},
    )
    assert delegation.status_code == 201, delegation.text
    token = client.post(
        f"/api/v1/agents/delegations/{delegation.json()['id']}/tokens",
        headers=auth_headers(owner),
        json={"capabilities": [READ], "audience": TOOL_GATEWAY, "ttl_seconds": 300},
    )
    assert token.status_code == 201, token.text
    return token.json()["secret"]


def _invoke(client: TestClient, secret: str, key: str, **body: object) -> None:  # type: ignore[no-untyped-def]
    payload: dict[str, object] = {"idempotency_key": key}
    payload.update(body)
    response = client.post(
        "/api/v1/agents/tools/invoke",
        headers={"Authorization": f"Bearer {secret}"},
        json=payload,
    )
    assert response.status_code in {200, 401, 403}, response.text


def _note(client: TestClient, *, owner: str = ALICE) -> str:
    response = client.post(
        "/api/v1/notes",
        headers=auth_headers(owner),
        json={"title": "Secret", "content": "body"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _agent_with_one_denial(client: TestClient, *, owner: str = ALICE) -> str:
    """An agent whose single trail entry is a *denied* attempt — the cheapest forgery target."""
    agent_id = _register_agent(client, owner=owner)
    secret = _secret(client, agent_id, owner=owner)
    _invoke(client, secret, "k-denied", tool=CREATE, arguments={"title": "x", "content": "y"})
    return agent_id


class TestTheGateFailsClosed:
    def test_a_non_operator_is_refused_as_not_found(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with pytest.raises(ResourceNotFound):
            _use_case(session_factory).run(_principal(BOB))

    def test_an_empty_allowlist_admits_nobody(self, session_factory: sessionmaker[Session]) -> None:
        """The default deployment grants the sweep to no one, not to everyone."""
        with pytest.raises(ResourceNotFound):
            _use_case(session_factory, operators=()).run(_principal(ALICE))


class TestTheSweepReportsWhatItFinds:
    def test_a_clean_deployment_verifies_with_full_coverage(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        note_id = _note(client)
        agent_id = _register_agent(client)
        _invoke(
            client, _secret(client, agent_id), "k-ok", tool=READ, arguments={"note_id": note_id}
        )

        report = _use_case(session_factory).run(_principal(ALICE))

        assert report.verified is True
        assert report.broken == ()
        assert report.agents_total == 1
        assert report.agents_checked == 1
        assert report.truncated is False

    def test_a_tampered_chain_is_reported_with_the_agent_that_owns_it(
        self,
        client: TestClient,
        session_factory: sessionmaker[Session],
    ) -> None:
        # The sweep runs as ALICE but the broken agent belongs to BOB: an
        # integrity control that only sees the caller's own agents would miss
        # exactly the case it exists for.
        agent_id = _agent_with_one_denial(client, owner=BOB)

        with session_factory() as session:
            session.execute(
                update(ToolInvocationRow)
                .where(ToolInvocationRow.idempotency_key == "k-denied")
                .values(status="succeeded")
            )
            session.commit()

        report = _use_case(session_factory).run(_principal(ALICE))

        assert report.verified is False
        assert report.agents_checked == 1
        (finding,) = report.broken
        assert finding.agent_id == agent_id
        assert finding.owner_user_id == BOB
        # ``checked`` is what passed *before* the contradiction, and the
        # contradiction is the first entry — so nothing verified cleanly. A
        # finding that claimed to have checked the broken entry would be lying.
        assert finding.checked == 0
        assert finding.reason
        assert finding.first_break_sequence == 1

    def test_a_capped_sweep_is_never_verified(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        """Two intact agents, a cap of one: coverage is partial, so the answer is 'unknown'."""
        _register_agent(client, name="First")
        _register_agent(client, name="Second")

        report = _use_case(session_factory, max_agents=1).run(_principal(ALICE))

        assert report.agents_total == 2
        assert report.agents_checked == 1
        assert report.truncated is True
        assert report.broken == ()
        # The unexamined agent could be broken; a partial pass is not a pass.
        assert report.verified is False
