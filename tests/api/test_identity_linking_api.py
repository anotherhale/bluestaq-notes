"""HTTP tests for IdP identity linking — the OIDC login write path (NEX-091).

The application-level tests cover the rules; these pin the *contract* an admin
console codes against:

* the unlink endpoint addresses the link by its own id, so an IdP subject that
  contains ``/``, ``@`` or ``+`` needs no URL escaping (an opaque subject in the
  path used to break routing entirely);
* an unreadable member is 404 and an unauthorized one is 403, the same split the
  rest of the admin surface uses;
* the response never echoes the local user id, because the URL already implies it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL, auth_headers


def create_org(client: TestClient, owner: str, slug: str) -> dict:
    response = client.post(
        "/api/v1/organizations",
        headers=auth_headers(owner),
        json={"name": slug.title(), "slug": slug},
    )
    assert response.status_code == 201, response.text
    return response.json()


def invite(client: TestClient, org_id: str, user_id: str, role: str = "org_member") -> None:
    response = client.post(
        f"/api/v1/organizations/{org_id}/members",
        headers=auth_headers(ALICE),
        json={"user_id": user_id, "role": role},
    )
    assert response.status_code == 201, response.text


def identities_url(org_id: str, user_id: str) -> str:
    return f"/api/v1/organizations/{org_id}/members/{user_id}/identities"


class TestIdentityLinkLifecycle:
    def test_link_then_list_then_unlink(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)
        url = identities_url(org["id"], BOB)

        linked = client.post(
            url,
            headers=auth_headers(ALICE),
            json={"provider": "entra", "subject": "bob@acme.example"},
        )
        assert linked.status_code == 201, linked.text
        body = linked.json()
        assert body["provider"] == "entra"
        assert body["subject"] == "bob@acme.example"
        assert body["id"]
        assert "user_id" not in body

        listing = client.get(url, headers=auth_headers(ALICE))
        assert listing.status_code == 200, listing.text
        listed_ids = [i["id"] for i in listing.json()["items"]]
        # Seeded users already carry a ``demo`` link, so the assertion is about
        # the new link's presence, not the absolute size of the list.
        assert body["id"] in listed_ids

        removed = client.delete(f"{url}/{body['id']}", headers=auth_headers(ALICE))
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"sessions_revoked": 0}

        after = client.get(url, headers=auth_headers(ALICE)).json()["items"]
        assert body["id"] not in [i["id"] for i in after]

    def test_opaque_subject_with_path_characters_survives_round_trip(
        self, client: TestClient
    ) -> None:
        """An IdP subject containing ``/`` must not break the unlink route.

        Addressing the link by id is what makes this work; putting the subject in
        the path produced a 404 from the router, never reaching the use case.
        """
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)
        url = identities_url(org["id"], BOB)
        subject = "auth0|64f/0a+b@tenant/realms/acme#user"

        linked = client.post(
            url,
            headers=auth_headers(ALICE),
            json={"provider": "auth0", "subject": subject},
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["subject"] == subject

        removed = client.delete(f"{url}/{linked.json()['id']}", headers=auth_headers(ALICE))
        assert removed.status_code == 200, removed.text

    def test_relinking_the_same_pair_is_idempotent(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)
        url = identities_url(org["id"], BOB)
        payload = {"provider": "entra", "subject": "bob@acme.example"}

        first = client.post(url, headers=auth_headers(ALICE), json=payload)
        second = client.post(url, headers=auth_headers(ALICE), json=payload)

        assert second.status_code in (200, 201), second.text
        assert second.json()["id"] == first.json()["id"]
        items = client.get(url, headers=auth_headers(ALICE)).json()["items"]
        assert len([i for i in items if i["provider"] == "entra"]) == 1

    def test_a_link_already_owned_by_another_account_is_refused(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)
        invite(client, org["id"], CAROL)
        payload = {"provider": "entra", "subject": "shared@acme.example"}

        first = client.post(
            identities_url(org["id"], BOB), headers=auth_headers(ALICE), json=payload
        )
        assert first.status_code == 201, first.text

        clash = client.post(
            identities_url(org["id"], CAROL), headers=auth_headers(ALICE), json=payload
        )

        # A silent rebind would move Bob's login to Carol's account.
        assert clash.status_code == 422, clash.text


class TestUnlinkScope:
    def test_unknown_identity_id_is_404(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)
        url = identities_url(org["id"], BOB)

        response = client.delete(
            f"{url}/00000000-0000-4000-8000-000000000000", headers=auth_headers(ALICE)
        )

        assert response.status_code == 404

    def test_cannot_unlink_through_the_wrong_member_path(self, client: TestClient) -> None:
        """A link is removed only via the member that owns it.

        Otherwise an admin of one organization could delete a link that belongs to
        a different account by guessing its id.
        """
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)
        invite(client, org["id"], CAROL)
        linked = client.post(
            identities_url(org["id"], BOB),
            headers=auth_headers(ALICE),
            json={"provider": "entra", "subject": "bob@acme.example"},
        )
        assert linked.status_code == 201, linked.text

        response = client.delete(
            f"{identities_url(org['id'], CAROL)}/{linked.json()['id']}",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 404
        listing = client.get(identities_url(org["id"], BOB), headers=auth_headers(ALICE))
        items = listing.json()["items"]
        assert [i["id"] for i in items if i["provider"] == "entra"] == [linked.json()["id"]]


class TestIdentityLinkAuthorization:
    def test_plain_member_cannot_link(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)

        response = client.post(
            identities_url(org["id"], BOB),
            headers=auth_headers(BOB),
            json={"provider": "entra", "subject": "bob@acme.example"},
        )

        # 403, not 404: Bob knows this organization because he belongs to it.
        assert response.status_code == 403

    def test_non_member_sees_the_organization_as_absent(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")

        response = client.post(
            identities_url(org["id"], BOB),
            headers=auth_headers(BOB),
            json={"provider": "entra", "subject": "bob@acme.example"},
        )

        assert response.status_code == 404

    def test_admin_cannot_link_an_identity_to_a_more_privileged_account(
        self, client: TestClient
    ) -> None:
        """Linking is a takeover route, so authority must not escalate upwards."""
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB, "org_admin")

        response = client.post(
            identities_url(org["id"], ALICE),
            headers=auth_headers(BOB),
            json={"provider": "entra", "subject": "alice@acme.example"},
        )

        assert response.status_code == 403

    def test_unauthenticated_request_is_rejected(self, client: TestClient) -> None:
        org = create_org(client, ALICE, "acme")
        invite(client, org["id"], BOB)

        response = client.get(identities_url(org["id"], BOB))

        assert response.status_code == 401
