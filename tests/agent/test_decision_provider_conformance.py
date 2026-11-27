"""A conformance suite every :class:`DecisionProvider` adapter must pass.

R30 recorded the honest gap: every bound in the decision contract has only ever
met the stub adapter, which always answers at 0.1 confidence with a short
rationale. So the 400-character rationale cap, the ``[0.0, 1.0]`` ranges, the
candidate choice set, and the provider-name bound had never seen a payload that
tests them. The first real vendor adapter would have been the first genuine test,
and finding a bound too tight *then* means a contract change under production
pressure.

This module inverts that. :func:`assert_provider_conformance` is the contract a
real adapter is run through, so a vendor integration is checked against the same
adversarial payloads the platform's own adapters are. It is exercised here
against :class:`HostileDecisionProvider`, a deliberately badly-behaved fake that
breaks one rule at a time — because a conformance suite that cannot fail proves
nothing, and the meta-tests below assert it fails on each violation.

Writing it found four real defects in the seam that polices providers, all fixed
in this slice:

* ``MAX_PROVIDER_NAME_LENGTH`` was declared and never enforced, so an adapter
  could put an unbounded string — newlines included — into telemetry and into an
  API response.
* A provider whose ``name`` property raised took down the caller, violating the
  use case's own rule that a provider failure is an abstention.
* A non-``str`` name was carried through to the response unchanged.
* An over-long rationale raised ``ValidationError`` inside ``decide`` and was
  reported as ``PROVIDER_ERROR``, sending an operator to look at connectivity
  when the payload was the problem.

The general lesson: the port's ``decide`` was guarded as third-party code and its
``name`` property was not, although an adapter implements both.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from notes_service.application.ports.decisioning import DecisionProvider
from notes_service.application.use_cases.decisioning import (
    UNIDENTIFIED_PROVIDER,
    EvaluateDecision,
)
from notes_service.domain.agent.context import (
    ContextEnvelope,
    ContextField,
    ContextHash,
    DataClassification,
)
from notes_service.domain.agent.decisioning import (
    MAX_CANDIDATE_LENGTH,
    MAX_CANDIDATES,
    MAX_PROVIDER_NAME_LENGTH,
    MAX_RATIONALE_LENGTH,
    AbstentionReason,
    Decision,
    DecisionKind,
    DecisionOutcome,
    DecisionRequest,
    validate_against,
    validate_provider_name,
)
from notes_service.domain.errors import ValidationError
from notes_service.infrastructure.decisioning.stub import StubDecisionProvider


def _minted_hash() -> ContextHash:
    return ContextEnvelope(
        fields=(
            ContextField(
                name="note_body",
                classification=DataClassification.PUBLIC,
                value="conformance",
            ),
        ),
        ceiling=DataClassification.INTERNAL,
    ).context_hash


CONTEXT = _minted_hash()

#: Below every confidence a real adapter should claim, so the harness can assert
#: that a provider's *shape* is legal without depending on its opinion.
_PERMISSIVE_THRESHOLD = 0.0


def _choice_request(*candidates: str) -> DecisionRequest:
    return DecisionRequest(
        kind=DecisionKind.INTENT_ROUTING,
        context_hash=CONTEXT,
        candidates=candidates or ("alpha", "beta"),
    )


def _score_request() -> DecisionRequest:
    return DecisionRequest(kind=DecisionKind.RISK_SCORE, context_hash=CONTEXT)


# --------------------------------------------------------------------------
# The contract a real adapter must satisfy.
# --------------------------------------------------------------------------


def assert_provider_conformance(provider: DecisionProvider) -> None:
    """Check an adapter against every rule the platform relies on.

    Called by the adapter's own test suite. Each assertion names the rule, so a
    failure tells a vendor integrator what their adapter did rather than merely
    that something is wrong.
    """
    # Rule: a provider must identify itself as a bounded identifier. This is
    # checked directly as well as through the use case, because an adapter that
    # only fails when wrapped is an adapter whose own tests pass.
    validate_provider_name(provider.name)

    # Rule: the port is one method wide and must answer every decision kind.
    for kind in DecisionKind:
        request = (
            DecisionRequest(kind=kind, context_hash=CONTEXT, candidates=("alpha", "beta"))
            if kind.is_choice
            else DecisionRequest(kind=kind, context_hash=CONTEXT)
        )
        decision = provider.decide(request)
        assert isinstance(decision, Decision), (
            f"{provider.name} returned {type(decision).__name__}, not a Decision"
        )
        assert decision.kind is kind, (
            f"{provider.name} answered {decision.kind.value} for a {kind.value} request"
        )
        # Rule: whatever the adapter returns must satisfy the contract's own
        # bounds. A Decision cannot be constructed out of bounds, so reaching
        # here proves the rationale, confidence, and score ranges held.
        validate_against(request, decision)
        assert len(decision.rationale) <= MAX_RATIONALE_LENGTH
        assert 0.0 <= decision.confidence <= 1.0
        if decision.outcome is DecisionOutcome.DECIDED:
            if kind.is_choice:
                assert decision.choice in request.candidates, (
                    f"{provider.name} chose {decision.choice!r}, not an offered candidate"
                )
            else:
                assert decision.score is not None
                assert 0.0 <= decision.score <= 1.0

    # Rule: the same question must not produce a different answer on a re-ask
    # when the candidates are merely reordered. A provider whose answer depends
    # on input ordering is a flaky test generator, not a judgment service.
    forward = provider.decide(_choice_request("alpha", "beta", "gamma"))
    backward = provider.decide(_choice_request("gamma", "beta", "alpha"))
    assert forward.choice == backward.choice, (
        f"{provider.name} is order-dependent: {forward.choice!r} vs {backward.choice!r}"
    )

    # Rule: the answer must survive the seam that polices it. A provider can be
    # individually legal and still be downgraded by EvaluateDecision — for
    # instance by answering below a caller's threshold — but it must never
    # produce a *malformed* verdict, which would mean the adapter and the
    # contract disagree about what a valid answer is.
    evaluation = EvaluateDecision(provider=provider, min_confidence=_PERMISSIVE_THRESHOLD).run(
        _choice_request()
    )
    assert evaluation.abstention_reason is not AbstentionReason.MALFORMED_OUTPUT, (
        f"{provider.name} produced malformed output through the use case"
    )
    assert evaluation.abstention_reason is not AbstentionReason.PROVIDER_ERROR, (
        f"{provider.name} raised through the use case"
    )


def test_the_stub_satisfies_the_conformance_contract() -> None:
    """The one shipped adapter is the reference implementation of the contract."""
    assert_provider_conformance(StubDecisionProvider())


# --------------------------------------------------------------------------
# The hostile fake: one violation at a time.
# --------------------------------------------------------------------------


class HostileDecisionProvider:
    """A provider that breaks a chosen rule, to prove the suite can fail.

    ``violation`` names the single rule to break, so each meta-test below
    asserts that the harness catches exactly the defect it is aimed at.
    """

    def __init__(self, violation: str) -> None:
        self._violation = violation

    @property
    def name(self) -> str:
        if self._violation == "name_too_long":
            return "v" * (MAX_PROVIDER_NAME_LENGTH + 1)
        if self._violation == "name_with_newline":
            return "vendor\nINFO injected log line"
        if self._violation == "name_not_a_str":
            return b"bytes"  # type: ignore[return-value]
        if self._violation == "name_raises":
            raise RuntimeError("vendor metadata endpoint unreachable")
        return "hostile"

    def decide(self, request: DecisionRequest) -> Decision:
        if self._violation == "wrong_kind":
            # Answer a different question than the one asked.
            other = (
                DecisionKind.RISK_SCORE if request.kind.is_choice else DecisionKind.INTENT_ROUTING
            )
            return Decision(
                kind=other,
                outcome=DecisionOutcome.DECIDED,
                confidence=0.9,
                score=0.5 if not other.is_choice else None,
                choice="alpha" if other.is_choice else None,
            )
        if self._violation == "unoffered_choice":
            return Decision(
                kind=request.kind,
                outcome=DecisionOutcome.DECIDED,
                confidence=0.9,
                choice="gamma",
            )
        if self._violation == "over_long_rationale":
            # Constructing this raises inside the adapter, which is how a real
            # adapter building a Decision from a vendor payload would fail.
            return Decision(
                kind=request.kind,
                outcome=DecisionOutcome.DECIDED,
                confidence=0.9,
                choice="alpha",
                rationale="r" * (MAX_RATIONALE_LENGTH + 1),
            )
        if self._violation == "not_a_decision":
            return "definitely not a decision"  # type: ignore[return-value]
        if self._violation == "order_dependent" and request.kind.is_choice:
            # Picks by position, so reordering the candidates changes the answer.
            return Decision(
                kind=request.kind,
                outcome=DecisionOutcome.DECIDED,
                confidence=0.9,
                choice=request.candidates[0],
            )
        if request.kind.is_choice:
            return Decision(
                kind=request.kind,
                outcome=DecisionOutcome.DECIDED,
                confidence=0.9,
                choice=min(request.candidates),
            )
        return Decision(
            kind=request.kind,
            outcome=DecisionOutcome.DECIDED,
            confidence=0.9,
            score=0.5,
        )


@pytest.mark.parametrize(
    "violation",
    ["name_too_long", "name_with_newline", "name_not_a_str", "name_raises"],
)
def test_conformance_rejects_a_malformed_provider_name(violation: str) -> None:
    """A provider name is an identifier, not an unbounded free-text channel."""
    with pytest.raises((ValidationError, RuntimeError)):
        assert_provider_conformance(HostileDecisionProvider(violation))


@pytest.mark.parametrize(
    "violation", ["wrong_kind", "unoffered_choice", "over_long_rationale", "not_a_decision"]
)
def test_conformance_rejects_a_malformed_answer(violation: str) -> None:
    with pytest.raises((ValidationError, AssertionError)):
        assert_provider_conformance(HostileDecisionProvider(violation))


def test_conformance_rejects_an_order_dependent_provider() -> None:
    with pytest.raises(AssertionError, match="order-dependent"):
        assert_provider_conformance(HostileDecisionProvider("order_dependent"))


def test_conformance_passes_a_well_behaved_provider() -> None:
    """The fake's default behaviour is legal, so the failures above are the
    violation and not the harness rejecting everything."""
    assert_provider_conformance(HostileDecisionProvider("none"))


# --------------------------------------------------------------------------
# The four seam defects this suite found, pinned individually.
# --------------------------------------------------------------------------


def test_provider_name_bound_is_enforced() -> None:
    """``MAX_PROVIDER_NAME_LENGTH`` existed but nothing read it.

    An unbounded name travels into structured log fields and into the API
    response, so this is a real bound and not decoration.
    """
    with pytest.raises(ValidationError):
        validate_provider_name("v" * (MAX_PROVIDER_NAME_LENGTH + 1))
    assert validate_provider_name("v" * MAX_PROVIDER_NAME_LENGTH) == (
        "v" * MAX_PROVIDER_NAME_LENGTH
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "has space",
        "HasUpperCase",
        "trailing\n",
        "line\nbreak",
        "semi;colon",
        "quote'",
        'json"',
        "unicode-\u00e9",
        " leading",
    ],
)
def test_provider_name_must_be_an_identifier(bad: str) -> None:
    """A name is a telemetry key and a log field; free text there is injection."""
    with pytest.raises(ValidationError):
        validate_provider_name(bad)


def test_a_raising_provider_name_does_not_escape_the_use_case() -> None:
    """The use case's rule 2 must cover ``name``, not only ``decide``."""
    evaluation = EvaluateDecision(provider=HostileDecisionProvider("name_raises")).run(
        _choice_request()
    )
    assert evaluation.provider_name == UNIDENTIFIED_PROVIDER
    assert evaluation.is_decided


def test_a_non_string_provider_name_is_not_carried_through() -> None:
    evaluation = EvaluateDecision(provider=HostileDecisionProvider("name_not_a_str")).run(
        _choice_request()
    )
    assert evaluation.provider_name == UNIDENTIFIED_PROVIDER
    assert isinstance(evaluation.provider_name, str)


def test_a_hostile_name_never_reaches_the_evaluation() -> None:
    """A newline in a provider name would forge a log line."""
    evaluation = EvaluateDecision(provider=HostileDecisionProvider("name_with_newline")).run(
        _choice_request()
    )
    assert evaluation.provider_name == UNIDENTIFIED_PROVIDER
    assert "\n" not in evaluation.provider_name


def test_the_name_is_read_once_per_run() -> None:
    """Reading it per-record would let a flapping provider name one run twice."""

    class CountingProvider:
        def __init__(self) -> None:
            self.reads = 0

        @property
        def name(self) -> str:
            self.reads += 1
            return f"flip-{self.reads}"

        def decide(self, request: DecisionRequest) -> Decision:
            return Decision(
                kind=request.kind,
                outcome=DecisionOutcome.DECIDED,
                confidence=0.9,
                choice=min(request.candidates),
            )

    provider = CountingProvider()
    evaluation = EvaluateDecision(provider=provider).run(_choice_request())
    assert provider.reads == 1
    assert evaluation.provider_name == "flip-1"


def test_an_out_of_contract_answer_is_malformed_not_a_provider_error() -> None:
    """A contract violation is not an unreachable vendor.

    Reporting it as ``PROVIDER_ERROR`` sends an operator to look at connectivity
    when the payload is the problem — two situations that need different
    responses.
    """
    evaluation = EvaluateDecision(provider=HostileDecisionProvider("over_long_rationale")).run(
        _choice_request()
    )
    assert evaluation.abstention_reason is AbstentionReason.MALFORMED_OUTPUT
    assert evaluation.choice is None


def test_the_stub_is_not_merely_accepted_it_is_checked() -> None:
    """A conformance suite that cannot fail proves nothing.

    Asserted by construction: the hostile fake is rejected by the same function
    that accepts the stub, so the function discriminates.
    """
    accepting: Callable[[DecisionProvider], None] = assert_provider_conformance
    accepting(StubDecisionProvider())
    with pytest.raises((ValidationError, AssertionError)):
        accepting(HostileDecisionProvider("unoffered_choice"))


# --------------------------------------------------------------------------
# Bounds a real vendor payload will meet for the first time.
# --------------------------------------------------------------------------


def test_a_maximum_length_rationale_is_accepted() -> None:
    """The cap is a bound, not a margin: a legal payload at the limit must pass."""

    class AtLimitProvider:
        @property
        def name(self) -> str:
            return "at-limit"

        def decide(self, request: DecisionRequest) -> Decision:
            return Decision(
                kind=request.kind,
                outcome=DecisionOutcome.DECIDED,
                confidence=1.0,
                choice=min(request.candidates) if request.kind.is_choice else None,
                score=None if request.kind.is_choice else 0.5,
                rationale="r" * MAX_RATIONALE_LENGTH,
            )

    assert_provider_conformance(AtLimitProvider())
    evaluation = EvaluateDecision(provider=AtLimitProvider()).run(_choice_request())
    assert evaluation.is_decided
    assert len(evaluation.decision.rationale) == MAX_RATIONALE_LENGTH


def test_a_full_candidate_set_is_accepted() -> None:
    """A vendor asked to choose among the maximum candidates must be answerable."""
    candidates = tuple(f"candidate-{i:03d}" for i in range(MAX_CANDIDATES))
    assert all(len(c) <= MAX_CANDIDATE_LENGTH for c in candidates)
    request = _choice_request(*candidates)
    evaluation = EvaluateDecision(
        provider=StubDecisionProvider(), min_confidence=_PERMISSIVE_THRESHOLD
    ).run(request)
    assert evaluation.is_decided
    assert evaluation.choice in candidates
