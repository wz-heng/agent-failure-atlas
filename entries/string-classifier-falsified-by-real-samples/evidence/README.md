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

Captured 2026-07-14 on macOS by pointing each CLI at a local HTTP upstream that
returned a genuine 429 envelope, then running the CLI's own spawn →
stream-json path. The CLI binaries, their argument handling, their retry
behaviour and their output encoding are all real. Only the upstream is local,
which is why no quota was spent: the model is never reached.

These are not synthesized. Nothing in them was written to fit a conclusion —
the conclusion was reached *because* the samples contradicted the design that
existed before they were taken.

## Redaction

Applied mechanically to the original captures. Removed:

- **Identifiers** — every `session_id`, `thread_id`, `uuid`, `hook_id`, and
  message `id` replaced with a fixed placeholder. Equal identifiers in the
  original remain equal after replacement, so record linkage within a stream is
  preserved.
- **Local paths** — `cwd`, `memory_paths`, and plugin install paths dropped.
- **Machine inventory** — on claude's `init` record, the `tools`,
  `slash_commands`, `skills`, `agents`, `plugins`, `mcp_servers`, and
  `capabilities` arrays dropped. These describe one laptop's configuration and
  have no bearing on how a CLI reports a limit.
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
from the identifier substitution described above. The `resetsAt` epochs are
real (`1784038967` → 2026-07-14 22:22 +08:00), and match the local times in the
rendered prose — a consistency check you can run yourself.
