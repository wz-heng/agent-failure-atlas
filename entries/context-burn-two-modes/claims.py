#!/usr/bin/env python3
"""Every number this entry quotes, recomputed from the traces and checked
against the prose that quotes it.

    python3 claims.py            # print the table
    python3 claims.py --check    # assert traces AND README still agree

Why this file exists. An earlier revision of this entry claimed that its
regression tests "pin the prose to the traces", so that editing a figure in
README.md without editing the evidence would turn them red. That was false and
a reviewer proved it: no test read README.md at all, so README-only drift was
permanently green, and a trace row that no test happened to name could be
altered with everything still passing.

So each claim below carries three things: a key, the exact literal string as it
appears in the prose, and the computation that derives it from the committed
traces. `--check` asserts both halves — the computation still produces the
value, and the value still appears in the file that quotes it. Neither the
prose nor the evidence can now move without the other.

This is the same discipline `mutations.py` applies in the silent-empty-turn
entry: a number a reader cannot regenerate does not belong in an entry whose
thesis is that you should assert on what you actually produced.
"""

from __future__ import annotations

import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

from repro import CACHE_WRITE_MULTIPLIER_1H, billed, cost_parts, load

HERE = Path(__file__).resolve().parent

A = "hot_reread_A.jsonl"
B = "cold_rewrite_B.jsonl"
C = "cold_rewrite_C.jsonl"
D = "codex_no_cache_field_D.jsonl"
CLAUDE_TRACES = (A, B, C)
ALL_TRACES = (A, B, C, D)


# SHA-256 of every committed trace, so that ANY edit to the evidence is caught
# — not only edits to rows a claim happens to name. A reviewer demonstrated the
# gap: incrementing a token counter on an unnamed row, with the cost adjusted to
# keep the pricing model consistent, left every claim and every test green. The
# traces are fixed evidence; a digest is the honest way to say so.
TRACE_DIGESTS = {
    "codex_no_cache_field_D.jsonl":
        "bca398c4c35842ef085775fde1de63a14f766b7acf9a9e4606e748a0ee852bed",
    "cold_rewrite_B.jsonl":
        "65186a844ff0e5d2b20be41ec12f3ea284cc086079d4e32899d09af6502dc844",
    "cold_rewrite_C.jsonl":
        "c7bc5326fad7d455ca8c6f72acd02eb9f10604c3a7dae5900ebe658b558c8b34",
    "hot_reread_A.jsonl":
        "997ab986283d73695b61b7063fc6e39ed7a5f1437ddb14dff7031ee092d59b82",
}



# --- where each claim must appear, and how many times ----------------------
# A reviewer showed that `literal in readme` is not a location check: "20"
# occurs 41 times in this file, so changing one of three occurrences of a
# figure left the checker green. Each claim is therefore bound to specific
# files with an EXACT expected match count. Change one of three occurrences
# and the count drops to two, and --check goes red.
#
# For figures whose literal is a short number that recurs incidentally
# ("20", "six", "95"), a context ANCHOR regex replaces the bare literal so the
# count is not hostage to unrelated prose. Everything else uses the literal with
# numeric boundaries, so "20" does not match inside "2026" or "20,000".
#
# Known limitation, stated rather than papered over: this pins how MANY times a
# figure appears in each file, not which sentence each occurrence sits in.
# Moving a correct figure from one paragraph to another inside the same file is
# not detected. Editing, deleting, or duplicating one is.
# Every ANCHOR must contain the figure itself, and must cover EVERY
# localisation of it -- English prose, tables, the Chinese summary, and both
# root READMEs. An earlier revision anchored only the English phrasing, so
# editing the Chinese summary's copy of the same figure left the count
# unchanged and the checker green. A reviewer found that by mutation too;
# `check_mutation_coverage.py` now proves it cannot recur — and it immediately
# found one: an anchor branch matching Chinese text that does not contain the
# English literal cannot detect a change to that text, so the Chinese numerals
# ("四", "五") now carry their own claims rather than riding an English one.
ANCHORS: dict[str, str] = {
    "A.turn_count":            r"across \*\*20\*\* turns|\*\*20\*\* 个 turn",
    "A.billed_turn_count":     r"\*\*15\*\* carry usage|\*\*15\*\* 个轮次带有用量",
    "B.turn_count":            r"across its \*\*66\*\* turns|\*\*66\*\* 个轮次",
    "gap.long_n":              r"after a >1h gap \| \*\*20\*\*|>1h 间隔 \*\*20\*\* 轮",
    "gap.short_n":             r"after a .1h gap \| \*\*75\*\*|1h 间隔 \*\*75\*\* 轮",
    "price.reconciling_1h":    r"\*\*95\*\* of \*\*99\*\* priced turns|95 fit\b|\*\*95\*\* 轮",
    "price.priced_turns":      r"\*\*95\*\* of \*\*99\*\* priced turns|the 99 priced turns|\*\*99\*\* 个计费轮",
    "price.unreconciled":      r"\*\*four turns do not\s+reconcile\*\*|pins the count at \*\*four\*\*",
    "price.unreconciled.zh":   r"有四轮无法对账",
    "B.jul15_write_turns":     r"\*\*five\*\* turns rewriting",
    "B.jul15_write_turns.zh":  r"\*\*五\*\*个重写",
    "codex.turn_count":        r"\*\*6\*\*-turn codex session|\*\*6\*\* 轮 codex",
    "codex.long_gap_zero_write": r"\*\*132\*\*-minute gap|\*\*132\*\* 分钟间隔|132-minute codex gap",
    "B.peak_duration_seconds": r"\*\*131\*\* seconds|\*\*131\*\* 秒|131-second turn",
    "C.sibling_delay_seconds": r"\*\*81\*\* seconds|\*\*81\*\* 秒|81s after B|81-second sibling",
    "B.priced_turns":          r"\| B \| 60 \||60 个计费轮",
    "B.reconciled_turns":      r"\*\*56\*\* \||56 轮对账",
    "C.priced_turns":          r"\| C \| 24 \||24 轮",
    "A.reconciled_turns":      r"\| A \| 15 \| \*\*15\*\*|15 个计费轮\*\*全部\*\*对账",
}

BOUND_FILES = {
    "README.md": HERE / "README.md",
    "evidence/README.md": HERE / "evidence" / "README.md",
    "root README.md": HERE.parent.parent / "README.md",
    "root README.zh-CN.md": HERE.parent.parent / "README.zh-CN.md",
}


def _pattern(key: str, literal: str) -> str:
    """Regex for one claim's literal.

    The right boundary forbids only a DIGIT continuation -- `(?!\d|[.,]\d)` --
    so "20" still refuses to match inside "20,000" or "2026", but a figure at
    the end of a sentence ("... 9.2%.") is matched. An earlier revision excluded
    plain `.` and `,` outright, which silently dropped every sentence-final
    occurrence: the root README's "9.2%." counted as zero matches, so editing it
    was invisible. A reviewer found it by mutation.
    """
    if key in ANCHORS:
        return ANCHORS[key]
    if literal[0].isdigit() or literal[0] == "$":
        return r"(?<![\d,.$])" + re.escape(literal) + r"(?!\d|[.,]\d)"
    return re.escape(literal)


def occurrences(key: str, literal: str) -> dict[str, int]:
    pat = re.compile(_pattern(key, literal))
    out = {}
    for label, path in BOUND_FILES.items():
        n = len(pat.findall(path.read_text()))
        if n:
            out[label] = n
    return out


EXPECTED_COUNTS = {
    'A.cache_read_tokens': {'README.md': 3},
    'A.output_tokens': {'README.md': 5},
    'A.read_to_output_ratio': {'README.md': 2},
    'A.total_cost': {'README.md': 6, 'evidence/README.md': 1},
    'A.context_share': {'README.md': 5, 'root README.md': 1, 'root README.zh-CN.md': 1},
    'A.generation_share': {'README.md': 12, 'root README.md': 1, 'root README.zh-CN.md': 1},
    'A.cache_read_line': {'README.md': 5},
    'A.cache_write_line': {'README.md': 3},
    'A.output_line': {'README.md': 2},
    'A.input_line': {'README.md': 2},
    'A.worst_turn_cache_read': {'README.md': 2},
    'A.call_floor': {'README.md': 4, 'evidence/README.md': 1},
    'A.turn_count': {'README.md': 2},
    'A.billed_turn_count': {'README.md': 1},
    'B.peak_cache_write': {'README.md': 4},
    'B.peak_cost': {'README.md': 9, 'root README.md': 1, 'root README.zh-CN.md': 1},
    'B.peak_write_line': {'README.md': 5},
    'B.peak_write_share': {'README.md': 2},
    'B.peak_output_tokens': {'README.md': 6, 'root README.md': 1, 'root README.zh-CN.md': 1},
    'B.peak_duration_seconds': {'README.md': 4, 'root README.md': 1},
    'B.peak_idle_hours': {'README.md': 2, 'evidence/README.md': 1},
    'B.lifetime_cost': {'README.md': 5, 'evidence/README.md': 1},
    'B.turn_count': {'README.md': 1},
    'B.jul15_write_line': {'README.md': 4},
    'B.jul15_write_turns.zh': {'README.md': 1},
    'B.jul15_write_turns': {'README.md': 2},
    'C.sibling_cost': {'README.md': 2},
    'C.sibling_cache_write': {'README.md': 2},
    'C.sibling_delay_seconds': {'README.md': 4, 'evidence/README.md': 2},
    'A.reconciled_turns': {'README.md': 2},
    'B.priced_turns': {'README.md': 2},
    'B.reconciled_turns': {'README.md': 2},
    'C.priced_turns': {'README.md': 2},
    'C.total_cost': {'evidence/README.md': 1},
    'price.reconciling_1h': {'README.md': 1, 'evidence/README.md': 1},
    'price.priced_turns': {'README.md': 1, 'evidence/README.md': 1},
    'price.unreconciled.zh': {'README.md': 1},
    'price.unreconciled': {'README.md': 1},
    'price.zero_token_charge': {'README.md': 2, 'evidence/README.md': 1},
    'gap.long_n': {'README.md': 2},
    'gap.short_n': {'README.md': 2},
    'gap.long_median': {'README.md': 2},
    'gap.short_median': {'README.md': 2},
    'gap.A_long': {'README.md': 4},
    'gap.A_short': {'README.md': 2},
    'gap.B_long': {'README.md': 2},
    'gap.B_short': {'README.md': 2},
    'gap.C_long': {'README.md': 2},
    'gap.C_short': {'README.md': 2},
    'codex.turn_count': {'README.md': 2},
    'codex.long_gap_zero_write': {'README.md': 2, 'evidence/README.md': 1},
}


def session_totals(name: str) -> dict[str, float]:
    totals = {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0}
    for row in billed(load(name)):
        parts = cost_parts(row, CACHE_WRITE_MULTIPLIER_1H)
        assert parts is not None, row["model"]
        for key, value in parts.items():
            totals[key] += value
    return totals


def peak_rewrite(name: str) -> dict:
    return max(billed(load(name)), key=lambda r: r["cache_creation_tokens"])


def when(row: dict) -> datetime:
    return datetime.fromisoformat(row["created_at"])


def rewrite_shares(name: str) -> tuple[list[float], list[float]]:
    """(shares after a >1h gap, shares after a <=1h gap) for one trace,
    counting only claude-code turns."""
    long_gap: list[float] = []
    short_gap: list[float] = []
    rows = [r for r in billed(load(name)) if r["backend"] == "claude-code"]
    for previous, current in zip(rows, rows[1:]):
        touched = current["cache_read_tokens"] + current["cache_creation_tokens"]
        if not touched:
            continue
        share = current["cache_creation_tokens"] / touched
        gap = (when(current) - when(previous)).total_seconds()
        (long_gap if gap > 3600 else short_gap).append(share)
    return long_gap, short_gap


def pooled_shares() -> tuple[list[float], list[float]]:
    long_all: list[float] = []
    short_all: list[float] = []
    for name in CLAUDE_TRACES:
        long_gap, short_gap = rewrite_shares(name)
        long_all += long_gap
        short_all += short_gap
    return long_all, short_all


def reconciling_turns(multiplier: float) -> tuple[int, int]:
    matched = total = 0
    for name in ALL_TRACES:
        for row in load(name):
            if not row["cost"]:
                continue
            parts = cost_parts(row, multiplier)
            if parts is None:
                continue
            total += 1
            if abs(sum(parts.values()) - row["cost"]) / row["cost"] < 0.001:
                matched += 1
    return matched, total


# --- the claims -----------------------------------------------------------
# (key, literal as it appears in the prose, callable -> value to compare)
def _a_totals() -> dict[str, float]:
    return session_totals(A)


CLAIMS: list[tuple[str, str, callable]] = [
    # --- session A, the hot re-read
    ("A.cache_read_tokens", "43,715,956",
     lambda: f"{sum(r['cache_read_tokens'] for r in load(A)):,}"),
    ("A.output_tokens", "197,896",
     lambda: f"{sum(r['output_tokens'] for r in load(A)):,}"),
    ("A.read_to_output_ratio", "220.9",
     lambda: f"{sum(r['cache_read_tokens'] for r in load(A)) / sum(r['output_tokens'] for r in load(A)):.1f}"),
    ("A.total_cost", "$53.85",
     lambda: f"${sum(r['cost'] for r in load(A)):.2f}"),
    ("A.context_share", "90.8%",
     lambda: f"{(_a_totals()['input'] + _a_totals()['cache_read'] + _a_totals()['cache_write']) / sum(_a_totals().values()):.1%}"),
    ("A.generation_share", "9.2%",
     lambda: f"{_a_totals()['output'] / sum(_a_totals().values()):.1%}"),
    ("A.cache_read_line", "$21.86", lambda: f"${_a_totals()['cache_read']:.2f}"),
    ("A.cache_write_line", "$26.85", lambda: f"${_a_totals()['cache_write']:.2f}"),
    ("A.output_line", "$4.95", lambda: f"${_a_totals()['output']:.2f}"),
    ("A.input_line", "$0.19", lambda: f"${_a_totals()['input']:.2f}"),
    ("A.worst_turn_cache_read", "11,289,438",
     lambda: f"{max(r['cache_read_tokens'] for r in load(A)):,}"),
    ("A.call_floor", "11.3",
     lambda: f"{max(load(A), key=lambda r: r['cache_read_tokens'])['cache_read_tokens'] / max(load(A), key=lambda r: r['cache_read_tokens'])['context_window']:.1f}"),
    ("A.turn_count", "20", lambda: str(len(load(A)))),
    ("A.billed_turn_count", "15", lambda: str(len(billed(load(A))))),

    # --- session B, the cold rewrite
    ("B.peak_cache_write", "974,755",
     lambda: f"{peak_rewrite(B)['cache_creation_tokens']:,}"),
    ("B.peak_cost", "$20.6988", lambda: f"${peak_rewrite(B)['cost']:.4f}"),
    ("B.peak_write_line", "$19.50",
     lambda: f"${cost_parts(peak_rewrite(B), CACHE_WRITE_MULTIPLIER_1H)['cache_write']:.2f}"),
    ("B.peak_write_share", "94.2%",
     lambda: f"{cost_parts(peak_rewrite(B), CACHE_WRITE_MULTIPLIER_1H)['cache_write'] / sum(cost_parts(peak_rewrite(B), CACHE_WRITE_MULTIPLIER_1H).values()):.1%}"),
    ("B.peak_output_tokens", "3,080",
     lambda: f"{peak_rewrite(B)['output_tokens']:,}"),
    ("B.peak_duration_seconds", "131",
     lambda: str(round(peak_rewrite(B)["duration_ms"] / 1000))),
    ("B.peak_idle_hours", "4.1",
     lambda: f"{(when(peak_rewrite(B)) - when(billed(load(B))[billed(load(B)).index(peak_rewrite(B)) - 1])).total_seconds() / 3600:.1f}"),
    ("B.lifetime_cost", "$235.7484",
     lambda: f"${sum(r['cost'] for r in load(B)):.4f}"),
    ("B.turn_count", "66", lambda: str(len(load(B)))),
    ("B.jul15_write_line", "$56.80",
     lambda: "$%.2f" % sum(
         cost_parts(r, CACHE_WRITE_MULTIPLIER_1H)["cache_write"]
         for r in billed(load(B))
         if r["created_at"].startswith("2026-07-15") and r["cache_creation_tokens"] >= 400_000)),
    ("B.jul15_write_turns.zh", "五",
     lambda: {5: "五"}.get(sum(
         1 for r in billed(load(B))
         if r["created_at"].startswith("2026-07-15") and r["cache_creation_tokens"] >= 400_000), "?")),
    ("B.jul15_write_turns", "five",
     lambda: {5: "five"}.get(sum(
         1 for r in billed(load(B))
         if r["created_at"].startswith("2026-07-15") and r["cache_creation_tokens"] >= 400_000), "?")),

    # --- session C, the sibling
    ("C.sibling_cost", "$3.8608",
     lambda: f"${min(billed(load(C)), key=lambda r: abs((when(r) - when(peak_rewrite(B))).total_seconds()))['cost']:.4f}"),
    ("C.sibling_cache_write", "175,728",
     lambda: f"{min(billed(load(C)), key=lambda r: abs((when(r) - when(peak_rewrite(B))).total_seconds()))['cache_creation_tokens']:,}"),
    ("C.sibling_delay_seconds", "81",
     lambda: str(round(abs((when(min(billed(load(C)), key=lambda r: abs((when(r) - when(peak_rewrite(B))).total_seconds()))) - when(peak_rewrite(B))).total_seconds())))),

    # --- the per-session reconciliation table (README §3, evidence/README)
    ("A.reconciled_turns", "15",
     lambda: str(sum(1 for r in load(A) if r["cost"] and
                     abs(sum(cost_parts(r, CACHE_WRITE_MULTIPLIER_1H).values()) - r["cost"]) / r["cost"] < 0.001))),
    ("B.priced_turns", "60", lambda: str(sum(1 for r in load(B) if r["cost"]))),
    ("B.reconciled_turns", "56",
     lambda: str(sum(1 for r in load(B) if r["cost"] and
                     abs(sum(cost_parts(r, CACHE_WRITE_MULTIPLIER_1H).values()) - r["cost"]) / r["cost"] < 0.001))),
    ("C.priced_turns", "24", lambda: str(sum(1 for r in load(C) if r["cost"]))),
    ("C.total_cost", "$69.92", lambda: f"${sum(r['cost'] for r in load(C)):.2f}"),

    # --- the pricing reconciliation
    ("price.reconciling_1h", "95", lambda: str(reconciling_turns(CACHE_WRITE_MULTIPLIER_1H)[0])),
    ("price.priced_turns", "99", lambda: str(reconciling_turns(CACHE_WRITE_MULTIPLIER_1H)[1])),
    ("price.unreconciled.zh", "四",
     lambda: {4: "四"}.get(
         reconciling_turns(CACHE_WRITE_MULTIPLIER_1H)[1]
         - reconciling_turns(CACHE_WRITE_MULTIPLIER_1H)[0], "?")),
    ("price.unreconciled", "four",
     lambda: {4: "four"}.get(
         reconciling_turns(CACHE_WRITE_MULTIPLIER_1H)[1]
         - reconciling_turns(CACHE_WRITE_MULTIPLIER_1H)[0], "?")),
    ("price.zero_token_charge", "$9.8231",
     lambda: "$%.4f" % max(
         (r["cost"] for name in ALL_TRACES for r in load(name)
          if r["total_tokens"] == 0 and (r["cost"] or 0) > 1), default=0)),

    # --- the association, pooled and stratified
    ("gap.long_n", "20", lambda: str(len(pooled_shares()[0]))),
    ("gap.short_n", "75", lambda: str(len(pooled_shares()[1]))),
    ("gap.long_median", "28.1%", lambda: f"{statistics.median(pooled_shares()[0]):.1%}"),
    ("gap.short_median", "1.2%", lambda: f"{statistics.median(pooled_shares()[1]):.1%}"),
    ("gap.A_long", "0.2%", lambda: f"{statistics.median(rewrite_shares(A)[0]):.1%}"),
    ("gap.A_short", "5.8%", lambda: f"{statistics.median(rewrite_shares(A)[1]):.1%}"),
    ("gap.B_long", "47.4%", lambda: f"{statistics.median(rewrite_shares(B)[0]):.1%}"),
    ("gap.B_short", "0.8%", lambda: f"{statistics.median(rewrite_shares(B)[1]):.1%}"),
    ("gap.C_long", "20.6%", lambda: f"{statistics.median(rewrite_shares(C)[0]):.1%}"),
    ("gap.C_short", "3.1%", lambda: f"{statistics.median(rewrite_shares(C)[1]):.1%}"),

    # --- the codex confound
    ("codex.turn_count", "6", lambda: str(len(load(D)))),
    ("codex.long_gap_zero_write", "132",
     lambda: str(round(max(
         (when(b) - when(a)).total_seconds()
         for a, b in zip(billed(load(D)), billed(load(D))[1:])) / 60))),
]


def evaluate() -> list[tuple[str, str, str, bool]]:
    rows = []
    for key, literal, compute in CLAIMS:
        actual = str(compute())
        rows.append((key, literal, actual, actual == literal))
    return rows


def verify_digests() -> list[str]:
    import hashlib
    bad = []
    for name, expected in TRACE_DIGESTS.items():
        actual = hashlib.sha256((HERE / "evidence" / name).read_bytes()).hexdigest()
        if actual != expected:
            bad.append(f"{name}: expected {expected[:16]}..., got {actual[:16]}...")
    return bad


def main() -> int:
    check = "--check" in sys.argv
    rows = evaluate()

    width = max(len(k) for k, _, _, _ in rows)
    bad_value = []
    bad_prose = []
    print(f"{'claim'.ljust(width)}  {'quoted':>12}  {'computed':>12}  in README")
    for key, literal, actual, ok in rows:
        found = occurrences(key, literal)
        expected = EXPECTED_COUNTS.get(key, {})
        if not ok:
            bad_value.append(key)
        if found != expected:
            bad_prose.append((key, expected, found))
        flag = "ok" if ok else "MISMATCH"
        seen = "ok" if found == expected else "MOVED"
        total = sum(found.values())
        print(f"{key.ljust(width)}  {literal:>12}  {actual:>12}  "
              f"{total:>3} occ  {seen:>5}  {flag}")

    if not check:
        return 0

    print()
    if bad_value:
        print(f"{len(bad_value)} claim(s) no longer match the traces:")
        for key in bad_value:
            print(f"  - {key}")
    if bad_prose:
        print(f"{len(bad_prose)} claim(s) no longer appear where they should:")
        for key, expected, found in bad_prose:
            print(f"  - {key}: expected {expected}, found {found}")
    bad_digest = verify_digests()
    if bad_digest:
        print(f"{len(bad_digest)} trace file(s) changed since the claims were fixed:")
        for line in bad_digest:
            print(f"  - {line}")
    if bad_value or bad_prose or bad_digest:
        return 1
    print(f"All {len(rows)} claims match both the traces and the prose; "
          f"all {len(TRACE_DIGESTS)} trace digests unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
