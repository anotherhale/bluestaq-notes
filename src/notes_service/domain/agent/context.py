"""Context assembly: what may influence an outbound judgment.

:mod:`notes_service.domain.agent.decisioning` establishes that a decision carries
a SHA-256 of the caller's context rather than the context itself. That leaves the
important question unanswered: *what* is hashed? A hash is not a declassification.
SHA-256 of a note body is a fingerprint of that body, and for low-entropy content
(an email address, a title, a short message) it is a fingerprint the recipient can
confirm by guessing. Retaining only a digest therefore does **not** make it safe
to feed secret material to a third-party judgment service.

This module is the one place that decides what crosses that boundary.

A :class:`ContextEnvelope` is an ordered set of named :class:`ContextField`
values, each carrying a :class:`DataClassification`. The envelope's
:attr:`ContextEnvelope.context_hash` is computed over **only** the fields whose
classification is at or below the envelope's ceiling. Anything above the ceiling
is omitted from the canonical form entirely, so its value cannot influence the
digest at all — two envelopes that differ only in a confidential field hash
identically, which is exactly the property "the secret did not leak" and is
asserted directly in the test suite rather than assumed.

Three properties are structural:

* **The most sensitive class can never leave the process.** ``RESTRICTED`` cannot
  be named as a ceiling; a construction that tries is rejected. There is no
  configuration under which restricted content contributes to an outbound hash.
* **Exclusion is recorded, not silent.** ``excluded_field_names`` names what was
  withheld, so an audit record can say *that* a confidential field was present
  without storing or hashing it. A silent drop would make "we asked about less
  than we believed" invisible, which is the failure this whole module exists to
  prevent.
* **A field is named and bounded.** Names must be stable lowercase identifiers so
  telemetry can aggregate them; values are capped, so a field cannot become an
  unbounded blob that a hashed-but-recoverable body slips through as.

Envelopes are built with :meth:`ContextEnvelope.from_fields` (caller has already
bounded every value) or :meth:`ContextEnvelope.from_candidates` (values come from
records and may exceed the cap — those are withheld and named, never raised). The
only sanctioned way to turn one into a question is
:meth:`DecisionRequest.from_context`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum

from notes_service.domain.errors import ValidationError

#: A field name is a stable, aggregate-able identifier, not free text. Requiring
#: lowercase snake-ish names keeps telemetry groupable and makes a name that was
#: accidentally a value (a note title pasted as a key) obviously wrong.
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: A field's value is a reference — an id, a kind, a count, a short label — never
#: a document. The cap is what stops "just hash the whole note" from fitting.
MAX_FIELD_VALUE_LENGTH = 200

#: Context minimisation: a request is about one thing. A large envelope is a
#: smell that raw content is being smuggled in under many small names.
MAX_FIELDS = 32


class DataClassification(StrEnum):
    """How far a piece of context may travel.

    Ordered from least to most sensitive; the ordering is what
    :meth:`ContextEnvelope` compares a field against the ceiling. The ladder is
    deliberately coarser than a full sensitivity taxonomy — four rungs are enough
    to express "public", "ours", "the subject's", and "never leaves".
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        """Position on the ladder; higher means more sensitive."""
        return _RANK[self]


_RANK: dict[DataClassification, int] = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.RESTRICTED: 3,
}

#: The default ceiling: internal context may inform an outbound judgment, the
#: subject's confidential material may not. Callers may widen this per request,
#: but never to ``RESTRICTED``.
DEFAULT_CEILING = DataClassification.INTERNAL


class WithholdingReason(StrEnum):
    """Why a context field was held back from an outbound judgment.

    The two reasons are operationally different even though both mean "present,
    not sent", which is why they are not one flag:

    * :attr:`ABOVE_CEILING` is the control *working as designed*. The caller
      classified the value and the envelope's ceiling excluded it. Nothing is
      wrong, and nothing should change.
    * :attr:`OVER_CAP` means the value did not fit ``MAX_FIELD_VALUE_LENGTH``, so
      it could not be represented at all. This is the thinner-than-expected
      signal: if it happens routinely, every advisory is being computed on less
      context than the caller believes, and the caller should either send a
      reference instead of content or the cap should be revisited deliberately.

    Collapsing the two into one list made a routine design decision
    indistinguishable from a quiet degradation.
    """

    ABOVE_CEILING = "above_ceiling"
    OVER_CAP = "over_cap"


@dataclass(frozen=True, slots=True)
class WithheldField:
    """A field name withheld from the digest, with the reason it was withheld.

    Names only — never values. The value that was withheld is deliberately not
    retained here: keeping it would leave the raw subject text in memory one
    refactor away from the canonical form, which is the thing withholding exists
    to prevent.
    """

    name: str
    reason: WithholdingReason

    def __post_init__(self) -> None:
        _validate_field_name(self.name)
        if not isinstance(self.reason, WithholdingReason):
            raise ValidationError("a withheld field requires a WithholdingReason")


def _validate_field_name(name: object) -> str:
    """Reject a name that is not a stable, aggregate-able identifier.

    Extracted so the withholding constructor can check a name *before* deciding
    whether the value is small enough to include, without duplicating the rule.
    """
    if not isinstance(name, str) or not _FIELD_NAME_RE.match(name):
        raise ValidationError(
            "a context field name must be a lowercase identifier of at most 64 characters"
        )
    return name


@dataclass(frozen=True, slots=True)
class ContextField:
    """One named, classified piece of context.

    ``value`` is expected to be a reference (an id, a kind, a bounded label), not
    document text. The bound is enforced, but it is not a licence: the
    classification, not the length, is what decides whether the value travels.
    """

    name: str
    classification: DataClassification
    value: str

    def __post_init__(self) -> None:
        _validate_field_name(self.name)
        if not isinstance(self.classification, DataClassification):
            raise ValidationError("a context field requires a DataClassification")
        if not isinstance(self.value, str):
            raise ValidationError("a context field value must be a string")
        if len(self.value) > MAX_FIELD_VALUE_LENGTH:
            raise ValidationError(
                f"a context field value may be at most {MAX_FIELD_VALUE_LENGTH} characters"
            )


#: A digest is a SHA-256 rendered as lowercase hex. Validated at mint time so a
#: malformed digest can never reach a request.
_CONTEXT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

#: Construction token. Only :meth:`ContextHash._mint` holds it, and only
#: :class:`ContextEnvelope` calls that. A plain ``ContextHash("…")`` — the shape a
#: caller would write to hand-assemble a declassification — raises instead.
_MINT = object()


class ContextHash(str):
    """A context digest that only an envelope may mint.

    The distinction this type exists to make is *who decided what left the
    process*. A digest is a fingerprint of the included fields, so asserting one
    is asserting "these were the inputs". If any caller could construct the
    digest directly, it could present a hash that does not correspond to the
    envelope it is sending — deciding the declassification itself, which is
    exactly what ADR-012 forbids.

    It subclasses ``str`` so that it serialises, compares, and hashes as the
    plain hex digest in JSON, logs, and audit records; the type is a
    construction-time guard, not a new wire format.

    Honest limit: ``str.__new__(ContextHash, …)`` still bypasses this, and a
    determined caller can reach it. The guard's job is to make the mistake
    impossible to write by accident and the bypass deliberate and visible, not
    to defeat a hostile process that already runs in this one.
    """

    __slots__ = ()

    def __new__(cls, digest: str, *, _token: object | None = None) -> ContextHash:
        if _token is not _MINT:
            raise ValidationError(
                "a ContextHash may only be minted by a ContextEnvelope; "
                "construct the envelope and read its context_hash"
            )
        if not isinstance(digest, str) or not _CONTEXT_HASH_RE.fullmatch(digest):
            raise ValidationError("a context hash must be a lowercase 64-character hex digest")
        return super().__new__(cls, digest)

    @classmethod
    def _mint(cls, digest: str) -> ContextHash:
        return cls(digest, _token=_MINT)

    @property
    def digest(self) -> str:
        """The digest as a plain ``str``, for explicit unwrapping at boundaries."""
        return str.__str__(self)


def _canonical(fields: tuple[ContextField, ...]) -> str:
    """Canonical JSON of the *included* fields.

    Sorted by name and separated compactly so the digest is reproducible across
    processes and platforms. Only name and value enter it: the classification
    itself is not part of the identity of the content, and including it would
    make the same value hash differently depending on how carefully it was
    labelled.
    """
    payload = [{"name": field.name, "value": field.value} for field in sorted(fields, key=_by_name)]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _by_name(field: ContextField) -> str:
    return field.name


def _by_withheld_name(field: WithheldField) -> str:
    return field.name


@dataclass(frozen=True, slots=True)
class ContextEnvelope:
    """The classified inputs to one outbound judgment, and their shared digest.

    Construct with :meth:`from_fields`. The envelope is immutable and its hash is
    derived, not stored by the caller, so a caller cannot assert a digest that
    disagrees with the fields it is presenting.
    """

    fields: tuple[ContextField, ...]
    ceiling: DataClassification
    #: Names withheld because their value exceeded ``MAX_FIELD_VALUE_LENGTH``.
    #: Kept separate from ``fields`` so a withheld value is never constructed —
    #: storing it and filtering later would leave the raw subject text in memory
    #: and one refactor away from entering the digest.
    withheld_field_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.ceiling, DataClassification):
            raise ValidationError("the ceiling must be a DataClassification")
        if self.ceiling is DataClassification.RESTRICTED:
            # No configuration may send restricted content to a judgment service.
            # This is the module's hardest rule and it is a construction error,
            # not a runtime branch, so it cannot be reached by data at all.
            raise ValidationError("restricted context may never leave the process")
        if len(self.fields) > MAX_FIELDS:
            raise ValidationError(f"an envelope may hold at most {MAX_FIELDS} fields")
        # A withheld name must still be a legal name, and must not also be present
        # as a field: "withheld" and "included" are contradictory statements about
        # one input, and an audit reader would have no way to choose between them.
        present = {field.name for field in self.fields}
        for name in self.withheld_field_names:
            _validate_field_name(name)
            if name in present:
                raise ValidationError(f"a field cannot be both included and withheld: {name!r}")
        if len(set(self.withheld_field_names)) != len(self.withheld_field_names):
            raise ValidationError("withheld field names must not repeat")

    @classmethod
    def from_fields(
        cls,
        fields: tuple[ContextField, ...] | list[ContextField],
        *,
        ceiling: DataClassification = DEFAULT_CEILING,
    ) -> ContextEnvelope:
        """Build an envelope, refusing duplicate field names.

        Duplicates are rejected rather than merged: two fields with one name would
        make ``excluded_field_names`` ambiguous about which value was withheld.
        """
        materialised = tuple(fields)
        seen: set[str] = set()
        for field in materialised:
            if not isinstance(field, ContextField):
                raise ValidationError("an envelope holds only ContextField values")
            if field.name in seen:
                raise ValidationError(f"duplicate context field name: {field.name!r}")
            seen.add(field.name)
        return cls(fields=materialised, ceiling=ceiling)

    @classmethod
    def from_candidates(
        cls,
        candidates: (
            tuple[tuple[str, DataClassification, str], ...]
            | list[tuple[str, DataClassification, str]]
        ),
        *,
        ceiling: DataClassification = DEFAULT_CEILING,
    ) -> ContextEnvelope:
        """Build an envelope from record-derived values that may exceed the cap.

        :meth:`from_fields` requires the caller to have bounded every value, which
        is correct for hand-authored references but wrong for subject content: a
        note title is legal at the notes domain's ``MAX_TITLE_LENGTH`` and can
        reach this module already validated. Passing such a value to
        :class:`ContextField` raises, which would turn "the title is long" into a
        failed advisory — a denial of a read the caller is entitled to.

        Here an over-cap value is **withheld instead**: the field is not
        constructed, so it cannot enter the digest, and its name is recorded in
        :attr:`excluded_field_names` so the audit trail still says the input
        existed. This mirrors the classification rule one level down — the
        envelope already withholds what it may not send rather than refusing the
        request, and an over-long value is withheld for the same reason: it is not
        a reference, so it must not travel.

        The two caps are independent on purpose. ``MAX_TITLE_LENGTH`` belongs to
        the notes domain; ``MAX_FIELD_VALUE_LENGTH`` belongs to context
        minimisation. They happen to both be 200 today, and this constructor is
        what makes that coincidence harmless.
        """
        included: list[ContextField] = []
        withheld: set[str] = set()
        for name, classification, value in candidates:
            _validate_field_name(name)
            # Validate the classification on *both* paths. Previously an over-cap
            # candidate skipped this check entirely, because the field was never
            # constructed — so a caller could pass a bare string and learn nothing,
            # while the same mistake under the cap raised. The withheld path must
            # not be a hole in the type check just because it is the path that
            # discards the value.
            if not isinstance(classification, DataClassification):
                raise ValidationError("a context field requires a DataClassification")
            if not isinstance(value, str):
                raise ValidationError("a context field value must be a string")
            if len(value) > MAX_FIELD_VALUE_LENGTH:
                withheld.add(name)
                continue
            included.append(ContextField(name=name, classification=classification, value=value))
        envelope = cls.from_fields(included, ceiling=ceiling)
        # Union rather than replace: the ceiling already withholds some names, and
        # an over-cap field that is *also* above the ceiling must still be named
        # once, not shadowed.
        return replace(envelope, withheld_field_names=tuple(sorted(withheld)))

    def _is_included(self, field: ContextField) -> bool:
        return field.classification.rank <= self.ceiling.rank

    @property
    def included_fields(self) -> tuple[ContextField, ...]:
        """Fields that contribute to :attr:`context_hash`."""
        return tuple(field for field in self.fields if self._is_included(field))

    @property
    def excluded_field_names(self) -> tuple[str, ...]:
        """Names withheld from the digest, for honest audit without the values.

        Two distinct reasons are folded into one list because an audit reader
        needs the same fact either way — *this input was held back*. Above-ceiling
        classification and an over-cap value are both "present, not sent".

        Use :attr:`withheld_fields` when the *reason* matters: this list cannot
        tell a deliberate exclusion from a value that did not fit.
        """
        by_classification = {field.name for field in self.fields if not self._is_included(field)}
        return tuple(sorted(by_classification | set(self.withheld_field_names)))

    @property
    def withheld_fields(self) -> tuple[WithheldField, ...]:
        """Every withheld field with the reason it was withheld.

        The reason is what makes a thinner-than-expected advisory visible.
        ``excluded_field_names`` says *that* an input was held back; this says
        *why*, and an :attr:`~WithholdingReason.OVER_CAP` entry is the one worth
        watching — it means the value could not be represented, not that policy
        excluded it.

        An over-cap value that is also above the ceiling is reported once, as
        ``OVER_CAP``: the value did not fit, and reporting it twice would double
        count one input in telemetry. ``excluded_field_names`` still names it
        exactly once.
        """
        over_cap = set(self.withheld_field_names)
        withheld = [
            WithheldField(name=field.name, reason=WithholdingReason.ABOVE_CEILING)
            for field in self.fields
            if not self._is_included(field) and field.name not in over_cap
        ]
        withheld.extend(
            WithheldField(name=name, reason=WithholdingReason.OVER_CAP)
            for name in self.withheld_field_names
        )
        return tuple(sorted(withheld, key=_by_withheld_name))

    @property
    def over_cap_field_names(self) -> tuple[str, ...]:
        """Names withheld because the value exceeded the field cap.

        The subset of :attr:`excluded_field_names` that signals degradation
        rather than design. Kept as its own accessor so a caller (or a metric)
        can watch it without parsing reasons.
        """
        return tuple(sorted(self.withheld_field_names))

    @property
    def context_hash(self) -> ContextHash:
        """SHA-256 over the included fields only, minted by this envelope.

        Fields above the ceiling are absent from the canonical form, so their
        values cannot be recovered from, confirmed against, or even distinguished
        by this digest.
        """
        digest = hashlib.sha256(_canonical(self.included_fields).encode("utf-8")).hexdigest()
        return ContextHash._mint(digest)


__all__ = [
    "DEFAULT_CEILING",
    "MAX_FIELDS",
    "MAX_FIELD_VALUE_LENGTH",
    "ContextEnvelope",
    "ContextField",
    "ContextHash",
    "DataClassification",
    "WithheldField",
    "WithholdingReason",
]
