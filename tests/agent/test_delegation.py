"""Domain tests for delegation and the attenuation algebra.

``attenuate`` is the single place authority is narrowed, so these tests pin the
property the whole agent context rests on: **a delegated authority is never
larger than the authority that created it**, at any depth.

The capability set is finite, so the multi-hop test enumerates real chains
exhaustively rather than sampling them: every subset of a small capability
universe is used as a parent, and every subset of *that* as a child. That is a
stronger guarantee for a finite domain than random generation would be.

Pure unit tests: no database, no framework.
"""

from __future__ import annotations

from itertools import combinations

import pytest

from notes_service.domain.agent.delegation import DelegatedAuthorization, attenuate
from notes_service.domain.agent.entities import AgentIdentity
from notes_service.domain.agent.value_objects import (
    NON_DELEGABLE_CAPABILITIES,
    AgentId,
    Capability,
    DelegatedAuthorizationId,
    DelegationStatus,
)
from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.value_objects import UserId

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")

# A small, delegable universe used for exhaustive enumeration. Deliberately
# excludes ORG_SEARCH (non-delegable) so the subset sweep exercises the ordinary
# path; non-delegability is tested separately.
DELEGABLE_UNIVERSE = (
    Capability.READ_NOTE,
    Capability.SEARCH_NOTES,
    Capability.UPDATE_NOTE,
)


def _subsets(items: tuple[Capability, ...]) -> list[frozenset[Capability]]:
    """Every non-empty subset of ``items``, as frozensets."""
    result: list[frozenset[Capability]] = []
    for size in range(1, len(items) + 1):
        result.extend(frozenset(combo) for combo in combinations(items, size))
    return result


def _agent(capabilities: frozenset[Capability]) -> AgentIdentity:
    return AgentIdentity.register(
        display_name="Summarizer",
        owner_user_id=ALICE,
        runtime_identity="svc-summarizer",
        capabilities=capabilities,
    )


class TestAttenuateSubset:
    def test_returns_exactly_the_requested_set(self) -> None:
        granted = frozenset({Capability.READ_NOTE, Capability.UPDATE_NOTE})
        requested = frozenset({Capability.READ_NOTE})

        assert attenuate(granted=granted, requested=requested) == requested

    def test_narrowing_does_not_widen_back_to_the_parent(self) -> None:
        granted = frozenset({Capability.READ_NOTE, Capability.UPDATE_NOTE})
        narrowed = attenuate(granted=granted, requested=frozenset({Capability.READ_NOTE}))

        assert narrowed != granted
        assert narrowed < granted

    def test_requesting_the_whole_parent_set_is_allowed(self) -> None:
        granted = frozenset({Capability.READ_NOTE, Capability.UPDATE_NOTE})

        assert attenuate(granted=granted, requested=granted) == granted

    @pytest.mark.parametrize("requested", _subsets(DELEGABLE_UNIVERSE))
    def test_any_subset_of_the_grant_is_accepted(self, requested: frozenset[Capability]) -> None:
        granted = frozenset(DELEGABLE_UNIVERSE)

        assert attenuate(granted=granted, requested=requested) == requested


class TestAttenuateRejectsGrowth:
    def test_excess_capability_raises_rather_than_being_trimmed(self) -> None:
        granted = frozenset({Capability.READ_NOTE})
        requested = frozenset({Capability.READ_NOTE, Capability.DELETE_NOTE})

        with pytest.raises(InvariantViolation, match="exceeds"):
            attenuate(granted=granted, requested=requested)

    def test_silently_trimming_is_impossible(self) -> None:
        # A caller that asks for too much must learn it was rejected, never
        # receive a quietly-reduced set it might mistake for full consent.
        granted = frozenset({Capability.READ_NOTE})

        with pytest.raises(InvariantViolation):
            attenuate(granted=granted, requested=frozenset(DELEGABLE_UNIVERSE))

    def test_empty_request_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            attenuate(granted=frozenset(DELEGABLE_UNIVERSE), requested=frozenset())

    def test_empty_grant_rejects_any_request(self) -> None:
        with pytest.raises(InvariantViolation):
            attenuate(
                granted=frozenset(),
                requested=frozenset({Capability.READ_NOTE}),
            )

    @pytest.mark.parametrize("parent", _subsets(DELEGABLE_UNIVERSE))
    @pytest.mark.parametrize("child", _subsets(DELEGABLE_UNIVERSE))
    def test_no_pair_of_subsets_ever_grows(
        self, parent: frozenset[Capability], child: frozenset[Capability]
    ) -> None:
        # Exhaustive over the finite universe: for every (parent, child) pair the
        # result either raises or is a subset of the parent. There is no pair
        # that yields a capability the parent did not already hold.
        if child <= parent:
            assert attenuate(granted=parent, requested=child) == child
        else:
            with pytest.raises(InvariantViolation):
                attenuate(granted=parent, requested=child)


class TestAttenuateNonDelegable:
    def test_org_search_cannot_be_delegated_by_a_user(self) -> None:
        granted = frozenset({Capability.ORG_SEARCH, Capability.READ_NOTE})

        with pytest.raises(InvariantViolation, match="not delegable"):
            attenuate(
                granted=granted,
                requested=frozenset({Capability.ORG_SEARCH}),
                require_delegable=True,
            )

    @pytest.mark.parametrize("capability", sorted(NON_DELEGABLE_CAPABILITIES))
    def test_every_non_delegable_capability_is_rejected(self, capability: Capability) -> None:
        with pytest.raises(InvariantViolation):
            attenuate(
                granted=frozenset({capability}),
                requested=frozenset({capability}),
            )

    def test_service_policy_may_skip_the_delegable_check(self) -> None:
        # The service-agent policy path is the only one allowed to bypass the
        # delegable guard, and it must say so explicitly.
        granted = frozenset({Capability.ORG_SEARCH})

        assert (
            attenuate(
                granted=granted,
                requested=granted,
                require_delegable=False,
            )
            == granted
        )


class TestAttenuateChains:
    def test_authority_never_grows_across_three_hops(self) -> None:
        # ceiling -> delegation -> token, each hop narrowing the one above it.
        ceiling = frozenset(DELEGABLE_UNIVERSE)
        first = attenuate(granted=ceiling, requested=frozenset(DELEGABLE_UNIVERSE[:2]))
        second = attenuate(granted=first, requested=frozenset(DELEGABLE_UNIVERSE[:1]))

        assert second <= first <= ceiling

    @pytest.mark.parametrize("hop", _subsets(DELEGABLE_UNIVERSE))
    def test_repeated_narrowing_converges_and_never_widens(
        self, hop: frozenset[Capability]
    ) -> None:
        current = frozenset(DELEGABLE_UNIVERSE)
        for _ in range(3):
            current = attenuate(granted=current, requested=hop)
            assert current <= hop


class TestDelegatedAuthorization:
    def test_grant_narrows_against_the_agent_ceiling(self) -> None:
        agent = _agent(frozenset({Capability.READ_NOTE, Capability.UPDATE_NOTE}))

        delegation = DelegatedAuthorization.grant(
            agent=agent,
            delegator_user_id=ALICE,
            capabilities=frozenset({Capability.READ_NOTE}),
        )

        assert delegation.capabilities == frozenset({Capability.READ_NOTE})
        assert delegation.agent_id == agent.id
        assert delegation.delegator_user_id == ALICE
        assert delegation.status is DelegationStatus.ACTIVE

    def test_grant_above_the_ceiling_is_rejected(self) -> None:
        agent = _agent(frozenset({Capability.READ_NOTE}))

        with pytest.raises(InvariantViolation, match="exceeds"):
            DelegatedAuthorization.grant(
                agent=agent,
                delegator_user_id=ALICE,
                capabilities=frozenset({Capability.READ_NOTE, Capability.DELETE_NOTE}),
            )

    def test_grant_of_a_non_delegable_capability_is_rejected(self) -> None:
        # Even if an agent somehow held ORG_SEARCH, a user cannot delegate it.
        agent = _agent(frozenset({Capability.ORG_SEARCH}))

        with pytest.raises(InvariantViolation, match="not delegable"):
            DelegatedAuthorization.grant(
                agent=agent,
                delegator_user_id=ALICE,
                capabilities=frozenset({Capability.ORG_SEARCH}),
            )

    def test_revocation_is_idempotent_and_sticks(self) -> None:
        agent = _agent(frozenset({Capability.READ_NOTE}))
        delegation = DelegatedAuthorization.grant(
            agent=agent,
            delegator_user_id=ALICE,
            capabilities=frozenset({Capability.READ_NOTE}),
        )

        delegation.revoke()
        first_revoked_at = delegation.revoked_at
        delegation.revoke()

        assert delegation.status is DelegationStatus.REVOKED
        assert delegation.revoked_at == first_revoked_at
        assert delegation.is_active() is False

    def test_a_revoked_delegation_fails_closed(self) -> None:
        agent = _agent(frozenset({Capability.READ_NOTE}))
        delegation = DelegatedAuthorization.grant(
            agent=agent,
            delegator_user_id=ALICE,
            capabilities=frozenset({Capability.READ_NOTE}),
        )
        delegation.revoke()

        with pytest.raises(InvariantViolation, match="revoked"):
            delegation.assert_usable()

    def test_delegator_is_taken_from_the_argument_not_the_agent(self) -> None:
        # The delegator is the consenting human, which may differ from the
        # agent's owner only if a caller explicitly passes another id; the use
        # case always passes the authenticated principal.
        agent = _agent(frozenset({Capability.READ_NOTE}))

        delegation = DelegatedAuthorization.grant(
            agent=agent,
            delegator_user_id=BOB,
            capabilities=frozenset({Capability.READ_NOTE}),
        )

        assert delegation.delegator_user_id == BOB
        assert delegation.delegator_user_id != agent.owner_user_id

    def test_grant_assigns_a_fresh_identifier(self) -> None:
        agent = _agent(frozenset({Capability.READ_NOTE}))

        first = DelegatedAuthorization.grant(
            agent=agent, delegator_user_id=ALICE, capabilities=frozenset({Capability.READ_NOTE})
        )
        second = DelegatedAuthorization.grant(
            agent=agent, delegator_user_id=ALICE, capabilities=frozenset({Capability.READ_NOTE})
        )

        assert isinstance(first.id, DelegatedAuthorizationId)
        assert first.id != second.id
        assert isinstance(agent.id, AgentId)
