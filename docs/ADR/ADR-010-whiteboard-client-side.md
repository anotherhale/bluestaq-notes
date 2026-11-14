# ADR-010: Whiteboards are a client-side spatial view, not a new domain aggregate

## Status

Accepted (prototype scope).

## Context

The workspace needed a Google-Drive-style "whiteboard" view: multiple boards,
dragging notes from a tray onto a canvas, and moving/resizing/recolouring the
resulting sticky cards.

Every board feature touches *presentation* state — where a card sits, how big it
is, what colour it is. None of it changes a note, who owns it, or who may read
it. The tempting move is to model `Board` and `BoardCard` as new backend
aggregates with their own tables, endpoints, migrations, and authorization. That
is real work, and it would also mean a round-trip (and a failure mode) for every
pointer move.

The guiding constraints:

- The backend stays authoritative for persistence, authentication,
  authorization, and domain behaviour.
- Do **not** simulate *persistent domain* features in the browser.
- A board is a way of *looking at* notes, not a new kind of note.

## Decision

**A board is a per-user spatial annotation of existing notes, and it is stored
client-side for this prototype.** A board holds no note data: a `BoardCard` is
`{ noteId, x, y, color, shape, size, z, pinned }`, and the note itself continues
to come from the workspace (the only module that talks to the API).

Concretely:

- **Boards are per-user, like a star or a folder.** They are keyed by the current
  demo user's id, so switching users shows a different board set and no boards
  leak between users. This mirrors the backend's per-user star model.
- **A card never mutates a note.** Removing a card from a board removes only the
  placement; the note is untouched. Opening a card opens the real note in the
  existing editor, with all existing authorization applied.
- **The backend is still the authority for everything that is domain state.**
  Note CRUD, folders, stars, sharing, permissions, rich-text content, and
  optimistic concurrency are unchanged and continue to be enforced by the
  backend. The board adds no new authorization surface because it grants no new
  access: it can only show notes the workspace already returned for that user.
- **Persistence is behind an interface.** `BoardStore` (`load`/`save`) is the
  only persistence dependency. `LocalStorageBoardStore` is the prototype
  implementation; `MemoryBoardStore` backs tests. Swapping in an `ApiBoardStore`
  later is a one-line change at the composition point and touches no component.

### Why client-side is honest here

The instruction "do not simulate persistent features using local storage"
targets *domain* features: folders, stars, sharing, note content. Those are all
backend-owned. A board is explicitly a *view* over that data. Storing the view's
layout client-side is the same category of decision as remembering a scroll
position or a sort order — and, crucially, the app never claims a note was
created, shared, or moved as a result of a board interaction.

Two properties keep this defensible:

1. **No data is faked.** Every note on a board is a real note fetched through the
   real API for the real user. If the backend refuses to return it, it cannot
   appear on a board.
2. **The seam is explicit.** The `BoardStore` interface is the documented
   migration point (see `docs/WHITEBOARD_DESIGN.md` § G4), so the prototype
   choice is reversible without rework.

## Consequences

### Positive

- No migrations, endpoints, or authorization rules are added for a purely
  cosmetic feature; the domain model and security boundaries are untouched.
- Card drags are instant (no network per move), which is what makes a canvas feel
  usable.
- The board reuses the workspace's notes and the existing editor, so there is one
  source of truth and one authorization path.

### Negative / accepted

- Boards do not sync across devices or browsers, and are lost if browser storage
  is cleared. Acceptable for a demonstration; the `BoardStore` seam is the fix.
- Boards are per-user, not shared. There is no board-level collaboration, by
  design (board sharing was explicitly out of scope).
- `localStorage` contents are untrusted input: they can be edited, truncated, or
  written by an older schema. `parseBoards` validates every field and drops
  anything malformed rather than throwing.

### Rejected alternatives

- **Full backend aggregate (boards + cards tables, CRUD endpoints).** Correct for
  a production whiteboard, disproportionate here: it adds domain surface,
  migrations, and authorization for a feature that grants no new access, and it
  would make every pointer move a network round-trip.
- **Boards as a shared, permissioned resource.** Out of scope; it would create a
  second authorization model parallel to note sharing, which the assignment
  explicitly warns against.
- **Storing boards inside a note.** Conflates a view with content and would make
  one note's edit a board edit.

## Related

- `docs/WHITEBOARD_DESIGN.md` — the implementation design and the G4 migration path.
- ADR-009 — folders, per-user stars, search, rich text (the domain features that
  *are* backend-owned).
- `frontend/src/board/` — the implementation.
