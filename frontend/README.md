# Bluestaq Notes — Web Client

A small, polished React + TypeScript client that exercises the
[Bluestaq Notes REST API](../README.md) end to end. It exists to **demonstrate
and verify** the backend's multi-user behaviour — private ownership, READ/WRITE
sharing, revocation, and optimistic concurrency — through a real UI rather than
only through curl.

It is deliberately minimal. There is no state-management library, no component
framework, and no data-fetching library: the whole app is React + `fetch` +
a small amount of CSS.

> **This is a development/demo client.** It authenticates by sending the
> backend's demo header (`X-Demo-User-ID`) so you can switch between seeded
> users with one dropdown. It is **not** production authentication, and the UI
> says so on screen.

---

## 1. What it demonstrates

- **Identity switching** — pick any seeded demo user and act as them instantly.
- **Ownership** — notes you own appear under **Owned**; notes shared with you
  appear under **Shared with me**.
- **Sharing** — an owner can grant `read` or `write`, upgrade a level, or revoke
  access, all from a dialog.
- **Server-enforced authorization** — the client never pretends to enforce
  anything. When a non-owner attempts a modification the backend rejects, the
  API's error is shown verbatim.
- **Optimistic concurrency** — if someone else changed a note while you were
  editing, saving returns `409` and the UI preserves your unsaved draft instead
  of silently discarding or overwriting it.
- **Honest capability model** — see [§5](#5-the-capability-model-why-the-ui-does-not-show-a-permission-level).

## 2. Prerequisites

- Node.js 18+ (developed on Node 24) and npm.
- The backend running in development mode with demo auth enabled and a seeded
  database. From the repository root:

  ```bash
  # One-time: create and seed the SQLite database
  DATABASE_PROVIDER=sqlite DATABASE_URL="sqlite:///./notes.db" \
    ENVIRONMENT=development .venv/bin/notes-service init-db --seed

  # Run the API (allow the Vite dev origin so the browser can call it)
  DATABASE_PROVIDER=sqlite DATABASE_URL="sqlite:///./notes.db" \
    ENVIRONMENT=development \
    CORS_ALLOW_ORIGINS="http://localhost:5173" \
    .venv/bin/notes-service serve --host 127.0.0.1 --port 8000
  ```

  `CORS_ALLOW_ORIGINS` matters: the backend ships with **no** cross-origin
  allowlist, so a browser at `http://localhost:5173` cannot call it until you
  allow that exact origin. This is intentional (explicit allowlist, deny by
  default) and is documented in `docs/SECURITY.md`.

## 3. Install and run

```bash
cd frontend
npm install

# Point the client at the API and start the dev server
VITE_API_BASE_URL="http://127.0.0.1:8000" npm run dev
```

Open the printed URL (default <http://localhost:5173>). You should see the
identity switcher populated with the seeded demo users and the notes list.

`VITE_API_BASE_URL` defaults to `http://127.0.0.1:8000` when unset, so for the
common case you can just run `npm run dev`.

## 4. Try the multi-user flow

1. As **Alice**, click **New note**, give it a title, and create it. It appears
   under **Owned**.
2. Open it and click **Share…**. Grant **Read** to **Bob**, then **Done**.
3. In the identity switcher, switch to **Bob**. The note now appears under
   **Shared with me**.
4. Open it. Note there is **no Share and no Delete control** — Bob is not the
   owner. Edit the content and click **Save**: the backend returns `403` and the
   UI shows the error.
5. Switch back to **Alice**, click **Share…**, and change Bob's level to
   **Write**. Switch to **Bob** again and save an edit — it now succeeds and the
   version number increments.
6. As **Alice**, revoke Bob's access. Switch to **Bob**: the note is gone.
7. As **Alice**, open a note in two contexts (or edit after another writer
   changed it) and save a stale version to see the `409` conflict banner.

## 5. The capability model (why the UI does not show a permission level)

The backend only lets an **owner** list a note's permissions. A collaborator
cannot ask "what is my level on this note?", so the client genuinely does not
know whether it holds READ or WRITE until it tries.

Rather than guess (and risk displaying "read-only" on a note the user can
actually edit, or vice versa), the client:

- shows an **Owned / Shared** badge, which it can determine from `owner_id`;
- offers **Share** and **Delete** only to the owner;
- otherwise lets the user attempt an edit and surfaces the backend's decision.

This keeps the UI truthful. Authorization is always the backend's job; the
client merely avoids offering actions that are certain to fail.

## 6. Architecture

```
src/
├── api/
│   ├── client.ts       NotesApi interface + HttpNotesApi (the only place fetch is called)
│   ├── errors.ts       ApiError — the parsed error envelope + request id
│   └── types.ts        Wire types mirroring the backend's response models
├── hooks/
│   └── useWorkspace.ts Single stateful hook: identities, notes, selection, drafts, sharing
├── components/
│   ├── AppHeader.tsx / IdentitySwitcher.tsx
│   ├── NoteWorkspace.tsx / NoteList.tsx / NoteEditor.tsx
│   ├── CreateNoteForm.tsx / ShareDialog.tsx
│   └── Banner.tsx / EmptyState.tsx
├── lib/                Small pure helpers (date + title formatting)
├── test/
│   ├── fakeApiClient.ts        In-memory NotesApi with the backend's real rules
│   └── integration/workflow.test.ts  Tests against a live backend
├── App.tsx             Composition root; wires the client into the hook
└── main.tsx            React entry point
```

**Dependency direction:** components → hook → `NotesApi` interface → transport.
Components never call `fetch`; only `HttpNotesApi` does. `App` accepts an
injectable `api` prop, which is how tests substitute the fake.

## 7. Tests

Two suites:

```bash
# Unit + component tests (fast, no network)
npm test

# Integration tests against a REAL backend (see below)
BACKEND_BASE_URL="http://127.0.0.1:8000" npm run test:integration
```

- **Unit / component** (`npm test`): the HTTP client's header, error-envelope,
  and `204`/`401`/network handling; the workspace hook's state transitions; and
  component tests that drive the whole `App` against `FakeApiClient`. The fake
  **reproduces the backend's authorization rules** (ownership, READ/WRITE,
  revocation, 403-vs-404, optimistic concurrency) so the failure paths — not
  just the happy path — are exercised.
- **Integration** (`test:integration`): runs the real `HttpNotesApi` against a
  live FastAPI service, asserting the actual status codes the backend returns
  (including `404` for another user's private note and `409` for a stale
  update). It needs a development backend with demo users seeded; it creates and
  cleans up its own note.

## 8. Scripts

| Script | Purpose |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | Type-check (`tsc -b`) and produce a production build |
| `npm run preview` | Serve the production build |
| `npm run typecheck` | Type-check only |
| `npm run lint` | ESLint |
| `npm test` | Unit + component tests |
| `npm run test:integration` | Tests against a live backend |

## 9. Known limitations

- Demo-header authentication only; there is no login screen or token storage.
- No optimistic UI updates; the list refreshes from the backend after mutations.
- No offline support, routing, or deep links — it is a single view.
- The permission dialog lists every active user; it is not paginated.

These are deliberate scope choices for a demonstration client, not oversights.
