"""Governance policy AST: typed, declarative, and non-executable (NEX-047).

These tests pin the four properties the security architecture requires of a
policy model, each of which is a *domain* rule and therefore testable without a
database, a clock, or a settings object:

* **the AST is closed and typed.** A condition is one of four node types over a
  fixed operator vocabulary, and a type combination the declared attribute type
  does not allow is rejected at construction. There is no string to ``eval``.
* **missing attributes do not grant.** A condition over an absent attribute is
  false under *every* operator, including ``!=`` — absence must never satisfy a
  "differs from X" test.
* **conflict semantics are explicit.** An explicit deny overrides an allow, and an
  empty or non-matching policy set denies. Both are asserted directly, because
  they are the rules a reviewer most needs to trust.
* **published versions are immutable.** A version can move DRAFT → PUBLISHED →
  RETIRED and no further; the transitions return new values and refuse illegal
  ones.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.governance.policy import (
    MAX_CONDITION_DEPTH,
    MAX_CONDITION_NODES,
    MAX_REASON_LENGTH,
    AllOf,
    AnyOf,
    AttributeRef,
    AttributeType,
    Comparison,
    Not,
    PolicyEffect,
    PolicyRule,
    PolicyStatus,
    PolicyVersion,
    evaluate,
    new_policy_version,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

RISK = AttributeRef("risk", AttributeType.STRING)
CLASSIFICATION = AttributeRef("classification", AttributeType.CLASSIFICATION)
AMOUNT = AttributeRef("amount", AttributeType.NUMBER)
EXTERNAL = AttributeRef("external", AttributeType.BOOLEAN)


def _rule(rule_id: str = "r1", effect: PolicyEffect = PolicyEffect.ALLOW, when=None) -> PolicyRule:
    return PolicyRule(rule_id=rule_id, effect=effect, when=when)


def _published(rules: tuple[PolicyRule, ...], *, policy_id: str = "test-policy") -> PolicyVersion:
    draft = new_policy_version(
        policy_id=policy_id,
        version=1,
        rules=rules,
        rationale="test policy",
        drafted_by="dana",
    )
    return draft.publish(now=NOW, published_by="alice")


# --------------------------------------------------------------------------- #
# Typed AST: construction rejects what cannot be evaluated safely
# --------------------------------------------------------------------------- #


def test_numeric_operator_requires_a_number_attribute() -> None:
    """`>` on a STRING attribute is a type error, not a runtime surprise."""
    with pytest.raises(ValidationError, match="numeric and requires a NUMBER attribute"):
        Comparison(RISK, ">", "low")


def test_number_attribute_rejects_a_string_value() -> None:
    """Implicit coercion is prohibited: "5" is not 5."""
    with pytest.raises(ValidationError, match="int or float"):
        Comparison(AMOUNT, ">", "5")


def test_number_attribute_rejects_a_bool_value() -> None:
    """bool is an int subclass; accepting it would let True satisfy `>`."""
    with pytest.raises(ValidationError, match="int or float"):
        Comparison(AMOUNT, ">", True)


def test_boolean_attribute_requires_a_bool_value() -> None:
    with pytest.raises(ValidationError, match="bool value"):
        Comparison(EXTERNAL, "==", "true")


def test_unknown_operator_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unsupported comparison operator"):
        Comparison(RISK, "~=", "low")


def test_set_operator_requires_a_non_empty_set() -> None:
    with pytest.raises(ValidationError, match="requires a set of values"):
        Comparison(RISK, "in", "low")
    with pytest.raises(ValidationError, match="at least one value"):
        Comparison(RISK, "in", ())


def test_attribute_name_must_be_a_lowercase_identifier() -> None:
    """Names land in audit records; a newline is a log-injection vector."""
    with pytest.raises(ValidationError, match="lowercase identifier"):
        AttributeRef("Risk\nLevel", AttributeType.STRING)


def test_condition_tree_depth_is_bounded() -> None:
    """A tree deeper than the bound cannot be constructed at all."""
    node: Comparison | Not = Comparison(RISK, "==", "low")
    for _ in range(MAX_CONDITION_DEPTH + 2):
        node = Not(node)
    with pytest.raises(ValidationError, match="maximum depth"):
        _rule(when=node)


def test_condition_tree_size_is_bounded() -> None:
    """Depth does not bound width: a wide AllOf of many leaves is still refused."""
    leaves = tuple(Comparison(RISK, "==", f"r{i}") for i in range(MAX_CONDITION_NODES + 2))
    with pytest.raises(ValidationError, match="maximum node count"):
        _rule(when=AllOf(leaves))


def test_empty_allof_and_anyof_are_rejected() -> None:
    with pytest.raises(ValidationError, match="AllOf requires at least one"):
        AllOf(())
    with pytest.raises(ValidationError, match="AnyOf requires at least one"):
        AnyOf(())


# --------------------------------------------------------------------------- #
# Missing attributes do not grant, under any operator
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "operator,value",
    [
        ("==", "low"),
        ("!=", "low"),
        ("in", ("low", "medium")),
        ("not_in", ("low", "medium")),
    ],
)
def test_a_missing_attribute_never_satisfies_a_condition(operator: str, value: object) -> None:
    """The critical case is `!=`: absence must NOT read as "different from low".

    A policy that requires ``classification != secret`` must not pass a resource
    whose classification is unknown, or unclassified data would be treated as
    non-secret.
    """
    version = _published((_rule(when=Comparison(RISK, operator, value)),))
    decision = evaluate((version,), {})
    assert decision.effect is PolicyEffect.DENY
    assert decision.matched_rules == ()


def test_a_present_attribute_of_the_wrong_type_raises_not_denies() -> None:
    """Coercion is prohibited and hiding a data defect is worse than raising."""
    version = _published((_rule(when=Comparison(AMOUNT, ">", 10)),))
    with pytest.raises(ValidationError, match="not of declared type number"):
        evaluate((version,), {"amount": "50"})


# --------------------------------------------------------------------------- #
# Conflict semantics: deny overrides, default deny
# --------------------------------------------------------------------------- #


def test_an_explicit_deny_overrides_a_matching_allow() -> None:
    allow = _rule("allow-read", PolicyEffect.ALLOW, Comparison(RISK, "==", "low"))
    deny = _rule("deny-secret", PolicyEffect.DENY, Comparison(CLASSIFICATION, "==", "secret"))
    version = _published((allow, deny))
    decision = evaluate((version,), {"risk": "low", "classification": "secret"})
    assert decision.effect is PolicyEffect.DENY
    # Both rules matched and both are reported: the decision is explainable.
    assert set(decision.matched_rules) == {"test-policy@allow-read", "test-policy@deny-secret"}


def test_deny_wins_regardless_of_rule_order() -> None:
    """The ordering rule is the semantics, not the storage order."""
    allow = _rule("allow", PolicyEffect.ALLOW, Comparison(RISK, "==", "low"))
    deny = _rule("deny", PolicyEffect.DENY, Comparison(CLASSIFICATION, "==", "secret"))
    attributes = {"risk": "low", "classification": "secret"}
    assert evaluate((_published((allow, deny)),), attributes).effect is PolicyEffect.DENY
    assert evaluate((_published((deny, allow)),), attributes).effect is PolicyEffect.DENY


def test_an_empty_policy_set_denies() -> None:
    """Default deny: nothing in force means nothing is allowed."""
    decision = evaluate((), {"risk": "low"})
    assert decision.effect is PolicyEffect.DENY
    assert decision.matched_rules == ()


def test_no_matching_rule_denies() -> None:
    version = _published((_rule(when=Comparison(RISK, "==", "low")),))
    decision = evaluate((version,), {"risk": "high"})
    assert decision.effect is PolicyEffect.DENY


def test_an_unconditional_allow_is_an_allow() -> None:
    version = _published((_rule(when=None),))
    assert evaluate((version,), {}).effect is PolicyEffect.ALLOW


def test_an_unconditional_deny_is_a_deny() -> None:
    version = _published((_rule(effect=PolicyEffect.DENY, when=None),))
    assert evaluate((version,), {"risk": "low"}).effect is PolicyEffect.DENY


def test_composite_conditions_evaluate_correctly() -> None:
    condition = AllOf(
        (
            Comparison(RISK, "==", "low"),
            AnyOf((Comparison(AMOUNT, "<", 100), Comparison(EXTERNAL, "==", False))),
        )
    )
    version = _published((_rule(when=condition),))
    assert evaluate((version,), {"risk": "low", "amount": 50, "external": True}).allowed
    assert evaluate((version,), {"risk": "low", "amount": 500, "external": False}).allowed
    assert not evaluate((version,), {"risk": "low", "amount": 500, "external": True}).allowed
    assert not evaluate((version,), {"risk": "high", "amount": 50, "external": False}).allowed


def test_a_draft_cannot_be_evaluated() -> None:
    """A draft in force is a policy nobody approved; it must not silently apply."""
    draft = new_policy_version(policy_id="p", version=1, rules=(_rule(),), rationale="draft")
    with pytest.raises(ValidationError, match="is a draft and cannot be evaluated"):
        evaluate((draft,), {})


def test_decision_records_the_versions_consulted() -> None:
    """A decision must name which policy versions produced it."""
    a = _published((_rule("r", PolicyEffect.ALLOW, None),), policy_id="alpha")
    b = _published((_rule("r", PolicyEffect.ALLOW, None),), policy_id="beta")
    decision = evaluate((a, b), {})
    assert decision.policy_versions == ("alpha@v1", "beta@v1")
    assert decision.allowed


# --------------------------------------------------------------------------- #
# Immutability and lifecycle
# --------------------------------------------------------------------------- #


def test_a_draft_carries_no_publication_data() -> None:
    draft = new_policy_version(policy_id="p", version=1, rules=(_rule(),), rationale="x")
    assert draft.status is PolicyStatus.DRAFT
    assert draft.published_at is None
    assert draft.published_by is None
    assert not draft.is_immutable


def test_publish_returns_a_new_immutable_value() -> None:
    draft = new_policy_version(
        policy_id="p", version=1, rules=(_rule(),), rationale="x", drafted_by="dana"
    )
    published = draft.publish(now=NOW, published_by="alice")
    assert published.status is PolicyStatus.PUBLISHED
    assert published.published_at == NOW
    assert published.published_by == "alice"
    assert published.is_immutable
    # The draft object the caller still holds is untouched: publishing did not
    # mutate it in place.
    assert draft.status is PolicyStatus.DRAFT


def test_publishing_twice_is_an_invariant_violation() -> None:
    draft = new_policy_version(
        policy_id="p", version=1, rules=(_rule(),), rationale="x", drafted_by="dana"
    )
    published = draft.publish(now=NOW, published_by="alice")
    with pytest.raises(InvariantViolation, match="already published"):
        published.publish(now=NOW, published_by="alice")


def test_only_a_published_version_can_be_retired() -> None:
    draft = new_policy_version(policy_id="p", version=1, rules=(_rule(),), rationale="x")
    with pytest.raises(InvariantViolation, match="only a published version can be retired"):
        draft.retire()


def test_retire_preserves_publication_provenance() -> None:
    """Retiring supersedes; it does not erase who published what, or when."""
    draft = new_policy_version(
        policy_id="p", version=1, rules=(_rule(),), rationale="x", drafted_by="dana"
    )
    published = draft.publish(now=NOW, published_by="alice")
    retired = published.retire()
    assert retired.status is PolicyStatus.RETIRED
    assert retired.published_at == NOW
    assert retired.published_by == "alice"
    assert retired.is_immutable


def test_a_published_version_requires_a_publisher() -> None:
    """Constructing PUBLISHED by hand without provenance is refused."""
    with pytest.raises(ValidationError, match="must record when and by whom"):
        PolicyVersion(
            policy_id="p",
            version=1,
            rules=(_rule(),),
            rationale="x",
            status=PolicyStatus.PUBLISHED,
        )


def test_a_draft_cannot_smuggle_publication_data() -> None:
    with pytest.raises(ValidationError, match="must not carry publication data"):
        PolicyVersion(
            policy_id="p",
            version=1,
            rules=(_rule(),),
            rationale="x",
            status=PolicyStatus.DRAFT,
            published_at=NOW,
            published_by="alice",
        )


def test_duplicate_rule_ids_are_rejected() -> None:
    """A decision naming "rule-x" must refer to exactly one rule."""
    with pytest.raises(ValidationError, match="duplicate rule id"):
        new_policy_version(
            policy_id="p",
            version=1,
            rules=(_rule("same"), _rule("same")),
            rationale="x",
        )


def test_rationale_is_bounded() -> None:
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_LENGTH}"):
        new_policy_version(
            policy_id="p", version=1, rules=(_rule(),), rationale="x" * (MAX_REASON_LENGTH + 1)
        )


def test_version_number_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        new_policy_version(policy_id="p", version=0, rules=(_rule(),), rationale="x")
