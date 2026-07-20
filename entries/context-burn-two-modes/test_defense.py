#!/usr/bin/env python3
"""Regression tests: the watermark defense, and the traces the entry quotes.

Two groups, and the split matters:

* `test_defense_*` pin the proposed defense (`simulate.py`). The defense is a
  PROPOSAL — Owlery does not implement it (README.md §5). These tests pin the
  behaviour of the reference implementation in this directory, nothing more.

* `test_trace_*` pin the evidence files against the exact figures the entry's
  prose quotes. If someone edits a number in README.md, or regenerates a trace
  from a changed ledger, these go red. The prose cannot drift away from the
  traces in silence.

    python3 test_defense.py            # or, if pytest is installed:
    python3 -m pytest test_defense.py -q
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from repro import (
    CACHE_WRITE_MULTIPLIER_1H,
    CACHE_WRITE_MULTIPLIER_5M,
    LIST_PRICE,
    billed,
    cost_parts,
)
from simulate import (
    CACHE_TTL_SECONDS,
    CONTEXT_WINDOW,
    run_session_strategy,
    should_retire_session,
)

EVIDENCE = Path(__file__).resolve().parent / "evidence"

# Exactly the keys `export_traces.py` emits. A record carrying anything else
# means the redaction transform changed and the entry's redaction claim in
# evidence/README.md needs re-checking.
EXPECTED_KEYS = {
    "session", "created_at", "backend", "model", "input_tokens",
    "cache_read_tokens", "cache_creation_tokens", "output_tokens",
    "total_tokens", "duration_ms", "is_error", "cost", "context_window",
}


def load(name: str) -> list[dict]:
    with (EVIDENCE / name).open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ===========================================================================
# The defense
# ===========================================================================
def test_defense_fires_at_the_threshold_and_not_below() -> None:
    assert should_retire_session(500_000, 1_000_000) is True
    assert should_retire_session(499_999, 1_000_000) is False


def test_defense_threshold_is_configurable() -> None:
    assert should_retire_session(800_000, 1_000_000, threshold=0.80) is True
    assert should_retire_session(799_999, 1_000_000, threshold=0.80) is False
    # A threshold of 0 retires immediately; 1.0 only at a literally full window.
    assert should_retire_session(0, 1_000_000, threshold=0.0) is True
    assert should_retire_session(999_999, 1_000_000, threshold=1.0) is False


def test_defense_rejects_a_nonsensical_window() -> None:
    """A zero or negative window would make every comparison meaningless and
    silently disable the guard — the exact failure mode a watermark exists to
    prevent. It raises instead."""
    for bad in (0, -1):
        try:
            should_retire_session(1, bad)
        except ValueError:
            continue
        raise AssertionError(f"window={bad} should have raised")


def test_defense_reduces_spend_on_identical_work() -> None:
    marathon, marathon_sessions, marathon_peak = run_session_strategy(None)
    guarded, guarded_sessions, guarded_peak = run_session_strategy(
        int(CONTEXT_WINDOW * 0.50)
    )
    assert guarded.cost < marathon.cost
    assert guarded_sessions > marathon_sessions        # it actually fired
    assert guarded_peak < marathon_peak                # and it capped the context


def test_defense_does_not_change_what_was_produced() -> None:
    """The guard must not be able to look good by doing less work. Output
    tokens are identical across both arms; only context handling differs."""
    marathon, _, _ = run_session_strategy(None)
    guarded, _, _ = run_session_strategy(int(CONTEXT_WINDOW * 0.50))
    assert marathon.output_tokens == guarded.output_tokens


def test_defense_is_inert_when_the_watermark_is_never_reached() -> None:
    """A threshold the workload never crosses must behave exactly like no
    guard at all — no spurious session churn. This test exists because an
    earlier revision of `simulate.py` shipped with precisely this bug: the
    workload topped out at 430k against a 500k watermark, so the 'defended'
    arm was byte-identical to the marathon and the comparison was vacuous.
    An oracle caught it; this pins it."""
    marathon, marathon_sessions, _ = run_session_strategy(None)
    never, never_sessions, _ = run_session_strategy(10_000_000)
    assert never.cost == marathon.cost
    assert never_sessions == marathon_sessions == 1


def test_simulation_is_deterministic() -> None:
    """No wall clock, no randomness — two runs must agree exactly, or the
    numbers in the entry are not reproducible."""
    first, first_sessions, first_peak = run_session_strategy(500_000)
    second, second_sessions, second_peak = run_session_strategy(500_000)
    assert (first.cost, first_sessions, first_peak) == (
        second.cost, second_sessions, second_peak
    )


# ===========================================================================
# The traces — pinning the entry's prose
# ===========================================================================
def test_trace_records_carry_no_content_and_no_identifiers() -> None:
    for name in ("hot_reread_A.jsonl", "cold_rewrite_B.jsonl", "cold_rewrite_C.jsonl"):
        raw = (EVIDENCE / name).read_text()
        assert "/Users/" not in raw and "/tmp/" not in raw, name
        for record in load(name):
            assert set(record) == EXPECTED_KEYS, (name, set(record) ^ EXPECTED_KEYS)
            assert record["session"].startswith("session-"), name


def test_trace_session_a_totals_match_the_entry() -> None:
    rows = load("hot_reread_A.jsonl")
    assert len(rows) == 20
    assert len(billed(rows)) == 15                 # five zero-usage rows
    assert sum(r["cache_read_tokens"] for r in rows) == 43_715_956
    assert sum(r["output_tokens"] for r in rows) == 197_896
    assert round(sum(r["cost"] for r in rows), 4) == 53.8524


def test_trace_session_b_wakeup_turn_matches_the_entry() -> None:
    rows = billed(load("cold_rewrite_B.jsonl"))
    peak = max(rows, key=lambda r: r["cache_creation_tokens"])
    assert peak["created_at"].startswith("2026-07-15T06:06:34")
    assert peak["cache_creation_tokens"] == 974_755
    assert round(peak["cost"], 4) == 20.6988
    parts = cost_parts(peak, CACHE_WRITE_MULTIPLIER_1H)
    assert parts is not None
    assert round(parts["cache_write"], 2) == 19.50


def test_trace_session_b_lifetime_total_matches_the_entry() -> None:
    """The measured lifetime figure. The operator's notes said $212; the
    ledger says $235.7484, and the entry uses the ledger."""
    rows = load("cold_rewrite_B.jsonl")
    assert len(rows) == 66
    assert round(sum(r["cost"] for r in rows), 4) == 235.7484


def test_trace_session_c_paid_its_own_tax_at_the_same_moment() -> None:
    b_peak = max(billed(load("cold_rewrite_B.jsonl")),
                 key=lambda r: r["cache_creation_tokens"])
    c_rows = billed(load("cold_rewrite_C.jsonl"))
    sibling = min(c_rows, key=lambda r: abs(
        (datetime.fromisoformat(r["created_at"])
         - datetime.fromisoformat(b_peak["created_at"])).total_seconds()))
    assert round(sibling["cost"], 4) == 3.8608
    assert sibling["cache_creation_tokens"] == 175_728
    delta = (datetime.fromisoformat(sibling["created_at"])
             - datetime.fromisoformat(b_peak["created_at"])).total_seconds()
    assert 0 < delta < 120                          # 81 seconds later


def test_trace_the_five_day_cache_write_line_dominates_session_b() -> None:
    """§2's claim that the rewrite, not the reading and not the thinking, is
    session B's largest single cost line."""
    totals = {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0}
    for row in billed(load("cold_rewrite_B.jsonl")):
        parts = cost_parts(row, CACHE_WRITE_MULTIPLIER_1H)
        assert parts is not None
        for key, value in parts.items():
            totals[key] += value
    assert max(totals, key=totals.get) == "cache_write"
    assert totals["cache_write"] > totals["cache_read"] > totals["output"]


def test_trace_codex_turns_never_report_cache_creation() -> None:
    """The confound oracle 4 controls for. Every codex-backend turn reports
    cache_creation_tokens as 0 — not because nothing was written, but because
    that backend does not report the field. Counting them would manufacture
    long-gap turns that appear to rewrite nothing."""
    codex = [r for name in ("hot_reread_A.jsonl", "cold_rewrite_B.jsonl",
                            "cold_rewrite_C.jsonl")
             for r in load(name) if r["backend"] == "codex"]
    assert all(r["cache_creation_tokens"] == 0 for r in codex)


def test_trace_only_the_1h_rate_reconciles() -> None:
    """The finding that identifies the TTL from billing. Pinned here as well
    as in repro.py, because it is the load-bearing premise of every dollar
    figure in the entry."""
    rows = [r for name in ("hot_reread_A.jsonl", "cold_rewrite_B.jsonl",
                           "cold_rewrite_C.jsonl") for r in load(name)]
    def matches(multiplier: float) -> int:
        hits = 0
        for row in rows:
            if not row["cost"]:
                continue
            parts = cost_parts(row, multiplier)
            if parts and abs(sum(parts.values()) - row["cost"]) / row["cost"] < 0.001:
                hits += 1
        return hits
    assert matches(CACHE_WRITE_MULTIPLIER_5M) == 0
    assert matches(CACHE_WRITE_MULTIPLIER_1H) == 95


def test_trace_unreconciled_turn_count_is_what_the_entry_says() -> None:
    """§3 says four turns in these traces do not reconcile. If that number
    changes, §3 is wrong and this goes red."""
    rows = [r for name in ("hot_reread_A.jsonl", "cold_rewrite_B.jsonl",
                           "cold_rewrite_C.jsonl") for r in load(name)]
    unreconciled = []
    for row in rows:
        if not row["cost"]:
            continue
        parts = cost_parts(row, CACHE_WRITE_MULTIPLIER_1H)
        assert parts is not None, row["model"]
        if abs(sum(parts.values()) - row["cost"]) / row["cost"] >= 0.001:
            unreconciled.append(row)
    assert len(unreconciled) == 4
    # One of them is the strangest row in the corpus and §3 names it: an
    # errored turn billed $9.82 with every token counter at zero.
    zero_token_charge = [r for r in unreconciled
                         if r["total_tokens"] == 0 and r["cost"] > 1]
    assert len(zero_token_charge) == 1
    assert round(zero_token_charge[0]["cost"], 4) == 9.8231
    assert zero_token_charge[0]["is_error"] == 1


def test_trace_price_table_covers_every_model_in_the_corpus() -> None:
    """If a trace ever carries a model the price table lacks, cost_parts()
    returns None and that turn silently vanishes from every aggregate. Fail
    loudly instead."""
    for name in ("hot_reread_A.jsonl", "cold_rewrite_B.jsonl", "cold_rewrite_C.jsonl"):
        for row in load(name):
            if row["backend"] == "codex":
                continue                    # priced by a different vendor
            assert (row["model"] or "").lower() in LIST_PRICE, row["model"]


def test_ttl_constant_agrees_with_what_the_billing_shows() -> None:
    """`simulate.py` hardcodes a 1-hour TTL. That is not a free parameter — it
    is the value repro.py's oracle 1 derives from the real billing data."""
    assert CACHE_TTL_SECONDS == 3600
    assert CACHE_WRITE_MULTIPLIER_1H == 2.00


def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:                     # noqa: BLE001 - test runner
            failed.append((name, exc))
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
