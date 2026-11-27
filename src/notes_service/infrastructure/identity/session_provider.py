"""Session-cookie identity provider.

Authenticates a request from the ``notes_session`` cookie rather than a header.
The provider itself does no database work beyond delegating to the
:class:`ResolveSession` use case, which owns the fail-closed rules (expiry on two
clocks, revocation, and re-checking that the owning user is still active).

The cookie carries an opaque high-entropy secret; only its SHA-256 hash is
stored, so this provider never sees anything a database leak could forge.
"""

from __future__ import annotations

from collections.abc import Callable

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.identity import AuthenticationContext
from notes_service.application.use_cases.authentication import ResolveSession
from notes_service.domain.identity.principal import Principal


class SessionIdentityProvider:
    """Resolves the session cookie into a :class:`Principal`."""

    def __init__(
        self,
        *,
        cookie_name: str,
        resolve_session: Callable[[], ResolveSession],
        context_token: Callable[[AuthenticationContext], str | None],
        provider_name: str = "session",
    ) -> None:
        self._cookie_name = cookie_name
        self._resolve_session = resolve_session
        self._context_token = context_token
        self._provider_name = provider_name

    @property
    def name(self) -> str:
        return self._provider_name

    def authenticate(self, context: AuthenticationContext) -> Principal:
        token = self._context_token(context)
        if not token:
            raise AuthenticationError("no session cookie presented")
        return self._resolve_session().run(session_token=token)


__all__ = ["SessionIdentityProvider"]
