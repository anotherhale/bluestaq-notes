"""The single-writer lease.

Only one NEXUS run may write to the coordination files at a time. The lease
enforces that across machines, processes, and crashes.

**One ref, compare-and-swap.** Mutual exclusion needs every agent to contend for
the *same* ref; a per-holder ref name would let two agents create two different
refs and both believe they hold the lock. So the lease lives at one fixed ref and
carries its holder and expiry in the commit message:

    refs/locks/nexus/lease  ->  commit whose message is
        {"holder": "hostA-1", "expires_at": 1760000000, "run_id": "NEX-038"}

Every mutation is a server-enforced compare-and-swap:

- acquire on a free lock — plain push; a second writer gets non-fast-forward.
- acquire on an expired lock — ``--force-with-lease=<observed sha>``; refused if
  anyone moved it in between.
- renew — the same compare-and-swap against our own sha.
- release — delete, guarded by the sha we hold.

**Bounded, not permanent.** A run that dies must not block the programme. The
expiry travels with the lease so any agent can decide, without trusting the
holder, whether the lock is still live. Expiry is not correctness: a holder whose
lease expires mid-run can be superseded, so the holder must renew inside the
window and must stop writing if renewal fails.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from dataclasses import dataclass

from .gitref import GitRemote, RefExistsError, RefMovedError

LEASE_REF = "refs/locks/nexus/lease"

# Must outlive one run (NEXUS caps runs at 30 minutes) so a healthy run never
# loses the lease, but not by so much that a crashed run blocks the next
# scheduled one.
DEFAULT_TTL_SECONDS = 45 * 60


def default_holder() -> str:
    """Identify this machine and process.

    The pid keeps two overlapping runs on one host distinct, which is exactly
    the collision this exists to prevent.
    """
    host = os.environ.get("NEXUS_HOLDER_HOST") or socket.gethostname() or "unknown"
    return f"{host}-{os.getpid()}"


@dataclass(frozen=True)
class Lease:
    """An acquired lease. Holding one is permission to write coordination state."""

    holder: str
    expires_at: int
    ref: str
    sha: str
    now: int
    run_id: str = ""

    def remaining(self) -> int:
        return self.expires_at - self.now

    def is_expired(self) -> bool:
        return self.remaining() <= 0


class LeaseHeldError(RuntimeError):
    """Another live holder owns the lease."""

    def __init__(self, holder: str, expires_at: int, remaining: int) -> None:
        self.holder = holder
        self.expires_at = expires_at
        self.remaining = remaining
        super().__init__(
            f"lease held by {holder!r} for another {remaining}s "
            f"(expires at {expires_at}); refusing to write"
        )


class LeaseManager:
    """Acquire, renew, and release the single-writer lease.

    Every method is safe to call concurrently from any number of machines: the
    only shared state is one remote ref, and every mutation of it is a
    compare-and-swap performed by git.
    """

    def __init__(
        self,
        git: GitRemote,
        holder: str | None = None,
        ttl: int = DEFAULT_TTL_SECONDS,
        clock: object = time.time,
        run_id: str = "",
    ) -> None:
        self._git = git
        self.holder = holder or default_holder()
        self._ttl = ttl
        self._clock = clock
        self.run_id = run_id

    def _now(self) -> int:
        return int(self._clock())  # type: ignore[operator]

    def _payload(self, expires_at: int) -> str:
        return json.dumps(
            {"holder": self.holder, "expires_at": expires_at, "run_id": self.run_id},
            sort_keys=True,
        )

    def _read(self, sha: str) -> Lease:
        local = "refs/nexus-read/lease"
        self._git.fetch(f"{LEASE_REF}:{local}")
        try:
            meta = json.loads(self._git.read_message(local) or "{}")
        except json.JSONDecodeError:
            meta = {}
        return Lease(
            holder=str(meta.get("holder", "unknown")),
            expires_at=int(meta.get("expires_at", 0)),
            ref=LEASE_REF,
            sha=sha,
            now=self._now(),
            run_id=str(meta.get("run_id", "")),
        )

    def _raw(self) -> Lease | None:
        """The lease as stored, expired or not."""
        sha = self._git.ls_remote(LEASE_REF).get(LEASE_REF)
        return None if sha is None else self._read(sha)

    def current(self) -> Lease | None:
        """The live lease, or ``None`` when the lock is free.

        An expired lease reads as free: expiry is what makes a crashed run
        recoverable without human intervention.
        """
        lease = self._raw()
        return None if lease is None or lease.is_expired() else lease

    def acquire(self) -> Lease:
        """Take the lease, or raise :class:`LeaseHeldError`.

        Raises rather than waiting: a run that cannot get the lock should exit
        immediately and cheaply, leaving the window to the run that holds it.
        """
        observed = self._raw()
        now = self._now()

        if observed is not None and observed.holder != self.holder and not observed.is_expired():
            raise LeaseHeldError(observed.holder, observed.expires_at, observed.remaining())

        expires_at = now + self._ttl
        sha = self._git.commit_with_message(self._payload(expires_at))

        if observed is None:
            try:
                self._git.push_create(LEASE_REF, sha)
            except RefExistsError:
                self._raise_lost_race()
            return Lease(self.holder, expires_at, LEASE_REF, sha, now, self.run_id)

        # Expired, or already ours: take it over with compare-and-swap.
        try:
            self._git.push_update(LEASE_REF, sha, expected=observed.sha)
        except RefMovedError:
            self._raise_lost_race()
        return Lease(self.holder, expires_at, LEASE_REF, sha, now, self.run_id)

    def _raise_lost_race(self) -> None:
        """Re-read after losing a race and report the actual winner."""
        live = self._raw()
        if live is not None and live.holder != self.holder:
            raise LeaseHeldError(live.holder, live.expires_at, live.remaining()) from None
        raise LeaseHeldError(self.holder, self._now() + self._ttl, self._ttl) from None

    def renew(self, lease: Lease) -> Lease:
        """Extend the lease. Raises :class:`LeaseHeldError` if it was taken.

        A renewal failure means another run decided this one was dead; the
        caller must treat it as "stop writing now".
        """
        expires_at = self._now() + self._ttl
        sha = self._git.commit_with_message(self._payload(expires_at))
        try:
            self._git.push_update(LEASE_REF, sha, expected=lease.sha)
        except RefMovedError:
            self._raise_lost_race()
        return Lease(self.holder, expires_at, LEASE_REF, sha, self._now(), self.run_id)

    def release(self, lease: Lease) -> None:
        """Drop the lease. Safe twice, and safe if it already expired."""
        with contextlib.suppress(RefMovedError):
            self._git.push_delete(LEASE_REF, expected=lease.sha)
