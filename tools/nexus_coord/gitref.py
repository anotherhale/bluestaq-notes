"""The git transport behind the coordination primitives.

Every coordination decision reduces to one question: *did my compare-and-swap
win?* This module is the only place that answers it, and the only place that
runs git, so the coordination logic above it can be tested against an in-memory
store with the same atomicity guarantees.

Git gives three compare-and-swap operations, all enforced server-side:

- **create** — `git push <sha>:<ref>` is rejected as non-fast-forward when the
  ref already exists. First writer wins; every later writer is told so.
- **update** — `git push --force-with-lease=<ref>:<expected>` is applied only if
  the remote ref still equals `<expected>`. This is the safe way to take over an
  expired lease without ever clobbering a live one.
- **delete** — the same, with an empty source.

Nothing here uses `--force`. `--force-with-lease` is the opposite of a force
push: it *refuses* to write when the remote has moved.
"""

from __future__ import annotations

import subprocess
from typing import Protocol

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

LEASE_REF = "refs/locks/nexus/lease"
CLAIMS_PREFIX = "refs/claims/nexus/"


class GitError(RuntimeError):
    """A git invocation failed for a reason other than losing a race."""


class RefExistsError(GitError):
    """A create lost the race: the ref is already there."""


class RefMovedError(GitError):
    """An update or delete lost the race: the remote ref is not what we saw."""


class GitRemote(Protocol):
    """Atomic ref operations, plus the reads the coordination layer needs."""

    def ls_remote(self, pattern: str) -> dict[str, str]:
        """Return ``{ref: sha}`` for remote refs matching ``pattern``."""

    def fetch(self, refspec: str) -> None:
        """Fetch remote refs locally so their commit messages can be read."""

    def read_message(self, local_ref: str) -> str | None:
        """Return the message of the commit a local ref points at."""

    def commit_with_message(self, message: str) -> str:
        """Create an unattached commit carrying ``message``; return its sha."""

    def push_create(self, ref: str, sha: str) -> None:
        """Atomically create ``ref``. Raises ``RefExistsError`` if present."""

    def push_update(self, ref: str, sha: str, expected: str) -> None:
        """Atomically move ``ref`` from ``expected`` to ``sha``."""

    def push_delete(self, ref: str, expected: str) -> None:
        """Atomically delete ``ref``, only if it still equals ``expected``."""

    def head_sha(self) -> str:
        """The current local HEAD, used as the object to push."""


class SubprocessGit:
    """A :class:`GitRemote` backed by the ``git`` binary and an ``origin`` remote."""

    def __init__(self, cwd: str | None = None, remote: str = "origin") -> None:
        self._cwd = cwd
        self._remote = remote

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("git", *args),
            cwd=self._cwd,
            capture_output=True,
            text=True,
            check=check,
        )

    def ls_remote(self, pattern: str) -> dict[str, str]:
        result = self._run("ls-remote", "--refs", self._remote, pattern)
        refs: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            sha, _, ref = line.partition("\t")
            refs[ref] = sha
        return refs

    def fetch(self, refspec: str) -> None:
        # A fetch that finds nothing is not an error; the caller treats an
        # absent local ref as "no such claim".
        self._run("fetch", "--quiet", self._remote, refspec, check=False)

    def read_message(self, local_ref: str) -> str | None:
        result = self._run("log", "-1", "--format=%B", local_ref, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def commit_with_message(self, message: str) -> str:
        result = self._run("commit-tree", EMPTY_TREE, "-m", message)
        return result.stdout.strip()

    def _push(self, *args: str) -> None:
        # The remote must be named explicitly. Without it git treats the first
        # positional argument (`<sha>:<ref>`) as a repository URL and tries to
        # resolve the sha as a hostname.
        result = self._run("push", "--porcelain", self._remote, *args, check=False)
        if result.returncode == 0:
            return
        output = f"{result.stdout}\n{result.stderr}"
        # Distinguishing "you lost a race" from "git is broken" matters: the
        # first is a normal, expected outcome; the second must stop the run.
        if "stale info" in output:
            raise RefMovedError(output.strip())
        if "[rejected]" in output or "non-fast-forward" in output:
            raise RefExistsError(output.strip())
        raise GitError(output.strip())

    def push_create(self, ref: str, sha: str) -> None:
        self._push(f"{sha}:{ref}")

    def push_update(self, ref: str, sha: str, expected: str) -> None:
        self._push(f"--force-with-lease={ref}:{expected}", f"{sha}:{ref}")

    def push_delete(self, ref: str, expected: str) -> None:
        self._push(f"--force-with-lease={ref}:{expected}", f":{ref}")

    def head_sha(self) -> str:
        return self._run("rev-parse", "HEAD").stdout.strip()
