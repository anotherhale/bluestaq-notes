"""The decision port is advisory, bounded, and fails closed.

These tests pin the four properties that make a judgment service safe to use at
all, because each one is the difference between "a model offered an opinion" and
"a model granted authority":

1. **Nothing here can grant authority.** The value objects have no field naming
   a capability, a principal, a resource, or a scope — asserted structurally, so
   adding one later is a failing test rather than a quiet regression.
2. **Answers are bounded.** A choice must be one of the candidates the caller
   offered; scores and confidence live in ``[0.0, 1.0]``; the rationale is
   capped. An answer outside the contract is discarded, not trusted.
3. **Abstention is a first-class outcome** with a named reason, so "no opinion"
   is distinguishable from "opinion: no" and from "the provider is broken".
4. **A failure is an abstention, never an exception and never an allow.** A
   raising provider, a garbage return value, a wrong-kind answer, an unoffered
   choice, and a low-confidence answer all resolve to a named abstention.

The architecture test alongside this one pins the fifth property: the inner
layers never import a vendor SDK, and the adapter package is the only place a
vendor could appear.
"""

from __future__ import annotations

import dataclasses

import pytest

from notes_service.application.ports.decisioning import DecisionProvider
from notes_service.application.use_cases.decisioning import EvaluateDecision
from notes_service.domain.agent.decisioning import (
    MAX_CANDIDATE_LENGTH,
    MAX_CANDIDATES,
    MAX_RATIONALE_LENGTH,
    AbstentionReason,
    Decision,
    DecisionKind,
    DecisionOutcome,
    DecisionRequest,
    validate_against,
)
from notes_service.domain.errors import ValidationError
from notes_service.infrastructure.decisioning.stub import StubDecisionProvider

CONTEXT = "a" * 64


def _choice_request(*candidates: str, kind=DecisionKind.INTENT_ROUTING) -> DecisionRequest:
    return DecisionRequest(
        kind=kind, context_hash=CONTEXT, candidates=candidates or ("alpha", "beta")
    )


def _score_request(kind=DecisionKind.RISK_SCORE) -> DecisionRequest:
    return DecisionRequest(kind=kind, context_hash=CONTEXT)


def _decided(**overrides) -> Decision:
    base = {
        "kind": DecisionKind.INTENT_ROUTING,
        "outcome": DecisionOutcome.DECIDED,
        "confidence": 0.9,
        "choice": "alpha",
    }
    base.update(overrides)
    return Decision(**base)


# --------------------------------------------------------------------------
# 1. Structural: a decision cannot carry authority.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("model", [DecisionRequest, Decision], ids=["DecisionRequest", "Decision"])
def test_decision_types_expose_no_authority_fields(model) -> None:
    """No field may name a grant, an actor, or a scope.

    This is the invariant the whole module exists to protect. A field called
    ``capabilities`` or ``principal`` or ``scope`` would give a provider a place
    to put an authorization, and the next reader would eventually honour it.
    """
    forbidden = {
        "capability",
        "capabilities",
        "permission",
        "permissions",
        "principal",
        "user_id",
        "agent_id",
        "scope",
        "scopes",
        "grant",
        "authorized",
        "allowed",
        "allow",
    }
    field_names = {f.name for f in dataclasses.fields(model)}
    assert not (field_names & forbidden), f"{model.__name__} exposes authority-ish fields"


def test_decision_kind_set_is_closed_and_split() -> None:
    """Every kind is exactly one of choice-shaped or score-shaped."""
    for kind in DecisionKind:
        assert kind.is_choice != kind.is_score, kind


def test_approval_recommendation_is_a_score_not_a_boolean() -> None:
    """Advisory approval must not be expressible as a yes/no grant."""
    assert DecisionKind.APPROVAL_RECOMMENDATION.is_score
    assert not DecisionKind.APPROVAL_RECOMMENDATION.is_choice


# --------------------------------------------------------------------------
# 2. Bounded answers.
# --------------------------------------------------------------------------


def test_choice_kind_requires_candidates() -> None:
    with pytest.raises(ValidationError):
        DecisionRequest(kind=DecisionKind.INTENT_ROUTING, context_hash=CONTEXT)


def test_score_kind_rejects_candidates() -> None:
    with pytest.raises(ValidationError):
        DecisionRequest(kind=DecisionKind.RISK_SCORE, context_hash=CONTEXT, candidates=("x",))


def test_candidate_count_and_length_are_capped() -> None:
    with pytest.raises(ValidationError):
        _choice_request(*[f"c{i}" for i in range(MAX_CANDIDATES + 1)])
    with pytest.raises(ValidationError):
        _choice_request("x" * (MAX_CANDIDATE_LENGTH + 1))


def test_duplicate_candidates_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _choice_request("alpha", "alpha")


def test_context_hash_must_be_a_sha256_digest() -> None:
    for bad in ("", "not-a-hash", "A" * 64, "a" * 63, "a" * 65):
        with pytest.raises(ValidationError):
            DecisionRequest(kind=DecisionKind.RISK_SCORE, context_hash=bad)


def test_confidence_is_bounded() -> None:
    for bad in (-0.1, 1.1):
        with pytest.raises(ValidationError):
            _decided(confidence=bad)


def test_score_is_bounded_and_required() -> None:
    with pytest.raises(ValidationError):
        Decision(kind=DecisionKind.RISK_SCORE, outcome=DecisionOutcome.DECIDED, confidence=0.9)
    with pytest.raises(ValidationError):
        Decision(
            kind=DecisionKind.RISK_SCORE,
            outcome=DecisionOutcome.DECIDED,
            confidence=0.9,
            score=1.5,
        )


def test_choice_and_score_are_not_interchangeable() -> None:
    """A choice kind must not smuggle a score, and vice versa."""
    with pytest.raises(ValidationError):
        _decided(score=0.4)
    with pytest.raises(ValidationError):
        Decision(
            kind=DecisionKind.RISK_SCORE,
            outcome=DecisionOutcome.DECIDED,
            confidence=0.9,
            choice="alpha",
            score=0.4,
        )


def test_rationale_is_capped() -> None:
    with pytest.raises(ValidationError):
        _decided(rationale="x" * (MAX_RATIONALE_LENGTH + 1))


def test_choice_must_be_one_of_the_offered_candidates() -> None:
    """The check that needs the request: an unoffered choice is malformed."""
    request = _choice_request("alpha", "beta")
    validate_against(request, _decided(choice="alpha"))
    with pytest.raises(ValidationError):
        validate_against(request, _decided(choice="gamma"))


def test_answer_must_match_the_question_kind() -> None:
    with pytest.raises(ValidationError):
        validate_against(_score_request(), _decided())


# --------------------------------------------------------------------------
# 3. Abstention is a first-class outcome.
# --------------------------------------------------------------------------


def test_abstention_requires_a_reason() -> None:
    with pytest.raises(ValidationError):
        Decision(kind=DecisionKind.RISK_SCORE, outcome=DecisionOutcome.ABSTAINED, confidence=0.0)


def test_abstention_carries_no_answer() -> None:
    with pytest.raises(ValidationError):
        Decision(
            kind=DecisionKind.INTENT_ROUTING,
            outcome=DecisionOutcome.ABSTAINED,
            confidence=0.0,
            reason=AbstentionReason.DISABLED,
            choice="alpha",
        )


def test_decided_outcome_rejects_a_reason() -> None:
    with pytest.raises(ValidationError):
        _decided(reason=AbstentionReason.DISABLED)


def test_abstain_helper_builds_a_reasoned_abstention() -> None:
    decision = Decision.abstain(DecisionKind.RISK_SCORE, AbstentionReason.PROVIDER_ERROR)
    assert decision.outcome is DecisionOutcome.ABSTAINED
    assert decision.reason is AbstentionReason.PROVIDER_ERROR
    assert decision.score is None


# --------------------------------------------------------------------------
# 4. The use case polices the provider.
# --------------------------------------------------------------------------


class _FixedProvider:
    def __init__(self, decision: Decision) -> None:
        self._decision = decision
        self.calls = 0

    @property
    def name(self) -> str:
        return "fixed"

    def decide(self, request: DecisionRequest) -> Decision:
        self.calls += 1
        return self._decision


class _RaisingProvider:
    @property
    def name(self) -> str:
        return "raising"

    def decide(self, request: DecisionRequest) -> Decision:
        raise RuntimeError("vendor exploded")


class _GarbageProvider:
    @property
    def name(self) -> str:
        return "garbage"

    def decide(self, request: DecisionRequest):  # type: ignore[no-untyped-def]
        return "definitely not a decision"


def test_disabled_flag_abstains_without_calling_the_provider() -> None:
    provider = _FixedProvider(_decided())
    evaluation = EvaluateDecision(provider=provider, enabled=False).run(_choice_request())
    assert provider.calls == 0
    assert evaluation.abstention_reason is AbstentionReason.DISABLED
    assert evaluation.choice is None


def test_raising_provider_becomes_provider_error() -> None:
    evaluation = EvaluateDecision(provider=_RaisingProvider()).run(_choice_request())
    assert evaluation.abstention_reason is AbstentionReason.PROVIDER_ERROR
    assert not evaluation.is_decided


def test_non_decision_return_becomes_malformed_output() -> None:
    evaluation = EvaluateDecision(provider=_GarbageProvider()).run(_choice_request())
    assert evaluation.abstention_reason is AbstentionReason.MALFORMED_OUTPUT


def test_unoffered_choice_is_downgraded_to_malformed() -> None:
    provider = _FixedProvider(_decided(choice="gamma"))
    evaluation = EvaluateDecision(provider=provider).run(_choice_request("alpha", "beta"))
    assert evaluation.abstention_reason is AbstentionReason.MALFORMED_OUTPUT
    assert evaluation.choice is None


def test_wrong_kind_answer_is_downgraded_to_malformed() -> None:
    provider = _FixedProvider(_decided())
    evaluation = EvaluateDecision(provider=provider).run(_score_request())
    assert evaluation.abstention_reason is AbstentionReason.MALFORMED_OUTPUT


def test_low_confidence_becomes_abstention_at_the_threshold() -> None:
    provider = _FixedProvider(_decided(confidence=0.4))
    evaluation = EvaluateDecision(provider=provider, min_confidence=0.5).run(_choice_request())
    assert evaluation.abstention_reason is AbstentionReason.LOW_CONFIDENCE
    assert evaluation.choice is None


def test_confident_answer_passes_through_with_telemetry() -> None:
    provider = _FixedProvider(_decided(confidence=0.9))
    request = DecisionRequest(
        kind=DecisionKind.INTENT_ROUTING,
        context_hash=CONTEXT,
        candidates=("alpha", "beta"),
        correlation_id="run-42",
    )
    evaluation = EvaluateDecision(provider=provider, min_confidence=0.5).run(request)
    assert evaluation.is_decided
    assert evaluation.choice == "alpha"
    assert evaluation.provider_name == "fixed"
    assert evaluation.correlation_id == "run-42"
    assert evaluation.context_hash == CONTEXT
    assert evaluation.threshold == 0.5
    assert evaluation.latency_ms >= 0.0
    assert evaluation.abstention_reason is None


def test_evaluation_exposes_the_context_hash_not_the_context() -> None:
    """Telemetry must be able to trace a decision without holding its inputs."""
    evaluation = EvaluateDecision(provider=_FixedProvider(_decided())).run(_choice_request())
    fields = {f.name for f in dataclasses.fields(type(evaluation))}
    assert "context_hash" in fields
    assert not ({"context", "content", "text", "notes"} & fields)


def test_threshold_outside_unit_interval_is_refused() -> None:
    for bad in (-0.01, 1.01):
        with pytest.raises(ValueError):
            EvaluateDecision(provider=_FixedProvider(_decided()), min_confidence=bad)


def test_stub_satisfies_the_port() -> None:
    assert isinstance(StubDecisionProvider(), DecisionProvider)


# --------------------------------------------------------------------------
# The stub: deterministic, order-independent, and honest about not knowing.
# --------------------------------------------------------------------------


def test_stub_is_order_independent() -> None:
    stub = StubDecisionProvider()
    forward = stub.decide(_choice_request("alpha", "beta", "gamma"))
    backward = stub.decide(_choice_request("gamma", "beta", "alpha"))
    assert forward.choice == backward.choice


def test_stub_answers_every_kind() -> None:
    stub = StubDecisionProvider()
    for kind in DecisionKind:
        request = (
            _choice_request("alpha", "beta", kind=kind)
            if kind.is_choice
            else DecisionRequest(kind=kind, context_hash=CONTEXT)
        )
        decision = stub.decide(request)
        assert decision.outcome is DecisionOutcome.DECIDED
        assert decision.kind is kind


def test_stub_abstains_under_a_realistic_threshold() -> None:
    """A stub must never quietly look like a real judgment."""
    stub = StubDecisionProvider()
    evaluation = EvaluateDecision(provider=stub, min_confidence=0.5).run(_choice_request())
    assert evaluation.abstention_reason is AbstentionReason.LOW_CONFIDENCE


def test_stub_does_not_lean_towards_approving() -> None:
    stub = StubDecisionProvider()
    decision = stub.decide(_score_request(DecisionKind.APPROVAL_RECOMMENDATION))
    assert decision.score == 0.5


def test_stub_rejects_an_invalid_confidence() -> None:
    with pytest.raises(ValueError):
        StubDecisionProvider(confidence=2.0)
