# ADR-004: Authorization provider and deny-by-default policy

## Status

Accepted.

## Context

Authentication answers *who* the caller is; authorization answers *what they may
do*. These are separate responsibilities and must not be conflated. Every
protected operation has to be authorized against the authenticated `Principal`,
and the policy has to be replaceable (RBAC, ABAC, external engine) without
rewriting the Notes use cases.

## Decision

Define an `AuthorizationProvider` **port** with a single method:

```python
def decide(*, principal: Principal, action: Action, note: Note) -> Decision
```

`Action` is an explicit vocabulary (`READ`, `UPDATE`, `DELETE`,
`MANAGE_PERMISSIONS`). Every protected use case calls `decide(...)` and treats
the result as authoritative — there is no implicit allow anywhere.

`DefaultAuthorizationProvider` encodes the requirements directly:

| Action | Owner | WRITE collaborator | READ collaborator | Other |
|---|---|---|---|---|
| READ | ✅ | ✅ | ✅ | ❌ |
| UPDATE | ✅ | ✅ | ❌ | ❌ |
| DELETE | ✅ | ❌ | ❌ | ❌ |
| MANAGE_PERMISSIONS | ✅ | ❌ | ❌ | ❌ |

Anything not explicitly granted is **denied by default**.

Additional rules enforced in the application layer:

- Ownership is taken from the `Principal` only; `owner_id` in a request body is
  never trusted.
- A user cannot grant permissions to a non-existent or inactive user, or to the
  owner.
- Revocation deletes the grant row; the next request re-evaluates access and is
  denied immediately.

## Alternatives considered

- **Inline `if user == note.owner` checks scattered across routes.** Rejected:
  easy to forget on a new endpoint, hard to test in isolation, and impossible to
  replace.
- **A full policy engine (e.g. OPA/Casbin) now.** Rejected as premature; the
  port makes it a drop-in later.
- **Putting the decision inside the `Note` aggregate.** Rejected (see ADR-002):
  it would entangle the replaceable policy with structural invariants.

## Consequences

- A single, auditable place decides access; the mandatory cross-user tests
  target exactly this component plus the use cases that call it.
- Swapping in RBAC/ABAC or an external engine means implementing one Protocol.
- Every use case returns the *same* kind of `Decision`, so error semantics are
  uniform.

## Tradeoffs

- `Action` is a small closed enum; a future policy that needs richer context
  (resource attributes, environment) can extend the `decide` signature behind
  the same port, at the cost of a wider interface.
