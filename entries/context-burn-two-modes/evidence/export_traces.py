#!/usr/bin/env python3
"""Regenerate the redacted usage traces in this directory from the source ledger.

This script is the redaction transform, published so the reduction is
checkable rather than asserted. It is **not** part of the entry's minimal
case — `repro.py` reads the committed traces and never touches a database.
Nobody but the operator can run this; it needs the private Owlery instance.

**No real session id appears in this file.** An earlier revision hardcoded the
three source ids in a module-level table, which published exactly the
identifiers the transform exists to remove — and did so in the script whose
own docstring promised they were gone. Review caught it. The mapping is now
supplied at run time and the operator keeps it outside the repository.

    python3 export_traces.py --db /path/to/owlery.db \
        --session <real-id>=session-A:hot_reread_A.jsonl \
        --session <real-id>=session-B:cold_rewrite_B.jsonl \
        --session <real-id>=session-C:cold_rewrite_C.jsonl \
        --session <real-id>=session-D:codex_no_cache_field_D.jsonl

    # or, keeping the mapping in a file the repo ignores:
    python3 export_traces.py --db ... --session-file ~/.owlery-atlas-map

`--check` regenerates in memory and diffs against the committed files instead
of writing, so a reviewer with the original can confirm the committed traces
are exactly what this transform produces.

Redaction is REDUCTION ONLY, with one documented exception (`context_window`,
below). Every other field that survives is copied verbatim from the ledger;
nothing is rounded, rescaled, or recomputed. See README.md in this directory
for the field-by-field list.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Columns carried into the trace. Everything else in `turn_usage` is dropped.
# Note what is NOT here: agent_id, the autoincrement id, session name,
# working_dir, and every message/content table in the database. No prompt or
# reply text exists in `turn_usage` at all — it is a numbers-only ledger.
KEPT = [
    "created_at",
    "backend",
    "model",
    "input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "output_tokens",
    "total_tokens",
    "duration_ms",
    "is_error",
    "cost",
]


def parse_session_spec(spec: str) -> tuple[str, str, str]:
    """`<real-id>=<placeholder>:<filename>`"""
    try:
        real, rest = spec.split("=", 1)
        placeholder, filename = rest.split(":", 1)
    except ValueError:
        raise SystemExit(
            f"bad --session spec {spec!r}; expected <real-id>=<placeholder>:<filename>"
        )
    if not placeholder.startswith("session-"):
        raise SystemExit(f"placeholder {placeholder!r} must start with 'session-'")
    return real.strip(), placeholder.strip(), filename.strip()


def export(db_path: str, sessions: list[tuple[str, str, str]]) -> dict[str, list[dict]]:
    """Read the ledger read-only and return {filename: [record, ...]}."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: dict[str, list[dict]] = {}
        mapping: dict[str, str] = {}
        for real_id, placeholder, filename in sessions:
            mapping[real_id] = placeholder
            rows = db.execute(
                f"SELECT {', '.join(KEPT)}, model_usage FROM turn_usage "
                "WHERE session_id = ? ORDER BY created_at, id",
                (real_id,),
            ).fetchall()
            if not rows:
                raise SystemExit(f"no turn_usage rows for the id mapped to {placeholder}")
            records = []
            for row in rows:
                rec = {"session": placeholder}
                rec.update(dict(zip(KEPT, row[: len(KEPT)])))
                # THE ONE NON-VERBATIM FIELD. `contextWindow` is a per-model
                # constant the CLI reports inside the model_usage JSON; the
                # analysis needs the window size, so it is lifted out into a
                # column. A turn may name more than one model (a subagent on a
                # cheaper model), so the set is asserted to be a single value
                # before it is copied — an earlier revision took max() of the
                # set, which is a *choice* rather than a reduction, and was not
                # declared as one. If a turn ever reports two different
                # windows, this raises instead of silently picking one.
                raw = row[len(KEPT)]
                windows = set()
                if raw:
                    for usage in json.loads(raw).values():
                        if isinstance(usage, dict) and usage.get("contextWindow"):
                            windows.add(usage["contextWindow"])
                if len(windows) > 1:
                    raise SystemExit(
                        f"{placeholder} {rec['created_at']}: turn reports "
                        f"{len(windows)} distinct context windows {sorted(windows)}; "
                        "the single-value assumption in this transform is broken"
                    )
                rec["context_window"] = windows.pop() if windows else None
                records.append(rec)
            out[filename] = records
        # Redaction must be injective: equal ids stay equal, distinct stay distinct.
        if len(set(mapping.values())) != len(mapping):
            raise SystemExit("mapping is not injective")
        return out
    finally:
        db.close()


def render(records: list[dict]) -> str:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="path to the source owlery.db")
    ap.add_argument("--session", action="append", default=[],
                    metavar="REAL=PLACEHOLDER:FILE")
    ap.add_argument("--session-file", help="file with one --session spec per line")
    ap.add_argument("--check", action="store_true", help="diff instead of write")
    args = ap.parse_args()

    specs = list(args.session)
    if args.session_file:
        for line in Path(args.session_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    if not specs:
        raise SystemExit("no sessions given; pass --session or --session-file")

    sessions = [parse_session_spec(s) for s in specs]
    exported = export(args.db, sessions)

    failed = False
    for filename, records in exported.items():
        text = render(records)
        target = HERE / filename
        if args.check:
            actual = target.read_text() if target.exists() else ""
            if actual != text:
                failed = True
            print(f"{'OK ' if actual == text else 'DIFF'} {filename} "
                  f"({len(records)} records)")
        else:
            target.write_text(text)
            print(f"wrote {filename} ({len(records)} records)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
