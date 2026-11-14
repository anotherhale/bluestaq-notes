"""A tiny clock port so time-dependent behaviour is testable.

Domain entities default to :func:`datetime.now`; use cases that need a
deterministic timestamp (or a future audit/expiry feature) depend on this port
instead of calling the clock directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
