"""Publish a governance policy version: draft, validate, publish, retire (NEX-047).

The security architecture's policy lifecycle is
``Draft → Validate/Test → Impact Preview → Approve → Publish → Monitor``, with the
hard rule that **published releases are immutable** and a rollback is a new
release. This use case implements the part of that lifecycle the domain cannot
enforce on its own, because it needs a store and a clock:

* **drafting** assigns the next version number from the store, never from the
  caller, so two authors cannot both claim v4;
* **publishing** stamps the clock and the publisher and retires whatever was
  published before, so exactly one version per policy id is ever in force;
* **rollback** is modelled as *re-drafting an old version's rules as a new
  version* — there is deliberately no "un-publish" path, because un-publishing
  would delete history, and history is what an audit of a past decision needs.

Approve is not here yet, and that is honest
-------------------------------------------

The full lifecycle includes an approval step. This slice ships draft → publish →
retire because those are the transitions the *store* must support, and approval
is a governance decision that depends on M3.3 (separation of duties), which is not
built. Rather than fake an approval gate that always passes — which would be a
policy surface that looks governed and is not — this use case records the
publisher and leaves the approval requirement to the slice that can enforce it.
That is a deliberate, stated gap, not an oversight: see the BACKLOG entry's
"Residual" section.

Why a use case and not a repository method
------------------------------------------

Publication has rules that span calls: the new version must outrank the current
one, the previous version must be retired in the same operation, and the whole
thing must be idempotent enough that a retried publish does not produce two
published versions. Those are application concerns. The repository stores; this
decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import Conflict, ResourceNotFound
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.policy_versions import PolicyVersionRepository
from notes_service.domain.errors import ValidationError
from notes_service.domain.governance.policy import (
    PolicyRule,
    PolicyStatus,
    PolicyVersion,
    new_policy_version,
)


@dataclass(frozen=True, slots=True)
class DraftPolicyVersion:
    """A request to draft the next version of a policy."""

    policy_id: str
    rules: tuple[PolicyRule, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class PublishPolicyVersion:
    """A request to publish a specific draft."""

    policy_id: str
    version: int
    published_by: str


@dataclass(frozen=True, slots=True)
class RollbackPolicyVersion:
    """A request to restore an earlier version's rules as a *new* version.

    Rollback is not a mutation of history: it reads the named version's rules and
    drafts them under the next version number, so the fact that a rollback
    happened is itself recorded.
    """

    policy_id: str
    to_version: int
    published_by: str
    rationale: str


class DraftPolicy:
    """Create a draft version of a policy, or of a new policy."""

    def __init__(self, *, store: PolicyVersionRepository, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def execute(self, request: DraftPolicyVersion) -> PolicyVersion:
        if not request.rules:
            # A policy with no rules can only ever deny, which is a valid but
            # almost always unintended shape. Refusing here turns a silent
            # deny-everything into a loud request error.
            raise ValidationError("a policy version must have at least one rule")
        version_number = self._store.next_version_number(request.policy_id)
        draft = new_policy_version(
            policy_id=request.policy_id,
            version=version_number,
            rules=request.rules,
            rationale=request.rationale,
            created_at=self._clock.now(),
        )
        self._store.add_draft(draft)
        return draft


class PublishDraft:
    """Publish a stored draft, retiring whatever was in force before it."""

    def __init__(self, *, store: PolicyVersionRepository, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def execute(self, request: PublishPolicyVersion) -> PolicyVersion:
        draft = self._store.get(request.policy_id, request.version)
        if draft is None:
            raise ResourceNotFound(
                f"policy version {request.policy_id}@v{request.version} not found"
            )
        if draft.status is not PolicyStatus.DRAFT:
            # Publishing a published or retired version would rewrite history.
            # It is a conflict with the version's state, not a missing resource.
            raise Conflict(
                f"policy version {draft.ref} is {draft.status.value} and cannot be published"
            )
        current = self._store.published_for(request.policy_id)
        published = draft.publish(now=self._clock.now(), published_by=request.published_by)
        # Retire the outgoing version *before* publishing the new one. If the
        # process dies between the two writes, the store is briefly left with the
        # old version retired and nothing published — deny, not a wrong allow —
        # and a retry completes. The reverse order would leave two published
        # versions, which the store refuses.
        if current is not None:
            self._store.retire(current.retire())
        self._store.publish(published)
        return published


class RollbackPolicy:
    """Restore an earlier version's rules as a new, higher version number."""

    def __init__(self, *, store: PolicyVersionRepository, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def execute(self, request: RollbackPolicyVersion) -> PolicyVersion:
        source = self._store.get(request.policy_id, request.to_version)
        if source is None:
            raise ResourceNotFound(
                f"policy version {request.policy_id}@v{request.to_version} not found"
            )
        if source.status is PolicyStatus.DRAFT:
            # Rolling back to a draft would publish something nobody approved.
            raise Conflict(
                f"cannot roll back to {source.ref}: it is a draft, not a released version"
            )
        version_number = self._store.next_version_number(request.policy_id)
        draft = new_policy_version(
            policy_id=request.policy_id,
            version=version_number,
            rules=source.rules,
            rationale=request.rationale,
            created_at=self._clock.now(),
        )
        self._store.add_draft(draft)
        published = draft.publish(now=self._clock.now(), published_by=request.published_by)
        current = self._store.published_for(request.policy_id)
        if current is not None:
            self._store.retire(current.retire())
        self._store.publish(published)
        return published


@dataclass(frozen=True, slots=True)
class PolicyHistory:
    """The full version history of one policy, oldest first."""

    policy_id: str
    versions: tuple[PolicyVersion, ...]

    @property
    def published(self) -> PolicyVersion | None:
        matches = [v for v in self.versions if v.status is PolicyStatus.PUBLISHED]
        return matches[0] if matches else None


class GetPolicyHistory:
    """Read a policy's version history, including drafts and retired versions."""

    def __init__(self, *, store: PolicyVersionRepository) -> None:
        self._store = store

    def execute(self, policy_id: str) -> PolicyHistory:
        versions = self._store.history(policy_id)
        if not versions:
            raise ResourceNotFound(f"policy {policy_id!r} has no versions")
        return PolicyHistory(policy_id=policy_id, versions=versions)


__all__ = [
    "DraftPolicy",
    "DraftPolicyVersion",
    "GetPolicyHistory",
    "PolicyHistory",
    "PublishDraft",
    "PublishPolicyVersion",
    "RollbackPolicy",
    "RollbackPolicyVersion",
]
