# ADR-002: Domain model and note ownership/permission rules

## Status

Accepted.

## Context

The product requirement is narrow and concrete: a note has exactly one owner,
is private by default, and may be shared with other users at READ or WRITE
level. Only the owner may delete or manage sharing. Shared users may not
transfer ownership or grant further access.

The temptation in a take-home is to reach for a generic RBAC/ABAC model with
roles, policies, and permission inheritance. That would be speculative
complexity for a domain with exactly two permission levels and one special
principal (the owner).

## Decision

Model the requirements directly as a `Note` **aggregate root**:

- `Note` owns its identity, title, content, version, and its collection of
  `NotePermission` grants. Sharing invariants are enforced inside the aggregate,
  so they cannot be bypassed by an adapter.
- **Ownership is a field of the aggregate**, fixed at creation
  (`owner_id`). The owner is *implicitly* fully authorised and is **never**
  represented by a `NotePermission` row — this removes an entire class of
  "owner has a redundant/inconsistent grant" bugs.
- `PermissionLevel` is an ordered `StrEnum` (`READ < WRITE`) with an `includes`
  relation; `WRITE` implies `READ`. A user holds at most one grant per note.
- Aggregate invariants: exactly one owner; no self-grant; only the owner may
  `update_content`, `grant_permission`, or `revoke_permission`; at most
  `MAX_PERMISSIONS_PER_NOTE` grants.

Value objects (`NoteId`, `NoteTitle`, `NoteContent`, `NoteVersion`,
`PermissionLevel`, `UserId`, `Email`, `ProviderSubject`) validate at
construction and are immutable.

**Authorization is kept out of the aggregate as a policy decision.** The
aggregate exposes queries (`can_read`, `can_write`, `can_manage`,
`effective_level`) and *safety-net* invariants, but the authoritative decision
is made by an `AuthorizationProvider` port (see ADR-004). This keeps the
"who may do what" policy replaceable while the "what is structurally valid"
rules stay in the domain.

## Alternatives considered

- **Role-based model (`OWNER`, `EDITOR`, `VIEWER` roles).** Rejected: roles here
  are just permission levels plus ownership; a role table adds indirection with
  no behavioural gain.
- **Permissions stored as a bitmask/column on the note.** Rejected: loses the
  audit trail (`granted_by`, `created_at`) and makes per-user revocation and
  listing awkward.
- **Representing the owner as a `WRITE` permission row.** Rejected: it creates a
  second source of truth for ownership and permits inconsistent states.

## Consequences

- The mandatory sharing behaviours (READ reads, WRITE edits, WRITE cannot
  delete or manage sharing, revocation is immediate) fall out of a small,
  explicit policy plus the aggregate invariants.
- Ownership can never be supplied or changed via a request body: `owner_id` is
  set from the authenticated `Principal` only.
- Adding a future level (e.g. `COMMENT`) is a local change to the enum and the
  policy, not a schema redesign.

## Tradeoffs

- Keeping authorization outside the aggregate means the aggregate alone cannot
  answer "may this user read this note?" — that is intentional. It is answered
  by the injected policy, which is the documented extension point for RBAC,
  ABAC, or an external policy engine.
