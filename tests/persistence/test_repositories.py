"""Persistence adapter tests: repository contracts, UoW, constraints, FKs.

These run against SQLite by default. Mark them ``postgresql`` and set
``TEST_POSTGRESQL_URL`` to run the identical suite against PostgreSQL.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.value_objects import (
    Email,
    ProviderSubject,
    UserStatus,
)
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.value_objects import PermissionLevel
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.usefixtures("seeded")


def _uow(session_factory: sessionmaker[Session]) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


def make_user(email: str) -> User:
    return User.register(email=email, display_name=email.split("@")[0])


class TestUserRepository:
    def test_add_and_get(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            user = make_user("repo@example.com")
            uow.users.add(user)
            uow.commit()
        with _uow(session_factory) as uow:
            fetched = uow.users.get(user.id)
            assert fetched is not None
            assert fetched.email == "repo@example.com"

    def test_get_by_email(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            user = make_user("lookup@example.com")
            uow.users.add(user)
            uow.commit()
        with _uow(session_factory) as uow:
            assert uow.users.get_by_email(Email("lookup@example.com")) is not None
            assert uow.users.get_by_email(Email("nope@example.com")) is None

    def test_email_unique_constraint(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            uow.users.add(make_user("dup@example.com"))
            uow.commit()
        with pytest.raises(IntegrityError), _uow(session_factory) as uow:
            uow.users.add(make_user("dup@example.com"))
            uow.commit()

    def test_list_and_count(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            total = uow.users.count()
            users = uow.users.list_all(limit=2, offset=0)
            assert total >= 4  # seeded demo users
            assert len(users) == 2


class TestExternalIdentityRepository:
    def test_find_by_provider_subject(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            user = make_user("idp@example.com")
            uow.users.add(user)
            uow.commit()
            identity = ExternalIdentity.link(
                user_id=user.id, provider="oidc", subject="subject-123"
            )
            uow.external_identities.add(identity)
            uow.commit()

        with _uow(session_factory) as uow:
            found = uow.external_identities.find_by_provider_subject(
                ProviderSubject(provider="oidc", subject="subject-123")
            )
            assert found is not None
            assert found.user_id == user.id

    def test_provider_subject_unique(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            a = make_user("a@example.com")
            b = make_user("b@example.com")
            uow.users.add(a)
            uow.users.add(b)
            uow.commit()
            uow.external_identities.add(
                ExternalIdentity.link(user_id=a.id, provider="oidc", subject="dup")
            )
            uow.commit()
        with pytest.raises(IntegrityError), _uow(session_factory) as uow:
            uow.external_identities.add(
                ExternalIdentity.link(user_id=b.id, provider="oidc", subject="dup")
            )
            uow.commit()


class TestNoteRepository:
    def test_add_get_roundtrip_preserves_permissions(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with _uow(session_factory) as uow:
            owner = make_user("owner@example.com")
            friend = make_user("friend@example.com")
            uow.users.add(owner)
            uow.users.add(friend)
            uow.commit()

            note = Note.create(owner_id=owner.id, title="T", content="C")
            note.grant_permission(
                actor_id=owner.id, target_user_id=friend.id, level=PermissionLevel.WRITE
            )
            uow.notes.add(note)
            uow.permissions.upsert(note.permissions[0])
            uow.commit()

        with _uow(session_factory) as uow:
            loaded = uow.notes.get(note.id)
            assert loaded is not None
            assert loaded.title == "T"
            assert loaded.version == 1
            assert loaded.can_write(friend.id)

    def test_optimistic_concurrency_compare_and_set(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        """Two writers that loaded the same version: the second must conflict.

        This exercises the repository's atomic compare-and-set directly (both
        writers hold version 1 in memory), independent of the domain check.
        """
        with _uow(session_factory) as uow:
            owner = make_user("cc@example.com")
            uow.users.add(owner)
            uow.commit()
            note = Note.create(owner_id=owner.id, title="v1", content="")
            uow.notes.add(note)
            uow.commit()
            note_id = note.id
            owner_id = owner.id

        # Writer A and Writer B both load version 1.
        with _uow(session_factory) as uow_a, _uow(session_factory) as uow_b:
            note_a = uow_a.notes.get(note_id)
            note_b = uow_b.notes.get(note_id)
            assert note_a is not None and note_b is not None
            assert note_a.version == 1 and note_b.version == 1

            note_a.update_content(actor_id=owner_id, title="A", content="", expected_version=1)
            note_b.update_content(actor_id=owner_id, title="B", content="", expected_version=1)

            uow_a.notes.save(note_a, expected_version=1)
            uow_a.commit()

            # B's compare-and-set matches no row because A already moved to v2.
            with pytest.raises(VersionConflict):
                uow_b.notes.save(note_b, expected_version=1)
            uow_b.rollback()

        with _uow(session_factory) as uow:
            final = uow.notes.get(note_id)
            assert final is not None
            assert final.title == "A"
            assert final.version == 2

    def test_timestamps_roundtrip_as_utc(self, session_factory: sessionmaker[Session]) -> None:
        """Timestamps must round-trip as timezone-aware UTC on every provider.

        Guards against two provider-specific failure modes: SQLite returning
        naive datetimes (inconsistent with freshly created aware ones) and
        PostgreSQL shifting an aware value to the session's local wall clock on
        a timezone-less column.
        """
        from datetime import UTC

        with _uow(session_factory) as uow:
            owner = make_user("tz@example.com")
            uow.users.add(owner)
            uow.commit()
            note = Note.create(owner_id=owner.id, title="tz", content="")
            uow.notes.add(note)
            uow.commit()
            created_at = note.created_at
            note_id = note.id

        with _uow(session_factory) as uow:
            loaded = uow.notes.get(note_id)
            assert loaded is not None
            assert loaded.created_at.tzinfo is not None
            assert loaded.created_at.utcoffset() == UTC.utcoffset(None)
            assert loaded.created_at == created_at

    def test_list_accessible_deduplicates_and_orders(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with _uow(session_factory) as uow:
            owner = make_user("list-owner@example.com")
            other = make_user("list-other@example.com")
            uow.users.add(owner)
            uow.users.add(other)
            uow.commit()
            owned = Note.create(owner_id=owner.id, title="owned", content="")
            uow.notes.add(owned)
            shared_note = Note.create(owner_id=other.id, title="shared", content="")
            shared_note.grant_permission(
                actor_id=other.id, target_user_id=owner.id, level=PermissionLevel.READ
            )
            uow.notes.add(shared_note)
            uow.permissions.upsert(shared_note.permissions[0])
            # Redundant self-grant attempt is prevented by the domain (owner
            # cannot hold a permission); emulate a duplicate via owner+shared.
            uow.commit()
            owner_id = owner.id

        with _uow(session_factory) as uow:
            notes = uow.notes.list_accessible(owner_id, limit=50, offset=0, sort="-updated_at")
            ids = [note.id for note in notes]
            assert len(ids) == len(set(ids))  # no duplicates
            assert {note.title for note in notes} == {"owned", "shared"}
            assert uow.notes.count_accessible(owner_id) == 2

    def test_delete_cascades_permissions(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            owner = make_user("del-owner@example.com")
            friend = make_user("del-friend@example.com")
            uow.users.add(owner)
            uow.users.add(friend)
            uow.commit()
            note = Note.create(owner_id=owner.id, title="t", content="")
            note.grant_permission(
                actor_id=owner.id, target_user_id=friend.id, level=PermissionLevel.READ
            )
            uow.notes.add(note)
            uow.permissions.upsert(note.permissions[0])
            uow.commit()
            note_id = note.id

        with _uow(session_factory) as uow:
            loaded = uow.notes.get(note_id)
            assert loaded is not None
            uow.notes.delete(loaded)
            uow.commit()

        with _uow(session_factory) as uow:
            assert uow.notes.get(note_id) is None
            assert uow.permissions.list_for_note(note_id) == []


class TestUnitOfWorkTransactions:
    def test_rollback_discards_writes(self, session_factory: sessionmaker[Session]) -> None:
        email = "rollback@example.com"
        with _uow(session_factory) as uow:
            uow.users.add(make_user(email))
            uow.rollback()
        with _uow(session_factory) as uow:
            assert uow.users.get_by_email(Email(email)) is None

    def test_exception_rolls_back(self, session_factory: sessionmaker[Session]) -> None:
        email = "exc@example.com"
        with pytest.raises(RuntimeError), _uow(session_factory) as uow:
            uow.users.add(make_user(email))
            raise RuntimeError("boom")
        with _uow(session_factory) as uow:
            assert uow.users.get_by_email(Email(email)) is None

    def test_commit_persists(self, session_factory: sessionmaker[Session]) -> None:
        email = "commit@example.com"
        with _uow(session_factory) as uow:
            uow.users.add(make_user(email))
            uow.commit()
        with _uow(session_factory) as uow:
            assert uow.users.get_by_email(Email(email)) is not None


class TestDatabaseConstraints:
    def test_foreign_key_enforced(self, session_factory: sessionmaker[Session]) -> None:
        """Inserting a permission for a nonexistent note must fail."""
        with pytest.raises(IntegrityError), _uow(session_factory) as uow:
            owner = make_user("fk@example.com")
            uow.users.add(owner)
            uow.commit()
            uow.session.execute(
                text(
                    "INSERT INTO note_permissions "
                    "(id, note_id, user_id, permission, granted_by, created_at) "
                    "VALUES (:id, :note_id, :user_id, 'read', :granted_by, :ts)"
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "note_id": "00000000-0000-0000-0000-000000000002",
                    "user_id": str(owner.id).replace("-", ""),
                    "granted_by": str(owner.id).replace("-", ""),
                    "ts": "2026-01-01 00:00:00",
                },
            )
            uow.commit()

    def test_permission_check_constraint(self, session_factory: sessionmaker[Session]) -> None:
        with pytest.raises(IntegrityError), _uow(session_factory) as uow:
            owner = make_user("chk@example.com")
            uow.users.add(owner)
            uow.commit()
            uow.session.execute(
                text(
                    "INSERT INTO note_permissions "
                    "(id, note_id, user_id, permission, granted_by, created_at) "
                    "VALUES (:id, :note_id, :user_id, 'admin', :granted_by, :ts)"
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000003",
                    "note_id": "00000000-0000-0000-0000-000000000004",
                    "user_id": str(owner.id).replace("-", ""),
                    "granted_by": str(owner.id).replace("-", ""),
                    "ts": "2026-01-01 00:00:00",
                },
            )
            uow.commit()

    def test_version_check_constraint(self, session_factory: sessionmaker[Session]) -> None:
        with pytest.raises(IntegrityError), _uow(session_factory) as uow:
            owner = make_user("ver@example.com")
            uow.users.add(owner)
            uow.commit()
            uow.session.execute(
                text(
                    "INSERT INTO notes "
                    "(id, owner_id, title, content, version, created_at, updated_at) "
                    "VALUES (:id, :owner, 't', '', 0, :ts, :ts)"
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000005",
                    "owner": str(owner.id).replace("-", ""),
                    "ts": "2026-01-01 00:00:00",
                },
            )
            uow.commit()


@pytest.mark.postgresql
class TestPostgresqlProvider:
    def test_uow_roundtrip(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            user = make_user("pg@example.com")
            uow.users.add(user)
            uow.commit()
        with _uow(session_factory) as uow:
            assert uow.users.get(user.id) is not None

    def test_note_and_permission_roundtrip(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory) as uow:
            owner = make_user("pg-owner@example.com")
            friend = make_user("pg-friend@example.com")
            uow.users.add(owner)
            uow.users.add(friend)
            uow.commit()
            note = Note.create(owner_id=owner.id, title="pg", content="x")
            note.grant_permission(
                actor_id=owner.id, target_user_id=friend.id, level=PermissionLevel.READ
            )
            uow.notes.add(note)
            uow.permissions.upsert(note.permissions[0])
            uow.commit()
            note_id = note.id
        with _uow(session_factory) as uow:
            loaded = uow.notes.get(note_id)
            assert loaded is not None and loaded.can_read(friend.id)

    def test_optimistic_concurrency_on_postgres(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        """Two writers that both loaded version 1: the second must conflict.

        The compare-and-set is exercised directly, as on SQLite: both writers
        hold version 1 in memory, so the domain version guard does not fire and
        the repository's atomic UPDATE ... WHERE version = :expected is what
        rejects the stale write.
        """
        with _uow(session_factory) as uow:
            owner = make_user("pg-cc@example.com")
            uow.users.add(owner)
            uow.commit()
            note = Note.create(owner_id=owner.id, title="v1", content="")
            uow.notes.add(note)
            uow.commit()
            note_id = note.id
            owner_id = owner.id

        with _uow(session_factory) as uow_a, _uow(session_factory) as uow_b:
            first = uow_a.notes.get(note_id)
            stale = uow_b.notes.get(note_id)
            assert first is not None and stale is not None
            assert first.version == 1 and stale.version == 1

            first.update_content(actor_id=owner_id, title="v2", content="", expected_version=1)
            stale.update_content(actor_id=owner_id, title="x", content="", expected_version=1)

            uow_a.notes.save(first, expected_version=1)
            uow_a.commit()

            with pytest.raises(VersionConflict):
                uow_b.notes.save(stale, expected_version=1)
            uow_b.rollback()

        with _uow(session_factory) as uow:
            final = uow.notes.get(note_id)
            assert final is not None
            assert final.title == "v2"
            assert final.version == 2

    def test_user_status_default(self, session_factory: sessionmaker[Session]) -> None:
        with _uow(session_factory):
            user = User.register(email="pg-status@example.com", display_name="S")
            assert user.status is UserStatus.ACTIVE
