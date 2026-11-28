"""The policy lifecycle over HTTP is operator-only and attributes the publisher (NEX-049).

The use cases are covered directly in ``tests/application/test_policy_lifecycle.py``.
This file covers what that cannot: route/schema/container agreement, and the one
security rule that lives only at the boundary — the publisher is stamped from the
authenticated principal, so a request body cannot attribute a policy in force to
someone else.

Two properties are load-bearing:

* **An empty operator allowlist admits nobody.** The default deployment grants
  these routes to no one, so an authenticated ordinary user must not be able to
  put a policy in force merely by being logged in.
* **A non-operator sees 404, not 403.** A 403 would confirm that a policy
  lifecycle surface exists at this path.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL, auth_headers, operator_grant

BASE = "/api/v1/ops/policies"

DRAFT = {
    "rules": [
        {
            "rule_id": "allow-low-risk",
            "effect": "allow",
            "when": {
                "kind": "comparison",
                "attribute": {"name": "risk", "type": "string"},
                "operator": "==",
                "value": "low",
            },
        }
    ],
    "rationale": "permit low-risk actions",
}


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def operator_client(app: FastAPI) -> TestClient:
    # Every lifecycle operation is scoped separately, so the grant must name
    # each one as its own entry: a wildcard here would hide a missing scope
    # check. ``model_copy(update=...)`` bypasses the settings validator that
    # splits a comma-joined string, so each grant is a separate list item.
    return _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "policy-draft"),
            operator_grant(ALICE, "policy-approve"),
            operator_grant(ALICE, "policy-publish"),
            operator_grant(ALICE, "policy-rollback"),
            operator_grant(ALICE, "policy-history"),
            operator_grant(BOB, "policy-approve"),
            operator_grant(BOB, "policy-publish"),
            operator_grant(BOB, "policy-rollback"),
            operator_grant(BOB, "policy-history"),
            # A third principal is required to publish (NEX-078): the author
            # drafts, a second operator approves, and only a third may put the
            # version in force, so no operator can complete the control alone.
            operator_grant(CAROL, "policy-publish"),
            operator_grant(CAROL, "policy-rollback"),
            operator_grant(CAROL, "policy-history"),
        ],
    )


def test_operator_drafts_then_publishes(operator_client: TestClient) -> None:
    drafted = operator_client.post(
        f"{BASE}/risk-gate/versions", json=DRAFT, headers=auth_headers(ALICE)
    )
    assert drafted.status_code == 201, drafted.text
    body = drafted.json()
    assert body["policy_id"] == "risk-gate"
    assert body["version"] == 1
    assert body["status"] == "draft"
    # A draft is not in force, so it has no publisher.
    assert body["published_by"] is None
    assert body["rules"][0]["rule_id"] == "allow-low-risk"
    assert body["rules"][0]["effect"] == "allow"

    approved = operator_client.post(
        f"{BASE}/risk-gate/approve", json={"version": 1}, headers=auth_headers(BOB)
    )
    assert approved.status_code == 200, approved.text
    # Approval is a recorded transition, not a side effect of publishing.
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == f"user-{BOB}"

    published = operator_client.post(
        f"{BASE}/risk-gate/publish",
        json={"version": 1},
        headers=auth_headers(CAROL),
    )
    assert published.status_code == 200, published.text
    in_force = published.json()
    assert in_force["status"] == "published"
    # The publisher is the caller, stamped at the boundary, and it is a third
    # identity: ALICE drafted the rules and BOB approved them (NEX-078).
    assert in_force["published_by"] == f"user-{CAROL}"


def test_publishing_a_superseded_version_is_a_409(operator_client: TestClient) -> None:
    """An older approved version cannot displace the one in force.

    The store would accept it — the old version is still ``APPROVED`` — so the
    refusal has to come from the use case, and it has to reach the client as a
    ``409`` rather than a ``500`` or a silent success. v1 is approved but never
    published, which is the only reachable way to get here: publishing v1 would
    first have to *retire* it, and a retired version is refused earlier.
    """
    permissive = {
        "rules": [
            {
                "rule_id": "allow-high-risk",
                "effect": "allow",
                "when": {
                    "kind": "comparison",
                    "attribute": {"name": "risk", "type": "string"},
                    "operator": "==",
                    "value": "high",
                },
            }
        ],
        "rationale": "permit high-risk actions",
    }
    first = operator_client.post(
        f"{BASE}/dg/versions", json=permissive, headers=auth_headers(ALICE)
    )
    assert first.status_code == 201, first.text
    operator_client.post(f"{BASE}/dg/approve", json={"version": 1}, headers=auth_headers(BOB))

    second = operator_client.post(f"{BASE}/dg/versions", json=DRAFT, headers=auth_headers(ALICE))
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    operator_client.post(f"{BASE}/dg/approve", json={"version": 2}, headers=auth_headers(BOB))
    live = operator_client.post(
        f"{BASE}/dg/publish", json={"version": 2}, headers=auth_headers(CAROL)
    )
    assert live.status_code == 200, live.text
    assert live.json()["version"] == 2

    downgrade = operator_client.post(
        f"{BASE}/dg/publish", json={"version": 1}, headers=auth_headers(CAROL)
    )
    assert downgrade.status_code == 409, downgrade.text
    assert "is already in force and is newer" in downgrade.json()["error"]["message"]

    # v2 stayed in force: the refused downgrade changed nothing.
    history = operator_client.get(f"{BASE}/dg/versions", headers=auth_headers(ALICE)).json()
    assert history["published_version"] == 2
    assert [(v["version"], v["status"]) for v in history["versions"]] == [
        (1, "approved"),
        (2, "published"),
    ]


def test_publisher_cannot_be_chosen_by_the_request(operator_client: TestClient) -> None:
    """``published_by`` is not a request field; ``extra="forbid"`` rejects it."""
    operator_client.post(f"{BASE}/p2/versions", json=DRAFT, headers=auth_headers(ALICE))
    response = operator_client.post(
        f"{BASE}/p2/publish",
        json={"version": 1, "published_by": str(BOB)},
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_a_drafting_operator_cannot_publish_their_own_version(
    operator_client: TestClient,
) -> None:
    """Separation of duties is enforced at the boundary, as a 409 (NEX-050).

    Also pinned at the approval step (NEX-065): the author cannot approve their own
    draft, so they can never supply the approval half of the control themselves.
    """
    operator_client.post(f"{BASE}/p5/versions", json=DRAFT, headers=auth_headers(ALICE))
    self_approval = operator_client.post(
        f"{BASE}/p5/approve", json={"version": 1}, headers=auth_headers(ALICE)
    )
    assert self_approval.status_code == 409, self_approval.text
    assert "separation of duties" in self_approval.json()["error"]["message"]

    operator_client.post(f"{BASE}/p5/approve", json={"version": 1}, headers=auth_headers(BOB))
    response = operator_client.post(
        f"{BASE}/p5/publish", json={"version": 1}, headers=auth_headers(ALICE)
    )
    assert response.status_code == 409, response.text
    assert "separation of duties" in response.json()["error"]["message"]


def test_a_draft_cannot_be_published_without_approval(operator_client: TestClient) -> None:
    """The gap NEX-065 closes: publish is not allowed to imply approval."""
    operator_client.post(f"{BASE}/p9/versions", json=DRAFT, headers=auth_headers(ALICE))
    response = operator_client.post(
        f"{BASE}/p9/publish", json={"version": 1}, headers=auth_headers(BOB)
    )
    assert response.status_code == 409, response.text
    assert "must be approved" in response.json()["error"]["message"]


def test_a_third_operator_publishes_a_version_they_did_not_author(
    operator_client: TestClient,
) -> None:
    """Publishing needs a distinct principal, not a specific one (NEX-078).

    The publisher may be anyone who is neither the author nor the approver, which
    is the whole point of a three-way control: no single operator can draft,
    approve and publish one version.
    """
    operator_client.post(f"{BASE}/p6/versions", json=DRAFT, headers=auth_headers(ALICE))
    operator_client.post(f"{BASE}/p6/approve", json={"version": 1}, headers=auth_headers(BOB))
    response = operator_client.post(
        f"{BASE}/p6/publish", json={"version": 1}, headers=auth_headers(CAROL)
    )
    assert response.status_code == 200, response.text
    assert response.json()["published_by"] == f"user-{CAROL}"


def test_the_approver_cannot_also_publish(operator_client: TestClient) -> None:
    """The gap NEX-078 closes: two identities were not enough.

    Before this rule an operator could approve a version and then publish it,
    supplying both halves of the control themselves while the author stood aside.
    Separation of duties has to read the approver as well as the author.
    """
    operator_client.post(f"{BASE}/p10/versions", json=DRAFT, headers=auth_headers(ALICE))
    operator_client.post(f"{BASE}/p10/approve", json={"version": 1}, headers=auth_headers(BOB))
    response = operator_client.post(
        f"{BASE}/p10/publish", json={"version": 1}, headers=auth_headers(BOB)
    )
    assert response.status_code == 409, response.text
    assert "separation of duties" in response.json()["error"]["message"]
    # Refused means nothing moved: v1 is still approved, never in force.
    history = operator_client.get(f"{BASE}/p10/versions", headers=auth_headers(ALICE)).json()
    assert [(v["version"], v["status"]) for v in history["versions"]] == [(1, "approved")]
    assert history["published_version"] is None


def test_history_reports_the_version_in_force(operator_client: TestClient) -> None:
    operator_client.post(f"{BASE}/p3/versions", json=DRAFT, headers=auth_headers(ALICE))
    operator_client.post(f"{BASE}/p3/approve", json={"version": 1}, headers=auth_headers(BOB))
    operator_client.post(f"{BASE}/p3/publish", json={"version": 1}, headers=auth_headers(CAROL))

    response = operator_client.get(f"{BASE}/p3/versions", headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policy_id"] == "p3"
    assert [v["version"] for v in body["versions"]] == [1]
    assert body["published_version"] == 1


def test_rollback_keeps_the_history(operator_client: TestClient) -> None:
    operator_client.post(f"{BASE}/p4/versions", json=DRAFT, headers=auth_headers(ALICE))
    operator_client.post(f"{BASE}/p4/approve", json={"version": 1}, headers=auth_headers(BOB))
    operator_client.post(f"{BASE}/p4/publish", json={"version": 1}, headers=auth_headers(CAROL))

    rolled_back = operator_client.post(
        f"{BASE}/p4/rollback",
        json={"to_version": 1, "rationale": "restore"},
        headers=auth_headers(CAROL),
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["version"] == 2

    history = operator_client.get(f"{BASE}/p4/versions", headers=auth_headers(ALICE)).json()
    # Nothing is deleted: the rollback is itself a version.
    assert [v["version"] for v in history["versions"]] == [1, 2]


def test_rolling_back_to_a_draft_is_a_conflict(operator_client: TestClient) -> None:
    operator_client.post(f"{BASE}/p5/versions", json=DRAFT, headers=auth_headers(ALICE))
    response = operator_client.post(
        f"{BASE}/p5/rollback",
        json={"to_version": 1, "rationale": "too early"},
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 409, response.text


def test_publishing_an_unknown_version_is_not_found(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/nope/publish", json={"version": 7}, headers=auth_headers(ALICE)
    )
    assert response.status_code == 404, response.text


def test_a_policy_needs_at_least_one_rule(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/empty/versions",
        json={"rules": [], "rationale": "x"},
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_an_unknown_effect_is_refused(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/bad-effect/versions",
        json={
            "rules": [{"rule_id": "r", "effect": "maybe", "when": None}],
            "rationale": "x",
        },
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_a_non_operator_sees_404_everywhere(app: FastAPI) -> None:
    """No operator allowlist: an ordinary authenticated user is not an operator."""
    client = _client(app)
    assert (
        client.post(f"{BASE}/p6/versions", json=DRAFT, headers=auth_headers(BOB)).status_code == 404
    )
    assert (
        client.post(
            f"{BASE}/p6/publish", json={"version": 1}, headers=auth_headers(BOB)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/p6/rollback",
            json={"to_version": 1, "rationale": "x"},
            headers=auth_headers(BOB),
        ).status_code
        == 404
    )
    assert client.get(f"{BASE}/p6/versions", headers=auth_headers(BOB)).status_code == 404


def test_a_grant_for_one_operation_does_not_cover_another(app: FastAPI) -> None:
    """Drafting is not publishing: scopes are per operation, not per endpoint family."""
    client = _client(app, ops_operator_user_ids=[operator_grant(ALICE, "policy-draft")])
    assert (
        client.post(f"{BASE}/p7/versions", json=DRAFT, headers=auth_headers(ALICE)).status_code
        == 201
    )
    assert (
        client.post(
            f"{BASE}/p7/publish", json={"version": 1}, headers=auth_headers(ALICE)
        ).status_code
        == 404
    )


def test_an_unauthenticated_caller_is_rejected(operator_client: TestClient) -> None:
    assert operator_client.post(f"{BASE}/p8/versions", json=DRAFT).status_code == 401


# --------------------------------------------------------------------------- #
# Policies in force over HTTP (NEX-083)
#
# The use cases are covered directly in ``tests/application/test_policy_lifecycle.py``.
# These cover what that cannot: the route answers without a caller-supplied id,
# the response carries the audit fields, an empty set is a 200, and the same
# operator gate guards it.
# --------------------------------------------------------------------------- #


def _publish_one(client: TestClient, policy_id: str = "risk-gate") -> None:
    drafted = client.post(f"{BASE}/{policy_id}/versions", json=DRAFT, headers=auth_headers(ALICE))
    assert drafted.status_code == 201, drafted.text
    approved = client.post(
        f"{BASE}/{policy_id}/approve", json={"version": 1}, headers=auth_headers(BOB)
    )
    assert approved.status_code == 200, approved.text
    published = client.post(
        f"{BASE}/{policy_id}/publish", json={"version": 1}, headers=auth_headers(CAROL)
    )
    assert published.status_code == 200, published.text


def test_in_force_route_is_200_with_an_empty_set(operator_client: TestClient) -> None:
    """Nothing published is a real answer, not a 404 for a missing route.

    "Deny everything" is the most restrictive state a deployment can be in and
    the one an operator most needs to see. Reporting it as an error would hide it
    behind the same status as a typo'd path.
    """
    response = operator_client.get(BASE, headers=auth_headers(ALICE))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policies"] == []
    assert body["policy_count"] == 0
    # The adapter is named so the reader knows whether this is durable evidence.
    assert body["source_name"] == "sqlalchemy"


def test_in_force_route_returns_the_published_set(operator_client: TestClient) -> None:
    """One request answers "what is restricting this deployment" — no id needed."""
    _publish_one(operator_client)

    body = operator_client.get(BASE, headers=auth_headers(ALICE)).json()

    assert body["policy_count"] == 1
    policy = body["policies"][0]
    assert policy["policy_id"] == "risk-gate"
    assert policy["version"] == 1
    assert policy["rules"][0]["rule_id"] == "allow-low-risk"
    # The audit fields an operator reads first, serialised as data.
    assert policy["approved_by"] == f"user-{BOB}"
    assert policy["published_by"] == f"user-{CAROL}"
    assert policy["approval_on_record"] is True
    assert policy["unenforceable_obligations"] == []


def test_in_force_route_omits_a_draft(operator_client: TestClient) -> None:
    """A draft is not in force, so it must not appear — the report is the set,
    not the rows, and ``policy_count`` counts policies in force."""
    pending = operator_client.post(
        f"{BASE}/pending/versions", json=DRAFT, headers=auth_headers(ALICE)
    )
    assert pending.status_code == 201, pending.text
    _publish_one(operator_client, "live")

    body = operator_client.get(BASE, headers=auth_headers(ALICE)).json()

    assert [p["policy_id"] for p in body["policies"]] == ["live"]
    assert body["policy_count"] == 1


def test_in_force_route_is_404_for_a_non_operator(
    app: FastAPI, operator_client: TestClient
) -> None:
    """A logged-in ordinary user must not learn that this surface exists.

    The policy is published by an operator and stays in the durable store, so the
    ungranted client sees the same rows — the 404 is the gate refusing, not an
    empty repo.
    """
    _publish_one(operator_client)

    denied = _client(app).get(BASE, headers=auth_headers(BOB))
    assert denied.status_code == 404, denied.text


def test_in_force_route_requires_authentication(operator_client: TestClient) -> None:
    """No credential is not an empty policy set; it is a rejected request."""
    assert operator_client.get(BASE).status_code == 401
