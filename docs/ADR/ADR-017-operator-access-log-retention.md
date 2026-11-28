# ADR-017: The Operator Access Log Is Retained on a Bounded Window, and Erasing It Is Its Own Authority

## Status

Accepted

## Context

NEX-072 made the operator access log durable: every granted `/ops` read appends a row to
`operator_access_log` through `SqlAlchemyOperatorAccessLog`. The log is append-only by design —
it answers "who read this, recently" — and nothing removed a row. Two consequences followed, and
they pull in opposite directions:

- **The table grows without limit.** One row per granted operational read, forever. That is a
  storage problem, but a slow one.
- **Old reads are kept forever.** Every row names a user and an operation. A read performed years
  ago by someone who has since left the organisation is still attributed to them. Retaining
  personal data with no stated limit is a compliance problem, and a sharper one than the storage.

The obvious fix — let an operator delete rows — is the fix that destroys the property the log
exists for. An audit trail an operator can silently shorten is not an audit trail; the first
thing a compromised operator would do is erase the record of what they read. So the question is
not *whether* to delete, but **what makes deletion safe**.

Three authorities meet here and must not be conflated:

1. the **policy** that decides how old a row must be to be deleted,
2. the **authority** to run the purge, and
3. the **record** that a purge happened.

## Decision

### 1. The window is a domain policy, and it is bounded on both sides

`domain/governance/retention.py` → `OperatorAccessRetentionPolicy` holds the window. It is
constructed from deployment configuration (`OPERATOR_ACCESS_RETENTION_DAYS`) and validated at
construction, so a bad value fails the boot rather than the first purge an operator runs.

- **Floor: 90 days.** A shorter window would let one privileged read erase the record of the
  previous one — the exact property the log exists to deny. 90 days is the shortest window that
  keeps a quarter of history, which is the shortest span over which a privileged-read pattern is
  meaningfully reviewable.
- **Ceiling: 3650 days.** `36500` is a plausible typo for `3650` (one extra zero). A deployment
  that believes it has retention while actually pinning every row forever is worse than one with
  no retention at all, because it stops anyone from asking the question. "Keep everything" is
  expressed by **disabling** retention, never by a very long number.

Both bounds are pinned by literal assertions in
`tests/domain/test_operator_access_retention.py`, so lowering the floor or raising the ceiling is
a visible edit to a test, not a quiet configuration change.

### 2. The cutoff comes from the policy, never from the caller

`PurgeOperatorAccessLog` computes `cutoff = policy.cutoff(now=clock.now())` and passes it down.
There is no request field for a cutoff, a user, or an operation. A caller cannot express "delete
everything" or "delete Bob's rows", because there is no place to say it.

The boundary is **inclusive at its near edge**: a row stamped exactly `window` ago is kept. The
same rule decides both what the adapter deletes (`at < cutoff`) and what `covers()` reports, so
the two cannot disagree at the oldest surviving row.

### 3. Erasing is a distinct authority from reading

The purge is gated on `access-log-retention`, which is **not** `operator-access-report`. Reading
who read what and erasing who read what are different authorities: a deployment can grant the
read widely (to auditors, to every operator) and the erase narrowly (to one compliance role). The
use-case tests include the case that matters — a caller holding only `operator-access-report` is
refused — so collapsing the two scopes is a test failure, not a policy drift.

A refused caller gets `ResourceNotFound` (404), not 403, consistent with ADR-008: the endpoint
must stay indistinguishable from a path that does not exist.

### 4. The purge records itself, before it deletes

`OperatorGate.authorize()` writes the granted `access-log-retention` call to the log, stamped
`now` — inside the window the purge is about to prune. The record of the erasure therefore
survives the erasure, by construction and not by ordering luck. An audit trail that deleted its
own erasures would be worse than no trail.

### 5. Retention is off by default

`OPERATOR_ACCESS_RETENTION_DAYS` unset means "keep everything", and the endpoint then answers
404 rather than offering a purge with no mandate. Retention is a compliance decision with a
named window; it is never switched on silently by a default.

## Consequences

- **A purge is bounded, attributed, and self-recording.** It cannot be shortened below a quarter,
  cannot be pointed at a person, and cannot erase the fact that it ran.
- **The default deployment keeps every row.** That is the safe default, and it leaves the storage
  growth of NEX-072 in place until an operator configures a window. This ADR does not choose a
  window for anyone; it only bounds what may be chosen.
- **`purge_before` is the one mutation the port permits.** The in-memory adapter returns `0` and
  says why: its ring already self-bounds, so it holds none of the debt a window exists to clear.
  A future append-only/exporter adapter must implement the same method and must not become
  writable in any other way.
- **No scheduled purge exists.** The endpoint is operator-invoked. A cron or worker that purges
  unattended is a separate decision — it changes who is accountable for the erasure — and is not
  made here.
- **The window is a policy, not a legal opinion.** 90 days and 3650 days are engineering bounds.
  A deployment with a statutory retention obligation must choose a window inside them that
  satisfies it, or this ADR needs revisiting.
