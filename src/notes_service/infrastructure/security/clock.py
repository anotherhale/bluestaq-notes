"""A trivial clock adapter using the system UTC clock."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Returns the current timezone-aware UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)
