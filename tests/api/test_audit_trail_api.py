"""HTTP contract tests for the tool-invocation audit trail.

``GET /agents/{agent_id}/tool-invocations`` is the read side of the governed tool
gateway. The gateway writes a row for every attempt, refusals included, but a
record nobody can read is not an audit trail. These tests pin the properties an
auditor depends on:

* the trail is complete — a refusal is as visible as a success, because "the
  agent never tried" and "the agent was blocked" are different findings;
* the trail belongs to the owning human, so a non-owner sees ``404`` and cannot
  use the endpoint to probe for another user's agents;
* the trail is evidence, not a copy — arguments come back as a digest, never as
  the note content that was read.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def _create_note(client: TestClient, title: str = "Secret", content: str = "do not leak") -> str:
    response = client.post(
        "/api/v1/notes", headers=auth_headers(ALICE), json={"title": title, "content": content}
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


class TestAuditTrailHttp:
    def test_owner_reads_a_successful_invocation(self, client: TestClient) -> None:
        note_id = _create_note(client)
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        assert (
            _invoke(client, secret, "k-1", tool=READ, arguments={"note_id": note_id}).status_code
            == 200
        )

        response = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations", headers=auth_headers(ALICE)
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 1
        row = body["items"][0]
        assert row["tool"] == READ
        assert row["status"] == "succeeded"
        assert row["agent_id"] == agent_id
        assert row["resource_ref"] == f"note:{note_id}"
        assert row["idempotency_key"] == "k-1"
        assert row["completed_at"] is not None

    def test_the_trail_keeps_refusals_not_only_successes(self, client: TestClient) -> None:
        # The agent holds READ but asks for a write capability. The gateway denies
        # the call; the trail must still show that the attempt happened.
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        denied = _invoke(
            client, secret, "k-denied", tool=CREATE, arguments={"title": "x", "content": "y"}
        )
        assert denied.status_code in {403, 401}, denied.text

        response = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations", headers=auth_headers(ALICE)
        )

        assert response.status_code == 200, response.text
        rows = response.json()["items"]
        assert [row["status"] for row in rows] == ["denied"]
        assert rows[0]["outcome"] is not None

    def test_arguments_are_a_digest_not_the_content(self, client: TestClient) -> None:
        note_id = _create_note(client, content="a very private sentence")
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        _invoke(client, secret, "k-digest", tool=READ, arguments={"note_id": note_id})

        response = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations", headers=auth_headers(ALICE)
        )

        row = response.json()["items"][0]
        assert row["arguments_digest"]
        assert "a very private sentence" not in response.text
        assert note_id not in row["arguments_digest"]

    def test_a_non_owner_gets_404_not_403(self, client: TestClient) -> None:
        agent_id = _register_agent(client, READ)

        response = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations", headers=auth_headers(BOB)
        )

        assert response.status_code == 404, response.text

    def test_an_unknown_agent_id_is_indistinguishable_from_an_unowned_one(
        self, client: TestClient
    ) -> None:
        unowned = client.get(
            "/api/v1/agents/00000000-0000-0000-0000-000000000000/tool-invocations",
            headers=auth_headers(ALICE),
        )

        assert unowned.status_code == 404, unowned.text

    def test_paging_bounds_the_window(self, client: TestClient) -> None:
        note_id = _create_note(client)
        agent_id = _register_agent(client, READ)
        secret = _token_for(client, agent_id, READ)
        for index in range(3):
            _invoke(client, secret, f"k-page-{index}", tool=READ, arguments={"note_id": note_id})

        first = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations?limit=2&offset=0",
            headers=auth_headers(ALICE),
        )
        second = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations?limit=2&offset=2",
            headers=auth_headers(ALICE),
        )

        assert first.status_code == 200, first.text
        assert len(first.json()["items"]) == 2
        assert first.json()["meta"]["limit"] == 2
        assert len(second.json()["items"]) == 1

    def test_an_out_of_range_limit_is_rejected(self, client: TestClient) -> None:
        agent_id = _register_agent(client, READ)

        response = client.get(
            f"/api/v1/agents/{agent_id}/tool-invocations?limit=500",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 422, response.text

    def test_an_unauthenticated_caller_is_refused(self, client: TestClient) -> None:
        agent_id = _register_agent(client, READ)

        response = client.get(f"/api/v1/agents/{agent_id}/tool-invocations")

        assert response.status_code in {401, 403}, response.text
