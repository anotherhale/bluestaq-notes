import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { FakeApiClient, FAKE_ALICE, FAKE_BOB } from "./test/fakeApiClient";

/**
 * End-to-end component tests driving the real App against the FakeApiClient.
 * They exercise the multi-user demonstration the assignment calls for: private
 * ownership, sharing, READ vs WRITE, revocation, and cross-user isolation.
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

describe("App — identity and ownership", () => {
  it("shows the current user and lists owned notes", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Alice private");
    api.seedNote(FAKE_BOB, "Bob private");

    await boot(api);

    expect(await screen.findByText("Alice Anderson")).toBeInTheDocument();
    // Owned tab shows only Alice's note.
    expect(screen.getByText("Alice private")).toBeInTheDocument();
    expect(screen.queryByText("Bob private")).not.toBeInTheDocument();
    // Owned count reflects a single note.
    expect(screen.getByRole("tab", { name: /Owned \(1\)/ })).toBeInTheDocument();
  });

  it("does not reveal another user's private note when switching identity", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Alice private");

    const user = await boot(api);
    expect(await screen.findByText("Alice private")).toBeInTheDocument();

    await selectIdentity(user, "bob@example.com");

    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());
    // Bob must not see Alice's private note anywhere.
    await waitFor(() => expect(screen.queryByText("Alice private")).not.toBeInTheDocument());
    expect(screen.getByRole("tab", { name: /Owned \(0\)/ })).toBeInTheDocument();
  });
});

describe("App — sharing (READ)", () => {
  it("shares a note READ, then the recipient can read but not modify it", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Shared design doc", "draft body");

    const user = await boot(api);
    // Open the note and the share dialog.
    await user.click(await screen.findByText("Shared design doc"));
    await user.click(await screen.findByRole("button", { name: "Share…" }));

    const dialog = await screen.findByRole("dialog");
    // Grant READ to Bob.
    await user.selectOptions(
      within(dialog).getByLabelText("Choose a user to share with"),
      within(dialog).getByRole("option", { name: /Bob Baker/ }),
    );
    await user.click(within(dialog).getByRole("button", { name: "Grant access" }));

    // Bob now appears in the access list at "read".
    await waitFor(() =>
      expect(within(dialog).getByText(/Bob Baker/)).toBeInTheDocument(),
    );
    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    // Switch to Bob.
    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());

    // Bob sees the note under "Shared with me".
    await user.click(screen.getByRole("tab", { name: /Shared with me/ }));
    await user.click(await screen.findByText("Shared design doc"));
    await screen.findByDisplayValue("draft body");

    // Bob is not the owner: no Share and no Delete controls.
    expect(screen.queryByRole("button", { name: "Share…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();

    // Bob attempts to modify: the backend (fake) rejects with 403 and the UI shows it.
    await user.clear(screen.getByLabelText("Content"));
    await user.type(screen.getByLabelText("Content"), "bob was here");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(/do not have permission to modify/i),
    ).toBeInTheDocument();

    // The stored content is unchanged: Alice still sees the original.
    expect(note.content).toBe("draft body");
  });
});

describe("App — sharing (WRITE)", () => {
  it("allows a WRITE collaborator to modify the note", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Co-authored", "v1");
    api.seedPermission(note.id, FAKE_BOB, "write");

    const user = await boot(api);
    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());

    await user.click(screen.getByRole("tab", { name: /Shared with me/ }));
    await user.click(await screen.findByText("Co-authored"));
    await screen.findByDisplayValue("v1");

    await user.clear(screen.getByLabelText("Content"));
    await user.type(screen.getByLabelText("Content"), "v2 by bob");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(note.content).toBe("v2 by bob"));
    // Version advanced, proving the write persisted.
    expect(note.version).toBe(2);
  });

  it("does not let a WRITE collaborator delete the note", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Protected", "body");
    api.seedPermission(note.id, FAKE_BOB, "write");

    const user = await boot(api);
    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: /Shared with me/ }));
    await user.click(await screen.findByText("Protected"));
    await screen.findByDisplayValue("body");

    // No delete control is even offered to a non-owner.
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();

    // The note still exists in the backend, unchanged.
    await expect(api.getNote(note.id)).resolves.toMatchObject({ title: "Protected" });
  });
});

describe("App — revocation", () => {
  it("removes access immediately after the owner revokes it", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Temporary access", "secret");
    api.seedPermission(note.id, FAKE_BOB, "read");

    const user = await boot(api);
    // Alice revokes Bob.
    await user.click(await screen.findByText("Temporary access"));
    await user.click(await screen.findByRole("button", { name: "Share…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await user.click(within(dialog).getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() =>
      expect(within(dialog).getByText(/Not shared with anyone yet/i)).toBeInTheDocument(),
    );
    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    // Bob now has no access at all.
    await selectIdentity(user, "bob@example.com");
    await waitFor(() => expect(screen.getByText("Bob Baker")).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: /Shared with me/ }));
    await waitFor(() =>
      expect(screen.queryByText("Temporary access")).not.toBeInTheDocument(),
    );
    await expect(api.getNote(note.id)).rejects.toMatchObject({ status: 404 });
  });
});

describe("App — optimistic concurrency", () => {
  it("shows a conflict and preserves the local draft when saving a stale version", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Concurrent", "v1");

    const user = await boot(api);
    await user.click(await screen.findByText("Concurrent"));
    await screen.findByDisplayValue("v1");

    // Another writer updates the note behind our back.
    api.mutateNoteExternally(note.id, "v2 elsewhere");

    await user.clear(screen.getByLabelText("Content"));
    await user.type(screen.getByLabelText("Content"), "my edit");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/Someone else changed this note/i)).toBeInTheDocument();
    // The local draft is preserved, not overwritten.
    expect(screen.getByLabelText("Content")).toHaveValue("my edit");
  });
});

describe("App — creating notes", () => {
  it("creates a note and shows it as owned", async () => {
    const api = new FakeApiClient();
    const user = await boot(api);

    await user.click(screen.getByRole("button", { name: "New note" }));
    await user.type(screen.getByLabelText("New note title"), "Standup notes");
    await user.click(screen.getByRole("button", { name: "Create note" }));

    await waitFor(() => expect(screen.getByRole("tab", { name: /Owned \(1\)/ })).toBeInTheDocument());
    expect(screen.getByDisplayValue("Standup notes")).toBeInTheDocument();
  });
});

describe("App — error handling", () => {
  it("reports a backend outage clearly", async () => {
    const api = new FakeApiClient();
    api.failNetwork = true;

    render(<App api={api} />);

    // The bootstrap fails; the UI must show an error rather than a blank screen.
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the backend/i);
  });

  it("explains when the server has demo auth disabled", async () => {
    const api = new FakeApiClient({ demoEnabled: false });
    render(<App api={api} />);

    expect(
      await screen.findByText(/demo identity selection is disabled/i),
    ).toBeInTheDocument();
  });
});

describe("App — user list for sharing", () => {
  it("offers other users (not the owner) as share candidates", async () => {
    const api = new FakeApiClient();
    api.seedNote(FAKE_ALICE, "Owner note");

    const user = await boot(api);
    await user.click(await screen.findByText("Owner note"));
    await user.click(await screen.findByRole("button", { name: "Share…" }));

    const dialog = await screen.findByRole("dialog");
    const options = within(dialog).getAllByRole("option");
    const names = options.map((option) => option.textContent ?? "");
    expect(names.some((name) => name.includes("Bob Baker"))).toBe(true);
    expect(names.some((name) => name.includes("Carol Chen"))).toBe(true);
    // The owner is never a grant candidate.
    expect(names.some((name) => name.includes("Alice Anderson"))).toBe(false);
  });
});
