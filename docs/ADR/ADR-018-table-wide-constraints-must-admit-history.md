# ADR-018: A table-wide constraint must admit the history it will be evaluated against

## Status

Accepted.

## Context

Bluestaq Notes records a governance policy as an append-only sequence of
`policy_versions` rows, and it enforces separation of duties in that table: the
author, approver and publisher of a version in force must be pairwise distinct.
The rule is enforced in three places, deliberately — the domain
(`separation_of_duties`, `approval_of_duties`), the use cases, and a DB CHECK
constraint — because a rule enforced at only some layers is one an operator walks
around by choosing another entry point.

That layering is the problem this ADR records. A CHECK constraint is *table-wide*:
it has no notion of "new rows only". Alembic's `batch_alter_table` on SQLite
rebuilds the table and re-evaluates every predicate against every existing row;
PostgreSQL revalidates the table. So the moment a constraint is added or replaced,
it runs against history that was written under a laxer rule.

Two provenance columns illustrate the drift:

| Revision | Change | Columns a row can have |
|---|---|---|
| `4b1e7a9c3f28` | creates `policy_versions` | `published_by` only |
| `9c4d1e8a7b23` | adds the author | `drafted_by` (nullable) |
| `c4e7a1b93d05` | adds the approval | `approved_at`, `approved_by` (both nullable) |
| `5d2b7c41e9a8` | three-way distinctness | — |

A policy published at `4b1e7a9c3f28` therefore has `drafted_by = NULL` and
`approved_by = NULL` for the rest of its life. It cannot be backfilled: there is
no author to record, and inventing one (the publisher, a placeholder) would
manufacture an audit trail that never existed. That is precisely the control the
column exists to provide.

## Decision

**A CHECK constraint on `policy_versions` expresses only comparisons, never
presence.** The SoD predicate is:

```sql
status = 'draft'
OR (status = 'approved' AND drafted_by <> approved_by)
OR (status IN ('published','retired')
    AND drafted_by <> published_by
    AND drafted_by <> approved_by
    AND approved_by <> published_by)
```

There is deliberately no `drafted_by IS NOT NULL` and no `approved_by IS NOT
NULL` guard. The reason is SQL three-valued logic:

- `NULL <> 'publisher-1'` evaluates to `NULL`, and a CHECK treats `NULL` as
  **satisfied** — a legacy row passes.
- `NULL IS NOT NULL` evaluates to `FALSE` — a legacy row is **violated**, the
  table rebuild aborts, and because this happens *during* the revision that adds
  the guard, `upgrade head` cannot proceed at all. A later revision cannot repair
  it, because the chain stops before the repair is reached.

Presence is enforced where history cannot reach: the domain (`separation_of_duties`
refuses an unspecified author; `approval_of_duties` refuses an unspecified
approver) and the `Publish`/`Approve` use cases, which stamp both from the
authenticated principal. No application path can publish a version without them,
so the constraint does not need to carry the presence rule — and *cannot* carry
it without also rejecting the rows it must preserve.

## Consequences

- A deployment holding a policy published before authors were recorded can reach
  head. That is the whole point: the alternative is a database that cannot be
  upgraded and a policy that cannot be published.
- A legacy in-force row is grandfathered indefinitely. It stays readable, stays
  in force, and still decides — a policy that silently stopped enforcing would be
  a worse outcome than one whose author is unknown. Re-publishing it under current
  rules requires re-drafting it with a named author, which is the correct
  remediation and is documented rather than automated.
- Every pairwise violation among *recorded* principals is still refused at the DB
  layer, so a caller that bypasses the domain cannot collapse two roles.
- The constraint cannot tell a grandfathered row from a malformed new one. That is
  an accepted limitation, not an oversight: no table-wide predicate can, and
  trying to force it with a presence guard is what broke the upgrade.

## Enforcement of this decision

`tests/persistence/test_migrations.py` pins it in both directions, and both
regression classes are load-bearing (each fails when the corresponding source
change is reverted):

- `TestLegacyPolicyWithoutAnyAuthorshipUpgrades` — builds the database at
  `4b1e7a9c3f28`, inserts a published row in that schema's *actual* shape (only
  the columns that revision had), then upgrades to head. It asserts the upgrade
  completes, the NULL provenance is preserved without a backfill, the row is
  readable through the shipped adapter, the row still decides, the upgrade is
  repeatable, and downgrade/re-upgrade round-trips. These tests fail with 7
  distinct errors if a presence guard is reintroduced at any of the three
  revisions.
- `TestModernPoliciesStillEnforceSeparationOfDuties` — asserts that a newly
  published row naming its own author, an approver that publishes, and an author
  that approves its own draft are each still refused, that three distinct
  principals are accepted, and that the full draft → approve → publish lifecycle
  runs against a migrated database.

Two traps that let these defects ship, recorded so they are not repeated:

1. **`Base.metadata.create_all` is not the migrated schema.** The pytest fixtures
   build their schema from the ORM model, so a constraint that the migrations get
   wrong is invisible to every fixture-based test. A migration test must run
   `alembic upgrade`, and must seed data at the revision that *predates* the
   constraint under test — seeding the current schema with NULLs tests nothing.
2. **`alembic check` does not diff CHECK constraint expressions.** It compares
   tables, columns, indexes and some constraint classes. A CHECK whose text
   disagrees with the model reports "No new upgrade operations detected". Verify
   constraint text against the model by hand, or by asserting on
   `sqlite_master`/`pg_constraint` in a test.

## Related

- ADR-007 (optimistic concurrency), ADR-008 (error semantics) — the same
  principle that a persistence-layer rule must leave the domain's invariants
  authoritative.
- `docs/SECURITY.md`, `docs/NEXUS/STATE.md` (NEX-078 correction blocks).
