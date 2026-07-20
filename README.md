# Agent Failure Atlas

A catalogue of failure modes in AI agent systems — **only ones I hit myself**,
each with the evidence that proves it, and a minimal case you can run offline in
under a minute.

Every entry answers five questions: what broke, what the real mechanism was
(*including the wrong diagnosis I tried first*), what evidence supports the
claim and how strong it is, how to reproduce it, and how to detect and defend
against it in production.

**Where these come from.** Building and running **Owlery**, a personal agent
platform (FastAPI + React) that drives the Claude Code and Codex CLIs as
long-lived agent sessions — scheduled runs, agent-to-agent delegation,
messaging bridges. Owlery's own repository is private; each entry carries the
evidence out of it in redacted, self-contained form, so nothing here depends on
access to it.

**Why this exists.** As models get stronger, "did it actually do the right
thing?" gets *harder* to eyeball, not easier. Knowledge about how these systems
fail appreciates as capability grows — a patch is a consumable, a documented
failure mode is not. And a blog post is an opinion; a graded, reproducible case
is a checkable fact.

---

## Entries

| # | Failure mode | Symptom | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| 1 | [A usage-limit classifier falsified by real samples](entries/string-classifier-falsified-by-real-samples/) | For Claude, a quota-exhausted turn and a server-side throttle both arrive as HTTP 429, and the throttle's prose carries *more* rate-limit vocabulary than the real limit's; for Codex, the real usage limit carries no 429 at all | ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) | Sessions die unattended for hours — or suspend for hours on a blip that clears in seconds | Per backend: Claude keys on a structured field (`rateLimitType` + `resetsAt`); Codex has no such field, so it keys on a string marker fixed by captured traces of *both* classes, and reads its epoch structurally out of band |
| 2 | [Killing only the process-group leader leaks descendants that block the next run](entries/leader-only-kill-leaks-descendants/) | Tests hang at a stable percentage and delegations fail repeatedly, because a process nobody can see already owns what they need | ![live reproduction](https://img.shields.io/badge/evidence-live%20reproduction-brightgreen) | Hours lost to misdiagnosis — the symptom surfaces in code that is not at fault, and any A/B test run under contention is a coin flip | Spawn with `start_new_session=True`; on teardown signal the process **group**, then reap — both halves, or you trade an orphan for a zombie |
| 3 | [The silent empty turn: a model name that belonged to the other backend](entries/silent-empty-turn-cross-backend-model-mismatch/) | The agent goes "read but no reply" — the turn ends, the CLI exits 0, the stream ends in success, and there is simply no answer in it | ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) | Three unrelated upstream failures over six days wore one indistinguishable face, and the natural first hypothesis — "the model chose not to answer" — is plausible and wrong | Assert on what a success *produced*: a terminal success carrying no assistant output is an error. Plus a pre-spawn check that the model name belongs to the backend that will run it — and `which -a`, never `which` |
| 4 | [Two ways a long-context agent session burns money, neither of them thinking](entries/context-burn-two-modes/) | Nothing breaks. Turns complete, tools run, tests pass — and one 131-second turn that produced 3,080 tokens was billed $20.6988 (list-price equivalent), while two unrelated sessions spike 81 seconds apart | ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) ![mechanism simulation](https://img.shields.io/badge/evidence-mechanism%20simulation-yellow) | 90.8% of one session's cost was moving context around rather than generating anything; the output line was 9.2%. The natural hypothesis — "it's thinking too hard" — targets the smallest of the four cost lines | Watch the quantity that scales: context tokens as a fraction of the window, per live session. Retire a session at a watermark instead of handing it the next task — the ledger records the window but not the level, so this needs a new field, not a new query |

More entries land as their evidence matures — one at a time, not as a batch.

---

## Evidence levels

Every entry is graded. The grade is a claim about *what the artifact proves*,
and it is deliberately conservative.

| Level | Meaning |
|---|---|
| ![live reproduction](https://img.shields.io/badge/evidence-live%20reproduction-brightgreen) | The real dependency's behaviour is reproducible at low cost. |
| ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) | A redacted real event stream is replayed, reproducing how the consumer failed. The vendor's behaviour is proven by the versioned trace, not re-elicited. |
| ![mechanism simulation](https://img.shields.io/badge/evidence-mechanism%20simulation-yellow) | A minimal model demonstrates the same *class* of mechanism. Prominently labelled; **does not** claim to reproduce the original incident. |

The grade describes an entry's **runnable artifact**. Narrative that no
artifact establishes — an operator's recollection of an incident, say — is not
graded on this scale at all; it is labelled inline as testimony, with whatever
independent corroboration exists stated separately. An entry may pair a graded
artifact with ungraded narrative, and must then keep the two visibly apart.

## Rules this repo holds itself to

- **Default cases are offline, account-free, unpaid, and finish in under 60
  seconds.** No entry requires a vendor account or spends API budget.
- **Every case has a machine-checked oracle** — an assertion that passes or
  fails, not a wall of output for you to squint at.
- **No entry imports Owlery.** Cases are self-contained reimplementations of the
  logic that shipped there, so they stay runnable and readable in isolation.
- **Nothing is hand-written to fit the conclusion.** Where a fake process
  replays a stream, it replays a *captured real* stream; it never performs a
  scripted puppet show that confirms the thesis.
- **Second-hand cases are not collected.** If I did not hit it myself, it is not
  here.
- **Simulation is never written up as empirical**, and correlation is never
  written up as cause.
- **Reproducibility is pinned to versions, not promised in perpetuity.** Vendors
  change their behaviour; entries record the versions they were true of and
  preserve history as traces.
- **No private transcripts, paths, session IDs, or credentials** appear in any
  artifact. Each entry documents exactly what was redacted.

## Layout

```
entries/<slug>/
  README.md        the five-section entry
  repro.py         minimal case with oracles — offline, <60s
  test_defense.py  regression tests for the defense
  evidence/        redacted captures + a provenance & redaction log
  mutations.py     where an entry quotes mutation-testing numbers: generates
                   them, and `--check`s that the entry still matches
  simulate.py      where an entry pairs a measured artifact with a simulated
                   one: the simulation lives in its own file, under its own
                   banner, so its numbers can never be read as measurements
  claims.py        every figure the prose quotes, recomputed from the evidence
                   and pinned to where it appears; `--check` fails on drift in
                   either direction
  check_mutation_coverage.py
                   perturbs each occurrence of each claim in turn and asserts
                   the checker catches it — so "the prose is pinned" is a
                   property that was executed, not one that was asserted
docs/              design notes
```

---

[中文说明](README.zh-CN.md) · English is the canonical version; each entry
carries a Chinese summary at the end.
