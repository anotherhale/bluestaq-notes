"""The access-log purge is operator-only and windowed at the HTTP boundary (NEX-075).

The use case is covered in ``tests/application/test_operator_access_retention.py``.
This file covers what only the route can show:

* the endpoint exists and answers when a retention window is configured;
* it is **absent** (``404``) when retention is disabled — a deployment that keeps
  everything must not offer a purge with no mandate;
* it is gated on ``access-log-retention`` and *not* on the read scope, so an
  auditor who may read the log cannot erase it by that fact alone;
* the body states the boundary and window it applied, not just a count.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from notes_service.domain.errors import ValidationError
from tests.conftest import ALICE, BOB, auth_headers, operator_grant

PATH = "/api/v1/ops/operator-access-log/purge"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def retention_client(app: FastAPI) -> TestClient:
    return _client(
        app,
        operator_access_retention_days=90,
        ops_operator_user_ids=[operator_grant(ALICE, "access-log-retention")],
    )


def test_an_operator_purges_the_log(retention_client: TestClient) -> None:
    response = retention_client.post(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {"cutoff", "removed", "window_days", "retained_from", "log_name"}
    assert body["window_days"] == 90
    assert body["removed"] >= 0
    assert isinstance(body["log_name"], str) and body["log_name"]
    # The purge records itself inside the window, so something survives.
    assert body["retained_from"] is not None


def test_the_purge_is_absent_when_retention_is_disabled(app: FastAPI) -> None:
    """No configured window means no mandate: the endpoint answers 404."""
    client = _client(
        app,
        operator_access_retention_days=None,
        ops_operator_user_ids=[operator_grant(ALICE, "access-log-retention")],
    )
    response = client.post(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_reading_the_log_does_not_confer_the_right_to_purge_it(app: FastAPI) -> None:
    """The read scope must not reach this route — erasing is a graver authority."""
    client = _client(
        app,
        operator_access_retention_days=90,
        ops_operator_user_ids=[operator_grant(ALICE, "operator-access-report")],
    )
    response = client.post(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_a_non_operator_gets_not_found(app: FastAPI) -> None:
    """404, never 403: the endpoint must not confirm that it exists."""
    client = _client(
        app,
        operator_access_retention_days=90,
        ops_operator_user_ids=[operator_grant(ALICE, "access-log-retention")],
    )
    response = client.post(PATH, headers=auth_headers(BOB))
    assert response.status_code == 404, response.text


def test_an_unauthenticated_caller_is_refused(app: FastAPI) -> None:
    client = _client(app, operator_access_retention_days=90)
    response = client.post(PATH)
    assert response.status_code == 401, response.text


def test_a_too_short_retention_window_fails_the_boot(app: FastAPI) -> None:
    """The floor is enforced at startup, so a bad config cannot reach a purge."""
    with pytest.raises(ValidationError):
        _client(app, operator_access_retention_days=1)
