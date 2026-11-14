import type { CurrentUser, DemoIdentity, NoteSort } from "../api/types";
import type { AppView } from "../App";
import { IdentitySwitcher } from "./IdentitySwitcher";
import { SearchBar } from "./SearchBar";

interface AppHeaderProps {
  currentUser: CurrentUser | null;
  demoEnabled: boolean;
  demoHeader: string;
  identities: DemoIdentity[];
  searchQuery: string;
  sort: NoteSort;
  resultCount: number;
  loadingNotes: boolean;
  /** The active top-level presentation. */
  view: AppView;
  onViewChange: (view: AppView) => void;
  onQueryChange: (query: string) => void;
  onSortChange: (sort: NoteSort) => void;
  onSelectIdentity: (userId: string) => void;
  onRefresh: () => void;
  switchingDisabled: boolean;
}

/**
 * The application header: brand, prominent search, current user, and the demo
 * identity switcher (with an explicit development-only indicator).
 */
export function AppHeader({
  currentUser,
  demoEnabled,
  demoHeader,
  identities,
  searchQuery,
  sort,
  resultCount,
  loadingNotes,
  view,
  onViewChange,
  onQueryChange,
  onSortChange,
  onSelectIdentity,
  onRefresh,
  switchingDisabled,
}: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <span className="app-header__logo" aria-hidden="true">
          📝
        </span>
        <h1 className="app-header__title">Notes</h1>
        {demoEnabled ? (
          <span className="app-header__demo-badge" title="Development demo authentication">
            Demo mode
          </span>
        ) : null}
        <div className="app-header__view-toggle" role="group" aria-label="Choose view">
          <button
            type="button"
            className={`app-header__view${view === "list" ? " app-header__view--active" : ""}`}
            aria-pressed={view === "list"}
            onClick={() => onViewChange("list")}
          >
            List
          </button>
          <button
            type="button"
            className={`app-header__view${view === "board" ? " app-header__view--active" : ""}`}
            aria-pressed={view === "board"}
            onClick={() => onViewChange("board")}
          >
            Board
          </button>
        </div>
      </div>

      <div className="app-header__search">
        <SearchBar
          query={searchQuery}
          onQueryChange={onQueryChange}
          sort={sort}
          onSortChange={onSortChange}
          resultCount={resultCount}
          loading={loadingNotes}
        />
      </div>

      <div className="app-header__controls">
        {currentUser ? (
          <div className="app-header__user" aria-live="polite">
            <span className="app-header__user-name">{currentUser.display_name}</span>
            <span className="app-header__user-email">{currentUser.email}</span>
          </div>
        ) : (
          <div className="app-header__user app-header__user--empty">No user selected</div>
        )}

        {demoEnabled ? (
          <IdentitySwitcher
            identities={identities}
            selectedId={currentUser?.id ?? null}
            headerName={demoHeader}
            disabled={switchingDisabled}
            onSelect={onSelectIdentity}
          />
        ) : (
          <p className="app-header__no-demo">Demo identity selection is disabled by the server.</p>
        )}

        <button type="button" className="button button--ghost" onClick={onRefresh}>
          Refresh
        </button>
      </div>
    </header>
  );
}
