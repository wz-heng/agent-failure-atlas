# Agent Failure Atlas

A catalogue of failure modes in AI agent systems — **only ones I hit myself**,
each with the evidence that proves it, and a minimal case you can run offline in
under a minute.

Every entry answers five questions: what broke, what the real mechanism was
(*including the wrong diagnosis I tried first*), what evidence supports the
claim and how strong it is, how to reproduce it, and how to detect and defend
against it in production.

**Why this exists.** As models get stronger, "did it actually do the right
thing?" gets *harder* to eyeball, not easier. Knowledge about how these systems
fail appreciates as capability grows — a patch is a consumable, a documented
failure mode is not. And a blog post is an opinion; a graded, reproducible case
is a checkable fact.

---

## Entries

| # | Failure mode | Symptom | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| 1 | [A string classifier falsified by real samples: usage-limit detection](entries/string-classifier-falsified-by-real-samples/) | A quota-exhausted turn and a server-side throttle arrive as the same HTTP 429 with overlapping prose, but need opposite handling | ![live reproduction](https://img.shields.io/badge/evidence-live%20reproduction-brightgreen) | Sessions die unattended for hours — or suspend for hours on a blip that clears in seconds | Key on whether the CLI *names the exhausted window* (`rateLimitType` + `resetsAt`), never on rendered prose |

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

## Rules this repo holds itself to

- **Default cases are offline, account-free, unpaid, and finish in under 60
  seconds.** No entry requires a vendor account or spends API budget.
- **Every case has a machine-checked oracle** — an assertion that passes or
  fails, not a wall of output for you to squint at.
- **No entry imports the system it came from.** Cases are self-contained
  reimplementations, so they stay runnable and readable in isolation.
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
docs/              design notes
```

---

[中文说明](README.zh-CN.md) · English is the canonical version; each entry
carries a Chinese summary at the end.
