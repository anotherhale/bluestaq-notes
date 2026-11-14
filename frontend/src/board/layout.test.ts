import { describe, expect, it } from "vitest";

import {
  arrange,
  autoShape,
  cardDimensions,
  cardPreview,
  clamp,
  defaultColor,
  freeSlot,
  makeCard,
  nextColor,
  nextShape,
  resolveColor,
  resolveShape,
  topZ,
  MAX_DIMENSION,
  MIN_DIMENSION,
} from "./layout";
import { MAX_SIZE, MIN_SIZE, STICKY_COLORS, type BoardCard, type BoardNote } from "./types";

function note(id: string, over: Partial<BoardNote> = {}): BoardNote {
  return {
    id,
    title: `Note ${id}`,
    folder_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("clamp", () => {
  it("bounds values and tolerates NaN", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(99, 0, 10)).toBe(10);
    expect(clamp(Number.NaN, 2, 10)).toBe(2);
  });
});

describe("defaultColor", () => {
  it("is deterministic for the same note id", () => {
    expect(defaultColor("abc")).toBe(defaultColor("abc"));
    expect(defaultColor("note-1")).toBe(defaultColor("note-1"));
  });

  it("always returns a palette color", () => {
    for (const id of ["a", "b", "c", "deadbeef", "11111111-1111-1111-1111-111111111111"]) {
      expect(STICKY_COLORS).toContain(defaultColor(id));
    }
  });

  it("spreads different ids across more than one color", () => {
    const seen = new Set(
      Array.from({ length: 50 }, (_value, index) => defaultColor(`note-${index}`)),
    );
    expect(seen.size).toBeGreaterThan(1);
  });
});

describe("resolveColor", () => {
  it("prefers an explicit color", () => {
    expect(resolveColor({ noteId: "x", color: "mint" })).toBe("mint");
  });
  it("falls back to the deterministic default", () => {
    expect(resolveColor({ noteId: "x", color: null })).toBe(defaultColor("x"));
  });
});

describe("autoShape / resolveShape", () => {
  it("picks tall for long bodies", () => {
    expect(autoShape({ title: "T", content: "x".repeat(300) })).toBe("tall");
  });
  it("picks wide for long titles", () => {
    expect(autoShape({ title: "x".repeat(60), content: "short" })).toBe("wide");
  });
  it("picks square for very short bodies (no round shape)", () => {
    expect(autoShape({ title: "T", content: "hi" })).toBe("square");
  });
  it("defaults to square", () => {
    expect(autoShape({ title: "T", content: "x".repeat(80) })).toBe("square");
  });
  it("strips HTML before measuring", () => {
    // The tags should not count toward the length budget.
    expect(autoShape({ title: "T", content: `<p>${"x".repeat(300)}</p>` })).toBe("tall");
  });
  it("resolveShape uses the explicit shape when set", () => {
    expect(resolveShape({ shape: "wide" }, { title: "T", content: "x".repeat(300) })).toBe("wide");
  });
  it("resolveShape falls back to auto", () => {
    expect(resolveShape({ shape: "auto" }, { title: "T", content: "hi" })).toBe("square");
  });
});

describe("cardDimensions", () => {
  it("scales with size and clamps to the bounds", () => {
    const base = cardDimensions("square", 1);
    const bigger = cardDimensions("square", 2);
    expect(bigger.width).toBeGreaterThan(base.width);

    const tiny = cardDimensions("square", 0.0001);
    expect(tiny.width).toBeGreaterThanOrEqual(MIN_DIMENSION);
    expect(tiny.height).toBeGreaterThanOrEqual(MIN_DIMENSION);

    const huge = cardDimensions("square", 999);
    expect(huge.width).toBeLessThanOrEqual(MAX_DIMENSION);
    expect(huge.height).toBeLessThanOrEqual(MAX_DIMENSION);
  });

  it("honours the size multiplier clamp", () => {
    // Out-of-range multipliers are clamped before scaling.
    expect(cardDimensions("wide", MAX_SIZE + 5)).toEqual(cardDimensions("wide", MAX_SIZE));
    expect(cardDimensions("wide", MIN_SIZE - 5)).toEqual(cardDimensions("wide", MIN_SIZE));
  });
});

describe("makeCard / topZ", () => {
  it("creates a card with defaults", () => {
    const card = makeCard("n1", 10.4, 20.6, 3);
    expect(card).toMatchObject({
      noteId: "n1",
      x: 10,
      y: 21,
      color: null,
      shape: "auto",
      size: 1,
      z: 3,
      pinned: false,
    });
  });
  it("reports the highest z, zero when empty", () => {
    expect(topZ([])).toBe(0);
    expect(topZ([{ ...makeCard("a", 0, 0, 2) }, { ...makeCard("b", 0, 0, 7) }])).toBe(7);
  });
});

describe("freeSlot", () => {
  it("returns the first cell when empty", () => {
    expect(freeSlot([])).toEqual({ x: 48, y: 48 });
  });
  it("skips occupied cells", () => {
    const first = freeSlot([]);
    const occupied: BoardCard[] = [makeCard("a", first.x, first.y, 1)];
    const next = freeSlot(occupied);
    expect(next).not.toEqual(first);
  });
  it("finds a slot that no card occupies", () => {
    const cards = [makeCard("a", 48, 48, 1), makeCard("b", 272, 48, 2)];
    const slot = freeSlot(cards);
    const clashes = cards.some(
      (card) => Math.abs(card.x - slot.x) < 100 && Math.abs(card.y - slot.y) < 100,
    );
    expect(clashes).toBe(false);
  });
});

describe("nextColor / nextShape", () => {
  it("cycles through the palette and wraps", () => {
    let color = nextColor(null);
    expect(color).toBe(STICKY_COLORS[0]);
    for (let index = 1; index < STICKY_COLORS.length; index += 1) {
      color = nextColor(color);
    }
    expect(nextColor(color)).toBe(STICKY_COLORS[0]);
  });
  it("cycles shapes starting from auto", () => {
    expect(nextShape("auto")).toBe("square");
    expect(nextShape("square")).toBe("wide");
    expect(nextShape("wide")).toBe("tall");
    expect(nextShape("tall")).toBe("square");
  });
});

describe("cardPreview", () => {
  it("strips HTML and collapses whitespace", () => {
    expect(cardPreview({ content: "<h1>Hello</h1>   <p>world</p>" })).toBe("Hello world");
  });
  it("handles missing content", () => {
    expect(cardPreview({})).toBe("");
    expect(cardPreview({ content: "" })).toBe("");
  });
  it("truncates long content with an ellipsis", () => {
    const preview = cardPreview({ content: "x".repeat(500) }, 20);
    expect(preview.length).toBeLessThanOrEqual(20);
    expect(preview.endsWith("…")).toBe(true);
  });
  it("does not render HTML entities as tags", () => {
    expect(cardPreview({ content: "a &amp; b" })).toBe("a & b");
  });
});

describe("arrange", () => {
  const notesById: Record<string, BoardNote> = {
    a: note("a", { folder_id: "f1", updated_at: "2026-01-01T00:00:00Z" }),
    b: note("b", { folder_id: "f2", updated_at: "2026-03-01T00:00:00Z" }),
    c: note("c", { folder_id: "f1", updated_at: "2026-02-01T00:00:00Z" }),
  };

  function cards(): BoardCard[] {
    return [
      { ...makeCard("a", 9999, 9999, 1) },
      { ...makeCard("b", 8888, 8888, 2) },
      { ...makeCard("c", 7777, 7777, 3) },
    ];
  }

  it("does not mutate the input array or its cards", () => {
    const input = cards();
    const snapshot = JSON.parse(JSON.stringify(input));
    arrange(input, notesById, "grid");
    expect(input).toEqual(snapshot);
  });

  it("returns the same number of cards", () => {
    expect(arrange(cards(), notesById, "grid")).toHaveLength(3);
  });

  it("is deterministic for the same input", () => {
    expect(arrange(cards(), notesById, "color")).toEqual(arrange(cards(), notesById, "color"));
    expect(arrange(cards(), notesById, "recency")).toEqual(arrange(cards(), notesById, "recency"));
  });

  it("keeps pinned cards in place", () => {
    const input = cards();
    input[0] = { ...input[0]!, pinned: true, x: 4242, y: 2424 };
    const result = arrange(input, notesById, "grid");
    const pinned = result.find((card) => card.noteId === "a");
    expect(pinned).toMatchObject({ x: 4242, y: 2424, pinned: true });
  });

  it("moves unpinned cards off their original positions", () => {
    const result = arrange(cards(), notesById, "grid");
    const moved = result.filter((card) => !card.pinned);
    expect(moved.every((card) => card.x < 9999 && card.y < 9999)).toBe(true);
  });

  it("places every unpinned card in a distinct cell", () => {
    for (const mode of ["grid", "color", "folder", "recency"] as const) {
      const result = arrange(cards(), notesById, mode);
      const cells = new Set(result.map((card) => `${card.x}:${card.y}`));
      expect(cells.size).toBe(result.length);
    }
  });

  it("groups by folder under the folder mode", () => {
    const result = arrange(cards(), notesById, "folder");
    // f1 cards (a, c) should be adjacent in the layout order.
    const a = result.find((card) => card.noteId === "a")!;
    const c = result.find((card) => card.noteId === "c")!;
    const b = result.find((card) => card.noteId === "b")!;
    expect(Math.abs(a.y - c.y)).toBeLessThanOrEqual(Math.abs(a.y - b.y) + 1);
  });

  it("orders newest-updated first under recency", () => {
    const result = arrange(cards(), notesById, "recency");
    const positions = ["b", "c", "a"].map(
      (id) => result.find((card) => card.noteId === id)!,
    );
    // b (newest) should not be to the right of / below a (oldest) in row order.
    const rank = (card: BoardCard) => card.y * 10000 + card.x;
    expect(rank(positions[0]!)).toBeLessThan(rank(positions[1]!));
    expect(rank(positions[1]!)).toBeLessThan(rank(positions[2]!));
  });
});
