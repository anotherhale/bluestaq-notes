"""Mandatory authorization and cross-user isolation tests.

These tests exercise the full HTTP -> application -> domain -> database path
with real persistence, and assert on *persisted state* as well as responses.
Each numbered test maps to the required cases in the assignment brief.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import ALICE, BOB, CAROL, auth_headers

UNKNOWN_UUID = "99999999-9999-9999-9999-999999999999"


def create_note(client: TestClient, owner: str, title: str = "t", content: str = "c") -> str:
    response = client.post(
        "/api/v1/notes", headers=auth_headers(owner), json={"title": title, "content": content}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def share(client: TestClient, owner: str, note_id: str, target: str, level: str) -> None:
    response = client.post(
        f"/api/v1/notes/{note_id}/permissions",
        headers=auth_headers(owner),
        json={"user_id": target, "permission": level},
    )
    assert response.status_code == 201, response.text


def note_count(session_factory: sessionmaker[Session]) -> int:
    from notes_service.infrastructure.db.models import NoteRow

    with session_factory() as session:
        return session.query(NoteRow).count()


class TestCrossUserIsolation:
    def test_1_user_a_cannot_read_user_bs_private_note(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        response = client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB))
        assert response.status_code == 404
        assert "error" in response.json()

    def test_2_user_a_cannot_update_user_bs_private_note(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        response = client.put(
            f"/api/v1/notes/{note_id}",
            headers=auth_headers(BOB),
            json={"title": "hacked", "content": "x", "version": 1},
        )
        assert response.status_code == 404
        # The note must be unchanged.
        owner_view = client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(ALICE)).json()
        assert owner_view["title"] == "t"
        assert owner_view["version"] == 1

    def test_3_user_a_cannot_delete_user_bs_private_note(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        response = client.delete(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB))
        assert response.status_code == 404
        assert (
            client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(ALICE)).status_code == 200
        )

    def test_12_guessing_a_uuid_does_not_bypass_authorization(self, client: TestClient) -> None:
        # An existing note owned by Alice, addressed by Bob with the real id.
        note_id = create_note(client, ALICE)
        assert uuid.UUID(note_id)
        assert client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB)).status_code == 404
        # A random (nonexistent) UUID is indistinguishable from a forbidden one.
        assert (
            client.get(f"/api/v1/notes/{UNKNOWN_UUID}", headers=auth_headers(BOB)).status_code
            == 404
        )


class TestReadPermission:
    def test_4_read_permission_allows_retrieval(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE, title="shared")
        share(client, ALICE, note_id, BOB, "read")
        response = client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB))
        assert response.status_code == 200
        assert response.json()["title"] == "shared"

    def test_5_read_permission_does_not_allow_modification(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        share(client, ALICE, note_id, BOB, "read")
        response = client.put(
            f"/api/v1/notes/{note_id}",
            headers=auth_headers(BOB),
            json={"title": "nope", "content": "x", "version": 1},
        )
        assert response.status_code == 403


class TestWritePermission:
    def test_6_write_permission_allows_modification(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        share(client, ALICE, note_id, BOB, "write")
        response = client.put(
            f"/api/v1/notes/{note_id}",
            headers=auth_headers(BOB),
            json={"title": "edited by bob", "content": "new", "version": 1},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "edited by bob"
        assert response.json()["version"] == 2

    def test_7_write_permission_does_not_allow_deletion(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        share(client, ALICE, note_id, BOB, "write")
        response = client.delete(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB))
        assert response.status_code == 403
        assert (
            client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(ALICE)).status_code == 200
        )

    def test_8_write_permission_does_not_allow_permission_management(
        self, client: TestClient
    ) -> None:
        note_id = create_note(client, ALICE)
        share(client, ALICE, note_id, BOB, "write")
        # Bob (WRITE) cannot grant Carol access.
        response = client.post(
            f"/api/v1/notes/{note_id}/permissions",
            headers=auth_headers(BOB),
            json={"user_id": CAROL, "permission": "read"},
        )
        assert response.status_code == 403
        # Bob cannot revoke his own (or anyone's) access either.
        revoke = client.delete(
            f"/api/v1/notes/{note_id}/permissions/{BOB}", headers=auth_headers(BOB)
        )
        assert revoke.status_code == 403
        # Bob cannot list permissions.
        listing = client.get(f"/api/v1/notes/{note_id}/permissions", headers=auth_headers(BOB))
        assert listing.status_code == 403


class TestPermissionManagement:
    def test_9_non_owner_cannot_grant_permissions(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        share(client, ALICE, note_id, BOB, "read")
        response = client.post(
            f"/api/v1/notes/{note_id}/permissions",
            headers=auth_headers(BOB),
            json={"user_id": CAROL, "permission": "write"},
        )
        assert response.status_code == 403

    def test_13_user_cannot_share_a_note_they_do_not_own(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        # Bob has no access at all; he should not even learn the note exists.
        response = client.post(
            f"/api/v1/notes/{note_id}/permissions",
            headers=auth_headers(BOB),
            json={"user_id": CAROL, "permission": "read"},
        )
        assert response.status_code == 404

    def test_14_cannot_grant_access_to_nonexistent_user(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        response = client.post(
            f"/api/v1/notes/{note_id}/permissions",
            headers=auth_headers(ALICE),
            json={"user_id": UNKNOWN_UUID, "permission": "read"},
        )
        assert response.status_code == 422

    def test_cannot_grant_permission_to_owner(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        response = client.post(
            f"/api/v1/notes/{note_id}/permissions",
            headers=auth_headers(ALICE),
            json={"user_id": ALICE, "permission": "read"},
        )
        assert response.status_code == 422

    def test_10_revoked_permissions_immediately_remove_access(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        share(client, ALICE, note_id, BOB, "read")
        assert client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB)).status_code == 200

        revoke = client.delete(
            f"/api/v1/notes/{note_id}/permissions/{BOB}", headers=auth_headers(ALICE)
        )
        assert revoke.status_code == 204

        # Access is gone on the very next request.
        assert client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB)).status_code == 404

    def test_revoke_nonexistent_permission_is_404(self, client: TestClient) -> None:
        note_id = create_note(client, ALICE)
        response = client.delete(
            f"/api/v1/notes/{note_id}/permissions/{BOB}", headers=auth_headers(ALICE)
        )
        assert response.status_code == 404


class TestClientControlledOwnership:
    def test_11_client_supplied_owner_id_cannot_change_ownership(self, client: TestClient) -> None:
        # ``owner_id`` is not part of the request schema and is rejected outright.
        response = client.post(
            "/api/v1/notes",
            headers=auth_headers(BOB),
            json={"title": "t", "content": "c", "owner_id": ALICE},
        )
        assert response.status_code == 422

    def test_ownership_is_taken_from_principal(self, client: TestClient) -> None:
        note_id = create_note(client, BOB)
        note = client.get(f"/api/v1/notes/{note_id}", headers=auth_headers(BOB)).json()
        assert note["owner_id"] == BOB


class TestPersistenceOfDecisions:
    def test_delete_removes_note_and_its_permissions_from_db(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        from notes_service.infrastructure.db.models import NotePermissionRow, NoteRow

        note_id = create_note(client, ALICE)
        share(client, ALICE, note_id, BOB, "write")
        assert note_count(session_factory) == 1

        response = client.delete(f"/api/v1/notes/{note_id}", headers=auth_headers(ALICE))
        assert response.status_code == 204

        with session_factory() as session:
            assert session.query(NoteRow).count() == 0
            # ON DELETE CASCADE plus SQLite FK enforcement must clear grants.
            assert session.query(NotePermissionRow).count() == 0
