"""A transport-neutral authentication context.

The presentation layer adapts a FastAPI request into this small object; identity
providers read only the fields they understand. This keeps providers free of
framework types and easy to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HeaderAuthenticationContext:
    authorization_header: str | None = None
    demo_user_id_header: str | None = None

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> HeaderAuthenticationContext:
        """Build a context from a case-insensitive header mapping."""
        lowered = {key.lower(): value for key, value in headers.items()}
        return cls(
            authorization_header=lowered.get("authorization"),
            demo_user_id_header=lowered.get("x-demo-user-id"),
        )
