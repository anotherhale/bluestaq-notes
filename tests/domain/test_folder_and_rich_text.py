"""Domain unit tests for the Folder aggregate and rich-text sanitisation."""

from __future__ import annotations

import pytest

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.folder import Folder
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.rich_text import sanitize_html
from notes_service.domain.notes.value_objects import FolderId, FolderName, NoteContent

OWNER = UserId("11111111-1111-1111-1111-111111111111")
OTHER = UserId("22222222-2222-2222-2222-222222222222")


class TestFolder:
    def test_create_trims_and_owns(self) -> None:
        folder = Folder.create(owner_id=OWNER, name="  Reports  ")
        assert folder.name == "Reports"
        assert folder.is_owned_by(OWNER)
        assert not folder.is_owned_by(OTHER)

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Folder.create(owner_id=OWNER, name="   ")

    def test_overlong_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FolderName("x" * 101)

    def test_only_owner_may_rename(self) -> None:
        folder = Folder.create(owner_id=OWNER, name="A")
        with pytest.raises(InvariantViolation):
            folder.rename(actor_id=OTHER, name="B")

    def test_folder_id_parse_rejects_garbage(self) -> None:
        with pytest.raises(ValidationError):
            FolderId.parse("not-a-uuid")


class TestNotePlacement:
    def test_move_to_folder_only_by_owner(self) -> None:
        note = Note.create(owner_id=OWNER, title="t", content="")
        folder_id = FolderId.new()
        with pytest.raises(InvariantViolation):
            note.move_to_folder(actor_id=OTHER, folder_id=folder_id)

    def test_move_does_not_advance_content_version(self) -> None:
        note = Note.create(owner_id=OWNER, title="t", content="")
        before = note.version
        note.move_to_folder(actor_id=OWNER, folder_id=FolderId.new())
        assert note.version == before
        assert note.folder_id is not None

    def test_move_to_root_clears_folder(self) -> None:
        note = Note.create(owner_id=OWNER, title="t", content="", folder_id=FolderId.new())
        note.move_to_folder(actor_id=OWNER, folder_id=None)
        assert note.folder_id is None


class TestRichTextSanitisation:
    @pytest.mark.parametrize(
        "payload",
        [
            "<script>alert(1)</script>",
            '<img src="x" onerror="alert(1)">',
            '<iframe src="https://evil.example"></iframe>',
            '<a href="javascript:alert(1)">x</a>',
            '<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>',
            "<style>body{display:none}</style>",
        ],
    )
    def test_dangerous_markup_removed(self, payload: str) -> None:
        cleaned = sanitize_html(payload)
        for forbidden in ("<script", "<iframe", "<style", "onerror", "javascript:", "data:"):
            assert forbidden not in cleaned

    def test_plain_text_is_preserved(self) -> None:
        # Plain text is valid HTML with no markup, so legacy content is safe.
        assert NoteContent("just some text") == "just some text"

    def test_allowed_tags_survive(self) -> None:
        cleaned = sanitize_html("<h1>a</h1><ul><li>b</li></ul><blockquote>c</blockquote><hr>")
        assert "<h1>a</h1>" in cleaned
        assert "<ul><li>b</li></ul>" in cleaned
        assert "<blockquote>c</blockquote>" in cleaned
        assert "<hr" in cleaned

    def test_note_content_sanitises_on_construction(self) -> None:
        content = NoteContent("<p onclick='x'>ok</p><script>bad()</script>")
        assert "onclick" not in content
        assert "<script" not in content
        assert "ok" in content
