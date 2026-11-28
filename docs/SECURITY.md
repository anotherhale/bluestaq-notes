# Security

Security is treated as a first-class requirement, not a later hardening pass.
This document describes the controls actually implemented, and is honest about
what is demonstration-grade versus production-grade.

## Threat model (in scope)

- An authenticated user attempting to read, modify, delete, or enumerate
  another user's private notes.
- A user with limited (READ/WRITE) access attempting to exceed it.
- A client attempting to forge ownership or identity via request fields.
- Unauthenticated access to protected resources.
- Resource-existence probing via UUID guessing.
- Basic abuse (oversized bodies, request floods).
- Misconfiguration that would enable development auth in production.

## Out of scope (documented, not implemented)

- DDoS mitigation, WAF, TLS termination — assumed to be provided by the
  deployment platform.
- Multi-tenant hard isolation beyond the user/note ownership model.
- Secrets management / KMS (configuration is environment-driven; see below).
- A full OIDC provider — only the relying-party (validation) side is built.

## Authentication

Authentication is independent of the Notes domain and is expressed as an
`IdentityProvider` port (ADR-003).

- **Demo (`DemoIdentityProvider`)** — development/testing only. The caller
  selects a seeded user with `X-Demo-User-ID`. The value is validated against
  the database; unknown or inactive users are rejected. The provider never
  mints identities, so guessing an arbitrary id fails. Demo auth is disabled in
  production at **two independent points**:
  - `Settings` refuses to start when `ENVIRONMENT=production` and
    `AUTH_DEMO_ENABLED=true`.
  - `DemoIdentityProvider.__init__` raises if constructed with
    `environment_is_production=True`.
- **OIDC (`OidcIdentityProvider`)** — validates bearer JWTs against a JWKS:
  signature, issuer, audience, expiry, issued-at, and required claims
  (`exp, iat, sub, iss, aud`). Cryptography is delegated to PyJWT/`cryptography`;
  nothing is hand-rolled. `sub` is mapped to a local user via
  `external_identities`; unknown subjects are rejected (no auto-provisioning).
  Construction refuses `none`/`HS*` algorithms.
- **Routing (`RoutingIdentityProvider`)** — if a bearer token is present, the
  request is treated as OIDC and a failure is **final**; it is never retried
  against demo auth. This prevents downgrading a rejected token into a demo
  identity. **Invalid authentication never falls back to demo.**

A missing or invalid credential yields `401` with `WWW-Authenticate: Bearer`.

## Authorization

Authentication and authorization are separate responsibilities. Every protected
use case calls an `AuthorizationProvider` port and treats the result as
authoritative (deny by default). ADR-004 describes the current implemented
owner/collaborator matrix. The enterprise target model is documented in
[Authorization Architecture](AUTHORIZATION_ARCHITECTURE.md),
[Authorization Policy Model](AUTHORIZATION_POLICY_MODEL.md), and proposed
[ADR-011](ADR/ADR-011-unified-authorization-model.md).

| Operation | Owner | WRITE | READ |
|---|---|---|---|
| Read | ✅ | ✅ | ✅ |
| Update | ✅ | ✅ | ❌ |
| Delete | ✅ | ❌ | ❌ |
| Manage permissions | ✅ | ❌ | ❌ |

- Ownership comes **only** from the authenticated `Principal`; a `owner_id` in a
  request body is never trusted.
- A non-owner cannot grant access, even with WRITE.
- Access cannot be granted to a nonexistent or inactive user.
- Revocation deletes the grant; the next request is denied immediately.
- Listing collaborators is owner-only (WRITE collaborators cannot enumerate the
  share list).

## Inaccessible-resource semantics (information disclosure)

To prevent enumeration of other users' private notes, an authenticated caller
who **cannot read** a note receives `404` whether the note exists or not. A
caller who **can read** but lacks the specific permission receives `403`
(see ADR-008). `GetNote`, `UpdateNote`, `DeleteNote`, `ShareNote`,
`RevokePermission`, and `ListPermissions` all check `READ` before reporting a
more specific denial.

## Input handling

- Pydantic v2 request models with `extra="forbid"` reject unknown fields.
- Field constraints: title `1..200` chars (non-blank), content `≤ 64 KiB`,
  UUID-typed path/body ids, `permission ∈ {read, write}`, `version ≥ 1`,
  pagination `limit ∈ [1,100]`, `offset ≥ 0`.
- Domain value objects re-validate and reject NUL characters and oversized
  content independently of the HTTP layer.
- A `BodySizeLimitMiddleware` rejects bodies over `MAX_REQUEST_BODY_BYTES`
  (default 256 KiB) with `413`.

## HTTP security

- **CORS**: explicit allowlist from `CORS_ALLOW_ORIGINS`; wildcard is rejected
  in production.
- **Security headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store`, a restrictive `Content-Security-Policy`,
  `Strict-Transport-Security`, and `Permissions-Policy`.
- **Request correlation**: every response carries `X-Request-ID` (a valid
  inbound value is honoured, otherwise a UUID is generated) and the id is echoed
  in the error envelope.
- **Centralized exception handling**: one error envelope for every failure;
  unhandled errors become `500` with a logged traceback and no internal detail.

## Rate limiting

A `RateLimiter` port with an in-memory fixed-window adapter
(`InMemoryRateLimiter`), keyed by the authenticated principal id so one user
cannot exhaust another's budget. Limits are configurable
(`RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`) and can be disabled
(`RATE_LIMIT_ENABLED=false`). Exceeding the limit returns `429` with
`Retry-After`.

> **Process-local limitation:** the in-memory limiter is **not** enforced across
> multiple workers or replicas behind a load balancer, and a restart clears all
> counters. It demonstrates the port and the 429 contract. A production
> deployment must swap in a shared store (e.g. Redis) or delegate to an API
> gateway, implementing the same port.

## Logging

- Structured logging (JSON by default) with a correlation id on every line.
- Access logs record method, path, status, duration, and the authenticated
  `user_id`.
- **Never logged:** passwords, tokens, secrets, note content, or request bodies.
  The demo header is not logged; only the resolved user id is.
- The `X-Request-ID` value is validated (bounded length, printable ASCII) before
  use, preventing log injection.

## Configuration

- Environment-driven, validated at startup (`Settings`); invalid or unsafe
  combinations fail fast rather than fail open.
- Production guards: demo auth must be off, `debug` must be off, CORS must be an
  explicit non-wildcard allowlist, and OIDC (when enabled) must have an issuer
  and audience.
- Provider/URL consistency is enforced (`sqlite://` ⇔ `sqlite`,
  `postgresql+...://` ⇔ `postgresql`).
- No secrets are committed; `.env.example` documents the variables with
  placeholder values, and `.env` is git-ignored.

## Database

- All access goes through SQLAlchemy ORM constructs (parameterized); there is no
  string interpolation of untrusted input into SQL.
- SQLite foreign-key enforcement is enabled per connection
  (`PRAGMA foreign_keys=ON`), so `ON DELETE CASCADE` and FK constraints are real.
- Transaction boundaries are owned by the Unit of Work; a failure rolls back.
- Optimistic concurrency prevents lost updates (ADR-007).

## Cross-site request forgery

Browser login puts a session credential in a cookie, and a cookie is *ambient*
authority: a browser attaches it to a request another origin initiated. `SameSite=Lax`
is not an enforcement point — a deployment that needs a cross-origin frontend must set
`SameSite=None`, at which point nothing server-side remained. This section previously
claimed CSRF was "not applicable: the API is token/header-based"; that stopped being
true when session cookies shipped.

Enforced server-side in `presentation/csrf.py`, at the middleware layer rather than in
a per-route dependency, because `POST /auth/logout` reads the cookie without going
through `PrincipalDep` — a dependency-level guard would have left it exposed.

The rule, and only the rule:

- Safe methods (`GET`/`HEAD`/`OPTIONS`/`TRACE`) are never blocked. Any other method,
  including one this build does not recognise, is treated as unsafe.
- A request that presents **no session cookie** is never blocked: a bearer or demo
  caller rides no ambient authority, and there is nothing to forge.
- Any other request must carry an `Origin` naming either the host and scheme the
  request was addressed to, or an entry in `CSRF_TRUSTED_ORIGINS`. A **missing**
  `Origin`, an opaque `null`, a lookalike suffix, a scheme downgrade and a port
  mismatch are all refused with `403 csrf_rejected`. Browsers send `Origin` on every
  unsafe request — including same-origin ones — so a real browser client is unaffected;
  a non-browser client that sends a cookie must send `Origin` too.

The check is fail-closed and refuses rather than rewrites; it never inspects the body,
so it composes with the body-size middleware. It is registered *after* CORS so it runs
*before* it: a forged write is refused by the guard, never answered by the CORS layer.

`CSRF_TRUSTED_ORIGINS` is deliberately separate from `CORS_ALLOW_ORIGINS`. CORS is a
*read* barrier the browser enforces; a permissive CORS list must not silently become a
write permission, so widening one does not widen the other.

Residual: this defends the cookie path only, and it is origin-based rather than
token-based, so a browser that omits `Origin` on an unsafe request would be refused
rather than served. That is the intended direction of failure.

## Deliberately deferred

- Full end-to-end OIDC handshake against a live IdP (the validator is
  implemented and unit-tested with locally generated keys).
- Distributed rate limiting.
- Per-request CSRF *tokens* (origin validation is enforced; a synchroniser-token
  scheme would add defence in depth for non-browser cookie clients, at the cost of a
  token-issuance endpoint and a second cookie — no demonstrated need yet).
- Audit log persistence (permission `granted_by`/`created_at` are the only
  audit trail).


## Enterprise authorization target (proposed, not implemented)

The target model combines RBAC, scoped RBAC, ABAC, resource ACLs, capabilities,
scoped administrative privileges, and constrained delegation through one policy
system. Mandatory tenant/trust constraints and applicable explicit denies
override ordinary grants; missing grants default to deny. Admin roles do not
automatically provide access to private note content. Agent and service actors
use the same policy semantics with authority attenuation and re-authorization.
These are architecture decisions being documented for future implementation,
not claims about current runtime behavior.
