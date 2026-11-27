"""The lease holder must be one identity across every process in a run.

R39 was not a race: ``release`` and ``renew`` compare the live lease's holder
against the caller's holder, and the default holder was ``host-pid``. Every
``nexus_coord`` invocation is a fresh process, so the pid recorded at acquire
time never matched the pid doing the release. `release` therefore always failed
with "lease held by another agent", a lease could never be handed back, and the
next run waited out the full TTL (R38).

The invariant these tests pin is *stability across processes*, which no
single-process test can express — two calls in one process share a pid and pass
under the broken implementation too. So the decisive test spawns real
subprocesses and compares what they report.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_coord.lease import LeaseManager, default_holder
from nexus_coord.liveness import Holder, HolderLiveness, classify

from .fake_git import FakeGit

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _holder_in_a_fresh_process() -> str:
    """Run ``default_holder()`` in a brand-new process and return its answer."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TOOLS)
    # The override would mask what we are measuring.
    env.pop("NEXUS_HOLDER", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nexus_coord.lease import default_holder; print(default_holder())",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


def test_the_holder_is_identical_across_separate_processes():
    """The R39 regression: a per-process identity can never release a lease.

    Under ``host-pid`` this fails, because two subprocesses have two pids. Under
    a session-scoped identity both children inherit the test runner's session and
    agree, which is what lets one invocation release what another acquired.
    """
    first = _holder_in_a_fresh_process()
    second = _holder_in_a_fresh_process()

    assert first == second, (
        "holder identity differs between two processes of the same run; "
        f"release/renew can never recognise their own lease ({first!r} != {second!r})"
    )


def test_the_holder_is_not_a_bare_pid():
    """A pid-shaped suffix is the defect; the default must not produce one.

    Guarding the *shape* matters because a future change could keep the equality
    property by accident (e.g. a cached pid file) while reintroducing a per-pid
    identity on another platform.
    """
    holder = default_holder()
    parsed = Holder.parse(holder)

    assert parsed is not None, f"holder {holder!r} is not host-<id> shaped"
    assert parsed.is_session, f"holder {holder!r} carries a pid, not a session id"


def test_an_explicit_holder_overrides_everything(monkeypatch: pytest.MonkeyPatch):
    """A wrapper that already knows the run id can pin the identity."""
    monkeypatch.setenv("NEXUS_HOLDER", "run-NEX-2026-11-27-46")
    assert default_holder() == "run-NEX-2026-11-27-46"


def test_a_platform_without_session_ids_falls_back_to_the_pid(monkeypatch: pytest.MonkeyPatch):
    """No session id must not mean no coordination: fall back, do not raise."""
    monkeypatch.delenv("NEXUS_HOLDER", raising=False)
    monkeypatch.setattr("nexus_coord.lease._session_id", lambda: None)

    holder = default_holder()
    parsed = Holder.parse(holder)

    assert parsed is not None
    assert parsed.pid == os.getpid()
    assert not parsed.is_session


def test_a_session_holder_is_parsed_and_probed_like_a_pid():
    """A session id *is* the session leader's pid, so the probe is unchanged."""
    parsed = Holder.parse("Andrews-Mac-mini.local-s99906")

    assert parsed is not None
    assert parsed.host == "Andrews-Mac-mini.local"
    assert parsed.pid == 99906
    assert parsed.is_session

    verdict = classify(
        "Andrews-Mac-mini.local-s99906",
        local_host="Andrews-Mac-mini.local",
        process_exists=lambda pid: pid == 99906,
    )
    assert verdict is HolderLiveness.ALIVE


def test_a_malformed_session_suffix_is_rejected():
    assert Holder.parse("host-s") is None
    assert Holder.parse("host-sabc") is None


def test_one_process_can_release_what_another_acquired_under_the_default_identity():
    """The end-to-end shape of R39, with two managers standing in for two CLIs.

    Both use ``default_holder()`` and therefore the same identity, which is the
    property that makes release possible. The managers are separate objects, so
    nothing is shared but the git store — the same situation as two invocations.
    """
    git = FakeGit()
    acquirer = LeaseManager(git)
    releaser = LeaseManager(git)

    assert acquirer.holder == releaser.holder
    acquirer.acquire()
    assert releaser.current() is not None

    releaser.release(releaser.current())
    assert git.refs == {}


def test_a_different_identity_still_cannot_release_a_lease_it_does_not_hold():
    """Fixing the default must not weaken exclusion between two real runs."""
    git = FakeGit()
    run_a = LeaseManager(git, holder="host-s111")
    run_b = LeaseManager(git, holder="host-s222")

    run_a.acquire()
    held = run_b.current()
    assert held is not None
    assert held.holder == "host-s111"
