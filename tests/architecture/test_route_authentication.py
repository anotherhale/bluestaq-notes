"""Every API route must require authentication, derived from the dependency graph.

``presentation/dependencies.py`` documents that authentication "happens exactly
once per request" and that protected routes are authenticated before they are
rate limited. That is true today, but only as a convention: nothing in the tree
enforces it. Delete ``principal: PrincipalDep`` from a router signature, or add a
route with no ``dependencies=[...]`` at all, and the suite stays green while an
anonymous data path is created -- the same "an uncounted path" shape NEX-099b
fixed for identity providers, one layer up, at the transport boundary.

There are two independent authenticating paths, and the guard checks both:

* an explicit route parameter naming a ``PrincipalDep`` or ``AgentTokenSecretDep``
  alias (74 routes take ``PrincipalDep``, 3 take ``AgentTokenSecretDep``);
* ``RateLimitDep``, whose ``enforce_rate_limit`` takes a ``PrincipalDep`` and so
  authenticates transitively for every route that carries it.

This test derives the answer instead of restating it. It resolves the
authentication graph from ``dependencies.py`` itself -- primitives, the
functions that depend on them, and the ``*Dep`` aliases that name them -- then
requires every route in every router to reference an authenticating dependency,
either in its ``dependencies=[...]`` list or in a parameter. A route that
authenticates through no known path fails here.

A route that is *deliberately* unauthenticated (the login bootstrap and the
demo-discovery affordance) is listed in ``ANONYMOUS_ROUTES`` with the reason.
Listing it forces a conscious decision, and ``test_anonymous_routes_are_not_stale``
keeps the list from silently covering a route that was renamed.

The transitive path is defence in depth, not the load-bearing one:
``test_protected_routes_name_authentication_explicitly`` holds every protected
route to naming an authenticating alias itself, so no single route's safety
depends on ``RateLimitDep`` continuing to authenticate. If that dependency ever
loses its principal parameter, ``test_the_shared_rate_limit_dependency_still_authenticates``
fails at the cause instead of the guard quietly resting on it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "notes_service"
DEPENDENCIES = SRC / "presentation" / "dependencies.py"
ROUTERS_DIR = SRC / "presentation" / "api" / "routers"

#: A dependency function authenticates a request when it either calls an
#: ``authenticate`` method (``get_principal`` resolves the request into a
#: Principal) or raises ``AuthenticationError`` on a missing credential
#: (``get_agent_token_secret`` fails closed on an absent bearer token).
AUTHENTICATE_METHOD = "authenticate"
AUTHENTICATION_ERROR = "AuthenticationError"

#: Aliases known to exist today. Discovery must name at least these, so a broken
#: parse or a renamed symbol cannot make every route pass by resolving nothing.
EXPECTED_AUTH_ALIASES = frozenset(
    {"PrincipalDep", "RateLimitDep", "AgentTokenSecretDep", "AgentRateLimitDep"}
)

#: Routes that are unauthenticated by design, keyed by ``router::function`` with
#: the reason. The login bootstrap cannot require a session (it creates one), and
#: demo discovery is the affordance a client uses to learn which demo identities
#: exist before it can present one. Every other route must authenticate.
ANONYMOUS_ROUTES = {
    "auth::begin_login": "starts the authorization-code flow before a session exists",
    "auth::complete_login": "the identity-provider redirect target; establishes the session",
    "auth::logout": "revokes the current session; safe to call with a stale or absent cookie",
    "demo::list_demo_identities": "unauthenticated discovery of development demo identities",
}


@dataclass(frozen=True)
class Route:
    module: str
    function: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    referenced: frozenset[str]

    @property
    def id(self) -> str:
        return f"{self.module}::{self.function}"


def _called_names(node: ast.AST) -> set[str]:
    """Names actually invoked in a subtree, as ``func`` of an ``ast.Call``.

    A bare mention is not authentication: a function that imports
    ``ensure_principal`` and never calls it enforces nothing. Only a call or a
    raise counts toward the primitive test, so this is deliberately call-shaped.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _is_primitive(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether a dependency function itself establishes an identity or fails closed.

    Two shapes count, and only these two:

    * it calls something named ``authenticate`` -- it resolves the request into a
      Principal (``get_principal``);
    * it raises ``AuthenticationError`` -- it refuses an unauthenticated caller
      (``get_agent_token_secret``).

    Rate limiting, pagination and container access do neither.
    """
    if AUTHENTICATE_METHOD in _called_names(function):
        return True
    for child in ast.walk(function):
        if not isinstance(child, ast.Raise):
            continue
        if AUTHENTICATION_ERROR in _called_names(child):
            return True
        exc = child.exc
        if isinstance(exc, ast.Name) and exc.id == AUTHENTICATION_ERROR:
            return True
        if isinstance(exc, ast.Call):
            func = exc.func
            if (isinstance(func, ast.Name) and func.id == AUTHENTICATION_ERROR) or (
                isinstance(func, ast.Attribute) and func.attr == AUTHENTICATION_ERROR
            ):
                return True
    return False


def _annotation_references(node: ast.AST) -> set[str]:
    """Every name in a parameter list, annotations included."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _dependency_aliases(tree: ast.Module) -> dict[str, str]:
    """Map each ``*Dep`` alias to the dependency function it points at.

    Two assignment shapes appear in ``dependencies.py``:

    * ``XDep = Depends(get_x)``
    * ``XDep = Annotated[T, Depends(get_x)]``

    Both name exactly one ``Depends(...)`` target. An assignment with no
    ``Depends`` target is not a dependency alias and is skipped.
    """
    aliases: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = next((t for t in node.targets if isinstance(t, ast.Name)), None)
        if target is None:
            continue
        for child in ast.walk(node.value):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            is_depends = (isinstance(func, ast.Name) and func.id == "Depends") or (
                isinstance(func, ast.Attribute) and func.attr == "Depends"
            )
            if not is_depends or not child.args:
                continue
            arg = child.args[0]
            if isinstance(arg, ast.Name):
                aliases[target.id] = arg.id
                break
    return aliases


@dataclass(frozen=True)
class AuthGraph:
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    primitives: frozenset[str]
    authenticating: frozenset[str]
    aliases: dict[str, str]
    auth_aliases: frozenset[str]

    def authenticates(self, names: frozenset[str] | set[str]) -> bool:
        """Whether a set of referenced names includes an authenticating dependency."""
        if names & self.auth_aliases:
            return True
        return bool(names & self.authenticating)


def _build_graph() -> AuthGraph:
    tree = ast.parse(DEPENDENCIES.read_text(), filename=str(DEPENDENCIES))
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node

    primitives = frozenset(name for name, node in functions.items() if _is_primitive(node))

    aliases = _dependency_aliases(tree)

    # Propagate authentication: a function authenticates if it takes a parameter
    # annotated with an authenticating alias (or one that itself authenticates).
    # Iterate to a fixed point so multi-hop chains resolve regardless of order.
    authenticating: set[str] = set(primitives)
    changed = True
    while changed:
        changed = False
        for name, node in functions.items():
            if name in authenticating:
                continue
            params = _annotation_references(node.args)
            if params & authenticating or params & set(aliases):
                transitive = {aliases[a] for a in params & set(aliases)}
                if params & authenticating or transitive & authenticating:
                    authenticating.add(name)
                    changed = True

    auth_aliases = frozenset(alias for alias, target in aliases.items() if target in authenticating)
    return AuthGraph(
        functions=functions,
        primitives=primitives,
        authenticating=frozenset(authenticating),
        aliases=aliases,
        auth_aliases=auth_aliases,
    )


def _discover_routes() -> list[Route]:
    routes: list[Route] = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        module = path.stem
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_route = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in {"get", "post", "put", "patch", "delete"}
                for decorator in node.decorator_list
            )
            if not is_route:
                continue
            referenced = set()
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    for kw in decorator.keywords:
                        if kw.arg == "dependencies":
                            referenced |= _annotation_references(kw.value)
            referenced |= _annotation_references(node.args)
            routes.append(
                Route(
                    module=module,
                    function=node.name,
                    node=node,
                    referenced=frozenset(referenced),
                )
            )
    return routes


GRAPH = _build_graph()
ROUTES = _discover_routes()


def test_discovery_finds_the_known_aliases() -> None:
    """Guard against a vacuous pass: the aliases and primitives must resolve."""
    missing = EXPECTED_AUTH_ALIASES - GRAPH.auth_aliases
    assert not missing, (
        f"authentication graph resolved aliases {sorted(GRAPH.auth_aliases)}; "
        f"missing {sorted(missing)}. If the shared dependencies were renamed, update "
        "the resolution logic and re-check that the primitives still authenticate."
    )
    assert GRAPH.primitives, "no primitive authenticating dependency was found"


def test_discovery_finds_routes() -> None:
    """A broken router glob must fail loudly, not pass with zero routes."""
    assert len(ROUTES) >= 50, (
        f"discovered only {len(ROUTES)} routes; the router glob is probably broken"
    )


def test_the_shared_rate_limit_dependency_still_authenticates() -> None:
    """Pin the premise the transitive path rests on: ``RateLimitDep`` authenticates.

    ``enforce_rate_limit`` takes a ``PrincipalDep``, so any route relying on it for
    authentication depends on that parameter staying there. Asserting it here means
    a regression fails at the cause rather than at whichever route noticed first.
    """
    assert "RateLimitDep" in GRAPH.auth_aliases, (
        "RateLimitDep no longer resolves to an authenticating dependency. Any route "
        "that relies on it for authentication is now reachable anonymously."
    )
    assert "AgentRateLimitDep" in GRAPH.auth_aliases, (
        "AgentRateLimitDep no longer resolves to an authenticating dependency. The tool "
        "gateway and agent-run routes would accept unauthenticated calls."
    )


def test_protected_routes_name_authentication_explicitly() -> None:
    """No protected route may lean on ``RateLimitDep`` alone for authentication.

    ``RateLimitDep`` authenticates, but only because ``enforce_rate_limit`` happens
    to take a ``PrincipalDep``. That is an accident of one signature, and it is a
    thin one: a route whose sole authenticating reference is a rate limiter reads as
    "unprotected" to anyone auditing it and breaks silently if the limiter changes.
    Every protected route must name its own ``PrincipalDep`` or
    ``AgentTokenSecretDep`` so its safety is visible at the route and independent of
    the limiter.
    """
    explicit = {"PrincipalDep", "AgentTokenSecretDep"}
    relying_on_limiter = sorted(
        route.id
        for route in ROUTES
        if route.id not in ANONYMOUS_ROUTES and not (route.referenced & explicit)
    )
    assert not relying_on_limiter, (
        f"these routes authenticate only via a rate-limit dependency: {relying_on_limiter}. "
        "Give each one an explicit PrincipalDep or AgentTokenSecretDep parameter."
    )


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.id)
def test_route_requires_authentication(route: Route) -> None:
    """Every route authenticates, or is a documented anonymous exemption."""
    if route.id in ANONYMOUS_ROUTES:
        return
    assert GRAPH.authenticates(route.referenced), (
        f"{route.id} references no authenticating dependency "
        f"(referenced: {sorted(route.referenced)}). Either add a rate-limit dependency "
        "that authenticates (PrincipalDep through RateLimitDep, or an agent-token "
        "dependency), or declare it in ANONYMOUS_ROUTES with the reason it is safe to "
        "call without a principal. An unauthenticated route is an anonymous data path."
    )


def test_anonymous_routes_are_not_stale() -> None:
    """Every exemption names a route that still exists and is still anonymous.

    An exemption for a deleted route, or for a route that has since gained
    authentication, is a stale allowance that could later cover a real gap.
    """
    ids = {route.id for route in ROUTES}
    stale = sorted(name for name in ANONYMOUS_ROUTES if name not in ids)
    assert not stale, f"ANONYMOUS_ROUTES names routes that no longer exist: {stale}"


def test_anonymous_exemptions_are_genuinely_anonymous() -> None:
    """An exemption must not also carry an authenticating dependency.

    If a route listed as anonymous starts authenticating, the exemption is
    misleading and should be removed, so a future reader is not told a protected
    route is open.
    """
    by_id = {route.id: route for route in ROUTES}
    contradicted = sorted(
        name
        for name in ANONYMOUS_ROUTES
        if name in by_id and GRAPH.authenticates(by_id[name].referenced)
    )
    assert not contradicted, (
        f"these routes are declared anonymous but authenticate anyway: {contradicted}. "
        "Remove them from ANONYMOUS_ROUTES."
    )
