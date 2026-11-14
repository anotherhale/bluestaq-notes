# Frontend integration guide

How the React workspace (`frontend/`) integrates with the Notes API. This is the
contract the client actually relies on, written against the *current* backend —
where the implementation and the older prose disagree, the code (and this file)
wins.

- [1. Running the stack](#1-running-the-stack)
- [2. Authentication model](#2-authentication-model)
- [3. The one HTTP client](#3-the-one-http-client)
- [4. State ownership](#4-state-ownership)
- [5. Notes API used by the client](#5-notes-api-used-by-the-client)
- [6. Folders API](#6-folders-api)
- [7. Starring](#7-starring)
- [8. Search, filtering and sorting](#8-search-filtering-and-sorting)
- [9. Permissions and sharing](#9-permissions-and-sharing)
- [10. Rich-text content and sanitisation](#10-rich-text-content-and-sanitisation)
- [11. Error envelope and status codes](#11-error-envelope-and-status-codes)
- [12. Optimistic concurrency](#12-optimistic-concurrency)
- [13. CORS](#13-cors)
- [14. Capability rules the UI mirrors](#14-capability-rules-the-ui-mirrors)

---

## 1. Running the stack

Backend (SQLite, development, demo auth on):

```bash
DATABASE_PROVIDER=sqlite DATABASE_URL="sqlite:///./notes.db" \
  ENVIRONMENT=development AUTH_DEMO_ENABLED=true \
  .venv/bin/notes-service init-db --seed

DATABASE_PROVIDER=sqlite DATABASE_URL="sqlite:///./notes.db" \
  ENVIRONMENT=development AUTH_DEMO_ENABLED=true \
  CORS_ALLOW_ORIGINS="http://localhost:5173" \
  .venv/bin/notes-service serve --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev   # http://localhost:5173
```

`VITE_API_BASE_URL` is read in exactly one place (`src/api/client.ts`). Leave it
unset to default to `http://127.0.0.1:8000`; set it to the empty string to make
requests same-origin (the Docker / reverse-proxy case).

---

## 2. Authentication model

The demonstration uses **development demo authentication**. It is enabled only
when `ENVIRONMENT=development` **and** demo auth is on; the production
environment refuses to activate it.

The client discovers who it may act as at runtime — it never hardcodes user ids:

```
GET /api/v1/demo/identities          # unauthenticated
200 { "enabled": true, "header": "X-Demo-User-ID", "items": [ {id, email, display_name}, ... ], "meta": {...} }
404 when demo mode is disabled
```

Flow in the client:

1. On boot, `useWorkspace` calls `getDemoIdentities()`. If enabled, it selects
   the first identity so the app is immediately usable.
2. Every subsequent request carries the chosen identity in the header named by
   the `header` field (`X-Demo-User-ID`), whose value must be one of the seeded
   ids. Unknown ids are rejected with `401`.
3. `GET /api/v1/me` resolves the current identity to a `CurrentUser`.
4. Switching identity clears **all** user-specific client state (selected note,
   draft, permissions, notes, folders, filters) before reloading.

Seeded demo users (stable ids used by tests):

| User | id |
|---|---|
| Alice | `11111111-1111-1111-1111-111111111111` |
| Bob | `22222222-2222-2222-2222-222222222222` |
| Carol | `33333333-3333-3333-3333-333333333333` |

The client shows an explicit **"Demo mode"** indicator in the header whenever
demo auth is enabled, and a clear message when the server has it disabled.

---

## 3. The one HTTP client

`src/api/client.ts` is the **only** module that calls `fetch`. Components never
know a URL. They depend on the `NotesApi` interface:

```ts
interface NotesApi {
  getDemoIdentities(): Promise<DemoIdentityList>;
  getCurrentUser(): Promise<CurrentUser | null>;
  selectIdentity(userId: string | null): void;
  selectedIdentity(): string | null;
  listUsers(): Promise<UserList>;
  listNotes(query?: NoteListQuery): Promise<NoteList>;
  createNote(input: NoteCreateInput): Promise<Note>;
  getNote(noteId: string): Promise<Note>;
  updateNote(noteId: string, input: NoteUpdateInput): Promise<Note>;
  deleteNote(noteId: string): Promise<void>;
  moveNote(noteId: string, input: NoteMoveInput): Promise<Note>;
  starNote(noteId: string): Promise<Note>;
  unstarNote(noteId: string): Promise<Note>;
  listFolders(): Promise<FolderList>;
  createFolder(input: FolderInput): Promise<Folder>;
  renameFolder(folderId: string, input: FolderInput): Promise<Folder>;
  deleteFolder(folderId: string): Promise<void>;
  listPermissions(noteId: string): Promise<PermissionList>;
  grantPermission(noteId: string, input: PermissionGrantInput): Promise<Permission>;
  revokePermission(noteId: string, userId: string): Promise<void>;
}
```

`HttpNotesApi` is the production implementation. `FakeApiClient`
(`src/test/fakeApiClient.ts`) implements the same interface with an in-memory
store that mirrors the backend's authorization rules, and is what the component
tests run against.

---

## 4. State ownership

There is a **single** application-state layer: `src/hooks/useWorkspace.ts`. It
owns the current user, the active view, the selected folder, the folder list,
the notes, the selected note and its draft, the search query, filters, sort,
star state, permissions, and all loading/error/conflict flags. Components are
presentational and receive data and callbacks through props.

Two rules the code follows:

- **No second state architecture.** No Redux/Zustand/context store was added.
- **No frontend authorization.** The hook may *hide* a control the backend would
  reject (better UX), but it never claims to enforce anything: every action is a
  real API call, and a rejection is surfaced verbatim from the backend.

Server-side filtering is deliberate: the hook sends the active
view/folder/query/sort to `GET /notes` and renders exactly what comes back, so
it can never display a note the caller may not read, and it never fetches
unauthorized notes to filter them in the browser.

---

## 5. Notes API used by the client

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/notes` | List (see §8 for query params) |
| `POST` | `/api/v1/notes` | Create a private note |
| `GET` | `/api/v1/notes/{id}` | Retrieve one note (with content) |
| `PUT` | `/api/v1/notes/{id}` | Update (optimistic concurrency) |
| `PATCH` | `/api/v1/notes/{id}/folder` | Move to a folder / the root |
| `PUT` | `/api/v1/notes/{id}/star` | Star for the caller |
| `DELETE` | `/api/v1/notes/{id}/star` | Unstar for the caller |
| `DELETE` | `/api/v1/notes/{id}` | Delete (owner only) |

### Note representation

`GET /notes` returns **summaries** (no content); `GET/POST/PUT/PATCH /notes…`
return the full note:

```json
{
  "id": "uuid",
  "owner_id": "uuid",
  "title": "Roadmap",
  "content": "<h1>Q4 Plan</h1><p><strong>bold</strong></p>",
  "version": 3,
  "folder_id": "uuid | null",
  "is_starred": true,
  "caller_permission": "read" | "write" | null,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

- `folder_id` is `null` for the root ("My Drive").
- `is_starred` is **the caller's own** star state, not a global flag.
- `caller_permission` is **the caller's own** effective level: `"write"` for the
  owner, `"read"`/`"write"` for a collaborator, `null` if the caller cannot read
  it. This is the field that lets a collaborator know whether they may edit
  **without** the server leaking the full collaborator list. The client derives
  `isOwner` by comparing `owner_id` to the current user id, and `canEdit` from
  `isOwner || caller_permission === "write"`.

### Creating a note

```json
POST /api/v1/notes
{ "title": "Roadmap", "content": "<p>…</p>", "folder_id": "uuid-or-null" }
```

`title` is required and non-blank; `content` is optional HTML (see §10);
`folder_id` is optional and, if given, must be a folder the caller owns.

---

## 6. Folders API

Folders are **flat** (no hierarchy — the domain does not model a parent) and
**owner-scoped**: they are an organisation tool, never a sharing boundary.
Sharing a note does not share its folder, and a folder never grants access to
the notes inside it.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/folders` | List the caller's folders |
| `POST` | `/api/v1/folders` | Create a folder |
| `PATCH` | `/api/v1/folders/{id}` | Rename a folder |
| `DELETE` | `/api/v1/folders/{id}` | Delete a folder |

```json
{ "id": "uuid", "owner_id": "uuid", "name": "Project Alpha",
  "created_at": "…", "updated_at": "…" }
```

Rules the client relies on:

- Folder names are unique **per owner**; a duplicate is `409 conflict`.
- A folder belonging to another user is reported as `404` (never `403`), so ids
  cannot be probed.
- **Deleting a folder does not delete its notes.** Its notes move to the root
  (their `folder_id` becomes `null`) and are preserved. The client's delete
  confirmation says exactly this.
- Creating a note with, or moving a note to, a folder you do not own is `422
  invalid_input`.
- Only the **note owner** may move a note. A WRITE collaborator can edit content
  but cannot reorganise someone else's note (`403`, because they *can* read it).

---

## 7. Starring

A star is **personal to the user**. The backend stores it as a `(note, user)`
relationship, so one collaborator starring a note does not star it for anyone
else.

```
PUT    /api/v1/notes/{id}/star    → 200 <Note>   (idempotent)
DELETE /api/v1/notes/{id}/star    → 200 <Note>   (idempotent)
```

- Both return the updated note with the **caller's** `is_starred`.
- Any user who may read the note may star it (including a READ collaborator on a
  shared note). A user who cannot read it gets `404`.
- The client updates the star **optimistically** and rolls back on failure.
- The "Starred" view is `GET /notes?starred=true`, scoped to the caller's own
  stars and to notes they can read.

---

## 8. Search, filtering and sorting

All of this is resolved **server-side** on `GET /api/v1/notes`:

| Param | Values | Meaning |
|---|---|---|
| `q` | free text | Case-insensitive match against title **or** content |
| `view` | `all` \| `owned` \| `shared` | All readable / owned by caller / shared *with* caller |
| `folder_id` | uuid | Only notes in that folder (caller's own folder) |
| `starred` | `true` | Only the caller's starred notes |
| `sort` | see below | Ordering |
| `limit`, `offset` | int | Pagination |

Sort keys: `updated_at`, `-updated_at` (default), `created_at`, `-created_at`,
`title`, `-title` (a leading `-` means descending).

Search runs against the caller's **authorized** set, so an unauthorized note can
never be returned even if it matches. `content` is searched as stored HTML, so a
plain-text term still matches.

Client behaviour:

- The search box is **debounced (250 ms)** before a request is sent.
- The sidebar views map to `view`/`starred`/`folder_id`; the header sort control
  maps to `sort`.
- Clearing the search restores the current view.

---

## 9. Permissions and sharing

Owner-only endpoints (unchanged from the original design):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/notes/{id}/permissions` | List collaborators |
| `POST` | `/api/v1/notes/{id}/permissions` | Grant / upgrade |
| `DELETE` | `/api/v1/notes/{id}/permissions/{user_id}` | Revoke |

```json
{ "user_id": "uuid", "permission": "read" | "write", "granted_by": "uuid", "created_at": "…" }
```

Authorization rules (enforced by the backend; the UI only mirrors them):

| Caller | Read | Update | Delete | Manage sharing | Move |
|---|---|---|---|---|---|
| Owner | ✅ | ✅ | ✅ | ✅ | ✅ |
| WRITE collaborator | ✅ | ✅ | ❌ | ❌ | ❌ |
| READ collaborator | ✅ | ❌ | ❌ | ❌ | ❌ |
| Unrelated user | ❌ (404) | ❌ (404) | ❌ (404) | ❌ (404) | ❌ (404) |

`GET /users` provides grant candidates. The Share dialog offers only users who
are not the owner and not already shared; an existing row can be changed to
READ/WRITE. Revocation requires an explicit confirmation.

The client deliberately does **not** show a READ/WRITE badge for a collaborator
in the list: the API only exposes the permission *list* to owners, so a
collaborator cannot know its own level beyond `caller_permission` on the note
itself. The list shows Owned/Shared and lets the backend enforce.

---

## 10. Rich-text content and sanitisation

**Representation.** Note content is a **sanitised HTML fragment** (TipTap's
`getHTML()` output), stored as text. This is backward-compatible: plain text
from before this feature is valid HTML with no markup and renders unchanged — no
migration was required. The editor is TipTap with `StarterKit`, `Underline`,
`Link`, and `Placeholder`.

**Sanitisation is a domain invariant.** `NoteContent` sanitises through `nh3`
(Ammonia bindings) on **construction**, so:

- it happens on write *and* on read/rehydration — content that somehow reached
  the database unsanitised is still safe when served;
- it happens in the domain, so it cannot be bypassed by a new transport.

The allowlist mirrors exactly the editor toolbar vocabulary: `p`, `br`, `hr`,
`h1`–`h3`, `strong`, `b`, `em`, `i`, `u`, `s`, `ul`, `ol`, `li`, `blockquote`,
`code`, `pre`, `a[href]`. Anything else is dropped.

Removed: `<script>`, `<style>`, `<iframe>`, event-handler attributes
(`onclick`, `onerror`, …), `javascript:` and `data:` URLs. Safe external links
are kept and gain `rel="noopener noreferrer"`.

**Client-side.** The editor is a controlled component: the draft lives in the
workspace hook and every edit reports HTML back up. The client renders HTML only
through TipTap, and never uses `dangerouslySetInnerHTML`. The backend is the
authority on safety; the client never trusts incoming HTML for anything
security-sensitive.

---

## 11. Error envelope and status codes

Every failure uses one envelope:

```json
{ "error": { "code": "not_found", "message": "…", "details": [] }, "request_id": "uuid" }
```

| Status | When | Client behaviour |
|---|---|---|
| `401` | No/invalid identity | Clear the user; prompt to pick an identity |
| `403` | Can read, but not allowed to act | Show the message (e.g. "only the owner may…") |
| `404` | Unreadable or nonexistent | Treat as gone; drop the selection, refresh the list |
| `409` | Optimistic-concurrency conflict | Keep the draft, show the conflict banner |
| `422` | Validation | Show the message |
| `429` | Rate limited | Show the message (respect `Retry-After`) |

The `404` vs `403` split is intentional and documented in
[ADR-008](ADR/ADR-008-http-error-semantics.md): unreadable resources are hidden
as `404`; a *known but insufficient* permission is `403`.

---

## 12. Optimistic concurrency

`PUT /api/v1/notes/{id}` requires the `version` the client last loaded:

```json
{ "title": "…", "content": "…", "version": 3 }
```

If the stored version differs, the backend returns `409 conflict` and does not
write. The client:

- sends the version it loaded (never a guess);
- on `409`, **preserves the user's unsaved content** and shows a conflict banner
  offering an explicit "Load the latest version" action — it never silently
  overwrites.

The client uses **explicit Save** (no autosave), which keeps the concurrency
contract honest and simple.

---

## 13. CORS

The backend ships with an **empty CORS allowlist**. A browser origin must be
explicitly allowed:

```bash
CORS_ALLOW_ORIGINS="http://localhost:5173"
```

If you forget this, the browser blocks the request and it *looks* like a network
or CORS failure in the console — that is the cause, not a client bug.

---

## 14. Capability rules the UI mirrors

The UI mirrors these so it does not offer actions that would fail, but the
**backend enforces every one of them independently**:

- Only the owner sees Move, Share, and Delete.
- READ collaborators get a read-only editor (no toolbar, disabled title).
- A star control is shown to any reader.
- Folder rename/delete controls are shown only on the caller's own folders
  (the folder list already contains only their folders).
- Switching identity clears all user-specific state.
