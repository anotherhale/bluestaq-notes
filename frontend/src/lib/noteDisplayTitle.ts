/**
 * A safe, human-readable title for a note.
 *
 * The backend allows a title of length >= 1, so an empty title should never
 * arrive; if it does (e.g. a malformed response) we render a placeholder rather
 * than an empty, unclickable row.
 */
export function noteDisplayTitle(title: string | null | undefined): string {
  const trimmed = (title ?? "").trim();
  return trimmed.length > 0 ? trimmed : "Untitled note";
}

/** A one-line preview of note content for the list. */
export function notePreview(content: string | null | undefined, max = 80): string {
  const normalised = (content ?? "").replace(/\s+/g, " ").trim();
  if (normalised.length === 0) return "No content";
  return normalised.length > max ? `${normalised.slice(0, max - 1)}…` : normalised;
}
