import { useEffect, useMemo, useState } from "react";

import type { Permission, PermissionLevel, User } from "../api/types";

interface ShareDialogProps {
  open: boolean;
  noteTitle: string;
  ownerId: string;
  permissions: Permission[];
  candidates: User[];
  busy: boolean;
  onClose: () => void;
  onGrant: (userId: string, level: PermissionLevel) => Promise<boolean>;
  onRevoke: (userId: string) => Promise<boolean>;
}

/**
 * Owner-only sharing dialog.
 *
 * It lists current collaborators, lets the owner grant READ/WRITE or upgrade an
 * existing grant, and revoke access. Revocation requires an explicit confirm so
 * a stray click cannot silently remove someone's access.
 */
export function ShareDialog({
  open,
  noteTitle,
  ownerId,
  permissions,
  candidates,
  busy,
  onClose,
  onGrant,
  onRevoke,
}: ShareDialogProps) {
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedLevel, setSelectedLevel] = useState<PermissionLevel>("read");
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);

  const usersById = useMemo(() => {
    const map = new Map<string, User>();
    for (const user of candidates) map.set(user.id, user);
    return map;
  }, [candidates]);

  const sharedIds = useMemo(
    () => new Set(permissions.map((permission) => permission.user_id)),
    [permissions],
  );

  // Only offer users who are not the owner and not already shared; upgrades are
  // done from the existing row instead.
  const grantable = useMemo(
    () => candidates.filter((user) => user.id !== ownerId && !sharedIds.has(user.id)),
    [candidates, ownerId, sharedIds],
  );

  useEffect(() => {
    if (!open) {
      setSelectedUserId("");
      setSelectedLevel("read");
      setConfirmRevoke(null);
      return;
    }
    const first = grantable[0];
    setSelectedUserId(first ? first.id : "");
  }, [open, grantable]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const labelFor = (userId: string): string => {
    const user = usersById.get(userId);
    return user ? `${user.display_name} (${user.email})` : userId;
  };

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Share "${noteTitle}"`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal__header">
          <h2 className="modal__title">Share this note</h2>
          <button
            type="button"
            className="button button--ghost"
            onClick={onClose}
            aria-label="Close share dialog"
          >
            ×
          </button>
        </div>

        <p className="modal__subtitle">{noteTitle}</p>

        <section className="modal__section">
          <h3 className="modal__section-title">People with access</h3>
          {permissions.length === 0 ? (
            <p className="modal__empty">Not shared with anyone yet.</p>
          ) : (
            <ul className="share-list" role="list">
              {permissions.map((permission) => (
                <li key={permission.user_id} className="share-list__row">
                  <span className="share-list__user">{labelFor(permission.user_id)}</span>
                  <span className="share-list__controls">
                    <select
                      className="share-list__level"
                      value={permission.permission}
                      disabled={busy}
                      aria-label={`Access level for ${labelFor(permission.user_id)}`}
                      onChange={(event) =>
                        void onGrant(
                          permission.user_id,
                          event.target.value as PermissionLevel,
                        )
                      }
                    >
                      <option value="read">Read</option>
                      <option value="write">Write</option>
                    </select>
                    {confirmRevoke === permission.user_id ? (
                      <>
                        <button
                          type="button"
                          className="button button--danger"
                          disabled={busy}
                          onClick={() => {
                            void onRevoke(permission.user_id).then(() =>
                              setConfirmRevoke(null),
                            );
                          }}
                        >
                          Confirm revoke
                        </button>
                        <button
                          type="button"
                          className="button button--ghost"
                          onClick={() => setConfirmRevoke(null)}
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="button button--ghost"
                        disabled={busy}
                        onClick={() => setConfirmRevoke(permission.user_id)}
                      >
                        Revoke
                      </button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="modal__section">
          <h3 className="modal__section-title">Add a collaborator</h3>
          {grantable.length === 0 ? (
            <p className="modal__empty">Everyone available already has access.</p>
          ) : (
            <div className="share-add">
              <select
                className="share-add__user"
                value={selectedUserId}
                disabled={busy}
                aria-label="Choose a user to share with"
                onChange={(event) => setSelectedUserId(event.target.value)}
              >
                {grantable.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.display_name} ({user.email})
                  </option>
                ))}
              </select>
              <select
                className="share-add__level"
                value={selectedLevel}
                disabled={busy}
                aria-label="Choose an access level"
                onChange={(event) => setSelectedLevel(event.target.value as PermissionLevel)}
              >
                <option value="read">Read</option>
                <option value="write">Write</option>
              </select>
              <button
                type="button"
                className="button button--primary"
                disabled={busy || selectedUserId === ""}
                onClick={() => void onGrant(selectedUserId, selectedLevel)}
              >
                Grant access
              </button>
            </div>
          )}
        </section>

        <div className="modal__footer">
          <button type="button" className="button" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
