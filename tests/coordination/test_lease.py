"""Tests for the NEXUS single-writer lease.

The interesting cases are the interleaved ones. Each models two managers against
one shared remote — which is exactly two machines sharing one origin — and
asserts that exactly one of them wins.
"""

from __future__ import annotations

import pytest

from nexus_coord.gitref import RefExistsError, RefMovedError
from nexus_coord.lease import (
    DEFAULT_TTL_SECONDS,
    LEASE_REF,
    LeaseHeldError,
    LeaseManager,
)
from nexus_coord.liveness import HolderLiveness

from .fake_git import FakeGit


class Clock:
    def __init__(self, now: int = 1_000_000) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


def managers(ttl: int = 600):
    git = FakeGit()
    clock = Clock()
    return (
        git,
        clock,
        LeaseManager(git, holder="hostA-1", ttl=ttl, clock=clock, run_id="RUN-A"),
        LeaseManager(git, holder="hostB-2", ttl=ttl, clock=clock, run_id="RUN-B"),
    )


def test_free_lock_has_no_holder():
    _, _, a, _ = managers()
    assert a.current() is None


def test_acquire_records_holder_and_run_id():
    git, _, a, b = managers()
    lease = a.acquire()

    assert lease.holder == "hostA-1"
    assert LEASE_REF in git.refs
    live = b.current()
    assert live is not None
    assert live.holder == "hostA-1"
    assert live.run_id == "RUN-A"


def test_second_agent_is_refused_while_the_first_holds_it():
    _, _, a, b = managers()
    a.acquire()

    with pytest.raises(LeaseHeldError) as err:
        b.acquire()
    assert err.value.holder == "hostA-1"
    assert err.value.remaining > 0


def test_expired_lease_is_free_so_a_crashed_run_does_not_block_the_programme():
    git, clock, a, b = managers(ttl=600)
    a.acquire()
    clock.advance(601)

    assert b.current() is None
    assert b.acquire().holder == "hostB-2"
    assert len(git.refs) == 1


def test_renew_extends_the_same_ref():
    git, clock, a, _ = managers(ttl=600)
    first = a.acquire()
    clock.advance(300)
    renewed = a.renew(first)

    assert renewed.expires_at > first.expires_at
    assert renewed.sha != first.sha
    assert len(git.refs) == 1
    assert a.current().holder == "hostA-1"


def test_renew_fails_once_another_agent_took_over():
    _, clock, a, b = managers(ttl=600)
    lease = a.acquire()
    clock.advance(601)
    b.acquire()

    with pytest.raises(LeaseHeldError):
        a.renew(lease)


def test_release_frees_the_lock_for_the_next_agent():
    git, _, a, b = managers()
    lease = a.acquire()
    a.release(lease)

    assert git.refs == {}
    assert b.acquire().holder == "hostB-2"


def test_release_is_idempotent():
    _, _, a, _ = managers()
    lease = a.acquire()
    a.release(lease)
    a.release(lease)


def test_release_of_a_superseded_lease_does_not_steal_from_the_new_holder():
    _, clock, a, b = managers(ttl=600)
    stale = a.acquire()
    clock.advance(601)
    b.acquire()

    a.release(stale)  # stale sha: must be refused, not forced

    assert b.current() is not None
    assert b.current().holder == "hostB-2"


def test_simultaneous_acquire_only_one_wins():
    """The core guarantee: two agents racing to acquire cannot both succeed."""
    git = FakeGit()
    clock = Clock()
    a = LeaseManager(git, holder="hostA-1", ttl=600, clock=clock)
    b = LeaseManager(git, holder="hostB-2", ttl=600, clock=clock)

    # B slips its create in after A decided the lock was free but before A
    # pushed — the exact window a naive read-then-write loses.
    b_lease = None

    def b_wins_the_race():
        nonlocal b_lease
        b_lease = b.acquire()

    git.before_create = b_wins_the_race

    with pytest.raises(LeaseHeldError) as err:
        a.acquire()

    assert err.value.holder == "hostB-2"
    assert b_lease is not None
    assert len(git.refs) == 1


def test_takeover_of_an_expired_lease_uses_compare_and_swap_not_force():
    """If the expired ref moves under us, the takeover must fail, not force."""
    git, clock, a, b = managers(ttl=600)
    a.acquire()
    clock.advance(601)

    def b_takes_it_first():
        b.acquire()

    git.before_update = b_takes_it_first  # B claims during A's takeover

    with pytest.raises(LeaseHeldError):
        a.acquire()


def test_holder_that_expires_mid_run_loses_the_lock_on_renewal():
    """A run must not keep writing after its lease was taken from it."""
    _, clock, a, b = managers(ttl=600)
    lease = a.acquire()
    clock.advance(601)  # A stalls past its lease
    b.acquire()  # B reasonably concludes A is dead

    with pytest.raises(LeaseHeldError):
        a.renew(lease)
    assert b.current().holder == "hostB-2"


def test_default_ttl_outlives_a_run_but_not_the_cadence():
    # A 30-minute run must never lose its lease; a crashed run must not block
    # more than a couple of scheduled successors.
    assert DEFAULT_TTL_SECONDS > 30 * 60
    assert DEFAULT_TTL_SECONDS <= 60 * 60


def test_push_create_conflict_raises_ref_exists():
    git = FakeGit()
    git.push_create(LEASE_REF, "s1")
    with pytest.raises(RefExistsError):
        git.push_create(LEASE_REF, "s2")


def test_force_with_lease_refuses_when_the_remote_moved():
    git = FakeGit()
    git.push_create(LEASE_REF, "s1")
    git.refs[LEASE_REF] = "s2"
    with pytest.raises(RefMovedError):
        git.push_update(LEASE_REF, "s3", expected="s1")


# --- R38: a stale lease, and why liveness must NOT decide take-over -------------


def _probe_says(git, clock, verdict, holder="hostB-2", ttl=600):
    """A manager whose liveness probe always returns ``verdict``."""
    return LeaseManager(
        git,
        holder=holder,
        ttl=ttl,
        clock=clock,
        run_id="RUN-B",
        holder_liveness=lambda h: verdict,
    )


def test_a_live_lease_is_not_reclaimable_even_when_the_holder_pid_is_gone():
    """R38 regression: liveness must never free an unexpired lease.

    The coordination protocol is a series of one-shot CLI invocations, so the
    pid that acquired the lease has already exited before any successor looks
    at it. If take-over were gated on a pid probe, every successor would see
    "dead" and two runs would hold the lock at once — mutual exclusion lost.
    This pins the fix: an unexpired lease blocks a successor no matter what the
    probe claims about the holder.
    """
    git, clock, a, _ = managers(ttl=600)
    a.acquire()  # hostA-1 holds an unexpired lease, and its pid is long gone

    successor = _probe_says(git, clock, HolderLiveness.DEAD)

    # The lock has not expired, so the successor must see it as held...
    assert successor.current() is not None
    assert successor.current().holder == "hostA-1"
    # ...and must not be able to steal it, however dead the probe says it is.
    with pytest.raises(LeaseHeldError) as err:
        successor.acquire()
    assert err.value.holder == "hostA-1"
    # The original holder still owns it, unchanged.
    assert a.current() is not None and a.current().holder == "hostA-1"


def test_an_expired_lease_is_reclaimable_regardless_of_liveness():
    """Expiry is the one and only thing that frees a lease."""
    git, clock, a, _ = managers(ttl=600)
    a.acquire()
    clock.advance(601)  # past the TTL

    # Even a probe that insists the holder is alive must not keep a dead lease.
    successor = _probe_says(git, clock, HolderLiveness.ALIVE)
    assert successor.current() is None
    assert successor.acquire().holder == "hostB-2"


def test_liveness_is_reported_for_status_but_does_not_gate_takeover():
    """`lease status` may report a verdict; it must not be a take-over rule."""
    git, clock, a, _ = managers(ttl=600)
    a.acquire()
    rival = _probe_says(git, clock, HolderLiveness.DEAD)

    # Reporting still works...
    assert rival.holder_liveness("hostA-1") is HolderLiveness.DEAD
    # ...but the lease is still held.
    assert rival.current() is not None


def test_the_owner_always_sees_its_own_lease():
    """A holder must never treat its own live lease as reclaimable."""
    _, _, a, _ = managers(ttl=600)
    a.acquire()

    a._holder_liveness = lambda h: HolderLiveness.DEAD
    assert a.current() is not None
    assert a.current().holder == "hostA-1"
