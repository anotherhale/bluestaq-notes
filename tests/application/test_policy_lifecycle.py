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
    ApproveDraft,
    ApprovePolicyVersion,
    DraftPolicy,
    DraftPolicyVersion,
    GetPolicyHistory,
    ListPoliciesInForce,
    PoliciesInForce,
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
    PolicyVersion,
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


# A third principal is required to publish (NEX-078): the author drafts, a second
# operator approves, and only a third may put the version in force. Kept distinct
# from OPERATOR so the fixture mirrors that three-way rule rather than hiding it.
PUBLISHER = Principal(
    user_id=UserId("33333333-3333-3333-3333-333333333333"),
    email=Email("publisher@example.com"),
    display_name="Publisher",
    provider="demo",
    subject="33333333-3333-3333-3333-333333333333",
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
            {
                OPERATOR.user_id: frozenset({"*"}),
                AUTHOR.user_id: frozenset({"*"}),
                PUBLISHER.user_id: frozenset({"*"}),
            }
        ),
        access_log=InMemoryOperatorAccessLog(),
        clock=FixedClock(),
    )


def _approve_then_publish(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
    policy_id: str,
    version: int,
) -> PolicyVersion:
    """Drive the lifecycle: a second principal approves, a third publishes.

    Three distinct identities are required (NEX-078): ``AUTHOR`` drafts,
    ``OPERATOR`` approves, ``PUBLISHER`` puts it in force. Kept as one helper
    because every lifecycle test needs the same trio, and a test that published
    without approving — or reused one identity for two roles — would now be
    asserting on a state the domain refuses to produce.
    """
    ApproveDraft(store=store, clock=clock, operator_gate=gate).execute(
        OPERATOR, ApprovePolicyVersion(policy_id=policy_id, version=version)
    )
    return PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
        PUBLISHER, PublishPolicyVersion(policy_id=policy_id, version=version)
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
    approved = draft.approve(now=clock.now(), approved_by="reviewer-1")
    store.approve(approved)
    store.publish(approved.publish(now=clock.now(), published_by="alice"))
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
    published = draft.approve(now=clock.now(), approved_by="reviewer-1").publish(
        now=clock.now(), published_by="alice"
    )
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
    published = _approve_then_publish(store, clock, gate, "p", draft.version)
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
    v1 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    _approve_then_publish(store, clock, gate, "p", v1.version)
    v2 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    _approve_then_publish(store, clock, gate, "p", v2.version)

    assert store.get("p", 1).status is PolicyStatus.RETIRED  # type: ignore[union-attr]
    assert store.get("p", 2).status is PolicyStatus.PUBLISHED  # type: ignore[union-attr]
    assert len([v for v in store.history("p") if v.status is PolicyStatus.PUBLISHED]) == 1


def test_publishing_an_unknown_version_is_not_found(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    with pytest.raises(ResourceNotFound):
        _approve_then_publish(store, clock, gate, "p", 99)


def test_publishing_an_already_published_version_is_a_conflict(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    draft = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    _approve_then_publish(store, clock, gate, "p", draft.version)
    # The second attempt fails at the approval step, because a published version
    # is no longer a draft: the lifecycle cannot be re-entered at the middle.
    with pytest.raises(Conflict, match="only a draft can be approved"):
        _approve_then_publish(store, clock, gate, "p", draft.version)


def test_publishing_makes_the_policy_evaluable(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """End to end: a drafted-and-published policy actually decides something."""
    draft = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    _approve_then_publish(store, clock, gate, "p", draft.version)
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
    v1 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    _approve_then_publish(store, clock, gate, "p", v1.version)
    v2 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    _approve_then_publish(store, clock, gate, "p", v2.version)

    rolled_back = RollbackPolicy(store=store, clock=clock, operator_gate=gate).execute(
        PUBLISHER,
        RollbackPolicyVersion(policy_id="p", to_version=1, rationale="restore v1 behaviour"),
    )

    assert rolled_back.version == 3
    assert rolled_back.status is PolicyStatus.PUBLISHED
    assert rolled_back.rules == v1.rules
    # The caller is the publisher, and it is a third identity: the restored rules
    # were authored by AUTHOR and approved by OPERATOR.
    assert rolled_back.published_by == actor_label(PUBLISHER)


def test_rollback_does_not_delete_history(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """The original versions stay readable exactly as they were."""
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    v1 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    _approve_then_publish(store, clock, gate, "p", v1.version)
    v2 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    _approve_then_publish(store, clock, gate, "p", v2.version)

    RollbackPolicy(store=store, clock=clock, operator_gate=gate).execute(
        PUBLISHER,
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
    _approve_then_publish(store, clock, gate, "p", draft.version)
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
    for policy_id in ("zeta", "alpha", "mu"):
        draft = draft_policy.execute(
            AUTHOR,
            DraftPolicyVersion(policy_id=policy_id, rules=(_allow_low_risk(),), rationale="x"),
        )
        _approve_then_publish(store, clock, gate, policy_id, draft.version)
    assert [v.policy_id for v in store.all_published()] == ["alpha", "mu", "zeta"]


def test_the_store_reports_itself_as_in_memory(
    store: InMemoryPolicyVersionRepository,
) -> None:
    """A reader must not mistake the demo adapter for durable storage."""
    assert store.name == "in-memory"


# --------------------------------------------------------------------------- #
# Publishing an older approved version is refused, not a silent downgrade
# --------------------------------------------------------------------------- #


def _allow_all() -> PolicyRule:
    return PolicyRule(rule_id="allow-all", effect=PolicyEffect.ALLOW, when=None)


def _approve_only(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
    policy_id: str,
    version: int,
) -> PolicyVersion:
    """Approve without publishing — the state that makes the downgrade reachable.

    A *retired* version is already refused by the store's own status transition, so
    it cannot be the exploit. The reachable case is a version that was approved and
    then left behind: still ``APPROVED``, never published, and older than what is
    now in force.
    """
    return ApproveDraft(store=store, clock=clock, operator_gate=gate).execute(
        OPERATOR, ApprovePolicyVersion(policy_id=policy_id, version=version)
    )


def _permissive_v1_denying_v2(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> tuple[PolicyVersion, PolicyVersion]:
    """v1 allows everything but is never published; v2 denies everything and is.

    Returns ``(stale_v1, in_force_v2)``. Publishing the stale v1 would turn a
    ``risk=high`` decision from deny back to allow, which is the fail-open these
    tests pin shut.
    """
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    stale = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_all(),), rationale="permissive")
    )
    _approve_only(store, clock, gate, "p", stale.version)

    in_force = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="tightened")
    )
    _approve_then_publish(store, clock, gate, "p", in_force.version)
    return store.get("p", stale.version), in_force


def test_publishing_a_superseded_version_is_refused(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """A lower version number must not displace the version in force.

    The stale version is still a valid ``APPROVED`` value, so the store accepts it —
    the use case is the only thing standing between a tightening and its reversal.
    """
    stale, in_force = _permissive_v1_denying_v2(store, clock, gate)

    with pytest.raises(Conflict, match="is already in force and is newer"):
        PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
            PUBLISHER, PublishPolicyVersion(policy_id="p", version=stale.version)
        )

    assert store.published_for("p") == store.get("p", in_force.version)


def test_a_refused_downgrade_leaves_the_decision_denying(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """The whole point: the deny that v2 introduced survives the attempt.

    Asserted through :func:`evaluate` rather than on the store's status fields,
    because "the enforcement path still denies" is the property that matters — a
    status-only assertion could pass while the evaluator disagreed.
    """
    stale, _ = _permissive_v1_denying_v2(store, clock, gate)
    assert evaluate(store.all_published(), {"risk": "high"}).effect is PolicyEffect.DENY

    with pytest.raises(Conflict, match="is already in force and is newer"):
        PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
            PUBLISHER, PublishPolicyVersion(policy_id="p", version=stale.version)
        )

    assert evaluate(store.all_published(), {"risk": "high"}).effect is PolicyEffect.DENY
    assert [v.ref for v in store.all_published()] == ["p@v2"]


def test_a_refused_downgrade_leaves_history_untouched(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """v2 must not be retired by an attempt that was refused."""
    stale, _ = _permissive_v1_denying_v2(store, clock, gate)

    with pytest.raises(Conflict, match="is already in force and is newer"):
        PublishDraft(store=store, clock=clock, operator_gate=gate).execute(
            PUBLISHER, PublishPolicyVersion(policy_id="p", version=stale.version)
        )

    assert [(v.ref, v.status) for v in store.history("p")] == [
        ("p@v1", PolicyStatus.APPROVED),
        ("p@v2", PolicyStatus.PUBLISHED),
    ]


def test_rollback_remains_the_supported_way_to_restore_old_rules(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """The downgrade refusal must not block rollback, which is the recorded path.

    Rollback drafts the old rules under the *next* number, so it is always newer
    than what is in force. If this ever starts failing, the guard has been written
    too broadly and has blocked the legitimate operation along with the exploit.
    """
    _permissive_v1_denying_v2(store, clock, gate)

    rolled_back = RollbackPolicy(store=store, clock=clock, operator_gate=gate).execute(
        PUBLISHER, RollbackPolicyVersion(policy_id="p", to_version=1, rationale="restore")
    )

    assert rolled_back.version == 3
    assert store.published_for("p") == rolled_back
    assert evaluate(store.all_published(), {"risk": "high"}).effect is PolicyEffect.ALLOW


# --------------------------------------------------------------------------- #
# Policies in force (NEX-083)
#
# The endpoint that answers "what is constraining this deployment right now".
# Before it, the only way to ask was to already know every ``policy_id`` and read
# each history in turn — and an id is free-form text no other endpoint enumerates.
# A policy set that cannot be enumerated can be sampled but not audited, so these
# tests pin the properties an audit depends on: the report equals the set the
# gateway evaluates, an empty set is a real answer, and nothing in it can claim a
# review that did not happen.
# --------------------------------------------------------------------------- #


def _gate_for(user_ids: set[UserId], clock: FixedClock, *scopes: str) -> OperatorGate:
    """A gate granting only ``user_ids``, each carrying ``scopes`` (default ``*``)."""
    held = frozenset(scopes) if scopes else frozenset({"*"})
    return OperatorGate(
        grants=grants_from_scopes(dict.fromkeys(user_ids, held)),
        access_log=InMemoryOperatorAccessLog(),
        clock=clock,
    )


def _in_force(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
    *,
    rate_limiter_configured: bool = True,
) -> PoliciesInForce:
    return ListPoliciesInForce(
        store=store,
        operator_gate=gate,
        clock=clock,
        rate_limiter_configured=rate_limiter_configured,
    ).execute(OPERATOR)


def _rate_limited() -> PolicyRule:
    return PolicyRule(
        rule_id="allow-rate-limited",
        effect=PolicyEffect.ALLOW,
        obligations=(PolicyObligation.RATE_LIMIT,),
    )


def test_in_force_lists_only_the_published_versions(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """A draft is not in force, so it must not appear in this report.

    This is the whole point: the report must equal the set the gateway evaluates,
    not the set of rows that exist. A draft left behind by an unfinished lifecycle
    is exactly what an operator would wrongly assume constrains the deployment if
    the report listed every row.
    """
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    live = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="live", rules=(_deny_all(),), rationale="in force")
    )
    _approve_then_publish(store, clock, gate, "live", live.version)
    draft_policy.execute(
        AUTHOR,
        DraftPolicyVersion(policy_id="pending", rules=(_deny_all(),), rationale="never published"),
    )

    snapshot = _in_force(store, clock, gate)

    assert [p.policy_id for p in snapshot.policies] == ["live"]
    assert [p.policy_id for p in snapshot.policies] == [
        v.policy_id for v in store.all_published()
    ], "the report must equal the set the enforcement path evaluates"


def test_in_force_reports_each_policy_once_when_one_is_superseded(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """A superseded version is retired, so the report never shows a policy twice.

    "Exactly one version per policy in force" is the invariant the gateway relies
    on. A report listing both v1 and v2 for one id would tell an operator that two
    rule sets are simultaneously binding when only one is.
    """
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    v1 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v1")
    )
    _approve_then_publish(store, clock, gate, "p", v1.version)
    v2 = draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v2")
    )
    _approve_then_publish(store, clock, gate, "p", v2.version)

    snapshot = _in_force(store, clock, gate)

    assert [p.version for p in snapshot.policies] == [2]
    assert snapshot.policy_count == 1


def test_in_force_is_empty_rather_than_an_error_when_nothing_is_published(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """Nothing published means the evaluator allows nothing — a fact, not a 404.

    The most restrictive deployment state is an empty policy set, and it is the
    one an operator most needs to see. Reporting it as "not found" would conflate
    it with a missing route and hide the state behind a status that reads like a
    client mistake.
    """
    snapshot = _in_force(store, clock, gate)

    assert snapshot.policies == ()
    assert snapshot.policy_count == 0


def test_in_force_is_gated_and_a_refusal_is_not_found(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """A caller with no grant must not learn the policy set, or that it exists."""
    drafted = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="x")
    )
    _approve_then_publish(store, clock, gate, "p", drafted.version)

    with pytest.raises(ResourceNotFound):
        _in_force(store, clock, _gate_for({AUTHOR.user_id}, clock))


def test_in_force_gate_runs_before_the_store_is_read(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorisation precedes the query, so a refusal cannot leak a count.

    If the store were read first, a refused caller would still have caused a full
    policy enumeration. Pinned by making any read of the store fail loudly: a
    refused caller must never reach it.
    """
    bystander = _gate_for({AUTHOR.user_id}, clock)

    def _explode() -> tuple[PolicyVersion, ...]:
        raise AssertionError("the store was read before authorisation")

    monkeypatch.setattr(store, "all_published", _explode)

    with pytest.raises(ResourceNotFound):
        _in_force(store, clock, bystander)


def test_in_force_excludes_drafts_from_the_count(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """``policy_count`` counts policies in force, not rows in the store.

    A count that included drafts would overstate how much governance is actually
    being applied — the number an operator is most likely to quote upward.
    """
    draft_policy = DraftPolicy(store=store, clock=clock, operator_gate=gate)
    for policy_id in ("a", "b"):
        drafted = draft_policy.execute(
            AUTHOR, DraftPolicyVersion(policy_id=policy_id, rules=(_deny_all(),), rationale="x")
        )
        _approve_then_publish(store, clock, gate, policy_id, drafted.version)
    draft_policy.execute(
        AUTHOR, DraftPolicyVersion(policy_id="c", rules=(_deny_all(),), rationale="draft only")
    )

    assert _in_force(store, clock, gate).policy_count == 2


def test_in_force_names_the_enforcement_gaps_the_gateway_would_refuse_on(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """A version in force that this deployment cannot enforce is reported, not hidden.

    The authoring guard refuses to *draft* an unenforceable obligation, so this
    state is reachable only by a deployment change after publication — the limiter
    removed under a live policy. The rules stay in force, the gateway denies every
    call they match, and the reason lives only in the audit trail. This report is
    where an operator can see it.
    """
    drafted = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="rl", rules=(_rate_limited(),), rationale="bounded")
    )
    _approve_then_publish(store, clock, gate, "rl", drafted.version)

    assert _in_force(store, clock, gate).policies[0].unenforceable_obligations == ()

    unenforceable = _in_force(store, clock, gate, rate_limiter_configured=False)
    assert unenforceable.policies[0].unenforceable_obligations == (
        PolicyObligation.RATE_LIMIT.value,
    )


def test_in_force_reports_a_missing_approval_rather_than_inferring_one(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version in force without a recorded approver must say so.

    ``published_by != approved_by`` is not the question. A version published
    before approval became a recorded transition (NEX-065) is in force and must
    stay there, but reporting it as reviewed would claim a human signed off on
    rules nobody did. ``approval_on_record`` is the field that cannot lie.
    """
    legacy = PolicyVersion(
        policy_id="legacy",
        version=1,
        rules=(_deny_all(),),
        rationale="published before approval became a recorded transition",
        status=PolicyStatus.PUBLISHED,
        created_at=NOW,
        published_at=NOW,
        published_by=actor_label(PUBLISHER),
        drafted_by=actor_label(AUTHOR),
        approved_by=None,
    )
    monkeypatch.setattr(store, "all_published", lambda: (legacy,))

    policy = _in_force(store, clock, gate).policies[0]

    assert policy.approval_on_record is False
    assert policy.published_by == actor_label(PUBLISHER)


def test_a_version_with_a_recorded_approval_is_reported_as_reviewed(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """The positive half: an honoured lifecycle yields ``approval_on_record``.

    Without this, the previous test would pass for a field hard-wired to ``False``
    — and a report that never reports an approval is as useless to an auditor as
    one that always does.
    """
    drafted = DraftPolicy(store=store, clock=clock, operator_gate=gate).execute(
        AUTHOR, DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="x")
    )
    _approve_then_publish(store, clock, gate, "p", drafted.version)

    policy = _in_force(store, clock, gate).policies[0]

    assert policy.approval_on_record is True
    assert policy.approved_by == actor_label(OPERATOR)
    assert policy.drafted_by == actor_label(AUTHOR)


def test_in_force_stamps_the_report_with_the_injected_clock(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """``as_of`` comes from the injected clock, not the wall clock (R29)."""
    clock.advance(minutes=3)

    assert _in_force(store, clock, gate).as_of == clock.now()


def test_in_force_names_the_adapter_that_answered(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
) -> None:
    """The process-local store must be labelled as such, never as durable storage."""
    assert _in_force(store, clock, gate).source_name == "in-memory"


def test_in_force_raises_on_a_corrupt_row_rather_than_dropping_it(
    store: InMemoryPolicyVersionRepository,
    clock: FixedClock,
    gate: OperatorGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable policy must fail the report, not silently shrink it.

    Omitting a corrupt row would understate the restriction set: an operator would
    believe fewer policies are in force than the gateway is applying. A report
    that fails loudly is the only one that cannot mislead about what is enforced.
    """

    def _corrupt() -> tuple[PolicyVersion, ...]:
        raise InvariantViolation("stored policy version is unreadable")

    monkeypatch.setattr(store, "all_published", _corrupt)

    with pytest.raises(InvariantViolation):
        _in_force(store, clock, gate)
