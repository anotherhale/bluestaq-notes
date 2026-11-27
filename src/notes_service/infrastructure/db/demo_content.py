"""Deterministic generation of a realistic demo dataset.

The demo dataset exists so the running service *looks and behaves* like a real
multi-team deployment: a roster of users grouped into teams, each user with a
few dozen notes, folders, stars, and a web of READ/WRITE shares that exercises
the authorization rules.

Two properties matter and are both guaranteed here:

* **Determinism.** Everything is derived from a SHA-256 counter-based generator
  seeded by stable strings, so the same code produces byte-identical data on
  every machine and every run. We deliberately avoid the ``random`` module
  (whose method output is not contractually stable across Python versions) and
  derive every identifier with :func:`uuid.uuid5`, so note/folder/permission ids
  are stable and reseeding is idempotent.
* **No schema invention.** "Teams" are a *demo grouping* that shapes the sharing
  graph — the domain has no Team entity and the assignment's schema has no teams
  table. A team is expressed purely through data: a lead owns a "team hub" note
  shared with the members, plus assorted 1:1 shares. This keeps the model honest
  rather than adding a first-class concept the product does not have.

The module builds **domain objects only**; persistence is the job of
:mod:`notes_service.infrastructure.db.seed`.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from notes_service.domain.identity.value_objects import UserId, UserStatus
from notes_service.domain.notes.folder import Folder
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission, NotePermissionId
from notes_service.domain.notes.star import NoteStar
from notes_service.domain.notes.value_objects import (
    FolderId,
    NoteId,
    NoteTitle,
    PermissionLevel,
)

# --------------------------------------------------------------------------
# Roster and teams
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DemoUserSpec:
    """A deterministic demo user used for local runs and tests."""

    email: str
    display_name: str
    # A stable UUID makes curl examples and docs reproducible.
    id: str
    status: UserStatus = UserStatus.ACTIVE
    # The demo team this user belongs to (a data grouping, not a domain entity).
    team: str | None = None


@dataclass(frozen=True, slots=True)
class DemoTeam:
    """A demo grouping that shapes the sharing graph."""

    name: str
    lead_email: str
    member_emails: tuple[str, ...]


# The original four users keep their exact ids and order: they are referenced by
# the test suite (``DEMO_USERS[0..3]``) and by the README's curl examples.
DEMO_USERS: tuple[DemoUserSpec, ...] = (
    DemoUserSpec(
        id="11111111-1111-1111-1111-111111111111",
        email="alice@example.com",
        display_name="Alice Anderson",
        team="Engineering",
    ),
    DemoUserSpec(
        id="22222222-2222-2222-2222-222222222222",
        email="bob@example.com",
        display_name="Bob Baker",
        team="Engineering",
    ),
    DemoUserSpec(
        id="33333333-3333-3333-3333-333333333333",
        email="carol@example.com",
        display_name="Carol Chen",
        team="Product",
    ),
    DemoUserSpec(
        id="44444444-4444-4444-4444-444444444444",
        email="dave@example.com",
        display_name="Dave Diaz",
        status=UserStatus.SUSPENDED,
    ),
    DemoUserSpec(
        id="55555555-5555-5555-5555-555555555555",
        email="priya@example.com",
        display_name="Priya Patel",
        team="Engineering",
    ),
    DemoUserSpec(
        id="66666666-6666-6666-6666-666666666666",
        email="miguel@example.com",
        display_name="Miguel Rivera",
        team="Product",
    ),
    DemoUserSpec(
        id="77777777-7777-7777-7777-777777777777",
        email="sofia@example.com",
        display_name="Sofia Rossi",
        team="Product",
    ),
    DemoUserSpec(
        id="88888888-8888-8888-8888-888888888888",
        email="dana@example.com",
        display_name="Dana Kim",
        team="Design",
    ),
    DemoUserSpec(
        id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        email="lena@example.com",
        display_name="Lena Novak",
        team="Design",
    ),
    DemoUserSpec(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        email="tariq@example.com",
        display_name="Tariq Hassan",
        team="Design",
    ),
    DemoUserSpec(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        email="omar@example.com",
        display_name="Omar Farouk",
        team="Operations",
    ),
    DemoUserSpec(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        email="grace@example.com",
        display_name="Grace Lee",
        team="Operations",
    ),
    DemoUserSpec(
        id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        email="yuki@example.com",
        display_name="Yuki Tanaka",
        team="Operations",
    ),
)

DEMO_TEAMS: tuple[DemoTeam, ...] = (
    DemoTeam(
        name="Engineering",
        lead_email="alice@example.com",
        member_emails=("bob@example.com", "priya@example.com"),
    ),
    DemoTeam(
        name="Product",
        lead_email="carol@example.com",
        member_emails=("miguel@example.com", "sofia@example.com"),
    ),
    DemoTeam(
        name="Design",
        lead_email="dana@example.com",
        member_emails=("lena@example.com", "tariq@example.com"),
    ),
    DemoTeam(
        name="Operations",
        lead_email="omar@example.com",
        member_emails=("grace@example.com", "yuki@example.com"),
    ),
)

MIN_NOTES_PER_USER = 20
MAX_NOTES_PER_USER = 50


# --------------------------------------------------------------------------
# Deterministic generator
# --------------------------------------------------------------------------

_NAMESPACE = uuid.UUID("6f6f6f6f-0000-4000-8000-000000000000")


def _uid(*parts: object) -> uuid.UUID:
    """A stable UUID derived from ``parts`` (reproducible across runs)."""
    return uuid.uuid5(_NAMESPACE, ":".join(str(part) for part in parts))


class _Rng:
    """A tiny counter-based deterministic generator.

    Each draw hashes ``seed:counter`` with SHA-256, so the sequence depends only
    on the seed string and the order of draws — never on the Python version or
    process state.
    """

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode("utf-8")
        self._counter = 0

    def _draw(self) -> int:
        digest = hashlib.sha256(self._seed + b":" + str(self._counter).encode()).digest()
        self._counter += 1
        return int.from_bytes(digest[:8], "big")

    def below(self, bound: int) -> int:
        """An integer in ``[0, bound)``."""
        return self._draw() % bound

    def between(self, low: int, high: int) -> int:
        """An integer in ``[low, high]`` inclusive."""
        return low + self.below(high - low + 1)

    def choice(self, items: tuple[str, ...] | list[str]) -> str:
        return items[self.below(len(items))]

    def sample(self, items: tuple[str, ...] | list[str], k: int) -> list[str]:
        """``k`` distinct items, order-stable for a given seed."""
        pool = list(items)
        picked: list[str] = []
        for _ in range(min(k, len(pool))):
            picked.append(pool.pop(self.below(len(pool))))
        return picked

    def chance(self, percent: int) -> bool:
        return self.below(100) < percent


@dataclass(frozen=True, slots=True)
class DemoDataset:
    """The generated demo graph, ready to persist."""

    folders: tuple[Folder, ...]
    notes: tuple[Note, ...]
    permissions: tuple[NotePermission, ...]
    stars: tuple[NoteStar, ...]


_TOPICS: tuple[str, ...] = (
    "auth service",
    "payments API",
    "search indexing",
    "mobile release",
    "design system",
    "data pipeline",
    "customer portal",
    "notification service",
    "billing",
    "observability",
    "kubernetes migration",
    "rate limiting",
    "SSO rollout",
    "mobile push",
    "analytics events",
    "on-call rotation",
    "incident review",
    "API versioning",
    "feature flags",
    "data retention",
    "accessibility audit",
    "load testing",
    "cost optimisation",
    "schema migration",
)

_FOLDER_NAMES: tuple[str, ...] = (
    "Meetings",
    "Projects",
    "Design Docs",
    "Runbooks",
    "1:1s",
    "Roadmap",
    "Research",
    "Retros",
    "Onboarding",
    "Archive",
)

_AGENDA: tuple[str, ...] = (
    "Review last week's action items",
    "Demo the current build",
    "Unblock the open question on scope",
    "Agree the rollout plan",
    "Prioritise the backlog",
    "Walk through the error budget",
    "Discuss the migration timeline",
    "Confirm owners for each workstream",
)

_DECISIONS: tuple[str, ...] = (
    "Ship behind a feature flag first",
    "Split the work into two milestones",
    "Keep the current API shape for now",
    "Adopt the proposal with the smaller blast radius",
    "Defer the optimisation to next quarter",
    "Run a two-week spike before committing",
)

_ACTIONS: tuple[str, ...] = (
    "Draft the design doc by Friday",
    "Add integration tests for the new path",
    "Follow up with the platform team",
    "Update the runbook with the new steps",
    "Write the migration script and dry-run it",
    "Book a review with security",
)

_OPEN_QUESTIONS: tuple[str, ...] = (
    "Do we need backwards compatibility for v1 clients?",
    "What is the rollback plan if the migration fails?",
    "Who owns the on-call runbook after handover?",
    "Is the current rate limit still appropriate?",
    "How do we measure success for this change?",
)

_RISKS: tuple[str, ...] = (
    "The dependency is not yet GA",
    "Peak traffic may exceed the current budget",
    "The migration touches a shared table",
    "Client version skew in the wild",
    "The vendor's SLA is unclear",
)


def _meeting_body(rng: _Rng, owner: str, peers: list[str], topic: str, when: datetime) -> str:
    attendees = ", ".join([owner, *peers]) if peers else owner
    agenda = "".join(f"<li>{item}</li>" for item in rng.sample(_AGENDA, 3))
    decisions = "".join(f"<li>{item}</li>" for item in rng.sample(_DECISIONS, 2))
    actions = "".join(f"<li>{item}</li>" for item in rng.sample(_ACTIONS, 2))
    return (
        f"<p>Meeting on {when:%d %b %Y} about the {topic}.</p>"
        f"<p><strong>Attendees:</strong> {attendees}</p>"
        f"<h2>Agenda</h2><ul>{agenda}</ul>"
        f"<h2>Decisions</h2><ul>{decisions}</ul>"
        f"<h2>Action items</h2><ul>{actions}</ul>"
    )


def _design_body(rng: _Rng, owner: str, topic: str) -> str:
    goals = "".join(f"<li>{item}</li>" for item in rng.sample(_AGENDA, 2))
    risks = "".join(f"<li>{item}</li>" for item in rng.sample(_RISKS, 2))
    questions = "".join(f"<li>{item}</li>" for item in rng.sample(_OPEN_QUESTIONS, 2))
    return (
        f"<h1>{topic.title()} — design</h1>"
        f"<p>Owner: {owner}. Status: draft.</p>"
        f"<h2>Goals</h2><ul>{goals}</ul>"
        f"<h2>Risks</h2><ul>{risks}</ul>"
        f"<h2>Open questions</h2><ul>{questions}</ul>"
    )


def _runbook_body(rng: _Rng, topic: str) -> str:
    steps = "".join(
        f"<li>{item}</li>"
        for item in rng.sample(
            (
                "Check the service dashboard for error rate",
                "Confirm the latest deploy succeeded",
                "Drain traffic from the affected node",
                "Roll back to the previous release",
                "Page the on-call engineer",
                "Post an update in the incident channel",
            ),
            4,
        )
    )
    return (
        f"<h1>Runbook: {topic}</h1>"
        "<p>Use this when the alert fires. Keep changes small and reversible.</p>"
        f"<ol>{steps}</ol>"
        "<blockquote>Escalate early rather than debugging alone.</blockquote>"
    )


def _retro_body(rng: _Rng, team: str) -> str:
    well = "".join(f"<li>{item}</li>" for item in rng.sample(_DECISIONS, 2))
    badly = "".join(f"<li>{item}</li>" for item in rng.sample(_RISKS, 2))
    actions = "".join(f"<li>{item}</li>" for item in rng.sample(_ACTIONS, 3))
    return (
        f"<h1>{team} retro</h1>"
        f"<h2>What went well</h2><ul>{well}</ul>"
        f"<h2>What did not</h2><ul>{badly}</ul>"
        f"<h2>Actions</h2><ul>{actions}</ul>"
    )


def _one_on_one_body(rng: _Rng, owner: str, peer: str, topic: str) -> str:
    topics = "".join(f"<li>{item}</li>" for item in rng.sample(_AGENDA, 2))
    follow = "".join(f"<li>{item}</li>" for item in rng.sample(_ACTIONS, 2))
    return (
        f"<h1>1:1 — {owner} &amp; {peer}</h1>"
        f"<p>Talking point: {topic}.</p>"
        f"<h2>Topics</h2><ul>{topics}</ul>"
        f"<h2>Follow-ups</h2><ul>{follow}</ul>"
    )


def _note_body(
    rng: _Rng, owner: str, team: str, peers: list[str], topic: str, when: datetime
) -> str:
    variant = rng.below(100)
    if variant < 40:
        return _meeting_body(rng, owner, peers, topic, when)
    if variant < 65:
        return _design_body(rng, owner, topic)
    if variant < 80:
        return _runbook_body(rng, topic)
    if variant < 92:
        return _retro_body(rng, team)
    return _one_on_one_body(rng, owner, peers[0] if peers else owner, topic)


def _title(rng: _Rng, topic: str) -> str:
    patterns: tuple[str, ...] = (
        "{t} — weekly sync",
        "{t} design doc",
        "Meeting notes: {t}",
        "{t} runbook",
        "RFC: {t}",
        "{t} rollout plan",
        "Notes on {t}",
        "{t} — open questions",
        "{t} retrospective",
        "Spike: {t}",
    )
    return rng.choice(patterns).format(t=topic.title())


def _display_name_by_email() -> dict[str, str]:
    return {spec.email: spec.display_name for spec in DEMO_USERS}


def _user_id(email: str) -> UserId:
    """The stable demo ``UserId`` for ``email`` (all demo users have one)."""
    return UserId(next(spec.id for spec in DEMO_USERS if spec.email == email))


def _team_by_email() -> dict[str, DemoTeam]:
    membership: dict[str, DemoTeam] = {}
    for team in DEMO_TEAMS:
        for email in (team.lead_email, *team.member_emails):
            membership[email] = team
    return membership


def build_demo_dataset(*, now: datetime | None = None) -> DemoDataset:
    """Generate the full demo dataset deterministically."""
    reference = now or datetime.now(UTC)
    names = _display_name_by_email()
    teams = _team_by_email()
    active = [spec for spec in DEMO_USERS if spec.status is UserStatus.ACTIVE]

    folders: list[Folder] = []
    notes: list[Note] = []
    permissions: list[NotePermission] = []
    stars: list[NoteStar] = []

    notes_by_email: dict[str, list[Note]] = {}

    for spec in active:
        rng = _Rng(f"user:{spec.email}")
        owner_id = UserId(spec.id)
        team = teams[spec.email]

        # -- folders ------------------------------------------------------
        user_folders: list[Folder] = []
        for name in rng.sample(_FOLDER_NAMES, 3):
            folder = Folder.create(
                owner_id=owner_id,
                name=name,
                folder_id=FolderId(str(_uid("folder", spec.email, name))),
            )
            user_folders.append(folder)
        folders.extend(user_folders)

        # -- notes --------------------------------------------------------
        note_count = rng.between(MIN_NOTES_PER_USER, MAX_NOTES_PER_USER)
        peers = [names[email] for email in (*team.member_emails, team.lead_email)]
        peers = [name for name in peers if name != spec.display_name]
        user_notes: list[Note] = []
        for index in range(note_count):
            topic = rng.choice(_TOPICS)
            note_folder = user_folders[rng.below(len(user_folders))] if rng.chance(70) else None
            created = reference - timedelta(
                days=rng.between(0, 120),
                hours=rng.between(0, 23),
                minutes=rng.between(0, 59),
            )
            updated = min(created + timedelta(hours=rng.between(1, 240)), reference)
            note = Note.create(
                owner_id=owner_id,
                title=_title(rng, topic),
                content=_note_body(rng, spec.display_name, team.name, peers, topic, created),
                note_id=NoteId(str(_uid("note", spec.email, index))),
                folder_id=note_folder.id if note_folder is not None else None,
            )
            note.created_at = created
            note.updated_at = updated
            user_notes.append(note)
            notes.append(note)
        notes_by_email[spec.email] = user_notes

        # -- stars (personal) --------------------------------------------
        for starred_id in rng.sample([str(n.id) for n in user_notes], rng.between(2, 5)):
            stars.append(NoteStar(note_id=NoteId(starred_id), user_id=owner_id))

    # -- sharing ----------------------------------------------------------
    # Each team lead owns a "team hub" note shared WRITE with every member, so
    # every member sees something from their team in "Shared with me". Then the
    # lead shares a handful of their own notes with each member (READ or WRITE),
    # and members share a couple back — a realistic bidirectional web.
    for team in DEMO_TEAMS:
        lead_notes = notes_by_email[team.lead_email]
        lead_id = _user_id(team.lead_email)
        hub = lead_notes[0]
        hub.title = NoteTitle(f"{team.name} — team hub")
        for member_email in team.member_emails:
            member_id = _user_id(member_email)
            permissions.append(_permission(hub.id, member_id, PermissionLevel.WRITE, lead_id))

        for member_email in team.member_emails:
            member_id = _user_id(member_email)
            share_rng = _Rng(f"share:{team.lead_email}->{member_email}")
            for note_id in share_rng.sample([str(n.id) for n in lead_notes[1:]], 3):
                level = PermissionLevel.WRITE if share_rng.chance(35) else PermissionLevel.READ
                permissions.append(_permission(NoteId(note_id), member_id, level, lead_id))
            member_notes = notes_by_email[member_email]
            for note_id in share_rng.sample([str(n.id) for n in member_notes], 2):
                level = PermissionLevel.WRITE if share_rng.chance(25) else PermissionLevel.READ
                permissions.append(_permission(NoteId(note_id), lead_id, level, member_id))

    # A few cross-team shares, so sharing is not only intra-team.
    cross_rng = _Rng("share:cross-team")
    for spec in active:
        shared_note = notes_by_email[spec.email][1]
        other = cross_rng.choice([s.email for s in active if s.team != spec.team])
        permissions.append(
            _permission(shared_note.id, _user_id(other), PermissionLevel.READ, UserId(spec.id))
        )

    return DemoDataset(
        folders=tuple(folders),
        notes=tuple(notes),
        permissions=tuple(permissions),
        stars=tuple(stars),
    )


def _permission(
    note_id: NoteId,
    user_id: UserId,
    level: PermissionLevel,
    granted_by: UserId,
) -> NotePermission:
    # (note_id, user_id) is unique, so this id is stable and collision-free.
    return NotePermission(
        id=NotePermissionId(str(_uid("perm", note_id, user_id))),
        note_id=note_id,
        user_id=user_id,
        level=level,
        granted_by=granted_by,
    )


__all__ = [
    "DEMO_TEAMS",
    "DEMO_USERS",
    "MAX_NOTES_PER_USER",
    "MIN_NOTES_PER_USER",
    "DemoDataset",
    "DemoTeam",
    "DemoUserSpec",
    "build_demo_dataset",
]
