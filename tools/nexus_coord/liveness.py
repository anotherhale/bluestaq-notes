"""Whether a lease holder can still be alive.

The lease is bounded by expiry, not by liveness, so a run that crashes mid-window
blocks every successor until the TTL runs out (R38). On a *single* host that is
needlessly slow: the holder embeds its pid in the holder string
(``host-pid``), and the OS can tell us whether that pid is still running.

The decision is deliberately conservative, because a wrong "dead" verdict lets a
second writer in while the first is still writing:

- only a holder on *this* host is probed; a foreign host is ``UNKNOWN`` and the
  caller falls back to expiry, since we cannot see its process table;
- a pid we cannot read is ``ALIVE`` — the probe failing must never be read as
  proof of death;
- a pid of 1 (or 0, or an unparseable holder) is never declared dead. These are
  init/self sentinels that ``os.kill(pid, 0)`` reports as live, so treating them
  as reclaimable would break the guard rather than strengthen it.

Taking over an ``UNKNOWN`` or ``ALIVE`` holder is the caller's decision; this
module only answers "can this holder still be alive on this host", not "should I
take the lease".
"""

from __future__ import annotations

import errno
import os
import socket
from dataclasses import dataclass
from enum import Enum

# ``os.kill(pid, 0)`` sends no signal; it only reports whether we *may* signal
# the pid. EPERM therefore means the process exists but belongs to another user.
_EPERM = errno.EPERM


class HolderLiveness(Enum):
    """What this host can say about a lease holder's process."""

    # The holder is on this host and its pid is gone: safe to treat as dead.
    DEAD = "dead"
    # The holder is on this host and its pid still exists.
    ALIVE = "alive"
    # A different host, an unparseable holder, or a probe that failed. We have
    # no evidence of death, so the caller must fall back to the expiry bound.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Holder:
    """A parsed lease holder: ``host-pid`` (see :func:`default_holder`)."""

    host: str
    pid: int

    @classmethod
    def parse(cls, holder: str) -> Holder | None:
        """Split a holder string, or ``None`` if it is not ``host-pid`` shape.

        ``rsplit`` on the last dash, because a hostname may contain dashes
        (``Andrews-Mac-mini.local-56084`` -> host ``Andrews-Mac-mini.local``,
        pid ``56084``) while the pid never does.
        """
        host, sep, pid_text = holder.rpartition("-")
        if not sep or not host or not pid_text.isdigit():
            return None
        return cls(host=host, pid=int(pid_text))


def _process_exists(pid: int) -> bool | None:
    """Whether ``pid`` exists, or ``None`` if the probe itself failed.

    ``None`` is distinct from ``False`` on purpose: a failed probe must not be
    read as a dead process, so the caller maps ``None`` to ``UNKNOWN``.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by another user.
        return True
    except OSError as exc:
        if exc.errno == _EPERM:
            return True
        return None
    return True


def classify(
    holder: str,
    *,
    local_host: str | None = None,
    process_exists: object = _process_exists,
) -> HolderLiveness:
    """Classify ``holder`` from this host's point of view.

    ``local_host`` and ``process_exists`` are injectable so the classification
    is testable without spawning real processes or depending on the test host's
    name.
    """
    parsed = Holder.parse(holder)
    if parsed is None:
        return HolderLiveness.UNKNOWN

    # pid 1 is init and pid 0 is a process-group sentinel; both answer "live" to
    # a signal-0 probe, so declaring them dead would be wrong in principle and
    # unreachable in practice. Never reclaim them.
    if parsed.pid <= 1:
        return HolderLiveness.ALIVE

    this_host = local_host if local_host is not None else (socket.gethostname() or "unknown")
    if parsed.host != this_host:
        return HolderLiveness.UNKNOWN

    exists = process_exists(parsed.pid)  # type: ignore[operator]
    if exists is False:
        return HolderLiveness.DEAD
    if exists is True:
        return HolderLiveness.ALIVE
    return HolderLiveness.UNKNOWN
