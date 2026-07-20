#!/usr/bin/env python3
"""Mechanism simulation: a fake clock and a fake TTL cache, billed at list price.

EVIDENCE LEVEL: mechanism simulation. READ THIS BEFORE QUOTING ANY NUMBER
FROM THIS FILE.

Nothing here is a measurement. The workload below is invented — I chose the
task count, the step count, the context growth, and the idle gaps. Change any
of them and every number this file prints changes with it. It demonstrates
that the *mechanism* has the shape the entry claims; it does not establish
that any particular session cost any particular amount.

The measured half of this entry is `repro.py`, which replays real ledger
traces and never guesses at anything. Numbers from that file and numbers from
this one must never appear in the same table.

What the simulation is for: the counterfactual the traces cannot supply. The
ledger records what the marathon sessions cost. It cannot record what the same
work would have cost under different session hygiene, because that run never
happened. A fake clock and a fake cache can run both arms.

    python3 simulate.py     # exit 0 = every oracle held

Offline, stdlib only, no account, no network, no API spend, well under a second.
"""

from __future__ import annotations

import sys

from repro import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER_1H,
    LIST_PRICE,
    check,
    FAILURES,
)

# The one-hour TTL is not a free parameter — `repro.py` oracle 1 identifies it
# from the real billing data. Everything else in this file is a choice.
CACHE_TTL_SECONDS = 3600

MODEL = "claude-opus-4-8"
CONTEXT_WINDOW = 1_000_000


class FakeClock:
    """Monotonic simulated time. No wall-clock dependency, so the run is
    deterministic and cannot be made flaky by a slow machine."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCache:
    """A prompt cache with a TTL, modelled at the only resolution that matters
    for billing: how many tokens of the prefix are still warm when a request
    arrives.

    Deliberately simplified. A real prompt cache is a *prefix* match with
    breakpoints, eviction, and a minimum cacheable size; this models none of
    that. It models exactly one thing — that a warm prefix bills at the read
    rate and a cold one bills at the write rate — because that is the only
    mechanism the entry claims.
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.warm_tokens = 0
        self.expires_at = 0.0

    def warm(self) -> int:
        if self.clock.now >= self.expires_at:
            self.warm_tokens = 0
        return self.warm_tokens

    def store(self, tokens: int) -> None:
        self.warm_tokens = tokens
        self.expires_at = self.clock.now + CACHE_TTL_SECONDS


class Meter:
    """List-price billing, using the same rate table as the trace replay."""

    def __init__(self) -> None:
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.output_tokens = 0

    def charge(self, read: int, write: int, output: int) -> None:
        self.cache_read_tokens += read
        self.cache_write_tokens += write
        self.output_tokens += output

    @property
    def cost(self) -> float:
        per_in, per_out = LIST_PRICE[MODEL]
        return (
            self.cache_read_tokens * per_in * CACHE_READ_MULTIPLIER
            + self.cache_write_tokens * per_in * CACHE_WRITE_MULTIPLIER_1H
            + self.output_tokens * per_out
        ) / 1e6


# --- the invented workload ------------------------------------------------
# Sixteen tasks. Each is one turn of ten tool-calling steps, and each leaves
# 50k tokens of transcript behind. Between tasks the operator wanders off for
# ninety minutes — past the TTL, which is the whole point of the exercise.
#
# Sized so the marathon arm ends near, but never above, the context window:
# 30k + 16 x 50k = 830k of a 1M window. Above the window a real harness would
# compact, which is a different mechanism this file does not model — so the
# workload deliberately stops short of it rather than simulating something
# whose behaviour I would be inventing.
TASK_COUNT = 16
STEPS_PER_TURN = 10
CONTEXT_ADDED_PER_TASK = 50_000
BASE_CONTEXT = 30_000          # system prompt, tools, project files
OUTPUT_PER_TASK = 4_000
IDLE_BETWEEN_TASKS = 90 * 60


def run_session_strategy(retire_at_tokens: int | None) -> tuple[Meter, int, int]:
    """Run the same ten tasks under one session-hygiene policy.

    `retire_at_tokens=None` is the marathon: one session, forever, context
    growing monotonically. A number is the defended policy: when the context
    crosses that watermark, wrap up and start the next task in a fresh session.

    Returns (meter, sessions_opened, peak_context).
    """
    clock = FakeClock()
    cache = FakeCache(clock)
    meter = Meter()
    context = BASE_CONTEXT
    sessions = 1
    peak = context

    for task in range(TASK_COUNT):
        if task:
            clock.advance(IDLE_BETWEEN_TASKS)

        if retire_at_tokens is not None and context >= retire_at_tokens:
            # Retire the session. A fresh one starts from the base context;
            # the accumulated transcript is gone, and so is its cache.
            context = BASE_CONTEXT
            cache = FakeCache(clock)
            sessions += 1

        # One turn = STEPS_PER_TURN model calls, each re-sending the whole
        # context. The first call after an idle gap finds a cold cache and
        # pays the write rate; the rest are warm reads.
        for _ in range(STEPS_PER_TURN):
            warm = cache.warm()
            if warm >= context:
                meter.charge(read=context, write=0, output=0)
            else:
                meter.charge(read=warm, write=context - warm, output=0)
                cache.store(context)
            clock.advance(20)          # a step takes 20 simulated seconds

        meter.charge(read=0, write=0, output=OUTPUT_PER_TASK)
        context += CONTEXT_ADDED_PER_TASK
        peak = max(peak, context)
        cache.store(context)

    return meter, sessions, peak


# --- the defense ----------------------------------------------------------
def should_retire_session(context_tokens: int, window: int, threshold: float = 0.50) -> bool:
    """The defense Owlery does not implement (README.md §5).

    A context watermark: once a session's context passes `threshold` of the
    model's window, it should be wrapped up rather than handed the next task.
    Cheap, needs no knowledge of what the session is doing, and is computable
    from a number the CLI already reports on every single turn.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    return context_tokens >= window * threshold


def main() -> int:
    print("=" * 72)
    print("MECHANISM SIMULATION — invented workload, not a measurement.")
    print("Numbers below depend entirely on parameters I chose. They show the")
    print("mechanism's shape. They are not evidence about any real session.")
    print("=" * 72)

    print(f"\nWorkload: {TASK_COUNT} tasks x {STEPS_PER_TURN} steps, "
          f"+{CONTEXT_ADDED_PER_TASK:,} context tokens each,")
    print(f"          {IDLE_BETWEEN_TASKS // 60} min idle between tasks "
          f"(TTL is {CACHE_TTL_SECONDS // 60} min)")

    marathon, marathon_sessions, marathon_peak = run_session_strategy(None)
    hygienic, hygienic_sessions, hygienic_peak = run_session_strategy(
        int(CONTEXT_WINDOW * 0.50)
    )

    for name, meter, sessions, peak in (
        ("marathon (one session)", marathon, marathon_sessions, marathon_peak),
        ("watermark at 50% of window", hygienic, hygienic_sessions, hygienic_peak),
    ):
        print(f"\n  {name}")
        print(f"    sessions opened : {sessions}")
        print(f"    peak context    : {peak:,} tokens")
        print(f"    cache read      : {meter.cache_read_tokens:>12,} tokens")
        print(f"    cache write     : {meter.cache_write_tokens:>12,} tokens")
        print(f"    output          : {meter.output_tokens:>12,} tokens")
        print(f"    list-price cost : ${meter.cost:,.2f}")

    print("\nOracles")

    check(
        "the marathon spends more than the watermarked run on identical work",
        marathon.cost > hygienic.cost,
        f"${marathon.cost:,.2f} vs ${hygienic.cost:,.2f} "
        f"= {marathon.cost / hygienic.cost:.2f}x for the same {TASK_COUNT} tasks "
        f"and identical {marathon.output_tokens:,} output tokens",
    )
    check(
        "both runs produced exactly the same output — only context handling differs",
        marathon.output_tokens == hygienic.output_tokens,
        f"{marathon.output_tokens:,} output tokens either way; the difference is "
        f"{marathon.cache_read_tokens + marathon.cache_write_tokens:,} vs "
        f"{hygienic.cache_read_tokens + hygienic.cache_write_tokens:,} context tokens moved",
    )
    check(
        "in the marathon, context handling dwarfs generation",
        (marathon.cost - (marathon.output_tokens * LIST_PRICE[MODEL][1] / 1e6))
        / marathon.cost > 0.90,
        "the same shape oracle 2 measures in the real session-A trace",
    )

    # The wakeup tax is per-idle-gap, so it scales with how many times you
    # come back -- not with how much work you do.
    lonely, _, _ = run_session_strategy(None)
    print()
    check(
        "every task after the first pays a cold-start write in the marathon",
        marathon.cache_write_tokens > BASE_CONTEXT * TASK_COUNT,
        f"{marathon.cache_write_tokens:,} tokens rewritten across "
        f"{TASK_COUNT} wakeups",
    )
    check(
        "the watermark defense fires exactly where it is told to",
        should_retire_session(500_000, 1_000_000)
        and not should_retire_session(499_999, 1_000_000),
        "50% of a 1M window; see test_defense.py for the full regression set",
    )
    assert lonely.cost == marathon.cost, "simulation must be deterministic"

    print()
    if FAILURES:
        print(f"{len(FAILURES)} oracle(s) FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("All simulation oracles held. (Simulation — see the banner above.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
