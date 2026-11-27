"""In-memory governance policy version store (NEX-047).

The demo adapter. It exists for the same reason the in-memory operator grant
store does: to prove the port's behaviour with no database, so the use cases and
the evaluator can be tested without a session, and so a deployment that never
configures governance still has a working — empty, and therefore deny-everything
— store.

``name`` reports ``"in-memory"`` so a reader cannot mistake it for durable. A
restart forgets every version, including published ones, which is why the
roadmap's next governance slice is a durable adapter behind this same port rather
than a change to the use cases.

History is preserved within a process: ``publish`` replaces a draft in place and
``retire`` replaces a published version in place, but no entry is ever removed, so
``history`` always answers "what was in force" for the life of the process.
"""

from __future__ import annotations

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.governance.policy import PolicyStatus, PolicyVersion


class InMemoryPolicyVersionRepository:
    """Process-local policy version store, keyed by ``(policy_id, version)``."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], PolicyVersion] = {}

    @property
    def name(self) -> str:
        return "in-memory"

    def next_version_number(self, policy_id: str) -> int:
        existing = [v for (pid, _), v in self._versions.items() if pid == policy_id]
        return max((v.version for v in existing), default=0) + 1

    def add_draft(self, version: PolicyVersion) -> None:
        if not isinstance(version, PolicyVersion):
            raise ValidationError("add_draft requires a PolicyVersion")
        if version.status is not PolicyStatus.DRAFT:
            raise InvariantViolation("only a draft can be added; publish it instead")
        key = (version.policy_id, version.version)
        if key in self._versions:
            # Version numbers are history. Overwriting one would silently rewrite
            # what was in force, which is the exact thing versioning prevents.
            raise InvariantViolation(
                f"policy version {version.ref} already exists and cannot be overwritten"
            )
        self._versions[key] = version

    def publish(self, version: PolicyVersion) -> None:
        if not isinstance(version, PolicyVersion):
            raise ValidationError("publish requires a PolicyVersion")
        if version.status is not PolicyStatus.PUBLISHED:
            raise InvariantViolation("publish requires a published version value")
        key = (version.policy_id, version.version)
        stored = self._versions.get(key)
        if stored is None:
            raise InvariantViolation(f"cannot publish {version.ref}: it was never added")
        if stored.status is not PolicyStatus.DRAFT:
            raise InvariantViolation(
                f"cannot publish {version.ref}: it is already {stored.status.value}"
            )
        self._versions[key] = version

    def retire(self, version: PolicyVersion) -> None:
        if not isinstance(version, PolicyVersion):
            raise ValidationError("retire requires a PolicyVersion")
        if version.status is not PolicyStatus.RETIRED:
            raise InvariantViolation("retire requires a retired version value")
        key = (version.policy_id, version.version)
        stored = self._versions.get(key)
        if stored is None:
            raise InvariantViolation(f"cannot retire {version.ref}: it was never added")
        if stored.status is not PolicyStatus.PUBLISHED:
            raise InvariantViolation(
                f"cannot retire {version.ref}: it is {stored.status.value}, not published"
            )
        self._versions[key] = version

    def get(self, policy_id: str, version: int) -> PolicyVersion | None:
        return self._versions.get((policy_id, version))

    def published_for(self, policy_id: str) -> PolicyVersion | None:
        published = [
            v
            for (pid, _), v in self._versions.items()
            if pid == policy_id and v.status is PolicyStatus.PUBLISHED
        ]
        if len(published) > 1:  # pragma: no cover - the use case prevents this
            # Two published versions of one policy is a contradiction the store
            # must never silently resolve by picking one.
            raise InvariantViolation(
                f"policy {policy_id!r} has {len(published)} published versions"
            )
        return published[0] if published else None

    def history(self, policy_id: str) -> tuple[PolicyVersion, ...]:
        rows = [v for (pid, _), v in self._versions.items() if pid == policy_id]
        rows.sort(key=lambda v: v.version)
        return tuple(rows)

    def all_published(self) -> tuple[PolicyVersion, ...]:
        published = [v for v in self._versions.values() if v.status is PolicyStatus.PUBLISHED]
        published.sort(key=lambda v: v.policy_id)
        return tuple(published)


__all__ = ["InMemoryPolicyVersionRepository"]
