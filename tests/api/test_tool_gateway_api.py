"""HTTP contract tests for the governed tool gateway (``POST /agents/tools/invoke``).

These pin the *edge* behaviour that the application-layer gateway tests cannot
see, because the edge is where a caller could otherwise substitute a weaker
credential or a wider authority:

* the credential is the agent token in ``Authorization`` — the demo-identity
  header grants nothing here, so a human cannot drive the gateway;
* audience is enforced, so a Notes-API token is refused even though it is a
  valid token;
* a token's capability set bounds the tools it may reach;
* a retried call replays its recorded outcome instead of repeating a side
  effect, and says so.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, auth_headers

READ = "read_note"
CREATE = "create_note"
NOTES_API = "notes-api"
TOOL_GATEWAY = "tool-gateway"


def _agent_with_token(
    client: TestClient,
    *capabilities: str,
    audience: str = TOOL_GATEWAY,
) -> str:
    """Register an agent, delegate ``capabilities``, and mint one token."""
    registered = client.post(
        "/api/v1/agents",
        headers=auth_headers(ALICE),
        json={
            "display_name": "Summarizer",
            "runtime_identity": "svc-summarizer",
            "capabilities": list(capabilities),
        },
    )
    assert registered.status_code == 201, registered.text
    agent_id = registered.json()["id"]

    delegation = client.post(
        f"/api/v1/agents/{agent_id}/delegations",
        headers=auth_headers(ALICE),
        json={"capabilities": list(capabilities)},
    )
    assert delegation.status_code == 201, delegation.text

    token = client.post(
        f"/api/v1/agents/delegations/{delegation.json()['id']}/tokens",
        headers=auth_headers(ALICE),
        json={"capabilities": list(capabilities), "audience": audience, "ttl_seconds": 300},
    )
    assert token.status_code == 201, token.text
    return token.json()["secret"]


def _create_note(client: TestClient, title: str = "Agent note", content: str = "hello") -> str:
    response = client.post(
        "/api/v1/notes", headers=auth_headers(ALICE), json={"title": title, "content": content}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _invoke(client: TestClient, secret: str, **body: object):  # type: ignore[no-untyped-def]
    payload: dict[str, object] = {"idempotency_key": "key-1"}
    payload.update(body)
    return client.post(
        "/api/v1/agents/tools/invoke",
        headers={"Authorization": f"Bearer {secret}"},
        json=payload,
    )


class TestToolGatewayHttp:
    def test_a_delegated_read_returns_the_audited_outcome(self, client: TestClient) -> None:
        note_id = _create_note(client)
        secret = _agent_with_token(client, READ)

        response = _invoke(client, secret, tool="read_note", arguments={"note_id": note_id})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tool"] == "read_note"
        assert body["status"] == "succeeded"
        assert body["replayed"] is False
        assert body["resource_ref"] == f"note:{note_id}"
        assert body["output"]["note"]["id"] == note_id

    def test_the_demo_identity_header_is_not_authority_here(self, client: TestClient) -> None:
        # A human principal is not an agent token. Presenting one must fail
        # closed with 401 rather than invoking a tool on that human's behalf.
        response = client.post(
            "/api/v1/agents/tools/invoke",
            headers=auth_headers(ALICE),
            json={"tool": "search_notes", "arguments": {}, "idempotency_key": "k-human"},
        )

        assert response.status_code == 401, response.text

    def test_a_malformed_authorization_header_is_401(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/agents/tools/invoke",
            headers={"Authorization": "Basic abc123"},
            json={"tool": "search_notes", "arguments": {}, "idempotency_key": "k-basic"},
        )

        assert response.status_code == 401, response.text

    def test_a_notes_api_token_is_refused_by_the_gateway(self, client: TestClient) -> None:
        secret = _agent_with_token(client, READ, audience=NOTES_API)

        response = _invoke(client, secret, tool="search_notes", arguments={})

        assert response.status_code == 403, response.text

    def test_a_tool_outside_the_token_capabilities_is_403(self, client: TestClient) -> None:
        secret = _agent_with_token(client, READ)

        response = _invoke(
            client, secret, tool="create_note", arguments={"title": "T", "content": "C"}
        )

        assert response.status_code == 403, response.text

    def test_an_unknown_tool_is_refused_without_a_handler(self, client: TestClient) -> None:
        secret = _agent_with_token(client, READ)

        response = _invoke(client, secret, tool="drop_everything", arguments={})

        assert response.status_code == 422, response.text

    def test_a_retry_replays_the_recorded_outcome(self, client: TestClient) -> None:
        note_id = _create_note(client)
        secret = _agent_with_token(client, READ)

        first = _invoke(client, secret, tool="read_note", arguments={"note_id": note_id})
        second = _invoke(client, secret, tool="read_note", arguments={"note_id": note_id})

        assert first.status_code == second.status_code == 200
        assert second.json()["replayed"] is True
        assert second.json()["id"] == first.json()["id"]

    def test_a_reused_key_with_different_arguments_is_a_conflict(self, client: TestClient) -> None:
        first_note = _create_note(client, title="One")
        second_note = _create_note(client, title="Two")
        secret = _agent_with_token(client, READ)

        assert (
            _invoke(client, secret, tool="read_note", arguments={"note_id": first_note}).status_code
            == 200
        )
        response = _invoke(client, secret, tool="read_note", arguments={"note_id": second_note})

        assert response.status_code == 409, response.text

    def test_the_agent_token_is_never_echoed(self, client: TestClient) -> None:
        secret = _agent_with_token(client, READ)

        response = _invoke(client, secret, tool="search_notes", arguments={})

        assert secret not in response.text
