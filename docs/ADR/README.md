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
| [ADR-012](ADR-012-context-assembly-before-judgment.md) | Context assembly precedes outbound judgment |
| [ADR-013](ADR-013-git-refs-as-coordination-primitive.md) | Git refs as the coordination primitive |
| [ADR-014](ADR-014-obligations-as-separate-axis.md) | Obligations are a separate decision axis, not a third effect |
| [ADR-015](ADR-015-step-up-assurance-signal.md) | `step-up` is satisfied by a recorded authentication assurance, not a judgement |
| [ADR-016](ADR-016-risk-based-approval.md) | Risk is derived from the capability, and it can only raise the gate |
| [ADR-017](ADR-017-operator-access-log-retention.md) | The operator access log is retained on a bounded window; erasing it is its own authority |
| [ADR-018](ADR-018-table-wide-constraints-must-admit-history.md) | A table-wide constraint must admit the history it will be evaluated against |


See also the [Enterprise Authorization Architecture](../AUTHORIZATION_ARCHITECTURE.md) and [Authorization Policy Model](../AUTHORIZATION_POLICY_MODEL.md).


The canonical proposed authorization types and invariants are documented in the [Authorization Domain Model](../AUTHORIZATION_DOMAIN_MODEL.md).
