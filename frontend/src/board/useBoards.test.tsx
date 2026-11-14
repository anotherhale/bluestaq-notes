import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MemoryBoardStore } from "./store";
import { useBoards } from "./useBoards";
import type { BoardNote } from "./types";

const NOTES: BoardNote[] = [
  {
    id: "n1",
    title: "One",
    folder_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "n2",
    title: "Two",
    folder_id: "f1",
    created_at: "2026-01-02T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
];

function setup(userKey: string | null = "alice", store = new MemoryBoardStore()) {
  const utils = renderHook(
    ({ key, boardStore }: { key: string | null; boardStore: MemoryBoardStore }) =>
      useBoards({ userKey: key, notes: NOTES, store: boardStore }),
    { initialProps: { key: userKey, boardStore: store } },
  );
  return { ...utils, store };
}

describe("useBoards — board lifecycle", () => {
  it("starts with no boards and no active board", () => {
    const { result } = setup();
    expect(result.current.boards).toEqual([]);
    expect(result.current.activeBoard).toBeNull();
  });

  it("creates a board, selects it, and persists it", () => {
    const { result, store } = setup();
    act(() => {
      result.current.createBoard("Roadmap");
    });
    expect(result.current.boards).toHaveLength(1);
    expect(result.current.activeBoard?.name).toBe("Roadmap");
    expect(store.load("alice")).toHaveLength(1);
  });

  it("names boards by sequence when no name is given", () => {
    const { result } = setup();
    act(() => result.current.createBoard());
    act(() => result.current.createBoard());
    expect(result.current.boards.map((board) => board.name)).toEqual(["Board 1", "Board 2"]);
  });

  it("renames and deletes boards", () => {
    const { result } = setup();
    let boardId = "";
    act(() => {
      boardId = result.current.createBoard("First").id;
    });
    act(() => result.current.renameBoard(boardId, "  Renamed  "));
    expect(result.current.activeBoard?.name).toBe("Renamed");

    act(() => result.current.deleteBoard(boardId));
    expect(result.current.boards).toHaveLength(0);
    expect(result.current.activeBoard).toBeNull();
  });

  it("ignores a blank rename", () => {
    const { result } = setup();
    act(() => result.current.createBoard("Keep"));
    const id = result.current.activeBoard!.id;
    act(() => result.current.renameBoard(id, "   "));
    expect(result.current.activeBoard?.name).toBe("Keep");
  });

  it("selects a different board", () => {
    const { result } = setup();
    act(() => result.current.createBoard("A"));
    act(() => result.current.createBoard("B"));
    const firstId = result.current.boards[0]!.id;
    act(() => result.current.selectBoard(firstId));
    expect(result.current.activeBoard?.name).toBe("A");
  });
});

describe("useBoards — cards", () => {
  function withBoard() {
    const utils = setup();
    act(() => utils.result.current.createBoard("Board"));
    return utils;
  }

  it("adds a note once, even if called twice", () => {
    const { result } = withBoard();
    act(() => result.current.addNote("n1", 10, 10));
    act(() => result.current.addNote("n1", 99, 99));
    expect(result.current.activeBoard!.cards).toHaveLength(1);
    expect(result.current.activeBoard!.cards[0]!.x).toBe(10);
  });

  it("moves, resizes (clamped), recolors, reshapes and pins a card", () => {
    const { result } = withBoard();
    act(() => result.current.addNote("n1", 0, 0));
    act(() => result.current.moveCard("n1", 120, 240));
    act(() => result.current.resizeCard("n1", 99));
    act(() => result.current.setCardColor("n1", "mint"));
    act(() => result.current.setCardShape("n1", "wide"));
    act(() => result.current.togglePin("n1"));

    const card = result.current.activeBoard!.cards[0]!;
    expect(card.x).toBe(120);
    expect(card.y).toBe(240);
    // Clamped to MAX_SIZE.
    expect(card.size).toBeLessThanOrEqual(2);
    expect(card.color).toBe("mint");
    expect(card.shape).toBe("wide");
    expect(card.pinned).toBe(true);
  });

  it("cycles color and shape", () => {
    const { result } = withBoard();
    act(() => result.current.addNote("n1", 0, 0));
    act(() => result.current.cycleCardColor("n1"));
    const firstColor = result.current.activeBoard!.cards[0]!.color;
    expect(firstColor).not.toBeNull();
    act(() => result.current.cycleCardColor("n1"));
    expect(result.current.activeBoard!.cards[0]!.color).not.toBe(firstColor);

    act(() => result.current.cycleCardShape("n1"));
    expect(result.current.activeBoard!.cards[0]!.shape).not.toBe("auto");
  });

  it("brings a card to the front and is a no-op when already on top", () => {
    const { result } = withBoard();
    act(() => result.current.addNote("n1", 0, 0));
    act(() => result.current.addNote("n2", 0, 0));
    const beforeTopZ = result.current.cardFor("n2")!.z;

    act(() => result.current.bringToFront("n2"));
    // n2 was already on top: unchanged.
    expect(result.current.cardFor("n2")!.z).toBe(beforeTopZ);

    act(() => result.current.bringToFront("n1"));
    expect(result.current.cardFor("n1")!.z).toBeGreaterThan(result.current.cardFor("n2")!.z);
  });

  it("removes a card without touching the note", () => {
    const { result } = withBoard();
    act(() => result.current.addNote("n1", 0, 0));
    act(() => result.current.removeCard("n1"));
    expect(result.current.activeBoard!.cards).toHaveLength(0);
    // The note is still offered by the workspace.
    expect(NOTES.map((note) => note.id)).toContain("n1");
  });

  it("arranges unpinned cards and leaves pinned ones put", () => {
    const { result } = withBoard();
    act(() => result.current.addNote("n1", 5000, 5000));
    act(() => result.current.addNote("n2", 6000, 6000));
    act(() => result.current.togglePin("n1"));
    const pinned = result.current.cardFor("n1")!;

    act(() => result.current.arrangeBoard("color"));
    const after = result.current.cardFor("n1")!;
    expect(after.x).toBe(pinned.x);
    expect(after.y).toBe(pinned.y);
    expect(result.current.cardFor("n2")!.x).toBeLessThan(6000);
  });
});

describe("useBoards — per-user isolation", () => {
  it("keeps each user's boards separate", () => {
    const store = new MemoryBoardStore();
    const { result, rerender } = setup("alice", store);

    act(() => result.current.createBoard("Alice board"));
    expect(store.load("alice")).toHaveLength(1);

    rerender({ key: "bob", boardStore: store });
    // Bob sees none of Alice's boards...
    expect(result.current.boards).toHaveLength(0);

    act(() => result.current.createBoard("Bob board"));
    expect(store.load("bob")).toHaveLength(1);
    // ...and Alice's are untouched.
    expect(store.load("alice")[0]!.name).toBe("Alice board");
  });

  it("drops the active board when the user changes", () => {
    const store = new MemoryBoardStore();
    const { result, rerender } = setup("alice", store);
    act(() => result.current.createBoard("A"));
    expect(result.current.activeBoardId).not.toBeNull();

    rerender({ key: "bob", boardStore: store });
    expect(result.current.activeBoardId).toBeNull();
  });

  it("loads a previously persisted board for the same user", () => {
    const store = new MemoryBoardStore();
    const first = setup("alice", store);
    act(() => first.result.current.createBoard("Persisted"));

    const second = setup("alice", store);
    expect(second.result.current.boards.map((board) => board.name)).toEqual(["Persisted"]);
    expect(second.result.current.activeBoardId).toBe(second.result.current.boards[0]!.id);
  });

  it("does nothing for a null user", () => {
    const { result, store } = setup(null);
    act(() => result.current.createBoard("Nope"));
    expect(result.current.boards).toHaveLength(0);
    expect(store.load("alice")).toEqual([]);
  });
});
