import type { NoteSort } from "../api/types";

interface SearchBarProps {
  query: string;
  onQueryChange: (query: string) => void;
  sort: NoteSort;
  onSortChange: (sort: NoteSort) => void;
  resultCount: number;
  loading: boolean;
}

/** The header search field and the sort control for the current view. */
export function SearchBar({
  query,
  onQueryChange,
  sort,
  onSortChange,
  resultCount,
  loading,
}: SearchBarProps) {
  return (
    <div className="searchbar">
      <label className="searchbar__field">
        <span className="visually-hidden">Search notes</span>
        <span className="searchbar__icon" aria-hidden="true">
          🔍
        </span>
        <input
          className="searchbar__input"
          type="search"
          value={query}
          placeholder="Search notes by title or content"
          aria-label="Search notes"
          onChange={(event) => onQueryChange(event.target.value)}
        />
        {query ? (
          <button
            type="button"
            className="searchbar__clear"
            aria-label="Clear search"
            onClick={() => onQueryChange("")}
          >
            ×
          </button>
        ) : null}
      </label>

      <label className="searchbar__sort">
        <span className="visually-hidden">Sort notes</span>
        <select
          className="searchbar__sort-select"
          value={sort}
          aria-label="Sort notes"
          onChange={(event) => onSortChange(event.target.value as NoteSort)}
        >
          <option value="-updated_at">Last modified</option>
          <option value="updated_at">Last modified (oldest)</option>
          <option value="-created_at">Newest created</option>
          <option value="created_at">Oldest created</option>
          <option value="title">Title (A–Z)</option>
          <option value="-title">Title (Z–A)</option>
        </select>
      </label>

      <span className="searchbar__status" aria-live="polite">
        {loading ? "Searching…" : `${resultCount} note${resultCount === 1 ? "" : "s"}`}
      </span>
    </div>
  );
}
