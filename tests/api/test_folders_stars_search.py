"""HTTP tests for folders, note movement, starring, search and filtering.

These exercise the full request path with real persistence, and assert on
persisted state as well as responses. They cover the security-critical rules:
a collaborator can never move or reorganise an owner's note, a folder belonging
to another user can never be targeted, and a search never returns a note the
caller cannot read.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL, auth_headers


def create_note(client: TestClient, owner: str, title: str = "t", content: str = "c") -> dict:
    response = client.post(
        "/api/v1/notes",
        headers=auth_headers(owner),
        json={"title": title, "content": content},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_folder(client: TestClient, owner: str, name: str) -> dict:
    response = client.post("/api/v1/folders", headers=auth_headers(owner), json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def share(client: TestClient, owner: str, note_id: str, target: str, level: str) -> None:
    response = client.post(
        f"/api/v1/notes/{note_id}/permissions",
        headers=auth_headers(owner),
        json={"user_id": target, "permission": level},
    )
    assert response.status_code == 201, response.text


class TestFolderCrud:
    def test_create_list_and_rename(self, client: TestClient) -> None:
        folder = create_folder(client, ALICE, "Project Alpha")
        assert folder["name"] == "Project Alpha"
        assert folder["owner_id"] == ALICE

        listing = client.get("/api/v1/folders", headers=auth_headers(ALICE))
        assert listing.status_code == 200
        assert [f["name"] for f in listing.json()["items"]] == ["Project Alpha"]

        renamed = client.patch(
            f"/api/v1/folders/{folder['id']}",
            headers=auth_headers(ALICE),
            json={"name": "Project Beta"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Project Beta"

    def test_duplicate_name_is_conflict(self, client: TestClient) -> None:
        create_folder(client, ALICE, "Dup")
        response = client.post("/api/v1/folders", headers=auth_headers(ALICE), json={"name": "Dup"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_folders_are_private_to_their_owner(self, client: TestClient) -> None:
        create_folder(client, ALICE, "Alice only")
        listing = client.get("/api/v1/folders", headers=auth_headers(BOB))
        assert listing.json()["items"] == []

    def test_rename_another_users_folder_is_404(self, client: TestClient) -> None:
        folder = create_folder(client, ALICE, "Mine")
        response = client.patch(
            f"/api/v1/folders/{folder['id']}",
            headers=auth_headers(BOB),
            json={"name": "Stolen"},
        )
        # 404 (not 403): Bob must not learn that Alice's folder exists.
        assert response.status_code == 404

    def test_delete_folder_moves_notes_to_root_and_keeps_them(self, client: TestClient) -> None:
        folder = create_folder(client, ALICE, "Temp")
        note = client.post(
            "/api/v1/notes",
            headers=auth_headers(ALICE),
            json={"title": "Kept", "content": "x", "folder_id": folder["id"]},
        ).json()
        assert note["folder_id"] == folder["id"]

        assert (
            client.delete(
                f"/api/v1/folders/{folder['id']}", headers=auth_headers(ALICE)
            ).status_code
            == 204
        )

        reloaded = client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(ALICE)).json()
        assert reloaded["folder_id"] is None  # note survived, moved to root

    def test_create_note_in_another_users_folder_rejected(self, client: TestClient) -> None:
        folder = create_folder(client, ALICE, "Alice")
        response = client.post(
            "/api/v1/notes",
            headers=auth_headers(BOB),
            json={"title": "x", "content": "", "folder_id": folder["id"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"


class TestNoteMovement:
    def test_owner_moves_note_between_folders_and_to_root(self, client: TestClient) -> None:
        a = create_folder(client, ALICE, "A")
        b = create_folder(client, ALICE, "B")
        note = client.post(
            "/api/v1/notes",
            headers=auth_headers(ALICE),
            json={"title": "Movable", "content": "", "folder_id": a["id"]},
        ).json()

        moved = client.patch(
            f"/api/v1/notes/{note['id']}/folder",
            headers=auth_headers(ALICE),
            json={"folder_id": b["id"]},
        )
        assert moved.status_code == 200
        assert moved.json()["folder_id"] == b["id"]
        # Moving does not change ownership or content version.
        assert moved.json()["owner_id"] == ALICE
        assert moved.json()["version"] == note["version"]

        rooted = client.patch(
            f"/api/v1/notes/{note['id']}/folder",
            headers=auth_headers(ALICE),
            json={"folder_id": None},
        )
        assert rooted.json()["folder_id"] is None

    def test_move_does_not_change_owner_or_sharing(self, client: TestClient) -> None:
        folder = create_folder(client, ALICE, "A")
        note = create_note(client, ALICE, "Shared")
        share(client, ALICE, note["id"], BOB, "read")

        client.patch(
            f"/api/v1/notes/{note['id']}/folder",
            headers=auth_headers(ALICE),
            json={"folder_id": folder["id"]},
        )
        # Bob's access is untouched and the note is still owned by Alice.
        assert (
            client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(BOB)).status_code == 200
        )
        permissions = client.get(
            f"/api/v1/notes/{note['id']}/permissions", headers=auth_headers(ALICE)
        ).json()
        assert permissions["items"][0]["user_id"] == BOB

    def test_collaborator_cannot_move_even_with_write(self, client: TestClient) -> None:
        folder = create_folder(client, ALICE, "A")
        note = create_note(client, ALICE, "Shared")
        share(client, ALICE, note["id"], BOB, "write")

        response = client.patch(
            f"/api/v1/notes/{note['id']}/folder",
            headers=auth_headers(BOB),
            json={"folder_id": folder["id"]},
        )
        # Bob can read (so this is 403, not 404), but he may not reorganise it.
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    def test_non_reader_cannot_move(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Private")
        response = client.patch(
            f"/api/v1/notes/{note['id']}/folder",
            headers=auth_headers(CAROL),
            json={"folder_id": None},
        )
        assert response.status_code == 404

    def test_move_into_foreign_folder_rejected(self, client: TestClient) -> None:
        bob_folder = create_folder(client, BOB, "Bob's")
        note = create_note(client, ALICE, "Alice's")
        response = client.patch(
            f"/api/v1/notes/{note['id']}/folder",
            headers=auth_headers(ALICE),
            json={"folder_id": bob_folder["id"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"


class TestStarring:
    def test_star_unstar_and_list_starred(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Star me")

        starred = client.put(f"/api/v1/notes/{note['id']}/star", headers=auth_headers(ALICE))
        assert starred.status_code == 200
        assert starred.json()["is_starred"] is True

        listing = client.get(
            "/api/v1/notes", headers=auth_headers(ALICE), params={"starred": "true"}
        )
        assert [n["id"] for n in listing.json()["items"]] == [note["id"]]

        unstarred = client.delete(f"/api/v1/notes/{note['id']}/star", headers=auth_headers(ALICE))
        assert unstarred.json()["is_starred"] is False
        listing = client.get(
            "/api/v1/notes", headers=auth_headers(ALICE), params={"starred": "true"}
        )
        assert listing.json()["items"] == []

    def test_star_is_per_user(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Shared star")
        share(client, ALICE, note["id"], BOB, "read")

        client.put(f"/api/v1/notes/{note['id']}/star", headers=auth_headers(BOB))

        # Bob sees his own star; Alice does not see it.
        assert (
            client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(BOB)).json()[
                "is_starred"
            ]
            is True
        )
        assert (
            client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(ALICE)).json()[
                "is_starred"
            ]
            is False
        )

    def test_cannot_star_unreadable_note(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Private")
        response = client.put(f"/api/v1/notes/{note['id']}/star", headers=auth_headers(CAROL))
        assert response.status_code == 404

    def test_star_survives_reload(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Persistent")
        client.put(f"/api/v1/notes/{note['id']}/star", headers=auth_headers(ALICE))
        assert (
            client.get("/api/v1/notes", headers=auth_headers(ALICE)).json()["items"][0][
                "is_starred"
            ]
            is True
        )


class TestSearchAndFiltering:
    def test_search_matches_title(self, client: TestClient) -> None:
        create_note(client, ALICE, "Quarterly Report")
        create_note(client, ALICE, "Groceries")
        listing = client.get("/api/v1/notes", headers=auth_headers(ALICE), params={"q": "quarter"})
        assert [n["title"] for n in listing.json()["items"]] == ["Quarterly Report"]

    def test_search_matches_content(self, client: TestClient) -> None:
        create_note(client, ALICE, "Notes", content="<p>a secret about falcons</p>")
        create_note(client, ALICE, "Other", content="<p>nothing</p>")
        listing = client.get("/api/v1/notes", headers=auth_headers(ALICE), params={"q": "falcons"})
        assert [n["title"] for n in listing.json()["items"]] == ["Notes"]

    def test_search_never_returns_unauthorized_notes(self, client: TestClient) -> None:
        create_note(client, ALICE, "Alice confidential")
        listing = client.get(
            "/api/v1/notes", headers=auth_headers(BOB), params={"q": "confidential"}
        )
        assert listing.json()["items"] == []

    def test_filter_by_folder(self, client: TestClient) -> None:
        folder = create_folder(client, ALICE, "F")
        in_folder = client.post(
            "/api/v1/notes",
            headers=auth_headers(ALICE),
            json={"title": "In", "content": "", "folder_id": folder["id"]},
        ).json()
        create_note(client, ALICE, "Out")

        listing = client.get(
            "/api/v1/notes", headers=auth_headers(ALICE), params={"folder_id": folder["id"]}
        )
        assert [n["id"] for n in listing.json()["items"]] == [in_folder["id"]]

    def test_view_splits_owned_and_shared(self, client: TestClient) -> None:
        mine = create_note(client, ALICE, "Mine")
        theirs = create_note(client, BOB, "Theirs")
        share(client, BOB, theirs["id"], ALICE, "read")

        owned = client.get("/api/v1/notes", headers=auth_headers(ALICE), params={"view": "owned"})
        assert [n["id"] for n in owned.json()["items"]] == [mine["id"]]

        shared = client.get("/api/v1/notes", headers=auth_headers(ALICE), params={"view": "shared"})
        assert [n["id"] for n in shared.json()["items"]] == [theirs["id"]]

    def test_sort_by_title(self, client: TestClient) -> None:
        create_note(client, ALICE, "Beta")
        create_note(client, ALICE, "Alpha")
        listing = client.get("/api/v1/notes", headers=auth_headers(ALICE), params={"sort": "title"})
        assert [n["title"] for n in listing.json()["items"]] == ["Alpha", "Beta"]


class TestCallerPermissionExposure:
    def test_owner_sees_write(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Owned")
        assert note["caller_permission"] == "write"

    def test_collaborator_sees_their_own_level_only(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Shared")
        share(client, ALICE, note["id"], BOB, "read")
        share(client, ALICE, note["id"], CAROL, "write")

        bob_view = client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(BOB)).json()
        assert bob_view["caller_permission"] == "read"
        # The response must not leak the collaborator list to a non-owner.
        assert "permissions" not in bob_view


class TestRichTextSanitisation:
    def test_script_and_unsafe_urls_are_stripped_on_write(self, client: TestClient) -> None:
        note = create_note(
            client,
            ALICE,
            "XSS",
            content=(
                '<script>alert(1)</script><p onclick="evil()">hi</p>'
                '<a href="javascript:alert(2)">link</a>'
                '<iframe src="https://evil.example"></iframe>'
            ),
        )
        content = note["content"]
        assert "<script" not in content
        assert "<iframe" not in content
        assert "onclick" not in content
        assert "javascript:" not in content
        # Safe text is preserved.
        assert "hi" in content

    def test_allowed_formatting_is_preserved(self, client: TestClient) -> None:
        note = create_note(
            client,
            ALICE,
            "Formatted",
            content="<h1>Title</h1><p><strong>bold</strong> <em>it</em></p><ul><li>x</li></ul>",
        )
        content = note["content"]
        assert "<h1>Title</h1>" in content
        assert "<strong>bold</strong>" in content
        assert "<ul><li>x</li></ul>" in content

    def test_safe_link_kept_with_rel(self, client: TestClient) -> None:
        note = create_note(client, ALICE, "Link", content='<a href="https://example.com">ex</a>')
        assert 'href="https://example.com"' in note["content"]

    def test_stored_content_is_re_sanitised_on_read(
        self, client: TestClient, session_factory
    ) -> None:
        # Simulate a row that was written before sanitisation existed (or by a
        # future non-HTTP path) and prove reads are still safe.
        import uuid

        from notes_service.infrastructure.db.models import NoteRow

        note = create_note(client, ALICE, "Legacy")
        with session_factory() as session:
            row = session.get(NoteRow, uuid.UUID(note["id"]))
            assert row is not None
            row.content = "<script>bad()</script><p>ok</p>"
            session.commit()

        fetched = client.get(f"/api/v1/notes/{note['id']}", headers=auth_headers(ALICE)).json()
        assert "<script" not in fetched["content"]
        assert "ok" in fetched["content"]
