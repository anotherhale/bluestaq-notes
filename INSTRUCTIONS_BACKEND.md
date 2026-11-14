# OpenHands Mission: Bluestaq Principal Engineer Take-Home Assignment

## Secure Multi-User Notes REST Service

### 1. Mission and evaluation objective

Build a secure, maintainable, production-minded REST backend for a note-taking service used and shared among several small teams.

The assignment from Bluestaq is:

"You're building the backend for a note-taking service used and shared amongst several small teams. Users need somewhere to capture and work with their notes.

Deliver a runnable REST service and README. We will review your code for clarity, correctness, and tradeoffs you made. You choose the language, framework, schema, endpoints, storage, and what's in scope. Be ready to defend those choices.

Harden the REST service and make it rock!"

The objective is not to build the largest possible application. The objective is to demonstrate excellent engineering judgment, clear architecture, secure implementation, thoughtful tradeoffs, and a working service.

The deliverable is a backend REST service. Do not spend time building a React frontend. The REST API, its behavior, its tests, and its documentation are the primary deliverables.

### 2. Operating mode

Operate autonomously through:

INSPECT → PLAN → DESIGN → IMPLEMENT → TEST → REVIEW → HARDEN → DOCUMENT → DELIVER.

Do not stop after creating architecture documents or scaffolding.

Inspect the existing repository and preserve useful existing work. Do not overwrite or discard existing implementation without understanding it.

Create and maintain:

- OPENHANDS_SPEC.md
- IMPLEMENTATION_PLAN.md
- docs/ARCHITECTURE.md
- docs/DOMAIN_MODEL.md
- docs/SECURITY.md
- docs/API.md
- docs/ADR/

Make routine engineering decisions autonomously. Document significant decisions and their rationale. Ask only when a genuinely blocking decision or potentially destructive operation requires clarification.

Maintain a continuously updated implementation checklist.

### 3. Technology decisions

Use:

- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- SQLite as the default database
- PostgreSQL as an alternative database provider
- pytest
- httpx for API integration tests
- Ruff for formatting and linting
- mypy or an equivalent static type checker where practical

Use maintained dependencies and compatible versions.

Keep the dependency list small. Do not introduce infrastructure that is not needed to demonstrate the assignment.

### 4. Architecture

Use a modular monolith following Domain-Driven Design, Hexagonal Architecture, and Ports and Adapters.

The architecture must have clear dependency direction:

Domain → independent business rules and models.

Application → use cases and ports.

Infrastructure → concrete implementations of ports.

Presentation → FastAPI routes, request/response schemas, and HTTP concerns.

Bootstrap → configuration and dependency composition.

The domain and application layers must not import FastAPI, SQLAlchemy, JWT libraries, HTTP request objects, or database-specific implementation details.

Use a centralized composition root to construct dependencies.

Avoid:

- Global mutable application state.
- Service locator patterns.
- Business logic in API routes.
- Direct ORM model exposure.
- Generic abstractions without a concrete use.
- Excessive inheritance and unnecessary interfaces.
- Microservices or distributed architecture.

Use dependency injection through explicit constructor or function dependencies.

Enforce architectural boundaries with automated checks where practical.

### 5. Domain model

Implement meaningful domain entities, value objects, and invariants.

Identity context:

- User
- UserId
- Principal
- ExternalIdentity
- UserStatus

Notes context:

- Note (aggregate root)
- NoteId
- NoteContent
- NoteVersion
- NotePermission
- PermissionLevel (READ, WRITE)

A note has exactly one owner and is private by default.

A note owner has complete control over the note.

READ permits retrieval only.

WRITE permits retrieval and modification.

Only the owner can delete the note or manage sharing permissions.

Shared users cannot transfer ownership or grant additional permissions.

Enforce invariants in the domain and application layers, not only in HTTP controllers.

Avoid introducing a complicated role and policy hierarchy for this small application. Model the actual requirements directly while retaining an authorization extension point.

### 6. Authentication architecture

Authentication must be independent of the Notes domain.

Define an IdentityProvider port that resolves a request into a Principal.

The Principal should contain the authenticated application identity, including the application UserId and relevant identity metadata.

Implement:

A. DemoIdentityProvider

This is the default provider for local execution and automated testing.

Requirements:

- No login screen or external identity service required.
- Support multiple seeded demo users.
- Support selecting a demo identity using an explicit development-only mechanism, such as X-Demo-User-ID.
- Validate the selected identity against known seeded users.
- Never trust arbitrary user identifiers without resolving them through the provider.
- Reject unknown identities.
- Clearly identify demo authentication in the API documentation.
- Refuse to activate demo identity selection when the environment is production.

The demo mechanism exists to exercise multi-user behavior and demonstrate authorization. It is not production authentication.

B. OIDC extension

Define the boundary needed for a future OIDC provider.

If time permits, implement an OIDC adapter using a maintained JWT/OIDC library with signature, issuer, audience, expiration, and claim validation.

Do not hand-roll JWT cryptography.

Do not build an OAuth authorization server.

If full OIDC implementation is not practical within the assignment, document the port, configuration requirements, and integration design. Do not create a fake adapter that claims to validate tokens when it does not.

### 7. Authorization architecture

Authentication and authorization are separate responsibilities.

Define an AuthorizationProvider port.

Implement a DefaultAuthorizationProvider with explicit authorization decisions.

The application must authorize every protected operation using the Principal resolved by authentication.

Required behavior:

- Owner can read, update, delete, and share.
- READ user can read.
- WRITE user can read and update.
- WRITE user cannot delete.
- Shared users cannot manage permissions.
- Non-owners cannot grant access.
- Revoked permissions immediately stop granting access.
- Unknown or inaccessible resources must not leak information unnecessarily.
- Deny by default.

Never accept owner_id or authenticated user identity from a request body as authoritative.

Never rely on frontend or client-side authorization.

The authorization design should allow future RBAC, ABAC, or an external policy engine without rewriting the Notes use cases.

### 8. Persistence architecture

Use SQLAlchemy 2.x.

Define application-level ports:

- UserRepository
- ExternalIdentityRepository
- NoteRepository
- NotePermissionRepository
- UnitOfWork

Implement SQLAlchemy adapters for the repositories and Unit of Work.

SQLite is the default and must work out of the box.

PostgreSQL must be supported through configuration using the same repository implementations and domain model.

Example SQLite configuration:

DATABASE_PROVIDER=sqlite
DATABASE_URL=sqlite:///./notes.db

Example PostgreSQL configuration:

DATABASE_PROVIDER=postgresql
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/notes

Use Alembic migrations.

Required tables:

users:

- id
- email
- display_name
- status
- created_at
- updated_at

external_identities:

- id
- user_id
- provider
- subject
- created_at

notes:

- id
- owner_id
- title
- content
- version
- created_at
- updated_at

note_permissions:

- id
- note_id
- user_id
- permission
- granted_by
- created_at

Use UUID primary keys, foreign keys, uniqueness constraints, and appropriate indexes.

Required indexes:

- users(email)
- external_identities(provider, subject)
- notes(owner_id, updated_at)
- note_permissions(note_id, user_id)
- note_permissions(user_id, note_id)

Use database constraints to reinforce application invariants.

Use Unit of Work for transaction boundaries.

Repositories must not commit transactions independently.

Ensure rollback works when an operation fails.

Use optimistic concurrency control for note updates. A stale update must never silently overwrite a newer update.

Implement and test SQLite first. Implement PostgreSQL compatibility using the same ports and adapters. Run PostgreSQL integration tests if the environment supports it. Otherwise, explicitly document that PostgreSQL integration testing was not performed.

### 9. REST API design

Use the /api/v1 prefix.

Implement the following endpoints:

Identity:

GET /api/v1/me

Users:

GET /api/v1/users

Notes:

GET /api/v1/notes
POST /api/v1/notes
GET /api/v1/notes/{note_id}
PUT /api/v1/notes/{note_id}
DELETE /api/v1/notes/{note_id}

Permissions:

GET /api/v1/notes/{note_id}/permissions
POST /api/v1/notes/{note_id}/permissions
DELETE /api/v1/notes/{note_id}/permissions/{user_id}

The users endpoint must return only information necessary for sharing, such as user ID, email, and display name.

The notes listing must return both owned notes and notes shared with the current user without duplicates.

Support pagination and reasonable sorting.

Use Pydantic v2 request and response models.

Do not expose SQLAlchemy ORM models directly.

Use consistent error response structures and correct HTTP status codes.

Validate:

- Required fields.
- Title length.
- Content size.
- UUID formats.
- Permission values.
- Pagination limits.
- Invalid or unexpected fields.

Use explicit response models.

Document the API with OpenAPI.

### 10. Concurrency and HTTP semantics

Implement optimistic concurrency control.

Use a note version field or ETag/If-Match mechanism.

A client updating an outdated version must receive an appropriate conflict response, such as HTTP 409.

Do not silently accept last-write-wins behavior.

Use appropriate HTTP semantics:

- 201 for successful creation.
- 200 for successful retrieval and updates.
- 204 for successful deletion where appropriate.
- 400 for malformed or invalid requests.
- 401 for unauthenticated requests.
- 403 or consistent 404 behavior for unauthorized resource access, according to the documented security policy.
- 404 for nonexistent resources.
- 409 for concurrency conflicts.
- 422 for request validation errors where appropriate.
- 429 for rate limiting.

Make error behavior predictable and documented.

### 11. Security hardening

Treat security as a first-class requirement.

Implement:

Authentication:

- Explicit authentication boundary.
- Demo identity restricted to development/testing.
- Clear rejection of invalid identities.
- OIDC validation boundary.

Authorization:

- Centralized authorization.
- Deny-by-default.
- Owner and permission enforcement on every protected operation.
- No client-controlled ownership.

Input handling:

- Pydantic validation.
- Field length limits.
- Request body size limits.
- UUID validation.
- Strict handling of unexpected fields where appropriate.

HTTP security:

- Explicit CORS allowlist.
- Security headers.
- Request correlation IDs.
- Centralized exception handling.
- Configurable rate limiting.

Rate limiting:

- Define a replaceable rate-limiting boundary.
- Implement a lightweight in-memory adapter for the demo.
- Make limits configurable.
- Document that the in-memory implementation is process-local and unsuitable for coordinated enforcement across multiple replicas.

Logging:

- Structured logs.
- Request IDs.
- Appropriate error and audit information.
- Never log passwords, tokens, secrets, or note content.
- Avoid logging sensitive personal information unnecessarily.

Configuration:

- Environment-driven configuration.
- Validate required settings at startup.
- Never commit secrets.
- Provide .env.example.
- Production configuration must reject insecure demo authentication.

Database:

- Parameterized ORM queries.
- Foreign key enforcement.
- Appropriate transaction boundaries.
- No direct SQL interpolation using untrusted input.

Review all security-sensitive code before delivery.

### 12. Automated testing

Testing quality is a major evaluation criterion.

Implement meaningful tests across the following categories.

A. Domain unit tests:

- Note creation invariants.
- Title/content validation.
- Owner behavior.
- Permission behavior.
- Version conflict behavior.

B. Application tests:

- Create note.
- Update note.
- Delete note.
- Share note.
- Revoke sharing.
- Transaction rollback.
- Authorization decisions.

C. Authentication tests:

- Demo identity resolution.
- Unknown demo identity rejection.
- Demo provider disabled in production.
- Missing identity.
- Invalid credentials.
- OIDC validation behavior if implemented.

D. Authorization and security tests:

These are mandatory:

1. User A cannot read User B's private note.
2. User A cannot update User B's private note.
3. User A cannot delete User B's private note.
4. READ permission allows retrieval.
5. READ permission does not allow modification.
6. WRITE permission allows modification.
7. WRITE permission does not allow deletion.
8. WRITE permission does not allow permission management.
9. A non-owner cannot grant permissions.
10. Revoked permissions immediately remove access.
11. Client-supplied owner IDs cannot change note ownership.
12. Guessing a UUID does not bypass authorization.
13. A user cannot share a note they do not own.
14. A user cannot grant access to nonexistent users.
15. Invalid authentication cannot fall back to demo authentication.

E. Persistence tests:

- Repository contract tests.
- Transaction behavior.
- Rollback.
- Foreign key behavior.
- Unique constraints.
- SQLite integration.
- PostgreSQL integration if available.

F. API tests:

- Request validation.
- Pagination.
- Correct status codes.
- Consistent errors.
- Authentication.
- Authorization.
- Concurrency conflicts.
- Rate limiting.

Do not write superficial tests that merely assert HTTP 200.

Tests must verify actual persisted state and security outcomes.

Never weaken, delete, skip, or modify a meaningful test just to obtain a passing build.

### 13. Architecture and code quality

Use:

- Python type annotations.
- Explicit domain and application interfaces.
- Small, focused classes and functions.
- Dependency injection.
- Meaningful domain terminology.
- Clear exception types.
- Consistent formatting.
- Minimal dependencies.

Avoid:

- Overengineering.
- Generic CRUD frameworks.
- Unnecessary abstract base classes.
- Repository abstractions that merely duplicate ORM methods.
- Business logic in controllers.
- ORM leakage into the domain.
- Hidden global dependencies.
- Premature microservices.
- Kafka, Redis, Kubernetes, CQRS, event sourcing, or distributed transactions without an actual requirement.

Use abstractions when they create a meaningful substitution boundary or protect domain logic from infrastructure concerns.

### 14. Documentation

The README is a primary deliverable and must be written for an engineer reviewing and running the project.

It must include:

1. Project overview.
2. Architecture summary.
3. Technology choices and rationale.
4. How to install dependencies.
5. How to configure the application.
6. How to initialize the database.
7. How to run migrations.
8. How to start the service.
9. How to run tests.
10. How to use the REST API.
11. Demo authentication instructions.
12. Example multi-user workflow.
13. Security decisions.
14. Concurrency strategy.
15. SQLite and PostgreSQL configuration.
16. Known limitations.
17. Deliberately deferred capabilities.
18. Production hardening considerations.

Include runnable curl examples demonstrating:

- Resolving the current user.
- Creating a private note.
- Retrieving a note.
- Sharing with another user as READ.
- Switching to the recipient's demo identity.
- Reading the shared note.
- Confirming the recipient cannot modify it.
- Granting WRITE access.
- Successfully updating the note.
- Revoking access.
- Confirming access is denied after revocation.

The README must accurately describe what is implemented. Never claim functionality that does not exist.

### 15. Architecture Decision Records

Create concise ADRs for the most significant decisions:

ADR-001: Modular monolith and Hexagonal Architecture.

ADR-002: Domain model and note ownership/permission rules.

ADR-003: Authentication provider abstraction and development identity.

ADR-004: Authorization provider and deny-by-default policy.

ADR-005: SQLAlchemy repository and Unit of Work strategy.

ADR-006: SQLite default and PostgreSQL compatibility.

ADR-007: Optimistic concurrency strategy.

ADR-008: HTTP error and inaccessible-resource semantics.

Each ADR must state:

- Context.
- Decision.
- Alternatives considered.
- Consequences.
- Tradeoffs.

Do not create ADRs for trivial implementation details.

### 16. Delivery priorities

Prioritize implementation in this order:

P0 — Essential:

- Runnable FastAPI application.
- Clean architecture boundaries.
- SQLite persistence.
- User and note domain model.
- Note CRUD.
- Multi-user identity simulation.
- Ownership enforcement.
- READ/WRITE sharing.
- Critical authorization tests.
- README with working startup instructions.

P1 — High value:

- Unit of Work.
- Optimistic concurrency.
- Alembic migrations.
- PostgreSQL compatibility.
- Repository contract tests.
- Consistent errors.
- Input and request-size limits.
- Rate limiting.
- Security headers.
- Structured logging.

P2 — Additional hardening:

- Full OIDC adapter implementation.
- Advanced observability.
- Additional production deployment guidance.
- Additional static architecture checks.

P0 must be complete before optional P1/P2 work is allowed to consume the remaining time.

If time is limited, implement the extension point and document a deferred capability rather than delivering a misleading or incomplete implementation.

### 17. Final quality gates

Before delivery:

- Run the full backend test suite.
- Run Ruff lint and formatting checks.
- Run static type checking.
- Verify Alembic migrations from a clean database.
- Verify the application starts using README instructions.
- Exercise all primary REST workflows.
- Verify cross-user isolation.
- Verify READ and WRITE permissions.
- Verify revocation.
- Verify stale-update conflicts.
- Verify demo authentication cannot be activated in production mode.
- Inspect all security-sensitive code.
- Inspect dependency direction.
- Confirm no secrets are committed.
- Confirm no critical TODOs or placeholders remain.
- Confirm all claimed functionality has actually been executed and tested.

Do not report a passing test count unless the tests were executed.

Do not hide failures or silently downgrade quality gates.

### 18. Final deliverables

Deliver:

- A fully runnable Python REST service.
- README.md.
- OPENHANDS_SPEC.md.
- IMPLEMENTATION_PLAN.md.
- docs/ARCHITECTURE.md.
- docs/DOMAIN_MODEL.md.
- docs/SECURITY.md.
- docs/API.md.
- docs/ADR/.
- .env.example.
- Alembic migrations.
- Automated tests.

Produce a final engineering report with:

1. Implemented functionality.
2. Architecture and dependency direction.
3. Key design decisions and tradeoffs.
4. Authentication and authorization implementation.
5. Persistence provider support.
6. Security controls.
7. Concurrency behavior.
8. Actual test execution results.
9. Known limitations.
10. Deferred capabilities.
11. Exact local startup and test commands.

The assignment is complete only when the service is runnable, its primary workflows work end-to-end, and the mandatory cross-user authorization tests pass.

The objective is a small, exceptionally well-engineered REST service—not a large application with unnecessary complexity.
