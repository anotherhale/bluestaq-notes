"""HTTP contract tests for audit-chain verification.

``GET /agents/{agent_id}/tool-invocations/verification`` is what turns the audit
trail from a log into evidence. The listing endpoint reports what the rows *say*;
this endpoint recomputes each row's seals and reports where they disagree with
what is stored.

The tests below pin the properties that make the verdict meaningful:

* an untouched trail verifies, and ``checked`` proves it was actually walked —
  a bare ``verified: true`` could otherwise come from checking nothing;
* a tampered row is *reported*, not raised: a break is a finding, and the caller
  needs to know where it starts;
* tampering is applied through the database directly, because that is exactly
  the threat model — someone with write access editing history behind the API's
  back. Tampering through the API would prove nothing;
* a non-owner gets ``404``, so the endpoint cannot be used to probe for another
  user's agents.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from notes_service.infrastructure.db.models import ToolInvocationRow
from tests.conftest import ALICE, BOB, auth_headers

READ = "read_note"
CREATE = "create_note"
TOOL_GATEWAY = "tool-gateway"


def _register_agent(client: TestClient, *capabilities: str) -> str:
    response = client.post(
        "/api/v1/agents",
        headers=auth_headers(ALICE),
        json={
            "display_name": "Auditor",
            "runtime_identity": "svc-auditor",
            "capabilities": list(capabilities),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _token_for(client: TestClient, agent_id: str, *capabilities: str) -> str:
    delegation = client.post(
        f"/api/v1/agents/{agent_id}/delegations",
        headers=auth_headers(ALICE),
        json={"capabilities": list(capabilities)},
    )
    assert delegation.status_code == 201, delegation.text
    token = client.post(
        f"/api/v1/agents/delegations/{delegation.json()['id']}/tokens",
        headers=auth_headers(ALICE),
        json={"capabilities": list(capabilities), "audience": TOOL_GATEWAY, "ttl_seconds": 300},
    )
    assert token.status_code == 201, token.text
    return token.json()["secret"]


def _create_note(client: TestClient) -> str:
    response = client.post(
        "/api/v1/notes",
        headers=auth_headers(ALICE),
        json={"title": "Secret", "content": "do not leak"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _invoke(client: TestClient, secret: str, key: str, **body: object):  # type: ignore[no-untyped-def]
    payload: dict[str, object] = {"idempotency_key": key}
    payload.update(body)
    return client.post(
        "/api/v1/agents/tools/invoke",
        headers={"Authorization": f"Bearer {secret}"},
        json=payload,
    )


def _verdict(client: TestClient, agent_id: str, user_id: str = ALICE):  # type: ignore[no-untyped-def]
    return client.get(
        f"/api/v1/agents/{agent_id}/tool-invocations/verification",
        headers=auth_headers(user_id),
    )


class TestChainVerificationHttp:
    def test_an_untouched_trail_verifies_and_says_how_much_it_checked(
        self, client: TestClient
    ) -> None:
        note_id = _create_note(client)
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        for index in range(3):
            assert (
                _invoke(
                    client, secret, f"k-{index}", tool=READ, arguments={"note_id": note_id}
                ).status_code
                == 200
            )

        response = _verdict(client, agent_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verified"] is True
        # ``checked`` is the evidence: a bare ``verified`` could mean "checked
        # nothing". Three invocations were recorded, so three must be walked.
        assert body["checked"] == 3
        assert body["first_break"] is None

    def test_refusals_are_part_of_the_verified_chain(self, client: TestClient) -> None:
        # A denied attempt is written in one step (no outcome sealing beyond the
        # verdict), a successful one in two. The chain must verify across both
        # shapes rather than only the happy path.
        note_id = _create_note(client)
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        assert (
            _invoke(client, secret, "k-ok", tool=READ, arguments={"note_id": note_id}).status_code
            == 200
        )
        denied = _invoke(
            client, secret, "k-denied", tool=CREATE, arguments={"title": "x", "content": "y"}
        )
        assert denied.status_code in {403, 401}, denied.text

        response = _verdict(client, agent_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verified"] is True
        assert body["checked"] == 2

    def test_an_empty_trail_passes_with_nothing_checked(self, client: TestClient) -> None:
        # A brand-new agent has done nothing wrong, and reporting an empty trail
        # as a failure would make every new agent look compromised.
        agent_id = _register_agent(client, READ)

        response = _verdict(client, agent_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verified"] is True
        assert body["checked"] == 0

    def test_editing_a_recorded_verdict_is_reported_not_raised(
        self,
        client: TestClient,
        session_factory: sessionmaker[Session],
    ) -> None:
        # The threat model: someone with database write access rewrites history
        # behind the API's back. Flipping a *denied* attempt's outcome to a
        # success must not go unnoticed — that is the exact edit an audit trail
        # exists to catch.
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        denied = _invoke(
            client, secret, "k-denied", tool=CREATE, arguments={"title": "x", "content": "y"}
        )
        assert denied.status_code in {403, 401}, denied.text

        with session_factory() as session:
            session.execute(
                update(ToolInvocationRow)
                .where(ToolInvocationRow.idempotency_key == "k-denied")
                .values(status="succeeded")
            )
            session.commit()

        response = _verdict(client, agent_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verified"] is False
        assert body["checked"] == 0
        assert body["first_break"] is not None
        assert body["first_break"]["sequence"] == 1
        assert "outcome hash" in body["first_break"]["reason"]

    def test_deleting_a_recorded_attempt_is_reported_as_a_sequence_gap(
        self,
        client: TestClient,
        session_factory: sessionmaker[Session],
    ) -> None:
        # Removing a row is the other half of the threat: an operator erases a
        # denied attempt rather than editing it. The surviving rows still chain,
        # but the numbering no longer starts where the chain says it should.
        note_id = _create_note(client)
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        for index in range(3):
            assert (
                _invoke(
                    client, secret, f"k-{index}", tool=READ, arguments={"note_id": note_id}
                ).status_code
                == 200
            )

        with session_factory() as session:
            session.execute(
                ToolInvocationRow.__table__.delete().where(ToolInvocationRow.chain_sequence == 2)
            )
            session.commit()

        response = _verdict(client, agent_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verified"] is False
        assert body["first_break"] is not None
        assert body["first_break"]["sequence"] == 3
        assert "sequence gap" in body["first_break"]["reason"]

    def test_a_non_owner_gets_404_not_403(self, client: TestClient) -> None:
        agent_id = _register_agent(client, READ)

        response = _verdict(client, agent_id, user_id=BOB)

        assert response.status_code == 404, response.text

    def test_the_chain_fields_are_visible_in_the_trail_listing(self, client: TestClient) -> None:
        # The listing and the verdict must agree on the same rows; if the seals
        # were not exposed, an auditor reading the trail could not see the data
        # the verdict is computed from.
        note_id = _create_note(client)
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        _invoke(client, secret, "k-1", tool=READ, arguments={"note_id": note_id})

        response = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations", headers=auth_headers(ALICE)
        )

        assert response.status_code == 200, response.text
        row = response.json()["items"][0]
        assert row["chain_sequence"] == 1
        assert row["previous_hash"] == "0" * 64
        assert len(row["intent_hash"]) == 64
        assert row["outcome_hash"] is not None
