"""Cross-site request forgery defence for cookie-authenticated requests.

The API authenticates two ways: a bearer credential, and — since browser login
shipped — an ambient session cookie. Only the cookie case is reachable by a
cross-site attacker: a browser attaches it to a request another origin
initiated, so a page on ``evil.example`` can make the victim's browser issue an
authenticated ``POST`` without ever reading a token.

``SameSite=Lax`` is a partial defence at best. It is a per-browser default that
a deployment can weaken (``SameSite=None`` is supported and is required for a
cross-origin frontend), and it still permits top-level cross-site navigations on
some paths. A server-side check is therefore required, not optional.

The rule enforced here, and only here:

* A **safe** method (``GET``/``HEAD``/``OPTIONS``/``TRACE``) is never blocked.
  These must not change state, and auditing that is a separate concern.
* A request that presents **no session cookie** is never blocked by this guard.
  A header- or token-authenticated caller is not riding ambient authority, and
  a caller with no cookie has nothing to forge.
* Any other request must carry an ``Origin`` that names either this host or a
  configured trusted origin. A missing ``Origin``, an opaque ``null`` origin, and
  a mismatched origin are all rejected. Browsers send ``Origin`` on every unsafe
  request, including same-origin ones, so this does not inconvenience a real
  browser client.

The guard refuses rather than rewrites, and it never inspects the request body,
so it composes with the body-size and correlation middleware regardless of order.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

#: Methods that must not change state. Everything outside this set is treated as
#: unsafe, so a method this build has never seen is guarded rather than assumed
#: harmless.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: The header a browser sets to the initiating origin on an unsafe request.
ORIGIN_HEADER = "origin"

#: Proxy headers consulted, in order, to learn the scheme the browser used.
_SCHEME_HEADERS: tuple[str, ...] = ("x-forwarded-proto", "x-forwarded-scheme")

#: Default ports that may be omitted from an origin or a ``Host`` header. Kept
#: so ``https://app.example.com`` and ``https://app.example.com:443`` compare equal.
_DEFAULT_PORTS: dict[str, str] = {"http": "80", "https": "443"}


def is_safe_method(method: str) -> bool:
    """True when ``method`` must not change state and needs no origin check."""
    return method.upper() in SAFE_METHODS


def _split_origin(value: str) -> tuple[str, str, str] | None:
    """Parse ``scheme://host[:port]`` into ``(scheme, host, port)``.

    Returns ``None`` for anything that is not an absolute http(s) origin: an
    opaque ``null`` origin, a relative reference, or a value carrying a path,
    query or credentials. Those are all rejection cases, and returning ``None``
    lets the caller refuse without a second parse.
    """
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in _DEFAULT_PORTS:
        return None
    if not parts.hostname:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    return scheme, parts.hostname.lower(), str(port if port is not None else _DEFAULT_PORTS[scheme])


def request_scheme(request: Request) -> str:
    """The scheme the *browser* used, honouring a proxy's forwarded header.

    A TLS-terminating proxy makes ``request.url.scheme`` read ``http`` even for an
    https request, so the forwarded header is authoritative when present. An
    unrecognised value falls back to the connection's own scheme rather than
    guessing.
    """
    for header in _SCHEME_HEADERS:
        forwarded = request.headers.get(header)
        if forwarded:
            candidate = forwarded.split(",")[0].strip().lower()
            if candidate in _DEFAULT_PORTS:
                return candidate
    return request.url.scheme.lower()


def request_host_port(request: Request) -> tuple[str, str] | None:
    """The ``(host, port)`` the browser addressed, from the ``Host`` header."""
    host_header = request.headers.get("host")
    if not host_header:
        return None
    parsed = _split_origin(f"{request_scheme(request)}://{host_header}")
    if parsed is None:
        return None
    _, host, port = parsed
    return host, port


def origin_is_allowed(
    *,
    origin: str | None,
    request_host: tuple[str, str] | None,
    request_scheme: str,
    trusted_origins: Iterable[str],
) -> bool:
    """Decide whether ``origin`` may drive a cookie-authenticated request.

    Allowed when the origin is exactly a configured trusted origin, or when it
    names the scheme, host and port this request was addressed to. A missing or
    unparseable origin is never allowed: this is a fail-closed check, and
    ``None`` here means "the browser told us nothing", which is not consent.
    """
    if not origin or not origin.strip():
        return False
    parsed = _split_origin(origin)
    if parsed is None:
        return False
    if parsed in {p for p in (_split_origin(o) for o in trusted_origins) if p is not None}:
        return True
    if request_host is None:
        return False
    scheme, host, port = parsed
    request_host_name, request_host_port = request_host
    return scheme == request_scheme and host == request_host_name and port == request_host_port


@dataclass(frozen=True, slots=True)
class CsrfDecision:
    """The guard's verdict, with the reason kept for the audit log."""

    allowed: bool
    reason: str


def evaluate(
    *,
    method: str,
    cookie_present: bool,
    origin: str | None,
    request_host: tuple[str, str] | None,
    request_scheme: str,
    trusted_origins: Iterable[str],
) -> CsrfDecision:
    """Apply the rule and explain the verdict. Pure, so it is unit-testable."""
    if is_safe_method(method):
        return CsrfDecision(allowed=True, reason="safe-method")
    if not cookie_present:
        return CsrfDecision(allowed=True, reason="not-cookie-authenticated")
    if not origin or not origin.strip():
        return CsrfDecision(allowed=False, reason="origin-missing")
    if _split_origin(origin) is None:
        return CsrfDecision(allowed=False, reason="origin-unparseable")
    if origin_is_allowed(
        origin=origin,
        request_host=request_host,
        request_scheme=request_scheme,
        trusted_origins=trusted_origins,
    ):
        return CsrfDecision(allowed=True, reason="origin-allowed")
    return CsrfDecision(allowed=False, reason="origin-mismatch")


def _cookie_values(header: str | None) -> dict[str, str]:
    """Parse a ``Cookie`` header into name→value, first occurrence winning.

    Mirrors the parser the identity adapter uses so the guard and authentication
    cannot disagree about whether a cookie was presented.
    """
    values: dict[str, str] = {}
    if not header:
        return values
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key not in values:
            values[key] = value
    return values


class CookieAuthOriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject cookie-authenticated unsafe requests from a foreign origin."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        cookie_name: str,
        trusted_origins: Iterable[str] = (),
    ) -> None:
        super().__init__(app)
        if not cookie_name or not cookie_name.strip():
            raise ValueError("a cookie name is required to guard cookie auth")
        self._cookie_name = cookie_name
        self._trusted_origins = tuple(
            origin for origin in trusted_origins if origin and origin.strip()
        )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cookie_present = self._cookie_name in _cookie_values(request.headers.get("cookie"))
        decision = evaluate(
            method=request.method,
            cookie_present=cookie_present,
            origin=request.headers.get(ORIGIN_HEADER),
            request_host=request_host_port(request),
            request_scheme=request_scheme(request),
            trusted_origins=self._trusted_origins,
        )
        if not decision.allowed:
            # Recorded on the request so the access log can name the refusal
            # without echoing the hostile header back to the caller.
            request.state.csrf_rejection = decision.reason
            return _error_response(request, decision.reason)
        return await call_next(request)


def _error_response(request: Request, reason: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = {
        "error": {
            "code": "csrf_rejected",
            "message": "cross-site request rejected",
            "details": [{"reason": reason}],
        },
        "request_id": request_id,
    }
    response = JSONResponse(status_code=403, content=body)
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


__all__ = [
    "ORIGIN_HEADER",
    "SAFE_METHODS",
    "CookieAuthOriginGuardMiddleware",
    "CsrfDecision",
    "evaluate",
    "is_safe_method",
    "origin_is_allowed",
    "request_host_port",
    "request_scheme",
]
