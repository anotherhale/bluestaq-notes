"""Durable governance policy version store (NEX-048).

``InMemoryPolicyVersionRepository`` proved the port, and it stays as the fast
double. This adapter makes a published version outlive the process. R41 records
the gap precisely: a restart forgot every version, including the published ones, so
the policy set silently emptied — and an empty policy set denies everything. That
fails closed, so it was never a security hole, but it meant a deployment could not
hold a policy across a deploy.

Nothing above this file changes. ``PublishDraft``, ``RollbackPolicy`` and the
evaluator depend on
:class:`~notes_service.application.ports.policy_versions.PolicyVersionRepository`,
not on a store, and that is the whole reason the port exists.

The lifecycle is *not* reimplemented here
-----------------------------------------

This adapter stores and transitions; it does not decide. It does not assign version
numbers beyond reporting the next one, it does not choose what to publish, and it
does not retire on its own. It enforces only the two invariants the port promises,
and it enforces them in the schema as well as here so a future caller that bypasses
the use cases cannot corrupt history:

* ``UNIQUE (policy_id, version_number)`` — two authors cannot both claim v4.
* a partial unique index, ``(policy_id) WHERE status = 'published'`` — at most one
  version per policy is ever in force.

The second is the interesting one. The use case already retires the predecessor
*before* publishing the successor, so the adapter sees a legal sequence. A future
caller that forgot the retire would otherwise leave two published rows, and
``published_for`` would have to pick one — silently choosing which policy is in
force is exactly the failure this store exists to prevent. The index turns that into
an ``IntegrityError`` at write time instead of a wrong answer at read time.

Retire-before-publish and crash safety
--------------------------------------

The adapter mirrors the use case's ordering rather than inventing its own. Each
mutation commits in its own transaction, so a crash between the retire and the
publish leaves the old version retired and nothing published — deny, not a wrong
allow — and a retry completes. Committing the two writes together would widen the
window in which a reader sees both, and the partial index would reject the pair
anyway.

A session, not a unit of work
-----------------------------

Like the operator grant store, this adapter takes a ``Session`` factory directly
rather than participating in a content
:class:`~notes_service.infrastructure.db.uow.SqlAlchemyUnitOfWork`. A policy read is
not part of a content transaction: the enforcement path consults the published set
on every gated action, including ones that open no unit of work, and giving it a
session of its own keeps a policy read from holding a content transaction open.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.errors import DomainError, InvariantViolation, ValidationError
from notes_service.domain.governance.policy import PolicyStatus, PolicyVersion
from notes_service.infrastructure.db.models import PolicyVersionRow
from notes_service.infrastructure.governance.policy_codec import decode_rules, encode_rules

_LOGGER = logging.getLogger("notes_service.governance.policy_store")


def _to_domain(row: PolicyVersionRow) -> PolicyVersion:
    """Rebuild a version from its row, re-validating the AST through the domain.

    A stored row is untrusted input on the way back in, so every failure is a
    data-integrity event an operator has to see rather than a silent deny. The
    error is logged *and* re-raised: returning ``None`` here would turn "this
    policy will not parse" into "this policy does not exist", and an absent policy
    is a policy that restricts nothing. Raising keeps the read fail-closed and
    keeps the corrupt row visible.
    """
    try:
        return PolicyVersion(
            policy_id=row.policy_id,
            version=row.version_number,
            rules=decode_rules(row.rules),
            rationale=row.rationale,
            status=PolicyStatus(row.status),
            created_at=row.created_at,
            published_at=row.published_at,
            published_by=row.published_by,
            drafted_by=row.drafted_by,
        )
    except DomainError as error:
        _LOGGER.warning(
            "stored policy version failed re-validation and will not be used: "
            "policy_id=%s version=%s status=%s error=%s",
            row.policy_id,
            row.version_number,
            row.status,
            error,
        )
        raise


def _apply(version: PolicyVersion) -> dict[str, object]:
    """The mutable columns of a row, taken from a domain value."""
    return {
        "status": version.status.value,
        "rationale": version.rationale,
        "rules": encode_rules(version.rules),
        "created_at": version.created_at,
        "published_at": version.published_at,
        "published_by": version.published_by,
        "drafted_by": version.drafted_by,
    }


class SqlAlchemyPolicyVersionRepository:
    """Table-backed policy version store, safe across restarts and workers."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "sqlalchemy"

    def next_version_number(self, policy_id: str) -> int:
        with self._session_factory() as session:
            highest = session.execute(
                select(PolicyVersionRow.version_number)
                .where(PolicyVersionRow.policy_id == policy_id)
                .order_by(PolicyVersionRow.version_number.desc())
                .limit(1)
            ).scalar_one_or_none()
        return (highest or 0) + 1

    def add_draft(self, version: PolicyVersion) -> None:
        if not isinstance(version, PolicyVersion):
            raise ValidationError("add_draft requires a PolicyVersion")
        if version.status is not PolicyStatus.DRAFT:
            raise InvariantViolation("only a draft can be added; publish it instead")
        with self._session_factory() as session:
            existing = session.execute(
                select(PolicyVersionRow).where(
                    PolicyVersionRow.policy_id == version.policy_id,
                    PolicyVersionRow.version_number == version.version,
                )
            ).scalar_one_or_none()
            if existing is not None:
                # Version numbers are history. Overwriting one would rewrite what
                # was in force, which is the exact thing versioning prevents.
                raise InvariantViolation(
                    f"policy version {version.ref} already exists and cannot be overwritten"
                )
            session.add(
                PolicyVersionRow(
                    policy_id=version.policy_id,
                    version_number=version.version,
                    **_apply(version),
                )
            )
            session.commit()

    def publish(self, version: PolicyVersion) -> None:
        self._transition(
            version,
            expected=PolicyStatus.DRAFT,
            required=PolicyStatus.PUBLISHED,
            verb="publish",
        )

    def retire(self, version: PolicyVersion) -> None:
        self._transition(
            version,
            expected=PolicyStatus.PUBLISHED,
            required=PolicyStatus.RETIRED,
            verb="retire",
        )

    def _transition(
        self,
        version: PolicyVersion,
        *,
        expected: PolicyStatus,
        required: PolicyStatus,
        verb: str,
    ) -> None:
        if not isinstance(version, PolicyVersion):
            raise ValidationError(f"{verb} requires a PolicyVersion")
        if version.status is not required:
            raise InvariantViolation(f"{verb} requires a {required.value} version value")
        with self._session_factory() as session:
            row = session.execute(
                select(PolicyVersionRow).where(
                    PolicyVersionRow.policy_id == version.policy_id,
                    PolicyVersionRow.version_number == version.version,
                )
            ).scalar_one_or_none()
            if row is None:
                raise InvariantViolation(f"cannot {verb} {version.ref}: it was never added")
            # ``!=``, not ``is not``: ``row.status`` is a fresh ``str`` decoded from
            # the database, so identity against the enum's interned value is not
            # guaranteed and an identity check would let a legal transition through
            # (or reject one) depending on the string cache.
            if row.status != expected.value:
                raise InvariantViolation(
                    f"cannot {verb} {version.ref}: it is {row.status}, not {expected.value}"
                )
            for column, value in _apply(version).items():
                setattr(row, column, value)
            session.commit()

    def get(self, policy_id: str, version: int) -> PolicyVersion | None:
        with self._session_factory() as session:
            row = session.execute(
                select(PolicyVersionRow).where(
                    PolicyVersionRow.policy_id == policy_id,
                    PolicyVersionRow.version_number == version,
                )
            ).scalar_one_or_none()
            return _to_domain(row) if row is not None else None

    def published_for(self, policy_id: str) -> PolicyVersion | None:
        with self._session_factory() as session:
            rows = list(
                session.execute(
                    select(PolicyVersionRow).where(
                        PolicyVersionRow.policy_id == policy_id,
                        PolicyVersionRow.status == PolicyStatus.PUBLISHED.value,
                    )
                ).scalars()
            )
        if len(rows) > 1:  # pragma: no cover - the partial unique index prevents this
            # Two published versions of one policy is a contradiction the store
            # must never silently resolve by picking one.
            raise InvariantViolation(f"policy {policy_id!r} has {len(rows)} published versions")
        return _to_domain(rows[0]) if rows else None

    def history(self, policy_id: str) -> tuple[PolicyVersion, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                select(PolicyVersionRow)
                .where(PolicyVersionRow.policy_id == policy_id)
                .order_by(PolicyVersionRow.version_number)
            ).scalars()
            return tuple(_to_domain(row) for row in rows)

    def all_published(self) -> tuple[PolicyVersion, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                select(PolicyVersionRow)
                .where(PolicyVersionRow.status == PolicyStatus.PUBLISHED.value)
                .order_by(PolicyVersionRow.policy_id)
            ).scalars()
            return tuple(_to_domain(row) for row in rows)


__all__ = ["SqlAlchemyPolicyVersionRepository"]
