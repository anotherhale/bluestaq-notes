"""An in-memory git remote with real compare-and-swap semantics.

The coordination primitives are only worth anything if their atomicity actually
holds, and that cannot be shown by mocking the calls away. This fake implements
the same three server-side guarantees as git:

- ``push_create`` fails if the ref exists (first writer wins).
- ``push_update`` / ``push_delete`` fail unless the ref still equals the
  observed value (``--force-with-lease``).

Two managers sharing one instance therefore model two machines sharing one
origin, and a test can assert that exactly one of them wins.
"""

from __future__ import annotations

import fnmatch

from nexus_coord.gitref import RefExistsError, RefMovedError


class FakeGit:
    def __init__(self) -> None:
        self.refs: dict[str, str] = {}
        self.objects: dict[str, str] = {}
        self.local_refs: dict[str, str] = {}
        self.head = "head-sha"
        self._counter = 0
        # Hooks that let a test interleave a competitor between two steps.
        self.before_create = None
        self.before_delete = None
        self.before_update = None

    def _new_sha(self) -> str:
        self._counter += 1
        return f"sha{self._counter:04d}"

    def ls_remote(self, pattern: str) -> dict[str, str]:
        return {ref: sha for ref, sha in self.refs.items() if fnmatch.fnmatchcase(ref, pattern)}

    def fetch(self, refspec: str) -> None:
        src, _, dst = refspec.partition(":")
        if src in self.refs:
            self.local_refs[dst or src] = self.refs[src]

    def read_message(self, local_ref: str) -> str | None:
        sha = self.local_refs.get(local_ref)
        if sha is None:
            return None
        return self.objects.get(sha)

    def commit_with_message(self, message: str) -> str:
        sha = self._new_sha()
        self.objects[sha] = message
        return sha

    def push_create(self, ref: str, sha: str) -> None:
        if self.before_create is not None:
            hook, self.before_create = self.before_create, None
            hook()
        if ref in self.refs:
            raise RefExistsError(f"{ref} already exists")
        self.refs[ref] = sha

    def push_update(self, ref: str, sha: str, expected: str) -> None:
        if self.before_update is not None:
            hook, self.before_update = self.before_update, None
            hook()
        if self.refs.get(ref) != expected:
            raise RefMovedError(f"{ref} moved")
        self.refs[ref] = sha

    def push_delete(self, ref: str, expected: str) -> None:
        if self.before_delete is not None:
            hook, self.before_delete = self.before_delete, None
            hook()
        if self.refs.get(ref) != expected:
            raise RefMovedError(f"{ref} moved")
        del self.refs[ref]

    def head_sha(self) -> str:
        return self.head
