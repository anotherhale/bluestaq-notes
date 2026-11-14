import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  children?: ReactNode;
}

/** A neutral placeholder for "nothing selected / nothing here" panels. */
export function EmptyState({ title, children }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <p className="empty-state__title">{title}</p>
      {children ? <p className="empty-state__body">{children}</p> : null}
    </div>
  );
}
