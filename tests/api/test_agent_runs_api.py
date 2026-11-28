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
    runtime_identity: str = "svc-runner",
) -> tuple[str, str]:
    """Register an agent, delegate ``capabilities``, and mint one tool-gateway token."""
    registered = client.post(
        "/api/v1/agents",
        headers=auth_headers(owner),
        json={
            "display_name": "Runner",
            "runtime_identity": runtime_identity,
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


def test_listing_reports_the_whole_history_not_the_window(client: TestClient) -> None:
    """``meta.total`` counts every run, so a page cannot pass itself off as the answer.

    Before NEX-090 the store capped the listing at 50 rows and the route reported
    ``total=len(items)``, so an agent with 60 runs looked like it had exactly 50
    and the other 10 were unreachable: there was no ``limit``/``offset`` to page
    with. An auditor reading ``meta.total`` had no way to tell a complete history
    from a truncated one.
    """
    agent_id, secret = _agent_with_token(client, CREATE)
    for index in range(3):
        planned = _plan(
            client,
            secret,
            steps=[{"tool": CREATE, "arguments": {"title": f"h{index}", "content": "b"}}],
        )
        assert planned.status_code == 201, planned.text

    body = client.get(
        "/api/v1/agent-runs",
        headers=auth_headers(ALICE),
        params={"agent_id": agent_id, "limit": 2},
    ).json()
    assert len(body["items"]) == 2, body
    assert body["meta"]["total"] == 3, body
    assert body["meta"]["limit"] == 2, body
    assert body["meta"]["offset"] == 0, body


def test_paging_reaches_past_the_default_window(client: TestClient) -> None:
    """Offset walks the rest of the history, so no run is unreachable.

    This is what the missing paging cost: an agent with more runs than one window
    could not read the remainder through the API at all, not even one at a time.
    """
    agent_id, secret = _agent_with_token(client, CREATE)
    planned = []
    for index in range(3):
        response = _plan(
            client,
            secret,
            steps=[{"tool": CREATE, "arguments": {"title": f"p{index}", "content": "b"}}],
        )
        assert response.status_code == 201, response.text
        planned.append(response.json()["id"])

    first = client.get(
        "/api/v1/agent-runs",
        headers=auth_headers(ALICE),
        params={"agent_id": agent_id, "limit": 2, "offset": 0},
    ).json()
    second = client.get(
        "/api/v1/agent-runs",
        headers=auth_headers(ALICE),
        params={"agent_id": agent_id, "limit": 2, "offset": 2},
    ).json()

    seen = [run["id"] for run in first["items"]] + [run["id"] for run in second["items"]]
    assert seen == list(reversed(planned)), "newest first, and every run once"
    assert second["meta"]["total"] == 3, "the total describes the set, not the page"
    assert second["meta"]["offset"] == 2


def test_the_total_counts_only_this_agents_runs(client: TestClient) -> None:
    """A total is scoped to the agent, so it cannot disclose another agent's activity."""
    busy_id, busy_secret = _agent_with_token(client, CREATE)
    quiet_id, _quiet_secret = _agent_with_token(client, CREATE, runtime_identity="svc-runner-quiet")
    for index in range(2):
        assert (
            _plan(
                client,
                busy_secret,
                steps=[{"tool": CREATE, "arguments": {"title": f"b{index}", "content": "x"}}],
            ).status_code
            == 201
        )

    busy = client.get(
        "/api/v1/agent-runs", headers=auth_headers(ALICE), params={"agent_id": busy_id}
    ).json()
    quiet = client.get(
        "/api/v1/agent-runs", headers=auth_headers(ALICE), params={"agent_id": quiet_id}
    ).json()

    assert busy["meta"]["total"] == 2
    assert quiet["meta"]["total"] == 0
    assert quiet["items"] == []


def test_resumable_listing_is_never_windowed(client: TestClient) -> None:
    """The recovery set is complete even when a window would truncate it.

    ``resumable_only`` is what a client resumes interrupted work from. Applying
    ``limit`` to it would silently abandon runs, so it is exempt from paging and
    its total is the size of the whole recovery set.
    """
    agent_id, secret = _agent_with_token(client, CREATE)
    for index in range(3):
        assert (
            _plan(
                client,
                secret,
                steps=[{"tool": CREATE, "arguments": {"title": f"r{index}", "content": "b"}}],
            ).status_code
            == 201
        )

    body = client.get(
        "/api/v1/agent-runs",
        headers=auth_headers(ALICE),
        params={"agent_id": agent_id, "resumable_only": True, "limit": 1},
    ).json()

    assert len(body["items"]) == 3, "all pending runs, despite limit=1"
    assert body["meta"]["total"] == 3
    assert body["meta"]["limit"] == 3, "meta describes the set, not the ignored limit"
    assert body["meta"]["offset"] == 0


def test_paging_parameters_are_validated(client: TestClient) -> None:
    """A nonsensical window is a 422, not a silently different query.

    ``le=200`` caps how much one request can read, and ``ge=1`` rejects
    ``limit=0``, which would otherwise return an empty page that looks like an
    empty history. ``offset`` accepts ``0`` because the first page has no offset.
    """
    agent_id, _secret = _agent_with_token(client, CREATE)
    headers = auth_headers(ALICE)

    for params in (
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
    ):
        response = client.get(
            "/api/v1/agent-runs", headers=headers, params={"agent_id": agent_id, **params}
        )
        assert response.status_code == 422, (params, response.text)

    ok = client.get(
        "/api/v1/agent-runs",
        headers=headers,
        params={"agent_id": agent_id, "limit": 200, "offset": 0},
    )
    assert ok.status_code == 200, ok.text


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
