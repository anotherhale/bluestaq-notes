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


class TestApprovalValidityBackfill:
    """The approval-validity backfill must run against a *non-empty* table.

    The rest of the migration suite upgrades an empty database, so a data
    backfill can raise on the first row it sees and still leave every test
    green. That is exactly how this migration shipped with a ``TypeError``:
    a plain ``sa.text()`` SELECT returns SQLite's stored string, and adding a
    ``timedelta`` to it aborts the upgrade mid-flight.
    """

    PREVIOUS_REVISION = "b2e5c81f4a07"

    def _seed_legacy_approval(self, db_path: Path) -> None:
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO agent_approval_requests"
                " (id, agent_id, capability, resource_ref, status, requested_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "aaaaaaaa-0000-0000-0000-000000000001",
                    "11111111-1111-1111-1111-111111111111",
                    "delete_note",
                    "note:legacy",
                    "approved",
                    "2026-01-01 00:00:00.000000",
                ),
            )

    def test_backfill_sets_expiry_from_requested_at(self, tmp_path: Path) -> None:
        db_path = tmp_path / "backfill.db"
        result = _alembic(db_path, "upgrade", self.PREVIOUS_REVISION)
        assert result.returncode == 0, f"upgrade to base failed:\n{result.stderr}"
        self._seed_legacy_approval(db_path)

        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, f"backfill failed:\n{result.stderr}"

        with sqlite3.connect(db_path) as connection:
            requested_at, expires_at, consumed_at = connection.execute(
                "SELECT requested_at, expires_at, consumed_at FROM agent_approval_requests"
            ).fetchone()

        assert expires_at is not None, "legacy approval kept an unbounded validity window"
        assert expires_at.startswith("2026-01-04"), expires_at
        assert consumed_at is None, "backfill must not mark an unspent approval as used"
        assert expires_at > requested_at


class TestRevocationDowngradeFoldsRevokedRows:
    """Downgrading must rewrite a real ``revoked`` row, not just an empty table.

    ``e5a3b9c04f21`` widens the status CHECK to admit ``revoked`` and its
    downgrade narrows it again. A narrow CHECK constraint cannot be recreated
    while a ``revoked`` row still exists, so the downgrade folds those rows into
    ``denied`` first. Run against the empty database the rest of this suite uses,
    that ``UPDATE`` touches nothing and the ordering could be wrong without any
    test noticing — the upgrade would then fail on a production downgrade.
    """

    REVOCABLE_REVISION = "e5a3b9c04f21"

    def _seed_revoked_approval(self, db_path: Path) -> None:
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO agent_approval_requests"
                " (id, agent_id, capability, resource_ref, status, requested_at,"
                "  expires_at, revoked_at, revoked_by_user_id, revoke_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "aaaaaaaa-0000-0000-0000-000000000002",
                    "11111111-1111-1111-1111-111111111111",
                    "delete_note",
                    "note:revoked",
                    "revoked",
                    "2026-01-01 00:00:00.000000",
                    "2026-01-04 00:00:00.000000",
                    "2026-01-02 00:00:00.000000",
                    "11111111-1111-1111-1111-111111111111",
                    "customer withdrew consent",
                ),
            )

    def test_downgrade_over_a_revoked_row_succeeds_and_folds_to_denied(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "downgrade.db"
        result = _alembic(db_path, "upgrade", self.REVOCABLE_REVISION)
        assert result.returncode == 0, f"upgrade failed:\n{result.stderr}"
        self._seed_revoked_approval(db_path)

        result = _alembic(db_path, "downgrade", "d4c8e2a71f05")
        assert result.returncode == 0, f"downgrade failed:\n{result.stderr}"

        with sqlite3.connect(db_path) as connection:
            status = connection.execute("SELECT status FROM agent_approval_requests").fetchone()[0]
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(agent_approval_requests)")
            }

        assert status == "denied", "a revoked row must fold to denied, not vanish"
        assert "revoked_at" not in columns, "downgrade must drop the revocation columns"

    def test_the_folded_row_can_be_upgraded_again(self, tmp_path: Path) -> None:
        """The fold is not a one-way door: re-upgrading must still work."""
        db_path = tmp_path / "roundtrip.db"
        assert _alembic(db_path, "upgrade", self.REVOCABLE_REVISION).returncode == 0
        self._seed_revoked_approval(db_path)
        assert _alembic(db_path, "downgrade", "d4c8e2a71f05").returncode == 0
        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, f"re-upgrade failed:\n{result.stderr}"

        with sqlite3.connect(db_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(agent_approval_requests)")
            }

        assert {"revoked_at", "revoked_by_user_id", "revoke_reason"} <= columns


class TestPolicyVersionsColumnBackfill:
    """NEX-052 adds ``policy_versions`` to a table that already has rows in it.

    Two things can go wrong here and neither shows up in an empty-database
    upgrade. First, a NOT NULL column added without a default cannot be filled
    for existing rows, so the upgrade aborts. Second — and this one is silent —
    a *lingering* server default would let a writer that forgets the column
    record "no policy was cited" instead of failing, quietly asserting something
    about a decision that was never made.

    The default is therefore dropped once the backfill is done, and that is
    asserted here against the real schema rather than the migration's prose.
    """

    PREVIOUS_REVISION = "9c4d1e8a7b23"

    def _seed_legacy_invocation(self, db_path: Path) -> None:
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO tool_invocations"
                " (id, agent_id, tool_name, capability, idempotency_key,"
                "  arguments_digest, resource_ref, status, outcome, invoked_at,"
                "  chain_sequence, previous_hash, intent_hash, outcome_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-invocation",
                    "11111111-1111-1111-1111-111111111111",
                    "delete_note",
                    "delete_note",
                    "legacy-key",
                    "a" * 64,
                    "note:legacy",
                    "succeeded",
                    "deleted",
                    "2026-01-01 00:00:00.000000",
                    1,
                    "b" * 64,
                    "c" * 64,
                    "d" * 64,
                ),
            )

    def _policy_versions_column(self, db_path: Path) -> tuple:
        with sqlite3.connect(db_path) as connection:
            for row in connection.execute("PRAGMA table_info(tool_invocations)"):
                if row[1] == "policy_versions":
                    return row
        raise AssertionError("policy_versions column was not created")

    def test_existing_rows_are_backfilled_with_no_citation(self, tmp_path: Path) -> None:
        db_path = tmp_path / "backfill.db"
        assert _alembic(db_path, "upgrade", self.PREVIOUS_REVISION).returncode == 0
        self._seed_legacy_invocation(db_path)

        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, f"backfill failed:\n{result.stderr}"

        with sqlite3.connect(db_path) as connection:
            stored = connection.execute(
                "SELECT policy_versions FROM tool_invocations WHERE id='legacy-invocation'"
            ).fetchone()

        assert stored == ("",), f"a pre-NEX-052 row must read as no citation, got {stored}"

    def test_the_server_default_is_dropped_after_the_backfill(self, tmp_path: Path) -> None:
        db_path = tmp_path / "default.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        column = self._policy_versions_column(db_path)
        assert column[3] == 1, f"policy_versions must be NOT NULL: {column}"
        assert column[4] is None, f"a lingering server default hides a forgotten write: {column}"

    def test_downgrade_removes_the_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "downgrade.db"
        assert _alembic(db_path, "upgrade", self.PREVIOUS_REVISION).returncode == 0
        self._seed_legacy_invocation(db_path)
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        result = _alembic(db_path, "downgrade", self.PREVIOUS_REVISION)
        assert result.returncode == 0, f"downgrade failed:\n{result.stderr}"

        with sqlite3.connect(db_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tool_invocations)")}
            surviving = connection.execute(
                "SELECT status FROM tool_invocations WHERE id='legacy-invocation'"
            ).fetchone()

        assert "policy_versions" not in columns
        assert surviving == ("succeeded",), "downgrade must not drop the invocation itself"
