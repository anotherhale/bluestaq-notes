"""The organization roster is paged with a counted total (NEX-098).

``list_for_organization`` returned the whole roster, and the route derived
``meta.total`` from that list, so a large tenant made one read and one response
grow without bound. There is no per-organization member cap, so this is the same
unbounded-listing shape NEX-096 removed from the agent listing.

Two properties are pinned here that an API test cannot see:

* ``total`` is a real count of the roster predicate, not ``len(page)``. A
  page-derived total cannot distinguish a complete roster from a truncated one.
* the ``OFFSET`` window is stable. Memberships granted in one burst share
  ``created_at``, so an order without a tiebreaker lets a row repeat while
  another is skipped as the window shifts. Insertion order is *reversed* against
  the expected order, so a database that happened to return rows in insertion
  order would fail.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.value_objects import (
    MembershipStatus,
    OrganizationId,
    OrganizationRole,
)
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.usefixtures("seeded")

# One instant for every row: the tie is the whole point.
TIED_AT = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
ROSTER_SIZE = 5


@pytest.fixture
def uow(session_factory: sessionmaker[Session]) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


@pytest.fixture
def org_id(uow: SqlAlchemyUnitOfWork) -> OrganizationId:
    organization = Organization.create(slug="acme", name="Acme")
    # Insertion order is the reverse of id order, so the database's natural
    # (insertion) order is the opposite of the order the listing must return.
    # Without this the walk test passes on any engine whose tied rows come back
    # in insertion order, which is not a property the contract promises.
    with uow:
        uow.organizations.add(organization)
        for spec in reversed(DEMO_USERS[:ROSTER_SIZE]):
            membership = replace(
                OrganizationMembership.grant(
                    organization_id=organization.id,
                    user_id=spec.id,
                    role=OrganizationRole.ORG_MEMBER,
                ),
                created_at=TIED_AT,
            )
            uow.organization_memberships.add(membership)
        uow.commit()
    return organization.id


def test_the_total_is_the_roster_size_at_every_page_size(
    uow: SqlAlchemyUnitOfWork, org_id: OrganizationId
) -> None:
    with uow:
        # A window narrower than the set must still report the set's size.
        assert uow.organization_memberships.count_for_organization(org_id) == ROSTER_SIZE
        page = uow.organization_memberships.list_for_organization(org_id, limit=2, offset=0)
        assert len(page) == 2


def test_walking_the_window_neither_repeats_nor_skips_a_member(
    uow: SqlAlchemyUnitOfWork, org_id: OrganizationId
) -> None:
    seen: list[str] = []
    with uow:
        whole = uow.organization_memberships.list_for_organization(org_id)
        for offset in range(0, ROSTER_SIZE, 2):
            page = uow.organization_memberships.list_for_organization(
                org_id, limit=2, offset=offset
            )
            seen.extend(str(member.id) for member in page)

    # Every row exactly once. With a non-total order over a shared created_at,
    # a shifting OFFSET window can show one member twice and hide another.
    assert len(seen) == ROSTER_SIZE
    assert len(set(seen)) == ROSTER_SIZE

    # The window must also agree with the unpaged read, position by position.
    # Row-set equality alone would pass for a stable-but-arbitrary order that
    # disagrees with the unwindowed listing; this pins the sequence itself.
    assert seen == [str(member.id) for member in whole]

    # And that sequence is the contract's order: created_at ties, so it is id
    # order. Asserting it here fails on any engine that returns tied rows in
    # insertion order rather than the declared order.
    assert [str(member.id) for member in whole] == sorted(str(member.id) for member in whole)


def test_a_deprovisioned_member_is_absent_from_both_the_page_and_the_total(
    uow: SqlAlchemyUnitOfWork, org_id: OrganizationId
) -> None:
    # The count must use the same predicate as the listing. A count that
    # included offboarded rows would report a total no page can ever reach.
    with uow:
        roster = uow.organization_memberships.list_for_organization(org_id)
        uow.organization_memberships.save(replace(roster[0], status=MembershipStatus.DEPROVISIONED))
        uow.commit()
        assert uow.organization_memberships.count_for_organization(org_id) == ROSTER_SIZE - 1
        page = uow.organization_memberships.list_for_organization(org_id)
        assert len(page) == ROSTER_SIZE - 1
        assert roster[0].id not in [member.id for member in page]


def test_no_limit_still_returns_the_whole_roster(
    uow: SqlAlchemyUnitOfWork, org_id: OrganizationId
) -> None:
    # Internal callers (authorization checks, team membership validation) rely
    # on the unpaged form; only the client-facing read must bound itself.
    with uow:
        assert len(uow.organization_memberships.list_for_organization(org_id)) == ROSTER_SIZE
