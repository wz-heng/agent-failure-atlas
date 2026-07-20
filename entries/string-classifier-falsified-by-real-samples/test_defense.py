"""Regression tests for the defense — the structural classifier.

    python3 -m pytest test_defense.py -q      # or: python3 test_defense.py

These are the tests the production fix shipped with, reduced to this entry's
self-contained classifier. They exist because the defense has exactly one job:
keep the two dispositions disjoint on real samples of BOTH classes. A defense
without a regression test is a defense that silently rots the next time a
vendor changes a message.

Runs offline in milliseconds. Imports nothing but `repro`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from repro import (
    EVIDENCE,
    classify_by_pattern,
    classify_structurally,
    read_stream,
    reset_epoch,
)

USER_LIMIT = "claude_user_limit_5h.jsonl"
SERVER_429 = "claude_server_429.jsonl"
CODEX_LIMIT = "codex_user_limit.jsonl"
CODEX_429 = "codex_server_429.jsonl"


def load(filename):
    return read_stream(EVIDENCE / filename)


# ------------------------------------------------------- the captured corpus

def test_claude_user_limit_parks():
    """The real 5-hour limit: the CLI names the exhausted window, so we park."""
    text, info = load(USER_LIMIT)
    assert classify_structurally(text, info) == "park"
    assert info["rateLimitType"] == "five_hour"


def test_claude_server_429_retries():
    """THE bug this design exists to avoid. Same HTTP 429, same "rate limit"
    prose, opposite disposition: a seconds-scale blip must not park for hours."""
    text, info = load(SERVER_429)
    assert classify_structurally(text, info) == "retry"


def test_codex_user_limit_parks():
    text, info = load(CODEX_LIMIT)
    assert classify_structurally(text, info) == "park"


def test_codex_server_429_retries():
    text, info = load(CODEX_429)
    assert classify_structurally(text, info) == "retry"


def test_dispositions_are_disjoint_on_every_sample():
    """No sample may be claimed by both dispositions, and none may be unclaimed.
    This is the property the whole design rests on."""
    for filename in (USER_LIMIT, SERVER_429, CODEX_LIMIT, CODEX_429):
        text, info = load(filename)
        verdict = classify_structurally(text, info)
        assert verdict in ("park", "retry"), filename


# ------------------------------------------- the properties behind the corpus

def test_reset_epoch_comes_from_the_structured_field_not_the_prose():
    """The prose says "resets 10:22pm (Asia/Shanghai)" — a rendered, localized
    string. The epoch is taken from `resetsAt` instead, verbatim."""
    _, info = load(USER_LIMIT)
    assert reset_epoch(info) == 1784038967


def test_rejection_without_a_window_claim_is_not_a_limit():
    """The server-throttle SHAPE, stated as a property rather than a sample: a
    rejection that claims no window must fall through to retry, whatever its
    prose says."""
    assert classify_structurally(
        "API Error: Request rejected (429)",
        {"status": "rejected", "resetsAt": 1784038586},
    ) == "retry"


def test_approaching_the_limit_does_not_park():
    """`allowed_warning` rides along on turns that SUCCEEDED. Parking on it
    would suspend a session that has quota left."""
    assert classify_structurally(
        "",
        {"status": "allowed_warning", "rateLimitType": "five_hour",
         "resetsAt": 1784038967},
    ) == "retry"


def test_a_limit_without_an_epoch_still_parks():
    """Detection and scheduling are separate concerns: a hit with no epoch is
    still a hit — it degrades to probing, it does not degrade to 'not a limit'."""
    info = {"status": "rejected", "rateLimitType": "five_hour"}
    assert classify_structurally("", info) == "park"
    assert reset_epoch(info) is None


def test_the_negation_trap():
    """The throttle message contains the phrase "usage limit" only inside
    "(not your usage limit)". Substring matching cannot see negation; the
    structural classifier never looks at the prose in the first place."""
    text, info = load(SERVER_429)
    assert "not your usage limit" in text.lower()
    assert classify_by_pattern(text, info) == "park"       # the old bug, pinned
    assert classify_structurally(text, info) == "retry"    # the fix


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
