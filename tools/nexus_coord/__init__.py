"""Cross-agent coordination primitives for NEXUS.

NEXUS coordinates itself through files on a single git branch, which has no
compare-and-swap: two writers produce last-writer-wins at file level. This
package supplies the missing atomic primitive.

The only server-side atomic operations available without new infrastructure are
git ref updates, so a lease is one fixed ref:

    refs/locks/nexus/lease

with the holder and expiry carried in the ref's commit message. Every mutation
is a compare-and-swap: a plain push for a free lock, `--force-with-lease=<sha>`
for a takeover or renewal, and a sha-guarded delete for release.

See ADR-013 for the reasoning and the rejected alternatives.
"""

from __future__ import annotations

from .claims import Claim, ClaimRegistry, claim_ref, parse_claim_ref
from .lease import (
    DEFAULT_TTL_SECONDS,
    LEASE_REF,
    Lease,
    LeaseHeldError,
    LeaseManager,
    default_holder,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "LEASE_REF",
    "Claim",
    "ClaimRegistry",
    "Lease",
    "LeaseHeldError",
    "LeaseManager",
    "claim_ref",
    "default_holder",
    "parse_claim_ref",
]
