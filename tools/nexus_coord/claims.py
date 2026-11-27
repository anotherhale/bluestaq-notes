"""Per-task claims.

The lease makes writes to the coordination files safe. It does not stop two
agents from *choosing* the same task, because they would take turns rather than
collide: A claims NEX-040, writes it up, releases; B then re-reads the backlog
and picks NEX-040 again because A's update has not been pushed yet.

A claim closes that gap. Each task is a ref, and creating a ref is atomic on the
server, so exactly one agent can win ``refs/claims/nexus/<task>``. The winner
proceeds; everyone else gets a definite "already taken" and moves to the next
task in priority order. This is the mechanism that makes parallel agents safe
rather than merely serialised.

A claim is deliberately *not* time-limited the way the lease is: a task may
legitimately take hours. Instead it is held until released, and reported as
stale after a configurable age so a crashed holder can be identified and
cleared by a human or by an explicit takeover.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass

from .gitref import GitRemote, RefExistsError, RefMovedError

CLAIMS_PREFIX = "refs/claims/nexus/"

# A claim older than this is reported as stale. This is a *report*, not an
# automatic takeover: silently reassigning long-running work would be worse than
# asking, because the original holder may still be alive and about to push.
STALE_AFTER_SECONDS = 4 * 60 * 60


def claim_ref(task_id: str) -> str:
    """Build the ref for a task id, sanitised to be ref-safe."""
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in task_id)
    return f"{CLAIMS_PREFIX}{safe.strip('.') or 'unnamed'}"


def parse_claim_ref(ref: str) -> str | None:
    if not ref.startswith(CLAIMS_PREFIX):
        return None
    return ref[len(CLAIMS_PREFIX) :] or None


@dataclass(frozen=True)
class Claim:
    """A task claim held by one agent.

    ``now`` is carried so staleness is judged by the same clock the claim was
    read with; comparing an injected clock against wall time reports nonsense.
    """

    task_id: str
    holder: str
    claimed_at: int
    run_id: str
    ref: str
    sha: str
    now: int

    def age(self) -> int:
        return self.now - self.claimed_at

    def is_stale(self) -> bool:
        return self.age() > STALE_AFTER_SECONDS


class ClaimRegistry:
    """Claim tasks so no two agents work the same one.

    ``claim`` returns a :class:`Claim` on success and ``None`` when another
    holder already owns the task. ``None`` is the normal, expected answer for
    every agent but one, and callers must treat it as "pick another task" rather
    than as an error.
    """

    def __init__(self, git: GitRemote, holder: str, run_id: str, clock: object = time.time) -> None:
        self._git = git
        self.holder = holder
        self.run_id = run_id
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())  # type: ignore[operator]

    def _read(self, task_id: str, ref: str, sha: str) -> Claim:
        local = f"refs/nexus-read/{ref}"
        self._git.fetch(f"{ref}:{local}")
        message = self._git.read_message(local) or "{}"
        try:
            meta = json.loads(message)
        except json.JSONDecodeError:
            meta = {}
        return Claim(
            task_id=task_id,
            holder=str(meta.get("holder", "unknown")),
            claimed_at=int(meta.get("claimed_at", 0)),
            run_id=str(meta.get("run_id", "")),
            ref=ref,
            sha=sha,
            now=self._now(),
        )

    def get(self, task_id: str) -> Claim | None:
        """The current claim on ``task_id``, or ``None`` if unclaimed."""
        ref = claim_ref(task_id)
        refs = self._git.ls_remote(ref)
        sha = refs.get(ref)
        if sha is None:
            return None
        return self._read(task_id, ref, sha)

    def claim(self, task_id: str) -> Claim | None:
        """Try to claim ``task_id``. ``None`` means someone else holds it."""
        ref = claim_ref(task_id)
        claimed_at = self._now()
        payload = json.dumps(
            {
                "task_id": task_id,
                "holder": self.holder,
                "claimed_at": claimed_at,
                "run_id": self.run_id,
            }
        )
        sha = self._git.commit_with_message(payload)
        try:
            self._git.push_create(ref, sha)
        except RefExistsError:
            return None
        return Claim(task_id, self.holder, claimed_at, self.run_id, ref, sha, claimed_at)

    def claim_first(self, task_ids: list[str]) -> Claim | None:
        """Claim the first available task in priority order.

        Priority order is the caller's; this walks it and stops at the first win,
        so the highest-priority unclaimed task is the one taken.
        """
        for task_id in task_ids:
            claim = self.claim(task_id)
            if claim is not None:
                return claim
        return None

    def release(self, claim: Claim) -> None:
        """Drop a claim. Safe if it is already gone."""
        with contextlib.suppress(RefMovedError):
            self._git.push_delete(claim.ref, claim.sha)

    def all_claims(self) -> list[Claim]:
        """Every outstanding claim, for reconciliation and reporting."""
        refs = self._git.ls_remote(f"{CLAIMS_PREFIX}*")
        claims = []
        for ref, sha in sorted(refs.items()):
            task_id = parse_claim_ref(ref)
            if task_id is None:
                continue
            claims.append(self._read(task_id, ref, sha))
        return claims
