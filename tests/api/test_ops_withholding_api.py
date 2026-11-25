"""The withholding aggregate is operator-only and fails closed (R34).

``GetWithholdingReport`` is covered directly in
``tests/application/test_withholding_report.py``. This file covers what that
cannot: that the route, the response schema and the container wiring agree, and
that the *gate* behaves at the HTTP boundary.

Two properties are load-bearing and are asserted here rather than assumed:

* **An empty allowlist admits nobody.** The default deployment configuration
  grants this endpoint to no one, so an authenticated ordinary user must not be
  able to read process-wide totals simply by being logged in.
* **A non-operator sees 404, not 403.** ``403`` would confirm that a
  platform-operations surface exists at this path, which is itself a small
  disclosure. The application layer raises ``ResourceNotFound``; the router must
  not remap it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers

PATH = "/api/v1/ops/withholding-report"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    """Rebuild the app from ``app``'s settings with overrides applied."""
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def operator_client(app: FastAPI) -> TestClient:
    return _client(app, ops_operator_user_ids=[ALICE])


def test_operator_reads_totals_with_a_rate(operator_client: TestClient) -> None:
    response = operator_client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {
        "advisories_total",
        "over_cap_advisories_total",
        "over_cap_fields_total",
        "over_cap_rate",
    }
    # Counts and a rate, never a field name, value, note id or user id.
    for key in ("advisories_total", "over_cap_advisories_total", "over_cap_fields_total"):
        assert isinstance(body[key], int)
        assert body[key] >= 0
    assert 0.0 <= body["over_cap_rate"] <= 1.0


def test_empty_allowlist_admits_nobody(app: FastAPI) -> None:
    """The default configuration exposes this to no one, including a real user."""
    client = _client(app, ops_operator_user_ids=[])
    response = client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_non_operator_gets_404_not_403(operator_client: TestClient) -> None:
    """A known user who is not on the list must not learn the route exists."""
    response = operator_client.get(PATH, headers=auth_headers(BOB))
    assert response.status_code == 404, response.text
    # 403 would confirm the endpoint and merely deny the role.
    assert response.status_code != 403


def test_unauthenticated_caller_is_rejected(operator_client: TestClient) -> None:
    response = operator_client.get(PATH)
    assert response.status_code in {401, 404}, response.text
    assert response.status_code != 200
