import type { NavView } from "../hooks/useWorkspace";

interface BreadcrumbsProps {
  view: NavView;
  folderName: string | null;
  onNavigateRoot: () => void;
}

const VIEW_LABELS: Record<NavView, string> = {
  drive: "My Drive",
  all: "All notes",
  starred: "Starred",
  shared: "Shared with me",
};

/**
 * Breadcrumb navigation for the main workspace.
 *
 * It reflects the active destination: within "My Drive" a folder is a crumb
 * after the root, so the current location is always unambiguous.
 */
export function Breadcrumbs({ view, folderName, onNavigateRoot }: BreadcrumbsProps) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol className="breadcrumbs__list">
        <li className="breadcrumbs__item">
          {view === "drive" ? (
            <button type="button" className="breadcrumbs__link" onClick={onNavigateRoot}>
              My Drive
            </button>
          ) : (
            <span className="breadcrumbs__current">{VIEW_LABELS[view]}</span>
          )}
        </li>
        {view === "drive" && folderName ? (
          <li className="breadcrumbs__item">
            <span className="breadcrumbs__sep" aria-hidden="true">
              /
            </span>
            <span className="breadcrumbs__current">{folderName}</span>
          </li>
        ) : null}
      </ol>
    </nav>
  );
}
