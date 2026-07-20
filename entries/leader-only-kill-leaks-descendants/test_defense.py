"""Regression tests for the defense — supervise a job as a process GROUP.

    python3 -m pytest test_defense.py -q      # or: python3 test_defense.py

`supervise()` below is the whole defense: spawn into a new session, and on
teardown signal the group and reap it. It is the same shape Owlery uses at its
CLI spawn seam (`start_new_session=True` + `_terminate_process_group` +
`await proc.wait()`), reduced to stdlib and stripped of asyncio.

These tests exist because the failure is silent. A supervisor that leaks
descendants looks identical to one that doesn't until the NEXT run needs the
resource — by which time the evidence is a hang in unrelated code. The only
cheap way to keep the defense honest is to assert, every build, that nothing
outlives teardown.

POSIX only. Runs offline in a few seconds.
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
from pathlib import Path

from repro import (
    BLOCK_TIMEOUT,
    ps_field,
    port_is_free,
    start_job,
    wait_for_port_free,
)

POSIX_ONLY = os.name == "posix"


# --------------------------------------------------------------------- defense

def supervise_teardown(leader, *, group: bool) -> None:
    """Tear a job down. `group=False` is the bug; `group=True` is the fix.

    The fix has two halves and BOTH matter:
      1. signal the process GROUP, so descendants are addressed at all;
      2. reap, so the supervisor doesn't accumulate zombies of its own.
    """
    if not group:
        leader.kill()
        leader.wait()
        return

    try:
        pgid = os.getpgid(leader.pid)
    except (ProcessLookupError, PermissionError):
        # Already gone; fall back to the direct child like Owlery's helper does.
        leader.kill()
        leader.wait()
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    leader.wait()  # reap the leader; the group is dead


# ------------------------------------------------------------------- the tests

def test_leader_only_kill_leaks_a_live_descendant():
    """The bug, pinned. If this ever stops failing to release the port, either
    the platform changed or the case stopped demonstrating anything."""
    if not POSIX_ONLY:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        leader, port, holder_pid = start_job(tmpdir)
        supervise_teardown(leader, group=False)
        time.sleep(0.3)
        try:
            assert not port_is_free(port), "the descendant did not survive"
            assert ps_field(holder_pid, "stat"), "the descendant is gone"
        finally:
            try:
                os.kill(holder_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            wait_for_port_free(port, 5.0)


def test_group_kill_leaves_nothing_holding_the_resource():
    """The defense. After teardown the port must be immediately reusable."""
    if not POSIX_ONLY:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        leader, port, holder_pid = start_job(tmpdir)
        supervise_teardown(leader, group=True)
        assert wait_for_port_free(port, 5.0), "the port was still held"
        assert not ps_field(holder_pid, "stat"), "the descendant is still alive"


def test_the_next_run_starts_after_a_group_teardown():
    """The property that actually matters to a user: run, tear down, run again.
    A leaked descendant turns the second start into a deterministic failure."""
    if not POSIX_ONLY:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        first, port, _ = start_job(tmpdir)
        supervise_teardown(first, group=True)
        assert wait_for_port_free(port, 5.0)

        second, port2, holder2 = start_job(tmpdir)
        try:
            assert not port_is_free(port2), "the second run never came up"
        finally:
            supervise_teardown(second, group=True)
            assert wait_for_port_free(port2, 5.0)


def test_teardown_is_idempotent_on_an_already_dead_job():
    """Teardown races with natural exit constantly. It must not raise."""
    if not POSIX_ONLY:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        leader, port, holder_pid = start_job(tmpdir)
        supervise_teardown(leader, group=True)
        supervise_teardown(leader, group=True)   # again, on a corpse
        assert wait_for_port_free(port, 5.0)


def test_the_supervisor_reaps_and_leaves_no_zombie_of_its_own():
    """The second half of the fix. Signalling without reaping trades a leaked
    orphan for an accumulating zombie — better, but still a leak."""
    if not POSIX_ONLY:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        leader, port, _ = start_job(tmpdir)
        pid = leader.pid
        supervise_teardown(leader, group=True)
        assert wait_for_port_free(port, 5.0)
        state = ps_field(pid, "stat")
        assert not state.startswith("Z"), f"leader left as a zombie: {state!r}"


def test_a_zombie_holds_no_resource():
    """The taxonomy, as a property rather than a demo: an exited-but-unreaped
    child cannot be why anything is wedged. Documented here so nobody 'fixes'
    a resource contention bug by hunting Z states."""
    if not POSIX_ONLY:
        return
    import subprocess
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "repro.py"),
                             "--role=exiter"])
    time.sleep(0.5)
    try:
        assert ps_field(proc.pid, "stat").startswith("Z")
        # It has exited: every fd it held is closed by the kernel already.
        assert ps_field(proc.pid, "ppid") == str(os.getpid())
    finally:
        proc.wait()
    assert not ps_field(proc.pid, "stat"), "the zombie was not reaped"


def test_block_timeout_is_short_enough_to_stay_a_test():
    """A case that demonstrates blocking by actually blocking is a case nobody
    runs twice. Keep the verdict bounded."""
    assert BLOCK_TIMEOUT <= 5.0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    print()
    print(f"{failed} failed" if failed else "all defense tests passed.")
    sys.exit(1 if failed else 0)
