/**
 * Pure board geometry and layout helpers.
 *
 * No React, no DOM, no network. Every function is deterministic and side-effect
 * free so the layout engine can be unit-tested in isolation and reasoned about
 * without a browser. `arrange` and friends never mutate their inputs.
 */

import {
  BOARD_SURFACES,
  MAX_SIZE,
  MIN_SIZE,
  STICKY_COLORS,
  STICKY_SHAPES,
  type ArrangeMode,
  type BoardCard,
  type BoardNote,
  type BoardSurface,
  type CardDimensions,
  type ShapeChoice,
  type StickyColor,
  type StickyShape,
} from "./types";

/** Board-space spacing between grid cells during automatic arrangement. */
export const GRID_ORIGIN = 48;
export const CELL_WIDTH = 224;
export const CELL_HEIGHT = 224;
export const GRID_COLUMNS = 4;

/** Dimension bounds, in board-space pixels, after the size multiplier. */
export const MIN_DIMENSION = 120;
export const MAX_DIMENSION = 420;

/** Base dimensions for each concrete shape at scale 1. */
const BASE_DIMENSIONS: Record<StickyShape, CardDimensions> = {
  square: { width: 180, height: 180 },
  wide: { width: 260, height: 150 },
  tall: { width: 150, height: 260 },
};

/** Clamp `value` into the inclusive `[min, max]` range. */
export function clamp(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

/**
 * A small, stable, non-cryptographic hash (FNV-1a, 32-bit).
 *
 * Deterministic across reloads and machines; used only to pick a default color.
 */
function hashString(value: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    // Multiply by the FNV prime (16777619) using shifts to stay in 32 bits.
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * The deterministic default color for a note.
 *
 * The same note id always yields the same color, so default colors are stable
 * across reloads and never need to be persisted.
 */
export function defaultColor(noteId: string): StickyColor {
  const palette = STICKY_COLORS;
  const index = hashString(noteId) % palette.length;
  // `noUncheckedIndexedAccess` needs the assertion; the modulo guarantees a hit.
  return palette[index] as StickyColor;
}

/** The effective color for a card: the explicit choice, else the default. */
export function resolveColor(card: Pick<BoardCard, "noteId" | "color">): StickyColor {
  return card.color ?? defaultColor(card.noteId);
}

/** Strip HTML tags and collapse whitespace to get plain text (never rendered). */
function toPlainText(html: string | undefined): string {
  if (!html) return "";
  return html
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Choose a shape from a note's content and title.
 *
 * Deterministic and cheap: it measures plain-text length and title length only,
 * and never parses the rich-text document.
 */
export function autoShape(note: Pick<BoardNote, "title"> & { content?: string }): StickyShape {
  const bodyLength = toPlainText(note.content).length;
  if (bodyLength > 220) return "tall";
  if (note.title.length > 40) return "wide";
  return "square";
}

/** The effective shape for a card: the explicit choice, or the note's auto. */
export function resolveShape(
  card: Pick<BoardCard, "shape">,
  note: Pick<BoardNote, "title"> & { content?: string },
): StickyShape {
  if (card.shape !== "auto") return card.shape;
  return autoShape(note);
}

/** Compute a card's pixel dimensions for a shape and size multiplier. */
export function cardDimensions(shape: StickyShape, size: number): CardDimensions {
  const base = BASE_DIMENSIONS[shape];
  const scale = clamp(size, MIN_SIZE, MAX_SIZE);
  return {
    width: clamp(Math.round(base.width * scale), MIN_DIMENSION, MAX_DIMENSION),
    height: clamp(Math.round(base.height * scale), MIN_DIMENSION, MAX_DIMENSION),
  };
}

/** Create a new card with sensible defaults at a board-space position. */
export function makeCard(noteId: string, x: number, y: number, z: number): BoardCard {
  return {
    noteId,
    x: Math.round(x),
    y: Math.round(y),
    color: null,
    shape: "auto",
    size: 1,
    z,
    pinned: false,
  };
}

/** The highest z-order currently in use (`0` when the board is empty). */
export function topZ(cards: readonly BoardCard[]): number {
  let max = 0;
  for (const card of cards) {
    if (card.z > max) max = card.z;
  }
  return max;
}

/** The board-space origin of grid cell `index`, laid out row-major. */
function cellOrigin(index: number): { x: number; y: number } {
  const column = index % GRID_COLUMNS;
  const row = Math.floor(index / GRID_COLUMNS);
  return {
    x: GRID_ORIGIN + column * CELL_WIDTH,
    y: GRID_ORIGIN + row * CELL_HEIGHT,
  };
}

/** Whether a card already sits on the given grid cell. */
function cellOccupied(cards: readonly BoardCard[], index: number): boolean {
  const { x, y } = cellOrigin(index);
  return cards.some(
    (card) => Math.abs(card.x - x) < CELL_WIDTH / 2 && Math.abs(card.y - y) < CELL_HEIGHT / 2,
  );
}

/**
 * The next free grid cell origin.
 *
 * Scans row-major and returns the first cell no existing card occupies, so a
 * newly added card does not land on top of another unless the board is full —
 * in which case it is placed past the last row.
 */
export function freeSlot(cards: readonly BoardCard[]): { x: number; y: number } {
  for (let index = 0; index < cards.length + GRID_COLUMNS; index += 1) {
    if (!cellOccupied(cards, index)) return cellOrigin(index);
  }
  return cellOrigin(cards.length + GRID_COLUMNS);
}

/** Cycle to the next palette color; `null` starts the cycle. */
export function nextColor(current: StickyColor | null): StickyColor {
  const palette = STICKY_COLORS;
  if (current === null) return palette[0] as StickyColor;
  const index = palette.indexOf(current);
  return palette[(index + 1) % palette.length] as StickyColor;
}

/** Cycle to the next concrete shape; `auto` starts the cycle. */
export function nextShape(current: ShapeChoice): StickyShape {
  const shapes = STICKY_SHAPES;
  if (current === "auto") return shapes[0] as StickyShape;
  const index = shapes.indexOf(current);
  return shapes[(index + 1) % shapes.length] as StickyShape;
}

/** Cycle to the next surface. */
export function nextSurface(current: BoardSurface): BoardSurface {
  const index = BOARD_SURFACES.indexOf(current);
  return BOARD_SURFACES[(index + 1) % BOARD_SURFACES.length] as BoardSurface;
}

/**
 * A concise, safe preview of a note's content.
 *
 * Returns plain text only — HTML is stripped, never rendered. Missing or empty
 * content falls back to an empty string so callers can decide on a placeholder.
 */
export function cardPreview(note: { content?: string }, maxLength = 140): string {
  const text = toPlainText(note.content);
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1).trimEnd()}…`;
}

/**
 * Sort key for a card under an arrangement mode.
 *
 * Pure and total: every card gets a defined key, so the sort is deterministic.
 */
function arrangeKey(
  card: BoardCard,
  notesById: Record<string, BoardNote | undefined>,
  mode: ArrangeMode,
): string {
  const note = notesById[card.noteId];
  switch (mode) {
    case "color":
      return resolveColor(card);
    case "folder":
      return note?.folder_id ?? "~root";
    case "recency":
      return note?.updated_at ?? "";
    default:
      return "";
  }
}

/**
 * Automatically arrange cards, returning a new array.
 *
 * - Pinned cards keep their exact position.
 * - Unpinned cards are placed into free grid cells, ordered by the mode:
 *   `grid` keeps input order, `color` groups by resolved color, `folder`
 *   groups by the note's folder, `recency` sorts newest-updated first.
 * - The input array and its cards are never mutated.
 */
export function arrange(
  cards: readonly BoardCard[],
  notesById: Record<string, BoardNote | undefined>,
  mode: ArrangeMode,
): BoardCard[] {
  const pinned = cards.filter((card) => card.pinned);
  const movable = cards.filter((card) => !card.pinned);

  const ordered = [...movable];
  if (mode === "recency") {
    ordered.sort((a, b) => {
      const keyA = arrangeKey(a, notesById, mode);
      const keyB = arrangeKey(b, notesById, mode);
      if (keyA === keyB) return a.noteId.localeCompare(b.noteId);
      return keyB.localeCompare(keyA); // newest first
    });
  } else if (mode !== "grid") {
    ordered.sort((a, b) => {
      const keyA = arrangeKey(a, notesById, mode);
      const keyB = arrangeKey(b, notesById, mode);
      if (keyA === keyB) return a.noteId.localeCompare(b.noteId);
      return keyA.localeCompare(keyB);
    });
  }

  // Lay the movable cards into the first free cells, treating pinned cards as
  // obstacles so the two sets never overlap.
  const occupied: BoardCard[] = [...pinned];
  const placed: BoardCard[] = [];
  for (const card of ordered) {
    const { x, y } = freeSlot(occupied);
    const moved: BoardCard = { ...card, x, y };
    placed.push(moved);
    occupied.push(moved);
  }

  // Preserve the original array order in the result for stable rendering.
  const byId = new Map(placed.map((card) => [card.noteId, card]));
  return cards.map((card) => (card.pinned ? { ...card } : (byId.get(card.noteId) as BoardCard)));
}
