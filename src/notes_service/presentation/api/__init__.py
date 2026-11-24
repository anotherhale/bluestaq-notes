"""Aggregates the versioned API routers under a single prefix."""

from __future__ import annotations

from fastapi import APIRouter

from notes_service.presentation.api.routers import (
    auth,
    demo,
    folders,
    identity,
    notes,
    organizations,
    permissions,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(demo.router)
api_router.include_router(identity.router)
api_router.include_router(users.router)
api_router.include_router(folders.router)
api_router.include_router(notes.router)
api_router.include_router(permissions.router)
api_router.include_router(organizations.router)

__all__ = ["api_router"]
