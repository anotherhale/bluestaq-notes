import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FakeApiClient, FAKE_ALICE, FAKE_BOB } from "../test/fakeApiClient";
import { useWorkspace } from "./useWorkspace";

describe("useWorkspace", () => {
  it("bootstraps identities and selects the first one", async () => {
    const api = new FakeApiClient();
    const { result } = renderHook(() => useWorkspace(api));

    await waitFor(() => expect(result.current.loadingIdentities).toBe(false));
    await waitFor(() => expect(result.current.currentUser?.id).toBe(FAKE_ALICE));
    expect(result.current.demoEnabled).toBe(true);
    expect(result.current.identities).toHaveLength(3);
  });

  it("splits notes into owned and shared views", async () => {
    const api = new FakeApiClient();
    const aliceNote = api.seedNote(FAKE_ALICE, "Alice private");
    const bobNote = api.seedNote(FAKE_BOB, "Bob private");
    api.seedPermission(bobNote.id, FAKE_ALICE, "read");

    const { result } = renderHook(() => useWorkspace(api));

    await waitFor(() => expect(result.current.currentUser?.id).toBe(FAKE_ALICE));
    await waitFor(() => expect(result.current.notes).toHaveLength(2));

    const owned = result.current.notes.filter((n) => n.owner_id === FAKE_ALICE);
    const shared = result.current.notes.filter((n) => n.owner_id !== FAKE_ALICE);
    expect(owned.map((n) => n.id)).toEqual([aliceNote.id]);
    expect(shared.map((n) => n.id)).toEqual([bobNote.id]);
  });

  it("clears user-specific state when switching identity", async () => {
    const api = new FakeApiClient();
    const aliceNote = api.seedNote(FAKE_ALICE, "Alice private");
    const { result } = renderHook(() => useWorkspace(api));

    await waitFor(() => expect(result.current.currentUser?.id).toBe(FAKE_ALICE));
    await act(async () => {
      result.current.selectNote(aliceNote.id);
    });
    await waitFor(() => expect(result.current.selectedNote?.id).toBe(aliceNote.id));

    act(() => {
      result.current.selectIdentity(FAKE_BOB);
    });

    // Selection and draft must be cleared immediately.
    expect(result.current.selectedNote).toBeNull();
    expect(result.current.draft.title).toBe("");
    expect(result.current.permissions).toEqual([]);

    await waitFor(() => expect(result.current.currentUser?.id).toBe(FAKE_BOB));
    // Bob cannot see Alice's private note.
    await waitFor(() => expect(result.current.notes).toHaveLength(0));
  });

  it("surfaces a 409 as conflict state without discarding the draft", async () => {
    const api = new FakeApiClient();
    const note = api.seedNote(FAKE_ALICE, "Shared", "original");
    const { result } = renderHook(() => useWorkspace(api));

    await waitFor(() => expect(result.current.currentUser?.id).toBe(FAKE_ALICE));
    await act(async () => {
      result.current.selectNote(note.id);
    });
    await waitFor(() => expect(result.current.selectedNote?.version).toBe(1));

    // Another writer bumps the version, making our loaded version stale.
    api.mutateNoteExternally(note.id, "changed elsewhere");

    act(() => {
      result.current.updateDraft({ content: "my local edit" });
    });

    await act(async () => {
      await result.current.saveNote();
    });

    expect(result.current.conflict).not.toBeNull();
    expect(result.current.draft.content).toBe("my local edit");
    expect(result.current.error).toBeNull();
  });

  it("creates a note and selects it", async () => {
    const api = new FakeApiClient();
    const { result } = renderHook(() => useWorkspace(api));

    await waitFor(() => expect(result.current.currentUser?.id).toBe(FAKE_ALICE));

    await act(async () => {
      await result.current.createNote({ title: "Fresh", content: "body" });
    });

    await waitFor(() => expect(result.current.selectedNote?.title).toBe("Fresh"));
    expect(result.current.notes.some((n) => n.title === "Fresh")).toBe(true);
  });

  it("reports a network failure as an error", async () => {
    const api = new FakeApiClient();
    const { result } = renderHook(() => useWorkspace(api));

    await waitFor(() => expect(result.current.currentUser?.id).toBe(FAKE_ALICE));

    api.failNetwork = true;
    await act(async () => {
      await result.current.createNote({ title: "x", content: "" });
    });

    expect(result.current.error?.status).toBe(0);
  });
});
