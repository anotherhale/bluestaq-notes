import type { Folder } from "../api/types";
import type { NavView } from "../hooks/useWorkspace";

interface NavigationItemProps {
  label: string;
  active: boolean;
  icon: string;
  onClick: () => void;
}

/** One destination in the left navigation. */
export function NavigationItem({ label, active, icon, onClick }: NavigationItemProps) {
  return (
    <button
      type="button"
      className={`nav-item${active ? " nav-item--active" : ""}`}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
    >
      <span className="nav-item__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="nav-item__label">{label}</span>
    </button>
  );
}

export interface SidebarProps {
  view: NavView;
  selectedFolderId: string | null;
  folders: Folder[];
  loadingFolders: boolean;
  onSelectView: (view: NavView) => void;
  onSelectFolder: (folderId: string | null) => void;
  onNewNote: () => void;
  onNewFolder: () => void;
  onRenameFolder: (folder: Folder) => void;
  onDeleteFolder: (folder: Folder) => void;
}

/**
 * The left navigation: create actions, the primary destinations, and the
 * folder list.
 *
 * Folders are flat (the domain has no hierarchy), so this is a single level of
 * selectable rows; each row is a destination with owner-only rename/delete
 * controls.
 */
export function Sidebar({
  view,
  selectedFolderId,
  folders,
  loadingFolders,
  onSelectView,
  onSelectFolder,
  onNewNote,
  onNewFolder,
  onRenameFolder,
  onDeleteFolder,
}: SidebarProps) {
  return (
    <nav className="sidebar" aria-label="Workspace navigation">
      <div className="sidebar__actions">
        <button type="button" className="button button--primary button--block" onClick={onNewNote}>
          + New note
        </button>
        <button type="button" className="button button--outline button--block" onClick={onNewFolder}>
          + New folder
        </button>
      </div>

      <ul className="sidebar__nav" role="list">
        <li>
          <NavigationItem
            label="My Drive"
            icon="🗂️"
            active={view === "drive" && selectedFolderId === null}
            onClick={() => onSelectFolder(null)}
          />
        </li>
        <li>
          <NavigationItem
            label="All notes"
            icon="📚"
            active={view === "all"}
            onClick={() => onSelectView("all")}
          />
        </li>
        <li>
          <NavigationItem
            label="Starred"
            icon="★"
            active={view === "starred"}
            onClick={() => onSelectView("starred")}
          />
        </li>
        <li>
          <NavigationItem
            label="Shared with me"
            icon="👥"
            active={view === "shared"}
            onClick={() => onSelectView("shared")}
          />
        </li>
      </ul>

      <div className="sidebar__section">
        <div className="sidebar__section-header">
          <span className="sidebar__section-title">Folders</span>
          <button
            type="button"
            className="sidebar__section-action"
            onClick={onNewFolder}
            aria-label="New folder"
            title="Create folder"
          >
            +
          </button>
        </div>
        {loadingFolders ? (
          <p className="sidebar__hint">Loading folders…</p>
        ) : folders.length === 0 ? (
          <p className="sidebar__hint">No folders yet.</p>
        ) : (
          <ul className="folder-tree" role="list">
            {folders.map((folder) => {
              const active = view === "drive" && selectedFolderId === folder.id;
              return (
                <li
                  key={folder.id}
                  className={`folder-tree__row${active ? " folder-tree__row--active" : ""}`}
                >
                  <button
                    type="button"
                    className="folder-tree__select"
                    aria-current={active ? "page" : undefined}
                    onClick={() => onSelectFolder(folder.id)}
                  >
                    <span className="folder-tree__icon" aria-hidden="true">
                      {active ? "📂" : "📁"}
                    </span>
                    <span className="folder-tree__name">{folder.name}</span>
                  </button>
                  <span className="folder-tree__controls">
                    <button
                      type="button"
                      className="folder-tree__control"
                      aria-label={`Rename folder ${folder.name}`}
                      title="Rename folder"
                      onClick={() => onRenameFolder(folder)}
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      className="folder-tree__control"
                      aria-label={`Delete folder ${folder.name}`}
                      title="Delete folder"
                      onClick={() => onDeleteFolder(folder)}
                    >
                      🗑
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </nav>
  );
}
