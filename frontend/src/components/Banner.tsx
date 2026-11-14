import type { ReactNode } from "react";

interface BannerProps {
  tone: "error" | "notice";
  children: ReactNode;
  onDismiss?: () => void;
}

/** A dismissible inline message for errors and transient notices. */
export function Banner({ tone, children, onDismiss }: BannerProps) {
  return (
    <div className={`banner banner--${tone}`} role={tone === "error" ? "alert" : "status"}>
      <span className="banner__text">{children}</span>
      {onDismiss ? (
        <button
          type="button"
          className="banner__dismiss"
          onClick={onDismiss}
          aria-label="Dismiss message"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
