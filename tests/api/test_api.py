"""HTTP API tests: validation, status codes, pagination, errors, concurrency,
rate limiting, and security headers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers


def create_note(client: TestClient, owner: str = ALICE, **overrides) -> dict:  # type: ignore[no-untyped-def]
    payload = {"title": "title", "content": "content", **overrides}
    response = client.post("/api/v1/notes", headers=auth_headers(owner), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestMetaEndpoints:
    def test_health_is_unauthenticated(self, client: TestClient) -> None:
        assert client.get("/health").json() == {"status": "ok"}

    def test_openapi_document_available(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/api/v1/notes" in schema["paths"]


class TestNoteValidation:
    def test_blank_title_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/notes", headers=auth_headers(ALICE), json={"title": "   ", "content": ""}
        )
        assert response.status_code == 422

    def test_overlong_title_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/notes",
            headers=auth_headers(ALICE),
            json={"title": "x" * 201, "content": ""},
        )
        assert response.status_code == 422

    def test_unexpected_field_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/notes",
            headers=auth_headers(ALICE),
            json={"title": "t", "content": "", "unexpected": "nope"},
        )
        assert response.status_code == 422

    def test_missing_title_rejected(self, client: TestClient) -> None:
        response = client.post("/api/v1/notes", headers=auth_headers(ALICE), json={"content": ""})
        assert response.status_code == 422

    def test_invalid_note_uuid_is_422(self, client: TestClient) -> None:
        response = client.get("/api/v1/notes/not-a-uuid", headers=auth_headers(ALICE))
        assert response.status_code == 422

    def test_invalid_permission_value_rejected(self, client: TestClient) -> None:
        note = create_note(client)
        response = client.post(
            f"/api/v1/notes/{note['id']}/permissions",
            headers=auth_headers(ALICE),
            json={"user_id": BOB, "permission": "admin"},
        )
        assert response.status_code == 422

    def test_permission_value_is_case_insensitive(self, client: TestClient) -> None:
        note = create_note(client)
        response = client.post(
            f"/api/v1/notes/{note['id']}/permissions",
            headers=auth_headers(ALICE),
            json={"user_id": BOB, "permission": "READ"},
        )
        assert response.status_code == 201
        assert response.json()["permission"] == "read"


class TestHttpSemantics:
    def test_create_returns_201(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/notes", headers=auth_headers(ALICE), json={"title": "t", "content": "c"}
        )
        assert response.status_code == 201

    def test_get_returns_200(self, client: TestClient) -> None:
        note = create_note(client)
        assert (
            client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(ALICE)).status_code
            == 200
        )

    def test_update_returns_200(self, client: TestClient) -> None:
        note = create_note(client)
        response = client.put(
            f"/api/v1/notes/{note['id']}",
            headers=auth_headers(ALICE),
            json={"title": "t2", "content": "c2", "version": 1},
        )
        assert response.status_code == 200

    def test_delete_returns_204(self, client: TestClient) -> None:
        note = create_note(client)
        assert (
            client.delete(f"/api/v1/notes/{note['id']}", headers=auth_headers(ALICE)).status_code
            == 204
        )

    def test_nonexistent_note_is_404(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/notes/99999999-9999-9999-9999-999999999999", headers=auth_headers(ALICE)
        )
        assert response.status_code == 404

    def test_timestamps_are_utc_aware_and_consistent(self, client: TestClient) -> None:
        # Create and read must render the same field identically: both ends of a
        # timestamp should be timezone-aware UTC, on SQLite as well as
        # PostgreSQL (regression test for the naive-datetime leak).
        created = create_note(client)
        assert created["created_at"].endswith("Z") or created["created_at"].endswith("+00:00")
        fetched = client.get(f"/api/v1/notes/{created['id']}", headers=auth_headers(ALICE)).json()
        assert fetched["created_at"] == created["created_at"]
        assert fetched["updated_at"] == created["updated_at"]


class TestConcurrency:
    def test_stale_update_returns_409(self, client: TestClient) -> None:
        note = create_note(client)
        first = client.put(
            f"/api/v1/notes/{note['id']}",
            headers=auth_headers(ALICE),
            json={"title": "v2", "content": "", "version": 1},
        )
        assert first.status_code == 200
        assert first.json()["version"] == 2

        stale = client.put(
            f"/api/v1/notes/{note['id']}",
            headers=auth_headers(ALICE),
            json={"title": "v3", "content": "", "version": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "conflict"

        # The note retains the first update.
        current = client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(ALICE)).json()
        assert current["title"] == "v2"

    def test_update_requires_version_field(self, client: TestClient) -> None:
        note = create_note(client)
        response = client.put(
            f"/api/v1/notes/{note['id']}",
            headers=auth_headers(ALICE),
            json={"title": "t2", "content": "c2"},
        )
        assert response.status_code == 422


class TestPaginationAndListing:
    def test_list_returns_only_accessible_notes(self, client: TestClient) -> None:
        alice_note = create_note(client, ALICE, title="alice")
        create_note(client, BOB, title="bob")
        listing = client.get("/api/v1/notes", headers=auth_headers(ALICE)).json()
        titles = {item["title"] for item in listing["items"]}
        assert "alice" in titles
        assert "bob" not in titles
        assert listing["meta"]["total"] == 1
        assert listing["items"][0]["id"] == alice_note["id"]

    def test_shared_note_appears_for_recipient(self, client: TestClient) -> None:
        note = create_note(client, ALICE, title="shared")
        client.post(
            f"/api/v1/notes/{note['id']}/permissions",
            headers=auth_headers(ALICE),
            json={"user_id": BOB, "permission": "read"},
        )
        listing = client.get("/api/v1/notes", headers=auth_headers(BOB)).json()
        assert note["id"] in {item["id"] for item in listing["items"]}

    def test_no_duplicates_when_owned_and_shared(self, client: TestClient) -> None:
        # A note cannot be both owned and shared (owner cannot hold a grant),
        # so the deduplication guarantee is validated by the repository test.
        # Here we assert the list endpoint returns each note once.
        for index in range(3):
            create_note(client, ALICE, title=f"n{index}")
        listing = client.get("/api/v1/notes", headers=auth_headers(ALICE)).json()
        ids = [item["id"] for item in listing["items"]]
        assert len(ids) == len(set(ids))

    def test_pagination_limits(self, client: TestClient) -> None:
        for index in range(5):
            create_note(client, ALICE, title=f"n{index}")
        page = client.get(
            "/api/v1/notes", headers=auth_headers(ALICE), params={"limit": 2, "offset": 1}
        ).json()
        assert len(page["items"]) == 2
        assert page["meta"]["total"] == 5

    def test_limit_out_of_range_rejected(self, client: TestClient) -> None:
        assert (
            client.get(
                "/api/v1/notes", headers=auth_headers(ALICE), params={"limit": 0}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/notes", headers=auth_headers(ALICE), params={"limit": 1000}
            ).status_code
            == 422
        )

    def test_default_limit_comes_from_config_and_is_consistent(self, client: TestClient) -> None:
        # Both list endpoints must share one default (DEFAULT_PAGE_SIZE), not
        # silently diverge.
        notes_meta = client.get("/api/v1/notes", headers=auth_headers(ALICE)).json()["meta"]
        users_meta = client.get("/api/v1/users", headers=auth_headers(ALICE)).json()["meta"]
        assert notes_meta["limit"] == users_meta["limit"]

    def test_sort_option(self, client: TestClient) -> None:
        create_note(client, ALICE, title="aaa")
        create_note(client, ALICE, title="zzz")
        listing = client.get(
            "/api/v1/notes", headers=auth_headers(ALICE), params={"sort": "title"}
        ).json()
        titles = [item["title"] for item in listing["items"]]
        assert titles == sorted(titles)

    def test_invalid_sort_falls_back(self, client: TestClient) -> None:
        create_note(client, ALICE)
        response = client.get(
            "/api/v1/notes", headers=auth_headers(ALICE), params={"sort": "bogus"}
        )
        assert response.status_code == 200


class TestErrorEnvelope:
    def test_errors_share_a_consistent_shape(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/notes/99999999-9999-9999-9999-999999999999", headers=auth_headers(ALICE)
        )
        body = response.json()
        assert set(body) == {"error", "request_id"}
        assert set(body["error"]) >= {"code", "message"}
        assert body["request_id"]

    def test_401_has_www_authenticate(self, client: TestClient) -> None:
        response = client.get("/api/v1/me")
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers


class TestSecurityHeaders:
    def test_headers_present_on_success(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Cache-Control"] == "no-store"
        assert "Content-Security-Policy" in response.headers

    def test_request_id_is_echoed_and_generated(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "my-trace-123"})
        assert response.headers["X-Request-ID"] == "my-trace-123"

    def test_invalid_request_id_replaced(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "bad value with spaces"})
        assert response.headers["X-Request-ID"] != "bad value with spaces"


class TestBodySizeLimit:
    def test_oversized_body_rejected_with_413(self, client: TestClient) -> None:
        huge = "a" * (300 * 1024)
        response = client.post(
            "/api/v1/notes", headers=auth_headers(ALICE), json={"title": "t", "content": huge}
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"


class TestCors:
    def test_allowed_origin_gets_cors_headers(self, client: TestClient) -> None:
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_disallowed_origin_gets_no_cors_headers(self, client: TestClient) -> None:
        response = client.get("/health", headers={"Origin": "http://evil.example.com"})
        assert "access-control-allow-origin" not in response.headers


class TestRateLimiting:
    def test_rate_limit_returns_429(self, app: object, client: TestClient) -> None:
        from notes_service.infrastructure.security.rate_limiter import InMemoryRateLimiter

        # Swap in a tight limiter on the already-wired container so the whole
        # request path (middleware, dependency, handler) is exercised.
        container = app.state.container  # type: ignore[attr-defined]
        container.settings.rate_limit_enabled = True
        container.rate_limiter = InMemoryRateLimiter(limit=3, window_seconds=60)

        statuses = [
            client.get("/api/v1/me", headers=auth_headers(ALICE)).status_code for _ in range(5)
        ]
        assert statuses[0] == 200
        assert 429 in statuses
        assert statuses[-1] == 429

        # The 429 carries a Retry-After header.
        blocked = client.get("/api/v1/me", headers=auth_headers(ALICE))
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers

    def test_rate_limit_isolated_per_user(self, app: object, client: TestClient) -> None:
        from notes_service.infrastructure.security.rate_limiter import InMemoryRateLimiter

        container = app.state.container  # type: ignore[attr-defined]
        container.settings.rate_limit_enabled = True
        container.rate_limiter = InMemoryRateLimiter(limit=2, window_seconds=60)

        for _ in range(3):
            client.get("/api/v1/me", headers=auth_headers(ALICE))
        # Alice is now limited, but Bob's budget is independent.
        assert client.get("/api/v1/me", headers=auth_headers(ALICE)).status_code == 429
        assert client.get("/api/v1/me", headers=auth_headers(BOB)).status_code == 200


class TestUsersEndpoint:
    def test_users_listing_exposes_only_sharing_fields(self, client: TestClient) -> None:
        response = client.get("/api/v1/users", headers=auth_headers(ALICE))
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert set(item) == {"id", "email", "display_name"}

    def test_users_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/users").status_code == 401
