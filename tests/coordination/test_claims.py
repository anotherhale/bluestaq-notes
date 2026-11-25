"""Tests for per-task claims.

The failure these prevent is subtle: with only a lease, two agents serialise but
still pick the same task, because the second one reads a backlog the first has
not pushed yet. A claim makes the choice itself atomic.
"""

from __future__ import annotations

import json

import pytest

from nexus_coord.claims import (
    STALE_AFTER_SECONDS,
    ClaimRegistry,
    claim_ref,
    parse_claim_ref,
)
from nexus_coord.gitref import RefMovedError

from .fake_git import FakeGit


class Clock:
    def __init__(self, now: int = 1_000_000) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


def registries():
    git = FakeGit()
    clock = Clock()
    return (
        git,
        clock,
        ClaimRegistry(git, holder="hostA-1", run_id="RUN-A", clock=clock),
        ClaimRegistry(git, holder="hostB-2", run_id="RUN-B", clock=clock),
    )


def test_claim_ref_roundtrip():
    assert parse_claim_ref(claim_ref("NEX-040")) == "NEX-040"


def test_claim_ref_sanitises():
    assert parse_claim_ref(claim_ref("NEX 040/x")) == "NEX-040-x"


def test_parse_rejects_foreign_refs():
    assert parse_claim_ref("refs/heads/main") is None


def test_claim_when_free_records_holder_and_run():
    git, _, a, _ = registries()
    claim = a.claim("NEX-040")

    assert claim is not None
    assert claim.holder == "hostA-1"
    assert claim.run_id == "RUN-A"
    meta = json.loads(git.objects[claim.sha])
    assert meta["task_id"] == "NEX-040"
    assert meta["holder"] == "hostA-1"


def test_second_agent_cannot_claim_the_same_task():
    _, _, a, b = registries()
    assert a.claim("NEX-040") is not None
    assert b.claim("NEX-040") is None


def test_get_reports_the_current_holder():
    _, _, a, b = registries()
    a.claim("NEX-040")
    found = b.get("NEX-040")
    assert found is not None
    assert found.holder == "hostA-1"
    assert found.run_id == "RUN-A"


def test_get_returns_none_when_unclaimed():
    _, _, a, _ = registries()
    assert a.get("NEX-041") is None


def test_claim_first_walks_priority_order_and_takes_the_first_free_one():
    _, _, a, b = registries()
    a.claim("NEX-040")

    taken = b.claim_first(["NEX-040", "NEX-041", "NEX-042"])

    assert taken is not None
    assert taken.task_id == "NEX-041"


def test_claim_first_returns_none_when_every_candidate_is_taken():
    _, _, a, b = registries()
    for task in ("NEX-040", "NEX-041"):
        a.claim(task)

    assert b.claim_first(["NEX-040", "NEX-041"]) is None


def test_two_agents_racing_claim_different_tasks():
    """The property that makes parallel agents useful rather than duplicated."""
    git = FakeGit()
    clock = Clock()
    a = ClaimRegistry(git, holder="hostA-1", run_id="RUN-A", clock=clock)
    b = ClaimRegistry(git, holder="hostB-2", run_id="RUN-B", clock=clock)

    candidates = ["NEX-040", "NEX-041"]
    a_claim = a.claim_first(candidates)
    b_claim = b.claim_first(candidates)

    assert a_claim is not None and b_claim is not None
    assert a_claim.task_id != b_claim.task_id


def test_simultaneous_claim_only_one_wins():
    git, _, a, b = registries()
    b_claim = None

    def b_wins_the_race():
        nonlocal b_claim
        b_claim = b.claim("NEX-040")

    git.before_create = b_wins_the_race

    assert a.claim("NEX-040") is None
    assert b_claim is not None
    assert len(git.refs) == 1


def test_release_frees_the_task():
    _, _, a, b = registries()
    claim = a.claim("NEX-040")
    assert claim is not None
    a.release(claim)

    assert b.claim("NEX-040") is not None


def test_release_is_idempotent():
    _, _, a, _ = registries()
    claim = a.claim("NEX-040")
    assert claim is not None
    a.release(claim)
    a.release(claim)


def test_stale_claim_is_reported_not_auto_reassigned():
    _, clock, a, b = registries()
    a.claim("NEX-040")
    clock.advance(STALE_AFTER_SECONDS + 1)

    found = b.get("NEX-040")
    assert found is not None
    assert found.is_stale()
    # Still held: a long-running task must not be silently stolen.
    assert b.claim("NEX-040") is None


def test_all_claims_lists_outstanding_work():
    _, _, a, _ = registries()
    a.claim("NEX-040")
    a.claim("NEX-042")

    claims = a.all_claims()

    assert [c.task_id for c in claims] == ["NEX-040", "NEX-042"]
    assert {c.holder for c in claims} == {"hostA-1"}


def test_release_of_reassigned_claim_does_not_delete_the_new_one():
    git, _, a, b = registries()
    old = a.claim("NEX-040")
    assert old is not None
    a.release(old)
    new = b.claim("NEX-040")
    assert new is not None

    a.release(old)  # stale handle, must be a no-op

    assert b.get("NEX-040") is not None
    assert new.ref in git.refs


def test_delete_with_stale_expected_sha_is_refused():
    git = FakeGit()
    git.push_create("refs/claims/nexus/NEX-040", "s1")
    git.refs["refs/claims/nexus/NEX-040"] = "s2"
    with pytest.raises(RefMovedError):
        git.push_delete("refs/claims/nexus/NEX-040", expected="s1")
