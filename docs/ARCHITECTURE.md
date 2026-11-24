# Architecture

This document describes the structure of the service, the dependency direction,
and where each responsibility lives. It is the companion to the ADRs in
`docs/ADR/`.

## Style: modular monolith, hexagonal, ports & adapters

A single deployable process organised into five layers with a strict, one-way
dependency direction. The domain and application layers are pure Python; the
framework, database, and transport live at the edges.

```
                ┌───────────────────────────────────────────────┐
                │                   bootstrap                    │
                │        (settings + composition root)           │
                └───────────────┬───────────────────────────────┘
                                │ wires concrete adapters to ports
        ┌───────────────────────┼───────────────────────────────┐
        │                       │                               │
┌───────▼────────┐   ┌──────────▼─────────┐        ┌────────────▼─────────┐
│  presentation  │   │   infrastructure   │        │   (adapters live     │
│  FastAPI routes│   │  SQLAlchemy, OIDC, │        │    in infrastructure)│
│  schemas, MW   │   │  demo auth, etc.   │        └──────────────────────┘
└───────┬────────┘   └──────────┬─────────┘
        │ depends on            │ implements
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────────┐
        │        application        │   use cases + ports (Protocols)
        │  CreateNote, ShareNote... │
        └───────────┬───────────────┘
                    │ depends on
                    ▼
        ┌───────────────────────────┐
        │          domain           │   entities, value objects, invariants
        │  Note, User, Principal... │
        └───────────────────────────┘
```

Dependency rule (enforced by `tests/architecture/test_layering.py`):

- **domain** imports nothing from the project except itself, and no framework
  (no FastAPI, SQLAlchemy, Pydantic, JWT, …).
- **application** imports domain only. It defines ports as `Protocol`s.
- **infrastructure** implements application ports using SQLAlchemy, PyJWT, etc.
- **presentation** adapts HTTP to application use cases.
- **bootstrap** is the only module allowed to import both ports and adapters.

## Package layout

```
src/notes_service/
├── domain/                     # pure business rules
│   ├── errors.py               # DomainError, ValidationError, InvariantViolation, VersionConflict
│   ├── identity/
│   │   ├── value_objects.py     # UserId, Email, UserStatus, ExternalIdentityId, ProviderSubject
│   │   ├── entities.py          # User, ExternalIdentity
│   │   └── principal.py         # Principal
│   └── notes/
│       ├── value_objects.py     # NoteId, NoteTitle, NoteContent, NoteVersion, PermissionLevel
│       ├── permission.py        # NotePermission, NotePermissionId
│       └── note.py              # Note aggregate root
│
├── application/                # use cases + ports
│   ├── errors.py                # AuthenticationError, AccessDenied, ResourceNotFound, Conflict, InvalidInput, RateLimited
│   ├── ports/
│   │   ├── repositories.py      # UserRepository, ExternalIdentityRepository, NoteRepository,
│   │   │                        #   NotePermissionRepository, UnitOfWork
│   │   ├── identity.py          # IdentityProvider, AuthenticationContext
│   │   ├── authorization.py     # AuthorizationProvider, Action, Decision
│   │   ├── rate_limit.py        # RateLimiter, RateLimitDecision
│   │   └── clock.py             # Clock
│   └── use_cases/
│       ├── base.py              # UseCase base + _load_note_for_read
│       ├── identity.py          # GetCurrentUser, ListUsers
│       ├── notes.py             # CreateNote, GetNote, ListNotes, UpdateNote, DeleteNote
│       ├── permissions.py       # ShareNote, RevokePermission, ListPermissions
│       └── authorization_policy.py  # DefaultAuthorizationProvider
│
├── infrastructure/             # adapters
│   ├── db/
│   │   ├── base.py              # DeclarativeBase, UTCDateTime, UUIDType, naming convention
│   │   ├── models.py            # UserRow, ExternalIdentityRow, NoteRow, NotePermissionRow
│   │   ├── repositories.py      # SQLAlchemy repository adapters
│   │   ├── uow.py               # SqlAlchemyUnitOfWork
│   │   ├── engine.py            # engine/session factory (SQLite + PostgreSQL)
│   │   └── seed.py              # deterministic demo users
│   ├── identity/
│   │   ├── demo_provider.py     # DemoIdentityProvider (X-Demo-User-ID)
│   │   ├── oidc_provider.py     # OidcIdentityProvider (JWT/JWKS)
│   │   ├── routing_provider.py  # RoutingIdentityProvider (bearer vs demo)
│   │   └── context.py           # HeaderAuthenticationContext
│   └── security/
│       ├── rate_limiter.py      # InMemoryRateLimiter
│       └── clock.py             # SystemClock
│
├── presentation/               # HTTP edge
│   ├── api/__init__.py          # /api/v1 router aggregation
│   ├── api/routers/             # identity, users, notes, permissions
│   ├── schemas.py               # Pydantic v2 request/response models
│   ├── dependencies.py          # ContainerDep, PrincipalDep, RateLimitDep
│   ├── middleware.py            # request id, body-size limit, security headers
│   └── errors.py                # centralized exception handlers
│
├── bootstrap/                  # composition
│   ├── config.py                # Settings (validated at startup)
│   ├── container.py             # build_container — the composition root
│   ├── app.py                   # create_app factory
│   └── logging_config.py        # structured logging
│
└── cli.py                      # serve / init-db / seed
```

## Request lifecycle

1. **Middleware (outermost first)** — `RequestContextMiddleware` assigns a
   correlation id (honouring a valid inbound `X-Request-ID`) and emits an access
   log line; `SecurityHeadersMiddleware` adds baseline headers; the optional CORS
   middleware applies the configured allowlist; `BodySizeLimitMiddleware` rejects
   over-sized bodies with `413`.
2. **Authentication** — the `get_principal` dependency builds a
   `HeaderAuthenticationContext` from the request and calls the configured
   `IdentityProvider`. Failure raises `AuthenticationError → 401`. The resolved
   principal id is stashed on `request.state` for logging only.
3. **Rate limiting** — `enforce_rate_limit` keys off the authenticated principal
   id, so one user cannot exhaust another's budget.
4. **Route handler** — validates the request with Pydantic, obtains a fresh use
   case from the container, and calls `run(principal, ...)`. No business logic
   lives in the route.
5. **Use case** — opens a Unit of Work, loads the aggregate, asks the
   `AuthorizationProvider`, applies the change, and commits.
6. **Error translation** — application/domain exceptions are mapped to the
   single error envelope by centralized handlers; anything unhandled becomes a
   `500` with a logged traceback and no internal detail in the response.

## Composition root

`bootstrap/container.py::build_container(settings, engine=None)` constructs every
concrete dependency exactly once and returns an immutable `Container` stored on
`app.state`. Use cases are exposed as zero-argument factories
(`container.create_note()`) that build a fresh use case with its own Unit of
Work while sharing the process-wide engine, providers, and rate limiter. There
is no service locator and no global mutable state.

## Testing the architecture

`tests/architecture/test_layering.py` parses every module with `ast` and asserts:

- domain and application import no framework modules;
- domain imports no Pydantic;
- presentation never imports ORM models directly;
- only bootstrap may import infrastructure.

These checks fail the build if the boundaries erode.

## Known architectural limitations

- The rate limiter is process-local (see `docs/SECURITY.md`).
- `Readiness` does not perform a database round-trip; it is intentionally cheap.
  A deeper check can be added behind the same endpoint.
- There is no event/audit bus; `granted_by`/`created_at` on permissions are the
  only audit trail.


## Enterprise authorization target

The existing `AuthorizationProvider` remains the application-layer policy port.
The target enterprise model (proposed in ADR-011) evolves it to support structured
context and decisions with capability resolution, scoped roles, ABAC, resource
grants, delegation, and obligations. Policy Decision Point, Policy Enforcement
Point, Policy Information Point, and Policy Administration Point are logical
components within the modular monolith initially. See
[Authorization Architecture](AUTHORIZATION_ARCHITECTURE.md) and
[Authorization Policy Model](AUTHORIZATION_POLICY_MODEL.md). This target design
must not be mistaken for functionality already implemented.
