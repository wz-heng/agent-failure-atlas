#!/usr/bin/env python3
"""Regenerate the mutation table in README.md §4.

    python3 mutations.py

Each mutation is a one-line edit to `repro.py`, applied to a throwaway copy of
this directory. For each, both scripts are run and the results printed as the
markdown table the README carries.

This file exists because the table was hand-transcribed twice and was wrong both
times. An entry arguing "assert on what it produced" should not ship numbers a
reader cannot reproduce, so the numbers are generated instead of typed. Nothing
here touches the real `repro.py`.

Offline, stdlib only, ~15s (it runs the suite once per mutation plus a baseline).
Exit 0 = the table printed. Exit 1 = a mutation no longer applies, which means
`repro.py` changed and the table needs regenerating anyway.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (label, exact substring in repro.py, its replacement)
MUTATIONS: list[tuple[str, str, str]] = [
    (
        "delete the standalone `error` branch",
        '    if kind == "error":\n'
        '        return [Event("error", content=obj.get("message") or "Unknown error", is_error=True)]\n',
        "",
    ),
    (
        "delete the `turn.failed` branch",
        '    if kind == "turn.failed":',
        "    if False:  # mutation",
    ),
    (
        "disable guard 1 (the empty-turn check)",
        "if not turn_failed and not out.delivered:",
        "if False:  # mutation",
    ),
    (
        "guard 1 ignores what was delivered",
        "if not turn_failed and not out.delivered:",
        "if not turn_failed:  # mutation",
    ),
    (
        "disable guard 2 (surfacing dropped records)",
        "        for obj in out.dropped:",
        "        for obj in []:  # mutation",
    ),
    (
        "emit an empty-string event instead of no event",
        "if completed and text:",
        "if completed and text is not None:  # mutation",
    ),
]


def run_in(workdir: Path) -> tuple[int, int]:
    """Return (tests_red, repro_exit) for the copy in `workdir`."""
    tests = subprocess.run(
        [sys.executable, "test_defense.py"], cwd=workdir,
        capture_output=True, text=True, timeout=300,
    )
    # The standalone runner prints "N/M passed" as its last line; derive the
    # red count from that rather than by counting FAIL lines, which a
    # multi-line assertion message would inflate. (It did: an earlier
    # hand-run of this matrix counted FAIL lines and over-reported.)
    match = re.search(r"(\d+)/(\d+) passed\s*$", tests.stdout.strip())
    if not match:
        print(f"could not parse the test summary:\n{tests.stdout[-2000:]}",
              file=sys.stderr)
        raise SystemExit(1)
    passed, total = int(match.group(1)), int(match.group(2))

    repro = subprocess.run(
        [sys.executable, "repro.py"], cwd=workdir,
        capture_output=True, text=True, timeout=300,
    )
    return total - passed, repro.returncode


def main() -> int:
    rows = []
    with tempfile.TemporaryDirectory() as root:
        base = Path(root) / "entry"

        def fresh() -> Path:
            if base.exists():
                shutil.rmtree(base)
            shutil.copytree(HERE, base, ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", "mutations.py"))
            return base

        red, code = run_in(fresh())
        if (red, code) != (0, 0):
            print(f"baseline is not green: {red} tests red, repro exit {code}",
                  file=sys.stderr)
            return 1
        print("baseline: 0 tests red, repro.py exit 0\n", file=sys.stderr)

        for label, old, new in MUTATIONS:
            work = fresh()
            target = work / "repro.py"
            source = target.read_text()
            if source.count(old) != 1:
                print(f"mutation {label!r} matches {source.count(old)} times in "
                      f"repro.py, expected exactly 1 — repro.py has changed",
                      file=sys.stderr)
                return 1
            target.write_text(source.replace(old, new))
            red, code = run_in(work)
            rows.append((label, red, "green" if code == 0 else "red"))
            print(f"  {label}: {red} red, repro {rows[-1][2]}", file=sys.stderr)

    print("\n| mutation | tests red | `repro.py` |")
    print("|---|---|---|")
    for label, red, verdict in rows:
        cell = f"**{verdict}**" if verdict == "green" else verdict
        print(f"| {label} | {red} | {cell} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
