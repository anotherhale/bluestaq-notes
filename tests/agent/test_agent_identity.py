"""Domain tests for the agent identity, token, and approval aggregates.

Three invariants live here, and each is enforced in the aggregate rather than a
use case so no caller can forget it:

* an agent's capability set is a **ceiling** that may only be narrowed;
* revocation of an agent is **terminal** — a revoked agent cannot be revived by
  flipping its status back, which would let a compromised agent return silently;
* a token stores only a **hash** of its secret, and a human — never the agent —
  decides an approval request.

Pure unit tests: no database, no framework.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.token import AgentToken, hash_agent_token
from notes_service.domain.agent.value_objects import (
    DEFAULT_MAX_TOKEN_TTL_SECONDS,
    HARD_MAX_TOKEN_TTL_SECONDS,
    AgentId,
    AgentStatus,
    ApprovalRequestId,
    ApprovalStatus,
    Capability,
    DelegatedAuthorizationId,
    TokenAudience,
)
from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.value_objects import UserId

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")

CAPS = frozenset({Capability.READ_NOTE, Capability.UPDATE_NOTE})


def _agent(
    *,
    capabilities: frozenset[Capability] = CAPS,
    max_token_ttl_seconds: int = DEFAULT_MAX_TOKEN_TTL_SECONDS,
) -> AgentIdentity:
    return AgentIdentity.register(
        display_name="Summarizer",
        owner_user_id=ALICE,
        runtime_identity="svc-summarizer",
        capabilities=capabilities,
        max_token_ttl_seconds=max_token_ttl_seconds,
    )


def _token(
    *,
    capabilities: frozenset[Capability] = CAPS,
    ttl_seconds: int = 300,
    now: datetime | None = None,
) -> tuple[AgentToken, str]:
    return AgentToken.issue(
        agent_id=AgentId.new(),
        delegation_id=DelegatedAuthorizationId.new(),
        audience=TokenAudience.NOTES_API,
        capabilities=capabilities,
        ttl_seconds=ttl_seconds,
        now=now,
    )


class TestAgentRegistration:
    def test_owner_is_taken_from_the_argument_not_the_body(self) -> None:
        agent = _agent()

        assert agent.owner_user_id == ALICE
        assert agent.status is AgentStatus.ACTIVE
        assert agent.version == 1

    def test_capabilities_are_a_ceiling_not_a_grant(self) -> None:
        # Registration records what the agent *may* ever hold; it grants nothing
        # on its own. A delegation must still be narrowed from this set.
        agent = _agent()

        assert agent.capabilities == CAPS
        assert agent.has_capability(Capability.READ_NOTE)
        assert not agent.has_capability(Capability.DELETE_NOTE)

    def test_empty_capability_set_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            AgentIdentity.register(
                display_name="Empty",
                owner_user_id=ALICE,
                runtime_identity="svc-empty",
                capabilities=frozenset(),
            )

    @pytest.mark.parametrize("display_name", ["", "   "])
    def test_blank_display_name_is_rejected(self, display_name: str) -> None:
        with pytest.raises(ValidationError, match="display name"):
            AgentIdentity.register(
                display_name=display_name,
                owner_user_id=ALICE,
                runtime_identity="svc-x",
                capabilities=CAPS,
            )

    def test_over_long_display_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            AgentIdentity.register(
                display_name="x" * 121,
                owner_user_id=ALICE,
                runtime_identity="svc-x",
                capabilities=CAPS,
            )

    @pytest.mark.parametrize(
        "runtime_identity", ["ab", "-leading", "trailing-", "has space", "under_score!", "x" * 65]
    )
    def test_invalid_runtime_identity_is_rejected(self, runtime_identity: str) -> None:
        with pytest.raises(ValidationError):
            AgentIdentity.register(
                display_name="A",
                owner_user_id=ALICE,
                runtime_identity=runtime_identity,
                capabilities=CAPS,
            )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("  Svc-Summarizer  ", "svc-summarizer"), ("UPPER", "upper"), ("svc.a_b-c", "svc.a_b-c")],
    )
    def test_runtime_identity_is_normalised_to_lowercase(self, raw: str, expected: str) -> None:
        # Case and surrounding whitespace are normalised before validation, so a
        # caller cannot register a second agent differing only in case.
        agent = AgentIdentity.register(
            display_name="A",
            owner_user_id=ALICE,
            runtime_identity=raw,
            capabilities=CAPS,
        )

        assert agent.runtime_identity == expected

    @pytest.mark.parametrize("ttl", [0, -1, HARD_MAX_TOKEN_TTL_SECONDS + 1])
    def test_out_of_range_max_token_ttl_is_rejected(self, ttl: int) -> None:
        with pytest.raises(ValidationError):
            AgentIdentity.register(
                display_name="A",
                owner_user_id=ALICE,
                runtime_identity="svc-x",
                capabilities=CAPS,
                max_token_ttl_seconds=ttl,
            )


class TestAgentLifecycle:
    def test_narrowing_capabilities_is_allowed_and_bumps_version(self) -> None:
        agent = _agent()
        before = agent.version

        agent.narrow_capabilities(capabilities=frozenset({Capability.READ_NOTE}))

        assert agent.capabilities == frozenset({Capability.READ_NOTE})
        assert agent.version == before + 1

    def test_widening_capabilities_is_rejected(self) -> None:
        agent = _agent(capabilities=frozenset({Capability.READ_NOTE}))

        with pytest.raises(InvariantViolation, match="narrowed"):
            agent.narrow_capabilities(
                capabilities=frozenset({Capability.READ_NOTE, Capability.DELETE_NOTE})
            )

    def test_narrowing_to_the_same_set_is_allowed(self) -> None:
        agent = _agent()

        agent.narrow_capabilities(capabilities=CAPS)

        assert agent.capabilities == CAPS

    def test_suspension_stops_issuance_and_can_be_undone(self) -> None:
        agent = _agent()
        agent.suspend()

        assert agent.is_operational is False
        with pytest.raises(InvariantViolation, match="cannot issue"):
            agent.assert_can_issue(audience=TokenAudience.NOTES_API, ttl_seconds=60)

        agent.activate()
        assert agent.is_operational is True
        agent.assert_can_issue(audience=TokenAudience.NOTES_API, ttl_seconds=60)

    def test_revocation_is_terminal(self) -> None:
        agent = _agent()
        agent.revoke()

        assert agent.status is AgentStatus.REVOKED
        assert agent.revoked_at is not None
        assert agent.is_operational is False

        with pytest.raises(InvariantViolation, match="reactivated"):
            agent.activate()
        with pytest.raises(InvariantViolation, match="suspended"):
            agent.suspend()

    def test_revocation_is_idempotent(self) -> None:
        agent = _agent()
        agent.revoke()
        first = agent.revoked_at
        version_after_first = agent.version

        agent.revoke()

        assert agent.revoked_at == first
        assert agent.version == version_after_first


class TestAgentIssuanceGuard:
    def test_ttl_above_the_agent_maximum_is_rejected(self) -> None:
        agent = _agent(max_token_ttl_seconds=60)

        with pytest.raises(ValidationError, match="exceeds the agent's maximum"):
            agent.assert_can_issue(audience=TokenAudience.NOTES_API, ttl_seconds=61)

    def test_ttl_above_the_hard_maximum_is_rejected(self) -> None:
        agent = _agent(max_token_ttl_seconds=HARD_MAX_TOKEN_TTL_SECONDS)

        with pytest.raises(ValidationError, match="exceeds the agent's maximum"):
            agent.assert_can_issue(
                audience=TokenAudience.NOTES_API, ttl_seconds=HARD_MAX_TOKEN_TTL_SECONDS + 1
            )

    @pytest.mark.parametrize("ttl", [0, -5])
    def test_non_positive_ttl_is_rejected(self, ttl: int) -> None:
        with pytest.raises(ValidationError, match="positive"):
            _agent().assert_can_issue(audience=TokenAudience.NOTES_API, ttl_seconds=ttl)

    def test_ttl_at_the_agent_maximum_is_allowed(self) -> None:
        _agent(max_token_ttl_seconds=60).assert_can_issue(
            audience=TokenAudience.NOTES_API, ttl_seconds=60
        )


class TestAgentToken:
    def test_only_the_hash_is_stored_never_the_secret(self) -> None:
        token, secret = _token()

        assert token.token_hash == hashlib.sha256(secret.encode()).hexdigest()
        assert secret not in token.token_hash
        assert token.token_hash != secret
        assert len(token.token_hash) == 64

    def test_hash_helper_matches_the_stored_form(self) -> None:
        _token_obj, secret = _token()

        assert hash_agent_token(secret) == hashlib.sha256(secret.encode()).hexdigest()

    def test_two_tokens_never_share_a_secret(self) -> None:
        _first, first_secret = _token()
        _second, second_secret = _token()

        assert first_secret != second_secret

    def test_expiry_is_computed_from_issuance(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        token, _secret = _token(ttl_seconds=90, now=now)

        assert token.issued_at == now
        assert token.expires_at == now + timedelta(seconds=90)

    def test_a_live_token_passes_the_guard(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        token, _secret = _token(ttl_seconds=60, now=now)

        token.assert_live(now=now)
        token.assert_live(now=now + timedelta(seconds=59))

    def test_an_expired_token_fails_closed_at_the_boundary(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        token, _secret = _token(ttl_seconds=60, now=now)

        with pytest.raises(InvariantViolation, match="expired"):
            token.assert_live(now=now + timedelta(seconds=60))
        with pytest.raises(InvariantViolation, match="expired"):
            token.assert_live(now=now + timedelta(seconds=61))

    def test_a_revoked_token_fails_closed_even_before_expiry(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        token, _secret = _token(ttl_seconds=3600, now=now)
        token.revoke(now=now)

        with pytest.raises(InvariantViolation, match="revoked"):
            token.assert_live(now=now)

    def test_revocation_is_idempotent(self) -> None:
        token, _secret = _token()
        token.revoke()
        first = token.revoked_at

        token.revoke()

        assert token.revoked_at == first

    def test_capabilities_are_carried_as_issued(self) -> None:
        token, _secret = _token(capabilities=frozenset({Capability.READ_NOTE}))

        assert token.capabilities == frozenset({Capability.READ_NOTE})
        assert token.has_capability(Capability.READ_NOTE)
        assert not token.has_capability(Capability.DELETE_NOTE)

    def test_a_token_without_capabilities_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            _token(capabilities=frozenset())

    @pytest.mark.parametrize("ttl", [0, -1, HARD_MAX_TOKEN_TTL_SECONDS + 1])
    def test_out_of_range_ttl_is_rejected(self, ttl: int) -> None:
        with pytest.raises(ValidationError, match="ttl"):
            _token(ttl_seconds=ttl)


class TestApprovalRequest:
    def _sensitive_agent(self) -> AgentIdentity:
        return _agent(capabilities=frozenset({Capability.DELETE_NOTE, Capability.READ_NOTE}))

    def test_agent_opens_a_pending_request(self) -> None:
        agent = self._sensitive_agent()

        request = ApprovalRequest.open(
            agent=agent, capability=Capability.DELETE_NOTE, resource_ref="note-42"
        )

        assert request.status is ApprovalStatus.PENDING
        assert request.agent_id == agent.id
        assert request.capability is Capability.DELETE_NOTE
        assert request.resource_ref == "note-42"
        assert request.decided_by_user_id is None
        assert isinstance(request.id, ApprovalRequestId)

    def test_a_non_sensitive_capability_needs_no_approval(self) -> None:
        agent = self._sensitive_agent()

        with pytest.raises(InvariantViolation, match="not sensitive"):
            ApprovalRequest.open(
                agent=agent, capability=Capability.READ_NOTE, resource_ref="note-42"
            )

    def test_an_agent_cannot_request_approval_for_a_capability_it_lacks(self) -> None:
        agent = _agent(capabilities=frozenset({Capability.READ_NOTE}))

        with pytest.raises(InvariantViolation, match="lacks capability"):
            ApprovalRequest.open(
                agent=agent, capability=Capability.DELETE_NOTE, resource_ref="note-42"
            )

    @pytest.mark.parametrize("resource_ref", ["", "   "])
    def test_a_blank_resource_ref_is_rejected(self, resource_ref: str) -> None:
        agent = self._sensitive_agent()

        with pytest.raises(ValidationError, match="resource"):
            ApprovalRequest.open(
                agent=agent, capability=Capability.DELETE_NOTE, resource_ref=resource_ref
            )

    def test_a_human_can_approve_and_is_recorded_as_the_decider(self) -> None:
        agent = self._sensitive_agent()
        request = ApprovalRequest.open(
            agent=agent, capability=Capability.DELETE_NOTE, resource_ref="note-42"
        )

        request.approve(approver_user_id=ALICE, reason="verified")

        assert request.status is ApprovalStatus.APPROVED
        assert request.decided_by_user_id == ALICE
        assert request.decided_at is not None
        assert request.reason == "verified"

    def test_a_human_can_deny(self) -> None:
        agent = self._sensitive_agent()
        request = ApprovalRequest.open(
            agent=agent, capability=Capability.DELETE_NOTE, resource_ref="note-42"
        )

        request.deny(approver_user_id=ALICE, reason="not now")

        assert request.status is ApprovalStatus.DENIED
        assert request.decided_by_user_id == ALICE

    def test_an_agent_cannot_approve_its_own_action(self) -> None:
        # No human approver id => the agent is trying to self-approve.
        agent = self._sensitive_agent()
        request = ApprovalRequest.open(
            agent=agent, capability=Capability.DELETE_NOTE, resource_ref="note-42"
        )

        with pytest.raises(InvariantViolation, match="human approver"):
            request.approve(approver_user_id=None)

    def test_a_request_cannot_be_decided_twice(self) -> None:
        agent = self._sensitive_agent()
        request = ApprovalRequest.open(
            agent=agent, capability=Capability.DELETE_NOTE, resource_ref="note-42"
        )
        request.approve(approver_user_id=ALICE)

        with pytest.raises(InvariantViolation, match="already"):
            request.deny(approver_user_id=BOB)

    def test_approval_is_separate_from_capability(self) -> None:
        # Opening a request never changes the agent's ceiling: approval gates
        # *use* of a capability, it does not grant it.
        agent = self._sensitive_agent()
        before = agent.capabilities

        ApprovalRequest.open(agent=agent, capability=Capability.DELETE_NOTE, resource_ref="note-42")

        assert agent.capabilities == before
