import type { DemoIdentity } from "../api/types";

interface IdentitySwitcherProps {
  identities: DemoIdentity[];
  selectedId: string | null;
  headerName: string;
  disabled: boolean;
  onSelect: (userId: string) => void;
}

/**
 * The demo identity selector.
 *
 * Switching identity is the demonstration's core interaction: it is how one
 * browser session acts as several different users. It is rendered as a labeled
 * control with an explicit caption explaining that this is development-only.
 */
export function IdentitySwitcher({
  identities,
  selectedId,
  headerName,
  disabled,
  onSelect,
}: IdentitySwitcherProps) {
  return (
    <div className="identity-switcher">
      <label className="identity-switcher__label" htmlFor="identity-select">
        Acting as
      </label>
      <select
        id="identity-select"
        className="identity-switcher__select"
        value={selectedId ?? ""}
        disabled={disabled || identities.length === 0}
        onChange={(event) => onSelect(event.target.value)}
        aria-label="Select the demo user to act as"
      >
        {identities.map((identity) => (
          <option key={identity.id} value={identity.id}>
            {identity.display_name} ({identity.email})
          </option>
        ))}
      </select>
      <p className="identity-switcher__hint">
        Demo mode — sent as the <code>{headerName}</code> header. Development only.
      </p>
    </div>
  );
}
