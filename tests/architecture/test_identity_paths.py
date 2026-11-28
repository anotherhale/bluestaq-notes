"""Every identity provider must obey the tenant-access seam.

``application/use_cases/tenant_access.py`` claims the tenant-access rule is
shared by "every path that establishes or restores an identity". The prose
enumeration in that docstring has been wrong twice -- it said three paths, then
four, while a fifth (the demo provider) authenticated without the check. A
comment cannot count; this test derives the provider set from the source tree,
so a provider added tomorrow without the seam fails here instead of shipping an
offboarding hole.

Discovery is structural, not a hardcoded list. Every class in
``infrastructure/identity`` that defines ``authenticate(self, context)`` is a
provider; each must satisfy one of:

* it calls the seam itself (``ensure_tenant_access`` / ``resolve_tenant_access``);
* it delegates to another provider's ``authenticate`` (the routing composite,
  which is covered transitively because every delegate is measured too);
* it delegates to an injected collaborator whose declared type is a class
  defined in this source tree, and that class reaches the seam -- this is the
  session cookie path, whose rule lives in ``ResolveSession.run``.

The delegation form is verified against the real class body, so moving the check
out of ``ResolveSession`` breaks this test rather than quietly vacating it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "notes_service"
IDENTITY_DIR = SRC / "infrastructure" / "identity"

#: Calling this *enforces* the rule: it raises ``AuthenticationError`` on denial.
ENFORCING_SEAM = "ensure_tenant_access"

#: Calling this only *reports* the rule. It counts as enforcement only alongside a
#: comparison against the denied status, otherwise ``resolve_tenant_access(...)``
#: whose result is discarded would satisfy this test while enforcing nothing.
REPORTING_SEAM = "resolve_tenant_access"
DENIED_STATUS_MARKER = "DENIED"

#: Modules in the identity package that are deliberately not providers, each with
#: the reason it cannot establish an identity. Listing them (rather than skipping
#: unknown files) means a new module needs a conscious decision here.
NON_PROVIDER_MODULES = {
    "context.py": (
        "transport-neutral request context; it describes a request, it does not "
        "resolve one into a Principal"
    ),
    "oidc_relying_party.py": (
        "HTTP adapter for the relying-party port; it verifies an ID token and "
        "returns VerifiedIdToken. The principal is established by the login "
        "callback (EstablishSession._resolve_user), which applies the seam"
    ),
}

#: Providers known to exist today. Discovery must find at least these, so a
#: broken path glob or a renamed directory cannot make every test pass by
#: finding nothing.
EXPECTED_PROVIDERS = frozenset(
    {
        "DemoIdentityProvider",
        "OidcIdentityProvider",
        "RoutingIdentityProvider",
        "SessionIdentityProvider",
    }
)


@dataclass(frozen=True)
class Provider:
    name: str
    path: Path
    node: ast.ClassDef
    authenticate: ast.FunctionDef

    @property
    def id(self) -> str:
        return f"{self.path.name}::{self.name}"


def _is_authenticate(node: ast.ClassDef) -> ast.FunctionDef | None:
    """The ``authenticate(self, context)`` method of a class, if it defines one."""
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name != "authenticate":
            continue
        positional = [*item.args.posonlyargs, *item.args.args]
        if len(positional) == 2 and item.args.vararg is None and item.args.kwarg is None:
            return item
    return None


def _discover_providers() -> list[Provider]:
    found: list[Provider] = []
    for path in sorted(IDENTITY_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            authenticate = _is_authenticate(node)
            if authenticate is not None:
                found.append(
                    Provider(name=node.name, path=path, node=node, authenticate=authenticate)
                )
    return found


def _referenced_names(node: ast.AST) -> set[str]:
    """Every name a subtree mentions, as attribute or bare identifier.

    Deliberately coarse: the question is "does this code touch the seam or a
    named collaborator", not "how exactly". A coarse set has no false negatives.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.Name):
            names.add(child.id)
    return names


def _annotation_names(node: ast.AST) -> set[str]:
    """Class names appearing in annotations or default collaborators."""
    return _referenced_names(node)


def _called_names(node: ast.AST) -> set[str]:
    """Names actually *invoked* in a subtree, as ``func`` of an ast.Call.

    Distinct from a bare mention on purpose. A provider that imports
    ``ensure_tenant_access`` and never calls it (or computes and discards it)
    reads as compliant to a mention-based check and enforces nothing -- that
    probe left the suite green. Only a call counts.
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


def _calls_seam(node: ast.AST) -> bool:
    """Whether this code actually *applies* the tenant-access decision.

    ``ensure_tenant_access`` raises on denial, so calling it is enforcement.
    ``resolve_tenant_access`` only reports; counting it as enforcement would let
    a provider read the verdict and ignore it, which is exactly the shape of the
    bug this test exists to catch. It therefore qualifies only when the code also
    mentions the ``DENIED`` status it would have to compare against.
    """
    calls = _called_names(node)
    if ENFORCING_SEAM in calls:
        return True
    return REPORTING_SEAM in calls and DENIED_STATUS_MARKER in _referenced_names(node)


def _delegates_to_authenticate(node: ast.AST) -> bool:
    """Calls another object's ``authenticate`` (the routing composite)."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr == "authenticate":
            return True
    return False


def _modules_by_class() -> dict[str, tuple[Path, ast.ClassDef]]:
    """Every class defined in the source tree, keyed by (unique) name."""
    by_name: dict[str, tuple[Path, ast.ClassDef]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                by_name[node.name] = (path, node)
    return by_name


def _enforcing_collaborators(provider: Provider) -> list[str]:
    """Classes this provider injects that themselves reach the seam."""
    classes = _modules_by_class()
    collaborators: list[str] = []
    declared = _annotation_names(provider.node) | _referenced_names(provider.authenticate)
    for candidate in sorted(declared):
        if candidate == provider.name:
            continue
        entry = classes.get(candidate)
        if entry is None:
            continue
        _path, class_node = entry
        if any(_calls_seam(method) for method in ast.walk(class_node)):
            collaborators.append(candidate)
    return collaborators


def _describe(provider: Provider) -> str:
    satisfies: list[str] = []
    if _calls_seam(provider.authenticate):
        satisfies.append("calls the seam directly")
    if _delegates_to_authenticate(provider.authenticate):
        satisfies.append("delegates to another provider's authenticate")
    for collaborator in _enforcing_collaborators(provider):
        satisfies.append(f"delegates to {collaborator}, which enforces the seam")
    return "; ".join(satisfies) or "does NOTHING that enforces the tenant-access seam"


def test_discovery_finds_the_known_providers() -> None:
    """Guard against a vacuous pass: the glob must find the real providers."""
    discovered = {provider.name for provider in _discover_providers()}
    missing = EXPECTED_PROVIDERS - discovered
    assert not missing, (
        f"provider discovery found {sorted(discovered)}; missing {sorted(missing)}. "
        "If a provider was renamed, update EXPECTED_PROVIDERS and re-check that its "
        "replacement still routes through the seam."
    )


@pytest.mark.parametrize("provider", _discover_providers(), ids=lambda p: p.id)
def test_provider_obeys_the_tenant_access_seam(provider: Provider) -> None:
    """A provider that establishes an identity must also establish entitlement."""
    assert _describe(provider) != "does NOTHING that enforces the tenant-access seam", (
        f"{provider.id} authenticates without enforcing tenant access. A suspended or "
        "deprovisioned membership would still authenticate through it. Route it through "
        "ensure_tenant_access (or delegate to something that does)."
    )


def test_no_provider_escapes_discovery() -> None:
    """Every module in the identity package is explained: a provider or an exclusion."""
    inspected = {provider.path for provider in _discover_providers()}
    present = set(IDENTITY_DIR.rglob("*.py"))
    excluded = set(NON_PROVIDER_MODULES)
    unexplained = sorted(
        path.name
        for path in present - inspected
        if path.name != "__init__.py" and path.name not in excluded
    )
    assert not unexplained, (
        f"these identity modules define no authenticate() and are not declared exclusions: "
        f"{unexplained}. Add them to NON_PROVIDER_MODULES with the reason they cannot "
        "establish an identity, or the next real provider may hide in an unread file."
    )


def test_exclusions_are_not_stale() -> None:
    """An exclusion whose module no longer exists hides the next provider's file."""
    present = {path.name for path in IDENTITY_DIR.rglob("*.py")}
    stale = sorted(set(NON_PROVIDER_MODULES) - present)
    assert not stale, f"NON_PROVIDER_MODULES names files that are gone: {stale}"
