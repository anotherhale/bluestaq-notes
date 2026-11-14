"""In-memory, process-local rate limiter.

A fixed-window counter keyed by an opaque string (for example the principal id,
or a client address for unauthenticated traffic). This is a *demo* adapter:

* state lives in the process, so limits are **not** enforced across multiple
  replicas or workers behind a load balancer;
* a restart clears all counters.

It exists to prove the port and the 429 behaviour. A production deployment would
swap in a shared store (for example Redis) or delegate to an API gateway,
implementing the same :class:`~notes_service.application.ports.rate_limit.RateLimiter`
port. The implementation uses a lock so it is safe under the threadpool.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from notes_service.application.ports.rate_limit import RateLimitDecision


class InMemoryRateLimiter:
    """A fixed-window counter suitable for single-process development use."""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("rate limit must be >= 1")
        if window_seconds < 1:
            raise ValueError("rate limit window must be >= 1 second")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (window_start, count)
        self._buckets: dict[str, tuple[float, int]] = {}

    @property
    def name(self) -> str:
        return "in-memory-fixed-window"

    def check(self, key: str) -> RateLimitDecision:
        now = self._clock()
        with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= self._window:
                start, count = now, 0
            count += 1
            self._buckets[key] = (start, count)
            allowed = count <= self._limit
            remaining = max(0, self._limit - count)
            retry_after = 0 if allowed else max(1, int(self._window - (now - start)) + 1)
        return RateLimitDecision(
            allowed=allowed,
            limit=self._limit,
            remaining=remaining,
            retry_after=retry_after,
        )

    def reset(self) -> None:
        """Clear all counters (used by tests)."""
        with self._lock:
            self._buckets.clear()
