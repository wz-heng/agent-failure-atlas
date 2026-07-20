# Evidence — empty and rejected Codex turns

Five captured Codex CLI event streams, taken **2026-07-20** on macOS 26.3
(Darwin 25.3.0). Both CLI versions were sampled, each with a same-configuration
control turn: `0.142.5` is the build that was installed during the 2026-07-13
incident, `0.144.6` is the build installed on 2026-07-19.

This is **not** a controlled single-variable comparison across versions. The
0.142.5 runs used a throwaway `CODEX_HOME` and `--ignore-user-config`; the
0.144.6 runs did not. What each version's pair *does* support is a within-version
comparison — rejection against control, same flags, same session — and that is
all this corpus is used for.

| file | CLI | what it is | exit |
|---|---|---|---|
| `codex_0.144.6_zero_content_turn.jsonl` | 0.144.6 | a turn that **completed successfully and delivered nothing** | 0 |
| `codex_0.144.6_model_rejected.jsonl` | 0.144.6 | `--model Claude-opus-4-8` — rejected, loudly | 1 |
| `codex_0.142.5_model_rejected.jsonl` | 0.142.5 | the same, on the incident's build — also rejected, loudly | 1 |
| `codex_0.144.6_control_turn.jsonl` | 0.144.6 | a normal turn | 0 |
| `codex_0.142.5_control_turn.jsonl` | 0.142.5 | a normal turn | 0 |

The pair that matters is `zero_content` against either `control`: **identical
terminal record (`turn.completed`), identical exit code, one of them carrying
nothing for the user, and no error anywhere in the stream.**

## What these traces do and do not show

They are captures of the *stream shapes* a consumer must handle. They are **not**
a replay of the 2026-07-13 incident, and nothing here should be cited as one.

- The two `model_rejected` captures are the incident's exact configuration —
  a Claude model name handed to the Codex backend — re-run today. They show the
  vendor now rejects it with an explicit `error` + `turn.failed` and exit 1, on
  **both** CLI versions. So the silent behaviour reported on 2026-07-13 is not
  reproducible today. **Where the difference lives is undetermined**: these runs
  also differ from the incident on the client side (throwaway `CODEX_HOME`,
  `--ignore-user-config`), account and plan state may have changed, and the
  operator's "zero error events" may have meant "nothing surfaced in Owlery".
  A server-side change is consistent with the captures; it is not established
  by them. See the entry's §3.
- The `zero_content` capture was elicited by *asking the model to say nothing*
  (prompt below), not by a rejection. It is a real capture of the stream shape
  the consumer mishandles — terminal success, zero content, zero errors —
  obtained by a different cause than the incident's. Same shape, different
  origin. It is the input to `repro.py`; it is not proof of what happened on
  2026-07-13.

## Provenance

Each capture is the real stdout of one real `codex exec --json` invocation
against the real service, carrying the identifier-only redaction documented
below and nothing else. It is not byte-identical to what the CLI wrote: each
record was parsed, its `thread_id` substituted, and re-serialized by
`json.dumps`, so key order is preserved but separator spacing is not. No field
was added, removed, or otherwise altered. Unlike this atlas's usage-limit
traces, there is no local upstream here: these requests reached OpenAI.

```
# 0.144.6 (the installed build, /opt/homebrew/bin/codex)
codex exec --json --ephemeral --skip-git-repo-check \
      [--model Claude-opus-4-8] -C <throwaway dir> "<prompt>" < /dev/null

# 0.142.5 (installed into a throwaway npm prefix for this capture only)
CODEX_HOME=<throwaway dir containing only a copy of auth.json> \
  ./codex exec --json --ephemeral --skip-git-repo-check --ignore-user-config \
      [--model Claude-opus-4-8] -C <throwaway dir> "<prompt>" < /dev/null
```

Prompts, verbatim:

| capture | prompt |
|---|---|
| both controls, both rejections | `Reply with exactly: OK` |
| `zero_content` | `Do not reply. Produce no final message of any kind. End your turn immediately with an empty response.` |

**Cost.** The two rejections cost nothing — a 400 is returned before inference.
The three completed turns reported `output_tokens` of 5, 5 and 4 respectively,
and their `usage` records are preserved in the files. Total spend: 14 output
tokens.

**Isolation.** The 0.142.5 runs used a throwaway `CODEX_HOME` containing a copy
of `auth.json` and nothing else, plus `--ignore-user-config`; the real
`CODEX_HOME` was not read for config and not written to. Every run used
`--ephemeral`, so no session files were persisted, and a throwaway working
directory. The 0.142.5 install, its npm prefix, the throwaway `CODEX_HOME` (which
held a credential copy) and the working directory were deleted after capture.

**Corroboration for the version history**, checkable on the machine that ran
this — filesystem timestamps, which are the only surviving record of the
2026-07-19 remediation:

| path | mtime | what it records |
|---|---|---|
| `/opt/homebrew/lib/node_modules/@openai/codex/package.json` | 2026-07-19 10:18:13 | the upgrade to 0.144.6 landing under Homebrew |
| `/Users/<user>/.local/bin/codex` | 2026-07-19 10:37:04 | that symlink being **rewritten**, 19 minutes later |

These fix the **timing** of the remediation and nothing more. In particular they
do not record what the symlink pointed at *before* 10:37 — that target is
unrecorded, and no older install survives on the machine — so the shadowing
mechanism itself rests on the operator's notes. The entry's §2 states the gap.

No commit in Owlery records the incident; the fix was operational, not a code
change.

## Redaction

Applied mechanically. **Removed: nothing but identifiers.** No field value that
survives was altered.

- **`thread_id`** — the only identifier these streams carry. Each distinct
  original value is replaced with `thread-NNNN-0000-0000-000000000000`, numbered
  in first-seen order, so equal ids stay equal and distinct ids stay distinct.
  Each file contains exactly one distinct `thread_id`, so the mapping is
  trivially injective, and generation asserts it.
- Nothing else was dropped. These streams contain no `cwd`, no paths, no machine
  inventory, no credentials — `--ephemeral` and `--ignore-user-config` keep them
  out, and the files are short enough to read end to end and confirm it.

The exact transform, so it reproduces:

```python
seen = {}
for line in original.splitlines():
    obj = json.loads(line)
    tid = obj.get("thread_id")
    if isinstance(tid, str):
        seen.setdefault(tid, "thread-%04d-0000-0000-000000000000" % (len(seen) + 1))
        obj["thread_id"] = seen[tid]
    emit(json.dumps(obj))
assert len(set(seen.values())) == len(seen)
```

Item ids (`item_0`) are the CLI's own per-turn counters, not identifiers of
anything outside the stream, and are preserved verbatim.

## Checks anyone can run without the originals

- **No local identifiers survive.** `grep -c '/Users/\|/tmp/' *.jsonl` returns 0
  for every file. `test_defense.py::test_every_trace_is_valid_jsonl_and_carries_no_raw_identifiers`
  asserts this plus the `thread-` prefix on every `thread_id`.
- **The rejections really are loud, and the empty turn really is
  success-shaped.** `test_todays_rejection_is_loud_on_both_versions` asserts each
  rejection carries `error` + `turn.failed` and **no** `turn.completed`;
  `test_the_zero_content_capture_really_is_a_success_shaped_stream` asserts the
  empty capture carries `turn.completed`, no error record, and exactly one
  `agent_message` whose text is `""`. Together they pin the claim this entry
  rests on, so prose cannot drift away from the files.
- **The usage records are internally consistent** with the "14 output tokens"
  claim above: sum the `output_tokens` across the three `turn.completed`
  records.

One property genuinely requires the originals, stated so a reviewer who has them
knows what to run: **redaction was reduction only** — strip `thread_id` from both
the original and the shipped file and the remaining structures compare equal, on
all five files.
