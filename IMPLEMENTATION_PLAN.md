# IMPLEMENTATION_PLAN.md

The plan followed to deliver the Bluestaq Notes Service, in priority order.
Priorities follow the assignment: P0 essential, P1 high value, P2 additional
hardening. P0 was completed before P1/P2 work was taken on.

## Operating mode

INSPECT → PLAN → DESIGN → IMPLEMENT → TEST → REVIEW → HARDEN → DOCUMENT →
DELIVER.

## P0 — Essential

| # | Item | Status |
|---|---|---|
| 1 | Scaffold: `pyproject.toml`, package layout, config, `.env.example`, `.gitignore` | ✅ |
| 2 | Domain: identity + notes contexts, value objects, invariants, errors | ✅ |
| 3 | Application: ports (repositories, UoW, identity, authorization, rate limit, clock) and use cases | ✅ |
| 4 | Infrastructure: SQLAlchemy models, repository adapters, UoW, engine | ✅ |
| 5 | Presentation: routers, Pydantic schemas, middleware, error handlers, dependencies | ✅ |
| 6 | Bootstrap: settings, composition root, app factory, logging, CLI | ✅ |
| 7 | Multi-user identity simulation (`DemoIdentityProvider`, seeding) | ✅ |
| 8 | Ownership enforcement + READ/WRITE sharing | ✅ |
| 9 | Critical authorization tests (the 15 mandatory scenarios) | ✅ |
| 10 | README with working startup instructions | ✅ |

## P1 — High value

| # | Item | Status |
|---|---|---|
| 11 | Unit of Work with explicit transaction boundaries | ✅ |
| 12 | Optimistic concurrency (version + atomic compare-and-set, HTTP 409) | ✅ |
| 13 | Alembic migrations (initial schema, clean-DB upgrade, no drift) | ✅ |
| 14 | PostgreSQL compatibility (same model/ports; integration tests) | ✅ |
| 15 | Repository contract tests (incl. rollback, FKs, unique constraints) | ✅ |
| 16 | Consistent error envelope + documented status codes | ✅ |
| 17 | Input limits + request body-size limit | ✅ |
| 18 | Rate limiting (port + in-memory adapter, configurable) | ✅ |
| 19 | Security headers + CORS allowlist | ✅ |
| 20 | Structured logging with request ids | ✅ |

## P2 — Additional hardening

| # | Item | Status |
|---|---|---|
| 21 | OIDC adapter (signature/issuer/audience/expiry/claims via PyJWT+JWKS) | ✅ implemented |
| 22 | Live-IdP end-to-end OIDC handshake | ⏸ deferred (documented) |
| 23 | Static architecture checks (`tests/architecture/`) | ✅ |
| 24 | Advanced observability (metrics/tracing) | ⏸ deferred (documented) |
| 25 | Production deployment guidance | ✅ (README) |

## Delivery phases (as executed)

1. **Inspect** the stub repository; confirm only a README existed.
2. **Design** the layer boundaries and domain model; capture decisions as ADRs.
3. **Implement** bottom-up: domain → application ports/use cases →
   infrastructure adapters → presentation → bootstrap/CLI.
4. **Test** continuously; write domain, application, auth, security,
   persistence, and API suites; add architecture checks.
5. **Harden** during verification, which surfaced and fixed three real defects
   (see below).
6. **Document**: README, spec, plan, `docs/` set, ADRs.
7. **Verify**: run all quality gates and live multi-user workflows on SQLite and
   PostgreSQL; confirm production misconfiguration is refused.

## Defects found and fixed during hardening

These were discovered by exercising the service, not by reading code, and each
was fixed with a regression test.

1. **List-valued settings rejected the documented CSV form.** `CORS_ALLOW_ORIGINS`
   and `OIDC_ALGORITHMS` are `list[str]`; pydantic-settings JSON-decoded the env
   value before the validator ran, so `a,b` failed at startup. Fixed with
   `Annotated[list[str], NoDecode]` plus a validator that accepts both CSV and
   JSON-array forms. (tests: `TestListSettingsFromEnvironment`)
2. **Timestamps serialised inconsistently across create vs. read, and were
   wrong on PostgreSQL.** SQLite returned naive datetimes (so create and read
   rendered differently); PostgreSQL shifted aware values by the server's UTC
   offset on a timezone-less column. Fixed with a `UTCDateTime` `TypeDecorator`
   (naive UTC on write, aware UTC on read). (tests:
   `test_timestamps_roundtrip_as_utc`, `test_timestamps_are_utc_aware_and_consistent`)
3. **PostgreSQL optimistic-concurrency test did not exercise the
   compare-and-set.** It reloaded the note after the first writer committed, so
   the domain version guard fired before the repository CAS ran; it passed for
   the wrong reason and only ran under `-m postgresql`. Rewritten to model two
   concurrent writers (both holding version 1), matching the SQLite test.

## Verification checklist (final)

- [x] Full test suite passes (SQLite).
- [x] PostgreSQL integration tests pass.
- [x] Ruff lint and format clean.
- [x] mypy (strict) clean.
- [x] Alembic upgrade from clean DB + `alembic check` (SQLite and PostgreSQL).
- [x] App starts per README instructions.
- [x] Multi-user workflows exercised (create, share READ, deny write, upgrade
      WRITE, allow write, revoke, deny) on both providers.
- [x] Cross-user isolation verified.
- [x] Stale-update conflict returns 409.
- [x] Demo auth cannot be activated in production.
- [x] No secrets committed; `.env` ignored, `.env.example` provided.
- [x] Documentation matches implemented behaviour.
