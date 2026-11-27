"""Durable policy version store (NEX-048, closes R41).

The in-memory adapter's tests (``tests/application/test_policy_lifecycle.py``)
prove the *rules*. These prove the thing that adapter could not do: survive the
process. R41 is the gap being closed, so the tests are shaped around it:

* a published version is readable through a **fresh** store on the same database,
  which is what "survives a restart" actually means for a process-local dict;
* two workers, each with its own store, see one another's writes — the failure
  R40 found in the operator grant store, checked here before it can recur;
* the two invariants live in the schema, so a caller that bypasses the use cases
  still cannot create a second published version or reuse a version number;
* a corrupt row raises and is logged rather than silently reading as "no policy".
  An absent policy is a policy that restricts nothing, so "cannot parse" must
  never collapse into "does not exist".

As in the operator grant tests, the assertions that matter build a **second**
repository over the same database. Asserting against the instance that was
written to would pass against a dict and prove nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.governance.policy import (
    AllOf,
    AnyOf,
    AttributeRef,
    AttributeType,
    Comparison,
    Not,
    PolicyEffect,
    PolicyRule,
    PolicyStatus,
    new_policy_version,
)
from notes_service.infrastructure.governance.policy_versions_db import (
    SqlAlchemyPolicyVersionRepository,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
RISK = AttributeRef("risk", AttributeType.STRING)


@pytest.fixture(autouse=True)
def _empty_policy_table(engine: Engine) -> None:
    """The shared fixture does not truncate; each test starts from no versions."""
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM policy_versions"))


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> SqlAlchemyPolicyVersionRepository:
    return SqlAlchemyPolicyVersionRepository(session_factory)


def _restart(session_factory: sessionmaker[Session]) -> SqlAlchemyPolicyVersionRepository:
    """A brand-new adapter over the same database — what a restart produces."""
    return SqlAlchemyPolicyVersionRepository(session_factory)


def _rules() -> tuple[PolicyRule, ...]:
    return (
        PolicyRule(
            rule_id="allow-low",
            effect=PolicyEffect.ALLOW,
            when=Comparison(RISK, "==", "low"),
        ),
    )


def _draft(policy_id: str = "content.access", version: int = 1):
    return new_policy_version(
        policy_id=policy_id,
        version=version,
        rules=_rules(),
        rationale="initial release",
        drafted_by="dana",
        created_at=NOW,
    )


def test_reports_a_distinct_name(store: SqlAlchemyPolicyVersionRepository) -> None:
    # The container picks an adapter by name; two adapters answering to the same
    # name would make "which store is this process using" unanswerable in a log.
    assert store.name == "sqlalchemy"


def test_draft_round_trips_through_a_fresh_store(
    store: SqlAlchemyPolicyVersionRepository,
    session_factory: sessionmaker[Session],
) -> None:
    store.add_draft(_draft())

    found = _restart(session_factory).get("content.access", 1)

    assert found is not None
    assert found.status is PolicyStatus.DRAFT
    assert found.ref == "content.access@v1"
    assert found.rules == _rules()
    assert found.rationale == "initial release"
    assert found.created_at == NOW
    assert found.published_at is None and found.published_by is None


def test_published_version_survives_a_restart(
    store: SqlAlchemyPolicyVersionRepository,
    session_factory: sessionmaker[Session],
) -> None:
    """R41, closed: a restart must not empty the policy set.

    This is the assertion the in-memory adapter cannot pass — a new instance is a
    new process, and the published version has to still be there.
    """
    draft = _draft()
    store.add_draft(draft)
    store.publish(draft.publish(now=NOW, published_by="operator-1"))

    restarted = _restart(session_factory)

    published = restarted.published_for("content.access")
    assert published is not None
    assert published.status is PolicyStatus.PUBLISHED
    assert published.published_by == "operator-1"
    assert published.published_at == NOW
    assert [v.ref for v in restarted.all_published()] == ["content.access@v1"]


def test_two_stores_see_each_others_writes(session_factory: sessionmaker[Session]) -> None:
    """R40's lesson: workers must not hold private copies of shared state."""
    writer = SqlAlchemyPolicyVersionRepository(session_factory)
    observer = SqlAlchemyPolicyVersionRepository(session_factory)

    writer.add_draft(_draft())

    assert observer.get("content.access", 1) is not None
    assert observer.next_version_number("content.access") == 2


def test_next_version_number_counts_retired_versions(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    v1 = _draft(version=1)
    store.add_draft(v1)
    store.publish(v1.publish(now=NOW, published_by="operator-1"))
    retired = store.get("content.access", 1)
    assert retired is not None
    store.retire(retired.retire())

    # A retired number is spent: reusing it would make two policies share a ref.
    assert store.next_version_number("content.access") == 2
    assert store.next_version_number("other.policy") == 1


def test_add_draft_refuses_a_non_draft(store: SqlAlchemyPolicyVersionRepository) -> None:
    published = _draft().publish(now=NOW, published_by="operator-1")
    with pytest.raises(InvariantViolation, match="only a draft can be added"):
        store.add_draft(published)


def test_add_draft_refuses_a_duplicate_version(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    store.add_draft(_draft())
    with pytest.raises(InvariantViolation, match="already exists and cannot be overwritten"):
        store.add_draft(_draft())


def test_add_draft_requires_a_policy_version(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    with pytest.raises(ValidationError, match="requires a PolicyVersion"):
        store.add_draft(object())  # type: ignore[arg-type]


def test_publish_requires_a_stored_draft(store: SqlAlchemyPolicyVersionRepository) -> None:
    with pytest.raises(InvariantViolation, match="was never added"):
        store.publish(_draft().publish(now=NOW, published_by="operator-1"))


def test_publish_refuses_an_already_published_version(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    draft = _draft()
    store.add_draft(draft)
    store.publish(draft.publish(now=NOW, published_by="operator-1"))

    with pytest.raises(InvariantViolation, match="it is published, not draft"):
        store.publish(draft.publish(now=NOW, published_by="operator-1"))


def test_retire_requires_a_retired_value(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    """The adapter checks the value it is handed, not only the stored row.

    The domain already refuses ``draft.retire()``, so "retire a draft" cannot be
    expressed. What is still reachable is passing a *published* value to
    ``retire``, and the adapter must refuse that rather than transition the row.
    """
    draft = _draft()
    store.add_draft(draft)
    published = draft.publish(now=NOW, published_by="operator-1")
    store.publish(published)

    with pytest.raises(InvariantViolation, match="retire requires a retired version value"):
        store.retire(published)

    still = store.get("content.access", 1)
    assert still is not None and still.status is PolicyStatus.PUBLISHED


def test_history_is_oldest_first_and_includes_every_status(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    v1 = _draft(version=1)
    store.add_draft(v1)
    store.publish(v1.publish(now=NOW, published_by="operator-1"))
    retired = store.get("content.access", 1)
    assert retired is not None
    # Retire before publishing v2: the schema allows only one published version
    # per policy, which is why ``PublishDraft`` retires the predecessor first.
    store.retire(retired.retire())
    v2 = _draft(version=2)
    store.add_draft(v2)
    store.publish(v2.publish(now=NOW, published_by="operator-1"))

    history = store.history("content.access")

    assert [v.version for v in history] == [1, 2]
    assert [v.status for v in history] == [PolicyStatus.RETIRED, PolicyStatus.PUBLISHED]


def test_history_of_an_unknown_policy_is_empty(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    assert store.history("never.existed") == ()
    assert store.get("never.existed", 1) is None
    assert store.published_for("never.existed") is None
    assert store.all_published() == ()


def test_all_published_is_sorted_by_policy_id(
    store: SqlAlchemyPolicyVersionRepository,
) -> None:
    for policy_id in ("zeta.policy", "alpha.policy", "mid.policy"):
        draft = _draft(policy_id=policy_id)
        store.add_draft(draft)
        store.publish(draft.publish(now=NOW, published_by="operator-1"))

    # Deterministic order is what makes a decision's policy_versions list stable.
    assert [v.policy_id for v in store.all_published()] == [
        "alpha.policy",
        "mid.policy",
        "zeta.policy",
    ]


def test_schema_refuses_a_second_published_version_of_one_policy(
    store: SqlAlchemyPolicyVersionRepository,
    session_factory: sessionmaker[Session],
) -> None:
    """The invariant is in the schema, so a caller bypassing the use case fails.

    ``PublishDraft`` retires before publishing, so this sequence never arises
    through the application. Writing the second row directly proves the database
    refuses it rather than trusting every future caller to remember.
    """
    v1 = _draft(version=1)
    store.add_draft(v1)
    store.publish(v1.publish(now=NOW, published_by="operator-1"))
    store.add_draft(_draft(version=2))

    with pytest.raises(IntegrityError), session_factory() as session:
        session.execute(
            text(
                "UPDATE policy_versions SET status = 'published',"
                " published_at = :at, published_by = :by"
                " WHERE policy_id = :pid AND version_number = 2"
            ),
            {"at": NOW, "by": "operator-1", "pid": "content.access"},
        )
        session.commit()


def test_schema_refuses_reusing_a_version_number(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(IntegrityError), session_factory() as session:
        for _ in range(2):
            session.execute(
                text(
                    "INSERT INTO policy_versions"
                    " (policy_id, version_number, status, rationale, rules)"
                    " VALUES ('dup.policy', 1, 'draft', 'r', '[]')"
                )
            )
        session.commit()


def test_a_corrupt_row_raises_and_is_logged(
    store: SqlAlchemyPolicyVersionRepository,
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A row that no longer validates must fail loudly, never read as absent.

    Reading it as ``None`` would turn "this policy is broken" into "this policy
    does not exist" — and a missing policy restricts nothing.
    """
    store.add_draft(_draft())
    with session_factory() as session:
        session.execute(
            text("UPDATE policy_versions SET rules = :bad WHERE policy_id = :pid"),
            {"bad": '{"not":"a list"}', "pid": "content.access"},
        )
        session.commit()

    with (
        caplog.at_level(logging.WARNING, logger="notes_service.governance.policy_store"),
        pytest.raises(ValidationError),
    ):
        store.get("content.access", 1)

    assert "failed re-validation" in caplog.text
    assert "content.access" in caplog.text


def test_rules_survive_a_round_trip_unchanged(
    store: SqlAlchemyPolicyVersionRepository,
    session_factory: sessionmaker[Session],
) -> None:
    """Encoding must be lossless, or a stored policy evaluates differently."""
    rich = (
        PolicyRule(
            rule_id="complex",
            effect=PolicyEffect.DENY,
            when=AllOf(
                (
                    Comparison(RISK, "==", "high"),
                    AnyOf((Not(Comparison(RISK, "in", ("a", "b"))),)),
                )
            ),
        ),
    )
    store.add_draft(
        new_policy_version(
            policy_id="rich.policy",
            version=1,
            rules=rich,
            rationale="nested",
            created_at=NOW,
        )
    )

    reread = _restart(session_factory).get("rich.policy", 1)

    assert reread is not None
    assert reread.rules == rich
