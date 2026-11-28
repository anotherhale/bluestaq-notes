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
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from notes_service.domain.errors import InvariantViolation
from notes_service.domain.governance.policy import (
    AttributeRef,
    AttributeType,
    Comparison,
    PolicyEffect,
    PolicyRule,
    evaluate,
    new_policy_version,
)
from notes_service.infrastructure.governance.policy_codec import encode_rules
from notes_service.infrastructure.governance.policy_versions_db import (
    SqlAlchemyPolicyVersionRepository,
)

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


class TestPolicyApprovalMigrationKeepsLegacyRows:
    """c4e7a1b93d05 must not strand a database that already holds a live policy.

    The revision's docstring promises that a version published before NEX-065 is
    left readable, but a CHECK constraint is table-wide and is evaluated against
    every existing row when SQLite rebuilds the table. The first draft of the
    constraint required an approval on any version in force, so ``upgrade head``
    failed with ``CHECK constraint failed`` on any deployment that had published a
    policy - a deployment that could not then be upgraded at all. These tests pin
    the behaviour the docstring claims.
    """

    PREVIOUS_REVISION = "e5a9c3b7d812"

    def _seed_published_version_without_approval(self, db_path: Path) -> None:
        # ``id`` is an INTEGER primary key: let it autoincrement rather than
        # inserting a text id, which SQLite rejects as a datatype mismatch.
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO policy_versions"
                " (policy_id, version_number, status, rationale, rules,"
                "  created_at, published_at, published_by, drafted_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "notes.default",
                    1,
                    "published",
                    "published before approval was a recorded step",
                    "[]",
                    "2026-11-01 00:00:00.000000",
                    "2026-11-01 00:00:00.000000",
                    "publisher-1",
                    "author-1",
                ),
            )

    def test_upgrade_succeeds_with_a_legacy_published_row(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy-published.db"
        assert _alembic(db_path, "upgrade", self.PREVIOUS_REVISION).returncode == 0
        self._seed_published_version_without_approval(db_path)

        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, (
            "a database holding a pre-NEX-065 published policy must still upgrade:\n"
            f"{result.stderr}"
        )

    def test_the_legacy_row_survives_with_no_invented_approval(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy-survives.db"
        assert _alembic(db_path, "upgrade", self.PREVIOUS_REVISION).returncode == 0
        self._seed_published_version_without_approval(db_path)
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        with sqlite3.connect(db_path) as connection:
            stored = connection.execute(
                "SELECT status, published_by, approved_at, approved_by"
                " FROM policy_versions WHERE policy_id='notes.default'"
            ).fetchone()

        assert stored == ("published", "publisher-1", None, None), (
            f"the row must stay in force and must not gain an approval nobody gave, got {stored}"
        )

    def test_a_half_written_approval_is_still_refused(self, tmp_path: Path) -> None:
        """Tolerating "no approval" must not tolerate a corrupt record."""
        db_path = tmp_path / "half-written.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        with sqlite3.connect(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO policy_versions"
                " (policy_id, version_number, status, rationale, rules,"
                "  created_at, published_at, published_by, drafted_by,"
                "  approved_at, approved_by)"
                " VALUES ('notes.default', 1, 'published', 'r', '[]',"
                " '2026-11-01 00:00:00.000000', '2026-11-01 00:00:00.000000',"
                " 'publisher-1', 'author-1', '2026-11-01 00:00:00.000000', NULL)"
            )


class TestLegacyPolicyWithoutAnyAuthorshipUpgrades:
    """NEX-101: a published row from the *earliest* policy schema must upgrade.

    Every test above seeds ``policy_versions`` at a revision where ``drafted_by``
    and ``approved_at``/``approved_by`` already exist, then writes NULL into them.
    That is the current schema with the constraints loosened by hand, not a
    historical one, and it cannot catch a defect at the revision that *creates*
    the rule. ``9c4d1e8a7b23`` originally carried
    ``drafted_by IS NOT NULL`` in the separation-of-duties CHECK. A legacy row has
    ``drafted_by = NULL``, ``NULL IS NOT NULL`` is FALSE, and SQLite rebuilds the
    table to add the column - so ``upgrade head`` aborted with ``CHECK constraint
    failed`` and the deployment could not be upgraded at all. The row this test
    seeds has only the columns ``4b1e7a9c3f28`` actually had, so the constraint is
    exercised exactly as a real upgrade would exercise it.
    """

    # The revision that creates ``policy_versions``. Its schema has no
    # ``drafted_by``, ``approved_at`` or ``approved_by`` column at all: those are
    # added by 9c4d1e8a7b23 and c4e7a1b93d05 further along the chain.
    EARLIEST_POLICY_REVISION = "4b1e7a9c3f28"

    def _seed_legacy_published(self, db_path: Path, *, policy_id: str) -> None:
        """Insert a row in the shape the earliest policy schema could produce.

        Only columns that existed at ``4b1e7a9c3f28`` are named. Adding
        ``drafted_by``/``approved_*`` here would either fail outright (no such
        column) or, worse, make the test pass by pre-supplying data a real
        deployment never had.
        """
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO policy_versions"
                " (policy_id, version_number, status, rationale, rules,"
                "  created_at, published_at, published_by)"
                " VALUES (?, 1, 'published', ?, '[]', ?, ?, ?)",
                (
                    policy_id,
                    "published before the platform recorded an author",
                    "2026-11-01 00:00:00.000000",
                    "2026-11-01 00:00:00.000000",
                    "publisher-1",
                ),
            )

    def test_the_seeding_revision_has_no_provenance_columns(self, tmp_path: Path) -> None:
        """Pins the premise: this test is meaningless if the columns pre-exist."""
        db_path = tmp_path / "earliest.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0

        with sqlite3.connect(db_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(policy_versions)")}

        assert columns == {
            "id",
            "policy_id",
            "version_number",
            "status",
            "rationale",
            "rules",
            "created_at",
            "published_at",
            "published_by",
        }, f"the earliest policy schema is not what this test assumes: {columns}"

    def test_a_legacy_published_row_upgrades_to_head(self, tmp_path: Path) -> None:
        """The defect: this is the upgrade that could not complete."""
        db_path = tmp_path / "legacy-published.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0
        self._seed_legacy_published(db_path, policy_id="notes.default")

        result = _alembic(db_path, "upgrade", "head")

        assert result.returncode == 0, (
            "a deployment holding a policy published before authors were recorded "
            f"must still reach head:\n{result.stderr}"
        )

    def test_the_legacy_row_keeps_null_provenance_without_a_backfill(self, tmp_path: Path) -> None:
        """No synthetic author, approver, publisher or timestamp is invented."""
        db_path = tmp_path / "legacy-survives.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0
        self._seed_legacy_published(db_path, policy_id="notes.default")
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        with sqlite3.connect(db_path) as connection:
            stored = connection.execute(
                "SELECT status, published_by, published_at, drafted_by,"
                " approved_at, approved_by FROM policy_versions"
                " WHERE policy_id = 'notes.default'"
            ).fetchone()

        assert stored == (
            "published",
            "publisher-1",
            "2026-11-01 00:00:00.000000",
            None,
            None,
            None,
        ), f"legacy provenance must be preserved as-is and not fabricated, got {stored}"

    def test_a_legacy_retired_row_also_upgrades(self, tmp_path: Path) -> None:
        """``retired`` is the other state the constraint covers, and it is history too."""
        db_path = tmp_path / "legacy-retired.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO policy_versions"
                " (policy_id, version_number, status, rationale, rules,"
                "  created_at, published_at, published_by)"
                " VALUES ('old.policy', 1, 'retired', 'superseded', '[]', ?, ?, ?)",
                (
                    "2026-11-01 00:00:00.000000",
                    "2026-11-01 00:00:00.000000",
                    "publisher-2",
                ),
            )

        result = _alembic(db_path, "upgrade", "head")

        assert result.returncode == 0, f"a retired legacy row must upgrade too:\n{result.stderr}"

    def test_the_legacy_row_is_readable_after_the_upgrade(self, tmp_path: Path) -> None:
        """``upgrade`` succeeding is not enough: the row must still load."""
        db_path = tmp_path / "readable.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0
        self._seed_legacy_published(db_path, policy_id="notes.default")
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        # Read through the shipped adapter, not raw SQL: the point is that the
        # application can still see a live policy, not merely that bytes survive.
        engine = sa.create_engine(f"sqlite:///{db_path}")
        store = SqlAlchemyPolicyVersionRepository(sessionmaker(engine))
        live = store.published_for("notes.default")

        assert live is not None, "the legacy policy vanished, which denies everything it allowed"
        assert live.status.value == "published"
        assert live.published_by == "publisher-1"
        assert live.drafted_by is None
        assert live.has_approval_record is False

    def test_the_legacy_row_still_decides_after_the_upgrade(self, tmp_path: Path) -> None:
        """The live policy must still *work*; loading it and denying would be worse."""
        db_path = tmp_path / "decides.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0
        rules = encode_rules(
            (
                PolicyRule(
                    rule_id="allow-low",
                    effect=PolicyEffect.ALLOW,
                    when=Comparison(AttributeRef("risk", AttributeType.STRING), "==", "low"),
                ),
            )
        )
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO policy_versions"
                " (policy_id, version_number, status, rationale, rules,"
                "  created_at, published_at, published_by)"
                " VALUES ('notes.default', 1, 'published', 'legacy', ?, ?, ?, ?)",
                (rules, "2026-11-01 00:00:00.000000", "2026-11-01 00:00:00.000000", "publisher-1"),
            )
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        engine = sa.create_engine(f"sqlite:///{db_path}")
        store = SqlAlchemyPolicyVersionRepository(sessionmaker(engine))
        in_force = store.all_published()

        assert evaluate(in_force, {"risk": "low"}).allowed
        assert not evaluate(in_force, {"risk": "high"}).allowed

    def test_the_upgrade_is_repeatable(self, tmp_path: Path) -> None:
        """Running ``upgrade head`` twice on a legacy database must be a no-op."""
        db_path = tmp_path / "repeatable.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0
        self._seed_legacy_published(db_path, policy_id="notes.default")
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        again = _alembic(db_path, "upgrade", "head")

        assert again.returncode == 0, f"a second upgrade head must be a no-op:\n{again.stderr}"
        with sqlite3.connect(db_path) as connection:
            count = connection.execute("SELECT count(*) FROM policy_versions").fetchone()[0]
        assert count == 1, "the repeat must not duplicate or drop the legacy row"

    def test_downgrade_and_reupgrade_round_trips_over_a_legacy_row(self, tmp_path: Path) -> None:
        """A rollback of the deployment must not strand the legacy row either."""
        db_path = tmp_path / "roundtrip.db"
        assert _alembic(db_path, "upgrade", self.EARLIEST_POLICY_REVISION).returncode == 0
        self._seed_legacy_published(db_path, policy_id="notes.default")
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        down = _alembic(db_path, "downgrade", self.EARLIEST_POLICY_REVISION)
        assert down.returncode == 0, f"downgrade over a legacy row failed:\n{down.stderr}"
        with sqlite3.connect(db_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(policy_versions)")}
            surviving = connection.execute(
                "SELECT status, published_by FROM policy_versions WHERE policy_id='notes.default'"
            ).fetchone()
        assert "drafted_by" not in columns, "downgrade must drop the author column"
        assert surviving == ("published", "publisher-1"), (
            f"downgrade must preserve the policy, not drop it, got {surviving}"
        )

        up = _alembic(db_path, "upgrade", "head")
        assert up.returncode == 0, f"re-upgrade over a legacy row failed:\n{up.stderr}"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT drafted_by FROM policy_versions WHERE policy_id='notes.default'"
            ).fetchone() == (None,), "re-upgrade must not invent an author"


class TestModernPoliciesStillEnforceSeparationOfDuties:
    """NEX-101: tolerating history must not loosen the rule for *new* rows.

    The fix removed ``drafted_by IS NOT NULL`` from the CHECK. That is only safe
    if no new row can reach ``published`` without an author, and if the pairwise
    inequality the constraint does express is still enforced against a writer that
    bypasses the domain. Both are asserted here against the migrated schema.
    """

    def _insert_draft(self, connection: sqlite3.Connection, *, policy_id: str) -> None:
        connection.execute(
            "INSERT INTO policy_versions"
            " (policy_id, version_number, status, rationale, rules, created_at, drafted_by)"
            " VALUES (?, 1, 'draft', 'r', '[]', '2026-11-01 00:00:00.000000', 'author-1')",
            (policy_id,),
        )

    def test_a_newly_published_row_naming_its_own_author_is_refused(self, tmp_path: Path) -> None:
        """The comparison the constraint does carry is still load-bearing."""
        db_path = tmp_path / "self-published.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        with sqlite3.connect(db_path) as connection:
            self._insert_draft(connection, policy_id="notes.default")
            with pytest.raises(sqlite3.IntegrityError, match="separation_of_duties"):
                connection.execute(
                    "UPDATE policy_versions SET status = 'published',"
                    " published_at = '2026-11-02 00:00:00.000000',"
                    " published_by = 'author-1'"
                    " WHERE policy_id = 'notes.default'"
                )

    def test_a_new_approver_that_also_publishes_is_refused(self, tmp_path: Path) -> None:
        """The NEX-078 pair survives the change."""
        db_path = tmp_path / "approver-publishes.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        with sqlite3.connect(db_path) as connection:
            self._insert_draft(connection, policy_id="notes.default")
            connection.execute(
                "UPDATE policy_versions SET status = 'approved',"
                " approved_at = '2026-11-01 12:00:00.000000', approved_by = 'reviewer-1'"
                " WHERE policy_id = 'notes.default'"
            )
            with pytest.raises(sqlite3.IntegrityError, match="separation_of_duties"):
                connection.execute(
                    "UPDATE policy_versions SET status = 'published',"
                    " published_at = '2026-11-02 00:00:00.000000',"
                    " published_by = 'reviewer-1'"
                    " WHERE policy_id = 'notes.default'"
                )

    def test_a_new_author_that_approves_its_own_draft_is_refused(self, tmp_path: Path) -> None:
        """The NEX-065 pair survives the change."""
        db_path = tmp_path / "author-approves.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        with sqlite3.connect(db_path) as connection:
            self._insert_draft(connection, policy_id="notes.default")
            with pytest.raises(sqlite3.IntegrityError, match="separation_of_duties"):
                connection.execute(
                    "UPDATE policy_versions SET status = 'approved',"
                    " approved_at = '2026-11-01 12:00:00.000000', approved_by = 'author-1'"
                    " WHERE policy_id = 'notes.default'"
                )

    def test_three_distinct_principals_are_still_accepted(self, tmp_path: Path) -> None:
        """The control refuses the collapse, not publication itself."""
        db_path = tmp_path / "accepted.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        with sqlite3.connect(db_path) as connection:
            self._insert_draft(connection, policy_id="notes.default")
            connection.execute(
                "UPDATE policy_versions SET status = 'approved',"
                " approved_at = '2026-11-01 12:00:00.000000', approved_by = 'reviewer-1'"
                " WHERE policy_id = 'notes.default'"
            )
            connection.execute(
                "UPDATE policy_versions SET status = 'published',"
                " published_at = '2026-11-02 00:00:00.000000',"
                " published_by = 'publisher-1'"
                " WHERE policy_id = 'notes.default'"
            )
            stored = connection.execute(
                "SELECT status, drafted_by, approved_by, published_by FROM policy_versions"
            ).fetchone()

        assert stored == ("published", "author-1", "reviewer-1", "publisher-1")

    def test_the_whole_lifecycle_runs_on_a_migrated_database(self, tmp_path: Path) -> None:
        """Draft -> approve -> publish, through the shipped adapter.

        This is the test that would have caught the second NEX-101 defect: the
        chain left ``status_valid`` at ``('draft','published','retired')`` after
        ``c4e7a1b93d05`` introduced ``approved``, so the middle of the only legal
        path to publication failed with a CHECK violation on any migrated
        database. ``create_all`` carries the right constraint, so the fixture-built
        schema could never show it.
        """
        db_path = tmp_path / "lifecycle.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        now = datetime(2026, 11, 20, tzinfo=UTC)
        store = SqlAlchemyPolicyVersionRepository(
            sessionmaker(sa.create_engine(f"sqlite:///{db_path}"))
        )
        draft = new_policy_version(
            policy_id="notes.default",
            version=1,
            rules=(),
            rationale="end to end",
            drafted_by="author-1",
        )
        store.add_draft(draft)
        approved = draft.approve(now=now, approved_by="reviewer-1")
        store.approve(approved)
        store.publish(approved.publish(now=now, published_by="publisher-1"))

        live = store.published_for("notes.default")
        assert live is not None
        assert live.status.value == "published"
        assert live.has_approval_record
        assert (live.drafted_by, live.approved_by, live.published_by) == (
            "author-1",
            "reviewer-1",
            "publisher-1",
        )

    def test_a_legacy_row_and_a_new_lifecycle_coexist(self, tmp_path: Path) -> None:
        """Grandfathering history must not block new governed policies on the same DB."""
        db_path = tmp_path / "coexist.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0

        now = datetime(2026, 11, 20, tzinfo=UTC)
        store = SqlAlchemyPolicyVersionRepository(
            sessionmaker(sa.create_engine(f"sqlite:///{db_path}"))
        )
        # A legacy in-force row for one policy, with no author and no approval.
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO policy_versions"
                " (policy_id, version_number, status, rationale, rules,"
                "  created_at, published_at, published_by, drafted_by)"
                " VALUES ('legacy.policy', 1, 'published', 'legacy', '[]',"
                " '2026-11-01 00:00:00.000000', '2026-11-01 00:00:00.000000',"
                " 'publisher-1', NULL)"
            )

        draft = new_policy_version(
            policy_id="new.policy", version=1, rules=(), rationale="new", drafted_by="author-1"
        )
        store.add_draft(draft)
        store.approve(draft.approve(now=now, approved_by="reviewer-1"))
        store.publish(
            draft.approve(now=now, approved_by="reviewer-1").publish(
                now=now, published_by="publisher-1"
            )
        )

        legacy = store.published_for("legacy.policy")
        current = store.published_for("new.policy")
        assert legacy is not None and legacy.drafted_by is None
        assert legacy.has_approval_record is False
        assert current is not None and current.has_approval_record is True

    def test_the_use_case_still_refuses_a_draft_with_no_author(self) -> None:
        """A NULL author cannot reach ``published`` through the domain either.

        The schema no longer refuses a NULL author on a *new* row (it cannot, since
        a table-wide CHECK must also admit history). This pins the control that
        replaces it, so removing the schema guard did not remove the rule.
        """
        draft = new_policy_version(
            policy_id="notes.default",
            version=1,
            rules=(),
            rationale="no named author",
            drafted_by=None,
            created_at=None,
        )
        # ``approve`` refuses first, which is the control working one step earlier.
        with pytest.raises(InvariantViolation, match="drafting principal is not recorded"):
            draft.approve(now=datetime(2026, 11, 1, 12, 0, tzinfo=UTC), approved_by="reviewer-1")
