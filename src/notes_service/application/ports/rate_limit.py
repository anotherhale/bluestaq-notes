"""Rate limiting port.

The port is intentionally tiny so that the in-memory demo adapter can be
replaced by a shared store (Redis, a gateway, etc.) without touching use cases
or HTTP handlers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of a single rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


@runtime_checkable
class RateLimiter(Protocol):
    """Counts requests per key and reports whether the caller is within budget."""

    @property
    def name(self) -> str: ...

    def check(self, key: str) -> RateLimitDecision: ...
