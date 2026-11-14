import { useMemo, useState } from "react";

import { HttpNotesApi, type NotesApi } from "./api/client";
import { AppHeader } from "./components/AppHeader";
import { NoteWorkspace } from "./components/NoteWorkspace";
import { BoardView } from "./board/BoardView";
import { useBoards } from "./board/useBoards";
import { useWorkspace } from "./hooks/useWorkspace";

interface AppProps {
  /** Injectable for tests; defaults to the real HTTP client. */
  api?: NotesApi;
}

/** The two top-level presentations of the same notes. */
export type AppView = "list" | "board";

/**
 * The application root.
 *
 * It constructs the API client once (or accepts one for tests), hands it to the
 * workspace hook, and renders the header and the active view (list or board).
 * No data fetching or business logic lives here.
 */
export function App({ api }: AppProps) {
  const client = useMemo(() => api ?? new HttpNotesApi(), [api]);
  const workspace = useWorkspace(client);
  const [view, setView] = useState<AppView>("list");

  const boards = useBoards({
    userKey: workspace.currentUser?.id ?? null,
    notes: workspace.notes,
  });

  const switchingDisabled =
    workspace.loadingIdentities || workspace.loadingNote || workspace.saving;

  return (
    <div className="app">
      <AppHeader
        currentUser={workspace.currentUser}
        demoEnabled={workspace.demoEnabled}
        demoHeader={workspace.demoHeader}
        identities={workspace.identities}
        searchQuery={workspace.searchQuery}
        sort={workspace.sort}
        resultCount={workspace.notesTotal}
        loadingNotes={workspace.loadingNotes}
        view={view}
        onViewChange={setView}
        onQueryChange={workspace.setSearchQuery}
        onSortChange={workspace.setSort}
        onSelectIdentity={workspace.selectIdentity}
        onRefresh={workspace.refresh}
        switchingDisabled={switchingDisabled}
      />

      {!workspace.loadingIdentities && !workspace.demoEnabled ? (
        <div className="app__notice">
          <p>
            This client expects the development demo authentication mode. Start the backend
            with <code>AUTH_DEMO_ENABLED=true</code> and <code>ENVIRONMENT=development</code>.
          </p>
        </div>
      ) : null}

      <main className="app__main">
        {workspace.loadingIdentities ? (
          <p className="workspace__loading">Connecting to the backend…</p>
        ) : view === "board" ? (
          <BoardView
            boards={boards}
            notes={workspace.notes}
            noteDetail={workspace.noteDetail}
            requestNoteDetail={workspace.requestNoteDetail}
            folders={workspace.folders}
            onOpenNote={(noteId) => {
              setView("list");
              workspace.selectNote(noteId);
            }}
          />
        ) : (
          <NoteWorkspace workspace={workspace} />
        )}
      </main>

      <footer className="app__footer">
        Demonstration client for the Bluestaq Notes REST API. Not production authentication.
      </footer>
    </div>
  );
}
