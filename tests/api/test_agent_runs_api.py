"""HTTP contract tests for durable runs (``/api/v1/agent-runs``).

The application-layer tests prove the run semantics. These pin what only the edge
can get wrong: *which credential opens which endpoint*. Durable runs have two
authorities — the agent that plans and advances, and the human who owns the agent
and must rule on an interrupted step — and a router that let either stand in for
the other would quietly hand an agent the power to settle its own ambiguity.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers

CREATE = "create_note"
READ = "read_note"
TOOL_GATEWAY = "tool-gateway"


def _agent_with_token(
    client: TestClient,
    *capabilities: str,
    owner: dict[str, str] = ALICE,
) -> tuple[str, str]:
    """Register an agent, delegate ``capabilities``, and mint one tool-gateway token."""
    registered = client.post(
        "/api/v1/agents",
        headers=auth_headers(owner),
        json={
            "display_name": "Runner",
            "runtime_identity": "svc-runner",
            "capabilities": list(capabilities),
        },
    )
    assert registered.status_code == 201, registered.text
    agent_id = registered.json()["id"]

    delegation = client.post(
        f"/api/v1/agents/{agent_id}/delegations",
        headers=auth_headers(owner),
        json={"capabilities": list(capabilities)},
    )
    assert delegation.status_code == 201, delegation.text

    token = client.post(
        f"/api/v1/agents/delegations/{delegation.json()['id']}/tokens",
        headers=auth_headers(owner),
        json={"capabilities": list(capabilities), "audience": TOOL_GATEWAY, "ttl_seconds": 300},
    )
    assert token.status_code == 201, token.text
    return agent_id, token.json()["secret"]


def _create_note(client: TestClient, title: str) -> str:
    response = client.post(
        "/api/v1/notes", headers=auth_headers(ALICE), json={"title": title, "content": "body"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _plan(client: TestClient, secret: str, **body: object):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/agent-runs",
        headers={"Authorization": f"Bearer {secret}"},
        json=body,
    )


def test_plan_run_requires_the_agent_token_not_a_demo_identity(client: TestClient) -> None:
    """A human demo identity cannot plan a run: planning is the agent's authority."""
    response = client.post(
        "/api/v1/agent-runs",
        headers=auth_headers(ALICE),
        json={"steps": [{"tool": CREATE, "arguments": {"title": "t", "content": "c"}}]},
    )
    assert response.status_code == 401, response.text


def test_plan_run_refuses_a_step_the_token_cannot_reach(client: TestClient) -> None:
    """A plan naming a tool outside the token's capability set is refused, not queued."""
    _agent_id, secret = _agent_with_token(client, READ)

    response = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "t", "content": "c"}}]
    )

    assert response.status_code == 403, response.text


def test_plan_and_advance_execute_steps_and_complete(client: TestClient) -> None:
    """The full slice over HTTP: plan, advance, read back a completed run."""
    agent_id, secret = _agent_with_token(client, CREATE, READ)

    planned = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "from run", "content": "b"}}]
    )
    assert planned.status_code == 201, planned.text
    run_id = planned.json()["id"]
    assert planned.json()["agent_id"] == agent_id
    assert planned.json()["status"] == "pending"

    advanced = client.post(
        f"/api/v1/agent-runs/{run_id}/advance",
        headers={"Authorization": f"Bearer {secret}"},
        json={"max_steps": 10},
    )
    assert advanced.status_code == 200, advanced.text
    body = advanced.json()
    assert body["run"]["status"] == "completed"
    assert body["halted_reason"] is None
    assert [outcome["tool"] for outcome in body["outcomes"]] == [CREATE]

    fetched = client.get(f"/api/v1/agent-runs/{run_id}", headers=auth_headers(ALICE))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "completed"
    assert fetched.json()["steps"][0]["status"] == "succeeded"


def test_step_capability_comes_from_the_catalog_not_the_request(client: TestClient) -> None:
    """A caller cannot declare a weaker capability than the tool actually needs."""
    _agent_id, secret = _agent_with_token(client, CREATE)

    response = _plan(
        client,
        secret,
        steps=[
            {
                "tool": CREATE,
                "arguments": {"title": "t", "content": "c"},
                "capability": "read_note",
            }
        ],
    )
    assert response.status_code == 422, response.text

    accepted = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "t", "content": "c"}}]
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["steps"][0]["capability"] == CREATE


def test_run_is_invisible_to_a_non_owner(client: TestClient) -> None:
    """Runs are owner-scoped; another user gets 404, not 403, so existence stays hidden."""
    _agent_id, secret = _agent_with_token(client, CREATE)
    planned = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "t", "content": "c"}}]
    )
    assert planned.status_code == 201, planned.text
    run_id = planned.json()["id"]

    response = client.get(f"/api/v1/agent-runs/{run_id}", headers=auth_headers(BOB))
    assert response.status_code == 404, response.text


def test_an_agent_cannot_settle_its_own_interrupted_step(client: TestClient) -> None:
    """Resolving an ambiguous step is the owner's call; an agent token must not open it."""
    _agent_id, secret = _agent_with_token(client, CREATE)
    planned = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "t", "content": "c"}}]
    )
    run_id = planned.json()["id"]

    response = client.post(
        f"/api/v1/agent-runs/{run_id}/resolve",
        headers={"Authorization": f"Bearer {secret}"},
        json={"effect_landed": True},
    )
    assert response.status_code == 401, response.text


def test_abort_is_owner_scoped_and_terminal(client: TestClient) -> None:
    """The owner can abort; the run stops and cannot then be advanced."""
    _agent_id, secret = _agent_with_token(client, CREATE)
    planned = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "t", "content": "c"}}]
    )
    run_id = planned.json()["id"]

    other = client.post(f"/api/v1/agent-runs/{run_id}/abort", headers=auth_headers(BOB), json={})
    assert other.status_code == 404, other.text

    aborted = client.post(
        f"/api/v1/agent-runs/{run_id}/abort",
        headers=auth_headers(ALICE),
        json={"reason": "no longer needed"},
    )
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["status"] == "aborted"

    advanced = client.post(
        f"/api/v1/agent-runs/{run_id}/advance",
        headers={"Authorization": f"Bearer {secret}"},
        json={"max_steps": 10},
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["run"]["status"] == "aborted"
    assert advanced.json()["outcomes"] == []


def test_listing_is_scoped_to_the_callers_agent(client: TestClient) -> None:
    """Listing runs takes an agent id, but only for an agent the caller owns."""
    agent_id, secret = _agent_with_token(client, CREATE)
    planned = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "t", "content": "c"}}]
    )
    assert planned.status_code == 201, planned.text

    owned = client.get(
        "/api/v1/agent-runs", headers=auth_headers(ALICE), params={"agent_id": agent_id}
    )
    assert owned.status_code == 200, owned.text
    assert [run["id"] for run in owned.json()["items"]] == [planned.json()["id"]]
    assert owned.json()["meta"]["total"] == 1

    foreign = client.get(
        "/api/v1/agent-runs", headers=auth_headers(BOB), params={"agent_id": agent_id}
    )
    assert foreign.status_code == 404, foreign.text


def test_advance_is_idempotent_on_a_completed_run(client: TestClient) -> None:
    """Advancing a finished run reports it as finished and executes nothing more."""
    _agent_id, secret = _agent_with_token(client, CREATE)
    planned = _plan(
        client, secret, steps=[{"tool": CREATE, "arguments": {"title": "once", "content": "b"}}]
    )
    run_id = planned.json()["id"]

    first = client.post(
        f"/api/v1/agent-runs/{run_id}/advance",
        headers={"Authorization": f"Bearer {secret}"},
        json={"max_steps": 10},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["outcomes"]) == 1

    second = client.post(
        f"/api/v1/agent-runs/{run_id}/advance",
        headers={"Authorization": f"Bearer {secret}"},
        json={"max_steps": 10},
    )
    assert second.status_code == 200, second.text
    assert second.json()["outcomes"] == []
    assert second.json()["halted_reason"] == "run is completed"

    notes = client.get("/api/v1/notes", headers=auth_headers(ALICE))
    assert notes.status_code == 200, notes.text
    assert [note["title"] for note in notes.json()["items"]].count("once") == 1
