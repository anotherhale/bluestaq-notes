# ADR-008: HTTP error and inaccessible-resource semantics

## Status

Accepted.

## Context

Error behaviour must be predictable and documented, and the service must not
leak the existence of resources a caller is not allowed to see. A naive design
returns `403` for "exists but forbidden" and `404` for "does not exist", which
lets an attacker distinguish the two and enumerate other users' private notes by
id.

## Decision

Adopt a single error envelope for every failure:

```json
{ "error": { "code": "...", "message": "...", "details": [...] }, "request_id": "..." }
```

Use a consistent status-code mapping, decided by the *caller's relationship to
the resource*:

| Situation | Status | Rationale |
|---|---|---|
| No/invalid credentials | `401` | unauthenticated (with `WWW-Authenticate: Bearer`) |
| Authenticated, resource unreadable (or nonexistent) | `404` | do **not** reveal existence |
| Authenticated, can read but not allowed to act | `403` | caller already knows it exists |
| Malformed body / bad UUID / unknown field | `422` | validation |
| Body over the size limit | `413` | payload too large |
| Optimistic-concurrency failure | `409` | conflict |
| Rate limit exceeded | `429` | with `Retry-After` |
| Unexpected server error | `500` | details never leaked |

The key rule: **authorization failures for resources the caller cannot read are
reported as `404`, not `403`.** Concretely, `GetNote`/`UpdateNote`/`DeleteNote`/
`ShareNote`/`RevokePermission`/`ListPermissions` load the note, ask the policy
`READ` first; if the caller cannot read it, they get `404`. Only when the caller
*can* read but lacks the specific permission (e.g. a WRITE collaborator trying
to delete) do they get `403`.

`AuthenticationError → 401`, `AccessDenied → 403`, `ResourceNotFound → 404`,
`Conflict`/`VersionConflict → 409`, `InvalidInput`/`ValidationError → 422`,
`RateLimited → 429`, unhandled `Exception → 500`.

## Alternatives considered

- **Always `403` for forbidden.** Rejected: leaks existence.
- **Always `404` for forbidden (never `403`).** Rejected: unhelpful for the
  legitimate case where the caller already knows the resource exists (they can
  read it), and it obscures a meaningful distinction for clients.
- **Per-endpoint ad-hoc error shapes.** Rejected: inconsistent and hard to
  consume.

## Consequences

- Probing for another user's private note by id yields `404` regardless of
  whether the id exists, defeating enumeration.
- Clients can rely on one error shape and a documented status mapping.
- The mandatory "guessing a UUID does not bypass authorization" test asserts
  this behaviour.

## Tradeoffs

- The 404-vs-403 rule requires each use case to check `READ` before reporting a
  more specific denial. That extra check is deliberate and small, and it is
  centralised in the policy call rather than duplicated per route.
