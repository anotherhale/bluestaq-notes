/**
 * Board persistence abstraction.
 *
 * The board components depend on this interface — never on a concrete storage
 * mechanism. G1–G3 ship a local prototype (`LocalStorageBoardStore`); a future
 * server-backed implementation (`ApiBoardStore`) can replace it at the
 * application composition point without touching any component. See
 * `docs/WHITEBOARD_DESIGN.md` (§ G4).
 *
 * IMPORTANT: this store holds *board annotations only* — never note content or
 * note metadata. Notes continue to come from the existing workspace, which is
 * the only thing that talks to the backend.
 */

import {
  BOARD_SURFACES,
  STICKY_COLORS,
  type Board,
  type BoardCard,
  type BoardSurface,
  type ShapeChoice,
  type StickyColor,
} from "./types";

export interface BoardStore {
  /** All boards belonging to `userKey`. Never throws; returns `[]` on failure. */
  load(userKey: string): Board[];
  /** Replace `userKey`'s boards. Never throws. */
  save(userKey: string, boards: Board[]): void;
}

/** Schema version stamped onto persisted payloads. */
export const BOARD_STORE_VERSION = 1;

interface PersistedEnvelope {
  version: number;
  boards: unknown[];
}

/** A card is one of our shapes only if every field validates. */
function parseCard(value: unknown): BoardCard | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const { noteId, x, y, z, size, pinned, color, shape } = record;

  if (typeof noteId !== "string" || noteId.length === 0) return null;
  if (typeof x !== "number" || !Number.isFinite(x)) return null;
  if (typeof y !== "number" || !Number.isFinite(y)) return null;
  if (typeof z !== "number" || !Number.isFinite(z)) return null;
  if (typeof size !== "number" || !Number.isFinite(size)) return null;
  if (typeof pinned !== "boolean") return null;

  const validColors: readonly (StickyColor | null)[] = [...STICKY_COLORS, null];
  if (!validColors.includes(color as StickyColor | null)) return null;

  const validShapes: readonly ShapeChoice[] = ["auto", "square", "wide", "tall"];
  // "round" was removed in favour of square cards. Migrate legacy payloads to
  // "square" rather than discarding the whole board.
  const shape_ = shape === "round" ? "square" : shape;
  if (!validShapes.includes(shape_ as ShapeChoice)) return null;

  return {
    noteId,
    x,
    y,
    z,
    size,
    pinned,
    color: color as StickyColor | null,
    shape: shape_ as ShapeChoice,
  };
}

function parseBoard(value: unknown): Board | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const { id, name, folderId, surface, cards, createdAt, updatedAt } = record;

  if (typeof id !== "string" || id.length === 0) return null;
  if (typeof name !== "string") return null;
  if (folderId !== null && typeof folderId !== "string") return null;
  if (!BOARD_SURFACES.includes(surface as BoardSurface)) return null;
  if (typeof createdAt !== "string" || typeof updatedAt !== "string") return null;
  if (!Array.isArray(cards)) return null;

  const parsedCards: BoardCard[] = [];
  for (const card of cards) {
    const parsed = parseCard(card);
    if (parsed) parsedCards.push(parsed);
  }

  return {
    id,
    name,
    folderId,
    surface: surface as BoardSurface,
    cards: parsedCards,
    createdAt,
    updatedAt,
  };
}

/**
 * Turn an untrusted parsed payload into valid boards.
 *
 * Anything malformed is dropped rather than thrown, because persisted browser
 * data can be edited, truncated, or written by an older schema.
 */
export function parseBoards(payload: unknown): Board[] {
  if (typeof payload !== "object" || payload === null) return [];
  const envelope = payload as Partial<PersistedEnvelope>;
  if (envelope.version !== BOARD_STORE_VERSION) return [];
  if (!Array.isArray(envelope.boards)) return [];

  const boards: Board[] = [];
  for (const entry of envelope.boards) {
    const board = parseBoard(entry);
    if (board) boards.push(board);
  }
  return boards;
}

/** Serialise boards into the versioned envelope written to storage. */
export function serializeBoards(boards: Board[]): string {
  const envelope: PersistedEnvelope = { version: BOARD_STORE_VERSION, boards };
  return JSON.stringify(envelope);
}

/** An in-memory store for tests. Boards are copied in and out defensively. */
export class MemoryBoardStore implements BoardStore {
  private readonly byUser = new Map<string, Board[]>();

  load(userKey: string): Board[] {
    const stored = this.byUser.get(userKey);
    return stored ? structuredClone(stored) : [];
  }

  save(userKey: string, boards: Board[]): void {
    this.byUser.set(userKey, structuredClone(boards));
  }
}

/** The prefix under which each user's boards are stored. */
const STORAGE_PREFIX = "bluestaq.notes.boards.v1.";

function storageKey(userKey: string): string {
  return `${STORAGE_PREFIX}${userKey}`;
}

/**
 * A `localStorage`-backed store, keyed per user so switching demo users shows a
 * different board set.
 *
 * Every browser-storage interaction is guarded: storage can be unavailable
 * (private mode, disabled, quota) and its contents are never trusted.
 */
export class LocalStorageBoardStore implements BoardStore {
  constructor(private readonly storage: Storage | null = resolveStorage()) {}

  load(userKey: string): Board[] {
    if (!this.storage) return [];
    try {
      const raw = this.storage.getItem(storageKey(userKey));
      if (raw === null) return [];
      return parseBoards(JSON.parse(raw));
    } catch {
      // Malformed JSON or a storage read error: behave as if empty.
      return [];
    }
  }

  save(userKey: string, boards: Board[]): void {
    if (!this.storage) return;
    try {
      this.storage.setItem(storageKey(userKey), serializeBoards(boards));
    } catch {
      // Quota exceeded or storage disabled: drop the write rather than crash.
    }
  }
}

function resolveStorage(): Storage | null {
  try {
    if (typeof window === "undefined" || !window.localStorage) return null;
    // Access can throw in some privacy modes; touch it once to find out.
    const probe = "__bluestaq_probe__";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    return null;
  }
}
