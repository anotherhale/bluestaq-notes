"""Governance policy: a typed declarative AST, and versions that cannot be edited.
M3.1 asks for a governance policy model with versioning. The security
architecture (section 8) is unusually specific about *how* that must work, and
every requirement here exists because the alternative is a policy engine that
looks correct while granting what it should deny:
* **"typed, declarative AST — never arbitrary Python, JavaScript, SQL, or
  user-executable code."** So a policy is a tree of :class:`Condition` nodes over
  a fixed operator vocabulary. There is no evaluator hook, no ``eval``, no
  expression string. A policy that cannot be represented by these nodes cannot be
  stored, which is a stronger guarantee than rejecting dangerous strings.
* **"missing attributes do not grant; implicit coercion is prohibited."** So
  evaluation takes an explicit attribute mapping and a *missing* key fails the
  condition that needs it rather than comparing ``None`` to something. Type
  mismatches raise rather than coerce ``"5"`` into ``5``.
* **"published policy releases are immutable"** and **"rollback is a new release
  that restores prior configuration, not mutation/deletion of published
  history."** So a :class:`PolicyVersion` that has left ``DRAFT`` is frozen: it
  cannot be edited, and a rollback is a *new* version whose rules are copied from
  an old one. Nothing here deletes a published version, because the audit trail
  of what was in force at a moment is the point of versioning.
* **"policy conflict semantics must be explicitly encoded"** and **"mandatory
  security constraints first; applicable explicit deny overrides ordinary
  grants."** So the decision order is fixed and stated once, in
  :func:`evaluate`, rather than depending on the order policies happen to be
  stored in.
What this module deliberately does not do
-----------------------------------------
It does not decide *who* may publish, *when* approval is required, or *what* the
attributes mean. Those are application and integration concerns. This module is
the vocabulary and the semantics; the use cases that gate publication come after
it, and the PIP that supplies attributes comes later still. Keeping the AST pure
means the conflict semantics can be unit-tested exhaustively without a database,
a clock, or a settings object.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from notes_service.domain.errors import InvariantViolation, ValidationError

MAX_POLICY_ID_LENGTH = 64
"""Bound on a policy's stable identifier.
A policy id is a fixed name a use case chooses (``note-share-default``), never
caller-supplied prose, so this only catches a typo or a pasted blob.
"""
MAX_REASON_LENGTH = 400
"""Bound on a version's rationale.
The same figure NEX-027 uses for a decision rationale: it is human-readable
justification, and an unbounded string in an audit record is a storage and log
problem waiting to happen.
"""
MAX_CONDITION_DEPTH = 8
"""Maximum nesting of condition nodes.
Deep trees are cheap to write and expensive to reason about; a policy that needs
more than this is usually several policies. The bound is also what makes the
recursive evaluator's cost predictable.
"""
MAX_CONDITION_NODES = 128
"""Maximum total nodes in one policy tree.
Depth alone does not bound size: a wide tree of 10 000 leaf conditions is shallow
and still unreasonable. Both bounds are enforced at construction so a policy that
passes validation has a bounded evaluation cost.
"""
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class PolicyEffect(StrEnum):
    """The outcome a matched rule contributes.
    Only two effects exist. There is deliberately no ``ABSTAIN``: a policy that
    has nothing to say is represented by *not matching*, and an explicit third
    outcome would let a caller mistake "no opinion" for a decision.
    """

    ALLOW = "allow"
    DENY = "deny"


class PolicyStatus(StrEnum):
    """Lifecycle position of a policy version.
    ``DRAFT`` versions are mutable. Every other state is frozen, because
    "published policy releases are immutable" and a rollback is a new release.
    ``RETIRED`` marks a version superseded by a later one without deleting it —
    the history of what was in force must survive.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class AttributeType(StrEnum):
    """The declared type of an attribute a condition may reference.
    Declaring the type up front is what makes "implicit coercion is prohibited"
    enforceable: a condition that compares a ``STRING`` attribute to a number is
    a validation error, not a runtime surprise.
    """

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    CLASSIFICATION = "classification"


def _validate_identifier(value: object, *, what: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be a string")
    candidate = value.strip()
    if not candidate:
        raise ValidationError(f"{what} must not be empty")
    if len(candidate) > MAX_POLICY_ID_LENGTH:
        raise ValidationError(f"{what} must be at most {MAX_POLICY_ID_LENGTH} characters")
    if not _IDENTIFIER_RE.match(candidate):
        # A dotted/hyphenated lowercase identifier. Forbidding spaces and upper
        # case is not cosmetic: these names land in audit records and log fields,
        # and a name with a newline is a log-injection vector (the same reasoning
        # as NEX-035's provider-name rule).
        raise ValidationError(
            f"{what} must be a lowercase identifier (letters, digits, '.', '_', '-')"
        )
    return candidate


class _Node:
    """Marker base for the condition tree.
    Intentionally not a dataclass: nodes are constructed through their own
    ``__post_init__`` validators and are never instantiated polymorphically.
    """

    __slots__ = ()

    def node_count(self) -> int:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class AttributeRef:
    """A reference to one attribute, with its declared type.
    The type travels with the reference rather than being looked up in a separate
    registry, so a policy is self-describing: validating a tree never needs an
    external schema, and two policies cannot disagree about what an attribute is.
    """

    name: str
    type: AttributeType

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _validate_identifier(self.name, what="attribute name"))
        if not isinstance(self.type, AttributeType):
            raise ValidationError("attribute type must be an AttributeType")


@dataclass(frozen=True, slots=True)
class Comparison(_Node):
    """``attribute <op> value`` for a constrained operator vocabulary.
    Supported operators are equality/inequality and numeric comparison plus set
    membership, matching the security architecture's allowed set. ``in`` and
    ``not_in`` take a tuple of candidate values; the rest take a single value.
    Type checking happens at construction, not evaluation: a ``NUMBER`` attribute
    compared with ``">"`` must carry a number, and a ``BOOLEAN`` attribute may only
    be compared with equality. This is the "type combinations are validated" rule
    and it means a malformed policy cannot be stored at all.
    """

    attribute: AttributeRef
    operator: str
    value: object
    _NUMERIC_OPS = frozenset({">", ">=", "<", "<="})
    _SET_OPS = frozenset({"in", "not_in"})
    _EQUALITY_OPS = frozenset({"==", "!="})

    def __post_init__(self) -> None:
        if not isinstance(self.attribute, AttributeRef):
            raise ValidationError("comparison attribute must be an AttributeRef")
        if self.operator not in self._all_operators():
            raise ValidationError(f"unsupported comparison operator: {self.operator!r}")
        self._validate_value()

    @classmethod
    def _all_operators(cls) -> frozenset[str]:
        return cls._NUMERIC_OPS | cls._SET_OPS | cls._EQUALITY_OPS

    def _validate_value(self) -> None:
        attr_type = self.attribute.type
        op = self.operator
        if op in self._SET_OPS:
            if not isinstance(self.value, (tuple, frozenset)):
                raise ValidationError(f"operator {op!r} requires a set of values")
            members = tuple(self.value)
            if not members:
                raise ValidationError(f"operator {op!r} requires at least one value")
            for member in members:
                self._validate_scalar(member, attr_type)
            return
        if op in self._NUMERIC_OPS and attr_type is not AttributeType.NUMBER:
            raise ValidationError(
                f"operator {op!r} is numeric and requires a NUMBER attribute, "
                f"not {attr_type.value!r}"
            )
        self._validate_scalar(self.value, attr_type)

    @staticmethod
    def _validate_scalar(value: object, attr_type: AttributeType) -> None:
        if attr_type is AttributeType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                # ``bool`` is an ``int`` subclass; accepting it here would let
                # ``True`` satisfy a numeric comparison, which is a coercion by
                # another name.
                raise ValidationError("NUMBER attribute requires an int or float value")
        elif attr_type is AttributeType.BOOLEAN:
            if not isinstance(value, bool):
                raise ValidationError("BOOLEAN attribute requires a bool value")
        elif not isinstance(value, str):
            raise ValidationError(f"{attr_type.value} attribute requires a string value")

    def node_count(self) -> int:
        return 1


@dataclass(frozen=True, slots=True)
class AllOf(_Node):
    """Every child must hold."""

    children: tuple[Condition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.children, tuple) or not self.children:
            raise ValidationError("AllOf requires at least one child condition")
        for child in self.children:
            if not isinstance(child, (Comparison, AllOf, AnyOf, Not)):
                raise ValidationError("AllOf children must be condition nodes")

    def node_count(self) -> int:
        return 1 + sum(child.node_count() for child in self.children)


@dataclass(frozen=True, slots=True)
class AnyOf(_Node):
    """At least one child must hold."""

    children: tuple[Condition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.children, tuple) or not self.children:
            raise ValidationError("AnyOf requires at least one child condition")
        for child in self.children:
            if not isinstance(child, (Comparison, AllOf, AnyOf, Not)):
                raise ValidationError("AnyOf children must be condition nodes")

    def node_count(self) -> int:
        return 1 + sum(child.node_count() for child in self.children)


@dataclass(frozen=True, slots=True)
class Not(_Node):
    """Negation, so "explicit deny overrides grants" can express a complement
    without a second operator per type."""

    child: Condition

    def __post_init__(self) -> None:
        if not isinstance(self.child, (Comparison, AllOf, AnyOf, Not)):
            raise ValidationError("Not child must be a condition node")

    def node_count(self) -> int:
        return 1 + self.child.node_count()


Condition = Comparison | AllOf | AnyOf | Not
"""The closed set of node types. A caller cannot add a fifth without editing this
module, which is what makes the AST non-executable by construction."""


def _check_bounds(node: Condition, *, depth: int = 1) -> None:
    if depth > MAX_CONDITION_DEPTH:
        raise ValidationError(f"condition tree exceeds maximum depth {MAX_CONDITION_DEPTH}")
    if node.node_count() > MAX_CONDITION_NODES:
        raise ValidationError(f"condition tree exceeds maximum node count {MAX_CONDITION_NODES}")
    if isinstance(node, (AllOf, AnyOf)):
        for child in node.children:
            _check_bounds(child, depth=depth + 1)
    elif isinstance(node, Not):
        _check_bounds(node.child, depth=depth + 1)


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One conditional effect: *if* ``when`` *then* ``effect``.
    A rule with ``when=None`` is unconditional. That is how a policy expresses
    "deny everything that reaches me", which is the shape a mandatory constraint
    uses when it wants to forbid a class of action outright.
    """

    rule_id: str
    effect: PolicyEffect
    when: Condition | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _validate_identifier(self.rule_id, what="rule id"))
        if not isinstance(self.effect, PolicyEffect):
            raise ValidationError("rule effect must be a PolicyEffect")
        if self.when is not None:
            if not isinstance(self.when, (Comparison, AllOf, AnyOf, Not)):
                raise ValidationError("rule condition must be a condition node or None")
            _check_bounds(self.when)


def separation_of_duties(*, author: str | None, publisher: str, ref: str) -> None:
    """Refuse to let the principal that drafted a version publish it (M3.3).

    The security architecture is explicit that sensitive actions need separation
    of duties, and the authorization model says the actor requesting a sensitive
    action cannot approve their own. A policy version is the most sensitive object
    in the platform — publishing one puts rules in force for every agent — so the
    author and the publisher must be two principals.

    Two things are deliberate:

    * **An unknown author fails closed.** A version whose author is not recorded
      cannot be checked, and "cannot be checked" must not mean "allowed", or the
      control is bypassed by omitting a field. ``None`` is therefore a refusal,
      not a pass. The message says so, because the fix (re-draft under an
      identified author) is different from the fix for a self-publish.
    * **The comparison is on the recorded identifiers**, not on a session, an
      email or a provider subject. ``published_by`` and ``drafted_by`` are both
      stamped from the authenticated principal at the boundary, so comparing them
      is comparing two authoritative identities rather than two claims.

    This is a *domain* rule because it must hold for every caller. It is checked
    again in :class:`~notes_service.application.use_cases.policy_lifecycle.
    PublishDraft` so the refusal is an application ``Conflict`` (a 409 that names
    the reason) rather than a domain invariant error; the domain check is the
    backstop for a caller that builds a published value without the use case.
    """
    if author is None:
        raise InvariantViolation(
            f"cannot publish {ref}: the drafting principal is not recorded, so "
            "separation of duties cannot be satisfied"
        )
    if author == publisher:
        raise InvariantViolation(
            f"cannot publish {ref}: {publisher} drafted it, and separation of duties "
            "requires a different principal to publish"
        )


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    """One immutable snapshot of a policy's rules.
    ``status`` is the only field that changes over a version's life, and it only
    moves forward: ``DRAFT`` → ``PUBLISHED`` → ``RETIRED``. A published version's
    *content* never changes. ``published_at`` and ``published_by`` are stamped by
    the application's clock at publication and are ``None`` while a draft, which
    is an honest "not yet published" rather than a placeholder date.
    The version number is monotonic per policy id and assigned by the store, not
    by the caller: a caller-chosen version number is a caller-chosen history.

    ``drafted_by`` is the author, stamped by the application from the
    authenticated caller for exactly the same reason ``published_by`` is: a
    caller-chosen author would let anyone attribute a policy to someone else, and
    separation of duties (M3.3) is only checkable if the author is trustworthy.
    It is ``None`` only for a version that predates the field; a version that has
    been published always names both its author and its publisher, and
    :func:`separation_of_duties` refuses the pair being the same principal.
    """

    policy_id: str
    version: int
    rules: tuple[PolicyRule, ...]
    rationale: str
    status: PolicyStatus = PolicyStatus.DRAFT
    created_at: datetime | None = None
    published_at: datetime | None = None
    published_by: str | None = None
    drafted_by: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "policy_id", _validate_identifier(self.policy_id, what="policy id")
        )
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise ValidationError("policy version must be an integer")
        if self.version < 1:
            raise ValidationError("policy version must be at least 1")
        if not isinstance(self.rules, tuple):
            raise ValidationError("policy rules must be a tuple")
        seen: set[str] = set()
        for rule in self.rules:
            if not isinstance(rule, PolicyRule):
                raise ValidationError("policy rules must be PolicyRule instances")
            if rule.rule_id in seen:
                # Duplicate rule ids make a decision unexplainable: an audit line
                # naming "rule-x" could refer to either.
                raise ValidationError(f"duplicate rule id in policy: {rule.rule_id!r}")
            seen.add(rule.rule_id)
        rationale = self.rationale.strip() if isinstance(self.rationale, str) else ""
        if not rationale:
            raise ValidationError("policy version rationale must not be empty")
        if len(rationale) > MAX_REASON_LENGTH:
            raise ValidationError(
                f"policy version rationale must be at most {MAX_REASON_LENGTH} characters"
            )
        object.__setattr__(self, "rationale", rationale)
        if not isinstance(self.status, PolicyStatus):
            raise ValidationError("policy status must be a PolicyStatus")
        if self.status is PolicyStatus.DRAFT and (
            self.published_at is not None or self.published_by is not None
        ):
            raise ValidationError("a draft policy version must not carry publication data")
        if self.status is PolicyStatus.PUBLISHED and (
            self.published_at is None or self.published_by is None
        ):
            raise ValidationError(
                "a published policy version must record when and by whom it was published"
            )

    @property
    def is_immutable(self) -> bool:
        """True once the version has left ``DRAFT``.
        Callers must not mutate a version in place at all — they are frozen
        dataclasses — but this names the *rule* so a use case can refuse to build
        an edit path for a published version rather than discovering it later.
        """
        return self.status is not PolicyStatus.DRAFT

    @property
    def ref(self) -> str:
        """The stable, human-readable reference used in decisions and audit lines."""
        return f"{self.policy_id}@v{self.version}"

    def publish(self, *, now: datetime, published_by: str) -> PolicyVersion:
        """Return this version transitioned to ``PUBLISHED``.
        Returns a new value rather than mutating, so the draft object a caller
        holds cannot be mistaken for the published one. Publishing an already
        published or retired version is a programming error, not a user error, so
        it raises :class:`InvariantViolation`.

        The publisher must not be the author: separation of duties is checked
        here as well as in the use case, so a caller that builds the published
        value directly cannot bypass it.
        """
        if self.status is not PolicyStatus.DRAFT:
            raise InvariantViolation(
                f"policy version {self.policy_id}@v{self.version} is already {self.status.value}"
            )
        publisher = _validate_identifier(published_by, what="published_by")
        separation_of_duties(author=self.drafted_by, publisher=publisher, ref=self.ref)
        return PolicyVersion(
            policy_id=self.policy_id,
            version=self.version,
            rules=self.rules,
            rationale=self.rationale,
            status=PolicyStatus.PUBLISHED,
            created_at=self.created_at,
            published_at=now,
            published_by=publisher,
            drafted_by=self.drafted_by,
        )

    def retire(self) -> PolicyVersion:
        """Return this version transitioned to ``RETIRED``.
        Retiring is how a newer release supersedes an older one without deleting
        it: the published history stays readable, and "what was in force" remains
        answerable.
        """
        if self.status is not PolicyStatus.PUBLISHED:
            raise InvariantViolation(
                f"only a published version can be retired, not {self.status.value}"
            )
        return PolicyVersion(
            policy_id=self.policy_id,
            version=self.version,
            rules=self.rules,
            rationale=self.rationale,
            status=PolicyStatus.RETIRED,
            created_at=self.created_at,
            published_at=self.published_at,
            published_by=self.published_by,
            drafted_by=self.drafted_by,
        )


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The outcome of evaluating a policy set against one attribute snapshot.
    ``matched_rules`` names the rules that contributed, in evaluation order, so a
    decision is explainable without re-running it. ``policy_versions`` records
    which version of each policy was consulted — the security architecture's
    "matched policy versions" field, and the thing that makes a decision
    reproducible after the policies move on.
    """

    effect: PolicyEffect
    matched_rules: tuple[str, ...] = field(default_factory=tuple)
    policy_versions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.effect is PolicyEffect.ALLOW


def _attribute_value(ref: AttributeRef, attributes: dict[str, object]) -> object | None:
    """Return the attribute value, or ``None`` when absent.
    Absence is signalled by ``None`` **and** a missing key, and both are treated
    identically by :func:`_compare`: a condition over a missing attribute does not
    hold. "Missing attributes do not grant" is enforced here, once.
    """
    if ref.name not in attributes:
        return None
    return attributes[ref.name]


def _compare(node: Comparison, attributes: dict[str, object]) -> bool:
    actual = _attribute_value(node.attribute, attributes)
    if actual is None:
        # Missing attribute: the condition does not hold, whichever operator was
        # used. ``!=`` on a missing attribute is False rather than True, because
        # treating absence as "different from the value" would let a policy that
        # requires "classification != secret" pass on an unclassified resource.
        return False
    attr_type = node.attribute.type
    if not _matches_declared_type(actual, attr_type):
        # The attribute exists but is the wrong type. Coercion is prohibited, and
        # silently returning False would hide a data defect, so this raises.
        raise ValidationError(
            f"attribute {node.attribute.name!r} is not of declared type {attr_type.value}"
        )
    op = node.operator
    if op == "==":
        return bool(actual == node.value)
    if op == "!=":
        return bool(actual != node.value)
    if op == ">":
        return bool(actual > node.value)  # type: ignore[operator]
    if op == ">=":
        return bool(actual >= node.value)  # type: ignore[operator]
    if op == "<":
        return bool(actual < node.value)  # type: ignore[operator]
    if op == "<=":
        return bool(actual <= node.value)  # type: ignore[operator]
    if op == "in":
        return bool(actual in node.value)  # type: ignore[operator]
    if op == "not_in":
        return bool(actual not in node.value)  # type: ignore[operator]
    raise ValidationError(f"unsupported comparison operator: {op!r}")  # pragma: no cover


def _matches_declared_type(value: object, attr_type: AttributeType) -> bool:
    if attr_type is AttributeType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if attr_type is AttributeType.BOOLEAN:
        return isinstance(value, bool)
    return isinstance(value, str)


def _evaluate_node(node: Condition, attributes: dict[str, object]) -> bool:
    if isinstance(node, Comparison):
        return _compare(node, attributes)
    if isinstance(node, AllOf):
        return all(_evaluate_node(child, attributes) for child in node.children)
    if isinstance(node, AnyOf):
        return any(_evaluate_node(child, attributes) for child in node.children)
    if isinstance(node, Not):
        return not _evaluate_node(node.child, attributes)
    raise ValidationError(f"unsupported condition node: {type(node).__name__}")


def evaluate(versions: tuple[PolicyVersion, ...], attributes: dict[str, object]) -> PolicyDecision:
    """Evaluate published policy versions in the fixed conflict order.
    The order is stated once, here, because "policy conflict semantics must be
    explicitly encoded and tested":
    1. **Mandatory constraints first, and an explicit deny anywhere wins.** Every
       matching ``DENY`` rule in any supplied version makes the decision ``DENY``,
       regardless of how many ``ALLOW`` rules also matched. This is "applicable
       explicit deny overrides ordinary grants".
    2. **An allow requires a matching allow.** If nothing matched, or only
       non-matching rules exist, the decision is ``DENY`` — default deny. An empty
       policy set therefore denies everything, which is the only safe default for
       a governance surface.
    3. **Only published versions may be consulted.** A draft in force is a policy
       nobody approved, so passing one raises rather than being ignored: silently
       dropping it would make a caller believe its rules applied.
    """
    matched: list[str] = []
    consulted: list[str] = []
    denied = False
    allowed = False
    for version in versions:
        if not isinstance(version, PolicyVersion):
            raise ValidationError("evaluate requires PolicyVersion instances")
        if version.status is PolicyStatus.DRAFT:
            raise ValidationError(f"policy {version.ref} is a draft and cannot be evaluated")
        consulted.append(version.ref)
        for rule in version.rules:
            if rule.when is not None and not _evaluate_node(rule.when, attributes):
                continue
            matched.append(f"{version.policy_id}@{rule.rule_id}")
            if rule.effect is PolicyEffect.DENY:
                denied = True
            else:
                allowed = True
    if denied:
        # Deny wins even when an allow also matched: this is the ordering rule,
        # not a tie-break.
        return PolicyDecision(
            effect=PolicyEffect.DENY,
            matched_rules=tuple(matched),
            policy_versions=tuple(consulted),
        )
    if allowed:
        return PolicyDecision(
            effect=PolicyEffect.ALLOW,
            matched_rules=tuple(matched),
            policy_versions=tuple(consulted),
        )
    return PolicyDecision(
        effect=PolicyEffect.DENY,
        matched_rules=(),
        policy_versions=tuple(consulted),
    )


def new_policy_version(
    *,
    policy_id: str,
    version: int,
    rules: tuple[PolicyRule, ...],
    rationale: str,
    drafted_by: str | None = None,
    created_at: datetime | None = None,
) -> PolicyVersion:
    """Construct a ``DRAFT`` version. The only way to make a draft.
    A small factory rather than a bare constructor call at each site, so the
    "drafts start unpublished and carry no publication data" rule has one
    implementation.

    ``drafted_by`` is the author, stamped by the application from the authenticated
    caller. It is validated through the same identifier rule as ``published_by`` so
    the two are directly comparable by :func:`separation_of_duties`; a value that is
    not a legal identifier would make the comparison meaningless.
    """
    author = _validate_identifier(drafted_by, what="drafted_by") if drafted_by is not None else None
    return PolicyVersion(
        policy_id=policy_id,
        version=version,
        rules=rules,
        rationale=rationale,
        status=PolicyStatus.DRAFT,
        created_at=created_at,
        drafted_by=author,
    )


def new_policy_version_id() -> str:
    """A fresh opaque policy id for tests and callers that do not name one."""
    return f"policy-{uuid.uuid4().hex[:12]}"


__all__ = [
    "MAX_CONDITION_DEPTH",
    "MAX_CONDITION_NODES",
    "MAX_POLICY_ID_LENGTH",
    "MAX_REASON_LENGTH",
    "AllOf",
    "AnyOf",
    "AttributeRef",
    "AttributeType",
    "Comparison",
    "Condition",
    "Not",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyRule",
    "PolicyStatus",
    "PolicyVersion",
    "evaluate",
    "new_policy_version",
    "new_policy_version_id",
    "separation_of_duties",
]
