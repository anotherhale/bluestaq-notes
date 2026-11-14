"""Use cases (application services).

Each use case is a small object constructed with its dependencies (a Unit of
Work factory, an authorization provider, and a clock). Use cases own
transaction boundaries and translate domain errors into application errors.
They contain no HTTP, ORM, or framework knowledge.
"""

from notes_service.application.use_cases.identity import (
    GetCurrentUser,
    ListUsers,
)
from notes_service.application.use_cases.notes import (
    CreateNote,
    DeleteNote,
    GetNote,
    ListNotes,
    UpdateNote,
)
from notes_service.application.use_cases.permissions import (
    ListPermissions,
    RevokePermission,
    ShareNote,
)

__all__ = [
    "CreateNote",
    "DeleteNote",
    "GetCurrentUser",
    "GetNote",
    "ListNotes",
    "ListPermissions",
    "ListUsers",
    "RevokePermission",
    "ShareNote",
    "UpdateNote",
]
