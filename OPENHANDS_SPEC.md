# OPENHANDS_SPEC.md — Bluestaq Notes Service

This is the working specification for the deliverable: what was built, why, and
the constraints it satisfies. It is the contract the implementation is measured
against; `IMPLEMENTATION_PLAN.md` tracks how it was delivered, and the
`docs/` directory holds the detailed architecture, security, domain, and API
references.

## 1. Objective

A runnable, secure, maintainable REST backend for a multi-user note-taking
service used across several small teams. The deliverable is the backend, its
tests, and its documentation. Correctness, clarity, security, and defensible
tradeoffs matter more than feature breadth.

## 2. Scope

**In scope**

- Identity: resolve the authenticated caller; list users available for sharing.
- Notes: create, read (one and many), update, delete.
- Sharing: per-note READ/WRITE permissions with owner-only management.
- Multi-user identity simulation for local runs and tests.
- SQLite (default) and PostgreSQL (alternative) persistence via one model.
- Optimistic concurrency, structured errors, rate limiting, security headers,
  structured logging.
- Automated tests across domain, application, authentication, authorization,
  persistence, and API layers.

**Out of scope**

- A frontend (explicitly excluded by the assignment).
- A full OAuth/OIDC authorization server (only the relying-party side).
- Infrastructure not required to demonstrate the assignment (no Kafka, Redis,
  Kubernetes, CQRS, event sourcing, microservices).

## 3. Technology

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.x · Alembic · SQLite
(default) / PostgreSQL (alternative, psycopg 3) · pytest · httpx · Ruff ·
mypy (strict). Dependencies are kept deliberately small.

## 4. Architecture

Modular monolith, hexagonal (ports & adapters), DDD-aligned. Five layers with a
one-way dependency direction — `presentation`/`bootstrap` → `application` →
`domain`, with `infrastructure` implementing application ports. The domain and
application layers import no FastAPI, SQLAlchemy, JWT, or HTTP types. A single
composition root (`bootstrap/container.py`) wires everything. Boundaries are
enforced by `tests/architecture/test_layering.py`. See `docs/ARCHITECTURE.md`
and ADR-001.

## 5. Domain

Two contexts: **identity** (`User`, `UserId`, `Principal`, `ExternalIdentity`,
`UserStatus`, `Email`) and **notes** (`Note` aggregate root, `NoteId`,
`NoteTitle`, `NoteContent`, `NoteVersion`, `NotePermission`, `PermissionLevel`).
Invariants: one owner, private by default, owner has full control, READ reads,
WRITE reads+writes, owner-only delete and sharing, no self-grants, no ownership
transfer by collaborators. Enforced in the domain, not only in controllers.
See `docs/DOMAIN_MODEL.md` and ADR-002.

## 6. Authentication

An `IdentityProvider` port resolves a request into a `Principal`. Three
adapters: `DemoIdentityProvider` (default for dev/test, `X-Demo-User-ID`
validated against seeded users, refused in production), `OidcIdentityProvider`
(bearer JWT validated for signature/issuer/audience/expiry/claims via
PyJWT+JWKS, `sub` mapped to a local user, no auto-provisioning), and
`RoutingIdentityProvider` (bearer failures never fall back to demo). See
`docs/SECURITY.md` and ADR-003.

## 7. Authorization

An `AuthorizationProvider` port with `DefaultAuthorizationProvider`: owner can
read/update/delete/manage; READ reads; WRITE reads+updates; WRITE cannot delete
or manage; deny by default. Ownership comes only from the `Principal`. See
ADR-004.

## 8. Persistence

Application ports: `UserRepository`, `ExternalIdentityRepository`,
`NoteRepository`, `NotePermissionRepository`, `UnitOfWork`. SQLAlchemy 2.x
adapters; the UoW owns transaction boundaries and never commits on exit;
repositories never commit. UUID primary keys, FKs, uniqueness constraints, and
the required indexes. Alembic migrations. SQLite and PostgreSQL share one model
and one migration. See ADR-005 and ADR-006.

## 9. REST API

`/api/v1` prefix. Endpoints: `GET /me`; `GET /users`; `GET|POST /notes`;
`GET|PUT|DELETE /notes/{note_id}`; `GET|POST /notes/{note_id}/permissions`;
`DELETE /notes/{note_id}/permissions/{user_id}`. Pydantic v2 schemas, explicit
response models, pagination, sorting, consistent error envelope. See
`docs/API.md`.

## 10. Concurrency & HTTP semantics

Optimistic concurrency via a monotonic `version` field checked by an atomic
`UPDATE ... WHERE version = :expected`; stale writes → `409`. Status codes:
201/200/204/400/401/403/404/409/413/422/429/500 as documented in ADR-008. See
ADR-007.

## 11. Security

Explicit authentication boundary; demo auth restricted to dev/test; centralized
deny-by-default authorization; Pydantic validation with field and body-size
limits; explicit CORS allowlist; security headers; correlation ids; centralized
exception handling; configurable rate limiting (in-memory adapter, documented as
process-local); structured logging that never records secrets or note content;
environment-driven config validated at startup that refuses demo auth in
production. See `docs/SECURITY.md`.

## 12. Testing

Domain, application, authentication, authorization/security (the 15 mandatory
scenarios), persistence (including PostgreSQL when available), and API tests.
Tests assert persisted state and security outcomes, not just status codes.
Executed with pytest; see the README for commands.

## 13. Quality gates

`pytest`, `ruff check`, `ruff format --check`, `mypy` (strict), `alembic
upgrade head` from a clean database, `alembic check` for drift, and a live
end-to-end multi-user workflow on both providers. All gates pass; results are
reported in the README and final report.

## 14. Documentation

README (primary deliverable), this spec, the implementation plan,
`docs/ARCHITECTURE.md`, `docs/DOMAIN_MODEL.md`, `docs/SECURITY.md`,
`docs/API.md`, and `docs/ADR/ADR-001…008`.

## 15. Non-goals / deferred

Full live-IdP OIDC handshake; distributed rate limiting; audit-log persistence;
deeper readiness checks; auto-provisioning of OIDC users. Each is documented
rather than faked.
