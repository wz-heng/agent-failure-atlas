# Evidence — three sessions of per-turn usage records

113 per-turn usage records exported from Owlery's `turn_usage` ledger on
**2026-07-20**, covering three real agent sessions run between 2026-07-09 and
2026-07-15 on macOS 26.3 (Darwin 25.3.0).

| file | session | span | turns | what it is |
|---|---|---|---|---|
| `hot_reread_A.jsonl` | `session-A` | 2026-07-09, 03:56 | 20 | the hot re-read: 20 turns, 43.7M cache-read tokens |
| `cold_rewrite_B.jsonl` | `session-B` | 2026-07-11 → 07-15 | 66 | the marathon: five days, repeated wakeup rewrites |
| `cold_rewrite_C.jsonl` | `session-C` | 2026-07-13 → 07-15 | 27 | a second session that paid its own tax 81s after B |

`export_traces.py` in this directory is the transform that produced them, and
`--check` re-runs it against the original to prove the committed files are
exactly its output.

## What these records are

`turn_usage` is a **numbers-only ledger**. Owlery appends one row per turn with
that turn's token counters, timing, and the cost the CLI reported
(`server/session_manager.py` → `_record_turn_usage`). It contains no prompt, no
reply, no tool call, and no file path — there is no content column to redact,
because there is no content column.

Each record carries exactly these fields:

```
session  created_at  backend  model  input_tokens  cache_read_tokens
cache_creation_tokens  output_tokens  total_tokens  duration_ms  is_error
cost  context_window
```

## What they do and do not show

- They are a **complete** record of both incidents' billing, turn by turn. Every
  dollar figure in the entry is a decomposition of these rows.
- They are **not** a reproduction. The sessions cannot be re-run: session B's row
  in the `sessions` table no longer exists, its messages are gone, and re-running
  five days of agent work against a real model would cost real money to
  demonstrate something the ledger already records. This entry is an executable
  *reconstruction*, and is graded accordingly.
- They record **no per-call detail.** A turn is one row, however many model calls
  it made. So the ledger cannot show "ten tool calls each re-sending the
  context" directly — only the turn's total. The entry's step-count claim is
  therefore stated as a *floor* derived from the context window (see the entry's
  §3), never as a measurement.

## Cost is provider-reported, and it is a list-price computation

The `cost` field is not Owlery's arithmetic. It is the `total_cost_usd` value
the Claude Code CLI reports at the end of each turn, stored verbatim
(`server/harness/claude_code.py:361`).

**It is not a record of money charged.** `repro.py` oracle 1 shows why: 95 of
the 99 priced turns in these traces reproduce *exactly* — to within 0.1% — from
published list prices and the token counters, using cache-read at 0.1× and
cache-write at 2.0× the model's input price. A figure that reconciles to list
price to the cent is a **list-price computation**, not a settlement. These
sessions ran on a subscription plan; no charge record was consulted, and none is
claimed. Every figure in this entry is a **list-price equivalent**.

That same reconciliation is what identifies the cache TTL. The published
cache-write rate is 1.25× input for the 5-minute TTL and 2.0× for the 1-hour
TTL. **Zero** turns reconcile at 1.25×; 95 reconcile at 2.0×. The one-hour TTL
is therefore established from billing arithmetic rather than from recollection —
which matters, because the entry's cold-rewrite mechanism turns on that TTL.

**Four turns do not reconcile**, and the entry says so rather than dropping
them. The strangest is an errored turn billed **$9.8231 with every token counter
at zero** — a charge with no recorded consumption. It is preserved in the trace
and pinned by `test_trace_unreconciled_turn_count_is_what_the_entry_says`. I do
not know what it is.

## Redaction

Applied mechanically by `export_traces.py`. **Removed: nothing but identifiers
and duplicated fields.** No surviving value was altered, rounded, or rescaled.

- **`session_id`** — the only identifier these rows carry. Each distinct original
  id is replaced with `session-A` / `session-B` / `session-C`, assigned in the
  order the entry introduces them, so equal ids stay equal and distinct ids stay
  distinct. Each file contains exactly one session, so the mapping is trivially
  injective, and the export asserts it.
- **`agent_id`** — dropped entirely (an opaque local id, load-bearing for
  nothing in the analysis).
- **`id`** — the ledger's autoincrement primary key, dropped. It leaks the
  volume and interleaving of *other*, unrelated sessions on the instance.
- **`origin`** — dropped; constant (`"turn"`) across every row in the database.
- **`model_usage`** — the raw per-model JSON blob, dropped. Everything in it
  duplicates the columns above except `contextWindow`, which the analysis needs
  and which is carried across as `context_window`.

Nothing else was removed. Timestamps are preserved to the microsecond because
the entire idle-gap analysis is a function of them, and the incident dates are
already stated in the entry.

Model names are preserved **verbatim**, including the inconsistent casing
(`Claude-opus-4-8` on the older rows, `claude-opus-4-8` on the newer). That
inconsistency is real data, not noise — the price lookup in `repro.py`
lowercases precisely because of it.

## Checks anyone can run without the original database

- **No local identifiers survive.** `grep -c '/Users/\|/tmp/' *.jsonl` returns 0
  for every file. `test_trace_records_carry_no_content_and_no_identifiers`
  asserts this, plus that every record carries exactly the whitelisted key set
  and a `session-` placeholder.
- **The traces still say what the entry says.** Nine `test_trace_*` tests pin
  the specific figures quoted in the prose — session A's 43,715,956 cache-read
  tokens, the $20.6988 wakeup turn and its 974,755-token rewrite, session B's
  $235.7484 lifetime, session C's $3.8608 at +81 seconds, and the count of
  unreconciled turns. Editing a number in `README.md` without editing the
  traces turns these red.
- **The pricing reconciliation is self-checking.**
  `test_trace_only_the_1h_rate_reconciles` asserts 95 matches at the 1-hour rate
  and exactly 0 at the 5-minute rate.

One property genuinely requires the original, stated so a reviewer who has it
knows what to run: **redaction was reduction only.** Run
`python3 export_traces.py --db <path> --check`; it regenerates from the source
ledger and diffs against the committed files. It prints `OK` for all three when
they match.
