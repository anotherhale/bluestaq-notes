# ADR-013: Git Refs as the Coordination Primitive

## Status

Accepted

## Context

NEXUS coordinates itself through plain files on one git branch:
`docs/NEXUS/CHECKPOINT.json`, `STATE.md`, `BACKLOG.md`, `EXECUTION_LOG.md`. Those
files are the programme's memory, and they have no concurrency control at all. A
git branch is last-writer-wins at file level: two runs that both read the
checkpoint, both edit it, and both push produce a tree containing one of the two
edits, chosen by push order rather than by intent.

That was tolerable while runs were serial and rare. It is not tolerable now, for
two reasons visible in this repository's own history:

1. **Overlapping runs already happen.** The scheduler does not serialise runs, so
   a slow run and the next scheduled run can be alive at once. When that happens
   both read the same checkpoint and one of them silently loses its state update.
2. **Repeated divergence.** The execution log records runs whose opening
   reconciliation found the local branch several commits behind the remote, and
   one run (`NEX-2026-11-25-37`) that had to fast-forward four commits before it
   could start. Some of that is scheduler overlap; some is a fresh clone reading a
   stale tracking ref. Both are symptoms of having no single writer.

The obvious fix — "just be careful" — is not a fix, because the failure is
invisible. Nothing errors. The losing run reports success, and its state update
is simply gone. A coordination substrate whose failure mode is silent data loss
cannot be made reliable by discipline.

What is needed is one primitive: an atomic **compare-and-swap** that a remote
server enforces, so that "I claim this" and "I hold the lock" have a single
winner and a definite loser who can be told so.

## Decision

Add `tools/nexus_coord`, an operator-side package that provides two primitives on
top of git refs, which are the only server-side atomic operations available
without new infrastructure.

### The lease: one ref, compare-and-swap

Mutual exclusion requires every agent to contend for the *same* ref. A
per-holder ref name would let two agents create two different refs and each
believe it holds the lock, which is worse than no lock because it looks like one.
So the lease is one fixed ref:

```
refs/locks/nexus/lease
```

with the holder, expiry, and run id carried in the commit message. Every mutation
is a server-enforced compare-and-swap:

| Operation | Mechanism | Loser sees |
| --- | --- | --- |
| acquire (free) | plain push | non-fast-forward → `RefExistsError` |
| acquire (expired) / renew | `--force-with-lease=<observed sha>` | `stale info` → `RefMovedError` |
| release | sha-guarded delete | `stale info` → `RefMovedError` |

`--force-with-lease` is the opposite of a force push: it *refuses* to write when
the remote has moved since the caller looked. Nothing in the package uses
`--force`, and nothing resets or cleans.

### The lease is bounded, not permanent

A run that dies must not block the programme until a human notices. The expiry
travels inside the lease, so any agent can decide — without trusting the holder —
whether the lock is still live. Expiry is deliberately **not** treated as
correctness: a holder whose lease expires mid-run can be superseded, so the
holder must renew inside the window and must stop writing if renewal fails.

### Claims: one ref per task

The lease makes writes safe but does not stop two agents from *choosing* the same
task, because they would take turns rather than collide. A claim closes that
gap: each task is a ref, and creating a ref is atomic, so exactly one agent wins
`refs/claims/nexus/<task>`.

Claims are not time-limited the way the lease is, because a task may legitimately
take hours. A claim older than `STALE_AFTER_SECONDS` is **reported** as stale, not
taken over. Silently reassigning long-running work would be worse than asking,
because the original holder may still be alive and about to push.

### Testability

All coordination logic depends on a `GitRemote` protocol, not on the `git`
binary. `SubprocessGit` is the production adapter; the tests drive an in-memory
`FakeGit` that reproduces git's three compare-and-swap semantics exactly. The
interleaving tests model two managers against one shared remote — which is
literally two machines sharing one origin — and assert that exactly one wins.

## Consequences

**Good.** Coordination becomes safe across machines and processes without new
infrastructure, a new service, or a new dependency. A crash leaves a bounded,
self-healing lock rather than a permanent one. Failures become loud: a losing
writer is told it lost instead of silently overwriting.

**Costs and limits, stated honestly.**

- The primitive coordinates *agents that use it*. It cannot stop a run that
  ignores the lease from pushing anyway; enforcement is by convention, not by the
  server. This is a real limit and the reason the package is a tool, not a gate.
- Ref names are global to the repository, so the lock is per-repository, not
  per-branch. Two agents working different branches of one repo contend for the
  same lease. For NEXUS that is the desired scope.
- Clock skew matters: expiry is compared against the local clock. A badly skewed
  host can believe a live lease is expired. The TTL (45 minutes, comfortably
  longer than the 30-minute run cap) absorbs ordinary skew but not a wrong clock.
- `commit-tree` writes dangling commits into the object store. They are
  unreachable and are garbage-collected; this is acceptable for a lock ref.

## Rejected alternatives

**A lockfile committed to the branch.** Requires read-modify-write on a branch,
which is the exact last-writer-wins problem this ADR exists to remove.

**A database row with `SELECT ... FOR UPDATE`.** Correct, but requires the
platform's database to be reachable from every agent host, and couples
coordination to application infrastructure. It also adds an operational
dependency to a tool that must work before the application is running.

**A dedicated lock service (Redis, etcd, ZooKeeper).** A new runtime dependency
and a new failure mode, for one primitive. Rejected as disproportionate.

**`--force` pushes to break ties.** Destroys the guarantee that a live lease is
never clobbered. `--force-with-lease` provides tie-breaking without that risk.

**Serialising runs in the scheduler only.** Fixes the symptom for scheduled runs
and not for manual, resumed, or overlapping ones, and leaves the silent-loss
failure mode intact for every other writer.

## References

- `tools/nexus_coord/lease.py` — the lease and its compare-and-swap
- `tools/nexus_coord/claims.py` — per-task claims
- `tools/nexus_coord/gitref.py` — the only module that runs git
- `tests/coordination/` — interleaving tests over an in-memory remote
