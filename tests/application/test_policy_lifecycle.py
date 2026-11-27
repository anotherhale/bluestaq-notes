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

from notes_service.application.errors import Conflict, ResourceNotFound
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
    PolicyRule,
    PolicyStatus,
    evaluate,
    new_policy_version,
)
from notes_service.infrastructure.governance.policy_versions import (
    InMemoryPolicyVersionRepository,
)

RISK = AttributeRef("risk", AttributeType.STRING)
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


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


# --------------------------------------------------------------------------- #
# Store: version numbers and history
# --------------------------------------------------------------------------- #


def test_next_version_number_starts_at_one(store: InMemoryPolicyVersionRepository) -> None:
    assert store.next_version_number("p") == 1


def test_next_version_number_counts_retired_versions(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """A retired version still occupies its number; reusing it would collide."""
    draft = new_policy_version(policy_id="p", version=1, rules=(_allow_low_risk(),), rationale="x")
    store.add_draft(draft)
    store.publish(draft.publish(now=clock.now(), published_by="alice"))
    store.retire(store.get("p", 1).retire())  # type: ignore[union-attr]
    assert store.next_version_number("p") == 2


def test_a_draft_cannot_overwrite_an_existing_version(
    store: InMemoryPolicyVersionRepository,
) -> None:
    draft = new_policy_version(policy_id="p", version=1, rules=(_allow_low_risk(),), rationale="x")
    store.add_draft(draft)
    with pytest.raises(InvariantViolation, match="already exists"):
        store.add_draft(draft)


def test_only_a_draft_can_be_added(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    draft = new_policy_version(policy_id="p", version=1, rules=(_allow_low_risk(),), rationale="x")
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
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    draft_policy = DraftPolicy(store=store, clock=clock)
    first = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="first")
    )
    second = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="second")
    )
    assert (first.version, second.version) == (1, 2)
    assert first.status is PolicyStatus.DRAFT


def test_draft_stamps_the_caller_clock(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """created_at comes from the injected clock, never the wall clock."""
    draft = DraftPolicy(store=store, clock=clock).execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    assert draft.created_at == NOW


def test_a_policy_must_have_at_least_one_rule(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    with pytest.raises(ValidationError, match="at least one rule"):
        DraftPolicy(store=store, clock=clock).execute(
            DraftPolicyVersion(policy_id="p", rules=(), rationale="x")
        )


# --------------------------------------------------------------------------- #
# Publish use case
# --------------------------------------------------------------------------- #


def test_publish_puts_the_version_in_force(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    draft = DraftPolicy(store=store, clock=clock).execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    published = PublishDraft(store=store, clock=clock).execute(
        PublishPolicyVersion(policy_id="p", version=draft.version, published_by="alice")
    )
    assert published.status is PolicyStatus.PUBLISHED
    assert published.published_at == NOW
    assert store.published_for("p") == published


def test_publishing_a_new_version_retires_the_previous_one(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """Exactly one version per policy is ever in force."""
    draft_policy = DraftPolicy(store=store, clock=clock)
    publish = PublishDraft(store=store, clock=clock)
    v1 = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    publish.execute(PublishPolicyVersion(policy_id="p", version=v1.version, published_by="alice"))
    v2 = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    publish.execute(PublishPolicyVersion(policy_id="p", version=v2.version, published_by="alice"))

    assert store.get("p", 1).status is PolicyStatus.RETIRED  # type: ignore[union-attr]
    assert store.get("p", 2).status is PolicyStatus.PUBLISHED  # type: ignore[union-attr]
    assert len([v for v in store.history("p") if v.status is PolicyStatus.PUBLISHED]) == 1


def test_publishing_an_unknown_version_is_not_found(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    with pytest.raises(ResourceNotFound):
        PublishDraft(store=store, clock=clock).execute(
            PublishPolicyVersion(policy_id="p", version=99, published_by="alice")
        )


def test_publishing_an_already_published_version_is_a_conflict(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    draft = DraftPolicy(store=store, clock=clock).execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    publish = PublishDraft(store=store, clock=clock)
    publish.execute(PublishPolicyVersion(policy_id="p", version=draft.version, published_by="a"))
    with pytest.raises(Conflict, match="cannot be published"):
        publish.execute(
            PublishPolicyVersion(policy_id="p", version=draft.version, published_by="a")
        )


def test_publishing_makes_the_policy_evaluable(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """End to end: a drafted-and-published policy actually decides something."""
    draft = DraftPolicy(store=store, clock=clock).execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    PublishDraft(store=store, clock=clock).execute(
        PublishPolicyVersion(policy_id="p", version=draft.version, published_by="alice")
    )
    in_force = store.all_published()
    assert evaluate(in_force, {"risk": "low"}).allowed
    assert not evaluate(in_force, {"risk": "high"}).allowed


# --------------------------------------------------------------------------- #
# Rollback: a new release, never a rewrite
# --------------------------------------------------------------------------- #


def test_rollback_creates_a_new_version_with_the_old_rules(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    draft_policy = DraftPolicy(store=store, clock=clock)
    publish = PublishDraft(store=store, clock=clock)
    v1 = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    publish.execute(PublishPolicyVersion(policy_id="p", version=v1.version, published_by="a"))
    v2 = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    publish.execute(PublishPolicyVersion(policy_id="p", version=v2.version, published_by="a"))

    rolled_back = RollbackPolicy(store=store, clock=clock).execute(
        RollbackPolicyVersion(
            policy_id="p", to_version=1, published_by="bob", rationale="restore v1 behaviour"
        )
    )

    assert rolled_back.version == 3
    assert rolled_back.status is PolicyStatus.PUBLISHED
    assert rolled_back.rules == v1.rules
    assert rolled_back.published_by == "bob"


def test_rollback_does_not_delete_history(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """The original versions stay readable exactly as they were."""
    draft_policy = DraftPolicy(store=store, clock=clock)
    publish = PublishDraft(store=store, clock=clock)
    v1 = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="v1")
    )
    publish.execute(PublishPolicyVersion(policy_id="p", version=v1.version, published_by="a"))
    v2 = draft_policy.execute(
        DraftPolicyVersion(policy_id="p", rules=(_deny_all(),), rationale="v2")
    )
    publish.execute(PublishPolicyVersion(policy_id="p", version=v2.version, published_by="a"))

    RollbackPolicy(store=store, clock=clock).execute(
        RollbackPolicyVersion(policy_id="p", to_version=1, published_by="bob", rationale="restore")
    )

    history = store.history("p")
    assert [v.version for v in history] == [1, 2, 3]
    assert history[0].status is PolicyStatus.RETIRED
    assert history[0].rules == v1.rules
    assert history[1].status is PolicyStatus.RETIRED
    assert history[2].status is PolicyStatus.PUBLISHED


def test_rolling_back_to_a_draft_is_refused(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """Rolling back to a draft would publish something nobody approved."""
    DraftPolicy(store=store, clock=clock).execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    with pytest.raises(Conflict, match="it is a draft"):
        RollbackPolicy(store=store, clock=clock).execute(
            RollbackPolicyVersion(policy_id="p", to_version=1, published_by="a", rationale="x")
        )


def test_rolling_back_to_a_missing_version_is_not_found(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    with pytest.raises(ResourceNotFound):
        RollbackPolicy(store=store, clock=clock).execute(
            RollbackPolicyVersion(policy_id="p", to_version=7, published_by="a", rationale="x")
        )


# --------------------------------------------------------------------------- #
# History read
# --------------------------------------------------------------------------- #


def test_history_reports_the_version_in_force(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    draft = DraftPolicy(store=store, clock=clock).execute(
        DraftPolicyVersion(policy_id="p", rules=(_allow_low_risk(),), rationale="x")
    )
    PublishDraft(store=store, clock=clock).execute(
        PublishPolicyVersion(policy_id="p", version=draft.version, published_by="alice")
    )
    history = GetPolicyHistory(store=store).execute("p")
    assert history.published is not None
    assert history.published.version == 1


def test_history_of_an_unknown_policy_is_not_found(
    store: InMemoryPolicyVersionRepository,
) -> None:
    with pytest.raises(ResourceNotFound):
        GetPolicyHistory(store=store).execute("missing")


def test_all_published_is_sorted_by_policy_id(
    store: InMemoryPolicyVersionRepository, clock: FixedClock
) -> None:
    """Deterministic ordering so a decision's consulted-version list is stable."""
    draft_policy = DraftPolicy(store=store, clock=clock)
    publish = PublishDraft(store=store, clock=clock)
    for policy_id in ("zeta", "alpha", "mu"):
        draft = draft_policy.execute(
            DraftPolicyVersion(policy_id=policy_id, rules=(_allow_low_risk(),), rationale="x")
        )
        publish.execute(
            PublishPolicyVersion(policy_id=policy_id, version=draft.version, published_by="a")
        )
    assert [v.policy_id for v in store.all_published()] == ["alpha", "mu", "zeta"]


def test_the_store_reports_itself_as_in_memory(
    store: InMemoryPolicyVersionRepository,
) -> None:
    """A reader must not mistake the demo adapter for durable storage."""
    assert store.name == "in-memory"
