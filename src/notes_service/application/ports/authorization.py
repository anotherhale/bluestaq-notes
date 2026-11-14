"""Authorization port and the actions it governs.

Authorization is a separate responsibility from authentication. Every protected
use case asks an :class:`AuthorizationProvider` for an explicit decision; there
is no implicit allow anywhere in the codebase (deny by default).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from notes_service.domain.identity.principal import Principal
from notes_service.domain.notes.note import Note


class Action(StrEnum):
    """A protected operation on a note resource.

    Modelling actions explicitly (rather than checking flags ad hoc) is what
    makes the policy replaceable: a future RBAC/ABAC or external policy engine
    implements the same port over the same action vocabulary.
    """

    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    MANAGE_PERMISSIONS = "manage_permissions"
    # Reorganising a note (folder placement) is an owner-only concern that is
    # distinct from content UPDATE: a WRITE collaborator may edit a note but may
    # not move it within the owner's drive.
    MOVE = "move"


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of an authorization query.

    ``reason`` is for logging and diagnostics; it is never returned to clients.
    """

    allowed: bool
    reason: str

    @classmethod
    def allow(cls, reason: str = "policy_allowed") -> Decision:
        return cls(allowed=True, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> Decision:
        return cls(allowed=False, reason=reason)


@runtime_checkable
class AuthorizationProvider(Protocol):
    """Decides whether a principal may perform an action on a note.

    Implementations must be pure with respect to their inputs and must default
    to denial when a rule does not explicitly grant access.
    """

    @property
    def name(self) -> str: ...

    def decide(self, *, principal: Principal, action: Action, note: Note) -> Decision: ...
