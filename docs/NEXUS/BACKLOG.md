# NEXUS Backlog

Prioritized, dependency-ready implementation backlog. Priority tiers follow the
NEXUS directive: P0 critical, P1 foundational, P2 agent platform, P3 enterprise
capabilities, P4 enhancements.

Statuses are evidence-based: `TODO`, `STARTED`, `IMPLEMENTED`, `TESTED`,
`VERIFIED`, `COMMITTED`, `PUSHED`, `BLOCKED`.

---

## NEX-001 — Organization hierarchy vertical slice

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 |
| Status | **VERIFIED / COMMITTED** |
| Dependencies | none |

**Objective:** organizations, units, teams, memberships, and managers with
enforced authorization.

**Architecture refs:** `ENTERPRISE_AGENTIC_EXPANSION_SPEC.md` (org hierarchy),
`AUTHORIZATION_DOMAIN_MODEL.md`, ADR-008 (404 vs 403).

**Acceptance criteria**

- An organization can be created, read, updated.
- Units form a tree with cycle prevention.
- Teams belong to a unit and carry members.
- A member can be suspended, which removes organization *and* team authority.
- The last administrator cannot be removed or demoted.
- A non-administrator cannot escalate its own role.
- Soft-deleted memberships never appear in rosters.

**Required tests:** domain unit tests, application use-case tests, security
tests for escalation and last-admin guards, API tests, migration test.

**Security considerations:** default-deny; 404 for inaccessible, 403 for
insufficient permission; no identity from request body.

**Complexity:** medium. **Outcome:** all criteria verified — 320 passed, 4
skipped.

---

## NEX-002 — OIDC authentication

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 |
| Status | **VERIFIED** (CI 4/4 green on `0fcc078`, run `NEX-2026-11-25-08`) |
| Dependencies | NEX-001 |

**Objective:** replace demo-only authentication with a real OIDC authorization-
code flow (PKCE) that binds a verified external subject to an organization
membership.

**Scope:** authorization request construction with `state` + `nonce` + PKCE
`code_verifier`; callback validation (signature, issuer, audience, expiry,
nonce); session establishment with `HttpOnly`/`Secure`/`SameSite` cookie;
subject→membership resolution; explicit failure paths that deny access.

**Acceptance criteria**

- A tampered or replayed `state`/`nonce` is rejected.
- An ID token with wrong issuer/audience/expiry is rejected.
- A valid login yields a session whose subject maps to exactly one active
  membership; no membership denies access.
- Demo auth remains available only in development and cannot activate in
  production.

**Required tests:** state/nonce replay rejection, token validation failures,
session cookie flags, subject→membership mapping, production demo-auth refusal.

**Security considerations:** no token in URL after callback; no secrets logged;
fail closed on any validation error.

**Complexity:** large. **Blocks:** NEX-003, all agent identity work.

**Outcome (NEX-2026-11-24-01):** IMPLEMENTED — authorization-code + PKCE with
state/nonce, JWKS-validated ID tokens, session cookies, and subject→user linkage
via `external_identities` (never auto-provisions). Covered by
`tests/authentication/{test_login_use_cases,test_identity_provider,test_login_router,test_session_domain}.py`.

**Status correction (NEX-2026-11-25-06):** the field above read `TODO`, which was
wrong — 92 tests exist across those four modules and the flow is implemented. It
is **IMPLEMENTED, not TESTED**, because one acceptance criterion is unverified:

> *An ID token with wrong issuer/audience/expiry is rejected.*

`grep` over `tests/` finds **no test that imports
`infrastructure/identity/oidc_relying_party`** — the module holding
`HttpOidcRelyingParty._verify_id_token`, the only code that actually checks the
signature, `iss`, `aud`, `exp`/`iat`, and the nonce. Every login test substitutes
a `FakeRelyingParty`, so the verification logic itself is exercised by nothing.
The other three criteria *are* covered (`test_replayed_callback_is_rejected`,
`test_expired_transaction_is_rejected`, the `httponly`/`samesite=lax` cookie
assertions in `test_login_router.py:202`, and the production demo-auth refusals
in `test_identity_provider.py`).

Closing the gap is a bounded, well-specified slice — see NEX-010.

**Gap closed (NEX-2026-11-25-07, NEX-010):** the verifier now has direct tests —
`tests/authentication/test_oidc_relying_party.py`, 21 tests importing
`HttpOidcRelyingParty` itself with real in-process RSA keys and real RS256
signatures. Each `jwt.decode` option and the nonce check is pinned by a test that
goes red when that option is removed (verified by mutation). Writing them found a
real defect — a non-ASCII `nonce` claim raised `TypeError` out of
`secrets.compare_digest` on `str`, turning a hostile token into a 500 instead of a
refusal — now fixed. The criterion above is covered. NEX-002 is **VERIFIED**:
CI ran the full suite on `0fcc078` (a descendant of the commit carrying the tests)
and all four check runs passed on both the push and pull_request events.

---

## NEX-006 — Tenant access enforcement

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 |
| Status | **TESTED** (local gates green) |
| Dependencies | NEX-002 |

**Objective:** close the offboarding gap. Authentication proves identity; it must
not, on its own, keep granting access after the caller's membership is suspended
or deprovisioned. Revocation must take effect on the next request, not at session
expiry.

**Scope:** a pure domain rule over the caller's memberships and their
organizations; one shared application seam; enforcement on all three
identity-establishing paths (OIDC callback, per-request session resolution,
bearer tokens).

**Acceptance criteria**

- An active membership in an operational organization grants access.
- A suspended or deprovisioned membership denies access.
- A membership in an archived/suspended organization denies access, so
  suspending a tenant revokes access for everyone in it at once.
- A user with several tenants keeps access while *any* one still grants it.
- A user with no memberships is `UNAFFILIATED` and allowed through, so a new
  identity can create its first organization — granting nothing in any existing
  tenant.
- A revoked membership is rejected on the bearer path, not only on the session
  path.
- The refusal message is uniform (no account-existence oracle).

**Required tests:** domain rule branch coverage; login use-case tenant cases;
bearer path with a real signed RS256 JWT against a real unit of work.

**Security considerations:** deterministic application-layer enforcement, never
model output. Default-deny. No caching, by design — that is what makes revocation
immediate.

**Complexity:** medium. **Outcome:** `pytest` 409 passed / 4 skipped; ruff and
mypy clean. Detail in `EXECUTION_LOG.md`.

---

## NEX-007 — Tenant-scoped capability answer

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P1 |
| Status | **VERIFIED** (local gates green + CI green on `e1cb0c5`) |
| Dependencies | NEX-006 |

**Objective:** give one authoritative answer to "which tenants may this caller act
in", so tenant-scoped use cases stop re-deriving it from membership rows and
organization status themselves.

**Scope:** the domain rule already computed this set
(`TenantAccess.organization_ids`) but nothing consumed it. Expose it through the
existing application seam and make the tenant listing use it.

**Defect closed:** `ListMyOrganizations` filtered on membership rows only, so a
**suspended or archived tenant was listed with its full capability set** while
`GetOrganization` refused the same tenant with `404`. A client would render a
tenant it could not then use.

**Acceptance criteria**

- `actionable_organization_ids(uow, user_id=...)` returns exactly the tenants the
  caller may act in: active membership *and* operational organization.
- A suspended or archived tenant is absent from the set, and therefore absent
  from `ListMyOrganizations`.
- Suspending one tenant does not hide the caller's other tenants.
- The listing agrees with the per-tenant read: what the list shows, the read
  serves.
- `domain/` and `application/` stay free of FastAPI/SQLAlchemy imports.

**Required tests:** `tests/application/test_organization.py::TestSuspendedTenantIsNotListed`
— suspended tenant omitted; other tenants survive suspension.

**Security considerations:** tenant isolation. A suspended tenant granting
capabilities in a listing is an inconsistency that leaks organizational state and
misleads clients into issuing calls that must fail. No caching: the answer is
re-derived per request, so a status change is reflected immediately.

**Complexity:** small. **Outcome:** `pytest` 614 passed / 4 skipped; ruff and mypy
clean. CI green on `e1cb0c5` (all four check runs completed/success). Detail in
`EXECUTION_LOG.md`.

---

## NEX-003 — Agent identity and token exchange

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P1 |
| Status | **TESTED** (local gates green); pending commit in this run |
| Dependencies | NEX-002 |

**Objective:** make agents first-class actors whose authority is derived by
attenuation from a human or service delegator.

**Acceptance criteria**

- An agent principal is registered with an owner and a capability set.
- Token exchange issues a token whose scopes are a strict subset of the
  delegator's effective authority; a request exceeding it is denied.
- Delegation chains cannot amplify authority at any hop.
- Revocation invalidates derived tokens immediately.
- An agent cannot approve its own sensitive action.

**Required tests:** attenuation property (child ⊆ parent) as a property-style
test over chains; over-broad request denial; revocation immediacy; self-approval
denial.

**Security considerations:** this is the platform's central security boundary.
Enforcement must be deterministic application code, never model output.

**Complexity:** large.

**Outcome (NEX-2026-11-24-01):** IMPLEMENTED and TESTED.

- **Domain** — `domain/agent/{entities,value_objects,delegation,token}.py`.
  `DelegatedAuthorization` is the source of authority; `AgentToken` is a derived
  credential stored as a SHA-256 hash only (no raw secret column). Attenuation is
  a pure function that refuses to grow authority at any hop.
- **Application** — `use_cases/agents.py`: register/list/get, grant/revoke
  delegation, issue token, `ResolveAgentToken`, approval request open/decide/list.
  `ResolveAgentToken.run()` re-checks token expiry, delegation revocation, and
  agent suspension on every call, so revocation takes effect on the next request.
- **Persistence** — migration `c3f1a9d47e02` (`agent_identity_delegations_tokens`),
  verified to apply on a fresh database and to round-trip through downgrade.
  `ON DELETE CASCADE` from both `agents` and `agent_delegations` confirmed
  empirically.
- **API** — `presentation/api/routers/agents.py`; `POST /tokens/resolve` requires
  an authenticated human principal (unauthenticated introspection answers 401),
  so an agent cannot introspect its own token.
- **Tests** — `tests/agent/{test_agent_identity,test_delegation}.py` and
  `tests/api/test_agents_api.py`. Attenuation is covered by **exhaustive
  enumeration** of every capability subset and every parent/child pair rather
  than by a `hypothesis` property test: `hypothesis` is not a project dependency
  and this run did not add one. Enumeration over the delegable universe is
  strictly stronger than sampling for this invariant.

**Defect found and fixed during validation (this run):** `POST /tokens/resolve`
returned `AgentResponse`, which includes the agent's full capability *ceiling*
plus `max_token_ttl_seconds`, `version`, and timestamps — strictly more authority
than the token under inspection carried, disclosed to any token holder. The
schema's own docstring states `AgentResponse` is "only ever returned to the
owner". Introspection now returns the narrower `ResolvedAgentIdentity`
(`id`, `display_name`, `runtime_identity`, `status`) and the token's own
capabilities. Pinned by two regression tests:
`test_introspection_is_refused_without_an_authenticated_caller` and
`test_introspection_never_discloses_the_agents_capability_ceiling`.

---

## NEX-004 — Refresh PR #3 and tracking issue #2

| Field | Value |
| --- | --- |
| Phase | n/a (delivery hygiene) |
| Priority | P4 |
| Status | VERIFIED |
| Dependencies | none |

**Objective:** make GitHub reflect verified reality.

**Acceptance criteria:** PR #3 description states what is implemented versus
planned, lists validation evidence, and carries an accurate checklist; issue #2
shows current phase and next priorities. PR remains a draft until validation is
complete and human review is requested.

**Evidence (2026-11-25):** PR #3 description rewritten with a phase status
table, the two-defect write-up, a validation table, and the open risks; progress
comment 7149894984 posted to issue #2. PR #3 stays a **draft** and unmerged —
automated merging is prohibited.

---

## NEX-005 — Tool gateway

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | VERIFIED |
| Dependencies | NEX-003 |

**Objective:** a single governed path for agent tool invocation with schema
validation, authorization checks, audit records, and idempotency keys.

**Acceptance criteria:** unknown tools denied; arguments validated against a
declared schema; every invocation audited with actor, capability, and outcome;
repeated idempotency key does not duplicate the side effect.

**Complexity:** large.

---

## NEX-005-followup — Tool gateway focused tests

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | VERIFIED |
| Dependencies | NEX-005 |

**Objective:** prove the gateway's idempotency and audit invariants directly
rather than inferring them from a green full suite.

**Acceptance criteria:** replay with the same idempotency key returns the
recorded outcome without re-executing; a denied invocation is audited as denied
and cannot be replayed into an execution; a revoked delegation refuses rather
than erroring; `arguments_digest` is stable and raw arguments are never stored.

**Outcome:** `tests/agent/test_tool_gateway.py`, 18 tests, all passing. Found
two real defects (retried denial raising `IntegrityError`; revoked delegation
surfacing `InvariantViolation` as a 500). Both fixed in `74d3d98`.

**Complexity:** small.

---

## NEX-005-HTTP — Expose the tool gateway over HTTP

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | VERIFIED (run `NEX-2026-11-25-05`, `c18bac7`; CI green on that commit) |
| Dependencies | NEX-005 |

**Objective:** make the gateway reachable through the API layer, so agents can
invoke tools over HTTP under the same governed path.

**Acceptance criteria:** a router exposes tool invocation; the auth dependency
resolves an agent token for `TokenAudience.TOOL_GATEWAY` and rejects tokens
minted for the Notes API; a denied invocation returns a refusal status, not a
500; an API contract test covers the deny path and a successful invocation.

**Delivered:** `POST /api/v1/agents/tools/invoke`
(`presentation/api/routers/tools.py`), request/response schemas, DI wiring in
`presentation/dependencies.py` (`AgentTokenSecretDep`, `AgentRateLimitDep`) and
`bootstrap/container.py`. `tests/api/test_tool_gateway_api.py` (9 tests) covers a
successful audited invocation, `X-Demo-User-ID` being ignored, a malformed
header, a Notes-API token refused, a capability refusal, an unknown tool, replay
of a recorded outcome, a reused key with different arguments (`409`), and the
token never being echoed.

The router makes no security decision of its own: it authenticates the agent and
delegates to `InvokeTool`, so audience, revocation, capability, approval, and
audit stay in one place. Rate limiting keys on a digest of the presented token,
not the client address, so one agent cannot exhaust another's budget and the raw
secret never becomes a limiter key.

**Status note:** TESTED, not VERIFIED — CI has not yet run on `c18bac7`.

**Security:** the caller identity must come from the token, never the body.
Token audience separation is load-bearing — a Notes API token must not invoke
tools.

**Complexity:** medium. Closes risk R5.

---

## NEX-009 — Durable agentic runtime

> Renumbered from NEX-006 in run `NEX-2026-11-25-06`: the id was already used by
> **NEX-006 — Tenant access enforcement** (line ~90), so `next_task: NEX-006` in
> the checkpoint pointed at a completed task. No content changed.

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | **VERIFIED** (slice 1 — durable runs; CI 4/4 green on `0e74d4f`) |
| Dependencies | NEX-005 |

**Objective:** durable runs that checkpoint progress and resume after
interruption without repeating completed side effects.

**Acceptance criteria:** a run interrupted mid-step resumes from its last
checkpoint; completed steps are not re-executed; run state is queryable.

**Delivered (slice 1, run `NEX-2026-11-25-09`, commit `0e74d4f`):** the run
aggregate (`domain/agent/runs.py`), the `AgentRunRepository` port and its
SQLAlchemy adapter, `agent_runs` / `agent_run_steps` with migration
`8a4c2e1f9b73`, and the use cases `PlanAgentRun`, `AdvanceAgentRun`,
`ResolveInterruptedStep`, `AbortAgentRun`, `GetAgentRun`, `ListAgentRuns`.
Pinned by `tests/agent/test_agent_runs.py` (13 tests).

Two of the three criteria are covered: *completed steps are not re-executed*
(the crash-halt-resolve path never re-runs the resolved step) and *run state is
queryable* (`GetAgentRun` / `ListAgentRuns`, plus `list_resumable_for_agent`).
The third is covered in the sense that matters — a run whose step was found
`started` does resume, and the test proves the second step then runs while the
first is not repeated.

**Remaining:** none at the use-case level. The HTTP surface is delivered by
**NEX-009-HTTP** (below), mirroring how NEX-005 preceded NEX-005-HTTP.

**Complexity:** large (both slices VERIFIED).

---

## NEX-009-HTTP — Expose durable agent runs over HTTP

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | **VERIFIED** (run `NEX-2026-11-25-10`; CI 4/4 `completed/success` on `e817d04`) |
| Dependencies | NEX-009 |

**Objective:** make the durable-run use cases reachable through the API layer, so
an agent can plan and advance a run over HTTP and a human can read, resolve, and
abort one — through the same governed use cases, never around them.

**Acceptance criteria:** a router exposes plan, advance, get, list, resolve, and
abort; planning and advancing require an agent token and reject a Notes-API token
or a demo-identity header; reading, resolving, and aborting are owner-scoped and
return 404 for a non-owner; an interrupted step can only be settled by the owner,
never by the agent; a refusal is 401/403/404/409/422 and never a 500; an API
contract test covers each boundary.

**Delivered:** `src/notes_service/presentation/api/routers/agent_runs.py`, mounted
under `/api/v1/agent-runs`, plus request/response schemas in
`presentation/schemas.py` and DI wiring in `bootstrap/container.py`.
`tests/api/test_agent_runs_api.py` (9 tests) pins the edge: agent-token-only
planning, a step the token cannot reach refused, a full plan→advance→complete
cycle, a caller-supplied `capability` rejected, a non-owner getting 404, an agent
refused the resolve endpoint, abort being owner-scoped and terminal, listing
scoped to the caller's agent, and advance idempotent on a completed run.

The router makes no security decision of its own — it authenticates and delegates
— so audience, revocation, capability, approval, ordering, and audit stay in the
use cases and the gateway.

**Hardening in the same slice:** `PlannedStep.capability` is now **derived from
the tool catalog** and is not an init field, so a plan can no longer declare a
weaker capability than the step it schedules. The mismatch is unrepresentable
rather than merely rejected, and the now-obsolete validation block in the use
case was removed. Pinned by
`tests/agent/test_agent_runs.py::test_a_planned_step_takes_its_capability_from_the_tool_catalog`
and `::test_a_planned_step_naming_an_unknown_tool_is_rejected`.

**Status note:** VERIFIED. CI ran on `e817d04` and reported 4/4 check runs
`completed/success` (`Frontend (typecheck, lint, test, build)` and
`Backend (ruff, mypy, pytest, migrations)`, once for the push event and once for
the pull_request event). The backend gate runs the full suite bare, so this is CI
evidence, not a local gate run (R10).

**Security:** the run row is what an operator reads when deciding whether an
interrupted step's effect landed, so a plan that could understate its own
authority would corrupt the audit of an ambiguous step. Only the owning human may
settle such a step; an agent has no endpoint that resolves its own ambiguity.

**Complexity:** medium. Closes risk R11.

---

## NEX-008 — Add continuous integration

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 |
| Status | VERIFIED (green CI on `6f30020`) |
| Dependencies | none |

**Objective:** give the repository a CI gate. Before this task the repository
had **no CI configuration at all** — no `.github/workflows` on any branch and
zero check runs on any commit — so every gate documented in `AGENTS.md` ran only
when a human or agent invoked it by hand (risk R3, the largest process risk in
the project).

**Acceptance criteria:** a workflow runs on push and pull request; it executes
the backend gates (`ruff format --check`, `ruff check`, `mypy`, `pytest`) and the
frontend gates (`typecheck`, `lint`, `test`, `build`); it also proves migrations
apply to and revert from a fresh database. Every step must be a command already
verified locally, so the first CI run cannot fail for a reason local runs do not
reproduce.

**Delivered:** `.github/workflows/ci.yml` — two jobs (`backend`, `frontend`).
The backend job additionally runs `alembic upgrade head` then `alembic downgrade
base` against a throwaway SQLite database, so a migration that cannot be
reverted fails CI rather than only being discovered during an incident.

**Security:** the workflow declares `permissions: contents: read` — CI needs to
read the repository and nothing more; it must not hold write scope it never
uses. `pull_request` uses the default (fork-safe) event, so no secrets are
exposed to untrusted PRs.

**Complexity:** small.

**Verified — and the first run earned its keep.** CI ran on `b223e02` and the
backend job failed in the pytest step:

```
FileNotFoundError: [Errno 2] No such file or directory: '.venv/bin/alembic'
```

`tests/persistence/test_migrations.py` shelled out to the literal path
`.venv/bin/alembic`. That resolves on a developer machine, where the virtualenv
sits at `.venv/` by convention, and nowhere else. The three migration tests —
the ones written specifically to catch a broken migration chain — had therefore
never run outside a local checkout. The gate found a hole in itself on its first
execution, which is the clearest possible argument for having it.

Fixed in `6f30020` by invoking `[sys.executable, "-m", "alembic", *args]`, which
holds locally, in CI, and in a container alike.

**Evidence (this is the closure of risk R3):**

| Run | Commit | Result |
| --- | --- | --- |
| `45531086512` (push) | `b223e02` | backend FAILED — portability defect found |
| `45531483962` (push) | `6f30020` | **success** — both jobs |
| `45531492783` (pull_request) | `6f30020` | **success** — both jobs |

`gh api .../commits/6f30020/check-runs` reports all four check runs
`completed/success`. The repository now has an automated regression gate on every
push and pull request, and it has demonstrably caught a real defect.


---

## NEX-010 — Direct tests for the OIDC token verifier

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 (security — closes an unverified acceptance criterion) |
| Status | **VERIFIED** (CI 4/4 green on `0fcc078`, run `NEX-2026-11-25-08`) |
| Dependencies | NEX-002 |
| Complexity | small |

**Objective:** prove that `HttpOidcRelyingParty._verify_id_token` rejects a token
with a wrong signature, issuer, audience, expiry, or nonce. This is the single
unverified acceptance criterion of NEX-002 and the only untested security-critical
code in the identity stack.

**Scope:** `tests/authentication/test_oidc_relying_party.py` (new). Do not change
production code unless a test exposes a real defect.

**Why it matters:** every existing login test injects a `FakeRelyingParty`, so the
assertions about wrong issuer/audience/expiry are made against the fake, not the
real verifier. If the real verifier were misconfigured — e.g. `verify_aud` dropped,
or the nonce comparison inverted — the suite would stay green. That is the same
class of defect the R3/R6 notes warn about: a gate that cannot fail is not a gate.

**Approach:** generate an RSA key pair in the test, sign real JWTs, and stub only
the JWKS lookup (`PyJWKClient.get_signing_key_from_jwt`) so no network is touched.
Then assert `AuthenticationError` for each deviation:

- valid token → `VerifiedIdToken` with the expected subject/issuer/audience
- wrong signing key → rejected
- `iss` mismatch → rejected
- `aud` mismatch → rejected
- expired `exp` → rejected
- nonce mismatch, and nonce absent → rejected
- missing `sub` → rejected

**Acceptance criteria:** all cases above have a test that fails if the
corresponding `jwt.decode` option or the nonce check is removed. NEX-002 may then
move to TESTED, and to VERIFIED once CI confirms it.

**Security considerations:** test keys are generated in-process and never
committed; no real IdP is contacted; no secrets in fixtures.

**Outcome (run `NEX-2026-11-25-07`):** delivered —
`tests/authentication/test_oidc_relying_party.py`, 21 tests, real in-process RSA
keys and real RS256 signatures with only `PyJWKClient.get_signing_key_from_jwt`
stubbed.

Writing the tests found a **real production defect**, fixed here: the nonce
comparison was `secrets.compare_digest(nonce, expected_nonce)` on `str`, which
raises `TypeError: comparing strings with non-ASCII characters is not supported`
whenever the untrusted `nonce` claim contains non-ASCII. A hostile or merely
misbehaving IdP response therefore produced a **500 at the edge** instead of an
`AuthenticationError` refusal — a fail-open-on-availability path in the one
function that decides whether a login token is genuine. It now compares the
UTF-8 encodings, which keeps the comparison constant-time for any claim value
and cannot raise.

The acceptance criterion ("a test fails if the corresponding `jwt.decode` option
or the nonce check is removed") was verified by mutation, not assumed: each of
`verify_aud`, `verify_iss`, `verify_exp`, `verify_iat`, `verify_signature`, the
nonce check, the nonce comparison, the blank-subject guard, and the algorithm
allowlist was disabled in turn, and each produced a red suite. The one mutation
that survives is deleting the `"require"` list — it is genuine defence in depth
rather than the enforcing mechanism, because `issuer=`/`audience=` and the
`_as_datetime` timestamp guard already refuse those tokens. The behaviour is
still pinned by `test_token_without_{expiry,issued_at,issuer,audience}_is_rejected`
and `test_required_claims_must_be_present`, so a future edit that opens the gap
is caught even though that particular line is redundant.

**Promoted to VERIFIED (run `NEX-2026-11-25-08`):** CI reported 4/4 check runs
`completed/success` on `0fcc078`, on both the push (`45541856292`) and
pull_request (`45541862595`) events. Because `0fcc078` descends from the commit
that added the tests, CI executed this file's 21 tests — the evidence is CI's
result, not the local run. NEX-002 was promoted on the same evidence.

