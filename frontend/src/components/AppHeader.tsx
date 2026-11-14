import type { CurrentUser, DemoIdentity } from "../api/types";
import { IdentitySwitcher } from "./IdentitySwitcher";

interface AppHeaderProps {
  currentUser: CurrentUser | null;
  demoEnabled: boolean;
  demoHeader: string;
  identities: DemoIdentity[];
  onSelectIdentity: (userId: string) => void;
  onRefresh: () => void;
  switchingDisabled: boolean;
}

/** The application header: title, current user, and the demo identity switcher. */
export function AppHeader({
  currentUser,
  demoEnabled,
  demoHeader,
  identities,
  onSelectIdentity,
  onRefresh,
  switchingDisabled,
}: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <h1 className="app-header__title">Bluestaq Notes</h1>
        <p className="app-header__subtitle">Secure multi-user notes — demo client</p>
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
          <p className="app-header__no-demo">
            Demo identity selection is disabled by the server.
          </p>
        )}

        <button type="button" className="button button--ghost" onClick={onRefresh}>
          Refresh
        </button>
      </div>
    </header>
  );
}
