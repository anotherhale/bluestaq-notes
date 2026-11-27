"""Tamper-evident chaining for the tool-invocation audit trail.

The gateway already writes a row for every attempt (NEX-005), and the trail is
readable over HTTP (NEX-011). Neither makes the trail *trustworthy*: a row is
ordinary mutable database state, so an operator with write access can delete a
denied invocation, flip a ``denied`` outcome to ``succeeded``, or reorder
attempts, and a reader cannot tell. An audit trail that can be quietly rewritten
records nothing worth reading.

This module makes modification detectable rather than impossible. It is the
first integrity layer of the security architecture's ledger design (canonical
event representation + partitioned hash chain); Merkle inclusion proofs, signed
checkpoints and independent anchors build on top of it later.

Two hashes per entry, because an invocation is written in two steps:

* :func:`intent_hash` seals the *immutable attempt* — who asked, for which tool,
  under which capability, with which argument digest — chained to the previous
  entry for the same agent. It is computed once, at insert, and never changes.
* :func:`outcome_hash` seals the *terminal verdict* against that intent. It is
  computed once, at finalisation, and never changes afterwards.

Splitting them is what makes the two-step write verifiable. A single hash over
the whole row could not be computed at insert (the outcome is not known yet) and
recomputing it at finalisation would break the chain of every later entry. With
the split, ``started`` rows are still fully chained, and a row whose outcome was
edited after the fact fails its own ``outcome_hash`` without disturbing anyone
else's link.

Pure by design: no ORM, no framework, no clock. The caller supplies timestamps.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

#: ``previous_hash`` of the first entry in a partition. Sixty-four zeroes, not
#: the empty string, so the field is always a well-formed SHA-256 hex digest and
#: a caller cannot distinguish "genesis" from "unset" by type.
GENESIS_HASH = "0" * 64

#: The one status that may legitimately carry no outcome seal. Kept as a literal
#: rather than importing ``InvocationStatus``: ``domain.agent.tools`` imports this
#: module for the two hash functions, so importing it back would be a cycle.
_STARTED_STATUS = "started"

_MAX_REASON = 200


def _canonical(payload: dict[str, object]) -> str:
    """A byte-stable rendering of ``payload``.

    ``sort_keys`` and a fixed separator set mean two runs produce identical
    bytes, which is the only property a hash chain needs. ``default=str``
    covers UUIDs and datetimes without letting a caller smuggle in a custom
    ``__repr__``.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp(value: datetime | None) -> str | None:
    """A timezone-independent, microsecond-precise rendering.

    Stored timestamps are UTC-aware, but a value read back from SQLite may lose
    its tzinfo. Rendering with ``isoformat()`` on a naive value would differ from
    the aware original and report a false tamper, so the offset is normalised to
    UTC explicitly.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def intent_hash(
    *,
    invocation_id: uuid.UUID | str,
    agent_id: uuid.UUID | str,
    token_id: uuid.UUID | str | None,
    tool_name: str,
    capability: str,
    idempotency_key: str,
    arguments_digest: str,
    resource_ref: str,
    approval_request_id: uuid.UUID | str | None,
    invoked_at: datetime,
    previous_hash: str,
    policy_versions: tuple[str, ...] = (),
    decision_evidence: DecisionEvidence | None = None,
) -> str:
    """Seal one immutable attempt, chained to the entry before it.

    The payload version rises only when a field is actually present, so an entry
    written before that field existed still verifies byte for byte:

    * ``v: 1`` — the original attempt.
    * ``v: 2`` — NEX-052: the attempt cites ``policy_versions``.
    * ``v: 3`` — NEX-055: a policy imposed the ``enhanced-audit`` obligation and
      the attempt carries ``decision_evidence``.

    Adding a key unconditionally would report every historical row as tampered —
    a false alarm that trains an operator to ignore the verifier. Evidence wins
    over versions when both are present: an enhanced-audit attempt always cites
    the policy versions that imposed it, so ``v: 3`` implies ``v: 2``.
    """
    payload: dict[str, object] = {
        "v": 1,
        "kind": "tool_invocation.intent",
        "id": str(invocation_id),
        "agent_id": str(agent_id),
        "token_id": str(token_id) if token_id is not None else None,
        "tool_name": tool_name,
        "capability": capability,
        "idempotency_key": idempotency_key,
        "arguments_digest": arguments_digest,
        "resource_ref": resource_ref,
        "approval_request_id": (
            str(approval_request_id) if approval_request_id is not None else None
        ),
        "invoked_at": _timestamp(invoked_at),
        "previous_hash": previous_hash,
    }
    if policy_versions:
        payload["v"] = 2
        payload["policy_versions"] = list(policy_versions)
    if decision_evidence is not None:
        payload["v"] = 3
        payload["decision_evidence"] = decision_evidence.to_payload()
    return _sha256(_canonical(payload))


def outcome_hash(
    *,
    intent_hash_value: str,
    status: str,
    outcome: str | None,
    completed_at: datetime | None,
) -> str:
    """Seal the terminal verdict against the intent it settles."""
    return _sha256(
        _canonical(
            {
                "v": 1,
                "kind": "tool_invocation.outcome",
                "intent_hash": intent_hash_value,
                "status": status,
                "outcome": outcome,
                "completed_at": _timestamp(completed_at),
            }
        )
    )


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """Extra proof recorded when a policy imposes the ``enhanced-audit`` obligation.

    The obligation says the attempt "must be recorded with more evidence than the
    default" (security architecture section 5). A boolean, or a word in the
    outcome string, would not be evidence — it would be a claim that evidence
    exists. So this type names the three things the default trail omits and the
    default trail therefore cannot answer:

    * ``matched_rules`` — *which* rules decided, not just which policy versions
      were consulted. Without it, "policy@v3 allowed this" cannot distinguish a
      rule that was meant to apply from one that merely happened to match.
    * ``obligations`` — the conditions the decision imposed, so a reader sees the
      call was allowed *conditionally* and what the conditions were.
    * ``attributes`` — the snapshot the policy was evaluated against, which is
      what makes the decision reproducible. Without the inputs, the rules can be
      replayed but the answer cannot be reproduced.

    Every field is a string, because the attribute vocabulary the enforcement
    path can attest to is strings (capability, tool, resource, agent owner), and
    because the value is sealed into a hash: a value whose *type* changed between
    write and read would recompute a different hash and be reported as tampering.
    ``to_payload`` is the single place that decides how the value renders, so the
    writer and the verifier cannot disagree.

    None of this is secret. The fields are names and identifiers, never note
    content or credentials — which is what makes it safe to record them on a
    trail that more people can read than could read the response.
    """

    matched_rules: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()

    def to_payload(self) -> dict[str, object]:
        """A JSON-native rendering, identical on the write and verify paths.

        Lists rather than tuples because a value read back from storage is a
        list; rendering a tuple here would hash differently from the list the
        verifier sees and report every enhanced-audit row as tampered.
        """
        return {
            "matched_rules": list(self.matched_rules),
            "obligations": list(self.obligations),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class ChainBreak:
    """The first entry whose stored hashes do not match its content."""

    sequence: int
    invocation_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChainAnchor:
    """The high-water mark of an agent's chain, held outside the entries.

    A hash chain alone can only prove that the entries it *contains* are intact
    and contiguous. It says nothing about how many entries there should be, so
    deleting the newest rows — the cheapest way to erase a record, since the
    survivors still verify — leaves a shorter chain that passes.

    The anchor closes that: it records the sequence and intent hash of the last
    entry ever written for the agent, in a row the entries do not live in. A
    verifier compares the trail's true tail against it, so a truncated tail is a
    contradiction rather than a shorter valid chain. New entries are linked to
    the *anchor*, not to the greatest surviving row, so a truncation cannot be
    healed by the next write either: the new entry would link to a hash whose
    entry no longer exists.
    """

    sequence: int
    intent_hash: str

    @property
    def is_empty(self) -> bool:
        return self.sequence == 0


@dataclass(frozen=True, slots=True)
class ChainVerdict:
    """The result of verifying one agent's trail.

    ``checked`` counts entries that were actually recomputed, so a caller can
    distinguish "verified 40 entries" from "verified nothing". An empty trail
    is a *passing* verdict with ``checked == 0``: there is nothing to contradict,
    and reporting it as a failure would make a new agent look compromised —
    unless an anchor says entries once existed, in which case an empty trail is
    the loudest possible finding.
    """

    verified: bool
    checked: int
    first_break: ChainBreak | None

    @property
    def reason(self) -> str | None:
        return self.first_break.reason if self.first_break is not None else None


@dataclass(frozen=True, slots=True)
class ChainAuditFinding:
    """One agent whose trail did not verify during a platform-wide sweep.

    A finding names the agent and its owner because that is the only form in
    which an integrity alert is actionable: an operator told "one of your chains
    is broken" has nothing to investigate. This is why the sweep that produces
    findings is operator-gated rather than available to any authenticated
    caller — see ``GetWithholdingReport`` for the same reasoning applied to
    counts.
    """

    agent_id: str
    owner_user_id: str
    checked: int
    reason: str
    first_break_sequence: int


@dataclass(frozen=True, slots=True)
class ChainAuditReport:
    """The result of verifying every agent's trail in one deployment.

    ``agents_checked`` against ``agents_total`` is the honesty of the sweep. A
    sweep that silently examined the first fifty agents and reported "all
    verified" would be worse than no sweep, so a capped run sets ``truncated``
    and the caller cannot mistake a partial sweep for a complete one. The same
    principle as ``ChainVerdict.checked``: a pass must carry the size of the
    thing it passed over.
    """

    agents_total: int
    agents_checked: int
    truncated: bool
    broken: tuple[ChainAuditFinding, ...]

    @property
    def verified(self) -> bool:
        """True only when a complete sweep found no contradiction.

        A truncated sweep is never ``verified``: the unexamined agents could
        each be broken, so the honest answer to "is every chain intact" is
        "unknown", and ``truncated`` is how the caller tells that apart.
        """
        return not self.truncated and not self.broken


class VerifiableEntry(Protocol):
    """Everything the verifier needs from one audited entry."""

    @property
    def chain_sequence(self) -> int: ...

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def agent_id(self) -> uuid.UUID: ...

    @property
    def token_id(self) -> uuid.UUID | None: ...

    @property
    def tool_name(self) -> str: ...

    @property
    def capability(self) -> str: ...

    @property
    def idempotency_key(self) -> str: ...

    @property
    def arguments_digest(self) -> str: ...

    @property
    def resource_ref(self) -> str: ...

    @property
    def policy_versions(self) -> tuple[str, ...]: ...

    @property
    def decision_evidence(self) -> DecisionEvidence | None: ...

    @property
    def approval_request_id(self) -> uuid.UUID | None: ...

    @property
    def invoked_at(self) -> datetime: ...

    @property
    def status(self) -> str: ...

    @property
    def outcome(self) -> str | None: ...

    @property
    def completed_at(self) -> datetime | None: ...

    @property
    def previous_hash(self) -> str: ...

    @property
    def intent_hash(self) -> str: ...

    @property
    def outcome_hash(self) -> str | None: ...


def verify_chain(
    entries: Sequence[VerifiableEntry], *, anchor: ChainAnchor | None = None
) -> ChainVerdict:
    """Recompute the chain over ``entries`` and report the first contradiction.

    ``entries`` must be in ``chain_sequence`` order. Each entry is checked twice:
    its ``intent_hash`` is recomputed from its own fields *and the preceding
    entry's stored hash*, and a terminal entry's ``outcome_hash`` is recomputed
    from its own terminal fields.

    Linking to the **stored** previous hash, rather than to the recomputed one,
    is deliberate: it localises the damage. A forged entry fails its own check
    and is reported as the break, instead of cascading a failure through every
    later entry and hiding which one was actually altered.

    ``anchor`` is what makes *deletion from the end* detectable. Without it the
    verdict describes the trail that is present and is silent about the rows that
    are not: a chain cut back to its first three entries verifies as a valid
    three-entry chain. With an anchor, the last entry must be the anchored one,
    so a shorter trail is reported as a break at the point the trail runs out.
    ``anchor=None`` means the caller had no anchor to compare against; it does
    not assert that the trail is complete.
    """
    previous = GENESIS_HASH
    expected_sequence = 1
    for entry in entries:
        if entry.chain_sequence != expected_sequence:
            return ChainVerdict(
                verified=False,
                checked=expected_sequence - 1,
                first_break=ChainBreak(
                    sequence=entry.chain_sequence,
                    invocation_id=str(entry.id),
                    reason=(
                        f"sequence gap: expected {expected_sequence}, found {entry.chain_sequence}"
                    )[:_MAX_REASON],
                ),
            )

        recomputed_intent = intent_hash(
            invocation_id=entry.id,
            agent_id=entry.agent_id,
            token_id=entry.token_id,
            tool_name=entry.tool_name,
            capability=entry.capability,
            idempotency_key=entry.idempotency_key,
            arguments_digest=entry.arguments_digest,
            resource_ref=entry.resource_ref,
            approval_request_id=entry.approval_request_id,
            invoked_at=entry.invoked_at,
            previous_hash=previous,
            policy_versions=entry.policy_versions,
            decision_evidence=entry.decision_evidence,
        )
        if recomputed_intent != entry.intent_hash:
            return ChainVerdict(
                verified=False,
                checked=expected_sequence - 1,
                first_break=ChainBreak(
                    sequence=entry.chain_sequence,
                    invocation_id=str(entry.id),
                    reason="intent hash does not match the recorded attempt",
                ),
            )

        if entry.outcome_hash is not None:
            if entry.completed_at is None:
                return ChainVerdict(
                    verified=False,
                    checked=expected_sequence - 1,
                    first_break=ChainBreak(
                        sequence=entry.chain_sequence,
                        invocation_id=str(entry.id),
                        reason="sealed outcome without a completion time",
                    ),
                )
            recomputed_outcome = outcome_hash(
                intent_hash_value=entry.intent_hash,
                status=entry.status,
                outcome=entry.outcome,
                completed_at=entry.completed_at,
            )
            if recomputed_outcome != entry.outcome_hash:
                return ChainVerdict(
                    verified=False,
                    checked=expected_sequence - 1,
                    first_break=ChainBreak(
                        sequence=entry.chain_sequence,
                        invocation_id=str(entry.id),
                        reason="outcome hash does not match the recorded verdict",
                    ),
                )
        elif entry.completed_at is not None:
            # A completion time with no sealing hash is a finalisation that
            # bypassed the chain — indistinguishable from a forged one.
            return ChainVerdict(
                verified=False,
                checked=expected_sequence - 1,
                first_break=ChainBreak(
                    sequence=entry.chain_sequence,
                    invocation_id=str(entry.id),
                    reason="completed invocation carries no outcome hash",
                ),
            )
        elif entry.status != _STARTED_STATUS:
            # A terminal verdict carrying no seal at all. Only ``started`` may
            # legitimately lack one, because its outcome is not known yet.
            # Erasing the seal is the cheapest forgery there is: rewrite a
            # ``denied`` row to ``succeeded``, null the hash and the completion
            # time, and a verifier that keyed only off ``outcome_hash is None``
            # would have nothing left to recompute against.
            return ChainVerdict(
                verified=False,
                checked=expected_sequence - 1,
                first_break=ChainBreak(
                    sequence=entry.chain_sequence,
                    invocation_id=str(entry.id),
                    reason="terminal invocation carries no outcome hash",
                ),
            )

        previous = entry.intent_hash
        expected_sequence += 1

    if anchor is not None and not anchor.is_empty:
        # Every present entry verified, but the trail must also end where the
        # anchor says it ends. A shorter trail means the newest rows were
        # deleted; an empty one means all of them were.
        if not entries:
            return ChainVerdict(
                verified=False,
                checked=0,
                first_break=ChainBreak(
                    sequence=anchor.sequence,
                    invocation_id="",
                    reason="the trail is empty but the anchor records entries that existed",
                ),
            )
        tail = entries[-1]
        if tail.chain_sequence != anchor.sequence:
            return ChainVerdict(
                verified=False,
                checked=len(entries),
                first_break=ChainBreak(
                    sequence=tail.chain_sequence + 1,
                    invocation_id="",
                    reason=(
                        "trail ends at sequence "
                        f"{tail.chain_sequence} but the anchor records {anchor.sequence}"
                    )[:_MAX_REASON],
                ),
            )
        if tail.intent_hash != anchor.intent_hash:
            return ChainVerdict(
                verified=False,
                checked=len(entries),
                first_break=ChainBreak(
                    sequence=tail.chain_sequence,
                    invocation_id=str(tail.id),
                    reason="the newest entry does not match the anchored hash",
                ),
            )

    return ChainVerdict(verified=True, checked=len(entries), first_break=None)


__all__ = [
    "GENESIS_HASH",
    "ChainAnchor",
    "ChainAuditFinding",
    "ChainAuditReport",
    "ChainBreak",
    "ChainVerdict",
    "VerifiableEntry",
    "intent_hash",
    "outcome_hash",
    "verify_chain",
]
