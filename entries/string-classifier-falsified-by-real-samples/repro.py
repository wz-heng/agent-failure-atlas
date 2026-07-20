#!/usr/bin/env python3
"""Minimal case: why HTTP status + generic rate-limit vocabulary is not a usable
discriminator for usage-limit detection, and what Owlery shipped instead.

Offline. No account, no network, no API spend. Runs in well under a second.
Self-contained: imports nothing from Owlery. Standard library only.

    python3 repro.py          # exit 0 = every oracle held

WHAT IS AND IS NOT CLAIMED HERE
-------------------------------
Claimed: the HTTP status code is not a usable discriminator — it is 429 on BOTH
sides of the claude pair, and absent entirely from codex's real usage limit —
and the one fixed vocabulary in oracle 2 ("rate limit", "429", "quota", "usage
limit") misclassifies these traces.

NOT claimed: that substring matching cannot work here, or that no vocabulary
could be found that does. One can — oracle 3 exhibits a hand-tuned pair of
substrings that classifies all four traces correctly. The case against prose
matching is FRAGILITY: those strings are localized, rendered output that changes
with locale, plan tier and release. An earlier revision of this entry claimed
impossibility and shipped a "proof" whose vocabulary had been chosen to exclude
the counterexample; that overreach is documented in the entry README.

The three-way disposition below mirrors Owlery's real failed-turn taxonomy
(harness-transient-retry.md §2 + limit-auto-resume.md §2), not a simplification:

  park     the user's own quota window is exhausted; persist the turn, wake at
           the reset epoch, resume once. Hours.
  retry    a provider-reliability blip; exponential backoff. Seconds.
  surface  anything else; report it and stop. No automatic recovery.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVIDENCE = Path(__file__).parent / "evidence"

# The four captured samples and the disposition Owlery actually gives each one.
# Note codex's server-side 429: it is NOT retried. It matches no transient
# pattern, so it surfaces as-is. Verified against Owlery's own
# tests/test_limit_auto_resume.py::test_dispositions_are_mutually_exclusive
# @ 858924d, which pins (expect_limit, expect_transient) = (False, False).
SAMPLES = [
    ("claude", "claude_user_limit_5h.jsonl", "park"),
    ("claude", "claude_server_429.jsonl", "retry"),
    ("codex", "codex_user_limit.jsonl", "park"),
    ("codex", "codex_server_429.jsonl", "surface"),
]


# --------------------------------------------------------------------------
# Reading a captured stream the way a harness would
# --------------------------------------------------------------------------

def read_stream(path: Path) -> tuple[str, dict | None]:
    """Return (error_text, rate_limit_info) from a captured CLI stream.

    error_text is what a consumer sees when a turn fails: the rendered message
    plus the raw terminal event, so the HTTP status the CLI recorded
    (`api_error_status`) is in scope for a text matcher — being generous to the
    text-matching approach, not stingy.

    rate_limit_info is claude's dedicated `rate_limit_event` payload. Note the
    production bug this exposed: Owlery's parser originally DISCARDED this
    record as unrenderable, which is why detection had nothing but prose to key
    on in the first place.
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
# ILLUSTRATIVE: the tempting approach, and why it fails
# --------------------------------------------------------------------------
# IMPORTANT — this classifier is ILLUSTRATIVE, NOT a reimplementation of
# anything Owlery shipped. Owlery never matched on bare "rate limit" / "429" /
# "quota"; its pattern sets deliberately excluded those tokens from the start,
# and its plan required deriving patterns from captured samples rather than
# from folklore. What this demonstrates is the strategy a reasonable engineer
# reaches for BEFORE seeing samples of both classes — and what the samples do
# to it.

GENERIC_LIMIT_VOCABULARY = ("rate limit", "429", "quota", "usage limit")


def classify_by_generic_vocabulary(error_text: str, info: dict | None) -> str:
    """Disposition by generic rate-limit vocabulary. Illustrative only."""
    haystack = error_text.lower()
    if any(p in haystack for p in GENERIC_LIMIT_VOCABULARY):
        return "park"
    return "surface"


# --------------------------------------------------------------------------
# The classifier Owlery shipped
# --------------------------------------------------------------------------
# Transcribed from server/harness/{claude_code,codex}.py @ 858924d. Per-backend,
# because the two CLIs genuinely do not offer the same footing.

# claude's server-side throttle annotates itself. Owlery matches THESE specific
# phrases rather than a bare "rate limit", precisely because the bare token is
# not safe. Trimmed to the entries relevant to these samples; the shipped tuple
# also carries the usual 5xx / connection-failure phrases.
CLAUDE_TRANSIENT_PATTERNS = (
    "temporarily limiting requests",
    "not your usage limit",
    "overloaded",
    "api error: 500", "api error: 502", "api error: 503",
    "api error: 504", "api error: 529",
    "internal server error", "service unavailable",
    "bad gateway", "gateway timeout",
    "connection error", "connection reset", "request timed out",
)

# Codex's transient set — note what is NOT in it: no "rate limit", no "429".
# codex's server-side 429 ("exceeded retry limit, last status: 429") therefore
# matches nothing and surfaces as-is.
CODEX_TRANSIENT_PATTERNS = (
    "temporarily limiting requests",
    "not your usage limit",
    "overloaded",
    "error 500", "error 502", "error 503", "error 504",
    "internal server error", "service unavailable",
    "bad gateway", "gateway timeout", "temporarily unavailable",
    "connection error", "connection reset", "stream error", "request timed out",
)

# Codex's user-limit marker. This IS a string match, and the entry says so
# plainly. What licenses it is not its form but its warrant: a captured sample
# of the opposing class proves the throttle message does not contain it.
CODEX_USAGE_LIMIT_MARKER = "you've hit your usage limit"


def classify_claude(error_text: str, info: dict | None) -> str:
    """claude: the limit verdict comes from a STRUCTURED field.

    The discriminator is not the status code and not the prose, but whether the
    CLI's `rate_limit_event` CLAIMS A WINDOW. A user-limit rejection names the
    exhausted window (`rateLimitType`, e.g. "five_hour") and carries the epoch
    it reopens. A server-side throttle is rejected with no claim on any window
    — same 429, same "rate limit" prose, no `rateLimitType`.
    """
    if isinstance(info, dict) and info.get("status") == "rejected":
        window = info.get("rateLimitType")
        if isinstance(window, str) and window:
            return "park"
        # Rejected with no window claim: the throttle shape. Fall through.
    # `allowed_warning` (approaching the limit) rides on turns that SUCCEEDED
    # and never reaches here as a failure.
    haystack = error_text.lower()
    if any(p in haystack for p in CLAUDE_TRANSIENT_PATTERNS):
        return "retry"
    return "surface"


def classify_codex(error_text: str, info: dict | None) -> str:
    """codex: the limit verdict comes from a STRING MARKER, not a structure.

    Codex puts no structured limit state on `exec --json` stdout, so there is
    no field to key on. Owlery keys on a phrase whose disjointness from the
    throttle message is established by captured samples of BOTH classes. The
    reset epoch is not taken from the prose either — it is read out of codex's
    rollout file as structured data, out of band (not modelled here: it is
    disk I/O, and this case stays stream-only).
    """
    haystack = error_text.lower()
    if CODEX_USAGE_LIMIT_MARKER in haystack:
        return "park"
    if any(p in haystack for p in CODEX_TRANSIENT_PATTERNS):
        return "retry"
    return "surface"


def classify_as_shipped(backend: str, error_text: str, info: dict | None) -> str:
    return (classify_claude if backend == "claude" else classify_codex)(
        error_text, info
    )


def reset_epoch(info: dict | None) -> int | None:
    """The epoch the exhausted window reopens — taken from the structured field,
    never parsed back out of the rendered local time in the prose."""
    if not isinstance(info, dict):
        return None
    value = info.get("resetsAt")
    return int(value) if isinstance(value, (int, float)) else None


# --------------------------------------------------------------------------
# Oracles
# --------------------------------------------------------------------------

def load_all():
    streams = {}
    for backend, filename, expected in SAMPLES:
        path = EVIDENCE / filename
        if not path.exists():
            raise SystemExit(f"missing evidence file: {path}")
        streams[filename] = (*read_stream(path), backend, expected)
    return streams


def oracle_status_code_is_useless(streams) -> list[str]:
    """The cheapest thing to key on fails in BOTH directions."""
    print("  [1] the status code is not a usable discriminator")
    failures = []
    # Asserted exactly, per sample — not summarised as "429 everywhere", which
    # is what an earlier revision of this file claimed and this oracle refuted.
    expected = {
        "claude_user_limit_5h.jsonl": True,
        "claude_server_429.jsonl": True,
        "codex_user_limit.jsonl": False,   # a REAL usage limit with no 429 in it
        "codex_server_429.jsonl": True,
    }
    for _, filename, want in SAMPLES:
        has_429 = "429" in streams[filename][0]
        print(f"      429 present: {str(has_429):<5}  {filename:<28} "
              f"(disposition: {want})")
        if has_429 != expected[filename]:
            failures.append(
                f"{filename}: expected 429 present={expected[filename]}, "
                f"got {has_429}"
            )
    print("      -> fails both ways. Within the claude pair it is present on")
    print("         BOTH sides, so it cannot separate park from retry. And it")
    print("         is absent from codex's real usage limit, so it is not even")
    print("         a reliable indicator that a limit was hit.")
    return failures


def oracle_generic_vocabulary_fails(streams) -> list[str]:
    """A classifier keyed on generic rate-limit vocabulary, run on real samples.

    This is a counterexample experiment against ONE fixed vocabulary, not a
    proof about substring matching in general. See oracle 3.
    """
    failures = []
    print("  [2] generic rate-limit vocabulary is not a discriminator")
    print(f"      vocabulary: {list(GENERIC_LIMIT_VOCABULARY)}")

    verdicts = {
        filename: classify_by_generic_vocabulary(*streams[filename][:2])
        for _, filename, _ in SAMPLES
    }
    # "MISCLASSIFIED" here is the RECORDED, EXPECTED behaviour of the strategy
    # being criticised. This oracle fails if reality departs from the entry's
    # claim — including if the vocabulary suddenly got these right.
    expected = {
        "claude_user_limit_5h.jsonl": "park",   # right, but only via "429"
        "claude_server_429.jsonl": "park",      # WRONG — retry in seconds
        "codex_user_limit.jsonl": "park",       # right
        "codex_server_429.jsonl": "park",       # WRONG — should surface
    }
    if verdicts != expected:
        failures.append(
            f"generic vocabulary did not behave as recorded:\n"
            f"    expected {expected}\n    got      {verdicts}"
        )
    for _, filename, want in SAMPLES:
        got = verdicts[filename]
        mark = "correct      " if got == want else "MISCLASSIFIED"
        print(f"      {mark}  {filename:<28} want={want:<8} got={got}")
    print("      -> answers 'park' to all four: no discriminating power.")

    # The negation trap: the throttle text contains "usage limit" only inside
    # "(not your usage limit)". Substring matching cannot see negation.
    throttle_text = streams["claude_server_429.jsonl"][0].lower()
    if "not your usage limit" not in throttle_text:
        failures.append("the '(not your usage limit)' negation is gone")
    else:
        print("      -> the throttle text contains 'usage limit' only inside")
        print("         '(not your usage limit)': the most specific-looking")
        print("         phrase appears where it means the opposite.")
    return failures


def oracle_prose_matching_is_possible_but_fragile(streams) -> list[str]:
    """Honesty check: a hand-tuned prose classifier DOES pass this corpus.

    This oracle exists to stop the entry from overclaiming. If prose matching
    were impossible, the argument would be a proof; it is not, so the argument
    has to be about fragility, and this makes that explicit and checkable.
    """
    tuned = ("session limit", "you've hit your usage limit")

    def classify_by_tuned_prose(error_text: str, info: dict | None) -> str:
        haystack = error_text.lower()
        if any(p in haystack for p in tuned):
            return "park"
        return "not-a-usage-limit"

    print("  [3] a hand-tuned prose classifier DOES separate this corpus")
    print(f"      patterns: {list(tuned)}")
    failures = []
    for _, filename, want in SAMPLES:
        text, info = streams[filename][:2]
        got = classify_by_tuned_prose(text, info)
        want_park = want == "park"
        ok = (got == "park") == want_park
        print(f"      {'ok  ' if ok else 'FAIL'}  {filename:<28} "
              f"is_limit={got == 'park'} (want {want_park})")
        if not ok:
            failures.append(f"tuned prose classifier: {filename} -> {got}")
    print("      -> it works. So the case against prose is NOT impossibility.")
    print("         'session limit' and 'resets 10:22pm (Asia/Shanghai)' are")
    print("         localized, rendered output — they change with locale, plan")
    print("         tier and release, and nothing obliges the vendor to keep")
    print("         them stable. That is the actual argument: fragility.")
    return failures


def oracle_shipped_classifier(streams) -> list[str]:
    """Owlery's shipped classifier is correct on all four, and recovers the epoch."""
    failures = []
    print("  [4] the shipped classifier")
    for backend, filename, want in SAMPLES:
        text, info = streams[filename][:2]
        got = classify_as_shipped(backend, text, info)
        mark = "ok  " if got == want else "FAIL"
        print(f"      {mark}  {filename:<28} want={want:<8} got={got}")
        if got != want:
            failures.append(f"shipped classifier got {filename} wrong: {got}")

    epoch = reset_epoch(streams["claude_user_limit_5h.jsonl"][1])
    if epoch != 1784038967:
        failures.append(f"reset epoch not recovered verbatim: {epoch}")
    else:
        print(f"      ok    reset epoch recovered verbatim: {epoch}")
    return failures


def main() -> int:
    streams = load_all()
    print(f"replaying {len(SAMPLES)} captured samples from {EVIDENCE.name}/\n")

    failures = []
    for oracle in (
        oracle_status_code_is_useless,
        oracle_generic_vocabulary_fails,
        oracle_prose_matching_is_possible_but_fragile,
        oracle_shipped_classifier,
    ):
        failures += oracle(streams)
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
