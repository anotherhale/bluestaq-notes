"""HTTP tests for the organization administration surface.

These exercise ``/api/v1/organizations`` end to end through the real app, real
persistence and both real authorization policies. They pin the invariants the
admin console depends on:

* an organization the caller does not belong to is indistinguishable from one
  that does not exist (404, never 403, never a leak of its name);
* a member who lacks a capability gets 403, because they already know the
  organization exists;
* authority never escalates — an admin cannot grant a role above their own, and
  the last administrator cannot be removed;
* the response exposes only the *caller's own* role and capabilities.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL, DEMO_USERS, auth_headers


def create_org(client: TestClient, owner: str, slug: str, name: str | None = None) -> dict:
    response = client.post(
        "/api/v1/organizations",
        headers=auth_headers(owner),
        json={"name": name or slug.title(), "slug": slug},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_unit(client: TestClient, actor: str, org_id: str, slug: str, **extra: object) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org_id}/units",
        headers=auth_headers(actor),
        json={"name": slug.title(), "slug": slug, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def invite(client: TestClient, actor: str, org_id: str, user_id: str, role: str) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org_id}/members",
        headers=auth_headers(actor),
        json={"user_id": user_id, "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestOrganizationLifecycle:
    def test_creator_becomes_owner_with_all_capabilities(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        assert org["slug"] == "acme"
        assert org["name"] == "Acme"
        assert org["status"] == "active"
        assert org["caller_role"] == "org_owner"
        # The owner is the only role with the full capability set.
        assert "manage_roles" in org["caller_capabilities"]
        assert "manage_memberships" in org["caller_capabilities"]

    def test_list_returns_only_the_callers_organizations(self, client: TestClient) -> None:
        create_org(client, ALICE, "alice-org")
        create_org(client, BOB, "bob-org")

        listing = client.get("/api/v1/organizations", headers=auth_headers(ALICE))

        assert listing.status_code == 200
        body = listing.json()
        assert [item["slug"] for item in body["items"]] == ["alice-org"]
        assert body["meta"]["total"] == 1

    def test_duplicate_slug_is_rejected(self, client: TestClient) -> None:
        create_org(client, ALICE, "acme")

        response = client.post(
            "/api/v1/organizations",
            headers=auth_headers(BOB),
            json={"name": "Acme Clone", "slug": "acme"},
        )

        assert response.status_code == 422, response.text

    def test_update_uses_optimistic_concurrency(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        stale = client.patch(
            f"/api/v1/organizations/{org['id']}",
            headers=auth_headers(ALICE),
            json={"name": "Acme Two", "version": org["version"]},
        )
        assert stale.status_code == 200, stale.text
        assert stale.json()["name"] == "Acme Two"

        # Replaying the original version must not silently overwrite.
        conflict = client.patch(
            f"/api/v1/organizations/{org['id']}",
            headers=auth_headers(ALICE),
            json={"name": "Acme Three", "version": org["version"]},
        )
        assert conflict.status_code == 409, conflict.text

    def test_unknown_organization_returns_404(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/organizations/00000000-0000-0000-0000-000000000000",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 404


class TestTenantIsolation:
    def test_non_member_cannot_read_organization(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        response = client.get(f"/api/v1/organizations/{org['id']}", headers=auth_headers(BOB))

        # 404, not 403: Bob must not be able to confirm the tenant exists.
        assert response.status_code == 404
        assert "acme" not in response.text

    def test_non_member_cannot_list_units(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        response = client.get(f"/api/v1/organizations/{org['id']}/units", headers=auth_headers(BOB))

        assert response.status_code == 404

    def test_non_member_cannot_create_unit(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        response = client.post(
            f"/api/v1/organizations/{org['id']}/units",
            headers=auth_headers(BOB),
            json={"name": "Shadow", "slug": "shadow"},
        )

        assert response.status_code == 404

    def test_member_without_capability_gets_403(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, ALICE, org["id"], BOB, "org_member")

        # Bob has standing (he is a member) but may not administer structure.
        response = client.post(
            f"/api/v1/organizations/{org['id']}/units",
            headers=auth_headers(BOB),
            json={"name": "Shadow", "slug": "shadow"},
        )

        assert response.status_code == 403

    def test_member_without_capability_can_still_read(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, ALICE, org["id"], BOB, "org_member")

        response = client.get(f"/api/v1/organizations/{org['id']}", headers=auth_headers(BOB))

        assert response.status_code == 200
        assert response.json()["caller_role"] == "org_member"

    def test_response_exposes_only_callers_own_capabilities(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, ALICE, org["id"], BOB, "org_auditor")

        response = client.get(f"/api/v1/organizations/{org['id']}", headers=auth_headers(BOB))

        body = response.json()
        assert body["caller_role"] == "org_auditor"
        # An auditor may look, not change.
        assert "view_audit" in body["caller_capabilities"]
        assert "manage_memberships" not in body["caller_capabilities"]
        assert "manage_roles" not in body["caller_capabilities"]


class TestOrganizationRoster:
    """Who the roster lists after a membership changes lifecycle state.

    Offboarding is a soft delete: the row survives for audit history, so the
    listing has to exclude it explicitly. The team roster already does
    (``SqlAlchemyTeamMembershipRepository.list_for_team``); the organization
    roster did not, and showed deprovisioned people as current members.
    """

    def roster(self, client: TestClient, org_id: str) -> dict:
        response = client.get(
            f"/api/v1/organizations/{org_id}/members", headers=auth_headers(ALICE)
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_a_deprovisioned_member_leaves_the_roster(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, ALICE, org["id"], BOB, "org_member")
        carol_membership = invite(client, ALICE, org["id"], CAROL, "org_member")
        assert (
            client.post(
                f"/api/v1/organizations/{org['id']}/members/{carol_membership['id']}/deprovision",
                headers=auth_headers(ALICE),
            ).status_code
            == 200
        )

        roster = self.roster(client, org["id"])

        users = [member["user_id"] for member in roster["items"]]
        assert CAROL not in users, "an offboarded person is still listed as a member"
        assert set(users) == {ALICE, BOB}
        # The total describes the roster, so it must not count the offboarded row
        # either — otherwise a client sees "3 members" above two rows.
        assert roster["meta"]["total"] == 2

    def test_a_suspended_member_stays_in_the_roster(self, client: TestClient) -> None:
        # Suspension is a reversible pause, not an offboarding: the membership
        # id must stay reachable or nothing could ever be reactivated. Filtering
        # the roster to active members only would look like a fix and break that.
        org = create_org(client, ALICE, "acme")
        bob_membership = invite(client, ALICE, org["id"], BOB, "org_member")
        client.post(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/suspend",
            headers=auth_headers(ALICE),
        )

        roster = self.roster(client, org["id"])

        listed = {member["user_id"]: member for member in roster["items"]}
        assert BOB in listed, "a suspended member must stay on the roster to be reactivated"
        assert listed[BOB]["status"] == "suspended"
        assert listed[BOB]["id"] == bob_membership["id"]
        assert roster["meta"]["total"] == 2

    def test_a_deprovisioned_member_cannot_be_reactivated_from_the_roster(
        self, client: TestClient
    ) -> None:
        # Offboarding is terminal, so the roster's silence about that person is
        # consistent rather than lossy: there is no action left to take on them.
        org = create_org(client, ALICE, "acme")
        carol_membership = invite(client, ALICE, org["id"], CAROL, "org_member")
        client.post(
            f"/api/v1/organizations/{org['id']}/members/{carol_membership['id']}/deprovision",
            headers=auth_headers(ALICE),
        )

        response = client.post(
            f"/api/v1/organizations/{org['id']}/members/{carol_membership['id']}/reactivate",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 422, response.text
        assert CAROL not in [m["user_id"] for m in self.roster(client, org["id"])["items"]]


class TestOrganizationRosterPaging:
    """The roster must be readable one window at a time (NEX-098).

    The route answered with every member of the organization and derived ``total``
    from that same list. There is no per-organization member cap, so a large
    tenant made one read — and one response — grow without bound, and the caller
    had no way to ask for a window. A page-derived total is also the NEX-082…
    NEX-097 family's defect: it cannot distinguish a complete listing from a
    truncated one.
    """

    def add_invitees(self, client: TestClient, org_id: str, count: int) -> list[str]:
        # ALICE is the creating owner, so invitees are drawn from the remaining
        # seeded identities; the roster total is one more than this count. Read
        # them from the seed rather than inventing ids, so a seed change is not a
        # silent divergence between the fixture and the generator.
        invitees = [spec.id for spec in DEMO_USERS if spec.id != ALICE][:count]
        assert len(invitees) == count, "not enough seeded identities to invite"
        return [invite(client, ALICE, org_id, user, "org_member")["id"] for user in invitees]

    def test_total_is_the_roster_size_not_the_page_size(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        self.add_invitees(client, org["id"], 3)  # owner + three invitees

        page = client.get(
            f"/api/v1/organizations/{org['id']}/members?limit=1&offset=0",
            headers=auth_headers(ALICE),
        ).json()

        assert len(page["items"]) == 1
        # A page-derived total would read "1" and hide that three more exist.
        assert page["meta"]["total"] == 4
        assert page["meta"]["limit"] == 1
        assert page["meta"]["offset"] == 0

    def test_offset_walks_the_roster_without_repeating_a_row(self, client: TestClient) -> None:
        # Memberships invited in one burst can share created_at, so the adapter's
        # order must be total or an OFFSET window can repeat a row while skipping
        # another. This pins the observable contract, not the tiebreaker.
        org = create_org(client, ALICE, "acme")
        self.add_invitees(client, org["id"], 3)

        first = client.get(
            f"/api/v1/organizations/{org['id']}/members?limit=2&offset=0",
            headers=auth_headers(ALICE),
        ).json()["items"]
        second = client.get(
            f"/api/v1/organizations/{org['id']}/members?limit=2&offset=2",
            headers=auth_headers(ALICE),
        ).json()["items"]

        first_ids = {item["id"] for item in first}
        second_ids = {item["id"] for item in second}
        assert first_ids.isdisjoint(second_ids)
        assert len(first_ids | second_ids) == 4

    def test_a_page_beyond_the_roster_is_empty_not_an_error(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        page = client.get(
            f"/api/v1/organizations/{org['id']}/members?limit=10&offset=5",
            headers=auth_headers(ALICE),
        )

        assert page.status_code == 200
        assert page.json()["items"] == []
        # The total still describes the set, so an empty page past the end is
        # distinguishable from an empty organization.
        assert page.json()["meta"]["total"] == 1

    def test_a_page_size_beyond_the_cap_is_rejected(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        response = client.get(
            f"/api/v1/organizations/{org['id']}/members?limit=201",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 422, response.text

    def test_the_window_does_not_widen_the_authorization_check(self, client: TestClient) -> None:
        # Paging must not become a way to read another tenant's roster: the
        # caller still has to belong to the organization. The outsider is a
        # seeded identity who was never invited, not one of the invitees.
        org = create_org(client, ALICE, "acme")
        self.add_invitees(client, org["id"], 2)
        outsider = DEMO_USERS[-1].id

        response = client.get(
            f"/api/v1/organizations/{org['id']}/members?limit=1&offset=0",
            headers=auth_headers(outsider),
        )

        assert response.status_code == 404, response.text


class TestPrivilegeEscalation:
    def test_admin_cannot_grant_a_role_above_their_own(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, ALICE, org["id"], BOB, "org_admin")

        response = client.post(
            f"/api/v1/organizations/{org['id']}/members",
            headers=auth_headers(BOB),
            json={"user_id": CAROL, "role": "org_owner"},
        )

        assert response.status_code == 403

    def test_admin_may_grant_a_role_at_or_below_their_own(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, ALICE, org["id"], BOB, "org_admin")

        membership = invite(client, BOB, org["id"], CAROL, "org_member")

        assert membership["role"] == "org_member"

    def test_last_admin_cannot_be_demoted(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        members = client.get(
            f"/api/v1/organizations/{org['id']}/members", headers=auth_headers(ALICE)
        ).json()["items"]
        alice_membership = next(m for m in members if m["user_id"] == ALICE)

        response = client.patch(
            f"/api/v1/organizations/{org['id']}/members/{alice_membership['id']}/role",
            headers=auth_headers(ALICE),
            json={"role": "org_member"},
        )

        assert response.status_code == 422, response.text

    def test_last_admin_cannot_be_suspended(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        members = client.get(
            f"/api/v1/organizations/{org['id']}/members", headers=auth_headers(ALICE)
        ).json()["items"]
        alice_membership = next(m for m in members if m["user_id"] == ALICE)

        response = client.post(
            f"/api/v1/organizations/{org['id']}/members/{alice_membership['id']}/suspend",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 422, response.text

    def test_admin_can_demote_a_non_last_admin(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        bob_membership = invite(client, ALICE, org["id"], BOB, "org_admin")

        response = client.patch(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/role",
            headers=auth_headers(ALICE),
            json={"role": "org_member"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["role"] == "org_member"


class TestMembershipLifecycle:
    def test_suspend_removes_all_authority(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        bob_membership = invite(client, ALICE, org["id"], BOB, "org_admin")
        # A second, active membership keeps Bob authenticated, so the assertion
        # below isolates org visibility (404) from authentication. Without it
        # tenant access would already refuse Bob at the identity boundary (401,
        # matching the bearer and session paths), and the test could not tell
        # "the org is invisible" apart from "the caller is not signed in".
        second = create_org(client, ALICE, "globex")
        invite(client, ALICE, second["id"], BOB, "org_member")

        suspended = client.post(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/suspend",
            headers=auth_headers(ALICE),
        )
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["status"] == "suspended"

        # A suspended member has no standing, so the org is now invisible.
        response = client.get(f"/api/v1/organizations/{org['id']}", headers=auth_headers(BOB))
        assert response.status_code == 404

    def test_deprovision_is_terminal(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        bob_membership = invite(client, ALICE, org["id"], BOB, "org_member")

        response = client.post(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/deprovision",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "deprovisioned"

    def test_reactivate_restores_authority(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        bob_membership = invite(client, ALICE, org["id"], BOB, "org_admin")
        client.post(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/suspend",
            headers=auth_headers(ALICE),
        )

        reactivated = client.post(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/reactivate",
            headers=auth_headers(ALICE),
        )

        assert reactivated.status_code == 200, reactivated.text
        assert reactivated.json()["status"] == "active"
        # Suspension is a reversible pause: the role survives untouched.
        assert reactivated.json()["role"] == "org_admin"
        assert (
            client.get(f"/api/v1/organizations/{org['id']}", headers=auth_headers(BOB)).status_code
            == 200
        )

    def test_reactivate_does_not_revive_a_deprovisioned_member(self, client: TestClient) -> None:
        third = create_org(client, ALICE, "initech")
        invite(client, ALICE, third["id"], BOB, "org_member")
        org = create_org(client, ALICE, "acme")
        bob_membership = invite(client, ALICE, org["id"], BOB, "org_member")
        # Bob keeps tenant access through `initech`, so the assertions below test
        # that reactivation is refused rather than that Bob can no longer sign
        # in. Offboarding from his only tenant would instead deny him at the
        # identity boundary (401), which is a different property.
        client.post(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/deprovision",
            headers=auth_headers(ALICE),
        )

        response = client.post(
            f"/api/v1/organizations/{org['id']}/members/{bob_membership['id']}/reactivate",
            headers=auth_headers(ALICE),
        )

        # Offboarding is terminal, but this is a rejected state change rather
        # than a permission failure: Alice is allowed to manage memberships.
        # Like the last-admin guards above, it is reported as 422, not 403.
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_input"
        assert (
            client.get(f"/api/v1/organizations/{org['id']}", headers=auth_headers(BOB)).status_code
            == 404
        )

    def test_cannot_invite_the_same_user_twice(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, ALICE, org["id"], BOB, "org_member")

        response = client.post(
            f"/api/v1/organizations/{org['id']}/members",
            headers=auth_headers(ALICE),
            json={"user_id": BOB, "role": "org_member"},
        )

        assert response.status_code == 422, response.text

    def test_cannot_invite_an_unknown_user(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        response = client.post(
            f"/api/v1/organizations/{org['id']}/members",
            headers=auth_headers(ALICE),
            json={"user_id": "00000000-0000-0000-0000-000000000000", "role": "org_member"},
        )

        assert response.status_code == 422, response.text


class TestOrganizationHierarchy:
    def test_nested_units_and_teams(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        division = create_unit(client, ALICE, org["id"], "engineering", unit_type="division")
        department = create_unit(client, ALICE, org["id"], "platform", parent_id=division["id"])
        assert department["parent_id"] == division["id"]

        team = client.post(
            f"/api/v1/organizations/{org['id']}/teams",
            headers=auth_headers(ALICE),
            json={"unit_id": department["id"], "name": "Core", "slug": "core"},
        )
        assert team.status_code == 201, team.text
        assert team.json()["unit_id"] == department["id"]

        units = client.get(f"/api/v1/organizations/{org['id']}/units", headers=auth_headers(ALICE))
        assert {u["slug"] for u in units.json()["items"]} == {"engineering", "platform"}

    def test_unit_can_be_promoted_to_root(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        division = create_unit(client, ALICE, org["id"], "engineering", unit_type="division")
        department = create_unit(client, ALICE, org["id"], "platform", parent_id=division["id"])

        response = client.patch(
            f"/api/v1/organizations/{org['id']}/units/{department['id']}/parent",
            headers=auth_headers(ALICE),
            json={"parent_id": None},
        )

        assert response.status_code == 200, response.text
        assert response.json()["parent_id"] is None

    def test_unit_cannot_be_parented_to_a_unit_in_another_org(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        other = create_org(client, ALICE, "other-org")
        foreign_unit = create_unit(client, ALICE, other["id"], "foreign")
        unit = create_unit(client, ALICE, org["id"], "engineering")

        response = client.patch(
            f"/api/v1/organizations/{org['id']}/units/{unit['id']}/parent",
            headers=auth_headers(ALICE),
            json={"parent_id": foreign_unit["id"]},
        )

        # A cross-tenant parent would be a tenancy breach, so it must be refused.
        assert response.status_code in (404, 422), response.text


class TestTeamMembership:
    def test_add_and_remove_team_member(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        unit = create_unit(client, ALICE, org["id"], "engineering")
        team = client.post(
            f"/api/v1/organizations/{org['id']}/teams",
            headers=auth_headers(ALICE),
            json={"unit_id": unit["id"], "name": "Core", "slug": "core"},
        ).json()
        invite(client, ALICE, org["id"], BOB, "org_member")

        added = client.post(
            f"/api/v1/organizations/{org['id']}/teams/{team['id']}/members",
            headers=auth_headers(ALICE),
            json={"user_id": BOB},
        )
        assert added.status_code == 201, added.text
        assert added.json()["role"] == "team_member"

        removed = client.delete(
            f"/api/v1/organizations/{org['id']}/teams/{team['id']}/members/{BOB}",
            headers=auth_headers(ALICE),
        )
        assert removed.status_code == 204, removed.text

        listing = client.get(
            f"/api/v1/organizations/{org['id']}/teams/{team['id']}/members",
            headers=auth_headers(ALICE),
        )
        assert listing.json()["items"] == []

    def test_non_member_cannot_join_a_team(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        unit = create_unit(client, ALICE, org["id"], "engineering")
        team = client.post(
            f"/api/v1/organizations/{org['id']}/teams",
            headers=auth_headers(ALICE),
            json={"unit_id": unit["id"], "name": "Core", "slug": "core"},
        ).json()

        # Team membership must never substitute for organization membership.
        response = client.post(
            f"/api/v1/organizations/{org['id']}/teams/{team['id']}/members",
            headers=auth_headers(ALICE),
            json={"user_id": BOB},
        )

        assert response.status_code == 422, response.text

    def test_removed_team_member_can_rejoin(self, client: TestClient) -> None:
        # Removal is a soft revoke that keeps the row for audit history. A team
        # is organisation-only, never a tenant boundary, so rejoining must work
        # instead of being refused as "already a member".
        org = create_org(client, ALICE, "acme")
        unit = create_unit(client, ALICE, org["id"], "engineering")
        team = client.post(
            f"/api/v1/organizations/{org['id']}/teams",
            headers=auth_headers(ALICE),
            json={"unit_id": unit["id"], "name": "Core", "slug": "core"},
        ).json()
        invite(client, ALICE, org["id"], BOB, "org_member")
        members = f"/api/v1/organizations/{org['id']}/teams/{team['id']}/members"

        assert (
            client.post(members, headers=auth_headers(ALICE), json={"user_id": BOB}).status_code
            == 201
        )
        assert client.delete(f"{members}/{BOB}", headers=auth_headers(ALICE)).status_code == 204

        rejoined = client.post(
            members,
            headers=auth_headers(ALICE),
            json={"user_id": BOB, "role": "team_lead"},
        )

        assert rejoined.status_code == 201, rejoined.text
        assert rejoined.json()["status"] == "active"
        assert rejoined.json()["role"] == "team_lead"
        listing = client.get(members, headers=auth_headers(ALICE))
        assert [(m["user_id"], m["role"]) for m in listing.json()["items"]] == [(BOB, "team_lead")]


class TestAuthenticationRequired:
    def test_unauthenticated_request_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/organizations")

        assert response.status_code == 401
