# Whiteboard design

The whiteboard view lets a user arrange their notes spatially: multiple boards,
a tray of draggable notes, and sticky cards that can be moved, resized,
recoloured, reshaped, pinned, stacked, and auto-arranged.

This document describes how it is built, the invariants it must not break, and
the migration path to server-backed boards. The *why* is in
[ADR-010](ADR/ADR-010-whiteboard-client-side.md).

## G0 — Scope and non-goals

In scope: boards CRUD (create, rename, delete, switch), a note tray, drag-to-place,
card move/resize/recolour/reshape/pin/z-order, auto-arrange, pan/zoom, surfaces,
and per-user isolation.

Out of scope (deliberately): board sharing and permissions, real-time
multiplayer, connectors/arrows, free-drawing, and server-side board persistence
(deferred behind `BoardStore`).

## G1 — Model, geometry, and persistence (framework-independent)

`frontend/src/board/types.ts`, `layout.ts`, `store.ts` import **nothing from
React and touch no browser API** except inside `LocalStorageBoardStore`. This is
what makes the geometry unit-testable in isolation and keeps the layout engine
honest.

### Data model

```ts
Board     { id, name, folderId | null, surface, cards[], createdAt, updatedAt }
BoardCard { noteId, x, y, color | null, shape, size, z, pinned }
```

- `x`/`y` are **board-space** coordinates, never screen coordinates.
- `color: null` means "use the deterministic default" (`defaultColor(noteId)`),
  so a card's default colour is stable across reloads without being persisted.
- `shape: "auto"` derives a shape from the note (`autoShape`): tall for long
  bodies, wide for long titles, round for very short notes, square otherwise.
- `size` is a scale multiplier, clamped to `[MIN_SIZE, MAX_SIZE]`.

### Geometry (`layout.ts`)

All pure and deterministic:

- `clamp`, `cardDimensions(shape, size)` — clamped to pixel bounds.
- `defaultColor` (FNV-1a hash of the note id → palette index), `nextColor`,
  `nextShape`, `nextSurface` — deterministic cycling.
- `autoShape`, `resolveShape`, `resolveColor`, `cardPreview` (strips HTML to
  plain text; **never rendered**).
- `freeSlot(cards)` — first unoccupied grid cell, row-major.
- `arrange(cards, notesById, mode)` — returns a new array; **never mutates its
  input**; pinned cards keep their exact position and act as obstacles for the
  unpinned ones; modes are `grid` (input order), `color`, `folder`, `recency`
  (newest-updated first).

### Persistence (`store.ts`)

```ts
interface BoardStore {
  load(userKey: string): Board[];
  save(userKey: string, boards: Board[]): void;
}
```

- `LocalStorageBoardStore` — the prototype. Keyed `bluestaq.notes.boards.v1.<userId>`
  so users are isolated. Every storage call is guarded: storage may be
  unavailable (private mode, quota) and its contents are never trusted.
- `MemoryBoardStore` — tests.
- `parseBoards` validates every field against the allowed vocabularies and drops
  malformed entries (wrong version, unknown surface/colour/shape, bad numbers)
  rather than throwing. A persisted payload is untrusted input.
- `BOARD_STORE_VERSION` gates schema changes: a version bump makes old payloads
  parse as empty rather than as garbage.

## G2 — Components and state

```
BoardView            owns the single DndContext, pan/zoom, dialogs
├── NoteTray         useDraggable per note (source)
├── BoardCanvas      useDroppable (target) + the pan/zoom transform
│   └── StickyCard   useDraggable (handle only) + resize handle + controls
```

`useBoards` owns all board state and mutations. It is keyed by the current user's
id and depends on a `BoardStore`; it never calls the network. Notes come from
`useWorkspace`, which remains the only thing that talks to the API.

### DEF-03 — the droppable must live inside the DndContext

**dnd-kit resolves `useDraggable`/`useDroppable` through React context.** A
droppable registered *above* the `DndContext` can never receive a drop, and it
fails **silently** — no error, no warning, the drop simply does nothing. This is
DEF-03.

The rule: `BoardView` owns exactly one `DndContext`, and `BoardCanvas` (the
component calling `useDroppable`) is rendered **inside** it. Because the failure
is silent, it is pinned by a regression test that performs a real drag through
dnd-kit's pointer pipeline and asserts the persisted card
(`frontend/src/board/dragDrop.test.tsx`).

### Coordinates and transforms

- A card's `x`/`y` are board space and are never rewritten by panning or zooming.
- The inner `.canvas` layer carries `translate(pan) scale(zoom)`; the viewport is
  a separate scrolling/clipping element.
- A tray drop converts a screen-space rect to board space:
  `(screenCenter - viewportOrigin - pan) / zoom`.
- A card move divides the dnd-kit pointer delta by `zoom` before committing.
- Pan is pointer-driven on the canvas background; zoom is `ctrl`/`cmd` + wheel or
  the toolbar buttons, clamped to `[0.5, 2]`.

### Interaction details

- **Drag handle only.** The card is a draggable, but only the handle is the drag
  listener, so the body stays clickable (open note) and the controls stay usable.
- **Resize** is a pointer-capture handle; it clamps through `resizeCard`.
- **Selection** highlights the card and brings it to the front.
- **Pinning** keeps a card in place during `arrange`.
- **Surfaces** are cosmetic (`whiteboard`, `grid`, `dots`, `cork`).

## G3 — Testing

- `layout.test.ts` (34) — geometry, colour/shape resolution, arrangement,
  determinism, and non-mutation.
- `store.test.ts` (16) — per-user isolation, schema validation, corrupt-payload
  handling, unavailable/quota-throwing storage.
- `useBoards.test.tsx` (16) — board lifecycle, card mutations, arrangement,
  per-user isolation, persistence across remounts.
- `BoardView.test.tsx` (11) — board management and card controls through the UI.
- `dragDrop.test.tsx` (1) — **DEF-03**: a real drag from tray to canvas persists
  a card at a board-space position.
- `App.test.tsx` — the List/Board toggle and open-note-from-card.

`src/test/setup.ts` polyfills `PointerEvent` and pointer capture for jsdom. The
drag test models element rects because jsdom reports zero-size rects, so
collision detection would otherwise never find a target.

## G4 — Migration path to server-backed boards

The `BoardStore` interface is the seam. To move boards to the backend:

1. **Backend**: add `Board` and `BoardCard` as per-user owned resources
   (`GET/POST/PATCH/DELETE /api/v1/boards`, `PUT .../boards/{id}/cards`), scoped
   to the caller exactly like notes and stars — owner-only, `404` for
   inaccessible ids, no cross-user access. Persist `noteId` as a foreign key and
   reject cards that reference notes the caller cannot read.
2. **Frontend**: implement `ApiBoardStore implements BoardStore`, mapping the
   same `load`/`save` calls onto those endpoints (or, better, `load` on mount and
   a debounced `save`).
3. **Wiring**: pass the new store where `BoardView`/`useBoards` are composed
   (`App.tsx`). No component, hook, or layout code changes.

Because the store is the only persistence dependency, this is additive. The
authorization rules are the backend's, unchanged in shape: owner-only
management, `404` rather than `403` for inaccessible resources, and note content
still reached only through the existing notes API.
