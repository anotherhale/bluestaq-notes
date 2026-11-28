"""The store of governance policy versions, as a port (NEX-047).

Versioning is the whole point of this port, and it is what a plain "current
policy" table cannot express. Three questions have to be answerable:

* **what is in force right now** for a policy id — the published version the
  enforcement path evaluates;
* **what was in force at a past moment** — which version was published and not
  yet superseded, because an audit of a decision made last week is only possible
  if last week's policy is still readable;
* **what is the next version number** — assigned by the store, never by the
  caller, so two concurrent authors cannot both claim v4.

Published versions are never deleted or overwritten. ``publish`` transitions a
version forward; ``retire`` marks it superseded. The adapter's job is to keep
that history, not to decide it.

Why a port rather than a table in the use case
----------------------------------------------

The publish use case must not know whether versions live in a dict or in a table.
A demo adapter keeps them in a process; a durable one would keep them in rows,
and neither changes the use case. The same seam NEX-045 and NEX-046 used for
operator grants, for the same reason: swap the store, keep the rules.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from notes_service.domain.governance.policy import PolicyVersion


@runtime_checkable
class PolicyVersionRepository(Protocol):
    """Storage for governance policy versions, append-mostly and never destructive."""

    @property
    def name(self) -> str: ...

    def next_version_number(self, policy_id: str) -> int:
        """The version number a new draft for ``policy_id`` must take.

        One past the highest version ever stored for that id, including retired
        ones. Retired versions count, because reusing a version number would make
        two different policies share a reference and an audit line ambiguous.
        """
        ...

    def add_draft(self, version: PolicyVersion) -> None:
        """Store a draft. Refuses a non-draft, and refuses a duplicate version number.

        The store is the authority on what version numbers exist, so it enforces
        the uniqueness the use case relies on rather than trusting it.
        """
        ...

    def approve(self, version: PolicyVersion) -> None:
        """Replace the stored draft with its approved form (NEX-065).

        The stored draft for the same ``(policy_id, version)`` is replaced in
        place. Approving a version that was never added is a programming error,
        and only a draft can be approved: an approval records who decided, so a
        second approval must not overwrite the first.

        The store enforces the transition rather than trusting the caller for the
        same reason ``publish`` does — a row that skipped the approval step must
        not be able to look approved afterwards.
        """
        ...

    def publish(self, version: PolicyVersion) -> None:
        """Replace the stored approved version with its published form.

        The stored version for the same ``(policy_id, version)`` is replaced in
        place; nothing else is touched. Publishing a version that was never added
        is a programming error, and a published version cannot be published
        again.

        Only an approved version may be published (NEX-065). The store refuses a
        draft, so the approval record cannot be skipped by writing through this
        port directly.
        """
        ...

    def retire(self, version: PolicyVersion) -> None:
        """Replace the stored published version with its retired form."""
        ...

    def get(self, policy_id: str, version: int) -> PolicyVersion | None:
        """One version, whatever its status. ``None`` when it does not exist."""
        ...

    def published_for(self, policy_id: str) -> PolicyVersion | None:
        """The published (not retired) version in force for ``policy_id``.

        At most one version per id is ever ``PUBLISHED``; publishing a new one
        retires the previous. ``None`` means nothing is in force, which the
        evaluator treats as deny.
        """
        ...

    def history(self, policy_id: str) -> tuple[PolicyVersion, ...]:
        """Every stored version for ``policy_id``, oldest first.

        Includes drafts and retired versions: "what was in force" and "what was
        proposed" are both audit questions.
        """
        ...

    def all_published(self) -> tuple[PolicyVersion, ...]:
        """Every currently published version across all policy ids, in id order.

        This is what the enforcement path evaluates: the set of policies in force
        at this instant. Sorted by ``policy_id`` so the answer is deterministic
        and a decision's ``policy_versions`` list is stable.
        """
        ...


__all__ = ["PolicyVersionRepository"]
