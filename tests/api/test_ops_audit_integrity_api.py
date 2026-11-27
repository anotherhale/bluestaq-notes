"""The platform-wide audit sweep is operator-only and fails closed (NEX-042).

``SweepAuditIntegrity`` is covered directly in
``tests/application/test_audit_sweep.py``. This file covers what that cannot:
that the route, the response schema and the container wiring agree, and that the
gate behaves at the HTTP boundary.

The sweep is the first surface that enumerates agents *across owners*, so the
gate is the security property, not a nicety. Two invariants are asserted here
rather than assumed:

* **An empty allowlist admits nobody.** A default deployment must not let an
  authenticated ordinary user enumerate every agent in the platform.
* **A non-operator sees 404, not 403.** ``403`` would confirm that a
  platform-wide integrity surface exists at this path, which is itself a
  disclosure. The use case raises ``ResourceNotFound``; the router must not
  remap it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers, operator_grant

PATH = "/api/v1/ops/audit-integrity"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    """Rebuild the app from ``app``'s settings with overrides applied."""
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def operator_client(app: FastAPI) -> TestClient:
    # Scoped to this endpoint's operation: the grant must not carry
    # over to another /ops view (NEX-044).
    return _client(app, ops_operator_user_ids=[operator_grant(ALICE, "audit-integrity")])


def test_operator_sweeps_an_empty_deployment(operator_client: TestClient) -> None:
    """No agents is a *passing* sweep, not an error: there is nothing to contradict."""
    response = operator_client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {"verified", "agents_total", "agents_checked", "truncated", "broken"}
    assert body["agents_total"] == 0
    assert body["agents_checked"] == 0
    assert body["truncated"] is False
    assert body["broken"] == []
    assert body["verified"] is True


def test_empty_allowlist_admits_nobody(app: FastAPI) -> None:
    """The default configuration exposes this to no one, including a real user."""
    client = _client(app, ops_operator_user_ids=[])
    response = client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_non_operator_gets_404_not_403(operator_client: TestClient) -> None:
    """A known user who is not on the list must not learn the route exists."""
    response = operator_client.get(PATH, headers=auth_headers(BOB))
    assert response.status_code == 404, response.text
    assert response.status_code != 403


def test_unauthenticated_caller_is_rejected(operator_client: TestClient) -> None:
    response = operator_client.get(PATH)
    assert response.status_code in {401, 404}, response.text
    assert response.status_code != 200
