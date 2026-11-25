"""A durable run must be able to *wait* for a human approval, not die asking.

The gateway refuses an approval-gated tool until a human has approved it. Inside a
durable run that refusal used to be recorded as a failed step, which marked the
whole run FAILED - a terminal state. The human's later approval was then stranded:
the run could never be advanced again, and the step's idempotency key was pinned
to the refusal, so even a retry would replay the denial instead of re-evaluating
it. Waiting on a human is not a failure, and only a run that survives the wait can
honour the approval the human granted.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, auth_headers

DELETE = "delete_note"
TOOL_GATEWAY = "tool-gateway"


def _agent_with_token(client: TestClient, *capabilities: str) -> tuple[str, str]:
    registered = client.post(
        "/api/v1/agents",
        headers=auth_headers(ALICE),
        json={
            "display_name": "Deleter",
            "runtime_identity": "svc-deleter",
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
        json={"capabilities": list(capabilities), "audience": TOOL_GATEWAY, "ttl_seconds": 300},
    )
    assert token.status_code == 201, token.text
    return agent_id, token.json()["secret"]


def _note(client: TestClient, title: str) -> str:
    response = client.post(
        "/api/v1/notes", headers=auth_headers(ALICE), json={"title": title, "content": "body"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _plan_delete(client: TestClient, secret: str, note_id: str) -> str:
    planned = client.post(
        "/api/v1/agent-runs",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "steps": [{"tool": DELETE, "arguments": {"note_id": note_id}, "resource_ref": note_id}]
        },
    )
    assert planned.status_code == 201, planned.text
    return planned.json()["id"]


def _advance(client: TestClient, secret: str, run_id: str) -> dict:
    response = client.post(
        f"/api/v1/agent-runs/{run_id}/advance",
        headers={"Authorization": f"Bearer {secret}"},
        json={"max_steps": 10},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_an_approval_gated_step_pauses_the_run_instead_of_failing_it(
    client: TestClient,
) -> None:
    """Advancing without an approval pauses the run; it must not become terminal."""
    agent_id, secret = _agent_with_token(client, DELETE)
    note_id = _note(client, "gate me")
    run_id = _plan_delete(client, secret, note_id)

    body = _advance(client, secret, run_id)

    assert body["run"]["status"] not in {"failed", "completed", "aborted"}, body
    assert body["halted_reason"] is not None
    assert "approval" in body["halted_reason"].lower()
    assert body["run"]["steps"][0]["status"] == "pending"

    # An approval request was opened, and the owner can see it.
    pending = client.get(f"/api/v1/agents/{agent_id}/approvals", headers=auth_headers(ALICE))
    assert pending.status_code == 200, pending.text
    assert len(pending.json()["items"]) == 1


def test_a_run_completes_once_the_human_approves(client: TestClient) -> None:
    """The whole point: approve, advance again, and the step actually runs."""
    agent_id, secret = _agent_with_token(client, DELETE)
    note_id = _note(client, "approve then delete")
    run_id = _plan_delete(client, secret, note_id)

    _advance(client, secret, run_id)

    pending = client.get(
        f"/api/v1/agents/{agent_id}/approvals", headers=auth_headers(ALICE)
    ).json()["items"]
    assert len(pending) == 1
    decision = client.post(
        f"/api/v1/agents/approvals/{pending[0]['id']}/decision",
        headers=auth_headers(ALICE),
        json={"approve": True, "reason": "deleting my own note"},
    )
    assert decision.status_code == 200, decision.text

    body = _advance(client, secret, run_id)

    assert body["run"]["status"] == "completed", body
    assert [outcome["tool"] for outcome in body["outcomes"]] == [DELETE]
    assert client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(ALICE)).status_code == 404


def test_a_second_advance_does_not_open_a_duplicate_approval_request(
    client: TestClient,
) -> None:
    """Re-advancing while still unapproved must reuse the open request, not pile them up."""
    agent_id, secret = _agent_with_token(client, DELETE)
    note_id = _note(client, "one request only")
    run_id = _plan_delete(client, secret, note_id)

    _advance(client, secret, run_id)
    _advance(client, secret, run_id)

    pending = client.get(
        f"/api/v1/agents/{agent_id}/approvals", headers=auth_headers(ALICE)
    ).json()["items"]
    assert len(pending) == 1
