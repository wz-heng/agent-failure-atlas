#!/usr/bin/env python3
"""Prove `claims.py --check` actually catches drift — every claim, every place.

    python3 check_mutation_coverage.py          # full sweep, ~30s
    python3 check_mutation_coverage.py --quick  # one occurrence per claim

Why this exists. Over four review rounds, every attempt to guarantee that this
entry's prose stays tied to its evidence was defeated by a mutation the reviewer
found and I had not:

1. the tests never read README.md at all, so prose-only drift was always green;
2. `literal in readme` is a presence test, not a location test — with the figure
   appearing nine times, editing one occurrence left it present;
3. the numeric right-boundary excluded a trailing `.`, so a figure at the end of
   a sentence ("9.2%.") matched zero times and was silently unprotected;
4. the context anchors covered the English phrasing only, so editing the same
   figure in the Chinese summary changed nothing.

Each fix was correct and each left a hole, because I was confirming coverage by
reading rather than by executing. This file stops doing that. It walks every
claim, finds every occurrence in every bound file, perturbs that ONE occurrence,
and asserts `claims.py --check` fails **and names that claim**. A hole of any of
the four kinds above shows up here as a mutation that survives.

Failure path leaves nothing behind: originals are restored in a `finally`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from claims import BOUND_FILES, CLAIMS, EXPECTED_COUNTS, _pattern

HERE = Path(__file__).resolve().parent


def perturb(literal: str) -> str:
    """Return a wrong-but-plausible version of a figure."""
    words = {"four": "five", "five": "six", "six": "seven",
             "四": "五", "五": "六", "六": "七"}
    if literal in words:
        return words[literal]
    # Bump the final digit; keeps formatting (commas, %, $) intact.
    for i in range(len(literal) - 1, -1, -1):
        if literal[i].isdigit():
            bumped = str((int(literal[i]) + 1) % 10)
            return literal[:i] + bumped + literal[i + 1:]
    raise ValueError(f"cannot perturb {literal!r}")


def mutate_nth(text: str, pattern: str, literal: str, n: int) -> str | None:
    """Replace the literal inside the n-th match of `pattern`."""
    matches = list(re.finditer(pattern, text))
    if n >= len(matches):
        return None
    match = matches[n]
    span = match.group(0)
    if literal not in span:
        # An anchor whose alternative branch does not contain the literal would
        # be a false-negative generator; claims.py must never have one.
        raise AssertionError(
            f"anchor matched {span!r} which does not contain the figure "
            f"{literal!r} — that anchor cannot detect a change to it")
    mutated_span = span.replace(literal, perturb(literal), 1)
    return text[:match.start()] + mutated_span + text[match.end():]


def check_is_red_for(key: str) -> tuple[bool, str]:
    """Run the real entry point; return (failed, output)."""
    result = subprocess.run(
        [sys.executable, str(HERE / "claims.py"), "--check"],
        capture_output=True, text=True, cwd=HERE)
    return result.returncode != 0, result.stdout + result.stderr


def main() -> int:
    quick = "--quick" in sys.argv
    originals = {label: path.read_text() for label, path in BOUND_FILES.items()}

    survived: list[str] = []
    unnamed: list[str] = []
    tested = 0

    try:
        for key, literal, _compute in CLAIMS:
            pattern = _pattern(key, literal)
            expected = EXPECTED_COUNTS.get(key, {})
            if not expected:
                survived.append(f"{key}: bound to NO file at all")
                continue
            for label, count in expected.items():
                path = BOUND_FILES[label]
                for n in range(1 if quick else count):
                    mutated = mutate_nth(originals[label], pattern, literal, n)
                    if mutated is None:
                        survived.append(f"{key} @ {label}#{n}: no such occurrence")
                        continue
                    path.write_text(mutated)
                    try:
                        red, output = check_is_red_for(key)
                        tested += 1
                        if not red:
                            survived.append(
                                f"{key} @ {label} occurrence {n}: MUTATION SURVIVED")
                        elif key not in output:
                            unnamed.append(
                                f"{key} @ {label} occurrence {n}: went red, "
                                f"but the report did not name this claim")
                    finally:
                        path.write_text(originals[label])
    finally:
        for label, path in BOUND_FILES.items():
            path.write_text(originals[label])

    print(f"mutated and re-checked {tested} individual occurrences "
          f"across {len(CLAIMS)} claims and {len(BOUND_FILES)} files")

    if unnamed:
        print(f"\n{len(unnamed)} mutation(s) were caught but not attributed:")
        for line in unnamed:
            print(f"  - {line}")
    if survived:
        print(f"\n{len(survived)} MUTATION(S) SURVIVED — coverage is incomplete:")
        for line in survived:
            print(f"  - {line}")
        return 1
    print("Every occurrence of every claim is detected. No false negatives.")
    return 0 if not unnamed else 1


if __name__ == "__main__":
    sys.exit(main())
