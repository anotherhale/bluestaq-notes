/**
 * useBoards — board application state.
 *
 * This hook owns the boards, the active board, and every board mutation. It
 * talks only to a `BoardStore` (never the network, never the notes API): the
 * notes it displays come from the existing workspace, passed in as `notes`.
 *
 * It is keyed by the current user's id, so switching demo identities loads a
 * different board set and no boards leak between users.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  arrange,
  cardDimensions,
  clamp,
  makeCard,
  nextColor,
  nextShape,
  nextSurface,
  resolveColor,
  resolveShape,
  topZ,
} from "./layout";
import { LocalStorageBoardStore, type BoardStore } from "./store";
import {
  MAX_SIZE,
  MIN_SIZE,
  type ArrangeMode,
  type Board,
  type BoardCard,
  type BoardNote,
  type BoardNoteDetail,
  type ShapeChoice,
  type StickyColor,
} from "./types";

export interface UseBoardsOptions {
  /** The active demo user's id; boards are scoped to it. `null` disables boards. */
  userKey: string | null;
  /** Notes available to the user (from the workspace), for the tray. */
  notes: readonly BoardNote[];
  /** Optional full-note lookup for content previews / auto-shaping. */
  noteDetail?: (noteId: string) => BoardNoteDetail | undefined;
  /** Persistence mechanism; defaults to local storage. */
  store?: BoardStore;
}

export interface Boards {
  boards: Board[];
  activeBoard: Board | null;
  activeBoardId: string | null;

  createBoard: (name?: string, folderId?: string | null) => Board;
  renameBoard: (boardId: string, name: string) => void;
  deleteBoard: (boardId: string) => void;
  selectBoard: (boardId: string) => void;
  setSurface: (surface: Board["surface"]) => void;
  cycleSurface: () => void;
  setFolder: (folderId: string | null) => void;

  addNote: (noteId: string, x: number, y: number) => void;
  removeCard: (noteId: string) => void;
  moveCard: (noteId: string, x: number, y: number) => void;
  resizeCard: (noteId: string, size: number) => void;
  setCardColor: (noteId: string, color: StickyColor | null) => void;
  cycleCardColor: (noteId: string) => void;
  setCardShape: (noteId: string, shape: ShapeChoice) => void;
  cycleCardShape: (noteId: string) => void;
  togglePin: (noteId: string) => void;
  bringToFront: (noteId: string) => void;
  arrangeBoard: (mode: ArrangeMode) => void;

  isOnBoard: (noteId: string) => boolean;
  cardFor: (noteId: string) => BoardCard | null;
  dimensionsFor: (noteId: string) => { width: number; height: number } | null;
  colorFor: (noteId: string) => StickyColor;
  shapeFor: (noteId: string) => string;
}

function nowIso(): string {
  return new Date().toISOString();
}

/**
 * The `{ title, content }` input the pure shape helpers need.
 *
 * The cached full note (with content) wins when available; otherwise we fall
 * back to the summary's title alone. Kept here so the two call sites stay typed
 * and free of `BoardNote.content` (which does not exist on the summary).
 */
function resolveNoteInput(
  noteId: string,
  noteDetail: ((noteId: string) => BoardNoteDetail | undefined) | undefined,
  notes: readonly BoardNote[],
): { title: string; content?: string } {
  const detail = noteDetail?.(noteId);
  if (detail) {
    // `exactOptionalPropertyTypes` forbids assigning an explicit `undefined`.
    return detail.content === undefined
      ? { title: detail.title }
      : { title: detail.title, content: detail.content };
  }
  const summary = notes.find((note) => note.id === noteId);
  return { title: summary?.title ?? "" };
}

function newBoardId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `board-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

export function useBoards(options: UseBoardsOptions): Boards {
  const { userKey, notes, noteDetail } = options;

  // A single store instance for the hook's lifetime; tests inject a Memory one.
  const storeRef = useRef<BoardStore | null>(null);
  if (storeRef.current === null) {
    storeRef.current = options.store ?? new LocalStorageBoardStore();
  }
  const store = storeRef.current;

  const [boards, setBoards] = useState<Board[]>([]);
  const [activeBoardId, setActiveBoardId] = useState<string | null>(null);

  // Load the user's boards whenever the identity changes, and drop any stale
  // active-board selection that belonged to the previous user.
  useEffect(() => {
    if (!userKey) {
      setBoards([]);
      setActiveBoardId(null);
      return;
    }
    const loaded = store.load(userKey);
    setBoards(loaded);
    setActiveBoardId(loaded[0]?.id ?? null);
  }, [store, userKey]);

  const persist = useCallback(
    (next: Board[]) => {
      // Without an identified user there is no board scope; stay inert rather
      // than accumulating state that would leak into the next user.
      if (!userKey) return;
      setBoards(next);
      store.save(userKey, next);
    },
    [store, userKey],
  );

  const activeBoard = useMemo(
    () => boards.find((board) => board.id === activeBoardId) ?? null,
    [boards, activeBoardId],
  );

  /** Apply a pure transformation to the active board and persist the result. */
  const mutateActive = useCallback(
    (transform: (board: Board) => Board) => {
      if (!activeBoardId) return;
      persist(
        boards.map((board) =>
          board.id === activeBoardId ? { ...transform(board), updatedAt: nowIso() } : board,
        ),
      );
    },
    [activeBoardId, boards, persist],
  );

  const createBoard = useCallback(
    (name?: string, folderId: string | null = null): Board => {
      const timestamp = nowIso();
      const board: Board = {
        id: newBoardId(),
        name: name?.trim() || `Board ${boards.length + 1}`,
        folderId,
        surface: "grid",
        cards: [],
        createdAt: timestamp,
        updatedAt: timestamp,
      };
      persist([...boards, board]);
      setActiveBoardId(board.id);
      return board;
    },
    [boards, persist],
  );

  const renameBoard = useCallback(
    (boardId: string, name: string) => {
      const trimmed = name.trim();
      if (!trimmed) return;
      persist(
        boards.map((board) =>
          board.id === boardId ? { ...board, name: trimmed, updatedAt: nowIso() } : board,
        ),
      );
    },
    [boards, persist],
  );

  const deleteBoard = useCallback(
    (boardId: string) => {
      const next = boards.filter((board) => board.id !== boardId);
      persist(next);
      if (activeBoardId === boardId) {
        setActiveBoardId(next[0]?.id ?? null);
      }
    },
    [boards, persist, activeBoardId],
  );

  const selectBoard = useCallback((boardId: string) => {
    setActiveBoardId(boardId);
  }, []);

  const setSurface = useCallback(
    (surface: Board["surface"]) => mutateActive((board) => ({ ...board, surface })),
    [mutateActive],
  );

  const cycleSurface = useCallback(
    () => mutateActive((board) => ({ ...board, surface: nextSurface(board.surface) })),
    [mutateActive],
  );

  const setFolder = useCallback(
    (folderId: string | null) => mutateActive((board) => ({ ...board, folderId })),
    [mutateActive],
  );

  // -- cards --------------------------------------------------------------

  const addNote = useCallback(
    (noteId: string, x: number, y: number) => {
      mutateActive((board) => {
        // A note appears at most once per board.
        if (board.cards.some((card) => card.noteId === noteId)) return board;
        const card = makeCard(noteId, x, y, topZ(board.cards) + 1);
        return { ...board, cards: [...board.cards, card] };
      });
    },
    [mutateActive],
  );

  const removeCard = useCallback(
    (noteId: string) => {
      // Removes only the board placement; the underlying note is untouched.
      mutateActive((board) => ({
        ...board,
        cards: board.cards.filter((card) => card.noteId !== noteId),
      }));
    },
    [mutateActive],
  );

  const updateCard = useCallback(
    (noteId: string, patch: Partial<BoardCard>) => {
      mutateActive((board) => ({
        ...board,
        cards: board.cards.map((card) =>
          card.noteId === noteId ? { ...card, ...patch } : card,
        ),
      }));
    },
    [mutateActive],
  );

  const moveCard = useCallback(
    (noteId: string, x: number, y: number) => updateCard(noteId, { x, y }),
    [updateCard],
  );

  const resizeCard = useCallback(
    (noteId: string, size: number) => updateCard(noteId, { size: clamp(size, MIN_SIZE, MAX_SIZE) }),
    [updateCard],
  );

  const setCardColor = useCallback(
    (noteId: string, color: StickyColor | null) => updateCard(noteId, { color }),
    [updateCard],
  );

  const cycleCardColor = useCallback(
    (noteId: string) => {
      const card = activeBoard?.cards.find((entry) => entry.noteId === noteId);
      if (!card) return;
      updateCard(noteId, { color: nextColor(card.color) });
    },
    [activeBoard, updateCard],
  );

  const setCardShape = useCallback(
    (noteId: string, shape: ShapeChoice) => updateCard(noteId, { shape }),
    [updateCard],
  );

  const cycleCardShape = useCallback(
    (noteId: string) => {
      const card = activeBoard?.cards.find((entry) => entry.noteId === noteId);
      if (!card) return;
      updateCard(noteId, { shape: nextShape(card.shape) });
    },
    [activeBoard, updateCard],
  );

  const togglePin = useCallback(
    (noteId: string) => {
      const card = activeBoard?.cards.find((entry) => entry.noteId === noteId);
      if (!card) return;
      updateCard(noteId, { pinned: !card.pinned });
    },
    [activeBoard, updateCard],
  );

  const bringToFront = useCallback(
    (noteId: string) => {
      const cards = activeBoard?.cards ?? [];
      const card = cards.find((entry) => entry.noteId === noteId);
      if (!card) return;
      // Already on top: avoid a pointless write (and a z-order bump).
      if (card.z === topZ(cards)) return;
      updateCard(noteId, { z: topZ(cards) + 1 });
    },
    [activeBoard, updateCard],
  );

  const arrangeBoard = useCallback(
    (mode: ArrangeMode) => {
      const notesById: Record<string, BoardNote | undefined> = {};
      for (const note of notes) notesById[note.id] = note;
      mutateActive((board) => ({ ...board, cards: arrange(board.cards, notesById, mode) }));
    },
    [mutateActive, notes],
  );

  const isOnBoard = useCallback(
    (noteId: string) => activeBoard?.cards.some((card) => card.noteId === noteId) ?? false,
    [activeBoard],
  );

  const cardFor = useCallback(
    (noteId: string) => activeBoard?.cards.find((card) => card.noteId === noteId) ?? null,
    [activeBoard],
  );

  const dimensionsFor = useCallback(
    (noteId: string) => {
      const card = activeBoard?.cards.find((entry) => entry.noteId === noteId);
      if (!card) return null;
      const shape = resolveShape(card, resolveNoteInput(noteId, noteDetail, notes));
      return cardDimensions(shape, card.size);
    },
    [activeBoard, noteDetail, notes],
  );

  const colorFor = useCallback(
    (noteId: string) => {
      const card = activeBoard?.cards.find((entry) => entry.noteId === noteId);
      return card ? resolveColor(card) : "yellow";
    },
    [activeBoard],
  );

  const shapeFor = useCallback(
    (noteId: string) => {
      const card = activeBoard?.cards.find((entry) => entry.noteId === noteId);
      if (!card) return "square";
      return resolveShape(card, resolveNoteInput(noteId, noteDetail, notes));
    },
    [activeBoard, noteDetail, notes],
  );

  return {
    boards,
    activeBoard,
    activeBoardId,
    createBoard,
    renameBoard,
    deleteBoard,
    selectBoard,
    setSurface,
    cycleSurface,
    setFolder,
    addNote,
    removeCard,
    moveCard,
    resizeCard,
    setCardColor,
    cycleCardColor,
    setCardShape,
    cycleCardShape,
    togglePin,
    bringToFront,
    arrangeBoard,
    isOnBoard,
    cardFor,
    dimensionsFor,
    colorFor,
    shapeFor,
  };
}
