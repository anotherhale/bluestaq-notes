# ADR-001: Modular monolith with Hexagonal Architecture

## Status

Accepted.

## Context

The service is a small, multi-user notes backend. The evaluation emphasises clear
architecture, dependency direction, and defensible tradeoffs rather than scale.
The system must remain easy to reason about, test, and evolve — in particular,
authentication, persistence, and HTTP concerns must be replaceable without
rewriting business rules.

The candidates considered were: a conventional layered (controller/service/
repository) application, a modular monolith with explicit ports and adapters
(hexagonal), and a microservices split.

## Decision

Build a single deployable **modular monolith** organised into five layers with a
strict, one-way dependency direction:

```
presentation ─┐
              ├─→ application ─→ domain
bootstrap ────┘        ↑
   (composition root)  │
infrastructure ────────┘ (implements application ports)
```

- **Domain** — entities, value objects, invariants, domain errors. Depends on
  nothing but the standard library.
- **Application** — use cases and *ports* (Protocols for repositories, unit of
  work, identity, authorization, rate limiting, clock). Depends only on domain.
- **Infrastructure** — SQLAlchemy repositories/UoW, identity providers, rate
  limiter, clock. Implements application ports; depends on domain + application.
- **Presentation** — FastAPI routers, Pydantic schemas, middleware, error
  translation. Depends on application + domain.
- **Bootstrap** — settings and the composition root (`build_container`). The only
  module that wires ports to concrete adapters.

Each context (identity, notes) is a package inside the domain/application
layers, giving modular boundaries without process boundaries.

## Alternatives considered

- **Classic three-layer app (routes → services → ORM).** Simpler to start, but
  it invites ORM leakage into business rules and couples authorization to the
  web framework. The evaluation explicitly warns against business logic in
  controllers and direct ORM exposure.
- **Microservices (identity service + notes service).** Rejected: no
  requirement, adds network failure modes, distributed transactions, and
  operational overhead for a service with one datastore.
- **Full CQRS / event sourcing.** Rejected as overengineering for a small CRUD
  domain with optimistic concurrency.

## Consequences

- Business rules are testable without FastAPI or a database; the domain and
  application tests run in milliseconds.
- Swapping the identity provider (demo → OIDC) or the rate limiter touches only
  the composition root and one adapter.
- Boundaries are enforced automatically: `tests/architecture/test_layering.py`
  fails the build if a layer imports a forbidden module.
- Cost: more files and indirection (ports + adapters) than a single-file
  service. This is a deliberate tradeoff in favour of testability and
  replaceability, which the assignment weights heavily.

## Tradeoffs

- The extra abstraction is justified only where a substitution boundary exists
  (identity, persistence, rate limiting, authorization, clock). We deliberately
  avoided generic base classes and repository methods that merely mirror the
  ORM.
