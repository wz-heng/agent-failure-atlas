"""Regression tests for the defense — the classifier Owlery shipped.

    python3 -m pytest test_defense.py -q      # or: python3 test_defense.py

These mirror the tests the production fix shipped with (Owlery's
tests/test_limit_auto_resume.py @ 858924d), reduced to this entry's
self-contained classifier. They exist because the defense has exactly one job:
keep the dispositions disjoint on real samples of BOTH classes. A defense
without a regression test is a defense that silently rots the next time a
vendor changes a message.

Runs offline in milliseconds. Imports nothing but `repro`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from repro import (
    CLAUDE_TRANSIENT_PATTERNS,
    CODEX_TRANSIENT_PATTERNS,
    CODEX_USAGE_LIMIT_MARKER,
    EVIDENCE,
    classify_as_shipped,
    classify_by_generic_vocabulary,
    classify_claude,
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
    assert classify_as_shipped("claude", text, info) == "park"
    assert info["rateLimitType"] == "five_hour"


def test_claude_server_429_retries():
    """THE bug this design exists to avoid. Same HTTP 429, same "rate limit"
    prose, opposite disposition: a seconds-scale blip must not park for hours."""
    text, info = load(SERVER_429)
    assert classify_as_shipped("claude", text, info) == "retry"


def test_codex_user_limit_parks():
    text, info = load(CODEX_LIMIT)
    assert classify_as_shipped("codex", text, info) == "park"


def test_codex_server_429_surfaces_rather_than_retrying():
    """Codex's server-side 429 matches NO transient pattern, so it surfaces
    as-is rather than being retried. Pinned against Owlery's own
    test_dispositions_are_mutually_exclusive @ 858924d, which asserts
    (expect_limit, expect_transient) == (False, False) for this sample."""
    text, info = load(CODEX_429)
    assert classify_as_shipped("codex", text, info) == "surface"


# --------------------------------------------------------------- disjointness

def _predicates(backend, text, info):
    """Compute each disposition's predicate INDEPENDENTLY, the way the real
    harness does (classify_usage_limit / is_transient_error are separate calls
    over the same failure). Asserting on independently-computed predicates is
    the point: a single function returning one label can never overlap with
    itself, so testing that would be vacuous."""
    haystack = text.lower()
    if backend == "claude":
        is_limit = classify_claude(text, info) == "park"
        is_transient = any(p in haystack for p in CLAUDE_TRANSIENT_PATTERNS)
    else:
        is_limit = CODEX_USAGE_LIMIT_MARKER in haystack
        is_transient = any(p in haystack for p in CODEX_TRANSIENT_PATTERNS)
    return is_limit, is_transient


def test_dispositions_never_double_claim_a_sample():
    """The property the whole design rests on: no sample is claimed by both the
    limit classifier and the transient classifier. Both predicates are computed
    separately, so this can actually fail."""
    for backend, filename in (
        ("claude", USER_LIMIT), ("claude", SERVER_429),
        ("codex", CODEX_LIMIT), ("codex", CODEX_429),
    ):
        text, info = load(filename)
        is_limit, is_transient = _predicates(backend, text, info)
        assert not (is_limit and is_transient), f"{filename} claimed by both"


def test_each_sample_gets_the_disposition_owlery_gives_it():
    """The exact (is_limit, is_transient) matrix Owlery pins @ 858924d."""
    expected = {
        (USER_LIMIT, "claude"): (True, False),
        (SERVER_429, "claude"): (False, True),
        (CODEX_LIMIT, "codex"): (True, False),
        (CODEX_429, "codex"): (False, False),   # -> surfaces as-is
    }
    for (filename, backend), want in expected.items():
        text, info = load(filename)
        assert _predicates(backend, text, info) == want, filename


# ------------------------------------------- the properties behind the corpus

def test_reset_epoch_comes_from_the_structured_field_not_the_prose():
    """The prose says "resets 10:22pm (Asia/Shanghai)" — a rendered, localized
    string. The epoch is taken from `resetsAt` instead, verbatim."""
    _, info = load(USER_LIMIT)
    assert reset_epoch(info) == 1784038967


def test_rejection_without_a_window_claim_is_not_a_limit():
    """The server-throttle SHAPE, stated as a property rather than a sample: a
    rejection that claims no window must not park, whatever its prose says."""
    assert classify_claude(
        "API Error: Request rejected (429)",
        {"status": "rejected", "resetsAt": 1784038586},
    ) != "park"


def test_approaching_the_limit_does_not_park():
    """`allowed_warning` rides along on turns that SUCCEEDED. Parking on it
    would suspend a session that has quota left."""
    assert classify_claude(
        "",
        {"status": "allowed_warning", "rateLimitType": "five_hour",
         "resetsAt": 1784038967},
    ) != "park"


def test_a_limit_without_an_epoch_still_parks():
    """Detection and scheduling are separate concerns: a hit with no epoch is
    still a hit — it degrades to probing, not to 'not a limit'."""
    info = {"status": "rejected", "rateLimitType": "five_hour"}
    assert classify_claude("", info) == "park"
    assert reset_epoch(info) is None


def test_the_negation_trap():
    """The throttle message contains "usage limit" only inside "(not your usage
    limit)". Generic vocabulary matching cannot see negation. The shipped
    classifier reads the structured field instead — and its transient set
    matches that exact phrase deliberately, to claim the turn for retry."""
    text, info = load(SERVER_429)
    assert "not your usage limit" in text.lower()
    assert classify_by_generic_vocabulary(text, info) == "park"   # the trap
    assert classify_as_shipped("claude", text, info) == "retry"   # the fix


def test_the_shipped_transient_set_avoids_bare_rate_limit_tokens():
    """Owlery's transient patterns deliberately exclude the generic tokens.
    If someone ever adds a bare "rate limit" / "429" / "quota" to them, the
    disjointness argument collapses — so pin it."""
    for patterns in (CLAUDE_TRANSIENT_PATTERNS, CODEX_TRANSIENT_PATTERNS):
        for banned in ("rate limit", "429", "quota"):
            assert banned not in patterns


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
