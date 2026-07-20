#!/usr/bin/env python3
"""Minimal case: killing only the process-group leader leaks descendants, and
the leaked grandchild deterministically blocks the next run.

Offline. No account, no network beyond a loopback listener, no API spend.
Finishes in a few seconds. Standard library only; imports nothing from Owlery.

    python3 repro.py          # exit 0 = every oracle held
                              # exit 1 = an oracle failed
                              # exit 3 = unsupported platform (NOT a pass)

WHAT THIS IS
------------
A LIVE REPRODUCTION of a MECHANISM, run for real on your machine: real
fork/exec, real process groups, real signals, a real held port, real `ps`
output. It is NOT a replay of the 2026-07-17 incident described in the entry.
That incident's leaked processes came from agent-session and dev-loop cleanup
paths, not from this script. Same mechanism, different origin.

WHAT IS AND IS NOT CLAIMED
--------------------------
Claimed: if a supervisor kills only the direct child, a grandchild holding an
exclusive resource survives, is reparented away from its original parent, and
makes the next run fail to acquire that resource — deterministically. Killing
the process GROUP and reaping releases it.

NOT claimed: that every leak manifests as a bound port, or that a port is what
wedged the incident. A port is used because it is exclusive, observable, and
behaves the same on macOS and Linux.

ON THIS SCRIPT'S OWN HYGIENE
----------------------------
An entry about leaked processes has no business leaking processes. Every
process group this script starts is registered the moment it exists, and a
`finally` sweep kills and reaps all of them — on success, on oracle failure, on
an unexpected exception, and on Ctrl-C. The first version of this file did NOT
do that: its error paths returned early past their own cleanup, so a crashed
run would hand the reader a sleeping orphan holding a port. That is precisely
the bug this entry documents, which is a good argument for the sweep and a
poor argument for the author.
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

# How long the "next run" waits for the resource before declaring itself
# blocked. Short on purpose: the point is a machine verdict, not a hang.
BLOCK_TIMEOUT = 2.0
STARTUP_TIMEOUT = 10.0
EXIT_UNSUPPORTED = 3


class ReproError(RuntimeError):
    """Setup failed. Raised, never `sys.exit`ed, so the sweep still runs."""


# --------------------------------------------------------------------------
# Process-group registry — this script's own defense against this script
# --------------------------------------------------------------------------

# pgid -> the leader Popen that leads it. The Popen is kept, not just the pid,
# because reaping has to be done PER CHILD and definitively. An earlier version
# registered pgids only and reaped with `waitpid(-1, WNOHANG)` in a loop that
# broke as soon as it returned 0 — which is exactly what it returns when a child
# exists but has not been collected yet. Right after SIGKILL that is the common
# case, so the leader was left in Z on 30 out of 30 runs. It looked clean from
# outside only because those zombies were re-parented and reaped when the main
# process exited: the very "let process exit clean it up" habit this entry
# criticises.
_ACTIVE_JOBS: dict[int, subprocess.Popen] = {}


def _register_job(pgid: int, proc: subprocess.Popen) -> None:
    _ACTIVE_JOBS[pgid] = proc


def group_is_alive(pgid: int) -> bool:
    """Does this process group still have members? Signal 0 probes without
    delivering anything."""
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def reap_leader(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    """Collect a leader definitively. `Popen.wait` blocks until the child is
    actually reaped and only ever waits on ITS OWN pid, so it cannot swallow
    another child's exit status the way `waitpid(-1, ...)` can."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def sweep_active_pgids() -> list[int]:
    """SIGKILL every process group we started, then REALLY reap each leader.

    Returns the pgids that still had live members — i.e. what would have leaked.
    Grandchildren are not our children, so we cannot `wait` on them; killing the
    group is what ends them, and their reaping belongs to whoever adopted them.
    The leaders ARE ours, and this collects every one.
    """
    leaked = []
    for pgid, proc in sorted(_ACTIVE_JOBS.items()):
        if group_is_alive(pgid):
            leaked.append(pgid)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        reap_leader(proc)
    _ACTIVE_JOBS.clear()
    return leaked


# --------------------------------------------------------------------------
# Roles — this file re-executes itself, so the whole case stays one file
# --------------------------------------------------------------------------

def _publish(port_file: str, port: int) -> None:
    tmp = f"{port_file}.tmp"
    with open(tmp, "w") as fh:
        fh.write(f"{port}\n{os.getpid()}\n")
    os.replace(tmp, port_file)


def _bind_loopback() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Deliberately NOT setting SO_REUSEADDR/SO_REUSEPORT: a second bind must
    # fail while this socket is alive, on both macOS and Linux.
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def role_holder(port_file: str) -> int:
    """The GRANDCHILD. Binds a loopback port, publishes it, then sleeps.

    Stands in for anything a real job leaves running that owns something
    exclusive: a test server, a DB handle, a lock file, an MCP subprocess.
    """
    _sock, port = _bind_loopback()
    _publish(port_file, port)
    time.sleep(3600)
    return 0


def role_leader(port_file: str) -> int:
    """The DIRECT CHILD — the process a naive supervisor knows about."""
    subprocess.Popen([sys.executable, str(SELF), "--role=holder", port_file])
    time.sleep(3600)
    return 0


def role_exiter(port_file: str) -> int:
    """Binds a port, publishes it, then EXITS — so the parent can observe a
    genuine zombie that demonstrably released what it held.

    The port is not closed explicitly. The kernel closes it at exit, which is
    the whole point: exiting frees resources, being unreaped does not hold them.
    """
    _sock, port = _bind_loopback()
    _publish(port_file, port)
    return 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def port_is_free(port: int) -> bool:
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
    """The 'next run' trying to start. False means it gave up — that return
    value IS the machine verdict for 'the next run is blocked'."""
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


def is_live_state(stat: str) -> bool:
    """A process that is alive and can still hold things. Excludes Z (exited,
    unreaped) and empty (gone). macOS and procps agree on the leading letter;
    suffixes (`Ss`, `S+`, `S<`) differ, so only the prefix is read."""
    return bool(stat) and not stat.startswith("Z")


def read_published(port_file: str, proc: subprocess.Popen | None,
                   timeout: float) -> tuple[int, int]:
    """Wait for a role to publish (port, pid)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(port_file):
            try:
                port_s, pid_s = Path(port_file).read_text().split()
                return int(port_s), int(pid_s)
            except ValueError:
                pass  # writer raced us; retry
        if proc is not None and proc.poll() is not None and not os.path.exists(port_file):
            raise ReproError("the process died before publishing a port")
        time.sleep(0.02)
    raise ReproError("timed out waiting for a port to be published")


def start_job(tmpdir: str) -> tuple[subprocess.Popen, int, int, int]:
    """Start leader -> grandchild, wait until the port is genuinely held.

    Returns (leader_popen, leader_pid, port, holder_pid). The process group is
    registered BEFORE anything can fail, so a timeout here still gets swept.
    """
    port_file = os.path.join(tmpdir, f"port-{time.monotonic_ns()}")
    leader = subprocess.Popen(
        [sys.executable, str(SELF), "--role=leader", port_file],
        # The supervisor's half of the contract: give the job its own process
        # group, so the whole tree CAN be addressed later as a unit.
        start_new_session=True,
    )
    # start_new_session makes the child its own group leader, so pgid == pid.
    # Register immediately; getpgid may already fail if it died instantly.
    try:
        pgid = os.getpgid(leader.pid)
    except (ProcessLookupError, PermissionError):
        pgid = leader.pid
    _register_job(pgid, leader)

    port, holder_pid = read_published(port_file, leader, STARTUP_TIMEOUT)
    return leader, leader.pid, port, holder_pid


def kill_and_reap_group(pgid: int, proc: subprocess.Popen | None = None) -> None:
    """SIGKILL a group and collect its leader. Pass the leader's Popen so the
    reap is real — see the note on `_ACTIVE_JOBS` for why a bare
    `waitpid(-1, WNOHANG)` loop is not a reap."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if proc is None:
        proc = _ACTIVE_JOBS.get(pgid)
    if proc is not None:
        reap_leader(proc)


# --------------------------------------------------------------------------
# Oracles
# --------------------------------------------------------------------------

def oracle_leader_only_kill_leaks_and_blocks(tmpdir: str) -> list[str]:
    """Kill only the direct child. The grandchild survives and blocks the next run."""
    failures = []
    print("  [1] leader-only kill: the descendant survives and blocks the next run")

    leader, leader_pid, port, holder_pid = start_job(tmpdir)
    pgid = os.getpgid(leader_pid) if is_live_state(ps_field(leader_pid, "stat")) else leader_pid
    print(f"      leader pid={leader_pid} pgid={pgid}  holder pid={holder_pid} "
          f"port={port}")
    if port_is_free(port):
        return ["setup failed: the holder never actually held the port"]

    # THE BUG: signal the process the supervisor has a handle on, and only that.
    leader.kill()
    leader.wait()
    time.sleep(0.3)  # let the kernel reparent the orphan

    holder_stat = ps_field(holder_pid, "stat")
    holder_ppid = ps_field(holder_pid, "ppid")
    print(f"      after killing the leader: holder stat={holder_stat!r} "
          f"ppid={holder_ppid} (was {leader_pid})")

    # Asserted, not printed.
    if not is_live_state(holder_stat):
        failures.append(
            f"the grandchild is not alive after the leader died "
            f"(stat={holder_stat!r}) — this platform does not reproduce the leak"
        )
    # Reparenting: away from its ORIGINAL parent, the leader. Deliberately not
    # asserting ppid == 1: Linux subreapers and PID namespaces can adopt
    # orphans instead of init.
    if holder_ppid == str(leader_pid):
        failures.append("the grandchild was not reparented away from the leader")

    acquired = wait_for_port_free(port, BLOCK_TIMEOUT)
    verdict = "ACQUIRED" if acquired else "BLOCKED (timed out)"
    print(f"      next run waiting {BLOCK_TIMEOUT}s for the port: {verdict}")
    if acquired:
        failures.append(
            "the port was released despite the leak — the blocking claim fails"
        )
    else:
        print("      -> deterministic, not flaky: the resource has an owner, and")
        print("         that owner is no longer anybody's child.")

    kill_and_reap_group(pgid, leader)
    if not wait_for_port_free(port, 5.0):
        failures.append("could not clean up the leaked holder")
    return failures


def oracle_group_kill_releases(tmpdir: str) -> list[str]:
    """Kill the process GROUP and reap. The resource comes back."""
    failures = []
    print("  [2] process-group kill + reap: the resource is released")

    leader, leader_pid, port, holder_pid = start_job(tmpdir)
    pgid = os.getpgid(leader_pid)
    print(f"      leader pid={leader_pid} pgid={pgid}  holder pid={holder_pid} "
          f"port={port}")
    if port_is_free(port):
        return ["setup failed: the holder never actually held the port"]

    # THE FIX: address the whole group, then reap it.
    kill_and_reap_group(pgid, leader)

    released = wait_for_port_free(port, 5.0)
    holder_stat = ps_field(holder_pid, "stat") or "gone"
    port_state = "FREE" if released else "STILL HELD"
    print(f"      after killpg + reap: holder stat={holder_stat!r} "
          f"port {port_state}")
    if not released:
        failures.append("the port was not released by the group kill")
    if is_live_state(ps_field(holder_pid, "stat")):
        failures.append("the descendant survived the group kill")
    if not failures:
        print("      -> the descendant died with the group it belonged to.")
    return failures


def oracle_orphan_is_not_a_zombie(tmpdir: str) -> list[str]:
    """The taxonomy, MEASURED rather than asserted from folklore.

    Both processes bind a port. The orphan keeps holding it; the zombie's was
    released by the kernel at exit. That difference is measured here, not
    printed as received wisdom.
    """
    failures = []
    print("  [3] an orphan is not a zombie")

    # A live ORPHAN: parent gone, still running, still owns its port.
    leader, leader_pid, orphan_port, holder_pid = start_job(tmpdir)
    pgid = os.getpgid(leader_pid)
    leader.kill()
    leader.wait()
    time.sleep(0.3)
    orphan_stat = ps_field(holder_pid, "stat")
    orphan_ppid = ps_field(holder_pid, "ppid")
    orphan_holds = not port_is_free(orphan_port)

    # A true ZOMBIE: bound a port, exited, deliberately NOT waited on.
    zombie_file = os.path.join(tmpdir, f"zombie-{time.monotonic_ns()}")
    zombie = subprocess.Popen(
        [sys.executable, str(SELF), "--role=exiter", zombie_file]
    )
    zombie_port, zombie_pid = read_published(zombie_file, None, STARTUP_TIMEOUT)
    # Wait for it to actually be a zombie rather than merely exiting.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ps_field(zombie_pid, "stat").startswith("Z"):
            break
        time.sleep(0.02)
    zombie_stat = ps_field(zombie_pid, "stat")
    zombie_ppid = ps_field(zombie_pid, "ppid")
    zombie_port_free = wait_for_port_free(zombie_port, 5.0)

    print(f"      orphan  pid={holder_pid:<7} stat={orphan_stat!r:<8} "
          f"ppid={orphan_ppid:<7} still holds its port: {orphan_holds}")
    print(f"      zombie  pid={zombie_pid:<7} stat={zombie_stat!r:<8} "
          f"ppid={zombie_ppid:<7} its port is rebindable: {zombie_port_free}")

    if not is_live_state(orphan_stat):
        failures.append(f"the orphan is not in a live state: {orphan_stat!r}")
    if not orphan_holds:
        failures.append("the orphan does not hold its port")
    if orphan_ppid == str(leader_pid):
        failures.append("the orphan was not reparented away from the leader")
    if not zombie_stat.startswith("Z"):
        failures.append(
            f"the exited-but-unreaped child is not in Z state ({zombie_stat!r}); "
            "this platform reports process states differently"
        )
    if zombie_ppid != str(os.getpid()):
        failures.append("the zombie should still be OUR child until reaped")
    # THE measurement: a zombie holds nothing, shown by rebinding its port.
    if not zombie_port_free:
        failures.append(
            "the zombie's port could not be rebound — the claim that a zombie "
            "holds no resources does not hold on this platform"
        )

    if not failures:
        print("      -> measured, not assumed: the orphan is alive, reparented,")
        print("         and still owns its port. The zombie is dead, still our")
        print("         child, and its port rebinds immediately. Only the orphan")
        print("         can wedge the next run; chasing Z states is chasing the")
        print("         wrong bug.")

    kill_and_reap_group(pgid, leader)
    zombie.wait()
    if not wait_for_port_free(orphan_port, 5.0):
        failures.append("could not clean up the orphan")
    return failures


class _Terminated(KeyboardInterrupt):
    """A termination signal, routed into the same path as Ctrl-C so the sweep
    runs. Without this, `kill <pid>` on this script would leak exactly the
    orphan the script exists to warn about — the default SIGTERM disposition
    dies immediately and runs no `finally`."""


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        raise _Terminated(f"signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):
            pass


def main() -> int:
    if os.name != "posix":
        print("UNSUPPORTED: this case needs POSIX process groups and `ps`.")
        print("It is not a pass — exiting non-zero so automation cannot")
        print("mistake a skipped platform for a green run.")
        return EXIT_UNSUPPORTED

    _install_signal_handlers()
    started = time.monotonic()
    failures: list[str] = []
    interrupted = False
    try:
        with tempfile.TemporaryDirectory(prefix="leader-only-kill-") as tmpdir:
            for oracle in (
                oracle_leader_only_kill_leaks_and_blocks,
                oracle_group_kill_releases,
                oracle_orphan_is_not_a_zombie,
            ):
                try:
                    failures += oracle(tmpdir)
                except ReproError as exc:
                    failures.append(f"{oracle.__name__}: {exc}")
                print()
    except KeyboardInterrupt:
        interrupted = True
        print("\ninterrupted — sweeping process groups before exit")
    finally:
        # Runs on success, on failure, on an unexpected exception, and on
        # Ctrl-C. Nothing this script started outlives it.
        leaked = sweep_active_pgids()
        if leaked:
            print(f"swept {len(leaked)} process group(s) that were still alive: "
                  f"{leaked}")

    if interrupted:
        return 130

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
            sys.exit(role_exiter(args[1]))
        sys.exit(f"unknown role: {role}")
    sys.exit(main())
