"""Default authorization policy.

The policy is intentionally small and explicit. It models the actual
requirements (one owner plus READ/WRITE collaborators) directly, rather than a
speculative role hierarchy, while remaining a single replaceable component
behind the :class:`AuthorizationProvider` port.
"""

from __future__ import annotations

from notes_service.application.ports.authorization import Action, Decision
from notes_service.domain.identity.principal import Principal
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.value_objects import PermissionLevel


class DefaultAuthorizationProvider:
    """Owner-plus-collaborator policy with deny-by-default semantics.

    Rules (checked in order):

    * ``READ``   — owner, or any READ/WRITE collaborator.
    * ``UPDATE`` — owner, or a WRITE collaborator.
    * ``DELETE`` — owner only.
    * ``MANAGE_PERMISSIONS`` — owner only.

    Anything not explicitly granted is denied.
    """

    @property
    def name(self) -> str:
        return "default"

    def decide(self, *, principal: Principal, action: Action, note: Note) -> Decision:
        user_id = principal.user_id

        if note.is_owned_by(user_id):
            return Decision.allow("owner")

        level = note.effective_level(user_id)
        if level is None:
            return Decision.deny("no_permission")

        match action:
            case Action.READ:
                # Any grant (READ or WRITE) permits reading.
                if level.includes(PermissionLevel.READ):
                    return Decision.allow(f"collaborator_{level.value}")
                return Decision.deny("insufficient_permission")
            case Action.UPDATE:
                if level.includes(PermissionLevel.WRITE):
                    return Decision.allow(f"collaborator_{level.value}")
                return Decision.deny("insufficient_permission")
            case Action.DELETE | Action.MANAGE_PERMISSIONS:
                # Shared users never delete or manage sharing, even with WRITE.
                return Decision.deny("owner_only")
