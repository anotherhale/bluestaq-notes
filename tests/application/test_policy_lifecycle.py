"""Policy lifecycle use cases and the version store (NEX-047).

The domain tests prove the AST and the conflict rules. These prove the parts that
need a store and a clock, and they pin the properties an audit depends on:

* **version numbers are history, not suggestions.** The store assigns them, a
  draft cannot overwrite an existing version, and a retired version still counts
  so a number is never reused.
* **exactly one version per policy is in force.** Publishing retires the
  predecessor in the same operation, so a reader can never see two published
  versions of one policy.
* **rollback is a new release, not a rewrite.** Rolling back to v1 produces v3
  whose rules equal v1's; v1 and v2 remain readable exactly as they were.
* **the clock is the caller's.** ``published_at`` comes from the injected clock,
  not the wall clock (R29's lesson, applied before it can bite).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.application.actor_label import actor_label
from notes_service.application.errors import Conflict, ResourceNotFound
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.application.use_cases.policy_lifecycle import (
    DraftPolicy,
    DraftPolicyVersion,
    GetPolicyHistory,
    PublishDraft,
    PublishPolicyVersion,
    RollbackPolicy,
    RollbackPolicyVersion,
)
from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.governance.policy import (
    AttributeRef,
    AttributeType,
    Comparison,
    PolicyEffect,
    PolicyObligation,
    PolicyRule,
    PolicyStatus,
    evaluate,
    new_policy_version,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.governance.policy_versions import (
    InMemoryPolicyVersionRepository,
)
from notes_service.infrastructure.security.operator_access_log import (
    InMemoryOperatorAccessLog,
)
from tests.application.grant_helpers import grants_from_scopes

RISK = AttributeRef("risk", AttributeType.STRING)
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

OPERATOR = Principal(
    user_id=UserId("11111111-1111-1111-1111-111111111111"),
    email=Email("operator@example.com"),
    display_name="Operator",
    provider="demo",
    subject="11111111-1111-1111-1111-111111111111",
)


AUTHOR = Principal(
    user_id=UserId("22222222-2222-2222-2222-222222222222"),
    email=Email("author@example.com"),
    display_name="Author",
    provider="demo",
    subject="22222222-2222-2222-2222-222222222222",
)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now = self._now + timedelta(**kwargs)


def _allow_low_risk() -> PolicyRule:
    return PolicyRule(
        rule_id="allow-low",
        effect=PolicyEffect.ALLOW,
        when=Comparison(RISK, "==", "low"),
    )


def _deny_all() -> PolicyRule:
    return PolicyRule(rule_id="deny-all", effect=PolicyEffect.DENY, when=None)


@pytest.fixture
def store() -> InMemoryPolicyVersionRepository:
    return InMemoryPolicyVersionRepository()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def gate() -> OperatorGate:
    """A gate that grants this caller every policy scope.

    These tests are about the lifecycle, not about who may drive it; the refusal
    path has its own test below and the HTTP boundary has its own file. A
    wildcard here keeps a lifecycle failure from being mistaken for a gate one.
    """
    return OperatorGate(
        grants=grants_from_scopes(
            {OPERATOR.user_id: frozenset({"*"}), AUTHOR.user_id: frozenset({"*"})}
        ),
        access_log=InMemoryOperatorAccessLog(),
        clock=FixedClock(),
    )


# --------------------------------------------------------------------------- #
# Store: version numbers and history
# --------------------------------------------------------------------------- #


def test_next_version_number_starts_at_one(store: InMemoryPolicyVersionRepository) -> None:
    assert store.next_version_number("p") == 1


def test_next_version_number_counts_retired_versions(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """A retired version still occupies its number; reusing it would collide."""
    draft = new_policy_version(
        policy_id="p", version=1, rules=(_allow_low_risk(),), rationale="x", drafted_by="dana"
    )
    store.add_draft(draft)
    store.publish(draft.publish(now=clock.now(), published_by="alice"))
    store.retire(store.get("p", 1).retire())  # type: ignore[union-attr]
    assert store.next_version_number("p") == 2


def test_a_draft_cannot_overwrite_an_existing_version(
    store: InMemoryPolicyVersionRepository,
) -> None:
    draft = new_policy_version(
        policy_id="p", version=1, rules=(_allow_low_risk(),), rationale="x", drafted_by="dana"
    )
    store.add_draft(draft)
    with pytest.raises(InvariantViolation, match="already exists"):
        store.add_draft(draft)


def test_only_a_draft_can_be_added(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    draft = new_policy_version(
        policy_id="p", version=1, rules=(_allow_low_risk(),), rationale="x", drafted_by="dana"
    )
    published = draft.publish(now=clock.now(), published_by="alice")
    with pytest.raises(InvariantViolation, match="only a draft can be added"):
        store.add_draft(published)


def test_history_is_ordered_and_complete(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    for n in (1, 2, 3):
        store.add_draft(
            new_policy_version(
                policy_id="p", version=n, rules=(_allow_low_risk(),), rationale=f"v{n}"
            )
        )
    history = store.history("p")
    assert [v.version for v in history] == [1, 2, 3]


def test_history_of_an_unknown_policy_is_empty(store: InMemoryPolicyVersionRepository) -> None:
    assert store.history("missing") == ()


# --------------------------------------------------------------------------- #
# Draft use case
# --------------------------------------------------------------------------- #


def test_draft_assigns_the_next_version_number(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    first = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="first")
    )
    second = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="second")
    )
    assert (first.version, second.version) == (1, 2)
    assert first.status is PolicyStatus.DRAFT


def test_draft_stamps_the_caller_clock(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """created_at comes from the injected clock, never the wall clock."""
    draft = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    assert draft.created_at == NOW


def test_a_policy_must_have_at_least_one_rule(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    with pytest.raises(ValidationError, match="at least one rule"):
        DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
            AUTHOR, DraftPolicyVersion(policy_id="p", rules=(), rationale="x")
        )


# --------------------------------------------------------------------------- #
# Obligation readiness at draft time
#
# The gateway refuses a call whose allow rule carries an obligation it cannot
# enforce. That refusal is the security control and it stays. These prove the
# *authoring* path catches the same gap one step earlier, so an author is told
# their policy would deny everything instead of discovering it from denials.
# --------------------------------------------------------------------------- #


def test_draft_refuses_rate_limit_when_no_limiter_is_configured(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    rule = PolicyRule(
        rule_id="allow",
        effect=PolicyEffect.ALLOW,
        obligations=(PolicyObligation.RATE_LIMIT,),
    )
    draft_policy = DraftPolicy(
        store=store, clock=clock, operator_gate=gate, rate_limiter_configured=False
    )
    with pytest.raises(ValidationError, match="rate-limit"):
        draft_policy.execute(
            AUTHOR, DraftPolicyVersion(policy_id="p", rules=(rule,), rationale="x")
        )
    # Refused before anything was written: a rejected draft leaves no version.
    assert store.history("p") == ()


def test_draft_accepts_rate_limit_when_a_limiter_is_configured(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    rule = PolicyRule(
        rule_id="allow",
        effect=PolicyEffect.ALLOW,
        obligations=(PolicyObligation.RATE_LIMIT,),
    )
    draft = DraftPolicy(
        store=store, clock=clock, operator_gate=gate, rate_limiter_configured=True
    ).execute(AUTHOR, DraftPolicyVersion(policy_id="p", rules=(rule,), rationale="x"))
    assert draft.rules[0].obligations == (PolicyObligation.RATE_LIMIT,)


@pytest.mark.parametrize(
    "obligation",
    [
        PolicyObligation.APPROVAL,
        PolicyObligation.SECOND_APPROVER,
        PolicyObligation.STEP_UP,
        PolicyObligation.REDACTION,
        PolicyObligation.ENHANCED_AUDIT,
    ],
)
def test_draft_accepts_obligations_with_a_built_in_adapter(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
    obligation: PolicyObligation,
) -> None:
    """These need no deployment component, so a draft naming one is never refused here.

    Whether a *given call* can satisfy ``approval``/``step-up`` depends on the tool
    and the delegation, and stays a call-time decision — refusing at draft time
    would block policies that are perfectly enforceable for the tools they target.
    """
    rule = PolicyRule(rule_id="allow", effect=PolicyEffect.ALLOW, obligations=(obligation,))
    draft = DraftPolicy(
        store=store, clock=clock, operator_gate=gate, rate_limiter_configured=False
    ).execute(AUTHOR, DraftPolicyVersion(policy_id="p", rules=(rule,), rationale="x"))
    assert draft.rules[0].obligations == (obligation,)


# --------------------------------------------------------------------------- #
# Publish use case
# --------------------------------------------------------------------------- #


def test_publish_puts_the_version_in_force(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    draft = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    published = PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
        OPERATOR, PublishPolicyVersion(policy_id="p", version=draft.version)
    )
    assert published.status is PolicyStatus.PUBLISHED
    assert published.published_at == NOW
    assert store.published_for("p") == published


def test_publishing_a_new_version_retires_the_previous_one(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """Exactly one version per policy is ever in force."""
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    publish = PublishDraft(store=store, clock=clock, operator_gate=gate)
    v1 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=v1.version))
    v2 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=v2.version))

    assert store.get("p", 1).status is PolicyStatus.RETIRED  # type: ignore[union-attr]
    assert store.get("p", 2).status is PolicyStatus.PUBLISHED  # type: ignore[union-attr]
    assert len([v for v in store.history("p") if v.status is PolicyStatus.PUBLISHED]) == 1


def test_publishing_an_unknown_version_is_not_found(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    with pytest.raises(ResourceNotFound):
        PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
            OPERATOR, PublishPolicyVersion(policy_id="p", version=99)
        )


def test_publishing_an_already_published_version_is_a_conflict(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    draft = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    publish = PublishDraft(store=store, clock=clock, operator_gate=gate)
    publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=draft.version))
    with pytest.raises(Conflict, match="cannot be published"):
        publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=draft.version))


def test_publishing_makes_the_policy_evaluable(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """End to end: a drafted-and-published policy actually decides something."""
    draft = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
        OPERATOR, PublishPolicyVersion(policy_id="p", version=draft.version)
    )
    in_force = store.all_published()
    assert evaluate(in_force, {"risk": "low"}).allowed
    assert not evaluate(in_force, {"risk": "high"}).allowed


# --------------------------------------------------------------------------- #
# Rollback: a new release, never a rewrite
# --------------------------------------------------------------------------- #


def test_rollback_creates_a_new_version_with_the_old_rules(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    publish = PublishDraft(store=store, clock=clock, operator_gate=gate)
    v1 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=v1.version))
    v2 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=v2.version))

    rolled_back = RollbackPolicy(store=store, clock=clock, operator_gate=gate).execute(
        OPERATOR,
        RollbackPolicyVersion(policy_id="p", to_version=1, rationale="restore v1 behaviour"),
    )

    assert rolled_back.version == 3
    assert rolled_back.status is PolicyStatus.PUBLISHED
    assert rolled_back.rules == v1.rules
    assert rolled_back.published_by == actor_label(OPERATOR)


def test_rollback_does_not_delete_history(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """The original versions stay readable exactly as they were."""
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    publish = PublishDraft(store=store, clock=clock, operator_gate=gate)
    v1 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=v1.version))
    v2 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    publish.execute(OPERATOR, PublishPolicyVersion(policy_id="p", version=v2.version))

    RollbackPolicy(store=store, clock=clock, operator_gate=gate).execute(
        OPERATOR,
        RollbackPolicyVersion(policy_id="p", to_version=1, rationale="restore"),
    )

    history = store.history("p")
    assert [v.version for v in history] == [1, 2, 3]
    assert history[0].status is PolicyStatus.RETIRED
    assert history[0].rules == v1.rules
    assert history[1].status is PolicyStatus.RETIRED
    assert history[2].status is PolicyStatus.PUBLISHED


def test_rolling_back_to_a_draft_is_refused(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """Rolling back to a draft would publish something nobody approved."""
    DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    with pytest.raises(Conflict, match="it is a draft"):
        RollbackPolicy(store=store, clock=clock, operator_gate=gate).execute(
            OPERATOR,
            RollbackPolicyVersion(policy_id="p", to_version=1, rationale="x"),
        )


def test_rolling_back_to_a_missing_version_is_not_found(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    with pytest.raises(ResourceNotFound):
        RollbackPolicy(store=store, clock=clock, operator_gate=gate).execute(
            OPERATOR,
            RollbackPolicyVersion(policy_id="p", to_version=7, rationale="x"),
        )


# --------------------------------------------------------------------------- #
# History read
# --------------------------------------------------------------------------- #


def test_history_reports_the_version_in_force(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    draft = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
        OPERATOR, PublishPolicyVersion(policy_id="p", version=draft.version)
    )
    history = GetPolicyHistory(store=store, operator_gate=gate).execute(OPERATOR, "p")
    assert history.published is not None
    assert history.published.version == 1


def test_history_of_an_unknown_policy_is_not_found(
    store: InMemoryPolicyVersionRepository,
    gate: OperatorGate,
) -> None:
    with pytest.raises(ResourceNotFound):
        GetPolicyHistory(store=store, operator_gate=gate).execute(OPERATOR, "missing")


def test_all_published_is_sorted_by_policy_id(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """Deterministic ordering so a decision's consulted-version list is stable."""
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    publish = PublishDraft(store=store, clock=clock, operator_gate=gate)
    for policy_id in ("zeta", "alpha", "mu"):
        draft = draft_policy.execute(
            AUTHOR,
            DraftPolicyVersion(policy_id=policy_id, rules=(_allow_low_risk(),), rationale="x"),
        )
        publish.execute(
            OPERATOR,
            PublishPolicyVersion(policy_id=policy_id, version=draft.version),
        )
    assert [v.policy_id for v in store.all_published()] == ["alpha", "mu", "zeta"]


def test_the_store_reports_itself_as_in_memory(
    store: InMemoryPolicyVersionRepository,
) -> None:
    """A reader must not mistake the demo adapter for durable storage."""
    assert store.name == "in-memory"
