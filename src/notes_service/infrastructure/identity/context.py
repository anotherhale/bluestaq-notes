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
    session_cookie: str | None = None

    @classmethod
    def from_headers(
        cls, headers: dict[str, str], *, cookie_name: str = "notes_session"
    ) -> HeaderAuthenticationContext:
        """Build a context from a case-insensitive header mapping."""
        lowered = {key.lower(): value for key, value in headers.items()}
        return cls(
            authorization_header=lowered.get("authorization"),
            demo_user_id_header=lowered.get("x-demo-user-id"),
            session_cookie=_parse_cookie(lowered.get("cookie"), cookie_name),
        )


def _parse_cookie(header: str | None, name: str) -> str | None:
    """Extract one cookie value from a ``Cookie`` header.

    Hand-rolled rather than pulled from ``http.cookies`` so this adapter stays a
    plain function over a string and remains trivially unit-testable. A
    duplicate name resolves to the first occurrence, matching browser order.
    """
    if not header:
        return None
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value or None
    return None
