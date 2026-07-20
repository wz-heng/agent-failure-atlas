#!/usr/bin/env python3
"""A turn that ends in success and delivers nothing.

Offline, stdlib only, no account, no network, no API spend, ~1s.
Imports nothing from Owlery — the consumer logic is transcribed into this file.

The traces in evidence/ are real captures from the real Codex CLI (see
evidence/README.md). This script spawns a fake `codex` that replays them over a
real pipe with the captured exit code, and drives them through two consumer
policies:

  * SHIPPED  — the classification Owlery runs today, transcribed. A terminal
               `turn.completed` is success; nothing counts the assistant
               messages.
  * DEFENDED — the same, plus two guards: a terminal success carrying no
               assistant content is an error, and item types the parser does
               not model are surfaced instead of dropped.

Exit 0 = every oracle held. Exit 1 = an oracle failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"


# --------------------------------------------------------------------------
# The fake CLI: replays a captured stream over a real pipe, then exits with the
# exit code the real binary returned. It reads a trace off disk and writes it
# out verbatim; it has no idea what any of the records mean, so it cannot be
# staging a puppet show for the conclusion below.
# --------------------------------------------------------------------------

def _replay(trace_path: str, exit_code: int) -> int:
    for line in Path(trace_path).read_text().splitlines():
        if line.strip():
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    return exit_code


# --------------------------------------------------------------------------
# The consumer.
# --------------------------------------------------------------------------

@dataclass
class Event:
    """One normalized event, in the shape Owlery's HarnessEvent carries."""
    type: str                      # "text" | "result" | "error" | "session_started" | "warning"
    content: str = ""
    is_error: bool = False


@dataclass
class Outcome:
    """What the user and the runtime end up with after a turn."""
    verdict: str                   # "success" | "failed"
    delivered: list[str] = field(default_factory=list)   # what the user sees
    surfaced_errors: list[str] = field(default_factory=list)
    surfaced_warnings: list[str] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)    # records the parser modelled nothing for
    debug_logged: list[str] = field(default_factory=list)  # dropped, but at least logged
    exit_code: int = 0


# --- parser: codex JSONL -> events.  Transcribed from server/harness/codex.py
#     (Owlery, private) as it stands on 2026-07-20.

def parse_shipped(obj: dict, out: Outcome) -> list[Event]:
    kind = obj.get("type")

    if kind == "thread.started":
        return [Event("session_started", obj.get("thread_id") or "")]

    if kind == "turn.started":
        return []

    if kind == "turn.completed":
        # No inspection of what the turn produced. This is the whole bug.
        return [Event("result", is_error=False)]

    if kind == "turn.failed":
        err = obj.get("error")
        msg = err if isinstance(err, str) else (err or {}).get("message")
        return [Event("result", content=msg or "Turn failed", is_error=True)]

    if kind == "error":
        return [Event("error", content=obj.get("message") or "Unknown error", is_error=True)]

    if isinstance(kind, str) and kind.startswith("item."):
        item = obj.get("item")
        if not isinstance(item, dict):
            return []
        item_type = item.get("type")
        completed = kind == "item.completed"

        if item_type == "agent_message":
            text = item.get("text")
            # `and text` — an empty string is falsy, so a zero-content message
            # produces no event at all. Owlery's line, transcribed.
            if completed and text:
                return [Event("text", content=text)]
            return []

        # agent_message is the only item type this reproduction needs to model;
        # Owlery models several more (reasoning, command_execution, file_*, …).
        # What matters here is the fall-through they share: an item type with no
        # branch returns [] and is gone — with no log line of any kind.
        out.dropped.append(obj)
        return []

    # The asymmetry is worth transcribing. Owlery's TOP-LEVEL fall-through logs:
    #     logger.debug("Unhandled codex event type: %s", kind)
    # while `_item_events` ends in a bare `return []`. So an unmodelled *event*
    # leaves a trace at debug level; an unmodelled *item* leaves none — and the
    # record carrying the model warning is an item.
    out.dropped.append(obj)
    out.debug_logged.append(str(kind))
    return []


def drive_turn(argv: list[str], policy: str) -> Outcome:
    """Spawn the CLI, consume its stream, decide what the turn meant."""
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)

    out = Outcome(verdict="", exit_code=proc.returncode)
    saw_result = False
    saw_error_event = False

    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        for ev in parse_shipped(obj, out):
            if ev.type == "text" and ev.content.strip():
                out.delivered.append(ev.content)
            if ev.type == "result":
                saw_result = True
            if ev.type in ("result", "error") and ev.is_error:
                saw_error_event = True
                if ev.content:
                    out.surfaced_errors.append(ev.content)

    # The disposition. `turn_failed = saw_error_event or not saw_result`,
    # transcribed from server/session_manager.py.
    turn_failed = saw_error_event or not saw_result

    if policy == "defended":
        # Guard 1 — a terminal success that delivered nothing to the user is
        # not a success. This is the one that closes the failure mode.
        if not turn_failed and not out.delivered:
            turn_failed = True
            out.surfaced_errors.append(
                "Turn ended with no assistant output. The backend reported "
                "success but produced no message; the request was accepted and "
                "silently answered with nothing."
            )
        # Guard 2 — records the parser models nothing for are surfaced as
        # warnings rather than discarded. They are warnings, not errors: they
        # never change the verdict, and they go in their own list so an oracle
        # looking for a real error cannot be satisfied by one of these.
        for obj in out.dropped:
            item = obj.get("item") or {}
            msg = item.get("message") or item.get("text")
            if msg:
                out.surfaced_warnings.append(f"[unmodelled {item.get('type')!r} item] {msg}")

    out.verdict = "failed" if turn_failed else "success"
    return out


# --------------------------------------------------------------------------
# The pre-spawn guard: does this model name belong to this backend?
# --------------------------------------------------------------------------

# Owlery has no such table. This is the defense the entry proposes, not
# transcribed production code — it is labelled PROPOSED wherever it is used.
#
# Matching is ANCHORED, never "substring anywhere". An earlier version of this
# function used `marker in model.lower()`, which classified `octopus-v2` as a
# claude model (it contains "opus"), `no1se` as a codex model (it contains
# "o1"), and `video-o4k` likewise. A name from neither namespace must pass
# through, and a bare substring test cannot promise that.
MODEL_OWNERSHIP = {
    # backend: (vendor prefixes, family aliases)
    "claude-code": (("claude-",), ("opus", "sonnet", "haiku")),
    "codex": (("gpt-",), ("codex", "o1", "o3", "o4")),
}


def _namespace_of(model: str) -> str | None:
    """Which backend's namespace does this name positively belong to, if any?

    Three ways to belong, all anchored:
      * a vendor prefix       — `claude-opus-4-8`, `gpt-5.3-codex`
      * a bare family alias   — `opus`, `codex`
      * a family alias with a version — `sonnet-4-5`, `o3-2`

    The version digit is what keeps `opus-magnum` out: a family alias followed
    by a *word* is somebody else's name, not a release of ours.

    Deliberately incomplete. It will not attribute `o3-mini` or `codex-mini`,
    because "family alias plus arbitrary word" is precisely the shape that
    collides with unrelated names. Missing a rejection costs one confusing turn;
    a false rejection blocks a legitimate model with a message insisting the
    user's own configuration is wrong. Soundness beats completeness here.
    """
    lowered = model.lower()
    for backend, (prefixes, families) in MODEL_OWNERSHIP.items():
        if lowered.startswith(prefixes):
            return backend
        for family in families:
            if lowered == family:
                return backend
            rest = lowered[len(family) + 1:]
            if lowered.startswith(family + "-") and rest[:1].isdigit():
                return backend
    return None


def attribute_model(backend: str, model: str | None) -> str | None:
    """Return an error string if `model` cannot belong to `backend`, else None.

    Rejects only names that positively match a *different* backend's namespace.
    An unrecognised name is passed through: the vendor, not this table, is the
    authority on what it accepts, and a stale allowlist that blocks a
    newly-released model is a worse failure than the one being fixed. That
    guarantee is why matching is anchored — see MODEL_OWNERSHIP.
    """
    if not model:
        return None
    owner = _namespace_of(model)
    if owner is None or owner == backend:
        return None
    return (
        f"model {model!r} looks like a {owner} model, but this session "
        f"runs on the {backend} backend"
    )


# --------------------------------------------------------------------------
# Oracles
# --------------------------------------------------------------------------

TRACES = {
    "zero_content":     ("codex_0.144.6_zero_content_turn.jsonl", 0),
    "rejected_0_144_6": ("codex_0.144.6_model_rejected.jsonl", 1),
    "rejected_0_142_5": ("codex_0.142.5_model_rejected.jsonl", 1),
    "control_0_144_6":  ("codex_0.144.6_control_turn.jsonl", 0),
    "control_0_142_5":  ("codex_0.142.5_control_turn.jsonl", 0),
}


def fake_cli(name: str) -> list[str]:
    trace, code = TRACES[name]
    return [sys.executable, str(Path(__file__).resolve()), "--replay",
            str(EVIDENCE / trace), "--exit-code", str(code)]


class OracleFailure(AssertionError):
    pass


def check(label: str, actual, expected) -> None:
    if actual != expected:
        raise OracleFailure(f"{label}\n  expected: {expected!r}\n  actual:   {actual!r}")
    print(f"    ok  {label}")


def oracle_1_zero_content_turn_is_swallowed() -> None:
    print("\n[1] A zero-content turn is reported to the user as a success.")
    out = drive_turn(fake_cli("zero_content"), policy="shipped")
    check("verdict", out.verdict, "success")
    check("text delivered to the user", out.delivered, [])
    check("errors surfaced", out.surfaced_errors, [])
    check("CLI exit code", out.exit_code, 0)
    print("    -> the user sent a message and received nothing, with no error "
          "anywhere in the system.")


def oracle_2_todays_rejection_is_loud() -> None:
    print("\n[2] TODAY, a cross-backend model name is REJECTED LOUDLY — on both "
          "CLI versions.")
    print("    This oracle exists to stop this entry claiming a reproduction it "
          "does not have.")
    for name, version in (("rejected_0_144_6", "0.144.6"),
                          ("rejected_0_142_5", "0.142.5")):
        out = drive_turn(fake_cli(name), policy="shipped")
        check(f"{version}: verdict", out.verdict, "failed")
        check(f"{version}: CLI exit code", out.exit_code, 1)
        # Counted, not `any()`. The stream carries the 400 twice — once as a
        # standalone `error` record and once inside `turn.failed` — and those
        # are two independent branches of the consumer. With `any()`, deleting
        # either branch left every oracle green; a review caught it by mutation.
        hits = sum("not supported when using Codex" in e for e in out.surfaced_errors)
        check(f"{version}: both error paths surface the 400", hits, 2)


def oracle_3_the_early_warning_is_dropped() -> None:
    print("\n[3] The CLI's own early warning never reaches the user — and is "
          "not even logged.")
    out = drive_turn(fake_cli("rejected_0_144_6"), policy="shipped")
    warnings = [o for o in out.dropped
                if (o.get("item") or {}).get("type") == "error"]
    check("unmodelled error items in the stream", len(warnings), 1)
    check("the warning's text",
          warnings[0]["item"]["message"],
          "Model metadata for `Claude-opus-4-8` not found. Defaulting to "
          "fallback metadata; this can degrade performance and cause issues.")
    if any("Model metadata" in e for e in
           out.surfaced_errors + out.surfaced_warnings):
        raise OracleFailure("the warning was surfaced; the shipped parser drops it")
    # The item fall-through has no log line, unlike the top-level one.
    check("debug-logged records", out.debug_logged, [])
    print("    -> the CLI names the model string in its first record — the one "
          "variable nobody looked at — and the parser has no branch for that "
          "item type, so it is dropped without so much as a debug line.")


def oracle_4_the_defense() -> None:
    print("\n[4] The defense: turn the silence into an error, without breaking "
          "a good turn.")
    out = drive_turn(fake_cli("zero_content"), policy="defended")
    check("zero-content verdict", out.verdict, "failed")
    if not any("no assistant output" in e for e in out.surfaced_errors):
        raise OracleFailure(f"expected an explicit empty-turn error, got "
                            f"{out.surfaced_errors!r}")
    print("    ok  the zero-content turn now fails with an explicit message")

    for name, version in (("control_0_144_6", "0.144.6"),
                          ("control_0_142_5", "0.142.5")):
        out = drive_turn(fake_cli(name), policy="defended")
        check(f"{version}: control turn verdict", out.verdict, "success")
        check(f"{version}: control turn delivered", out.delivered, ["OK"])

    out = drive_turn(fake_cli("rejected_0_144_6"), policy="defended")
    check("rejected turn still fails", out.verdict, "failed")
    if not any("Model metadata" in w for w in out.surfaced_warnings):
        raise OracleFailure("guard 2 should surface the dropped warning item")
    check("the warning stayed a warning, not an error",
          any("Model metadata" in e for e in out.surfaced_errors), False)
    print("    ok  the dropped warning is now surfaced too")


def oracle_5_pre_spawn_attribution() -> None:
    print("\n[5] PROPOSED pre-spawn guard: a model name is checked against the "
          "backend that will run it.")
    err = attribute_model("codex", "Claude-opus-4-8")
    if err is None:
        raise OracleFailure("the incident's exact configuration was accepted")
    check("the rejection names both sides",
          ("claude-code" in err and "codex" in err), True)
    check("a codex model on codex", attribute_model("codex", "gpt-5.3-codex"), None)
    check("a claude model on claude-code",
          attribute_model("claude-code", "claude-opus-4-8"), None)
    check("no model set at all", attribute_model("codex", None), None)
    check("an unrecognised name is passed through, not blocked",
          attribute_model("codex", "some-future-model-2027"), None)
    if attribute_model("claude-code", "gpt-5.3-codex") is None:
        raise OracleFailure("the check must fail in both directions, "
                            "or it is one hardcoded string")
    print("    ok  the check fails in both directions")


ORACLES = (
    oracle_1_zero_content_turn_is_swallowed,
    oracle_2_todays_rejection_is_loud,
    oracle_3_the_early_warning_is_dropped,
    oracle_4_the_defense,
    oracle_5_pre_spawn_attribution,
)


def main() -> int:
    if "--replay" in sys.argv:
        i = sys.argv.index("--replay")
        j = sys.argv.index("--exit-code")
        return _replay(sys.argv[i + 1], int(sys.argv[j + 1]))

    missing = [t for t, _ in TRACES.values() if not (EVIDENCE / t).exists()]
    if missing:
        print(f"missing evidence files: {missing}", file=sys.stderr)
        return 1

    print("Replaying real Codex CLI captures (2026-07-20) through the consumer "
          "logic Owlery ships.")
    try:
        for oracle in ORACLES:
            oracle()
    except OracleFailure as exc:
        print(f"\nORACLE FAILED: {exc}", file=sys.stderr)
        return 1

    print("\nAll oracles held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
