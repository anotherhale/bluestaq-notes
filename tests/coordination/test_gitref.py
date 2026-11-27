"""Tests for the real git-backed adapter, not the in-memory fake.

``FakeGit`` models the *semantics* the lease and claim layers rely on, so those
tests stay fast and deterministic. It cannot catch a defect in how
:class:`SubprocessGit` invokes the ``git`` binary, because it never invokes it.

That gap was real: ``_push`` omitted the remote name, so git parsed the
``<sha>:<ref>`` refspec as a repository URL and tried to resolve the sha as a
hostname. Every lease acquire and every claim failed against a real remote while
the fake-backed suite stayed green. These tests pin the argument shape.
"""

from __future__ import annotations

import subprocess

import pytest

from nexus_coord.gitref import SubprocessGit


class RecordingGit(SubprocessGit):
    """Captures the argv instead of running git."""

    def __init__(self) -> None:
        super().__init__(remote="origin")
        self.calls: list[tuple[str, ...]] = []
        self.fail: tuple[str, str] | None = None

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        stdout = ""
        if self.fail is not None and self.fail[0] in args:
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr=self.fail[1])
        return subprocess.CompletedProcess(args, returncode=0, stdout=stdout, stderr="")


def test_push_create_names_the_remote_before_the_refspec() -> None:
    git = RecordingGit()
    git.push_create("refs/locks/nexus/lease", "a" * 40)

    assert git.calls == [("push", "--porcelain", "origin", f"{'a' * 40}:refs/locks/nexus/lease")]


def test_push_update_names_the_remote_and_keeps_force_with_lease() -> None:
    git = RecordingGit()
    git.push_update("refs/claims/nexus/NEX-1", "b" * 40, expected="c" * 40)

    (call,) = git.calls
    assert call[:3] == ("push", "--porcelain", "origin")
    assert f"--force-with-lease=refs/claims/nexus/NEX-1:{'c' * 40}" in call
    assert f"{'b' * 40}:refs/claims/nexus/NEX-1" in call
    assert "--force" not in call


def test_push_delete_names_the_remote() -> None:
    git = RecordingGit()
    git.push_delete("refs/locks/nexus/lease", expected="d" * 40)

    (call,) = git.calls
    assert call[:3] == ("push", "--porcelain", "origin")
    assert ":refs/locks/nexus/lease" in call


def test_stale_info_is_a_lost_race_not_a_remote_name_error() -> None:
    git = RecordingGit()
    git.fail = ("push", " ! [rejected] ... (stale info)")

    with pytest.raises(Exception) as exc:
        git.push_update("refs/claims/nexus/NEX-1", "b" * 40, expected="c" * 40)
    assert "stale info" in str(exc.value)


def test_unresolvable_hostname_is_reported_as_a_git_error() -> None:
    """The pre-fix symptom: a sha resolved as a hostname is a hard error."""
    git = RecordingGit()
    git.fail = ("push", "ssh: Could not resolve hostname deadbeef: nodename nor servname")

    with pytest.raises(Exception) as exc:
        git.push_create("refs/locks/nexus/lease", "deadbeef")
    assert "Could not resolve hostname" in str(exc.value)
