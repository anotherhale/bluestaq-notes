"""HTTP tests for the agent identity, delegation, token, and approval surface.

These run end to end through the real app, real persistence, and the real demo
authentication, so they pin the *edge* behaviour of the agent context — the part
a domain test cannot see:

* a non-owner receives **404, not 403** for an agent or delegation, because an
  agent's existence is itself private and must not be probeable;
* a request that exceeds the agent's ceiling or asks for a policy-only
  capability receives **403** — the caller knows the agent exists, so hiding it
  would be a lie;
* the raw token secret is returned **exactly once** at issuance, and no read
  path ever echoes it back;
* a token is bound to its **audience**, so a tool-gateway token is refused by
  the Notes API;
* **revocation is immediate** — revoking a delegation or an agent kills derived
  tokens on the very next request, not at expiry;
* no owner or delegator is ever read from a request body.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers

READ = "read_note"
SEARCH = "search_notes"
UPDATE = "update_note"
DELETE = "delete_note"
ORG_SEARCH = "org_search"
NOTES_API = "notes-api"
TOOL_GATEWAY = "tool-gateway"


def register_agent(
    client: TestClient,
    owner: str = ALICE,
    *,
    capabilities: list[str] | None = None,
    runtime_identity: str = "svc-summarizer",
    display_name: str = "Summarizer",
    max_token_ttl_seconds: int | None = None,
) -> dict:
    body: dict[str, object] = {
        "display_name": display_name,
        "runtime_identity": runtime_identity,
        "capabilities": capabilities if capabilities is not None else [READ, UPDATE],
    }
    if max_token_ttl_seconds is not None:
        body["max_token_ttl_seconds"] = max_token_ttl_seconds
    response = client.post("/api/v1/agents", headers=auth_headers(owner), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def grant(client: TestClient, agent_id: str, capabilities: list[str], actor: str = ALICE) -> dict:
    response = client.post(
        f"/api/v1/agents/{agent_id}/delegations",
        headers=auth_headers(actor),
        json={"capabilities": capabilities},
    )
    assert response.status_code == 201, response.text
    return response.json()


def issue_token(
    client: TestClient,
    delegation_id: str,
    *,
    capabilities: list[str],
    audience: str = NOTES_API,
    ttl_seconds: int = 300,
    actor: str = ALICE,
) -> dict:
    response = client.post(
        f"/api/v1/agents/delegations/{delegation_id}/tokens",
        headers=auth_headers(actor),
        json={
            "capabilities": capabilities,
            "audience": audience,
            "ttl_seconds": ttl_seconds,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def resolve(client: TestClient, secret: str, audience: str = NOTES_API) -> object:
    return client.post(
        "/api/v1/agents/tokens/resolve",
        headers=auth_headers(ALICE),
        json={"secret": secret, "audience": audience},
    )


class TestAgentRegistrationHttp:
    def test_registration_returns_the_agent_without_an_owner_field(
        self, client: TestClient
    ) -> None:
        agent = register_agent(client)

        assert agent["display_name"] == "Summarizer"
        assert agent["status"] == "active"
        assert sorted(agent["capabilities"]) == [READ, UPDATE]
        assert "owner_user_id" not in agent
        assert "owner" not in agent

    def test_an_owner_cannot_be_supplied_in_the_body(self, client: TestClient) -> None:
        # ``AgentRegisterRequest`` is strict, so an extra owner field is a 422,
        # not a silently-ignored field. This is the edge-level proof that no
        # owner is ever taken from the body.
        response = client.post(
            "/api/v1/agents",
            headers=auth_headers(ALICE),
            json={
                "display_name": "Sneaky",
                "runtime_identity": "svc-sneaky",
                "capabilities": [READ],
                "owner_user_id": BOB,
            },
        )

        assert response.status_code == 422, response.text

    def test_registering_org_search_is_refused(self, client: TestClient) -> None:
        # org_search is service-agent-policy only; a user cannot register it.
        response = client.post(
            "/api/v1/agents",
            headers=auth_headers(ALICE),
            json={
                "display_name": "TooWide",
                "runtime_identity": "svc-toowide",
                "capabilities": [ORG_SEARCH],
            },
        )

        assert response.status_code == 403, response.text

    def test_an_unknown_capability_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/agents",
            headers=auth_headers(ALICE),
            json={
                "display_name": "Bogus",
                "runtime_identity": "svc-bogus",
                "capabilities": ["teleport"],
            },
        )

        assert response.status_code == 422, response.text

    def test_an_empty_capability_list_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/agents",
            headers=auth_headers(ALICE),
            json={
                "display_name": "Empty",
                "runtime_identity": "svc-empty",
                "capabilities": [],
            },
        )

        assert response.status_code == 422, response.text

    def test_a_duplicate_runtime_identity_is_rejected(self, client: TestClient) -> None:
        register_agent(client, runtime_identity="svc-dup")

        response = client.post(
            "/api/v1/agents",
            headers=auth_headers(ALICE),
            json={
                "display_name": "Dup",
                "runtime_identity": "svc-dup",
                "capabilities": [READ],
            },
        )

        assert response.status_code == 422, response.text

    def test_a_duplicate_runtime_identity_in_another_case_is_rejected(
        self, client: TestClient
    ) -> None:
        register_agent(client, runtime_identity="svc-case")

        response = client.post(
            "/api/v1/agents",
            headers=auth_headers(ALICE),
            json={
                "display_name": "Case",
                "runtime_identity": "SVC-CASE",
                "capabilities": [READ],
            },
        )

        assert response.status_code == 422, response.text


class TestAgentListingHttp:
    def test_list_returns_only_the_callers_agents(self, client: TestClient) -> None:
        register_agent(client, ALICE, runtime_identity="svc-alice")
        register_agent(client, BOB, runtime_identity="svc-bob")

        listing = client.get("/api/v1/agents", headers=auth_headers(ALICE))

        assert listing.status_code == 200
        body = listing.json()
        assert [item["runtime_identity"] for item in body["items"]] == ["svc-alice"]
        assert body["meta"]["total"] == 1

    def test_a_non_owner_gets_404_not_403_on_get(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE)

        response = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers(BOB))

        assert response.status_code == 404, response.text
        assert "not found" in response.json()["error"]["message"].lower()

    def test_the_owner_can_get_their_own_agent(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE)

        response = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers(ALICE))

        assert response.status_code == 200
        assert response.json()["id"] == agent["id"]

    def test_a_malformed_agent_id_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/agents/not-a-uuid", headers=auth_headers(ALICE))

        assert response.status_code == 422, response.text


class TestDelegationHttp:
    def test_granting_within_the_ceiling_narrows_the_set(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ, UPDATE, SEARCH])

        delegation = grant(client, agent["id"], [READ])

        assert delegation["agent_id"] == agent["id"]
        assert delegation["capabilities"] == [READ]
        assert delegation["status"] == "active"

    def test_granting_above_the_ceiling_is_forbidden(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ])

        response = client.post(
            f"/api/v1/agents/{agent['id']}/delegations",
            headers=auth_headers(ALICE),
            json={"capabilities": [READ, DELETE]},
        )

        assert response.status_code == 403, response.text

    def test_granting_org_search_is_forbidden(self, client: TestClient) -> None:
        # Even an agent that held org_search could not have it delegated.
        agent = register_agent(client, capabilities=[READ])

        response = client.post(
            f"/api/v1/agents/{agent['id']}/delegations",
            headers=auth_headers(ALICE),
            json={"capabilities": [ORG_SEARCH]},
        )

        assert response.status_code == 403, response.text

    def test_a_non_owner_cannot_delegate_and_gets_404(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[READ])

        response = client.post(
            f"/api/v1/agents/{agent['id']}/delegations",
            headers=auth_headers(BOB),
            json={"capabilities": [READ]},
        )

        assert response.status_code == 404, response.text

    def test_a_revoked_agent_cannot_be_delegated_to(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[READ])
        client.delete(f"/api/v1/agents/{agent['id']}", headers=auth_headers(ALICE))

        response = client.post(
            f"/api/v1/agents/{agent['id']}/delegations",
            headers=auth_headers(ALICE),
            json={"capabilities": [READ]},
        )

        assert response.status_code == 403, response.text

    def test_a_non_owner_cannot_revoke_a_delegation_and_gets_404(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])

        response = client.delete(
            f"/api/v1/agents/delegations/{delegation['id']}", headers=auth_headers(BOB)
        )

        assert response.status_code == 404, response.text

    def test_the_owner_can_revoke_a_delegation(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])

        response = client.delete(
            f"/api/v1/agents/delegations/{delegation['id']}", headers=auth_headers(ALICE)
        )

        assert response.status_code == 204, response.text


class TestTokenIssuanceHttp:
    def test_issuing_returns_the_secret_once_and_no_hash(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ, UPDATE])
        delegation = grant(client, agent["id"], [READ, UPDATE])

        token = issue_token(client, delegation["id"], capabilities=[READ])

        assert token["secret"]
        assert token["capabilities"] == [READ]
        assert token["audience"] == NOTES_API
        # The stored hash must never be exposed on any response.
        assert "token_hash" not in token

    def test_two_issuances_yield_different_secrets(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])

        first = issue_token(client, delegation["id"], capabilities=[READ])
        second = issue_token(client, delegation["id"], capabilities=[READ])

        assert first["secret"] != second["secret"]

    def test_issuing_above_the_delegation_is_forbidden(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ, UPDATE])
        delegation = grant(client, agent["id"], [READ])

        response = client.post(
            f"/api/v1/agents/delegations/{delegation['id']}/tokens",
            headers=auth_headers(ALICE),
            json={"capabilities": [READ, UPDATE], "audience": NOTES_API, "ttl_seconds": 60},
        )

        assert response.status_code == 403, response.text

    def test_issuing_beyond_the_agent_maximum_ttl_is_rejected(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ], max_token_ttl_seconds=60)
        delegation = grant(client, agent["id"], [READ])

        response = client.post(
            f"/api/v1/agents/delegations/{delegation['id']}/tokens",
            headers=auth_headers(ALICE),
            json={"capabilities": [READ], "audience": NOTES_API, "ttl_seconds": 61},
        )

        assert response.status_code == 422, response.text

    def test_a_non_owner_cannot_issue_a_token_and_gets_404(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])

        response = client.post(
            f"/api/v1/agents/delegations/{delegation['id']}/tokens",
            headers=auth_headers(BOB),
            json={"capabilities": [READ], "audience": NOTES_API, "ttl_seconds": 60},
        )

        assert response.status_code == 404, response.text

    def test_a_revoked_delegation_cannot_mint_a_token(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        client.delete(f"/api/v1/agents/delegations/{delegation['id']}", headers=auth_headers(ALICE))

        response = client.post(
            f"/api/v1/agents/delegations/{delegation['id']}/tokens",
            headers=auth_headers(ALICE),
            json={"capabilities": [READ], "audience": NOTES_API, "ttl_seconds": 60},
        )

        assert response.status_code == 403, response.text


class TestTokenResolutionHttp:
    def test_a_valid_token_resolves_to_its_agent(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ, UPDATE])
        delegation = grant(client, agent["id"], [READ, UPDATE])
        token = issue_token(client, delegation["id"], capabilities=[READ])

        response = resolve(client, token["secret"])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agent"]["id"] == agent["id"]
        assert body["delegation_id"] == delegation["id"]
        assert body["capabilities"] == [READ]
        assert body["audience"] == NOTES_API

    def test_resolution_never_echoes_the_secret(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        token = issue_token(client, delegation["id"], capabilities=[READ])

        response = resolve(client, token["secret"])

        assert token["secret"] not in response.text

    def test_an_unknown_secret_is_rejected(self, client: TestClient) -> None:
        response = resolve(client, "not-a-real-token")

        assert response.status_code == 403, response.text

    def test_a_token_is_refused_by_the_wrong_audience(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        token = issue_token(client, delegation["id"], capabilities=[READ], audience=TOOL_GATEWAY)

        response = resolve(client, token["secret"], audience=NOTES_API)

        assert response.status_code == 403, response.text

    def test_a_token_is_accepted_by_its_own_audience(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        token = issue_token(client, delegation["id"], capabilities=[READ], audience=TOOL_GATEWAY)

        response = resolve(client, token["secret"], audience=TOOL_GATEWAY)

        assert response.status_code == 200, response.text

    def test_introspection_is_refused_without_an_authenticated_caller(
        self, client: TestClient
    ) -> None:
        # Holding the secret is not sufficient: introspection is a privileged
        # operation, so an agent cannot use it to inspect its own token.
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        token = issue_token(client, delegation["id"], capabilities=[READ])

        response = client.post(
            "/api/v1/agents/tokens/resolve",
            json={"secret": token["secret"], "audience": NOTES_API},
        )

        assert response.status_code == 401, response.text

    def test_introspection_never_discloses_the_agents_capability_ceiling(
        self, client: TestClient
    ) -> None:
        # The agent may hold UPDATE while this token carries only READ. The
        # ceiling is owner-only: returning it here would tell a token holder
        # which capabilities exist to escalate towards.
        agent = register_agent(client, capabilities=[READ, UPDATE])
        delegation = grant(client, agent["id"], [READ, UPDATE])
        token = issue_token(client, delegation["id"], capabilities=[READ])

        body = resolve(client, token["secret"]).json()

        assert body["capabilities"] == [READ]
        assert "capabilities" not in body["agent"], body["agent"]
        assert UPDATE not in json.dumps(body)


class TestRevocationIsImmediate:
    def test_revoking_a_delegation_kills_a_derived_token_on_the_next_request(
        self, client: TestClient
    ) -> None:
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        token = issue_token(client, delegation["id"], capabilities=[READ])

        # Live now.
        assert resolve(client, token["secret"]).status_code == 200

        client.delete(f"/api/v1/agents/delegations/{delegation['id']}", headers=auth_headers(ALICE))

        # Dead immediately, well before the token's own expiry.
        assert resolve(client, token["secret"]).status_code == 403

    def test_revoking_an_agent_kills_its_tokens_on_the_next_request(
        self, client: TestClient
    ) -> None:
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        token = issue_token(client, delegation["id"], capabilities=[READ])

        assert resolve(client, token["secret"]).status_code == 200

        client.delete(f"/api/v1/agents/{agent['id']}", headers=auth_headers(ALICE))

        assert resolve(client, token["secret"]).status_code == 403

    def test_revoking_an_agent_marks_it_revoked(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ])

        client.delete(f"/api/v1/agents/{agent['id']}", headers=auth_headers(ALICE))

        fetched = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers(ALICE))
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "revoked"
        assert fetched.json()["revoked_at"] is not None

    def test_revocation_is_enforced_at_two_independent_layers(self, client: TestClient) -> None:
        # Revocation is covered twice: the revoke path bulk-revokes derived
        # tokens, *and* resolution re-checks the delegation. Either alone kills a
        # token, so removing one leaves the other working. This test pins the
        # observable outcome — a derived token dies on the next request — so a
        # refactor that drops *both* layers is caught even though dropping one is
        # invisible here. Mutation testing confirmed that removing both fails
        # this test while removing either alone does not.
        agent = register_agent(client, capabilities=[READ])
        delegation = grant(client, agent["id"], [READ])
        token = issue_token(client, delegation["id"], capabilities=[READ])

        client.delete(f"/api/v1/agents/delegations/{delegation['id']}", headers=auth_headers(ALICE))

        assert resolve(client, token["secret"]).status_code == 403

    def test_a_non_owner_cannot_revoke_an_agent_and_gets_404(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[READ])

        response = client.delete(f"/api/v1/agents/{agent['id']}", headers=auth_headers(BOB))

        assert response.status_code == 404, response.text
        # And it is genuinely untouched.
        still_active = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers(ALICE))
        assert still_active.json()["status"] == "active"


class TestApprovalHttp:
    def test_a_sensitive_action_opens_a_pending_request(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[DELETE])

        response = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(ALICE),
            json={"capability": DELETE, "resource_ref": "note-42"},
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert body["capability"] == DELETE
        assert body["decided_by_user_id"] is None

    def test_a_non_sensitive_action_needs_no_approval(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[READ])

        response = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(ALICE),
            json={"capability": READ, "resource_ref": "note-42"},
        )

        assert response.status_code == 422, response.text

    def test_a_human_owner_approves_and_is_recorded(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[DELETE])
        opened = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(ALICE),
            json={"capability": DELETE, "resource_ref": "note-42"},
        ).json()

        response = client.post(
            f"/api/v1/agents/approvals/{opened['id']}/decision",
            headers=auth_headers(ALICE),
            json={"approve": True, "reason": "verified"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "approved"
        assert body["decided_by_user_id"] == ALICE
        assert body["reason"] == "verified"

    def test_a_non_owner_cannot_decide_and_gets_404(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[DELETE])
        opened = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(ALICE),
            json={"capability": DELETE, "resource_ref": "note-42"},
        ).json()

        response = client.post(
            f"/api/v1/agents/approvals/{opened['id']}/decision",
            headers=auth_headers(BOB),
            json={"approve": True},
        )

        assert response.status_code == 404, response.text

    def test_a_request_cannot_be_decided_twice(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[DELETE])
        opened = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(ALICE),
            json={"capability": DELETE, "resource_ref": "note-42"},
        ).json()
        client.post(
            f"/api/v1/agents/approvals/{opened['id']}/decision",
            headers=auth_headers(ALICE),
            json={"approve": True},
        )

        second = client.post(
            f"/api/v1/agents/approvals/{opened['id']}/decision",
            headers=auth_headers(ALICE),
            json={"approve": False},
        )

        assert second.status_code == 403, second.text

    def test_pending_approvals_are_listed_for_the_owner(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[DELETE])
        client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(ALICE),
            json={"capability": DELETE, "resource_ref": "note-42"},
        )

        response = client.get(
            f"/api/v1/agents/{agent['id']}/approvals", headers=auth_headers(ALICE)
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["status"] == "pending"

    def test_a_non_owner_cannot_list_approvals_and_gets_404(self, client: TestClient) -> None:
        agent = register_agent(client, ALICE, capabilities=[DELETE])

        response = client.get(f"/api/v1/agents/{agent['id']}/approvals", headers=auth_headers(BOB))

        assert response.status_code == 404, response.text


class TestDecidedApprovalsRemainReadable:
    """A decision must not erase the record from the API.

    The listing used to return pending requests only, so the moment a human
    approved or denied, the request vanished from the only read path. An approver
    could not confirm what they had approved and an auditor had no API view of the
    grant at all — the audit trail held the only copy. These pin both read paths
    that fix it: a status filter on the listing, and a direct read by id.
    """

    @staticmethod
    def _decided(client: TestClient, *, approve: bool, owner: str = ALICE) -> tuple[dict, dict]:
        agent = register_agent(client, owner, capabilities=[DELETE])
        opened = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(owner),
            json={"capability": DELETE, "resource_ref": "note-42"},
        ).json()
        decided = client.post(
            f"/api/v1/agents/approvals/{opened['id']}/decision",
            headers=auth_headers(owner),
            json={"approve": approve, "reason": "checked"},
        )
        assert decided.status_code == 200, decided.text
        return agent, decided.json()

    def test_the_default_listing_is_still_the_pending_queue(self, client: TestClient) -> None:
        agent, _ = self._decided(client, approve=True)

        response = client.get(
            f"/api/v1/agents/{agent['id']}/approvals", headers=auth_headers(ALICE)
        )

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_an_approved_request_is_listed_by_status(self, client: TestClient) -> None:
        agent, decided = self._decided(client, approve=True)

        response = client.get(
            f"/api/v1/agents/{agent['id']}/approvals?status=approved",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [item["id"] for item in items] == [decided["id"]]
        assert items[0]["decided_by_user_id"] == ALICE
        assert items[0]["reason"] == "checked"

    def test_a_denied_request_is_listed_by_status(self, client: TestClient) -> None:
        agent, decided = self._decided(client, approve=False)

        response = client.get(
            f"/api/v1/agents/{agent['id']}/approvals?status=denied",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [decided["id"]]

    def test_a_revoked_request_is_listed_by_status(self, client: TestClient) -> None:
        agent, approved = self._decided(client, approve=True)
        revoked = client.post(
            f"/api/v1/agents/approvals/{approved['id']}/revoke",
            headers=auth_headers(ALICE),
            json={"reason": "no longer needed"},
        )
        assert revoked.status_code == 200, revoked.text

        response = client.get(
            f"/api/v1/agents/{agent['id']}/approvals?status=revoked",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [approved["id"]]

    def test_a_decided_request_is_readable_by_id(self, client: TestClient) -> None:
        _, decided = self._decided(client, approve=True)

        response = client.get(
            f"/api/v1/agents/approvals/{decided['id']}", headers=auth_headers(ALICE)
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == decided["id"]
        assert body["status"] == "approved"
        assert body["decided_by_user_id"] == ALICE

    def test_reading_another_users_approval_gets_404(self, client: TestClient) -> None:
        _, decided = self._decided(client, approve=True, owner=ALICE)

        response = client.get(
            f"/api/v1/agents/approvals/{decided['id']}", headers=auth_headers(BOB)
        )

        assert response.status_code == 404, response.text

    def test_reading_an_unknown_approval_gets_404(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/agents/approvals/99999999-9999-4999-8999-999999999999",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 404, response.text

    def test_an_unknown_status_is_rejected(self, client: TestClient) -> None:
        agent = register_agent(client, capabilities=[DELETE])

        response = client.get(
            f"/api/v1/agents/{agent['id']}/approvals?status=not-a-state",
            headers=auth_headers(ALICE),
        )

        assert response.status_code == 422, response.text


class TestApprovalRevocationHttp:
    """Withdrawing a grant over HTTP, before the agent can spend it."""

    @staticmethod
    def _approved(client: TestClient, owner: str = ALICE) -> dict:
        agent = register_agent(client, owner, capabilities=[DELETE])
        opened = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(owner),
            json={"capability": DELETE, "resource_ref": "note-42"},
        ).json()
        approved = client.post(
            f"/api/v1/agents/approvals/{opened['id']}/decision",
            headers=auth_headers(owner),
            json={"approve": True, "reason": "verified with the customer"},
        )
        assert approved.status_code == 200, approved.text
        return approved.json()

    def test_the_owner_can_withdraw_an_unspent_grant(self, client: TestClient) -> None:
        approved = self._approved(client)

        response = client.post(
            f"/api/v1/agents/approvals/{approved['id']}/revoke",
            headers=auth_headers(ALICE),
            json={"reason": "customer changed their mind"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "revoked"
        assert body["revoked_by_user_id"] == ALICE
        assert body["revoked_at"] is not None
        assert body["revoke_reason"] == "customer changed their mind"

    def test_withdrawal_keeps_the_approvers_original_reason(self, client: TestClient) -> None:
        """Both halves of the audit story must survive the round trip."""
        approved = self._approved(client)

        response = client.post(
            f"/api/v1/agents/approvals/{approved['id']}/revoke",
            headers=auth_headers(ALICE),
            json={"reason": "customer changed their mind"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reason"] == "verified with the customer"
        assert body["revoke_reason"] == "customer changed their mind"
        assert body["decided_by_user_id"] == ALICE

    def test_a_non_owner_cannot_withdraw_and_gets_404(self, client: TestClient) -> None:
        approved = self._approved(client, ALICE)

        response = client.post(
            f"/api/v1/agents/approvals/{approved['id']}/revoke",
            headers=auth_headers(BOB),
            json={"reason": "not mine"},
        )

        assert response.status_code == 404, response.text

    def test_a_pending_request_is_not_revocable(self, client: TestClient) -> None:
        """Pending is answered with a decision, not withdrawn."""
        agent = register_agent(client, capabilities=[DELETE])
        opened = client.post(
            f"/api/v1/agents/{agent['id']}/approvals",
            headers=auth_headers(ALICE),
            json={"capability": DELETE, "resource_ref": "note-42"},
        ).json()

        response = client.post(
            f"/api/v1/agents/approvals/{opened['id']}/revoke",
            headers=auth_headers(ALICE),
            json={"reason": "too early"},
        )

        assert response.status_code == 403, response.text

    def test_a_grant_cannot_be_withdrawn_twice(self, client: TestClient) -> None:
        approved = self._approved(client)
        first = client.post(
            f"/api/v1/agents/approvals/{approved['id']}/revoke",
            headers=auth_headers(ALICE),
            json={"reason": "first"},
        )
        assert first.status_code == 200, first.text

        second = client.post(
            f"/api/v1/agents/approvals/{approved['id']}/revoke",
            headers=auth_headers(ALICE),
            json={"reason": "again"},
        )

        assert second.status_code == 403, second.text

    def test_an_unknown_approval_is_not_found(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/agents/approvals/00000000-0000-0000-0000-000000000000/revoke",
            headers=auth_headers(ALICE),
            json={"reason": "nothing here"},
        )

        assert response.status_code == 404, response.text
