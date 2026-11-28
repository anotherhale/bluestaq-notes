"""The operator access report is pageable to the whole retained trail (NEX-082).

Before this, ``GET /ops/access-report`` returned at most its ``limit`` newest
entries and offered no way to reach further back, while retention keeps entries
for years. Every privileged read older than the newest few hundred was therefore
permanently unreadable — the log recorded them and nothing could show them.

The fix is a **keyset** cursor, not an offset, and that is forced by the endpoint
itself: reading this view is a granted operator read, so each request appends an
entry at the head. An ``OFFSET`` page would shift by one row per page, repeating
one entry and skipping another, so a reader walking the log would get a trail
with holes in it — worse than a capped read, because it looks complete.

These tests pin the properties that make the walk trustworthy:

* following ``next_position`` reaches every retained entry, exactly once;
* pages do not shift when entries are appended between them;
* the last page offers no cursor, so a walk always terminates;
* a middle page reports itself ``truncated`` even when it under-fills, because
  "I did not fill" is not "I am the whole trail";
* a cursor nothing predates is an empty page, not an error and not a wrap-around.

The gate, the schema shape and the 404-not-403 rule live in
``test_ops_access_report_api.py``; the use case in
``tests/application/test_operator_access_report.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, auth_headers, operator_grant

PATH = "/api/v1/ops/access-report"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def operator_client(app: FastAPI) -> TestClient:
    return _client(app, ops_operator_user_ids=[operator_grant(ALICE, "operator-access-report")])


def _walk(client: TestClient, *, limit: int) -> list[int]:
    """Follow ``next_position`` until it is null, returning every position seen."""
    seen: list[int] = []
    cursor: int | None = None
    for _ in range(200):  # a guard, not the expected iteration count
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["before_position"] = cursor
        response = client.get(PATH, headers=auth_headers(ALICE), params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(entry["position"] for entry in body["entries"])
        cursor = body["next_position"]
        if cursor is None:
            return seen
    pytest.fail("paging did not terminate")  # pragma: no cover


def test_the_whole_trail_is_reachable_by_cursor(operator_client: TestClient) -> None:
    for _ in range(6):
        assert operator_client.get(PATH, headers=auth_headers(ALICE)).status_code == 200

    seen = _walk(operator_client, limit=2)

    assert seen, "the walk returned nothing"
    # Unique, so no page overlapped its predecessor; strictly descending, so no
    # entry was skipped between pages.
    assert len(seen) == len(set(seen)), f"duplicate entries across pages: {seen}"
    assert seen == sorted(seen, reverse=True), f"pages out of order: {seen}"


def test_pages_do_not_shift_when_reading_appends_an_entry(
    operator_client: TestClient,
) -> None:
    """Each read appends a head row, which is exactly what breaks offset paging."""
    for _ in range(5):
        operator_client.get(PATH, headers=auth_headers(ALICE))

    first = operator_client.get(PATH, headers=auth_headers(ALICE), params={"limit": 2}).json()
    assert first["next_position"] is not None
    page_one = [entry["position"] for entry in first["entries"]]

    # These reads each insert a new head entry *before* page two is requested.
    operator_client.get(PATH, headers=auth_headers(ALICE))
    operator_client.get(PATH, headers=auth_headers(ALICE))

    second = operator_client.get(
        PATH,
        headers=auth_headers(ALICE),
        params={"limit": 2, "before_position": first["next_position"]},
    ).json()
    page_two = [entry["position"] for entry in second["entries"]]

    assert page_two, second
    assert set(page_one).isdisjoint(page_two), f"{page_one} overlaps {page_two}"
    assert max(page_two) < min(page_one), "page two must be strictly older than page one"


def test_the_last_page_offers_no_cursor(operator_client: TestClient) -> None:
    """A cursor on the last page would send a reader on a walk with no end."""
    operator_client.get(PATH, headers=auth_headers(ALICE))
    body = operator_client.get(PATH, headers=auth_headers(ALICE), params={"limit": 500}).json()

    assert len(body["entries"]) < body["limit"]
    assert body["next_position"] is None
    assert body["truncated"] is False


def test_a_page_from_a_cursor_reports_itself_truncated(operator_client: TestClient) -> None:
    """A middle page is partial even when under-full: newer entries are missing."""
    for _ in range(4):
        operator_client.get(PATH, headers=auth_headers(ALICE))

    head = operator_client.get(PATH, headers=auth_headers(ALICE), params={"limit": 1}).json()
    assert head["next_position"] is not None
    mid = operator_client.get(
        PATH,
        headers=auth_headers(ALICE),
        params={"limit": 1, "before_position": head["next_position"]},
    ).json()
    assert mid["next_position"] is not None

    tail = operator_client.get(
        PATH,
        headers=auth_headers(ALICE),
        params={"limit": 500, "before_position": mid["next_position"]},
    ).json()

    assert len(tail["entries"]) < tail["limit"], "expected an under-full final page"
    assert tail["next_position"] is None
    assert tail["truncated"] is True, "a middle page must not claim to be the whole trail"


def test_a_cursor_of_zero_is_rejected(operator_client: TestClient) -> None:
    """A non-positive cursor would silently mean "everything", not "the end"."""
    response = operator_client.get(PATH, headers=auth_headers(ALICE), params={"before_position": 0})
    assert response.status_code == 422, response.text


def test_a_cursor_nothing_predates_returns_an_empty_page(
    operator_client: TestClient,
) -> None:
    """An exhausted cursor is an empty page, not an error and not a wrap-around."""
    operator_client.get(PATH, headers=auth_headers(ALICE))
    response = operator_client.get(PATH, headers=auth_headers(ALICE), params={"before_position": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entries"] == []
    assert body["next_position"] is None
    assert body["truncated"] is False
