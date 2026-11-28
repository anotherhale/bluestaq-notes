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
| Status | **VERIFIED** (descendant CI 4/4 at `fa387a9`/`ca15eb1` + 239-test local rerun, run `NEX-2026-11-25-19`) |
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
| Status | **VERIFIED** (product commit `e5f1bb4`; descendant CI 4/4 at `fa387a9`/`ca15eb1` + 239-test local rerun, run `NEX-2026-11-25-19`) |
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

**Status note:** VERIFIED — corrected in run `NEX-2026-11-25-19`. The earlier "CI has not
yet run on `c18bac7`" note was stale: `c18bac7` was observed 4/4 `completed/success`
(Backend + Frontend, each reporting `head_sha` `c18bac7`) when re-polled this run.

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

## NEX-011 — Expose the tool-invocation audit trail over HTTP

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | **VERIFIED** (run `NEX-2026-11-25-11`; commit `f50a78d`; CI **4/4 `completed/success`** observed 2026-11-25T05:18Z) |
| Dependencies | NEX-003 (governed tool gateway), NEX-005-HTTP (owner-scoped listing pattern) |

**Objective:** make the gateway's own audit trail readable. The governed tool
gateway writes a `ToolInvocation` row for every attempt it handles — refusals
included — but until this slice nothing outside the gateway could read one back.
An owner had no way to answer "what did my agent actually try, and what was
refused?" A trail nobody can read is not an audit trail.

**Acceptance criteria:** an owner can list the invocations of their own agent;
a non-owner gets 404 and never 403; the response carries `arguments_digest` and
never the arguments; refused attempts appear in the trail with the gateway's
reason; the window is explicitly bounded by `limit`/`offset` with a validated
cap; an API contract test covers each boundary.

**Delivered:** `ListToolInvocations` use case in
`application/use_cases/agents.py`, `GET /api/v1/agents/{agent_id}/tool-invocations`
in `presentation/api/routers/agents.py`, `ToolInvocationResponse` /
`ToolInvocationListResponse` in `presentation/schemas.py`, and DI wiring in
`bootstrap/container.py`. No new port and no migration: the
`ToolInvocationRepository` port, its SQLAlchemy adapter, its
`list_for_agent(limit, offset)` method and the `tool_invocations` table all
already existed — this slice adds the read path and nothing else.

`tests/api/test_audit_trail_api.py` (8 tests) pins the edge: a successful
invocation is readable with its resource ref and completion time, a
capability-denied call still appears as a `denied` row carrying the gateway's
outcome, the response body never contains note content, a non-owner gets 404, an
unknown agent id is indistinguishable from an unowned one, paging bounds the
window, an out-of-range `limit` is 422, and an unauthenticated caller is refused.

**Design note — explicit paging instead of the `_page` helper.** The other agent
listings use a shared `_page` helper with a fixed window. An audit trail grows
without bound, so a caller needs to walk it deliberately; this route takes
`limit`/`offset` directly and caps `limit` at 200 at the edge.

**Status note:** TESTED, not VERIFIED. The full suite is green locally (670
passed, 4 skipped) and `ruff format --check` (bare), `ruff check` and `mypy` are
clean, but local gates are not CI evidence (R10). CI check runs were observed
`in_progress` on `f50a78d` at the end of the run and had not concluded, so this
task must not be promoted on the strength of the local run alone.

**Security:** the trail records which tools an agent reached for, when, and
whether it was allowed to. That is the owning human's evidence, so it is
owner-scoped like `ListApprovalRequests`, and the 404-not-403 rule keeps the
endpoint from being used to probe for another user's agents. Storing only
`arguments_digest` means the audit trail cannot become a second copy of note
content — a trail that leaked the data it was auditing would be a new exposure
created by the control meant to detect one.

**Complexity:** small. Narrows R11's scope to the run surface only.

---

## NEX-012 — Tamper-evident audit chain

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P2 |
| Status | **VERIFIED** (run `NEX-2026-11-25-12`; commit `3aade7c`; CI **4/4 `completed/success`** observed 2026-11-25T05:49Z) |
| Dependencies | NEX-005 (gateway write path), NEX-011 (read path) |

**Objective:** turn the audit trail from a log into evidence. NEX-005 wrote a
`ToolInvocation` row for every attempt and NEX-011 made it readable, but the
rows were mutable: anyone with database write access could edit a `denied`
verdict to `succeeded`, or delete a row entirely, and nothing would notice. A
record that cannot prove it is intact is not audit evidence.

**Acceptance criteria:** each invocation is sealed over its predecessor so that
an edit or deletion is detectable; the seal covers the recorded *decision*, not
just the arguments; an untouched trail verifies and reports how many entries
were walked; a break is *reported* with its sequence and reason, not raised; an
empty trail passes; only the owning human can verify; an API contract test
covers each boundary.

**Delivered:**

- `domain/agent/audit_chain.py` — `ChainVerdict` and `verify_chain`, pure and
  framework-free. A break is a return value, not an exception: the caller needs
  to know *where* history diverges.
- `application/ports/agents.py` — `next_chain_link(agent_id)` and
  `list_chain(agent_id)`. `list_chain` is deliberately unpaginated: the first
  entry of a window does not carry the hash of the entry before it, so a window
  cannot be verified.
- `application/use_cases/tools.py` — the gateway seals intent before the call
  and outcome after it, refusals included.
- `application/use_cases/agents.py` — `VerifyToolInvocationChain`, owner-only.
- `presentation/api/routers/agents.py` —
  `GET /api/v1/agents/{agent_id}/tool-invocations/verification`.
- migration `b2e5c81f4a07` adds `chain_sequence`, `previous_hash`,
  `intent_hash`, `outcome_hash` plus a unique `(agent_id, chain_sequence)`.

**Concurrency:** `next_chain_link` is read inside the inserting transaction, so
two concurrent attempts cannot both claim one sequence number; the unique
constraint rejects the loser rather than silently forking the chain.

**Tests:** `tests/api/test_audit_chain_verification_api.py` (7 tests). Tampering
is applied through the database directly, because that is the threat model —
someone editing history behind the API's back; tampering through the API would
prove nothing. Covered: untouched trail verifies with `checked` = number of
invocations; a mixed success/refusal trail verifies; an empty trail passes with
`checked` = 0; flipping a `denied` verdict to `succeeded` is reported at
sequence 1 with an outcome-hash reason; deleting a middle row is reported at
sequence 3 as a sequence gap; a non-owner gets 404; the chain fields are visible
in the trail listing so the verdict and the listing describe the same data.

**Status note:** VERIFIED. `ruff format` (bare), `ruff check` and `mypy` are
clean and the full suite is green locally (681 collected, 677 passed, 4 skipped),
and CI on `3aade7c` returned **4/4 `completed/success`** (Backend
ruff/mypy/pytest/migrations and Frontend typecheck/lint/test/build, both push and
pull_request), observed 2026-11-25T05:49Z. This is the fourth slice to reach
VERIFIED on observed CI, after NEX-009 slice 1, NEX-009-HTTP and NEX-011.

**Security:** this is the control that detects tampering with the audit trail,
so its own failure mode matters. `checked` is returned precisely so that
`verified: true` cannot be confused with "walked nothing" — a verifier that
reports success without examining rows would be worse than no verifier. The
verdict is owner-scoped with the 404-not-403 rule, so it cannot be used to probe
for another user's agents.

**Complexity:** small-to-medium. One migration, one pure domain module, no new
dependency.

---

## NEX-013 — Bound approval validity and make approvals single-use

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P1 (security) |
| Status | **VERIFIED** — criterion met on `12584a1`, CI observed. It was previously recorded VERIFIED on `a50a9a0` (CI [45559723156](https://github.com/anotherhale/bluestaq-notes/actions/runs/45559723156) green on `e2bf7bc`) and that promotion was **wrong**: green CI proved the assertions passed, not that the invariant held, because the tests never touched the broken write path. Run `NEX-2026-11-25-14` fixed the path in `12584a1` and added tests that pin it; CI on `12584a1` is 4/4 `completed/success` (2 push + 2 pull_request check runs, all `head_sha 12584a1`). See "Correction" below. |
| Dependencies | NEX-005 (gateway), NEX-003 (approval request lifecycle) |

**Objective:** an approval was an unlimited-use, never-expiring authorisation.
The gateway matched an `approved` row on `(agent, capability, resource)` and
neither spent it nor checked its age, so one human decision to delete a note
authorised **every later delete of that note, forever**. The security
architecture caps approval validity (High tier: 72 hours) and requires that a
lapsed approval block execution. Both properties were unimplemented, so a
replayed approval was indistinguishable from a fresh one.

**Acceptance criteria:** an approval authorises exactly one action and is spent
by it; a second attempt against the same resource finds no usable approval and
does not execute; an approval past its validity window does not authorise
execution (fail closed); an expired request cannot be *decided* into an
`approved` row that the gateway must then refuse; consumption is atomic, so two
concurrent attempts cannot both spend one approval; legacy rows get a bounded
window on migration.

**Delivered:**

- `domain/agent/entities.py` — `ApprovalRequest` gains `expires_at` /
  `consumed_at`, an `is_usable(now=)` predicate, and `consume(now=)`.
  `open()` takes a `ttl` (default `DEFAULT_APPROVAL_TTL` = 72h) and stamps
  `expires_at = requested_at + ttl`. `_assert_decidable` refuses to decide an
  expired request, so the recorded status never claims lapsed authority.
- `application/use_cases/tools.py` — the gateway calls
  `uow.approvals.consume(...)` before executing instead of matching an
  `approved` row, and re-checks expiry at execution time.
- `infrastructure/db/agent_repositories.py` — `consume()` is a single
  conditional `UPDATE ... WHERE status='approved' AND consumed_at IS NULL AND
  (expires_at IS NULL OR expires_at > :now)` returning `rowcount == 1`. The
  database decides the race, not a read-then-write in Python.
- migration `d4c8e2a71f05` adds both columns and backfills `expires_at` from
  `requested_at + DEFAULT_APPROVAL_TTL` for existing rows.

**Tests:** `tests/agent/test_tool_gateway.py` — an approval is spent by the
action it authorises (second attempt refused, note survives, `consumed_at`
stamped); an expired approval does not authorise execution (note survives).
`tests/persistence/test_migrations.py::TestApprovalValidityBackfill` seeds a
legacy `approved` row at the previous revision and asserts the upgrade sets
`expires_at` to `requested_at + 72h` and leaves `consumed_at` NULL.

**Defect found and fixed during this slice:** the backfill first shipped as
`requested_at + DEFAULT_APPROVAL_TTL` on the raw driver value. A plain
`sa.text()` SELECT bypasses the `UTCDateTime` type decorator, so SQLite returns
the stored *string* and the addition raised `TypeError`, aborting the upgrade
mid-flight on any non-empty table — leaving `expires_at` NULL and therefore
*unbounded*, the exact vulnerability the migration exists to close. The rest of
the migration suite upgrades an empty database, so every test stayed green.
Fixed by coercing through `_as_datetime()`, and pinned by the non-empty-table
test above. Both new gateway tests and the backfill test were confirmed to fail
against the pre-fix code.

**Security:** closes an approval-replay hole (P1). Consumption is enforced by a
conditional UPDATE, so it is correct under concurrency rather than merely
usually correct. The backfill is fail-closed: any approval older than the
window becomes unusable instead of silently keeping indefinite authority.
Columns stay nullable for backward compatibility mid-deploy; the aggregate
always populates them.

**Residual risk (R16, closed in run 14):** `expires_at` is nullable, and
`is_usable` treats NULL as "never expires". That is needed for rows written by an
older application version during a deploy, but it means a NULL row is unbounded.
Run 14 made a missing bound fail closed instead, so the column may stay nullable
without granting permanent authority.

**Correction (run `NEX-2026-11-25-14`, commit `12584a1`).** This entry was
promoted to VERIFIED, but the invariant it claims was **not enforced on the
production path**:

- `ApprovalRequestRepository.add()` built `ApprovalRequestRow` without
  `expires_at` or `consumed_at`. Every approval the tool gateway opened was
  written with `expires_at = NULL`.
- The `consume()` predicate shown above accepted `expires_at IS NULL`, and
  `is_usable` read NULL as "never expires". So the 72-hour bound was inert for
  every gateway-created approval, and an approved delete could be replayed
  indefinitely.
- The expiry test passed because it exercised `save()` — which does write the
  columns — never `add()`, the path the gateway uses. CI was green because the
  tests were green, and the tests did not cover the broken path.

Fixed in `12584a1`, three independent fail-closed changes: `add()` persists both
columns; the aggregate treats a missing bound as lapsed; the conditional UPDATE
requires `expires_at IS NOT NULL AND expires_at > :now`. New tests pin the
gateway path (`InvokeTool` → `add()` → reload) and the NULL-bound case, and were
confirmed to fail against the unfixed repository.

This is risk class **R18**: a slice can pass its tests and its CI while the
adapter that stores the invariant drops the field the check reads. Verified
status for any field a security check consumes must be established through the
write path production actually uses.

**Complexity:** small. One migration, no new dependency.

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

## NEX-015 - Keep decided approvals readable by their owner

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P1 (governance / auditability) |
| Status | **VERIFIED** - CI reported 4/4 check runs `completed/success` on `414a222`, on both the push ([45570071880](https://github.com/anotherhale/bluestaq-notes/actions/runs/45570071880)) and pull_request ([45570060946](https://github.com/anotherhale/bluestaq-notes/actions/runs/45570060946)) events, all `head_sha 414a222`, observed 2026-11-25T07:42Z. CI ran the eight `TestDecidedApprovalsRemainReadable` tests on that commit, so this rests on CI's result, not on the local run. |
| Dependencies | NEX-003 (approval lifecycle), NEX-013 (approval validity), NEX-014 (revocation) |

**Objective:** a decision erased the record from the only read path. `GET
/api/v1/agents/{id}/approvals` returned *pending* requests only, so the instant a
human approved or denied a sensitive action the request disappeared from the API.
The approver could not confirm what they had authorised, and an auditor had no API
view of the grant at all - the tool-invocation audit trail held the only copy. This
is a governance defect rather than a crash: nothing failed loudly, the record
simply became unreachable through the interface meant to review it.

**Evidence (reproduced before the fix, not inferred):** opened an approval as
Alice, decided it (`approved`), then re-listed. The pending list held the id, the
decision returned `approved`, and the after-decision list was `[]`.

**Root cause:** the use case called `list_pending_for_agent` unconditionally, while
the repository port already exposed `list_for_agent`. The capability existed; no
route reached it.

**Delivered:**

- `application/use_cases/agents.py` - `ListApprovalRequests` gains an optional
  `status`, defaulting to `pending` so existing behaviour and callers are
  unchanged; any other value selects from the agent's full history. New
  `GetApprovalRequest` reads one request by id, checking agent ownership and
  raising `ResourceNotFound` otherwise.
- `presentation/api/routers/agents.py` - `?status=` enum-typed query parameter on
  the listing (a bad value is 422, not silently ignored) and a new
  `GET /agents/approvals/{approval_id}` route.
- `bootstrap/container.py` - wires `get_approval_request`.

**Security:** both read paths are owner-only and return **404** to a non-owner,
matching the existing listing, so an approval id cannot be used to probe for
another user's agent. No weaker authorisation path was introduced; the ownership
check is the same `load_owned_agent` / `agent.owner_user_id` test used by the
decision and revocation routes.

**Tests:** `tests/api/test_agents_api.py::TestDecidedApprovalsRemainReadable` - 8
tests: approved/denied/revoked each listed by status with the deciding user and
reason; the default listing still the pending queue; read-by-id returns the decided
record; a non-owner read is 404; an unknown id is 404; an unknown status is 422.
Mutation-verified: reverting the use case to `list_pending_for_agent` for every
status fails the three `?status=` tests, and restoring it passes all eight.

**Next action:** none - complete. Reuse the pattern: for every state a record can enter, assert that some read path returns it (see R20).

## NEX-016 - Park approval-gated steps instead of failing the run

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P0 (a human approval could never be honoured - the gate was unusable) |
| Status | **VERIFIED** - CI reported 4/4 check runs `completed/success` on `b9b7207` (Backend ×2, Frontend ×2), polled 2026-11-25T08:23Z. Local gates at the same sha were also green: `ruff format --check` 196 files, `ruff check`, `mypy` 98 files, `pytest 714 passed / 4 skipped`. |
| Dependencies | NEX-009 (durable runtime), NEX-003 (approval lifecycle), NEX-013 (approval validity), NEX-014 (revocation) |
| Architecture | durable execution, explicit authorization, fail-closed governance |

**Objective:** a run that reached an approval-gated step could never resume, so
the approval mechanism was decorative for durable runs. Two coupled defects:

1. `AdvanceAgentRun` caught `AccessDenied` and called `fail_step`. A failed step
   drives the run to `FAILED`, which is terminal - the approval a human was
   about to grant had nothing left to resume.
2. The gateway recorded the "approval needed" refusal as a `DENIED` invocation
   under the step's own idempotency key. `(agent_id, idempotency_key)` is
   unique, so the denial permanently occupied the operational key. The retry
   after approval found the denial, replayed it, and re-raising was the only
   possible outcome. The gateway's own comment promised the agent could return
   after a decision the code made unreachable.

**Delivered:**

- `application/errors.py` - `ApprovalRequired`, an `AccessDenied` subtype, so
  the HTTP 403 surface and every existing handler are unchanged.
- `domain/agent/runs.py` - `Run.defer_step` returns the never-started step to
  `pending` and sets the run to `PENDING`. Honest (nothing is in flight and
  nothing failed) and resumable (a later advance picks the step up).
- `application/use_cases/agent_runs.py` - `AdvanceAgentRun` catches
  `ApprovalRequired` and defers instead of failing.
- `application/use_cases/tools.py` - audited under a `#awaiting-approval` sibling
  key via `_deferred_idempotency_key`, leaving the operational key free for the
  attempt that actually executes. Over-long keys are digested rather than
  suffixed past the column limit. `_approved_request` / `_defer_awaiting_approval`
  reuse an open request for the same (capability, resource), so repeatedly
  advancing a paused run cannot flood the approver's queue.

**Security:** the gate still fails closed - no step executes without a recorded
approval, and the deferral does not widen any permission. The run is now
resumable only through the same authenticated advance path, which re-checks the
approval on every attempt.

**Tests:** `tests/api/test_run_approval_gate_api.py` - 3 tests: an approval-gated
step pauses the run instead of failing it; a run completes after the human
approves (the end-to-end path that was previously impossible); a second advance
does not open a duplicate approval request.

**Next action:** observe CI on `b9b7207`; on 4/4 green promote to VERIFIED.

## NEX-017 - Re-check tenant access when resolving an agent token

| Field | Value |
| --- | --- |
| Phase | 2 (identity & delegation) / 5 (durable runtime) |
| Priority | P0 (offboarding did not revoke agent authority) |
| Status | **VERIFIED** at `990557d` - CI 4/4 completed/success, observed 2026-11-25T08:56Z |
| Dependencies | NEX-006 (tenant access enforcement), NEX-003 (agent identity), NEX-014 (revocation) |
| Architecture | explicit authorization, fail-closed security, one shared decision seam |

**Objective:** NEX-006 enforced tenant access on the three human identity paths.
Agent-token resolution is a fourth way to establish an identity and did not check
it, so a suspended or deprovisioned delegator's agent stayed fully operational
until the token expired.

**Why it mattered:** `_actor_principal` makes every tool call act *as the
delegator*. The agent inherits the person's authority, so it must also inherit
the person's loss of it. This was the NEX-006 offboarding gap one layer up.

**Delivered:** `ResolveAgentToken` in `application/use_cases/agents.py` now calls
`resolve_tenant_access` after its existing checks (expiry, delegation
revocation, agent suspension, capability subset) and refuses `DENIED`. The
`tenant_access.py` docstring is corrected from "three paths" to four.

**Decisions:**

- Reuses `resolve_tenant_access` rather than re-deriving the rule, so there is
  still one decision implementation.
- `UNAFFILIATED` remains allowed so a new identity can bootstrap; only `DENIED`
  is refused.
- The refusal is a pause: reactivating the membership restores the same token.

**Tests:** `tests/api/test_agents_api.py::TestDelegatorTenantAccess` - suspend the
delegator over HTTP, resolve the agent token (403), reactivate, resolve again
(success). Both were observed failing before the fix (200 while suspended).

**Security:** closes an offboarding hole in which a departed or suspended
person's agent retained live authority over the delegator's resources.

**Next action:** done — VERIFIED on 4/4 `completed/success` at `990557d`.

## NEX-018 — Erasing an outcome seal must not launder a verdict

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P1 (security: audit integrity) |
| Status | **VERIFIED** — descendant-CI at `2b3beb7` (4/4 `completed/success`), polled 2026-11-25T09:28Z |
| Dependencies | NEX-012 (audit chain), NEX-005 (gateway write path) |

**Objective:** close the cheapest forgery against the audit chain. NEX-012 sealed
each entry's verdict in `outcome_hash`, but `verify_chain` only recomputed that
seal when it was *present*. An operator with database write access could rewrite
a `denied` row to `succeeded`, then null both `outcome_hash` and `completed_at`.
That left nothing to recompute, so the verifier reported `verified: True` — the
exact threat NEX-012 exists to detect.

**Root cause:** the verifier keyed only off `outcome_hash is not None` and
`completed_at is not None`. Absence of a seal was never itself treated as
suspicious for a *terminal* row. Only `started` may honestly have no seal.

**Delivered:**

- `domain/agent/audit_chain.py` — `verify_chain` now rejects a non-`started`
  entry that carries no outcome seal, reason `"terminal invocation carries no
  outcome hash"`. `_STARTED_STATUS = "started"` is a literal, not an import of
  `InvocationStatus`, because `domain.agent.tools` imports this module (a cycle).
- `tests/agent/test_audit_chain.py` — 6 domain tests against the real
  `ToolInvocation` aggregate: genuine chain verifies; an honest unfinished
  `started` entry still verifies; the exact forgery (flip verdict, null hash and
  completion time) fails; the same forgery behind a valid link fails at the right
  sequence; the pre-existing `completed_at`-only guard still fires; a stale seal
  after a status edit still fails.

**Why the existing test missed it:** `tests/api/test_audit_chain_verification_api.py`
flipped `status` but left `outcome_hash` intact, so the stale-seal branch caught
it. The stronger forgery — nulling the seal too — had no test. This is R18 again:
the test exercised a weaker attack than the one the control must survive.

**Gates (local, on the feature branch):** `ruff format --check` clean (197
files), `ruff check` clean, `mypy` clean (98 sources), full `pytest` 722 passed /
4 skipped.

**CI (descendant-CI, stated as such):** the fix commit `789d78a` has **0 check
runs of its own** — the docs commit `2b3beb7` was pushed immediately after it and
the CI concurrency group cancelled the superseded run. `2b3beb7` is **4/4
`completed/success`** (Backend ×2, Frontend ×2), polled 2026-11-25T09:28Z. Because
`789d78a` is an ancestor of `2b3beb7`, that green run built a tree containing the
fix and ran the new tests. This is not a "green at this exact SHA" claim for
`789d78a`; it is descendant-CI, and it is labelled that way deliberately.

**Next action:** done — VERIFIED on descendant-CI at `2b3beb7`.


---

## NEX-019 — A stale write must not erase run progress

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P1 (data integrity: durable run concurrency) |
| Status | **VERIFIED** — CI 4/4 `completed/success` at `66e8cd6`, polled 2026-11-25T09:46Z (all runs' `head_sha` confirmed); local gates green |
| Dependencies | NEX-005 (agent runtime), NEX-011 (run aggregate) |

**Objective:** make the run aggregate's version meaningful on write. `AgentRun.version`
was incremented on every mutation but the adapter never compared it, so the value only
ever described the in-memory object. Two writers that loaded the same run both committed,
and the second overwrote the first writer's step progress with a state derived from its
stale copy. Nothing raised, so neither caller learned it had lost.

For a note that is a lost edit. For a durable run it is worse: a stale copy committing
after a crash can turn an `interrupted` step back into `pending`. The next resume then
re-executes a step whose effect may already have landed — the duplicate execution the
aggregate exists to prevent.

**Root cause:** `SqlAlchemyAgentRunRepository.save` wrote `version=run.version` with no
predicate. There was no record of which version the loaded aggregate corresponded to, so
the adapter had nothing to compare against. `run.version - 1` cannot supply it: a caller
may mutate several times between two saves.

**Delivered:**

- `domain/agent/runs.py` — the aggregate records the version it was last persisted at
  (`persisted_version`), seeded in `__post_init__` from the version it was constructed
  with, so a row loaded at v3 saves expecting v3. `mark_persisted()` is called by `add`
  and after a successful `save`.
- `infrastructure/db/agent_repositories.py` — `save` issues one
  `UPDATE ... WHERE id = :id AND version = :expected` (the same shape
  `SqlAlchemyNoteRepository.save` already uses) and raises `VersionConflict` when the
  rowcount is 0. `VersionConflict` already maps to HTTP 409 in `presentation/errors.py`,
  so the conflict is fail-closed and visible to the caller rather than silent.
- `tests/agent/test_run_concurrency.py` — 3 tests through production's `save` path. Two
  writers at one version cannot both commit and the winner's step survives; a
  matching-version write still succeeds and drives the run to `COMPLETED` (so a
  always-raising guard cannot pass the first test while breaking real advances); a stale
  write cannot erase an `interrupted` step.

**Gates (local, on the feature branch at `66e8cd6`):** `ruff format --check` clean (198
files), `ruff check` clean, `mypy` clean (98 sources), full `pytest` **725 passed / 4
skipped** (previous head was 722; the delta is exactly the 3 new tests).

**CI:** not yet observed on `66e8cd6`. Stated as an absence, not a pass.

**Next action:** poll CI for `66e8cd6` and promote to VERIFIED only on 4/4
`completed/success` reporting that `head_sha`.

## NEX-020 — A stale approval decision must not resurrect spent authority

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P0 (security: silent re-grant of withdrawn/spent authority) |
| Status | **VERIFIED** — CI 4/4 `completed/success` observed at `460a26a`, every check run reporting `head_sha` `460a26a` (polled 2026-11-25T10:16:40Z) |
| Dependencies | NEX-013 (approval validity), NEX-014 (revocation), NEX-016 (approval-gated steps), NEX-019 (the CAS pattern this copies) |

**Objective:** close the same lost-update hole NEX-019 closed for runs, on the aggregate
where losing the update hands back *authority* rather than progress.

`ApprovalRequestRow` carried no `version`, and `SqlAlchemyApprovalRequestRepository.save`
rewrote every decision-bearing column from whatever the caller had loaded. Two writers
that loaded the same request both committed, and the second wrote its stale copy over the
first. Concretely:

- A `DecideApprovalRequest` holding a pre-revocation copy writes `status=approved` back
  over a row a human had **revoked**, silently restoring withdrawn authority.
- A stale copy of an approval that was already **consumed** clears `consumed_at`, so a
  single-use approval becomes spendable a second time — the double-spend that NEX-013's
  single-use rule exists to prevent.

Nothing raised. For a note this would be a lost edit; here it is a silent re-grant of
authority a human explicitly withdrew, which is why the slice is P0 rather than P1.

**Root cause:** `save` was an unconditional full-row overwrite. The row had no version
column, so the adapter had nothing to compare against, and `ApprovalRequest` kept no
record of which version it had been loaded at. `version - 1` cannot supply that, because
a caller may mutate several times between two saves.

**Delivered:**

- `domain/agent/entities.py` — `ApprovalRequest` gains `version` (default 1) and
  `persisted_version` (seeded in `__post_init__`, updated by `mark_persisted()`), plus a
  private `_bump()` called by **every** authority-changing transition: `approve`, `deny`,
  `revoke` and `consume`. The `consume` bump is load-bearing: without it a stale save
  still matches the stored version and clears `consumed_at`.
- `infrastructure/db/models.py` — `ApprovalRequestRow.version`, matching the existing
  agent-table convention (`Integer`, `nullable=False`, `default=1`).
- `infrastructure/db/agent_repositories.py` — `_approval_to_domain` maps `version`;
  `add()` persists `version` and `revoke_reason` and calls `mark_persisted()`; `save()`
  is one `UPDATE ... WHERE id = :id AND version = :expected` raising `VersionConflict`
  on a rowcount of 0 (already HTTP 409 via `presentation/errors.py`); `consume()` sets
  `version = version + 1` in the same statement that sets `consumed_at`.
- `migrations/versions/f19a2c6d8b04_approval_request_version.py` — adds `version` with
  `server_default="1"` so existing rows are valid without a backfill. Round-tripped
  locally (upgrade → downgrade → upgrade).
- `tests/agent/test_approval_concurrency.py` — 5 tests through production's `save` path:
  a stale approve cannot resurrect a revoked approval; a stale write cannot clear a spent
  approval; a stale consume cannot double-spend; a **sequential** decision from the fresh
  version still commits (so an always-raising guard cannot pass); every transition bumps
  the version.

**Negative control:** removing the `version == expected` predicate from the `UPDATE`
turns 3 of the 5 tests red, so they detect the lost update rather than passing vacuously.

**Gates (local, bare, on the feature branch):** `ruff format` clean, `ruff check` clean,
`mypy` clean (98 sources), full `pytest` **730 passed / 4 skipped** (was 725; the delta is
exactly the 5 new tests), `alembic upgrade head → downgrade -1 → upgrade head` clean.

**CI:** observed **4/4 `completed/success` at `460a26a`**, every check run reporting
`head_sha` `460a26a` — Backend (ruff, mypy, pytest, migrations) ×2 and Frontend
(typecheck, lint, test, build) ×2. Polled 2026-11-25T10:16:40Z. Promoted to VERIFIED on
that observation, not on descendant-CI and not on the local gates.

**Next action:** continue the CAS audit (NEX-021) — enumerate the remaining repositories
whose `save()` rewrites a whole row without a compare-and-set; delegations, tokens and tool
invocations are the first candidates.

*Satisfied:* that audit is written up as NEX-021 below and the enumeration is complete. The
three candidates named here — delegations, tokens, tool invocations — turned out **not** to
need a version; `AgentIdentity` was the real remaining offender.

## NEX-021 — A stale write must not resurrect a suspended or revoked agent

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P0 (security: silent re-grant of withdrawn authority on the emergency-stop aggregate) |
| Status | **VERIFIED** — 4/4 `completed/success` at `e459f98` (polled 2026-11-25T10:46Z, every check run reporting that head_sha). 5 new tests pass, full suite 735 passed / 4 skipped, gates clean |
| Dependencies | NEX-003 (agent identity), NEX-019 (the CAS pattern), NEX-020 (the second copy of it) |

**Objective:** finish the CAS audit. Close the same lost-update hole NEX-019 and NEX-020
closed for runs and approvals, on the aggregate whose revocation *is* the kill switch.

The audit enumerated every `save()` in `infrastructure/db/` and asked one question per
aggregate: does the write rewrite authority-bearing columns from caller-supplied state,
and is there a version to compare against?

| Aggregate | `version` column | `persisted_version` | CAS on `save` |
| --- | --- | --- | --- |
| `Note` | yes | via `expected_version` arg | **yes** |
| `AgentRun` | yes | yes | **yes** (NEX-019) |
| `ApprovalRequest` | yes | yes | **yes** (NEX-020) |
| **`AgentIdentity`** | **yes** | **no** | **no — unconditional overwrite** |
| `DelegatedAuthorization` | no | n/a | writes 3 fields; revoke monotonic |
| `AgentToken` | no | n/a | writes `revoked_at` only; monotonic |
| `ToolInvocation` | no | n/a | writes verdict and seal together |

The table records the state **at audit time**, before this slice. After it, `AgentIdentity`
moves into the "yes / yes / yes" column and no aggregate is left without either a
compare-and-set or a monotonic write.

`AgentIdentity` was the only remaining offender, and the worst one. It already bumped
`version` on **every** authority-changing transition — `suspend`, `activate`, `revoke`,
`narrow_capabilities` — so the version was maintained and then ignored. `save()` wrote
`status`, `capabilities`, `revoked_at`, `max_token_ttl_seconds` and the rest straight from
whatever the caller had loaded.

**Harm.** Two requests can hold the same agent id at once (a human revoking in the control
centre while an unrelated edit finishes). With no compare-and-set the later commit wins
silently:

- a stale copy saved after a revoke writes `status=active` and `revoked_at=NULL` back over
  the revoked row — the emergency stop is undone and the agent can issue tokens again;
- a stale copy taken before a `narrow_capabilities` restores the removed capabilities,
  re-widening a ceiling the domain only ever permits to shrink.

Nothing raises. For a note this is a lost edit; here it is a silent re-grant of authority a
human explicitly withdrew.

**Root cause:** same shape as NEX-020. `save` was an unconditional full-row overwrite, and
`AgentIdentity` kept no record of the version it had been loaded at. `version - 1` cannot
supply it, because a caller may mutate several times between two saves (e.g. suspend then
narrow, one `save`).

**Delivered:**

- `domain/agent/entities.py` — `AgentIdentity` gains `persisted_version` (seeded in
  `__post_init__`, updated by a new `mark_persisted()`), matching `ApprovalRequest` and
  `AgentRun`. `version` and the `_bump()` calls were already correct and are unchanged.
- `infrastructure/db/agent_repositories.py` — `add()` now calls `mark_persisted()`;
  `save()` is one `UPDATE ... WHERE id = :id AND version = :expected` raising
  `VersionConflict` on rowcount 0 (already HTTP 409 via `presentation/errors.py`), then
  calls `mark_persisted()`.
- No migration: `agents.version` has existed since `c3f1a9d47e02`.

**Tests:** `tests/agent/test_agent_identity_concurrency.py` — 5 tests through the
production write path: a stale save cannot resurrect a revoked agent, cannot reinstate a
suspended one, cannot widen a narrowed capability set; an honest current-version write
still succeeds; and a double save without reload is refused because `mark_persisted()`
moved `persisted_version` forward.

**Deliberately out of scope:** `DelegatedAuthorization`, `AgentToken` and `ToolInvocation`
carry no version and need none. Each `save()` writes a strictly monotonic field
(`revoked_at`, a terminal verdict), so two concurrent writers converge on the same value
rather than one erasing the other's authority. Adding a version there would be ceremony,
not a guard. Recorded here so a future audit does not re-open it without cause.

**Next action:** observe CI on this commit; on 4/4 `completed/success` at this `head_sha`,
promote to VERIFIED.

## NEX-022 — A run must be bound to the delegation that planned it, not merely to its agent

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) |
| Priority | P0 (security: a durable run could outlive the consent that started it) |
| Status | **VERIFIED** — 4/4 `completed/success` at `1156f6e` (polled 2026-11-25T11:27:56Z, every check run reporting that `head_sha`). 2 new tests pass, full suite 737 passed / 4 skipped, gates clean, negative control verified |
| Dependencies | NEX-003 (agent identity), NEX-017 (delegator re-check per step) |

**Objective:** close the gap between what the run use cases *claim* and what they
*enforce* about authority.

The `agent_runs.py` module docstring states the invariant the durable runtime exists to
uphold: *"a durable run cannot outlive the authority that started it. Revoking the token
stops the run on its next step."* `PlanAgentRun` honours it — it records
`delegation_id=resolved.delegation.id` on the run at plan time. `AdvanceAgentRun._load_for_agent`
then re-resolved the token per step (good) but compared **only the agent id**:

```python
if run is None or AgentId.parse(run.agent_id) != resolved.agent.id:
    raise ResourceNotFound("run not found")
```

**Harm.** An agent can hold more than one delegation at a time — a supported state, not an
edge case (a user grants a narrow delegation, later a wider one; or two teams delegate to
the same service agent). The stored `delegation_id` was written and then never read on the
advance path, so:

- a token minted from delegation **B** could drive a run that delegation **A** planned and
  whose step capabilities were validated against **A** at plan time — so **B** need not
  even hold the capability the steps require;
- revoking **A** — the withdrawal of the specific consent that started this run — did not
  stop the run while any token from **B** existed. The run outlived the authority that
  started it, which is precisely what per-step re-resolution exists to prevent.

Nothing raised. `delegation_id` was persisted, carried a foreign key, and was inert.

**Root cause:** the check read the field that was convenient (`agent_id`, needed anyway for
the 404-vs-403 split) and never the field that carried the authority (`delegation_id`). The
column existed, was written on every plan, and had
`ForeignKey("agent_delegations.id", ondelete="SET NULL")` — the schema said the binding
mattered; the code did not read it. Same family as R23: the check was in *a* place, and no
test asserted the specific binding it was supposed to establish.

**Delivered:**
- `application/use_cases/agent_runs.py`: `_load_for_agent` now requires
  `run.delegation_id == resolved.delegation.id`, and fails closed when the binding is
  `None`. The column is nullable and its FK is `ON DELETE SET NULL`, so a stored run really
  can lose its binding; an unbound row must not be readable as "any token for this agent
  will do".
- `tests/agent/test_agent_runs.py`: two tests.
  `test_advance_refuses_a_token_from_a_different_delegation` mints a second delegation and
  token for the same agent, asserts `AccessDenied`, asserts the run was left untouched
  (every step still `PENDING`, no note created), then asserts a *fresh token from the
  planning delegation* still completes the run — so the refusal is about the delegation,
  not about minting a new token.
  `test_advance_fails_closed_when_a_run_records_no_delegation` clears the binding with a
  direct `UPDATE` (the adapter's `save` deliberately never rewrites it, so `save` cannot
  produce the state) and asserts `AccessDenied`.

**Negative control:** deleting the two-line check turns exactly those 2 tests red and leaves
the other 14 in the file green. Restored and re-verified.

**Security notes:** 404 is still returned for an unknown run or another agent's run, so
existence is not leaked. `AccessDenied` (403) is returned when the run is *this* agent's but
bound to a different delegation — the agent already knows the run exists, so 403 is the
honest answer, consistent with ADR-008.

**Not delivered, deliberately:** no migration (the column and its FK already exist) and no
change to the plan path (it was already correct). This is a read-path fix.

**Verified:** CI is 4/4 `completed/success` at `1156f6e`, the commit that carries the fix, its tests and this documentation — so the run covered the whole slice rather than an ancestor.
**Next action:** none for this slice; take the next dependency-ready Phase-5 task.

## NEX-023 — An owner's ruling must seal the audit record the crash left open

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) / audit integrity |
| Priority | P0 (the tamper-evident trail could permanently assert "unknown" about an effect that has since been ruled on) |
| Status | **VERIFIED** — 4/4 `completed/success` at `c367746`, polled 2026-11-25T11:50:20Z, `head_sha` matching the polled commit. Local gates at the slice: 19 passed in file, full suite 740 passed / 4 skipped (baseline 737; delta is exactly these 3 tests), negative control verified. |
| Dependencies | NEX-011 (audit trail), NEX-012 (hash chain), NEX-018 (a missing seal counts as a break) |

**Objective:** close the terminal-state gap between the run aggregate and the gateway's
audit chain.

`InvokeTool` commits its `ToolInvocation` row as `started`, in its own transaction,
*before* entering the handler. That ordering is correct and deliberate: a crash inside a
handler must leave a visible trace rather than none. But nothing ever closed that row for
the step a crash interrupted.

`ResolveInterruptedStep` settled the run — the step became `succeeded` or `failed` and the
run reached a terminal status — and left the gateway's invocation in `started` forever.
Three consequences, none cosmetic:

- **The trail is permanently wrong about a fact that is now known.** After an owner rules
  "the effect landed", a `started` link still asserts the outcome is unknown. The chain
  exists so a later reader can tell what happened; this makes it lie about the one event a
  human explicitly adjudicated.
- **`started` is ambiguous by construction.** It is exactly what an attempt *still in
  flight* looks like, so a crash-orphaned link and a live call are indistinguishable
  afterwards. Leaving one behind destroys that distinction permanently for that chain.
- **It made the chain's own repair path unreachable.** The step's deterministic key stayed
  at `started`, so `_find_prior` raised `Conflict` on every retry: the ruling that was
  supposed to unblock the run did not unblock the gateway.

**Implementation:** `ResolveInterruptedStep` now seals the step's audit link in the same
transaction that settles the run, so the two records cannot disagree. It locates the
invocation by the same deterministic key the gateway used
(`step_idempotency_key(run.id, sequence)`) and closes it only while it is still `started`:

- `effect_landed=True` → `succeed(outcome=...)`, which recomputes the outcome seal.
- `effect_landed=False` → `fail(outcome=...)`, recording what the human ruled rather than a
  blanket success.
- absent, or already terminal → left untouched. A link the gateway settled is stronger
  evidence than an operator's reconstruction, and rewriting it would defeat the chain. A
  step that crashed *before* the gateway wrote its row has nothing to seal, so this is a
  no-op rather than an error.

The seal is recomputed through the existing `succeed`/`fail` transitions, so `outcome_hash`
stays consistent with content and `verify_chain` still verifies.

**Tests** (`tests/agent/test_agent_runs.py`, +3, plus a hardened existing path):
- `test_owner_ruling_seals_the_audit_record_the_crash_left_open` — builds the exact crash
  state (step begun, invocation committed `started`, process gone), rules the effect
  landed, then asserts the link is `succeeded` with `completed_at` and `outcome_hash`, that
  **no** link is left `started`, and that `verify_chain` still verifies.
- `test_owner_ruling_records_a_failure_when_the_effect_did_not_land` — the same crash state
  ruled the other way must record `failed`, not success.
- `test_resolving_leaves_an_already_terminal_audit_link_untouched` — a link the gateway
  already settled keeps its status, `outcome` and `outcome_hash` byte-for-byte.

**Negative control:** reverting only the use-case change turns exactly the two sealing tests
red and leaves the rest of the file green; the change was restored and the file re-verified
at 19 passed.

**Not delivered, deliberately:** no migration (the invocation table already carries status,
outcome and seal), no change to the gateway's write-before-execute ordering (it is the right
ordering), and no attempt to infer whether an effect landed — the domain still refuses to
guess on the operator's behalf.

**Next action:** poll CI at the pushed head; promote to VERIFIED only on an observed
4/4 `completed/success` whose `head_sha` is that commit.

---

## NEX-024 — Aborting a run must seal the audit record too

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) / audit integrity |
| Priority | P0 (R27 recurrence: the trail keeps asserting "unknown" about a run that is definitively over) |
| Status | **VERIFIED** — 4/4 `completed/success` at `eaaae23`, polled 2026-11-25T12:16Z; all four `head_sha` equal the polled commit, so this is a run at the slice's own SHA (not descendant-CI). Full suite 742 passed / 4 skipped (baseline 740; delta is exactly these 2 tests); `ruff format`/`ruff check`/`mypy` clean; negative control verified. |
| Dependencies | NEX-011 (audit trail), NEX-012 (hash chain), NEX-018 (a missing seal counts as a break), NEX-023 (same defect on the resolve path) |

**Objective:** close R27 on the last settle path that still leaked an open audit link.

NEX-023 sealed the invocation a crash left open when an owner **rules** on an interrupted
step. It did not cover the owner's other way of settling a run: **aborting** it. So the
defect survived on exactly one path — the one taken when the owner decides not to adjudicate
at all.

The state it produces is the same one NEX-023 described, and it is worse in one respect:

- **A terminal run with a `started` link is a contradiction.** `aborted` says the run is
  over; `started` says an effect may still be in flight. Nothing downstream can tell which
  record to believe.
- **`started` is indistinguishable from a live call.** The crash-orphaned link and a call
  still executing look identical forever, which is the distinction the chain exists to
  preserve.
- **The step itself was also left mid-flight.** `AgentRun.abort` flipped the run's status
  and nothing else, so a `started` step sat inside an `aborted` run. The aggregate was
  internally inconsistent before the audit chain was even considered.

**Implementation**, two halves that must agree:

- **Domain** (`domain/agent/runs.py`): `AgentRun.abort` now settles every non-terminal,
  non-`pending` step before it flips the run's status — `started` → `failed`,
  `interrupted` → resolved as *effect not confirmed*. A new `RunStep.abandon` owns that
  rule. `pending` steps are left alone: they never began, so there is nothing to settle and
  marking them failed would invent a history. A step that already settled is untouched.
- **Application** (`application/use_cases/agent_runs.py`): `AbortAgentRun` records the
  mid-flight sequences *before* calling `run.abort` (afterwards they are no longer
  mid-flight), then seals each matching invocation by the gateway's own deterministic key
  (`step_idempotency_key(run.id, sequence)`) in the same transaction as the run save. It
  closes the link only while it is still `started`, and records `fail(...)` rather than
  `succeed(...)`: aborting is not a ruling that the effect landed, so claiming success would
  fabricate evidence. Absent or already-terminal links are left untouched, for the same
  reasons as NEX-023.

The seal goes through the existing `fail` transition, so `outcome_hash` is recomputed from
content and `verify_chain` still verifies.

**Tests** (`tests/agent/test_agent_runs.py`, +2):
- `test_abort_seals_the_audit_link_the_crash_left_open` — builds the real crash state
  (`_crash_mid_step`), recovers it the way a resume would so the step is `interrupted`,
  aborts, then asserts the step is `failed` with an abort outcome, the link is `failed` with
  `completed_at` and `outcome_hash`, **no** link remains `started`, and `verify_chain`
  verifies.
- `test_abort_leaves_a_settled_step_and_its_link_untouched` — a run whose first step and its
  invocation already succeeded keeps both byte-for-byte when the run around them is aborted.

**Negative control:** removing only the seal loop from `AbortAgentRun` turns exactly
`test_abort_seals_the_audit_link_the_crash_left_open` red (`assert STARTED is FAILED`) and
leaves the other 20 in the file green; restored and re-verified.

**R27 status:** closed on both settle paths that exist today — resolve (NEX-023) and abort
(this slice). A *third* settle path added later would reopen it; the invariant to carry
forward is "any operation that makes a run terminal must settle its mid-flight steps and
seal their invocations in the same transaction."

**Next action:** poll CI at the pushed head; promote to VERIFIED only on an observed
4/4 `completed/success` whose `head_sha` is that commit.

## NEX-026 — An approval must be judged against the clock that stamped it

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) / approval governance |
| Priority | P0 (R29: the approval control returns the wrong answer in both directions whenever the deciding clock differs from the opening clock) |
| Status | **VERIFIED** — CI observed at `b46e2e3`: **4/4 `completed/success`**, every check run reporting `head_sha` `b46e2e3`, polled 2026-11-25T13:34Z (Backend ruff/mypy/pytest/migrations, Frontend typecheck/lint/test/build). Local gates green: `ruff format`/`ruff check` clean, `mypy` 98 files clean, full `pytest` **754 passed / 4 skipped** (baseline 748; delta is exactly the 6 new tests). Negative control verified: reverting only `entities.py` turns 5 of the 6 new tests red. The docs commit `8742faa` pushed afterwards did not disturb it. |
| Dependencies | NEX-013 (the expiry bound), NEX-016 (approve-then-continue), NEX-020 (stale-decision authority), NEX-025 (revocation) |

**Objective:** close R29 — compare one instant against another, never two clocks.

`ApprovalRequest.open` derives `expires_at` from the timestamp it is given, and every
production use case passes its own `Clock` port. The decision path then asked `is_expired`,
which reads the module-level wall clock. Two clocks, one comparison:

- opened on a clock **behind** the wall clock, a live request is judged already lapsed, so a
  human cannot decide a request that is in fact still valid — the gate refuses consent it
  should accept;
- opened on a clock **ahead** of the wall clock, a lapsed request is judged still valid, so an
  approval outlives the bound it was issued under — consent survives longer than granted.

Neither failure is visible when the wall clock is the only clock in play, which is why every
existing expiry test passed throughout. The repository adapter was already correct (it
compares `expires_at > now` on the caller's clock, fail-closed on NULL); the defect was
confined to the aggregate's own decision path.

**Change:**
- `domain/agent/entities.py`: `expired_at(*, now)` becomes the single honest test. `is_expired`
  is kept as the wall-clock convenience for callers with no clock and now *delegates* to it,
  so NULL-means-lapsed is stated once. `is_usable`, `_assert_decidable`, `approve` and `deny`
  take the caller's instant. `approve`/`deny` use that one instant for both the decidability
  check and `decided_at`; previously they were two separate readings, so the recorded
  timestamp could disagree with the instant that was actually checked.
- `application/use_cases/agents.py`: `OpenApprovalRequest` passes `now=self._clock.now()`.
- `application/use_cases/tools.py`: `_open_or_reuse_approval_request` takes `now` and uses it
  for **both** the reuse test and a new request's bound.

**Tests** (`tests/agent/test_approval_clock.py`, +6): a request opened on a past clock is
still decidable; a lapsed request is refused even when the wall clock disagrees (approve and
deny); `decided_at` is the supplied instant; plus two cases pinning that `is_expired` still
reads the wall clock and a NULL bound is expired at any instant — so the fix cannot be
"delete the check".

**Negative control:** reverting only `entities.py` to the pre-slice version turns 5 of the 6
tests red (the sixth asserts the wall-clock property, which must keep passing).

**No migration.** `expires_at` was already persisted; only the judging clock changed.

**R29 status:** closed at the aggregate and the gateway. Invariant to carry forward: any
validity bound must be judged against the clock that stamped it; a new bound that reads a
module clock reopens this.

**Next action:** poll CI at `b46e2e3`; promote to VERIFIED only on an observed 4/4
`completed/success` whose `head_sha` is that commit.


## NEX-027 — Provider-neutral judgment that cannot grant authority

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) / agent decisioning (M2.6) |
| Priority | P2 (unblocks the agentic platform; the security property is P0-grade but nothing regresses without it) |
| Status | **VERIFIED** — CI observed at `fb8d5bd`: **4/4 `completed/success`**, every check run reporting `head_sha` `fb8d5bd5e43aa7940cbebb238c66e00fdaea8360`, polled 2026-11-25T14:18Z (Backend ruff/mypy/pytest/migrations, Frontend typecheck/lint/test/build). Local gates green: `ruff format`/`ruff check` clean, `mypy` 103 files clean, full `pytest` **794 passed / 4 skipped** (baseline 754; delta is exactly the 40 new tests). Negative control verified. |
| Dependencies | NEX-003 (agent identity), NEX-005 (tool gateway), NEX-016 (approvals). Blocks context assembly. |
| Complexity | Medium — 5 new source modules, 2 new test files, 2 modified bootstrap files, no migration |

**Objective:** give the platform a bounded way to ask a judgment service a narrow question,
with no path from an answer to an authorization.

M2.6 named "decisioning and context assembly" as its remaining work. This is the first half.
The design problem is not "call a model" — it is that a model-driven system's most common
security failure is letting an opinion become an authorization.

**Three structural properties, each enforced by the shape of the types:**

- **A decision grants nothing.** `DecisionRequest` and `Decision` carry no field that can name
  a permission, a capability, a principal, or a resource scope. There is nothing to *hold* an
  authorization in. The decisioning modules cannot reach the authorization provider at all,
  and an AST test asserts that rather than trusting review.
- **Every answer is bounded.** A choice must be one of the candidates the caller supplied; a
  score and a confidence are floats in `[0.0, 1.0]`; the rationale is capped at 400
  characters. There is no free-form channel, so a provider cannot smuggle an instruction back
  into the application.
- **Abstention is a first-class outcome.** A provider that is disabled, unsure, slow, or
  malformed produces `ABSTAINED` with a named reason (`DISABLED`, `PROVIDER_ERROR`,
  `MALFORMED_OUTPUT`, `LOW_CONFIDENCE`). Failing closed is expressed by *not deciding*, never
  by deciding "allow" — which is why no binary approve verdict exists anywhere in the
  vocabulary.

**Change:**
- `domain/agent/decisioning.py`: `DecisionKind` (closed set of seven questions, split into
  choice kinds and score kinds so the answer shapes cannot be confused), `DecisionRequest`
  (carries a SHA-256 context hash, never the context), `Decision` (bounded by construction),
  `Abstention`/`AbstentionReason`, `validate_against()`.
- `application/ports/decisioning.py`: `DecisionProvider`, one method wide and deliberately not
  a chat port, so an adapter cannot grow an escape hatch.
- `application/use_cases/decisioning.py`: `EvaluateDecision` — the single route to a decision.
  Four rules apply to every provider: disabled abstains without calling it; a raised error or
  non-`Decision` payload becomes an abstention; `validate_against` discards a wrong-kind answer
  or an unoffered choice; confidence below the caller's threshold abstains.
  `DecisionEvaluation` records provider name, latency, threshold, reason and context hash for
  audit. `DEFAULT_MIN_CONFIDENCE = 0.5`.
- `infrastructure/decisioning/stub.py`: deterministic, dependency-free `StubDecisionProvider`
  answering at `0.1` confidence, so enabling the flag alone does not start handing out
  opinions. Choice answers use `min(candidates)` so the result does not depend on input
  ordering.
- `bootstrap/config.py` (+7): `decisioning_enabled` defaults `False`,
  `decisioning_min_confidence` defaults `0.5`.
- `bootstrap/container.py` (+19): wires `evaluate_decision`.

**Tests** (+40): `tests/agent/test_decisioning.py` (36) and
`tests/architecture/test_decision_provider_neutrality.py` (4).

**Negative control:** planting `from jev_sdk import Client` in `application/ports/` turns two
neutrality tests red with the offending file named; probe deleted, file re-verified clean.

**Security:** no decision can grant, expand, or bypass authority. `approval_recommendation` is
a *score*, not a yes/no, precisely so no caller can read an advisory number as consent. No
raw content leaves the process — only a hash.

**Migration:** none. **New dependencies:** none.

**R30 (closed by NEX-035):** the stub was the only adapter, so the 400-character rationale
bound and the 50-candidate cap had only been exercised against a provider that answers
cheaply. NEX-035 shipped the conformance suite and, in building it, found the provider
*identity* was never policed at all — see the NEX-035 entry at the end of this file.

**Next action:** context assembly — the other half of M2.6. It decides *what* the context hash
is computed over and what may enter a request.

---

## NEX-028 — Context assembly: classify what may influence a judgment

| Field | Value |
| --- | --- |
| Phase | 5 (durable runtime) / agent decisioning (M2.6) |
| Priority | P2 (completes M2.6 decisioning; the "hash is not declassification" gap was P1-grade) |
| Status | **VERIFIED** — CI observed at `a3d1b32`: **4/4 `completed/success`**, every check run reporting `head_sha` `a3d1b3214fa990aab9abe2f93cdef642bde8c882`, polled 2026-11-25T14:42Z (Backend ruff/mypy/pytest/migrations ×2, Frontend typecheck/lint/test/build ×2 — push and pull_request runs both green). Local gates green: `ruff format`/`ruff check` clean, `mypy` 104 files clean, full `pytest` **824 passed / 4 skipped** (baseline 794; delta is exactly the 30 new tests). Negative control verified (3 tests red). |
| Dependencies | NEX-027 (the decision port and its context hash). Completes M2.6 decisioning. |
| Complexity | Medium — 1 new source module, 1 new test file, 3 extended files, 1 new ADR, no migration |

**Objective:** decide *what* may enter an outbound judgment, so the context hash NEX-027 sends
is a declassification boundary rather than a fingerprint of the content it was meant to protect.

NEX-027's own closing risk (R30) and its "Next action" named this. The gap it left is subtle
and reads as safety while not being safe: a SHA-256 of raw context is a **fingerprint** of that
context. For low-entropy content — an email address, a title, a two-word subject — a holder of
the digest can confirm a guess by hashing it. "Only a hash leaves the process" was therefore
never a declassification boundary, and hashing the *raw* context made that invisible.

**Three structural properties, enforced by construction rather than review:**

- **`RESTRICTED` can never be a ceiling.** Naming it is a construction error, not a runtime
  branch, so no configuration and no data path can select it. There is no setting under which
  restricted content contributes to an outbound digest.
- **Exclusion is recorded, never silent.** `excluded_field_names` names what was withheld —
  names only, never values — and this is threaded through `DecisionRequest` and
  `DecisionEvaluation` into the audit record. A silent drop would make "we asked with less than
  we held" indistinguishable from "there was nothing more", which is the failure being
  prevented.
- **Fields are named and bounded.** Names are stable lowercase identifiers so telemetry can
  aggregate them; values are capped at 200 characters and fields at 32 per envelope, so a whole
  document cannot be smuggled in under one name.

**Change:**
- `domain/agent/context.py` (new): `DataClassification` (four-rung ladder, `rank` ordering),
  `ContextField` (validated name/classification/bounded value), `ContextEnvelope`
  (`from_fields`, `included_fields`, `excluded_field_names`, `context_hash`). The hash is
  computed over **only** fields at or below the ceiling, so a field above it is absent from the
  canonical form and cannot influence — or be confirmed from — the digest. Default ceiling
  `INTERNAL`. Canonical form sorts by name for reproducible cross-process digests.
- `domain/agent/decisioning.py` (+34): `DecisionRequest.from_context()` (the only sanctioned way
  to turn an envelope into a question) and `excluded_context_fields` on the request.
- `application/use_cases/decisioning.py` (+7): `DecisionEvaluation.excluded_context_fields`,
  populated from the request, so the audit trail can say the provider was asked about less.
- `docs/ADR/ADR-012-context-assembly-before-judgment.md` (new): records why a hash is not a
  declassification and why assembly is owned by one module.

**Tests** (+30): `tests/agent/test_context_envelope.py` (28 collected) and 2 added to
`tests/architecture/test_decision_provider_neutrality.py` (6 collected at baseline → 8). The
30-test delta reconciles exactly with the suite going 794 → 824.

The load-bearing test is `test_confidential_value_cannot_influence_the_digest`: two envelopes
differing *only* in a confidential field must hash identically. That asserts the security
property directly instead of assuming it.

**Negative control:** making `included_fields` ignore the ceiling turns three tests red,
including the leak test — confirming the tests actually bind the mechanism rather than the
implementation's shape. Restored and re-verified green.

**Security:** a confidential value provably cannot influence an outbound digest. The judgment
path still cannot reach authorization; an architecture test pins both that and context.py's
purity (no framework, no vendor, no transport import).

**Known limit (documented in the `from_context` docstring):** a caller can still hand-assemble
a `context_hash` string, since it is a plain `str`. Doing so means taking on the
declassification decision personally. **Resolved by NEX-030:** `context_hash` is now a
`ContextHash`, minted only by `ContextEnvelope`; see the NEX-030 section below.

**Migration:** none. **New dependencies:** none.

**Next action:** wire `ContextEnvelope` into the first real caller (tool-gateway classification
or the approval-recommendation path) so assembly is exercised by production code, not only by
tests (R31). Promotion is done: CI on `a3d1b32` was observed 4/4 `completed/success` at
2026-11-25T14:42Z, which is what the VERIFIED status above rests on.

## NEX-029 — the judgment path gets a production caller

**Status:** VERIFIED (`evidence_basis: descendant-CI`, 2026-11-25).
**Commit:** `f71745e` (product). **Evidence:** green run at `e3b8517`.

The advisory route (`application/use_cases/approval_advisory.py`) is the first non-test
caller of `ContextEnvelope` + `DecisionRequest`. It builds an envelope from a real stored
approval record, classifies the subject's **title** `CONFIDENTIAL` (so it exceeds the default
`INTERNAL` ceiling and is withheld), and records the withheld field *name* — never its value —
in `excluded_context_fields`. The response carries **no verdict field**, so the contract shape
prevents the provider's output being read as an approval.

**Why the evidence is descendant-CI and not a bare green SHA.** `f71745e`'s four check runs
all read `completed/cancelled`: the docs commit `e3b8517` superseded it in the CI concurrency
group. A superseded run is neither pass nor fail, so `f71745e` can never be polled green.
`e3b8517` is a **docs-only child** of `f71745e` (`git show --stat` touches only
`docs/NEXUS/CHECKPOINT.json` and `EXECUTION_LOG.md`) and its tree contains all three NEX-029
product files; its run is 4/4 `completed/success` at `head_sha=e3b8517`. A green run over a
tree containing the code is evidence about that code.

**Negative control (run 2026-11-25T15:33Z, deferred from the NEX-029 slice):** demoting the
title from `CONFIDENTIAL` to `PUBLIC` turned **2 tests red** —
`test_confidential_title_is_withheld_and_named` and
`test_title_cannot_change_the_context_hash`. Restored byte-identical and re-verified green.
The tests bind the mechanism, not its shape.

**Migration:** none. **New dependencies:** none.

## NEX-030 — `ContextHash` is unforgeable by construction (closes R31)

**Status:** VERIFIED. **Commit:** `0727628`. **CI:** 4/4 `completed/success` at that exact
SHA, polled 2026-11-25T15:46Z.

`DecisionRequest.context_hash` was a plain `str`, so a caller could hand-assemble a digest
that did not correspond to the envelope it was sending. Asserting a hash asserts "these were
the inputs", so a caller-assembled value takes on the declassification decision ADR-012 gives
to `ContextEnvelope` alone.

`ContextHash` subclasses `str` — it serialises, compares and hashes as the plain hex digest in
JSON, logs and audit records, so it is a **construction-time guard, not a new wire format**.
Construction requires a module-private token held only by `ContextHash._mint`, and only
`ContextEnvelope.context_hash` calls it. A malformed digest is still rejected at mint time.
`_validate_context_hash` is now the type check: a `str` that merely looks like a digest is
exactly the hand-assembled hash the old docstring warned about, so it is refused.

**Stated limit, not overclaimed:** `str.__new__(ContextHash, …)` still bypasses the guard. The
goal is to make the mistake impossible to write by accident and the bypass deliberate and
visible — not to defeat a process already running inside this one. This is recorded in the
class docstring.

**Negative control:** disabling the `isinstance` check turns
`test_context_hash_must_be_minted_by_an_envelope` red. Restored byte-identical, re-verified.

**Remaining observation (not a code gap):** the envelope bounds (32 fields, 200 chars/field)
have still never met real **note text**; they have met real approval data.

**Migration:** none. **New dependencies:** none.

**Next action:** refresh PR #3 at the current head, update tracking issue #2, then pick the
next dependency-ready slice.

## NEX-031 — A record-derived value over the context cap is withheld, not fatal

**Status:** IMPLEMENTED, TESTED, VERIFIED locally. **Phase:** 5 (durable runtime) —
context minimisation hardening. **Priority:** P1 (security invariant, closes the
observation NEX-030 recorded).

**Objective.** Close the gap NEX-030 named: the envelope bounds (32 fields, 200
chars/field) had only ever met approval metadata — short titles and a capability
string. The real subject of an approval is a **note**, whose body may be
`MAX_CONTENT_BYTES` (64 KiB of UTF-8), roughly 300x the per-field cap.

**The defect.** `RecommendApprovalDecision._envelope` built its context with
`ContextEnvelope.from_fields`, which constructs every `ContextField` eagerly and
therefore *raises* when a value exceeds `MAX_FIELD_VALUE_LENGTH`. That is correct for
hand-authored references, where the caller is responsible for bounding input. It is
wrong for record-derived content: a note title is legal at the notes domain's
`MAX_TITLE_LENGTH`, so "the title is long" became "the advisory failed" — a denial of
a read the caller is entitled to. The two caps are independent constants that happen
to both be 200 today, which is exactly what hid the bug: a title at the domain maximum
sits *at* the cap, so a coincidence read like a guarantee.

**The fix.** New `ContextEnvelope.from_candidates` for record-derived values. An
over-cap value is **withheld instead of raised**: the field is never constructed (so
it cannot enter the digest, and the raw subject text is not left in memory one
refactor away from the canonical form), and its name is recorded in
`withheld_field_names` so the audit trail still says the input existed.

- `excluded_field_names` unions the two withholding reasons — above-ceiling
  classification and over-cap value — because an audit reader needs the same fact
  either way: *this input was present and not sent*.
- Invariants enforced at construction: a withheld name must still be a legal field
  name; a name may not be both included and withheld (contradictory statements about
  one input, with no way for a reader to choose); withheld names may not repeat.
- `_validate_field_name` was extracted so the withholding path can check a name
  *before* deciding whether the value fits, without duplicating the rule.
- `from_fields` is unchanged and still refuses over-cap values — the distinction is
  caller-knows-the-bound (fields) versus value-comes-from-a-record (candidates).

**Files.** `domain/agent/context.py` (+`from_candidates`, `withheld_field_names`,
`_validate_field_name`), `application/use_cases/approval_advisory.py` (builds
candidates, drops the `ContextField` import).

**Tests.**
- `tests/agent/test_context_envelope.py::TestWithholdingAnOverCapValue` — withheld and
  named; a withheld value never reaches the digest (two envelopes differing only in the
  over-cap value hash identically); ceiling + over-cap names union without shadowing;
  illegal withheld name rejected; included-and-withheld rejected.
- `tests/agent/test_approval_advisory.py::test_a_note_at_the_content_ceiling_still_produces_an_advisory`
  — the production-data check: a real stored note with a **64 KiB body** at the domain
  maximum still yields an advisory, the title is withheld by *classification* (it is
  CONFIDENTIAL, above the INTERNAL ceiling) rather than by length, the stored body is
  asserted to exceed the context cap, and the approval stays PENDING.
- `test_..._maximum_length_title...` — shrinks the context cap so the two caps
  diverge, proving the cap divergence directly rather than relying on today's
  coincidence.

**Load-bearing proof.** Reverting the fix made the max-title test fail with
`ValidationError: a context field value may be at most 50 characters` at
`context.py:133` — the exact denial-of-advisory the change removes. Restored and
re-verified green.

**Gates.** `ruff format` clean, `ruff check` clean, `mypy` clean (105 source files),
full `pytest` **850 passed, 4 skipped**.

**Migration:** none. **New dependencies:** none.

**Next action:** commit and push this slice, then refresh PR #3 and tracking issue #2.


---

## NEX-032 — Withholding is observable, not merely auditable

**Status:** VERIFIED locally (CI pending at `fe5eadc`). **Phase:** 5 (durable runtime) —
observability. **Priority:** P1 (closes R32).

**Objective.** NEX-031 made an over-cap record-derived value *withheld* rather than fatal,
which is the right failure direction — a legal note must not deny an advisory. But it made
withholding the quiet path: `excluded_field_names` recorded *that* an input was held back and
nothing recorded *why*. A caller reading an advisory could not tell a deliberate policy
exclusion from a value that did not fit, and those two mean opposite things operationally.

**The defect (R32).** One flat name list collapsed two distinct states:

- **ABOVE_CEILING** — the caller classified the value and the envelope's ceiling excluded it.
  The control working as designed. Nothing is wrong, nothing should change.
- **OVER_CAP** — the value exceeded `MAX_FIELD_VALUE_LENGTH`, so it could not be represented
  at all. Degradation: every advisory is computed on less context than the caller believes.

Only the second deserves attention, and the audit record made them indistinguishable. The
same shape as R20 (a record that cannot be read) and R24 (a note that reads as current
forever): a signal that exists but cannot be distinguished is not a signal.

**The fix.** `ContextEnvelope.withheld_fields` returns `(name, reason)` pairs;
`over_cap_field_names` exposes the subset an operator can watch without parsing reasons. An
input that is both over-cap and above the ceiling is reported **once**, as `OVER_CAP`, so
telemetry never double-counts one input. Names only — never values, so a withheld value stays
unreachable in memory. The reason travels end to end: `DecisionRequest` → `DecisionEvaluation`
→ advisory result → `ApprovalAdvisoryResponse.over_cap_context_fields`.

**Second defect found while testing.** `from_candidates` validated the classification only on
the under-cap path. The over-cap branch skipped the check entirely because the field was never
constructed, so a caller passing a bare string for an over-cap candidate was silently
accepted while the identical mistake under the cap raised. The path that *discards* the value
must not be a hole in the type check. `ContextField` still accepts `RESTRICTED` and lets the
ceiling withhold it — that is its documented "exclusion is recorded, not silent" contract, and
an earlier draft of this slice wrongly inverted it.

**Invariant enforced.** `over_cap_context_fields` must be a subset of
`excluded_context_fields` on `DecisionRequest`. Two records that disagree about which inputs
were held back are worse than one, so the relation is checked at construction.

**Tests.** 10 new cases: per-field reasons; the once-only rule for a doubly-withheld field; no
value in the record; the validation-bypass regression; the subset invariant; and the API
boundary asserting a ceiling-withheld title appears in `excluded_context_fields` but not in
`over_cap_context_fields`. The API test creates a **real note** — a fabricated `resource_ref`
would make the ceiling check vacuous and let the test pass while proving nothing.

**Gates.** `ruff format` clean, `ruff check` clean, `mypy` clean (105 source files), full
`pytest` **860 passed, 4 skipped** (baseline 850).

**Migration:** none. **New dependencies:** none.

**Remaining follow-up.** No metric aggregates over-cap withholdings across advisories. The
per-response field makes a thin advisory visible; turning that into an alert is a separate
monitoring task.


---

## NEX-034 — A credential is recognised by shape, not by an exact key spelling

**Status:** IMPLEMENTED + PUSHED at `e386e02` (CI in progress at time of writing).
**Phase:** 5 (durable runtime) — observability / security hardening.
**Priority:** P1. **Closes:** the redaction hole NEX-033 opened by wiring
`redact()` onto the formatting path.

**Objective.** Make the log redactor enforce the rule it advertises: a
credential must not reach a log line, whatever the caller happened to name it.

### The defect (two holes in one function)

`redact()` in `bootstrap/logging_config.py` decided whether a key was sensitive
by membership in a set of eleven exact strings. Anything spelled differently
was treated as safe:

- `client_secret`, `private_key`, `x-api-key`, `accessToken`,
  `session_cookie`, `Authorization` — all emitted in full.

Before NEX-033 this was latent: `redact()` had zero callers, so the module
docstring's "sensitive data must never be logged" was an unenforced aspiration.
NEX-033 put `redact()` on the formatting path *to enforce that rule* — which
converted a dormant gap into an active false claim of protection. A matcher
that covers the spellings someone thought to list is not a control; it is a
control-shaped object.

Second hole, found while testing: `_redact_value` inspected the key first but
only replaced **scalars**. A sequence under a sensitive key leaked wholesale:
`{"tokens": ["raw-secret-1", "raw-secret-2"]}` came out untouched.

### Why this is the same shape as the bug NEX-033 fixed

NEX-033 removed a six-key log **allowlist** — "emit what I listed, drop the
rest". This is the mirror image: an eleven-key **denylist** — "redact what I
listed, pass the rest". Both are enumerate-what-you-know. Both fail silently on
the case nobody enumerated. Fixing one layer and leaving the other is how a
class of defect survives a fix.

### The fix

`_key_words` segments a key across separators and camelCase, so `accessToken`,
`access_token` and `ACCESS-TOKEN` reach one verdict. `_is_sensitive_key`
then decides on words:

- a credential word in any position (`client_secret`, `accessToken`, `bearer`);
- a plural of one (`tokens`, `credentials`, `cookies`);
- a compound where neither word is sensitive alone (`private_key`, `api_key`,
  `signing_key`).

`key` on its own stays ordinary, so `idempotency_key` and `partition_key`
survive. `content` is sensitive but its descriptors (`content_type`,
`content_length`) are not.

A sequence under a sensitive key is replaced **whole**, not element by element:
per-element replacement would still leak the length and the ordering of a
secret set.

### Redaction stays surgical — deliberately

Over-redacting is *not* the safe default in this position. A naive substring
matcher would swallow `idempotency_key`, `content_type` and `duration_ms`, and
in doing so blind the withholding counter NEX-033 added — the log line exists
precisely to make a routinely-thin advisory visible. A redactor that hides the
telemetry is a different outage. A test pins the fields that must survive.

### Tests

4 new cases in `tests/bootstrap/test_logging_config.py`:

1. the six credential spellings the old matcher never knew;
2. a plural credential key;
3. a sequence under a sensitive key replaced whole;
4. the ordinary telemetry fields that must survive.

**Negative control, run on disk:** restoring the exact-match matcher turns
**3 of the 4 red**. The over-redaction test passes on both versions — correctly,
because it guards a property the old matcher already had, and a test that
asserts nothing new would be a false green.

### Gates

`ruff format` clean (164 files), `ruff check` clean, `mypy` clean (107 source
files), `pytest` **873 → 877 passed / 4 skipped**. The 873 baseline was measured
by stashing the change rather than assumed.

### Migration / dependencies

None. Stdlib only.

### Residual

`_SENSITIVE_WORD_PAIRS` and `_SENSITIVE_WORDS` are still finite lists, so a
credential named with a word on neither list would pass. The difference from the
predecessor is direction: the matcher now fails on *vocabulary*, not on
*spelling*, so a new vendor's `client_secret` is caught without anyone editing a
set. Widening vocabulary is cheap and additive; the structural hole — one
spelling per entry — is closed.

---

## NEX-035 — The adapter was policed on its answer and trusted on its name (closes R30)

**Phase:** 5 (durable runtime) / M2.6 decisioning. **Priority:** P1.
**Status:** VERIFIED — 4/4 `completed/success` at `12f6fa9`, polled 2026-11-25T18:14Z.

### Objective

Close R30: every bound in the decision contract had only ever met the stub adapter, which
always answers at 0.1 confidence with a short rationale. Build the conformance suite a real
vendor adapter must pass, so the first real adapter is not the first genuine test.

### The defect the suite found

`EvaluateDecision` guarded everything a provider *answered* — the 400-character rationale
bound, the `[0.0, 1.0]` confidence range, the caller-supplied candidate choice set — and
guarded nothing about what a provider *called itself*. A `DecisionProvider` implements both
`decide` and a `name` property. Only `decide` was treated as third-party code, although the
name reaches audit records, structured log fields, and API responses.

Five probes were run **before any fix**, so each finding is an observation:

| Probe | Observed |
| --- | --- |
| Over-long name | `provider_name` accepted 580 chars; `MAX_PROVIDER_NAME_LENGTH` is 80 and was never read |
| Over-long rationale | `ValidationError` from inside `decide` surfaced as `PROVIDER_ERROR` |
| Raising `name` property | `RuntimeError` propagated out of `run()` |
| Non-`str` name | recorded verbatim as `b'bytes-not-str'` |
| Name read count | once per run — correct, now pinned |

### The fix

- `domain/agent/decisioning.py` — `validate_provider_name` enforces the bound it always
  declared, plus an identifier shape (`^[a-z0-9][a-z0-9._-]*$`). It raises rather than
  coercing: a truncated name would record a *different* provider than the one that answered.
- `application/use_cases/decisioning.py` — `_safe_provider_name` reads the name once, before
  anything else can fail, and degrades to `UNIDENTIFIED_PROVIDER`. Reading it lazily inside
  `_evaluation` would also have read it on the disabled path, where the provider was never
  called. A `ValidationError` through `decide` is now `MALFORMED_OUTPUT`, not
  `PROVIDER_ERROR`.

### Acceptance criteria

- [x] A conformance helper exists that a real vendor adapter is run through.
- [x] Every contract bound is exercised with a payload that violates it.
- [x] The suite is proven to *reject* violations, not merely to accept the stub.
- [x] A provider name is bounded and identifier-shaped, or recorded as `unidentified`.
- [x] Third-party code cannot raise out of `run()`.

### Tests

`tests/agent/test_decision_provider_conformance.py` — 30 tests. Meta-tests drive a
`HostileProvider` that breaks one rule at a time and assert the suite raises for each, so the
conformance check has been shown to fail. Full suite 881 → **911 passed / 4 skipped**; the
881 baseline was measured by re-running with the new file ignored, not inferred.

### Migration / dependencies

None. No schema change, no new dependency.

### Residual

The suite proves a *conforming* adapter behaves. It cannot prove a vendor's model produces
good judgments — that is an evaluation question, not a contract one.

---

## NEX-037 — The count existed, the aggregate did not (closes R34)

**Phase:** 5 (durable runtime) / M2.6 decisioning. **Priority:** P2.
**Status:** VERIFIED on LOCAL evidence — ruff/mypy clean, pytest 920 passed / 4 skipped.
CI on `5c0575b` had not concluded at 2026-11-25T18:54Z; re-poll before calling it CI-verified.

### Objective

Close R34. `WithholdingRecorder` has counted every advisory and every over-cap withholding
since NEX-033, and `RecommendApprovalDecision` logs the running total. Nothing exports it, so
"how often are advisories thinner than they should be" was answerable only by reading
individual responses. A log line is not a number an operator can alert on.

### The design question was access, not exposure

The aggregate spans every tenant. It is not a view of the caller's own data, so handing it to
any authenticated caller would let one user infer another user's advisory volume — a
cross-tenant side channel that gets more informative the more precisely it is polled. That
ruled out a normal authenticated route, and ruled out reusing `AuthorizationProvider`, whose
job is note *content* access. Platform operations must never be reachable by content access
alone.

### What shipped

| Piece | Where |
| --- | --- |
| `GetWithholdingReport` (allowlist check + snapshot) | `application/use_cases/withholding_report.py` |
| `GET /api/v1/ops/withholding-report` | `presentation/api/routers/ops.py` |
| `OPS_OPERATOR_USER_IDS` (CSV, default empty) | `bootstrap/config.py` |
| Recorder wiring for the read side | `bootstrap/container.py` |
| Response schema | `presentation/schemas.py` |

- **Empty allowlist denies everyone.** The grant is not a role inherited by being logged in,
  so an unconfigured deployment exposes nothing — the right failure direction for a setting
  nobody has considered yet.
- **404, not 403, for a non-operator.** `403` would confirm a platform-operations surface
  exists at that path, which is itself a small disclosure to someone probing the API.
- **Counts only** — `advisories_total`, `over_cap_advisories_total`, `over_cap_fields_total`,
  `over_cap_rate`. No field name, value, note id or user id. That is what makes a
  process-wide number disclosable at all: it counts *withholding*, never *what was withheld*.
- **`over_cap_rate` sits beside its numerator and denominator.** `0.0` over three advisories
  and `0.0` over thirty thousand are different claims; a bare rate conflates them.
- **Malformed operator ids fail at startup.** The gate compares `UserId` values, so a typo
  would silently match nobody and look like a permissions bug for the one operator it was
  meant to serve.

### Tests

`tests/application/test_withholding_report.py` (3) and `tests/api/test_ops_withholding_api.py`
(4) — 7 total. Both layers are pinned because "the use case raises `ResourceNotFound`" and
"the router does not remap it" are separate claims, and only an HTTP-level test covers the
second. Cases: operator reads totals; empty allowlist denies; a listed other user is refused
as not-found; the route/schema/container agree; a non-operator gets 404 rather than 403.

### Migration / dependencies

None. No schema change, no new dependency (stdlib `uuid`, existing FastAPI/pydantic).

### Residual

**R36** — PARTIALLY CLOSED (run `NEX-2026-11-27-47`, commit `1eb1d3b`). The *record* half is
built: the duplicated per-use-case allowlist check is now one `OperatorGate`, and every granted
read of an operational view appends an `OperatorAccessEntry` naming caller, operation and the
gate's clock time. Refusal happens before recording, so an entry is evidence of authorisation.
Still open, deliberately: the allowlist is a static environment variable with **no expiry, no
per-operation scoping and no revocation**. Those belong to the scoped-capability-grant slice —
this slice made the use of a grant visible, which is the prerequisite for rotating one. The
adapter is process-local and bounded, so it answers "who read this recently", not "who ever did".

---

## NEX-038 — Re-poll NEX-037's CI, then close its delivery debt

**Phase:** 5. **Priority:** P0 for the first step (a VERIFIED claim rests on unpolled CI).
**Status:** PLANNED. **Dependencies:** none.

### Objective

NEX-037 shipped on local evidence with CI unconcluded. Finish the verification and the
delivery NEX-037 could not fit into its window.

### Scope

1. Poll `5c0575b` until every check run concludes. Record the result with a timestamp in
   `CHECKPOINT.json` `ci_status`. If it is red, fix it — do not re-label it.
2. PATCH PR #3's body with a NEX-037 section (objective, the access-control reasoning, the
   seven tests, R34 closed, R36 opened). Read the body back and assert on specific substrings
   and byte-equality.
3. POST one issue #2 comment for NEX-037. **Lists, never a markdown table** — a table in an
   issue comment returns HTTP 500 on this repository.

### Acceptance criteria

- [ ] `ci_status.product_slice_result` names a conclusion, not a `queued`/`in_progress` state.
- [ ] PR #3 body carries a NEX-037 section, verified byte-equal after PATCH.
- [ ] Issue #2 has a NEX-037 comment, verified byte-equal after POST.
- [ ] `STATE.md` and `CHECKPOINT.json` agree with GitHub on the PR head.

### Why this ranks first

A task marked VERIFIED on local evidence is honest but provisional. The longer the gap between
the claim and the poll, the more likely the claim is read later as "CI passed" — which is the
exact class of stale note R24 exists to prevent. Closing it is cheap; leaving it open makes the
record unreliable.

### Then

After delivery is current, the highest-value dependency-ready work is either the **R33
residual** (the log redactor's vocabulary is finite — additive, low risk) or the **first real
`DecisionProvider` adapter**, which NEX-035's conformance suite now exists to police. The
adapter is the larger unlock: it turns decisioning from a stub into a capability, and the
suite means the adapter's first real test is not its first genuine test. Verify the premise
against the code before starting — the last five `next_action` fields each needed correcting.

## NEX-039 — The adapter existed only as a stub

**Phase:** 5. **Priority:** P1 (the largest dependency-ready unlock in decisioning).
**Status:** VERIFIED — product commit `8e670a6`, CI 4/4 `completed/success` at that exact
SHA (polled 2026-11-25T19:32Z). **Dependencies:** NEX-035 (conformance suite).

### Objective

Ship the first real `DecisionProvider` so decisioning stops being a capability the platform
can exercise but never use.

### Scope

- `HttpDecisionProvider` (`infrastructure/decisioning/http_provider.py`), HTTPS only.
- Provider selection in the container; `Settings` validation of the same rules.
- 51 adapter tests + 13 config/wiring tests, including the NEX-035 conformance harness.

### Acceptance criteria

- [x] Request body carries only `kind` and sorted `candidates` — no context hash, no excluded
      field names, no correlation id.
- [x] Every malformed vendor answer becomes a contract-legal abstention, never an exception.
- [x] Non-https endpoint or unbounded provider name raises at construction.
- [x] Vendor error bodies are discarded, never parsed into the audit trail.
- [x] Passes `assert_provider_conformance` (`tests/agent/test_http_decision_provider.py:404`).
- [x] Negative control proves the order-independence invariant is genuinely tested.

### Recorded limits (R37)

**Closed in run `NEX-2026-11-27-43`.** The 64 KiB bound was enforced after httpx had
already buffered the body, so it bounded parsing and not memory. It is now enforced *while
reading*: the adapter streams the response and stops the instant the cap is passed, and a
peer that declares an oversized `Content-Length` is refused before a single body byte is
read. Pinned by `tests/agent/test_http_decision_provider.py` (six new tests, including a
chunk-counting body that proves the stream is abandoned rather than drained). The rationale
is still truncated rather than treated as fatal — that remains a deliberate choice, not a
gap. The cap still bounds *size*, not *duration*; that limit stands and is recorded in the
module docstring.

### Next

NEX-040 is now answered — see below. The next decisioning slice is NEX-042.

## NEX-040 (run `NEX-2026-11-25-42`) — VERIFIED: no default path reaches a live vendor

**Question.** The `DecisionProvider` port has two adapters and the container selects one.
Does anything, in a default or production configuration, actually route a request to a real
vendor? The previous run's `next_action` asked for this to be checked in the code rather than
assumed, because several earlier `next_action` fields were wrong.

**Answer: no.** Four independent lines of evidence, all read from this tree.

1. **Nothing configures an endpoint.** `grep -rn DECISIONING` over every deploy artifact —
   `docker-compose*.yml`, `Dockerfile`, `docker/`, `.env.example`, `.env.docker.example`,
   `*.toml`, `*.sh` — returns zero matches. The variables are documented only in
   `bootstrap/config.py`. There is no `.env` in the tree.

2. **The defaults are inert, measured not assumed.** Constructing `Settings()` with no
   environment yields `decisioning_enabled=False`, `decisioning_http_endpoint=None`,
   `decisioning_min_confidence=0.5`.

3. **No endpoint selects the stub, even if the flag were flipped on.**
   `Container._build_decision_provider` returns `StubDecisionProvider()` when the endpoint is
   `None`. The stub answers a choice question with the lexicographically first candidate and a
   score question with `0.5`, both at confidence `0.1`. `0.1 < 0.5`, so the default threshold
   downgrades every stub answer to an abstention. Two independent gates, either sufficient.

4. **One production route reaches the judgment path at all.** `GET
   /api/v1/agents/approvals/{id}/advisory` is the only route into `EvaluateDecision`; the
   route's own docstring says so. `EvaluateDecision.evaluate` short-circuits to
   `AbstentionReason.DISABLED` before touching the provider when `enabled` is false. So with
   the shipped configuration the advisory route returns an abstention and never opens a socket.

**This is already pinned by tests, and the pin is not vacuous.**
`tests/bootstrap/test_decision_provider_selection.py` (13 tests) asserts the `None` default,
the blank-endpoint handling, the https-only validation, stub selection with no endpoint even
when `enabled=True`, and `provider_name == "vendor"` when an endpoint is set. Negative control
run this session: replacing `if settings.decisioning_http_endpoint is None:` with `if True:`
in the container turned `test_a_configured_endpoint_selects_the_http_adapter` RED
(`1 failed, 12 passed`). `container.py` restored from `/tmp/container.bak`, guard re-grepped
(1 match), suite back to `13 passed`, `git status --porcelain` empty.

**What this does and does not establish.** It establishes that the shipped platform is
fail-closed with respect to third-party judgment: no configuration in this repository can
cause a vendor request, and the code path that would send one requires an operator to set an
endpoint explicitly. It does **not** establish that the `HttpDecisionProvider` is correct when
an operator does configure one — its transport, response-bound and timeout behaviour are
NEX-039's subject (R37) and were not re-examined here. It also does not establish that a
future default cannot change: the evidence is a property of this commit, not of the design.
The tests are what keep it true; they are the reason this section can say "verified" instead
of "appears to be".

**No code change was warranted.** The claim under test was already true and already tested.
Adding a redundant assertion would have been noise; the honest outcome of a verification task
is sometimes a documented finding rather than a diff.

## NEX-041 (run `NEX-2026-11-25-41`) — the coordination substrate had no compare-and-swap

### Why this slice, ahead of NEX-040

NEX-040 was the next decisioning slice and was unstarted when this work began; nothing here
changed it. It was subsequently verified in run `NEX-2026-11-25-42` with no code change (see
the section above). This slice was taken first because it is a **hazard to every run**,
including the one that would implement NEX-040.

NEXUS coordinates through plain files on one git branch. A git branch is last-writer-wins at
file level. Two runs that both read `CHECKPOINT.json`, both edit it, and both push produce a
tree containing one of the two edits, chosen by push order rather than intent. **Nothing
errors.** The losing run reports success and its state update is simply gone.

That is not hypothetical in this repository. The execution log records runs whose opening
reconciliation found the local branch commits behind the remote, including `NEX-2026-11-25-37`
which had to fast-forward four commits before it could begin, and this run itself opened on a
shallow local `f4388fc` while the remote was at `49814cc` — three commits of NEX-039 work it
had never seen. A substrate whose failure mode is silent data loss cannot be made reliable by
being careful, because the failure is invisible to the agent causing it.

### What shipped

`tools/nexus_coord`, built on the only server-side compare-and-swap available without new
infrastructure: git refs.

| Ref | Purpose |
| --- | --- |
| `refs/locks/nexus/lease` | single-writer lease; holder, expiry, run id in the commit message |
| `refs/claims/nexus/<task>` | per-task claim; creating a ref is atomic, so exactly one agent wins |

Every mutation is a compare-and-swap the remote enforces:

- acquire a free lock — plain push; a second writer is rejected non-fast-forward
- acquire an expired lock, or renew — `--force-with-lease=<observed sha>`; refused if anyone
  moved it in between
- release — delete guarded by the sha we hold

Nothing uses `--force`, and nothing resets or cleans. `--force-with-lease` is the opposite of
a force push: it *refuses* to write when the remote has moved since the caller looked.

### The two design decisions that matter

**The lease is bounded, not permanent.** A run that dies must not block the programme until a
human notices. Expiry travels inside the lease, so any agent can decide — without trusting the
holder — whether the lock is live. Expiry is deliberately not treated as correctness: a holder
whose lease expires mid-run can be superseded, so it must renew inside the window and must stop
writing if renewal fails. TTL is 45 minutes against a 30-minute run cap, so a healthy run never
loses it but a crashed one frees it quickly.

**Claims are reported stale, never taken over.** A task may legitimately take hours, so a claim
has no expiry. One older than `STALE_AFTER_SECONDS` is *reported* as stale so a human or an
explicit takeover can clear it. Silently reassigning long-running work would be worse than
asking, because the original holder may still be alive and about to push.

### A gate-wiring defect caught before it shipped

The tests were originally at `tools/tests/`. `pyproject.toml` sets `testpaths = ["tests"]`, so
CI would **never have run them** — 32 tests passing locally and zero running in CI, with the
suite reporting green either way. They now live at `tests/coordination/`. `ruff` `src` and the
`mypy` `packages` list also gained `tools` and `nexus_coord`, so the package is linted and
strict-typed in CI rather than escaping both (mypy 109 → 115 source files).

### Validation

- `ruff format --check` clean (240 files), `ruff check` clean, `mypy` strict clean over 115 files.
- `pytest`: **1016 passed / 4 skipped**. Baseline **measured**, not inferred — 984 with
  `tests/coordination` ignored, plus exactly 32 in that directory.
- **Negative control:** replacing the live-lease guard in `acquire()` with `if False:` turns
  `test_second_agent_is_refused_while_the_first_holds_it` red. File restored from backup and
  re-verified green, with `ruff` and `mypy` re-run afterwards.
- **CI: 4/4 `completed/success` at `6e7141e`**, every check run reporting `head_sha` `6e7141e`
  (Backend and Frontend, one pair per push and pull_request event), polled 2026-11-25T20:0xZ.

### Recorded limits

Stated in ADR-013 rather than left for a reader to discover:

- The primitive coordinates agents **that use it**. It cannot stop a run that ignores the lease
  from pushing anyway — enforcement is convention, not server policy. This is why it is a tool,
  not a gate.
- Ref names are repository-global, so the lock is per-repository, not per-branch.
- Expiry is compared against the local clock; a badly skewed host can misjudge a live lease.
  The TTL absorbs ordinary skew, not a wrong clock.
- `commit-tree` leaves dangling commits; they are unreachable and garbage-collected.

### Next

**NEX-040, unchanged:** verify whether any production route actually reaches a configured live
vendor. The adapter exists and the container selects one, but "nothing configures one by
default" is a claim to check in the code before building on it.

---

## R39 — The coordination lease could be acquired but never released

| Field | Value |
| --- | --- |
| Phase | 0 (process substrate) |
| Priority | P1 — it gated every run's ability to coordinate |
| Status | **VERIFIED / COMMITTED / PUSHED / CI PASSED** |
| Type | Defect, found in `tools/nexus_coord` |
| Commit | `64759f4` |
| Closed by | run `NEX-2026-11-27-46` |

**Symptom.** After a run acquired the coordination lease, `lease release` in that same run
exited 2 with `lease held by another agent`, naming the run's own holder. The next run then
opened with the lease still held, `holder_liveness: dead`, `mine: false`, and was locked out
of coordination writes for the remainder of the TTL.

**Root cause.** `release` and `renew` decide "is this mine?" by comparing the live lease's
`holder` with the caller's holder. The default holder was `host-pid`, and every `nexus_coord`
invocation is a fresh process — so the pid recorded at acquire time is never the pid doing the
release. `mine` is false on the very next call. A lease was therefore single-use per
acquisition, and every successful acquire blocked the following run.

**Why this also closes R38.** R38 was recorded as "a stale lease held by a dead process". It
was the predictable output of a run that acquired and could not hand back. The two entries were
one defect.

**Fix.** The holder must be identical across every process of one run and different between two
concurrent runs on one host. A pid satisfies the second and fails the first; the run id
satisfies both but is a briefing value passed by hand, which would make coordination depend on
a human remembering a flag. The POSIX **session id** satisfies both: one terminal session drives
a run, every process it spawns inherits it, and overlapping runs get different sessions.
`default_holder()` resolves `NEXUS_HOLDER`, then `host-s<sid>`, then `host-<pid>` only where
`os.getsid` is unavailable or raises.

**Acceptance criteria — all met**

- A process can release a lease that another process of the same session acquired.
- A process can renew it, and `status` reports `mine: true` from a different process.
- A different identity is still refused, so exclusion did not widen with self-recognition.
- Liveness remains reporting-only: expiry alone gates acquisition, so a wrong liveness answer
  can never cause a false takeover.

**Required tests**

- `tests/coordination/test_holder_identity.py` — two real subprocesses must report the same
  holder. This is the only shape that can express cross-process identity; two calls in one
  process share a pid and pass under the broken implementation.
- Negative control: reverting `default_holder()` to `host-pid` must turn that test red.

**Validation.** ruff format 249 files clean, ruff check clean, mypy 117 source files clean,
`pytest` 1068 passed / 4 skipped / 0 failed. Five-process CLI acceptance run on a scratch repo
with a bare origin: acquire -> mine=true -> renew -> release -> held=false. CI 4/4 success at
`64759f4`.

**Residual risk, recorded not hidden.** The identity is only as stable as the session; a harness
that spawns each invocation under its own session (via `setsid`) reproduces the original defect.
That is what `NEXUS_HOLDER` is for. A recycled session-leader pid can make `holder_liveness`
report `alive` for a dead run — reporting inaccuracy only, because acquisition is gated on
expiry, never on liveness.

**Not verified in the field.** This run did not acquire the shared lease: a prior run's legacy
`host-pid` lease (`Andrews-Mac-mini.local-95179`, expiring `1795775517`) still held the ref, and
the old code cannot release it. The fix was therefore proven on a scratch repo. The next run
should confirm acquire -> `mine: true` -> release against the real ref after that expiry.

## NEX-046 (run `NEX-2026-11-27-49`) — R40 CLOSED: a grant that outlives the process

**Status**: TESTED (local gates green) + PUSHED at `0e86304`; CI not yet polled;
issue #2 comment and PR #3 section still owed.

**Phase**: 5 (durable runtime) · **Priority**: P1 · **Dependencies**: NEX-045 (closed)
· **Risk closed**: R40

**Objective.** NEX-045 made an operator permission a record with a holder and a
lifetime, but the only adapter was a process-local dict: a restart forgot every
bounded grant and each worker held its own copy. It failed closed, so it was never a
security hole — but an invisible grant cannot be audited, which is the question
NEX-045 exists to answer.

**Scope.** `SqlAlchemyOperatorGrantRepository` behind the unchanged
`OperatorGrantRepository` port; `OperatorGrantRow`; migration `f7b3c0d95a12`;
`replace_configured` added to the port; container wiring; 23 persistence tests.

**Acceptance criteria — met.**

- `OperatorGate` and `ListOperatorGrants` compile and pass unchanged: the port was
  the seam and the store swapped behind it.
- A second adapter over the same database file sees a grant the first one wrote.
- `active_grants` order is stable, by surrogate `id`, never by `granted_at` (two
  grants in one seed share a timestamp).
- `add` is idempotent on `(user_id, scope, granted_at)`: re-issuing extends a
  deadline instead of duplicating.
- Re-running the seed does not grow the operator set, and does not erase a
  human-issued grant. This is `replace_configured`, and it exists *because* the
  store is durable: an appending seed would add the whole configuration per restart.
- Expiry is evaluated by `OperatorGrant.is_active` in exactly one place, not in SQL.
- Single Alembic head, correct parent, no model drift.

**Required tests** — `tests/persistence/test_operator_grants.py` (23): restart,
cross-adapter visibility, insertion order, both expiry boundaries, `not_before`,
per-user scoping, uniqueness, re-issue extending a deadline, `replace_configured`
idempotency and its refusal to touch human grants, empty-scope CHECK, user FK,
cascade delete, table presence.

**Validation.** ruff format 268 files clean, ruff check clean, mypy 126 source files
clean, `pytest` 1172 passed / 4 skipped / 0 failed. Pushed `c9bb7d6..0e86304`,
`ls-remote`-verified.

**Recorded limits, not hidden.** The `length(scope) > 0` CHECK and the `UTCDateTime`
round trip have only met SQLite in this window; four PostgreSQL-marked tests would
exercise both and were skipped locally. CI at `0e86304` was not polled.

**Next.** NEX-047 — poll CI at `0e86304`, then clear delivery debt: issue #2 comment
and PR #3 section for NEX-046 (lists, not tables, for issue comments on this repo).

## NEX-047 (run `NEX-2026-11-27-50`) — M3.1: typed policy AST with immutable published versions

**Status**: IMPLEMENTED + TESTED (local gates green) + PUSHED at `379a98a`; CI not yet
polled; issue #2 comment and PR #3 section owed.

**Phase**: 3 (governance) · **Priority**: P1 · **Milestone**: M3.1 · **Dependencies**:
M1.2 (complete) · **Complexity**: medium · **Risk opened**: none

**Objective.** The security architecture requires a declarative policy model with an
immutable published release and explicit conflict semantics. Before this slice the
platform had authorization rules scattered through use cases and no way to express a
policy, version it, or answer "what was in force when this decision was made".

**Scope.** `domain/governance/policy.py` (the AST, `PolicyRule`, `PolicyVersion`,
`evaluate`), `application/ports/policy_versions.py`,
`application/use_cases/policy_lifecycle.py` (draft, publish, rollback, history),
`infrastructure/governance/policy_versions.py` (in-memory adapter), and 56 tests.

**Acceptance criteria — met.**

- The AST is closed and typed: four node types over a fixed operator vocabulary; no
  `eval`, no fifth node a caller can invent.
- Type combinations are checked at construction: a numeric operator needs a NUMBER
  attribute, a `bool` is not an `int`, a NUMBER attribute refuses a `str` value.
- A wrong-typed value at evaluation raises rather than denying, because a silent deny
  would hide a data defect in the one place a reviewer most needs to see it.
- A missing attribute never satisfies a condition, under every operator including
  `!=`. Pinned by a parametrised test, because `classification != secret` passing an
  unclassified resource is the failure mode that matters.
- Conflict order is stated once and tested: explicit deny overrides allow, an empty or
  non-matching policy set denies, and rule order is irrelevant.
- `DRAFT → PUBLISHED → RETIRED` is the only transition path; `publish()` and
  `retire()` return new values; a PUBLISHED version cannot be constructed without a
  publisher and timestamp, and a DRAFT cannot carry them.
- The store assigns version numbers, so two authors cannot both claim v4.
- Publishing retires the predecessor in the same operation, so exactly one version per
  policy id is ever in force; the retire is written first so a crash leaves nothing
  published (deny) rather than two published versions.
- Rollback is a new release, not a rewrite: it drafts the old rules under the next
  number, so the rollback itself is recorded and the originals stay readable.
- Timestamps come from the injected `Clock`, never the wall clock (R29).

**Required tests** — `tests/domain/test_governance_policy.py` (34) and
`tests/application/test_policy_lifecycle.py` (22): type refusals, missing-attribute
non-satisfaction across four operators, deny-overrides in both orders, empty-set deny,
composite trees, draft-not-evaluable, version immutability and lifecycle refusals,
duplicate rule ids, bounded rationale, version-number assignment, predecessor
retirement, rollback-as-new-release, rollback-to-draft refusal, deterministic
`all_published` ordering.

**Residual — stated, not hidden.**

- **Approve is absent.** The full lifecycle includes an approval step. It depends on
  separation of duties (M3.3), which is not built. An approval gate that always passes
  would be a policy surface that looks governed and is not, so this slice records the
  publisher and leaves the requirement to the slice that can enforce it.
- **The adapter is in-memory.** A restart forgets every version, including published
  ones. `name` reports `"in-memory"` so a reader cannot mistake it for durable. The
  durable adapter belongs behind this same port and is the natural next governance
  slice.
- **No HTTP surface yet.** There is no route to draft or publish a policy. This is a
  domain + application slice; the API and the M4.3 policy UI come later, and neither
  should reimplement the lifecycle.

## NEX-048 — a durable `PolicyVersionRepository` behind the unchanged port

**Phase** 3 — governance, audit, Jev integration · **Priority** P1 (foundational) ·
**Status** **PUSHED** at `810d1dc` (IMPLEMENTED, TESTED, COMMITTED, PUSHED; CI for that
commit **not yet polled**; delivery to issue #2 / PR #3 outstanding) · **Complexity**
medium · **Depends on** NEX-047 (VERIFIED at `379a98a`)

> Implemented as `infrastructure/governance/policy_versions_db.py` rather than the
> `policy_versions.py` named below: that filename already holds NEX-047's in-memory
> adapter, which stays for tests. The port is unchanged, as required.

### Objective

Make published policy versions survive a restart. Today `InMemoryPolicyVersionRepository`
reports `name == "in-memory"` and forgets every version on restart, including published
ones — so a restart silently empties the policy set, and an empty policy set denies
everything. That is fail-closed, which is the right failure, but it is a live operational
gap rather than a design choice. NEX-048 replaces the adapter, **not** the lifecycle.

### Scope

- `infrastructure/governance/policy_versions.py` — a SQLAlchemy adapter implementing the
  existing `PolicyVersionRepository` port. **The port does not change**, so
  `PolicyLifecycle` and every caller stay untouched. If the port has to change to make
  storage work, stop and record why: that is a design signal, not a detail.
- A model plus one Alembic migration. Serialise the AST to JSON, and **re-validate it on
  read** — a stored policy is untrusted input on the way back in, and a row written by an
  older version of the code must not be able to reintroduce a node type the current
  validator refuses.
- Two storage-level invariants, enforced in the schema and not only in the use case:
  - `UNIQUE (policy_id, version_number)` — two authors cannot both claim v4 even if a
    future caller bypasses the store's numbering.
  - **At most one `PUBLISHED` row per `policy_id`** — a partial unique index on
    `(policy_id) WHERE status = 'PUBLISHED'`. The use case already retires the predecessor
    before publishing; this makes the invariant true even if a future caller forgets.
- Keep retire-before-publish ordering in the adapter, so a crash between the two writes
  still leaves nothing published rather than two versions in force.

### Acceptance criteria

- Every existing `tests/application/test_policy_lifecycle.py` test passes against the
  durable adapter with no change to the use case or the port.
- A new conformance suite runs the *same* contract tests against both adapters, so the
  in-memory adapter stays a legitimate fast double and cannot drift from the real one.
  This mirrors the adapter conformance approach NEX-035 established.
- Migration is idempotent under `alembic upgrade head` run twice, and downgrades cleanly.
- Two concurrent publishes of the same policy leave exactly one `PUBLISHED` row. This must
  be a real concurrency test against the database, not a mock.
- A row whose stored AST JSON is corrupt or names an unknown node type is refused on read
  with a clear error, never silently skipped and never returned as valid.
- `policy_versions.name` reports the storage backend truthfully, as the in-memory adapter
  does.

### Required tests

Adapter conformance (shared contract, both adapters), unique-version-constraint violation,
partial-unique-index violation on double publish, corrupt/unknown-AST-on-read refusal,
migration upgrade/downgrade idempotency, and the full existing lifecycle suite re-run
against the durable adapter.

### Security considerations

- The partial unique index is the enforcement; the use case is the caller. Do not rely on
  application ordering alone for an invariant an auditor will ask about.
- Re-validate stored ASTs on read: persistence is a trust boundary, and this one holds
  attacker-influenced data if a policy author is ever compromised.
- Fail closed on a read error. A policy that cannot be parsed must not become "no
  restriction" — it must deny.
- Do not add a write path that lets a caller set `status = 'PUBLISHED'` directly; publish
  stays a use-case operation.

### Migration implications

One new table. No existing table changes, so no data backfill and no destructive step. The
in-memory adapter remains available for tests.

### Observability

Log a warning, with the policy id and version, when a stored AST fails re-validation —
that is a data-integrity event an operator needs to see, and it should not be a silent deny.

### Rollback

Revert the adapter wiring to the in-memory implementation. No caller changes, because the
port is unchanged.

### Next

**NEX-049** — the HTTP surface for drafting and publishing a policy version, gated on an
operator scope, which is the first point at which this governance model becomes usable
outside tests. **NEX-050** — separation of duties (M3.3), which is the precondition for
ever adding an `approve` step to the lifecycle.

**Delivery state.** NEX-046 and NEX-047 delivery debt is **CLEAR** as of run
`NEX-2026-11-27-50`: CI 4/4 `completed/success` at `379a98a`, issue #2 comment
`7219198697`, PR #3 section appended. NEX-048 starts with no debt outstanding.

## NEX-056 (run `NEX-2026-11-27-57`) — the `rate-limit` obligation got an enforcement adapter

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Priority | P1 |
| Status | **VERIFIED** — CI 4/4 `completed/success` at `e7c7315` (polled 2026-11-27T18:14Z) |
| Dependencies | NEX-053 (obligations as a separate axis), NEX-054 (redaction adapter seam) |

**Objective:** make the `rate-limit` obligation enforceable, so a policy can express a volume
bound on one capability instead of refusing every call.

**Architecture refs:** `ENTERPRISE_SECURITY_ARCHITECTURE_V1.md` §5 (obligations),
ADR-014 (obligations as a separate axis), `application/ports/rate_limit.py`.

**Scope**

- `InvokeTool` accepts an optional `RateLimiter`; the obligation is enforced on a
  per-agent-per-tool key before the approval gate.
- `container.py` passes the `InMemoryRateLimiter` it already builds from
  `rate_limit_requests` / `rate_limit_window_seconds`.
- No new port, no new dependency, no schema change.

**Acceptance criteria**

- A call within the bound succeeds.
- A call over the bound raises `RateLimited` carrying the retry window (mapped to 429).
- The refusal is written as a `denied` invocation naming the obligation and the window.
- The bound is per tool, not per agent: exhausting one tool does not block another.
- An idempotent replay under a recorded key does not consume budget.
- With no limiter wired the obligation is unsatisfiable and the call is refused.

**Required tests:** `tests/agent/test_tool_gateway_obligations.py` (five new cases),
`tests/agent/test_tool_gateway_policy.py` (unsatisfiable case in a no-limiter harness).

**Security considerations**

- Fail closed with no limiter: never run unbounded because a dependency is missing.
- Enforcement precedes approval so a flood cannot fill the human approval queue.
- The refusal is audited, not merely returned: a throttled agent must be visible in the trail.

**Migration implications:** none.

**Observability:** the denial row is the signal; no new metric was added.

**Rollback:** stop passing `rate_limiter` into `InvokeTool`. The obligation reverts to
fail-closed refusal — no caller or port changes.

**Known limit, recorded deliberately:** the bound is deployment configuration, not policy
vocabulary. `PolicyObligation` is a closed set of names with no parameters, so a rule says "a
volume bound applies" and the operator sets the number. A per-rule bound needs a parameterised
obligation — an ADR, not a quiet widening (D-056).

---

## NEX-057 — the `step-up` obligation: what evidence raises the assurance requirement

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Priority | P1 |
| Status | **IMPLEMENTED** (ADR delivered; enforcement deferred to NEX-058) |
| Dependencies | NEX-053, NEX-054, NEX-055, NEX-056 (the four satisfied obligations) |
| ADR | [ADR-015](../ADR/ADR-015-step-up-assurance-signal.md) |

**Objective:** give `step-up` a mechanism, or record an ADR explaining why it cannot have one
in the current model. Today a policy that names it refuses every call.

**Why this is the hardest of the five:** `approval`, `redaction`, `enhanced-audit` and
`rate-limit` all have a *deterministic* mechanism that either fires or does not. `step-up`
requires a judgement — that the current assurance level is insufficient for this attempt — and
a judgement made by an AI model would be exactly the thing §10 of the directive forbids: a
model raising its own requirement. So the decision is *what deterministic signal raises the
requirement*, not *what model should decide*.

**First deliverable is an ADR, not code.** Candidate deterministic signals to evaluate:

- The capability's declared sensitivity (a static attribute on `ToolDefinition`).
- The classification of the data the call would touch (already modelled).
- Whether the delegating human's own session assurance is below the capability's floor.
- Accumulated risk in the window (a count of denials, not a model score).

The ADR must state what "stepped up" *means* — a fresh human confirmation, a re-authentication,
a second credential — because an obligation whose satisfaction is undefined is not enforceable.

**Acceptance criteria**

- An ADR names the deterministic signal and what satisfies the requirement.
- If a mechanism is feasible: the gateway enforces it and a test pins both the raise and the
  satisfy paths.
- If it is not feasible without a vocabulary change: the ADR says so and the backlog records the
  prerequisite, rather than the gateway quietly accepting the obligation.

**Security considerations:** a model must never raise its own assurance requirement. Any
step-up trigger is a deterministic application-layer control.

**Next:** `second-approver` follows and depends on the approval flow supporting a second
*distinct* approver rather than reusing the first — separation of duties, not a repeat click.

**Resolution (NEX-057, run NEX-2026-11-27-58):** the second acceptance path was taken — *not
feasible without a vocabulary change* — but narrowly: the signal itself is deterministic and
half-modelled already; the assurance vocabulary and its persistence do not exist yet.

ADR-015 names the signal as the **recorded authentication assurance of the delegating human**.
An agent has no assurance of its own — every tool handler acts as the delegator — so the honest
question is how strongly the *human* authenticated when they granted this authority. That is a
stored fact, read by the gateway, never inferred. Assurance is a closed ordered vocabulary
(`single-factor` < `multi-factor`) derived server-side from the IdP's `acr`/`amr` claims at
login, carried on `Principal`, copied onto `DelegatedAuthorization.grant`, and compared against a
static `STEP_UP_FLOOR` of multi-factor by ordinal rank. Absent or unrecognised assurance reads as
single-factor, so an unpersisted value can never become authority. The gateway keeps refusing
`step-up` until NEX-058 lands — correct, because an obligation satisfied by a weaker check than
its name promises is worse than a refusal.

Rejected in the ADR, with reasons: a model-scored risk signal (§10 forbids a model raising its
own requirement, and it is unauditable); denial-count risk accumulation (measures behaviour, not
identity, and an adversary can steer it in both directions); deriving it from the capability's
`requires_approval` (makes it the same obligation as `approval` under a second name); data
classification (says what data may flow, not who is acting, and lets a policy author widen the
step-up set by relabelling data); and reading the delegator's live session assurance per call
(the delegation can outlive the session, and it couples the gateway to the session store for a
fact that should be fixed at consent).

---

## NEX-058 — persist authentication assurance so `step-up` becomes enforceable

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Priority | P1 |
| Status | **VERIFIED** at `38292d2` (run 73). Backend pair 2/2 `completed/success`; frontend 1/2 `success`, the single failure being a pre-existing `App.test.tsx` race (NEX-073) in code this commit does not touch. All 7 scope items now implemented; item 7 (the claim-shape diagnostic) was the last gap. |
| Dependencies | NEX-057 (ADR-015), NEX-053 (delegation model) |
| Blocks | NEX-057 enforcement; `second-approver` (needs distinct-human identity) |

**Objective:** implement ADR-015's prerequisites so the gateway satisfies `step-up` instead of
refusing it.

**Scope**

1. `domain/identity/assurance.py` — `AuthenticationAssurance` (`single-factor`,
   `multi-factor`), `rank`, `meets()`. Closed vocabulary; absent reads as the lowest rank.
2. `Principal.assurance` — default `SINGLE_FACTOR`; derived in
   `infrastructure/identity/oidc_relying_party.py` from `acr`/`amr`, never from a request.
3. `DelegatedAuthorization.assurance`, set in `grant()` from the granting principal.
4. `DelegationRow.assurance` + migration: NOT NULL with `server_default='single-factor'`, then
   drop the default in a second batch (the `b7d2c48e91af` pattern).
5. `_delegation_to_domain` / `SqlAlchemyDelegationRepository.add` / `.save` carry the field.
6. Gateway: `STEP_UP` satisfied iff `resolved.delegation.assurance.meets(STEP_UP_FLOOR)`.
7. The OIDC adapter logs the raw *claim shape* (not the value) when it defaults to
   single-factor, so an operator whose IdP does support MFA can see why step-up policies refuse.

**Acceptance criteria**

- Raise path: a single-factor delegation is refused `step-up`, audited as a denial.
- Satisfy path: a multi-factor delegation is allowed and its assurance appears in the evidence.
- Fail-closed path: absent or unknown assurance is refused, never treated as satisfied.
- A test asserts that no request body or header can set assurance.

**Required tests:** the three gateway paths above, the migration on a populated table, and the
`acr`/`amr` mapping in the OIDC adapter.

**Security considerations:** the only writer of `assurance` is the verified-token path; the
gateway never re-derives it. Widening the enum is a vocabulary change and needs an ADR.

**Migration implications:** one additive column, nullable-then-NOT-NULL via server default.

**Next:** after NEX-058 lands, implement `second-approver` on the same distinct-human footing.

**Verified (run 73).** Scope item 7 landed at `38292d2`: the OIDC adapter logs the claim
*shape* (never a value) when `derive_assurance()` returns below `STEP_UP_FLOOR`, naming which
stage failed — claim absent, type unusable, method unrecognised, too few independent families,
or an `acr` negation vetoing the marker. The line is silent on a satisfying login. The marker
scan is shared with enforcement (`_acr_multi_factor_markers()`), and returns *every* marker with
a per-marker veto, so the diagnostic cannot drift from the decision — a first version returned
only the first marker, which would have silently changed enforcement for
`mfa-optional:multi-factor`. 7 tests added through the real `_verify_id_token` path, proved
non-vacuous by reverting the log call (4 fail). Full suite 1508 passed / 4 skipped; mypy clean
on 141 files; backend CI 2/2 success at `38292d2`.

## NEX-059 — make `second-approver` a real two-person control

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Priority | P1 |
| Status | **IMPLEMENTED + TESTED** (this run); CI verification pending |
| Dependencies | NEX-058 (distinct-human identity), NEX-053 (delegation model) |
| Blocks | `step-up`/`second-approver` being the last unimplemented named obligations |

**Objective:** the gateway must stop refusing `second-approver` and enforce it — one
signature from one human must not authorise a two-person control.

**The failure mode this task exists to prevent.** The obvious implementation — "if the
obligation is present, accept any approval" — *silently defeats* the control it claims to
enforce. The request is approved, the action runs, and nothing anywhere records that only
one person signed. So the threshold must live on the request (`required_approvals`), not be
re-derived by the gateway at spend time: a gateway that forgot to pass the number would
otherwise accept one signature for a two-person control.

**Scope delivered**

1. `domain/agent/entities.py` — `ApprovalRequest.required_approvals` (1 or 2, closed
   vocabulary), `second_approver_user_id`/`second_decided_at`/`second_reason`,
   `denied_by_user_id`, `approvals_recorded`, `awaiting_second_approval`.
   `approve(approver_user_id=…)` records the first signature and leaves the status
   `PENDING`; the **second, distinct** human completes it. Distinctness is enforced in the
   aggregate, not the use case. `raise_required_approvals()` may only raise (never lower),
   and refuses once any signature exists — silently un-approving a recorded human decision
   would rewrite history. `is_usable` fails closed when `approvals_recorded <
   required_approvals`, so a contradictory row (hand-written UPDATE, partial migration,
   future writer) never spends a control it never satisfied.
2. `application/use_cases/tools.py` — `second_approver_required()` derives the threshold
   from the matched obligations; `_approved_request()` opens the request with it.
3. `infrastructure/db/models.py` + `agent_repositories.py` — persist the new columns;
   `consume(request_id, now=)` carries the timestamp.
4. `migrations/versions/e5a9c3b7d812_two_person_approval.py` — additive, nullable-then-
   NOT-NULL via `server_default`, the `b7d2c48e91af` pattern.
5. `tests/agent/test_tool_gateway_obligations.py` — the fail-closed test claimed
   `second-approver` had no adapter. That is no longer true, so it was retargeted to a
   non-sensitive tool; the module docstring's "unsupported" claim was corrected rather
   than left to mislead the next reader.

**Acceptance criteria**

- Raise path: a policy with `second-approver` opens a request needing **two** signatures.
- One signature: status stays `PENDING`, `is_usable` is false, action is refused.
- Same human signing twice: refused as an invariant violation, not counted.
- Second distinct human: approved, usable, consumed once (replay refused).
- Denial after a partial approval: refused action, and *both* the grant and the refusal
  remain on the record for the auditor.
- Fail-closed: a row with `status=APPROVED` but fewer signatures than required is unusable.

**Required tests:** `tests/agent/test_second_approver_obligation.py` — 11 tests covering all
six paths above plus threshold weakening. **All 11 pass.**

**Security considerations:** the threshold is a property of the persisted request, never a
gateway argument and never settable from a request body or header. The vocabulary is closed
at 1–2; widening it needs an ADR (a variable threshold a caller can widen is not a control).

**Migration implications:** additive columns only; no backfill needed because `required_approvals`
defaults to 1, which is the behaviour every existing row already had.

**Next:** commit, push, poll CI, then report NEX-057/058/059 together to PR #5 and issue #2
— the interesting fact is that ADR-015 now has two of its three named obligations enforced.

## NEX-063 — risk-based approval: the gate follows the blast radius, not only the policy

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Priority | P1 — foundational |
| Status | VERIFIED — CI 4/4 `completed/success` at `934774e`, polled 2026-11-27T20:52Z (run 63) |
| Dependencies | NEX-059 (`required_approvals`, closed 1–2 vocabulary), ADR-015 |
| Blocks | the "risk-based approval" clause of `ENTERPRISE_SECURITY_ARCHITECTURE_V1` L147 and L212 |

**Objective.** Make approval strength follow how dangerous the action is, not only what a
policy author happened to write, and put that risk on the decision record.

**Why it was the right next task.** It is the last named ADR-015 obligation without a
mechanism, and it is the one the security architecture calls out twice (L147 "risk-based
approval", L212 "Tier 2 risk-based durable"). It also unlocks a later slice: once the record
carries a tier, a policy can require a control *at or above* it.

**Design.** `domain/agent/risk.py` — a pure, total, deterministic classifier from `Capability`
to a closed ordered tier (`low` < `elevated` < `high`). Code, not a model score: a risk figure
that sets an approval threshold is authority, and §10 forbids a model from widening its own.
`required_approvals_for()` returns the max of what the policy asked and the tier floor, clamped
to 1–2. `_policy_attributes()` emits `risk` so the gate's reasoning is on `DecisionEvidence`.
Full reasoning in `docs/ADR/ADR-016-risk-based-approval.md`.

**Acceptance criteria**

- A silent policy does **not** reduce a `HIGH` action below two signatures. *(pure test)*
- A policy **may** raise a `LOW` action to two. *(pure test)*
- The threshold result is always in the closed 1–2 vocabulary, for every capability and any
  input threshold including 0 and 99. *(pure test, parametrised)*
- Every capability is classed; `ORG_SEARCH` is the only `HIGH`; every sensitive capability is
  at least `ELEVATED`. *(pure tests)*
- The recorded decision evidence carries `risk`. *(gateway path, existing evidence assertions
  still green)*
- `assess_action_risk` refuses a bare string rather than coercing. *(pure test)*

**Required tests:** `tests/agent/test_risk_based_approval.py` — 25 tests. **All 25 pass.**

**Security considerations.** The tier is computed in the enforcement path from the capability
and is never read from a policy or a request body, so no caller can lower it. The approval
vocabulary stays closed at 1–2; widening it needs an ADR.

**Migration implications:** none — no schema change. `required_approvals` already defaults to 1.

**Honest limit.** `ORG_SEARCH` is `HIGH` *and* non-delegable, so no agent token reaches it and
the two-signature floor has no production caller yet. It is kept for the day a future ADR
permits delegated org-wide search. Because that branch cannot be exercised end to end, the
threshold logic is tested as a pure function; every other tier is exercised through the gateway.

**Deferred, with reasons in the ADR:** approval *preview* (no proposal surface to render), and
non-capability risk signals — bulk volume, unusual patterns, export size — which need
request-level facts the gateway does not collect.

**Next:** poll CI on the pushed head; if green, promote to VERIFIED and extend PR #5.

---

## NEX-064 — a retired policy version must not stay in force (fail-open closure)

| Field | Value |
| --- | --- |
| Task ID | NEX-064 |
| Phase | 3 (governance) |
| Priority | P1 — authorization correctness |
| Status | VERIFIED — CI 4/4 `completed/success` at `7456a89`, polled 2026-11-27T21:13Z |
| Dependencies | none — `evaluate()` already existed |

**Objective.** Make `evaluate()` honour its own rule 3, *"Only published versions may be
consulted"*. It refused a `DRAFT` but **consulted a `RETIRED`** version, so an `allow` rule from
a superseded release still allowed.

**Scope.** `domain/governance/policy.py` — one predicate in the version loop; two regression
tests in `tests/domain/test_governance_policy.py`. No port, no adapter, no schema, no migration.

**Architecture references.** ADR-015 (deterministic enforcement), `docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md`
(fail-closed), §10 (deterministic application-layer controls, never model judgement).

**Acceptance criteria.**
- A retired version alone raises `ValidationError` naming it `retired`.
- A retired version in a set **with** a published one refuses the whole evaluation — it is not
  silently skipped, because a caller would otherwise believe retired rules applied.
- The draft message keeps its exact existing wording (`"is a draft and cannot be evaluated"`).
- Full suite, ruff format/check, and mypy stay green.

**Required tests.** `test_a_retired_version_cannot_be_evaluated`,
`test_a_retired_version_alongside_a_published_one_still_refuses`. Both pass (verified: 3 passed
for `-k retired`).

**Security considerations.** This is a **fail-open** closure. It was latent — the gateway passes
`all_published()`, which cannot contain a retired row — so no production path was exploitable.
The exposure is the next caller: `history()` (what the history endpoint returns) would keep
enforcing replaced rules. Fixing the predicate makes the invariant true for every caller rather
than only the one that happened to pass a safe argument.

**Migration implications.** None.

**Observability implications.** None — the failure is now a `ValidationError` at the same point
a draft already failed, so existing audit behaviour is unchanged.

**Rollback.** Revert one predicate; no state to unwind.

**Complexity.** Low.

**Verified.** CI 4/4 `completed/success` at `7456a89` (2026-11-27T21:13Z); PR #5 body and issue #2 updated by run 64.
**Named follow-up (not this task):** the policy lifecycle has no `approve` verb — `DRAFT →
PUBLISHED` is direct, so approval of a policy change is not itself recorded. That is the
next governance slice.


## NEX-067 — MFA requires independent factor families, not distinct method names

| Field | Value |
| --- | --- |
| Task ID | NEX-067 |
| Phase | 3 (governance / identity assurance) |
| Priority | P1 — authorization correctness (a security control that overstates the login) |
| Status | VERIFIED — CI 4/4 `completed/success` at `c01fde5`, polled 2026-11-27T22:47Z |
| Dependencies | ADR-015 `step-up` obligation (NEX-058) — this is the fact that obligation enforces |

**Objective.** Make `derive_assurance()` grant `MULTI_FACTOR` only on evidence of two
*independent* authentication factor categories, not on two distinct strings in `amr`.

**Scope.** `infrastructure/identity/oidc_relying_party.py` — one family table plus the
predicate; `tests/authentication/test_derive_assurance.py` — negative and positive cases. No
port, no adapter, no schema, no migration, no new module.

**Architecture references.** ADR-015 (deterministic enforcement; obligations), ADR-008/ADR-016
(authorization decisions come from deterministic application-layer controls), §10 of
`docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md` (fail-closed; AI never overrides policy).

**Acceptance criteria.**
- `otp + totp` and `sms + tel` read as `SINGLE_FACTOR` — the two combinations the review named.
- Other same-family spellings behave the same: `hwk+swk`, `webauthn+passkey`, `face+iris`,
  `pwd+password+pin`, `fpt+face+iris+vbm`.
- Genuine independent pairs still read `MULTI_FACTOR`: `pwd+otp`, `pwd+fpt`, `pwd+hwk`,
  `otp+fpt`, `pin+hwk`.
- An unrecognised method is ignored, never counted, so the closed vocabulary can only
  understate assurance.
- Full suite, ruff format/check, and mypy stay green.

**Required tests.** `test_amr_methods_from_one_factor_family_read_as_single` (parametrised, 8
same-family cases) and `test_amr_methods_from_independent_families_read_as_multi`. Both pass;
12 cases fail if the family guard is reverted, so they are proved to catch the defect.

**Security considerations.** This closes a **fail-open**. The `step-up` obligation demanded two
factors and one factor satisfied it, because the gateway trusted a derived value that counted
spellings. The vulnerability was not reachable from an unauthenticated position — an attacker
had to control the `amr` claim, which the IdP signs — but a permissive IdP or a confused
deployment could mint it, and the control then enforced nothing.

**Migration implications.** None.

**Observability implications.** Assurance may now read lower for the same claim set. That is the
intended direction and is visible in existing audit records, which already carry the derived
assurance.

**Rollback.** Revert the family table and predicate; no state to unwind.

**Complexity.** Low.

**Verified.** CI 4/4 `completed/success` at `c01fde5` (2026-11-27T22:47Z); PR #5 body and
comment `7229055417` carry the evidence.
**Named follow-up (not this task):** NEX-065 — the policy lifecycle has no `approve` verb, so a
policy change is not itself recorded as approved.

## NEX-073 — de-race the note-list assertions in `App.test.tsx`

| Field | Value |
| --- | --- |
| Phase | 4 (developer experience / gate trust) |
| Priority | P4 (but cheap, and it protects every future verdict) |
| Status | **VERIFIED** at `09888ac` (test-only; 127 passed / 9 files, `tsc -b --force` + `eslint` + `vite build` clean, 20/20 repeat runs 0 failures) — **CI 4/4 `completed/success` (backend ×2, frontend ×2), polled 2026-11-28T01:56Z.** |
| Dependencies | None |
| Blocks | Nothing functionally; it degrades the reliability of the frontend CI signal |
| Owner | FRONTEND |

**Objective:** make the frontend test gate deterministic, so a red frontend run is evidence of a
real defect rather than a coin flip.

**The defect.** `frontend/src/App.test.tsx > App — identity and ownership > shows the current
user and lists owned notes` does this:

```tsx
expect(await screen.findByText("Alice Anderson")).toBeInTheDocument();
expect(screen.getByText("Alice private")).toBeInTheDocument();
```

The identity header and the note list are rendered by *different* effects. Awaiting the header
therefore says nothing about the list, and the synchronous `getByText` for the note title can
run before the list has committed. It failed one of two frontend CI runs on `38292d2` and passed
the other on the identical SHA, on a commit that touches **no** frontend file.

**Evidence.** Check-run `140815503026` (`failure`, 26s) vs `140815484127` (`success`, 67s) on
`38292d2dc5286f9f8bd05ed57fa8af30008fb394`. Annotation: `TestingLibraryElementError: Unable to
find an element with the text: Alice private`, with the app shell and identity select present in
the dumped DOM but no note list. The shorter runtime is consistent with failing early.

**Scope**

1. Replace synchronous `getByText` assertions on *asynchronously rendered* content with
   `findByText` / `waitFor` in `App.test.tsx`.
2. Audit the rest of the file for the same shape — a `getBy*` on note/folder content that
   follows an `await` on something else. The same race can be latent elsewhere and merely not
   have lost the coin flip yet.
3. Prefer asserting through a role/`aria` query over raw text where it makes the intent clearer,
   but do not rewrite tests wholesale: the goal is determinism, not churn.

**Acceptance criteria**

- The suite passes repeatedly in a loop (e.g. 20 consecutive runs of the file) with no failure.
- No assertion is weakened or deleted to achieve that; each changed assertion must still fail if
  the corresponding rendering is removed.
- No production code changes.

**Required tests:** the existing file, run in a loop locally; `npm run typecheck`, `npm run lint`,
`npm test`, `npm run build`.

**Security considerations:** none. This is test-only.

**Migration implications:** none.

**Complexity:** Low.

**Next action:** change the note-title assertions to `findByText`, audit the file for the same
pattern, then prove determinism with a loop.

### Resolution (run `NEX-2026-11-27-74`)

> **Correction (run `NEX-2026-11-27-73`, later pass).** The paragraph below originally claimed
> "Only one assertion had the race" and cleared line 172. **That was wrong.** The item-2 audit
> was completed properly in a later pass and found a *second*, worse instance in the two search
> tests. The corrected account is in **Resolution, corrected** at the end of this entry; read
> that instead. The paragraph is kept so the error stays visible.

Only one assertion had the race. The other synchronous `getByText` calls in the file are safe:
line 172 (`Quarterly Report`) follows a `waitFor` on the *same* `listNotes` result that the
debounced search re-renders, so it cannot beat a fetch it is already gated behind; line 216
(`/read-only access/i`) is rendered from `noteDetail` which the preceding `await
findByText("Shared design doc")` click already resolved; line 374 (`No boards yet`) is a
synchronous board-store empty state with no fetch. Only line 47 awaited the identity header and
then read note-list content — two different effects.

Fix at the assertion site, not in `boot()`: a shared barrier in `boot()` would have to be
something that renders *with* the note list. `Search notes` is in `AppHeader` and renders
regardless of `loadingNotes`, so awaiting it would have been a fake barrier that looked like a
fix — which is why the first attempt at that was reverted.

Proved non-vacuous by a temporary `FakeApiClient` subclass delaying `listNotes` 50 ms: the old
synchronous read throws, the awaited one resolves. Temp file removed (0 remnants).

Determinism: 20/20 consecutive `vitest run src/App.test.tsx` passed, 0 failures.
Gates: 127 passed / 9 files; `tsc -b --force` clean; `eslint .` clean; `vite build` succeeded.

### Resolution, corrected — scope item 2 (run `NEX-2026-11-27-73`, later pass)

Item 2 ("audit the rest of the file") was **not** actually done in run 74; the paragraph above
asserted it had been. Doing it properly found a second, more serious instance, in the two search
tests (`filters notes by title as the user types` and `clears the search and restores the view`):

```tsx
await user.type(screen.getByLabelText("Search notes"), "quarter");
await waitFor(() => expect(screen.queryByText("Groceries")).not.toBeInTheDocument());
expect(screen.getByText("Quarterly Report")).toBeInTheDocument();
```

**The real mechanism is not ordering — it is that `NoteWorkspace.tsx:189` swaps the entire list
for a loading state while `loadingNotes` is true.** So during a search refetch the list is
*empty*, not filtered, and an absence-only `waitFor` is satisfied trivially. Empirically proved
with a 50 ms-delayed `listNotes`: after the old `waitFor` resolved, `document.body.textContent`
did **not** contain `Quarterly Report` at all — the assertion that should have proved filtering
was checking an empty screen. The following synchronous `getByText("Quarterly Report")` then
threw. This is a *vacuous pass* plus a *latent failure*, i.e. strictly worse than the identity
test: that one always failed loudly when it lost the race, whereas this one silently proved
nothing on every green run.

Fix: assert both halves **atomically inside one `waitFor`**, so the absence check can only
succeed on a screen where the matching note is also present:

```tsx
await waitFor(() => {
  expect(screen.getByText("Quarterly Report")).toBeInTheDocument();
  expect(screen.queryByText("Groceries")).not.toBeInTheDocument();
});
```

This also removed the need for a "wait for the unfiltered list first" barrier — an earlier
attempt at that barrier *after* typing failed, correctly, because `Groceries` is filtered out by
then and may never render.

Verified under a 50 ms `listNotes` delay: both fixed tests pass; a control test reproducing the
old shape passes the absence-only `waitFor` and then shows `Quarterly Report` is absent, proving
the old assertion was vacuous (3/3 passed).

Determinism after both fixes: 20/20 consecutive `vitest run src/App.test.tsx`, 0 failures.
Gates: 127 passed / 9 files; `tsc -b --force` clean; `eslint .` clean.

**Lesson for future audits:** in this file a `waitFor` around a *negative* assertion is not a
synchronisation point — `loadingNotes` empties the list. Never assert absence alone; pair it with
a positive assertion in the same `waitFor`.

### Third instance: the tenant-isolation test (same pass)

`does not reveal another user's private note when switching identity` had the identical shape and
is the most important one in the file, because absence is its **entire** evidence:

```tsx
await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());
await waitFor(() => expect(screen.queryByText("Alice private")).not.toBeInTheDocument());
```

`Bob Baker` is the identity header, rendered from a different effect than the note list, so it is
not a barrier for list content. If Bob's list ever rendered empty — a load failure, a
`listNotes` bug, or a regression that returns nothing — the assertion would pass while proving
nothing, and a *leak* is exactly what it is meant to catch.

Fix: seed a note for Bob and anchor on it, so the absence check only runs on a screen where the
list has actually rendered for the new identity:

```tsx
await waitFor(() => expect(screen.getByText("Bob private")).toBeInTheDocument());
expect(screen.queryByText("Alice private")).not.toBeInTheDocument();
```

**Proved it can catch a leak** (the point of the test) with a temporary `FakeApiClient` subclass
whose `canRead()` returns `true` and whose `listNotes` forces `view: "all"`. The new assertion
failed exactly at `expected document not to contain element, found <span>Alice private</span>` —
i.e. it detects a real isolation regression. Temp file removed.

The other two absence-only `waitFor` calls were checked and left alone, because in both the
absence check is *not* the sole evidence: line ~111 (`Temp`) is followed by
`api.getNoteSync(note.id)?.folder_id === null`, and line ~290 (`Temporary access`) is followed by
`expect(api.getNote(note.id)).rejects.toMatchObject({ status: 404 })`. Those backend assertions
carry the real proof, so the DOM check is a UI hint, not the verdict.

## NEX-074 — Let an operator issue a bounded, attributable operator grant

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Status | **VERIFIED** at `e375a1f` — CI 4/4 `completed/success`, polled 2026-11-28T02:20Z |
| Priority | P1 — an incident remedy with no caller is a remedy that does not exist |
| Dependencies | NEX-072 (durable `operator_access_log`), the operator-grant store and `OperatorAccessLog` port |
| Architecture | `application/use_cases/operator_grants.py`, `application/ports/operator_grants.py`, ADR-008 (404-vs-403), ADR-015 |

**Objective.** `OperatorGrant` carried `expires_at` and `granted_by`, the store was durable, and
the holder report could read it — but no code path could *write* a human-issued grant. The only
writer was the deployment-configuration seed, which issues unbounded grants with
`granted_by=None`. The bounded, attributable grant the record exists for was unreachable.

**Delivered.** `IssueOperatorGrant` use case, container wiring, `POST /ops/operator-grants`
(`OperatorGrantIssueRequest`). Four refusals: an unbounded grant; a self-grant; a scope the issuer
does not hold (attenuation, checked against the gate's *live* scopes so a lapsed issuer cannot
delegate a stale one); and an unknown scope that no use case asks for. The issuer is stamped by
the application layer from the authenticated principal — the body carries no `granted_by`, so a
caller cannot attribute a widening to someone else. `ISSUE_OPERATION` is a distinct scope from
the read-side `operator-access-report`: reading who holds a grant and minting one are different
authorities.

**Deliberately omitted: `rationale`.** The grant row has no column for it, so a required
free-text field would be validated and thrown away — a record that reads as evidence but is
never written. Intent is carried by `granted_by` and the gate's access log.

**Follow-up (not a defect).** Persisting an issuance rationale needs a column and a migration;
until then nothing may promise one.

**Tests.** 11 in `tests/application/test_issue_operator_grant.py`, including a positive pin on
what the stored row actually records (`granted_by`, `expires_at`, `granted_at`, `is_active`).
Full suite 1545 passed / 4 skipped; ruff format+check and mypy (141 files) clean locally.

**Acceptance criteria.** An operator can issue a bounded grant; the stored row names the issuer;
a non-operator, a self-grant, an over-broad scope and an unknown scope are each refused; no
request body can set the issuer.

## NEX-075 — Retain the operator access log on a bounded window, and make erasing it its own authority

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Status | **VERIFIED** at `319a3dd` — CI 4/4 `completed/success`, polled 2026-11-28T02:45Z |
| Priority | P1 — an audit log that grows without limit and never forgets is a compliance and storage liability |
| Dependencies | NEX-072 (durable `operator_access_log`), NEX-074 (operator-grant issuance), the `OperatorAccessLog` port |
| Architecture | `domain/governance/retention.py`, `application/use_cases/operator_access_retention.py`, ADR-017, ADR-008 (404-vs-403) |

**Objective.** NEX-072 made the operator access log durable and NEX-074 made it the record of who
may read and issue operator grants. Nothing ever removed a row: every granted `/ops` read appends
one, forever, attributed to a named user. The table grows without limit, and a read by someone who
has since left the organisation stays attributed to them indefinitely. Both are risks NEX-074's
checkpoint recorded as follow-up work; no backlog task existed, so this one was created first.

**Delivered.** `OperatorAccessRetentionPolicy` (window bounded 90–3650 days, validated at
construction), `PurgeOperatorAccessLog` use case, `purge_before` on the `OperatorAccessLog` port,
`POST /ops/operator-access-log/purge`, container wiring from `OPERATOR_ACCESS_RETENTION_DAYS`, and
`tests/…` across four layers. ADR-017 records the decisions.

**Design note — the obvious fix is the wrong one.** Letting an operator delete rows destroys the
property the log exists for. So deletion is bounded from every direction: the window is a domain
policy rather than a request field (no caller can express "delete everything" or "delete Bob's
rows"); `access-log-retention` is a distinct authority from `operator-access-report`; the purge
records itself through the gate, stamped `now` inside the window it prunes, so it cannot erase the
record of its own action; and retention is off by default, with the endpoint answering 404 rather
than offering a purge with no mandate.

**Boundary.** The cutoff is inclusive at its near edge — a row stamped exactly `window` ago is kept
— and the same rule drives both the adapter's `at < cutoff` and `covers()`, so they cannot disagree
at the oldest surviving row. Proved by mutation: widening to `at <= cutoff` fails two tests.

**Deliberately omitted: a scheduled purge.** An unattended purger changes who is accountable for an
erasure. That is a separate decision with its own approval question, not a configuration flag.

**Tests.** `tests/domain/test_operator_access_retention.py` (window bounds, fail-closed),
`tests/application/test_operator_access_retention.py` (23), `tests/api/test_ops_access_retention_api.py`
(19, incl. 404-when-unconfigured and fail-boot-on-short-window), and an extension to
`tests/persistence/test_operator_access_log.py`. Full suite 1551 passed / 4 skipped; ruff
format+check and mypy (143 files) clean locally.

**Remaining.** None. VERIFIED at `319a3dd` (CI 4/4, polled 2026-11-28T02:45Z). PR #5's body carries
NEX-074 and NEX-075.

## NEX-076 — Record why a human issued an operator grant

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Status | **VERIFIED** — CI `4fa0463` 4/4 `completed/success`, polled 2026-11-28T03:22Z; 1569 passed / 4 skipped locally |
| Priority | P1 — an audit of a privileged hand-out that cannot say *why* answers half the question |
| Dependencies | NEX-074 (operator-grant issuance), NEX-072 (durable `operator_access_log`) |
| Architecture | `domain/identity/operator_grant.py`, `application/use_cases/operator_grants.py`, migration `b4e6f1a2c9d7` |

**Objective.** NEX-074 made a grant issuable and stamped the issuer, but the grant row had no
column for a reason — the omission NEX-074 recorded as its explicit follow-up. An auditor could
reconstruct *that* a grant was handed out and *who* handed it out, but never *why* it was
justified. `granted_by` plus the gate's access log names an actor and an instant, not a
justification.

**Delivered.**

- `OperatorGrantRationale` value object (stripped, non-empty, bounded).
- `OperatorGrant` enforces the pairing: a **human-issued** grant (`granted_by` set) must carry a
  rationale; a **configuration** grant (`granted_by IS NULL`) must not. This keeps the two
  provenance kinds honestly distinguishable.
- `IssueOperatorGrantRequest` gains a required `rationale`; `POST /ops/operator-grants` accepts it
  and returns it.
- Migration `b4e6f1a2c9d7` adds a nullable `rationale TEXT` column with a check constraint
  (`IS NULL OR length > 0`), via `batch_alter_table` so it runs on both SQLite and PostgreSQL.

**Design note — nullability mirrors provenance.** The column is nullable on purpose: a
configuration grant's reason *is* the reviewed deployment, so storing a per-row reason for it
would be redundant. Only human-issued grants are forced to state one, which is exactly where an
auditor's question lands. No backfill: existing rows predate the requirement and are configuration
grants by construction (the human write path did not exist before NEX-074), so inventing reasons
for them would fabricate audit facts.

**Boundary refusals.** A blank or over-long rationale is a `422` at the HTTP boundary (schema
`min_length`/`max_length`); the domain value object enforces the same bound for every other caller,
so the two cannot drift.

**Tests.**

- `tests/domain/test_operator_grant.py` — pairing rule (human-issued without rationale refused;
  configuration grant with rationale refused), rationale value object.
- `tests/application/test_issue_operator_grant.py` — the reason is persisted, not discarded.
- `tests/persistence/test_operator_grants.py` — the rationale survives a restart through a second
  adapter; an empty-string rationale is refused by the column constraint; migrated-schema column
  set includes `rationale`.
- `tests/api/test_ops_operator_grants_api.py` — 201 round-trips the reason; missing / blank /
  over-long reasons are `422`; non-operator `404`; self-grant `409`.

**Acceptance criteria.** An operator can issue a grant only with a stated reason; the reason is
stored, returned, and readable after a restart; a human-issued grant cannot be constructed without
one; a configuration grant cannot carry a fabricated one; the schema and domain agree on the bound.

**Remaining.** Nothing. Delivered to GitHub: PR #5's body now carries NEX-052..NEX-076.

**Follow-ups recorded by this slice (not part of it).** Expose the rationale on the grant read path
(a disclosure decision — the issue response already echoes it, listings do not), and ask the owner
whether PR #5's twenty-two stacked slices should be split before review.


## NEX-077 — Expose per-grant provenance (issuer and reason) on the read path

| Field | Value |
| --- | --- |
| Phase | 3 (governance) |
| Status | **IMPLEMENTED + TESTED + COMMITTED + PUSHED** at `9f9a70f`; CI pending at the time of writing. 1574 passed / 4 skipped locally |
| Priority | P1 — NEX-076 stored a justification no read path returned; a write-only audit field is not an audit trail |
| Dependencies | NEX-076 (the rationale exists), NEX-074 (issuer), NEX-072 (durable access log) |
| Architecture | `application/use_cases/operator_grants.py`, `presentation/schemas.py`; no migration |

**Objective.** NEX-076 recorded `granted_by` and `rationale` on an operator grant, but nothing
read them back. `ListOperatorGrants` folded per-scope grants into one `OperatorHolder` row per
user, and a fold cannot carry per-grant provenance: a holder with three grants has three issuers
and three reasons, so a single field on the holder row would either pick one arbitrarily or drop
them all. The stored justification was therefore write-only — one step short of an audit trail an
auditor can actually read.

**Delivered.**

- `OperatorGrantDetail` — a frozen dataclass with one row per *active* grant: `user_id`, `scope`,
  `granted_by`, `rationale`, `granted_at`, `expires_at`.
- `OperatorGrantSnapshot.grants` (detail rows) and `grant_count`, alongside the unchanged
  `holders` summary.
- `OperatorGrantDetailResponse` in `presentation/schemas.py`; `OperatorGrantReportResponse`
  now carries `grants`.

**Design note — detail is additive, not a replacement.** Both views are built from the same
`active_grants()` tuple at the same instant, so they cannot describe different sets; the detail
adds provenance without changing what the summary means. Keeping `holders` avoids breaking the
existing consumer contract (and its tests) for a field that is still the right answer to "who holds
what, in total".

**Tests.**

- `tests/application/test_operator_grants.py` — a human-issued grant reports its issuer and reason;
  a configuration grant reports neither (null, not invented); two grants for one holder keep their
  own reasons (the case a fold cannot express); the detail excludes an expired grant, so detail
  and summary describe the same active set; `grant_count == len(grants)`.
- `tests/api/test_ops_operator_grants_api.py` — the round-trip test now asserts the reason is
  readable in the *listing* response, not merely that the POST returned `200`.

**Mutation control.** Forcing `rationale=None` in the detail (the pre-NEX-077 behaviour) fails
four tests — two application, two API — so the new assertions are load-bearing rather than vacuous.

**Acceptance criteria.** An operator listing grants can read, per grant, who issued it and why;
a configuration grant honestly reports no issuer and no reason; the summary and the detail agree on
the active set; an expired grant appears in neither.

**Remaining.** Nothing for the read path. The disclosure question NEX-076 raised is answered here
for operators (the listing is operator-gated); non-operator callers still see nothing.

### NEX-078 — Three-way separation of duties and tz-aware grant expiry

- **Phase:** 3 (governance)
- **Priority:** P0 (security)
- **Status:** VERIFIED — CI 4/4 `completed/success` at `6637c50` (Backend ×2, Frontend ×2; polled 2026-11-27T20:43Z by run 78). This row said "CI pending" long after CI had passed; corrected here.
- **Source:** the two security findings in PR #5's discussion
- **Dependencies:** NEX-065 (approval columns), NEX-077 (rationale) — both met
- **Objective:** close two controls that were documented but not enforced at every boundary.
  1. Policy author, approver and publisher must be three distinct principals (all three pairs),
     enforced in the domain, the use cases and a DB CHECK.
  2. An operator grant's `expires_at` must be timezone-aware, refused before any comparison.
- **Acceptance criteria:**
  - Approving one's own draft is refused; publishing a version one approved is refused.
  - A naive `expires_at` is refused with a validation error, never a 500.
  - Legacy published rows with no approver stay readable.
  - The migration round-trips.
- **Tests:** domain (`test_governance_policy.py`, `test_operator_grant.py`), application
  (`test_policy_lifecycle.py`), API (`test_ops_policy_lifecycle_api.py`), persistence
  (`test_policy_versions_db.py`).
- **Security:** removes a one-person governance boundary wearing three names, and an
  ambiguous-expiry grant that could silently extend access by the deployment's UTC offset.
- **Next action:** poll CI on `6637c50`; mark VERIFIED only when all check-runs succeed.

### NEX-080 — Revocation provenance is recorded but not readable

- **Phase:** 3 (governance / audit)
- **Priority:** P1
- **Status:** **VERIFIED** — product `9336ba3`, docs checkpoint `139edf6`; CI 4/4 `completed/success` on both (Backend x2, Frontend x2, polled 2026-11-28T04:55Z). Corrected in run 85: this line still read "CI UNVERIFIED" long after the evidence existed — stale in the pessimistic direction, which invites wasted re-verification.
- **Dependencies:** NEX-079 (the write side), NEX-077 (the detail view) — both met
- **Objective:** surface the revocation facts NEX-079 stores. `revoked_at`/`revoked_by` existed,
  were paired by a CHECK, and survived their revoker, but nothing read them: an operator could see
  a grant was gone without learning who ended it, when, or whether it was revoked at all rather
  than merely expired. An audit fact no interface reads is half a control.
- **Acceptance criteria:**
  - `GET /api/v1/ops/operator-grants/revoked` returns each revoked grant with its instant and
    revoker, under the existing `operator-access-report` scope.
  - A live grant and a grant that merely expired are absent from that list.
  - A revoked grant whose revoker's account was deleted still appears, with `revoked_by: null`.
  - The holdings report (`OperatorGrantDetail`) carries `revoked_at`/`revoked_by`.
  - A non-operator gets `404`.
  - The migration round-trips and the CHECK still refuses an actor with no instant.
- **Tests:** `tests/api/test_ops_operator_grant_revoked_read_api.py` (new, 5), plus widened field
  set in `tests/api/test_ops_operator_grants_api.py`, use-case cases in
  `tests/application/test_operator_grants.py`, and store cases in
  `tests/persistence/test_operator_grants.py`.
- **Security:** read-only and gated. No new scope — reading the removal history is the authority
  the holder report already requires, and a fifth token would make an auditor be granted twice to
  answer one question. The gate runs before the read, so a refused caller learns nothing about
  how much authority has been removed.
- **Defects fixed in passing:** the NEX-079 revocation CHECK made any account holding revocation
  history undeletable; the domain refused the `SET NULL` post-deletion shape for `granted_by`,
  which turned one account deletion into an outage of the whole `/ops` surface. Both are
  consequences of `ON DELETE SET NULL` being ignored by validation written as if those columns
  only ever hold writer-supplied values.
- **Next action:** poll CI on `9336ba3`. Then append NEX-078…NEX-080 to PR #5's body and post the
  issue #2 comment — the standing delivery debt.

## NEX-081 — Let a capped integrity sweep be finished (run `NEX-2026-11-28-81`)

| Field | Value |
| --- | --- |
| Phase | 3 (governance) / platform operations |
| Priority | P1 — the check covered the OLDEST agent trails and could never reach the newest |
| Status | **VERIFIED** — CI 4/4 `completed/success` at `85a013c` (Backend ×2, Frontend ×2, polled 2026-11-28T05:18Z); product commit `3a05db2` |
| Dependencies | NEX-042 (the sweep, operator-gated) — met; closes its cap gap |
| Architecture | `application/ports/agents.py`, `application/use_cases/audit_sweep.py`, `domain/agent/audit_chain.py`, `infrastructure/db/agent_repositories.py`, `presentation/api/routers/ops.py`, `presentation/schemas.py`; no migration |

**Objective.** `SweepAuditIntegrity` (NEX-042) asks the one question no owner can: *is any
agent's audit trail in this deployment broken*. It read
`list_all_for_integrity_sweep(limit=max_agents)`. The cap is a legitimate bound — a sweep loads
every agent's whole trail, so an uncapped scan would hold the database open far longer than a
request should. The report said `truncated` when the cap bit, which is the honest half.

The defect is that `truncated` was a dead end. There was no `offset` and no cursor, so the
sweep was *always* the oldest `max_agents` agents: on any deployment with more agents than the
cap, everything past it was outside the integrity check permanently. Because the walk is
oldest-first, the excluded agents were the **newest** trails — written most recently, and so the
likeliest to have been tampered with by whoever just gained write access.

A cap with no cursor is not a bound on one request. It is a silent exclusion of everything past
it.

**Delivered.**

- `GET /api/v1/ops/audit-integrity?offset=N`; the response carries `offset` and `next_offset`.
  Follow `next_offset` while non-null to reach every agent.
- `ChainAuditReport.offset`, `.next_offset`, `.covered` (`offset + agents_checked`).
- `verified` additionally requires `offset == 0`. A second page that reaches the last agent and
  finds nothing examined *some* agents, not the deployment, and says nothing about the agents
  before it; without this term an operator could page through a broken deployment and see
  nothing but passes.
- `list_all_for_integrity_sweep(*, limit, offset=0)` ordered by `(created_at, id)` — paging
  needs a **total** order, or an `OFFSET` page can skip the agent it should have started at and
  still return a full page, reporting complete coverage of a set it never saw.
- `offset` validated as `InvalidInput` (422) **after** the operator gate, so a non-operator
  still gets `404` and cannot probe the validation by watching the error change.

**Tests.** `tests/application/test_audit_sweep.py` (`TestACappedSweepCanBeFinished`, 5 tests)
and `tests/api/test_ops_audit_integrity_api.py` (2). 16 targeted tests pass.

**Mutation control.** Removing `self.offset == 0` from `ChainAuditReport.verified` makes
`test_a_clean_second_page_is_not_an_all_clear` fail — observed failing, then the guard was
re-verified present and the module re-parsed before the commit. The guard was written alongside
its test and had never been seen failing; a test never seen fail is a hypothesis.

**Acceptance criteria.** ✅ A truncated sweep offers a cursor that reaches the remainder.
✅ Paging the whole set with the cap yields the same findings as one uncapped sweep. ✅ A clean
page after the first never reports `verified`. ✅ A negative offset is refused with 422, and a
non-operator gets 404 regardless of the offset. ✅ A non-operator cannot distinguish a valid
from an invalid offset.

**Remaining.** Nothing for this slice. CI 4/4 at `85a013c`; PR #5's body carries NEX-081 and
issue #2 comment `7234571708` reports it.

---

## NEX-082 — Page the operator access trail so the whole thing is readable (run `NEX-2026-11-28-82`)

| Field | Value |
| --- | --- |
| Phase | 3 (governance) / platform operations — Audit Center |
| Priority | P1 — an audit surface that could only show its newest window, permanently |
| Status | **VERIFIED** — CI 2/2 `completed/success` at `4f8e15d` (polled 2026-11-28T05:36Z); docs head `ab018b2`; PR #5 and issue #2 updated |
| Dependencies | ADR-017 (access-log retention), NEX-075 (bounded retention) — met |
| Architecture | `application/ports/operator_access.py`, `application/use_cases/operator_access_report.py`, `infrastructure/db/operator_access_log.py`, `infrastructure/security/operator_access_log.py`, `presentation/api/routers/ops.py`, `presentation/schemas.py`; no migration |

**Objective.** `GET /ops/access-report` returned at most `limit` newest entries and offered no
way to reach further back, while retention keeps the operator access log for years. Every
granted operator read older than the newest window was permanently unreadable: the log
recorded it, and no endpoint could show it. On an audit surface "read the trail" silently
meant "read its most recent few hundred rows".

**Delivered.**

- `OperatorAccessEntry.position` — an opaque, monotonic insertion ordinal, exposed for
  comparison only and documented as *not* a count (retention deletes and the ring evicts).
- `recent(*, limit, before_position=None)` — returns entries strictly older than the cursor.
- SQL adapter: the surrogate `id` is the position and is unique, so a page boundary can never
  land between two entries; predicate `id < cursor`.
- In-memory ring: a monotonic counter, **not** `len(entries)`, because the ring evicts.
- `before_position` query param on the route; per-entry `position` and per-page `next_position`
  in the response.
- A non-positive cursor is rejected; `next_position` is offered only when the window filled;
  `truncated` is true for a middle page too.

**Why keyset and not offset.** Reading this endpoint appends to the log it reads, so an OFFSET
page shifts by one row per page — repeating one entry and skipping another. See D-082 in
`DECISIONS.md`.

**Validation.** Full suite `1652 passed, 4 skipped` on `4f8e15d`; ruff format 321 files
unchanged; ruff check clean; mypy clean on 143 source files. 14 new API paging tests, 15 adapter
keyset tests, 16 use-case tests. Mutation control observed failing (forcing the cursor to `None`
fails 3 of 6 new paging tests).

**Remaining.** Poll CI for `4f8e15d`/`25f5a6c`, then append this section to PR #5's body and
update issue #2 (both still end at NEX-081).

## NEX-084 — A delegated grant may not outlive the grant it was delegated from (run `NEX-2026-11-28-84`)

| Field | Value |
| --- | --- |
| Phase | 3 (governance) / platform operations — Access Center |
| Priority | P1 — a bounded authority could be converted into a longer one by its holder |
| Status | **VERIFIED** — `09cc4ad`, CI 4/4 success (Backend + Frontend, pull_request and push events, polled 2026-11-28 run 84) |
| Dependencies | NEX-045 (operator grant store), NEX-074 (writable grant record), NEX-078 (aware `expires_at`), NEX-079 (revocation) — all met |
| Architecture | `application/use_cases/operator_grants.py` (`IssueOperatorGrant`, `_require_attenuation`); tests at `tests/application/test_issue_operator_grant.py` and `tests/api/test_ops_operator_grants_api.py`; no migration |

**Objective.** Operator grants support upper-bound attenuation in *scope*: an issuer may only
hand on a scope they themselves hold. Nothing bounded the *lifetime*. An operator holding a
one-hour incident grant could issue the same scope with a thirty-day expiry, so the recipient
held authority the issuer never had and could not have kept. A grant whose holder can convert
it into a longer one is not bounded in the dimension that makes it safe: expiry is the whole
safety property of a bounded grant.

**Delivered.**

- `_require_attenuation` now returns the issuer's **temporal ceiling** for the requested scope
  rather than only validating the scope: the furthest `expires_at` among the issuer's own live
  grants that cover it, or `None` when any such grant is unbounded.
- `IssueOperatorGrant` refuses with `409 Conflict` when the requested `expires_at` exceeds that
  ceiling, naming the instant the issuer may delegate until.
- Scope and lifetime are read in **one** store call, at the gate's clock, so attenuation and
  the horizon describe the same instant — the scope check can no longer pass against a grant
  that has already lapsed by the time the lifetime is compared.
- The ceiling is the **furthest** covering grant, not the nearest. An issuer who holds a scope
  for a week has not narrowed their authority by also holding an unrelated five-minute grant of
  the same scope; refusing on the nearer one would reject a hand-out they plainly cover.
- A wildcard grant covers both the scope and the horizon. A settings-seeded operator is
  unbounded (`expires_at=None`, `granted_by=None`), so the ceiling is `None` and existing
  deployments are unaffected.

**Why 409 and not 403.** The caller is authorized to issue grants; the *requested grant* is in
conflict with the issuer's own authority. A 403 would say the caller may not issue at all,
which is false, and would collide with the permission split in ADR-008.

**Validation.** Full suite `1678 passed, 4 skipped`, exit 0. `ruff format --check` 321 files
clean; `ruff check` clean; `mypy` clean on 143 source files. Mutation control **observed
failing**: with the temporal comparison disabled, all three new tests fail — the two use-case
tests and the API test — so neither the unit nor the HTTP assertion is vacuous.

**Tests.** Use-case level: a deadline beyond the issuer's own is a `Conflict`; a deadline
exactly at the ceiling is allowed; a wildcard issuer is still bounded by their own wildcard
deadline. API level: the 409 survives the store round trip through `TestClient`, asserting the
nested envelope `body["error"]["code"] == "conflict"` rather than a bare status, so a change to
the error contract is caught here.

**Remaining.** None for implementation. The only open item is documentation delivery: append this
section to PR #5's body and comment on issue #2.

## NEX-085 — The strongest grant in the system could not be revoked (run `NEX-2026-11-28-85`)

| Field | Value |
| --- | --- |
| Phase | 3 (governance) / platform operations — Access Center |
| Priority | P1 — a configuration-seeded wildcard operator was unrevokable through the platform |
| Status | **VERIFIED — CI 4/4 success on `d9d0a3a` (runs `46120082100` push and `46120085453` pull_request; Backend and Frontend each `success`), polled 2026-11-28 run 85. The product commit `c698979`'s own runs read `completed/cancelled` — superseded in the concurrency group by this docs commit, never a failure; `d9d0a3a` carries the identical product tree (proved with `git diff c698979 d9d0a3a -- src/ tests/`, empty). |
| Dependencies | NEX-079 (revocation path), NEX-084 (attenuation) — both met |
| Architecture | `application/use_cases/operator_grants.py` (`RevokeOperatorGrant`, `REVOCABLE_OPERATOR_SCOPES`); `presentation/schemas.py` docstring; tests at `tests/api/test_ops_operator_grant_revoke_api.py`; no migration |

**Objective.** `RevokeOperatorGrant` reused `KNOWN_OPERATOR_OPERATIONS` — the *issue*
allowlist — as its scope guard. That set deliberately omits `"*"`, because issuing amplifies
authority and the wildcard should be a deployment decision, not a request. Reusing it on the
revoke path meant a revocation refused the wildcard as an *unknown scope*. The consequence was
that the strongest grant in the system — a wildcard operator seeded from configuration — could
not be stood down through the running platform at all, only by editing the deployment seed and
restarting. That is precisely the "edit configuration to revoke" hole NEX-079 existed to close,
reappearing one level up.

**Delivered.**

- `REVOCABLE_OPERATOR_SCOPES = KNOWN_OPERATOR_OPERATIONS | {WILDCARD}`, used on the revoke
  path only. The issue path keeps rejecting `"*"`: revoking removes authority, so the
  fail-closed direction is to *permit* it; issuing adds authority, so the fail-closed
  direction there is to *refuse* it. One set per direction, not one set shared.
- The asymmetry is documented at the definition and at the use site, so the two allowlists
  cannot be re-conflated by a future refactor without a failing test.
- Naming `"*"` is a *specific* revocation, not a blanket "revoke everything": revocation is
  keyed by `(user, scope)`, so an explicit grant held beside the wildcard survives. The audit
  entry still names the single authority removed.

**Validation.** Full suite `1679 passed, 4 skipped`, exit 0. `ruff format --check` clean;
`ruff check` clean; `mypy` clean on 143 source files. Mutation control **observed failing**:
with `src/.../operator_grants.py` stashed, both wildcard-revocation tests fail with `422`
(the old "unknown scope" refusal), so the assertions are not vacuous. An unknown scope is
still refused, proving the new set did not become a wildcard-only hole.

**Tests.** `test_the_wildcard_grant_can_be_revoked` — a wildcard holder loses access to an
unrelated operator view after revocation; `test_revoking_the_wildcard_leaves_an_explicit_scope_in_place`
— an explicit grant survives the wildcard revocation; `test_an_unknown_scope_is_still_refused_now_that_the_wildcard_is_accepted`
— a bogus scope is still a validation error.

**Remaining.** None for implementation or verification. Delivery (append this section to
PR #5's body, comment on issue #2) is done in run 85 after this promotion.

---

## NEX-086 — Page the revocation history so the whole thing is readable (run `NEX-2026-11-28-86`)

| Field | Value |
| --- | --- |
| Phase | 3 (governance) / platform operations — Audit Center |
| Priority | P1 — the report could not be read at all on a long-lived deployment |
| Status | **VERIFIED** — product commit `70d7667`, CI **4/4 `completed/success`** on docs head `f780afb` (identical product tree, proved with `git --diff -- src/ tests/` empty). Runs `46122302349` (push) and `46122306894` (pull_request), Backend and Frontend both `success`, polled 2026-11-28T07:42:52Z. `70d7667`'s own Backend push-run reads `completed/cancelled` — superseded in the concurrency group, never a failure. |
| Dependencies | NEX-080 (the read side, whose endpoint this bounds), NEX-082 (the same defect fixed for the access trail) — met |
| Architecture | `application/ports/operator_grants.py`, `application/use_cases/operator_grants.py`, `infrastructure/db/operator_grants.py`, `infrastructure/security/operator_grants.py`, `presentation/api/routers/ops.py`, `presentation/schemas.py`; no migration |

**Objective.** NEX-080 made the revocation history readable; it served *all* of it in one
tuple. Revoked rows are never pruned — a revocation is a standing audit fact, not a value a
retention window may erase — so the history grows without bound for the life of the
deployment. On a long-lived deployment, reading the report meant loading every revocation
ever recorded into memory in a single response. That is the same unbounded-response defect
NEX-082 fixed for the operator access trail, in a second report.

**Delivered.**

- `OperatorGrantRepository.revoked_grants(*, limit=None, offset=0)` on the port; both adapters
  implement it. `limit=None` keeps the whole-history behaviour for the tests and a future
  export path; a serving path always passes a limit.
- `OperatorGrantRepository.count_revoked() -> int` — the total, counted through the same
  `is_revoked` domain predicate as the listing rather than `revoked_at IS NOT NULL` in SQL, so
  the total and the page can never describe different sets.
- `RevokedOperatorGrantSnapshot` carries `revoked_total`, `offset` and `next_offset`, plus a
  `truncated` property (`next_offset is not None`). `grant_count` still counts rows *on this
  page*; it is deliberately not the total.
- `GET /api/v1/ops/operator-grants/revoked?limit=&offset=`; `limit` is `ge=1, le=MAX_LIMIT`, so
  a mistyped value is refused rather than silently clamped and a caller cannot believe it read
  more than the cap. `offset` is `ge=0`.
- `_clamp_limit()` in the use case, mirroring `GetOperatorAccessReport`: a non-positive limit
  becomes `1`, never "everything". `offset < 0` raises `InvalidInput` (422) **after** the gate,
  so a non-operator still gets `404` and cannot probe the validation by watching the error
  change.

**Why offset and not a keyset cursor.** NEX-082's access trail is *appended to by reading it*
(the read is itself audited), so its pages would shift under a walker and it needs a keyset
cursor. Reading the revocation history writes nothing, so offset paging over a total order is
correct and simpler. The adapter keeps `is_revoked` in Python rather than pushing a
`revoked_at IS NOT NULL` predicate into SQL for exactly that reason: the definition of
"revoked" must stay in one place or the page could drift from the count.

**Tests.** +17. `tests/application/test_operator_grants.py` (6): the page reports the total
beyond it, the last page has no cursor, walking pages covers every revocation once, an offset
past the end is an empty final page, a non-positive limit does not serve everything, an
over-ceiling limit clamps. `tests/api/test_ops_operator_grant_revoked_read_api.py` (8): the
response carries the paging fields, paging reaches every revocation, `limit=0/-1` and
`limit=10000` are 422, a negative offset is 422, a non-operator cannot page the history.
`tests/persistence/test_operator_grants.py` (2): the adapter pages over the same stable order
it returns whole, and `count_revoked` omits live and merely-expired grants.

**Mutation control.** Two mutations at once — `next_offset` forced to `None`, and the limit
ignored — was observed failing 4/4 (`test_a_page_reports_the_total_beyond_the_page`,
`test_the_last_page_has_no_next_offset`, `test_a_non_positive_limit_does_not_serve_the_whole_history`,
`test_paging_reaches_every_revocation_and_the_total_holds`), then the source was restored and
re-run green before the commit.

**Local gates on the committed tree.** `ruff format --check .` 321 files already formatted;
`ruff check .` all checks passed; `mypy` 143 source files clean; `pytest` 1696 passed,
4 skipped, exit 0.

**Security.** Read-only and gated, unchanged scope. The gate still runs before the read, and
`count_revoked` is called *after* it — a refused caller must not learn how much authority has
been removed, and the total is that number. Bounding the read also bounds the resource a single
authorized operator can force the service to hold.

**Remaining.** None. Implementation, local gates and CI are all green; the delivery to PR #5's
body and issue #2 is owed by the next run.

---


## NEX-087 — Report the exact oldest retained access entry (run `NEX-2026-11-28-87`)

**Phase.** 3 (governance) — audit integrity.
**Priority.** P1 (foundational: the one number that states how far back retained evidence reaches).
**Status.** IMPLEMENTED · TESTED · COMMITTED `82d8a64` · PUSHED. **Not VERIFIED** — CI has not been
read to a verdict yet.
**Depends on.** NEX-075 (the purge and its `retained_from` field), NEX-072 (the durable log).

### The defect

`PurgeOperatorAccessLog` is the only mutation the append-only operator access log permits, and it
reports `retained_from` — "the oldest entry still in the log after the purge". That field is the
whole reason the result is not just a count: an operator reads it to learn how far back the
retained evidence actually reaches. NEX-075 documented it as *the* oldest surviving entry and the
API schema promises the same.

The implementation computed it from `self._access_log.recent(limit=1000)[-1]`. `recent` is
newest-first and capped, so its last element is the oldest entry of **that page**, not of the log.
Once the log held more than one page of survivors, the field reported an entry that was too
recent: the trail looked shorter than it was, and the error grew with traffic. It is the
dangerous direction. An auditor asking "does our retained evidence still cover the incident?" gets
"no, it starts on the 31st" when the evidence in fact starts earlier — the answer that makes them
conclude history is gone when it is not, or, worse, the answer that makes a shorter-than-claimed
retention look compliant.

The existing suite passed because every fixture was smaller than one page. The bug was invisible
below 1000 entries and monotonic above it.

### Evidence, before any change

A probe recorded one old entry plus 1500 newer ones into the in-memory ring and read the use case:

```
removed: 0
true oldest survivor:   2026-08-31T12:00:00+00:00
reported retained_from: 2026-08-31T12:08:22+00:00
field lies? True   understates age by: 0:08:22
```

The understatement is exactly the width of the discarded window, which is why it scales with
volume and vanishes on a quiet deployment.

### The fix

`recent` cannot answer this question at all — it is newest-first and has to be capped — so the
question is asked directly. The port gains `oldest_retained() -> datetime | None`:

- **SQL adapter**: `select(func.min(OperatorAccessRow.at))`. An aggregate reads no rows and cannot
  be truncated by volume. The result is relabelled to aware UTC with the same rule
  `UTCDateTime.process_result_value` applies, because SQLite hands back a naive value for a plain
  `TIMESTAMP` column and the domain speaks aware UTC.
- **In-memory ring**: `self._entries[0].at` under the lock — the `deque` already keeps insertion
  order, so the first element *is* the oldest.
- **Test spy**: `min(entry.at ...)`, so the fake stays honest against the port rather than
  certifying whatever the use case happens to do.

`None` still means "empty log". An empty log is a fact; the caller must be able to say "nothing is
retained" without inventing a timestamp.

The use case now delegates: `oldest = self._access_log.oldest_retained()`. The `_OLDEST_SCAN_LIMIT`
constant and its apologetic comment ("never claims to be exhaustive") are deleted — a constant that
exists to paper over a truncation should not survive the fix that removes the truncation.

### Tests — built to fail against the old answer

Each new assertion is chosen so a page-derived implementation *cannot* satisfy it:

- `test_the_oldest_surviving_entry_is_exact_beyond_one_page` (application): 1500 survivors, past
  the ring's `DEFAULT_CAPACITY` of 1000. Asserts `retained_from == true_oldest` exactly.
- `test_oldest_retained_is_exact_beyond_one_page` (persistence): one 10-year-old row plus 1200
  filler rows, past `_OLDEST_SCAN_LIMIT`.
- `test_oldest_retained_is_aware_utc`: pins that SQLite's naive `MIN` does not leak into the
  domain as a naive datetime.
- `test_oldest_retained_is_none_on_an_empty_log`: empty is a fact, not an invented stamp.
- `test_oldest_retained_advances_past_a_purge`: after the ancient row is purged, the read names the
  actual survivor — it tracks reality, not the pre-purge state.

**Mutation control.** With the page-derived read restored inside the use case, the application
regression fails on the observed wrong value (`2026-08-30T17:08:22` where `2026-08-31T12:00:00`
was expected). Source restored, re-run green before the commit. This is the NEX-083 guard: the
test was proved to fail without the fix, not merely to pass with it.

### Local gates on the committed tree

`ruff format .` (1 file reformatted) then clean; `ruff check .` all checks passed; `mypy` 143
source files clean; `pytest` full suite exit 0 (4 skipped). Run bare, as `.github/workflows/ci.yml`
does.

### Security

Read-only, gated, unchanged scope. No new authority, no new endpoint, no migration. The gate still
runs before the purge. The change makes an audit field truthful, which is the security property:
a retention report that understates how far back the evidence reaches is a report an operator
cannot use to decide whether history was destroyed prematurely.

### Remaining

1. Poll CI on `82d8a64` to a `completed/success` verdict before writing VERIFIED anywhere.
2. Deliver to PR #5 body and issue #2.
3. Follow-up worth a ticket: `count_revoked()`/`revoked_grants()` still scan the grant table in
   Python (recorded as a risk); this slice is the same *shape* of defect — a bounded read standing
   in for an exact answer — so an audit of the remaining audit reads for page-derived values is
   warranted.

---
