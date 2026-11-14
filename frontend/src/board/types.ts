/**
 * Whiteboard domain types.
 *
 * This module is deliberately framework-independent: it imports nothing from
 * React and touches no browser API. Everything here is plain data plus the
 * named vocabularies the board speaks (colors, shapes, surfaces, sizes).
 *
 * A `Board` is a *per-user spatial organisation* of existing notes — the same
 * category of thing as a star or a folder. A `BoardCard` annotates a note with
 * board-specific presentation data (position, color, shape, size, z-order,
 * pin); it is never a note and never changes one. See `docs/WHITEBOARD_ADR.md`.
 */

/** The named sticky-note palette. The theme maps these to actual colors. */
export type StickyColor = "yellow" | "pink" | "mint" | "sky" | "peach" | "lilac";

/** Every palette entry, in cycling order. */
export const STICKY_COLORS: readonly StickyColor[] = [
  "yellow",
  "pink",
  "mint",
  "sky",
  "peach",
  "lilac",
];

/** The sticky-card shapes. */
export type StickyShape = "square" | "wide" | "tall";

/** A concrete shape or `auto` (derive it from the note). */
export type ShapeChoice = StickyShape | "auto";

/** Every concrete shape, in cycling order (auto is not part of the cycle). */
export const STICKY_SHAPES: readonly StickyShape[] = ["square", "wide", "tall"];

/** Cosmetic canvas backgrounds. */
export type BoardSurface = "whiteboard" | "cork" | "grid" | "dots";

/** Every surface, in cycling order. */
export const BOARD_SURFACES: readonly BoardSurface[] = ["whiteboard", "cork", "grid", "dots"];

/** How a card is placed on its board. */
export interface BoardCard {
  /** The note this card annotates. */
  noteId: string;
  /** Board-space X (never a screen coordinate). */
  x: number;
  /** Board-space Y. */
  y: number;
  /** Explicit palette choice; `null` means "use the deterministic default". */
  color: StickyColor | null;
  /** Concrete shape or `auto`. */
  shape: ShapeChoice;
  /** Scale multiplier applied to the shape's base dimensions. */
  size: number;
  /** Stacking order; higher is nearer the viewer. */
  z: number;
  /** Pinned cards keep their position during automatic arrangement. */
  pinned: boolean;
}

/** A named, user-owned collection of cards. */
export interface Board {
  id: string;
  name: string;
  /** Optional owner-only convenience; never determines membership. */
  folderId: string | null;
  surface: BoardSurface;
  cards: BoardCard[];
  /** ISO-8601 timestamp. */
  createdAt: string;
  /** ISO-8601 timestamp. */
  updatedAt: string;
}

/**
 * The projection of a note the board actually needs.
 *
 * A full `NoteSummary` satisfies this structurally, so the board never needs a
 * second note model — it consumes the workspace's notes as-is.
 */
export interface BoardNote {
  id: string;
  title: string;
  folder_id: string | null;
  created_at: string;
  updated_at: string;
}

/** A note plus the optional plain-text body used for previews/auto-shaping. */
export interface BoardNoteDetail extends BoardNote {
  content?: string;
}

/** The automatic arrangement strategies. */
export type ArrangeMode = "grid" | "color" | "folder" | "recency";

/** A card's computed pixel dimensions in board space. */
export interface CardDimensions {
  width: number;
  height: number;
}

/** Minimum and maximum scale multipliers (resize bounds). */
export const MIN_SIZE = 0.6;
export const MAX_SIZE = 2;
