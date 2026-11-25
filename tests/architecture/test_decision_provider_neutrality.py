"""The decision port must stay vendor-neutral.

The whole value of a ``DecisionProvider`` port is that the platform asks a
question and does not care who answers. That property erodes the moment a vendor
name appears in the inner layers: an ``if provider == "jev"`` branch in a use
case, a ``JevClient`` import in ``domain``, a vendor SDK in the application
layer. Each is small and each makes the port a fiction.

These are static checks, so they cost nothing and fail at the point the mistake
is made rather than when a second provider is attempted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "notes_service"

#: Vendor names that must never be baked into the inner layers. ``jev`` is
#: System One's judgment product; the list is intentionally short and concrete
#: so it does not flag innocent identifiers.
VENDOR_TOKENS = ("jev", "system_one", "systemone")

#: Only the adapter package may know a vendor exists.
ADAPTER_PACKAGE = "infrastructure/decisioning"


def _iter_python_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_inner_layers_do_not_import_a_decision_vendor(layer: str) -> None:
    violations: list[str] = []
    for path in _iter_python_files(layer):
        for module in _imported_modules(path):
            lowered = module.lower()
            if any(token in lowered for token in VENDOR_TOKENS):
                violations.append(f"{path.relative_to(SRC)} imports {module}")
    assert not violations, "vendor leaked into the inner layers:\n" + "\n".join(violations)


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_inner_layers_do_not_branch_on_a_vendor_name(layer: str) -> None:
    """A provider-name branch in a use case defeats the port."""
    violations: list[str] = []
    for path in _iter_python_files(layer):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            # Only string literals used in a comparison matter; a docstring
            # mentioning Jev is documentation, not a decision branch.
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for operand in operands:
                is_text = isinstance(operand, ast.Constant) and isinstance(operand.value, str)
                if is_text and any(token in operand.value.lower() for token in VENDOR_TOKENS):
                    violations.append(
                        f"{path.relative_to(SRC)}:{node.lineno} compares against {operand.value!r}"
                    )
    assert not violations, "vendor-name branch in an inner layer:\n" + "\n".join(violations)


def test_decision_port_imports_only_the_domain_contract() -> None:
    """The port depends on the domain value objects and nothing else."""
    port = SRC / "application" / "ports" / "decisioning.py"
    imports = _imported_modules(port)
    allowed_prefixes = (
        "notes_service.domain.agent.decisioning",
        "typing",
        "__future__",
    )
    unexpected = {
        module
        for module in imports
        if not any(module == p or module.startswith(p + ".") for p in allowed_prefixes)
    }
    assert not unexpected, f"decision port has unexpected imports: {sorted(unexpected)}"


def test_vendor_adapters_are_confined_to_one_package() -> None:
    """Any module naming a decision vendor must live in the adapter package."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith(ADAPTER_PACKAGE):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                lowered = node.module.lower()
                if any(token in lowered for token in VENDOR_TOKENS):
                    offenders.append(f"{relative} imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(token in alias.name.lower() for token in VENDOR_TOKENS):
                        offenders.append(f"{relative} imports {alias.name}")
    assert not offenders, "vendor adapter outside its package:\n" + "\n".join(offenders)
