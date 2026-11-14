# ADR-005: SQLAlchemy repository and Unit of Work strategy

## Status

Accepted.

## Context

The domain must not depend on SQLAlchemy. Persistence needs explicit transaction
boundaries so that a use case either commits fully or rolls back, and
repositories must not commit independently (which would make multi-step
operations non-atomic).

## Decision

Define application-level persistence **ports**: `UserRepository`,
`ExternalIdentityRepository`, `NoteRepository`, `NotePermissionRepository`, and
`UnitOfWork`. Provide SQLAlchemy 2.x adapters.

- **Unit of Work owns the transaction.** It wraps one `Session`, exposes the
  repository adapters, and **never commits on `__exit__`** — the use case calls
  `commit()` explicitly, making the transaction boundary visible in the
  application layer. `__exit__` rolls back if an exception propagated and always
  closes the session.
- **Repositories never commit.** They only stage changes on the shared session.
- **Mapping is explicit.** Adapters translate ORM rows ↔ domain objects via
  small functions (`_note_to_domain`, etc.). ORM models are never returned from
  the API and never imported by the domain or application layers.
- **Aggregate consistency.** `NoteRepository.add/get` operate on the whole
  aggregate (note + grants). `save(note, expected_version=...)` updates only the
  note's scalar fields using an atomic compare-and-set (see ADR-007); permission
  rows are written through `NotePermissionRepository`.

The `UnitOfWork` port is a `Protocol`; `SqlAlchemyUnitOfWork` subclasses it
(`class SqlAlchemyUnitOfWork(UnitOfWork)`) so the composition root can inject it
without a cast while the application layer still depends only on the port.

## Alternatives considered

- **ORM/ActiveRecord entities passed through the app.** Rejected: leaks
  persistence into business rules and the API surface.
- **Repository-managed transactions (commit inside each method).** Rejected:
  breaks atomicity for multi-step use cases and hides transaction scope.
- **A generic `Repository[T]` base with CRUD.** Rejected: it would merely
  duplicate the ORM and add indirection with no substitution value.
- **Session-per-request middleware with implicit commit.** Rejected: makes the
  commit point invisible and encourages accidental partial writes.

## Consequences

- Use cases read clearly: open UoW → do work → `commit()`.
- Rollback on failure is guaranteed by the context manager and covered by tests.
- Swapping the ORM or database is a change to adapters only.

## Tradeoffs

- Explicit mapping code is more verbose than `session.scalar(Model)` returning a
  domain object directly. In exchange, the domain stays free of ORM concepts and
  the API cannot accidentally expose columns.
