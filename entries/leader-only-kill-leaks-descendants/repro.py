#!/usr/bin/env python3
"""Minimal case: killing only the process-group leader leaks its descendants,
and the leaked grandchild deterministically blocks the next run.

Offline. No account, no network beyond a loopback listener, no API spend.
Finishes in a few seconds. Standard library only; imports nothing from Owlery.

    python3 repro.py          # exit 0 = every oracle held

WHAT THIS IS
------------
This is a LIVE REPRODUCTION of a MECHANISM, run for real on your machine: real
processes, real fork/exec, real signals, a real held port. It is NOT a replay of
the 2026-07-17 incident described in the entry. That incident's leaked processes
came from agent-session and dev-loop cleanup, not from this script. Same
mechanism, different origin — the entry says so explicitly.

WHAT IS AND IS NOT CLAIMED
--------------------------
Claimed: if a supervisor kills only the direct child, a grandchild holding an
exclusive resource survives, is reparented away, and makes the next run fail to
acquire that resource — deterministically, not flakily. Killing the process
GROUP and reaping it releases the resource.

NOT claimed: that every leak manifests as a bound port, or that a port is what
wedged the incident. A port is used here because it is an exclusive, observable,
cross-platform resource. The incident's contended resources were reported as
loopback and database resources; that report is an operator's recollection, not
a measurement, and the entry grades it accordingly.

PLATFORM
--------
POSIX only (macOS and Linux). It needs process groups (`setsid`/`killpg`) and
`ps`. It will refuse to run on Windows rather than pretend.
"""

from __future__ import annotations

import errno
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SELF = Path(__file__).resolve()

# How long the "next run" is willing to wait for the resource before declaring
# itself blocked. Short on purpose: the point is a machine verdict, not making
# you watch a hang.
BLOCK_TIMEOUT = 2.0
# How long to wait for a spawned holder to actually own the port.
STARTUP_TIMEOUT = 10.0


# --------------------------------------------------------------------------
# Roles — this file re-executes itself, so the whole case stays one file
# --------------------------------------------------------------------------

def role_holder(port_file: str) -> int:
    """The GRANDCHILD. Binds a loopback port, publishes it, then sleeps.

    Stands in for anything a real job leaves running that owns something
    exclusive: a test server, a DB handle, a lock file, an MCP subprocess.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Deliberately NOT setting SO_REUSEADDR/SO_REUSEPORT: we want a second
    # bind to fail while this socket is alive, on both macOS and Linux.
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    # Publish atomically so the parent never reads a half-written file.
    tmp = port_file + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(f"{port}\n{os.getpid()}\n")
    os.replace(tmp, port_file)
    time.sleep(3600)
    return 0


def role_leader(port_file: str) -> int:
    """The DIRECT CHILD — the process a naive supervisor knows about.

    Spawns the grandchild and then does nothing interesting. Killing this
    process alone is the bug the whole case is about.
    """
    subprocess.Popen([sys.executable, str(SELF), "--role=holder", port_file])
    time.sleep(3600)
    return 0


def role_exiter() -> int:
    """Exits immediately, so the parent can observe a genuine ZOMBIE."""
    return 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def port_is_free(port: int) -> bool:
    """True if we can bind the port right now."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            return False
        raise
    finally:
        sock.close()


def wait_for_port_free(port: int, timeout: float) -> bool:
    """The 'next run' trying to start. Returns False if it gave up — that
    return value IS the machine verdict for 'the next run is blocked'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_free(port):
            return True
        time.sleep(0.05)
    return False


def ps_field(pid: int, field: str) -> str:
    """One `ps` field for a pid, or "" if the process is gone.

    `-o <field>=` (empty header) is portable across macOS and procps.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def start_job(tmpdir: str) -> tuple[subprocess.Popen, int, int]:
    """Start leader -> grandchild, wait until the port is genuinely held.

    Returns (leader_popen, port, holder_pid).
    """
    port_file = os.path.join(tmpdir, f"port-{time.monotonic_ns()}")
    leader = subprocess.Popen(
        [sys.executable, str(SELF), "--role=leader", port_file],
        # The supervisor's half of the contract: give the job its own process
        # group, so the whole tree CAN be addressed later as a unit.
        start_new_session=True,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if os.path.exists(port_file):
            try:
                port_s, pid_s = Path(port_file).read_text().split()
                return leader, int(port_s), int(pid_s)
            except ValueError:
                pass  # writer raced us; retry
        if leader.poll() is not None:
            raise SystemExit("leader died before publishing a port")
        time.sleep(0.02)
    raise SystemExit("timed out waiting for the holder to bind a port")


def reap_group(pgid: int) -> None:
    """SIGKILL a process group and reap what we can, best effort."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            time.sleep(0.02)


# --------------------------------------------------------------------------
# Oracles
# --------------------------------------------------------------------------

def oracle_leader_only_kill_leaks_and_blocks(tmpdir: str) -> list[str]:
    """Kill only the direct child. The grandchild survives and blocks the next run."""
    failures = []
    print("  [1] leader-only kill: the descendant survives and blocks the next run")

    leader, port, holder_pid = start_job(tmpdir)
    pgid = os.getpgid(leader.pid)
    print(f"      leader pid={leader.pid} pgid={pgid}  holder pid={holder_pid} "
          f"port={port}")
    if port_is_free(port):
        failures.append("setup failed: the holder never actually held the port")
        return failures

    # THE BUG: signal the process the supervisor has a handle on, and only that.
    leader.kill()
    leader.wait()
    time.sleep(0.3)  # let the kernel reparent the orphan

    holder_state = ps_field(holder_pid, "stat")
    holder_ppid = ps_field(holder_pid, "ppid")
    print(f"      after killing the leader: holder stat={holder_state!r} "
          f"ppid={holder_ppid}")

    if not holder_state:
        failures.append(
            "the grandchild died with the leader — this platform does not "
            "reproduce the leak, so the rest of the oracle is meaningless"
        )
        return failures
    if holder_state.startswith("Z"):
        failures.append(
            f"the grandchild is a ZOMBIE ({holder_state}), not a live orphan; "
            "the entry's taxonomy claim would be wrong"
        )

    # The next run, with a bounded wait instead of a real hang.
    acquired = wait_for_port_free(port, BLOCK_TIMEOUT)
    print(f"      next run waiting {BLOCK_TIMEOUT}s for the port: "
          f"{'ACQUIRED' if acquired else 'BLOCKED (timed out)'}")
    if acquired:
        failures.append(
            "the port was released despite the leak — the blocking claim fails"
        )
    else:
        print("      -> deterministic, not flaky: the resource has an owner, and")
        print("         that owner is no longer anybody's child.")

    # Clean up after ourselves — the leak is the demo, not a parting gift.
    try:
        os.kill(holder_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if not wait_for_port_free(port, 5.0):
        failures.append("could not clean up the leaked holder")
    return failures


def oracle_group_kill_releases(tmpdir: str) -> list[str]:
    """Kill the process GROUP and reap. The resource comes back."""
    failures = []
    print("  [2] process-group kill + reap: the resource is released")

    leader, port, holder_pid = start_job(tmpdir)
    pgid = os.getpgid(leader.pid)
    print(f"      leader pid={leader.pid} pgid={pgid}  holder pid={holder_pid} "
          f"port={port}")
    if port_is_free(port):
        failures.append("setup failed: the holder never actually held the port")
        return failures

    # THE FIX: address the whole group, then reap it.
    reap_group(pgid)

    released = wait_for_port_free(port, 5.0)
    holder_state = ps_field(holder_pid, "stat") or "gone"
    port_state = "FREE" if released else "STILL HELD"
    print(f"      after killpg + reap: holder stat={holder_state!r} "
          f"port {port_state}")
    if not released:
        failures.append("the port was not released by the group kill")
    else:
        print("      -> the descendant died with the group it belonged to.")
    return failures


def oracle_orphan_is_not_a_zombie(tmpdir: str) -> list[str]:
    """The taxonomy, demonstrated rather than asserted.

    These are routinely conflated, and the confusion sends you looking in the
    wrong place: a zombie explains nothing about a wedged resource.
    """
    failures = []
    print("  [3] an orphan is not a zombie")

    # A live ORPHAN: parent gone, still running, still owns things.
    leader, port, holder_pid = start_job(tmpdir)
    leader.kill()
    leader.wait()
    time.sleep(0.3)
    orphan_stat = ps_field(holder_pid, "stat")
    orphan_ppid = ps_field(holder_pid, "ppid")
    orphan_holds = not port_is_free(port)

    # A true ZOMBIE: exited, not yet reaped. Deliberately NOT waited on.
    zombie = subprocess.Popen([sys.executable, str(SELF), "--role=exiter"])
    time.sleep(0.5)
    zombie_stat = ps_field(zombie.pid, "stat")
    zombie_ppid = ps_field(zombie.pid, "ppid")

    print(f"      orphan  pid={holder_pid:<7} stat={orphan_stat!r:<8} "
          f"ppid={orphan_ppid:<7} holds the port: {orphan_holds}")
    print(f"      zombie  pid={zombie.pid:<7} stat={zombie_stat!r:<8} "
          f"ppid={zombie_ppid:<7} holds anything: False (it has exited)")

    if orphan_stat.startswith("Z"):
        failures.append(f"the orphan reports as a zombie: {orphan_stat!r}")
    if not orphan_holds:
        failures.append("the orphan does not hold the port")
    if orphan_ppid == str(os.getpid()):
        failures.append("the orphan was not reparented away")
    if not zombie_stat.startswith("Z"):
        failures.append(
            f"the exited-but-unreaped child is not in Z state ({zombie_stat!r}); "
            "this platform reports process states differently"
        )
    if zombie_ppid != str(os.getpid()):
        failures.append("the zombie should still be OUR child until reaped")

    print("      -> the orphan is alive (S/R), reparented, and holds an exclusive")
    print("         resource. The zombie is dead (Z), still our child, and holds")
    print("         NOTHING — no port, no lock, no memory. Only the orphan can")
    print("         wedge the next run; chasing Z states is chasing the wrong bug.")

    # Clean up both.
    try:
        os.kill(holder_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    zombie.wait()
    if not wait_for_port_free(port, 5.0):
        failures.append("could not clean up the orphan")
    return failures


def main() -> int:
    if os.name != "posix":
        print("This case needs POSIX process groups and `ps`. "
              "Skipping on this platform.")
        return 0

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="leader-only-kill-") as tmpdir:
        failures = []
        for oracle in (
            oracle_leader_only_kill_leaks_and_blocks,
            oracle_group_kill_releases,
            oracle_orphan_is_not_a_zombie,
        ):
            failures += oracle(tmpdir)
            print()

    print(f"elapsed: {time.monotonic() - started:.1f}s")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all oracles held.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0].startswith("--role="):
        role = args[0].split("=", 1)[1]
        if role == "holder":
            sys.exit(role_holder(args[1]))
        if role == "leader":
            sys.exit(role_leader(args[1]))
        if role == "exiter":
            sys.exit(role_exiter())
        sys.exit(f"unknown role: {role}")
    sys.exit(main())
