"""The caller's identity may never arrive in a request body.

Three separate guarantees hold this line, and all three have been *conventional*
until now -- prose in the routers and docstrings, with a spot-check per model:

1. ``Extra="forbid"``. A body that names a field nobody declared is rejected by
   pydantic before a use case sees it. This is what stops ``{"owner_id": ...}``
   from ever being parsed, and it is the reason the two spot-check tests that
   exist today pass (``tests/api/test_ops_policy_lifecycle_api.py`` for
   ``published_by``, ``tests/api/test_agents_api.py`` for a registered agent's
   owner). Those tests name one field each. Nothing stopped the *next* request
   model from being written against a base without ``extra="forbid"``.

2. No request model names the actor. A model that declared ``owner_id`` or
   ``created_by`` would forbid unknown fields just fine and still let the caller
   choose the actor, because the field would be *known*.

3. The identity argument of every use-case invocation comes from the
   authenticated principal, not from the parsed body. A router could satisfy
   guarantees 1 and 2 and still write
   ``container.create_note().run(payload.owner, ...)`` if it first copied the
   caller into the payload.

The repository states this as an invariant ("No ``owner_id`` or caller identity
is ever taken from a request body"), and every pre-existing check of it is a
behavioural test of one route. The test below derives all three answers from the
source tree instead, so a request model added on the wrong base, an actor field
added to a correct-looking model, or a router reaching into its payload for the
caller all fail here at the cause rather than waiting for a reviewer.

``user_id`` is deliberately *not* treated as an actor field. On this platform it
is always the **target** of a share or admin action -- "grant this to that
user" -- and the actor performing it still comes from the principal. That the
target interpretation is the real one, rather than a covering story, is pinned
behaviourally by the sharing and organization-admin suites, which assert that a
caller can grant to *another* user and cannot thereby act *as* them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "notes_service"
ROUTERS = SRC / "presentation" / "api" / "routers"
SCHEMAS = SRC / "presentation" / "schemas.py"
DEPENDENCIES = SRC / "presentation" / "dependencies.py"

#: The base every wire-bound request model must derive from. Its ``model_config``
#: must set ``extra="forbid"``; both are checked below rather than assumed.
STRICT_BASE = "StrictModel"
FORBID = "forbid"

#: Field names that denote *who is acting*. A request model may not declare any
#: of these: the actor is the authenticated principal, which the boundary
#: supplies, so a body that can name it lets the caller choose whose authority
#: the write carries.
ACTOR_FIELD_PREFIXES = (
    "owner",
    "actor",
    "principal",
    "caller",
    "requester",
    "requested_by",
    "created_by",
    "updated_by",
    "deleted_by",
    "granted_by",
    "decided_by",
    "revoked_by",
    "published_by",
    "approved_by",
    "issued_by",
    "invited_by",
    "impersonat",
    "on_behalf",
    "tenant",
)

#: Route parameters annotated with the raw framework request. FastAPI does not
#: parse a body into these, so they are not request models and are exempt from
#: the model checks. Listed by annotation so a new one is visible.
NON_MODEL_BODY_ANNOTATIONS = frozenset({"Request"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


HTTP_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _is_route_decorator(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in HTTP_ROUTE_METHODS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "router"
    )


def _all_top_level_functions() -> list[tuple[str, ast.FunctionDef]]:
    """Every top-level function in every router module, routes and helpers alike."""
    found: list[tuple[str, ast.FunctionDef]] = []
    for path in sorted(ROUTERS.glob("*.py")):
        tree = _parse(path)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                found.append((path.name, node))
    return found


def _router_functions() -> list[tuple[str, ast.FunctionDef]]:
    """Every top-level function that FastAPI actually serves, with its module name.

    Scoped to decorated handlers: a module-level helper shares a router module but
    is not a request boundary, so its parameters are not parsed from any body.
    ``test_no_use_case_calling_helper_escapes_the_scan`` keeps a helper from
    hiding a use-case invocation out here.
    """
    return [
        (module, fn)
        for module, fn in _all_top_level_functions()
        if any(_is_route_decorator(d) for d in fn.decorator_list)
    ]


def _annotations(fn: ast.FunctionDef) -> dict[str, str]:
    """Route parameter name -> annotation source text."""
    return {
        arg.arg: ast.unparse(arg.annotation)
        for arg in [*fn.args.args, *fn.args.kwonlyargs]
        if arg.annotation is not None
    }


def _dependency_alias_names() -> frozenset[str]:
    """Module-level names ``dependencies.py`` exports, e.g. ``PrincipalDep``.

    Route parameters annotated with one of these are supplied by FastAPI's
    dependency machinery, never parsed from a body.
    """
    tree = _parse(DEPENDENCIES)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


#: Annotations that are plainly not a body model.
SCALAR_ANNOTATIONS = frozenset(
    {"UUID", "str", "int", "bool", "float", "bytes", "ApprovalStatus", "DelegationStatus"}
)


def _annotation_head(annotation: str) -> str:
    """The model name behind ``X``, ``X | None`` and ``list[X]``."""
    head = annotation.split("|")[0].strip()
    if head.startswith("list[") or head.startswith("List["):
        head = head[head.index("[") + 1 : -1].strip()
    return head


def _unresolved_body_annotations() -> list[str]:
    """Route parameters carrying a model-like type this module cannot vouch for.

    If a body model lived outside ``schemas.py`` -- a locally declared
    ``BaseModel``, say -- discovery would skip it and every check below would
    pass while covering nothing. Nothing is expected here; the assertion in
    ``test_every_body_payload_resolves_to_a_known_schema_model`` makes sure that
    stays true instead of becoming an invisible blind spot.
    """
    aliases = _dependency_alias_names()
    unresolved: list[str] = []
    for module, fn in _router_functions():
        for annotation in _annotations(fn).values():
            head = _annotation_head(annotation)
            if head in NON_MODEL_BODY_ANNOTATIONS:
                continue
            if head in aliases or head in SCALAR_ANNOTATIONS:
                continue
            if head.startswith("Annotated") or head in {"Query", "Path", "Body"}:
                continue
            if head not in _schema_classes():
                unresolved.append(f"{module}:{fn.name} binds `{head}` (from `{annotation}`)")
    return unresolved


def _body_model_names() -> dict[str, str]:
    """Request-model class name -> the router that binds it, discovered from the wire.

    Discovery is by *route parameter annotation*, not by a ``*Request`` suffix, so
    a body model that is deliberately named otherwise is still found.
    """
    aliases = _dependency_alias_names()
    models: dict[str, str] = {}
    for module, fn in _router_functions():
        for annotation in _annotations(fn).values():
            head = _annotation_head(annotation)
            if head in NON_MODEL_BODY_ANNOTATIONS or head in aliases:
                continue
            if head in SCALAR_ANNOTATIONS or head.startswith("Annotated"):
                continue
            if head in _schema_classes():
                models[head] = module
    return models


def test_every_body_payload_resolves_to_a_known_schema_model() -> None:
    """No route may bind a model this module cannot enumerate.

    The three guarantees below are only as strong as the discovery that feeds
    them. A body model declared somewhere other than ``schemas.py`` would be
    skipped, and the suite would look green while checking nothing about it.
    """
    assert not _unresolved_body_annotations(), (
        "route parameters bind a model that is not declared in "
        f"presentation/schemas.py, so the body-identity checks cannot see it: "
        f"{_unresolved_body_annotations()}"
    )


_MODEL_CLASSES: dict[str, ast.ClassDef] | None = None


def _schema_classes() -> dict[str, ast.ClassDef]:
    global _MODEL_CLASSES
    if _MODEL_CLASSES is None:
        tree = _parse(SCHEMAS)
        _MODEL_CLASSES = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    return _MODEL_CLASSES


def _is_model_class(name: str) -> bool:
    return name in _schema_classes()


def _base_names(cls: ast.ClassDef) -> list[str]:
    return [ast.unparse(base) for base in cls.bases]


def _extra_setting(cls: ast.ClassDef) -> str | None:
    """The literal ``extra=`` value in the class's ``model_config``, if any."""
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        if "model_config" not in targets:
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        for keyword in stmt.value.keywords:
            if keyword.arg == "extra":
                return ast.unparse(keyword.value).strip("\"'")
    return None


def _declared_fields(cls: ast.ClassDef) -> list[str]:
    return [
        stmt.target.id
        for stmt in cls.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    ]


def _derives_extra_forbid(name: str, seen: frozenset[str] = frozenset()) -> bool:
    """Whether a model forbids unknown fields, resolved through the MRO by name.

    Reading the base's *own* config rather than trusting that ``StrictModel``
    still sets it: a model could inherit from a subclass that overrode it.
    """
    if name in seen:
        return False
    classes = _schema_classes()
    cls = classes.get(name)
    if cls is None:
        return False
    if _extra_setting(cls) == FORBID:
        return True
    return any(
        _derives_extra_forbid(base.split("[")[0], seen | {name}) for base in _base_names(cls)
    )


def _is_actor_field(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in ACTOR_FIELD_PREFIXES)


def _use_case_calls(fn: ast.FunctionDef) -> list[ast.Call]:
    """Calls into a container-built use case: ``container.x().execute(...)``/``.run(...)``.

    Both verbs are in use (73 invocations). Recognising only one would silently
    stop covering whichever the next slice picks.
    """
    calls: list[ast.Call] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in {"execute", "run"}:
            continue
        if not isinstance(func.value, ast.Call):
            continue
        if not isinstance(func.value.func, ast.Attribute):
            continue
        calls.append(node)
    return calls


def _trusted_principal_aliases() -> frozenset[str]:
    """Dependency aliases whose value is an authenticated principal.

    Derived from ``dependencies.py`` instead of hardcoded: an alias is trusted
    when its ``Annotated[..., Depends(<target>)]`` wrapper names a dependency
    that takes a principal-shaped parameter or raises on a missing credential.
    A hardcoded list would go stale the day a second authenticator is added.
    """
    tree = _parse(DEPENDENCIES)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def authenticates(fn_name: str, seen: frozenset[str] = frozenset()) -> bool:
        fn = functions.get(fn_name)
        if fn is None or fn_name in seen:
            return False
        for arg in fn.args.args:
            if arg.annotation is None:
                continue
            alias = ast.unparse(arg.annotation)
            if alias == "PrincipalDep" or alias.startswith("Annotated["):
                text = ast.unparse(fn)
                if "PrincipalDep" in text or "authenticate(" in text:
                    return True
        return "authenticate(" in ast.unparse(fn)

    aliases: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = (
            node.target.id
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            else next(
                (t.id for t in node.targets if isinstance(t, ast.Name))
                if isinstance(node, ast.Assign)
                else None,
                None,
            )
        )
        value = node.value
        if target is None or value is None:
            continue
        text = ast.unparse(value)
        if "Depends(" not in text:
            continue
        dep_target = text[text.index("Depends(") + len("Depends(") :].split(")")[0].strip()
        if dep_target == "get_principal" or authenticates(dep_target):
            aliases.add(target)
    return frozenset(aliases)


def test_no_use_case_invocation_escapes_the_route_scan() -> None:
    """A module-level helper must not reach a use case behind the scan's back.

    The identity checks only inspect decorated route handlers. If a route body
    were refactored to delegate its use-case call to a plain helper, the scan
    would go quiet on that route without any test changing. Rather than widen the
    scan to every function (helpers have no authenticated parameter to point at),
    forbid use-case invocations outside a route handler: put them back on the
    boundary where the identity is visible.
    """
    offenders = [
        f"{module}:{fn.name}"
        for module, fn in _all_top_level_functions()
        if not any(_is_route_decorator(d) for d in fn.decorator_list) and _use_case_calls(fn)
    ]
    assert not offenders, (
        "these non-route helpers invoke use cases, which puts the use-case call "
        f"outside the identity scan: {offenders}"
    )


# -- guarantee 1: the base forbids unknown fields ----------------------------


def test_every_wire_bound_request_model_forbids_unknown_fields() -> None:
    """A body naming an undeclared field must be rejected, not ignored.

    Without this, ``{"owner_id": some_other_user}`` is silently dropped by
    pydantic and the write looks fine -- until a model promotes that field to a
    real one and the drop becomes an assignment. ``extra="forbid"`` converts the
    silent drop into a ``422``, which is the behaviour the existing spot-check
    tests observe.
    """
    models = _body_model_names()
    assert models, "no request models were discovered from the routers; the scan has gone blind"
    offenders = [name for name in models if not _derives_extra_forbid(name)]
    assert not offenders, (
        "these request models do not set extra='forbid' (directly or by inheritance), "
        f"so a caller can smuggle unknown fields past pydantic: {sorted(offenders)}"
    )


def test_the_strict_base_itself_still_forbids_unknown_fields() -> None:
    """Pin the origin, so removing ``extra="forbid"`` there fails even if a test
    re-declares it on a model and hides the regression."""
    cls = _schema_classes()[STRICT_BASE]
    assert _extra_setting(cls) == FORBID


# -- guarantee 2: no request model can name the actor ------------------------


@pytest.mark.parametrize("model_name", sorted(_body_model_names()))
def test_no_request_model_declares_an_actor_field(model_name: str) -> None:
    """A body may name a *target* (``user_id``), never the *actor*.

    ``user_id`` is excluded by policy, documented in the module docstring and
    pinned behaviourally by the sharing/admin suites: it is who the action is
    performed *on*, and the actor is still the principal.
    """
    cls = _schema_classes()[model_name]
    fields = _declared_fields(cls)
    assert fields, f"{model_name} declares no fields; a body model that parses nothing is suspect"
    offenders = [f for f in fields if _is_actor_field(f)]
    assert not offenders, (
        f"{model_name} declares actor-shaped field(s) {offenders}. The caller is the "
        "authenticated principal; a body that can name it lets a caller choose whose "
        "authority the write carries."
    )


def test_the_actor_field_rule_recognises_the_real_spellings() -> None:
    """Mutation-style check on the matcher itself.

    The discovery above proves nothing if ``_is_actor_field`` matches no
    realistic spelling. These are the names the invariant is about.
    """
    for spelling in (
        "owner_id",
        "created_by",
        "published_by",
        "decided_by_user_id",
        "principal_id",
        "actor",
        "caller_id",
        "granted_by",
    ):
        assert _is_actor_field(spelling), f"{spelling!r} should be recognised as an actor field"
    for spelling in ("user_id", "title", "content", "version", "scope", "capabilities"):
        assert not _is_actor_field(spelling), f"{spelling!r} must not be treated as an actor field"


# -- guarantee 3: identity comes from the principal, not the payload ---------


def test_every_use_case_identity_argument_is_the_authenticated_principal() -> None:
    """Every positional identity argument resolves to a principal dependency.

    This is what makes guarantees 1 and 2 sufficient rather than merely helpful:
    even a model that somehow carried an actor field could not have it reach a
    use case this way, because the argument is bound to the dependency-annotated
    parameter -- the one FastAPI fills from authentication, never from JSON.
    """
    trusted = _trusted_principal_aliases()
    assert trusted, "no trusted principal aliases were discovered; the scan has gone blind"

    offenders: list[str] = []
    checked = 0
    for module, fn in _router_functions():
        annotations = _annotations(fn)
        for call in _use_case_calls(fn):
            if not call.args:
                continue
            first = call.args[0]
            checked += 1
            if not isinstance(first, ast.Name):
                offenders.append(f"{module}:{fn.name} passes `{ast.unparse(first)}`")
                continue
            annotation = annotations.get(first.id)
            if annotation not in trusted:
                offenders.append(f"{module}:{fn.name} passes `{first.id}` annotated `{annotation}`")
    assert checked, "no use-case invocations were discovered; the scan has gone blind"
    assert not offenders, (
        "these invocations do not take their identity from an authenticated "
        f"principal dependency: {offenders}"
    )


def test_no_use_case_argument_is_read_from_the_request_body() -> None:
    """No use-case argument may be an attribute of a parsed body model.

    The complementary direction of the previous test: that one pins where the
    identity *does* come from, this one pins that no body value is ever reached
    for it. A body field passed as a *named* argument (``owner_id=payload.x``)
    would otherwise slip past a check that only inspects the first position.
    """
    offenders: list[str] = []
    for module, fn in _router_functions():
        annotations = _annotations(fn)
        body_params = {
            name
            for name, annotation in annotations.items()
            if annotation.split("|")[0].strip() in _body_model_names()
        }
        for call in _use_case_calls(fn):
            for arg in [*call.args, *(kw.value for kw in call.keywords)]:
                if (
                    isinstance(arg, ast.Attribute)
                    and isinstance(arg.value, ast.Name)
                    and arg.value.id in body_params
                    and _is_actor_field(arg.attr)
                ):
                    offenders.append(f"{module}:{fn.name} passes `{ast.unparse(arg)}`")
    assert not offenders, (
        f"use cases are being given an actor-shaped value read from the request body: {offenders}"
    )


def test_the_identity_scan_reads_the_principal_alias_not_a_name() -> None:
    """Guard the scan against passing because it looked at nothing.

    ``PrincipalDep`` is the alias the routers must use; if the discovery in
    ``_trusted_principal_aliases`` ever stops resolving it, the test above would
    fail loudly on every route -- good -- but this makes the root cause obvious
    instead of a 73-line diff.
    """
    trusted = _trusted_principal_aliases()
    assert "PrincipalDep" in trusted


#: Invocations that legitimately pass a *credential under verification* from the
#: body, rather than an actor claiming authority. Token introspection is the only
#: such operation: the caller presents a secret and is told about it. The secret
#: is the subject of the call, not the authority behind it, and the human
#: principal still authenticates the route. Keyed by (module, function, keyword)
#: so an entry that stops being needed fails the staleness test below.
BODY_CREDENTIAL_INTROSPECTION = frozenset(
    {
        ("agents.py", "resolve_agent_token", "secret"),
    }
)


def test_keyword_identity_is_sourced_from_a_dependency() -> None:
    """The agent surface passes identity by keyword; it must still be trusted.

    ``tools.py`` authenticates the *agent* and calls ``run(secret=secret, ...)``
    with no positional identity, so it is invisible to the positional scan. A
    route that reaches a use case with no positional identity must source that
    identity from a dependency parameter.

    The one exception is token introspection, where the body's ``secret`` is the
    thing being *verified* rather than a claim of authority. It is listed
    explicitly in ``BODY_CREDENTIAL_INTROSPECTION`` -- the rule is not widened to
    accommodate it, because "a body may supply a credential" is exactly the shape
    of the hole this module exists to close. The widening is one named entry, and
    it is audited.
    """
    trusted = _trusted_principal_aliases() | {"AgentTokenSecretDep"}
    offenders: list[str] = []
    for module, fn in _router_functions():
        annotations = _annotations(fn)
        for call in _use_case_calls(fn):
            if call.args:
                continue
            identity_kwargs = [
                kw for kw in call.keywords if kw.arg in {"secret", "principal", "agent_principal"}
            ]
            if not identity_kwargs:
                offenders.append(f"{module}:{fn.name} invokes a use case with no identity at all")
                continue
            for kw in identity_kwargs:
                if (module, fn.name, kw.arg) in BODY_CREDENTIAL_INTROSPECTION:
                    continue
                if (
                    not isinstance(kw.value, ast.Name)
                    or annotations.get(kw.value.id) not in trusted
                ):
                    offenders.append(
                        f"{module}:{fn.name} passes {kw.arg}=`{ast.unparse(kw.value)}`"
                    )
    assert not offenders, f"agent/keyword identity is not sourced from a dependency: {offenders}"


def test_body_credential_exemptions_are_still_real() -> None:
    """An exemption that no longer matches anything must be removed, not ignored.

    A stale allowlist entry is how an audit trail quietly becomes a rubber stamp:
    it stops protecting anything while still reading as a deliberate decision.
    """
    observed = set()
    for module, fn in _router_functions():
        for call in _use_case_calls(fn):
            if call.args:
                continue
            for kw in call.keywords:
                if kw.arg is not None:
                    observed.add((module, fn.name, kw.arg))
    stale = BODY_CREDENTIAL_INTROSPECTION - observed
    assert not stale, (
        f"these body-credential exemptions no longer match a real call and must be "
        f"deleted: {sorted(stale)}"
    )
