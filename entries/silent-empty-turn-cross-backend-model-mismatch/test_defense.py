#!/usr/bin/env python3
"""Regression tests for the defense against silently-empty turns.

    python3 test_defense.py            # standalone
    python3 -m pytest test_defense.py -q

Offline, stdlib only, ~1.2s. Which tests touch what — the synthetic/real
boundary matters more than anything else in this file, so it is stated by name:

* **Real captures** from evidence/ — everything under "the fix", "the dropped
  warning" and "the evidence itself", plus two of the three under "the bug,
  pinned".
* **Hand-written records** — the three under "shapes the corpus does not
  contain" (built by `_synthetic_cli`, covering shapes no capture exhibits) and
  `test_an_empty_message_produces_no_event_at_all` (inline dicts, asserting on
  the parser directly). None of these is ever cited as evidence about the
  vendor.
* **No stream at all** — the three attribution tests, which are pure functions.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from repro import (EVIDENCE, TRACES, Outcome, attribute_model, drive_turn,
                   fake_cli, parse_shipped)

HERE = Path(__file__).resolve().parent


def _synthetic_cli(records: list[dict], exit_code: int = 0) -> tuple[list[str], object]:
    """A fake CLI over a hand-written stream. NOT evidence — see the docstring."""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for rec in records:
        tmp.write(json.dumps(rec) + "\n")
    tmp.close()
    argv = [sys.executable, str(HERE / "repro.py"), "--replay", tmp.name,
            "--exit-code", str(exit_code)]
    return argv, tmp


def _completed(items: list[dict]) -> list[dict]:
    return (
        [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"}]
        + [{"type": "item.completed", "item": it} for it in items]
        + [{"type": "turn.completed", "usage": {"output_tokens": 0}}]
    )


# --- the bug, pinned ------------------------------------------------------

def test_shipped_policy_swallows_a_zero_content_turn():
    out = drive_turn(fake_cli("zero_content"), policy="shipped")
    assert out.verdict == "success"
    assert out.delivered == []
    assert out.surfaced_errors == []


def test_an_empty_message_produces_no_event_at_all():
    """The transcribed `if completed and text:` line, pinned directly.

    This matters beyond "the user sees nothing": the empty message does not
    become a suppressed-but-present event, it never exists. Nothing downstream
    — no counter, no log, no metric — can see that a message arrived and was
    empty. Asserted on the parser rather than on the outcome, because at the
    outcome level a "" event and no event are indistinguishable.
    """
    out = Outcome(verdict="")
    empty = {"type": "item.completed",
             "item": {"id": "item_0", "type": "agent_message", "text": ""}}
    assert parse_shipped(empty, out) == []
    nonempty = {"type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "OK"}}
    assert [e.content for e in parse_shipped(nonempty, out)] == ["OK"]
    # ...and it is not routed to the unmodelled-record path either, so guard 2
    # cannot accidentally rescue it. Only guard 1 catches this.
    assert out.dropped == []


def test_shipped_policy_leaves_the_user_with_literally_nothing():
    """Not just 'no text' — no error, no warning, no non-zero exit either.

    If any one of these were non-empty the failure would be diagnosable from
    the outside, and this entry would not exist.
    """
    out = drive_turn(fake_cli("zero_content"), policy="shipped")
    assert (out.delivered, out.surfaced_errors, out.exit_code) == ([], [], 0)


# --- the fix --------------------------------------------------------------

def test_defended_policy_fails_a_zero_content_turn():
    out = drive_turn(fake_cli("zero_content"), policy="defended")
    assert out.verdict == "failed"
    assert any("no assistant output" in e for e in out.surfaced_errors)


def test_defended_policy_does_not_break_a_good_turn():
    for name in ("control_0_144_6", "control_0_142_5"):
        out = drive_turn(fake_cli(name), policy="defended")
        assert out.verdict == "success", name
        assert out.delivered == ["OK"], name
        assert out.surfaced_errors == [], name


def test_defended_policy_does_not_change_the_loud_path():
    """The guard must not turn an already-diagnosable failure into a different one."""
    for name in ("rejected_0_144_6", "rejected_0_142_5"):
        shipped = drive_turn(fake_cli(name), policy="shipped")
        defended = drive_turn(fake_cli(name), policy="defended")
        assert shipped.verdict == defended.verdict == "failed", name
        # Counted: the 400 arrives twice, via two independent consumer branches
        # (a standalone `error` record and `turn.failed`). `any()` here let
        # either branch be deleted with every test still green.
        assert shipped.surfaced_errors == defended.surfaced_errors, name
        assert sum("not supported when using Codex" in e
                   for e in defended.surfaced_errors) == 2, name
        # The empty-turn guard must NOT have fired here: this turn failed for a
        # reason the system already knew.
        assert not any("no assistant output" in e
                       for e in defended.surfaced_errors), name


def test_each_error_record_reaches_the_user_on_its_own_path():
    """Pins that BOTH transcribed error branches fire, not just one of them.

    The `error` record and the `turn.failed` record carry identical text, so a
    membership check cannot tell whether one branch has stopped working.
    """
    out = drive_turn(fake_cli("rejected_0_144_6"), policy="shipped")
    assert len(out.surfaced_errors) == 2
    assert out.surfaced_errors[0] == out.surfaced_errors[1]


# --- the dropped warning --------------------------------------------------

def test_the_shipped_parser_drops_the_cli_s_own_warning():
    out = drive_turn(fake_cli("rejected_0_144_6"), policy="shipped")
    assert not any("Model metadata" in e
                   for e in out.surfaced_errors + out.surfaced_warnings)
    dropped = [o for o in out.dropped if (o.get("item") or {}).get("type") == "error"]
    assert len(dropped) == 1
    assert "Model metadata for `Claude-opus-4-8` not found" in dropped[0]["item"]["message"]
    # Dropped without even a debug line: the item fall-through, unlike the
    # top-level one, logs nothing.
    assert out.debug_logged == []


def test_the_defense_surfaces_the_dropped_warning_as_a_warning():
    out = drive_turn(fake_cli("rejected_0_144_6"), policy="defended")
    assert any("Model metadata for `Claude-opus-4-8` not found" in w
               for w in out.surfaced_warnings)
    # It must not become an error: it never changes the verdict, and an oracle
    # looking for a real error must not be satisfiable by a warning.
    assert not any("Model metadata" in e for e in out.surfaced_errors)


def test_both_cli_versions_emit_the_same_warning_today():
    """The warning is not a 0.144.6 novelty — 0.142.5 emits it too.

    Pins what the two builds do today and nothing more. Whether the record was
    emitted on the day of the incident is unknown: no capture from that day
    exists.
    """
    for name in ("rejected_0_144_6", "rejected_0_142_5"):
        out = drive_turn(fake_cli(name), policy="defended")
        assert any("Model metadata" in w for w in out.surfaced_warnings), name


# --- shapes the corpus does not contain (SYNTHETIC) -----------------------

def test_whitespace_only_message_counts_as_empty():
    argv, tmp = _synthetic_cli(_completed([
        {"id": "item_0", "type": "agent_message", "text": "   \n  "}]))
    try:
        assert drive_turn(argv, policy="defended").verdict == "failed"
        assert drive_turn(argv, policy="shipped").verdict == "success"
    finally:
        Path(tmp.name).unlink()


def test_agent_message_with_no_text_field_counts_as_empty():
    argv, tmp = _synthetic_cli(_completed([{"id": "item_0", "type": "agent_message"}]))
    try:
        assert drive_turn(argv, policy="defended").verdict == "failed"
    finally:
        Path(tmp.name).unlink()


def test_a_turn_with_no_message_item_at_all_counts_as_empty():
    argv, tmp = _synthetic_cli(_completed([]))
    try:
        assert drive_turn(argv, policy="defended").verdict == "failed"
        assert drive_turn(argv, policy="shipped").verdict == "success"
    finally:
        Path(tmp.name).unlink()


# --- the pre-spawn guard --------------------------------------------------

def test_attribution_rejects_the_incident_s_exact_configuration():
    err = attribute_model("codex", "Claude-opus-4-8")
    assert err is not None
    assert "claude-code" in err and "codex" in err


def test_attribution_fails_in_both_directions():
    """A check that only knows one string is not a check."""
    assert attribute_model("codex", "Claude-opus-4-8") is not None
    assert attribute_model("claude-code", "gpt-5.3-codex") is not None


def test_attribution_accepts_matching_pairs():
    assert attribute_model("codex", "gpt-5.3-codex") is None
    assert attribute_model("codex", "codex") is None
    assert attribute_model("claude-code", "claude-opus-4-8") is None
    assert attribute_model("claude-code", "opus") is None
    assert attribute_model("claude-code", "sonnet-4-5") is None
    assert attribute_model("codex", None) is None
    assert attribute_model("codex", "") is None


def test_attribution_is_deliberately_incomplete_and_says_so():
    """`family-word` is not attributed, by design — it is the colliding shape.

    Pinned rather than left implicit: a later reader tempted to "improve" the
    check by matching `o3-mini` would reintroduce exactly the collisions
    `test_attribution_passes_unknown_names_through_including_colliding_ones`
    exists to prevent. Missing a rejection costs one confusing turn; a false
    rejection tells a user their working configuration is broken.
    """
    assert attribute_model("claude-code", "o3-mini") is None
    assert attribute_model("codex", "opus-magnum") is None


def test_attribution_passes_unknown_names_through_including_colliding_ones():
    """The class the check must NOT match — the one it is easiest to forget.

    A name from neither namespace must not be blocked: the vendor is the
    authority on what it accepts, and a stale allowlist that rejects a
    newly-released model is a worse failure than the one being fixed.

    Every name below is a real counterexample to the substring version of this
    function that shipped in rounds 1-4 and that these tests failed to catch,
    because the only unknown name they tried (`some-future-model-2027`) happened
    to collide with nothing. Sampling the negative class from names you already
    believe won't match is how a classifier passes its own tests while broken.
    """
    for backend in ("codex", "claude-code"):
        for name in (
            "some-future-model-2027",
            "octopus-v2",        # contains "opus"
            "opus-magnum",       # ...as a leading word, but not "opus-<version>"
            "sonnet-of-the-sea", # contains "sonnet"
            "haikus-for-hire",   # contains "haiku"
            "codexterity",       # contains "codex"
            "no1se",             # contains "o1"
            "video-o4k",         # contains "o4"
            "trigpt",            # contains "gpt"
            "unclaudeable",      # contains "claude"
        ):
            assert attribute_model(backend, name) is None, (backend, name)


# --- the evidence itself --------------------------------------------------

def test_every_trace_is_valid_jsonl_and_carries_no_raw_identifiers():
    for trace, _ in TRACES.values():
        text = (EVIDENCE / trace).read_text()
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        assert records, trace
        for rec in records:
            tid = rec.get("thread_id")
            if tid is not None:
                assert tid.startswith("thread-"), (trace, tid)
        assert "/Users/" not in text and "/tmp/" not in text, trace
        # evidence/README.md claims the only change is the identifier
        # substitution, applied by parse -> substitute -> json.dumps. Pin the
        # serialization half of that claim: every shipped line is exactly what
        # json.dumps produces for its own parse, so no line was hand-edited
        # after generation.
        for line, rec in zip(
                [l for l in text.splitlines() if l.strip()], records):
            assert json.dumps(rec) == line, (trace, line[:80])


def test_todays_rejection_is_loud_on_both_versions():
    """A guard against this entry quietly regrowing a claim it cannot support.

    The entry says the silent shape is NOT reproducible on either CLI version
    today. If a future re-capture were silent, this fails and forces a rewrite
    rather than letting the prose drift.
    """
    for trace in ("codex_0.144.6_model_rejected.jsonl",
                  "codex_0.142.5_model_rejected.jsonl"):
        records = [json.loads(l) for l in (EVIDENCE / trace).read_text().splitlines() if l.strip()]
        kinds = [r.get("type") for r in records]
        assert "error" in kinds, trace
        assert "turn.failed" in kinds, trace
        assert "turn.completed" not in kinds, trace


def test_the_warning_is_serialized_before_turn_started():
    """Pins the ordering §2 states — and only the ordering.

    A stream is a serialization order, not a timeline of network activity. This
    asserts that the warning record is written before `turn.started` in both
    captures. It does NOT establish that the record was produced before the
    request left the client, and the entry no longer says it does: an earlier
    revision inferred "client-side, therefore probably present on the day of
    the incident" from this ordering, which the ordering cannot support.
    """
    for trace in ("codex_0.144.6_model_rejected.jsonl",
                  "codex_0.142.5_model_rejected.jsonl"):
        records = [json.loads(l) for l in (EVIDENCE / trace).read_text().splitlines() if l.strip()]
        warn = next(i for i, r in enumerate(records)
                    if (r.get("item") or {}).get("type") == "error")
        started = next(i for i, r in enumerate(records) if r.get("type") == "turn.started")
        assert warn < started, trace


def test_the_zero_content_capture_really_is_a_success_shaped_stream():
    """The other half of the same guard: the empty capture must carry a
    terminal SUCCESS and no error, or oracle 1 is testing nothing."""
    records = [json.loads(l) for l in
               (EVIDENCE / "codex_0.144.6_zero_content_turn.jsonl").read_text().splitlines()
               if l.strip()]
    kinds = [r.get("type") for r in records]
    assert "turn.completed" in kinds
    assert "error" not in kinds and "turn.failed" not in kinds
    messages = [r["item"] for r in records
                if r.get("type") == "item.completed"
                and (r.get("item") or {}).get("type") == "agent_message"]
    assert len(messages) == 1
    assert messages[0]["text"] == ""


# --- runner ---------------------------------------------------------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    tests.sort(key=lambda f: f.__code__.co_firstlineno)
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok    {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - a standalone runner
            failed += 1
            print(f"FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
