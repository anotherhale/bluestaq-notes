"""Note read-then-authorize must live in one place, and must keep 404 before 403.

ADR-008 splits two outcomes that look identical to a client but must not be
interchangeable: a note the caller cannot *read* answers ``ResourceNotFound``
(so a denied action never confirms the note exists), while a caller who can read
but may not perform the action answers ``AccessDenied``. Getting that split
wrong in either direction leaks something -- existence to an outsider, or a
false 404 to a collaborateur who is entitled to a real refusal.

Before this guard, six use cases hand-rolled the same block: fetch the note,
raise ``ResourceNotFound`` if it is missing, call ``decide``, raise
``AccessDenied`` if refused. Each copy is an independent chance to check the
note after deciding to deny it, which turns the 403 into an existence oracle.
The blocks were byte-for-byte identical, so they were collapsed into
``UseCase._load_note_for_read`` / ``_load_note_for_action`` and the invariant was
moved into one method.

The ordering rule is a security property, so it is asserted here from the source
rather than trusted to review. The exemptions below are *documented* choices,
not oversights: ``approval_advisory._note_title`` returns ``None`` instead of
raising, because an advisory about a pending approval is not the place to decide
whether the underlying action is legal.
"""

from __future__ import annotations

import ast
from pathlib import Path

USE_CASES = (
    Path(__file__).resolve().parents[2] / "src" / "notes_service" / "application" / "use_cases"
)

#: The only module allowed to fetch a note and decide on it. Everything else
#: states its intent through the two helpers there.
HELPER_FILE = "base.py"
READ_HELPER = "_load_note_for_read"
ACTION_HELPER = "_load_note_for_action"

#: Functions that load a note without going through a helper, each for a reason
#: recorded in its docstring.
RAW_LOAD_EXEMPTIONS = {
    # Returns None for a missing or unreadable note: an advisory must not act as
    # an existence oracle and must not refuse the request either.
    ("approval_advisory.py", "_note_title"),
}


def _use_case_modules() -> list[Path]:
    return sorted(USE_CASES.glob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _enclosing_function(tree: ast.Module) -> dict[int, str]:
    """Map id(node) -> qualified function name for every node in the module."""
    owner: dict[int, str] = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    owner[id(node)] = f"{cls.name}.{fn.name}"
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(fn) not in owner:
            for node in ast.walk(fn):
                owner.setdefault(id(node), fn.name)
    return owner


def _decide_call_takes_note(call: ast.Call) -> bool:
    """A note authorization decision, as opposed to an organization/agent one."""
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "decide":
        return False
    return any(kw.arg == "note" for kw in call.keywords)


def _raw_note_fetch(call: ast.Call) -> bool:
    """``uow.notes.get(...)`` -- a fetch that no helper mediated."""
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "get"
        and isinstance(f.value, ast.Attribute)
        and f.value.attr == "notes"
    )


def _raises_access_denied(fn: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
        and n.exc.func.id == "AccessDenied"
        for n in ast.walk(fn)
    )


def _fetches_note(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        if _raw_note_fetch(n):
            return True
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in {READ_HELPER, ACTION_HELPER}:
            return True
    return False


def test_note_decisions_happen_only_in_the_helper() -> None:
    """No module may decide on a note except the helpers that own ADR-008."""
    offenders: list[str] = []
    for path in _use_case_modules():
        if path.name == HELPER_FILE:
            continue
        tree = _parse(path)
        owner = _enclosing_function(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _decide_call_takes_note(node):
                offenders.append(f"{path.name}:{owner.get(id(node), '<module>')}")
    assert not offenders, (
        "note authorization decided outside the shared helper; route it through "
        f"UseCase._load_note_for_action so 404-before-403 stays in one place: {offenders}"
    )


def test_note_fetch_happens_only_in_the_helper() -> None:
    """A raw ``uow.notes.get`` bypasses the readability check the helpers add."""
    offenders: list[str] = []
    for path in _use_case_modules():
        if path.name == HELPER_FILE:
            continue
        tree = _parse(path)
        owner = _enclosing_function(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _raw_note_fetch(node)):
                continue
            name = owner.get(id(node), "<module>")
            if (path.name, name.split(".")[-1]) in RAW_LOAD_EXEMPTIONS:
                continue
            offenders.append(f"{path.name}:{name}")
    assert not offenders, (
        "note fetched without the shared load-and-authorize helper: "
        f"{offenders}; use _load_note_for_read/_load_note_for_action"
    )


def test_no_use_case_both_fetches_a_note_and_refuses_with_403() -> None:
    """Fetching then raising AccessDenied inline reopens the existence oracle.

    A 403 is only safe once readability is established, which is exactly what
    the helper does. A use case that fetches the note itself and then refuses
    may have refused before knowing whether the caller could see it.
    """
    offenders: list[str] = []
    for path in _use_case_modules():
        if path.name == HELPER_FILE:
            continue
        for fn in ast.walk(_parse(path)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _fetches_note(fn) and _raises_access_denied(fn):
                offenders.append(f"{path.name}:{fn.name}")
    assert not offenders, (
        "use case both loads a note and raises AccessDenied directly, which can "
        f"deny before readability is known: {offenders}"
    )


def _method_body(path: Path, cls_name: str, method: str) -> ast.FunctionDef:
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method:
                    return item
    raise AssertionError(f"{cls_name}.{method} not found in {path.name}")


def test_action_helper_checks_readability_before_it_refuses() -> None:
    """The 404-before-403 ordering, asserted by position in the source.

    ``_load_note_for_action`` must consult ``_load_note_for_read`` (which raises
    404) before it raises its own 403. Reverse them and a caller who cannot read
    the note learns it exists.
    """
    helper = _method_body(USE_CASES / HELPER_FILE, "UseCase", ACTION_HELPER)

    read_line = next(
        n.lineno
        for n in ast.walk(helper)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == READ_HELPER
    )
    denied_line = next(
        n.lineno
        for n in ast.walk(helper)
        if isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
        and n.exc.func.id == "AccessDenied"
    )
    assert read_line < denied_line, (
        f"{ACTION_HELPER} raises AccessDenied (line {denied_line}) before the "
        f"readability check (line {read_line}); a non-reader would learn the note exists"
    )


def test_read_helper_answers_404_not_403() -> None:
    """An unreadable note and a missing note must be indistinguishable."""
    helper = _method_body(USE_CASES / HELPER_FILE, "UseCase", READ_HELPER)
    raised = {
        n.exc.func.id
        for n in ast.walk(helper)
        if isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
    }
    assert "ResourceNotFound" in raised, f"{READ_HELPER} must raise ResourceNotFound"
    assert "AccessDenied" not in raised, (
        f"{READ_HELPER} must not raise AccessDenied; that is {ACTION_HELPER}'s job "
        "once readability is established"
    )
