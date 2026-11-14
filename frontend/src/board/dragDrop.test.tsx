import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BoardView } from "./BoardView";
import { MemoryBoardStore } from "./store";
import { useBoards } from "./useBoards";
import type { BoardNote } from "./types";

/**
 * DEF-03 regression suite — drag a note from the tray onto the canvas.
 *
 * DEF-03: the canvas droppable (`useDroppable`) was once registered in a parent
 * *above* the `DndContext`. dnd-kit resolves droppables/draggables through React
 * context, so a droppable outside the provider can never receive a drop — and it
 * fails silently: no error, no warning, the drop just does nothing.
 *
 * `BoardView` owns exactly one `DndContext` and renders `BoardCanvas` inside it.
 * This suite drives a real drag through dnd-kit's pointer pipeline and asserts
 * the resulting *persisted* card, so a regression to the broken nesting fails
 * here. It lives in its own file because a completed drag leaves dnd-kit's
 * global pointer state behind, which would otherwise perturb later tests in the
 * same jsdom module (vitest isolates per file).
 */

const NOTES: BoardNote[] = [
  {
    id: "note-a",
    title: "Alpha note",
    folder_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

function Harness({ store }: { store: MemoryBoardStore }) {
  const boards = useBoards({ userKey: "alice", notes: NOTES, store });
  return (
    <BoardView
      boards={boards}
      notes={NOTES}
      noteDetail={(noteId) => NOTES.find((note) => note.id === noteId)}
      requestNoteDetail={() => undefined}
      folders={[]}
      onOpenNote={() => undefined}
    />
  );
}

function domRect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect;
}

describe("DEF-03 — tray note drags onto the board canvas", () => {
  it("creates a card at the drop position when a note is dragged onto the canvas", async () => {
    const store = new MemoryBoardStore();
    const user = userEvent.setup();
    render(<Harness store={store} />);

    await user.click(screen.getByRole("button", { name: "New board" }));
    const canvas = await screen.findByTestId("board-canvas");
    const source = screen.getByRole("button", { name: "Drag note Alpha note onto the board" });

    // jsdom reports zero-size rects, so collision detection would find nothing.
    // Model the canvas as a large box and everything else as a small one.
    const rectSpy = vi
      .spyOn(Element.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: Element) {
        if (this === canvas) return domRect(100, 100, 800, 600);
        return domRect(0, 0, 180, 40);
      });

    try {
      await user.pointer([
        { target: source, keys: "[MouseLeft>]", coords: { x: 20, y: 20 } },
        { target: canvas, coords: { x: 300, y: 300 } },
        { target: canvas, coords: { x: 400, y: 350 } },
        { keys: "[/MouseLeft]" },
      ]);
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    } finally {
      rectSpy.mockRestore();
    }

    const board = store.load("alice")[0]!;
    expect(board.cards).toHaveLength(1);
    expect(board.cards[0]!.noteId).toBe("note-a");
    // The card landed in board space (inside the modelled canvas), not at 0,0.
    expect(board.cards[0]!.x).toBeGreaterThan(0);
    expect(board.cards[0]!.y).toBeGreaterThan(0);
  });
});
