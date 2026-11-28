import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { FakeApiClient, FAKE_ALICE, FAKE_BOB } from "./test/fakeApiClient";

/**
 * End-to-end component tests driving the real App against the FakeApiClient.
 * They exercise the multi-user demonstration the assignment calls for: private
 * ownership, folders, starring, search, sharing, READ vs WRITE, revocation, and
 * cross-user isolation. The fake mirrors the backend's authorization rules, so
 * a passing test here means the UI works against a correct backend — the real
 * backend enforcement is covered by the Python suite.
 */

async function boot(api: FakeApiClient) {
  const user = userEvent.setup();
  render(<App api={api} />);
  // Wait until the first identity (Alice) is resolved.
  await screen.findByText("Alice Anderson");
  return user;
}

function selectIdentity(user: ReturnType<typeof userEvent.setup>, email: string) {
  const select = screen.getByLabelText("Select the demo user to act as") as HTMLSelectElement;
  const option = within(select)
    .getAllByRole("option")
    .find((candidate) => candidate.textContent?.includes(email));
  if (!option) throw new Error(`no identity option for ${email}`);
  return user.selectOptions(select, option);
}

function navTo(user: ReturnType<typeof userEvent.setup>, label: string) {
  return user.click(screen.getByRole("button", { name: label }));
}

describe("App — identity and ownership", () => {
  it("shows the current user and lists owned notes", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Alice private");
    api.seedNote(FAKE_BOB, "Bob private");

    await boot(api);

    expect(await screen.findByText("Alice Anderson")).toBeInTheDocument();
    // The notes list is fetched in a second effect keyed on the resolved
    // identity, so a synchronous read here can beat it to the DOM.
    expect(await screen.findByText("Alice private")).toBeInTheDocument();
    expect(screen.queryByText("Bob private")).not.toBeInTheDocument();
  });

  it("does not reveal another user's private note when switching identity", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Alice private");
    api.seedNote(FAKE_BOB, "Bob private");

    const user = await boot(api);
    expect(await screen.findByText("Alice private")).toBeInTheDocument();

    await selectIdentity(user, "bob@example.com");

    // Anchor on Bob's own note: the list is emptied while it refetches, so an
    // absence-only check would pass vacuously and could never catch a leak.
    await waitFor(() => expect(screen.getByText("Bob private")).toBeInTheDocument());
    expect(screen.queryByText("Alice private")).not.toBeInTheDocument();
  });
});

describe("App — folders", () => {
  it("creates a folder, creates a note inside it, and shows the breadcrumb", async () => {
    const api = new FakeApiClient();
    const user = await boot(api);

    await user.click(screen.getByRole("button", { name: "+ New folder" }));
    await user.type(await screen.findByLabelText("Folder name"), "Project Alpha");
    await user.click(screen.getByRole("button", { name: "Create folder" }));

    expect((await screen.findAllByText("Project Alpha")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "New note" }));
    await user.type(await screen.findByLabelText("New note title"), "Kickoff");
    await user.click(screen.getByRole("button", { name: "Create note" }));

    await waitFor(() => expect(screen.getByText("Kickoff")).toBeInTheDocument());
    const note = api.listNotesSync().find((candidate) => candidate.title === "Kickoff");
    expect(note?.folder_id).toBeTruthy();
  });

  it("renames a folder", async () => {
    const api = new FakeApiClient();
    api.seedFolder(FAKE_ALICE, "Old name");
    const user = await boot(api);

    await user.click(await screen.findByRole("button", { name: "Rename folder Old name" }));
    const input = await screen.findByLabelText("Folder name");
    await user.clear(input);
    await user.type(input, "New name");
    await user.click(screen.getByRole("button", { name: "Rename" }));

    expect(await screen.findByText("New name")).toBeInTheDocument();
    expect(screen.queryByText("Old name")).not.toBeInTheDocument();
  });

  it("deletes a folder after confirmation, keeping its notes in My Drive", async () => {
    const api = new FakeApiClient();
    const folder = api.seedFolder(FAKE_ALICE, "Temp");
    const note = api.seedNote(FAKE_ALICE, "Survivor", "", folder.id);
    const user = await boot(api);

    await user.click(await screen.findByRole("button", { name: "Delete folder Temp" }));
    await user.click(await screen.findByRole("button", { name: "Delete folder" }));

    await waitFor(() => expect(screen.queryByText("Temp")).not.toBeInTheDocument());
    await waitFor(() => expect(api.getNoteSync(note.id)?.folder_id).toBeNull());
  });

  it("moves a note into a folder via the context menu", async () => {
    const api = new FakeApiClient();
    const folder = api.seedFolder(FAKE_ALICE, "Archive");
    const note = api.seedNote(FAKE_ALICE, "Movable");
    const user = await boot(api);

    await user.click(await screen.findByRole("button", { name: "Note actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Move to folder…" }));

    const dialog = await screen.findByRole("dialog", { name: "Move note" });
    await user.selectOptions(
      within(dialog).getByLabelText("Destination folder"),
      within(dialog).getByRole("option", { name: "Archive" }),
    );
    await user.click(within(dialog).getByRole("button", { name: "Move here" }));

    await waitFor(() => expect(api.getNoteSync(note.id)?.folder_id).toBe(folder.id));
  });
});

describe("App — starring", () => {
  it("stars a note and finds it under Starred", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Important");
    const user = await boot(api);

    await user.click(await screen.findByRole("button", { name: "Star note" }));
    await waitFor(() => expect(api.isStarredSync(note.id, FAKE_ALICE)).toBe(true));

    await navTo(user, "Starred");
    expect(await screen.findByText("Important")).toBeInTheDocument();
  });

  it("keeps stars personal: Bob's star does not mark Alice's note", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Shared star");
    api.seedPermission(note.id, FAKE_BOB, "read");

    const user = await boot(api);
    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());
    await navTo(user, "Shared with me");

    await user.click(await screen.findByRole("button", { name: "Star note" }));
    await waitFor(() => expect(api.isStarredSync(note.id, FAKE_BOB)).toBe(true));
    expect(api.isStarredSync(note.id, FAKE_ALICE)).toBe(false);
  });
});

describe("App — search", () => {
  it("filters notes by title as the user types", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Quarterly Report");
    api.seedNote(FAKE_ALICE, "Groceries");
    const user = await boot(api);

    await user.type(screen.getByLabelText("Search notes"), "quarter");

    // Assert both halves in one waitFor: the list is swapped for a loading
    // state during the refetch, so an absence check on its own passes while
    // the list is still empty and proves nothing.
    await waitFor(() => {
      expect(screen.getByText("Quarterly Report")).toBeInTheDocument();
      expect(screen.queryByText("Groceries")).not.toBeInTheDocument();
    });
  });

  it("clears the search and restores the view", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Quarterly Report");
    api.seedNote(FAKE_ALICE, "Groceries");
    const user = await boot(api);

    await user.type(screen.getByLabelText("Search notes"), "quarter");
    await waitFor(() => {
      expect(screen.getByText("Quarterly Report")).toBeInTheDocument();
      expect(screen.queryByText("Groceries")).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Clear search" }));
    await waitFor(() => expect(screen.getByText("Groceries")).toBeInTheDocument());
  });
});

describe("App — sharing (READ)", () => {
  it("shares a note READ, then the recipient can read but not modify it", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Shared design doc", "draft body");

    const user = await boot(api);
    await user.click(await screen.findByText("Shared design doc"));
    await user.click(await screen.findByRole("button", { name: "Share…" }));

    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(
      within(dialog).getByLabelText("Choose a user to share with"),
      within(dialog).getByRole("option", { name: /Bob Baker/ }),
    );
    await user.click(within(dialog).getByRole("button", { name: "Grant access" }));

    await waitFor(() => expect(within(dialog).getByText(/Bob Baker/)).toBeInTheDocument());
    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());

    await navTo(user, "Shared with me");
    await user.click(await screen.findByText("Shared design doc"));

    expect(screen.queryByRole("button", { name: "Share…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only access/i)).toBeInTheDocument();

    expect(note.content).toBe("draft body");
  });
});

describe("App — sharing (WRITE)", () => {
  it("allows a WRITE collaborator to edit and save the note", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Co-authored", "v1");
    api.seedPermission(note.id, FAKE_BOB, "write");

    const user = await boot(api);
    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());

    await navTo(user, "Shared with me");
    await user.click(await screen.findByText("Co-authored"));

    const title = await screen.findByLabelText("Title");
    expect(title).not.toBeDisabled();
    await user.clear(title);
    await user.type(title, "Co-authored v2");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.getNoteSync(note.id)?.title).toBe("Co-authored v2"));
  });

  it("does not let a WRITE collaborator delete the note", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Protected", "body");
    api.seedPermission(note.id, FAKE_BOB, "write");

    const user = await boot(api);
    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());
    await navTo(user, "Shared with me");
    await user.click(await screen.findByText("Protected"));

    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    await expect(api.getNote(note.id)).resolves.toMatchObject({ title: "Protected" });
  });
});

describe("App — revocation", () => {
  it("removes access immediately after the owner revokes it", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Temporary access", "secret");
    api.seedPermission(note.id, FAKE_BOB, "read");

    const user = await boot(api);
    await user.click(await screen.findByText("Temporary access"));
    await user.click(await screen.findByRole("button", { name: "Share…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await user.click(within(dialog).getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() =>
      expect(within(dialog).getByText(/Not shared with anyone yet/i)).toBeInTheDocument(),
    );
    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());
    await navTo(user, "Shared with me");
    await waitFor(() => expect(screen.queryByText("Temporary access")).not.toBeInTheDocument());
    await expect(api.getNote(note.id)).rejects.toMatchObject({ status: 404 });
  });
});

describe("App — optimistic concurrency", () => {
  it("shows a conflict and preserves the local draft when saving a stale version", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Concurrent", "v1");

    const user = await boot(api);
    await user.click(await screen.findByText("Concurrent"));
    await screen.findByLabelText("Title");

    api.mutateNoteExternally(note.id, "v2 elsewhere");

    const title = screen.getByLabelText("Title");
    await user.clear(title);
    await user.type(title, "my edit");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/Someone else changed this note/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toHaveValue("my edit");
  });
});

describe("App — creating notes", () => {
  it("creates a note and opens it in the editor", async () => {
    const api = new FakeApiClient();
    const user = await boot(api);

    await user.click(screen.getByRole("button", { name: "New note" }));
    await user.type(screen.getByLabelText("New note title"), "Standup notes");
    await user.click(screen.getByRole("button", { name: "Create note" }));

    await waitFor(() => expect(screen.getByText("Standup notes")).toBeInTheDocument());
    expect(await screen.findByLabelText("Title")).toHaveValue("Standup notes");
  });
});

describe("App — rich text editor", () => {
  it("formats content with the toolbar and saves it", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Formatted", "");
    const user = await boot(api);

    await user.click(await screen.findByText("Formatted"));
    await user.click(await screen.findByRole("button", { name: "Heading 1" }));

    expect(screen.getByRole("button", { name: "Heading 1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.getNoteSync(note.id)?.version).toBe(2));
  });
});

describe("App — error handling", () => {
  it("reports a backend outage clearly", async () => {
    const api = new FakeApiClient();
    api.failNetwork = true;

    render(<App api={api} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the backend/i);
  });

  it("explains when the server has demo auth disabled", async () => {
    const api = new FakeApiClient({ demoEnabled: false });
    render(<App api={api} />);

    expect(await screen.findByText(/demo identity selection is disabled/i)).toBeInTheDocument();
  });
});

describe("App — whiteboard view", () => {
  it("toggles between the list view and the board view", async () => {
    const api = new FakeApiClient();
    const user = await boot(api);

    // The list workspace is the default.
    expect(screen.getByRole("button", { name: "List" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Board" }));
    expect(screen.getByRole("button", { name: "Board" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // With no boards yet, the board view shows its empty state.
    expect(screen.getByText("No boards yet")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getByRole("button", { name: "List" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("places a note on a board and opens it back in the editor", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Alpha note", "<p>hello</p>");
    const user = await boot(api);

    await user.click(screen.getByRole("button", { name: "Board" }));
    await user.click(screen.getByRole("button", { name: "New board" }));

    // Add the tray note to the board, then open it from the card.
    await user.click(await screen.findByRole("button", { name: "Add Alpha note to board" }));
    const canvas = screen.getByTestId("board-canvas");
    await waitFor(() => {
      expect(within(canvas).getByText("Alpha note")).toBeInTheDocument();
    });

    // Opening the card body returns to the list view with the note selected.
    await user.click(within(canvas).getByRole("button", { name: /hello/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "List" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });
  });
});
