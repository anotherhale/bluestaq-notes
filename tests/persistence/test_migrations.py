"""Alembic migration tests.

The rest of the suite builds its schema with ``Base.metadata.create_all``, which
never executes a migration. That blind spot is how a migration that raised
``NameError`` on import (an unimported ``UTCDateTime``) reached a branch with a
green test suite. These tests run the real migration chain, so a broken revision,
model drift, or a non-reversible downgrade fails CI instead of deployment.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ORGANIZATION_TABLES = {
    "organizations",
    "organization_units",
    "teams",
    "organization_memberships",
    "team_memberships",
}


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DATABASE_PROVIDER": "sqlite",
        "DATABASE_URL": f"sqlite:///{db_path}",
        "ENVIRONMENT": "testing",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in rows}


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "migrations.db"
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, f"upgrade head failed:\n{result.stderr}"
    return db_path


class TestMigrationChain:
    def test_upgrade_head_creates_organization_tables(self, migrated_db: Path) -> None:
        assert _tables(migrated_db) >= ORGANIZATION_TABLES

    def test_head_has_no_pending_model_drift(self, migrated_db: Path) -> None:
        """A model change without a matching revision would drift from deployment."""
        result = _alembic(migrated_db, "check")
        assert result.returncode == 0, f"schema drift detected:\n{result.stdout}\n{result.stderr}"

    def test_downgrade_is_reversible(self, migrated_db: Path) -> None:
        result = _alembic(migrated_db, "downgrade", "8b0debcd0ebc")
        assert result.returncode == 0, f"downgrade failed:\n{result.stderr}"
        tables = _tables(migrated_db)
        assert not (ORGANIZATION_TABLES & tables)
        assert "notes" in tables

    def test_downgrade_then_upgrade_round_trips(self, migrated_db: Path) -> None:
        assert _alembic(migrated_db, "downgrade", "8b0debcd0ebc").returncode == 0
        assert _alembic(migrated_db, "upgrade", "head").returncode == 0
        assert _tables(migrated_db) >= ORGANIZATION_TABLES
