#!/usr/bin/env python3
"""Two ways a long-context agent session burns money — reconstructed from a
real per-turn usage ledger.

EVIDENCE LEVEL: trace replay.

This file replays the redacted usage traces in `evidence/` and rebuilds both
burn modes from them. Every number it asserts comes out of the traces. It is
an *executable accident reconstruction*, not a live reproduction: nothing here
calls a model, and re-running the original sessions is not possible.

The mechanism *simulation* — fake clock, fake cache, counterfactual session
hygiene — lives in `simulate.py` and is graded separately and lower. The two
are kept in different files on purpose. Nothing in this file is simulated;
nothing in that file is evidence.

    python3 repro.py        # exit 0 = every oracle held

Offline, stdlib only, no account, no network, no API spend, well under a second.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"

# ---------------------------------------------------------------------------
# Published list prices, USD per million tokens, as of 2026-07-20.
#
# Source: Anthropic's published model pricing. These are LIST PRICES. This
# entry never claims they are what anybody was charged — see §3 of README.md.
# The multipliers are the published prompt-caching rates: a cache read costs
# 0.1x the model's input price, and a cache write costs 1.25x for the 5-minute
# TTL or 2x for the 1-hour TTL.
# ---------------------------------------------------------------------------
LIST_PRICE = {                      # model id (lowercased) -> (input, output)
    "claude-opus-4-8": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER_5M = 1.25
CACHE_WRITE_MULTIPLIER_1H = 2.00

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """Machine oracle. Records a failure rather than raising, so one run
    reports every broken assertion instead of only the first."""
    if condition:
        print(f"  PASS  {label}")
        if detail:
            print(f"        {detail}")
    else:
        print(f"  FAIL  {label}")
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def load(name: str) -> list[dict]:
    path = EVIDENCE / name
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def billed(turns: list[dict]) -> list[dict]:
    """Turns that actually consumed tokens.

    The ledger also records rows with every token counter at zero — aborted or
    instantly-failed turns. They are real rows, and dropping them silently
    would flatter every per-turn average in this file, so the entry states the
    count wherever it matters rather than hiding it.
    """
    return [t for t in turns if t["input_tokens"] or t["cache_read_tokens"]
            or t["cache_creation_tokens"] or t["output_tokens"]]


def cost_parts(turn: dict, write_multiplier: float) -> dict[str, float] | None:
    """Decompose one turn's list-price-equivalent cost, or None if the model
    is not in the price table."""
    price = LIST_PRICE.get((turn["model"] or "").lower())
    if price is None:
        return None
    per_in, per_out = price
    return {
        "input": turn["input_tokens"] * per_in / 1e6,
        "cache_read": turn["cache_read_tokens"] * per_in * CACHE_READ_MULTIPLIER / 1e6,
        "cache_write": turn["cache_creation_tokens"] * per_in * write_multiplier / 1e6,
        "output": turn["output_tokens"] * per_out / 1e6,
    }


def when(turn: dict) -> datetime:
    return datetime.fromisoformat(turn["created_at"])


# ===========================================================================
# Oracle 1 — the pricing model reconciles, and identifies the billed cache SKU
# ===========================================================================
def oracle_pricing_model(all_turns: list[dict]) -> None:
    """Every cost figure downstream is a *decomposition* of a provider-reported
    number, not a guess. This oracle earns that right: it shows the published
    list-price formula reproduces the CLI's own reported cost, to within 0.1%,
    on essentially every turn in the corpus.

    It also settles which cache SKU the CLI's cost calculator applies — from
    billing arithmetic rather than from recollection. The 5-minute and 1-hour
    TTLs have different published write prices (1.25x vs 2x input), so the
    reported costs can only match one of them.

    Read that precisely. It identifies THE RATE BEING BILLED, which is a fact
    about the price list. It does NOT establish that any particular cache entry
    survived an hour and then expired — nothing in this ledger observes the
    lifetime of a cache entry at all.
    """
    print("\nOracle 1 — the list-price model reproduces the reported cost")

    def tally(multiplier: float) -> tuple[int, int]:
        matched = missed = 0
        for turn in all_turns:
            reported = turn["cost"]
            if not reported:
                continue
            parts = cost_parts(turn, multiplier)
            if parts is None:
                continue
            if abs(sum(parts.values()) - reported) / reported < 0.001:
                matched += 1
            else:
                missed += 1
        return matched, missed

    hit_1h, miss_1h = tally(CACHE_WRITE_MULTIPLIER_1H)
    hit_5m, miss_5m = tally(CACHE_WRITE_MULTIPLIER_5M)
    total = hit_1h + miss_1h

    check(
        "1-hour-TTL rates reproduce >=90% of reported turn costs to within 0.1%",
        hit_1h / total >= 0.90,
        f"{hit_1h}/{total} turns reconcile exactly; {miss_1h} do not",
    )
    # The load-bearing half: it is not that 1h fits well, it is that 5m fits
    # *nothing*. A model that matched both would identify no SKU at all.
    check(
        "5-minute-TTL rates reproduce NONE of them",
        hit_5m == 0,
        f"{hit_5m}/{total} turns reconcile at the 1.25x write rate",
    )
    check(
        "so the rate being billed is the 1-hour cache SKU",
        hit_1h > 0 and hit_5m == 0,
        "cache writes bill at 2.00x the model's input price, not 1.25x "
        "(a fact about the price list, not about any entry's lifetime)",
    )


# ===========================================================================
# Oracle 2 — mode A, the hot re-read
# ===========================================================================
def oracle_hot_reread(turns: list[dict]) -> None:
    """Mode A: a marathon session where the context is re-sent on every step
    of every turn. The bill is dominated by moving the context around, and
    the model's actual output is a rounding error.
    """
    print("\nOracle 2 — mode A, the hot re-read (session A, 2026-07-09)")

    rows = billed(turns)
    totals = {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0}
    for turn in rows:
        parts = cost_parts(turn, CACHE_WRITE_MULTIPLIER_1H)
        assert parts is not None, "session A must be fully priced"
        for key, value in parts.items():
            totals[key] += value
    grand = sum(totals.values())

    context_share = (totals["input"] + totals["cache_read"] + totals["cache_write"]) / grand
    generation_share = totals["output"] / grand

    cache_read_tokens = sum(t["cache_read_tokens"] for t in rows)
    output_tokens = sum(t["output_tokens"] for t in rows)

    check(
        "context handling is >=90% of the session's cost",
        context_share >= 0.90,
        f"context {context_share:.1%} vs generation {generation_share:.1%} "
        f"(${grand:.2f} total: read ${totals['cache_read']:.2f}, "
        f"write ${totals['cache_write']:.2f}, output ${totals['output']:.2f})",
    )
    check(
        "the session read >=200 cached tokens for every token it produced",
        cache_read_tokens / output_tokens >= 200,
        f"{cache_read_tokens:,} cache-read / {output_tokens:,} output "
        f"= {cache_read_tokens / output_tokens:.1f}x",
    )

    # The step-count floor. Nobody recorded how many tool calls a turn made,
    # and the ledger aggregates a whole turn into one row -- so the number of
    # model calls is not directly observable. But it is *bounded*: a single
    # call cannot read more cached tokens than fit in the context window, so
    # cache_read / context_window is a hard floor on the number of calls.
    # This is the one place the entry can put a number on "~10 tool calls per
    # turn" without taking the operator's word for it.
    worst = max(rows, key=lambda t: t["cache_read_tokens"])
    window = worst["context_window"]
    floor = worst["cache_read_tokens"] / window
    check(
        "at least one turn provably re-read the context >10 times over",
        floor > 10,
        f"a single turn read {worst['cache_read_tokens']:,} cached tokens "
        f"against a {window:,}-token window: >= {floor:.1f} model calls in one turn",
    )


# ===========================================================================
# Oracle 3 — mode B, the cold rewrite
# ===========================================================================
def oracle_cold_rewrite(turns_b: list[dict], turns_c: list[dict]) -> None:
    """Mode B: turns billed for rewriting their context back into the cache,
    at the 2x write rate rather than the 0.1x read rate.

    Read the assertions literally. They establish that the largest rewrite
    FOLLOWED a long idle gap, was billed overwhelmingly for the rewrite, and
    that a second session was billed its own rewrite moments later. They do
    NOT establish that the idle gap CAUSED the rewrite — oracle 4 exists
    specifically to show these traces cannot support that step.
    """
    print("\nOracle 3 — mode B, the cold rewrite (sessions B and C, 2026-07-15)")

    rows = billed(turns_b)
    peak = max(rows, key=lambda t: t["cache_creation_tokens"])
    index = rows.index(peak)
    idle = (when(peak) - when(rows[index - 1])).total_seconds()
    parts = cost_parts(peak, CACHE_WRITE_MULTIPLIER_1H)
    assert parts is not None
    write_share = parts["cache_write"] / sum(parts.values())

    check(
        "the peak turn follows an idle gap longer than the 1-hour cache TTL",
        idle > 3600,
        f"idle {idle / 3600:.1f}h before {peak['created_at'][:19]}",
    )
    check(
        "that single turn rewrote ~1M tokens into the cache",
        peak["cache_creation_tokens"] >= 900_000,
        f"cache_creation = {peak['cache_creation_tokens']:,} tokens against a "
        f"{peak['context_window']:,}-token window",
    )
    check(
        "and it cost >$20, of which >90% is the rewrite alone",
        peak["cost"] > 20 and write_share > 0.90,
        f"provider-reported ${peak['cost']:.4f}; cache-write line "
        f"${parts['cache_write']:.2f} ({write_share:.1%}); "
        f"the model produced {peak['output_tokens']:,} output tokens "
        f"(${parts['output']:.2f}) for it",
    )

    median = statistics.median(t["cost"] for t in rows if t["cost"])
    check(
        "the rewrite turn was billed >10x the session's median turn",
        peak["cost"] / median > 10,
        f"${peak['cost']:.2f} vs median ${median:.2f} = {peak['cost'] / median:.1f}x",
    )

    # One idle period, two sessions, two rewrites. The rewrite is charged
    # per session, so it scales with how many large sessions are alive -- not
    # with how much work was done. (Co-occurrence, not causation: see oracle 4.)
    rows_c = billed(turns_c)
    sibling = min(
        rows_c, key=lambda t: abs((when(t) - when(peak)).total_seconds())
    )
    delta = abs((when(sibling) - when(peak)).total_seconds())
    check(
        "a second session was billed its own rewrite within 2 minutes of the first",
        delta < 120 and sibling["cache_creation_tokens"] > 100_000,
        f"session C rewrote {sibling['cache_creation_tokens']:,} tokens for "
        f"${sibling['cost']:.4f}, {delta:.0f}s after session B's rewrite",
    )


# ===========================================================================
# Oracle 4 — the limits of the causal claim
# ===========================================================================
def oracle_ttl_is_not_a_predictor(traces: dict[str, list[dict]]) -> None:
    """The honest oracle, and the reason this entry is graded the way it is.

    "The session went idle past the TTL, so the cache expired, so the next turn
    rewrote it" is a mechanism story. These traces show a pooled association
    CONSISTENT WITH it — not support for it: turns following a >1h gap rewrite a
    larger share of the CACHE TOKENS THEY TOUCHED than turns following a short
    gap. That ratio is cache_creation / (cache_creation + cache_read) -- a
    property of one turn's cache traffic, NOT the fraction of the session's
    context that was rewritten. The ledger does not contain the session's
    context size at all (README.md 5). Nothing here
    joins the association to the mechanism. And per-turn it fails outright:
    there are long-gap turns that rewrite almost nothing, and short-gap turns
    that rewrite almost everything.

    This oracle asserts BOTH halves, so the entry's prose cannot quietly grow
    into "idle time causes rewrites" — a claim these traces cannot carry.
    Only claude-code turns are counted: the codex backend reports
    cache_creation_tokens as 0 on every turn, so including it would fabricate
    a population of long-gap turns that "prove" no rewrite ever happens.
    """
    print("\nOracle 4 — the association is descriptive, and it is not per-turn")

    long_gap: list[float] = []
    short_gap: list[float] = []
    for turns in traces.values():
        rows = [t for t in billed(turns) if t["backend"] == "claude-code"]
        for previous, current in zip(rows, rows[1:]):
            touched = current["cache_read_tokens"] + current["cache_creation_tokens"]
            if not touched:
                continue
            share = current["cache_creation_tokens"] / touched
            gap = (when(current) - when(previous)).total_seconds()
            (long_gap if gap > 3600 else short_gap).append(share)

    median_long = statistics.median(long_gap)
    median_short = statistics.median(short_gap)

    check(
        "pooled: the median >1h-gap turn writes a far larger share of the cache "
        "tokens it touched",
        median_long > median_short * 5,
        f"median cache-write share {median_long:.1%} (n={len(long_gap)}) after a long gap "
        f"vs {median_short:.1%} (n={len(short_gap)}) after a short one "
        f"= {median_long / median_short:.0f}x",
    )
    check(
        "BUT some >1h-gap turns write almost none of it (not sufficient)",
        min(long_gap) < 0.05,
        f"lowest cache-write share after a long gap: {min(long_gap):.1%} — "
        "so exceeding the TTL does not guarantee a rewrite",
    )
    check(
        "AND some short-gap turns write almost all of it (not necessary)",
        max(short_gap) > 0.50,
        f"highest cache-write share after a <1h gap: {max(short_gap):.1%} — "
        "so a rewrite spike does not imply the session was idle",
    )
    check(
        "therefore idle-past-TTL is neither necessary nor sufficient for a spike",
        min(long_gap) < 0.05 and max(short_gap) > 0.50,
        "the entry reports the association and labels the per-turn causal "
        "claim as undetermined",
    )


def main() -> int:
    traces = {
        "A": load("hot_reread_A.jsonl"),
        "B": load("cold_rewrite_B.jsonl"),
        "C": load("cold_rewrite_C.jsonl"),
        # A real codex session, included so oracle 4's exclusion has something
        # to exclude. Its every turn reports cache_creation_tokens as 0.
        "D": load("codex_no_cache_field_D.jsonl"),
    }
    every_turn = [t for turns in traces.values() for t in turns]

    print(f"Replaying {len(every_turn)} redacted usage records from evidence/")
    print("(usage counters and timestamps only — the ledger holds no message text)")

    oracle_pricing_model(every_turn)
    oracle_hot_reread(traces["A"])
    oracle_cold_rewrite(traces["B"], traces["C"])
    oracle_ttl_is_not_a_predictor(traces)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} oracle(s) FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("All oracles held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
