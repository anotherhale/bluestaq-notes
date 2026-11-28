"""Cross-boundary security test: organization administration is not content access.

This is the invariant that the whole admin-console design rests on. Two separate
authorization ports answer two different questions:

* ``OrganizationAuthorizationProvider`` — may this person administer the org's
  *structure and membership*?
* ``AuthorizationProvider`` — may this person read or edit a note's *content*?

The classic enterprise bug is letting the first quietly answer the second:
"org admin" silently becoming "can read everyone's private documents". These
tests pin the separation with a real database, real adapters and both real
policies — nothing is mocked, so a regression that starts consulting org roles
from the note policy fails here.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied, ResourceNotFound
from notes_service.application.ports.authorization import Action, AuthorizationProvider
from notes_service.application.ports.organization_authorization import (
    OrganizationAuthorizationProvider,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.notes import (
    CreateNote,
    DeleteNote,
    GetNote,
    ListNotes,
    UpdateNote,
)
from notes_service.application.use_cases.organization import (
    ChangeMemberRole,
    CreateOrganization,
    InviteOrganizationMember,
    ListOrganizationMembers,
    ListOrganizationUnits,
)
from notes_service.application.use_cases.organization_policy import (
    DefaultOrganizationAuthorizationProvider,
)
from notes_service.application.use_cases.permissions import ShareNote
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.domain.notes.value_objects import PermissionLevel
from notes_service.domain.organization.value_objects import OrganizationRole
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)
CAROL = UserId(DEMO_USERS[2].id)


def principal_for(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email(f"{str(user_id)[:8]}@example.com"),
        display_name="X",
        provider="demo",
        subject=str(user_id),
    )


class Harness:
    """Every use case over one session factory, wired with the real policies."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)

    def _make(self, cls):  # type: ignore[no-untyped-def]
        """Organization use cases get both policies; note use cases cannot.

        Note use cases' constructors have no ``organization_authorization``
        parameter at all — the separation is structural, not conventional.
        """
        return cls(
            uow_factory=self._uow,
            authorization=DefaultAuthorizationProvider(),
            organization_authorization=DefaultOrganizationAuthorizationProvider(),
            clock=SystemClock(),
        )

    def _make_note(self, cls):  # type: ignore[no-untyped-def]
        return cls(
            uow_factory=self._uow,
            authorization=DefaultAuthorizationProvider(),
            clock=SystemClock(),
        )

    # Organization administration
    def create_org(self, principal: Principal, *, name: str, slug: str):  # type: ignore[no-untyped-def]
        return self._make(CreateOrganization).run(principal, name=name, slug=slug)

    def invite(self, principal: Principal, **kwargs):  # type: ignore[no-untyped-def]
        return self._make(InviteOrganizationMember).run(principal, **kwargs)

    def change_role(self, principal: Principal, **kwargs):  # type: ignore[no-untyped-def]
        return self._make(ChangeMemberRole).run(principal, **kwargs)

    def list_members(self, principal: Principal, **kwargs):  # type: ignore[no-untyped-def]
        return self._make(ListOrganizationMembers).run(principal, **kwargs)

    def list_units(self, principal: Principal, **kwargs):  # type: ignore[no-untyped-def]
        return self._make(ListOrganizationUnits).run(principal, **kwargs)

    # Note content
    def create_note(self, principal: Principal, *, title: str, content: str):  # type: ignore[no-untyped-def]
        return self._make_note(CreateNote).run(principal, title=title, content=content)

    def get_note(self, principal: Principal, *, note_id):  # type: ignore[no-untyped-def]
        return self._make_note(GetNote).run(principal, note_id=note_id)

    def list_notes(self, principal: Principal):  # type: ignore[no-untyped-def]
        return self._make_note(ListNotes).run(principal)

    def update_note(self, principal: Principal, **kwargs):  # type: ignore[no-untyped-def]
        return self._make_note(UpdateNote).run(principal, **kwargs)

    def delete_note(self, principal: Principal, *, note_id):  # type: ignore[no-untyped-def]
        return self._make_note(DeleteNote).run(principal, note_id=note_id)

    def share_note(self, principal: Principal, **kwargs):  # type: ignore[no-untyped-def]
        return self._make_note(ShareNote).run(principal, **kwargs)


@pytest.fixture
def harness(session_factory: sessionmaker[Session]) -> Harness:
    return Harness(session_factory)


@pytest.fixture
def org_with_bob_as_admin(harness: Harness):  # type: ignore[no-untyped-def]
    """Alice owns the organization; Bob is promoted to ORG_ADMIN.

    Bob is now a genuine organization administrator: he can manage units, teams,
    memberships and roles. The tests below prove that grants him nothing over
    note *content*.
    """
    alice = principal_for(ALICE)
    created = harness.create_org(alice, name="Acme Corp", slug="acme")
    org_id = created.organization.id
    harness.invite(alice, organization_id=org_id, user_id=BOB, role=OrganizationRole.ORG_MEMBER)
    members = harness.list_members(alice, organization_id=org_id)
    bob_membership = next(m for m in members.items if m.user_id == str(BOB))
    harness.change_role(
        alice,
        organization_id=org_id,
        membership_id=bob_membership.id,
        role=OrganizationRole.ORG_ADMIN,
    )
    return created


class TestOrgAdminHasNoContentAccess:
    def test_org_admin_cannot_read_another_members_private_note(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The core invariant: administering the org never exposes private notes."""
        alice_note = harness.create_note(
            principal_for(ALICE), title="Board compensation", content="confidential"
        )

        # Bob really is an admin — he can administer the organization ...
        assert harness.list_members(
            principal_for(BOB), organization_id=org_with_bob_as_admin.organization.id
        ).items

        # ... but the note is invisible to him, indistinguishable from absent.
        with pytest.raises(ResourceNotFound):
            harness.get_note(principal_for(BOB), note_id=alice_note.id)

    def test_org_admin_list_does_not_include_private_notes(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        harness.create_note(principal_for(ALICE), title="Alice private", content="secret")
        harness.create_note(principal_for(BOB), title="Bob own", content="mine")

        bob_page = harness.list_notes(principal_for(BOB))
        titles = {note.title for note in bob_page.items}
        assert titles == {"Bob own"}
        assert bob_page.total == 1

    def test_org_admin_cannot_update_or_delete_private_note(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        alice_note = harness.create_note(principal_for(ALICE), title="Plan", content="original")

        with pytest.raises(ResourceNotFound):
            harness.update_note(
                principal_for(BOB),
                note_id=alice_note.id,
                title="hijacked",
                content="overwritten",
                expected_version=alice_note.version,
            )
        with pytest.raises(ResourceNotFound):
            harness.delete_note(principal_for(BOB), note_id=alice_note.id)

        # The owner still sees untouched content.
        reloaded = harness.get_note(principal_for(ALICE), note_id=alice_note.id)
        assert reloaded.title == "Plan"
        assert reloaded.content == "original"

    def test_org_admin_cannot_enumerate_note_sharing(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Administration of people is not administration of note permissions."""
        from notes_service.application.use_cases.permissions import ListPermissions

        alice_note = harness.create_note(principal_for(ALICE), title="Shared", content="x")
        harness.share_note(
            principal_for(ALICE),
            note_id=alice_note.id,
            target_user_id=CAROL,
            level=PermissionLevel.READ,
        )

        with pytest.raises(ResourceNotFound):
            harness._make_note(ListPermissions).run(principal_for(BOB), note_id=alice_note.id)

    def test_org_owner_has_no_content_access_either(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Owning the organization is not owning its members' notes."""
        bob_note = harness.create_note(
            principal_for(BOB), title="Bob private", content="not Alice's"
        )
        # Alice is ORG_OWNER of Acme and Bob's manager in the hierarchy.
        with pytest.raises(ResourceNotFound):
            harness.get_note(principal_for(ALICE), note_id=bob_note.id)


class TestExplicitSharingIsTheOnlyPath:
    def test_explicit_share_grants_read_while_org_role_is_irrelevant(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Content access follows the note permission, not the org role.

        Carol is a plain org member (no admin capability) yet, once explicitly
        shared, she reads the note. Bob is an admin yet, unshared, reads nothing.
        That asymmetry is the proof the two policies are independent.
        """
        harness.invite(
            principal_for(ALICE),
            organization_id=org_with_bob_as_admin.organization.id,
            user_id=CAROL,
            role=OrganizationRole.ORG_MEMBER,
        )
        alice_note = harness.create_note(
            principal_for(ALICE), title="Q3 forecast", content="numbers"
        )
        harness.share_note(
            principal_for(ALICE),
            note_id=alice_note.id,
            target_user_id=CAROL,
            level=PermissionLevel.READ,
        )

        shared = harness.get_note(principal_for(CAROL), note_id=alice_note.id)
        assert shared.title == "Q3 forecast"

        with pytest.raises(ResourceNotFound):
            harness.get_note(principal_for(BOB), note_id=alice_note.id)

    def test_losing_org_role_does_not_revoke_an_explicit_share(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Demotion is an org concern; it must not silently rewrite note grants."""
        alice_note = harness.create_note(principal_for(ALICE), title="Shared doc", content="c")
        harness.share_note(
            principal_for(ALICE),
            note_id=alice_note.id,
            target_user_id=BOB,
            level=PermissionLevel.READ,
        )
        members = harness.list_members(
            principal_for(ALICE), organization_id=org_with_bob_as_admin.organization.id
        )
        bob_membership = next(m for m in members.items if m.user_id == str(BOB))
        harness.change_role(
            principal_for(ALICE),
            organization_id=org_with_bob_as_admin.organization.id,
            membership_id=bob_membership.id,
            role=OrganizationRole.ORG_MEMBER,
        )

        # The explicit share survives the demotion; the note policy is untouched.
        assert harness.get_note(principal_for(BOB), note_id=alice_note.id).title == "Shared doc"


class TestPortsStaySeparate:
    def test_organization_policy_does_not_speak_note_actions(self) -> None:
        """The org policy's decide() takes no note and no note Action.

        A structural guard: if someone merges the two policies, this signature
        changes and the test fails before the bug can ship.
        """
        params = inspect.signature(DefaultOrganizationAuthorizationProvider.decide).parameters
        assert "note" not in params
        assert "action" not in params
        assert {"organization", "membership", "permission"} <= set(params)

    def test_note_policy_does_not_speak_organization(self) -> None:
        params = inspect.signature(DefaultAuthorizationProvider.decide).parameters
        assert "note" in params
        assert params["action"].annotation in (Action, "Action")
        assert "organization" not in params
        assert "membership" not in params

    def test_both_providers_satisfy_their_own_port(self) -> None:
        org_policy = DefaultOrganizationAuthorizationProvider()
        note_policy = DefaultAuthorizationProvider()
        assert isinstance(org_policy, OrganizationAuthorizationProvider)
        assert isinstance(note_policy, AuthorizationProvider)

    def test_org_admin_capability_is_denied_without_membership(self, harness: Harness) -> None:
        """Default-deny: no membership means no administrative capability at all."""
        created = harness.create_org(principal_for(ALICE), name="Solo", slug="solo")
        with pytest.raises(ResourceNotFound):
            harness.list_units(principal_for(BOB), organization_id=created.organization.id)

    def test_member_without_admin_capability_is_forbidden_not_hidden(
        self, harness: Harness, org_with_bob_as_admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A known member asking above their role gets 403; a non-member gets 404."""
        from notes_service.application.use_cases.organization import CreateOrganizationUnit

        harness.invite(
            principal_for(ALICE),
            organization_id=org_with_bob_as_admin.organization.id,
            user_id=CAROL,
            role=OrganizationRole.ORG_MEMBER,
        )
        with pytest.raises(AccessDenied):
            harness._make(CreateOrganizationUnit).run(
                principal_for(CAROL),
                organization_id=org_with_bob_as_admin.organization.id,
                name="Engineering",
                slug="engineering",
            )
