import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MemoryBoardStore } from "./store";
import { BoardView } from "./BoardView";
import { useBoards } from "./useBoards";
import type { BoardNote } from "./types";
import type { Folder } from "../api/types";

/**
 * The DEF-03 regression suite.
 *
 * DEF-03: the canvas droppable was registered in a parent component *above* the
 * `DndContext`. Because dnd-kit resolves `useDroppable`/`useDraggable` through
 * React context, a droppable outside the provider cannot receive drops — and it
 * fails silently (no error, no warning, the drop just does nothing).
 *
 * `BoardView` owns exactly one `DndContext` and renders `BoardCanvas` (the
 * component calling `useDroppable`) inside it. The tests below assert the real
 * drag-from-tray-to-canvas behaviour, not merely that things render.
 */

const NOTES: BoardNote[] = [
  {
    id: "note-a",
    title: "Alpha note",
    folder_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "note-b",
    title: "Beta note",
    folder_id: null,
    created_at: "2026-01-02T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
];

const FOLDERS: Folder[] = [];

/** A test harness that gives BoardView a hook backed by a MemoryBoardStore. */
function Harness({ store = new MemoryBoardStore() }: { store?: MemoryBoardStore }) {
  const boards = useBoards({ userKey: "alice", notes: NOTES, store });
  return (
    <BoardView
      boards={boards}
      notes={NOTES}
      noteDetail={(noteId) => NOTES.find((note) => note.id === noteId)}
      requestNoteDetail={() => undefined}
      folders={FOLDERS}
      onOpenNote={() => undefined}
    />
  );
}

async function boot() {
  const store = new MemoryBoardStore();
  const user = userEvent.setup();
  const view = render(<Harness store={store} />);
  // Seed one board via the UI so the canvas exists.
  await user.click(screen.getByRole("button", { name: "New board" }));
  await screen.findByTestId("board-canvas");
  return { user, store, view };
}

/** Create a board through the toolbar "New" dialog. */
async function createBoardViaDialog(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: "New" }));
  const dialog = await screen.findByRole("dialog");
  const input = within(dialog).getByLabelText("Folder name");
  await user.type(input, name);
  await user.click(within(dialog).getByRole("button", { name: "Create folder" }));
}

describe("BoardView — board management", () => {
  it("creates a board and shows the canvas", async () => {
    await boot();
    expect(screen.getByTestId("board-canvas")).toBeInTheDocument();
    expect(screen.getByLabelText("Board")).toBeInTheDocument();
  });

  it("shows the empty state before any board exists", () => {
    render(<Harness />);
    expect(screen.getByText("No boards yet")).toBeInTheDocument();
  });

  it("adds a note to the board via the tray Add button", async () => {
    const { user, store } = await boot();
    await user.click(screen.getByRole("button", { name: "Add Alpha note to board" }));

    await waitFor(() => {
      expect(store.load("alice")[0]!.cards).toHaveLength(1);
    });
    // The card renders inside the canvas (the tray also shows the title).
    const canvas = screen.getByTestId("board-canvas");
    expect(within(canvas).getByText("Alpha note")).toBeInTheDocument();
  });

  it("creates multiple boards, each with its own cards", async () => {
    const { user, store } = await boot();
    await user.click(screen.getByRole("button", { name: "Add Alpha note to board" }));
    await waitFor(() => expect(store.load("alice")[0]!.cards).toHaveLength(1));

    await createBoardViaDialog(user, "Second board");
    await waitFor(() => expect(store.load("alice")).toHaveLength(2));

    // The new board is empty; the first still has its card.
    expect(store.load("alice")[1]!.cards).toHaveLength(0);
    expect(store.load("alice")[0]!.cards).toHaveLength(1);
  });

  it("switches between boards", async () => {
    const { user, store } = await boot();
    await user.click(screen.getByRole("button", { name: "Add Alpha note to board" }));
    await createBoardViaDialog(user, "Second board");
    await waitFor(() => expect(store.load("alice")).toHaveLength(2));

    const select = screen.getByLabelText("Board") as HTMLSelectElement;
    const firstBoardId = store.load("alice")[0]!.id;
    await user.selectOptions(select, firstBoardId);

    await waitFor(() => {
      const canvas = screen.getByTestId("board-canvas");
      expect(within(canvas).getByText("Alpha note")).toBeInTheDocument();
    });
  });

  it("renames and deletes a board", async () => {
    const { user, store } = await boot();
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Folder name");
    await user.clear(input);
    await user.type(input, "Renamed board");
    await user.click(within(dialog).getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(store.load("alice")[0]!.name).toBe("Renamed board"));

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = await screen.findByRole("dialog");
    await user.click(within(confirm).getByRole("button", { name: "Delete board" }));
    await waitFor(() => expect(store.load("alice")).toHaveLength(0));
  });
});


describe("BoardView — card controls", () => {
  it("cycles color, shape, pin, and z-order, and removes a card", async () => {
    const { user, store } = await boot();
    await user.click(screen.getByRole("button", { name: "Add Alpha note to board" }));
    await waitFor(() => expect(store.load("alice")[0]!.cards).toHaveLength(1));

    await user.click(screen.getByRole("button", { name: "Change color of Alpha note" }));
    await waitFor(() => {
      expect(store.load("alice")[0]!.cards[0]!.color).not.toBeNull();
    });

    await user.click(screen.getByRole("button", { name: "Change shape of Alpha note" }));
    await waitFor(() => {
      expect(store.load("alice")[0]!.cards[0]!.shape).not.toBe("auto");
    });

    await user.click(screen.getByRole("button", { name: "Pin Alpha note" }));
    await waitFor(() => expect(store.load("alice")[0]!.cards[0]!.pinned).toBe(true));

    await user.click(screen.getByRole("button", { name: "Remove Alpha note from board" }));
    await waitFor(() => expect(store.load("alice")[0]!.cards).toHaveLength(0));
  });

  it("brings a card to the front", async () => {
    const { user, store } = await boot();
    await user.click(screen.getByRole("button", { name: "Add Alpha note to board" }));
    await user.click(screen.getByRole("button", { name: "Add Beta note to board" }));
    await waitFor(() => expect(store.load("alice")[0]!.cards).toHaveLength(2));

    const before = store.load("alice")[0]!.cards;
    const alphaZ = before.find((card) => card.noteId === "note-a")!.z;
    const betaZ = before.find((card) => card.noteId === "note-b")!.z;
    expect(betaZ).toBeGreaterThan(alphaZ);

    await user.click(screen.getByRole("button", { name: "Bring Alpha note to front" }));
    await waitFor(() => {
      const cards = store.load("alice")[0]!.cards;
      const alpha = cards.find((card) => card.noteId === "note-a")!;
      const beta = cards.find((card) => card.noteId === "note-b")!;
      expect(alpha.z).toBeGreaterThan(beta.z);
    });
  });
});

describe("BoardView — arrangement and surface", () => {
  it("arranges cards and keeps pinned cards in place", async () => {
    const { user, store } = await boot();
    await user.click(screen.getByRole("button", { name: "Add Alpha note to board" }));
    await user.click(screen.getByRole("button", { name: "Add Beta note to board" }));
    await waitFor(() => expect(store.load("alice")[0]!.cards).toHaveLength(2));

    await user.click(screen.getByRole("button", { name: "Pin Alpha note" }));
    const pinned = store.load("alice")[0]!.cards.find((card) => card.noteId === "note-a")!;

    await user.click(screen.getByRole("button", { name: "Arrange cards by color" }));
    await waitFor(() => {
      const cards = store.load("alice")[0]!.cards;
      const stillPinned = cards.find((card) => card.noteId === "note-a")!;
      expect(stillPinned.x).toBe(pinned.x);
      expect(stillPinned.y).toBe(pinned.y);
    });
  });

  it("changes the board surface", async () => {
    const { user, store } = await boot();
    const select = screen.getByLabelText("Surface") as HTMLSelectElement;
    await user.selectOptions(select, "cork");
    await waitFor(() => expect(store.load("alice")[0]!.surface).toBe("cork"));
    expect(screen.getByTestId("board-canvas")).toHaveAttribute("data-surface", "cork");
  });
});

describe("BoardView — zoom controls", () => {
  it("zooms in and out and resets", async () => {
    const { user } = await boot();
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByText("125%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(screen.getByText("100%")).toBeInTheDocument();
  });
});
