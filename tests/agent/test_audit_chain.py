"""Can a terminal verdict be laundered by erasing its seal?

``audit_chain`` exists because a tool-invocation row is ordinary mutable
database state: an operator with write access can flip a ``denied`` attempt to
``succeeded`` and a reader cannot tell. The chain defends against that with two
hashes per entry — an intent seal and an outcome seal.

The outcome seal is only a defence if its *absence* is itself suspicious for a
terminal row. It was not: ``verify_chain`` recomputed the outcome hash only when
one was present, and otherwise objected only when ``completed_at`` was set. So
the cheapest possible forgery — rewrite the status, then null both the hash and
the completion time — left nothing to recompute and verified clean.

These tests pin the fix at the domain layer, where the rule lives. The API test
(``tests/api/test_audit_chain_verification_api.py``) covers the HTTP surface but
cannot construct a forged row that still carries a valid intent hash.
"""

from __future__ import annotations

import uuid

from notes_service.domain.agent.audit_chain import (
    GENESIS_HASH,
    ChainAnchor,
    _canonical,
    _sha256,
    _timestamp,
    verify_chain,
)
from notes_service.domain.agent.tools import InvocationStatus, ToolInvocation, resolve_tool

AGENT = uuid.uuid4()


def _begin(*, key: str = "k1", note: str = "n1", sequence: int = 1) -> ToolInvocation:
    return ToolInvocation.begin(
        agent_id=AGENT,
        token_id=None,
        tool=resolve_tool("delete_note"),
        idempotency_key=key,
        arguments={"note_id": note},
        resource_ref=f"note:{note}",
        previous_hash=GENESIS_HASH,
        chain_sequence=sequence,
    )


def _genuine_chain() -> list[ToolInvocation]:
    """A genuine two-entry chain: one denial, then one success."""
    denied = _begin(key="k-deny", note="n1", sequence=1)
    denied.deny(outcome="token lacks capability")
    second = ToolInvocation.begin(
        agent_id=AGENT,
        token_id=None,
        tool=resolve_tool("delete_note"),
        idempotency_key="k-ok",
        arguments={"note_id": "n2"},
        resource_ref="note:n2",
        previous_hash=denied.intent_hash,
        chain_sequence=2,
    )
    second.succeed(outcome="deleted")
    return [denied, second]


class TestGenuineChainVerifies:
    def test_an_untampered_chain_passes(self) -> None:
        verdict = verify_chain(_genuine_chain())
        assert verdict.verified is True
        assert verdict.checked == 2

    def test_an_unfinished_started_entry_is_not_a_tamper(self) -> None:
        """A crash mid-handler leaves ``started`` with no seal. That is honest."""
        verdict = verify_chain([_begin()])
        assert verdict.verified is True
        assert verdict.checked == 1


class TestErasingTheSealIsDetected:
    def test_a_denial_rewritten_to_succeeded_with_no_seal_fails(self) -> None:
        """The exact forgery: flip the verdict, null the hash and completion time."""
        forged = _begin(key="k-forge")
        forged.deny(outcome="denied")
        forged.status = InvocationStatus.SUCCEEDED
        forged.outcome = "deleted"
        forged.outcome_hash = None
        forged.completed_at = None

        verdict = verify_chain([forged])

        assert verdict.verified is False
        assert verdict.reason == "terminal invocation carries no outcome hash"
        assert verdict.checked == 0

    def test_the_forgery_is_caught_behind_a_valid_link(self) -> None:
        denied, second = _genuine_chain()
        second.outcome_hash = None
        second.completed_at = None

        verdict = verify_chain([denied, second])

        assert verdict.verified is False
        assert verdict.checked == 1
        assert verdict.first_break is not None
        assert verdict.first_break.sequence == 2

    def test_a_completed_entry_with_no_seal_still_fails(self) -> None:
        """The pre-existing guard must not be lost to the new branch."""
        forged = _begin(key="k-completed")
        forged.deny(outcome="denied")
        forged.outcome_hash = None

        verdict = verify_chain([forged])

        assert verdict.verified is False
        assert verdict.reason == "completed invocation carries no outcome hash"

    def test_an_edited_verdict_with_a_stale_seal_fails(self) -> None:
        """Rewriting the status without re-sealing leaves the old hash behind."""
        forged = _begin(key="k-stale")
        forged.deny(outcome="denied")
        forged.status = InvocationStatus.SUCCEEDED
        forged.outcome = "deleted"

        verdict = verify_chain([forged])

        assert verdict.verified is False
        assert verdict.reason == "outcome hash does not match the recorded verdict"


def _anchor_for(entries: list[ToolInvocation]) -> ChainAnchor:
    """The anchor a correct writer would have recorded for this trail."""
    tail = entries[-1]
    return ChainAnchor(sequence=tail.chain_sequence, intent_hash=tail.intent_hash)


class TestTruncatingTheTailIsDetected:
    """The chain must not be silent about rows that are no longer there.

    Deleting the newest entries is the cheapest way to erase a record: the
    survivors still link and still seal, so a chain-only verifier passes. The
    anchor is what turns a shorter chain into a finding.
    """

    def test_a_chain_cut_back_still_verifies_without_an_anchor(self) -> None:
        """Pins why the anchor exists: this is the hole, not the fix."""
        denied, _second = _genuine_chain()

        verdict = verify_chain([denied])

        assert verdict.verified is True
        assert verdict.checked == 1

    def test_a_chain_cut_back_fails_against_the_anchor(self) -> None:
        full = _genuine_chain()
        denied = full[0]
        verdict = verify_chain([denied], anchor=_anchor_for(full))

        assert verdict.verified is False
        assert verdict.reason is not None
        assert "ends at sequence 1" in verdict.reason
        assert "anchor records 2" in verdict.reason
        assert verdict.first_break is not None
        assert verdict.first_break.sequence == 2

    def test_a_fully_erased_trail_fails_against_the_anchor(self) -> None:
        full = _genuine_chain()

        verdict = verify_chain([], anchor=_anchor_for(full))

        assert verdict.verified is False
        assert verdict.reason == ("the trail is empty but the anchor records entries that existed")
        assert verdict.checked == 0

    def test_the_anchored_tail_must_match_its_hash(self) -> None:
        """Length alone is not enough: the newest row must be the anchored one.

        A trail of the right length can still be the wrong trail. Every entry
        links and seals, so only the anchor comparison notices the swap.
        """
        full = _genuine_chain()
        other_trail_tail = ChainAnchor(sequence=2, intent_hash="f" * 64)

        verdict = verify_chain(full, anchor=other_trail_tail)

        assert verdict.verified is False
        assert verdict.reason == "the newest entry does not match the anchored hash"

    def test_an_untouched_chain_matches_its_anchor(self) -> None:
        full = _genuine_chain()

        verdict = verify_chain(full, anchor=_anchor_for(full))

        assert verdict.verified is True
        assert verdict.checked == 2

    def test_an_agent_with_no_history_has_an_empty_anchor(self) -> None:
        """``(0, GENESIS)`` means "nothing recorded", not "everything deleted"."""
        verdict = verify_chain([], anchor=ChainAnchor(sequence=0, intent_hash=GENESIS_HASH))

        assert verdict.verified is True
        assert verdict.checked == 0

    def test_no_anchor_is_not_a_claim_of_completeness(self) -> None:
        """A caller without an anchor gets the weaker verdict, documented."""
        verdict = verify_chain([], anchor=None)

        assert verdict.verified is True
        assert verdict.checked == 0


class TestPolicyVersionCitationIsVersionedIntoTheSeal:
    """NEX-052 added ``policy_versions`` to the intent payload. That is a change
    to a hash function over data that is already stored, which is exactly the
    kind of change that can invalidate every historical row.

    The field is therefore versioned *into* the payload rather than added to it:
    an invocation that consulted no policy must seal to the byte-identical
    ``v: 1`` payload it always did, and only one that cites policy moves to
    ``v: 2``. These tests pin both halves — the compatibility promise and the
    fact that a citation is genuinely sealed rather than merely recorded.
    """

    def test_an_uncited_entry_seals_exactly_as_the_v1_payload(self) -> None:
        # The pre-NEX-052 payload, rebuilt here by hand. If ``intent_hash`` ever
        # adds a key to every payload, this stops matching and every historical
        # audit row starts reporting as tampered.
        entry = _begin(key="k-v1", note="n1")

        expected = _sha256(
            _canonical(
                {
                    "v": 1,
                    "kind": "tool_invocation.intent",
                    "id": str(entry.id),
                    "agent_id": str(AGENT),
                    "token_id": None,
                    "tool_name": entry.tool_name,
                    "capability": entry.capability.value,
                    "idempotency_key": "k-v1",
                    "arguments_digest": entry.arguments_digest,
                    "resource_ref": "note:n1",
                    "approval_request_id": None,
                    "invoked_at": _timestamp(entry.invoked_at),
                    "previous_hash": GENESIS_HASH,
                }
            )
        )

        assert entry.policy_versions == ()
        assert entry.intent_hash == expected

    def test_a_cited_entry_is_sealed_under_v2_and_still_verifies(self) -> None:
        entry = ToolInvocation.begin(
            agent_id=AGENT,
            token_id=None,
            tool=resolve_tool("delete_note"),
            idempotency_key="k-v2",
            arguments={"note_id": "n1"},
            resource_ref="note:n1",
            previous_hash=GENESIS_HASH,
            chain_sequence=1,
            policy_versions=("gateway@v1",),
        )
        entry.succeed(outcome="deleted")

        assert entry.policy_versions == ("gateway@v1",)
        assert verify_chain([entry]).verified is True

    def test_the_citation_changes_the_seal(self) -> None:
        # The whole point of sealing it: an operator cannot rewrite which policy
        # a decision was made under without breaking the hash.
        uncited = _begin(key="k-same", note="n1")
        cited = ToolInvocation.begin(
            agent_id=AGENT,
            token_id=None,
            tool=resolve_tool("delete_note"),
            idempotency_key="k-same",
            arguments={"note_id": "n1"},
            resource_ref="note:n1",
            invocation_id=uncited.id,
            previous_hash=GENESIS_HASH,
            chain_sequence=1,
            policy_versions=("gateway@v1",),
        )

        assert cited.intent_hash != uncited.intent_hash

    def test_rewriting_the_citation_is_detected(self) -> None:
        # The forgery this defends against: keep a valid-looking row, swap the
        # policy it claims to have been decided under.
        entry = ToolInvocation.begin(
            agent_id=AGENT,
            token_id=None,
            tool=resolve_tool("delete_note"),
            idempotency_key="k-swap",
            arguments={"note_id": "n1"},
            resource_ref="note:n1",
            previous_hash=GENESIS_HASH,
            chain_sequence=1,
            policy_versions=("gateway@v1",),
        )
        entry.succeed(outcome="deleted")
        entry.policy_versions = ("gateway@v2",)

        verdict = verify_chain([entry])
        assert verdict.verified is False
