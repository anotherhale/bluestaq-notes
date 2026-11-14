# AGENTS.md — repository knowledge

Persistent notes for agents working in this repository. Keep this file focused on
things that are easy to get wrong and expensive to rediscover.

## Layout

- Backend (Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic) lives in `src/notes_service/`
  with a DDD / hexagonal split: `domain/`, `application/` (ports + use cases),
  `infrastructure/`, `presentation/`, `bootstrap/`. The `notes-service` console
  script is the entry point (`init-db`, `serve`, ...).
- Frontend (React + TypeScript + Vite) lives in `frontend/`.
- Docs: `docs/` (ARCHITECTURE, DOMAIN_MODEL, SECURITY, API, ADR/), plus
  `OPENHANDS_SPEC.md`, `IMPLEMENTATION_PLAN.md`, and the root `README.md`.

## Backend

- Architecture boundary: `domain/` and `application/` must NOT import FastAPI,
  SQLAlchemy, or JWT libraries. This is enforced by an AST-based test in
  `tests/architecture/test_layering.py`; keep it green.
- Run gates: `.venv/bin/ruff format`, `.venv/bin/ruff check`, `.venv/bin/mypy`,
  `.venv/bin/python -m pytest`.
- Local run for the frontend:
  ```bash
  DATABASE_PROVIDER=sqlite DATABASE_URL="sqlite:///./notes.db" \
    ENVIRONMENT=development .venv/bin/notes-service init-db --seed
  DATABASE_PROVIDER=sqlite DATABASE_URL="sqlite:///./notes.db" \
    ENVIRONMENT=development CORS_ALLOW_ORIGINS="http://localhost:5173" \
    .venv/bin/notes-service serve --host 127.0.0.1 --port 8000
  ```

### Demo authentication

- Enabled only when `ENVIRONMENT=development` **and** demo auth is on. The
  production environment refuses to activate it.
- Requests select a demo identity with the `X-Demo-User-ID` header, whose value
  must be one of the seeded users. Unknown ids are rejected.
- Seeded demo users (stable UUIDs used by tests and the frontend):
  - Alice `11111111-1111-1111-1111-111111111111`
  - Bob   `22222222-2222-2222-2222-222222222222`
  - Carol `33333333-3333-3333-3333-333333333333`
- `GET /api/v1/demo/identities` is an **unauthenticated** discovery endpoint: a
  client must learn which identities exist *before* it can authenticate, and it
  must not hardcode UUIDs. It returns `404` when demo mode is disabled
  (including in production). This is the only unauthenticated data endpoint
  besides the health probes.

### Security invariants (do not regress)

- Notes are private by default; only the owner may delete or manage sharing.
- Inaccessible resources return **404**, not 403, for read/update paths to avoid
  leaking existence; a *known but insufficient* permission (e.g. a READ user
  attempting an update) returns **403**. This split is intentional — see
  `docs/ADR/ADR-008`.
- Optimistic concurrency: `PUT /notes/{id}` requires the current `version`; a
  stale value returns **409**. Never silently last-write-wins.
- No `owner_id` or caller identity is ever taken from a request body.
- CORS ships with an **empty allowlist** — a browser origin must be explicitly
  allowed via `CORS_ALLOW_ORIGINS`. This trips people up when wiring the
  frontend; the error looks like a network/CORS failure in the browser console.

## Frontend

- Only `frontend/src/api/client.ts` calls `fetch`. Components depend on the
  `NotesApi` interface, not on HTTP. `App` accepts an injectable `api` prop for
  tests.
- The base URL comes from `VITE_API_BASE_URL` (default `http://127.0.0.1:8000`).
- Gates: `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, and
  `BACKEND_BASE_URL=... npm run test:integration` (needs a live dev backend).
- `vite` is pinned to `^5` because `vitest@2` bundles `vite@5`; mixing in
  `vite@6` causes a duplicate-vite type error in the config files.
- The UI intentionally does **not** display a READ/WRITE badge for collaborators:
  the API only exposes permission lists to owners, so a collaborator cannot know
  its own level. It shows Owned/Shared and lets the backend enforce. Do not
  "fix" this by guessing a level — it would be a lie.
