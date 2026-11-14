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

### Folders, stars, search, rich text (added on top of the above)

- **Folders are flat and owner-scoped** (`domain/notes/folder.py`). No parent
  hierarchy. A folder is *organisation only*, never a sharing boundary: sharing
  a note never shares its folder, and a folder never grants access to the notes
  in it. Deleting a folder reassigns its notes to the root — it never deletes
  notes.
- **Only the note owner may move a note** (`PATCH /notes/{id}/folder`). A WRITE
  collaborator gets **403** (they can read it, so it is not 404). A move never
  changes the owner or the permission set.
- **Stars are per-user** (`domain/notes/star.py`, `NoteStarRow`), not a boolean
  on the note. Note responses expose the *caller's own* `is_starred`. Any reader
  may star.
- **Rich text is sanitised HTML, as a domain invariant**: `NoteContent`
  sanitises via `nh3` on construction (`domain/notes/rich_text.py`), so it runs
  on write *and* read. Plain text is valid HTML, so no content migration was
  needed. Never trust client HTML; never add `dangerouslySetInnerHTML`.
- **Search/filter/sort is server-side** on `GET /notes` via
  `q`/`view`/`folder_id`/`starred`/`sort`/`limit`/`offset`, applied to the
  caller's authorized set (SQL `LIKE`). The frontend debounces and never fetches
  unauthorized notes to filter them.
- Note responses include `folder_id`, `is_starred`, and `caller_permission`
  (the caller's own effective level: `"write"` for owner, `"read"`/`"write"` for
  a collaborator, `null` if unreadable). `caller_permission` is how the client
  learns whether a collaborator may edit without the server leaking the full
  permission list to non-owners.
- Full contract: `docs/FRONTEND_INTEGRATION.md`; design: `docs/ADR/ADR-009`.

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
- All application state lives in `frontend/src/hooks/useWorkspace.ts` (current
  user, view, folder, notes, selection, search, filters, sort, stars,
  permissions). Components are presentational; do not add a second state store.
- The editor is TipTap (`@tiptap/react` + `starter-kit`, `underline`, `link`,
  `placeholder`), producing sanitised HTML. Save is **explicit** and uses the
  backend's `version`; a `409` must preserve the user's draft and show a conflict
  banner — never silently overwrite. READ collaborators get a read-only editor.
- Search/filter/sort are server-side (the hook passes `q`/`view`/`folder_id`/
  `starred`/`sort` to `listNotes` and renders what returns). The search box is
  debounced; do not fetch unauthorized notes and filter in the browser.
- Component tests run against `src/test/fakeApiClient.ts`, which mirrors the
  backend's authorization rules. Keep the fake honest: it is what proves the UI
  works against a correct backend.
- `styles.css` holds the base design tokens/layout; `styles.workspace.css`
  (imported after it in `main.tsx`) holds the Drive-style workspace styles.

## Docker

- `docker compose up --build` starts the whole stack: `db` (postgres:16-alpine),
  `api` (root `Dockerfile`), `web` (frontend/Dockerfile → nginx). Web is on
  `:8080`, API on `:8000`.
- The API entrypoint (`docker/api-entrypoint.sh`) waits for the DB, runs
  `alembic upgrade head`, seeds demo users (`SEED_DEMO_USERS=true`), then execs
  uvicorn. Compose gates `web` on the API's healthcheck.
- The frontend image is built with `VITE_API_BASE_URL=""`, which the client
  treats as **same origin**; nginx (`frontend/docker/nginx.conf`) proxies `/api`,
  `/health`, `/readiness` to the `api` service. So there is no CORS and no baked
  hostname. Keep that contract if you change `resolveBaseUrl()`.
- Copy `.env.docker.example` → `.env` to override ports/credentials. `.env` is
  git-ignored; `.env.docker.example` is intentionally tracked (it has no secrets).
- `python3 docker/smoke-test.py` runs 26 end-to-end checks through the proxy
  (needs only stdlib + a running stack). Use it to verify the stack after changes.
- Gotcha: the container runs as non-root, so scripts copied in must be
  world-readable/executable — the Dockerfile uses explicit `chmod 0755` (a bare
  `chmod +x` on a `0600` context file yields `0700` and the non-root user cannot
  read it).
