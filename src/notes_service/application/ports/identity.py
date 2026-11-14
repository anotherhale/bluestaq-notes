"""Authentication port.

Authentication turns a transport request into a :class:`Principal`. It is
deliberately decoupled from the Notes domain: the domain never knows how a
caller proved their identity.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from notes_service.domain.identity.principal import Principal

#: Provider name used by the development demo identity mechanism. Both the
#: seeder and the demo identity directory agree on this value.
DEMO_PROVIDER_NAME = "demo"


class AuthenticationContext(Protocol):
    """The transport-neutral view of a request that authentication may inspect.

    Adapters read only what they need (for example a bearer token or a
    development header). Keeping this minimal prevents auth code from reaching
    into unrelated request state.
    """

    @property
    def authorization_header(self) -> str | None: ...

    @property
    def demo_user_id_header(self) -> str | None: ...


@runtime_checkable
class IdentityProvider(Protocol):
    """Resolves an :class:`AuthenticationContext` into a :class:`Principal`.

    Implementations MUST raise :class:`AuthenticationError` rather than
    returning ``None`` when a request cannot be authenticated, and MUST NOT
    invent an identity for an unauthenticated request.
    """

    @property
    def name(self) -> str: ...

    def authenticate(self, context: AuthenticationContext) -> Principal: ...
