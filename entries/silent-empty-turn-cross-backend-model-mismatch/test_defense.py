#!/usr/bin/env python3
"""Regression tests for the defense against silently-empty turns.

    python3 test_defense.py            # standalone
    python3 -m pytest test_defense.py -q

Offline, stdlib only, ~2s. Tests 1-9 and 13-15 run against the real captures in
evidence/. Tests 10-12 run against *synthetic* streams covering shapes the
corpus does not contain — they are unit tests of the guard, and are never cited
as evidence about the vendor.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from repro import EVIDENCE, TRACES, attribute_model, drive_turn, fake_cli

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
    """The guard must not turn a already-diagnosable failure into a different one."""
    for name in ("rejected_0_144_6", "rejected_0_142_5"):
        shipped = drive_turn(fake_cli(name), policy="shipped")
        defended = drive_turn(fake_cli(name), policy="defended")
        assert shipped.verdict == defended.verdict == "failed", name
        assert any("not supported when using Codex" in e
                   for e in defended.surfaced_errors), name
        # The empty-turn guard must NOT have fired here: this turn failed for a
        # reason the system already knew.
        assert not any("no assistant output" in e
                       for e in defended.surfaced_errors), name


# --- the dropped warning --------------------------------------------------

def test_the_shipped_parser_drops_the_cli_s_own_warning():
    out = drive_turn(fake_cli("rejected_0_144_6"), policy="shipped")
    assert not any("Model metadata" in e for e in out.surfaced_errors)
    dropped = [o for o in out.dropped if (o.get("item") or {}).get("type") == "error"]
    assert len(dropped) == 1
    assert "Model metadata for `Claude-opus-4-8` not found" in dropped[0]["item"]["message"]


def test_the_defense_surfaces_the_dropped_warning():
    out = drive_turn(fake_cli("rejected_0_144_6"), policy="defended")
    assert any("Model metadata for `Claude-opus-4-8` not found" in e
               for e in out.surfaced_errors)


def test_both_cli_versions_emit_the_same_warning():
    """The warning is not a 0.144.6 novelty — 0.142.5 emitted it too, which
    means it was available on the day of the incident and still went unread."""
    for name in ("rejected_0_144_6", "rejected_0_142_5"):
        out = drive_turn(fake_cli(name), policy="defended")
        assert any("Model metadata" in e for e in out.surfaced_errors), name


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


def test_attribution_accepts_matching_pairs_and_passes_unknown_names_through():
    assert attribute_model("codex", "gpt-5.3-codex") is None
    assert attribute_model("claude-code", "claude-opus-4-8") is None
    assert attribute_model("claude-code", "opus") is None
    assert attribute_model("codex", None) is None
    assert attribute_model("codex", "") is None
    # A name from neither namespace must NOT be blocked: the vendor is the
    # authority on what it accepts, and a stale allowlist that rejects a
    # newly-released model is a worse failure than the one being fixed.
    assert attribute_model("codex", "some-future-model-2027") is None


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
