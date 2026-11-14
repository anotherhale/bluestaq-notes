# ADR-006: SQLite default with PostgreSQL compatibility

## Status

Accepted.

## Context

The service must run out of the box for a reviewer (`DATABASE_PROVIDER=sqlite`)
and also support PostgreSQL through configuration, using the *same* repository
implementations and domain model. The assignment specifies both.

## Decision

- **SQLite is the default.** `DATABASE_URL=sqlite:///./notes.db`. No server to
  install; a reviewer can run the service immediately.
- **PostgreSQL is a configuration change only.** `DATABASE_PROVIDER=postgresql`,
  `DATABASE_URL=postgresql+psycopg://...`. The same declarative models,
  repositories, and UoW serve both. The only provider-specific code lives in
  `infrastructure/db/engine.py` (connection args) and a small `UTCDateTime`
  column type.
- **Portable column types.** `sqlalchemy.Uuid` maps to native UUID on PostgreSQL
  and `CHAR(32)` on SQLite. Timestamps use a `UTCDateTime` `TypeDecorator` that
  stores naive UTC and returns timezone-aware UTC on both providers (see the
  note below).
- **Alembic is the schema source of truth**, with `render_as_batch=True` for
  SQLite so ALTER-style changes work on a database that lacks them.
- **Foreign keys are enforced on SQLite.** SQLite disables FK enforcement by
  default; a `PRAGMA foreign_keys=ON` listener is registered per connection, so
  `ON DELETE CASCADE` and FK constraints are not silently ignored.

### Note: timestamp consistency across providers

Two provider-specific pitfalls were found and fixed during hardening:

1. SQLite returns **naive** datetimes, so a freshly created (aware) object and
   the same object reloaded from the database serialised differently.
2. PostgreSQL converts an **aware** value to the session's local wall clock on a
   timezone-less `TIMESTAMP` column, shifting stored instants by the server's
   UTC offset.

`UTCDateTime` normalises to naive UTC on write and re-labels UTC on read, which
fixes both. This is covered by a regression test that runs on both providers.

## Alternatives considered

- **A single provider only.** Rejected: the assignment requires both, and the
  portability exercise is part of the evaluation.
- **`DateTime(timezone=True)` (TIMESTAMPTZ) on PostgreSQL.** Would fix the
  offset shift but reintroduce a provider difference (SQLite ignores the flag)
  and would require a migration; the decorator keeps one schema and one code
  path.
- **A separate repository implementation per provider.** Rejected: pointless
  duplication; SQLAlchemy already abstracts the differences.

## Consequences

- Reviewers can run everything on SQLite with no setup; PostgreSQL integration
  tests run with `TEST_POSTGRESQL_URL` set (see README).
- The migration is identical for both providers (`alembic check` reports no
  drift on either).

## Tradeoffs

- The `UTCDateTime` decorator is a small amount of custom type code. It replaces
  a subtle, provider-dependent correctness bug, so the tradeoff favours
  correctness and a single schema.
