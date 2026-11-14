# Bluestaq Notes API — Design Notes

## 1. Overview

The objective was to design and deliver a secure, multi-user Notes REST API supporting private notes, shared notes, and differentiated access permissions.

Rather than immediately asking an AI coding agent to generate an application, I treated the assignment as an architecture and requirements exercise first. The initial effort focused on understanding the problem, identifying the domain and security requirements, establishing architectural boundaries, and making deliberate technology and scope decisions.

My approach was to use AI as a design and implementation accelerator while retaining responsibility for the architecture, engineering decisions, and quality of the resulting system.

The implementation was planned as a backend-first application, with a lightweight frontend added as a real client to exercise and demonstrate the backend's behavior.

## 2. Initial Problem Evaluation and Requirements

I spent approximately the first 30 minutes evaluating the assignment and directing AI to produce an implementation plan.

The goal during this period was to move beyond the surface-level requirement of building CRUD endpoints and identify the underlying engineering concerns.

The primary questions were:

- What are the core domain concepts?
- How do users own and share notes?
- What are the rules governing access to private and shared notes?
- How should authentication and authorization be represented?
- How can the security model remain independent of the identity provider?
- What persistence boundaries are necessary?
- How should concurrent modifications be handled?
- How can the implementation remain testable and extensible without introducing unnecessary complexity?
- What is the smallest complete solution that demonstrates sound engineering judgment?

### Functional requirements identified

1. Support multiple users.
2. Allow users to create, retrieve, update, and delete their own notes.
3. Keep notes private by default.
4. Allow note owners to share notes with other users.
5. Support READ and WRITE permissions.
6. Allow owners to review and revoke permissions.
7. Enforce ownership and permissions on every relevant operation.
8. Prevent unauthorized access, including attempts to access resources by guessing identifiers.
9. Handle concurrent updates without silently overwriting another user's changes.

### Nonfunctional requirements identified

- Security must be designed into the architecture from the beginning.
- Domain logic must remain independent of the web framework and persistence technology.
- Authentication and authorization must be independently replaceable.
- Persistence must be isolated behind repository and unit-of-work boundaries.
- The API must be independently consumable by clients.
- The implementation must be testable at domain, application, infrastructure, and API levels.
- The solution must be straightforward to run, understand, and evaluate.
- Documentation must explain the architecture and the reasoning behind important decisions.

## 3. Security-First Engineering

Authentication and authorization were not treated as features to add after the application was working. They were first-class architectural concerns from the initial requirements analysis.

I have extensive experience working with authentication and enterprise SSO, including centralized identity, CAC/PKI, LDAP-based access controls, and secure deployments in federal environments.

My work on the Secure Headers GitHub project also reflects an engineering philosophy in which security controls are considered early rather than retrofitted after application functionality has been implemented.

That experience influenced several decisions.

### Authentication and authorization are separate concerns

Authentication establishes who is making a request. Authorization determines what that authenticated identity is allowed to do.

These responsibilities were deliberately separated through independent architectural boundaries.

- An Identity Provider resolves the request into an authenticated principal.
- An Authorization Provider evaluates access to a particular resource and operation.
- Application use cases enforce authorization before performing protected operations.
- Infrastructure adapters provide the concrete implementations.

This prevents identity-provider-specific behavior from leaking into domain logic or application use cases.

### Demo authentication is an adapter, not an architectural shortcut

The assignment does not require a full login experience. However, a multi-user application still needs a way to establish different identities during development and testing.

The design therefore uses a development-only demo identity provider with seeded users.

The demo identity mechanism is explicitly constrained to development and testing. It does not represent production authentication and must not become an implicit fallback when real authentication fails.

The architecture preserves a clear extension point for an enterprise identity provider, such as OIDC, without requiring the application domain to understand the authentication protocol.

### Authorization is enforced by the backend

The backend remains authoritative for ownership and permissions.

The design establishes explicit rules:

- Owners have full control over their notes.
- READ permission allows retrieval but not modification.
- WRITE permission allows retrieval and modification but not deletion or permission management.
- Only owners can grant, change, or revoke access.
- Notes are private unless explicitly shared.
- Revoked permissions must immediately prevent subsequent access.

The frontend can improve usability by hiding or disabling unavailable actions, but it cannot be trusted to enforce access control.

## 4. Architecture and Technology Decisions

I translated the requirements into a modular monolith using Domain-Driven Design (DDD), Hexagonal Architecture, and Ports and Adapters.

The intent was to establish meaningful boundaries without introducing distributed-system complexity that the assignment did not justify.

### Domain-Driven Design

The initial domain model identifies the core concepts:

- Identity: User, UserId, Principal, ExternalIdentity, and UserStatus.
- Notes: Note, NoteId, NoteContent, and NoteVersion.
- Sharing: NotePermission and PermissionLevel.

The Note aggregate owns its content and version information. Sharing permissions are modeled explicitly rather than represented as arbitrary user identifiers embedded in the note.

This keeps ownership, note behavior, and access relationships understandable and testable.

### Hexagonal Architecture

The application is separated into four primary areas:

**Domain**

Contains the core domain concepts, invariants, and business rules. It does not depend on FastAPI, SQLAlchemy, or external infrastructure.

**Application**

Contains use cases and the ports required to perform application operations. It coordinates domain behavior, authorization, repositories, and transactions.

**Infrastructure**

Contains concrete adapters for persistence, authentication, authorization, and other technical concerns.

**Presentation**

Contains the REST API, request and response schemas, dependency wiring, and HTTP-specific behavior.

The dependency direction is intentional: infrastructure and presentation depend on the application and domain, not the reverse.

### Technology selection

The backend technology choices were made to support rapid delivery while preserving a clean architecture.

- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- SQLite for the initial runnable implementation
- PostgreSQL compatibility
- pytest and HTTPX for automated testing
- Ruff for linting and formatting

FastAPI provides a straightforward REST interface and OpenAPI documentation. Pydantic provides explicit request and response contracts. SQLAlchemy and Alembic provide persistence and schema evolution without coupling the domain to the database.

### Modular monolith rather than microservices

The assignment describes a relatively small application with a cohesive domain.

A modular monolith provides clear architectural boundaries, straightforward deployment, and transactional consistency without the operational overhead of distributed services.

Microservices, Kafka, Kubernetes, Redis, CQRS, and event sourcing were intentionally excluded because there was no demonstrated requirement for them.

The architecture should make future evolution possible without prematurely implementing it.

### Persistence and transaction boundaries

Repository interfaces isolate application behavior from SQLAlchemy and database-specific implementation details.

A Unit of Work coordinates transactions so that a use case can perform related changes consistently.

SQLite provides a simple local environment, while the persistence boundary and database configuration preserve a path to PostgreSQL.

The design avoids making database objects or ORM models part of the domain contract.

### Concurrency

A multi-user note application must account for the possibility that two users retrieve and modify the same note.

Optimistic concurrency was selected to prevent silent lost updates.

A version field or equivalent conditional update mechanism allows the backend to reject stale modifications with a conflict response rather than overwriting newer data.

The frontend can then preserve the user's unsaved changes and allow them to reload the latest version.

## 5. API and Client Design

The REST API was designed as an independent application boundary rather than as an interface tailored to a particular frontend.

The API exposes the operations required to:

- Resolve the current user.
- Retrieve available demo users when demo mode is enabled.
- List owned and shared notes.
- Create, retrieve, update, and delete notes.
- Grant and inspect permissions.
- Revoke access.

The API uses explicit request and response schemas, consistent error handling, validation, pagination where appropriate, and documented HTTP behavior.

A frontend was included as a deliberate architectural verification tool.

The frontend is not intended to contain domain logic or duplicate backend authorization. Its purpose is to exercise the API as an independent client and make the behavior visible during evaluation.

The frontend uses a typed API client, a centralized application state hook, and presentation-oriented components. Components do not issue HTTP requests or know backend endpoint URLs.

This separation helps demonstrate that the backend can support a real client without exposing its internal implementation.

## 6. AI-Assisted Planning and Implementation

After translating the assignment into requirements and constraints, I used ChatGPT to create a structured architecture and implementation plan.

The plan included:

- Domain model and aggregate boundaries.
- Hexagonal architecture and dependency direction.
- Authentication and authorization ports.
- Persistence repositories and Unit of Work.
- REST API contracts.
- Database schema and migration strategy.
- Security requirements.
- Concurrency handling.
- Automated testing strategy.
- Documentation requirements.
- Implementation phases and acceptance criteria.

I did not immediately hand the generated plan to an autonomous coding agent.

I reviewed the proposed architecture and implementation sequence, evaluated the tradeoffs, and adjusted the plan to ensure that it reflected the requirements and the intended engineering principles.

In particular, I wanted to ensure that:

- Security was part of the initial architecture.
- Authentication and authorization were independently replaceable.
- The domain remained framework-independent.
- The application did not become unnecessarily abstract.
- The implementation remained achievable within the assignment's time constraints.
- Critical authorization scenarios had explicit acceptance criteria.
- The final result could be demonstrated through an actual client.

Only after reviewing and refining the plan did I provide the implementation instructions to OpenHands.

### Human-directed AI workflow

The workflow was deliberately divided into distinct responsibilities:

1. **Problem evaluation:** I interpreted the assignment and identified the real requirements.
2. **Requirements definition:** I translated those findings into functional, security, and architectural constraints.
3. **Architecture planning:** ChatGPT helped develop a structured design and implementation plan.
4. **Engineering review:** I reviewed and adjusted the plan before implementation.
5. **Autonomous implementation:** OpenHands was given the approved instructions to implement the system.
6. **Verification:** Automated tests and a real frontend client were used to exercise the implementation.
7. **Final review:** The resulting code, architecture, documentation, and test results were evaluated against the original requirements.

AI accelerated the planning and implementation work, but the architecture, constraints, tradeoffs, and acceptance criteria remained deliberate engineering decisions.

## 7. Frontend as an Architectural Verification Tool

I decided that a frontend would provide a stronger demonstration than testing the REST API exclusively through isolated requests.

A real client exercises the API in the same way an external consumer would. It also exposes weaknesses in API design, authentication boundaries, error handling, and concurrency behavior.

The frontend provides a practical demonstration of the most important authorization scenarios:

1. User A creates a private note.
2. User B cannot see or retrieve that note.
3. User A shares the note with User B using READ permission.
4. User B can retrieve the note but cannot modify it.
5. User A changes the permission to WRITE.
6. User B can now modify the note but cannot delete it or manage its permissions.
7. User A revokes access.
8. User B immediately loses access.

The frontend also exercises stale-update conflicts, validation, loading states, error responses, and user switching.

This is more than a UI demonstration. It is an integration-level test of the backend's domain rules and security model.

## 8. Deliberate Scope and Tradeoffs

The assignment has a limited delivery window. I chose to prioritize a complete, defensible foundation over a larger collection of partially implemented features.

Included in the initial scope:

- Multi-user identity.
- Private note ownership.
- READ and WRITE sharing.
- Authorization enforcement.
- Note CRUD operations.
- Persistence abstraction.
- Transaction management.
- Optimistic concurrency.
- Security controls.
- Automated tests.
- OpenAPI documentation.
- Runnable frontend integration.

Intentionally deferred:

- Full enterprise OIDC deployment.
- Complex organizational role hierarchies.
- Microservices.
- Distributed messaging.
- Advanced search infrastructure.
- Folders, trash, starring, and file imports.
- Other features not required to demonstrate the core domain.

The objective was not to build the largest possible application. It was to demonstrate that the core application could be extended safely because the foundational boundaries were established correctly.

## 9. Engineering Principles Demonstrated

The design reflects several principles I consider important when building enterprise software:

- Security is an architectural concern, not a late-stage feature.
- Authentication and authorization are distinct responsibilities.
- Domain rules should not depend on frameworks or infrastructure.
- External systems should be accessed through explicit ports and adapters.
- Authorization must be enforced at the server-side application boundary.
- Transactions and concurrency must be deliberate.
- A modular monolith is often the right starting point.
- Abstraction should solve a real problem rather than exist for its own sake.
- A real client is an effective way to validate API design.
- Automated tests should verify security invariants, not merely successful requests.
- AI-generated implementation plans and code require engineering review.

## 10. Final Perspective

The most important part of this exercise was not simply generating a working Notes application.

It was demonstrating a repeatable engineering process: evaluate the problem, define the requirements, establish the architecture, identify risks, make deliberate tradeoffs, review the plan, and then use AI to accelerate implementation.

The first 30 minutes were spent thinking about the problem rather than writing code. That investment established the direction for the rest of the work.

The result was intended to demonstrate Principal-level engineering judgment: the ability to translate an ambiguous problem into a coherent architecture, make defensible technical decisions, establish security and quality requirements, and direct an autonomous implementation process without surrendering architectural control.
