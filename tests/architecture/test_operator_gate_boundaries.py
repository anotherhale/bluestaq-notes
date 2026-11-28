"""Every operator-gated read must pass through ``OperatorGate``, and gate first.

``application/use_cases/operator_gate.py`` is the single enforcement point in
front of every platform-wide operational view: it decides whether the caller
holds a live grant scoped to the operation, refuses with ``ResourceNotFound``,
and records exactly one access entry. Fourteen use cases across six modules ask
it. That is the *whole* control -- there is no second check at the router, and
``ops.py``/``policies.py`` carry only ``PrincipalDep`` and ``RateLimitDep``, both
of which any authenticated user satisfies.

So the boundaries are conventional today, not enforced:

* a new operational view whose use case forgets ``authorize(...)`` is reachable
  by every authenticated user, and nothing in the suite notices;
* a view that calls ``authorize`` *after* reading its data has already leaked
  the count it was supposed to gate -- the docstrings say "gate first, never
  after" but no test reads the order;
* a new scope token that no use case ever asks for is stored, listed, and then
  enforces nothing (``KNOWN_OPERATOR_OPERATIONS`` promises "adding a new
  operational view means adding its token here in the same change", which is
  again prose).

This is the same shape NEX-100 fixed one layer up for route authentication and
NEX-099b fixed for the identity providers: a convention that reads as a control.
The test derives each answer from the source instead of restating it, so it
fails at the cause when a new view is added without its gate.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "notes_service"
USE_CASES = SRC / "application" / "use_cases"
CONTAINER = SRC / "bootstrap" / "container.py"
OPS_ROUTER = SRC / "presentation" / "api" / "routers" / "ops.py"
POLICIES_ROUTER = SRC / "presentation" / "api" / "routers" / "policies.py"

#: The gate method that both decides and records. Only a *call* counts: importing
#: the gate and never asking it enforces nothing.
GATE_METHOD = "authorize"
GATE_CLASS = "OperatorGate"

#: Container factory fields that build an operator-gated use case. Every one of
#: these must resolve to a use case that actually calls the gate.
OPS_FIELDS = frozenset(
    {
        "get_withholding_report",
        "sweep_audit_integrity",
        "get_operator_access_report",
        "purge_operator_access_log",
        "list_operator_grants",
        "issue_operator_grant",
        "revoke_operator_grant",
        "list_revoked_operator_grants",
        "draft_policy",
        "approve_draft",
        "publish_draft",
        "rollback_policy",
        "get_policy_history",
        "list_policies_in_force",
    }
)

#: Router modules whose every route must resolve to a gated use case. These two
#: expose platform-wide data; ``agents``/``notes`` are owner-scoped and gated by
#: the authorization provider instead, so they are deliberately not listed.
GATED_ROUTERS = {"ops": OPS_ROUTER, "policies": POLICIES_ROUTER}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _gate_calls(node: ast.AST) -> list[ast.stmt]:
    """Statements in ``node`` that call ``<something>.authorize(...)``."""
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.stmt)
        and isinstance(child, ast.Expr)
        and isinstance(child.value, ast.Call)
        and isinstance(child.value.func, ast.Attribute)
        and child.value.func.attr == GATE_METHOD
    ]


def _method_of(node: ast.AST) -> str | None:
    """The nearest enclosing ``FunctionDef`` name, if any."""
    for child in ast.walk(node):
        if isinstance(child, ast.FunctionDef):
            return child.name
    return None


@dataclass(frozen=True)
class GatedUseCase:
    """A use case class that asks ``OperatorGate`` for a decision."""

    name: str
    module: str
    operations: frozenset[str]
    """The ``operation=`` names it passes. Derived from the module constant each
    call site names, so the value is the token the gate would actually compare."""

    gated_methods: tuple[str, ...]


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """``NAME = "literal"`` at module scope, including inside a ``frozenset``.

    The scopes are declared as module constants (``HISTORY_OPERATION``) and then
    re-listed inside ``KNOWN_OPERATOR_OPERATIONS``. Both spellings are read so the
    registry and the call sites can be compared without importing either module.
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = next((t for t in node.targets if isinstance(t, ast.Name)), None)
        if target is None or not isinstance(node.value, ast.Constant):
            continue
        if isinstance(node.value.value, str):
            constants[target.id] = node.value.value
    return constants


def _call_operations(node: ast.stmt, constants: dict[str, str]) -> list[str]:
    """Resolve the ``operation=`` argument of each gate call in ``node``."""
    resolved: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if not (isinstance(child.func, ast.Attribute) and child.func.attr == GATE_METHOD):
            continue
        operations = [kw.value for kw in child.keywords if kw.arg == "operation"]
        if not operations:
            # An operation-less call cannot be scoped: the gate requires the
            # token and would raise TypeError. Record it as unresolvable so the
            # test fails where a reader would.
            resolved.append("<MISSING>")
            continue
        value = operations[0]
        if isinstance(value, ast.Name) and value.id in constants:
            resolved.append(constants[value.id])
        else:
            resolved.append(f"<UNRESOLVED:{ast.dump(value)[:40]}>")
    return resolved


def _gate_calling_classes() -> dict[str, GatedUseCase]:
    """Every class in ``application/use_cases`` that calls the gate."""
    found: dict[str, GatedUseCase] = {}
    for path in sorted(USE_CASES.glob("*.py")):
        tree = _parse(path)
        constants = _module_constants(tree)
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            operations: set[str] = set()
            methods: list[str] = []
            for method in [m for m in cls.body if isinstance(m, ast.FunctionDef)]:
                calls = _gate_calls(method)
                if not calls:
                    continue
                methods.append(method.name)
                for call in calls:
                    operations.update(_call_operations(call, constants))
            if methods:
                found[cls.name] = GatedUseCase(
                    name=cls.name,
                    module=path.name,
                    operations=frozenset(operations),
                    gated_methods=tuple(methods),
                )
    return found


def _container_factories() -> dict[str, str]:
    """Map each container factory field to the use case class it is annotated to return.

    The container declares ``field: Callable[[], SomeUseCase]`` and later assigns
    ``field=make_something``. Reading the *annotation* rather than the factory
    body keeps this independent of how the factory is wired.
    """
    tree = _parse(CONTAINER)
    fields: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            annotation = stmt.annotation
            if not isinstance(annotation, ast.Subscript):
                continue
            if not (isinstance(annotation.value, ast.Name) and annotation.value.id == "Callable"):
                continue
            slice_node = annotation.slice
            if not (isinstance(slice_node, ast.Tuple) and len(slice_node.elts) == 2):
                continue
            returns = slice_node.elts[1]
            name = _return_annotation_name(returns)
            if name is not None:
                fields[stmt.target.id] = name
    return fields


def _return_annotation_name(node: ast.AST) -> str | None:
    """The use-case class an annotation names, ignoring an optional ``| None``.

    ``purge_operator_access_log: Callable[[], PurgeOperatorAccessLog | None]``
    names the class on the left of the union; ``None`` is the "retention is
    disabled" arm, not a type the factory can build.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.BinOp):
        return _return_annotation_name(node.left)
    return None


def _router_calls(path: Path) -> dict[str, frozenset[str]]:
    """Map each route function in ``path`` to the ``container.<factory>()`` names it calls."""
    tree = _parse(path)
    routes: dict[str, frozenset[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        is_route = any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in {"get", "post", "put", "patch", "delete"}
            for decorator in node.decorator_list
        )
        if not is_route:
            continue
        factories = {
            child.func.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "container"
        }
        routes[node.name] = frozenset(factories)
    return routes


GATED = _gate_calling_classes()
FACTORIES = _container_factories()


def test_discovery_finds_the_gate_calls() -> None:
    """Guard against a vacuous pass: a broken parse must not report zero gates."""
    assert len(GATED) >= len(OPS_FIELDS), (
        f"discovered only {len(GATED)} gate-calling use cases; expected at least "
        f"{len(OPS_FIELDS)}. A renamed ``{GATE_CLASS}.{GATE_METHOD}`` or a moved "
        "use-case package would make every assertion below pass by resolving nothing."
    )
    # Every discovered case must name at least one operation token.
    unresolved = sorted(
        f"{case.name}:{op}"
        for case in GATED.values()
        for op in case.operations
        if op.startswith("<")
    )
    assert not unresolved, (
        "gate calls whose operation token could not be resolved from a module "
        f"constant: {unresolved}. The token must be a module-level string "
        "constant so the scope can be compared against the registry."
    )


def test_every_ops_container_field_resolves_to_a_gated_use_case() -> None:
    """An operational view whose use case skips the gate is reachable by any user."""
    missing_fields = OPS_FIELDS - set(FACTORIES)
    assert not missing_fields, (
        f"container declares no factory for {sorted(missing_fields)}. If these "
        "were renamed, update OPS_FIELDS *and* confirm the replacements are gated."
    )

    ungated: list[str] = []
    for field in sorted(OPS_FIELDS):
        use_case = FACTORIES[field]
        if use_case not in GATED:
            ungated.append(f"{field} -> {use_case}")
    assert not ungated, (
        "these operator-facing container factories build a use case that never "
        f"calls {GATE_CLASS}.{GATE_METHOD}, so every authenticated user can reach "
        "the platform-wide view behind them:\n  " + "\n  ".join(ungated)
    )


def test_every_gated_use_case_gates_before_it_acts() -> None:
    """The gate must be the *first* statement: a later call has already leaked data.

    Each gated use case's entry method opens with ``authorize(...)`` today. Gating
    after a read would disclose the very count, size or existence the gate exists
    to withhold, and the audit entry would then record a read that already
    happened. The check is structural: the first statement of each method that
    calls the gate must *be* the gate call.
    """
    offenders: list[str] = []
    for path in sorted(USE_CASES.glob("*.py")):
        tree = _parse(path)
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            for method in [m for m in cls.body if isinstance(m, ast.FunctionDef)]:
                calls = _gate_calls(method)
                if not calls:
                    continue
                first = method.body[0] if method.body else None
                first_is_gate = (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Call)
                    and isinstance(first.value.func, ast.Attribute)
                    and first.value.func.attr == GATE_METHOD
                )
                if not first_is_gate:
                    offenders.append(f"{path.name}:{cls.name}.{method.name}")
    assert not offenders, (
        "these use cases call the operator gate but not as their first statement, "
        "so they may disclose platform data before authorising the read:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_every_operator_route_reaches_a_gated_use_case() -> None:
    """Each route in the op-exposing routers must land on a gated use case.

    A route that builds its response from anything other than a gated factory is
    an operational view with no gate. Resolving route -> factory -> use case from
    the source catches a handler wired to a plain ``container.make(...)`` or to a
    use case that was never given the gate.
    """
    offenders: list[str] = []
    unresolved: list[str] = []
    for module, path in sorted(GATED_ROUTERS.items()):
        for function, factories in _router_calls(path).items():
            if not factories:
                # A route in these routers that reads the container through some
                # other shape cannot be traced; treat it as a failure, not a skip.
                unresolved.append(f"{module}.{function} (no container.<factory>() call)")
                continue
            gated_factories = {f for f in factories if f in OPS_FIELDS}
            if not gated_factories:
                offenders.append(f"{module}.{function} -> {sorted(factories)}")
                continue
            for factory in gated_factories:
                use_case = FACTORIES[factory]
                if use_case not in GATED:
                    offenders.append(f"{module}.{function} -> {factory} -> {use_case}")
    assert not unresolved, (
        "routes in the operator routers that could not be traced to a container "
        "factory, so their gating cannot be verified:\n  " + "\n  ".join(unresolved)
    )
    assert not offenders, (
        "these operator routes do not resolve to a gate-calling use case:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_the_scope_registry_covers_every_enforced_operation() -> None:
    """``KNOWN_OPERATOR_OPERATIONS`` must name every token the gate checks.

    The registry's docstring promises "adding a new operational view means adding
    its token here in the same change". It is the issue allowlist, so a token the
    gate enforces but the registry omits cannot be *granted* -- the view would be
    unreachable by any operator hand-issued a scope, while still passing every
    check. The reverse (a registry token nobody enforces) is also caught, because
    it stores authority that buys nothing.
    """
    tree = _parse(USE_CASES / "operator_grants.py")
    constants = _module_constants(tree)
    registry: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = next((t for t in node.targets if isinstance(t, ast.Name)), None)
        if target is None or target.id != "KNOWN_OPERATOR_OPERATIONS":
            continue
        for child in ast.walk(node.value):
            # The set mixes literal tokens with the module constants declared
            # above it (``ISSUE_OPERATION``, ``REVOKE_OPERATION``, ...), so both
            # spellings have to be resolved or the comparison reads a partial set.
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                registry.add(child.value)
            elif isinstance(child, ast.Name) and child.id in constants:
                registry.add(constants[child.id])
    assert registry, "could not resolve KNOWN_OPERATOR_OPERATIONS; the registry moved"

    enforced = {op for case in GATED.values() for op in case.operations if not op.startswith("<")}
    missing_from_registry = sorted(enforced - registry)
    minted_but_unused = sorted(registry - enforced)
    assert not missing_from_registry, (
        "the gate enforces scopes that KNOWN_OPERATOR_OPERATIONS cannot grant, so "
        f"no operator can be issued them: {missing_from_registry}"
    )
    assert not minted_but_unused, (
        "KNOWN_OPERATOR_OPERATIONS lists scopes no use case enforces, so a grant "
        f"naming them buys nothing: {minted_but_unused}"
    )


@pytest.mark.parametrize("field", sorted(OPS_FIELDS))
def test_gate_field_is_gated(field: str) -> None:
    """Per-field view of the container assertion, so a regression names the field."""
    use_case = FACTORIES.get(field)
    assert use_case is not None, f"no container factory annotated for {field}"
    assert use_case in GATED, (
        f"{field} builds {use_case}, which never calls "
        f"{GATE_CLASS}.{GATE_METHOD}; the operational view is ungated"
    )
