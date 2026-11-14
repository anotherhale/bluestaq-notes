# Bluestaq Notes API — Architecture Decision Log

This document records the significant architectural decisions made during the design of the Notes REST API. It is intended to capture the alternatives considered, the selected approach, and the reasoning behind each decision.

## ADR-001 — Modular Monolith

**Decision:** Implement the application as a modular monolith.

**Alternatives considered:**

- Microservices.
- A single undifferentiated application.
- Modular monolith with explicit domain and infrastructure boundaries.

**Rationale:**

The domain and expected workload do not justify distributed deployment complexity. A modular monolith provides clear boundaries, simpler transactions, easier local execution, and a smaller operational footprint.

The architecture can evolve toward separately deployed services if future requirements justify that decision.

## ADR-002 — Domain-Driven Design and Hexagonal Architecture

**Decision:** Use DDD with Hexagonal Architecture and Ports and Adapters.

**Rationale:**

The application has meaningful domain concepts around users, ownership, notes, and permissions. These concepts should not be defined by HTTP routes or database tables.

Domain and application logic remain independent of FastAPI and SQLAlchemy. External capabilities are accessed through explicit ports and implemented by infrastructure adapters.

This supports testability, maintainability, and replacement of technical providers without rewriting domain behavior.

## ADR-003 — Authentication as a First-Class Architectural Concern

**Decision:** Introduce an IdentityProvider port from the beginning.

**Rationale:**

The application requires multiple user identities even though the assignment does not require a full login experience.

A development-only demo provider supplies seeded identities for testing. The port preserves the ability to introduce an enterprise identity provider without coupling the application to a particular authentication protocol.

Demo authentication is explicitly restricted to development and testing. It must never become a production fallback.

**Alternatives considered:**

- Hard-coded user identity in route handlers.
- Client-supplied owner identifiers.
- Directly coupling application code to a JWT or OIDC implementation.
- A replaceable identity provider boundary.

The replaceable provider boundary was selected.

## ADR-004 — Authorization as a Separate Application Boundary

**Decision:** Introduce an independent AuthorizationProvider port and enforce authorization in application use cases.

**Rationale:**

Authentication answers who the caller is. Authorization answers what that identity can do.

Keeping these responsibilities separate avoids coupling identity resolution to business access rules.

Authorization is enforced by the backend, not by frontend controls or route visibility.

The initial permission model is intentionally small:

- Owner.
- READ.
- WRITE.

The model is sufficient for the assignment without introducing a generalized RBAC or ABAC framework.

## ADR-005 — Private-by-Default Note Ownership

**Decision:** Notes are private by default and have one owner.

**Rationale:**

Private-by-default behavior minimizes accidental disclosure and provides a clear ownership model.

Sharing is an explicit action performed by the owner. Permissions are represented as domain relationships rather than arbitrary client-supplied fields.

Only the owner can delete a note or manage its permissions.

## ADR-006 — SQLite Initially, PostgreSQL-Compatible Persistence

**Decision:** Use SQLite as the default local database while keeping the persistence implementation compatible with PostgreSQL.

**Rationale:**

SQLite reduces setup and operational requirements for an evaluated take-home assignment.

SQLAlchemy and repository boundaries isolate persistence details from the domain and application layers. Alembic provides explicit schema evolution.

PostgreSQL remains a viable deployment option without requiring a redesign of the domain model.

## ADR-007 — Repository Ports and Unit of Work

**Decision:** Use repository interfaces and a Unit of Work abstraction.

**Rationale:**

Repositories isolate application operations from persistence technology. The Unit of Work coordinates transactions across related operations.

This avoids repositories independently committing partial changes and provides a consistent transaction boundary for use cases.

ORM models remain infrastructure details and do not become domain objects or API response contracts.

## ADR-008 — Optimistic Concurrency

**Decision:** Use optimistic concurrency to protect against stale updates.

**Rationale:**

Two users may retrieve the same note and attempt to modify it. Without concurrency protection, the last write could silently overwrite the first.

A version field or equivalent conditional update mechanism allows the API to reject stale modifications with a conflict response.

This preserves data integrity without introducing pessimistic locking complexity for a relatively small application.

## ADR-009 — REST API as an Independent Contract

**Decision:** Treat the REST API as an independent application boundary with explicit schemas and documented behavior.

**Rationale:**

The backend should not depend on a particular frontend or client implementation.

FastAPI and Pydantic provide a documented contract through OpenAPI. Request and response schemas prevent internal ORM models from leaking into the public interface.

The API should be independently testable and usable by clients other than the demonstration frontend.

## ADR-010 — Frontend as an Integration and Verification Client

**Decision:** Build a minimal React/TypeScript frontend after the backend contract is established.

**Rationale:**

A real client demonstrates whether the API is understandable and usable outside the backend implementation.

It exercises multi-user workflows, sharing, permission enforcement, error handling, and optimistic concurrency.

The frontend does not duplicate domain logic or act as an authorization authority. It consumes the actual API contract through a centralized typed client.

The frontend is therefore an architectural verification tool, not a reason to expand the backend domain unnecessarily.

## ADR-011 — Test Security Invariants Explicitly

**Decision:** Make cross-user authorization scenarios mandatory acceptance criteria.

**Rationale:**

A successful CRUD request does not establish that an application is secure.

Tests must verify that:

- Users cannot retrieve another user's private notes.
- READ users cannot modify notes.
- WRITE users cannot delete notes or manage permissions.
- Non-owners cannot grant or revoke access.
- Revoked permissions no longer grant access.
- Client-supplied ownership information cannot bypass authorization.
- Invalid authentication cannot fall back to demo identity.

These tests verify the actual persisted and authorized behavior rather than merely checking response status codes.

## ADR-012 — AI-Assisted Implementation with Human Architecture Review

**Decision:** Use ChatGPT to help create the implementation plan and OpenHands to execute the approved plan autonomously.

**Rationale:**

AI can accelerate requirements decomposition, architecture planning, implementation, and testing. However, the implementation plan must reflect deliberate engineering decisions and constraints.

The workflow separates planning from execution:

1. Evaluate the assignment.
2. Define functional and nonfunctional requirements.
3. Use ChatGPT to develop a proposed architecture and implementation plan.
4. Review and adjust the plan.
5. Provide the approved implementation instructions to OpenHands.
6. Verify the result through automated tests and an actual frontend client.

This preserves engineering oversight while taking advantage of autonomous implementation.

## Decision Summary

The common theme across these decisions is separation of concerns and deliberate scope control.

The solution establishes security, domain boundaries, persistence boundaries, and testability before implementation. It avoids unnecessary distributed architecture while preserving clear extension points for future requirements.

The goal is a small but complete system whose architecture can be explained, tested, and defended.
