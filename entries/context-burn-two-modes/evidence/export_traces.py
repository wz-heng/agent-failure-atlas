#!/usr/bin/env python3
"""Regenerate the redacted usage traces in this directory from the source ledger.

This script is the redaction transform, published so the reduction is
checkable rather than asserted. It is **not** part of the entry's minimal
case — `repro.py` reads the committed traces and never touches a database.
Nobody but the operator can run this; it needs the private Owlery instance.

Usage:
    python3 export_traces.py --db /path/to/owlery.db [--check]

`--check` regenerates in memory and diffs against the committed files
instead of writing, so a reviewer with the original can confirm the
committed traces are exactly what this transform produces.

Redaction is REDUCTION ONLY. Every field that survives is copied verbatim
from the ledger; nothing is rounded, rescaled, or recomputed. See
README.md in this directory for the field-by-field list.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The three sessions this entry is about, in the order they are introduced.
# Real ids map one-to-one onto these placeholders; the mapping is injective
# by construction (distinct keys, distinct values) and asserted below.
SESSIONS = [
    ("7970b1b120ea", "session-A", "hot_reread_A.jsonl"),
    ("87360a23856c", "session-B", "cold_rewrite_B.jsonl"),
    ("ed18c5ba095a", "session-C", "cold_rewrite_C.jsonl"),
]

# Columns carried into the trace. Everything else in `turn_usage` is dropped.
# Note what is NOT here: agent_id, session name, working_dir, and every
# message/content table in the database. No prompt or reply text exists in
# `turn_usage` at all — it is a numbers-only ledger.
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


def export(db_path: str) -> dict[str, list[dict]]:
    """Read the ledger read-only and return {filename: [record, ...]}."""
    uri = f"file:{db_path}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        out: dict[str, list[dict]] = {}
        mapping: dict[str, str] = {}
        for real_id, placeholder, filename in SESSIONS:
            mapping[real_id] = placeholder
            rows = db.execute(
                f"SELECT {', '.join(KEPT)}, model_usage FROM turn_usage "
                "WHERE session_id = ? ORDER BY created_at, id",
                (real_id,),
            ).fetchall()
            if not rows:
                raise SystemExit(f"no turn_usage rows for {real_id}")
            records = []
            for row in rows:
                rec = {"session": placeholder}
                rec.update(dict(zip(KEPT, row[: len(KEPT)])))
                # `contextWindow` is a per-model constant the CLI reports; it
                # is the only field lifted out of the model_usage JSON, because
                # the analysis needs the window size. The rest of that JSON is
                # a duplicate of the columns above and is dropped.
                raw = row[len(KEPT)]
                windows = set()
                if raw:
                    for usage in json.loads(raw).values():
                        if isinstance(usage, dict) and usage.get("contextWindow"):
                            windows.add(usage["contextWindow"])
                rec["context_window"] = max(windows) if windows else None
                records.append(rec)
            out[filename] = records
        # Redaction must be injective: equal ids stay equal, distinct stay distinct.
        assert len(set(mapping.values())) == len(mapping), "mapping is not injective"
        return out
    finally:
        db.close()


def render(records: list[dict]) -> str:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="path to the source owlery.db")
    ap.add_argument("--check", action="store_true", help="diff instead of write")
    args = ap.parse_args()

    exported = export(args.db)
    failed = False
    for filename, records in exported.items():
        text = render(records)
        target = HERE / filename
        if args.check:
            actual = target.read_text() if target.exists() else ""
            status = "OK " if actual == text else "DIFF"
            if actual != text:
                failed = True
            print(f"{status} {filename} ({len(records)} records)")
        else:
            target.write_text(text)
            print(f"wrote {filename} ({len(records)} records)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
