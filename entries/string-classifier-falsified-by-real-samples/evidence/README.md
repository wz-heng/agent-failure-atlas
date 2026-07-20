# Evidence — usage-limit samples

Four captured CLI streams: for each of two CLIs, one failure caused by the
**user's own exhausted quota** and one caused by a **server-side throttle**.

| file | CLI | class |
|---|---|---|
| `claude_user_limit_5h.jsonl` | Claude Code 2.1.209 | user's own 5-hour limit |
| `claude_server_429.jsonl` | Claude Code 2.1.209 | server-side 429 |
| `codex_user_limit.jsonl` | Codex CLI 0.142.5 | user's own usage limit |
| `codex_server_429.jsonl` | Codex CLI 0.142.5 | server-side 429 (retry exhaustion) |

The two claude files are the pair that matters: **identical HTTP status,
overlapping prose, opposite required disposition.**

## Provenance

Captured 2026-07-14 on macOS 26.3 (Darwin 25.3.0) by pointing each CLI at a
local HTTP upstream that returned a genuine 429 envelope, then running the
CLI's own spawn → stream-json path. The CLI binaries, their argument handling,
their retry behaviour and their output encoding are all real. Only the upstream
is local, which is why no quota was spent: the model is never reached.

These are not synthesized. Nothing in them was written to fit a conclusion —
the conclusion was reached *because* the traces contradicted the design that
existed before they were taken.

**Upstream provenance.** These are redactions of `tests/fixtures/limit_*.jsonl`
committed to Owlery (private) at `528d45f`, 2026-07-14 19:27. The surrounding
history that makes them evidence rather than data:

| commit | what |
|---|---|
| `45fb759` | the plan they falsified — a string pattern set, "mutually exclusive by construction" |
| `528d45f` | these traces |
| `cb3a1af` | the plan rewritten four minutes later: "detection is structural, not textual" |
| `3f08320` | detection split into a pure stream-only classifier + a separate I/O epoch lookup |
| `858924d` | the shipped classifiers (`server/harness/{claude_code,codex}.py`) and their tests |

## Redaction

Applied mechanically to the original captures. Removed:

- **Identifiers** — every `session_id`, `thread_id`, `uuid`, `hook_id`, and
  message `id` replaced with a placeholder. The mapping is **one-to-one within
  each file**: each distinct original value gets its own placeholder, numbered
  in first-seen order and prefixed by its field family (`session-0001-…`,
  `uuid-0002-…`). So equal identifiers stay equal and *distinct identifiers stay
  distinct* — record linkage within a stream survives.

  > An earlier revision of these files keyed the mapping on the field *name*
  > rather than the value, collapsing all six distinct identifiers in each
  > claude trace onto two placeholders while claiming linkage was preserved.
  > That was wrong and is fixed; the claim above is now true of the files as
  > shipped.
- **Local paths** — `cwd`, `memory_paths`, and plugin install paths dropped.
- **Machine inventory** — on claude's `init` record, the `tools`,
  `slash_commands`, `skills`, `agents`, `plugins`, `mcp_servers`, and
  `capabilities` arrays dropped, along with the `output_style` field. These
  describe one laptop's configuration and have no bearing on how a CLI reports
  a limit.

  The complete set of keys dropped from `init`, so the check below reproduces
  exactly: `tools`, `mcp_servers`, `slash_commands`, `agents`, `skills`,
  `plugins`, `memory_paths`, `cwd`, `capabilities`, `output_style`.
- **Whole records: `hook_started` / `hook_response`.** These carried a large
  local hook payload (this machine's agent configuration). They occur before
  any request is made and contain no limit state.

Preserved verbatim, because they are the evidence:

- `rate_limit_event` and its entire `rate_limit_info` payload, including
  `status`, `rateLimitType`, and the `resetsAt` epoch.
- Every error and terminal record: `api_error_status`, `is_error`,
  `terminal_reason`, and the rendered message text exactly as the CLI wrote it.
- `claude_code_version` and `model`, so the claim stays pinned to a version.
- Codex's `error` and `turn.failed` records in full.

Redaction was reduction only: no field value that survives was altered, apart
from the identifier substitution described above.

Two checks anyone can run without access to the originals:

- **The epochs are real and self-consistent.** `resetsAt: 1784038967` renders to
  2026-07-14 22:22 +08:00, matching the `resets 10:22pm (Asia/Shanghai)` in the
  prose of the same record — a value the CLI rendered from that epoch.
- **Placeholder distinctness and consistent reuse.** Collect every `session_id`,
  `uuid`, `hook_id`, `thread_id` and message `id`: the shipped files carry 6, 6,
  1 and 1 distinct placeholders respectively, and each placeholder recurs only
  where the same entity is referenced. This shows the shipped files did not
  collapse everything onto one token — it does **not**, on its own, prove the
  mapping is injective, since two distinct placeholders could in principle have
  come from one original value. Only the originals settle that.

Two properties genuinely require the originals, and are stated here so a
reviewer who has them knows exactly what to run:

- **The mapping is bijective** — each distinct original identifier maps to its
  own placeholder (constructed in first-seen order, so this holds by
  construction, and is asserted during generation).
- **Redaction was reduction only** — strip every identifier field and the ten
  dropped `init` keys listed above from both the original and the shipped file;
  the remaining structures compare equal, on all four files.
