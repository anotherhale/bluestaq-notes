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
) -> str:
    """Seal one immutable attempt, chained to the entry before it."""
    return _sha256(
        _canonical(
            {
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
        )
    )


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
class ChainBreak:
    """The first entry whose stored hashes do not match its content."""

    sequence: int
    invocation_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChainVerdict:
    """The result of verifying one agent's trail.

    ``checked`` counts entries that were actually recomputed, so a caller can
    distinguish "verified 40 entries" from "verified nothing". An empty trail
    is a *passing* verdict with ``checked == 0``: there is nothing to contradict,
    and reporting it as a failure would make a new agent look compromised.
    """

    verified: bool
    checked: int
    first_break: ChainBreak | None

    @property
    def reason(self) -> str | None:
        return self.first_break.reason if self.first_break is not None else None


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


def verify_chain(entries: Sequence[VerifiableEntry]) -> ChainVerdict:
    """Recompute the chain over ``entries`` and report the first contradiction.

    ``entries`` must be in ``chain_sequence`` order. Each entry is checked twice:
    its ``intent_hash`` is recomputed from its own fields *and the preceding
    entry's stored hash*, and a terminal entry's ``outcome_hash`` is recomputed
    from its own terminal fields.

    Linking to the **stored** previous hash, rather than to the recomputed one,
    is deliberate: it localises the damage. A forged entry fails its own check
    and is reported as the break, instead of cascading a failure through every
    later entry and hiding which one was actually altered.
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

        previous = entry.intent_hash
        expected_sequence += 1

    return ChainVerdict(verified=True, checked=len(entries), first_break=None)


__all__ = [
    "GENESIS_HASH",
    "ChainBreak",
    "ChainVerdict",
    "VerifiableEntry",
    "intent_hash",
    "outcome_hash",
    "verify_chain",
]
