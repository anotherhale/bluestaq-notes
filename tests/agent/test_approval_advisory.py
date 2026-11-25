"""The advisory path is the first production caller of the judgment layer.

``EvaluateDecision`` and ``ContextEnvelope`` existed with no production caller.
That is a claim of a control, not a control: nothing had shown that a real
envelope built from real records fits the classification bounds, or that an
answer reaches a human without becoming the decision.

These tests pin the three properties that make the wiring safe:

1. **Advice never decides.** The approval request is still ``PENDING`` after the
   advisory is produced, and the returned object has no field that could be read
   as a decision. Asserted against the reloaded aggregate, not the in-memory one.
2. **The subject's content does not travel.** A note title is ``CONFIDENTIAL``,
   above the default ceiling, so it is absent from the digest and named in
   ``excluded_context_fields``. Two notes differing only in title produce the
   *same* context hash — the strongest available evidence that the title never
   reached the provider.
3. **No opinion fails closed.** Disabled, below-threshold, and erroring all
   abstain with a named reason rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import ResourceNotFound
from notes_service.application.use_cases.agents import (
    OpenApprovalRequest,
    RegisterAgent,
)
from notes_service.application.use_cases.approval_advisory import (
    ApprovalAdvisory,
    RecommendApprovalDecision,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.decisioning import EvaluateDecision
from notes_service.application.use_cases.notes import CreateNote
from notes_service.domain.agent.context import MAX_FIELD_VALUE_LENGTH
from notes_service.domain.agent.decisioning import AbstentionReason, Decision
from notes_service.domain.agent.value_objects import ApprovalStatus, Capability
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.domain.notes.value_objects import MAX_CONTENT_BYTES, MAX_TITLE_LENGTH
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.decisioning.stub import StubDecisionProvider
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)


def principal_for(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email(f"{user_id}@example.com"),
        display_name="Caller",
        provider="demo",
        subject=str(user_id),
    )


class _ExplodingProvider:
    """A provider that always raises, to prove the failure path abstains."""

    @property
    def name(self) -> str:
        return "exploding"

    def decide(self, request):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider is down")


class FrozenClock:
    """A clock that never advances, so two requests share one age.

    Without it the ``request_age_seconds`` field would differ between two
    requests and the context-hash comparison could not isolate the title.
    """

    def __init__(self, now):  # type: ignore[no-untyped-def]
        self._now = now

    def now(self):  # type: ignore[no-untyped-def]
        return self._now


@dataclass
class Harness:
    session_factory: sessionmaker[Session]
    recommend: RecommendApprovalDecision
    register: RegisterAgent
    open_approval: OpenApprovalRequest
    create_note: CreateNote

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory)

    def agent(self, principal: Principal):
        # A distinct runtime identity per call: the repository rejects reuse,
        # and these tests register several agents for one principal.
        self._seq = getattr(self, "_seq", 0) + 1
        return self.register.run(
            principal,
            display_name="Summarizer",
            runtime_identity=f"svc-{principal.user_id}-{self._seq}",
            capabilities=[Capability.SHARE_NOTE.value],
        )

    def pending_approval(self, principal: Principal, resource_ref: str):
        agent = self.agent(principal)
        return self.open_approval.run(
            principal,
            agent_id=str(agent.id),
            capability=Capability.SHARE_NOTE.value,
            resource_ref=resource_ref,
        )


def _make_harness(
    session_factory: sessionmaker[Session],
    *,
    min_confidence: float,
    enabled: bool = True,
    clock=None,  # type: ignore[no-untyped-def]
):
    auth = DefaultAuthorizationProvider()
    clock = clock or SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls, **extra):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock, **extra)

    evaluator = EvaluateDecision(
        provider=StubDecisionProvider(), enabled=enabled, min_confidence=min_confidence
    )
    return Harness(
        session_factory=session_factory,
        recommend=make(RecommendApprovalDecision, evaluate_decision=evaluator),
        register=make(RegisterAgent),
        open_approval=make(OpenApprovalRequest),
        create_note=make(CreateNote),
    )


@pytest.fixture
def h(session_factory: sessionmaker[Session]) -> Harness:
    # Threshold below the stub's confidence so the *happy* path is observable;
    # the abstention tests override this.
    return _make_harness(session_factory, min_confidence=0.05)


# --------------------------------------------------------------------------
# 1. Advice never decides.
# --------------------------------------------------------------------------


def test_advisory_leaves_the_request_pending(h: Harness) -> None:
    """The whole point: a second opinion is not an approval.

    Reloaded from the database, so this proves nothing was written either.
    """
    principal = principal_for(ALICE)
    request = h.pending_approval(principal, "note:00000000-0000-0000-0000-000000000001")

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    assert isinstance(advisory, ApprovalAdvisory)
    with h.uow() as uow:
        reloaded = uow.approvals.get(request.id)
    assert reloaded is not None
    assert reloaded.status is ApprovalStatus.PENDING
    assert reloaded.decided_at is None
    assert reloaded.decided_by_user_id is None


def test_advisory_has_no_field_that_could_be_read_as_a_decision(h: Harness) -> None:
    """Structural: no ``approved``/``decision``/``status`` attribute exists."""
    principal = principal_for(ALICE)
    request = h.pending_approval(principal, "note:00000000-0000-0000-0000-000000000001")
    advisory = h.recommend.run(principal, approval_id=str(request.id))

    forbidden = {"approved", "decision", "status", "approve", "granted"}
    assert forbidden.isdisjoint(dir(advisory))


def test_advisory_for_another_users_approval_is_404(h: Harness) -> None:
    """An approval id must not be an oracle for another user's agent."""
    alice = principal_for(ALICE)
    bob = principal_for(BOB)
    request = h.pending_approval(alice, "note:00000000-0000-0000-0000-000000000001")

    with pytest.raises(ResourceNotFound):
        h.recommend.run(bob, approval_id=str(request.id))


def test_missing_approval_is_404(h: Harness) -> None:
    with pytest.raises(ResourceNotFound):
        h.recommend.run(principal_for(ALICE), approval_id="00000000-0000-0000-0000-0000000000ff")


# --------------------------------------------------------------------------
# 2. The subject's content does not travel.
# --------------------------------------------------------------------------


def test_confidential_title_is_withheld_and_named(h: Harness) -> None:
    """The title is above the ceiling: absent from the digest, named as excluded."""
    principal = principal_for(ALICE)
    note = h.create_note.run(principal, title="Q4 layoffs: draft", content="secret body")
    request = h.pending_approval(principal, f"note:{note.id}")

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    assert "note_title" in advisory.excluded_context_fields


def test_title_cannot_change_the_context_hash(session_factory: sessionmaker[Session]) -> None:
    """Two notes differing only in title hash identically.

    If the title reached the provider, these hashes would differ. They must not,
    because a differing hash is a one-bit leak of a confidential value.

    The clock is frozen so the only remaining difference between the two
    requests — their ids — is the only thing that could move the digest.
    """
    from datetime import UTC, datetime

    h = _make_harness(
        session_factory,
        min_confidence=0.05,
        clock=FrozenClock(datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)),
    )
    principal = principal_for(ALICE)
    first = h.create_note.run(principal, title="Alpha", content="same")
    second = h.create_note.run(principal, title="Omega", content="same")

    hash_first = h.recommend.run(
        principal,
        approval_id=str(h.pending_approval(principal, f"note:{first.id}").id),
    ).context_hash
    hash_second = h.recommend.run(
        principal,
        approval_id=str(h.pending_approval(principal, f"note:{second.id}").id),
    ).context_hash

    assert hash_first == hash_second


def test_unreadable_note_title_is_not_offered(h: Harness) -> None:
    """A note the caller does not own contributes no title at all."""
    principal = principal_for(ALICE)
    request = h.pending_approval(principal, "note:00000000-0000-0000-0000-0000000000aa")

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    # The note does not exist, so no confidential field was even built.
    assert "note_title" not in advisory.excluded_context_fields


def test_a_title_over_the_context_cap_is_withheld_not_a_denied_advisory(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two caps are independent; the advisory must survive them diverging.

    ``MAX_TITLE_LENGTH`` (notes domain) and ``MAX_FIELD_VALUE_LENGTH`` (context
    minimisation) both happen to be 200 today, which hides the defect: a title at
    the domain maximum sits exactly *at* the context cap, so a coincidence looks
    like a guarantee. Shrinking the context cap simulates the day they diverge.

    A title that is legal for a note but over the context cap must be withheld and
    named — never raise. Raising here would deny a read the caller is entitled to
    because their title was long, which is the wrong failure for an advisory.
    """
    from notes_service.domain.agent import context as context_module

    principal = principal_for(ALICE)
    note = h.create_note.run(principal, title="t" * MAX_TITLE_LENGTH, content="body")
    request = h.pending_approval(principal, f"note:{note.id}")

    monkeypatch.setattr(context_module, "MAX_FIELD_VALUE_LENGTH", 50)

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    assert isinstance(advisory, ApprovalAdvisory)
    assert advisory.excluded_context_fields == ("note_title",)
    # Still advisory-only: a withheld title did not tip the request into a decision.
    with h.uow() as uow:
        assert uow.approvals.get(request.id).status is ApprovalStatus.PENDING


def test_a_note_at_the_content_ceiling_still_produces_an_advisory(h: Harness) -> None:
    """NEX-031: the context bounds must hold against real stored note text.

    Until now the envelope had only ever met approval data — short titles and a
    capability string. The real subject is a note whose body may be
    ``MAX_CONTENT_BYTES`` (64 KiB) of UTF-8, roughly 300x the context field cap.
    The advisory reads only the *title*, so a maximum-size body must not leak
    into the envelope, must not raise, and must not move the digest: the body is
    not an input to this question at all.

    This is the production-data check the checkpoint named: the bounds are now
    validated by a body the notes domain actually permits, not by a fixture.
    """
    principal = principal_for(ALICE)
    body = "b" * MAX_CONTENT_BYTES
    note = h.create_note.run(principal, title="Quarterly plan", content=body)
    request = h.pending_approval(principal, f"note:{note.id}")

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    assert isinstance(advisory, ApprovalAdvisory)
    # The title is CONFIDENTIAL, above the default INTERNAL ceiling, so it is
    # withheld by classification — not by length. The 64 KiB body is never a
    # candidate at all, so it can neither raise nor reach the digest.
    assert advisory.excluded_context_fields == ("note_title",)
    with h.uow() as uow:
        stored = uow.notes.get(note.id)
        assert stored is not None
        # The stored body really is far past the context field cap, so the test
        # would fail if the advisory ever started feeding body text into the
        # envelope: the cap would either raise or withhold.
        assert len(stored.content.encode("utf-8")) > MAX_FIELD_VALUE_LENGTH
        assert uow.approvals.get(request.id).status is ApprovalStatus.PENDING


# --------------------------------------------------------------------------
# 3. No opinion fails closed.
# --------------------------------------------------------------------------


def test_disabled_decisioning_abstains(session_factory: sessionmaker[Session]) -> None:
    h = _make_harness(session_factory, min_confidence=0.05, enabled=False)
    principal = principal_for(ALICE)
    request = h.pending_approval(principal, "note:00000000-0000-0000-0000-000000000001")

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    assert advisory.score is None
    assert advisory.is_advisory_available is False
    assert advisory.abstention_reason is AbstentionReason.DISABLED


def test_below_threshold_abstains(session_factory: sessionmaker[Session]) -> None:
    """The stub answers at 0.1; a real threshold must abstain on it."""
    h = _make_harness(session_factory, min_confidence=0.5)
    principal = principal_for(ALICE)
    request = h.pending_approval(principal, "note:00000000-0000-0000-0000-000000000001")

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    assert advisory.score is None
    assert advisory.abstention_reason is AbstentionReason.LOW_CONFIDENCE


def test_provider_failure_abstains(session_factory: sessionmaker[Session]) -> None:
    """A raising provider becomes an abstention, never an exception or an allow."""
    h = _make_harness(session_factory, min_confidence=0.05)
    principal = principal_for(ALICE)
    request = h.pending_approval(principal, "note:00000000-0000-0000-0000-000000000001")

    exploding = EvaluateDecision(provider=_ExplodingProvider(), enabled=True, min_confidence=0.05)
    recommend = RecommendApprovalDecision(
        uow_factory=lambda: SqlAlchemyUnitOfWork(h.session_factory),
        authorization=DefaultAuthorizationProvider(),
        clock=SystemClock(),
        evaluate_decision=exploding,
    )

    advisory = recommend.run(principal, approval_id=str(request.id))

    assert advisory.score is None
    assert advisory.abstention_reason is AbstentionReason.PROVIDER_ERROR


def test_score_is_bounded_when_returned(h: Harness) -> None:
    """When an opinion is formed it is a bounded number, never a verdict."""
    principal = principal_for(ALICE)
    request = h.pending_approval(principal, "note:00000000-0000-0000-0000-000000000001")

    advisory = h.recommend.run(principal, approval_id=str(request.id))

    assert advisory.score is not None
    assert 0.0 <= advisory.score <= 1.0
    assert advisory.is_advisory_available is True
    # The stub's midpoint: no lean towards approving, by design.
    assert advisory.score == 0.5


def test_decision_import_is_a_score_not_a_choice() -> None:
    """The advisory kind must be a score question, so no candidate is offered."""
    from notes_service.domain.agent.decisioning import DecisionKind

    assert DecisionKind.APPROVAL_RECOMMENDATION.is_score
    assert not DecisionKind.APPROVAL_RECOMMENDATION.is_choice
    # ``Decision`` exposes no approval affordance either.
    assert not hasattr(Decision, "approve")
