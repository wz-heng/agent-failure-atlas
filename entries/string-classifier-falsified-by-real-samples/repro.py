#!/usr/bin/env python3
"""Minimal case: a string-pattern usage-limit classifier, falsified by four real
CLI samples; a structural classifier that survives them.

Offline. No account, no network, no API spend. Runs in well under a second.
Self-contained: both classifiers are reimplemented here from the shipped
production logic — this file imports nothing from the system it came from.

    python3 repro.py          # exit 0 = every oracle held

Three oracles:

  1. FALSIFICATION   The pattern classifier answers "usage limit" to all four
                     samples, including the two server-side throttles. Two
                     false positives; the discriminator carries no information.
  2. IMPOSSIBILITY   Stronger than (1), and the reason (1) is not a matter of
                     picking better patterns: every rate-limit vocabulary token
                     present in the claude user-limit sample is ALSO present in
                     the claude server-429 sample. The set difference is empty,
                     so NO substring classifier over that vocabulary can
                     separate the pair — in either direction.
  3. DEFENSE         The structural classifier answers correctly on all four,
                     and recovers the reset epoch verbatim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVIDENCE = Path(__file__).parent / "evidence"

# The four captured samples and the disposition each one REQUIRES.
# "park" = the user's own quota is exhausted; wait hours for the window, then
# resume once. "retry" = a server-side throttle; back off seconds and re-send.
# Getting these backwards is the failure this entry documents.
SAMPLES = [
    ("claude", "claude_user_limit_5h.jsonl", "park"),
    ("claude", "claude_server_429.jsonl", "retry"),
    ("codex", "codex_user_limit.jsonl", "park"),
    ("codex", "codex_server_429.jsonl", "retry"),
]


# --------------------------------------------------------------------------
# Reading a captured stream the way a harness would
# --------------------------------------------------------------------------

def read_stream(path: Path) -> tuple[str, dict | None]:
    """Return (error_text, rate_limit_info) from a captured CLI stream.

    error_text is what a consumer sees when a turn fails: the rendered message
    plus the raw terminal event, so the HTTP status the CLI recorded
    (`api_error_status`) is in scope for a pattern matcher — being generous to
    the pattern approach, not stingy.

    rate_limit_info is claude's dedicated `rate_limit_event` payload. Note the
    production bug this exposed: the parser originally DISCARDED this record as
    unrenderable, which is precisely why detection got pushed onto prose.
    """
    error_text = ""
    rate_limit_info = None

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        kind = obj.get("type")

        if kind == "rate_limit_event":
            rate_limit_info = obj.get("rate_limit_info")

        # claude: the terminal `result` record carries is_error + the message.
        elif kind == "result" and obj.get("is_error"):
            error_text += (obj.get("result") or "") + "\n" + json.dumps(obj) + "\n"

        # codex: a bare `error` record, echoed by `turn.failed`.
        elif kind == "error":
            error_text += (obj.get("message") or "") + "\n" + json.dumps(obj) + "\n"
        elif kind == "turn.failed":
            msg = (obj.get("error") or {}).get("message") or ""
            error_text += msg + "\n" + json.dumps(obj) + "\n"

    return error_text, rate_limit_info


# --------------------------------------------------------------------------
# The classifier that was falsified
# --------------------------------------------------------------------------

# The vocabulary the original design named: "the bare 'rate limit' / '429' /
# 'quota' tokens", plus the phrase any engineer would reach for first.
USAGE_LIMIT_PATTERNS = ("rate limit", "429", "quota", "usage limit")


def classify_by_pattern(error_text: str, rate_limit_info: dict | None) -> str:
    """Disposition by substring match — the design that lost.

    Signature takes rate_limit_info and ignores it on purpose: this is the
    classifier as it was actually proposed, before anyone knew the structured
    record existed.
    """
    haystack = error_text.lower()
    if any(p in haystack for p in USAGE_LIMIT_PATTERNS):
        return "park"
    return "retry"


# --------------------------------------------------------------------------
# The classifier that shipped
# --------------------------------------------------------------------------

# Codex's user-limit phrase. This IS a string match, and pretending otherwise
# would be dishonest — see the entry's "Discrimination and defense" section.
# What makes it sound is not its form but its evidence: codex's server-side
# throttle message provably does not contain it, and there is a captured
# sample of that class proving so.
CODEX_USAGE_LIMIT_MARKER = "you've hit your usage limit"


def classify_structurally(error_text: str, rate_limit_info: dict | None) -> str:
    """Disposition by structured field — the design that survived.

    claude: the discriminator is not the status code and not the prose, but
    whether the CLI's `rate_limit_event` CLAIMS A WINDOW. A user-limit rejection
    names the exhausted window (`rateLimitType`, e.g. "five_hour") and carries
    the epoch it reopens. A server-side throttle is rejected with no claim on
    any window at all — same 429, same "rate limit" prose, no `rateLimitType`.

    codex: no structured limit state reaches stdout, so classification keys on a
    phrase the throttle message does not share.
    """
    if isinstance(rate_limit_info, dict):
        if rate_limit_info.get("status") == "rejected":
            window = rate_limit_info.get("rateLimitType")
            if isinstance(window, str) and window:
                return "park"
        # Rejected with no window claim, or a non-rejection ("allowed_warning",
        # i.e. approaching the limit on a turn that SUCCEEDED): not a park.
        return "retry"

    if CODEX_USAGE_LIMIT_MARKER in error_text.lower():
        return "park"
    return "retry"


def reset_epoch(rate_limit_info: dict | None) -> int | None:
    """The epoch the exhausted window reopens — taken from the structured field,
    never parsed back out of the rendered local time in the prose."""
    if not isinstance(rate_limit_info, dict):
        return None
    value = rate_limit_info.get("resetsAt")
    return int(value) if isinstance(value, (int, float)) else None


# --------------------------------------------------------------------------
# Oracles
# --------------------------------------------------------------------------

def load_all():
    streams = {}
    for _, filename, expected in SAMPLES:
        path = EVIDENCE / filename
        if not path.exists():
            raise SystemExit(f"missing evidence file: {path}")
        streams[filename] = (*read_stream(path), expected)
    return streams


def oracle_falsification(streams) -> list[str]:
    """The pattern classifier's verdicts, asserted exactly."""
    failures = []
    verdicts = {}
    for _, filename, expected in SAMPLES:
        error_text, info, _ = streams[filename]
        verdicts[filename] = classify_by_pattern(error_text, info)

    # Asserting the SPECIFIC wrong answers, not merely "something was wrong":
    # a weaker assertion would still pass if the classifier broke differently.
    expected_verdicts = {
        "claude_user_limit_5h.jsonl": "park",   # right, but only via "429"
        "claude_server_429.jsonl": "park",      # WRONG — a seconds-scale blip
        "codex_user_limit.jsonl": "park",       # right
        "codex_server_429.jsonl": "park",       # WRONG — a seconds-scale blip
    }
    if verdicts != expected_verdicts:
        failures.append(
            f"pattern classifier did not behave as recorded:\n"
            f"    expected {expected_verdicts}\n    got      {verdicts}"
        )

    wrong = [
        filename
        for _, filename, expected in SAMPLES
        if verdicts[filename] != expected
    ]
    if len(wrong) != 2:
        failures.append(f"expected exactly 2 misclassifications, got {wrong}")

    # Note the labels: "MISCLASSIFIED" here is the RECORDED, EXPECTED behaviour
    # of the falsified design. This oracle fails only if reality departs from
    # what the entry claims — including if the pattern classifier suddenly got
    # these right.
    print("  [1] falsification")
    for _, filename, expected in SAMPLES:
        got = verdicts[filename]
        mark = "correct      " if got == expected else "MISCLASSIFIED"
        print(f"      {mark}  {filename:<28} want={expected:<5} got={got}")
    print(
        "      -> answers 'park' to everything: a classifier with no "
        "discriminating power."
    )
    return failures


def oracle_impossibility(streams) -> list[str]:
    """No substring classifier over rate-limit vocabulary can split the pair.

    A substring classifier says "park" iff at least one of its patterns occurs
    in the text. To be right on BOTH claude samples it needs a pattern that
    occurs in the user-limit text and NOT in the server-429 text. This checks
    whether any such pattern exists in the vocabulary. It does not.
    """
    vocabulary = (
        "rate limit", "rate-limit", "rate_limit", "ratelimit",
        "429", "too many requests",
        "quota", "usage limit", "limit reached", "limit exceeded",
        "exceeded", "limit", "throttl", "capacity",
    )
    user_text = streams["claude_user_limit_5h.jsonl"][0].lower()
    throttle_text = streams["claude_server_429.jsonl"][0].lower()

    in_user = {t for t in vocabulary if t in user_text}
    in_throttle = {t for t in vocabulary if t in throttle_text}
    separating = in_user - in_throttle

    print("  [2] impossibility")
    print(f"      vocabulary in user-limit text : {sorted(in_user)}")
    print(f"      vocabulary in server-429 text : {sorted(in_throttle)}")
    print(f"      tokens unique to user-limit   : {sorted(separating)}")

    failures = []
    if separating:
        failures.append(
            "a rate-limit token DOES separate the claude pair "
            f"({sorted(separating)}) — the impossibility claim is overstated "
            "and the entry's evidence section must be corrected."
        )
    else:
        print(
            "      -> empty. Every rate-limit token in the user-limit sample is\n"
            "         also in the throttle sample, so no substring classifier\n"
            "         over this vocabulary separates them. The only strings that\n"
            "         would are incidental localized prose ('session limit',\n"
            "         'resets 10:22pm'), which is rendered output, not contract."
        )

    # And the converse trap: the throttle text contains "usage limit" only
    # inside the NEGATION "(not your usage limit)". A matcher reading the most
    # specific-looking phrase gets the answer exactly backwards.
    if "not your usage limit" not in throttle_text:
        failures.append(
            "the server-429 sample no longer contains the '(not your usage "
            "limit)' negation the entry cites"
        )
    else:
        print(
            "      -> and the throttle text contains 'usage limit' only inside\n"
            "         '(not your usage limit)': the most specific-looking phrase\n"
            "         appears in the sample where it means the opposite."
        )
    return failures


def oracle_defense(streams) -> list[str]:
    """The structural classifier is right on all four, and recovers the epoch."""
    failures = []
    print("  [3] defense")
    for _, filename, expected in SAMPLES:
        error_text, info, _ = streams[filename]
        got = classify_structurally(error_text, info)
        mark = "ok " if got == expected else "FAIL"
        print(f"      {mark}  {filename:<28} want={expected:<5} got={got}")
        if got != expected:
            failures.append(f"structural classifier got {filename} wrong: {got}")

    epoch = reset_epoch(streams["claude_user_limit_5h.jsonl"][1])
    if epoch != 1784038967:
        failures.append(f"reset epoch not recovered verbatim: {epoch}")
    else:
        print(f"      ok   reset epoch recovered verbatim: {epoch}")
    return failures


def main() -> int:
    streams = load_all()
    print(f"replaying {len(SAMPLES)} captured samples from {EVIDENCE.name}/\n")

    failures = []
    failures += oracle_falsification(streams)
    print()
    failures += oracle_impossibility(streams)
    print()
    failures += oracle_defense(streams)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all oracles held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
