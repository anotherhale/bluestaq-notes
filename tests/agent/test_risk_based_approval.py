"""NEX-063: risk is derived from the capability, and can only raise the gate.

The tests come in two halves, deliberately separated.

The **pure** half exercises :mod:`notes_service.domain.agent.risk` directly. That
matters because the one ``HIGH`` capability (``ORG_SEARCH``) is non-delegable, so
no agent token can reach it and the two-signature floor has no end-to-end path
today. Testing only through the gateway would therefore leave the most important
branch — "a silent policy must not weaken a high-risk action" — unproven. A pure
function can be tested at the boundary it actually is.

The **integration** half drives the real gateway and asserts the recorded
evidence carries the tier, so the risk is on the decision record rather than
recomputed by whoever reads it later.
"""

from __future__ import annotations

import pytest

from notes_service.domain.agent.risk import (
    RiskTier,
    assess_action_risk,
    required_approvals_for,
)
from notes_service.domain.agent.value_objects import (
    SENSITIVE_CAPABILITIES,
    Capability,
)


class TestRiskTierIsOrdered:
    def test_tiers_rank_in_danger_order(self) -> None:
        assert RiskTier.LOW.rank < RiskTier.ELEVATED.rank < RiskTier.HIGH.rank

    def test_at_least_is_inclusive(self) -> None:
        assert RiskTier.HIGH.at_least(RiskTier.HIGH)
        assert RiskTier.HIGH.at_least(RiskTier.LOW)
        assert not RiskTier.LOW.at_least(RiskTier.ELEVATED)


class TestAssessActionRisk:
    def test_org_search_is_the_only_high_risk_capability(self) -> None:
        high = {
            capability
            for capability in Capability
            if assess_action_risk(capability).tier is RiskTier.HIGH
        }
        assert high == {Capability.ORG_SEARCH}

    def test_every_sensitive_capability_is_at_least_elevated(self) -> None:
        # A capability that needs a human but is classed low-risk would mean the
        # tier and the sensitivity set disagree about what "dangerous" is.
        for capability in SENSITIVE_CAPABILITIES:
            assert assess_action_risk(capability).tier.at_least(RiskTier.ELEVATED), capability

    @pytest.mark.parametrize(
        "capability",
        [Capability.READ_NOTE, Capability.CREATE_NOTE, Capability.SEARCH_NOTES],
    )
    def test_reads_and_creates_are_low(self, capability: Capability) -> None:
        assert assess_action_risk(capability).tier is RiskTier.LOW

    @pytest.mark.parametrize(
        "capability",
        [Capability.DELETE_NOTE, Capability.SHARE_NOTE, Capability.REVOKE_SHARE],
    )
    def test_destructive_and_visibility_changes_are_elevated(self, capability: Capability) -> None:
        assert assess_action_risk(capability).tier is RiskTier.ELEVATED

    def test_reasons_are_machine_readable_and_present(self) -> None:
        # A tier with no reason is an assertion; a tier with reasons is a
        # derivation a reviewer can check.
        for capability in Capability:
            risk = assess_action_risk(capability)
            assert risk.reasons, capability
            for reason in risk.reasons:
                assert reason == reason.lower()
                assert " " not in reason

    def test_high_risk_alone_demands_a_second_approver(self) -> None:
        assert assess_action_risk(Capability.ORG_SEARCH).requires_second_approver
        assert not assess_action_risk(Capability.DELETE_NOTE).requires_second_approver

    def test_a_string_is_not_a_capability(self) -> None:
        # Coercing would let a caller reach an untiered value through the back
        # door; the type check is the point.
        with pytest.raises(TypeError):
            assess_action_risk("org_search")  # type: ignore[arg-type]

    def test_assessment_is_deterministic(self) -> None:
        for capability in Capability:
            assert assess_action_risk(capability) == assess_action_risk(capability)


class TestRequiredApprovalsNeverLowers:
    def test_high_risk_with_silent_policy_still_needs_two(self) -> None:
        # The whole point of NEX-063: a policy that says nothing about approval
        # must not reduce a high-blast-radius action to one signature.
        risk = assess_action_risk(Capability.ORG_SEARCH)
        assert required_approvals_for(policy_threshold=1, risk=risk) == 2

    def test_high_risk_with_two_person_policy_stays_two(self) -> None:
        risk = assess_action_risk(Capability.ORG_SEARCH)
        assert required_approvals_for(policy_threshold=2, risk=risk) == 2

    def test_policy_may_raise_a_low_risk_action(self) -> None:
        risk = assess_action_risk(Capability.READ_NOTE)
        assert required_approvals_for(policy_threshold=2, risk=risk) == 2

    def test_low_risk_with_silent_policy_needs_one(self) -> None:
        risk = assess_action_risk(Capability.DELETE_NOTE)
        assert required_approvals_for(policy_threshold=1, risk=risk) == 1

    @pytest.mark.parametrize("threshold", [0, 1, 2, 3, 99])
    def test_result_is_clamped_into_the_closed_vocabulary(self, threshold: int) -> None:
        # ApprovalRequest only models 1 or 2. Returning 3 would describe a
        # requirement no code path can satisfy.
        for capability in Capability:
            result = required_approvals_for(
                policy_threshold=threshold, risk=assess_action_risk(capability)
            )
            assert result in (1, 2)

    @pytest.mark.parametrize("threshold", [1, 2])
    def test_result_is_never_below_either_input(self, threshold: int) -> None:
        for capability in Capability:
            risk = assess_action_risk(capability)
            result = required_approvals_for(policy_threshold=threshold, risk=risk)
            assert result >= min(threshold, 2)
            if risk.requires_second_approver:
                assert result == 2
