# ADR-003: Authentication provider abstraction and development identity

## Status

Accepted.

## Context

The service must be secure by default and demonstrate real multi-user
authorization, yet the assignment also requires it to run with no external
identity service and no login screen. We need authentication that is
independent of the Notes domain, that can be exercised locally and in tests,
and that can be replaced by a real OIDC provider without rewriting use cases.

There is a well-known danger with "demo" auth: a development back door that
silently survives into production.

## Decision

Define an `IdentityProvider` **port** that turns a transport-neutral
`AuthenticationContext` into a `Principal`. The `Principal` carries the
authoritative `user_id` (from the local user record) plus non-authoritative
provenance (`provider`, `subject`, `claims`).

Implement three adapters:

1. **`DemoIdentityProvider`** — the default for local runs and tests. The caller
   selects a seeded user via the `X-Demo-User-ID` header. The value is
   **validated against the database**: unknown or inactive users are rejected,
   and the provider never mints an identity, so guessing an arbitrary id fails.
2. **`OidcIdentityProvider`** — validates a bearer JWT against a provider's
   JWKS, checking signature, issuer, audience, expiry, issued-at, and required
   claims. Cryptography is delegated to PyJWT/`cryptography`; nothing is
   hand-rolled. The `sub` claim is mapped to a local user through the
   `external_identities` table; unknown subjects are rejected (no
   auto-provisioning). Constructing it refuses unsafe algorithms (`none`,
   `HS*`).
3. **`RoutingIdentityProvider`** — composes the two. **If an `Authorization:
   Bearer` header is present, the request is treated as OIDC and a failure is
   final** — it is never retried against demo auth. This prevents a caller from
   downgrading a rejected token into a demo identity.

Demo auth is disabled in production at **two independent points**:
`Settings` refuses to start when `ENVIRONMENT=production` and
`AUTH_DEMO_ENABLED=true`, and `DemoIdentityProvider` raises if constructed with
`environment_is_production=True`.

## Alternatives considered

- **A real OAuth authorization server.** Out of scope; the assignment says not
  to build one.
- **A fake OIDC adapter that "accepts" tokens without verifying signatures.**
  Explicitly rejected — a validator that does not validate is worse than none.
  The adapter that exists genuinely verifies; it is simply optional.
- **Header-only auth with no database check.** Rejected: it would let any caller
  impersonate any id, defeating the cross-user isolation tests.

## Consequences

- Local runs and tests exercise true multi-user behaviour with zero setup.
- The OIDC path is a genuine, tested implementation rather than a stub; the only
  thing not exercised end-to-end in this environment is a live IdP handshake,
  which is documented as a deferred capability.
- Misconfiguration (demo auth in production) fails fast at startup, not open at
  runtime.

## Tradeoffs

- `X-Demo-User-ID` is intentionally a development-only mechanism and is
  documented as such. It is not a credential and must never be enabled in
  production; the config guard enforces this.
