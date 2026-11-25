"""The advisory endpoint must be reachable, read-only, and never an approval.

The use case is covered directly in ``tests/agent/test_approval_advisory.py``.
This file covers the part those tests cannot: that the HTTP route, the response
schema, and the container wiring all line up, so a reviewer can actually get the
second opinion. A use case nobody can call is not a feature.

The read-only property is re-asserted here at the HTTP boundary, because that is
where an accidental "and approve it too" would be easiest to introduce and
hardest to notice.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers

SHARE = "share_note"


def _agent(client: TestClient, identity: str = "svc-advisory") -> str:
    response = client.post(
        "/api/v1/agents",
        headers=auth_headers(ALICE),
        json={
            "display_name": "Advised",
            "runtime_identity": identity,
            "capabilities": [SHARE],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _pending(client: TestClient, agent_id: str, resource_ref: str) -> str:
    response = client.post(
        f"/api/v1/agents/{agent_id}/approvals",
        headers=auth_headers(ALICE),
        json={"capability": SHARE, "resource_ref": resource_ref},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_advisory_route_returns_an_opinion_without_deciding(client: TestClient) -> None:
    agent_id = _agent(client)
    # A real note, so the subject title is a genuine confidential candidate. A
    # fabricated id would make the ceiling check vacuous and let this test pass
    # while proving nothing about withholding.
    note = client.post(
        "/api/v1/notes", headers=auth_headers(ALICE), json={"title": "Q3 payroll", "content": "b"}
    )
    assert note.status_code == 201, note.text
    approval_id = _pending(client, agent_id, f"note:{note.json()['id']}")

    response = client.get(
        f"/api/v1/agents/approvals/{approval_id}/advisory", headers=auth_headers(ALICE)
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Shape: an opinion and its provenance, nothing that could be read as a verdict.
    assert set(body) >= {
        "approval_id",
        "score",
        "confidence",
        "abstention_reason",
        "provider_name",
        "context_hash",
        "excluded_context_fields",
        "over_cap_context_fields",
        "latency_ms",
        "advisory_available",
    }
    assert "approved" not in body and "decision" not in body and "status" not in body
    assert len(body["context_hash"]) == 64

    # The two withholding reasons must not be conflated at the boundary. The
    # subject note's title is confidential, so it is withheld *by the ceiling* —
    # routine policy, not degradation — and must therefore appear in the audit
    # list without appearing in the over-cap list.
    assert body["excluded_context_fields"] == ["note_title"]
    assert body["over_cap_context_fields"] == []

    # The request is untouched: still pending, no decider recorded.
    reloaded = client.get(f"/api/v1/agents/approvals/{approval_id}", headers=auth_headers(ALICE))
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["status"] == "pending"


def test_advisory_route_404s_for_a_non_owner(client: TestClient) -> None:
    """Another user's approval id must not be an existence oracle."""
    agent_id = _agent(client, "svc-advisory-404")
    approval_id = _pending(client, agent_id, "note:00000000-0000-0000-0000-000000000001")

    response = client.get(
        f"/api/v1/agents/approvals/{approval_id}/advisory", headers=auth_headers(BOB)
    )
    assert response.status_code == 404, response.text


def test_advisory_route_requires_authentication(client: TestClient) -> None:
    agent_id = _agent(client, "svc-advisory-anon")
    approval_id = _pending(client, agent_id, "note:00000000-0000-0000-0000-000000000001")

    response = client.get(f"/api/v1/agents/approvals/{approval_id}/advisory")
    assert response.status_code == 401, response.text
