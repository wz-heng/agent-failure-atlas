# Evidence — four sessions of per-turn usage records

119 per-turn usage records exported from Owlery's `turn_usage` ledger on
**2026-07-20**, covering four real agent sessions run between 2026-07-09 and
2026-07-20 on macOS 26.3 (Darwin 25.3.0).

| file | session | span | turns | what it is |
|---|---|---|---|---|
| `hot_reread_A.jsonl` | `session-A` | 2026-07-09, 03:56 | 20 | the hot re-read: 20 turns, 43.7M cache-read tokens |
| `cold_rewrite_B.jsonl` | `session-B` | 2026-07-11 → 07-15 | 66 | the marathon: five days, repeated large rewrites |
| `cold_rewrite_C.jsonl` | `session-C` | 2026-07-13 → 07-15 | 27 | a second session billed its own rewrite 81s after B |
| `codex_no_cache_field_D.jsonl` | `session-D` | 2026-07-20 | 6 | **a control, not a case.** A codex-backend session, included because its adapter never populates `cache_creation_tokens` |

`export_traces.py` in this directory is the transform that produced them, and
`--check` re-runs it against the original to prove the committed files are
exactly its output.

**Session D exists to make an exclusion honest.** The entry's oracle 4 excludes
the codex backend from its idle-gap analysis, because the stored zero there means
"not reported" rather than "nothing was written" — so its turns look like "long
gap, no rewrite" when in fact nothing was measured.

That is a source-code fact, not an inference from six rows of zeros: Owlery's
codex usage normalizer (`server/harness/codex.py`, `_normalize_usage`) builds its
`TokenUsage` from the four fields Codex emits — `input_tokens`,
`cached_input_tokens`, `output_tokens`, `reasoning_output_tokens` — and never
assigns `cache_creation_tokens`, so it takes its default of zero. An earlier revision asserted that property with **no
codex data committed at all**, so the test ran `all(...)` over an empty list and
passed while proving nothing. Session D is real data with a real 132-minute gap
followed by an apparent zero rewrite, and
`test_trace_the_codex_exclusion_is_not_vacuous` now asserts the trace is
non-empty before asserting the zeros.

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

- They are a **complete** record of both incidents' billing, turn by turn.
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
- **`cache_read_tokens` is not a context level.** It is a whole-turn aggregate
  and can exceed the context window many times over — 11.3× in session A's worst
  turn. Nothing here reports the size of the context on any individual request.
  The entry's §5 depends on this: it is why the proposed watermark needs a new
  field rather than a new query.

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

That same reconciliation identifies which cache **SKU** the CLI's cost
calculator applies: the published cache-write rate is 1.25× input for the
5-minute TTL and 2.0× for the 1-hour TTL, and zero turns fit 1.25× while 95 fit
2.0×. **That is a fact about the rate being billed, not about how long any
particular cache entry survived.** The entry states mode B's mechanism on that
basis and no stronger — see its §3.

**Four turns do not reconcile**, and the entry says so rather than dropping
them. The strangest is an errored turn billed **$9.8231 with every token counter
at zero** — a charge with no recorded consumption. It is preserved in the trace
and pinned by `test_trace_unreconciled_turn_count_is_what_the_entry_says`. I do
not know what it is.

The four are **not spread evenly**, which changes what each session total can be
called. Session A reconciles 15 of 15 priced turns and session C 24 of 24, so
their totals ($53.85 and $69.92) are both provider-reported sums **and** complete
decompositions. Session B reconciles 56 of 60, so its **$235.7484** lifetime is a
provider-reported sum **only** — four turns inside it cannot be decomposed. An
earlier revision of this paragraph lumped A in with B and understated what A
supports.

## Redaction

Applied mechanically by `export_traces.py`. **Removed: nothing but identifiers
and duplicated fields.** One field is derived rather than copied, and it is
named below.

- **`session_id`** — the only identifier these rows carry. Each distinct original
  id is replaced with `session-A` … `session-D`, assigned in the order the entry
  introduces them, so equal ids stay equal and distinct ids stay distinct. Each
  file contains exactly one session, so the mapping is trivially injective, and
  the export asserts it. **The real ids are supplied to the script at run time
  and are not in this repository.** An earlier revision hardcoded them in a
  module-level table inside `export_traces.py` — publishing, in the redaction
  script itself, exactly the identifiers it exists to remove. Review caught it;
  `test_no_identifier_leaks_anywhere_in_the_entry` now scans every committed
  file in the entry, not just the `.jsonl`.
- **`agent_id`** — dropped entirely (an opaque local id, load-bearing for
  nothing in the analysis).
- **`id`** — the ledger's autoincrement primary key, dropped. It leaks the
  volume and interleaving of *other*, unrelated sessions on the instance.
- **`origin`** — dropped; constant (`"turn"`) across every row in the database.
- **`model_usage`** — the raw per-model JSON blob, dropped. Everything in it
  duplicates the columns above except `contextWindow`.

**The one derived field.** `context_window` is lifted out of the dropped
`model_usage` blob, because the analysis needs the window size. A turn may name
more than one model (a subagent on a cheaper model), so this is not a pure copy.
The transform asserts the turn reports exactly **one** distinct window value and
copies it; if a turn ever reports two, the export raises rather than choosing.
An earlier revision silently took `max()` of the set, which is a *choice* rather
than a reduction and was not declared as one.

Nothing else was removed or altered.

Model names are preserved **verbatim**, including the inconsistent casing
(`Claude-opus-4-8` on the older rows, `claude-opus-4-8` on the newer). That
inconsistency is real data, not noise — the price lookup in `repro.py`
lowercases precisely because of it.

### Accepted residual risk: timestamps

Timestamps are preserved to the microsecond, and this is a deliberate trade with
a real cost. The entire idle-gap analysis is a function of them — the 4.1-hour
gap, the 81-second sibling, the 132-minute codex gap — and relative offsets
alone would not let a reader re-derive which turns fall in the same working
session. But absolute timestamps **do disclose the operator's working hours**:
anyone reading these files can see when this person works, including the 06:06
and 01:03 turns quoted in the entry.

That is disclosed here rather than glossed. For this repository the exposure is
judged acceptable — the incident dates are already stated in the entry, the
timezone is UTC, and the operator is the repository owner publishing their own
data. **It would not be acceptable for a trace involving anyone else**, and a
future entry covering another person's sessions should export elapsed time from
a per-session epoch instead.

## Checks anyone can run without the original database

- **No identifiers survive, anywhere in the entry.**
  `test_no_identifier_leaks_anywhere_in_the_entry` walks every committed `.py`,
  `.jsonl`, and `.md` file and asserts none contains a session-id-shaped token
  or an absolute home path. `test_trace_records_carry_no_content_and_no_identifiers`
  additionally asserts every record carries exactly the whitelisted key set and
  a `session-` placeholder.
- **The traces still say what the entry says.** `claims.py --check` recomputes
  all **44** quoted figures from these files, asserts each still appears the
  expected number of times in the entry README and both root READMEs, and
  verifies a SHA-256 per trace. Editing a number in either place — or any single
  one of its several occurrences — turns it red.
  `test_claims_checker_agrees_with_traces_and_prose` runs it as part of the
  ordinary test command.
- **The pricing reconciliation is self-checking.**
  `test_trace_only_the_1h_rate_reconciles` asserts 95 matches at the 1-hour rate
  and exactly 0 at the 5-minute rate.

One property genuinely requires the original, stated so a reviewer who has it
knows what to run: **redaction was reduction only** (modulo the one derived
field above). Run:

```
python3 export_traces.py --db <path> --session-file <your mapping> --check
```

It regenerates from the source ledger and diffs against the committed files,
printing `OK` for all four when they match.
