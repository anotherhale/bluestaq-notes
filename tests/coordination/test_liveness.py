"""Tests for holder-liveness classification.

The security-relevant direction is a false ``DEAD``: it lets a second writer in
while the first is still writing. So most of these assert that an ambiguous case
resolves to ``UNKNOWN`` or ``ALIVE``, never ``DEAD``.
"""

from __future__ import annotations

from nexus_coord.liveness import Holder, HolderLiveness, classify

HOST = "worker-7"


def test_holder_parse_splits_host_and_pid_on_the_last_dash():
    parsed = Holder.parse("Andrews-Mac-mini.local-56084")

    assert parsed is not None
    assert parsed.host == "Andrews-Mac-mini.local"
    assert parsed.pid == 56084


def test_holder_parse_rejects_a_string_without_a_numeric_suffix():
    assert Holder.parse("no-pid-here") is None
    assert Holder.parse("") is None
    assert Holder.parse("host-") is None
    assert Holder.parse("host-abc") is None


def test_a_dead_pid_on_this_host_is_dead():
    verdict = classify(f"{HOST}-4242", local_host=HOST, process_exists=lambda pid: False)
    assert verdict is HolderLiveness.DEAD


def test_a_live_pid_on_this_host_is_alive():
    verdict = classify(f"{HOST}-4242", local_host=HOST, process_exists=lambda pid: True)
    assert verdict is HolderLiveness.ALIVE


def test_a_holder_on_another_host_is_unknown_never_dead():
    """We cannot see a foreign host's process table, so we must not guess."""
    verdict = classify("other-host-4242", local_host=HOST, process_exists=lambda pid: False)
    assert verdict is HolderLiveness.UNKNOWN


def test_an_unparseable_holder_is_unknown():
    verdict = classify("garbage", local_host=HOST, process_exists=lambda pid: False)
    assert verdict is HolderLiveness.UNKNOWN


def test_a_failed_probe_is_unknown_not_dead():
    """A probe that raises must never be read as proof of death."""
    verdict = classify(f"{HOST}-4242", local_host=HOST, process_exists=lambda pid: None)
    assert verdict is HolderLiveness.UNKNOWN


def test_pid_one_is_never_dead():
    """init answers a signal-0 probe as live; declaring it dead would be wrong."""
    verdict = classify(f"{HOST}-1", local_host=HOST, process_exists=lambda pid: False)
    assert verdict is HolderLiveness.ALIVE


def test_pid_zero_is_never_dead():
    verdict = classify(f"{HOST}-0", local_host=HOST, process_exists=lambda pid: False)
    assert verdict is HolderLiveness.ALIVE


def test_the_probe_is_not_consulted_for_a_foreign_host():
    """A foreign holder must short-circuit to UNKNOWN without touching the OS."""
    called = False

    def probe(pid: int) -> bool:
        nonlocal called
        called = True
        return False

    classify("other-host-4242", local_host=HOST, process_exists=probe)
    assert called is False


def test_real_probe_reports_the_current_process_as_alive():
    """Against the real OS: our own pid exists, so classify must say ALIVE."""
    import os

    verdict = classify(f"{HOST}-{os.getpid()}", local_host=HOST)
    assert verdict is HolderLiveness.ALIVE


def test_real_probe_reports_a_vanished_pid_as_dead():
    """Against the real OS: a pid we never spawned does not exist."""
    # 2**22 is above the default pid_max on macOS and Linux; no such process.
    verdict = classify(f"{HOST}-{2**22}", local_host=HOST)
    assert verdict is HolderLiveness.DEAD
