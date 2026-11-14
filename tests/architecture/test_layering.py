"""Architecture tests that enforce the dependency direction.

These are cheap, static checks that fail the build if a layer imports something
it must not. They protect the hexagonal boundaries from erosion over time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "notes_service"

# Modules the inner layers must never depend on.
FRAMEWORK_MODULES = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "alembic",
    "jwt",
    "uvicorn",
    "pydantic_settings",
}

LAYER_RULES = {
    # layer directory -> forbidden top-level import prefixes
    "domain": FRAMEWORK_MODULES
    | {
        "notes_service.application",
        "notes_service.infrastructure",
        "notes_service.presentation",
        "notes_service.bootstrap",
    },
    "application": FRAMEWORK_MODULES
    | {
        "notes_service.infrastructure",
        "notes_service.presentation",
        "notes_service.bootstrap",
    },
}


def _iter_python_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("layer", sorted(LAYER_RULES))
def test_layer_does_not_import_forbidden_modules(layer: str) -> None:
    forbidden = LAYER_RULES[layer]
    violations: list[str] = []
    for path in _iter_python_files(layer):
        for module in _top_level_imports(path):
            # Catch both exact matches and submodule imports of forbidden pkgs.
            if any(module == bad or module.startswith(bad + ".") for bad in forbidden):
                violations.append(f"{path.relative_to(SRC)} imports {module}")
    assert not violations, "architecture violations:\n" + "\n".join(sorted(set(violations)))


def test_domain_has_no_pydantic() -> None:
    for path in _iter_python_files("domain"):
        imports = _top_level_imports(path)
        assert not any(m.startswith("pydantic") for m in imports), f"{path} imports pydantic"


def test_presentation_does_not_import_orm_models_directly() -> None:
    """Routes must go through use cases, not reach into ORM rows."""
    presentation = SRC / "presentation"
    violations = []
    for path in presentation.rglob("*.py"):
        imports = _top_level_imports(path)
        if any("infrastructure.db.models" in m for m in imports):
            violations.append(str(path.relative_to(SRC)))
    assert not violations, f"presentation imports ORM models: {violations}"


def test_container_is_only_composition_root() -> None:
    """Infrastructure is imported by bootstrap, not by application/domain."""
    for layer in ("domain", "application"):
        for path in _iter_python_files(layer):
            imports = _top_level_imports(path)
            assert not any(m.startswith("notes_service.infrastructure") for m in imports), (
                f"{path.relative_to(SRC)} imports infrastructure"
            )
