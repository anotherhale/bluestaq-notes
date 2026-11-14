import { beforeEach, describe, expect, it } from "vitest";

import {
  BOARD_STORE_VERSION,
  LocalStorageBoardStore,
  MemoryBoardStore,
  parseBoards,
  serializeBoards,
} from "./store";
import { makeCard } from "./layout";
import type { Board } from "./types";

function board(id: string, name = "Board"): Board {
  return {
    id,
    name,
    folderId: null,
    surface: "grid",
    cards: [makeCard("note-1", 10, 20, 1)],
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  };
}

describe("MemoryBoardStore", () => {
  it("round-trips boards per user", () => {
    const store = new MemoryBoardStore();
    store.save("alice", [board("b1")]);
    expect(store.load("alice")).toHaveLength(1);
    expect(store.load("alice")[0]!.id).toBe("b1");
  });

  it("isolates users", () => {
    const store = new MemoryBoardStore();
    store.save("alice", [board("b1")]);
    store.save("bob", [board("b2"), board("b3")]);
    expect(store.load("alice").map((entry) => entry.id)).toEqual(["b1"]);
    expect(store.load("bob").map((entry) => entry.id)).toEqual(["b2", "b3"]);
  });

  it("returns an empty list for an unknown user", () => {
    expect(new MemoryBoardStore().load("nobody")).toEqual([]);
  });

  it("does not alias stored data to the caller", () => {
    const store = new MemoryBoardStore();
    const boards = [board("b1")];
    store.save("alice", boards);
    boards[0]!.name = "mutated";
    expect(store.load("alice")[0]!.name).toBe("Board");
  });
});

describe("parseBoards", () => {
  it("accepts a well-formed envelope", () => {
    const parsed = parseBoards(JSON.parse(serializeBoards([board("b1")])));
    expect(parsed).toHaveLength(1);
    expect(parsed[0]!.id).toBe("b1");
  });

  it("rejects a wrong version", () => {
    expect(parseBoards({ version: 999, boards: [board("b1")] })).toEqual([]);
  });

  it("rejects non-objects and missing boards", () => {
    expect(parseBoards(null)).toEqual([]);
    expect(parseBoards("nope")).toEqual([]);
    expect(parseBoards({ version: BOARD_STORE_VERSION })).toEqual([]);
    expect(parseBoards({ version: BOARD_STORE_VERSION, boards: "x" })).toEqual([]);
  });

  it("drops malformed boards but keeps valid ones", () => {
    const payload = {
      version: BOARD_STORE_VERSION,
      boards: [board("good"), { id: 42 }, null, { id: "x", name: "y" }],
    };
    const parsed = parseBoards(payload);
    expect(parsed.map((entry) => entry.id)).toEqual(["good"]);
  });

  it("drops malformed cards inside an otherwise valid board", () => {
    const bad = {
      ...board("b1"),
      cards: [makeCard("ok", 0, 0, 1), { noteId: "broken" }, { noteId: "n", x: "no" }],
    };
    const parsed = parseBoards({ version: BOARD_STORE_VERSION, boards: [bad] });
    expect(parsed).toHaveLength(1);
    expect(parsed[0]!.cards.map((card) => card.noteId)).toEqual(["ok"]);
  });

  it("rejects unknown surface and color values", () => {
    const badSurface = { ...board("b1"), surface: "lava" };
    expect(parseBoards({ version: BOARD_STORE_VERSION, boards: [badSurface] })).toEqual([]);

    const badColor = {
      ...board("b1"),
      cards: [{ ...makeCard("n", 0, 0, 1), color: "chartreuse" }],
    };
    const parsed = parseBoards({ version: BOARD_STORE_VERSION, boards: [badColor] });
    expect(parsed[0]!.cards).toEqual([]);
  });

  it("migrates a legacy round card to square instead of dropping it", () => {
    const legacy = {
      ...board("b1"),
      cards: [{ ...makeCard("n", 0, 0, 1), shape: "round" }],
    };
    const parsed = parseBoards({ version: BOARD_STORE_VERSION, boards: [legacy] });
    expect(parsed[0]!.cards).toHaveLength(1);
    expect(parsed[0]!.cards[0]!.shape).toBe("square");
  });

  it("rejects an unknown shape", () => {
    const badShape = {
      ...board("b1"),
      cards: [{ ...makeCard("n", 0, 0, 1), shape: "hexagon" }],
    };
    const parsed = parseBoards({ version: BOARD_STORE_VERSION, boards: [badShape] });
    expect(parsed[0]!.cards).toEqual([]);
  });
});

describe("LocalStorageBoardStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("persists and reloads per user", () => {
    const store = new LocalStorageBoardStore(window.localStorage);
    store.save("alice", [board("b1")]);
    store.save("bob", [board("b2")]);

    const reloaded = new LocalStorageBoardStore(window.localStorage);
    expect(reloaded.load("alice").map((entry) => entry.id)).toEqual(["b1"]);
    expect(reloaded.load("bob").map((entry) => entry.id)).toEqual(["b2"]);
  });

  it("isolates users across a reload", () => {
    const store = new LocalStorageBoardStore(window.localStorage);
    store.save("alice", [board("b1")]);
    // Bob never sees Alice's boards.
    expect(store.load("bob")).toEqual([]);
  });

  it("survives malformed JSON", () => {
    const store = new LocalStorageBoardStore(window.localStorage);
    window.localStorage.setItem("bluestaq.notes.boards.v1.alice", "{not json");
    expect(store.load("alice")).toEqual([]);
  });

  it("survives a wrong-version payload", () => {
    const store = new LocalStorageBoardStore(window.localStorage);
    window.localStorage.setItem(
      "bluestaq.notes.boards.v1.alice",
      JSON.stringify({ version: 0, boards: [board("b1")] }),
    );
    expect(store.load("alice")).toEqual([]);
  });

  it("degrades to an empty list when storage is unavailable", () => {
    const store = new LocalStorageBoardStore(null);
    expect(store.load("alice")).toEqual([]);
    expect(() => store.save("alice", [board("b1")])).not.toThrow();
  });

  it("does not throw when setItem fails (quota)", () => {
    const throwing = {
      getItem: () => null,
      setItem: () => {
        throw new Error("quota exceeded");
      },
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    } as unknown as Storage;
    const store = new LocalStorageBoardStore(throwing);
    expect(() => store.save("alice", [board("b1")])).not.toThrow();
  });
});
