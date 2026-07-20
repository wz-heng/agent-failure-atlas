"""Regression tests for the defense — supervise a job as a process GROUP.

    python3 -m pytest test_defense.py -q      # or: python3 test_defense.py

`supervise_teardown()` below is the whole defense: spawn into a new session,
and on teardown signal the group and reap it. It is the same shape Owlery uses
at its CLI spawn seam, reduced to stdlib and stripped of asyncio.

These tests exist because the failure is silent. A supervisor that leaks
descendants looks identical to one that doesn't until the NEXT run needs the
resource — by which time the evidence is a hang in unrelated code. The only
cheap way to keep the defense honest is to assert, every build, that nothing
outlives teardown.

Every test runs inside `swept()`, so a failing assertion cannot leave a
sleeping orphan behind. POSIX only; skipped elsewhere. Runs offline in ~2s.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from repro import (
    BLOCK_TIMEOUT,
    STARTUP_TIMEOUT,
    is_live_state,
    kill_and_reap_group,
    port_is_free,
    ps_field,
    read_published,
    start_job,
    sweep_active_pgids,
    wait_for_port_free,
)

POSIX = os.name == "posix"
SELF_DIR = Path(__file__).resolve().parent


@contextmanager
def swept():
    """A temp dir plus a guaranteed process-group sweep, however the body exits."""
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            yield tmpdir
        finally:
            sweep_active_pgids()


def supervise_teardown(leader: subprocess.Popen, *, group: bool) -> None:
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
        leader.kill()
        leader.wait()
        return
    kill_and_reap_group(pgid, leader)


# ------------------------------------------------------------------- the tests

def test_leader_only_kill_leaks_a_live_descendant():
    """The bug, pinned. If this stops holding, either the platform changed or
    the case stopped demonstrating anything."""
    if not POSIX:
        return
    with swept() as tmpdir:
        leader, leader_pid, port, holder_pid = start_job(tmpdir)
        supervise_teardown(leader, group=False)
        time.sleep(0.3)
        assert not port_is_free(port), "the descendant did not survive"
        assert is_live_state(ps_field(holder_pid, "stat")), "descendant not alive"
        assert ps_field(holder_pid, "ppid") != str(leader_pid), "not reparented"


def test_group_kill_leaves_nothing_holding_the_resource():
    """The defense. After teardown the port must be immediately reusable."""
    if not POSIX:
        return
    with swept() as tmpdir:
        leader, _leader_pid, port, holder_pid = start_job(tmpdir)
        supervise_teardown(leader, group=True)
        assert wait_for_port_free(port, 5.0), "the port was still held"
        assert not is_live_state(ps_field(holder_pid, "stat")), "descendant alive"


def test_the_next_run_starts_after_a_group_teardown():
    """The property that actually matters to a user: run, tear down, run again.
    A leaked descendant turns the second start into a deterministic failure."""
    if not POSIX:
        return
    with swept() as tmpdir:
        first, _pid, port, _holder = start_job(tmpdir)
        supervise_teardown(first, group=True)
        assert wait_for_port_free(port, 5.0)

        second, _pid2, port2, _holder2 = start_job(tmpdir)
        assert not port_is_free(port2), "the second run never came up"
        supervise_teardown(second, group=True)
        assert wait_for_port_free(port2, 5.0)


def test_teardown_is_idempotent_on_an_already_dead_job():
    """Teardown races with natural exit constantly. It must not raise."""
    if not POSIX:
        return
    with swept() as tmpdir:
        leader, _pid, port, _holder = start_job(tmpdir)
        supervise_teardown(leader, group=True)
        supervise_teardown(leader, group=True)   # again, on a corpse
        assert wait_for_port_free(port, 5.0)


def test_the_supervisor_reaps_and_leaves_no_zombie_of_its_own():
    """The second half of the fix. Signalling without reaping trades a leaked
    orphan for an accumulating zombie — better, but still a leak."""
    if not POSIX:
        return
    with swept() as tmpdir:
        leader, leader_pid, port, _holder = start_job(tmpdir)
        supervise_teardown(leader, group=True)
        assert wait_for_port_free(port, 5.0)
        state = ps_field(leader_pid, "stat")
        assert not state.startswith("Z"), f"leader left as a zombie: {state!r}"


def test_a_zombie_releases_its_resources_at_exit():
    """The taxonomy, MEASURED. An exited-but-unreaped child cannot be why
    anything is wedged — so it must not be hunted when a resource is stuck.

    The child binds a real port, then exits. While it sits in Z state, the very
    same port must be immediately rebindable. Asserting this rather than
    printing OS folklore is the difference between an oracle and a slogan.
    """
    if not POSIX:
        return
    with swept() as tmpdir:
        port_file = os.path.join(tmpdir, "zombie-port")
        proc = subprocess.Popen(
            [sys.executable, str(SELF_DIR / "repro.py"), "--role=exiter", port_file]
        )
        try:
            port, pid = read_published(port_file, None, STARTUP_TIMEOUT)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if ps_field(pid, "stat").startswith("Z"):
                    break
                time.sleep(0.02)
            assert ps_field(pid, "stat").startswith("Z"), "never became a zombie"
            assert ps_field(pid, "ppid") == str(os.getpid()), "not our child"
            # The measurement that matters:
            assert wait_for_port_free(port, 5.0), \
                "a zombie was still holding its port — the taxonomy claim fails"
        finally:
            proc.wait()
        assert not ps_field(pid, "stat"), "the zombie was not reaped"


def test_an_orphan_and_a_zombie_differ_on_the_thing_that_matters():
    """Side by side, on the only axis a debugger cares about: does it still own
    something? Orphan yes, zombie no."""
    if not POSIX:
        return
    with swept() as tmpdir:
        leader, leader_pid, orphan_port, holder_pid = start_job(tmpdir)
        supervise_teardown(leader, group=False)
        time.sleep(0.3)

        port_file = os.path.join(tmpdir, "z-port")
        proc = subprocess.Popen(
            [sys.executable, str(SELF_DIR / "repro.py"), "--role=exiter", port_file]
        )
        try:
            z_port, z_pid = read_published(port_file, None, STARTUP_TIMEOUT)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if ps_field(z_pid, "stat").startswith("Z"):
                    break
                time.sleep(0.02)

            orphan_holds = not port_is_free(orphan_port)
            zombie_holds = not wait_for_port_free(z_port, 5.0)
            assert orphan_holds is True, "the orphan released its port"
            assert zombie_holds is False, "the zombie held its port"
            assert is_live_state(ps_field(holder_pid, "stat"))
            assert ps_field(z_pid, "stat").startswith("Z")
        finally:
            proc.wait()


def test_the_repro_sweeps_its_own_process_groups_on_failure():
    """The script that demonstrates leaks must not leak. Start a job, abandon
    it without tearing it down, and confirm the sweep reclaims it — the group,
    the resource, AND the leader's process-table entry."""
    if not POSIX:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        _leader, leader_pid, port, holder_pid = start_job(tmpdir)
        assert not port_is_free(port), "setup failed"
        leaked = sweep_active_pgids()          # what `finally` would run
        assert leaked, "the sweep did not report the live group"
        assert wait_for_port_free(port, 5.0), "the sweep did not free the port"
        assert not is_live_state(ps_field(holder_pid, "stat"))
        # The leader must be GONE, not merely dead: a Z entry means the sweep
        # signalled without reaping.
        assert not ps_field(leader_pid, "stat"), "the leader was left unreaped"


def test_the_sweep_reaps_every_leader_over_many_cycles():
    """Repeated start -> sweep in ONE process, because that is what exposed the
    original bug and a single cycle does not.

    The first version of the sweep used `waitpid(-1, WNOHANG)` and stopped the
    moment it returned 0 — which is what it returns when a child exists but has
    not been collected yet, the normal state right after SIGKILL. Every leader
    was therefore left in Z. Nothing showed up in `ps` from outside only because
    the zombies were re-parented and reaped when the whole process exited, i.e.
    the leak was masked by exactly the "process exit will clean it up" habit
    this entry argues against. Looping inside one process removes that mask.
    """
    if not POSIX:
        return
    cycles = 30
    unreaped = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for _ in range(cycles):
            _leader, leader_pid, _port, _holder = start_job(tmpdir)
            sweep_active_pgids()
            if ps_field(leader_pid, "stat"):
                unreaped.append((leader_pid, ps_field(leader_pid, "stat")))
    assert not unreaped, (
        f"{len(unreaped)}/{cycles} leaders survived the sweep as process-table "
        f"entries: {unreaped[:5]}"
    )


def test_block_timeout_is_short_enough_to_stay_a_test():
    """A case that demonstrates blocking by actually blocking is a case nobody
    runs twice. Keep the verdict bounded."""
    assert BLOCK_TIMEOUT <= 5.0


if __name__ == "__main__":
    sys.path.insert(0, str(SELF_DIR))
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    sweep_active_pgids()
    print()
    print(f"{failed} failed" if failed else "all defense tests passed.")
    sys.exit(1 if failed else 0)
