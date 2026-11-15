"""Tests for the generated demo dataset.

These assert the properties the demo data must have to be useful *and* honest:
it is deterministic, idempotent, sized within the required 20-50 notes per user,
and its sharing graph is internally consistent (no owner holds a grant on their
own note, every grant references real rows, and shared notes are reachable).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.notes.value_objects import PermissionLevel
from notes_service.infrastructure.db.demo_content import (
    DEMO_TEAMS,
    DEMO_USERS,
    MAX_NOTES_PER_USER,
    MIN_NOTES_PER_USER,
    build_demo_dataset,
)
from notes_service.infrastructure.db.models import (
    NotePermissionRow,
    NoteRow,
    UserRow,
)
from notes_service.infrastructure.db.seed import seed_demo_data

_REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


def _email_to_id() -> dict[str, uuid.UUID]:
    return {spec.email: uuid.UUID(spec.id) for spec in DEMO_USERS}


class TestGenerator:
    def test_build_is_deterministic(self) -> None:
        first = build_demo_dataset(now=_REFERENCE)
        second = build_demo_dataset(now=_REFERENCE)
        assert [n.id for n in first.notes] == [n.id for n in second.notes]
        assert [str(n.title) for n in first.notes] == [str(n.title) for n in second.notes]
        assert [str(n.content) for n in first.notes] == [str(n.content) for n in second.notes]
        assert [p.id for p in first.permissions] == [p.id for p in second.permissions]

    def test_ids_are_unique(self) -> None:
        dataset = build_demo_dataset(now=_REFERENCE)
        note_ids = [n.id for n in dataset.notes]
        assert len(note_ids) == len(set(note_ids))
        pairs = [(p.note_id, p.user_id) for p in dataset.permissions]
        assert len(pairs) == len(set(pairs))

    def test_no_owner_holds_a_permission_on_their_own_note(self) -> None:
        dataset = build_demo_dataset(now=_REFERENCE)
        owners = {n.id: n.owner_id for n in dataset.notes}
        for permission in dataset.permissions:
            assert permission.user_id != owners[permission.note_id]


class TestSeededDataset:
    def test_every_active_user_has_20_to_50_notes(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory() as session:
            seed_demo_data(session, now=_REFERENCE)
            session.commit()

            rows = session.execute(
                select(UserRow.email, UserRow.status, func.count(NoteRow.id))
                .outerjoin(NoteRow, NoteRow.owner_id == UserRow.id)
                .group_by(UserRow.email, UserRow.status)
            ).all()

            active_counts = {email: count for email, status, count in rows if status == "active"}
            for spec in DEMO_USERS:
                if spec.status.value == "active":
                    assert MIN_NOTES_PER_USER <= active_counts[spec.email] <= MAX_NOTES_PER_USER

    def test_suspended_user_owns_no_notes(self, session_factory: sessionmaker[Session]) -> None:
        with session_factory() as session:
            seed_demo_data(session, now=_REFERENCE)
            session.commit()
            suspended_ids = {
                uuid.UUID(spec.id) for spec in DEMO_USERS if spec.status.value != "active"
            }
            count = session.execute(
                select(func.count()).select_from(NoteRow).where(NoteRow.owner_id.in_(suspended_ids))
            ).scalar_one()
            assert count == 0

    def test_seeding_is_idempotent(self, session_factory: sessionmaker[Session]) -> None:
        with session_factory() as session:
            seed_demo_data(session, now=_REFERENCE)
            session.commit()
            first = session.execute(select(func.count()).select_from(NoteRow)).scalar_one()
            perms = session.execute(
                select(func.count()).select_from(NotePermissionRow)
            ).scalar_one()

        with session_factory() as session:
            seed_demo_data(session, now=_REFERENCE)
            session.commit()
            assert session.execute(select(func.count()).select_from(NoteRow)).scalar_one() == first
            assert (
                session.execute(select(func.count()).select_from(NotePermissionRow)).scalar_one()
                == perms
            )

    def test_every_permission_references_a_real_note_and_user(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory() as session:
            seed_demo_data(session, now=_REFERENCE)
            session.commit()
            note_ids = set(session.execute(select(NoteRow.id)).scalars())
            user_ids = set(session.execute(select(UserRow.id)).scalars())
            for row in session.execute(select(NotePermissionRow)).scalars():
                assert row.note_id in note_ids
                assert row.user_id in user_ids
                assert row.granted_by in user_ids
                assert row.permission in {PermissionLevel.READ.value, PermissionLevel.WRITE.value}

    def test_each_team_hub_is_shared_with_every_member(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        ids = _email_to_id()
        with session_factory() as session:
            seed_demo_data(session, now=_REFERENCE)
            session.commit()
            for team in DEMO_TEAMS:
                hub = session.execute(
                    select(NoteRow)
                    .where(NoteRow.owner_id == ids[team.lead_email])
                    .where(NoteRow.title.like(f"{team.name} — team hub"))
                ).scalar_one()
                granted_to = set(
                    session.execute(
                        select(NotePermissionRow.user_id)
                        .where(NotePermissionRow.note_id == hub.id)
                        .where(NotePermissionRow.permission == PermissionLevel.WRITE.value)
                    ).scalars()
                )
                for member_email in team.member_emails:
                    assert ids[member_email] in granted_to

    def test_members_receive_shared_notes_from_their_lead(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        ids = _email_to_id()
        with session_factory() as session:
            seed_demo_data(session, now=_REFERENCE)
            session.commit()
            for team in DEMO_TEAMS:
                member_id = ids[team.member_emails[0]]
                shared = session.execute(
                    select(func.count())
                    .select_from(NotePermissionRow)
                    .join(NoteRow, NoteRow.id == NotePermissionRow.note_id)
                    .where(NotePermissionRow.user_id == member_id)
                    .where(NoteRow.owner_id == ids[team.lead_email])
                ).scalar_one()
                assert shared >= 1


@pytest.mark.parametrize("provider_email", ["alice@example.com", "carol@example.com"])
def test_sharing_is_bidirectional_within_a_team(
    session_factory: sessionmaker[Session], provider_email: str
) -> None:
    """A lead both shares with, and receives shares from, their members."""
    ids = _email_to_id()
    team = next(t for t in DEMO_TEAMS if t.lead_email == provider_email)
    with session_factory() as session:
        seed_demo_data(session, now=_REFERENCE)
        session.commit()
        lead_id = ids[team.lead_email]
        member_id = ids[team.member_emails[0]]
        lead_shares_out = session.execute(
            select(func.count())
            .select_from(NotePermissionRow)
            .join(NoteRow, NoteRow.id == NotePermissionRow.note_id)
            .where(NotePermissionRow.user_id == member_id)
            .where(NoteRow.owner_id == lead_id)
        ).scalar_one()
        member_shares_back = session.execute(
            select(func.count())
            .select_from(NotePermissionRow)
            .join(NoteRow, NoteRow.id == NotePermissionRow.note_id)
            .where(NotePermissionRow.user_id == lead_id)
            .where(NoteRow.owner_id == member_id)
        ).scalar_one()
        assert lead_shares_out >= 1
        assert member_shares_back >= 1
