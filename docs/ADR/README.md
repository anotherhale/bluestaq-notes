# Architecture Decision Records

Each ADR captures one significant decision: its context, the decision, the
alternatives considered, the consequences, and the tradeoffs. They are numbered
and immutable; superseding a decision means adding a new ADR.

| ADR | Title |
|---|---|
| [ADR-001](ADR-001-modular-monolith-hexagonal.md) | Modular monolith with Hexagonal Architecture |
| [ADR-002](ADR-002-domain-model-ownership-permissions.md) | Domain model and note ownership/permission rules |
| [ADR-003](ADR-003-authentication-provider-abstraction.md) | Authentication provider abstraction and development identity |
| [ADR-004](ADR-004-authorization-provider.md) | Authorization provider and deny-by-default policy |
| [ADR-005](ADR-005-sqlalchemy-repository-uow.md) | SQLAlchemy repository and Unit of Work strategy |
| [ADR-006](ADR-006-sqlite-default-postgresql.md) | SQLite default with PostgreSQL compatibility |
| [ADR-007](ADR-007-optimistic-concurrency.md) | Optimistic concurrency strategy |
| [ADR-008](ADR-008-http-error-semantics.md) | HTTP error and inaccessible-resource semantics |
| [ADR-009](ADR-009-folders-stars-search-rich-text.md) | Folders, per-user stars, search and rich-text content |
| [ADR-010](ADR-010-whiteboard-client-side.md) | Whiteboards are a client-side spatial view, not a new domain aggregate |
| [ADR-011](ADR-011-unified-authorization-model.md) | Unified enterprise authorization model (proposed) |


See also the [Enterprise Authorization Architecture](../AUTHORIZATION_ARCHITECTURE.md) and [Authorization Policy Model](../AUTHORIZATION_POLICY_MODEL.md).


The canonical proposed authorization types and invariants are documented in the [Authorization Domain Model](../AUTHORIZATION_DOMAIN_MODEL.md).
