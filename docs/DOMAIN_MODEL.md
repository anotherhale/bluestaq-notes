# Domain Model

The domain is pure Python with no framework dependencies. It is split into two
bounded contexts — **identity** and **notes** — under `src/notes_service/domain/`.

## Bounded contexts

```
domain/
├── errors.py            # shared domain error taxonomy
├── identity/            # who a caller is
│   ├── value_objects.py
│   ├── entities.py
│   └── principal.py
└── notes/               # notes and their sharing
    ├── value_objects.py
    ├── permission.py
    └── note.py
```

## Identity context

### Value objects (`identity/value_objects.py`)

| Type | Description | Key rules |
|---|---|---|
| `UserId` | `uuid.UUID` subclass | `new()`, `parse()` |
| `Email` | normalised (`strip().lower()`) address | validated shape, ≤ 320 chars, `domain` property |
| `UserStatus` | `StrEnum`: `active`, `suspended`, `disabled` | `can_authenticate` is true only for `active` |
| `ExternalIdentityId` | `uuid.UUID` subclass | identifies an IdP link |
| `ProviderSubject` | frozen dataclass `(provider, subject)` | non-empty, bounded lengths |

### Entities (`identity/entities.py`)

- **`User`** — a local account: `id: UserId`, `email: Email`,
  `display_name`, `status: UserStatus`, `created_at`, `updated_at`. Factory
  `User.register(...)` defaults status to `active`. `is_active` gates
  authentication.
- **`ExternalIdentity`** — links a `(provider, subject)` pair to a `UserId`.
  This is how an OIDC `sub` is mapped to a local account. `link(...)` is the
  factory.

### `Principal` (`identity/principal.py`)

The authenticated caller, produced by the identity provider and passed into
every use case. Carries the authoritative `user_id` plus non-authoritative
provenance (`provider`, `subject`, `claims`). **The domain never derives
ownership from anything except `principal.user_id`.**

## Notes context

### Value objects (`notes/value_objects.py`)

| Type | Description | Key rules |
|---|---|---|
| `NoteId` | `uuid.UUID` subclass | `new()`, `parse()` |
| `NoteTitle` | `str` subclass | trimmed, non-blank, ≤ `MAX_TITLE_LENGTH` (200), no NUL |
| `NoteContent` | `str` subclass | **sanitised HTML** (see below); may be empty, ≤ `MAX_CONTENT_BYTES` (64 KiB UTF-8), no NUL |
| `NoteVersion` | `int` subclass | ≥ 1, `next()` increments by one |
| `FolderId` | `uuid.UUID` subclass | `new()`, `parse()` |
| `FolderName` | `str` subclass | trimmed, non-blank, ≤ `MAX_FOLDER_NAME_LENGTH` (100), no NUL |
| `PermissionLevel` | `StrEnum`: `read`, `write` | ordered via `rank`; `includes()`; `parse()` |

`NoteContent` is the sanitisation boundary. Every value is run through
`sanitize_html` (`notes/rich_text.py`, backed by `nh3`) **on construction**, so
the invariant holds on both write and read and cannot be bypassed by a new
transport. The allowlist is exactly the editor's vocabulary (`p`, `br`, `hr`,
`h1`–`h3`, `strong`/`b`, `em`/`i`, `u`, `s`, `ul`/`ol`/`li`, `blockquote`,
`code`/`pre`, `a[href]`); scripts, iframes, event handlers and
`javascript:`/`data:` URLs are removed. Plain text remains valid input, so the
move from plain text to HTML needs no migration.

### `NotePermission` (`notes/permission.py`)

An entity: one `(note, user) → level` grant with audit fields
(`granted_by`, `created_at`). Invariant: a user cannot grant a permission to
themselves. `upgrade_to(level)` replaces the level on re-share.

### `Note` aggregate root (`notes/note.py`)

Owns the note's fields **and** its permission set, so sharing invariants are
enforced in one place.

```
Note
├── id: NoteId
├── owner_id: UserId            # exactly one owner, fixed at creation
├── title: NoteTitle
├── content: NoteContent        # sanitised HTML
├── version: NoteVersion        # optimistic concurrency
├── folder_id: FolderId | None  # owner-scoped organisation only
├── created_at / updated_at
└── permissions: list[NotePermission]
```

**Invariants (enforced in the aggregate):**

1. Exactly one owner, set at creation and never transferable.
2. The owner is never represented by a `NotePermission` (implicitly full
   access) — no self-grant.
3. A user holds at most one permission; `WRITE` supersedes `READ`.
4. Only the owner may `update_content`, `grant_permission`,
   `revoke_permission`, or change `folder_id`.
5. At most `MAX_PERMISSIONS_PER_NOTE` (100) grants.
6. Content mutations bump `version`; sharing and folder changes touch
   `updated_at` only.

**Behavioural methods:**

- `Note.create(owner_id, title, content, folder_id=None, note_id=None)` — factory.
- `update_content(actor_id, title, content, expected_version, now=None)` —
  owner-only; checks the expected version and increments it.
- `move_to_folder(actor_id, folder_id, now=None)` — owner-only; sets the folder
  (or the root). Never changes the owner or the permission set.
- `grant_permission(actor_id, target_user_id, level, now=None)` — owner-only;
  inserts or upgrades a grant.
- `revoke_permission(actor_id, target_user_id, now=None) -> bool` — owner-only;
  returns whether a grant was removed.
- Queries: `is_owned_by`, `effective_level`, `can_read`, `can_write`,
  `can_manage`.

### `Folder` aggregate root (`notes/folder.py`)

A flat, owner-scoped container. There is deliberately **no** parent/child
hierarchy — the need is "group my notes", and a flat model keeps move/delete
semantics unambiguous (ADR-009).

```
Folder
├── id: FolderId
├── owner_id: UserId
├── name: FolderName            # unique per owner
└── created_at / updated_at
```

- `Folder.create(owner_id, name, folder_id=None)` — factory.
- `rename(actor_id, name, now=None)` — owner-only.
- `assert_owned_by(actor_id)` — raises `InvariantViolation` otherwise.

Folders are **not** a sharing boundary: sharing a note never grants access to
its folder, and deleting a folder reassigns its notes to the root rather than
deleting them.

### `NoteStar` (`notes/star.py`)

A per-user bookmark: `(note_id, user_id)`. It is intentionally **not** a field
on `Note`, so one collaborator starring a note does not star it for everyone.
Any user who may read a note may star it.

## Error taxonomy (`domain/errors.py`)

- `DomainError` — base.
- `ValidationError` — invalid input (bad title, malformed id).
- `InvariantViolation` — an operation would break an aggregate invariant.
- `VersionConflict(expected, actual)` — optimistic-concurrency failure.

These carry no HTTP knowledge; the presentation layer maps them to status
codes (see `docs/ADR/ADR-008-http-error-semantics.md`).

## Where authorization lives

The aggregate enforces **structural** invariants and exposes query helpers, but
the authoritative "may this principal do this?" decision is made by the
`AuthorizationProvider` port in the application layer (ADR-004). This keeps the
policy replaceable while the invariants stay in the domain.

## Persistence mapping

The domain knows nothing about SQLAlchemy. The mapping lives entirely in
`infrastructure/db/repositories.py`, which translates `UserRow`/`NoteRow`/
`NotePermissionRow`/`FolderRow`/`NoteStarRow` rows to and from these domain
objects. See `docs/ADR/ADR-005-sqlalchemy-repository-uow.md`.
